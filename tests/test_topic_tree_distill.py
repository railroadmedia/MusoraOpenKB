"""Unit coverage for the bottom-up distill() engine (openkb/topic_tree.py).

Deterministic — injected cluster/summarize/relate callables, no LLM.
"""
import re

import pytest
import yaml

from openkb import topic_tree as tt


# --- deterministic injected callables ---------------------------------------

def chunk_cluster(target):
    """Group items into fixed-size chunks — guarantees the layer shrinks when
    len(items) > target, so distillation makes progress."""
    def cluster(items):
        groups = {}
        for i in range(0, len(items), target):
            groups[f"g{i // target}"] = [s for s, _ in items[i:i + target]]
        return groups
    return cluster


def all_in_one_cluster(items):
    """Degenerate: one all-inclusive group."""
    return {"all": [s for s, _ in items]}


def one_each_cluster(items):
    """Degenerate: every item its own group (no shrink)."""
    return {s: [s] for s, _ in items}


def summarize(name, summaries):
    return f"summary of {name}"


def adj_relate(nodes):
    names = [n for n, _ in nodes]
    rel = {}
    for i, n in enumerate(names):
        peers = []
        if i > 0:
            peers.append(names[i - 1])
        if i + 1 < len(names):
            peers.append(names[i + 1])
        rel[n] = peers
    return rel


# --- helpers ----------------------------------------------------------------

def _flat(root, n, prefix="c"):
    root.mkdir(parents=True, exist_ok=True)
    stems = [f"{prefix}{i:03d}" for i in range(n)]
    for s in stems:
        (root / f"{s}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "brief {s}"\n---\n# {s}\n\n[[c000]]\n',
            encoding="utf-8",
        )
    return set(stems)


def _concept_stems(root):
    return {p.stem for p in root.rglob("*.md") if p.name != tt.TOPIC_FILE}


def _topic_nodes(root):
    """name -> {layer, related, children} from every _topic.md under root."""
    out = {}
    for tp in root.rglob(tt.TOPIC_FILE):
        text = tp.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
        fm = yaml.safe_load(m.group(1)) if m else {}
        name = "(root)" if tp.parent == root else tp.parent.name
        out[name] = fm
    return out


# --- invariants -------------------------------------------------------------

@pytest.mark.parametrize("n", [1, 2, 3, 8, 9, 25, 100])
def test_single_root_and_min_two_layers(tmp_path, n):
    root = tmp_path / "concepts"
    stems = _flat(root, n)
    stats = tt.distill(root, cluster=chunk_cluster(3), summarize=summarize)
    # exactly one root file, tagged layer 1
    rootfm = _topic_nodes(root)["(root)"]
    assert rootfm.get("layer") == 1
    assert (root / tt.TOPIC_FILE).is_file()
    # always >= 2 layers
    assert stats["layers"] >= 2
    # nothing lost
    assert _concept_stems(root) == stems


@pytest.mark.parametrize("cluster", [all_in_one_cluster, one_each_cluster])
def test_degenerate_clusterers_terminate(tmp_path, cluster):
    root = tmp_path / "concepts"
    stems = _flat(root, 20)
    stats = tt.distill(root, cluster=cluster, summarize=summarize, max_depth=6)
    assert stats["layers"] >= 2
    assert _concept_stems(root) == stems  # no loss, no hang


def test_depth_respects_max_depth(tmp_path):
    root = tmp_path / "concepts"
    _flat(root, 64)
    stats = tt.distill(root, cluster=chunk_cluster(2), summarize=summarize, max_depth=4)
    assert stats["layers"] <= 4 + 1  # backstop honored (root wrap may add one)


def test_leaves_are_preserved_verbatim(tmp_path):
    root = tmp_path / "concepts"
    _flat(root, 12)
    before = {p.stem: p.read_text(encoding="utf-8")
              for p in root.glob("*.md") if p.name != tt.TOPIC_FILE}
    tt.distill(root, cluster=chunk_cluster(3), summarize=summarize)
    after = {p.stem: p.read_text(encoding="utf-8")
             for p in root.rglob("*.md") if p.name != tt.TOPIC_FILE}
    assert after == before  # content byte-identical, just relocated


def test_empty_kb_no_crash(tmp_path):
    root = tmp_path / "concepts"
    stats = tt.distill(root, cluster=chunk_cluster(3), summarize=summarize)
    assert stats == {"leaves": 0, "layers": 1, "nodes": 1}
    assert (root / tt.TOPIC_FILE).is_file()


def test_pathway_nodes_have_children_index(tmp_path):
    root = tmp_path / "concepts"
    _flat(root, 15)
    tt.distill(root, cluster=chunk_cluster(3), summarize=summarize)
    for name, fm in _topic_nodes(root).items():
        assert fm.get("type") == "topic"
        assert isinstance(fm.get("children"), list)
        assert fm.get("size") == len(fm["children"])
        assert isinstance(fm.get("layer"), int)


def test_data_loss_prevented_on_cluster_failure(tmp_path):
    root = tmp_path / "concepts"
    stems = _flat(root, 15)

    def boom(items):
        raise RuntimeError("LLM down mid-distill")

    with pytest.raises(RuntimeError):
        tt.distill(root, cluster=boom, summarize=summarize)
    # originals still present, flat, unharmed; no staging left behind
    assert {p.stem for p in root.glob("*.md") if p.name != tt.TOPIC_FILE} == stems
    assert not any(d.is_dir() for d in root.iterdir())
    assert not (root.parent / f".{root.name}.distill").exists()


# --- sideways links ---------------------------------------------------------

def test_sideways_links_bidirectional_same_layer_bounded(tmp_path):
    root = tmp_path / "concepts"
    _flat(root, 40)
    tt.distill(root, cluster=chunk_cluster(3), summarize=summarize,
               relate=adj_relate, sideways_links_max=2)
    nodes = _topic_nodes(root)
    # map name -> layer for same-layer + resolution checks
    layer = {n: fm.get("layer") for n, fm in nodes.items()}
    for name, fm in nodes.items():
        related = fm.get("related") or []
        assert len(related) <= 2                       # bounded
        assert name not in related                     # no self-links
        for peer in related:
            assert peer in nodes                       # resolves to a real node
            assert layer[peer] == layer[name]          # same layer only
            assert name in (nodes[peer].get("related") or [])  # bidirectional


def test_no_redundant_single_child_internal_nodes(tmp_path):
    """A clusterer that keeps producing a single group builds single-child wrapper
    layers; distill must collapse them so no internal node has exactly one
    (topic) child, while still preserving every concept and a single root."""
    root = tmp_path / "concepts"
    stems = _flat(root, 12)

    def single_group(items):  # always one group -> would nest 1-child layers
        return {"only": [s for s, _ in items]}

    stats = tt.distill(root, cluster=single_group, summarize=summarize, max_depth=6)
    assert _concept_stems(root) == stems           # nothing lost
    assert (root / tt.TOPIC_FILE).is_file()         # single root survives
    # no topic dir contains exactly one subtopic-and-nothing-else
    for d in [root, *[p for p in root.rglob("*") if p.is_dir()]]:
        subtopics = [c for c in d.iterdir() if c.is_dir()]
        concepts = [c for c in d.glob("*.md") if c.name != tt.TOPIC_FILE]
        if len(subtopics) == 1 and not concepts:
            raise AssertionError(f"redundant single-child wrapper at {d}")


def test_collapse_keeps_topic_with_single_concept_leaf(tmp_path):
    """A topic whose only child is a concept LEAF is kept (not collapsed to a bare
    leaf) so the >= 2-layer invariant and a real root summary survive."""
    root = tmp_path / "concepts"
    _flat(root, 1)
    stats = tt.distill(root, cluster=chunk_cluster(3), summarize=summarize)
    assert stats["layers"] >= 2
    assert (root / tt.TOPIC_FILE).is_file()


def test_sideways_disabled_when_no_relate(tmp_path):
    root = tmp_path / "concepts"
    _flat(root, 15)
    tt.distill(root, cluster=chunk_cluster(3), summarize=summarize, relate=None)
    for fm in _topic_nodes(root).values():
        assert (fm.get("related") or []) == []

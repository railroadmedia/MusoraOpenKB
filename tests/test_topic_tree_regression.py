"""Regression pins for previously-fixed topic-tree bugs.

Each test names the bug, reproduces the exact failing condition with the
smallest setup, and asserts the corrected behavior. Deterministic (injected
callables, no LLM).
"""
import re

import pytest
import yaml

from openkb import topic_tree as tt
from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks


def _flat(root, stems):
    root.mkdir(parents=True, exist_ok=True)
    for s in stems:
        (root / f"{s}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "b {s}"\n---\n# {s}\n', encoding="utf-8",
        )


def _chunk(target):
    def cluster(items):
        return {f"g{i // target}": [s for s, _ in items[i:i + target]]
                for i in range(0, len(items), target)}
    return cluster


def _adj_relate(nodes):
    names = [n for n, _ in nodes]
    return {n: [x for x in (names[i - 1] if i else None,
                            names[i + 1] if i + 1 < len(names) else None) if x]
            for i, n in enumerate(names)}


@pytest.mark.regression
def test_regression_bootstrap_no_data_loss_on_cluster_failure(tmp_path):
    """bootstrap() used to unlink the flat concept leaves BEFORE calling the
    clusterer, so an LLM/clusterer exception destroyed the source concepts.

    Fixed by building into a staging dir and only removing the originals after
    the tree builds successfully. See openkb/topic_tree.py::bootstrap.
    """
    root = tmp_path / "concepts"
    stems = [f"c{i:02d}" for i in range(tt.FANOUT_K + 5)]  # > K so cluster runs
    _flat(root, stems)

    def exploding_cluster(items):
        raise RuntimeError("LLM went down mid-build")

    with pytest.raises(RuntimeError):
        tt.bootstrap(root, cluster=exploding_cluster, summarize=lambda n, b: "s")

    # Every original leaf still on disk, still flat, no partial tree left behind.
    survivors = {p.stem for p in root.glob("*.md") if p.name != tt.TOPIC_FILE}
    assert survivors == set(stems)
    assert not any(d.is_dir() for d in root.iterdir())  # no half-built subtopics
    # Staging dir cleaned up.
    assert not (root.parent / f".{root.name}.rebuild").exists()


@pytest.mark.regression
def test_regression_degenerate_clusterer_does_not_recurse_forever(tmp_path):
    """A clusterer returning one all-inclusive group must terminate at MAX_DEPTH
    instead of recursing endlessly, and must not drop any concept."""
    root = tmp_path / "concepts"
    stems = [f"c{i:02d}" for i in range(tt.FANOUT_K + 5)]
    _flat(root, stems)

    placed = tt.bootstrap(
        root,
        cluster=lambda items: {"all": [s for s, _ in items]},
        summarize=lambda n, b: "s",
    )
    assert placed == len(stems)
    survivors = {p.stem for p in root.rglob("*.md") if p.name != tt.TOPIC_FILE}
    assert survivors == set(stems)
    depth = max(
        len(p.relative_to(root).parts) - 1
        for p in root.rglob("*.md") if p.name != tt.TOPIC_FILE
    )
    assert depth <= tt.MAX_DEPTH


@pytest.mark.regression
def test_regression_distill_sideways_links_resolve_no_ghosts(tmp_path):
    """Sideways (`related`) links point to topic NODES (directory names). lint
    must recognize topic-dir names as valid wikilink targets, otherwise every
    sideways link is a ghost. Guards the lint change that registers topic dirs.
    """
    wiki = tmp_path / "wiki"
    concepts = wiki / "concepts"
    _flat(concepts, [f"c{i:02d}" for i in range(30)])
    tt.distill(concepts, cluster=_chunk(3), summarize=lambda n, s: f"s {n}",
               relate=_adj_relate, sideways_links_max=3)

    targets = list_existing_wiki_targets(wiki)
    related_seen = 0
    for tp in concepts.rglob(tt.TOPIC_FILE):
        fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n",
                                     tp.read_text(encoding="utf-8"), re.DOTALL).group(1))
        for peer in fm.get("related") or []:
            related_seen += 1
            _, ghosts = strip_ghost_wikilinks(f"[[{peer}]]", targets)
            assert ghosts == [], f"sideways link [[{peer}]] resolved as a ghost"
    assert related_seen > 0, "expected the tree to have some sideways links"


@pytest.mark.regression
@pytest.mark.parametrize("n", [1, 2, 8])
def test_regression_single_root_min_two_layers_edge_sizes(tmp_path, n):
    """distill must always yield exactly one root file and >= 2 layers, even for
    tiny inputs (1, 2, and exactly one full fan-out of leaves)."""
    root = tmp_path / "concepts"
    _flat(root, [f"c{i}" for i in range(n)])
    stats = tt.distill(root, cluster=_chunk(8), summarize=lambda nm, s: "s")
    assert (root / tt.TOPIC_FILE).is_file()
    fm = yaml.safe_load(re.match(r"^---\n(.*?)\n---\n",
                                 (root / tt.TOPIC_FILE).read_text(encoding="utf-8"),
                                 re.DOTALL).group(1))
    assert fm["layer"] == 1
    assert stats["layers"] >= 2
    assert {p.stem for p in root.rglob("*.md") if p.name != tt.TOPIC_FILE} == {f"c{i}" for i in range(n)}

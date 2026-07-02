"""Tier-1 unit coverage for the topic-tree engine's structural invariants.

Complements ``test_topic_tree.py`` (happy paths) with the edge cases the
distillation plan calls out: defensive descent, bounded recursion under a
degenerate clusterer, no-concept-loss when the clusterer omits items, and the
tiny/empty size edges. All deterministic — no LLM, injected callables only.
"""
from pathlib import Path

from openkb import topic_tree as tt


def _flat(root: Path, stems, brief="b"):
    root.mkdir(parents=True, exist_ok=True)
    for s in stems:
        (root / f"{s}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "{brief}"\n---\n# {s}\n',
            encoding="utf-8",
        )


def _all_md(root: Path):
    return {p.stem for p in root.rglob("*.md") if p.name != tt.TOPIC_FILE}


# --- place_concept / place_topic_dir descent guards --------------------------


def test_place_concept_stops_on_nonexistent_child(tmp_path):
    """A ``choose`` that names a child that does not exist must stop defensively
    at the current node rather than descend into a bogus path."""
    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    tt.write_topic_md(root / "real", "real topic", 0)

    path = tt.place_concept(
        root, "c", "b", "# c\n",
        choose=lambda v, b: "ghost",  # never a real child
    )
    assert path == root / "c.md"  # landed at root, did not descend into ghost/
    assert not (root / "ghost").exists()


def test_place_topic_dir_descends_without_writing_concept(tmp_path):
    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    tt.write_topic_md(root / "attention", "att", 0)

    def choose(view, brief):
        return "attention" if any(t == "attention" for t, _ in view.child_topics) else None

    node = tt.place_topic_dir(root, brief="q attends k", choose=choose)
    assert node == root / "attention"
    # No concept file was written by the placement itself.
    assert list(node.glob("*.md")) == [node / tt.TOPIC_FILE]


def test_place_topic_dir_stops_on_nonexistent_child(tmp_path):
    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    node = tt.place_topic_dir(root, brief="x", choose=lambda v, b: "ghost")
    assert node == root
    assert not (root / "ghost").exists()


# --- split_node edge --------------------------------------------------------


def test_split_node_empty_is_noop(tmp_path):
    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    tt.split_node(root, cluster=lambda items: {"x": []}, summarize=lambda n, b: "s")
    # No subtopics created; node still just its own _topic.md.
    assert [p.name for p in root.iterdir()] == [tt.TOPIC_FILE]


# --- bootstrap size edges ---------------------------------------------------


def test_bootstrap_empty_root(tmp_path):
    """Empty KB: no crash, root topic written, zero placed."""
    root = tmp_path / "concepts"
    n = tt.bootstrap(root, cluster=lambda i: {}, summarize=lambda n, b: "s")
    assert n == 0
    assert (root / tt.TOPIC_FILE).exists()


def test_bootstrap_single_leaf_stays_flat(tmp_path):
    """Below fan-out the clusterer is never consulted and the leaf stays put."""
    root = tmp_path / "concepts"
    _flat(root, ["only"])

    def cluster(items):
        raise AssertionError("clusterer must not run below fan-out")

    n = tt.bootstrap(root, cluster=cluster, summarize=lambda n, b: "s")
    assert n == 1
    assert (root / "only.md").is_file()
    assert _all_md(root) == {"only"}


def test_bootstrap_exactly_fanout_stays_flat(tmp_path):
    """Exactly FANOUT_K leaves is the boundary: <= K means no split."""
    root = tmp_path / "concepts"
    stems = [f"c{i:02d}" for i in range(tt.FANOUT_K)]
    _flat(root, stems)
    n = tt.bootstrap(root, cluster=lambda i: {"g": [s for s, _ in i]},
                     summarize=lambda n, b: "s")
    assert n == tt.FANOUT_K
    assert _all_md(root) == set(stems)
    assert not any(d.is_dir() for d in root.iterdir())  # no subtopics


# --- bootstrap / _build_subtree structural invariants -----------------------


def test_degenerate_clusterer_terminates_bounded(tmp_path):
    """A clusterer that always returns one all-inclusive group must not recurse
    forever: recursion is capped at MAX_DEPTH and every concept survives."""
    root = tmp_path / "concepts"
    stems = [f"c{i:02d}" for i in range(tt.FANOUT_K + 5)]  # > K forces a split
    _flat(root, stems)

    n = tt.bootstrap(
        root,
        cluster=lambda items: {"all": [s for s, _ in items]},  # never shrinks
        summarize=lambda name, briefs: "s",
    )

    assert n == len(stems)
    assert _all_md(root) == set(stems)  # nothing lost
    # Nesting never exceeds MAX_DEPTH directories deep under the root.
    max_depth = max(
        len(p.relative_to(root).parts) - 1  # minus the file itself
        for p in root.rglob("*.md") if p.name != tt.TOPIC_FILE
    )
    assert max_depth <= tt.MAX_DEPTH


def test_bootstrap_preserves_concepts_clusterer_omits(tmp_path):
    """Stems the clusterer drops from every group stay as leaves at the node —
    no concept is silently lost."""
    root = tmp_path / "concepts"
    stems = [f"c{i:02d}" for i in range(tt.FANOUT_K + 2)]  # > K
    _flat(root, stems)
    orphans = {"c00", "c01"}

    def cluster(items):  # omit the two orphans from the only group
        return {"g": [s for s, _ in items if s not in orphans]}

    n = tt.bootstrap(root, cluster=cluster, summarize=lambda name, briefs: "s")
    assert n == len(stems)
    assert _all_md(root) == set(stems)  # orphans preserved
    # Orphans remained as flat leaves at the root, not inside the group.
    assert (root / "c00.md").is_file()
    assert (root / "c01.md").is_file()

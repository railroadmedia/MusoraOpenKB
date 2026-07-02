"""Regression pins for previously-fixed topic-tree bugs.

Each test names the bug, reproduces the exact failing condition with the
smallest setup, and asserts the corrected behavior. Deterministic (injected
callables, no LLM).
"""
import pytest

from openkb import topic_tree as tt


def _flat(root, stems):
    root.mkdir(parents=True, exist_ok=True)
    for s in stems:
        (root / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")


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

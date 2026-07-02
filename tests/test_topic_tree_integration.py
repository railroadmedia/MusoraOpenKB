"""Tier-2 integration: the real reindex path with a deterministic fake LLM.

Unlike test_reindex_cli.py (which mocks ``tt_bootstrap`` away), these drive the
real engine end-to-end through the CLI, routing every model call through the
deterministic fake (tests/support/fake_llm.py). Proves the wiring
CLI -> make_cluster/make_summarize -> bootstrap -> tree actually holds together.
"""
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from openkb import topic_tree as tt
from openkb.cli import cli
from tests.support.fake_llm import fake_llm


pytestmark = pytest.mark.integration


def _kb(tmp_path, n_concepts):
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    concepts = kb / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    for i in range(n_concepts):
        (concepts / f"concept-{i:02d}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "brief {i}"\n---\n# concept-{i:02d}\n',
            encoding="utf-8",
        )
    (kb / ".openkb" / "config.yaml").write_text(
        "model: gpt-5.4\ntopic_tree: true\n", encoding="utf-8"
    )
    return kb


def _all_concept_stems(concepts_root):
    return {p.stem for p in concepts_root.rglob("*.md") if p.name != tt.TOPIC_FILE}


def test_reindex_cli_builds_real_tree(tmp_path):
    kb = _kb(tmp_path, n_concepts=15)
    concepts = kb / "wiki" / "concepts"
    original = _all_concept_stems(concepts)

    with fake_llm(), patch("openkb.cli._setup_llm_key"):
        res = CliRunner().invoke(cli, ["--kb-dir", str(kb), "reindex"])

    assert res.exit_code == 0, res.output
    assert "15" in res.output

    # A real tree was built: root topic + at least one subtopic directory.
    assert (concepts / tt.TOPIC_FILE).exists()
    subtopics = [d for d in concepts.iterdir() if d.is_dir()]
    assert subtopics, "expected at least one subtopic directory"
    # Every concept survived the move, exactly once, none left flat at root.
    assert _all_concept_stems(concepts) == original
    assert not [p for p in concepts.glob("*.md") if p.name != tt.TOPIC_FILE]
    # Each subtopic has a summary and holds <= FANOUT_K children.
    for d in subtopics:
        assert (d / tt.TOPIC_FILE).exists()
        assert tt.child_count(d) <= tt.FANOUT_K


def test_reindex_twice_is_safe(tmp_path):
    """Re-running reindex after a tree exists must not lose or duplicate any
    concept. (bootstrap's cold-start only globs top-level flat files, so the
    second run is a no-op over an already-nested tree — assert that stays safe.)"""
    kb = _kb(tmp_path, n_concepts=15)
    concepts = kb / "wiki" / "concepts"
    original = _all_concept_stems(concepts)

    with fake_llm(), patch("openkb.cli._setup_llm_key"):
        first = CliRunner().invoke(cli, ["--kb-dir", str(kb), "reindex"])
        after_first = _all_concept_stems(concepts)
        second = CliRunner().invoke(cli, ["--kb-dir", str(kb), "reindex"])

    assert first.exit_code == 0 and second.exit_code == 0, (first.output, second.output)
    # No concept lost or duplicated across the second run.
    assert _all_concept_stems(concepts) == original == after_first
    # Second run reincorporated nothing new (tree already built).
    assert "0" in second.output

"""Tier-3 Group A — structural invariants against a REAL LLM.

Opt-in (never in CI). Runs the real reindex/bootstrap path over a small set of
concepts using an actual model, then asserts hard structural properties that
must hold regardless of the model's judgment. Non-deterministic content, but
these assertions are deterministic pass/fail.

Run:
  OPENKB_LLM_TESTS=1 OPENKB_TEST_MODEL=anthropic/claude-haiku-4-5 LLM_API_KEY=... \
    pytest -m llm tests/test_hierarchy_eval.py
  # or fully local:
  OPENKB_LLM_TESTS=1 OPENKB_TEST_MODEL=ollama/llama3.1 pytest -m llm
"""
import pytest

from openkb import topic_tree as tt
from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks
from tests.support.eval_corpus import CONCEPTS, build_real_tree, require_llm_or_skip

pytestmark = pytest.mark.llm


@pytest.fixture(scope="module")
def built_tree(tmp_path_factory):
    """Build the tree once per module against the real model."""
    model = require_llm_or_skip()
    wiki = tmp_path_factory.mktemp("wiki")
    return build_real_tree(model, wiki)


def test_single_root(require_llm, built_tree):
    assert (built_tree / "concepts" / tt.TOPIC_FILE).is_file()


def test_at_least_two_layers(require_llm, built_tree):
    concepts = built_tree / "concepts"
    subtopics = [d for d in concepts.iterdir() if d.is_dir()]
    assert subtopics, "real model should produce at least one subtopic (>=2 layers)"


def test_no_concept_lost(require_llm, built_tree):
    concepts = built_tree / "concepts"
    present = {p.stem for p in concepts.rglob("*.md") if p.name != tt.TOPIC_FILE}
    assert present == set(CONCEPTS)


def test_depth_and_fanout_bounded(require_llm, built_tree):
    concepts = built_tree / "concepts"
    for d in concepts.rglob("*"):
        if d.is_dir():
            assert tt.child_count(d) <= tt.FANOUT_K
    depth = max(
        len(p.relative_to(concepts).parts) - 1
        for p in concepts.rglob("*.md") if p.name != tt.TOPIC_FILE
    )
    assert 1 <= depth <= tt.MAX_DEPTH


def test_wikilinks_survive_moves(require_llm, built_tree):
    """major-scale is moved into a subtopic, but [[major-scale]] must still
    resolve by bare stem (zero ghost links)."""
    targets = list_existing_wiki_targets(built_tree)
    out, ghosts = strip_ghost_wikilinks("see [[major-scale]]", targets)
    assert ghosts == []
    assert "[[major-scale]]" in out


def test_every_subtopic_has_summary(require_llm, built_tree):
    concepts = built_tree / "concepts"
    for d in concepts.rglob("*"):
        if d.is_dir():
            topic = d / tt.TOPIC_FILE
            assert topic.is_file()
            assert topic.read_text(encoding="utf-8").strip()

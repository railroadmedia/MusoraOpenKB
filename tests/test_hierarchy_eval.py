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

pytestmark = pytest.mark.llm

# A small, topically-coherent concept set (music-education flavored) with enough
# related items that a real model must invent >1 coherent subtopic.
_CONCEPTS = {
    "major-scale": "The seven-note major scale and its whole/half-step pattern.",
    "minor-scale": "Natural, harmonic, and melodic minor scales.",
    "pentatonic-scale": "Five-note scales common in blues and rock soloing.",
    "circle-of-fifths": "How keys relate by perfect fifths; key signatures.",
    "triads": "Three-note chords: major, minor, diminished, augmented.",
    "seventh-chords": "Four-note chords adding a seventh above the triad.",
    "chord-inversions": "Reordering chord tones so a non-root is in the bass.",
    "chord-progressions": "Common functional progressions like ii-V-I.",
    "time-signatures": "How beats group into measures; 4/4, 3/4, 6/8.",
    "note-durations": "Whole, half, quarter, eighth notes and rests.",
    "syncopation": "Emphasizing off-beats against the underlying pulse.",
    "swing-feel": "Uneven eighth-note subdivision in jazz and blues.",
    "dynamics": "Volume markings from pianissimo to fortissimo.",
    "articulation": "Staccato, legato, and accent performance techniques.",
    "sight-reading": "Reading and performing notation at first sight.",
}


def _seed_concepts(wiki):
    d = wiki / "concepts"
    d.mkdir(parents=True, exist_ok=True)
    for stem, brief in _CONCEPTS.items():
        # Every concept links to major-scale to test link survival across moves.
        (d / f"{stem}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "{brief}"\n---\n'
            f"# {stem}\n\nSee also [[major-scale]].\n",
            encoding="utf-8",
        )
    return d


@pytest.fixture(scope="module")
def built_tree(tmp_path_factory):
    """Build the tree once per module against the real model."""
    import os
    if os.environ.get("OPENKB_LLM_TESTS") != "1" or not os.environ.get("OPENKB_TEST_MODEL"):
        pytest.skip("real-LLM tests are opt-in (OPENKB_LLM_TESTS=1 + OPENKB_TEST_MODEL)")
    from openkb.topic_tree_llm import make_cluster, make_summarize

    model = os.environ["OPENKB_TEST_MODEL"]
    wiki = tmp_path_factory.mktemp("wiki")
    concepts = _seed_concepts(wiki)
    tt.bootstrap(concepts, cluster=make_cluster(model), summarize=make_summarize(model))
    return wiki


def test_single_root(require_llm, built_tree):
    assert (built_tree / "concepts" / tt.TOPIC_FILE).is_file()


def test_at_least_two_layers(require_llm, built_tree):
    concepts = built_tree / "concepts"
    subtopics = [d for d in concepts.iterdir() if d.is_dir()]
    assert subtopics, "real model should produce at least one subtopic (>=2 layers)"


def test_no_concept_lost(require_llm, built_tree):
    concepts = built_tree / "concepts"
    present = {p.stem for p in concepts.rglob("*.md") if p.name != tt.TOPIC_FILE}
    assert present == set(_CONCEPTS)


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

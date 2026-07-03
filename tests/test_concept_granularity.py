"""Tests for concept-granularity tuning (docs/concept-granularity-tuning-plan.md).

Deterministic — no LLM. Covers the concept-altitude prompt guidance, the per-KB
`## Concepts` injection, and the regression pin for reading existing concepts
recursively (so reuse survives `distill` nesting).
"""
import pytest

from openkb.agent.compiler import (
    _CONCEPTS_PLAN_USER, _read_concept_briefs, _read_wiki_context,
)
from openkb.schema import get_agents_section


def _concept(wiki, relpath, stem, desc):
    d = wiki / "concepts" / relpath if relpath else wiki / "concepts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.md").write_text(
        f'---\ntype: "Concept"\ndescription: "{desc}"\n---\n# {stem}\n', encoding="utf-8")


# --- Phase 1: altitude guidance is in the prompt ----------------------------

def test_plan_prompt_has_altitude_guidance():
    assert "Concept altitude" in _CONCEPTS_PLAN_USER
    assert "recur across MANY documents" in _CONCEPTS_PLAN_USER
    # names the synonym-reuse behavior
    assert "different name" in _CONCEPTS_PLAN_USER
    assert "{concepts_guidance}" in _CONCEPTS_PLAN_USER


def test_plan_prompt_formats_with_and_without_guidance():
    for cg in ("", "\nKB-specific concept guidance: keep it broad.\n"):
        s = _CONCEPTS_PLAN_USER.format(
            concept_briefs="- x: y", entity_briefs="- e: f", concepts_guidance=cg,
        ).replace("__ENTITY_TYPES__", "person, other")
        assert '"concept-slug"' in s  # JSON example survived formatting


# --- Phase 2: per-KB ## Concepts guidance -----------------------------------

def test_agents_section_extracts_concepts_and_strips_comments(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "AGENTS.md").write_text(
        "# Wiki Schema\n\n## Concepts\n"
        "<!-- editing note: not sent to model -->\n"
        "Keep concepts at the level of a teachable skill, not individual facts.\n\n"
        "## Enrichment\nx\n", encoding="utf-8")
    g = get_agents_section(wiki, "Concepts")
    assert "teachable skill" in g
    assert "editing note" not in g and "not sent to model" not in g


def test_agents_section_absent_returns_empty(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "AGENTS.md").write_text("# Wiki Schema\n\n## Other\nx\n", encoding="utf-8")
    assert get_agents_section(wiki, "Concepts") == ""


# --- Phase 3: regression pin — existing-concept read is recursive ------------

@pytest.mark.regression
def test_read_concept_briefs_recursive_excludes_topic_and_enrich(tmp_path):
    """After `distill`, concepts are nested and paired with _topic.md / .enrich.md.
    The reuse list must still find nested concepts and must NOT list the topic
    index or enrichment files (else the planner fragments instead of reusing)."""
    wiki = tmp_path / "wiki"
    _concept(wiki, "", "flat-concept", "a flat concept")
    _concept(wiki, "harmony", "chord-tones", "root/third/fifth/seventh")
    _concept(wiki, "harmony/deep", "voice-leading", "smooth movement between chords")
    # noise that must be excluded
    (wiki / "concepts" / "_topic.md").write_text(
        "---\ntype: topic\nsummary: root\n---\n# root\n", encoding="utf-8")
    (wiki / "concepts" / "harmony" / "_topic.md").write_text(
        "---\ntype: topic\nsummary: h\n---\n# harmony\n", encoding="utf-8")
    (wiki / "concepts" / "harmony" / "chord-tones.enrich.md").write_text(
        "---\ntype: Enrichment\nsource_concept: chord-tones\n---\n# x\n", encoding="utf-8")

    briefs = _read_concept_briefs(wiki)
    _, slugs = _read_wiki_context(wiki)

    assert "chord-tones" in briefs and "voice-leading" in briefs and "flat-concept" in briefs
    assert set(slugs) == {"flat-concept", "chord-tones", "voice-leading"}
    assert "_topic" not in slugs and "chord-tones.enrich" not in slugs
    assert "_topic" not in briefs and ".enrich" not in briefs

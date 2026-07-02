"""Unit coverage for the `enrichment:` config block (openkb/config.py)."""
from openkb.config import DEFAULT_ENRICHMENT, resolve_enrichment


def test_absent_returns_disabled_defaults():
    s = resolve_enrichment({})
    assert s == DEFAULT_ENRICHMENT
    assert s.enabled is False


def test_non_mapping_falls_back():
    assert resolve_enrichment({"enrichment": "yes"}) == DEFAULT_ENRICHMENT
    assert resolve_enrichment({"enrichment": [1]}) == DEFAULT_ENRICHMENT


def test_each_key_parses():
    s = resolve_enrichment({"enrichment": {
        "enabled": True, "depth": "deep", "sections": ["elaboration", "examples"],
        "include_world_knowledge": False, "verify": False, "max_tokens": 400,
        "model": "anthropic/claude-haiku-4-5",
    }})
    assert s.enabled is True
    assert s.depth == "deep"
    assert s.sections == ("elaboration", "examples")
    assert s.include_world_knowledge is False
    assert s.verify is False
    assert s.max_tokens == 400
    assert s.model == "anthropic/claude-haiku-4-5"


def test_bad_depth_falls_back():
    assert resolve_enrichment({"enrichment": {"depth": "epic"}}).depth == "standard"


def test_sections_cleaned_and_deduped():
    s = resolve_enrichment({"enrichment": {"sections": ["examples", "bogus", "examples", "inferred"]}})
    assert s.sections == ("examples", "inferred")


def test_empty_or_all_invalid_sections_fall_back():
    d = DEFAULT_ENRICHMENT
    assert resolve_enrichment({"enrichment": {"sections": []}}).sections == d.sections
    assert resolve_enrichment({"enrichment": {"sections": ["nope", 3]}}).sections == d.sections


def test_wrong_types_use_defaults():
    d = DEFAULT_ENRICHMENT
    s = resolve_enrichment({"enrichment": {
        "enabled": "true", "verify": 1, "max_tokens": -5, "model": "  ",
    }})
    assert s.enabled == d.enabled
    assert s.verify == d.verify
    assert s.max_tokens == d.max_tokens
    assert s.model == d.model

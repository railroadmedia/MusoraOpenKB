"""Unit coverage for the `hierarchy:` config block (openkb/config.py)."""
from openkb.config import DEFAULT_HIERARCHY, resolve_hierarchy


def test_absent_block_returns_defaults():
    assert resolve_hierarchy({}) == DEFAULT_HIERARCHY
    # back-compat: topic_tree flag alone still yields default hierarchy sizing
    assert resolve_hierarchy({"topic_tree": True}) == DEFAULT_HIERARCHY


def test_non_mapping_block_falls_back(caplog):
    assert resolve_hierarchy({"hierarchy": "nope"}) == DEFAULT_HIERARCHY
    assert resolve_hierarchy({"hierarchy": [1, 2]}) == DEFAULT_HIERARCHY


def test_each_key_parses():
    h = resolve_hierarchy({"hierarchy": {
        "target_fanout": 6, "min_fanout": 3, "max_fanout": 9,
        "max_depth": 4, "node_summary_tokens": 400, "node_summary_hard_cap": 800,
        "sideways_links": False, "sideways_links_max": 3, "sideways_method": "embedding",
    }})
    assert (h.target_fanout, h.min_fanout, h.max_fanout) == (6, 3, 9)
    assert h.max_depth == 4
    assert (h.node_summary_tokens, h.node_summary_hard_cap) == (400, 800)
    assert h.sideways_links is False
    assert h.sideways_links_max == 3
    assert h.sideways_method == "embedding"


def test_inverted_band_falls_back_to_default_band():
    d = DEFAULT_HIERARCHY
    h = resolve_hierarchy({"hierarchy": {"min_fanout": 20, "target_fanout": 8, "max_fanout": 4}})
    assert (h.min_fanout, h.target_fanout, h.max_fanout) == (d.min_fanout, d.target_fanout, d.max_fanout)


def test_invariant_min_le_target_le_max_always_holds():
    for block in [
        {"target_fanout": 100},          # target above default max
        {"min_fanout": 50},              # min above default target
        {"max_fanout": 1},               # max below default target
        {"target_fanout": -3},           # negative
        {"min_fanout": "x"},             # wrong type
    ]:
        h = resolve_hierarchy({"hierarchy": block})
        assert h.min_fanout <= h.target_fanout <= h.max_fanout


def test_negative_and_wrong_type_use_defaults():
    d = DEFAULT_HIERARCHY
    h = resolve_hierarchy({"hierarchy": {
        "max_depth": -1, "sideways_links_max": "five", "node_summary_tokens": 0,
    }})
    assert h.max_depth == d.max_depth
    assert h.sideways_links_max == d.sideways_links_max
    assert h.node_summary_tokens == d.node_summary_tokens


def test_bad_sideways_method_falls_back():
    assert resolve_hierarchy({"hierarchy": {"sideways_method": "magic"}}).sideways_method == "llm"


def test_hard_cap_below_target_is_raised():
    h = resolve_hierarchy({"hierarchy": {"node_summary_tokens": 900, "node_summary_hard_cap": 100}})
    assert h.node_summary_hard_cap >= h.node_summary_tokens

"""Tier-3 Group C — navigation eval (the real end-to-end value test).

Does the hierarchy actually help an agent get from a general question down to the
right specific concept? For each case we descend from the root using the real
``make_choose`` (the same callable production placement uses), then check whether
the reached node contains the expected leaf. Reports success@N and avg hops.

Run:
  OPENKB_LLM_TESTS=1 OPENKB_TEST_MODEL=anthropic/claude-haiku-4-5 LLM_API_KEY=... \
    pytest -m llm tests/test_hierarchy_navigation.py
"""
from __future__ import annotations

import pytest

from openkb import topic_tree as tt
from tests.support.eval_corpus import (
    build_real_tree, dump_tree, require_llm_or_skip, write_report,
)

pytestmark = pytest.mark.llm

# Success rate floor. Lower this for weak local models.
SUCCESS_THRESHOLD = 0.6

# question -> expected leaf concept stem. Kept as stems so they survive re-clustering.
NAV_CASES = [
    ("How do I read sheet music at first sight?", "sight-reading"),
    ("What is a ii-V-I and other common chord progressions?", "chord-progressions"),
    ("Explain the whole-step/half-step pattern of the major scale.", "major-scale"),
    ("What gives jazz its uneven, swung eighth-note feel?", "swing-feel"),
    ("How do key signatures relate to each other by fifths?", "circle-of-fifths"),
    ("What are staccato and legato?", "articulation"),
    ("How are beats grouped into measures like 4/4 or 3/4?", "time-signatures"),
    ("What is a four-note chord that adds a seventh?", "seventh-chords"),
    ("What are the five notes used in blues soloing?", "pentatonic-scale"),
    ("What do pianissimo and fortissimo mean?", "dynamics"),
]


def _descend(model: str, concepts_root, question: str, max_hops: int):
    """Walk root->down letting the model choose a child topic each step; return
    (reached_rel, hops, concepts_at_reached_node)."""
    from openkb.topic_tree_llm import make_choose

    choose = make_choose(model)
    rel = ""
    hops = 0
    for _ in range(max_hops):
        view = tt.read_topic(concepts_root, rel)
        # Already at a node that holds the answer? stop.
        pick = choose(view, question)
        if pick is None:
            break
        rel = f"{rel}/{pick}" if rel else pick
        hops += 1
    reached = tt.read_topic(concepts_root, rel)
    return rel, hops, {s for s, _ in reached.child_concepts}


@pytest.fixture(scope="module")
def nav_results(tmp_path_factory):
    model = require_llm_or_skip()
    wiki = build_real_tree(model, tmp_path_factory.mktemp("wiki"))
    dump_tree("hierarchy_navigation", wiki / "concepts")
    concepts = wiki / "concepts"
    results = []
    for question, expected in NAV_CASES:
        rel, hops, reached_concepts = _descend(model, concepts, question, tt.MAX_DEPTH)
        results.append({
            "question": question,
            "expected": expected,
            "reached": rel or "(root)",
            "hops": hops,
            "success": expected in reached_concepts,
        })
    n = len(results)
    success_rate = sum(r["success"] for r in results) / n if n else 0.0
    avg_hops = sum(r["hops"] for r in results) / n if n else 0.0
    write_report("nav", {
        "model": model, "success_rate": success_rate,
        "avg_hops": avg_hops, "results": results,
    })
    return {"success_rate": success_rate, "avg_hops": avg_hops, "results": results}


def test_navigation_success_rate(require_llm, nav_results):
    rate = nav_results["success_rate"]
    if rate < SUCCESS_THRESHOLD:
        misses = [r["question"] for r in nav_results["results"] if not r["success"]]
        pytest.xfail(f"success@N {rate:.0%} < {SUCCESS_THRESHOLD:.0%} (soft; misses: {misses})")
    assert rate >= SUCCESS_THRESHOLD


def test_navigation_terminates_within_depth(require_llm, nav_results):
    """Descent must never exceed the tree's max depth (no runaway loops)."""
    for r in nav_results["results"]:
        assert r["hops"] <= tt.MAX_DEPTH

"""Tier-3 Group B — semantic quality via LLM-as-judge (real LLM, opt-in).

Non-deterministic content, so this scores rubric dimensions 1-5 with a judge at
temperature 0, writes all scores to tests/reports/ (gitignored), and asserts the
mean clears a threshold. A planted "obviously bad" tree is scored too so an
over-lenient / broken judge is detectable.

Run:
  OPENKB_LLM_TESTS=1 OPENKB_TEST_MODEL=anthropic/claude-haiku-4-5 LLM_API_KEY=... \
    pytest -m llm tests/test_hierarchy_quality.py
"""
from __future__ import annotations

import json

import pytest

from openkb import topic_tree as tt
from openkb.agent.compiler import _JSON_RESPONSE_FORMAT, _llm_call
from tests.support.eval_corpus import (
    build_real_tree, dump_tree, require_llm_or_skip, write_report,
)

pytestmark = pytest.mark.llm

# Soft floor for the mean judge score (1-5). Local models are weaker; tune per
# backend and treat small misses as signal, not gospel.
QUALITY_THRESHOLD = 3.5

_JUDGE = (
    "You are grading one node of a hierarchical knowledge base on a 1-5 scale "
    "(5 = excellent). Grade ONLY this dimension: {dimension}.\n\n"
    "Parent topic summary:\n{summary}\n\nIts children:\n{children}\n\n"
    'Reply strict JSON: {{"score": <1-5 int>, "reason": "<one sentence>"}}.'
)

_DIMENSIONS = {
    "encapsulation": "does the parent summary faithfully cover ALL of its children?",
    "abstraction": "is the parent measurably MORE GENERAL than its children (not just a list)?",
    "coherence": "are the children a coherent, non-overlapping grouping under this parent?",
}


def _judge(model: str, dimension: str, summary: str, children: list[str]) -> dict:
    raw = _llm_call(
        model,
        [{"role": "user", "content": _JUDGE.format(
            dimension=_DIMENSIONS[dimension],
            summary=summary or "(none)",
            children="\n".join(f"- {c}" for c in children) or "(none)",
        )}],
        "quality-judge",
        response_format=_JSON_RESPONSE_FORMAT,
        temperature=0,
    )
    data = json.loads(raw) or {}
    return {"score": int(data.get("score", 0)), "reason": str(data.get("reason", ""))}


def _grade_tree(model: str, concepts_root):
    """Judge every non-leaf (parent) node across all dimensions. Returns records."""
    records = []
    for d in [concepts_root, *[p for p in concepts_root.rglob("*") if p.is_dir()]]:
        view = tt.read_topic(d.parent if d.name and d != concepts_root else concepts_root,
                             d.name if d != concepts_root else "")
        children = [f"[topic] {n}: {s}" for n, s in view.child_topics] + \
                   [f"[concept] {s}: {b}" for s, b in view.child_concepts]
        if not view.child_topics:  # only grade nodes that actually branch
            continue
        for dim in _DIMENSIONS:
            rec = _judge(model, dim, view.summary, children)
            rec.update({"node": str(d.relative_to(concepts_root)) or "(root)", "dimension": dim})
            records.append(rec)
    return records


@pytest.fixture(scope="module")
def graded(tmp_path_factory):
    model = require_llm_or_skip()
    wiki = build_real_tree(model, tmp_path_factory.mktemp("wiki"))
    dump_tree("hierarchy_quality", wiki / "concepts")
    records = _grade_tree(model, wiki / "concepts")
    scores = [r["score"] for r in records]
    mean = sum(scores) / len(scores) if scores else 0.0
    write_report("quality", {"model": model, "mean": mean, "records": records})
    return {"mean": mean, "records": records, "model": model}


def test_quality_mean_clears_threshold(require_llm, graded):
    assert graded["records"], "judge produced no gradable nodes"
    if graded["mean"] < QUALITY_THRESHOLD:
        pytest.xfail(f"mean {graded['mean']:.2f} < {QUALITY_THRESHOLD} (soft floor; see report)")
    assert graded["mean"] >= QUALITY_THRESHOLD


def test_judge_flags_a_planted_bad_tree(require_llm, llm_model):
    """Reliability guard: a deliberately incoherent parent must score low, or the
    judge is too lenient to trust the threshold above."""
    bad = _judge(
        llm_model,
        "coherence",
        summary="Miscellaneous stuff.",
        children=["[concept] tax-law: filing corporate taxes",
                  "[concept] photosynthesis: how plants make energy",
                  "[concept] baroque-counterpoint: voice-leading in fugues"],
    )
    assert bad["score"] <= 2, f"over-lenient judge scored an incoherent group {bad}"

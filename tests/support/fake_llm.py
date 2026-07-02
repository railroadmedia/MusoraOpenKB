"""Deterministic fake LLM for integration tests.

Patches the single seam ``openkb.agent.compiler._llm_call`` and returns canned
but **structurally valid** responses keyed by ``step_name``, so the real
production code paths (JSON parsing, cluster cleanup, tree building) run without
a network. Responses are a pure function of the prompt, so runs are reproducible.

Currently routes the topic-tree steps (``topic-cluster``, ``topic-summary``,
``topic-choose``). Extend the router as more steps need fake coverage.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from unittest.mock import patch

# The cluster/choose prompts list concepts as lines like "- <stem>: <brief>".
_STEM_LINE = re.compile(r"^- ([^:\n]+):", re.MULTILINE)


def _stems(prompt: str) -> list[str]:
    return [m.group(1).strip() for m in _STEM_LINE.finditer(prompt)]


def fake_llm_call(model, messages, step_name, **kwargs):
    """Drop-in replacement for ``compiler._llm_call``."""
    prompt = messages[-1]["content"] if messages else ""

    if step_name == "topic-cluster":
        stems = _stems(prompt)
        half = len(stems) // 2 or len(stems)
        groups = {}
        if stems[:half]:
            groups["group-a"] = stems[:half]
        if stems[half:]:
            groups["group-b"] = stems[half:]
        return json.dumps({"groups": groups})

    if step_name == "topic-summary":
        # summarize(name, briefs) — the name is quoted in the prompt.
        m = re.search(r'subtopic "([^"]+)"', prompt)
        name = m.group(1) if m else "topic"
        return f"summary of {name}"

    if step_name == "topic-choose":
        return json.dumps({"pick": None})  # keep at current node

    raise AssertionError(f"fake_llm has no route for step_name={step_name!r}")


@contextmanager
def fake_llm():
    """Patch the LLM seam with the deterministic router.

    ``topic_tree_llm`` does ``from ...compiler import _llm_call`` so it holds its
    own reference; patch both the origin and that binding so every caller routes
    through the fake.
    """
    with patch("openkb.agent.compiler._llm_call", side_effect=fake_llm_call), \
            patch("openkb.topic_tree_llm._llm_call", side_effect=fake_llm_call) as m:
        yield m

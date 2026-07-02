"""LLM-backed decision callables for the topic-tree engine.

These are injected into the pure engine (``openkb.topic_tree``) so the engine
stays unit-testable without a network. Production code wires these in.
"""
from __future__ import annotations

import json

from openkb.agent.compiler import _JSON_RESPONSE_FORMAT, _llm_call
from openkb.topic_tree import FANOUT_K, TopicNodeView

_CHOOSE = (
    "You are placing a new concept into a topic tree. Given the current node's "
    "summary and its child topics, choose the ONE child topic the concept best "
    "belongs under, or null to keep it at this node. "
    'Reply JSON: {{"pick": <name|null>}}.\n\n'
    "Node summary: {summary}\nChild topics:\n{topics}\n\nConcept: {brief}"
)


def make_choose(model: str):
    def choose(view: TopicNodeView, brief: str):
        topics = "\n".join(f"- {n}: {s}" for n, s in view.child_topics) or "(none)"
        raw = _llm_call(
            model,
            [{"role": "user", "content": _CHOOSE.format(
                summary=view.summary, topics=topics, brief=brief)}],
            "topic-choose",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        pick = (json.loads(raw) or {}).get("pick")
        valid = {n for n, _ in view.child_topics}
        return pick if pick in valid else None

    return choose


_CLUSTER = (
    "Cluster these concepts into 2-{kmax} coherent subtopics. Reply JSON: "
    '{{"groups": {{"<subtopic-kebab-name>": ["<stem>", ...]}}}}. '
    "Every stem must appear exactly once.\n\nConcepts:\n{items}"
)


def make_cluster(model: str):
    def cluster(items):
        listing = "\n".join(f"- {stem}: {brief}" for stem, brief in items)
        raw = _llm_call(
            model,
            [{"role": "user", "content": _CLUSTER.format(
                kmax=max(2, FANOUT_K // 2), items=listing)}],
            "topic-cluster",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        groups = (json.loads(raw) or {}).get("groups", {})
        known = {s for s, _ in items}
        seen: set[str] = set()
        clean: dict[str, list[str]] = {}
        for name, stems in groups.items():
            kept = [s for s in stems if s in known and s not in seen]
            seen.update(kept)
            if kept:
                clean[name] = kept
        missing = [s for s in known if s not in seen]
        if missing:
            clean.setdefault("misc", []).extend(missing)
        return clean

    return cluster


_SUMMARIZE = (
    'Write a one-paragraph summary of the subtopic "{name}" that abstracts '
    "these concept briefs:\n{briefs}"
)


def make_summarize(model: str):
    def summarize(name: str, briefs: list[str]) -> str:
        raw = _llm_call(
            model,
            [{"role": "user", "content": _SUMMARIZE.format(
                name=name, briefs="\n".join(f"- {b}" for b in briefs))}],
            "topic-summary",
        )
        return raw.strip()

    return summarize


# ---------------------------------------------------------------------------
# Bottom-up distillation callables (Pass 2). These take the KB's AGENTS.md
# guidance so invented categories reflect the KB's purpose, plus the fan-out
# band so layers stay well-sized. See docs/smart-hierarchy-distillation-plan.md.
# ---------------------------------------------------------------------------

_GUIDANCE_PREFIX = (
    "This knowledge base is themed as follows — let it steer how you name and "
    "group categories (general/thematic at the top, specific at the leaves):\n"
    "{guidance}\n\n"
)

_DISTILL_CLUSTER = (
    "Group the items below into coherent, well-named categories for one layer of "
    "a knowledge hierarchy. Aim for about {target} categories (between {lo} and "
    "{hi}); each category should be a general theme that encapsulates its members. "
    "Name each category in short kebab-case. Every item stem must appear in "
    'exactly one category. Reply JSON: {{"groups": {{"<kebab-name>": ["<stem>", '
    '...]}}}}.\n\n{guidance}Items:\n{items}'
)

_RELATE = (
    "Below are sibling nodes at one layer of a knowledge hierarchy. For each "
    "node, list up to {k} OTHER nodes it is most conceptually related to "
    "(cross-links for lateral navigation). Only use the names given; never link "
    'a node to itself. Reply JSON: {{"related": {{"<name>": ["<name>", ...]}}}}.'
    "\n\n{guidance}Nodes:\n{items}"
)


def _guidance_block(guidance: str | None) -> str:
    guidance = (guidance or "").strip()
    return _GUIDANCE_PREFIX.format(guidance=guidance) if guidance else ""


def make_distill_cluster(model: str, *, guidance: str = "",
                         target_fanout: int = 8, min_fanout: int = 4,
                         max_fanout: int = 12):
    """Cluster + NAME sized categories, AGENTS.md-guided. Returns name->stems,
    with every input stem placed exactly once (leftovers land in ``misc``)."""
    def cluster(items):
        listing = "\n".join(f"- {stem}: {brief}" for stem, brief in items)
        raw = _llm_call(
            model,
            [{"role": "user", "content": _DISTILL_CLUSTER.format(
                target=target_fanout, lo=min_fanout, hi=max_fanout,
                guidance=_guidance_block(guidance), items=listing)}],
            "distill-cluster",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        groups = (json.loads(raw) or {}).get("groups", {})
        known = {s for s, _ in items}
        seen: set[str] = set()
        clean: dict[str, list[str]] = {}
        for name, stems in groups.items():
            kept = [s for s in stems if s in known and s not in seen]
            seen.update(kept)
            if kept:
                clean[name] = kept
        missing = [s for s in known if s not in seen]
        if missing:
            clean.setdefault("misc", []).extend(missing)
        return clean

    return cluster


def make_distill_summarize(model: str, *, guidance: str = ""):
    """Encapsulating parent summary, AGENTS.md-guided."""
    def summarize(name: str, briefs: list[str]) -> str:
        raw = _llm_call(
            model,
            [{"role": "user", "content": _guidance_block(guidance) + _SUMMARIZE.format(
                name=name, briefs="\n".join(f"- {b}" for b in briefs))}],
            "distill-summary",
        )
        return raw.strip()

    return summarize


def make_relate(model: str, *, guidance: str = "", k: int = 5):
    """Compute top-K same-layer related peers. Returns name->[related names]."""
    def relate(nodes):
        listing = "\n".join(f"- {name}: {summary}" for name, summary in nodes)
        raw = _llm_call(
            model,
            [{"role": "user", "content": _RELATE.format(
                k=k, guidance=_guidance_block(guidance), items=listing)}],
            "distill-relate",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        related = (json.loads(raw) or {}).get("related", {})
        known = {n for n, _ in nodes}
        clean: dict[str, list[str]] = {}
        for name, peers in related.items():
            if name not in known:
                continue
            kept = [p for p in peers if p in known and p != name][:k]
            if kept:
                clean[name] = kept
        return clean

    return relate

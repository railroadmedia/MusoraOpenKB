"""Generic hierarchical-index engine over a page collection.

A topic node is a directory containing a ``_topic.md`` (summary + size).
Children are derived from the directory: subdirectories are child topics,
``*.md`` files (except ``_topic.md``) are concept leaves. The POC wires
this to ``wiki/concepts/`` only; entities/documents can reuse it later by
passing different callables.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from openkb.locks import atomic_write_text

FANOUT_K = 10
MAX_DEPTH = 6
TOPIC_FILE = "_topic.md"
ENRICH_SUFFIX = ".enrich.md"  # paired enrichment files (see openkb/agent/enricher.py)


def _is_concept_md(path: Path) -> bool:
    """A real concept leaf — not the topic index and not a paired enrichment file."""
    return path.name != TOPIC_FILE and not path.name.endswith(ENRICH_SUFFIX)


@dataclass
class TopicNodeView:
    summary: str
    child_topics: list[tuple[str, str]] = field(default_factory=list)  # (name, summary)
    child_concepts: list[tuple[str, str]] = field(default_factory=list)  # (stem, brief)


def _frontmatter(md: Path) -> dict:
    if not md.is_file():
        return {}
    text = md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _brief(concept_md: Path) -> str:
    return str(_frontmatter(concept_md).get("description", "")).strip()


def write_topic_md(node_dir: Path, summary: str, size: int) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    # Dump the frontmatter as a mapping (not a bare scalar) so PyYAML never
    # emits a ``...`` document-end marker that would corrupt the block, and
    # multi-line summaries are properly escaped/round-tripped.
    fm = yaml.safe_dump(
        {"type": "topic", "summary": summary, "size": int(size)},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    body = f"---\n{fm}\n---\n\n# {node_dir.name or 'root'}\n\n{summary}\n"
    atomic_write_text(node_dir / TOPIC_FILE, body)


def child_count(node_dir: Path) -> int:
    subtopics = [d for d in node_dir.iterdir() if d.is_dir()]
    concepts = [f for f in node_dir.glob("*.md") if _is_concept_md(f)]
    return len(subtopics) + len(concepts)


def read_topic(concepts_root: Path, rel: str = "") -> TopicNodeView:
    node_dir = concepts_root if not rel else concepts_root / rel
    summary = str(_frontmatter(node_dir / TOPIC_FILE).get("summary", "")).strip()
    child_topics: list[tuple[str, str]] = []
    child_concepts: list[tuple[str, str]] = []
    if node_dir.is_dir():
        for child in sorted(node_dir.iterdir()):
            if child.is_dir():
                sub_sum = str(_frontmatter(child / TOPIC_FILE).get("summary", "")).strip()
                child_topics.append((child.name, sub_sum))
            elif child.suffix == ".md" and _is_concept_md(child):
                child_concepts.append((child.stem, _brief(child)))
    return TopicNodeView(
        summary=summary, child_topics=child_topics, child_concepts=child_concepts
    )


ChooseFn = Callable[[TopicNodeView, str], Optional[str]]


def place_concept(
    concepts_root: Path,
    stem: str,
    brief: str,
    content: str,
    *,
    choose: ChooseFn,
    on_overflow: Optional[Callable[[Path], None]] = None,
) -> Path:
    """Descend from the root, letting ``choose`` pick a child topic at each
    level, until it returns None; drop the concept as a leaf there.

    Cost is O(depth) ``choose`` calls. ``on_overflow`` (if given) fires on
    the landing node when its direct-child count exceeds ``FANOUT_K``.
    """
    rel = ""
    for _ in range(MAX_DEPTH):
        view = read_topic(concepts_root, rel)
        pick = choose(view, brief)
        if pick is None:
            break
        if pick not in {t for t, _ in view.child_topics}:
            break  # choose returned a non-existent child; stop here defensively
        rel = f"{rel}/{pick}" if rel else pick
    node_dir = concepts_root if not rel else concepts_root / rel
    node_dir.mkdir(parents=True, exist_ok=True)
    path = node_dir / f"{stem}.md"
    atomic_write_text(path, content)
    if on_overflow is not None and child_count(node_dir) > FANOUT_K:
        on_overflow(node_dir)
    return path


ClusterFn = Callable[[list[tuple[str, str]]], dict[str, list[str]]]
SummarizeFn = Callable[[str, list[str]], str]


def split_node(node_dir: Path, *, cluster: ClusterFn, summarize: SummarizeFn) -> None:
    """Cluster a node's direct concept leaves into subtopics and move them in.

    Files are moved (``Path.replace``), not copied; because wikilinks resolve
    by bare stem, links to moved concepts keep resolving.
    """
    view = read_topic(node_dir.parent if node_dir.name else node_dir, node_dir.name)
    leaves = {stem: brief for stem, brief in view.child_concepts}
    if not leaves:
        return
    groups = cluster(list(leaves.items()))
    for sub_name, stems in groups.items():
        if not stems:
            continue
        sub_dir = node_dir / sub_name
        sub_dir.mkdir(parents=True, exist_ok=True)
        write_topic_md(
            sub_dir, summarize(sub_name, [leaves.get(s, "") for s in stems]), len(stems)
        )
        for stem in stems:
            src = node_dir / f"{stem}.md"
            if src.is_file():
                src.replace(sub_dir / f"{stem}.md")
    # refresh the split node's own summary/size
    new_view = read_topic(node_dir.parent if node_dir.name else node_dir, node_dir.name)
    size = len(new_view.child_topics) + len(new_view.child_concepts)
    write_topic_md(node_dir, view.summary or node_dir.name, size)


def place_topic_dir(concepts_root: Path, *, brief: str, choose: ChooseFn) -> Path:
    """Descend with ``choose`` and return the landing topic directory WITHOUT
    writing a concept file. Lets the caller own the concept-page format (e.g.
    the compiler's ``_write_concept``) while the tree owns placement."""
    rel = ""
    for _ in range(MAX_DEPTH):
        view = read_topic(concepts_root, rel)
        pick = choose(view, brief)
        if pick is None or pick not in {t for t, _ in view.child_topics}:
            break
        rel = f"{rel}/{pick}" if rel else pick
    node = concepts_root if not rel else concepts_root / rel
    node.mkdir(parents=True, exist_ok=True)
    return node


def _build_subtree(
    node_dir: Path,
    items: list[tuple[str, str, str]],  # (stem, brief, content)
    cluster: ClusterFn,
    summarize: SummarizeFn,
    depth: int,
) -> int:
    """Recursively build a topic subtree under ``node_dir`` (already created,
    with its ``_topic.md``). Clusters the full item set at this level, recurses
    into any group still larger than ``FANOUT_K``, and writes leaves otherwise."""
    if len(items) <= FANOUT_K or depth >= MAX_DEPTH:
        for stem, _brief_, content in items:
            atomic_write_text(node_dir / f"{stem}.md", content)
        return len(items)

    briefs = {stem: b for stem, b, _ in items}
    contents = {stem: c for stem, _, c in items}
    groups = cluster([(s, briefs[s]) for s in contents])

    placed = 0
    seen: set[str] = set()
    for name, stems in groups.items():
        kept = [s for s in stems if s in contents and s not in seen]
        if not kept:
            continue
        seen.update(kept)
        sub = node_dir / name
        sub.mkdir(parents=True, exist_ok=True)
        write_topic_md(sub, summarize(name, [briefs[s] for s in kept]), len(kept))
        placed += _build_subtree(
            sub, [(s, briefs[s], contents[s]) for s in kept], cluster, summarize, depth + 1
        )
    # Any concept the clusterer dropped stays as a leaf at this node.
    for s in [s for s in contents if s not in seen]:
        atomic_write_text(node_dir / f"{s}.md", contents[s])
        placed += 1
    return placed


def bootstrap(
    concepts_root: Path,
    *,
    cluster: ClusterFn,
    summarize: SummarizeFn,
) -> int:
    """Build a topic tree over the existing flat concepts under ``concepts_root``.

    Top-down, global cold-start seed: cluster the FULL concept set into top
    topics, recurse into any topic still over ``FANOUT_K``. Building from the
    whole set (rather than greedily one-by-one) avoids freezing the high-level
    taxonomy on early-arriving concepts. Returns the number placed.
    """
    concepts_root.mkdir(parents=True, exist_ok=True)
    # Deterministic order; read all into memory. The flat leaves are the source
    # of truth and are NOT removed until the tree is built successfully — build
    # into a staging dir first so a mid-build failure (e.g. the LLM clusterer
    # raising) cannot destroy the concepts.
    flat = sorted(p for p in concepts_root.glob("*.md") if _is_concept_md(p))
    items = [(p.stem, _brief(p), p.read_text(encoding="utf-8")) for p in flat]

    staging = concepts_root.parent / f".{concepts_root.name}.rebuild"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        placed = _build_subtree(staging, items, cluster, summarize, depth=0)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise  # originals on disk are untouched

    # Build succeeded: now it is safe to swap the staged tree in for the flat
    # leaves. Preserve an existing root _topic.md (only seed the default one).
    for p in flat:
        p.unlink()
    if not (concepts_root / TOPIC_FILE).exists():
        write_topic_md(concepts_root, "Knowledge base topics.", 0)
    for child in sorted(staging.iterdir()):
        dest = concepts_root / child.name
        if dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists():
            dest.unlink()
        child.replace(dest)
    shutil.rmtree(staging, ignore_errors=True)
    return placed


# ---------------------------------------------------------------------------
# Bottom-up distillation (Pass 2) — RAPTOR-style. See
# docs/smart-hierarchy-distillation-plan.md.
#
# Distill builds the hierarchy UPWARD from the flat concept leaves: cluster the
# current layer into LLM-named, sized categories, summarize each category into a
# parent pathway node, link peers sideways, and repeat until a single root
# remains. Leaf concept files are the source of truth and are only replaced once
# the whole tree builds successfully (atomic staging swap).
# ---------------------------------------------------------------------------

RelateFn = Callable[[list[tuple[str, str]]], dict[str, list[str]]]


@dataclass
class _DistillNode:
    name: str
    summary: str
    children: list["_DistillNode"] = field(default_factory=list)
    content: Optional[str] = None  # leaf file body; None for internal nodes
    brief: str = ""                # one-line brief shown in a parent's child index
    related: list[str] = field(default_factory=list)
    # Sidecar files that travel with a leaf (e.g. its <stem>.enrich.md), so the
    # pairing survives distill's atomic rebuild. filename -> content.
    attachments: dict[str, str] = field(default_factory=dict)

    @property
    def is_leaf(self) -> bool:
        return not self.children


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return s or "topic"


def _unique_name(base: str, used: set[str]) -> str:
    base = _slug(base)
    if base not in used:
        used.add(base)
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    name = f"{base}-{i}"
    used.add(name)
    return name


def _truncate_words(text: str, cap: int) -> str:
    words = text.split()
    if len(words) <= cap:
        return text
    return " ".join(words[:cap]).rstrip() + " …"


def _first_sentence(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    m = re.search(r"(.+?[.!?])(\s|$)", text)
    brief = m.group(1) if m else text
    return brief if len(brief) <= limit else brief[: limit - 1].rstrip() + "…"


def _read_leaves(concepts_root: Path) -> list[_DistillNode]:
    """All concept leaves anywhere under the root (so re-distill rebuilds from
    an existing tree too), deterministic by relative path."""
    leaves = []
    for p in sorted(concepts_root.rglob("*.md"), key=lambda x: str(x).lower()):
        if not _is_concept_md(p):
            continue
        brief = _brief(p)
        node = _DistillNode(
            name=p.stem, summary=brief or p.stem,
            content=p.read_text(encoding="utf-8"), brief=brief,
        )
        # Carry the paired enrichment file (if any) so it stays co-located after rebuild.
        sp = p.with_name(p.stem + ENRICH_SUFFIX)
        if sp.is_file():
            node.attachments[sp.name] = sp.read_text(encoding="utf-8")
        leaves.append(node)
    return leaves


def write_pathway_md(
    node_dir: Path,
    summary: str,
    layer: int,
    children: list[tuple[str, str, bool]],  # (name, brief, is_topic)
    related: list[str],
    *,
    title: Optional[str] = None,
) -> None:
    """Write a pathway node: frontmatter (layer/children/related) + distilled
    summary + a linked child index + a related-pathways section.

    ``title`` overrides the heading (the root is materialized in a staging dir
    whose name must not leak into the heading — pass the logical node name)."""
    node_dir.mkdir(parents=True, exist_ok=True)
    fm = yaml.safe_dump(
        {
            "type": "topic",
            "layer": int(layer),
            "size": len(children),
            "summary": summary,
            "children": [c[0] for c in children],
            "related": list(related),
        },
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    heading = title or node_dir.name or "root"
    parts = [f"---\n{fm}\n---\n", f"# {heading}\n", summary, ""]
    if children:
        parts.append("## Contents\n")
        for name, brief, _is_topic in children:
            parts.append(f"- [[{name}]]" + (f" — {brief}" if brief else ""))
        parts.append("")
    if related:
        parts.append("## Related pathways\n")
        for r in related:
            parts.append(f"- [[{r}]]")
        parts.append("")
    atomic_write_text(node_dir / TOPIC_FILE, "\n".join(parts).rstrip() + "\n")


def _build_parent_layer(
    nodes: list[_DistillNode],
    cluster: ClusterFn,
    summarize: SummarizeFn,
    used: set[str],
    cap: Callable[[str], str],
) -> list[_DistillNode]:
    """Cluster ``nodes`` into named categories and summarize each into a parent.
    Any node the clusterer drops is collected into a ``misc`` parent so nothing
    is ever lost."""
    by_name = {n.name: n for n in nodes}
    groups = cluster([(n.name, n.brief or n.summary) for n in nodes])
    parents: list[_DistillNode] = []
    seen: set[str] = set()
    for cat, members in groups.items():
        kept = [by_name[m] for m in members if m in by_name and m not in seen]
        if not kept:
            continue
        seen.update(m.name for m in kept)
        name = _unique_name(cat, used)
        summary = cap(summarize(name, [m.summary for m in kept]))
        parents.append(_DistillNode(
            name=name, summary=summary, children=kept, brief=_first_sentence(summary),
        ))
    leftovers = [n for n in nodes if n.name not in seen]
    if leftovers:
        name = _unique_name("misc", used)
        summary = cap(summarize(name, [m.summary for m in leftovers]))
        parents.append(_DistillNode(
            name=name, summary=summary, children=leftovers, brief=_first_sentence(summary),
        ))
    return parents


def _link_sideways(parents: list[_DistillNode], relate: Optional[RelateFn], k: int) -> None:
    """Add bidirectional, same-layer ``related`` links (top-K per node)."""
    if relate is None or k <= 0 or len(parents) < 2:
        return
    by_name = {p.name: p for p in parents}
    names = set(by_name)
    rel = relate([(p.name, p.summary) for p in parents]) or {}
    pairs: set[frozenset] = set()
    for a, others in rel.items():
        if a not in names:
            continue
        for b in others or []:
            if b in names and b != a:
                pairs.add(frozenset((a, b)))
    # Only add a pair when BOTH endpoints have room, keeping links strictly
    # bidirectional and each node's list <= k. Deterministic order.
    for pair in sorted(pairs, key=lambda s: sorted(s)):
        a, b = sorted(pair)
        if b in by_name[a].related:
            continue
        if len(by_name[a].related) < k and len(by_name[b].related) < k:
            by_name[a].related.append(b)
            by_name[b].related.append(a)
    for p in parents:
        p.related.sort()


def _materialize(node: _DistillNode, node_dir: Path, layer: int) -> None:
    node_dir.mkdir(parents=True, exist_ok=True)
    child_index: list[tuple[str, str, bool]] = []
    for child in node.children:
        if child.is_leaf:
            atomic_write_text(node_dir / f"{child.name}.md",
                              child.content or f"# {child.name}\n")
            for fname, fcontent in child.attachments.items():
                atomic_write_text(node_dir / fname, fcontent)
            child_index.append((child.name, child.brief, False))
        else:
            _materialize(child, node_dir / child.name, layer + 1)
            child_index.append((child.name, child.brief, True))
    write_pathway_md(node_dir, node.summary, layer, child_index, node.related,
                     title=node.name)


def _tree_layers(node: _DistillNode) -> int:
    if node.is_leaf:
        return 1
    return 1 + max(_tree_layers(c) for c in node.children)


def _collapse_single_child(node: _DistillNode) -> _DistillNode:
    """Collapse redundant single-child internal nodes bottom-up: a node whose only
    child is itself an internal (topic) node is a wasted layer that just restates
    that child, so it is replaced by the child. Never collapses a node down to a
    bare leaf (a topic with a single concept leaf is kept), so the root always
    retains children and the >= 2-layer invariant holds."""
    node.children = [_collapse_single_child(c) for c in node.children]
    while len(node.children) == 1 and not node.children[0].is_leaf:
        node = node.children[0]
    return node


def _internal_names(node: _DistillNode) -> set[str]:
    if node.is_leaf:
        return set()
    names = {node.name}
    for c in node.children:
        names |= _internal_names(c)
    return names


def _prune_related(node: _DistillNode, valid: set[str]) -> None:
    """Drop any sideways ``related`` link that no longer resolves to an existing
    node (e.g. a peer that was collapsed away) so no ghost links remain."""
    if node.is_leaf:
        return
    node.related = [r for r in node.related if r in valid and r != node.name]
    for c in node.children:
        _prune_related(c, valid)


def distill(
    concepts_root: Path,
    *,
    cluster: ClusterFn,
    summarize: SummarizeFn,
    relate: Optional[RelateFn] = None,
    target_fanout: int = 8,
    min_fanout: int = 4,
    max_fanout: int = 12,
    max_depth: int = 6,
    sideways_links_max: int = 5,
    summary_hard_cap: int = 1000,
) -> dict:
    """Distil the flat concept leaves under ``concepts_root`` into a bottom-up
    pathway hierarchy. Returns ``{"leaves", "layers", "nodes"}`` stats.

    Invariants: exactly one root file; always >= 2 layers (root + at least the
    leaf layer). Leaf files are the source of truth — the tree is built into a
    staging dir and swapped in only on success, so a mid-build LLM failure never
    loses concepts. ``target_fanout`` / ``min_fanout`` / ``max_fanout`` are
    passed to the clusterer (advisory); ``max_depth`` bounds the number of
    parent layers built.
    """
    concepts_root = Path(concepts_root)
    concepts_root.mkdir(parents=True, exist_ok=True)
    leaves = _read_leaves(concepts_root)

    # Empty KB: just (re)seed a root topic, nothing to distil.
    if not leaves:
        write_pathway_md(concepts_root, "Knowledge base topics.", 1, [], [])
        return {"leaves": 0, "layers": 1, "nodes": 1}

    used: set[str] = {n.name for n in leaves}
    cap = lambda text: _truncate_words(text, summary_hard_cap)  # noqa: E731

    current = leaves
    built = 0
    # Build one parent layer per iteration. Reserve one depth level for the root.
    while len(current) > 1 and built < max_depth - 1:
        parents = _build_parent_layer(current, cluster, summarize, used, cap)
        if len(parents) >= len(current):
            break  # no distillation progress; collapse survivors into root below
        _link_sideways(parents, relate, sideways_links_max)
        current = parents
        built += 1

    if len(current) == 1 and not current[0].is_leaf:
        root = current[0]
    else:
        # Wrap the survivors (leaves and/or top-layer nodes) in a single root so
        # there is always exactly one root and always >= 2 layers.
        root_name = _unique_name("root", used)
        root = _DistillNode(
            name=root_name,
            summary=cap(summarize(root_name, [c.summary for c in current])),
            children=list(current),
            brief="",
        )

    # Remove redundant single-child wrapper layers, then repair sideways links
    # so none point at a node that was collapsed away.
    root = _collapse_single_child(root)
    _prune_related(root, _internal_names(root))

    # Materialize into staging, then atomically swap for the current contents.
    staging = concepts_root.parent / f".{concepts_root.name}.distill"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _materialize(root, staging, layer=1)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise  # leaves on disk untouched

    for child in list(concepts_root.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in sorted(staging.iterdir()):
        child.replace(concepts_root / child.name)
    shutil.rmtree(staging, ignore_errors=True)

    def _count(node: _DistillNode) -> int:
        return 1 + sum(_count(c) for c in node.children)

    return {"leaves": len(leaves), "layers": _tree_layers(root), "nodes": _count(root)}

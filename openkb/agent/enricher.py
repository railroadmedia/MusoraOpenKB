"""Generative enrichment (Pass 3) — the enrichment layer.

Authors a paired ``<stem>.enrich.md`` for each grounded concept leaf: grounded
*elaboration* plus verify-gated *inferred* world knowledge, examples, and
cross-links. The grounded concept file is the source of truth and is NEVER
modified here; enrichment files are machine-owned and regenerable. See
docs/enrichment-layer-plan.md.

The engine (:func:`enrich`) takes injected ``generate`` / ``verify`` callables so
it stays unit-testable without a network; production wires the LLM-backed ones
(:func:`make_generate`, :func:`make_verify`).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Optional

import yaml

from openkb.agent.compiler import _JSON_RESPONSE_FORMAT, _llm_call
from openkb.locks import atomic_write_text

ENRICH_SUFFIX = ".enrich.md"
_SECTION_TITLES = {
    "elaboration": "Elaboration",
    "inferred": "Inferred context",
    "examples": "Examples",
    "see_also": "See also",
}
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def is_enrich_file(path: Path) -> bool:
    return path.name.endswith(ENRICH_SUFFIX)


def enrich_path_for(concept_md: Path) -> Path:
    """foo.md -> foo.enrich.md (same directory)."""
    return concept_md.with_name(concept_md.stem + ENRICH_SUFFIX)


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _frontmatter(md: Path) -> dict:
    if not md.is_file():
        return {}
    m = re.match(r"^---\n(.*?)\n---\n", md.read_text(encoding="utf-8"), re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _brief(md: Path) -> str:
    return str(_frontmatter(md).get("description", "")).strip()


# --- enrichment file writer -------------------------------------------------

def write_enrichment_md(
    path: Path,
    *,
    source_concept: str,
    src_hash: str,
    verified: bool,
    elaboration: str = "",
    inferred: Optional[list[str]] = None,
    examples: Optional[list[str]] = None,
    see_also: Optional[list[str]] = None,
    section_order: tuple[str, ...] = ("elaboration", "inferred", "examples", "see_also"),
) -> list[str]:
    """Write a paired enrichment file. Returns the list of section keys written."""
    inferred = inferred or []
    examples = examples or []
    see_also = see_also or []
    content = {
        "elaboration": elaboration.strip(),
        "inferred": inferred,
        "examples": examples,
        "see_also": see_also,
    }

    written: list[str] = []
    body_parts: list[str] = []
    for key in section_order:
        val = content.get(key)
        if not val:
            continue
        written.append(key)
        body_parts.append(f"## {_SECTION_TITLES[key]}\n")
        if key == "elaboration":
            body_parts.append(val)
        elif key == "inferred":
            body_parts.extend(f"> [!inferred] {item}" for item in val)
        elif key == "examples":
            body_parts.extend(f"- {item}" for item in val)
        elif key == "see_also":
            body_parts.extend(f"- [[{stem}]]" for stem in val)
        body_parts.append("")

    fm = yaml.safe_dump(
        {
            "type": "Enrichment",
            "source_concept": source_concept,
            "source_hash": src_hash,
            "provenance": "enriched",
            "verified": bool(verified),
            "sections": written,
        },
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    header = f"---\n{fm}\n---\n\n# {source_concept} (enrichment)\n"
    atomic_write_text(path, header + "\n" + "\n".join(body_parts).rstrip() + "\n")
    return written


# --- orchestrator -----------------------------------------------------------

GenerateFn = Callable[[dict], dict]
VerifyFn = Callable[[dict, list[str]], list[str]]


def _grounded_leaves(concepts_root: Path) -> list[Path]:
    return sorted(
        p for p in concepts_root.rglob("*.md")
        if p.name != "_topic.md" and not is_enrich_file(p)
    )


def enrich(
    concepts_root: Path,
    *,
    generate: GenerateFn,
    verify: Optional[VerifyFn] = None,
    sections: tuple[str, ...] = ("elaboration", "inferred", "examples", "see_also"),
    include_world_knowledge: bool = True,
    force: bool = False,
    only_stems: Optional[set[str]] = None,
) -> dict:
    """Enrich every grounded concept leaf with a paired enrichment file.

    Idempotent: a concept whose ``source_hash`` still matches its existing
    enrichment is skipped unless ``force``. The grounded leaf is never modified.
    Inferred content is only produced when ``include_world_knowledge`` and the
    section is enabled, and is gated by ``verify`` (claims classified
    ``contradicts`` are dropped; ``verified`` is False only when inferred content
    is kept unverified).
    """
    concepts_root = Path(concepts_root)
    leaves = _grounded_leaves(concepts_root)
    index = {p.stem: p for p in leaves}  # bare stem -> grounded path (for neighbors)
    targets = [p for p in leaves if only_stems is None or p.stem in only_stems]

    want_inferred = include_world_knowledge and "inferred" in sections
    stats = {"created": 0, "updated": 0, "skipped": 0, "total": len(targets)}

    for leaf in targets:
        text = leaf.read_text(encoding="utf-8")
        h = source_hash(text)
        ep = enrich_path_for(leaf)
        existed = ep.exists()
        if not force and existed and _frontmatter(ep).get("source_hash") == h:
            stats["skipped"] += 1
            continue

        neighbors = []
        for stem in dict.fromkeys(_WIKILINK.findall(text)):
            stem = Path(stem.strip()).stem  # tolerate concepts/<stem>
            if stem in index and stem != leaf.stem:
                neighbors.append((stem, _brief(index[stem])))

        concept = {
            "stem": leaf.stem,
            "body": text,
            "brief": _brief(leaf),
            "neighbors": neighbors,
        }
        result = generate(concept) or {}

        inferred = list(result.get("inferred") or []) if want_inferred else []
        verified = True
        if inferred:
            if verify is not None:
                verdicts = verify(concept, inferred)
                inferred = [
                    it for it, v in zip(inferred, verdicts) if v != "contradicts"
                ]
            else:
                verified = False  # kept unverified world-knowledge

        write_enrichment_md(
            ep,
            source_concept=leaf.stem,
            src_hash=h,
            verified=verified,
            elaboration=str(result.get("elaboration", "")) if "elaboration" in sections else "",
            inferred=inferred,
            examples=list(result.get("examples") or []) if "examples" in sections else [],
            see_also=[s for s in (result.get("see_also") or []) if s in index]
            if "see_also" in sections else [],
            section_order=sections,
        )
        stats["updated" if existed else "created"] += 1

    return stats


# --- LLM-backed callables ---------------------------------------------------

_GUIDANCE_PREFIX = (
    "Enrichment guidance for this knowledge base (follow it for audience, tone, "
    "and depth):\n{guidance}\n\n"
)

_GENERATE = (
    "You are enriching one concept of a knowledge base. Produce additional, "
    "well-organized material for it. Return STRICT JSON with only these keys: "
    "{keys}.\n"
    "- elaboration: one grounded paragraph strictly derivable from the concept "
    "content and its related concepts (add no new facts).\n"
    "- inferred: a list of short statements adding useful world knowledge BEYOND "
    "the given content (may be empty); each must not contradict the content.\n"
    "- examples: a list of concrete illustrative examples.\n"
    "- see_also: a list of related concept stems, chosen only from the Related "
    "concepts listed.\n"
    "Keep the total under about {max_tokens} tokens. Depth: {depth}.\n\n"
    "{guidance}Concept: {stem}\n\nGrounded content:\n{body}\n\n"
    "Related concepts:\n{neighbors}"
)

_VERIFY = (
    "Classify each candidate statement about a concept as exactly one of: "
    '"supported" (entailed by the grounded content), "inferred" (plausible and '
    'consistent world knowledge not stated in the content), or "contradicts" '
    "(conflicts with the grounded content). Reply STRICT JSON: "
    '{{"verdicts": ["supported"|"inferred"|"contradicts", ...]}} in the same '
    "order as the statements.\n\nGrounded content:\n{body}\n\nStatements:\n{claims}"
)


def _guidance_block(guidance: str | None) -> str:
    guidance = (guidance or "").strip()
    return _GUIDANCE_PREFIX.format(guidance=guidance) if guidance else ""


def make_generate(model: str, *, guidance: str = "",
                  sections: tuple[str, ...] = ("elaboration", "inferred", "examples", "see_also"),
                  depth: str = "standard", max_tokens: int = 800):
    keys = ", ".join(sections)

    def generate(concept: dict) -> dict:
        neighbors = "\n".join(f"- {s}: {b}" for s, b in concept.get("neighbors", [])) or "(none)"
        raw = _llm_call(
            model,
            [{"role": "user", "content": _GENERATE.format(
                keys=keys, max_tokens=max_tokens, depth=depth,
                guidance=_guidance_block(guidance), stem=concept["stem"],
                body=concept["body"], neighbors=neighbors)}],
            "enrich",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        data = json.loads(raw) or {}
        return data if isinstance(data, dict) else {}

    return generate


def make_verify(model: str):
    def verify(concept: dict, claims: list[str]) -> list[str]:
        if not claims:
            return []
        numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
        raw = _llm_call(
            model,
            [{"role": "user", "content": _VERIFY.format(
                body=concept["body"], claims=numbered)}],
            "enrich-verify",
            response_format=_JSON_RESPONSE_FORMAT,
        )
        verdicts = (json.loads(raw) or {}).get("verdicts", [])
        allowed = {"supported", "inferred", "contradicts"}
        out = [v if v in allowed else "inferred" for v in verdicts][: len(claims)]
        out += ["inferred"] * (len(claims) - len(out))  # pad defensively
        return out

    return verify

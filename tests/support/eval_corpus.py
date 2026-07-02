"""Shared corpus + helpers for the Tier-3 real-LLM eval suites.

One coherent, self-contained concept set (music-education flavored) reused by
the structural (Group A), quality (Group B), and navigation (Group C) evals so
they judge the same tree definition. No fetched data — keeps the eval runnable
with only a model + key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openkb import topic_tree as tt

# stem -> brief. Big enough (> FANOUT_K) that a real model must invent
# multiple coherent subtopics, and spanning three natural themes
# (scales/harmony, rhythm, performance) so grouping quality is measurable.
CONCEPTS: dict[str, str] = {
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


def seed_concepts(wiki: Path) -> Path:
    """Write the flat concept leaves under ``wiki/concepts`` and return that dir.
    Every concept links to ``major-scale`` so link survival across moves is testable.
    """
    d = wiki / "concepts"
    d.mkdir(parents=True, exist_ok=True)
    for stem, brief in CONCEPTS.items():
        (d / f"{stem}.md").write_text(
            f'---\ntype: "Concept"\ndescription: "{brief}"\n---\n'
            f"# {stem}\n\nSee also [[major-scale]].\n",
            encoding="utf-8",
        )
    return d


def build_real_tree(model: str, wiki: Path) -> Path:
    """Seed concepts and bootstrap them into a tree using the real model."""
    from openkb.topic_tree_llm import make_cluster, make_summarize

    concepts = seed_concepts(wiki)
    tt.bootstrap(concepts, cluster=make_cluster(model), summarize=make_summarize(model))
    return wiki


def require_llm_or_skip() -> str:
    """Skip the calling test unless real-LLM testing is opted in; return the model."""
    if os.environ.get("OPENKB_LLM_TESTS") != "1":
        pytest.skip("real-LLM tests are opt-in; set OPENKB_LLM_TESTS=1 to run")
    model = os.environ.get("OPENKB_TEST_MODEL")
    if not model:
        pytest.skip("set OPENKB_TEST_MODEL (e.g. anthropic/claude-haiku-4-5 or ollama/llama3.1)")
    if model.split("/")[0] not in {"ollama", "lmstudio"} and not os.environ.get("LLM_API_KEY"):
        pytest.skip("hosted OPENKB_TEST_MODEL requires LLM_API_KEY")
    return model


def write_report(name: str, payload: dict) -> Path:
    """Write a JSON eval report under tests/reports/ (gitignored). Returns the path."""
    reports = Path(__file__).resolve().parent.parent / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    # Caller passes a stable name; no timestamp here (Date.now is unavailable in
    # some sandboxes and stable names keep the latest run easy to find).
    path = reports / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def tree_snapshot(concepts_root: Path) -> list[str]:
    """Canonical structural snapshot: sorted ``relpath|kind`` lines, prose excluded.
    Stable across prose changes; used for eyeballing/reporting, not exact-match."""
    lines = []
    for p in sorted(concepts_root.rglob("*")):
        if p.is_dir():
            lines.append(f"{p.relative_to(concepts_root)}/|topic")
        elif p.suffix == ".md" and p.name != tt.TOPIC_FILE:
            lines.append(f"{p.relative_to(concepts_root)}|concept")
    return lines

"""Tier-3 real-LLM eval for the synthesis layer (opt-in, never in CI).

Groups:
  A structural (hard): every grounded leaf paired, required frontmatter, verified,
    inferred tagged, synth links resolve.
  B quality (LLM-as-judge, thresholded → report): faithfulness + usefulness of the
    elaboration and correctness of provenance tagging.
  C hallucination (the real risk): the verifier must catch planted unsupported
    claims; report the false-negative rate + a reliability guard.

Run:
  OPENKB_LLM_TESTS=1 OPENKB_TEST_MODEL=anthropic/claude-haiku-4-5 LLM_API_KEY=... \
    pytest -m llm tests/test_enrichment_eval.py
"""
from __future__ import annotations

import json
import re

import pytest
import yaml

from openkb.agent import enricher as E
from openkb.agent.compiler import _JSON_RESPONSE_FORMAT, _llm_call
from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks
from tests.support.eval_corpus import (
    CONCEPTS, dump_tree, require_llm_or_skip, seed_concepts, write_artifact, write_report,
)

pytestmark = pytest.mark.llm

QUALITY_THRESHOLD = 3.5
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def _fm(path):
    return yaml.safe_load(re.match(r"^---\n(.*?)\n---\n",
                                   path.read_text(encoding="utf-8"), re.DOTALL).group(1))


@pytest.fixture(scope="module")
def enriched(tmp_path_factory):
    model = require_llm_or_skip()
    wiki = tmp_path_factory.mktemp("wiki")
    concepts = seed_concepts(wiki)

    # Wrap verify to record every claim's verdict, so a reviewer can see exactly
    # which inferred claims were kept vs. dropped as contradictions.
    base_verify = E.make_verify(model)
    verdict_log: list[str] = []

    def verify(concept, claims):
        verdicts = base_verify(concept, claims)
        for claim, v in zip(claims, verdicts):
            mark = {"contradicts": "DROPPED ", "supported": "kept(sup)", "inferred": "kept(inf)"}.get(v, v)
            verdict_log.append(f"[{concept['stem']}] {mark}: {claim}")
        return verdicts

    E.enrich(
        concepts,
        generate=E.make_generate(model, guidance="A music-education KB for learners."),
        verify=verify,
    )
    dump_tree("enrichment", concepts)  # browsable: every .enrich.md the model wrote
    write_artifact("enrichment", "verify_log.txt", "\n".join(verdict_log) or "(no inferred claims)")
    return {"model": model, "wiki": wiki, "concepts": concepts}


# --- Group A: structural (hard) ---------------------------------------------

def test_A_every_concept_paired(require_llm, enriched):
    concepts = enriched["concepts"]
    for stem in CONCEPTS:
        assert (concepts / f"{stem}.enrich.md").is_file(), f"{stem} missing synth"


def test_A_frontmatter_and_verified(require_llm, enriched):
    concepts = enriched["concepts"]
    for stem in CONCEPTS:
        fm = _fm(concepts / f"{stem}.enrich.md")
        assert fm["type"] == "Enrichment"
        assert fm["source_concept"] == stem
        assert fm["provenance"] == "enriched"
        assert fm["verified"] is True  # verify pass ran


def test_A_inferred_tagged_and_links_resolve(require_llm, enriched):
    wiki, concepts = enriched["wiki"], enriched["concepts"]
    targets = list_existing_wiki_targets(wiki)
    for stem in CONCEPTS:
        body = (concepts / f"{stem}.enrich.md").read_text(encoding="utf-8")
        # any inferred content must be tagged
        if "## Inferred context" in body:
            section = body.split("## Inferred context", 1)[1]
            assert "> [!inferred]" in section
        for link in _WIKILINK.findall(body):
            _, ghosts = strip_ghost_wikilinks(f"[[{link}]]", targets)
            assert ghosts == [], f"ghost [[{link}]] in {stem}.enrich.md"


# --- Group B: quality (judge → report) --------------------------------------

def test_B_elaboration_quality(require_llm, enriched):
    model, concepts = enriched["model"], enriched["concepts"]
    scores = []
    for stem, brief in list(CONCEPTS.items())[:6]:  # sample to bound cost
        body = (concepts / f"{stem}.enrich.md").read_text(encoding="utf-8")
        raw = _llm_call(model, [{"role": "user", "content": (
            "Score 1-5 (5 best) whether this synthesized note is faithful to and "
            f"usefully elaborates the concept '{stem}' ({brief}). Penalize any "
            "claim that contradicts common knowledge of the concept. Reply JSON "
            '{"score": <1-5>, "reason": "<one sentence>"}.\n\n' + body)}],
            "enrichment-quality-judge", response_format=_JSON_RESPONSE_FORMAT, temperature=0)
        scores.append(int((json.loads(raw) or {}).get("score", 0)))
    mean = sum(scores) / len(scores) if scores else 0.0
    write_report("enrichment_quality", {"model": model, "mean": mean, "scores": scores})
    if mean < QUALITY_THRESHOLD:
        pytest.xfail(f"quality mean {mean:.2f} < {QUALITY_THRESHOLD} (soft floor)")
    assert mean >= QUALITY_THRESHOLD


# --- Group C: hallucination guard -------------------------------------------

def test_C_verifier_catches_planted_unsupported(require_llm, enriched):
    """The verifier must classify obviously-false claims as `contradicts`.
    Reports the false-negative rate; a reliability guard keeps it honest."""
    model, concepts = enriched["model"], enriched["concepts"]
    verify = E.make_verify(model)
    planted = {
        "major-scale": "The major scale has thirteen notes and no half steps.",
        "triads": "A triad is a single note played very loudly.",
        "time-signatures": "Time signatures indicate the color of the sheet music.",
    }
    caught = 0
    results = []
    for stem, false_claim in planted.items():
        body = (concepts / f"{stem}.md").read_text(encoding="utf-8")
        verdicts = verify({"body": body}, [false_claim])
        ok = verdicts and verdicts[0] == "contradicts"
        caught += bool(ok)
        results.append({"stem": stem, "verdict": verdicts[0] if verdicts else None})
    fn_rate = 1 - caught / len(planted)
    write_report("enrichment_halluc", {"model": model, "false_negative_rate": fn_rate,
                                       "results": results})
    # Reliability guard: at least the majority of blatant falsehoods are caught.
    assert caught >= 2, f"verifier too lenient: only caught {caught}/{len(planted)}"

# Enrichment Layer — Plan

Status: proposal · Builds on the `feat/concept-topic-tree` branch (compile → enrich → distill).

## Goal

Add a generative **enrichment** pass ("enrichment layer") where the LLM authors
*new* material on top of the grounded concept leaves: elaboration, connective
tissue, worked examples, prerequisites, "why it matters", and cross-links. It is
steered by general user guidance (an `AGENTS.md` section), the same way `distill`
is steered by the `## Hierarchy` guidance.

This is a different operation from everything the pipeline does today. Compile
(Pass 1) and distill (Pass 2) are **extractive/abstractive but source-bound** —
they never invent. Enrichment is **generative**: it deliberately goes beyond the
sources. That power is also the risk, so the design is built around one principle.

## Governing principle: provenance separation

Generated content must never contaminate the grounded source-of-truth leaves.
This repo already has the proven pattern: the customer-support KB's **paired
two-layer model** (`<category>-human.md` authoritative + `<category>-distilled.md`
machine-generated and regenerable, "human wins on conflict"). Enrichment copies
that shape at the concept level.

## Decisions (chosen)

1. **Fidelity: both, tagged by provenance.** Do grounded elaboration (strictly
   derivable from the concept + its cited links) AND allow tagged world-knowledge
   additions ("inferred"), with a verification pass gating the inferred content.
2. **Placement: paired file per concept.** `concepts/<…>/foo.md` (grounded, the
   source of truth, never modified by enrich) + `concepts/<…>/foo.enrich.md`
   (generated, regenerable, disposable).
3. **Order: enrich before distill.** Enrich the leaves first so richer knowledge
   is available; `distill` still builds its pathway summaries from the **grounded**
   leaves (never from enriched text), but may use enrichment briefs to inform
   category naming. Compile → **enrich** → distill.

## The core shape

```
Pass 1 (exists):  raw docs ──compile──▶ grounded concept leaves      concepts/**/foo.md
Pass 3 (new):     foo.md ──enrich──▶ paired enrichment               concepts/**/foo.enrich.md
                             │
                             └─ verify pass gates the "inferred" blocks
Pass 2 (exists):  grounded leaves ──distill──▶ pathway hierarchy     concepts/**/_topic.md
```

## Enrichment file format (`foo.enrich.md`)

Frontmatter (code-managed):
- `type: Enrichment`
- `source_concept: foo` (the grounded leaf it pairs with)
- `source_hash: <sha256 of foo.md>` (staleness detection; mirrors the HashRegistry ethos)
- `provenance: enriched`
- `verified: true|false`
- `sections: [elaboration, inferred, examples, see_also]`

Body sections (configurable, each block links the grounded concepts it builds on):
- `## Elaboration` — grounded, derivable expansion (rephrasing, structure, depth).
- `## Inferred context` — world-knowledge additions **beyond** the sources; every
  item is tagged, e.g. `> [!inferred]`, and is gated by the verifier.
- `## Examples`, `## See also` — worked examples and `[[wikilinks]]` cross-refs.

The grounded `foo.md` is **not edited** (that is a hard invariant and a regression
pin). Pairing is by naming convention + `source_concept` frontmatter, so consumers
and the query agent discover `foo.enrich.md` without touching the leaf.

## Two-tier verification (because we allow "inferred")

A second adversarial LLM pass classifies each enriched claim:
- **supported** — entailed by the concept/cited links → keep (elaboration).
- **plausible-inferred** — beyond sources but not contradicting → keep, **tagged**.
- **contradicts** — conflicts with grounded content → **drop**.

Sets `verified: true` when the file passes. This is the same adversarial-verify
pattern the testing tiers already use. Planted-unsupported guards make an
over-lenient verifier detectable (as in the Tier-3 quality eval).

## Config (`enrichment:` block, resolved like `resolve_hierarchy`)

| Key | Default | Controls |
|---|---|---|
| `enrichment.enabled` | `false` | master switch (experimental, opt-in) |
| `enrichment.depth` | `standard` | `brief` \| `standard` \| `deep` — how much to add |
| `enrichment.sections` | `[elaboration, inferred, examples, see_also]` | which blocks to author |
| `enrichment.include_world_knowledge` | `true` | enable the `inferred` block (gated by verify) |
| `enrichment.verify` | `true` | run the verification pass |
| `enrichment.max_tokens` | `~800` | soft cap per enrichment file |
| `enrichment.model` | inherit | optional model override for the enrich pass |

Add `resolve_enrichment(config) -> EnrichmentConfig` in `openkb/config.py`, mirroring
`resolve_hierarchy` (per-key validation, fallback-with-warning, back-compat).

## Plan

### Phase 0 — Config & guidance
- Add the `enrichment:` block + `EnrichmentConfig`/`resolve_enrichment`.
- Add a `## Enrichment` section to the `AGENTS.md` template (`openkb/schema.py`):
  audience, tone, depth, what to add vs. avoid. Injected into every enrich prompt
  via a `_enrichment_guidance()` helper (mirror `_hierarchy_guidance` in `cli.py`).

### Phase 1 — Enrichment engine
- New module `openkb/agent/enricher.py` (keep it beside `compiler.py`):
  - `make_generate(model, *, guidance, sections, depth)` → given a grounded
    concept (body + frontmatter + its linked neighbors), returns structured
    section content. Routes through `compiler._llm_call` with step `enrich`.
  - `make_verify(model)` → per-claim verdicts. Step `enrich-verify`.
- Orchestrator `enrich(concepts_root, *, enrich, verify=None, ...)`:
  reads grounded leaves (skips `*.enrich.md`), builds each paired file, runs verify,
  writes atomically via `openkb/locks.py`. Idempotent: skip a concept whose
  `source_hash` matches an existing fresh `foo.enrich.md`; regenerate stale/missing.

### Phase 2 — CLI + integration
- `openkb enrich` (flag-gated on `enrichment.enabled`), `--concept <stem>` for one,
  `--force` to ignore `source_hash` freshness. Prints counts (created/updated/skipped).
- **distill integration:** `topic_tree._read_leaves` must **exclude `*.enrich.md`**
  so enrichment files never become tree leaves. Optionally pass enrichment briefs to
  `make_distill_cluster` for better category naming (summaries stay grounded).
- **remove/recompile integration:** removing `foo.md` also removes `foo.enrich.md`;
  recompiling `foo.md` invalidates its enrichment (hash mismatch → stale, regenerated
  on next `enrich`).
- **query agent:** when reading a concept, also surface its `.enrich.md` (clearly
  labeled generated), and honor the `> [!inferred]` tags in answers.
- **lint:** register `.enrich.md` so its `[[wikilinks]]` resolve; exclude enrichment
  files from the "orphan concept" and "missing-from-index" checks (they are
  attachments to a grounded leaf, not standalone concepts).

### Phase 3 — Safety & refresh
- Grounded leaves are the source of truth; enrichment files are fully regenerable.
- Per-file atomic writes; a failed/again-run `enrich` never corrupts leaves.
- Staleness via `source_hash` keeps re-runs cheap and correct.

## Testing (mirror the existing four-tier strategy in full)

Reuses the existing scaffolding: the `llm` / `integration` / `regression` markers
and `addopts = "-m 'not llm'"` are already registered in `pyproject.toml`, and CI
(`.github/workflows/test.yml`) already runs the non-`llm` suite. Tier 1/2/4 run in
CI; Tier 3 is `@pytest.mark.llm`, opt-in, never in CI.

**Fixtures.** Reuse `tests/support/eval_corpus.py` (the grounded concept set) for
the Tier-3 corpus; add a tiny committed grounded-concept fixture for Tier-2.
Extend `tests/support/fake_llm.py` with deterministic `enrich` and
`enrich-verify` routes (verify route returns a fixed supported/inferred/contradicts
verdict per claim, including one planted contradiction).

**Tier 1 — unit (`test_enrichment_*.py`)**
- `resolve_enrichment` parsing: defaults when absent, each key parses, invalid
  values fall back with a warning, `include_world_knowledge=false` disables the
  inferred block (mirror `resolve_hierarchy` tests).
- enrichment-file writer: frontmatter keys (`type`/`source_concept`/`source_hash`/
  `provenance`/`verified`/`sections`), section rendering, `> [!inferred]` tagging.
- `source_hash` staleness: matching hash → skip, mismatch/missing → regenerate.
- `topic_tree._read_leaves` excludes `*.enrich.md` (so enrichment never becomes a leaf).

**Tier 2 — integration, fake LLM (`test_enrichment_integration.py`)**
- `openkb enrich` CLI creates one `foo.enrich.md` per grounded concept.
- grounded leaf is **byte-identical** after enrich (diff == empty).
- verifier drops a planted contradicting claim; keeps supported + tags inferred.
- idempotent re-enrich: unchanged concepts skipped via `source_hash`; `--force`
  regenerates.
- `enrich` → `distill` builds the hierarchy over **grounded leaves only** (enrichment
  files absent from the tree; concept count unchanged).
- `remove` of a grounded concept also deletes its `foo.enrich.md`.
- lint: enrichment `[[wikilinks]]` resolve; enrichment files are excluded from the orphan
  and missing-from-index checks.
- query agent (fake): surfaces the paired enrichment and preserves `> [!inferred]`.
- **Golden snapshot:** canonical structure of a enrichment file (frontmatter keys +
  section headings, prose excluded) under `tests/golden/`, with the
  `OPENKB_UPDATE_GOLDEN=1` refresh mechanism.

**Tier 3 — real-LLM eval, opt-in (`test_enrichment_eval.py`), Groups A/B/C**
- **A structural (hard):** every grounded leaf has a paired enrichment; required
  frontmatter present; `verified: true`; inferred items are tagged; enrichment links
  resolve. Deterministic pass/fail even with a real model.
- **B quality (LLM-as-judge, thresholded → report):** usefulness and
  faithfulness of the elaboration; tag correctness (are "inferred" items truly
  beyond the sources, and is nothing load-bearing left untagged). Scores to
  `tests/reports/enrichment_quality.json`.
- **C hallucination (the real risk test):** measured hallucination rate — planted
  unsupported claims MUST be caught and dropped by the verifier; report
  false-negative rate to `tests/reports/enrichment_halluc.json`. Include a
  planted "obviously unsupported" set as a verifier-reliability guard so an
  over-lenient verifier is detectable.

**Tier 4 — regression pins**
- (a) enrich never mutates a grounded leaf (byte-identical).
- (b) a contradicting inferred claim is dropped.
- (c) stale enrichment (source_hash mismatch) is regenerated on next enrich.
- (d) `distill` ignores `*.enrich.md`.
- (e) removing a grounded concept removes its paired enrichment (no orphan enrichment).
- (f) enrichment `[[wikilinks]]` resolve and enrichment files are not flagged as orphan
  concepts by lint.

Optional: a coverage floor (`--cov-fail-under`) once `pytest-cov` is added, same
as noted for the base testing plan.

## Risks & mitigations
- **Hallucination** → provenance tags + verify pass + drop-on-contradiction +
  planted-guard eval.
- **Cost** (per-concept generate + verify) → `source_hash` idempotency, `--concept`
  scoping, `depth` control, optional cheaper `enrichment.model`.
- **Staleness** after recompile → hash-based invalidation.
- **Retrieval pollution** → enrichment is a separate, labeled layer the agent treats as
  non-authoritative; grounded content always wins.

## Background / naming
This is **generative knowledge enrichment / abstractive elaboration** (as opposed
to the extractive compilation OpenKB does today). The paired-layer trust model is
the same one already in production for the customer-support KB (human vs distilled).

## Est. effort
Phase 0: ~0.5 day · Phase 1: ~2 days · Phase 2: ~2 days · Phase 3: ~0.5 day ·
Tests: ~2–3 days (fake-LLM routes + eval harness reuse the existing scaffolding).

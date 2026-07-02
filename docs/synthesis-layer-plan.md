# Synthesis / Enrichment Layer — Plan

Status: proposal · Builds on the `feat/concept-topic-tree` branch (compile → enrich → distill).

## Goal

Add a generative **enrichment** pass ("synthesis layer") where the LLM authors
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
   source of truth, never modified by enrich) + `concepts/<…>/foo.synth.md`
   (generated, regenerable, disposable).
3. **Order: enrich before distill.** Enrich the leaves first so richer knowledge
   is available; `distill` still builds its pathway summaries from the **grounded**
   leaves (never from synthesized text), but may use synth briefs to inform
   category naming. Compile → **enrich** → distill.

## The core shape

```
Pass 1 (exists):  raw docs ──compile──▶ grounded concept leaves      concepts/**/foo.md
Pass 3 (new):     foo.md ──enrich──▶ paired synthesis               concepts/**/foo.synth.md
                             │
                             └─ verify pass gates the "inferred" blocks
Pass 2 (exists):  grounded leaves ──distill──▶ pathway hierarchy     concepts/**/_topic.md
```

## Synthesis file format (`foo.synth.md`)

Frontmatter (code-managed):
- `type: Synthesis`
- `source_concept: foo` (the grounded leaf it pairs with)
- `source_hash: <sha256 of foo.md>` (staleness detection; mirrors the HashRegistry ethos)
- `provenance: synthesized`
- `verified: true|false`
- `sections: [elaboration, inferred, examples, see_also]`

Body sections (configurable, each block links the grounded concepts it builds on):
- `## Elaboration` — grounded, derivable expansion (rephrasing, structure, depth).
- `## Inferred context` — world-knowledge additions **beyond** the sources; every
  item is tagged, e.g. `> [!inferred]`, and is gated by the verifier.
- `## Examples`, `## See also` — worked examples and `[[wikilinks]]` cross-refs.

The grounded `foo.md` is **not edited** (that is a hard invariant and a regression
pin). Pairing is by naming convention + `source_concept` frontmatter, so consumers
and the query agent discover `foo.synth.md` without touching the leaf.

## Two-tier verification (because we allow "inferred")

A second adversarial LLM pass classifies each synthesized claim:
- **supported** — entailed by the concept/cited links → keep (elaboration).
- **plausible-inferred** — beyond sources but not contradicting → keep, **tagged**.
- **contradicts** — conflicts with grounded content → **drop**.

Sets `verified: true` when the file passes. This is the same adversarial-verify
pattern the testing tiers already use. Planted-unsupported guards make an
over-lenient verifier detectable (as in the Tier-3 quality eval).

## Config (`synthesis:` block, resolved like `resolve_hierarchy`)

| Key | Default | Controls |
|---|---|---|
| `synthesis.enabled` | `false` | master switch (experimental, opt-in) |
| `synthesis.depth` | `standard` | `brief` \| `standard` \| `deep` — how much to add |
| `synthesis.sections` | `[elaboration, inferred, examples, see_also]` | which blocks to author |
| `synthesis.include_world_knowledge` | `true` | enable the `inferred` block (gated by verify) |
| `synthesis.verify` | `true` | run the verification pass |
| `synthesis.max_tokens` | `~800` | soft cap per synth file |
| `synthesis.model` | inherit | optional model override for the enrich pass |

Add `resolve_synthesis(config) -> SynthesisConfig` in `openkb/config.py`, mirroring
`resolve_hierarchy` (per-key validation, fallback-with-warning, back-compat).

## Plan

### Phase 0 — Config & guidance
- Add the `synthesis:` block + `SynthesisConfig`/`resolve_synthesis`.
- Add a `## Synthesis` section to the `AGENTS.md` template (`openkb/schema.py`):
  audience, tone, depth, what to add vs. avoid. Injected into every enrich prompt
  via a `_synthesis_guidance()` helper (mirror `_hierarchy_guidance` in `cli.py`).

### Phase 1 — Enrichment engine
- New module `openkb/agent/synthesizer.py` (keep it beside `compiler.py`):
  - `make_synthesize(model, *, guidance, sections, depth)` → given a grounded
    concept (body + frontmatter + its linked neighbors), returns structured
    section content. Routes through `compiler._llm_call` with step `synthesize`.
  - `make_verify_synth(model)` → per-claim verdicts. Step `synth-verify`.
- Orchestrator `enrich(concepts_root, *, synthesize, verify=None, ...)`:
  reads grounded leaves (skips `*.synth.md`), builds each paired file, runs verify,
  writes atomically via `openkb/locks.py`. Idempotent: skip a concept whose
  `source_hash` matches an existing fresh `foo.synth.md`; regenerate stale/missing.

### Phase 2 — CLI + integration
- `openkb enrich` (flag-gated on `synthesis.enabled`), `--concept <stem>` for one,
  `--force` to ignore `source_hash` freshness. Prints counts (created/updated/skipped).
- **distill integration:** `topic_tree._read_leaves` must **exclude `*.synth.md`**
  so synth files never become tree leaves. Optionally pass synth briefs to
  `make_distill_cluster` for better category naming (summaries stay grounded).
- **remove/recompile integration:** removing `foo.md` also removes `foo.synth.md`;
  recompiling `foo.md` invalidates its synth (hash mismatch → stale, regenerated
  on next `enrich`).
- **query agent:** when reading a concept, also surface its `.synth.md` (clearly
  labeled generated), and honor the `> [!inferred]` tags in answers.
- **lint:** register `.synth.md` so its `[[wikilinks]]` resolve; exclude synth
  files from the "orphan concept" and "missing-from-index" checks (they are
  attachments to a grounded leaf, not standalone concepts).

### Phase 3 — Safety & refresh
- Grounded leaves are the source of truth; synth files are fully regenerable.
- Per-file atomic writes; a failed/again-run `enrich` never corrupts leaves.
- Staleness via `source_hash` keeps re-runs cheap and correct.

## Testing (mirror the existing tiers)

- **Tier 1 unit:** `resolve_synthesis` parsing; synth frontmatter/format + tags;
  `source_hash` staleness; `_read_leaves` excludes `*.synth.md`.
- **Tier 2 integration (fake LLM):** `enrich` CLI creates paired files; grounded
  leaf is **byte-identical** after; verifier drops a planted contradiction;
  `enrich`→`distill` builds the hierarchy over grounded leaves only. Extend
  `tests/support/fake_llm.py` with `synthesize` / `synth-verify` routes.
- **Tier 3 real-LLM eval (opt-in):** usefulness + faithfulness of elaboration;
  measured **hallucination rate** (a planted unsupported claim must be caught);
  provenance-tag correctness. Reports under `tests/reports/`.
- **Tier 4 regression pins:** (a) enrich never mutates a grounded leaf;
  (b) a contradicting inferred claim is dropped; (c) stale synth is regenerated;
  (d) distill ignores `*.synth.md`.

## Risks & mitigations
- **Hallucination** → provenance tags + verify pass + drop-on-contradiction +
  planted-guard eval.
- **Cost** (per-concept generate + verify) → `source_hash` idempotency, `--concept`
  scoping, `depth` control, optional cheaper `synthesis.model`.
- **Staleness** after recompile → hash-based invalidation.
- **Retrieval pollution** → synth is a separate, labeled layer the agent treats as
  non-authoritative; grounded content always wins.

## Background / naming
This is **generative knowledge enrichment / abstractive elaboration** (as opposed
to the extractive compilation OpenKB does today). The paired-layer trust model is
the same one already in production for the customer-support KB (human vs distilled).

## Est. effort
Phase 0: ~0.5 day · Phase 1: ~2 days · Phase 2: ~2 days · Phase 3: ~0.5 day ·
Tests: ~2–3 days (fake-LLM routes + eval harness reuse the existing scaffolding).

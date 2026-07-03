# Concept Granularity Tuning — Plan

Status: proposal · Addresses concept over-fragmentation observed on the 100-doc
Pianote run (122 concepts with near-duplicate/over-granular families).

## Does OpenKB's approach already prevent this? Mostly yes — so tune, don't rebuild.

The compiler's `concepts-plan` step (`_CONCEPTS_PLAN_USER` in `openkb/agent/compiler.py`)
is *already* designed to avoid fragmentation:
- it feeds the model the existing concept pages (`{concept_briefs}`),
- rule: **"Prefer 'update' over 'create' for any concept already listed above,"**
- rule: **"Do NOT create a concept that overlaps an existing one — use 'update'."**

So we do **not** need a post-hoc dedup/merge pass. The observed fragmentation comes
from two fixable gaps:

1. **No granularity/altitude guidance.** The prompt forbids *overlap* but says nothing
   about how *general* a concept should be. So the model creates over-granular leaves
   (a page per note value; a page per named progression) and near-synonyms that don't
   overlap verbatim, e.g. `thumb-crossover` **and** `crossover-technique`, `c-position`
   vs `five-finger-position` vs `hand-position`, `melody` vs `melody-as-top-voice` vs
   `melody-creation`. None are exact duplicates, so the "no overlap" rule doesn't catch
   them.

2. **(Latent bug) The existing-concept list breaks after distill.** `_read_concept_briefs`
   and `_read_index_and_concept_slugs` use a **non-recursive** `concepts_dir.glob("*.md")`
   and don't exclude `_topic.md` / `*.enrich.md`. On a flat KB (our fresh compile) this
   works, but once concepts are nested by `distill`, the model is shown essentially no
   existing concepts → reuse collapses and fragmentation gets *worse* on any later
   `add`/`recompile`. Must fix before we can validate the tuning via `recompile` on the
   already-distilled Pianote KB.

## Approach: make each leaf slightly more general + reuse harder (prompt-level)

### Phase 1 — Tune the `concepts-plan` prompt (primary)
Edit `_CONCEPTS_PLAN_USER`:
- **Add a "Concept altitude" block.** A concept should be general enough to recur across
  multiple lessons. Prefer the broad umbrella; **fold closely-related specifics into ONE
  concept as within-page detail (sections/examples), not separate pages.** Never create a
  concept for a single fact. Ground it with examples from this domain:
  - GOOD: one `note-durations` concept covering whole/half/quarter/eighth/dotted/ties/
    triplets as sections. BAD: seven separate note-value pages.
  - GOOD: reuse `thumb-crossover`. BAD: also create `crossover-technique`.
  - GOOD: one `hand-position` (with C-position / five-finger as facets). BAD: three pages.
- **Strengthen synonym reuse.** "Before creating, scan the existing list for the SAME idea
  under a different name, phrasing, or plurality; if present, `update` that slug even if
  you would have named it differently. Treat synonyms and narrow sub-aspects of an existing
  concept as that concept."
- Keep/reinforce the existing "prefer update", "no overlap", "don't page the doc topic".
- Optionally one short few-shot example (a plan that reuses + generalizes correctly).

### Phase 2 — Per-KB granularity guidance (optional; matches `## Hierarchy` / `## Enrichment`)
Add an `AGENTS.md` `## Concepts` section injected into the plan prompt via
`_agents_section(wiki_dir, "Concepts")` (HTML comments stripped, as we now do). Lets each KB
set its altitude, e.g. Pianote: *"Keep concepts at the level of a teachable skill or idea,
not individual facts — one 'note durations', not one per value."* Add the placeholder +
comment note to the `AGENTS_MD` template in `schema.py`.

### Phase 3 — Fix the existing-concept read (required)
`_read_concept_briefs` and `_read_index_and_concept_slugs`: switch to `rglob("*.md")`,
exclude `_topic.md` and `*.enrich.md`. Concept reuse then works on a distilled KB and the
briefs list is accurate. Add a deterministic regression pin.

### Phase 4 — Validate on the Pianote corpus (before/after, real LLM)
Baseline (today): **122 concepts**; near-dup families listed above.
- Re-run compile on the 100 transcripts (fresh KB) with the tuned prompt.
- **Metrics:** total concept count drops meaningfully (rough target ~25–40% fewer, ≈75–95);
  the named near-dup families collapse to single concepts; note-values consolidated.
- **Guardrail against over-generalization:** spot-check that surviving pages still carry the
  specific lesson detail (now as sections), that genuinely distinct concepts stay distinct,
  and that Tier-3 **navigation success@N does not drop** (over-merging would hurt retrieval).
- Add a lightweight **"granularity" dimension** to the Tier-3 quality eval (judge: are
  concepts at a good altitude — neither redundant nor vague?).

### Phase 5 — Tests
- Unit (deterministic): `_CONCEPTS_PLAN_USER` contains the altitude guidance; injected
  `## Concepts` guidance reaches the plan prompt; `_read_concept_briefs` is recursive and
  excludes `_topic.md`/`*.enrich.md` (regression pin).
- Real-LLM (opt-in): the before/after concept-count + granularity check on Pianote.

## Trade-off / risk
Generalizing trades specificity for cohesion — push too far and concepts get vague and
retrieval suffers. This is a dial, aimed at "teachable skill/idea" altitude: **not** one
page per fact, **not** one page per whole domain. Mitigations: fold specifics into the
general page (never drop detail), keep distinct concepts distinct, and hold nav success@N
as a regression guard so we detect over-merging.

## Est. effort
Phase 1 ~0.5d · Phase 2 ~0.5d · Phase 3 ~0.5d · Phase 4 = 1–2 real-LLM runs · Phase 5 ~0.5d.

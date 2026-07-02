# Tier 3 — Eval Tests (Real LLM, Local, Opt-in)

Runs the real pipeline against a real model over real corpora. **Non-deterministic**,
so assertions are property/rubric-based, never exact-match. Opt-in, never in CI.

## Objective
Answer the questions mocks cannot: does the LLM actually build a *good* hierarchy,
and does that hierarchy *help* an agent navigate general→specific?

## Gating
- Marker `@pytest.mark.llm`; excluded by default via `addopts = "-m 'not llm'"`.
- `conftest.py` fixture `require_llm` that `pytest.skip`s unless `OPENKB_LLM_TESTS=1`
  AND a key/endpoint is configured.
- Model via `OPENKB_TEST_MODEL` (default a cheap one). Two supported backends:
  - **Local, free:** Ollama / LM Studio through litellm (`ollama/llama3.1`, etc.).
    Set generous `litellm.timeout`. Lower quality → looser judge thresholds.
  - **Hosted, cheap:** `anthropic/claude-haiku-4-5` or a small OpenAI model — higher
    fidelity, small cost on tiny corpora.

## Determinism controls
- `temperature=0` where the provider supports it; judge always at `temperature=0`.
- Assert thresholds/properties, not equality.
- For the few critical invariants, optionally build **3×** and require stability.
- Keep corpora tiny (see `fixtures-datasets.md`) to bound cost and runtime.

## Test group A — Structural invariants (hard pass/fail, deterministic even w/ real LLM)
File: `tests/test_hierarchy_eval.py`. Build the tree once per session (fixture-scoped),
then assert:
- exactly **one root file**
- **≥2 layers**; depth ≤ `max_depth`
- every leaf reachable from root; **no orphans**
- all `[[wikilinks]]` resolve (reuse `lint`'s ghost-link check) — zero ghosts
- fan-out per node within `[min_fanout, max_fanout]`
- every node summary under `node_summary_tokens` hard cap
- `related` links bidirectional, same-layer, point to existing nodes, ≤ max

These are cheap and catch most real-world breakage without any judgment.

## Test group B — Semantic quality (LLM-as-judge, thresholded → report)
File: `tests/test_hierarchy_quality.py`. Use a judge prompt (temperature 0) scoring
1–5; record all scores to `tests/reports/quality_<ts>.json` and assert mean ≥
threshold (soft-fail: xfail/warn on small misses, hard-fail on floor):
- **Encapsulation:** does each parent summary faithfully cover its children?
- **Abstraction gradient:** is each layer measurably more general than its children?
- **Category coherence:** are invented category names coherent and non-overlapping?
- **AGENTS.md adherence:** do categories reflect the KB's stated purpose/theme?

Judge reliability guard: include 1–2 planted "obviously bad" trees the judge must
score low, so a broken/over-lenient judge is detectable.

## Test group C — Navigation eval (the real end-to-end value test)
File: `tests/test_hierarchy_navigation.py`. Per corpus, maintain a small
`nav_cases.yaml` of `question → expected_leaf_concept`. Run the query agent in
tree-descent mode and assert it reaches the expected leaf within N hops.
- Report **success@N** and **avg hops** to `tests/reports/nav_<ts>.json`.
- Assert success rate ≥ threshold (e.g. 0.7 hosted, lower for local models).
- Optional A/B: compare descent vs flat retrieval on the same questions to quantify
  the hierarchy's benefit.

## Corpora
Use 2–3 sets from `fixtures-datasets.md`: one Wikipedia single-domain slice, one
docs-site export, and (priority) a small set of the user's own transcripts/blog
posts for the navigation eval.

## How to run
```bash
# hosted cheap model
OPENKB_LLM_TESTS=1 OPENKB_TEST_MODEL=anthropic/claude-haiku-4-5 LLM_API_KEY=... \
  pytest -m llm

# fully local via Ollama
OPENKB_LLM_TESTS=1 OPENKB_TEST_MODEL=ollama/llama3.1 pytest -m llm -k navigation
```

## Outputs
- Hard pass/fail on structural invariants.
- JSON eval reports (quality, navigation) written under `tests/reports/` (gitignored)
  for tracking quality over time / across models.

## Definition of done
- Group A green against at least one real model on one corpus.
- Group B + C runnable, emitting reports, with documented thresholds per backend.
- A short `docs/testing/eval-runbook.md` (or section) noting known-good models +
  expected score ranges.

## Est. effort
Group A: ~1 day. Groups B & C: ~2–3 days (judge + nav harness + case authoring).

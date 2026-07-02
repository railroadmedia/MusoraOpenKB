# Tier 2 — Integration Tests (Fake LLM)

Deterministic, full-pipeline, no network. Proves the plumbing holds together:
`add` → compile → concept leaves → `distill` → layered tree with down/sideways links,
over real (small) Markdown fixtures. Runs in CI.

## Objective
Catch wiring/regression bugs across module boundaries that unit tests miss, while
staying fully deterministic by scripting the LLM instead of mocking each call
individually.

## Prerequisites
- Shared conventions (markers) — see `README.md`.
- Tiny committed fixture corpus — see `fixtures-datasets.md` (the `mini/` set).

## The fake LLM
Inject a deterministic fake at the single seam `openkb/agent/compiler.py::_llm_call`
(and `_llm_call_async`). It returns canned but **structurally valid** responses keyed
by `step_name`:

| `step_name` | Fake returns |
|---|---|
| `concepts-plan` | fixed JSON list of concept names derived deterministically from the doc (e.g. headings) |
| `concept: <name>` | short JSON `{brief, content}` echoing the name + a stable sentence |
| `topic-cluster` | deterministic grouping (e.g. first-letter or hash-bucketed) honoring fan-out |
| `topic-summary` | `"summary of <name>: " + joined child briefs` (truncated to token cap) |
| sideways relatedness | fixed pairs by index adjacency |

Implement as a `conftest.py` fixture `fake_llm` (a context manager patching the seam)
plus a small `tests/support/fake_llm.py` router. Keep responses schema-valid so
`_JSON_RESPONSE_FORMAT` parsing exercises real code paths.

## Fixtures
- `tests/fixtures/corpora/mini/` — ~8–15 tiny `.md` files, one coherent theme,
  committed (see `fixtures-datasets.md`). Small enough that a full pipeline run is
  instant.
- Reuse `conftest.py::kb_dir` skeleton; drop fixtures into `raw/`.

## Concrete test scenarios (`tests/test_pipeline_integration.py`)

1. **Full compile pass** — `add mini/` → assert one summary per doc, concept pages
   created, `[[wikilinks]]` present, hash registry populated, index/log updated.
2. **Distill pass** — run `distill` on the compiled flat concepts → assert:
   - one root file; ≥2 layers; every leaf reachable from root
   - fan-out within `[min,max]` at every node; depth ≤ `max_depth`
   - each non-leaf node has a down-link child index; briefs present
   - sideways `related` links present, bidirectional, same-layer only
3. **Idempotent re-distill** — running `distill` twice yields an equivalent tree
   (structure stable given the same fake outputs).
4. **Atomic rebuild** — fake raises mid-distill → **no partial tree left, leaves
   intact** (guards the data-loss bug; see tier-4).
5. **Lint clean** — `lint` on the produced tree reports zero ghost links, zero
   orphans.
6. **Query descent (fake agent)** — with a scripted `read_topic`/choose, the query
   agent traverses root→leaf and returns the expected leaf.
7. **Remove** — removing a source doc cleans its concept leaves and does not orphan
   parent pathway nodes.

## Golden files
Snapshot the **structure**, not the prose, so tests are stable:
- serialize the tree to a canonical form: sorted list of
  `relpath | layer | children[] | related[]` (+ frontmatter keys, excluding summary
  body text).
- store under `tests/golden/mini_tree.txt`; assert equality; provide an
  `OPENKB_UPDATE_GOLDEN=1` env to regenerate.

## How to run
```bash
pytest -m integration
pytest tests/test_pipeline_integration.py -x
```

## Definition of done
- One end-to-end integration test green in CI covering compile→distill→lint→query.
- Golden structural snapshot in place with an update mechanism.
- Atomic-rebuild failure path asserted.

## Est. effort
~2–3 days (fake-LLM router is the bulk; scenarios are quick once it exists).

# Tier 1 — Unit Tests

Fast, deterministic, LLM mocked. The existing suite is strong; this plan is about
(a) turning it into an enforced gate and (b) extending it to the new hierarchy code.

## Objective
Every pure function and small unit of logic is covered with no network and no real
LLM. This tier is the merge gate and must stay <~30s wall clock.

## Prerequisites
- Shared conventions applied (markers, `addopts`, CI) — see `README.md`.

## Part A — Enforce what exists
1. Add the CI `test.yml` workflow (see `README.md`) so `pytest` actually runs on
   push/PR. This is the single highest-leverage change; today nothing gates merges.
2. Confirm the full suite is green under pytest 9 / pytest-asyncio 1.3 locally
   (`pip install -e ".[dev]" && pytest`).
3. Add a coverage floor (optional): `pytest --cov=openkb --cov-fail-under=70` in CI.

## Part B — Unit coverage for the hierarchy feature
New/changed modules from the distillation plan: `openkb/topic_tree.py`,
`openkb/topic_tree_llm.py`, `openkb/agent/compiler.py` (`_write_concept` seam),
`openkb/lint.py`, `openkb/config.py`, `openkb/cli.py` (`distill`/`reindex`).

Mock the LLM at `_llm_call` (or inject deterministic `cluster`/`summarize`/`choose`
callables, matching the branch's existing dependency-injection pattern).

### Test files to add / extend
- `tests/test_topic_tree.py` (extend the branch's version)
- `tests/test_topic_tree_distill.py` (new — bottom-up orchestrator)
- `tests/test_topic_tree_sideways.py` (new — peer links)
- `tests/test_config_hierarchy.py` (new — `hierarchy:` config block)
- `tests/test_lint.py` (extend — nested + related link resolution)

### Concrete cases

**Config (`hierarchy:` block)**
- defaults resolve when block absent (back-compat with `topic_tree: true`)
- each key parses: `target_fanout`, `min_fanout`, `max_fanout`, `max_depth`,
  `node_summary_tokens`, `sideways_links`, `sideways_links_max`, `sideways_method`
- invalid values (fanout band inverted, negative, non-int) fall back to defaults +
  log a warning (mirror `resolve_entity_types` behavior)
- invariants enforced in code: `min_fanout <= target_fanout <= max_fanout`

**Distill orchestrator (deterministic injected callables)**
- N leaves, fan-out K → correct number of layers ≈ `ceil(log_K(N))`
- **always exactly one root file** (0, 1, 2, K, K+1, 1000 leaves)
- **always ≥2 layers** even for tiny input (e.g. 3 leaves)
- terminates at `max_depth` (degenerate clusterer returning one all-inclusive group
  does not infinitely recurse)
- each parent node written with correct `layer`, `children`, and down-link index
- empty/one-leaf KB handled without crashing
- leaves are never mutated (source of truth preserved)

**Sideways links**
- top-`sideways_links_max` peers per node, no more
- links are **bidirectional** (A→B implies B→A)
- links only ever point to existing **same-layer** nodes (never up/down/cross-layer)
- disabled cleanly when `sideways_links: false`
- self-links never produced

**Compiler `_write_concept` seam**
- with `topic_dir=None` → writes flat `concepts/<stem>.md` (unchanged behavior)
- with `topic_dir` set → writes under that dir, basename unchanged
- path-escape guard still holds for nested dirs

**Lint link resolution (extend existing)**
- nested concept resolvable by bare stem, `concepts/<stem>`, and full rel path
- `related` (sideways) wikilinks resolve and are not stripped as ghosts
- `_topic.md` files excluded from concept target set

## How to run
```bash
pytest -m "not llm"
pytest tests/test_topic_tree_distill.py -x
```

## Definition of done
- CI runs `pytest` on every PR and is required to pass.
- New hierarchy modules at ≥80% line coverage.
- All structural invariants (one root, ≥2 layers, bounded depth/fanout, bidirectional
  sideways links) have at least one deterministic unit test each.

## Est. effort
Part A: ~0.5 day. Part B: ~2–3 days (tracks feature build).

# OpenKB Testing Strategy — Index

Execute-later plans for a comprehensive automated testing strategy, with emphasis on
the **smart hierarchy distillation** feature (see
`../smart-hierarchy-distillation-plan.md`).

## The four tiers

| Tier | LLM | Deterministic? | CI? | Plan |
|---|---|---|---|---|
| 1. Unit | mocked | yes | yes | [tier-1-unit.md](tier-1-unit.md) |
| 2. Integration (fake LLM) | scripted fake | yes | yes | [tier-2-integration-fake-llm.md](tier-2-integration-fake-llm.md) |
| 3. Eval (real LLM, local) | real, opt-in | no (property/rubric) | no | [tier-3-eval-real-llm.md](tier-3-eval-real-llm.md) |
| 4. Regression | either | yes | yes | [tier-4-regression.md](tier-4-regression.md) |

Shared: [fixtures-datasets.md](fixtures-datasets.md) — corpora used by tiers 2–4.

## Current state (baseline, as of this writing)
- 55 test files, ~13.9k lines, all **unit** tests with LLM calls mocked.
- pytest 9.0.3 + pytest-asyncio 1.3.0; `testpaths=["tests"]`; no markers, no `addopts`.
- The single LLM seam: `openkb/agent/compiler.py::_llm_call()` / `_llm_call_async()`
  (wrap `litellm.completion` / `acompletion`; structured steps pass
  `_JSON_RESPONSE_FORMAT`).
- `conftest.py` fixtures: `kb_dir`, `sample_tree`. No document/corpus fixtures.
- **CI (`.github/workflows/publish.yml`) does NOT run tests** — publish-only.

## Shared conventions (apply once, used by all tiers)

### Markers & default selection — `pyproject.toml`
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
  "llm: exercises a real LLM; opt-in, skipped by default",
  "integration: full-pipeline, deterministic (fake LLM)",
  "regression: pins a previously fixed bug",
]
addopts = "-m 'not llm'"
```

### Commands
```bash
pytest                      # tiers 1,2,4 (real-LLM excluded by addopts)
pytest -m integration       # tier 2 only
pytest -m regression        # tier 4 only
OPENKB_LLM_TESTS=1 LLM_API_KEY=... pytest -m llm   # tier 3 (local, opt-in)
```

### CI
Add a `test.yml` workflow running `pip install -e ".[dev]"` then `pytest` on
push/PR to `main`/`dev`. It runs tiers 1/2/4 only (tier 3 is env-gated off).

## Suggested execution order
1. Shared conventions (markers + CI) — closes the "nothing runs" gap.
2. Tier 2 fake-LLM harness — deterministic pipeline gate for the new feature.
3. Tier 3 skeleton (gating + structural invariants) — cheap, deterministic even
   against a real model.
4. Tier 4 regression pins as bugs are fixed.
5. Tier 3 semantic (judge) + navigation evals once the feature lands.

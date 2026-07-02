# Tier 4 — Regression Tests

Deterministic pins for previously fixed bugs. Every bug fix lands with a test that
fails before the fix and passes after. Runs in CI.

## Objective
Prevent re-breakage. Each test names the bug, reproduces the exact failing condition
with the smallest possible setup, and asserts the corrected behavior.

## Conventions
- Marker `@pytest.mark.regression`.
- One test (or class) per bug; docstring links the commit/PR/issue.
- Prefer the fake-LLM seam (tier 2) or injected callables so pins stay deterministic.
- Name: `test_regression_<short-slug>`.

## Seed pins to write now (from review of `feat/concept-topic-tree`)

1. **Distill/reindex data loss on LLM failure**
   `bootstrap`/`distill` deletes flat concept leaves after reading them into memory,
   then calls the LLM. If the call raises, concepts are lost.
   *Test:* build flat concepts, inject a clusterer that raises, run distill, assert
   **all original leaf files still exist on disk** and no partial tree remains.
   (Locks in the atomic temp-swap fix from the plan, Phase 5.)

2. **Non-recursive reindex ignores an existing tree**
   `bootstrap` globs only top-level `*.md`, so re-running after a tree exists leaves
   nested concepts un-reincorporated and piles new ones at root.
   *Test:* build a tree, add new flat concepts, re-run distill, assert the whole
   corpus is re-incorporated (no stray flat leaves at root; counts match).

3. **Degenerate clustering recursion**
   A clusterer returning one all-inclusive group must not recurse to `max_depth` with
   silly `a/a/a/...` chains.
   *Test:* inject such a clusterer, assert a "no progress → stop" guard fires and
   depth stays bounded/sane.

4. **Sideways links must not survive as ghosts after a move**
   Moving a node between layers must keep `related` links resolving (bare-stem).
   *Test:* build tree with sideways links, move a node, run `lint`, assert zero ghost
   links.

5. **Single-root / min-2-layer invariants under edge sizes**
   *Test:* 1 leaf, 2 leaves, exactly `target_fanout` leaves — each yields one root and
   ≥2 layers, no crash.

## Ongoing rule
When any bug is fixed in `topic_tree`, `compiler`, `lint`, `converter`, `indexer`, or
`cli`, add a `@pytest.mark.regression` test in the same PR. Reference it in the PR
description.

## How to run
```bash
pytest -m regression
```

## Definition of done
- Pins 1–5 written and green (each demonstrably fails against the buggy version).
- Regression-test-with-every-fix noted in `CLAUDE.md` / PR template.

## Est. effort
~1 day for the seed pins; marginal thereafter (one test per fix).

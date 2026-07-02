# Test Fixtures & Datasets

Corpora used by tiers 2–4. Hierarchy distillation only works on **topically coherent,
multi-document** corpora (you need enough related concepts to cluster), so pick
coherent sets and keep them small.

## Layout & git policy
```
tests/fixtures/corpora/
  mini/          # ~8–15 tiny .md, one theme — COMMITTED (tier 2 / CI)
  wiki-domain/   # 20–40 Wikipedia articles, one category — fetched (gitignored)
  docs-site/     # a project's docs/ export — fetched (gitignored)
  gutenberg/     # 1 public-domain book, chapter-split — fetched (gitignored)
  own-domain/    # music-education transcripts/blog posts — fetched (gitignored)
```
- Commit only the tiny `mini/` set (license-clean, hand-written or trimmed).
- Everything else is pulled by a script into a **gitignored** path so large data
  never enters the repo. `tests/fixtures/corpora/` is already covered by the repo's
  ignore of `raw/`-style artifacts; add an explicit ignore if needed, with a
  `!mini/` exception.

## Fetch script
`scripts/fetch_test_corpora.py` (network, run manually):
- `--set mini|wiki-domain|docs-site|gutenberg|own-domain|all`
- deterministic subset selection (pin article titles / file lists in the script so
  runs are reproducible)
- convert to `.md` on download; record provenance + license in a `MANIFEST.json` per
  set.

## Recommended sources

| Set | Source | License | Why | Exercises |
|---|---|---|---|---|
| `mini` | hand-authored / trimmed | own | instant, deterministic, CI-safe | tier 2 wiring, golden snapshot |
| `wiki-domain` | Wikipedia REST/export, one category (e.g. a music-theory or ML sub-tree) | CC BY-SA | coherent, many related concepts → tests clustering + category naming | tier 3 quality |
| `docs-site` | a permissively-licensed project's `docs/` | MIT/CC | naturally hierarchical → ground truth for "did it recover real structure" | tier 3 quality/nav |
| `gutenberg` | Project Gutenberg book, chapter-split | public domain | long docs → PageIndex path | tier 2/3 long-doc |
| `own-domain` | your music-education blog posts / video transcripts | internal | most representative; matches the real use case | **tier 3 navigation (priority)** |

Also available for free: the repo's own `examples/` folder already contains
OpenKB-generated artifacts from a sample paper — a ready-made micro-fixture.

## Navigation eval cases
Per corpus that feeds tier-3 group C, add `nav_cases.yaml`:
```yaml
- question: "How does X relate to Y?"
  expected_leaf: "some-concept-stem"
- question: "What is the definition of Z?"
  expected_leaf: "z-concept-stem"
```
Author 10–20 per corpus; keep expected leaves as stems so they survive tree moves.

## License hygiene
- Only commit `mini/` (content you own/control).
- For fetched sets, keep a `MANIFEST.json` with source URL + license per file; never
  commit CC BY-SA / copyrighted text into the repo — fetch at runtime only.

## Definition of done
- `mini/` committed; `fetch_test_corpora.py` pulls the other sets reproducibly.
- Each fetched set has a `MANIFEST.json`.
- `nav_cases.yaml` authored for at least `own-domain` and one public set.

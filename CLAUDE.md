# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OpenKB (Open LLM Knowledge Base) — a Python CLI that compiles raw documents into a structured, interlinked wiki-style knowledge base using LLMs, powered by PageIndex's vectorless, reasoning-based retrieval. This is `railroadmedia/MusoraOpenKB`, Musora's fork of the upstream `VectifyAI/OpenKB`. The published PyPI package is `openkb`.

Two conceptual layers:
- **Wiki foundation** — compiles and maintains the knowledge (`init`, `add`, `recompile`, `remove`, `list`, `status`, `watch`, `lint`).
- **Generators** — turn the compiled wiki into output (`query`, `chat`, `visualize`, `skill`, `deck`).

### Hierarchical distillation (experimental, `topic_tree: true`)
A second pass builds a multi-layer navigable hierarchy over the flat concept
leaves. Two engines live in `openkb/topic_tree.py`:
- `bootstrap()` (`openkb reindex`) — top-down cold-start seed.
- `distill()` (`openkb distill`) — **bottom-up RAPTOR-style distillation** (the
  intended approach): clusters concepts into LLM-named, `AGENTS.md`-guided,
  sized categories, summarizes each into a parent *pathway* node (`_topic.md`
  with `layer`/`children`/`related` frontmatter), adds bidirectional **sideways
  links** between same-layer peers, and repeats up to a single root. Sizing is
  configured under a `hierarchy:` block (see `openkb/config.py::resolve_hierarchy`
  and `docs/smart-hierarchy-distillation-plan.md`). Leaf concept files are the
  source of truth; the tree is built in a staging dir and atomically swapped in,
  so a mid-build LLM failure never loses concepts. LLM callables live in
  `openkb/topic_tree_llm.py` (`make_distill_cluster`/`make_distill_summarize`/`make_relate`).

### Generative enrichment (experimental, `enrichment.enabled: true`)
A third pass (`openkb enrich`) authors a paired `<concept>.enrich.md` for each
grounded concept: grounded *elaboration* plus verify-gated *inferred* world
knowledge (tagged `> [!inferred]`), examples, and cross-links. The grounded
concept file is the source of truth and is never modified; enrichment files are
machine-owned and regenerable (idempotent via a `source_hash`). Engine +
LLM callables in `openkb/agent/enricher.py` (`enrich`/`make_generate`/`make_verify`);
config via `openkb/config.py::resolve_enrichment` (the `enrichment:` block);
guidance from the `## Enrichment` section of `AGENTS.md`. Runs before `distill`,
which excludes `*.enrich.md` from leaves and carries each as an attachment so the
pairing survives the atomic rebuild. See `docs/enrichment-layer-plan.md`.

## Commands

This project uses `uv` (see `uv.lock`). Dev dependencies (`pytest`, `pytest-asyncio`) are in the `dev` extra.

```bash
# Install for development (editable)
pip install -e ".[dev]"        # or: uv sync --extra dev

# Run the full test suite
pytest                          # testpaths=tests is configured in pyproject.toml

# Run a single test file / test
pytest tests/test_compiler.py
pytest tests/test_compiler.py::test_name -x

# Exercise the CLI locally
openkb --help                   # entry point: openkb.cli:cli
python -m openkb --help         # equivalent (openkb/__main__.py)
```

There is no linter/formatter config checked in; match surrounding style. CI (`.github/workflows/publish.yml`) only builds and publishes to PyPI on a `v*` tag — it does not run tests, so run `pytest` yourself before pushing.

## Releasing

Version is derived from git tags via `hatch-vcs` (no static version in `pyproject.toml`). Release = tag + push:
```bash
git tag -a vX.Y.Z -m "Release X.Y.Z" && git push origin vX.Y.Z
```
The workflow builds, publishes via PyPI OIDC trusted publishing, and creates a GitHub Release with auto-generated notes. Tags must be PEP 440 (`v0.1.4`, `v0.2.0rc1`). Never `python -m build && twine upload` locally — that skips the GitHub Release and PyPI rejects duplicate versions.

## Architecture

### On-disk knowledge base layout (created by `openkb init`)
A KB lives in a user directory, not in this repo. The structure the code reads/writes:
- `raw/` — source documents the user adds
- `wiki/` — the compiled output: `summaries/`, `concepts/`, `sources/` (+ `sources/images/`), `explorations/`, `reports/`, and entity pages. Plain Markdown with `[[wikilinks]]` (Obsidian-compatible, follows Google OKF).
- `wiki/AGENTS.md` — the LLM's instruction manual for how to maintain the wiki. Read from disk at runtime, so edits take effect immediately.
- `.openkb/` — state: `config.yaml` (per-KB settings) and `hashes.json` (the dedup registry).

### Code structure (`openkb/`)
- `cli.py` (~2600 lines) — all Click commands. `@cli.group()` is the root; `skill` and `deck` are sub-groups. Start here to trace any command.
- `config.py` — loads/merges per-KB `.openkb/config.yaml` with a global `~/.config/openkb/global.yaml`; resolves entity types; stashes process-wide LiteLLM `extra_headers`/`timeout`.
- `state.py` — `HashRegistry`, the SHA-256-keyed registry (`hashes.json`) that makes `add` idempotent and tracks doc→wiki-page provenance.
- `converter.py` / `url_ingest.py` / `images.py` — ingestion: markitdown for short docs, PageIndex for long PDFs (`pageindex_threshold`, default 20 pages), trafilatura for URLs, image extraction.
- `indexer.py` — PageIndex tree indexing (local by default; PageIndex Cloud optional via `PAGEINDEX_API_KEY`).
- `locks.py` — cross-platform file locking (`portalocker`) and atomic writes (`atomic_write_text/json`). Use these for any KB file mutation; do not write KB files directly.
- `lint.py` — structural + knowledge-health checks (`openkb lint`).
- `agent/` — the LLM-driven work: `compiler.py` (the big one — turns documents into summary/concept/entity pages with cross-document synthesis), `query.py`, `chat.py` + `chat_session.py`, `linter.py`, `skill_runner.py`, `tools.py` (agent tool definitions).
- `skill/` — Skill Factory: `creator.py`, `generator.py`, `validator.py`, `evaluator.py`, `workspace.py`, `marketplace.py`. Distills a portable agent skill from the wiki.
- `deck/` — single-file HTML slide-deck generation (`creator.py`, `validator.py`).
- `templates/graph.html`, `prompts/` — the visualize template and prompt markdown.

### LLM access
All model calls go through **LiteLLM** (pinned to `1.87.2` for a security fix), using the OpenAI Agents SDK as the agent framework. Models are configured as `provider/model` (e.g. `anthropic/claude-sonnet-4-6`); OpenAI models may omit the prefix. API key comes from `LLM_API_KEY` in `.env`.

**Dependencies are pinned exactly on purpose** (supply-chain caution — see the litellm poisoning incident note in `pyproject.toml`). Bump deliberately after vetting; don't loosen pins casually.

### Packaging note
`pyproject.toml` force-includes three skills (`openkb-deck-neon`, `openkb-deck-editorial`, `openkb-html-critic`) from `skills/` into the wheel under `openkb/_skills/` so `deck new`/`--critique` work right after `pip install`. `scan_local_skills` looks under `openkb/_skills/`.

## Conventions

- All file mutations to a KB go through `openkb/locks.py` (locking + atomic write) — never write KB state files directly.
- The compiler is provider-aware: e.g. it strips `cache_control` for non-Anthropic providers (commit e896070). Keep provider-specific quirks isolated rather than assuming Anthropic.
- The agent-facing skill (`skills/openkb/SKILL.md`) is intentionally **read-only** — it must not run `add`, `remove`, or `lint --fix` without an explicit user ask.

# Smart Hierarchy Distillation & KB Building — Plan

Status: proposal · Builds on the `feat/concept-topic-tree` branch.

## Goal

Let an LLM build a multi-layer concept hierarchy for an OpenKB knowledge base by
**distilling upward** from the existing flat concept pages. The LLM invents its own
categories for each layer, guided by `wiki/AGENTS.md` and by configurable sizing
targets. Higher layers are general, thematic, encapsulating, and archetypal; lower
layers are highly specific facts and concepts. Each non-leaf node is a *pathway*
that guides an agent down to the relevant lower-layer elements (and sideways to
related branches). Depth is dynamic but always ≥2 layers, always terminating at a
single root file.

This is a deliberate pivot from the branch's current **top-down** `bootstrap()`
(which produces a generic root summary) to a **bottom-up** distillation
(RAPTOR-style), which yields a genuinely distilled root — the better fit for this
goal.

## The core shape

```
Pass 1 (exists today):  raw docs ──normal compile──▶ flat concept leaves   (wiki/concepts/*.md)
Pass 2 (new):           leaves ──distill upward──▶ layer N ─▶ ... ─▶ layer 1 ─▶ single root
```

Each non-leaf node is a pathway file: a distilled summary of everything beneath it,
plus links **down** to its children (with one-line briefs) and **sideways** to
related peers at the same layer. An agent reads the root, follows the most relevant
child pathway, descends, and can jump laterally to a related pathway without
backtracking to root.

Three link directions per node:
- **up** — implicit (the tree parent)
- **down** — the pathway child index
- **sideways** — top-K related peers at the same layer (the LeanRAG "semantic
  islands" fix; see Phase 2 / Phase 3)

## Sizing recommendations (starting defaults, all configurable)

Configured under a `hierarchy:` block in `.openkb/config.yaml`. Defaults are tuned
so each pathway file is cheap to load, parent summaries can genuinely encapsulate
their children, and depth stays shallow.

| Config key | Default | Controls | Rationale |
|---|---|---|---|
| `hierarchy.target_fanout` | **8** | children per node the LLM aims for | Shallow tree; one summary can encapsulate them; agent choice isn't overwhelmed |
| `hierarchy.min_fanout` / `max_fanout` | **4 / 12** | allowed band per layer | Avoids degenerate 2-way or 30-way splits while fitting natural category counts |
| `hierarchy.max_depth` | **6** | safety cap on layers | At fan-out 8, depth 6 ≈ 32k leaves; backstop only, real depth is dynamic |
| `hierarchy.node_summary_tokens` | **~600 target / 1000 hard cap** | size of each pathway file's distilled summary | Cheap descent; child index adds only ~`fanout × 20` tokens |
| `hierarchy.sideways_links` | **true** | enable peer links | Lateral navigation across branches |
| `hierarchy.sideways_links_max` | **5** | max related-peer links per node | Top-K keeps lateral nav useful without noise |
| `hierarchy.sideways_method` | **llm** | how relatedness is computed | `llm` stays true to OpenKB's vectorless ethos; `embedding` optional at extreme scale |

**Invariants (not configurable):** always ≥2 layers; always exactly one root file.
`target_fanout` drives layer count ≈ `ceil(log_fanout(num_leaves))`.

**Worked examples (defaults):**
- 1,000 concepts → ~125 → ~16 → ~2 → **1 root** ≈ 4–5 layers.
- 50 concepts → ~7 → **1 root** = 2 layers (the minimum).

Depth falls out of the math; nobody sets it directly.

## Plan

### Phase 0 — Config & guidance (small)
- Add the `hierarchy:` config block above (supersedes the branch's flat
  `topic_tree: true` flag; keep back-compat if cheap).
- Add a `## Hierarchy` section to `wiki/AGENTS.md` describing how *this* KB should be
  themed (its purpose, what "general vs specific" means here). This text is injected
  into every clustering / category-naming / summarization prompt so categories
  reflect the KB's purpose, not generic topics.

### Phase 1 — Pass 1 stays as-is
- No change to the compiler. Normal `openkb add` / `recompile` produces the flat
  concept leaves that feed Pass 2.

### Phase 2 — Pass 2: bottom-up distillation engine (the heart)
- Add a `distill()` orchestrator in `openkb/topic_tree.py`, alongside the existing
  `bootstrap()`. Loop upward:
  1. Take the current layer's nodes (start = leaf concepts).
  2. Ask the LLM to **invent sized categories** for them, guided by `AGENTS.md` +
     the fan-out band. (Extend the existing `cluster()` callable to also *name*
     categories and respect sizing.)
  3. For each category, **summarize its members into a new parent pathway node**
     (general, encapsulating, with down-links). (Reuse `summarize()` +
     `write_topic_md()`.)
  4. **Build sideways links for the layer just created** (see below).
  5. Repeat on the new parent layer.
  6. **Stop when one node remains** → that's the root. If a layer collapses toward
     ≤ target but >1, do a final summarization of the survivors into a single root.
     Always ≥2 layers.
- Extend the LLM callables in `openkb/topic_tree_llm.py` to accept the AGENTS.md
  guidance + sizing parameters.

**Sideways links (per-layer step, LeanRAG-style):**
- Runs at the end of each layer's distillation, once that layer's nodes exist.
- Default `llm` method: batch the layer's node summaries to the LLM, ask for the
  most-related pairs, keep top-`sideways_links_max` per node, write them
  **bidirectionally**. For very large layers, scope candidates to cousins (same
  grandparent) plus a sampled global set to bound the prompt; `embedding` method is
  the escape hatch at extreme scale.

### Phase 3 — Pathway node format
- Each layer node (`_topic.md`) gets frontmatter (`layer` level, `children` list,
  `related` list) and a body: the distilled summary, a linked child index with
  briefs, and a "Related pathways" section. This *is* the navigation affordance.
- The branch's lint changes (bare-stem + recursive wikilink resolution) already keep
  `[[wikilinks]]` — including `related` links — valid as files move between layers.

### Phase 4 — CLI + agent integration
- Repurpose the branch's `reindex` command (or add `openkb distill`) to run Pass 2.
- Reuse the branch's `read_topic` query tool + tree-descent prompt so the agent
  navigates root→leaf. Add one rule: "if a related/sibling pathway looks more
  relevant, follow the `related` link instead of backtracking to root."

### Phase 5 — Rebuild safety & refresh
- **Build into a temp tree, then atomically swap.** This fixes the data-loss bug in
  the branch's current `bootstrap` (it deletes leaves before the LLM call — if the
  call fails, concepts are lost). Leaf concept files stay the source of truth; all
  upper layers are regenerable from them.
- Re-distillation = re-run Pass 2. Treat incremental / partial re-distill as a later
  optimization, not v1.

## Decisions already made (recommended defaults)
- **Sizing metric:** drive layering by target children-per-node (predictable), with
  token-size as a soft cap per pathway file.
- **Build on the branch, don't restart:** keep `topic_tree.py`'s
  `cluster/summarize/write_topic_md/read_topic` and the lint link-resolution; add
  `distill()` as the new bottom-up orchestrator and deprecate top-down `bootstrap()`.
- **Sideways links are in v1** (LeanRAG approach), computed per layer via the LLM.

## Background / why
See the companion R&D thread: the approach is *hierarchical/recursive summarization
+ taxonomy induction for multi-resolution retrieval*; it is not a graph database.
The sideways-link requirement comes from the documented "semantic islands" failure
mode in GraphRAG/HiRAG, which LeanRAG fixes by adding explicit inter-cluster
relations. RAPTOR (embed→cluster→summarize) is the canonical build-side technique
this plan adapts, substituting LLM-named, AGENTS.md-guided categories for RAPTOR's
GMM clusters.

# OpenKB MCP Server on Cloudflare Workers — Plan

Status: proposal · Serves an OpenKB-compiled wiki to claude.ai as a remote MCP
custom connector, so web-Claude navigates the KB agentically (search → descend →
read → follow links) instead of embedding top-k retrieval.

## Goal
Give the claude.ai web interface the same navigate-and-read loop Claude Code has,
over an OpenKB wiki, by exposing **navigation tools** from a Cloudflare Worker.
Read-only. claude.ai is the agent; the Worker serves tools.

## Why this shape
- claude.ai custom connectors need a **public HTTPS MCP endpoint** (Streamable
  HTTP / SSE) with **OAuth**.
- Agentic navigation beats embedding top-k for a *structured* wiki: it preserves
  the distilled hierarchy, concept coherence, and `[[wikilinks]]` we built.
- Reuse the existing artifacts directly: `index.md`, the `_topic.md` pathway tree,
  concept/summary/entity pages, and paired `.enrich.md`.

## Stack (all OSS / first-party Cloudflare)
- **`agents` SDK `McpAgent`** — remote MCP server + Streamable HTTP transport.
- **`@cloudflare/workers-oauth-provider`** — OAuth for a private KB.
- **Search:** start with a **bundled JSON keyword index** (fine for hundreds of
  pages); migrate to **D1 + FTS5 (BM25)** when the KB grows past ~1–2k pages.
- **Page bodies:** bundled as a data module (small KB) or **R2/KV** (large).
- **Reference design:** `openkb/agent/query.py` + `openkb/agent/tools.py`
  (`read_topic` → descend → `read_file`); port its semantics to TS.

## Location & conventions
- Implement under railenvironment **`cloudflare/openkb-mcp/`**, mirroring
  `cs-responder` (wrangler, `cf-cli` docker profile, a `sync-kb` bake step).
- Build/deploy via the `cf-cli` container:
  `docker compose --profile cf run --rm -w /workspace/openkb-mcp cf-cli npm run deploy`.
  `CLOUDFLARE_API_TOKEN` from `credentials/credentials` (no browser login).

## Tools exposed (MCP)
- `search_kb(query, limit=8)` → `[{path, title, snippet, area, layer}]`.
- `get_index()` → the `index.md` catalog (the map).
- `read_topic(path="")` → a pathway node: distilled summary + child topics (with
  briefs) + concept leaves + `related` sideways links. Mirrors `read_topic_node`.
- `read_page(path)` → a concept / summary / entity page body (bare-stem OR full
  path; resolves nested concepts, like the branch's lint link resolution).
- `read_enrichment(concept)` → the paired `.enrich.md`, clearly labeled generated
  (so Claude treats `> [!inferred]` as non-authoritative).
- (optional) `list_entities(type?)` / `read_entity(name)`.

## KB bake pipeline (like cs-responder)
- A **`sync-kb`** step copies a compiled OpenKB wiki into the service and generates
  a data module (`src/kb-data.ts`: path → `{frontmatter, body}`) plus a search
  index — mirroring `cs-responder/scripts/sync-kb.sh` → `gen-kb.mjs`.
- **Source is parameterized** (e.g. a Pianote KB, or any OpenKB `wiki/`); this is
  distinct from `cs-responder`, whose baked KB is the customer-support domain.
- **Freshness:** redeploy on KB change (read-only). Optional scheduled rebuild.

## Search design
- **v1:** in-memory keyword/substring ranking over the bundled index. Rank
  title/`description` hits above body hits; return a snippet + the node's
  area/layer so Claude can **descend from a hit**, not just read it.
- **v2:** **D1 FTS5** (BM25) seeded from the same bake step; migrate at scale.
- Always return the hierarchy path with each result.

## Auth
- OAuth via `workers-oauth-provider` (claude.ai supports OAuth connectors). For
  internal-only use, a shared-secret bearer gate is an acceptable v1. Read-only scope.

## Navigation-parity checklist (behave like Claude Code)
- **Map:** `get_index()` + `read_topic("")` give a clear starting map.
- **Descent:** `read_topic` returns child topics + briefs + leaves + related links.
- **Cross-links:** `[[wikilinks]]` resolve via `read_page` by bare stem or path.
- **Grounded-first:** enrichment/inferred content is labeled non-authoritative in
  tool output so Claude weights grounded pages.
- **Search → jump → descend:** snippets + paths let Claude land then walk down.

## Phases
0. **Scaffold** `cloudflare/openkb-mcp/` (wrangler.toml, package.json, `McpAgent`
   entry, cf-cli wiring).
1. **Bake pipeline** — `sync-kb` (compiled wiki → `kb-data.ts` / D1 seed) + gen script.
2. **Tools** — implement `search_kb`, `get_index`, `read_topic`, `read_page`,
   `read_enrichment` (+entities). Port `query.py` semantics: bare-stem + nested
   wikilink resolution; exclude `_topic.md`/`*.enrich.md` from concept lists;
   surface `related` links.
3. **Transport + OAuth** — `McpAgent` over Streamable HTTP + `workers-oauth-provider`.
4. **Deploy + connect** — deploy via cf-cli; register as a claude.ai custom
   connector; verify the end-to-end navigate loop from the web UI.
5. **Tests** — vitest (`@cloudflare/vitest-pool-workers`) over a fixture wiki:
   search ranking, wikilink resolution, `read_topic` shape, enrichment labeling.
   A small parity test mirroring `query.py` behavior to catch TS-port drift.
6. **Docs** — README: deploy, connect to claude.ai, refresh the KB.

## Risks
- **Port drift** from the OpenKB Python tools → keep a parity test; document that
  the TS tools mirror `query.py`.
- **Workers limits** (CPU/mem/bundle) → bundle small KBs as data, R2 + D1 for large.
- **claude.ai OAuth registration friction** → document the connector flow explicitly.

## Est. effort
Scaffold + bake ~1d · tools ~1d · transport + OAuth ~0.5–1d · tests + docs ~0.5d.

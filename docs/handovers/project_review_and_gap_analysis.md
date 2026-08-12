# Refreshed Project Review & Gap Analysis Report (v2)

Following the successful implementation of all Phase 1–5 improvements, the **InvestYo Quant Platform** is in a highly performant and stable state. This second review identifies next-generation architectural, security, and operational improvements across the codebase.

---

## 1. Web Application (React PWA) Resilience

| Gap Identified | Current Codebase Status | Recommended Solution |
| :--- | :--- | :--- |
| **Offline State Resilience** | `webapp/` fetches the pilot catalog and performance curves live from the FastAPI backend. If the daemon is temporarily down, the app degrades to empty screens with loading errors. | Implement `localStorage` caching inside `webapp/src/api/client.ts` to cache the pilot list and performance metrics, allowing the PWA to load cached historical data when offline. |
| **PWA Service Worker Telemetry** | Service workers are registered, but there is no operator UI feedback indicating whether they are active, caching successfully, or running on the latest updated version. | Add PWA updates and cache-status indicators inside the settings/status drawer of the PWA dashboard. |

---

## 2. Database & MCP Security Hardening

| Gap Identified | Current Codebase Status | Recommended Solution |
| :--- | :--- | :--- |
| **Database-Level Read-Only Enforcement** | `investyo_mcp_server.py::query_investyo_db` checks for keywords (`INSERT`, `UPDATE`, etc.) using regex to prevent mutations. While functional, regex-based guards are bypassable. | Enforce database-level read-only connections. Open SQLite database connections in read-only mode `sqlite3.connect('file:quant_platform.db?mode=ro', uri=True)`. |
| **Supabase/PostgreSQL Read-Only Mode** | Dual-backend seams support PostgreSQL/Supabase, but read-only queries are executed using standard SQLAlchemy engines that do not restrict DDL/DML. | Create a dedicated read-only connection pool using a restricted database user role for all MCP query executions. |

---

## 3. SEC EDGAR Ingestion & Data Automation — ✅ RESOLVED 2026-07-16

| Gap Identified | Current Codebase Status | Recommended Solution |
| :--- | :--- | :--- |
| ~~**EDGAR Backfill Automation**~~ | ~~PIT historical fundamentals database rows are populated only when the operator manually invokes `scripts/backfill_edgar_fundamentals.py` for specific tickers.~~ **Investigation found the weekly cron/launchd automation this row asked for already existed (`deploy/crontab.txt`) but had never actually run**: the script lacked a repo-root `sys.path` shim, so the direct-path invocation cron/the MCP tool both use died at import (`ModuleNotFoundError: No module named 'data'`) — silently failing every Sunday, and breaking `trigger_edgar_backfill` for every input, not just "all". A second bug meant `--tickers all` resolved to a literal ticker named `"ALL"` (zero rows) even once the import worked. Both fixed; `all` now resolves via a shared `data.portfolio_sync.resolve_universe()` to held ∪ watchlists ∪ `DEFAULT_TICKERS`, used identically by the CLI and the MCP tool. A macOS launchd job (`scripts/com.investyo.weekly-edgar.plist`) was added alongside the existing Linux cron entry. See `CLAUDE.md`'s `scripts/backfill_edgar_fundamentals.py` entry for the full contract. | Done — no further action; the recommended weekly-cron shape was directionally right, the script underneath it was silently broken. |
| ~~**Filing-Date Parsing Latency**~~ | ~~Direct XBRL parsing on backfill is slow due to synchronous filing lookups and SEC rate-limits (10 requests/sec).~~ **This diagnosis was wrong**: the fetcher makes ~1 HTTP request per ticker total (one shared `company_tickers.json` for the whole run, then one `companyfacts` fetch per ticker) — at the existing 150ms throttle, 500 tickers is ~75s of rate-limit budget, nowhere near the bottleneck. The actual cost is each `companyfacts` payload being multi-MB JSON, i.e. download wait — an async queue targets nothing real. Fixed with a `ThreadPoolExecutor` (`EDGAR_MAX_CONCURRENCY`, default 4 — a memory knob, not a rate-limit knob) over a made-thread-safe throttle, not an async batch queue. | Done — implemented as bounded thread-pool concurrency instead of an async queue; see CLAUDE.md for why. |

---

## 4. LLM Commentary & Diagnostics — ✅ RESOLVED 2026-07-17

| Gap Identified | Current Codebase Status | Recommended Solution |
| :--- | :--- | :--- |
| ~~**LLM Key Misconfiguration Surfacing**~~ | ~~If `GEMINI_API_KEY` or `OPENAI_API_KEY` is missing or invalid, LLM analyst narratives degrade silently to `null`. The user has no UI visibility into this.~~ **Closed, but deliberately NOT as a live "connectivity probe" endpoint.** A probe would have to *make an LLM call to test a key* — spending the operator's money on a call that produces no analyst value, on an endpoint reachable by anyone holding the fail-open read token. Instead `llm/status_store.py` records what actually happened on the last **real** call (written from `llm/providers.py`'s own except blocks, classified by exception *type name* only so no SDK is ever imported — the google.genai bad-key case is HTTP 400 + `API_KEY_INVALID`, matched exactly and never guessed) and `GET /llm/status` reports it. A verdict is bound to the key that produced it by a truncated one-way fingerprint, so **fixing a key clears the warning instantly, with zero LLM calls** — no probe needed. Only an `auth` rejection ever renders as "invalid key"; a rate-limit/network/timeout is surfaced as telemetry but never as a key problem. Presence checks alone were insufficient because a garbage-but-present key showed 🟢 ready and still produced `null` — the last-call verdict closes that gap. Surfaced on the PWA `/settings` "AI providers" section + a gear-icon attention dot, and as a 5th `invalid_key` state in the Streamlit AI Control Center (added additively — the four-state truth table and Gravity step_86 check 4 are unchanged). New env var: `LLM_STATUS_MAX_AGE_HOURS`. | Done — implemented as last-call telemetry rather than a probe endpoint; see CLAUDE.md's `llm/status_store.py` + `api/pilots_api.py` entries for why. |

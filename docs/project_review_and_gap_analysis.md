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

## 3. SEC EDGAR Ingestion & Data Automation

| Gap Identified | Current Codebase Status | Recommended Solution |
| :--- | :--- | :--- |
| **EDGAR Backfill Automation** | PIT historical fundamentals database rows are populated only when the operator manually invokes `scripts/backfill_edgar_fundamentals.py` for specific tickers. | Add an optional weekly task to `main_orchestrator.py` or a dedicated system cron utility to automatically query SEC EDGAR for new filings for S&P 500 components. |
| **Filing-Date Parsing Latency** | Direct XBRL parsing on backfill is slow due to synchronous filing lookups and SEC rate-limits (10 requests/sec). | Implement a multi-ticker batch queue that respects SEC headers while processing filings asynchronously. |

---

## 4. LLM Commentary & Diagnostics

| Gap Identified | Current Codebase Status | Recommended Solution |
| :--- | :--- | :--- |
| **LLM Key Misconfiguration Surfacing** | If `GEMINI_API_KEY` or `OPENAI_API_KEY` is missing or invalid, LLM analyst narratives degrade silently to `null`. The user has no UI visibility into this. | Expose API connectivity status endpoints (e.g. `/api/status/llm`) to show helpful configuration warnings in the PWA when keys are missing. |

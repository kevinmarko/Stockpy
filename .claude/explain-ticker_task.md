# Explain This Ticker — Task Tracker

> Companion to `explain-this-ticker_implementation_plan.md`. Save as
> `.claude/explain-this-ticker_task.md`.

## §0 Dependency Check (blocking — do first)
- [ ] Confirm no existing `/profile` wrapper in `data/fmp_client.py`
- [ ] Verify real FMP `/profile` response shape against a live key (field names in this plan are unverified guesses)
- [ ] Confirm `GET /data/fundamentals/{symbol}` current response shape
- [ ] Confirm whether a generic price-chart component already exists
- [ ] Confirm whether tickers are already clickable anywhere today
- [ ] Confirm `api/data_api.py` routing conventions before naming new endpoint

## Wave 0 — Scaffold (1 agent)
- [ ] `ExplainTickerData` TS interface, all fields nullable
- [ ] Stub `GET /data/explain/{symbol}` returning honest nulls

## Wave 1 — Parallel (4 agents)
- [ ] **A**: `company_profile()` wrapper, `FMP_PROFILE_ENABLED` gate, `scripts/verify_fmp_profile.py`
- [ ] **B**: per-symbol why-it's-here composition (reuse Universe Transparency plan's logic if it exists)
- [ ] **C**: factor-breakdown adapter (read-only, reuses Signal Breakdown's data)
- [ ] **D**: price-action data fetch + chart component

## Wave 2 — Serialized (1 agent)
- [ ] Drawer/panel shell component
- [ ] Global trigger wiring across every existing symbol-rendering location

## Wave 3 — Docs (1 agent)
- [ ] `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`
- [ ] `docs/FMP_INTEGRATION.md` new §10
- [ ] `.env.example`
- [ ] `helpContent.ts`

## Fabrication-risk checklist (verify before merge)
- [ ] `/profile` failure → "unavailable," never empty-string-as-real-answer
- [ ] Flag-off renders identically to fetch-failure (both honestly "unavailable")
- [ ] Untracked symbol clearly says "not tracked," never implies watchlisted
- [ ] Empty DailySignals → honest empty state, never a zeroed placeholder
- [ ] Bars gap → honest gap indicator, never a fabricated flat line
- [ ] No new synthesized "summary score" invented across the four sections

## Claude audit protocol (6-8 agents)
- [ ] Auditor 1 — WP-A: `verify_fmp_profile.py` run + flag-off byte-identical check
- [ ] Auditor 2 — WP-B: real held / watchlisted-only / untracked symbol, three real states
- [ ] Auditor 3 — WP-C: diff against Signal Breakdown's own live render, same cycle
- [ ] Auditor 4 — WP-D: real bars-gap symbol renders honest gap
- [ ] Auditor 5 — WP-E: **live non-mock click-through** of every wired screen (not a grep)
- [ ] Auditor 6 — cross-cutting: full fabrication-risk checklist against real combined degraded state
- [ ] (If 8) Auditors 7-8 — second independent pass on Auditor 5's scope

## Explicitly NOT in this task list
- Any change to `signals/` scoring or universe-resolution logic
- A new "add to watchlist" flow (reuse the existing one)
- A synthesized cross-section summary score

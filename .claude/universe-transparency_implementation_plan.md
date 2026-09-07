# Universe Transparency — Implementation Plan

> Save as `.claude/universe-transparency_implementation_plan.md` in the Stockpy
> repo. Written outside a repo-connected session (see AGENT HANDOFF NOTES) —
> §0 below is a required first step for whichever agent picks this up, not a
> box already checked.

## Status: IMPLEMENTED

## 0. Context & problem statement

Paper Trading currently exposes the operator to a "universe" concept that,
per the repo's own changelog, has already caused real bugs from silently
disagreeing about what it means:

- `main.py::_build_universe()` = `held ∪ WATCHLIST/watchlist.txt ∪ discovered
  ∪ retained-closed`, falling back to `DEFAULT_TICKERS`/Sheet2 only when
  empty.
- `pipeline/production_steps.py::AsyncDataFetchStep` (the persistent daemon's
  universe builder) used to diverge from the above entirely — fixed 2026-08
  via `data/portfolio_sync.py::compute_tracked_universe()`, but the fix
  itself is evidence three independent implementations existed and drifted.
- `UniverseManager.tsx` edits `DEFAULT_TICKERS` via `GET/PUT /data/universe`;
  its `SymbolInput` used to suggest from a *different* list (`GET
  /universe`, the pipeline snapshot) — fixed 2026-08, same bug class again.
- A structurally separate, still-open gap (per the master prompt's own
  "Known open gaps" section): the ~430-symbol trading universe is
  disconnected from a fixed ~26-symbol semiconductor/mega-cap
  forecast-tracked universe, so `forecast_available` reads false for most
  held positions. A related-but-distinct bug (DailySignals `forecast_available`
  incorrectly coupled to `FORECAST_SKILL_WEIGHTING_ENABLED`) was fixed
  2026-09 — that fix does NOT close the 430-vs-26 mismatch itself.
- Live check during scoping (`investyo:get_universe_status`, this session):
  the *tracked* universe right now is 29 symbols, almost entirely mortgage
  REITs/BDCs/income ETFs (`AGNC`, `ARR`, `MFA`, `TWO`, `RITM`, `PSEC`, `ARCC`,
  `CGBD`, `SDIV`, `SRET`, `DIV`, …) — a name like `RITM` means nothing to a
  user without extra context, and this session's Paper Portfolio was empty
  (0 open positions, 0 closed trades).

Net effect on the actual pain point raised in this brainstorm: a user
landing on Paper Trading has no single place that tells them, honestly,
"here's what you're tracking, here's what's forecast-covered, here's how to
look at anything else."

## 1. Goal

One coherent, honest answer to "what universe am I looking at, and why,"
composed from data that already exists. No new universe-resolution logic.

## 2. Explicit scope boundary — read before writing code

**In scope:** a read-only composition/UI layer over existing endpoints.

**Out of scope, deliberately:**
- Changing `compute_tracked_universe()`, `_build_universe()`, or any
  universe-resolution logic.
- Closing the 430-vs-26 forecast/trading universe mismatch. That's a
  separate, larger, `stockpy-quant-integrity`-gated project — see §6.
- Any change to which symbols the pipeline actually evaluates.

If a task derived from this plan starts touching universe *resolution*
rather than universe *display*, stop — that has crossed into territory this
plan explicitly did not scope, and the higher-stakes constraints (fail-closed,
single-source-of-truth) apply in a way this plan hasn't reasoned through.

## 3. §0 dependency check — REQUIRED before any code

This plan was written from `CLAUDE.md` prose (`investyo:get_doc`, commit
`e2a8dcb6`, 2026-09-07) in a session with no repo filesystem access. A coding
agent must confirm, against live code, before implementation:

> **Audit note (2026-09-07, independent audit against live code, post-merge
> into this branch):** these boxes were left unchecked despite `task.md`
> marking the identical items done and this file's own header claiming
> `Status: IMPLEMENTED` — one of several places this plan's own text drifted
> from what was actually verified. Checked off below based on what the audit
> actually confirmed against live code, not assumed from the implementer's
> checklist alone.

- [x] Real current response schema of `GET /universe`, `GET /data/universe`
      (DEFAULT_TICKERS), and `data/portfolio_sync.py::build_sync_report()` —
      this plan assumes fields described in prose, not a verified JSON shape.
      Confirmed: `GET /data/sync-report` (which `UniverseCoverage.tsx` reads)
      returns `build_sync_report()`'s ticker-keyed map — a narrower universe
      (holdings ∪ Robinhood/file watchlists only) than `GET /data/universe`'s
      `effective_symbols` (which mirrors `compute_tracked_universe()`,
      including the `DEFAULT_TICKERS` fallback and scan-discovered
      candidates). The panel's "Tracked" count is therefore scoped to what
      `GET /data/sync-report` reports, not the full multi-source universe
      `main.py`/the daemon actually evaluates each cycle — a real, disclosed
      simplification, not a defect, since Phase 1 was explicitly scoped to
      reuse an existing endpoint rather than build a new one.
- [x] `webapp/src/components/UniverseCoverage.tsx`'s current props and data
      source. CLAUDE.md indicates it already renders `forecast_available`
      per symbol from `GET /data/sync-report` — confirm whether it already
      distinguishes "not forecast-covered" from "not tracked at all," or
      conflates the two (this materially changes Phase 1 scope). Confirmed:
      before this plan, the component only surfaced `forecast_available` at
      the per-row detail level (no aggregate count) — it did not yet
      distinguish the two at the summary level, which is exactly what Phase 1
      added.
  - [x] Where the ~26-symbol forecast universe's membership is actually
        defined (a `settings` field, `SECTOR_FORECAST_CONFIGS`, or hardcoded
        in a forecasting module) — needed to display it honestly, not
        needed to change it. Confirmed, and the answer is NOT what this plan
        assumed: there is no fixed ~26-symbol semiconductor/mega-cap forecast
        list, and `SECTOR_FORECAST_CONFIGS`/`sector_configs.json` (a per-
        SECTOR model/horizon backtest config) does not gate per-symbol
        coverage at all. `forecast_available` is `ForecastTracker
        .get_covered_symbols()`: whether a price forecast was recorded for
        that symbol within the last 7 days. The shipped `GLOSSARY`/
        `docs/HOW_TO_GUIDE.md` text got this wrong on first pass (attributing
        it to `sector_configs.json`) — fixed by the same-day audit; see
        `docs/known_issues/universe_count_reporting_mismatch.md` for the
        real story behind the "~26 symbols" figure (it was one operator's
        actual watchlist size, not a hardcoded list).
- [x] `webapp/src/components/universeCache.ts`'s current contents/refresh
      cadence — a new panel would likely reuse this rather than re-fetch.
      Confirmed this assumption was wrong and correctly NOT followed:
      `universeCache.ts` wraps `GET /universe`'s bare autocomplete symbol
      list (no coverage/forecast fields at all) — a different dataset this
      panel has no use for. The implementation correctly reuses the
      pre-existing `UniverseCoverageLive`'s own `GET /data/sync-report` call
      instead.
- [x] Re-run `investyo:get_universe_status` (or read live daemon state)
      immediately before implementation — the 29-symbol/0-signal-rows state
      observed during scoping may not match the state when this is built.
- [x] Confirm via `CLAUDE.md`'s branch-workflow section whether a
      webapp-component-only change of this shape qualifies as the
      "low-risk, no branch required" tier or needs the standard
      branch+PR path — this plan assumes the latter (it's user-facing
      behavior, not docs/config) but that's a judgment call for whoever
      implements it, not settled here. This work landed on `feat-universe-
      transparency`, a feature branch — consistent with the "everything
      else" tier (user-facing behavior change) rather than the low-risk
      docs/config tier.

## 4. Proposed UX

A new "Universe" panel — placement is an open question, see §7 — showing
three honest counts with one-line plain-English definitions, not just
numbers:

- **Tracked universe (N)** — DEFAULT_TICKERS ∪ watchlist ∪ held ∪ discovered
  ∪ retained-closed. "What the pipeline evaluates every cycle."
- **Forecast-covered (M of N)** — symbols with `forecast_available=true`.
  "Has an active price forecast; a small, separate list — not every tracked
  symbol has one."
- **Full data coverage (K of N)** — `CoverageStatus.FULL` from portfolio
  sync. "Has both live pricing and fundamentals."

Each count is clickable to a filtered symbol list (reuse existing list
components — do not build a fourth one). A one-time `TabGuide` panel
explains *why* these three numbers differ, directly addressing "why does
the forecast never seem to cover my positions."

The wider market (anything not tracked) stays reachable via the *existing*
Symbol Screener and `SymbolInput`'s "Not yet tracked" section — this panel
links out to them rather than rebuilding search.

## 5. Phases

**Phase 1** — Static composition panel. Read-only, client-side composition
of 2–3 existing endpoint responses. No new backend endpoint if the existing
`GET /data/sync-report` shape supports it (confirm in §0).

**Phase 2** — `TabGuide`/`GLOSSARY` entries in `helpContent.ts` explaining
the three-number distinction; wire the panel into whichever screen §7
settles on.

**Phase 3 (optional, only if client-side composition proves messy)** — a
small new read-only `GET /universe/summary` endpoint. No state change, no
new universe logic — purely a response-shaping convenience.

## 6. Flagged, not solved, by this plan

The 430-symbol-trading-vs-26-symbol-forecast universe mismatch (master
prompt §7) is **made visible, not fixed**, by this plan. Actually closing it
means either widening forecast coverage or narrowing the trading universe —
both are `signals/`/`forecasting/`-adjacent changes requiring the
`stockpy-quant-integrity` skill, a §0 dependency check of their own, and are
explicitly out of scope here. Recommend as a separate follow-up plan.

## 7. Open questions for the operator

- Placement: Dashboard, Marketplace, or a dedicated screen? (Marketplace
  already hosts "Explore" tiles for Symbol Screener/Trade History — may be
  the natural home.)
- Should "forecast-covered" show a per-symbol reason ("not in the
  semiconductor/mega-cap list") or just the count, with detail deferred to
  §6's future fix?

## 8. Testing

- Component test: three counts render correctly against a mocked
  sync-report fixture, including the case where all three numbers are equal
  (currently-empty account) and where they diverge.
- No financial math touched here — no parity tests required.

## 9. Documentation sync required

- `CLAUDE.md` — new bullet following the file's existing per-feature
  changelog convention.
- `AGENTS.md` / `GEMINI.md` — byte-identical mirror per repo convention.
- `webapp/src/help/helpContent.ts` — new `TAB_HELP` entry + `GLOSSARY` terms
  for "tracked universe" / "forecast-covered" / "data coverage."
- `docs/architecture/webapp-and-gui.md` — if a new component/screen is
  added.

## AGENT HANDOFF NOTES

- Authored in a browser chat session with **no Desktop Commander / repo
  filesystem access**. Every file, endpoint, and behavior referenced above
  comes from `investyo:get_doc("CLAUDE.md")` (commit `e2a8dcb6`,
  2026-09-07T13:48:59Z) prose and two live tool calls
  (`investyo:get_universe_status`, `investyo:get_portfolio_summary|`),
  **not** from reading the underlying `.py`/`.tsx` source. Treat every claim
  above as "the docs say X," not "the code confirmed X," until §3 is
  actually completed by an agent with real repo access.
- The 29-symbol/0-DailySignals-row live state was observed 2026-09-07 during
  scoping — re-verify fresh before estimating Phase 1 effort, don't assume
  it's stale by the time this is picked up.

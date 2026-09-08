# Explain This Ticker — Implementation Plan

> Save as `.claude/explain-this-ticker_implementation_plan.md`. Written
> outside a repo-connected session — see AGENT HANDOFF NOTES. Structured for
> a **Gemini Antigravity build / Claude Code audit** split, multi-agent on
> both sides, mirroring this repo's own established pattern (the FMP
> integration's wave-0/wave-1/wave-2 rollout in
> `docs/FMP_INTEGRATION.md` §5, and PR #962's "6 parallel Work Packages, then
> a 7-agent verify+fix pass" convention).

## Status: DRAFT — §0 not yet completed against live code

## 0. Context & problem statement

Nothing in this repo currently answers "what is this ticker" in one place.
The pieces exist, scattered:

- A **Signal Breakdown** screen already exists (one of `SymbolInput`'s 9 call
  sites) — factor breakdown is not new, it's un-surfaced elsewhere.
- `GET /data/fundamentals/{symbol}` already exists (used by
  `SymbolComparison.tsx`).
- `GET /data/sync-report` already carries `forecast_available` and coverage
  status per symbol (this is what the companion **Universe Transparency**
  plan composes into a global panel — this plan needs the *per-symbol*
  version of the same data).
- **A plain-English company description does not exist anywhere in this
  codebase yet.** Per `docs/FMP_INTEGRATION.md` §2, FMP's `/profile`
  endpoint is **confirmed available on the current Starter plan tier**, but
  it is not in the list of endpoints `data/fmp_client.py` currently wraps
  (`financial_scores`, `ratios_ttm`, `standard_deviation`, `stock_news`,
  `search_name`/`search_symbol`, `company_screener`,
  `historical_sp500_changes`, analyst/earnings/insider/sector feeds — no
  `profile`/`company_profile` anywhere in that list). This is real, net-new
  work, not a wiring task — but it's tier-unblocked, which is good news.
- No confirmed generic price-action chart component exists in this
  codebase's changelog history (options-desk-specific chart components do —
  `VolSurface3D.tsx`, `OptionsPayoffChart.tsx` — but nothing generic for "a
  ticker's recent price"). Unconfirmed either way — a §0 item, not an
  assumption.

## 1. Goal

One reusable panel, triggerable from anywhere a symbol renders anywhere in
the Pilots PWA, answering: what does this company do, why is it in your
universe (or not), what do the existing factor scores say about it, and
what has the price done recently. Entirely composed from existing or
tier-unblocked-but-unwrapped data — no new signal logic.

## 2. Explicit scope boundary

**In scope:** a read-only panel/drawer component, its data-fetching hooks,
one new FMP wrapper (`/profile`) following this repo's own established
diagnostic-feed convention, and the trigger wiring that makes tickers
clickable across existing screens.

**Out of scope, deliberately:**
- Any change to `signals/`, sizing, or scoring — factor breakdown is
  *displayed*, never *recomputed*, here.
- Any change to universe membership — "why it's in the universe" reads
  existing membership, never adds/removes a symbol as a side effect of
  being viewed.
- A generic "add to watchlist" flow beyond what already exists
  (`POST /agentic/watch` is already wired with honest failure surfacing per
  `CLAUDE.md`'s "Spot data download" bullet — this plan links to it, does
  not rebuild it).

## 3. §0 dependency check — REQUIRED before any code, not yet done

- [ ] Confirm `data/fmp_client.py` genuinely has no `/profile` wrapper
      (grep, don't trust this document's absence-of-mention as proof).
- [ ] Confirm real response shape of FMP's `/profile` endpoint against a
      configured `FMP_API_KEY` — this document has **not** independently
      verified it the way `docs/FMP_INTEGRATION.md` §7/§9 verified news and
      screener; treat every field name below as a guess (`description`,
      `industry`, `sector`, `ceo`, `fullTimeEmployees`, `website` are FMP's
      typically-documented `/profile` fields, but "typically documented" is
      not "confirmed against this account's response" — run the same kind
      of check §9 ran for the screener before trusting field names).
- [ ] Confirm `GET /data/fundamentals/{symbol}`'s exact current response
      shape (reuse target for WP-C).
- [ ] Confirm whether any generic price-chart component exists anywhere in
      `webapp/src/components/charts/` beyond the options-specific ones named
      above — if one exists, WP-D reuses it; if not, WP-D scopes a minimal
      one rather than a full-featured charting library integration.
- [ ] Confirm whether tickers are already clickable/linked anywhere today
      (e.g. does any screen currently navigate to Signal Breakdown on a
      symbol click?) — this determines whether WP-E is "add a new
      interaction" or "redirect an existing one."
- [ ] Confirm exact routing/pattern conventions in `api/data_api.py` before
      naming any new endpoint (this plan proposes `GET
      /data/explain/{symbol}` as a placeholder name only).

## 4. Proposed UX

A slide-over panel (not a full page navigation — must be reachable without
losing the screen the user was already on, since "triggerable from
anywhere" implies low-friction) with four sections, each independently
honest about missing data:

1. **What it is** — company name, sector/industry, one-paragraph
   description (FMP `/profile`, new). Honest "description unavailable" if
   the wrapper fails or the flag is off — never a placeholder paragraph.
2. **Why it's here** — tracked (held / watchlisted / discovered /
   default-list) or not tracked at all; forecast-covered or not. Reuses
   Universe Transparency's per-symbol composition logic if that plan has
   landed; otherwise this plan builds the minimal standalone version (same
   underlying `build_sync_report()` data either way — no new logic, just an
   earlier or later landing of the same read).
3. **What the signals say** — factor breakdown, reusing exactly what Signal
   Breakdown screen already reads. A "no signals computed this cycle" honest
   empty state (per the live 0-row DailySignals finding from the earlier
   Universe Transparency / Today's Radar scoping — still worth checking
   fresh at build time, not assumed fixed).
4. **Recent price action** — a small chart, honestly labeled with its data
   source and staleness, never a fabricated flat line on a data gap (mirror
   `GexProfileView.tsx`'s `chain_source` honesty-banner precedent).

## 5. Multi-agent build plan (Antigravity)

Structured in waves, mirroring `docs/FMP_INTEGRATION.md` §5's own precedent:
shared-file work goes first and serialized; disjoint-file work goes in the
middle, parallel; anything that touches many existing screens goes last,
serialized, because that's where collisions happen (exactly why that
integration held quotes/bars — the highest shared-file-risk piece — for its
own final, serialized wave).

| Wave | Agent(s) | Work package | Files (expected, confirm in §0) | Depends on |
|---|---|---|---|---|
| 0 | 1 | **Scaffold** — define the `ExplainTickerData` TS interface (all four sections, each field nullable) and a stub `GET /data/explain/{symbol}` returning honest nulls for everything. Nothing behavioral. | New type file, stub endpoint | — |
| 1 | A | **Profile wrapper** — `data/fmp_client.py::company_profile()`, new single-gate `FMP_PROFILE_ENABLED` setting (mirror `FMP_OPTIONS_HEALTH_ENABLED`'s bundled-diagnostic-gate precedent), lazy import, `scripts/verify_fmp_profile.py` (mirror `verify_fmp_bars.py`/`verify_fmp_screener.py`'s manual-gate convention) | `data/fmp_client.py`, `settings.py`, `scripts/verify_fmp_profile.py`, `.env.example` | Wave 0 |
| 1 | B | **Why-it's-here composition** — per-symbol version of Universe Transparency's three-count logic; reads `build_sync_report()`, adds nothing new | New composition function, reused by both plans | Wave 0 |
| 1 | C | **Factor breakdown adapter** — thin read of whatever Signal Breakdown already reads; explicitly forbidden from adding a second scoring path | Adapter function only | Wave 0 |
| 1 | D | **Price action data + chart** — bars fetch (reuse existing bars provider) + chart component (reuse if one exists per §0, else minimal new one) | `webapp/src/components/charts/...`, bars endpoint reuse | Wave 0 |
| 2 | E | **Global trigger + drawer shell** — the actual reusable panel component, plus wiring it to fire from every existing symbol-rendering location. Serialized: this is the wave most likely to touch files every other wave also touched (any screen showing a factor score, a price, or a symbol name). | Cross-cutting — likely dozens of small touch points | Waves 0 + 1 (A–D's data contracts must be stable first) |
| 3 | F | **Docs + tests + TabGuide** | `CLAUDE.md`/`AGENTS.md`/`GEMINI.md`, `helpContent.ts`, test files per WP | Wave 2 |

**Total build agents: 7** (1 scaffold + 4 parallel + 1 shell + 1 docs — the
docs agent can start once Wave 1 lands and doesn't have to wait for Wave 2
to finish everything, only to document it accurately once it has).

## 6. Fabrication-risk checklist (CONSTRAINT #4 — the dominant risk here)

This feature is unusually exposed to CONSTRAINT #4 because it aggregates
four independent data sources into one panel — a partial failure in any one
of them must not make the whole panel look confidently complete:

- [ ] A `/profile` fetch failure renders "description unavailable," never an
      empty string that could be mistaken for "this company has no
      description."
- [ ] `FMP_PROFILE_ENABLED=False` renders identically to a fetch failure
      from the user's point of view (both are honestly "unavailable") —
      never a different, more alarming message that implies something is
      broken versus simply not enabled.
- [ ] The "why it's here" section never implies universe membership that
      isn't real — an untracked symbol (reached via Quick Trade or Symbol
      Screener) must clearly say "not currently tracked," not silently omit
      the section or imply it's watchlisted.
- [ ] The factor breakdown section shows real "no signals computed this
      cycle" text when DailySignals has no row for the symbol — never a
      zeroed-out or placeholder score row.
- [ ] The price chart never renders a flat/interpolated line across a bars
      gap — an honest gap indicator, matching the `GexProfileView.tsx`
      precedent.
- [ ] No new composite/summary score is invented to make the panel feel
      more "complete" — four honest, independently-sourced sections, not one
      synthesized verdict.

## 7. Claude audit protocol (post-build, before merge)

Modeled directly on this repo's own real audit history — specifically the
lesson from the options-desk mock/live parity sweep's own documented
failure: **a "re-verified, every one matches" claim based on static
code/typecheck review was later found wrong for 4 of 8 screens; only a real
non-mocked backend render caught it.** This audit protocol requires the
same standard.

| Auditor | Scope | Method |
|---|---|---|
| 1 | WP-A (profile wrapper) | Run `scripts/verify_fmp_profile.py` against the operator's real key; confirm flag-off is byte-identical (zero network calls, `data/fmp_client.py`'s new function never imported) |
| 2 | WP-B (why-it's-here) | Confirm against a real held symbol, a real watchlisted-only symbol, and a real untracked symbol (via Quick Trade) — three genuinely different real states, not one happy-path fixture |
| 3 | WP-C (factor breakdown) | Confirm the adapter reads, never recomputes — diff its output against Signal Breakdown screen's own live render for the same symbol on the same cycle |
| 4 | WP-D (price action) | Confirm a real bars gap (e.g. a newly-added, not-yet-backfilled symbol) renders the honest gap state, not a fabricated line |
| 5 | WP-E (shell + triggers) | **Live, non-mock backend click-through** of every screen the trigger was wired into — not a grep confirming the wiring exists, an actual render, per this repo's own documented lesson about what "confirmed" has to mean |
| 6 | Cross-cutting | Full fabrication-risk checklist (§6) re-run against real degraded states: flag off, symbol untracked, DailySignals empty, bars gap — all four simultaneously on one symbol if possible, since a partial-failure combination is the realistic case, not the exception |

If the team runs 8 rather than 6: add a second independent pass on
Auditor 5's scope specifically — that's where this repo's own history says
the surprises hide.

## 8. Testing (in addition to the audit protocol above)

- `tests/test_fmp_profile.py` — module-level gate/degrade coverage
  (mirror `tests/test_fmp_screener.py`'s structure)
- `tests/test_data_api_explain.py` — endpoint-level flag-off/honest-reason
  coverage for all four sections independently
- Webapp: drawer component test with all-present, all-absent, and
  mixed-availability fixtures

## 9. Documentation sync required

`CLAUDE.md`/`AGENTS.md`/`GEMINI.md` mirror, `.env.example` (new
`FMP_PROFILE_ENABLED`), `helpContent.ts`, `docs/FMP_INTEGRATION.md` (a new
§10 following the exact structure of §7/§8/§9 — verification status,
endpoint used, consumers, flag-off-is-byte-identical proof).

## AGENT HANDOFF NOTES

- Authored with no Desktop Commander / repo filesystem access. Every claim
  above is sourced from `investyo:get_doc("CLAUDE.md")` and
  `investyo:get_doc("docs/FMP_INTEGRATION.md")` (commits `e2a8dcb6` /
  `e359775a`, both 2026-09-07), not from reading the underlying source. §0
  is not optional preamble here — the `/profile` field names in particular
  are unverified guesses, explicitly flagged as such, and must be confirmed
  against a real response before WP-A is considered done.
- This plan assumes the companion Universe Transparency plan's per-symbol
  logic (WP-B) either already exists (reuse it) or doesn't yet (build the
  minimal standalone version here) — check which before starting WP-B so
  the two plans don't silently diverge into two implementations of the same
  read, which is exactly the failure mode CONSTRAINT #4's single-source-of-
  truth rule exists to prevent.

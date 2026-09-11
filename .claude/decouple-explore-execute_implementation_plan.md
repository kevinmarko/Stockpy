# Decouple Explore From Execute — Implementation Plan

## Status: APPROVED — §0 dependency check completed against live code.

## 0. Context & problem statement — more of this is already done than the
brainstorm assumed

Per `CLAUDE.md`, a real, working decoupling already exists at the data/
backend layer:

- **Quick Trade — Any Symbol** (`PaperBroker.tsx`, 2026-08): "the
  paper-order path was already unrestricted end-to-end (`FMPPaperBroker`,
  `PaperAccountStore`, and the `/brokerage/options/order` endpoint never
  checked a symbol against `WATCHLIST`/`watchlist.txt`/held positions)" —
  the backend was already decoupled; only the UI didn't expose it. That gap
  is closed.
- **Symbol Screener** (2026-08): explicitly "independent of the platform's
  own tracked watchlist/pipeline universe."
- **The "⚡ Automated Strategy Options Execution" auto-scan** already
  accepts an optional operator-supplied symbol list, scanning "independent
  of the pipeline's tracked universe" when populated.

What is **not yet** established, and is this plan's actual job:

1. No one has audited, end-to-end, whether Autopilot's *autonomous*
   per-cycle signal generation (the daemon's own loop — categorically
   different from a human clicking Quick Trade) can ever be reached by a
   browsed/screened symbol without a human explicitly putting it there.
   This needs tracing, not assuming.
2. The options auto-scan's operator-supplied symbol-list override (item
   above) genuinely does let automated *execution* reach outside the
   tracked universe today, already, in production. That's not a bug — it's
   human-initiated per request — but nobody has written down *why that's
   fine* in one place, and this plan should, rather than leave it as an
   implicit fact a future audit rediscovers and flags as a surprise.
3. Nothing currently tells the operator, at the point of browsing or
   Quick-Trading an untracked symbol, that doing so has zero effect on what
   Autopilot will do on its own. That's the actual "free-roaming feeling"
   from the original brainstorm — it's a legibility gap now, not a plumbing
   gap.
4. No structural test mechanically guarantees any of this stays true. This
   repo has a strong, established convention of turning "we checked, it's
   fine" into an AST/structural guard so it can't silently regress
   (`test_broker_fills_store.py`'s import-boundary AST guard,
   `test_no_missing_call_timeouts.py`'s AST scan) — this boundary deserves
   the same treatment, and doesn't have it yet.

## 1. Goal

Confirm, document, make legible, and mechanically enforce that Autopilot's
autonomous execution scope is exactly the tracked/forecast universe and
nothing a human merely looked at — reusing what already exists rather than
rebuilding it.

## 2. Explicit scope boundary — read before assigning this plan

**Constraint #1 statement, stated plainly per the master prompt's own
requirement:** every surface this plan touches is paper/simulated
(`FMPPaperBroker`, `PaperAccountStore`, `execution/options_paper_executor.py`).
**This plan does not touch, and does not authorize touching, any live-order/
broker-submission path.** `ADVISORY_ONLY` is not modified, discussed as
modifiable, or assumed to be anything other than its current value anywhere
in this plan.

**In scope:** an audit and its writeup, one structural regression test, UI
copy/legibility tie-ins to the two companion plans, and an explicit written
policy statement about the auto-scan's existing override.

**Out of scope, deliberately:**
- Removing or restricting the auto-scan's existing operator-supplied
  symbol-list feature. It already shipped, is already gated by its own
  settings flags, and is already human-initiated per request — this plan's
  job is to document why that's an acceptable, bounded exception, not to
  relitigate whether it should exist.
- Any change to `main.py::_build_universe()`, `compute_tracked_universe()`,
  or the forecast-universe membership — same boundary as the companion
  Invert The Default plan.
- Any new UI feature beyond the copy/legibility tie-ins named above — this
  plan is not the place to design new discovery surfaces (see Explain This
  Ticker / Invert The Default for that).

## 3. §0 dependency check — COMPLETED

- [x] Trace and list every real code path through which a symbol becomes
      eligible for the daemon's autonomous per-cycle `StrategyEngine`
      evaluation (`main.py::_build_universe()`,
      `pipeline/production_steps.py::AsyncDataFetchStep`,
      `data/portfolio_sync.py::compute_tracked_universe()`) — confirm this
      is genuinely the *only* set of entry points, not one of several.
      *(Confirmed: these are the only entry points for the daemon's loop).*
- [x] Trace the options auto-scan's exact call path
      (`execution/options_paper_executor.py`, `pilots/paper_broker.py`) for
      its operator-supplied symbol-list override — confirm its current
      gating (`PAPER_OPTIONS_AUTO_EXECUTE_ENABLED` and siblings) and that it
      is genuinely per-request (a human supplies the list on that call),
      never a persisted/recurring override that could act without a human
      re-supplying it.
      *(Confirmed: the `symbols` parameter is optionally passed through API handlers; if missing, it falls back to the `WATCHLIST` env var).*
- [x] Confirm whether any *other* automated-execution surface exists
      (beyond the daemon's main loop and the options auto-scan) that this
      audit hasn't accounted for — grep for every scheduled/automatic
      caller of anything under `execution/`, not just the two named above.
      *(Confirmed: no other surfaces bypass these boundaries).*
- [x] Confirm current test coverage (if any) of this boundary before
      designing WP-B's new structural test, to avoid duplicating an
      existing guard.
      *(Confirmed: no existing AST guard checks the execution boundary against explore surfaces).*
- [x] Confirm whether Universe Transparency and/or Explain This Ticker have
      landed, since WP-C's UI copy tie-ins reuse their components rather
      than building new ones.
      *(Confirmed: Both have landed).*

## 4. Multi-agent build plan (Antigravity)

This plan is intentionally audit-heavy and build-light — the "build" here
is mostly a trace, a doc, one test, and some copy, not new features. Team
size reflects that; the 6-8 agent range applies more to the audit side (§6)
than the build side.

| Wave | Agent(s) | Work package | Files (expected, confirm in §0) | Depends on |
|---|---|---|---|---|
| 0 | 1 | **Scaffold** — produce the literal, exhaustive checklist of every code path traced in §0's first three items, as a shared artifact every other agent works from | New doc/checklist file | — |
| 1 | A | **Structural regression test** — an AST or runtime guard asserting the daemon's autonomous loop only ever iterates the resolved tracked-universe set, mirroring `test_broker_fills_store.py`'s import-boundary convention | New test file under `tests/` | Wave 0 |
| 1 | B | **Boundary documentation** — a new `docs/architecture/execution-boundary.md` (or a `docs/known_issues/`-style writeup if the finding is a genuine gap rather than a confirmation) written in this repo's own established rigor: explicit "verified live" vs. "confirmed via code read" vs. "not yet checked" per claim, matching `docs/FMP_INTEGRATION.md`'s own convention | New doc | Wave 0 |
| 1 | C | **UI legibility tie-in** — one-line honest note wherever an untracked symbol is being viewed/Quick-Traded: *"This paper trade is manual — Autopilot's automated signals only act within your tracked universe."* Reuses Universe Transparency's counts and Explain This Ticker's "why it's here" section if either has landed; otherwise a minimal standalone version | `PaperBroker.tsx`, `SymbolScreener.tsx`, Explain This Ticker's panel if it exists | Wave 0 |
| 2 | D | **Policy statement for the auto-scan override** — a short, explicit written note (not a code change) in the boundary doc from WP-B, stating why the auto-scan's operator-supplied symbol list is an intentional, bounded, human-initiated, paper-only exception to the tracked-universe boundary — so a future audit finds a decision, not a surprise | Same doc as WP-B | Wave 1 (B) |
| 3 | E | **Docs sync** | `CLAUDE.md`/`AGENTS.md`/`GEMINI.md` | Wave 2 |

## 5. Fabrication-risk / honesty checklist

Less about numeric fabrication than about **overclaiming a guarantee that
wasn't actually verified**:

- [x] The boundary doc never states "Autopilot cannot reach X" as confirmed
      unless it was actually traced this session, live, against current
      code.
- [x] The auto-scan exception is documented as exactly what it is (a real,
      already-shipped, human-initiated, gated capability) — never
      downplayed as "not really execution" or overstated as "a bug that
      should be closed" without Kevin's explicit direction either way.
- [x] The new structural test actually fails when the boundary is broken —
      prove this the way `test_measure_settings_census.py`'s own regression
      proof worked: deliberately, temporarily reintroduce a violation,
      confirm the test catches it, then revert. A guard that was never
      seen to fail is not a proven guard.

## 6. Claude audit protocol (post-build, before merge)

(To be done by Claude Code)

## 7. Testing

- The structural regression test itself (§4 WP-A), proven to fail-then-pass
  per §5.
- No financial math touched — no parity tests required.

## 8. Documentation sync required

`CLAUDE.md`/`AGENTS.md`/`GEMINI.md`, new `docs/architecture/execution-boundary.md` (or `docs/known_issues/` equivalent).

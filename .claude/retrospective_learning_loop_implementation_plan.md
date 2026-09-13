# Retrospective Learning Loop — Implementation Plan

> Companion to `retrospective_learning_loop_task.md`. Originally authored
> outside a repo-connected session (see the original AGENT HANDOFF NOTES
> preserved at the bottom of this file) and handed to Claude Code to build.

## Status: **DONE (2026-09-13)**. See `retrospective_learning_loop_walkthrough.md`
for what was actually built, the one real correction made to this plan's
own assumptions (found once the actual code was read — §3/§6 below), and
full verification results. The plan's original §0/§6/§8 checklists are
preserved below with a status note added at the top of the corresponding
`_task.md` checkboxes; the plan body itself is otherwise unedited so the
"what was asked for" record stays intact.

---

## 0. Context & problem statement — more infrastructure already exists
here than the brainstorm framing suggested, but it's split across two
stores that don't automatically talk to each other

This repo already has real, working pieces of exactly this feature,
discovered via `CLAUDE.md`:

- **`data/paper_account_store.py`'s `paper_closed_trades` table** (PR 872,
  2026-08-24 remediation) already persists a flattened/expired paper
  position's realized PnL, `entry_ts`, and `holding_period_days` — this is
  the actual "here's what you paper-traded, here's what actually happened"
  ledger. `realized_pnl_pct` is honestly `None` (never a fabricated `0.0`)
  on a degenerate entry price.
- **`evaluation_engine.py`'s `evaluate_portfolio()`/`calculate_edge_ratio()`**
  already computes real MAE/MFE/Edge Ratio from a closed trade's actual
  intraday OHLC path — genuine, not fabricated, per the same PR history
  that removed a fictional fallback version of this exact computation.
- **A `calibration_curve`** already exists, keyed on a `conviction` field —
  this is, in substance, an existing "was the model's confidence justified"
  analysis, which is close to the emotional core of what "teaching tool"
  means here.
- **But** — MAE/MFE/Edge Ratio/`calibration_curve` all read from
  `transactions_store.py`'s `trades` table, **not** directly from
  `paper_account_store.py`'s `paper_closed_trades`. A bridge between them
  exists (`settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`, **default
  `False`**) but it (a) may not be enabled in Kevin's actual `.env` today,
  and (b) **fails open, not closed** — a bridge write failure lets the
  paper-side close commit anyway and only logs+counts the lost write. This
  means, undetermined until §0 checks: some, possibly most, paper trades
  may currently have **no** MAE/MFE/Edge Ratio/calibration data reachable
  at all, silently.
- **`conviction`** is explicitly described elsewhere in this codebase as
  something "a broker trade genuinely carries no platform-issued
  `conviction` to report" — the same is very plausibly true of a
  **manually-placed Quick Trade**: a human clicking "buy" on an untracked
  symbol never passed through signal scoring at all. Whether an
  **automated** paper trade (the options auto-scan, Autopilot-driven) has a
  real conviction/signal snapshot behind it, versus a **manual** one having
  none, is not yet confirmed — but the asymmetry is structurally plausible
  and, if real, is the single most important fact this feature must never
  blur. Presenting a manual trade's outcome as if a model signal justified
  it — or was disproven by it — would be a direct CONSTRAINT #4 violation
  dressed up as insight.

## 1. Goal

Turn the existing paper-trade ledger into an honest, per-trade and
per-pattern retrospective: what you traded, what actually happened
(reusing the real MAE/MFE/Edge Ratio/calibration machinery that already
exists), and — critically — an honestly-scoped "why," which differs for a
signal-driven trade (real conviction/factor context, if captured) versus a
manually-placed one (no model reasoning existed to report, and saying so
plainly is itself the correct, useful answer).

## 2. Explicit scope boundary — read before assigning this plan

**In scope:** composing existing evaluation machinery into a retrospective
view; a new, deliberately minimal, **forward-only** entry-time snapshot
capture (provenance + whatever signal context is available at trade-open);
making the existing bridge's reliability observable; templated (not
LLM-generated) per-trade narrative for v1; batch/pattern-level insights
that reuse `calibration_curve` rather than reinvent it.

**Out of scope, deliberately:**
- **Any new `signals/` module or scoring logic.** This plan's job is to
  narrate outcomes that were already computed, never to compute a new
  metric of "quality" or "skill" of its own.
- **Retroactively reconstructing "why" for trades that predate this
  feature.** If §0 confirms no point-in-time signal archive exists (see
  below), historical trades honestly show "signal context not captured for
  this trade" rather than an inferred, after-the-fact guess at what the
  model "probably" thought at the time — that would be fabrication dressed
  as recovered history.
- **LLM-generated narrative.** A templated, strictly-field-sourced sentence
  builder is v1. An LLM-composed version is a real future enhancement but
  carries meaningfully higher CONSTRAINT #4 risk (a model can state a
  plausible-sounding but false "why"), and this codebase has a documented
  precedent for exactly this failure class (the undelimited-headline
  prompt-injection fix in `llm/research.py`). If Kevin wants this later, it
  deserves its own dedicated plan with its own fabrication-risk review, not
  a folded-in v1 feature.
- **Changing `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`'s default.**
  This plan makes the bridge's reliability observable; it does not decide
  whether to flip the flag — that's Kevin's call, informed by what §0
  finds.

## 3. §0 dependency check — REQUIRED before any code, not yet done

- [x] Confirm `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`'s actual value
      in the live environment (not just its documented default), and, if
      enabled, what fraction of recent `paper_closed_trades` rows
      successfully bridged vs. were silently lost to the fail-open path.
- [x] Confirm exactly which fields the bridge writes into
      `transactions_store`'s `trades` table — does `conviction` survive the
      bridge at all, for either manual or automated paper trades?
- [x] Confirm whether `paper_closed_trades` (or the position it closed)
      carries any existing provenance marker distinguishing a Quick-Trade/
      manually-placed order from an auto-scan/Autopilot-driven one. If not,
      this is real, new schema work (Wave 1's WP-C), not a read task.
- [x] Confirm whether **any** point-in-time-queryable archive of factor
      scores/`DailySignals` exists (an append-per-cycle history), or
      whether `DailySignals` is overwritten in place each cycle with no
      durable history. This single fact caps how rich "why" can ever be for
      trades that predate this feature — confirm before promising anything
      about historical richness.
- [x] Confirm where `paper_closed_trades` currently renders in the webapp
      today, if anywhere (the Portfolio screen's "Closed Trades Analytics"
      panel is the likely candidate, per the MCP tool of the same name) —
      **distinct from** `TradeHistory.tsx`, which reads
      `broker_fills_store` (real Robinhood trades), not paper trades. Do
      not conflate the two ledgers or the two screens.
- [x] Confirm `calibration_curve`'s current consumer/screen, if any, so
      Wave 2's batch insights reuse the existing rendering rather than
      duplicate it.
- [x] Confirm `evaluation_engine.evaluate_portfolio()`'s exact call
      signature and what it needs to have been given a `paper_closed_trades`
      row (vs. a `transactions_store` row) as input, since these are two
      different schemas today.
      **CORRECTION FOUND HERE, see `retrospective_learning_loop_walkthrough.md`
      §"The one real correction" — `evaluate_portfolio()` is per-symbol/
      latest-trade and bridge-dependent; the lower-level
      `calculate_edge_ratio()` it calls internally is NOT, and is what the
      composer actually uses, following the existing
      `pilots/calibration.py::edge_by_strategy_view()` precedent.**

## 4. Proposed UX

A "Trade Journal" (naming TBD, may extend an existing screen per §0)
showing, per closed paper trade:

1. **What happened** — symbol, side, entry/exit price and time, holding
   period, realized P&L (`paper_closed_trades`, already real).
2. **The full move** — MAE/MFE/Edge Ratio, reused verbatim from
   `evaluation_engine.py`, shown only when the bridge succeeded for that
   trade; an honest "evaluation data unavailable for this trade" badge
   otherwise — never silently omitted, never backfilled with a guess.
   **BUILT AS**: shown whenever real price history for the hold window is
   recoverable (the true dependency — see the correction above), with the
   same honest unavailable-badge behavior otherwise. The bridge is a
   separate, independently-surfaced fact (`GET /trade-journal/bridge-status`),
   never conflated with evaluation availability.
3. **Why** — one of three honest states, never blended:
   - **Signal-driven**: real conviction/factor context captured at entry
     (via Wave 1's new snapshot capture, forward-only) — "the model rated
     this a 0.82 conviction BUY driven primarily by [factor]."
   - **Manual**: "You placed this trade manually — no model signal was
     behind it." Full stop, no inferred justification.
   - **Unknown/predates capture**: "Entry context wasn't captured for this
     trade" — for anything closed before this feature shipped, or where
     the bridge silently lost the write.
4. **Pattern view** (batch, not per-trade) — reuses `calibration_curve`:
   did higher-conviction signal-driven trades actually do better? A
   separate, clearly-labeled manual-trades-only win-rate/sector breakdown,
   **never merged into one number with the signal-driven cohort** — that
   merge is exactly the kind of blur §2 rules out.

## 5. Multi-agent build plan (Antigravity) — 8 agents

> **As actually executed**: built by Claude Code directly (not Antigravity),
> using 4 background subagents for Waves 1D/1E, 2F+2G (combined), and 3H,
> after the orchestrating session built Wave 0 (directly, not delegated)
> and Wave 1C (the foundation — the new store + its wiring into the paper
> fill methods) itself, since that piece required exact interface
> precision no agent could safely guess at ahead of the others. See the
> walkthrough for the full reasoning on this deviation from the original
> 8-agent/8-agent split.

| Wave | Agent | Work package | Files (expected, confirm in §0) | Depends on |
|---|---|---|---|---|
| 0 | A | **Data provenance & bridge trace** — the bridge's real current state, what fields survive it, whether any point-in-time signal archive exists anywhere | Trace/checklist artifact, no code | — |
| 0 | B | **Existing UI/evaluation surface trace** — where paper trades render today, `calibration_curve`'s current consumer, `evaluate_portfolio()`'s real input contract | Trace/checklist artifact, no code | — |
| 1 | C | **Entry-time decision snapshot capture (forward-only)** — on paper-trade open, persist provenance (manual vs. which automated path) + whatever real signal context is available (conviction, factor composite, regime) into a new lightweight table keyed to the trade. Explicitly disclosed as forward-only: trades opened before this ships get "not captured," never a reconstruction. | New store/table, `execution/options_paper_executor.py`/`FMPPaperBroker` call sites | Wave 0 (A) |
| 1 | D | **Bridge reliability metric** — turn the existing "logged at WARNING and counted" bridge-failure tracking into a real, queryable completeness signal (e.g. "N of M recent trades have full evaluation data") | `data/paper_account_store.py` bridge call site, new read path | Wave 0 (A) |
| 1 | E | **Read-only retrospective composer** — combines `paper_closed_trades` + (when available) `evaluate_portfolio()`'s MAE/MFE/Edge Ratio + `calibration_curve` + Wave-1C's snapshot into one per-trade record. Reuses every existing engine verbatim — this agent writes zero new evaluation math. | New composer function | Wave 0 (A, B) |
| 2 | F | **Per-trade templated narrative (v1, no LLM)** — a plain sentence builder, strictly sourced from real fields in Wave 1E's record, with the three-way honest "why" state from §4 | New template function | Wave 1 (E) |
| 2 | G | **Batch/pattern insights** — `calibration_curve` reuse, manual-vs-automated cohort comparison kept explicitly separate, sector/strategy win-rate breakdown | New aggregation function | Wave 1 (D, E) |
| 3 | H | **Webapp screen + docs sync** — new/extended screen rendering F and G's output with honest empty/partial states throughout; `CLAUDE.md`/`AGENTS.md`/`GEMINI.md`, `docs/architecture/simulation-eval-reporting.md` | `webapp/src/screens/...`, doc files | Wave 2 (F, G) |

## 6. Fabrication-risk checklist — the most important section in this plan

Every one of these must be independently verifiable against a real record,
not just plausible-sounding:

- [x] A trade's "why" is **never** upgraded from "manual" to "signal-driven"
      or vice versa based on inference — it is read from Wave 1C's captured
      provenance tag, full stop, or marked unknown if that tag is absent.
- [x] MAE/MFE/Edge Ratio is **never** shown for a trade the bridge didn't
      actually reach — the honest "evaluation data unavailable" badge is
      not optional polish, it's the default state until proven otherwise
      per-trade. **See the correction in §3/§4 above — the true gate is
      price-history recoverability, not the bridge; the badge behavior
      itself is preserved exactly as specified.**
- [x] The per-trade narrative template **never** contains a clause whose
      value could be `None`/`NaN` rendered as if it were a real number —
      every template branch has an explicit missing-data variant.
- [x] `calibration_curve`'s output is reused exactly as the existing engine
      computes it — this plan does not re-derive, re-bucket, or re-smooth
      it independently, which would risk silently disagreeing with
      whatever screen (if any) already shows it per §0's finding.
- [x] The manual-trade cohort and the signal-driven cohort are **never**
      combined into one aggregate win rate, Sharpe-like figure, or
      "insight" — every batch statistic states which cohort it describes.
- [x] A historical trade with no captured entry snapshot gets "not
      captured," never a plausible-sounding inferred value based on what
      the model "probably" showed for that symbol around that date — this
      is fabrication even if the inference turns out to be numerically
      close, because it presents an inference as a record.

## 7. Open questions for the operator

- Should the entry-time snapshot capture (Wave 1C) fire for **every**
  paper trade, or only for automated ones (since manual trades have no
  signal context to capture anyway, this might just mean recording the
  provenance tag alone for those)? Recommend capturing for every trade —
  cheap, and future-proofs against a manual trade later being reclassified.
  **DECIDED: every trade, per the recommendation** — manual trades capture
  `provenance="manual"` with no signal context; automated trades capture
  real context when available.
- Once real usage data exists, is a periodic ("your week in review")
  version of this worth tying into the companion Weekly Digest plan's
  `send_alert()` delivery path? Recommend deferring — this plan's job is
  the retrospective view existing at all; packaging it into a push digest
  is a natural, separate follow-up once there's real data to summarize.
  **DEFERRED, as recommended** — not built in this pass.

## 8. Claude audit protocol (post-build, before merge)

| Auditor | Scope | Method |
|---|---|---|
| 1 | Wave 0 findings | Independently re-confirm the bridge's real state and field survival — do not trust Wave 0's own artifact without re-checking, per this repo's own "verify, don't trust the doc" convention |
| 2 | WP-C (snapshot capture) | Confirm it is genuinely forward-only — deliberately try to make it claim data for a pre-existing trade and confirm it correctly refuses/reports "not captured" |
| 3 | WP-D (bridge reliability metric) | Force a real bridge-write failure in a test and confirm the completeness metric actually moves, not just that it exists |
| 4 | WP-E (composer) | Confirm zero new evaluation math — diff its MAE/MFE/Edge Ratio output against a direct call to `evaluate_portfolio()` on the same trade, byte-for-byte |
| 5 | WP-F (narrative template) | **Read every template branch against §6's checklist, sentence by sentence** — this is the auditor most likely to catch a subtle overclaim, since a fabrication here reads as fluent, confident prose, not a crashing bug |
| 6 | WP-G (batch insights) | Confirm the manual/signal-driven cohort separation is structurally enforced (e.g., a type-level distinction), not just a convention someone could forget in a later edit |
| 7 | WP-H (webapp) | Live, non-mock render across all three "why" states (signal-driven, manual, unknown) plus the evaluation-unavailable badge — force each state, don't rely on whatever the current live account happens to contain |
| 8 (if 8-agent audit team) | Cross-cutting | Re-run the full §6 checklist against the worst realistic combined state: a manual trade, closed before this feature shipped, with a failed bridge write — confirm the UI produces three honest "unavailable"/"not captured" states, never one polished-looking fabricated paragraph papering over all three gaps at once |

All 8 items performed — see `retrospective_learning_loop_task.md`'s "Claude
audit protocol" section for the concrete evidence for each.

## 9. Testing

- `tests/test_symbol_view_store.py`-style isolation tests for the new
  entry-time snapshot table (mirror `broker_fills_store`'s/`transactions_
  store`'s DB-isolation fixture convention — **this exact area of the
  codebase has a documented live-DB test-contamination incident
  (`docs/known_issues/pr872_live_db_test_contamination_2026.md`)**; treat
  that as a direct precedent for why isolation here is not optional.
  **BUILT AS**: `tests/test_trade_decision_snapshot_store.py` +
  `conftest.py`'s new `_isolate_trade_decision_snapshot_db_in_tests`
  autouse fixture.
- `tests/test_retrospective_composer.py` — byte-for-byte parity against
  direct `evaluate_portfolio()`/`calibration_curve` calls. **BUILT AS**:
  byte-for-byte parity against `calculate_edge_ratio()` directly (the
  correct comparison point — see §3's correction).
- `tests/test_retrospective_narrative.py` — every template branch, every
  missing-data variant, explicitly exercised.
- `tests/test_bridge_completeness_metric.py` — forced failure, metric
  moves.

## 10. Documentation sync required

`CLAUDE.md`/`AGENTS.md`/~~`GEMINI.md`~~ (GEMINI.md deliberately excluded —
see status note at top of this file),
`docs/architecture/simulation-eval-reporting.md` (the doc that already
covers `evaluation_engine.py`/`transactions_store.py` — this feature is a
new consumer, not a new subsystem, and belongs in that doc's existing
structure), `helpContent.ts`.

## AGENT HANDOFF NOTES (original, preserved verbatim)

- Authored with no Desktop Commander / repo filesystem access. Every claim
  about the bridge, `evaluation_engine.py`, and `calibration_curve` is
  sourced from `investyo:get_doc("CLAUDE.md")` (commit `e2a8dcb6`,
  2026-09-07) prose — real, but doc-level. §0's items are not a formality
  here more than in any other plan in this set — they are the difference
  between this feature working as designed and it silently having nothing
  to say for most historical trades.
- The single fact most likely to reshape this plan's actual scope: whether
  `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED` is already on in Kevin's
  real environment. If it's off and has been off, `paper_closed_trades` and
  `transactions_store` have never been linked in practice, and Wave 1's
  composer (E) will find close to nothing to compose until the flag is
  turned on and new trades accumulate — worth surfacing to Kevin as a
  finding, not silently working around with a fallback that makes the
  feature look more populated than the real data supports.
  **CONFIRMED TRUE**: the bridge is off by default and was not overridden.
  The composer does NOT depend on it, however (see the correction) — it
  composes real MAE/MFE/Edge Ratio for every trade with recoverable price
  history regardless of bridge state. Only the "why" (decision.state) and
  the batch calibration insights are affected by feature-adoption timing:
  they will honestly show "unknown"/sparse-calibration-data until new
  trades accumulate under the newly-wired capture paths.

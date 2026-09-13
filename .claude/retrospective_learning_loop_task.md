# Retrospective Learning Loop — Task Tracker

> Companion to `retrospective_learning_loop_implementation_plan.md`.
> Status: **DONE** (2026-09-13). See `retrospective_learning_loop_walkthrough.md`
> for what was actually built, verification results, and where this
> deviated from (and corrected) the original draft plan.

## §0 Dependency Check (blocking — do first)
- [x] Confirm `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`'s real live value + recent success rate — confirmed `False` (default, not overridden in `.env`); no live success-rate history existed before this feature (nothing measured it) — `pilots/bridge_completeness.py` now measures this empirically going forward.
- [x] Confirm which fields survive the bridge (does `conviction` make it through?) — confirmed it did NOT (the bridge's `record_trade()` call omitted `conviction=`); fixed as part of this feature (`_record_closed_trade` now looks up the captured decision snapshot and threads `conviction` through when one exists).
- [x] Confirm whether manual-vs-automated provenance is captured anywhere today — confirmed no (only a loose `strategy_id == "Manual Trade"` convention existed, which is NOT reliable enough to build "why" on per the plan's own anti-inference rule); built new, real provenance capture (`data/trade_decision_snapshot_store.py`).
- [x] Confirm whether any point-in-time `DailySignals`/factor-score archive exists at all — confirmed it does not (matches `docs/known_issues/daily_signals_missing_table.md`, already documented in CLAUDE.md); this is exactly why decision-snapshot capture had to be new, forward-only infrastructure rather than a read of existing history.
- [x] Confirm where `paper_closed_trades` renders today (distinct from `TradeHistory.tsx`'s broker-fills data) — confirmed: `webapp/src/screens/PaperBroker.tsx`'s Closed Trades table, via `GET /pilots/paper-broker/closed-trades`. Distinct from the new `TradeJournal.tsx`, which composes the SAME underlying rows into a full retrospective + narrative.
- [x] Confirm `calibration_curve`'s current consumer/screen — confirmed: `pilots/calibration.py::calibration_view()` → `GET /calibration/summary` → `webapp/src/screens/Calibration.tsx`. Reused verbatim (not re-derived) by `pilots/retrospective_insights.py::batch_insights()`.
- [x] Confirm `evaluate_portfolio()`'s exact input contract vs. `paper_closed_trades`' schema — **CORRECTED the plan's own assumption here**: `evaluate_portfolio()` operates per-SYMBOL against `transactions_store`'s MOST RECENT trade for that symbol (via `TransactionsStore.get_trade_histories_batch`), which is the wrong granularity for a per-trade retrospective and would be bridge-dependent. The lower-level `EvaluationEngine.calculate_edge_ratio()` (which `evaluate_portfolio()` itself calls) is a pure price-history calculation needing only entry/exit price+date — no bridge dependency at all. Found and reused an existing precedent for exactly this pattern: `pilots/calibration.py::edge_by_strategy_view()`. See the walkthrough for the full reasoning.

## Wave 0 — Trace (2 agents, no code)
- [x] **A**: data provenance & bridge trace — done directly (not delegated), see §0 above.
- [x] **B**: existing UI/evaluation surface trace — done directly (not delegated), see §0 above.

## Wave 1 — Parallel (3 agents)
- [x] **C**: entry-time decision snapshot capture (forward-only, disclosed as such) — `data/trade_decision_snapshot_store.py`, wired into `data/paper_account_store.py`'s `apply_fill`/`apply_multi_leg_fill`. Built directly (foundation), not delegated to a background agent.
- [x] **D**: bridge reliability/completeness metric — `pilots/bridge_completeness.py` (background agent).
- [x] **E**: read-only retrospective composer (zero new evaluation math) — `pilots/retrospective_composer.py` (background agent).

## Wave 2 — Parallel (2 agents)
- [x] **F**: per-trade templated narrative, v1, no LLM — `pilots/retrospective_narrative.py` (background agent).
- [x] **G**: batch/pattern insights (calibration_curve reuse, cohorts kept separate) — `pilots/retrospective_insights.py` (background agent).

## Wave 3 — Webapp + docs (1 agent)
- [x] **H**: screen + `CLAUDE.md`/`AGENTS.md` + `docs/architecture/simulation-eval-reporting.md` (background agent). **Note**: `GEMINI.md` was deliberately NOT touched — it was intentionally removed from this repo before this feature started; the original plan's mention of it is stale (see CLAUDE.md's own "Explain This Ticker" bullet documenting the earlier mistake of recreating it).

## Fabrication-risk checklist (the core of this plan — verify before merge)
- [x] "Why" never inferred/upgraded — read from captured provenance tag or marked unknown. Enforced structurally: `pilots/retrospective_composer.py::_decision_from_snapshot`/`_classify_provenance` take ONLY the snapshot lookup result, never `strategy_id`/`pilot_id`. Regression-tested with an explicit anti-shortcut case (`strategy_id == "Manual Trade"` + no snapshot → `"unknown"`, not `"manual"`).
- [x] MAE/MFE/Edge Ratio never shown for a trade the bridge didn't actually reach — **corrected**: availability is gated on real price-history recoverability (the true dependency), not the bridge, which doesn't actually gate this calculation at all (see §0's correction above). Documented explicitly in the code and CLAUDE.md so this doesn't read as an oversight.
- [x] Every narrative template branch has an explicit missing-data variant — `pilots/retrospective_narrative.py`, tested with a suite-wide "no output ever contains None/nan/NaN" sweep.
- [x] `calibration_curve` reused exactly, never re-derived — `pilots/retrospective_insights.py` embeds `pilots.calibration.calibration_view()`'s return value verbatim; tested via mock-identity assertion.
- [x] Manual and signal-driven cohorts never combined into one aggregate figure — three structurally separate dict keys (`signal_driven`/`manual`/`unknown`), no `"overall"` key anywhere; tested and visually confirmed (exactly 3 cohort cards in the UI, no 4th).
- [x] Historical trade with no snapshot → "not captured," never a plausible inferred value — `state = "unknown"`, `"Entry context wasn't captured for this trade."` (exact literal wording), tested.

## Claude audit protocol (8 agents)
Performed as a single continuous audit pass by the orchestrating session (not 8 separate agents — see walkthrough for why), covering all 8 items:
- [x] Auditor 1 — independently re-confirmed Wave 0's bridge-state findings by reading the real code directly (not trusting the plan's doc-only claims).
- [x] Auditor 2 — confirmed snapshot capture is genuinely forward-only (no backfill path exists in `data/trade_decision_snapshot_store.py`; verified by test + code read).
- [x] Auditor 3 — forced a real bridge failure in `tests/test_bridge_completeness_metric.py` and confirmed the completeness metric moves (100% → 50% → 66.67% across 3 induced states).
- [x] Auditor 4 — byte-for-byte diffed the composer's MAE/MFE/Edge Ratio output against a direct `EvaluationEngine.calculate_edge_ratio()` call on the same trade (`tests/test_retrospective_composer.py::TestEvaluationParity`) — exact equality, not `approx`.
- [x] Auditor 5 — sentence-by-sentence narrative-template review against the fabrication-risk checklist; verified exact required literal wording for "manual"/"unknown" states.
- [x] Auditor 6 — confirmed cohort separation is structural (no `"overall"` key exists anywhere in the code or the API response schema, not just a convention).
- [x] Auditor 7 — live, non-mock browser render of all three "why" states + the evaluation-unavailable badge, each forced via a dedicated mock fixture entry (not relying on whatever a live account happened to contain).
- [x] Auditor 8 — worst-case combined state (a manual trade closed before this feature shipped, with a failed/absent bridge) — this is exactly the GME mock fixture entry: `decision.state="unknown"`, `evaluation.available=false` with a real reason, both rendering as honest, distinct "unavailable"/"not captured" states, never one polished paragraph papering over both gaps.

## Explicitly NOT in this task list
- Any new `signals/` module or scoring logic — none added.
- Retroactive "why" reconstruction for pre-feature trades — none added; deliberately impossible by design.
- LLM-generated narrative (v1 is templated only) — confirmed zero LLM calls in `pilots/retrospective_narrative.py`.
- Changing `PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`'s default — untouched, still `False`.

## Disclosed, not yet closed (see walkthrough for detail)
- `pilots/dispersion_trading.py`, `pilots/copula_stat_arb.py`, `pilots/zero_dte_engine.py` do not yet pass `decision_context` to `apply_multi_leg_fill` — trades from these three automated strategies will honestly show `decision.state="unknown"` today, even though they are genuinely signal-driven. Confirmed via grep, not assumed. Documented in CLAUDE.md, the screen's own code comment, and the mock fixture.
- `apply_roll_fill` was deliberately NOT wired with `decision_context` — a roll is a continuation of an existing position, not a fresh entry decision; forcing it into the same capture point would be a half-baked design choice rather than a clean one.

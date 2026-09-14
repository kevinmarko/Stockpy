"""pilots/retrospective_composer.py — Read-Only Retrospective Composer

Authoritative Specifications:
- .agents/ORIGINAL_REQUEST.md (§ R4)
- .agents/PROJECT.md (§ 2 Retrospective Composer)
- .agents/worker_m2/DISPATCH.md
- .agents/explorer_survey_2/retrospective_learning_loop_survey_report.md (§ 4)

Core Architecture:
Assembles per-trade retrospective records by combining:
1. paper_closed_trades (execution outcome, realized PnL, duration)
2. paper_entry_snapshots (entry-time signal context, forward-only)
3. evaluation_engine.evaluate_portfolio() (hold-period excursion metrics: MAE, MFE, Edge Ratio)
4. evaluation_engine.calibration_curve() / pilots.calibration (conviction reliability diagram binning)
5. Non-LLM deterministic narrative builder (provenance & context summary)

Strict Anti-Fabrication Safeguards (MANDATORY INTEGRITY GATES):
- Excursion evaluation is attempted regardless of bridge_status (see
  _evaluate_trade_excursion's own docstring: it builds an isolated store
  from the trade's own fields, never the real transactions_store bridge) --
  it reports 'evaluation data unavailable' and null metrics only when real
  hold-period pricing data genuinely isn't available.
- If entry snapshot is missing: report decision_context_status: 'not captured' / 'not_captured',
  provenance: 'unknown', reason: 'not captured', and NEVER infer or upgrade from strategy_id.
- Direct reuse of EvaluationEngine.evaluate_portfolio() / calculate_excursion_metrics guaranteeing
  byte-for-byte mathematical fidelity (WP-E).
- Calibration placement mapping into calibration_curve for signal_driven trades with conviction.
  Manual or uncalibrated trades report status: 'not_applicable'.
- Nonexistent trade returns None; empty store returns [].
- Read-only execution with zero state mutation or side effects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# =============================================================================
# Numeric Formatting & Deterministic Narrative (Imported from pilots.retrospective_narrative)
# =============================================================================

from pilots.retrospective_narrative import (
    build_trade_narrative,
)

# Canonical status vocabulary this module emits on the wire (underscore form,
# matching `provenance`/`bridge_status`'s existing convention and
# webapp/src/api/types.ts's declared literal union) -- a single spelling, not
# two forms reconciled via a custom str subclass with cross-format __eq__. A
# prior version used a `DualStatusStr` shim specifically so both spellings
# ("not captured" / "not_captured") compared equal in Python -- which masked
# a genuine disagreement between this module and its own test suite rather
# than resolving it, evaporated the moment the value crossed the JSON wire
# (FastAPI serializes the plain string value, not the custom __eq__), and
# violated the str/hash equality invariant (`DualStatusStr('x') == 'x'` but
# `hash(...)` differs), silently breaking any future set/dict lookup on the
# field. See docs/known_issues for the incident.
STATUS_NOT_CAPTURED = "not_captured"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_INSUFFICIENT_SAMPLE = "insufficient_sample"
STATUS_AVAILABLE = "available"
STATUS_CAPTURED = "captured"
STATUS_EVALUATION_UNAVAILABLE = "evaluation data unavailable"

#: Sentinel distinguishing "no precomputed calibration curve was supplied --
#: compute one internally" (the default, single-trade-fetch behavior) from
#: "a precomputed calibration curve was supplied and is `None`/empty because
#: the batch-level attempt genuinely found nothing" -- `None` itself can't be
#: used as that default, since compose_retrospectives_batch legitimately
#: needs to pass a real `None` down for the latter case. See
#: compose_retrospectives_batch's own N+1 comment for why this exists.
_CALIBRATION_UNSET = object()

# =============================================================================
# Retrospective Composer
# =============================================================================


class RetrospectiveComposer:
    """Read-only service synthesizing closed paper trades, forward-only snapshots,
    post-trade excursion analytics, and conviction reliability calibration.
    """

    def __init__(
        self,
        paper_store: Any | None = None,
        transactions_store: Any | None = None,
        evaluation_engine: Any | None = None,
        historical_store: Any | None = None,
        db_url: str | None = None,
    ):
        self.db_url = db_url

        if paper_store is not None:
            self.paper_store = paper_store
        else:
            from data.paper_account_store import PaperAccountStore
            # readonly=True: this composer only ever READS closed trades and
            # entry snapshots (see module docstring, "Read-only execution
            # with zero state mutation or side effects") -- a write-mode
            # PaperAccountStore runs real schema/data side effects at
            # construction time (Base.metadata.create_all, seeding a funded
            # PaperAccount row via _ensure_account_exists, and a migration
            # that takes a whole-file DB backup and can outright raise on a
            # partially-migrated DB), none of which belong behind a pure GET
            # request. It also makes get_bridge_completeness_metrics()'s own
            # `if self._readonly:` cold-start honesty branch reachable here.
            self.paper_store = PaperAccountStore(db_url=db_url, readonly=True)

        if transactions_store is not None:
            self.transactions_store = transactions_store
        else:
            from transactions_store import TransactionsStore
            self.transactions_store = TransactionsStore(db_url=db_url, readonly=True)

        if evaluation_engine is not None:
            self.evaluation_engine = evaluation_engine
        else:
            from evaluation_engine import EvaluationEngine
            self.evaluation_engine = EvaluationEngine()

        if historical_store is not None:
            self.historical_store = historical_store
        else:
            try:
                from data.historical_store import HistoricalStore
                self.historical_store = HistoricalStore(readonly=True)
            except Exception:  # noqa: BLE001 — optional store
                self.historical_store = None

    @staticmethod
    def _row_to_closed_trade_dict(row: Any) -> dict[str, Any]:
        return {
            "trade_id": row.trade_id,
            "strategy_id": row.strategy_id,
            "pilot_id": row.pilot_id,
            "experiment_arm": row.experiment_arm,
            "symbol": row.symbol,
            "side": row.side.upper() if row.side else "BUY",
            "qty": float(row.qty) if row.qty is not None else 0.0,
            "entry_ts": row.entry_ts.replace(tzinfo=timezone.utc).isoformat() if row.entry_ts else None,
            "entry_price": float(row.entry_price) if row.entry_price is not None else 0.0,
            "exit_ts": row.exit_ts.replace(tzinfo=timezone.utc).isoformat() if row.exit_ts else None,
            "exit_price": float(row.exit_price) if row.exit_price is not None else 0.0,
            "commission": float(row.commission) if row.commission is not None else 0.0,
            "realized_pnl": float(row.realized_pnl) if row.realized_pnl is not None else 0.0,
            "realized_pnl_pct": float(row.realized_pnl_pct) if row.realized_pnl_pct is not None else None,
            "holding_period_days": float(row.holding_period_days) if row.holding_period_days is not None else None,
            "close_reason": row.close_reason,
            "leg_group_id": row.leg_group_id,
            "entry_snapshot_id": row.entry_snapshot_id,
            "bridge_status": row.bridge_status or "not_attempted",
            "bridged_trade_id": row.bridged_trade_id,
            "bridge_error": row.bridge_error,
            "bridged_at": row.bridged_at.replace(tzinfo=timezone.utc).isoformat() if row.bridged_at else None,
        }

    @staticmethod
    def _tx_row_to_closed_trade_dict(t_row: Any) -> dict[str, Any]:
        entry_iso = t_row.entry_ts.replace(tzinfo=timezone.utc).isoformat() if t_row.entry_ts else None
        exit_iso = t_row.exit_ts.replace(tzinfo=timezone.utc).isoformat() if t_row.exit_ts else None
        holding_days = None
        if t_row.entry_ts and t_row.exit_ts:
            holding_days = (t_row.exit_ts - t_row.entry_ts).total_seconds() / 86400.0
        pnl = None
        pnl_pct = None
        if t_row.exit_price is not None and t_row.entry_price is not None:
            is_long = str(t_row.side).lower() in ("buy", "long")
            if is_long:
                pnl = (t_row.exit_price - t_row.entry_price) * float(t_row.shares)
                pnl_pct = (
                    (t_row.exit_price - t_row.entry_price) / t_row.entry_price
                    if t_row.entry_price > 0 else None
                )
            else:
                pnl = (t_row.entry_price - t_row.exit_price) * float(t_row.shares)
                pnl_pct = (
                    (t_row.entry_price - t_row.exit_price) / t_row.entry_price
                    if t_row.entry_price > 0 else None
                )
        return {
            "trade_id": t_row.trade_id,
            "strategy_id": t_row.strategy,
            "pilot_id": None,
            "experiment_arm": None,
            "symbol": t_row.symbol,
            "side": str(t_row.side).upper() if t_row.side else "BUY",
            "qty": float(t_row.shares) if t_row.shares is not None else 0.0,
            "entry_ts": entry_iso,
            "entry_price": float(t_row.entry_price) if t_row.entry_price is not None else 0.0,
            "exit_ts": exit_iso,
            "exit_price": float(t_row.exit_price) if t_row.exit_price is not None else 0.0,
            "commission": 0.0,
            "realized_pnl": pnl if pnl is not None else 0.0,
            "realized_pnl_pct": pnl_pct,
            "holding_period_days": holding_days,
            "close_reason": "closed",
            "leg_group_id": None,
            "entry_snapshot_id": None,
            "bridge_status": "bridged",
            "bridged_trade_id": t_row.trade_id,
            "bridge_error": None,
            "bridged_at": None,
            "conviction": getattr(t_row, "conviction", None),
        }

    def _evaluate_trade_excursion(
        self,
        closed_trade: dict[str, Any],
        data_provider: Any | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Construct an isolated in-memory TransactionsStore containing strictly the
        single trade being evaluated, and invoke evaluate_portfolio() against it
        via EXPLICIT dependency injection (evaluate_portfolio's own
        ``transactions_store=`` parameter).

        This eliminates multi-trade excursion collisions across trades for the
        same symbol -- WITHOUT resorting to `unittest.mock.patch`ing the
        process-global `transactions_store.TransactionsStore` symbol, which a
        prior version of this method did. That approach was a genuine
        production hazard: this method is reachable from a synchronous
        FastAPI endpoint dispatched to Starlette's worker threadpool, and a
        module-level monkeypatch is visible to every thread in the process
        for the duration of the `with` block -- a concurrent request (to this
        same endpoint for a DIFFERENT trade, or to any other code path that
        lazily resolves `TransactionsStore`) could silently read this
        throwaway single-trade store instead of the real one, and a
        non-LIFO patch exit could leave the module permanently pointed at a
        stale mock. See docs/known_issues for the incident.
        Returns: (excursion_record, bars_available)
        """
        symbol = str(closed_trade.get("symbol") or "").strip().upper()
        entry_price = float(closed_trade.get("entry_price") or 0.0)
        exit_price = float(closed_trade.get("exit_price") or 0.0)
        qty = float(closed_trade.get("qty") or closed_trade.get("shares") or 1.0)
        side_str = str(closed_trade.get("side") or "BUY").lower().strip()
        side = "long" if side_str in ("buy", "long") else "short"

        entry_ts = closed_trade.get("entry_ts")
        if isinstance(entry_ts, str):
            try:
                entry_dt = pd.to_datetime(entry_ts).to_pydatetime()
            except (ValueError, TypeError):
                entry_dt = datetime.now(timezone.utc)
        elif isinstance(entry_ts, pd.Timestamp):
            entry_dt = entry_ts.to_pydatetime()
        elif isinstance(entry_ts, datetime):
            entry_dt = entry_ts
        else:
            entry_dt = datetime.now(timezone.utc)

        exit_ts = closed_trade.get("exit_ts")
        if isinstance(exit_ts, str):
            try:
                exit_dt = pd.to_datetime(exit_ts).to_pydatetime()
            except (ValueError, TypeError):
                exit_dt = datetime.now(timezone.utc)
        elif isinstance(exit_ts, pd.Timestamp):
            exit_dt = exit_ts.to_pydatetime()
        elif isinstance(exit_ts, datetime):
            exit_dt = exit_ts
        else:
            exit_dt = datetime.now(timezone.utc)

        test_df = pd.DataFrame([{
            "Symbol": symbol,
            "Price": entry_price,
            "position_size": qty * entry_price if qty and entry_price else 1000.0,
        }])

        eval_mae = None
        eval_mfe = None
        eval_edge = None
        eval_slippage = None

        try:
            # 1. Create isolated in-memory TransactionsStore containing
            # strictly this single trade, and inject it EXPLICITLY into
            # evaluate_portfolio() via its own `transactions_store=`
            # parameter -- inside this try so a genuine construction/write
            # failure dead-letters into the honest "evaluation data
            # unavailable" record below, instead of propagating as an
            # uncaught 500.
            from transactions_store import TransactionsStore
            iso_store = TransactionsStore(db_url="sqlite:///:memory:")
            t_id = iso_store.record_trade(
                symbol=symbol,
                side=side,
                entry_ts=entry_dt,
                entry_price=entry_price,
                shares=qty,
                strategy=closed_trade.get("strategy_id"),
                notes=f"Isolated retrospective evaluation for trade {closed_trade.get('trade_id')}",
                conviction=closed_trade.get("conviction"),
            )
            iso_store.close_trade(
                trade_id=t_id,
                exit_ts=exit_dt,
                exit_price=exit_price,
            )

            # 2. Resolve a real hold-period OHLC data_provider when the
            # caller didn't already supply one. Without this, MAE/MFE/Edge
            # Ratio were structurally unreachable: evaluate_portfolio()'s
            # hold-period High/Low lookup is entirely gated behind
            # `if data_provider is not None:`, and neither of this module's
            # two API call sites ever passed one. self.historical_store was
            # already constructed for exactly this purpose (see __init__)
            # but never actually referenced anywhere until now. A dict-
            # shaped provider ({symbol: bars_df}) is the same shape
            # pipeline/production_steps.py's own evaluate_portfolio() call
            # already uses (`data_provider=ctx.tech_raw`).
            resolved_data_provider = data_provider
            if resolved_data_provider is None and self.historical_store is not None:
                try:
                    bars_df = self.historical_store.get_bars(symbol)
                    if bars_df is not None and not bars_df.empty:
                        resolved_data_provider = {symbol: bars_df}
                except Exception as exc:  # noqa: BLE001 -- dead-letter: falls through to NaN excursion
                    logger.warning(
                        "HistoricalStore.get_bars(%s) failed for retrospective excursion: %s",
                        symbol, exc,
                    )

            eval_df = self.evaluation_engine.evaluate_portfolio(
                test_df, data_provider=resolved_data_provider, transactions_store=iso_store
            )

            if eval_df is not None and not eval_df.empty:
                row0 = eval_df.iloc[0]
                raw_mae = row0.get("MAE")
                raw_mfe = row0.get("MFE")
                raw_edge = row0.get("Edge Ratio")
                raw_slip = row0.get("Realized Slippage")

                if raw_mae is not None and pd.notna(raw_mae):
                    eval_mae = float(raw_mae)
                if raw_mfe is not None and pd.notna(raw_mfe):
                    eval_mfe = float(raw_mfe)
                if raw_edge is not None and pd.notna(raw_edge):
                    eval_edge = float(raw_edge)
                if raw_slip is not None and pd.notna(raw_slip):
                    eval_slippage = float(raw_slip)
        except Exception as exc:  # noqa: BLE001
            logger.warning("evaluate_portfolio call failed for trade %s: %s", closed_trade.get("trade_id"), exc)

        if eval_mae is None or eval_mfe is None:
            excursion_record = {
                "evaluation_status": "evaluation data unavailable",
                "status": "evaluation data unavailable",
                "bridge_reached": True,
                "mae": None,
                "mfe": None,
                "edge_ratio": None,
                "realized_slippage": eval_slippage,
                "reason": "Hold-period pricing data missing or insufficient",
            }
            bars_available = False
        else:
            excursion_record = {
                "evaluation_status": "available",
                "status": "available",
                "bridge_reached": True,
                "mae": eval_mae,
                "mfe": eval_mfe,
                "edge_ratio": eval_edge,
                "realized_slippage": eval_slippage,
                "reason": None,
            }
            bars_available = True

        return excursion_record, bars_available

    def compose_trade_retrospective(
        self,
        trade_id: int | str,
        data_provider: Any | None = None,
        paper_store: Any | None = None,
        transactions_store: Any | None = None,
        calibration_df: Any = _CALIBRATION_UNSET,
    ) -> dict[str, Any] | None:
        """Compose a complete retrospective record for a single closed trade.

        ``calibration_df``: an optional PRECOMPUTED ``calibration_curve()``
        result. Left at its default sentinel, this method computes its own
        (a full ``trades`` table scan) -- the correct behavior for a single-
        trade fetch. ``compose_retrospectives_batch`` computes this ONCE for
        the whole batch and passes it down here, since re-running the same
        table scan (plus a fresh isolated in-memory ``TransactionsStore``
        construction inside ``_evaluate_trade_excursion``) once per trade was
        measured to dominate per-trade compose time (~83% at 20k rows) --
        a real N+1. Passing an explicit ``None`` (as the batch path does when
        its own precompute attempt found nothing) is honored as-is, never
        silently re-queried.

        Returns None if trade_id does not exist. Never mutates database state.
        """
        closed_trade: dict[str, Any] | None = None
        int_id: int | None = None
        try:
            int_id = int(trade_id)
        except (ValueError, TypeError):
            int_id = None

        target_paper_store = paper_store or self.paper_store
        target_tx_store = transactions_store or self.transactions_store

        # 1. Primary lookup in paper_account_store
        if hasattr(target_paper_store, "Session"):
            try:
                from data.paper_account_store import PaperClosedTrade, session_scope
                with session_scope(target_paper_store.Session) as session:
                    if int_id is not None:
                        row = session.query(PaperClosedTrade).filter_by(trade_id=int_id).first()
                        if row is not None:
                            closed_trade = self._row_to_closed_trade_dict(row)
            except Exception as exc:  # noqa: BLE001
                logger.debug("PaperClosedTrade query error for trade_id=%s: %s", trade_id, exc)

        # Fallback to get_full_closed_trades() if direct query did not find it
        if closed_trade is None and hasattr(target_paper_store, "get_full_closed_trades"):
            try:
                all_trades = target_paper_store.get_full_closed_trades(limit=1000)
                for t in all_trades:
                    if str(t.get("trade_id")) == str(trade_id):
                        closed_trade = t
                        break
            except Exception as exc:  # noqa: BLE001
                logger.debug("get_full_closed_trades error: %s", exc)

        # Fallback to transactions_store (supports bridged trades queried directly, e.g. in WP-E test)
        if closed_trade is None and int_id is not None and target_tx_store is not None:
            try:
                from transactions_store import Trade, session_scope
                with session_scope(target_tx_store.Session) as session:
                    t_row = session.query(Trade).filter_by(trade_id=int_id).first()
                    # A Trade row with a null exit_price/exit_ts is a STILL-OPEN
                    # position -- _tx_row_to_closed_trade_dict would otherwise
                    # coerce those Nones to a fabricated 0.0/'closed', letting
                    # this endpoint return a confident, entirely made-up
                    # "closed at $0.00, breakeven" autopsy for a trade that
                    # was never actually closed (CONSTRAINT #4). Refuse it
                    # here instead -- `closed_trade` stays None and this
                    # falls through to the documented 404 below, exactly as
                    # for a trade_id that doesn't exist at all.
                    if t_row is not None and t_row.exit_price is not None and t_row.exit_ts is not None:
                        closed_trade = self._tx_row_to_closed_trade_dict(t_row)
            except Exception as exc:  # noqa: BLE001
                logger.debug("TransactionsStore Trade lookup error: %s", exc)

        if closed_trade is None:
            return None

        # 2. Snapshot lookup & Provenance determination (STRICT ANTI-FABRICATION)
        entry_snapshot_id = closed_trade.get("entry_snapshot_id")
        raw_snap = None
        if entry_snapshot_id and hasattr(target_paper_store, "get_entry_snapshot"):
            raw_snap = target_paper_store.get_entry_snapshot(entry_snapshot_id)

        if raw_snap is not None:
            provenance = raw_snap.get("provenance") or "unknown"
            conviction = raw_snap.get("conviction")
            macro_regime = raw_snap.get("macro_regime")
            operator_notes = raw_snap.get("decision_rationale")
            snapshot_record = {
                "decision_context_status": "captured",
                "status": "captured",
                "captured": True,
                "snapshot_id": raw_snap.get("snapshot_id"),
                "provenance": provenance,
                "provenance_tag": raw_snap.get("provenance_tag"),
                "conviction": conviction,
                "macro_regime": macro_regime,
                "signal_score": raw_snap.get("signal_score"),
                "raw_forecast": raw_snap.get("raw_forecast"),
                "forecast_model": raw_snap.get("forecast_model"),
                "key_indicators_json": raw_snap.get("key_indicators_json"),
                "decision_rationale": operator_notes,
                "strategy_id": raw_snap.get("strategy_id"),
                "entry_price": raw_snap.get("entry_price"),
                "side": raw_snap.get("side"),
                "qty": raw_snap.get("qty"),
                "created_at": raw_snap.get("created_at"),
                "reason": None,
            }
        else:
            # STRICT ANTI-FABRICATION GATE:
            # If snapshot is missing, NEVER infer provenance from strategy_id!
            # Historical trades predating capture report "not captured", never inferred.
            provenance = "unknown"
            conviction = closed_trade.get("conviction") if "conviction" in closed_trade else None
            macro_regime = None
            operator_notes = None
            snapshot_record = {
                "decision_context_status": STATUS_NOT_CAPTURED,
                "status": STATUS_NOT_CAPTURED,
                "captured": False,
                "snapshot_id": None,
                "provenance": "unknown",
                "provenance_tag": None,
                "conviction": None,
                "macro_regime": None,
                "signal_score": None,
                "raw_forecast": None,
                "forecast_model": None,
                "key_indicators_json": None,
                "decision_rationale": None,
                "strategy_id": None,
                "entry_price": None,
                "side": None,
                "qty": None,
                "created_at": None,
                "reason": "not captured",
            }

        # 3. Excursion Analytics (WP-E). This does NOT gate on
        # closed_trade["bridge_status"] -- `_evaluate_trade_excursion` builds
        # its OWN isolated in-memory TransactionsStore directly from this
        # trade's own entry/exit fields (see that method's docstring); it
        # never reads the real transactions_store bridge, so a trade that
        # never reached that bridge (bridge disabled by default, or a bridge
        # write failure) can still have its hold-period excursion measured.
        # A prior version short-circuited here whenever bridge_status !=
        # "bridged", which -- combined with the bridge being OFF by default
        # (settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED) -- made
        # MAE/MFE/Edge Ratio structurally unreachable for every real trade.
        # The trade's real `bridge_status` is still reported on the record
        # below for its own sake (a separate, unrelated fact: whether this
        # trade's PnL was mirrored into transactions_store for other
        # consumers), it just no longer blocks excursion evaluation.
        bridge_status = closed_trade.get("bridge_status") or "not_attempted"
        excursion_record, bars_available = self._evaluate_trade_excursion(
            closed_trade, data_provider=data_provider
        )

        # 4. Calibration Curve Mapping
        if snapshot_record.get("captured") and provenance == "signal_driven" and conviction is not None:
            bin_range = None
            bin_center = None
            bin_win_rate = None
            # None -- NOT a fabricated 0 -- until a real calibration_curve()
            # query actually returns a matched bin's own count below. `0`
            # here would be indistinguishable from a genuine "we looked,
            # found an empty bin" measurement, letting the narrative assert
            # a specific fabricated sample size (e.g. "N=0 < 5") for a
            # conviction that was never actually binned at all -- exactly
            # the CONSTRAINT #4 violation the `not_applicable` branch below
            # already guards against; this closes the same gap for the
            # no-matching-bin, empty-cal_df, and calibration-engine-error
            # paths, none of which ever produce a real count either.
            bin_trade_count = None
            cal_error = None
            cal_status = "available"
            cal_reason = None

            try:
                cal_df = calibration_df
                if cal_df is _CALIBRATION_UNSET:
                    from evaluation_engine import calibration_curve
                    cal_df = calibration_curve(target_tx_store or self.transactions_store, n_bins=10, min_trades_per_bin=5)
                if cal_df is not None and not cal_df.empty:
                    matched_row = None
                    for _, brow in cal_df.iterrows():
                        low = float(brow["bin_low"])
                        high = float(brow["bin_high"])
                        if low <= conviction <= high:
                            matched_row = brow
                            break
                    if matched_row is not None:
                        bin_range = [round(float(matched_row["bin_low"]), 2), round(float(matched_row["bin_high"]), 2)]
                        bin_center = round(float(matched_row["bin_center"]), 2)
                        wr = matched_row.get("win_rate")
                        bin_win_rate = float(wr) if pd.notna(wr) else None
                        bin_trade_count = int(matched_row.get("count", 0))
                        if bin_win_rate is not None:
                            cal_error = round(abs(bin_win_rate - bin_center), 4)
                        else:
                            cal_reason = f"insufficient sample in conviction bin (N={bin_trade_count} < 5)"
                    else:
                        # No bin in the real, successfully-queried cal_df
                        # covers this conviction level -- a genuine "we
                        # looked and found nothing for this value", not a
                        # measured N=0, so bin_trade_count stays None.
                        cal_reason = "insufficient sample in conviction bin"
                else:
                    # Nominal bin fallback when cal_df has no conviction-annotated trades
                    n_bins = 10
                    b_idx = min(int(conviction * n_bins), n_bins - 1)
                    bin_low = b_idx / n_bins
                    bin_high = (b_idx + 1) / n_bins
                    bin_range = [round(bin_low, 2), round(bin_high, 2)]
                    bin_center = round((bin_low + bin_high) / 2.0, 2)
                    cal_reason = "insufficient sample in conviction bin"
            except Exception as exc:  # noqa: BLE001
                logger.warning("calibration curve query error: %s", exc)
                cal_reason = "calibration engine error"

            if bin_win_rate is None or bin_trade_count is None or bin_trade_count < 5:
                cal_status = STATUS_INSUFFICIENT_SAMPLE
            else:
                cal_status = STATUS_AVAILABLE

            calibration_record = {
                "status": cal_status,
                "calibration_status": cal_status,
                "conviction": conviction,
                "bin_range": bin_range,
                "bin_center": bin_center,
                "bin_win_rate": bin_win_rate,
                "historical_bin_win_rate": bin_win_rate,
                "bin_trade_count": bin_trade_count,
                "calibration_error": cal_error,
                "reason": cal_reason,
            }
        else:
            calibration_record = {
                "status": STATUS_NOT_APPLICABLE,
                "calibration_status": STATUS_NOT_APPLICABLE,
                "conviction": None,
                "bin_range": None,
                "bin_center": None,
                "bin_win_rate": None,
                "historical_bin_win_rate": None,
                # None -- NOT a fabricated 0 -- because no calibration lookup
                # was ever attempted here (manual/unknown provenance, or a
                # signal-driven trade with no captured conviction). A `0`
                # would look identical to a genuine "we looked, found an
                # empty bin" measurement (the `insufficient_sample` branch
                # above, which legitimately can report a real `0`), letting
                # the narrative render a fabricated "N=0 < 5" sample-size
                # claim for a trade that was never actually binned at all.
                "bin_trade_count": None,
                "calibration_error": None,
                "reason": "Model calibration not applicable for manual or uncalibrated trades",
            }

        # 5. Narrative Generation (Zero None/NaN formatting leakage)
        narrative_text = build_trade_narrative(
            provenance=provenance,
            side=str(closed_trade.get("side") or "BUY").lower(),
            strategy_id=closed_trade.get("strategy_id"),
            entry_price=closed_trade.get("entry_price"),
            conviction=conviction,
            macro_regime=macro_regime,
            operator_notes=operator_notes,
            exit_price=closed_trade.get("exit_price"),
            holding_days=closed_trade.get("holding_period_days"),
            pnl=closed_trade.get("realized_pnl"),
            pnl_pct=closed_trade.get("realized_pnl_pct"),
            mfe=excursion_record.get("mfe"),
            mae=excursion_record.get("mae"),
            edge_ratio=excursion_record.get("edge_ratio"),
            bin_win_rate=calibration_record.get("bin_win_rate"),
            bin_count=calibration_record.get("bin_trade_count"),
            bridge_reached=(bridge_status == "bridged"),
            bars_available=bars_available,
        )

        record = {
            "trade_id": closed_trade.get("trade_id"),
            "symbol": closed_trade.get("symbol"),
            "strategy_id": closed_trade.get("strategy_id"),
            "pilot_id": closed_trade.get("pilot_id"),
            "experiment_arm": closed_trade.get("experiment_arm"),
            "side": closed_trade.get("side"),
            "qty": closed_trade.get("qty"),
            "entry_ts": closed_trade.get("entry_ts"),
            "entry_price": closed_trade.get("entry_price"),
            "exit_ts": closed_trade.get("exit_ts"),
            "exit_price": closed_trade.get("exit_price"),
            "commission": closed_trade.get("commission"),
            "realized_pnl": closed_trade.get("realized_pnl"),
            "realized_pnl_pct": closed_trade.get("realized_pnl_pct"),
            "holding_period_days": closed_trade.get("holding_period_days"),
            "close_reason": closed_trade.get("close_reason"),
            "provenance": provenance,
            "snapshot": snapshot_record,
            "entry_snapshot": snapshot_record,
            "bridge_status": bridge_status,
            "bridged_trade_id": closed_trade.get("bridged_trade_id"),
            "bridge_error": closed_trade.get("bridge_error"),
            "bridged_at": closed_trade.get("bridged_at"),
            "excursion": excursion_record,
            "evaluation": excursion_record,
            "calibration": calibration_record,
            "narrative": narrative_text,
            "narrative_text": narrative_text,
        }
        return record

    def compose_retrospectives_batch(
        self,
        symbol: str | None = None,
        strategy_id: str | None = None,
        limit: int = 100,
        data_provider: Any | None = None,
        paper_store: Any | None = None,
        transactions_store: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Compose retrospective records for closed trades matching query filters.
        Returns empty list [] if store contains zero closed trades.
        """
        target_paper_store = paper_store or self.paper_store
        if not hasattr(target_paper_store, "get_full_closed_trades"):
            return []

        try:
            # `get_full_closed_trades` only ever filters by `symbol` at the
            # SQL level; `strategy_id` is applied in Python below. Requesting
            # only `limit` rows here and THEN filtering by strategy_id would
            # apply the recency cap BEFORE the filter -- a strategy whose
            # matching trades sit outside the `limit` most-recent trades
            # OVERALL would silently under-sample (or come back empty)
            # instead of returning its own most-recent `limit` trades. Widen
            # the fetch window whenever a strategy_id filter is requested,
            # then truncate to `limit` AFTER filtering.
            fetch_limit = limit
            if strategy_id is not None:
                fetch_limit = max(limit * 20, 2000)
            trades = target_paper_store.get_full_closed_trades(symbol=symbol, limit=fetch_limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("compose_retrospectives_batch error: %s", exc)
            return []

        if not trades:
            return []

        if strategy_id is not None:
            trades = [t for t in trades if t.get("strategy_id") == strategy_id]
            trades = trades[:limit]

        # Precompute the calibration curve ONCE for the whole batch instead
        # of once per trade inside compose_trade_retrospective -- measured to
        # be ~83% of per-trade compose time at 20k rows (a real N+1; see that
        # method's own `calibration_df` docstring). A genuine query failure
        # here still lets the batch proceed: `calibration_df` stays the
        # UNSET sentinel and each trade's own compose call falls back to
        # computing (and honestly failing) its own, exactly as if no
        # precompute had been attempted.
        target_tx_store = transactions_store or self.transactions_store
        calibration_df = _CALIBRATION_UNSET
        try:
            from evaluation_engine import calibration_curve
            calibration_df = calibration_curve(target_tx_store, n_bins=10, min_trades_per_bin=5)
        except Exception as exc:  # noqa: BLE001
            logger.warning("compose_retrospectives_batch: calibration_curve precompute failed: %s", exc)
            calibration_df = _CALIBRATION_UNSET

        results = []
        for t in trades:
            t_id = t.get("trade_id")
            if t_id is not None:
                composed = self.compose_trade_retrospective(
                    t_id,
                    data_provider=data_provider,
                    paper_store=target_paper_store,
                    transactions_store=transactions_store,
                    calibration_df=calibration_df,
                )
                if composed is not None:
                    results.append(composed)
        return results


# =============================================================================
# Module-Level Convenience Functions
# =============================================================================

def compose_trade_retrospective(
    trade_id: int | str,
    data_provider: Any | None = None,
    composer: RetrospectiveComposer | None = None,
    paper_store: Any | None = None,
    transactions_store: Any | None = None,
    db_url: str | None = None,
    **kwargs,
) -> dict[str, Any] | None:
    """Compose a single trade retrospective using default or injected composer."""
    c = composer or RetrospectiveComposer(
        paper_store=paper_store,
        transactions_store=transactions_store,
        db_url=db_url,
        **kwargs,
    )
    return c.compose_trade_retrospective(
        trade_id,
        data_provider=data_provider,
        paper_store=paper_store,
        transactions_store=transactions_store,
    )


def compose_retrospectives_batch(
    symbol: str | None = None,
    strategy_id: str | None = None,
    limit: int = 100,
    data_provider: Any | None = None,
    composer: RetrospectiveComposer | None = None,
    paper_store: Any | None = None,
    transactions_store: Any | None = None,
    db_url: str | None = None,
    **kwargs,
) -> list[dict[str, Any]]:
    """Compose batch trade retrospectives using default or injected composer."""
    c = composer or RetrospectiveComposer(
        paper_store=paper_store,
        transactions_store=transactions_store,
        db_url=db_url,
        **kwargs,
    )
    return c.compose_retrospectives_batch(
        symbol=symbol,
        strategy_id=strategy_id,
        limit=limit,
        data_provider=data_provider,
        paper_store=paper_store,
        transactions_store=transactions_store,
    )

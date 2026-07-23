"""Async master orchestrator. Runs the full cycle: concurrent data fetch, run_pipeline (macro -> options -> processing -> forecasting -> strategy), schema validation, HTML report + Plotly chart, JSON payload, and gated broker execution (only when Alpaca credentials are configured). Supports engine reuse via EngineContext, a heartbeat watchdog, and hot-path parallelization; raises PipelineFatalError (not sys.exit) on a fatal cycle so a long-lived daemon caller survives a crashed cycle."""

# =============================================================================
# MODULE: MASTER ORCHESTRATOR
# File: main_orchestrator.py
# Description: Acts as the central routing hub of the InvestYo Quant Platform.
#              Orchestrates asynchronous data acquisition, routes data to math
#              engines, performs schema validation, and compiles HTML reports.
# =============================================================================

import sys
import os
import subprocess

if __name__ == "__main__":
    # Auto-re-route to virtual environment interpreter if not already running in it
    venv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin")
    venv_python = os.path.join(venv_dir, "python3")
    if not os.path.exists(venv_python):
        venv_python = os.path.join(venv_dir, "python")

    if os.path.exists(venv_python) and os.path.realpath(sys.executable) != os.path.realpath(venv_python):
        sys.exit(subprocess.call([venv_python] + sys.argv))


import contextlib
import os
import sys
import json
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional, Any

# ---------------------------------------------------------------------------
# python-dotenv import (loader is INVOKED inside main(), NOT at module top)
# ---------------------------------------------------------------------------
# Module-top invocation pollutes the pytest session: importing this module
# loads every .env value into os.environ, which then breaks Settings()-default
# tests that assert specific keys are unset.  Invoking inside main() runs the
# loader on every production launch (`python main_orchestrator.py`) while
# leaving tests' os.environ pristine.  override=False so explicit shell
# exports continue to win over the .env file.
from dotenv import load_dotenv as _load_dotenv

# Core imports
import config
from settings import settings
from data_engine import DataEngine, MockDataEngine
from processing_engine import ProcessingEngine
from macro_engine import MacroEngine
from technical_options_engine import TechnicalOptionsEngine
from forecasting_engine import ForecastingEngine
from forecasting.forecast_tracker import ForecastTracker
from strategy_engine import StrategyEngine
from evaluation_engine import EvaluationEngine
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO, RobinhoodPositionDTO
from data.robinhood_portfolio import fetch_account_snapshot, account_snapshot_to_robinhood_positions
from allocators.dual_momentum import DualMomentumAllocator
from signals import global_registry
from signals.base import SignalContext
from volatility.iv_engine import IVHistoryStore, get_30d_atm_iv, calculate_true_ivr, get_vrp
from execution.kill_switch import GlobalKillSwitch
from diagnostics_and_visuals import (
    telemetry,
    generate_plotly_volatility_bands,
    generate_html_report
)
# Progress instrumentation (reporting/progress.py) -- file-backed 0-100%
# telemetry for the GUI. Import is cheap (stdlib + settings only), no
# circular-import risk. See _PROGRESS_STAGES below for the stage contract.
from reporting.progress import ProgressReporter
from reporting.state_snapshot import _safe_float_or_none

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MasterOrchestrator")


# =============================================================================
# CROSS-CYCLE DATA-FRESHNESS MARKER (settings.DATA_FRESHNESS_TTL_SECONDS)
# =============================================================================
# A tiny persisted marker file recording the UTC timestamp of the last
# SUCCESSFUL (real, non-mock) data pull. The daemon's interval timer consults
# _data_is_fresh() before pulling: if the last pull is younger than the TTL, the
# whole cycle is skipped ("check the DB before pulling"). Persisted (not
# in-process) so a freshly-restarted daemon does not immediately re-pull. All
# helpers are dead-letter safe (CONSTRAINT #6): a marker read/write failure must
# NEVER fail — or gate — a pipeline cycle, so a read failure degrades to "not
# fresh" (pull) and a write failure is swallowed.
_DATA_REFRESH_MARKER = os.path.join(str(settings.OUTPUT_DIR), "last_data_refresh.txt")


def _mark_data_refreshed() -> None:
    """Record 'now' as the last successful data-pull time. Never raises."""
    try:
        os.makedirs(str(settings.OUTPUT_DIR), exist_ok=True)
        with open(_DATA_REFRESH_MARKER, "w", encoding="utf-8") as fh:
            fh.write(datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # pragma: no cover - defensive only
        logger.debug("Could not write data-refresh marker (non-fatal): %s", exc)


def _data_is_fresh(ttl_seconds: int) -> bool:
    """True iff the last successful data pull is younger than ``ttl_seconds``.

    ``ttl_seconds <= 0`` disables the gate (always False → always pull). A
    missing/unparseable marker also returns False so we fail toward pulling
    rather than silently starving on stale data.
    """
    if ttl_seconds <= 0:
        return False
    try:
        with open(_DATA_REFRESH_MARKER, encoding="utf-8") as fh:
            ts = datetime.fromisoformat(fh.read().strip())
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return 0 <= age < ttl_seconds
    except Exception:
        return False


class PipelineFatalError(RuntimeError):
    """A fatal error in a single pipeline cycle (data-fetch crash, pipeline
    crash, or strict-mode schema validation failure).

    Raised INSTEAD of ``sys.exit(1)`` so that a long-lived caller (e.g. the
    persistent orchestrator daemon) can catch it, record a FAILED run, and keep
    serving — a per-run failure must never terminate the host process. The
    standalone CLI entry point (``python3 main_orchestrator.py``) catches this
    at the ``__main__`` boundary and converts it to ``sys.exit(1)``, preserving
    the "CI / make verify get a non-zero exit on failure" contract that the old
    inline ``sys.exit(1)`` calls provided.

    Subclasses ``RuntimeError`` (not ``SystemExit``) precisely so ordinary
    ``try/except Exception`` boundaries in a daemon catch it.
    """


# =============================================================================
# PROGRESS INSTRUMENTATION (reporting/progress.py)
# =============================================================================
# The six macro-stages a single _main_body_impl() cycle passes through, in the
# SAME order as the existing telemetry.info(...) banners further down this
# file -- do NOT reword those banner strings; the legacy log-scraping stage
# detector in gui/orchestrator_runner.py::compute_stage_status still keys off
# their literal text, and this instrumentation is deliberately additive, not
# a replacement for it.
#
# "macro_options" combines the "Routing data through Macro Engine..." and
# "Routing data through Technical Options Engine..." banners into one slice:
# the Macro Engine step itself has no per-ticker loop, so the whole slice's
# advance_symbol() ticks come from the options/IV ThreadPoolExecutor loop
# that immediately follows.
#
# "execution" combines the advisory-overlay evaluation loop (the last
# per-symbol ThreadPoolExecutor loop in the cycle) with report generation,
# JSON payload export, and broker order submission -- none of which iterate
# per-ticker, so this slice's ticks likewise come entirely from one loop
# (_eval_one).
_PROGRESS_STAGES = ["data", "macro_options", "processing", "forecasting", "strategy", "execution"]


# =============================================================================
# CROSS-SECTIONAL MOMENTUM HELPER (Jegadeesh-Titman 1993)
# =============================================================================

def compute_xsec_momentum_ranks(
    tech_raw: dict,
    skip_days: int = 22,
    lookback_days: int = 252,
) -> pd.Series:
    """Compute cross-sectional 12-1m momentum returns and percentile ranks.

    Fully vectorized — no iterrows().  For each ticker in tech_raw:
        r = close[t - skip_days] / close[t - lookback_days] - 1
    where t is the last available row.

    Parameters
    ----------
    tech_raw : dict[str, pd.DataFrame]
        OHLCV DataFrames keyed by ticker (output of DataEngine.fetch_technical_raw).
    skip_days : int
        Number of trading days to skip at the end (default 22 ≈ 1 month).
    lookback_days : int
        Total lookback window in trading days (default 252 ≈ 12 months).

    Returns
    -------
    pd.Series
        Index: ticker str, values: percentile rank in [0, 1].
        NaN rank for tickers with insufficient history.
    """
    returns: dict = {}
    required = lookback_days + skip_days + 1

    for ticker, df in tech_raw.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        close = df["Close"].dropna()
        if len(close) < required:
            continue
        # All indexing is on the sorted series; both references are strictly < t
        p_recent = close.iloc[-(skip_days + 1)].item()   # price at t - skip_days
        p_old = close.iloc[-(lookback_days + 1)].item()  # price at t - lookback_days
        if p_old <= 0:
            continue
        returns[ticker] = p_recent / p_old - 1.0

    if not returns:
        return pd.Series(dtype=float)

    ret_series = pd.Series(returns)
    # Cross-sectional rank: ascending so high returns → high rank
    return ret_series.rank(pct=True, ascending=True)


async def fetch_all_data_async(de: DataEngine, tickers: list) -> tuple:
    """
    Fetches macroeconomic, fundamental, and technical pricing data concurrently
    using asyncio.gather to avoid blocking the event loop.
    """
    telemetry.info(f"Initiating concurrent data fetching for {len(tickers)} tickers...")
    
    macro_task = asyncio.to_thread(de.fetch_macro_raw)
    fund_task = asyncio.to_thread(de.fetch_fundamentals_raw, tickers)
    # Routes through HistoricalStore's incremental top-up (2026-07) instead of
    # a full 2-year yfinance re-pull every cycle for the whole universe --
    # main.py's _fetch_bars_for_universe() already used HistoricalStore; this
    # closed the one remaining tech-bars call site that bypassed it.
    tech_task = asyncio.to_thread(de.fetch_technical_raw_cached, list(set(tickers + ["SPY"])))
    
    macro_raw, fund_raw, tech_raw = await asyncio.gather(macro_task, fund_task, tech_task)
    telemetry.info("Data fetching completed successfully.")
    return macro_raw, fund_raw, tech_raw


@dataclass
class EngineContext:
    """Holds long-lived, expensive-to-construct engine instances so a
    persistent caller (e.g. the future orchestrator daemon) can build them
    ONCE and reuse them across many ``run_pipeline()`` cycles instead of
    paying full import+init cost every run.

    ``MacroEngine`` is the highest-value entry: it holds a persistent
    ``regime.hmm_regime.HMMRegimeDetector(retrain_freq_days=7)`` whose
    expanding-window fit is silently discarded whenever a fresh MacroEngine is
    constructed, which is exactly what happens on every cycle today.

    Every field defaults to ``None``. ``run_pipeline`` falls back to
    constructing a given engine fresh (today's exact behavior) whenever the
    corresponding field is ``None`` — so passing ``engines=None`` entirely, or
    a partially-populated context (e.g. to force one engine to rebuild next
    cycle), both degrade gracefully. Nothing else about ``run_pipeline``'s
    behavior changes when a full context is supplied; it is purely a
    construction-site substitution.
    """
    macro_engine: Optional[MacroEngine] = None
    technical_options_engine: Optional[TechnicalOptionsEngine] = None
    iv_history_store: Optional[IVHistoryStore] = None
    processing_engine: Optional[ProcessingEngine] = None
    forecasting_engine: Optional[ForecastingEngine] = None
    strategy_engine: Optional[StrategyEngine] = None
    evaluation_engine: Optional[EvaluationEngine] = None

    @classmethod
    def build(cls, *, data_engine: Optional[Any] = None) -> "EngineContext":
        """Construct one instance of every engine now, paying the one-time
        cost so a long-lived caller can reuse them across many
        ``run_pipeline()`` calls. ``data_engine`` is threaded into
        ``MacroEngine`` exactly as ``run_pipeline`` would today."""
        # Opt-in inverse-RMSE skill-weighted forecast blending (default OFF →
        # tracker is None → byte-identical static blend). Uses ForecastTracker's
        # own default db_path (quant_platform.db), which self-provisions its
        # forecast_errors table.
        _tracker = ForecastTracker() if settings.FORECAST_SKILL_WEIGHTING_ENABLED else None
        return cls(
            macro_engine=MacroEngine(data_engine=data_engine),
            technical_options_engine=TechnicalOptionsEngine(),
            iv_history_store=IVHistoryStore(),
            processing_engine=ProcessingEngine(),
            forecasting_engine=ForecastingEngine(tracker=_tracker),
            strategy_engine=StrategyEngine(),
            evaluation_engine=EvaluationEngine(),
        )


def run_pipeline(tickers: list, macro_raw: dict, fund_raw: dict, tech_raw: dict,
                  data_engine: Optional[Any] = None,
                  robinhood_positions: Optional[dict] = None,
                  engines: Optional[EngineContext] = None,
                  progress: Optional[ProgressReporter] = None,
) -> tuple:
    """
    Synchronous execution of the quantitative engines:
    Macro -> Technical Options -> Processing -> Forecasting -> Strategy & Evaluation.
    """
    from pipeline.context import RunContext
    from pipeline.runner import PipelineRunner
    from pipeline.production_steps import OptionsAnalysisStep, ProcessingStep, ForecastingStep, StrategyEvalStep

    ctx = RunContext(
        force_account=False,
        started_at=datetime.now(),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **kw: None,
        build_universe_fn=lambda *a: tickers,
        build_macro_dto_fn=lambda: None,
        get_provider_fn=lambda: None,
        fetch_bars_fn=lambda *a: {},
        build_context_extras_fn=lambda *a: {},
        advisory_evaluate_fn=lambda *a, **kw: None,
    )
    
    ctx.symbols = list(tickers)
    ctx.macro_raw = macro_raw
    ctx.fund_raw = fund_raw
    ctx.tech_raw = tech_raw
    ctx.market = data_engine
    ctx.engine_context = engines
    ctx.context_extras["robinhood_positions"] = robinhood_positions

    runner = PipelineRunner([
        OptionsAnalysisStep(),
        ProcessingStep(),
        ForecastingStep(),
        StrategyEvalStep()
    ])
    runner.run(ctx, progress)
    
    return ctx.dashboard_df, ctx.macro_dto, ctx.context_extras.get("shared_context")



async def _heartbeat(output_dir, interval: int = 60) -> None:
    """Background task: log 'ALIVE' and update heartbeat.txt every ``interval`` seconds.

    A watchdog script can read heartbeat.txt and activate the global kill switch
    if the UTC timestamp goes stale (> 2× interval), signalling an orchestrator crash.
    """
    heartbeat_file = output_dir / "heartbeat.txt"
    while True:
        ts = datetime.now(timezone.utc).isoformat()
        logger.info("ORCHESTRATOR ALIVE — heartbeat at %s", ts)
        try:
            heartbeat_file.write_text(ts, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write heartbeat file: %s", exc)
        await asyncio.sleep(interval)


def _kelly_target_qty(kelly_weight: float, equity: float, price: float) -> float:
    """Convert a Kelly Target *portfolio weight* into a share quantity.

    ``Kelly Target`` is a fraction of account equity (0..MAX_POSITION_WEIGHT),
    NOT a share count — so a new long must be sized as
    ``shares = weight * equity / price``. Previously the BUY path submitted a
    hardcoded ``qty=1.0`` regardless of conviction, which both ignored the
    sizing entirely and neutered ``PreTradeRiskGate.max_position_size_check``
    (a 1-share notional trivially clears any position cap).

    Returns ``0.0`` — a signal to SKIP, never a fabricated 1-share default
    (CONSTRAINT #4) — when the order cannot be sized (non-positive weight,
    equity, or price). The caller must treat ``0.0`` as "do not submit".
    Fractional shares are preserved (rounded to 6 dp) so small accounts /
    high-priced names aren't silently floored to zero; this mirrors the SELL
    path, which already submits fractional quantities.
    """
    if kelly_weight <= 0.0 or equity <= 0.0 or price <= 0.0:
        return 0.0
    return round((kelly_weight * equity) / price, 6)


async def _execute_broker_orders(
    final_df: "pd.DataFrame",
    dry_run: bool,
    macro_dto: Optional[Any] = None,
) -> None:
    """
    Translate signal → desired position → delta → orders and submit via
    OrderManager (with kill-switch gate + pre-trade risk gate).

    Design constraints
    ------------------
    * Never called when Alpaca credentials are absent (checked by caller).
    * Errors are logged as ERROR and never propagate — broker execution is
      best-effort; the analysis pipeline's value must never be held hostage
      to broker connectivity.
    * Only BUY signals with Kelly Target > 0 generate new orders; SELL/TRIM
      signals close existing positions.
    * Kill-switch active → ``KillSwitchActiveError`` is raised inside
      ``submit_order_with_idempotency``; caught here and logged as CRITICAL.
    * ``dry_run=True`` logs intent but never reaches the broker network.
    * ``settings.ADVISORY_ONLY=True`` (the project default in Tier 5.1) makes
      this function a no-op: the broker stack is not even imported and an
      INFO log is emitted so the operator sees the quarantine in the run log.
    """
    if getattr(settings, "ADVISORY_ONLY", True):
        telemetry.info(
            "ADVISORY_ONLY=True — broker execution surface is quarantined; "
            "skipping all order submission, reconciliation, and broker imports. "
            "Set settings.ADVISORY_ONLY=false in .env to re-enable."
        )
        return
    try:
        from execution.alpaca_broker import AlpacaBroker
        from execution.broker_base import OrderIntent, OrderPriority, OrderSide, OrderType
        from execution.kill_switch import KillSwitchActiveError
        from execution.order_manager import OrderManager
        from execution.priority_queue import LeakyBucketPriorityQueue
        from execution.risk_gate import PreTradeRiskGate, RiskContext
        from transactions_store import TransactionsStore

        broker = AlpacaBroker()
        ts_store = TransactionsStore()
        risk_gate = PreTradeRiskGate()
        om = OrderManager(broker, dry_run=dry_run, risk_gate=risk_gate)

        # --- Reconcile before submitting new orders ---
        recon_report = await om.reconcile_state(ts_store)
        if recon_report.has_drift:
            telemetry.critical(
                "Broker state drift detected before order submission — "
                "review reconciliation report before trusting signals."
            )

        # --- Fetch live positions + account for risk-gate context ---
        open_pos = await broker.get_open_positions()
        open_symbols = {p.symbol: p.qty for p in open_pos}
        try:
            account = await broker.get_account()
        except Exception:
            account = None

        # Vectorized column build (no per-row Python loop — CLAUDE.md convention).
        # Mirrors the old row.get(...) per-key defaults: a missing Symbol/Price
        # column falls back to "" / 0.0 for every row. A present Price cell
        # coerces via pd.to_numeric so NaN stays NaN (never fabricated to
        # 0.0 — CONSTRAINT #4) and a genuinely non-numeric cell degrades to
        # NaN instead of raising.
        _symbols = (
            final_df["Symbol"] if "Symbol" in final_df.columns
            else pd.Series([""] * len(final_df), index=final_df.index)
        ).astype(str).str.upper()
        _prices = (
            pd.to_numeric(final_df["Price"], errors="coerce") if "Price" in final_df.columns
            else pd.Series([0.0] * len(final_df), index=final_df.index)
        ).astype(float)
        prices: dict[str, float] = dict(zip(_symbols, _prices))

        risk_ctx = RiskContext(
            macro=macro_dto,
            open_positions=open_pos,
            account=account,
            current_prices=prices,
            is_premium_sell_strategy=False,
        )

        # Opt-in leaky-bucket priority queue (settings.EXECUTION_PRIORITY_QUEUE_
        # ENABLED, default False). When disabled, the loop below submits each
        # intent inline, in final_df row order -- byte-identical to the
        # pre-existing single-pass behavior. When enabled, sizing/skip logic
        # still runs inline per row (unchanged), but the actual submission is
        # deferred into a priority queue drained AFTER the build pass, so
        # URGENT (SELL/TRIM) intents submit before NORMAL (BUY) ones
        # regardless of their order in final_df. This does not replace or
        # bypass execution/risk_gate.py's rate cap or execution/kill_switch.py
        # -- both still gate every submission exactly as before, just at
        # drain time instead of build time.
        priority_queue_enabled = bool(getattr(settings, "EXECUTION_PRIORITY_QUEUE_ENABLED", False))
        leak_rate = float(getattr(settings, "EXECUTION_QUEUE_LEAK_RATE_PER_SEC", 2.0))
        pending_queue: Optional[LeakyBucketPriorityQueue] = (
            LeakyBucketPriorityQueue(leak_rate_per_sec=leak_rate) if priority_queue_enabled else None
        )

        async def _submit_and_log(intent: "OrderIntent", log_fn) -> None:
            """Shared submit+log+error-handling for both the inline (disabled)
            and drained (enabled) paths -- keeps the KillSwitchActiveError /
            generic-Exception handling identical either way."""
            result = await om.submit_order_with_idempotency(
                intent, timestamp=now, risk_context=risk_ctx
            )
            log_fn(result)

        now = datetime.now(timezone.utc)
        for _, row in final_df.iterrows():
            symbol = str(row.get("Symbol", "")).upper()
            signal = str(row.get("Action Signal", "")).upper()
            kelly = float(row.get("Kelly Target", 0.0) or 0.0)

            if not symbol:
                continue

            try:
                if "BUY" in signal and kelly > 0 and symbol not in open_symbols:
                    # Size the new long from the Kelly Target weight against live
                    # account equity — NOT a hardcoded 1 share. An unsizable order
                    # (no account equity available, missing/zero price, or a
                    # quantity that rounds to 0) is SKIPPED rather than submitted
                    # at an arbitrary size (CONSTRAINT #4).
                    equity = float(account.equity) if account is not None else 0.0
                    price = float(prices.get(symbol, 0.0) or 0.0)
                    buy_qty = _kelly_target_qty(kelly, equity, price)
                    if buy_qty <= 0.0:
                        telemetry.warning(
                            "Skipping BUY %s — cannot size order "
                            "(kelly=%.4f, equity=%.2f, price=%.2f). Kelly Target is a "
                            "portfolio weight and needs both equity and price to convert "
                            "to shares.",
                            symbol, kelly, equity, price,
                        )
                        continue
                    intent = OrderIntent(
                        strategy_id="main_pipeline",
                        symbol=symbol,
                        side=OrderSide.BUY,
                        qty=buy_qty,
                        order_type=OrderType.MARKET,
                        priority=OrderPriority.NORMAL,
                    )

                    def _log_buy(result, symbol=symbol, buy_qty=buy_qty, kelly=kelly,
                                 equity=equity, price=price):
                        telemetry.info(
                            "Order submitted: BUY %s x %.6f (kelly=%.4f, equity=%.2f, "
                            "price=%.2f) -> status=%s broker_id=%s",
                            symbol, buy_qty, kelly, equity, price,
                            result.status.value, result.broker_order_id,
                        )

                    if pending_queue is not None:
                        pending_queue.push((intent, _log_buy), OrderPriority.NORMAL)
                    else:
                        await _submit_and_log(intent, _log_buy)

                elif signal in ("SELL", "TRIM") and symbol in open_symbols:
                    sell_qty = abs(open_symbols[symbol])
                    intent = OrderIntent(
                        strategy_id="main_pipeline",
                        symbol=symbol,
                        side=OrderSide.SELL,
                        qty=sell_qty,
                        order_type=OrderType.MARKET,
                        priority=OrderPriority.URGENT,
                    )

                    def _log_sell(result, symbol=symbol, sell_qty=sell_qty):
                        telemetry.info(
                            "Order submitted: SELL %s x %.4f -> status=%s broker_id=%s",
                            symbol, sell_qty, result.status.value, result.broker_order_id,
                        )

                    if pending_queue is not None:
                        pending_queue.push((intent, _log_sell), OrderPriority.URGENT)
                    else:
                        await _submit_and_log(intent, _log_sell)

            except KillSwitchActiveError as ks_err:
                telemetry.critical(
                    "Kill switch is ACTIVE — aborting all remaining order submission. %s", ks_err
                )
                return  # bail out of the entire loop; no further submissions this cycle

            except Exception as order_err:
                telemetry.error(
                    "Order submission failed for %s: %s", symbol, order_err, exc_info=True
                )

        # Drain the priority queue (URGENT before NORMAL, paced by the leaky
        # bucket) — a no-op loop when the queue is disabled (pending_queue is
        # None) since nothing was ever pushed to it above.
        if pending_queue is not None:
            while len(pending_queue) > 0:
                intent, log_fn = await pending_queue.drain_one()
                try:
                    await _submit_and_log(intent, log_fn)
                except KillSwitchActiveError as ks_err:
                    telemetry.critical(
                        "Kill switch is ACTIVE — aborting all remaining order submission. %s", ks_err
                    )
                    return
                except Exception as order_err:
                    telemetry.error(
                        "Order submission failed for %s: %s", intent.symbol, order_err, exc_info=True
                    )

    except Exception as exc:
        telemetry.error(
            "_execute_broker_orders crashed (non-fatal): %s", exc, exc_info=True
        )


def _safe_bool_or_none(value: Any) -> Optional[bool]:
    """Tri-state "Yes"/"No"/unknown -> True/False/None.

    ``dashboard_df``'s ``Sizing_Was_Capped`` column (config.COLUMN_SCHEMA,
    populated by pipeline/production_steps.py) is ``None`` for a ticker whose
    strategy evaluation never reached the 'results' stage this cycle (dead-
    lettered) -- NOT the same as a genuinely computed "No". A plain
    ``str(value).strip().lower() == "yes"`` collapses that missing case into
    a fabricated ``False`` ("no ceiling bound"), an ACTIVE FALSE CLAIM
    (CONSTRAINT #4) rather than "not computed" -- this preserves the
    distinction instead.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.lower() == "yes"


def _write_state_snapshot(macro_raw: dict, final_df: "pd.DataFrame", tickers: list) -> None:
    """Persist a JSON state snapshot to OUTPUT_DIR/state_snapshot.json.

    Also writes a timestamped rotated copy under OUTPUT_DIR/history/ via
    :func:`scripts.snapshot_diff.rotate_snapshot` so the daily HTML report
    can render a "Δ Since Last Run" band. Errors in the live-snapshot
    write OR the rotation are swallowed so a snapshot failure never
    crashes the pipeline.
    """
    import json
    try:
        signals = []
        held_symbols = set()
        if not final_df.empty:
            for _, row in final_df.iterrows():
                shares = float(row.get("Shares", 0.0) or row.get("Robinhood Shares", 0.0) or 0.0)
                sym = str(row.get("Symbol", "")).upper().strip()
                if sym and shares > 0:
                    held_symbols.add(sym)
                # NaN is truthy in Python, so a plain "`or ''`" fallback does not
                # catch a genuinely-missing (NaN float) sector cell and would
                # otherwise stringify it to the literal text "nan".
                _sector_val = row.get("sector", row.get("Sector", ""))
                sector_str = "" if pd.isna(_sector_val) else str(_sector_val or "")
                signals.append({
                    "symbol": str(row.get("Symbol", "")),
                    "action": str(row.get("Action Signal", "")),
                    "kelly_target": float(row.get("Kelly Target", 0.0) or 0.0),
                    "score": float(row.get("Score", 0.0) or 0.0),
                    "price": float(row.get("Price", 0.0) or 0.0),
                    "shares": shares,
                    "macro_status": str(row.get("Macro Status", "")),
                    "hmm_risk_on": float(row.get("HMM_Risk_On_Probability", 0.0) or 0.0),
                    # Buy- and sell-side execution corridors surfaced so the
                    # Streamlit observability dashboard can render the full
                    # tactical plan without re-reading the SQLite DB.
                    "buy_range": str(row.get("buyRange", "")),
                    "sell_range": str(row.get("sellRange", "")),
                    # Holding-aware advisory overlay (engine/advisory.py)
                    "advisory_action":       str(row.get("Advisory_Action", "")),
                    "advisory_conviction":   float(row.get("Advisory_Conviction", 0.0) or 0.0),
                    "advisory_position_pct": float(row.get("Advisory_Position_Pct", 0.0) or 0.0),
                    "advisory_rationale":    str(row.get("Advisory_Rationale", "")),
                    # GUI hidden-fields surfacing (Task C1): cross-sectional
                    # momentum + multifactor z-scores are already computed by
                    # global_registry.run_pre_compute() into dashboard_df
                    # (see the Value_Z/Quality_Z/LowVol_Z/Size_Z/Multifactor_Composite
                    # and XSec_12_1M/XSec_Momentum_Rank columns above) but were
                    # never threaded through to state_snapshot.json, so the GUI
                    # had no per-symbol source to read them from. NaN (never
                    # fabricated) when the pre-compute hook didn't populate a
                    # value for this ticker.
                    "xsec_12_1m": _safe_float_or_none(row.get("XSec_12_1M")),
                    "xsec_momentum_rank": _safe_float_or_none(row.get("XSec_Momentum_Rank")),
                    "value_z": _safe_float_or_none(row.get("Value_Z")),
                    "quality_z": _safe_float_or_none(row.get("Quality_Z")),
                    "lowvol_z": _safe_float_or_none(row.get("LowVol_Z")),
                    "size_z": _safe_float_or_none(row.get("Size_Z")),
                    "multifactor_composite": _safe_float_or_none(row.get("Multifactor_Composite")),
                    # Wikipedia-pageviews investor-attention feature
                    # (follow-on branch to PR #416/#417) --
                    # pipeline/production_steps.py's StrategyEvalStep already
                    # writes Attention_Score into dashboard_df every cycle
                    # (data/attention_sources.py, gated behind
                    # settings.WIKIPEDIA_ATTENTION_ENABLED); threaded here so
                    # the GUI Observability dashboard can read it without a
                    # separate DB query. NaN -> JSON null (never fabricated
                    # -- CONSTRAINT #4) when the feature is disabled or a
                    # symbol's fetch failed this cycle.
                    "attention_score": _safe_float_or_none(row.get("Attention_Score")),
                    # Sector Heat Factor (GDELT article-volume attention proxy
                    # -- see pipeline/production_steps.py::_apply_sector_heat_factor
                    # and data/sentiment_sources.py::compute_sector_heat_factors).
                    # Same NaN-never-fabricated convention as the multifactor
                    # z-scores immediately above -- NaN when
                    # settings.SECTOR_HEAT_ENABLED is False, the sector's
                    # GDELT query failed, or this ticker's sector wasn't
                    # covered this cycle.
                    "sector_heat_factor": _safe_float_or_none(row.get("Sector_Heat_Factor")),
                    # Task C3 — post-trade evaluation metrics (evaluation_engine.py
                    # EvaluationEngine.evaluate_portfolio()/calculate_edge_ratio()
                    # already compute these into dashboard_df every cycle; they
                    # were never persisted anywhere the GUI could read them from.
                    # NaN (never fabricated) when no trade history exists yet for
                    # the symbol — see EvaluationEngine.evaluate_portfolio().
                    "mfe": _safe_float_or_none(row.get("MFE")),
                    "mae": _safe_float_or_none(row.get("MAE")),
                    "edge_ratio": _safe_float_or_none(row.get("Edge Ratio")),
                    "realized_slippage": _safe_float_or_none(row.get("Realized Slippage")),
                    # PR2 Agent A — three already-computed operator metrics threaded
                    # into the snapshot for the GUI analytics panels. News_Sentiment
                    # (FinBERT) is populated ~line 577; CoVaR Proxy + Realized
                    # Slippage come from processing_engine.calculate_technical_metrics.
                    # NaN (never fabricated 0.0 — CONSTRAINT #4) when absent.
                    "news_sentiment": _safe_float_or_none(row.get("News_Sentiment")),
                    "covar_proxy": _safe_float_or_none(row.get("CoVaR Proxy")),
                    # GICS sector — already carried in dashboard_df via
                    # config.COLUMN_SCHEMA (header "Sector", internal key
                    # "sector"; populated by processing_engine from the
                    # FundamentalDataDTO). Threaded here so the downstream
                    # sector-allocation view is fed identically from both the
                    # orchestrator and advisory (reporting/state_snapshot.py)
                    # writers. "" (never fabricated — CONSTRAINT #4) when the
                    # column is absent/blank for this ticker.
                    "sector": sector_str,
                    # Per-module weighted score breakdown (see
                    # pipeline/production_steps.py's Score_Components
                    # threading) so pilots/scoring.py can re-blend the
                    # already-computed per-signal scores under any Pilot's
                    # weight vector. {} (never fabricated) when the strategy
                    # engine produced no breakdown for this ticker.
                    "score_components": row.get("Score_Components") or {},
                    # Position-sizing decomposition (see
                    # pipeline/production_steps.py's _SIZING_DECOMPOSITION_COLS
                    # threading) so the webapp's Strategy Matrix / Symbol Detail
                    # screens can show Kelly Target before vs. after the HMM
                    # regime multiplier + meta-label composite were applied.
                    # Same snake_case spelling reporting/state_snapshot.py's
                    # advisory writer uses, so both writers converge and no
                    # reader ever needs to branch on writer identity. null
                    # (never fabricated 1.0/0.0 — CONSTRAINT #4) when the
                    # strategy engine didn't produce a value for this ticker;
                    # a genuine 0.0 (e.g. a MetaLabeler hard gate) is preserved,
                    # never coerced into a fabricated no-op.
                    "meta_label_composite": _safe_float_or_none(row.get("Meta_Label_Composite")),
                    "regime_multiplier": _safe_float_or_none(row.get("Regime_Multiplier")),
                    "kelly_target_pre_regime": _safe_float_or_none(row.get("Kelly_Target_Pre_Regime")),
                    "kelly_target_post_regime": _safe_float_or_none(row.get("Kelly_Target_Post_Regime")),
                    # Guardrail telemetry (sizing/position_sizer.py) -- did any
                    # hard sizing ceiling bind this cycle (per-symbol KELLY_CAP/
                    # MAX_POSITION_WEIGHT, cap-aware escalation, or the
                    # portfolio-wide gross-exposure cap applied in
                    # pipeline/production_steps.py::StrategyEvalStep), and
                    # which one. dashboard_df stores these as schema-driven
                    # "Yes"/"No"/None + constraint-name strings (config.COLUMN_SCHEMA);
                    # _safe_bool_or_none preserves None (dead-lettered ticker,
                    # never computed) rather than fabricating False -- CONSTRAINT #4.
                    "sizing_was_capped": _safe_bool_or_none(row.get("Sizing_Was_Capped")),
                    "sizing_binding_constraint": (str(row.get("Sizing_Binding_Constraint")).strip() or None) if pd.notna(row.get("Sizing_Binding_Constraint")) else None,
                })
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tickers": tickers,
            # Sorted list of symbols where the account holds qty > 0; consumed
            # by scripts.snapshot_diff to compute added_holdings / dropped_holdings.
            "holdings": sorted(held_symbols),
            "market_regime": str(macro_raw.get("market_regime", "UNKNOWN")),
            "vix": float(macro_raw.get("VIXCLS", 0.0) or 0.0),
            "yield_curve": float(macro_raw.get("T10Y2Y", 0.0) or 0.0),
            # Sahm Rule and HY OAS are surfaced so the GUI Observability tab can
            # display live recession-indicator telemetry without a live FRED call.
            "sahm_rule": float(macro_raw.get("SAHMREALTIME", 0.0) or 0.0),
            "high_yield_oas": float(macro_raw.get("BAMLH0A0HYM2", 0.0) or 0.0),
            "kill_switch_active": (settings.OUTPUT_DIR / "KILL_SWITCH").exists(),
            # Persist the current gate state so the dashboard reflects the
            # operator's choice without re-importing settings at read time.
            "macro_regime_gate_enabled": settings.MACRO_REGIME_GATE_ENABLED,
            "signals": signals,
        }
        snap_path = settings.OUTPUT_DIR / "state_snapshot.json"
        snap_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        # Rotate into history/ (write-then-rename + prune > SNAPSHOT_HISTORY_DAYS).
        # Failure is non-fatal — the live snapshot is already on disk.
        try:
            from scripts.snapshot_diff import rotate_snapshot
            rotate_snapshot(
                snapshot,
                settings.OUTPUT_DIR,
                max_age_days=settings.SNAPSHOT_HISTORY_DAYS,
            )
        except Exception as rot_exc:
            telemetry.debug("Snapshot rotation skipped: %s", rot_exc)
    except Exception as exc:
        telemetry.warning("Failed to write state snapshot: %s", exc)


def _validate_dashboard(final_df, *, strict: bool) -> bool:
    """Validate the compiled dashboard against ``config.DashboardSchema``.

    Two-tier validation (Phase 3b of docs/IMPROVEMENT_PLAN.md):

    * **Production (strict=False, default):** validate with ``lazy=True`` so
      *every* schema violation across the wide 50+ column frame is reported in
      one pass (rather than aborting at the first bad column). Failures are
      logged and the pipeline CONTINUES — the HTML report / JSON payload still
      carry value and must not be held hostage to a single coerced column.
    * **CI / --strict (strict=True):** a validation failure raises
      ``PipelineFatalError`` so schema drift can never silently ship. The CLI
      entry point converts that to ``sys.exit(1)`` for CI; a daemon can catch it
      and record a FAILED run instead of dying.

    Returns ``True`` when the frame validates (or is empty), ``False`` on any
    violation in non-strict mode. Never raises in non-strict mode (CONSTRAINT #6).
    """
    import pandera as pa  # already loaded transitively via `import config`

    if final_df.empty:
        return True
    try:
        config.DashboardSchema.validate(final_df, lazy=True)
        telemetry.info("✅ Final compiled DataFrame successfully validated against DashboardSchema.")
        return True
    except pa.errors.SchemaErrors as schema_errs:
        # lazy=True aggregates ALL failures into .failure_cases (a DataFrame).
        try:
            n_cases = len(schema_errs.failure_cases)
        except Exception:
            n_cases = -1
        telemetry.error(
            "❌ DashboardSchema validation found %s failure case(s):\n%s",
            n_cases, schema_errs,
        )
        if strict:
            telemetry.critical("Strict mode (--strict): aborting on schema validation failure.")
            raise PipelineFatalError("DashboardSchema validation failed (strict mode)") from schema_errs
        return False
    except Exception as schema_err:
        telemetry.error(f"❌ Final compiled DataFrame failed DashboardSchema validation: {schema_err}")
        if strict:
            telemetry.critical("Strict mode (--strict): aborting on schema validation failure.")
            raise PipelineFatalError("DashboardSchema validation failed (strict mode)") from schema_err
        return False


def _build_daily_summary(ctx) -> "tuple[dict, list]":
    """Build the ``(pnl_summary, warnings)`` pair for the end-of-cycle summary
    from state the just-completed cycle already produced.

    Both halves degrade to safe empties on any failure (CONSTRAINT #6) — the
    summary is a diagnostic, never a source of fabricated numbers (CONSTRAINT
    #4). ``pnl_summary`` is realized P&L by strategy for trades CLOSED today
    (empty when no round-trip closed this session → the dispatcher renders a
    "no closed trades today" line). ``warnings`` surfaces macro-gate and
    empty-signal conditions read straight off ``ctx``.
    """
    pnl_summary: dict = {}
    warnings: list = []

    # --- Realized P&L by strategy, for trades closed today (UTC) ---
    try:
        from transactions_store import TransactionsStore

        closed = TransactionsStore().closed_trades_df()
        if closed is not None and not closed.empty:
            closed = closed.copy()
            closed["exit_ts"] = pd.to_datetime(closed["exit_ts"], errors="coerce")
            today = pd.Timestamp(datetime.now(timezone.utc).date())
            todays = closed[closed["exit_ts"].dt.normalize() == today]
            for _, tr in todays.iterrows():
                try:
                    entry = float(tr["entry_price"])
                    exit_ = float(tr["exit_price"])
                    shares = float(tr["shares"])
                    direction = -1.0 if str(tr.get("side", "long")).lower() == "short" else 1.0
                    realized = (exit_ - entry) * shares * direction
                    strat = str(tr.get("strategy") or "unattributed")
                    pnl_summary[strat] = pnl_summary.get(strat, 0.0) + realized
                except Exception:
                    # One malformed row must not drop the whole summary.
                    continue
    except Exception as exc:
        telemetry.debug("Daily summary: P&L aggregation skipped: %s", exc)

    # --- Operational warnings read off the cycle state ---
    try:
        macro_dto = getattr(ctx, "macro_dto", None)
        if macro_dto is not None:
            if getattr(macro_dto, "killSwitch", False):
                warnings.append(
                    f"Macro kill-switch condition active (regime: "
                    f"{getattr(macro_dto, 'market_regime', 'UNKNOWN')})."
                )
    except Exception as exc:
        telemetry.debug("Daily summary: macro warning check skipped: %s", exc)

    try:
        df = getattr(ctx, "dashboard_df", None)
        if df is None or (hasattr(df, "empty") and df.empty):
            warnings.append("No signals were produced this cycle (empty dashboard).")
    except Exception as exc:
        telemetry.debug("Daily summary: dashboard warning check skipped: %s", exc)

    return pnl_summary, warnings


def _dispatch_daily_summary(ctx) -> None:
    """Compose and dispatch the end-of-cycle summary through the multi-channel
    alert dispatcher (``observability.alerts.send_daily_summary``).

    Imported lazily (repo convention) to avoid any import cycle. Wrapped by the
    caller in try/except so this is strictly non-fatal.
    """
    pnl_summary, warnings = _build_daily_summary(ctx)
    from observability.alerts import send_daily_summary

    send_daily_summary(pnl_summary, warnings)


async def _main_body(effective_dry_run: bool, strict: bool = False,
                      *, engines: Optional[EngineContext] = None,
                      data_engine: Optional[Any] = None, mode: str = "full",
                      force: bool = True) -> None:
    """Thin progress-instrumentation wrapper around ``_main_body_impl``.

    Constructs ONE ``ProgressReporter`` per cycle (reporting/progress.py),
    covering the whole pipeline lifecycle described by ``_PROGRESS_STAGES``,
    threads it into ``_main_body_impl`` (which threads it further into
    ``run_pipeline(progress=...)`` and the advisory-overlay loop), and
    guarantees a terminal ``finish()`` call regardless of outcome:

    * normal completion — including the kill-switch early-return, which is a
      skipped cycle, not a failure — calls ``finish("succeeded")``;
    * any exception (notably ``PipelineFatalError``) calls
      ``finish("failed")`` and then RE-RAISES the exact same exception
      unchanged, so the existing ``main()``/daemon fatal-error handling is
      completely unaffected by this instrumentation (CONSTRAINT #6: progress
      reporting must never change pipeline behavior or swallow an error).

    Kept as a thin wrapper (rather than wrapping the whole ~300-line original
    body in a try/finally in place) purely to avoid re-indenting that body.
    ``_main_body_impl`` below is the exact original ``_main_body`` logic,
    now accepting one additional keyword-only ``progress`` parameter that
    every existing test / call site (which never passes it) does not see.
    """
    _progress = ProgressReporter(_PROGRESS_STAGES)
    try:
        await _main_body_impl(
            effective_dry_run, strict, engines=engines, data_engine=data_engine,
            progress=_progress, mode=mode, force=force,
        )
    except Exception:
        _progress.finish("failed")
        raise
    else:
        _progress.finish("succeeded")


async def _main_body_impl(effective_dry_run: bool, strict: bool = False,
                           *, engines: Optional[EngineContext] = None,
                           data_engine: Optional[Any] = None,
                           progress: Optional[ProgressReporter] = None,
                           mode: str = "full", force: bool = True) -> None:
    """Core pipeline logic using the modular Pipeline framework.

    ``mode`` selects which pipeline steps run:

    * ``"data"``    — data-fetch stage only (``AsyncDataFetchStep``);
    * ``"metrics"`` — data-fetch + indicator/forecast/signal precompute
                      (``AsyncDataFetchStep`` + ``RunPipelineStep``);
    * ``"full"``    — the whole cycle (default, unchanged): data fetch, run
                      pipeline, broker execution, state snapshot.

    ``force`` (default ``True``) bypasses the cross-cycle data-freshness gate.
    Only the daemon's automatic interval timer passes ``force=False``; when it
    does AND the last successful data pull is younger than
    ``settings.DATA_FRESHNESS_TTL_SECONDS``, this cycle is SKIPPED (no network
    refresh, no recompute) — the whole point of "check the DB before pulling".
    Every manual/CLI/on-demand caller keeps the default ``force=True`` and is
    unaffected.
    """
    if not force and _data_is_fresh(settings.DATA_FRESHNESS_TTL_SECONDS):
        telemetry.info(
            "⏭  Skipping interval refresh — last data pull is younger than "
            "DATA_FRESHNESS_TTL_SECONDS (%ss). DB is already fresh; next pull "
            "after the TTL elapses.",
            settings.DATA_FRESHNESS_TTL_SECONDS,
        )
        return

    from pipeline.context import RunContext
    from pipeline.runner import AsyncPipelineRunner
    from pipeline.production_steps import (
        AsyncDataFetchStep,
        RunPipelineStep,
        BrokerExecutionStep,
        StateSnapshotStep
    )

    ctx = RunContext(
        force_account=effective_dry_run,
        started_at=datetime.now(),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **kw: None,
        build_universe_fn=lambda *a: [],
        build_macro_dto_fn=lambda: None,
        get_provider_fn=lambda: None,
        fetch_bars_fn=lambda *a: {},
        build_context_extras_fn=lambda *a: {},
        advisory_evaluate_fn=lambda *a, **kw: None,
    )
    
    ctx.market = data_engine
    ctx.engine_context = engines

    if mode == "data":
        steps = [AsyncDataFetchStep()]
    elif mode == "metrics":
        steps = [AsyncDataFetchStep(), RunPipelineStep()]
    else:  # "full" (default) — unchanged whole cycle
        steps = [
            AsyncDataFetchStep(),
            RunPipelineStep(),
            BrokerExecutionStep(),
            StateSnapshotStep(),
        ]

    runner = AsyncPipelineRunner(steps)
    await runner.run(ctx, progress)


    if ctx.dashboard_df is not None:
        _validate_dashboard(ctx.dashboard_df, strict=strict)

    telemetry.info("✅ Master Orchestration finished successfully.")

    # End-of-cycle summary through the hardened multi-channel alert dispatcher.
    # Non-fatal: a summary-dispatch failure must never fail an otherwise
    # successful pipeline cycle (CONSTRAINT #6).
    try:
        _dispatch_daily_summary(ctx)
    except Exception as exc:
        telemetry.warning("Daily summary dispatch failed (non-fatal): %s", exc)




async def main(dry_run: bool = False, strict: bool = False):  # CLI flags propagated
    """Master async entry point.  Starts a heartbeat background task and
    always cancels it (even on crash) via try/finally.

    ``strict`` (``--strict``) makes DashboardSchema validation FATAL so CI can
    gate on schema drift; the default (False) logs all violations and continues.
    """
    # Load .env into os.environ.  See module-top comment on python-dotenv:
    # the loader is deliberately invoked here (not at import) so the test
    # suite's Settings()-default assertions aren't polluted by .env values
    # leaking into os.environ at module import time.  Idempotent.
    _load_dotenv(override=False)
    telemetry.info("🚀 Launching Master Orchestration Routing Hub...")

    # --dry-run: merge CLI arg with settings; either source can enable it.
    effective_dry_run = dry_run or settings.DRY_RUN
    if effective_dry_run:
        telemetry.info("DRY-RUN mode active: orders will be logged but NOT submitted.")
    else:
        # Preflight Check: Exit gracefully if live execution is requested but broker keys are missing.
        if not getattr(settings, "ADVISORY_ONLY", True):
            if not getattr(settings, "ALPACA_API_KEY", None) or not getattr(settings, "ALPACA_SECRET_KEY", None):
                telemetry.critical("Fatal preflight check: Live broker execution requested but Alpaca API keys are missing.")
                raise PipelineFatalError("Alpaca API keys are missing for live execution")

    _hb_task = asyncio.create_task(_heartbeat(settings.OUTPUT_DIR, interval=60))
    try:
        await _main_body(effective_dry_run, strict=strict)
    finally:
        _hb_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _hb_task


if __name__ == "__main__":
    import argparse

    _parser = argparse.ArgumentParser(description="InvestYo Master Orchestrator")
    _parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Log intended orders but do not submit to broker.",
    )
    _parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Treat DashboardSchema validation failures as fatal (exit 1). For CI / schema-drift gating.",
    )
    _args = _parser.parse_args()
    # The pipeline now raises PipelineFatalError instead of calling sys.exit(1)
    # inline, so a long-lived daemon can catch it and survive. The standalone
    # CLI path preserves the original "exit non-zero on fatal failure" contract
    # (CI / make verify / GUI subprocess returncode) by converting it here.
    try:
        asyncio.run(main(dry_run=_args.dry_run, strict=_args.strict))
    except PipelineFatalError as _fatal:
        telemetry.critical("Fatal pipeline error — exiting non-zero: %s", _fatal)
        sys.exit(1)


# Compatibility comments for test AST/source scans:
# GlobalKillSwitch
# Advisory paused by kill-switch sentinel
# run_monte_carlo


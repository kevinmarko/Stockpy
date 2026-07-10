"""
main.py — InvestYo Quant Platform Entry Point
============================================
Clean sequential orchestrator that runs one full pipeline cycle per refresh.

Pipeline stages (per cycle)
---------------------------
  A. Account snapshot   — Robinhood positions via data.robinhood_portfolio,
                          at-most-once-per-day (daily JSON cache).
  B. Universe build     — held symbols ∪ WATCHLIST env var ∪ watchlist.txt
  C. Macro context      — FRED data + HMM second opinion (degrades to neutral
                          defaults when FRED_API_KEY is not configured)
  D. Context pre-compute— 12-1m cross-sectional momentum ranks + multifactor
                          composites built once for the full universe before
                          the per-symbol loop so advisory signals see real
                          cross-sectional data rather than the 0-score fallback
  E. Per-symbol evaluate— market data + advisory engine; dead-letter error
                          capture per symbol; never aborts the run
  F. Sheet sink         — write RunResult to Google Sheets (skipped when
                          credentials.json is absent)
  G. HTML report        — generate daily HTML report (skipped on IO error)
  H. Run summary        — structured log line; return RunResult

Two-tier refresh cadence
------------------------
  Account tier  : Robinhood snapshot fetched at most once per day via a daily
                  JSON cache at cache/account_snapshot.json.  Use --refresh-account
                  to force a fresh login on this launch; subsequent iterations of
                  --interval mode then resume normal caching.
  Market tier   : prices, bars, indicators, forecasts refreshed on every call to
                  run_once() from the live market-data provider.

NOTE — Double-fetch for pre-compute
  _fetch_bars_for_universe() fetches OHLCV history once for the full universe
  to build the cross-sectional pre-compute context.  engine.advisory.evaluate()
  will then fetch bars again per symbol internally (the market provider does not
  cache bars, only quotes).  This is a known tradeoff accepted for correctness:
  pre-compute requires the full universe before the per-symbol loop, so bars
  must be fetched upfront.  Future optimisation: extend MarketDataProvider with
  a bars cache or pass bars through to advisory via context_extras.
"""

# ---------------------------------------------------------------------------
# Auto-route to the project's .venv interpreter (must be first executable code)
# ---------------------------------------------------------------------------
import sys
import os
import subprocess as _sp

_venv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin")
_venv_python = os.path.join(_venv_dir, "python3")
if not os.path.exists(_venv_python):
    _venv_python = os.path.join(_venv_dir, "python")
if os.path.exists(_venv_python) and os.path.realpath(sys.executable) != os.path.realpath(_venv_python):
    sys.exit(_sp.call([_venv_python] + sys.argv))

# ---------------------------------------------------------------------------
# Standard-library imports (after venv guarantee)
# ---------------------------------------------------------------------------
import argparse
import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# python-dotenv import (loader is INVOKED inside main() and run_once(), NOT
# at module top)
# ---------------------------------------------------------------------------
# Why not at module top?
#   Module-top invocation would copy every .env value into os.environ on first
#   import, which pollutes the test session: tests/test_settings.py asserts
#   that constructing Settings() with no env returns the documented defaults,
#   and that assertion fails as soon as another test (e.g. test_run_once)
#   imports main and triggers the loader.
#
# Why call it inside both main() AND run_once()?
#   Production launchers (launch.command → `python main.py`) enter through
#   main(), so the loader fires there.  `make verify` and `verify.command`
#   import main and call `main.run_once()` directly without going through
#   main() — so run_once() must also call the loader as a defensive backstop.
#   load_dotenv() is idempotent and fast; the duplicate call is harmless.
#
# Why override=False?
#   So an explicit shell export ALWAYS wins over the .env file.
from dotenv import load_dotenv as _load_dotenv

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
import config
from data.market_data import get_provider, MarketDataProvider
from data.robinhood_portfolio import (
    AccountSnapshot,
    PortfolioPosition,
    fetch_account_snapshot,
)
from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
from engine.advisory import Recommendation, evaluate as advisory_evaluate
from settings import settings
from signals import global_registry
from signals.base import SignalContext

# ---------------------------------------------------------------------------
# Module-level logger (root logger is configured at runtime by setup_logging()
# inside main() — do NOT call logging.basicConfig() at module level here).
# ---------------------------------------------------------------------------
from alerting import notify, setup_logging, summarize_run
from pipeline.context import RunContext
from pipeline.runner import PipelineRunner
from pipeline.steps import (
    AccountStep,
    AdvisoryEvalStep,
    KillSwitchGateStep,
    MacroStep,
    PrecomputeStep,
    UniverseStep,
)
from reporting.sheets_client import (
    CREDENTIALS_FILE,
    SHEET_NAME,
    TAB_NAME_OUTPUT,
    get_service_account_client,
)
from reporting.sheet_publisher import write_recommendations as _write_to_sheet
from reporting.html_publisher import write_html_report as _write_html_report
from reporting.progress import ProgressReporter

logger = logging.getLogger("InvestYo.main")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WATCHLIST_FILE = "watchlist.txt"      # one ticker per line; '#' lines ignored


# ---------------------------------------------------------------------------
# Per-process MacroEngine reuse (Task A4)
# ---------------------------------------------------------------------------
# _build_macro_dto() is called once per run_once() cycle. In --interval / agent
# loop mode, run_once() is called repeatedly WITHIN THE SAME PROCESS. The
# regime/hmm_regime.py HMMRegimeDetector.fit() gate (retrain_freq_days) is only
# meaningful if the SAME detector instance persists across those cycles --
# constructing a fresh MacroEngine (and therefore a fresh, never-fitted
# HMMRegimeDetector) every cycle makes the gate a no-op and forces a full
# EM refit every single cycle. This module-level cache keeps one MacroEngine
# (and its DataEngine) alive for the lifetime of the process, keyed by the
# FRED API key so a mid-process key rotation still gets a correctly-scoped
# engine instead of silently reusing one built for a stale key.
_MACRO_ENGINE_CACHE: Dict[str, Any] = {}


def _get_macro_engine(fred_key: str):
    """Return a process-lifetime MacroEngine for ``fred_key``, constructing it
    once and reusing it on subsequent calls so the HMMRegimeDetector's
    retrain_freq_days gate is honored across --interval / agent-loop cycles.

    A distinct cache entry per fred_key means rotating the key mid-process
    (rare, but handled) gets a fresh engine/detector rather than silently
    reusing one fit against the old key's data.
    """
    cached = _MACRO_ENGINE_CACHE.get(fred_key)
    if cached is not None:
        return cached

    from data_engine import DataEngine
    from macro_engine import MacroEngine

    de = DataEngine(fred_key)
    me = MacroEngine(data_engine=de)
    _MACRO_ENGINE_CACHE.clear()  # only one key's engine needs to live at a time
    _MACRO_ENGINE_CACHE[fred_key] = me
    return me


def _reset_macro_engine_cache() -> None:
    """Test-only helper: clears the process-lifetime MacroEngine cache so each
    test gets a fresh engine/detector instead of bleeding HMM fit state across
    tests."""
    _MACRO_ENGINE_CACHE.clear()


# ---------------------------------------------------------------------------
# RunResult — immutable container for one full pipeline cycle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunResult:
    """Immutable result of one run_once() cycle.

    Attributes
    ----------
    snapshot : AccountSnapshot
        Robinhood account snapshot (may be stale; check snapshot.is_stale()).
    recommendations : list[Recommendation]
        Advisory results for every symbol that evaluated successfully.
    errors : list[dict]
        Dead-letter entries for each symbol that failed.  Each dict has keys:
        symbol, stage, error_type, message, timestamp (UTC ISO-8601).
    started_at : datetime  (UTC-aware)
    finished_at : datetime (UTC-aware)
    duration_seconds : float
    """

    snapshot: AccountSnapshot
    recommendations: List[Recommendation]
    errors: List[dict]
    started_at: datetime
    finished_at: datetime
    duration_seconds: float


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------

def _load_watchlist() -> List[str]:
    """Return uppercase tickers from WATCHLIST env var or watchlist.txt.

    WATCHLIST env var (comma-separated) takes precedence over the file.
    Returns an empty list when neither source is configured.
    """
    env_val = os.environ.get("WATCHLIST", "").strip()
    if env_val:
        return [t.strip().upper() for t in env_val.split(",") if t.strip()]

    wl_path = Path(WATCHLIST_FILE)
    if wl_path.exists():
        tickers = [
            line.strip().upper()
            for line in wl_path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        logger.info("Loaded %d tickers from %s.", len(tickers), WATCHLIST_FILE)
        return tickers

    return []


def _load_tickers_from_sheet2() -> List[str]:
    """Return tickers from Sheet2 column A of the Google Sheet.

    Used as a last-resort fallback when Robinhood is unavailable and no
    WATCHLIST / watchlist.txt is configured.  Silently returns [] when
    credentials.json is absent, Sheet2 doesn't exist, or any error occurs.
    """
    gc = get_service_account_client()
    if gc is None:
        return []
    try:
        sh = gc.open(SHEET_NAME)
        ws = sh.worksheet("Sheet2")
        col_a = ws.col_values(1)  # 1-indexed; returns list of strings
        tickers = [v.strip().upper() for v in col_a if v.strip() and not v.strip().startswith("#")]
        logger.info("Loaded %d tickers from Google Sheet Sheet2 column A.", len(tickers))
        return tickers
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read Sheet2 ticker list: %s", exc)
        return []


def _build_universe(snapshot: AccountSnapshot) -> List[str]:
    """Return the evaluation universe: held symbols ∪ watchlist, deduped, sorted.

    Priority order when building the universe:
      1. Robinhood held positions (always included when available).
      2. WATCHLIST env var or watchlist.txt (always merged in when present).
      3. Google Sheet → Sheet2 column A (fallback only when 1 + 2 are both empty).
    """
    held = set(snapshot.positions.keys())
    watchlist = set(_load_watchlist())
    combined = held | watchlist

    if not combined:
        sheet2 = set(_load_tickers_from_sheet2())
        if sheet2:
            logger.info(
                "Using %d tickers from Sheet2 (Robinhood unavailable, no WATCHLIST configured).",
                len(sheet2),
            )
        combined = sheet2

    universe = sorted(combined)
    logger.info(
        "Universe: %d symbols (%d held, %d watchlist-only).",
        len(universe),
        len(held),
        len(combined - held),
    )
    return universe


# ---------------------------------------------------------------------------
# Macro context (FRED + HMM second opinion)
# ---------------------------------------------------------------------------

def _build_macro_dto() -> MacroEconomicDTO:
    """Fetch FRED macro data and build MacroEconomicDTO with HMM probability.

    Degrades gracefully to neutral defaults when FRED_API_KEY is absent or
    FRED is unreachable.  Never raises.
    """
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    if not fred_key:
        logger.info("FRED_API_KEY not configured; using neutral macro defaults.")
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.50,
            high_yield_oas=3.50,
            inflation_rate=3.0,
            nominal_10y=4.5,
            vix_value=18.0,
            sahm_rule_indicator=0.0,
        )

    try:
        # Reuse ONE MacroEngine (and therefore one HMMRegimeDetector) across
        # every run_once() cycle within this process -- see _get_macro_engine()
        # docstring / Task A4. This makes the HMM's retrain_freq_days gate
        # meaningful in --interval / agent-loop mode instead of forcing a full
        # refit every single cycle.
        me = _get_macro_engine(fred_key)
        de = me.data_engine
        macro_raw = de.fetch_macro_raw()

        spy_df: Optional[pd.DataFrame] = None
        try:
            spy_raw = de.fetch_technical_raw(["SPY"])
            spy_df = spy_raw.get("SPY")
        except Exception as spy_exc:
            logger.debug("SPY history for HMM unavailable: %s", spy_exc)

        hmm_prob = me.compute_hmm_risk_on_probability(spy_df)

        dto = MacroEconomicDTO(
            yield_curve_10y_2y=float(macro_raw.get("T10Y2Y", 0.5)),
            high_yield_oas=float(macro_raw.get("BAMLH0A0HYM2", 3.5)),
            inflation_rate=float(macro_raw.get("CPIAUCSL_YoY", 2.0)),
            nominal_10y=float(macro_raw.get("DGS10", 4.0)),
            vix_value=float(macro_raw.get("VIXCLS", 18.0)),
            sahm_rule_indicator=float(macro_raw.get("SAHMREALTIME", 0.0)),
            hmm_risk_on_probability=hmm_prob,
        )
        logger.info(
            "Macro DTO built — regime=%s  VIX=%.1f  HMM=%.2f.",
            dto.market_regime,
            dto.vix,
            hmm_prob if hmm_prob is not None else float("nan"),
        )
        return dto

    except Exception as exc:
        logger.warning("Macro DTO construction failed (%s); using neutral defaults.", exc)
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.50,
            high_yield_oas=3.50,
            inflation_rate=3.0,
            nominal_10y=4.5,
            vix_value=18.0,
            sahm_rule_indicator=0.0,
        )


# ---------------------------------------------------------------------------
# Context pre-compute (cross-sectional ranks + multifactor composites)
# ---------------------------------------------------------------------------

def _fetch_bars_for_universe(
    symbols: List[str],
    market: MarketDataProvider,
) -> Dict[str, pd.DataFrame]:
    """Fetch ~450-day OHLCV history for all symbols via the market provider.

    The 12-1m cross-sectional momentum in ``_build_context_extras`` needs
    ``252 + 22 + 1 = 275`` *trading* days; fetching only 252 leaves every
    symbol below that floor, so the xsec rank pass silently yields nothing.
    Request 450 calendar days (~310 trading days) to clear the floor with
    headroom (this also maps yfinance to its "2y" period, avoiding a short
    "1y" pull that tops out near 252 rows).

    Returns a dict symbol → DataFrame.  Failures are dead-lettered per symbol
    so one bad ticker never aborts the pre-compute pass.
    """
    _store = None
    if settings.HISTORICAL_STORE_ENABLED:
        try:
            from data.historical_store import HistoricalStore
            _store = HistoricalStore()
        except Exception as exc:
            logger.warning("HistoricalStore unavailable; using direct provider. %s", exc)

    bars: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            if _store is not None:
                df = _store.get_bars(sym, lookback_days=450, provider=market)
            else:
                df = market.get_intraday_bars(sym, lookback_days=450)
            if df is not None and not df.empty:
                bars[sym] = df
        except Exception as exc:
            logger.debug("Bars pre-fetch skipped for %s: %s", sym, exc)
    logger.info("Pre-fetched bars for %d / %d symbols.", len(bars), len(symbols))
    return bars


def _build_context_extras(
    symbols: List[str],
    bars_dict: Dict[str, pd.DataFrame],
    macro_dto: MacroEconomicDTO,
) -> Dict[str, Any]:
    """Build universe-wide pre-computed signal context for injection into advisory.

    Computes 12-1m cross-sectional momentum ranks and Fama-French multifactor
    composites by running global_registry.run_pre_compute() on a minimal
    universe DataFrame.  The result is passed as context_extras to each
    advisory.evaluate() call so cross-sectional and multifactor signals score
    with real data instead of their neutral-0 fallback.

    Returns an empty dict (and logs a warning) if pre_compute raises.
    """
    SKIP_DAYS = 22       # 1-month skip for Jegadeesh-Titman momentum
    LOOKBACK_DAYS = 252  # 12-month lookback
    REQUIRED = LOOKBACK_DAYS + SKIP_DAYS + 1

    try:
        # ── Step 1: compute 12-1m cross-sectional returns ────────────────────
        xsec_return: Dict[str, float] = {}
        for sym, df in bars_dict.items():
            close = df["Close"].dropna()
            if len(close) < REQUIRED:
                continue
            p_recent = float(close.iloc[-(SKIP_DAYS + 1)])
            p_old = float(close.iloc[-(LOOKBACK_DAYS + 1)])
            if p_old > 0:
                xsec_return[sym] = p_recent / p_old - 1.0

        if xsec_return:
            ret_series = pd.Series(xsec_return)
            xsec_rank_series = ret_series.rank(pct=True, ascending=True)
        else:
            xsec_rank_series = pd.Series(dtype=float)

        # ── Step 2: build a minimal universe DataFrame for pre_compute ────────
        rows = []
        for sym in symbols:
            df = bars_dict.get(sym)
            price = float(df["Close"].iloc[-1]) if df is not None and not df.empty else 0.0
            rows.append({
                "Symbol": sym,
                "Price": price,
                "XSec_12_1M": xsec_return.get(sym, float("nan")),
                "XSec_Momentum_Rank": (
                    float(xsec_rank_series[sym])
                    if sym in xsec_rank_series.index
                    else float("nan")
                ),
            })
        universe_df = pd.DataFrame(rows)

        # ── Step 3: run global_registry.run_pre_compute() ────────────────────
        stub_bar = MarketBarDTO(
            date=datetime.now(),
            ticker="__UNIVERSE__",
            open_price=100.0,
            high_price=100.0,
            low_price=100.0,
            close_price=100.0,
            volume=0,
        )
        stub_fund = FundamentalDataDTO(
            ticker="__UNIVERSE__",
            pe_ratio=None,
            pb_ratio=None,
            dividend_yield=0.0,
            book_value=0.0,
            eps_trailing=0.0,
            dividend_growth_rate=0.0,
            payout_ratio=0.0,
            sector="Unknown",
            company_name="Universe stub",
        )
        shared_ctx = SignalContext(bar=stub_bar, fundamentals=stub_fund, macro=macro_dto)
        global_registry.run_pre_compute(universe_df, shared_ctx)

        logger.info(
            "Context pre-compute: %d xsec ranks, %d multifactor scores.",
            len(shared_ctx.xsec_percentile_ranks),
            len(shared_ctx.multifactor_scores),
        )
        return {
            "xsec_percentile_ranks": shared_ctx.xsec_percentile_ranks,
            "multifactor_scores": shared_ctx.multifactor_scores,
        }

    except Exception as exc:
        logger.warning(
            "Context pre-compute failed (%s); cross-sectional signals will score 0.", exc
        )
        return {}


# ---------------------------------------------------------------------------
# Run summary
# ---------------------------------------------------------------------------

def _log_summary(result: RunResult) -> None:
    """Emit a structured run summary to the module logger."""
    n_ok = len(result.recommendations)
    n_err = len(result.errors)
    buys  = sum(1 for r in result.recommendations if r.action == "BUY")
    holds = sum(1 for r in result.recommendations if r.action == "HOLD")
    sells = sum(1 for r in result.recommendations if r.action == "SELL")

    logger.info(
        "=== RUN SUMMARY ========================================\n"
        "  Duration   : %.2fs  (started %s)\n"
        "  Universe   : %d symbols — %d OK, %d errors\n"
        "  Signals    : BUY=%d  HOLD=%d  SELL=%d\n"
        "  Account    : age=%.2fh  equity=$%.0f  cash=$%.0f  stale=%s\n"
        "========================================================",
        result.duration_seconds,
        result.started_at.strftime("%H:%M:%S UTC"),
        n_ok + n_err, n_ok, n_err,
        buys, holds, sells,
        result.snapshot.age_hours(),
        result.snapshot.total_equity,
        result.snapshot.buying_power,
        result.snapshot.is_stale(max_age_hours=20.0),
    )
    for err in result.errors:
        logger.warning(
            "  DEAD-LETTER  %-8s  stage=%-22s  %s: %s",
            err["symbol"], err["stage"],
            err["error_type"], err["message"][:80],
        )


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def run_once(force_account: bool = False) -> RunResult:
    """Execute one full pipeline cycle and return an immutable RunResult.

    Parameters
    ----------
    force_account : bool
        ``True`` → bypass the daily Robinhood cache and force a live TOTP
        login.  ``False`` (default) → use the cached snapshot when it is
        younger than 20 hours; only re-fetch when the cache has expired.

    Returns
    -------
    RunResult
        Always returned.  Never raises.  Per-symbol failures are collected in
        ``RunResult.errors`` rather than being propagated.

    .env loading contract
    ---------------------
    This function does NOT call load_dotenv() itself — doing so would pollute
    the pytest session (test_run_once.py invokes run_once() many times and
    each call would copy every .env value into os.environ, breaking
    test_settings_defaults).  The caller is responsible for ensuring
    os.environ contains the secrets that downstream modules read via
    os.environ.get(), notably data/robinhood_portfolio.py.

    Standard call sites:
      • main() invokes _load_dotenv() before run_once() — production launch.
      • Makefile target `verify` invokes load_dotenv() in its python -c block
        before calling main.run_once().
      • verify.command invokes load_dotenv() in its python heredoc before
        calling main.run_once().
      • Tests use mock.patch on fetch_account_snapshot etc., so they don't
        need real env vars.
    """
    started_at = datetime.now(timezone.utc)

    ctx = RunContext(
        force_account=force_account,
        started_at=started_at,
        watchlist_file=WATCHLIST_FILE,
        fetch_account_snapshot_fn=fetch_account_snapshot,
        build_universe_fn=_build_universe,
        build_macro_dto_fn=_build_macro_dto,
        get_provider_fn=get_provider,
        fetch_bars_fn=_fetch_bars_for_universe,
        build_context_extras_fn=_build_context_extras,
        advisory_evaluate_fn=advisory_evaluate,
    )
    steps = [
        AccountStep(),
        UniverseStep(),
        KillSwitchGateStep(),
        MacroStep(),
        PrecomputeStep(),
        AdvisoryEvalStep(),
    ]
    # Progress instrumentation (reporting/progress.py): one ProgressReporter per
    # cycle, stages = the step names in the SAME order as `steps` above (so
    # PipelineRunner.run()'s start_stage(step.name, ...) calls always match a
    # real stage in the list). AdvisoryEvalStep is the only step with a
    # per-symbol loop (see pipeline/steps.py); every other step's stage slice
    # just holds at its starting boundary -- see PipelineRunner.run()'s own
    # docstring for the full contract. finish() is guaranteed via try/finally
    # so a raised exception from an unguarded step (UniverseStep/MacroStep/
    # PrecomputeStep -- see pipeline/runner.py's module docstring) still marks
    # the cycle "failed" before propagating unchanged (CONSTRAINT #6: progress
    # reporting must never swallow an error or change pipeline behavior).
    progress = ProgressReporter([s.name for s in steps])
    try:
        PipelineRunner(steps).run(ctx, progress=progress)
    except Exception:
        progress.finish("failed")
        raise
    else:
        progress.finish("succeeded")

    finished_at = datetime.now(timezone.utc)
    result = RunResult(
        snapshot=ctx.snapshot,
        recommendations=ctx.recommendations,
        errors=ctx.errors,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=(finished_at - started_at).total_seconds(),
    )
    if not ctx.stopped:
        _log_summary(result)
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _read_macro_snapshot_hint() -> Dict[str, Any]:
    """Return ``{"vix": float|None, "market_regime": str|None}`` from the last
    ``output/state_snapshot.json`` write — or empty values when missing.

    Used by ``_run_agent_loop`` to inform the adaptive cadence policy without
    re-running ``_build_macro_dto`` (which would re-hit FRED).  Returns a
    neutral hint dict (both keys ``None``) on any error so the policy falls
    back to its default time-of-day cadence.
    """
    try:
        import json
        snap_path = settings.OUTPUT_DIR / "state_snapshot.json"
        if not snap_path.exists():
            return {"vix": None, "market_regime": None}
        payload = json.loads(snap_path.read_text(encoding="utf-8"))
        vix_raw = payload.get("vix")
        regime_raw = payload.get("market_regime")
        return {
            "vix": float(vix_raw) if vix_raw not in (None, 0, 0.0) else None,
            "market_regime": str(regime_raw) if regime_raw else None,
        }
    except Exception as exc:
        logger.debug("Could not read macro hint from state_snapshot.json: %s", exc)
        return {"vix": None, "market_regime": None}


def _run_agent_loop(run_cycle) -> None:
    """Autonomous advisory loop driver.

    Replaces ``--interval N``'s fixed timer with the policy in
    ``engine.advisory_agent``:
      * adaptive cadence (RTH-aware, VIX/regime-adaptive, error back-off)
      * actionable-backlog reminders for high-conviction signals the operator
        has not yet logged a decision for
      * persistent state at ``OUTPUT_DIR/agent_state.json`` so the backlog
        survives restarts

    The ``run_cycle`` callable is the same closure used by ``--interval`` mode;
    it must return the ``RunResult`` of one full cycle.  All ntfy push behavior
    inside ``run_cycle`` is preserved (errors → high-priority push, clean run →
    one default push per launch, watch_engine alerts per cycle).

    SIGINT / SIGTERM are caught and the loop exits cleanly after the current
    cycle plus any post-cycle reminder dispatch — never mid-sleep.
    """
    # Lazy import keeps test imports of main.py cheap.
    from engine.advisory_agent import (  # noqa: PLC0415
        apply_reminder_dispatch,
        compute_backlog_reminders,
        compute_next_run_delay,
        dispatch_backlog_reminders,
        load_agent_state,
        process_run_result,
        save_agent_state,
        update_backlog,
    )

    state_path = settings.OUTPUT_DIR / "agent_state.json"
    state = load_agent_state(state_path)
    logger.info(
        "Agent mode starting — state path=%s loaded backlog=%d cycle_count=%d",
        state_path, len(state.backlog), state.cycle_count,
    )

    _shutdown = False

    def _handle_signal(signum: int, frame: Any) -> None:
        nonlocal _shutdown
        logger.info("Shutdown signal received; finishing current cycle then exiting.")
        _shutdown = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    dashboard_url = os.environ.get("NTFY_DASHBOARD_URL")

    while not _shutdown:
        # ── (1) Run one full advisory cycle (sheet + html + watch_engine) ────
        cycle_started_at = datetime.now(timezone.utc)
        try:
            result = run_cycle()
        except Exception as exc:
            logger.exception("Agent cycle failed unexpectedly: %s", exc)
            result = None

        # ── (2) Update agent state with cycle outcome ────────────────────────
        if result is not None:
            try:
                process_run_result(state, result, datetime.now(timezone.utc))
            except Exception as exc:
                logger.warning("process_run_result failed (%s); skipping", exc)

            # ── (3) Refresh backlog from latest recommendations + decision log
            decision_entries: List[Any] = []
            try:
                from gui.decision_log import read_decisions  # noqa: PLC0415
                decision_entries = read_decisions()
            except Exception as exc:
                logger.debug("decision_log read failed (%s); backlog clear step skipped", exc)
            try:
                update_backlog(
                    state, result.recommendations, decision_entries,
                    datetime.now(timezone.utc),
                )
            except Exception as exc:
                logger.warning("update_backlog failed (%s); skipping", exc)

            # ── (4) Compute and dispatch backlog reminders ───────────────────
            try:
                reminders = compute_backlog_reminders(state, datetime.now(timezone.utc))
                if reminders:
                    dispatch_backlog_reminders(reminders, dashboard_url=dashboard_url)
                    apply_reminder_dispatch(state, reminders, datetime.now(timezone.utc))
                    logger.info(
                        "Agent dispatched %d backlog reminder(s).", len(reminders),
                    )
            except Exception as exc:
                logger.warning("Backlog reminder dispatch failed (%s); skipping", exc)

            # ── (4b) Trade-signal abilities (conviction momentum + price triggers)
            try:
                from engine.trade_signals import (  # noqa: PLC0415
                    detect_conviction_momentum,
                    detect_price_triggers,
                    dispatch_trade_alerts,
                    update_conviction_history,
                )
                recs = result.recommendations
                # Ability A — conviction momentum (cross-cycle trajectory).
                state.conviction_history = update_conviction_history(
                    state.conviction_history, recs,
                )
                mom_alerts, state.momentum_alerted = detect_conviction_momentum(
                    state.conviction_history, recs, state.momentum_alerted,
                )
                # Ability B — stop / take-profit proximity for held positions.
                price_alerts, state.price_trigger_alerted = detect_price_triggers(
                    result.snapshot, recs, state.price_trigger_alerted,
                )
                trade_alerts = mom_alerts + price_alerts
                if trade_alerts:
                    dispatch_trade_alerts(trade_alerts, dashboard_url=dashboard_url)
                    logger.info(
                        "Agent dispatched %d trade alert(s) — momentum=%d price=%d.",
                        len(trade_alerts), len(mom_alerts), len(price_alerts),
                    )
            except Exception as exc:
                logger.warning("Trade-signal abilities failed (%s); skipping", exc)

        # ── (5) Persist state regardless of cycle outcome ─────────────────────
        try:
            save_agent_state(state, state_path)
        except Exception as exc:
            logger.warning("save_agent_state failed (%s); skipping", exc)

        if _shutdown:
            break

        # ── (6) Compute adaptive sleep duration ──────────────────────────────
        macro_hint = _read_macro_snapshot_hint()
        delay_s = compute_next_run_delay(
            datetime.now(timezone.utc),
            state=state,
            vix=macro_hint.get("vix"),
            market_regime=macro_hint.get("market_regime"),
        )
        logger.info(
            "Agent sleeping %ds (cycle #%d, backlog=%d, vix=%s, regime=%s, err_streak=%d).",
            delay_s, state.cycle_count, len(state.backlog),
            macro_hint.get("vix"), macro_hint.get("market_regime"),
            state.consecutive_error_cycles,
        )

        # Sleep in 1-second slices so SIGINT/SIGTERM are caught promptly.
        for _ in range(int(delay_s)):
            if _shutdown:
                break
            time.sleep(1)

    logger.info("Agent loop exited cleanly.")


def main() -> None:
    """CLI entry point with two-tier refresh support.

    Flags
    -----
    (no flags)
        Run once, write Sheet, generate HTML report, exit 0.  Even if some
        symbols errored (their entries appear in RunResult.errors and as
        ERROR rows in the Sheet).

    --interval N
        Loop: refresh market data every N seconds.  The account tier still
        fetches Robinhood at most once per day — an all-day run logs in once.
        Ctrl-C or SIGTERM exits cleanly after the current cycle completes.

    --agent
        Run the autonomous advisory agent loop.  Replaces ``--interval``'s
        fixed timer with the adaptive cadence + backlog reminder policy in
        ``engine/advisory_agent.py``.  Cadence is RTH/VIX/regime-aware;
        high-conviction signals the operator hasn't logged a decision for
        are re-pinged at 1h / 4h / 24h escalation tiers.  Persistent state
        at ``OUTPUT_DIR/agent_state.json`` survives restarts.  Takes
        precedence over ``--interval``.

    --refresh-account
        Force a fresh Robinhood login on this launch, bypassing the daily
        cache.  For subsequent iterations in --interval mode, normal caching
        resumes (so re-auth happens at most once per launch).
    """
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="InvestYo Quant Platform — advisory pipeline launcher",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Refresh market data every N seconds (0 = run once and exit).",
    )
    parser.add_argument(
        "--refresh-account",
        action="store_true",
        default=False,
        help="Force a fresh Robinhood fetch on this launch (bypasses daily cache).",
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        default=False,
        help=(
            "Run the autonomous advisory agent loop: adaptive cadence based on "
            "market hours / VIX / regime, backlog reminders for high-conviction "
            "signals the operator has not yet logged a decision for, persistent "
            "state across restarts.  Takes precedence over --interval."
        ),
    )
    args = parser.parse_args()

    # Load .env into os.environ before any runtime os.environ.get() call.
    # This is the primary load point when launched as `python main.py`; the
    # call inside run_once() is the defensive backstop for direct imports.
    _load_dotenv(override=False)
    setup_logging()   # configure root logger (file + console, rotating, structured)
    logger.info("InvestYo Quant Platform starting.")
    settings.warn_if_fred_key_leaked(logger)

    # Force-account flag applies only to the FIRST cycle; subsequent iterations
    # use the daily cache regardless.
    _force_next = args.refresh_account
    # Tracks whether a "clean run" push notification has been sent this launch.
    # At most one per launch in --interval mode to avoid notification spam.
    _clean_notified = False

    def _run_cycle() -> RunResult:
        nonlocal _force_next, _clean_notified
        result = run_once(force_account=_force_next)
        _force_next = False  # one-shot; cache resumes from here

        # ── Alerting: compact summary → log + optional push notification ──────
        summary = summarize_run(result)
        logger.info("\n%s", summary)

        if result.errors:
            # High-priority push: list failing symbols and stages.
            err_preview = ", ".join(
                f"{e.get('symbol', '?')} ({e.get('stage', '?')})"
                for e in result.errors[:3]
            )
            suffix = (
                f" +{len(result.errors) - 3} more"
                if len(result.errors) > 3
                else ""
            )
            notify(
                title="InvestYo ⚠ Errors Detected",
                message=(
                    f"{len(result.errors)} symbol(s) failed: "
                    f"{err_preview}{suffix}\n"
                    f"OK={len(result.recommendations)}  "
                    f"Duration={result.duration_seconds:.1f}s"
                ),
                priority="high",
            )
        elif not _clean_notified:
            # One normal-priority notification per launch on a fully clean run.
            notify(
                title="InvestYo ✓ Refresh Complete",
                message=summary,
                priority="default",
            )
            _clean_notified = True
        # ─────────────────────────────────────────────────────────────────────

        # ── Symbol watch alerts (Tier 1.4) — non-fatal ───────────────────────────
        # Evaluated immediately after run_once() so alerts reflect the freshest
        # signal output.  State is loaded from the PREVIOUS run, compared
        # against the CURRENT recommendations, and saved AFTER dispatch.
        # This is the shift-adjusted, no-lookahead contract: no market data
        # is re-fetched inside watch_engine; it only compares already-computed
        # advisory outputs.
        try:
            from watch_engine import (
                dispatch_watch_alerts,
                evaluate_watch_rules,
                load_watch_rules,
                load_watch_state,
                save_watch_state,
            )

            _watch_rules = load_watch_rules(settings.WATCH_RULES_FILE)
            _watch_state_path = settings.OUTPUT_DIR / "watch_state.json"
            _prev_watch_state = load_watch_state(_watch_state_path)
            _watch_alerts, _new_watch_state = evaluate_watch_rules(
                _watch_rules,
                result.recommendations,
                _prev_watch_state,
            )
            dispatch_watch_alerts(
                _watch_alerts,
                dashboard_url=os.environ.get("NTFY_DASHBOARD_URL"),
            )
            # Always save — keeps edge-trigger state current even on quiet runs.
            save_watch_state(_new_watch_state, _watch_state_path)
            if _watch_alerts:
                logger.info(
                    "Symbol watch: %d alert(s) dispatched across %d rule(s).",
                    len(_watch_alerts),
                    len(_watch_rules),
                )
        except Exception as _watch_exc:
            logger.warning(
                "Symbol watch alert evaluation failed (non-critical): %s",
                _watch_exc,
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── Robinhood execution queue (Tier 8) — non-fatal, advisory-only ─────
        # Emits a GATED, DRY-RUN proposed-order queue to
        # output/execution_queue.json for the Claude Code "robinhood-execution"
        # agent to consume.  This NEVER contacts a broker or places an order —
        # the headless pipeline cannot call the Robinhood MCP.  When
        # ROBINHOOD_EXECUTION_MODE=off (the default) nothing is written and this
        # block is a no-op.  The kill-switch advisory-pause gate above already
        # short-circuits run_once(), so a paused cycle emits nothing.
        try:
            from execution.queue_builder import emit_execution_queue  # noqa: PLC0415

            _queue_path = emit_execution_queue(result)
            if _queue_path is not None:
                logger.info("Robinhood execution queue emitted → %s", _queue_path)
        except Exception as _queue_exc:
            logger.warning(
                "Execution queue emit failed (non-critical): %s", _queue_exc,
            )
        # ─────────────────────────────────────────────────────────────────────

        # ── Robinhood OPTIONS execution queue (Tier 8) — non-fatal, advisory ──
        # Sibling of the equity queue above, for multi-leg premium-selling
        # directives.  Emits a GATED, DRY-RUN queue to
        # output/options_execution_queue.json for the Robinhood execution agent.
        # Same off-mode no-op + best-effort try/except contract as the equity
        # path; NEVER contacts a broker or places an order.
        try:
            from execution.options_queue_builder import (  # noqa: PLC0415
                emit_options_execution_queue,
            )

            _opt_queue_path = emit_options_execution_queue(result)
            if _opt_queue_path is not None:
                logger.info(
                    "Robinhood options execution queue emitted → %s", _opt_queue_path,
                )
        except Exception as _opt_queue_exc:
            logger.warning(
                "Options execution queue emit failed (non-critical): %s",
                _opt_queue_exc,
            )
        # ─────────────────────────────────────────────────────────────────────

        market = get_provider()
        _write_to_sheet(result, market=market)

        # Build macro_dto again cheaply (same result, neutral defaults are fast)
        # to pass macro context to the HTML report template.
        try:
            from dto_models import MacroEconomicDTO as _MDTO

            _macro = _MDTO(
                yield_curve_10y_2y=0.5,
                high_yield_oas=3.5,
                inflation_rate=3.0,
                nominal_10y=4.5,
                vix_value=18.0,
                sahm_rule_indicator=0.0,
            )
        except Exception:
            _macro = None

        _write_html_report(result, macro_dto=_macro)
        return result

    if args.agent:
        _run_agent_loop(run_cycle=_run_cycle)
    elif args.interval > 0:
        _shutdown = False

        def _handle_signal(signum: int, frame: Any) -> None:
            nonlocal _shutdown
            logger.info("Shutdown signal received; finishing current cycle then exiting.")
            _shutdown = True

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        logger.info("Interval mode: market data refreshes every %ds.", args.interval)
        while not _shutdown:
            _run_cycle()
            if _shutdown:
                break
            logger.info(
                "Sleeping %ds until next market-data refresh...", args.interval
            )
            # Sleep in 1-second increments to catch shutdown signals promptly.
            for _ in range(args.interval):
                if _shutdown:
                    break
                time.sleep(1)

        logger.info("Exiting interval loop cleanly.")

    else:
        # Single-run mode
        _run_cycle()

    logger.info("InvestYo Quant Platform finished.")
    sys.exit(0)


if __name__ == "__main__":
    main()

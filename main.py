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
  D. Context pre-compute— 12-1m cross-sectional momentum ranks + Fama-French
                          multifactor (value/quality/low-vol/size) raw inputs,
                          built once for the full universe before the
                          per-symbol loop so advisory signals see real
                          cross-sectional data rather than the 0-score fallback
  E. Per-symbol evaluate— market data + advisory engine; dead-letter error
                          capture per symbol; never aborts the run
  F. HTML report        — generate daily HTML report (skipped on IO error)
  G. Run summary        — structured log line; return RunResult

Two-tier refresh cadence
------------------------
  Account tier  : Robinhood snapshot fetched at most once per day via a daily
                  JSON cache at cache/account_snapshot.json.  Use --refresh-account
                  to force a fresh login on this launch; subsequent iterations of
                  --interval mode then resume normal caching.
  Market tier   : prices, bars, indicators, forecasts refreshed on every call to
                  run_once() from the live market-data provider.

NOTE — Double-fetch for pre-compute
  advisory_inputs.fetch_bars_for_universe() fetches OHLCV once for the whole
  universe for the cross-sectional pre-compute. engine.advisory.evaluate()
  will then fetch bars again per symbol internally (the market provider does not
  cache bars, only quotes).  This is a known tradeoff accepted for correctness:
  pre-compute requires the full universe before the per-symbol loop, so bars
  must be fetched upfront.  Future optimisation: extend MarketDataProvider with
  a bars cache or pass bars through to advisory via context_extras.

  fetch_fundamentals_for_universe() has the same shape (fetched once upfront
  for the multifactor pre-compute pass, then evaluate() fetches fundamentals
  again per symbol in its own Step 3) but with a much smaller real cost: when
  HISTORICAL_STORE_ENABLED (the default), the pre-compute pass's fetch writes
  each symbol's row to HistoricalStore's fundamentals_history table, and
  evaluate()'s Step 3 re-read lands inside that row's FUNDAMENTALS_REFRESH_DAYS
  freshness window (default 1 day) — a DB cache hit, not a second network call.
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
if (
    "pytest" not in sys.modules
    and "unittest" not in sys.modules
    and not os.environ.get("PYTEST_CURRENT_TEST")
    and os.path.exists(_venv_python)
    and os.path.realpath(sys.executable) != os.path.realpath(_venv_python)
):
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

# ---------------------------------------------------------------------------
# TensorFlow, if installed, MUST be imported before pandas/pyarrow -- defense
# in depth for the CNN-LSTM/TensorFlow deadlock (issue #381, docs/known_issues/
# cnn_lstm_tf_deadlock.md). forecasting_engine.py's own import reorder (PR
# #387) only protects a process where IT is the first thing to touch pandas;
# main.py imports pandas below, well before forecasting_engine is ever
# reached, so without this guard the real entry point stays exposed. A no-op
# when TensorFlow isn't installed (ImportError -> nothing changes). The
# primary fix is CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED (settings.py), which
# runs CNN-LSTM fit/predict in an isolated subprocess and doesn't depend on
# any entry point's import order at all; this import is a cheap second layer
# for the case isolation is left off.
# ---------------------------------------------------------------------------
try:
    import tensorflow  # noqa: F401
except ImportError:
    pass

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
import config
from data.market_data import get_provider
from data.robinhood_portfolio import (
    AccountSnapshot,
    PortfolioPosition,
    fetch_account_snapshot,
)
from dto_models import MacroEconomicDTO
from engine.advisory import Recommendation, evaluate as advisory_evaluate
from settings import ENV_PATH, settings

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
from reporting.html_publisher import write_html_report as _write_html_report
from reporting.progress import ProgressReporter

logger = logging.getLogger("InvestYo.main")

# ---------------------------------------------------------------------------
# Advisory input builders (re-exported from pipeline/advisory_inputs.py)
# ---------------------------------------------------------------------------
# The advisory input builders (universe, macro DTO, bars/fundamentals
# pre-fetch, cross-sectional/multifactor pre-compute) live in
# pipeline/advisory_inputs.py since step 5.0, so the orchestrator daemon can
# import them without importing main. They are re-exported here under their old
# underscore names: run_once() binds the injected ones from THIS module's
# globals at call time, so patch("main._build_universe") etc. keep working. A
# call made inside pipeline/advisory_inputs.py (e.g. build_universe ->
# discovery) is patched at pipeline.advisory_inputs.<name> instead.
from pipeline.advisory_inputs import (  # noqa: E402,F401  (re-exports)
    WATCHLIST_FILE,
    _MACRO_ENGINE_CACHE,
    build_context_extras as _build_context_extras,
    build_macro_dto as _build_macro_dto,
    build_realized_vol_60d_map as _build_realized_vol_60d_map,
    build_universe as _build_universe,
    fetch_bars_for_universe as _fetch_bars_for_universe,
    fetch_fundamentals_for_universe as _fetch_fundamentals_for_universe,
    get_macro_engine as _get_macro_engine,
    load_watchlist as _load_watchlist,
    recently_closed_universe_symbols as _recently_closed_universe_symbols,
    reset_macro_engine_cache as _reset_macro_engine_cache,
)


def _interval_cycle_gated(now_utc: datetime) -> bool:
    """True when an automatic interval cycle should be skipped (market-hours gate)."""
    from engine.advisory_agent import is_automatic_run_gated
    return is_automatic_run_gated(now_utc, extended_hours_only=settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY)


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
    macro_dto : MacroEconomicDTO | None
        The cycle's real macro context (built once by MacroStep), so callers
        after run_once() returns (e.g. the automated options executor) can
        gate on real VIX/regime/HY-OAS state instead of a default None.
    """

    snapshot: AccountSnapshot
    recommendations: List[Recommendation]
    errors: List[dict]
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    macro_dto: Optional[MacroEconomicDTO] = None


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
    test_settings_defaults).  Most downstream modules (including
    data/robinhood_portfolio.py, since the 2026-08 .env-resolution fix) read
    credentials via ``settings.settings.X``, which loads ``.env`` independently
    through pydantic-settings' own ``env_file=ENV_PATH`` and needs no
    load_dotenv() call at all.  A handful of call sites still read raw
    ``os.environ.get(...)`` directly for values with no ``settings.py`` field;
    for those, the caller remains responsible for ensuring
    ``_load_dotenv(ENV_PATH, ...)`` ran first.

    Standard call sites:
      • main() invokes _load_dotenv(ENV_PATH) before run_once() — production launch.
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
        macro_dto=ctx.macro_dto,
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

    dashboard_url = settings.NTFY_DASHBOARD_URL

    while not _shutdown:
        # ── (1) Run one full advisory cycle (sheet + html + watch_engine) ────
        cycle_started_at = datetime.now(timezone.utc)
        if _interval_cycle_gated(cycle_started_at):
            # Same market-hours gate as --interval mode (settings.ORCHESTRATOR_
            # EXTENDED_HOURS_ONLY). compute_next_run_delay()'s own off-hours/
            # extended-hours cadence below still governs how long we sleep
            # before checking again -- this only skips actually RUNNING the
            # cycle outside the window, composing cleanly with the existing
            # adaptive-delay policy rather than replacing it.
            logger.debug(
                "Market-hours gate: skipping agent cycle (outside 4am-8pm ET weekday window)."
            )
            result = None
        else:
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
                from shared.decision_log import read_decisions  # noqa: PLC0415
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
    # Anchored to ENV_PATH (settings.py) rather than a bare load_dotenv() —
    # bare load_dotenv() uses find_dotenv(), which walks UP from this file's
    # directory and, in a git worktree with no .env of its own, silently
    # finds a PARENT checkout's .env instead. See settings.ENV_PATH's
    # docstring comment for the full three-locators writeup.
    _load_dotenv(ENV_PATH, override=False)
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

        # Step 5.3: with DAEMON_AGENTIC_QUEUE_MODE=primary the orchestrator
        # daemon (AgenticQueueStep) writes the real execution queue and sends
        # the summary push and watch alerts. Skip ours so there are never two
        # writers of execution_queue.json / watch_state.json and no double
        # pushes. daily_report.html is retired (step 5 decision 5), and our
        # advisory state_snapshot.json write would fight the daemon's.
        from pipeline.agentic_queue import daemon_owns_agentic_side_effects  # noqa: PLC0415

        if daemon_owns_agentic_side_effects(getattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "off")):
            logger.warning(
                "DAEMON_AGENTIC_QUEUE_MODE=primary: the orchestrator daemon owns the execution "
                "queue, watch alerts and summary push. main.py computed %d recommendation(s) "
                "but writes no queue, watch state, push, daily_report.html or state snapshot. "
                "Trigger a daemon cycle (POST /run) to refresh the queue.",
                len(result.recommendations),
            )
            return result

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
                dashboard_url=settings.NTFY_DASHBOARD_URL,
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
        #
        # Routes through execution.compose (the cross-Pilot + advisory queue
        # composer) rather than calling emit_execution_queue directly: this
        # advisory cycle writes its OWN source file (queue_sources/advisory.json)
        # and then composes it together with every actively-followed Pilot's
        # own source file into ONE queue, instead of silently overwriting
        # whatever a follow may have written (or vice versa) — the two writers
        # already shared this one file. In the default advisory-only posture
        # (no active follows) this is a no-op change: the composed output for
        # every symbol is byte-identical to advisory alone.
        try:
            from execution.compose import compose_and_emit, write_advisory_source  # noqa: PLC0415

            write_advisory_source(result.recommendations)
            _queue_path = compose_and_emit(result.snapshot, macro_dto=result.macro_dto)
            if _queue_path is not None:
                logger.info("Robinhood execution queue emitted → %s", _queue_path)
        except Exception as _queue_exc:
            logger.warning(
                "Execution queue emit failed (non-critical): %s", _queue_exc,
            )
        # ─────────────────────────────────────────────────────────────────────

        # Pass the cycle's real macro context (built once by MacroStep). This
        # used to be a hand-built "neutral" DTO, which wrote a fake RISK ON /
        # VIX-from-defaults macro into daily_report.html and state_snapshot.json
        # every run, overwriting the daemon's real macro fields.
        _write_html_report(result, macro_dto=result.macro_dto)
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
            if _interval_cycle_gated(datetime.now(timezone.utc)):
                logger.debug("Market-hours gate: skipping interval cycle (outside 4am-8pm ET weekday window).")
            else:
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

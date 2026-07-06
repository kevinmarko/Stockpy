"""Concrete PipelineStep implementations for main.py's run_once() cycle.

Each class below is a line-for-line port of one stage of the original
run_once() function body (see main.py's docstring for the A-H stage list).
Exception-handling boundaries are preserved EXACTLY as they were in the
original: AccountStep and AdvisoryEvalStep each have their own internal
try/except (as the original did); UniverseStep/MacroStep/PrecomputeStep make
unguarded calls (as the original did) — if those raise, the exception
propagates out of run_once() uncaught, exactly as before. Do not add new
error handling to any step; that would be a behavior change.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from data.robinhood_portfolio import AccountSnapshot
from execution.kill_switch import GlobalKillSwitch
from reporting.sheets_client import SHEET_NAME
from settings import settings

from pipeline.base import PipelineStep
from pipeline.context import RunContext

logger = logging.getLogger("InvestYo.main")


class AccountStep(PipelineStep):
    """Ports Stage A (lines 577-606 of main.py's run_once()): Robinhood account
    snapshot fetch with a daily cache, degrading to an empty AccountSnapshot on
    failure."""

    name = "account"

    def run(self, ctx: RunContext) -> None:
        try:
            snapshot = ctx.fetch_account_snapshot_fn(max_age_hours=20.0, force=ctx.force_account)
            age_h = snapshot.age_hours()
            if ctx.force_account:
                cache_msg = "force-refreshed"
            elif age_h < 1.0:
                cache_msg = "served from cache (fresh)"
            else:
                cache_msg = f"served from cache (age={age_h:.1f}h)"
            logger.info(
                "Account snapshot %s — equity=$%.0f  positions=%d.",
                cache_msg,
                snapshot.total_equity,
                len(snapshot.positions),
            )
        except Exception as rh_exc:
            logger.warning(
                "Robinhood snapshot unavailable (%s); proceeding with empty account. "
                "Watchlist universe will still be evaluated.",
                rh_exc,
            )
            snapshot = AccountSnapshot(
                positions={},
                buying_power=0.0,
                total_equity=0.0,
                total_dividends=0.0,
                fetched_at=datetime.now(timezone.utc),
            )
        ctx.snapshot = snapshot


class UniverseStep(PipelineStep):
    """Ports Stage B (lines 608-632 of main.py's run_once()): builds the symbol
    universe and stops the pipeline (empty_universe) when it comes back empty."""

    name = "universe"

    def run(self, ctx: RunContext) -> None:
        ctx.symbols = ctx.build_universe_fn(ctx.snapshot)
        if not ctx.symbols:
            # Held positions are empty AND WATCHLIST is unset AND watchlist.txt is
            # absent / empty.  Spell out every possible fix so the user can act
            # without spelunking through the source.
            logger.warning(
                "Empty symbol universe — nothing to evaluate. "
                "Fix one of: (1) set RH_USERNAME / RH_PASSWORD / RH_MFA_SECRET (optional) in "
                ".env so Robinhood positions populate the universe, (2) set the "
                "WATCHLIST env var (e.g. WATCHLIST=SPY,QQQ,AAPL,MSFT), (3) "
                "create %s with one ticker per line, or (4) add tickers to "
                "Sheet2 column A in the '%s' Google Sheet (requires credentials.json).",
                ctx.watchlist_file,
                SHEET_NAME,
            )
            ctx.stopped = True
            ctx.stop_reason = "empty_universe"


class KillSwitchGateStep(PipelineStep):
    """Ports the kill-switch advisory pause gate (lines 634-664 of main.py's
    run_once()), checked after Stage B (universe known for telemetry) but before
    Stage C (macro) and the per-symbol pipeline."""

    name = "kill_switch_gate"

    def run(self, ctx: RunContext) -> None:
        ks = GlobalKillSwitch()
        if ks.is_active():
            ks_reason = ks.reason() or "(no reason recorded)"
            logger.info(
                "Advisory paused by kill-switch sentinel — skipping evaluation cycle. "
                "Reason: %s  |  Universe would have been: %s  |  "
                "Deactivate with: python -m execution.kill_switch --deactivate",
                ks_reason,
                ", ".join(ctx.symbols[:10]) + ("..." if len(ctx.symbols) > 10 else ""),
            )
            ctx.errors.append({
                "symbol": "_advisory",
                "stage": "kill_switch_gate",
                "error_type": "AdvisoryPaused",
                "message": f"Kill-switch sentinel active: {ks_reason}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            ctx.stopped = True
            ctx.stop_reason = "kill_switch"


class MacroStep(PipelineStep):
    """Ports Stage C (lines 666-678 of main.py's run_once()): macro context
    build plus the once-per-run meta-labeler runtime registration bootstrap."""

    name = "macro"

    def run(self, ctx: RunContext) -> None:
        ctx.macro_dto = ctx.build_macro_dto_fn()

        # ── Meta-labeler runtime registration (once per run, before signals) ──────
        # Load any trained meta-labeler pickles into global_meta_registry so the
        # SignalAggregator's meta_hard_gate can fire. Strict no-op (logged) when no
        # saved model exists — preserves the exact pre-model behavior. Lazy import
        # mirrors HistoricalStore's lazy-import pattern; dead-letter resilient.
        try:
            from ml.meta_bootstrap import bootstrap_meta_registry
            bootstrap_meta_registry()
        except Exception as meta_exc:  # never let meta-label wiring crash the run
            logger.warning("Meta-labeler bootstrap failed (%s); continuing.", meta_exc)


class PrecomputeStep(PipelineStep):
    """Ports Stage D (lines 680-683 of main.py's run_once()): universe-wide
    context pre-compute (market provider, bars, cross-sectional/multifactor
    extras) run once before the per-symbol loop."""

    name = "precompute"

    def run(self, ctx: RunContext) -> None:
        ctx.market = ctx.get_provider_fn()
        ctx.bars_dict = ctx.fetch_bars_fn(ctx.symbols, ctx.market)
        ctx.context_extras = ctx.build_context_extras_fn(ctx.symbols, ctx.bars_dict, ctx.macro_dto)


class AdvisoryEvalStep(PipelineStep):
    """Ports Stage E (lines 685-751 of main.py's run_once()): the parallel,
    dead-letter-resilient per-symbol advisory evaluation loop."""

    name = "advisory_eval"

    def run(self, ctx: RunContext) -> None:
        # Each evaluate() call is independent (engine.advisory constructs its engines
        # per call; the shared inputs — snapshot, market, macro_dto, context_extras —
        # are read-only during the loop), so the loop parallelizes across a bounded
        # thread pool.  The win is per-symbol network I/O (quote fetch) plus the
        # native-compute sections (numpy/pandas/statsmodels/arch release the GIL).
        # Results are reassembled in the ORIGINAL symbol order so the Sheet/HTML/
        # snapshot output and logs are byte-identical regardless of completion order
        # or worker count.  Dead-letter semantics are preserved exactly: a per-symbol
        # exception becomes an entry in RunResult.errors and never aborts the run.
        logger.info("Evaluating %d symbols...", len(ctx.symbols))

        def _eval_one(symbol: str):
            """Return ('ok', Recommendation) or ('err', error_dict) for one symbol.

            Never raises — mirrors the original per-symbol try/except so a single
            bad ticker is dead-lettered, not propagated (CONSTRAINT #6).
            """
            try:
                position = ctx.snapshot.positions.get(symbol)
                rec = ctx.advisory_evaluate_fn(
                    symbol=symbol,
                    position=position,
                    market=ctx.market,
                    snapshot=ctx.snapshot,
                    macro_dto=ctx.macro_dto,
                    context_extras=ctx.context_extras,
                )
                return ("ok", rec)
            except Exception as exc:
                return ("err", {
                    "symbol": symbol,
                    "stage": "advisory_evaluate",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        workers = max(1, int(getattr(settings, "ADVISORY_MAX_CONCURRENCY", 8)))
        if workers == 1 or len(ctx.symbols) <= 1:
            # Sequential path — original, fully-deterministic behavior.
            results_by_symbol = {sym: _eval_one(sym) for sym in ctx.symbols}
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(ctx.symbols))) as pool:
                # executor.map preserves input order; we still key by symbol below
                # so the assembly pass is order-explicit and robust.
                mapped = pool.map(_eval_one, ctx.symbols)
                results_by_symbol = {sym: res for sym, res in zip(ctx.symbols, mapped)}

        # Ordered assembly: rebuild recommendations/errors and emit logs in the
        # original symbol order so output is deterministic.
        for symbol in ctx.symbols:
            kind, payload = results_by_symbol[symbol]
            if kind == "ok":
                rec = payload
                ctx.recommendations.append(rec)
                logger.info(
                    "  %-6s  %-10s  conviction=%.2f  quality=%-7s  pos=%.1f%%",
                    symbol,
                    rec.action,
                    rec.conviction,
                    rec.data_quality,
                    rec.suggested_position_pct * 100.0,
                )
            else:
                logger.warning("Advisory failed for %s: %s", symbol, payload["message"])
                ctx.errors.append(payload)

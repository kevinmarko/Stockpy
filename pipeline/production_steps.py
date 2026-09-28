"""Concrete PipelineStep implementations for the production async orchestrator: data fetch, run_pipeline, options/GARCH analysis, indicator processing, multi-horizon forecasting, strategy + advisory overlay, gated broker execution, and state-snapshot / report rendering. Each step reads and writes the shared RunContext."""

# ---------------------------------------------------------------------------
# TensorFlow, if installed, MUST be imported before pandas/pyarrow -- defense
# in depth for the CNN-LSTM/TensorFlow deadlock (issue #381, docs/known_issues/
# cnn_lstm_tf_deadlock.md). forecasting_engine.py's own import reorder (PR
# #387) only protects a process where IT is the first thing to touch pandas;
# this module (the actual forecasting step run by main_orchestrator.py) is
# imported after this file's own `import pandas as pd` below in every real
# call chain, so without this guard the real production forecasting step
# stays exposed. A no-op when TensorFlow isn't installed. The primary fix is
# CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED (settings.py), which isolates
# CNN-LSTM fit/predict in a subprocess and doesn't depend on any entry
# point's import order at all; this import is a cheap second layer for the
# case isolation is left off.
# ---------------------------------------------------------------------------
try:
    import tensorflow  # noqa: F401
except ImportError:
    pass

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from typing import Any, Optional

from pipeline.base import PipelineStep
from pipeline.context import RunContext
from settings import settings
class TelemetryProxy:
    def __getattr__(self, name):
        import main_orchestrator
        return getattr(main_orchestrator.telemetry, name)

telemetry = TelemetryProxy()

logger = logging.getLogger("ProductionPipeline")

# Fraction of a stage's INPUT symbols that must be dropped before we log a
# WARNING calling it out. 0.5 is deliberately loose (a routine handful of
# dead-lettered symbols must never spam the log) -- this exists purely so a
# repeat of the "430-symbol universe, 26 forecasted" operator report is
# visible in the logs at the moment it happens, not only reconstructable
# after the fact from state_snapshot.json (see
# docs/known_issues/universe_count_reporting_mismatch.md).
_UNIVERSE_FUNNEL_DROP_WARN_FRACTION = 0.5


def _warn_on_universe_funnel_drop(before: int, after: int, stage_label: str) -> None:
    """Log a WARNING when *stage_label* dropped more than
    ``_UNIVERSE_FUNNEL_DROP_WARN_FRACTION`` of *before*'s symbols, producing
    *after*. No-op when ``before`` is 0 (nothing to drop) or ``after >=
    before`` (no drop, or a stage that can only add symbols). Never raises
    (CONSTRAINT #6) -- this is diagnostic-only and must never affect the
    pipeline it's observing.
    """
    try:
        if before <= 0 or after >= before:
            return
        dropped = before - after
        if (dropped / before) > _UNIVERSE_FUNNEL_DROP_WARN_FRACTION:
            logger.warning(
                "Universe funnel: %s dropped %d/%d symbols (%.0f%%) -- %d -> %d. "
                "See state_snapshot.json's 'universe_funnel' field for the full "
                "per-stage breakdown this cycle.",
                stage_label, dropped, before, 100.0 * dropped / before, before, after,
            )
    except Exception:  # noqa: BLE001 - diagnostic helper must never raise
        pass


class AsyncDataFetchStep(PipelineStep):
    """Fetches macro, fundamentals, and technicals concurrently."""
    name = "data"
    
    async def run(self, ctx: RunContext) -> None:
        """Fetch macro, fundamentals, and technicals concurrently into the RunContext."""
        import main_orchestrator
        import data_engine as data_engine_mod
        from data_engine import DataEngine, MockDataEngine
        from dto_models import RobinhoodPositionDTO

        settings.warn_if_fred_key_leaked(telemetry)

        # Robinhood account snapshot first: its held symbols are one of the
        # universe's inputs. Same call as before step 5.1 (20 h cache, then
        # the existing ROBINHOOD_AUTO_REFRESH_ENABLED-gated live tier); it
        # only moved up from below the data-engine setup. No new login path.
        rh_positions = {}
        snapshot = None
        try:
            snapshot = await asyncio.to_thread(main_orchestrator.fetch_account_snapshot)
            rh_positions = main_orchestrator.account_snapshot_to_robinhood_positions(snapshot)
        except Exception as rh_exc:
            snapshot = None
            telemetry.warning(
                f"Robinhood account snapshot unavailable: {rh_exc}; "
                "proceeding without holdings-aware overlay."
            )
        ctx.context_extras["robinhood_positions"] = rh_positions
        # Kept for AdvisoryOverlayStep / AgenticQueueStep (step 5.2), which
        # used to fetch the same 20 h-cached snapshot a second time. None on
        # failure; AdvisoryOverlayStep substitutes main.py's empty snapshot.
        ctx.snapshot = snapshot

        # One universe builder for both orchestrators (step 5.1):
        # held ∪ WATCHLIST/watchlist.txt ∪ discovered, rating auto-drop,
        # DEFAULT_TICKERS only when that whole union is empty, then
        # recently-closed retention unioned LAST. main.py calls the same
        # pipeline.advisory_inputs.build_universe_detailed(), so the two can
        # no longer diverge. Before 5.1 this step left held out of the union
        # (so DEFAULT_TICKERS could fire while positions were held) and had
        # no closed-position retention -- see
        # docs/known_issues/daemon_universe_watchlist_divergence.md.
        from pipeline.advisory_inputs import build_universe_detailed

        build = build_universe_detailed(snapshot, watchlist_file=ctx.watchlist_file)
        base_symbols = list(build.symbols)

        # Permanent universe-funnel diagnostic (see
        # docs/known_issues/universe_count_reporting_mismatch.md): records the
        # symbol count surviving each narrowing stage of a cycle so a future
        # "N-symbol universe but only M forecasted" report can be diagnosed
        # from state_snapshot.json directly instead of re-deriving it from
        # scratch. Never gates behavior -- purely additive telemetry.
        # Since step 5.1 held symbols go into the builder's union rather than
        # being appended afterwards, so tracked_universe_before_held now
        # counts the universe symbols that are neither held nor retained
        # (the ones only watchlist/discovery/DEFAULT_TICKERS contributed).
        universe_funnel: dict = {
            "configured_default_tickers": len(settings.DEFAULT_TICKERS or []),
            "watchlist_count": len(build.watchlist),
            "discovered_count": len(build.discovered),
            "default_tickers_is_fallback": build.default_tickers_is_fallback,
            "tracked_universe_before_held": len(
                set(base_symbols) - build.held - build.recently_closed
            ),
            "held_positions_added": len(rh_positions),
            "recently_closed_added": len(build.recently_closed),
        }
        ctx.context_extras["universe_funnel"] = universe_funnel

        # Initialize data engine
        de = ctx.market
        if de is None:
            if data_engine_mod.live_data_configured():
                settings.ensure_fred_configured()
                de = DataEngine(settings.FRED_API_KEY)
                ctx.symbols = base_symbols
            else:
                telemetry.warning("FRED_API_KEY not configured. Operating with deterministic MockDataEngine.")
                de = MockDataEngine()
                # Mock mode keeps its pre-5.1 universe: AAPL plus held.
                ctx.symbols = ["AAPL"] + [tk for tk in rh_positions if tk != "AAPL"]
            ctx.market = de
        else:
            ctx.symbols = base_symbols
        universe_funnel["tracked_universe_total"] = len(ctx.symbols)

        # 1. Asynchronous concurrent data fetching
        if ctx.progress is not None:
            ctx.progress.start_stage("data", symbols_total=len(ctx.symbols))

        try:
            ctx.macro_raw, ctx.fund_raw, ctx.tech_raw = await main_orchestrator.fetch_all_data_async(de, ctx.symbols)
        except Exception as fetch_err:
            telemetry.critical(f"Asynchronous data gathering crashed: {fetch_err}")
            raise main_orchestrator.PipelineFatalError("Asynchronous data gathering crashed") from fetch_err

        # Fail-safe check
        if not ctx.tech_raw or all(df.empty for df in ctx.tech_raw.values()):
            telemetry.warning("Fetched pricing data is empty (likely due to network offline). Falling back to MockDataEngine for verification.")
            de = MockDataEngine()
            ctx.market = de
            ctx.macro_raw = de.fetch_macro_raw()
            ctx.fund_raw = de.fetch_fundamentals_raw(ctx.symbols)
            ctx.tech_raw = de.fetch_technical_raw(ctx.symbols)
            # Broker-agnostic synthetic-data marker (Finding 1): this cycle's
            # figures are MockDataEngine's flat $10 prices and fabricated
            # fundamentals, not real market data. Mirrors
            # main_orchestrator._mark_data_refreshed()'s real-data-only
            # asymmetry below -- set ONLY on this fallback branch, never on
            # the real-data path. BrokerExecutionStep checks this marker
            # before it will submit any order, regardless of which broker
            # backend (Alpaca, FMPPaperBroker, ...) settings.BROKER_BACKEND
            # selects one layer deeper.
            ctx.context_extras['data_is_synthetic'] = True
        else:
            # Real (non-mock) data landed — stamp the cross-cycle freshness
            # marker so the daemon's interval gate (DATA_FRESHNESS_TTL_SECONDS)
            # can skip the next few pulls. The mock fallback above deliberately
            # does NOT stamp it, so an offline blip re-tries on the next cycle
            # rather than being treated as a fresh pull.
            main_orchestrator._mark_data_refreshed()

        universe_funnel["tech_raw_count"] = len(ctx.tech_raw)
        universe_funnel["fund_raw_count"] = len(ctx.fund_raw)
        _warn_on_universe_funnel_drop(
            universe_funnel["tracked_universe_total"], universe_funnel["tech_raw_count"], "technical data fetch",
        )

        # Kill-switch check
        ks = main_orchestrator.GlobalKillSwitch()
        if ks.is_active():
            ks_reason = ks.reason() or "(no reason recorded)"
            telemetry.info(
                "Advisory paused by kill-switch sentinel — skipping pipeline. "
                "Reason: %s  |  Deactivate with: "
                "python -m execution.kill_switch --deactivate",
                ks_reason,
            )
            ctx.stopped = True
            ctx.stop_reason = "kill_switch"


class RunPipelineStep(PipelineStep):
    """Executes the synchronous run_pipeline step."""
    name = "run_pipeline"

    def run(self, ctx: RunContext) -> None:
        """Execute the synchronous run_pipeline stage over the fetched data."""
        import main_orchestrator
        try:
            final_df, macro_dto, shared_context = main_orchestrator.run_pipeline(
                tickers=ctx.symbols,
                macro_raw=ctx.macro_raw,
                fund_raw=ctx.fund_raw,
                tech_raw=ctx.tech_raw,
                data_engine=ctx.market,
                robinhood_positions=ctx.context_extras.get("robinhood_positions"),
                engines=ctx.engine_context,
                progress=ctx.progress,
            )
        except Exception as pipe_err:
            main_orchestrator.telemetry.critical(f"Platform execution pipeline crashed: {pipe_err}", exc_info=True)
            raise main_orchestrator.PipelineFatalError("Platform execution pipeline crashed") from pipe_err
        ctx.dashboard_df = final_df
        ctx.macro_dto = macro_dto
        ctx.context_extras["shared_context"] = shared_context


class MacroStep(PipelineStep):
    """Builds this cycle's ``MacroEconomicDTO`` (Sahm, macro kill switch, HMM).

    Split out of the old ``OptionsAnalysisStep`` (2026-09, step 3d) with its
    body unchanged, so the equity path no longer runs through the options
    desk to get its macro regime.
    """
    name = "macro_volatility"

    def run(self, ctx: RunContext) -> None:
        """Compute the macro regime DTO onto ``ctx.macro_dto``."""
        from main_orchestrator import MacroEngine, MacroEconomicDTO
        from macro_engine import macro_killswitch_data_unavailable

        if ctx.progress is not None:
            ctx.progress.start_stage("macro_volatility", symbols_total=len(ctx.symbols))

        engines = ctx.engine_context

        # Macro economic analysis
        telemetry.info("Routing data through Macro Engine...")
        me = (engines.macro_engine if engines is not None and engines.macro_engine is not None
              else MacroEngine(data_engine=ctx.market))
        sahm_val, sahm_used_fallback = me._calculate_sahm_rule_detailed()
        # ctx.macro_raw originates from main_orchestrator.fetch_all_data_async(de, ...)
        # calling de.fetch_macro_raw() on this SAME ctx.market instance, earlier
        # in this cycle -- so de.last_macro_raw_fabricated_keys is already set
        # by the time this step runs. Closes the populated-but-fabricated blind
        # spot: DataEngine.fetch_macro_raw()'s hardcoded emergency fallback
        # populates EVERY key with a benign literal, so a plain presence check
        # alone would report "available" even during a total FRED outage.
        macro_raw_fabricated_keys = getattr(ctx.market, "last_macro_raw_fabricated_keys", frozenset())
        macro_data = me.run_macro_killswitch(
            ctx.macro_raw, sahm_val, fabricated_keys=macro_raw_fabricated_keys,
        )

        hmm_result = me.compute_hmm_risk_on_probability(ctx.tech_raw.get('SPY'))
        hmm_risk_on_probability = hmm_result["risk_on_probability"] if hmm_result else None
        hmm_regime_state = hmm_result["regime_state_label"] if hmm_result else None

        # CONSTRAINT #4/#6: a caller substituting a benign literal default
        # (e.g. VIXCLS=15.0) for a genuinely missing FRED key must not let
        # the kill switch read that as a real "risk on" measurement. Forces
        # MacroEconomicDTO.killSwitch/_rules_based_regime to fail closed --
        # see dto_models.py and macro_engine.py::macro_killswitch_data_unavailable.
        data_unavailable = (
            macro_killswitch_data_unavailable(ctx.macro_raw, fabricated_keys=macro_raw_fabricated_keys)
            or sahm_used_fallback
        )

        ctx.macro_dto = MacroEconomicDTO(
            yield_curve_10y_2y=float(ctx.macro_raw.get('T10Y2Y', 0.5)),
            high_yield_oas=float(ctx.macro_raw.get('BAMLH0A0HYM2', 3.5)),
            inflation_rate=float(ctx.macro_raw.get('CPIAUCSL_YoY', 2.0)),
            nominal_10y=float(ctx.macro_raw.get('DGS10', 4.0)),
            vix_value=float(ctx.macro_raw.get('VIXCLS', 15.0)),
            sahm_rule_indicator=sahm_val,
            hmm_risk_on_probability=hmm_risk_on_probability,
            hmm_regime_state=hmm_regime_state,
            data_unavailable=data_unavailable,
        )


class TrendVolatilityStep(PipelineStep):
    """Per-ticker GJR-GARCH vol and the Aroon/Coppock/Chandelier indicators.

    What is left of the old ``OptionsAnalysisStep`` once the options desk was
    cut out of core (2026-09, step 3d): the implied-vol reads, True IVR, VRP,
    realized-vol rank and the options strategy matrix are gone; ``GARCH_Vol``
    (cold-start sizing), the GARCH term structure ``ForecastingStep`` reuses,
    and the trend/exit indicators ``StrategyEvalStep`` uses are computed
    exactly as before, now from ``volatility.garch`` and ``trend_indicators``.
    """
    name = "macro_volatility"

    def run(self, ctx: RunContext) -> None:
        """Compute per-ticker trend/exit indicators and GJR-GARCH volatility."""
        from main_orchestrator import GarchVolatilityEstimator
        from trend_indicators import calculate_trend_exit_indicators
        from concurrent.futures import ThreadPoolExecutor

        engines = ctx.engine_context

        telemetry.info("Routing data through Trend & Volatility Engine...")
        garch = (engines.garch_estimator
                 if engines is not None and engines.garch_estimator is not None
                 else GarchVolatilityEstimator())

        trend_vol_indicators = {}

        def _trend_vol_one(ticker):
            df_hist = ctx.tech_raw.get(ticker)
            if df_hist is None or df_hist.empty:
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Volatility: {ticker} (no data)")
                return ticker, None, None
            try:
                indicators = calculate_trend_exit_indicators(df_hist)
                # ONE GJR-GARCH fit covers both the horizon=1 GARCH_Vol column
                # AND the forecasting step's per-horizon Monte Carlo sigma
                # (10/30/60/90) -- see estimate_gjr_garch_volatility_term_structure's
                # docstring. Threaded through via garch_term_structure below
                # so ForecastingStep never has to refit.
                garch_term_structure = garch.estimate_gjr_garch_volatility_term_structure(
                    df_hist, horizons=(1, 10, 30, 60, 90)
                )
                # None means there isn't even enough history to measure a
                # historical-stdev fallback (CONSTRAINT #4 -- see the
                # estimator's own docstring). Degrade GARCH_Vol to NaN rather
                # than letting this whole per-ticker step crash on
                # `garch_term_structure[1]` and lose Aroon/Coppock/Chandelier
                # too, none of which depend on GARCH at all.
                vol = garch_term_structure[1] if garch_term_structure is not None else float('nan')
                result = {
                    "Aroon_Oscillator": indicators["Aroon_Oscillator"],
                    "Coppock_Curve": indicators["Coppock_Curve"],
                    "Chandelier_Long": indicators["Chandelier_Long"],
                    "Chandelier_Short": indicators["Chandelier_Short"],
                    "GARCH_Vol": vol,
                }
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Volatility: {ticker}")
                return ticker, result, garch_term_structure
            except Exception as tv_exc:
                telemetry.warning(
                    f"Trend & volatility analysis failed for {ticker}: {tv_exc}. "
                    f"Skipping GARCH/trend metrics for this ticker this cycle."
                )
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Volatility: {ticker} (failed)")
                return ticker, None, None

        tv_workers = min(int(getattr(settings, "FORECAST_MAX_CONCURRENCY", 8)), max(1, len(ctx.symbols)))
        if tv_workers <= 1 or len(ctx.symbols) <= 1:
            tv_results = [_trend_vol_one(t) for t in ctx.symbols]
        else:
            with ThreadPoolExecutor(max_workers=tv_workers) as tv_pool:
                tv_results = list(tv_pool.map(_trend_vol_one, ctx.symbols))

        garch_term_structures: dict[str, dict[int, float]] = {}
        for tk, res, term_structure in tv_results:
            if res is not None:
                trend_vol_indicators[tk] = res
            if term_structure is not None:
                garch_term_structures[tk] = term_structure

        ctx.context_extras["trend_vol_indicators"] = trend_vol_indicators
        # Per-ticker {horizon: annualized_vol} GARCH term structure, consumed
        # by ForecastingStep below to avoid refitting GJR-GARCH a second time
        # this cycle for the SAME per-horizon Monte Carlo sigma computation.
        ctx.context_extras["garch_term_structures"] = garch_term_structures


class ProcessingStep(PipelineStep):
    """Processes indicators and creates dashboard_df."""
    name = "processing"
    
    def run(self, ctx: RunContext) -> None:
        """Process indicators and build the dashboard DataFrame."""
        from main_orchestrator import ProcessingEngine, FundamentalDataDTO

        telemetry.info("Routing data through Computational Core (Processing)...")
        if ctx.progress is not None:
            ctx.progress.start_stage("processing")

        engines = ctx.engine_context
        pe = (engines.processing_engine if engines is not None and engines.processing_engine is not None
              else ProcessingEngine())
        
        regime_metrics = pe.process_macro_regime(ctx.macro_dto)
        tech_metrics = pe.calculate_technical_metrics(ctx.tech_raw, transactions_df=None)
        
        fund_dtos = {}
        for ticker, data in ctx.fund_raw.items():
            try:
                if data and 'info' in data:
                    fund_dtos[ticker] = FundamentalDataDTO.from_raw_dict(ticker, data['info'], dividends=data.get('dividends'))
            except Exception as fund_dto_exc:
                telemetry.warning(
                    f"Fundamental DTO construction failed for {ticker}: {fund_dto_exc}. "
                    f"Skipping fundamentals for this ticker this cycle."
                )

        ctx.context_extras["fund_dtos"] = fund_dtos

        realized_vol_60d_map = {
            ticker: metrics.get('Realized_Vol_60D', float('nan'))
            for ticker, metrics in tech_metrics.items()
        }
        fund_metrics = pe.calculate_fundamental_metrics(fund_dtos, realized_vol_60d_map=realized_vol_60d_map)
        
        ctx.dashboard_df = pe.compile_dashboard(tech_metrics, fund_metrics, regime_metrics)

        # Universe-funnel diagnostic (see AsyncDataFetchStep above and
        # docs/known_issues/universe_count_reporting_mismatch.md).
        # compile_dashboard() is a UNION of tech/fund keys, so this stage can
        # only add rows relative to tech_raw/fund_raw individually -- but it
        # can still be smaller than the tracked universe if BOTH tech_raw and
        # fund_raw dropped the same ticker.
        universe_funnel = ctx.context_extras.get("universe_funnel")
        if universe_funnel is not None:
            universe_funnel["dashboard_rows"] = len(ctx.dashboard_df)
            _warn_on_universe_funnel_drop(
                universe_funnel.get("tracked_universe_total", 0),
                universe_funnel["dashboard_rows"],
                "dashboard compilation (tech ∪ fund raw data)",
            )

        trend_vol_indicators = ctx.context_extras.get("trend_vol_indicators", {})
        _apply_trend_vol_columns(ctx.dashboard_df, trend_vol_indicators)


class ForecastingStep(PipelineStep):
    """Executes multi-horizon forecasting."""
    name = "forecasting"
    
    def run(self, ctx: RunContext) -> None:
        """Run multi-horizon forecasting for each ticker."""
        from main_orchestrator import ForecastingEngine, ForecastTracker
        from concurrent.futures import ThreadPoolExecutor

        telemetry.info("Routing data through Forecasting Engine...")
        if ctx.progress is not None:
            ctx.progress.start_stage("forecasting", symbols_total=len(ctx.dashboard_df))

        engines = ctx.engine_context
        # A ForecastTracker is ALWAYS attached (2026-09 fix) so forecast-
        # coverage recording (record_forecasts/update_actuals) runs every
        # cycle regardless of settings.FORECAST_SKILL_WEIGHTING_ENABLED --
        # see main_orchestrator.py::EngineContext.build's identical comment
        # for the full rationale. The flag itself continues to gate ONLY the
        # skill-weighted ensemble blending read-back inside
        # ForecastingEngine.generate_forecast.
        fallback_tracker = ForecastTracker()
        fe = (engines.forecasting_engine if engines is not None and engines.forecasting_engine is not None
              else ForecastingEngine(tracker=fallback_tracker))
        
        forecast_cols = ['Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
                         'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90',
                         'Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper',
                         # Disclosure flags (CONSTRAINT #4) -- True when EVERY
                         # forecasting model failed to produce output for that
                         # horizon and ForecastingEngine._blend_with_skill had
                         # nothing left to blend but current_price (see
                         # forecasting_engine.py::generate_forecast's own
                         # comment on why that's current_price, not NaN).
                         # Deliberately NOT registered in config.COLUMN_SCHEMA
                         # -- Pandera's DashboardSchema is non-strict, so an
                         # extra column here is fine, and adding it to the
                         # schema would force main.py's advisory path /
                         # report templates to also
                         # populate it, which is out of scope for this change
                         # (see docs/known_issues/forecast_fallback_current_price_disclosure.md).
                         'Forecast_10_Is_Fallback', 'Forecast_30_Is_Fallback',
                         'Forecast_60_Is_Fallback', 'Forecast_90_Is_Fallback']
        # Forecasting rebuild F3: the gate's "published naive" disclosure
        # columns exist only when the gate drives the published forecast, so
        # the flag-off dashboard keeps exactly its pre-F3 columns.
        if bool(getattr(settings, "FORECAST_NAIVE_GATE_ENABLED", False)):
            forecast_cols += ['Forecast_10_Gated_Naive', 'Forecast_30_Gated_Naive',
                              'Forecast_60_Gated_Naive', 'Forecast_90_Gated_Naive']

        def _forecast_one(row) -> tuple[str, dict | None]:
            ticker = row['Symbol']
            price = row['Price']
            if not price or price == 0:
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Forecasting: {ticker} (no price)")
                return ticker, None

            history_df = ctx.tech_raw.get(ticker)
            history_series = history_df['Close'] if history_df is not None else None

            try:
                # {horizon: annualized_vol}, fit ONCE by TrendVolatilityStep above
                # (against this same history_df) -- lets generate_forecast give
                # each of the 10/30/60/90-day horizons its OWN mean-reversion
                # -aware sigma without a redundant second GJR-GARCH fit here.
                precomputed_term_structure = ctx.context_extras.get(
                    "garch_term_structures", {}
                ).get(ticker)
                forecasts = fe.generate_forecast(
                    row, price, history_series, history_df=history_df,
                    precomputed_garch_term_structure=precomputed_term_structure,
                )
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Forecasting: {ticker}")
                return ticker, forecasts
            except Exception as ml_err:
                telemetry.warning(f"Forecasting Engine failure for {ticker}: {ml_err}. Reverting to baseline default.")
                mu = float('nan')
                sigma = float('nan')
                if history_series is not None and len(history_series) > 1:
                    returns = np.log(history_series / history_series.shift(1)).dropna()
                    mu = float(returns.mean())
                    sigma = float(returns.std())

                mc_target, mc_low, mc_high = fe.run_monte_carlo(price, mu, sigma, 30)
                mc_10, _, _ = fe.run_monte_carlo(price, mu, sigma, 10)
                mc_60, _, _ = fe.run_monte_carlo(price, mu, sigma, 60)
                mc_90, _, _ = fe.run_monte_carlo(price, mu, sigma, 90)
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Forecasting: {ticker} (fallback)")
                return ticker, {
                    'Target_Days': 30,
                    'ARIMA': price,
                    'MC_Target': mc_target,
                    'MC_Lower': mc_low,
                    'MC_Upper': mc_high,
                    'Forecast_10': mc_10,
                    'Forecast_30': mc_target,
                    'Forecast_60': mc_60,
                    'Forecast_90': mc_90,
                    # Deliberately no Forecast_30_Prophet_Lower/_Upper here --
                    # these are MC percentiles, not Prophet output, and
                    # writing them under the Prophet name was a mislabeling
                    # bug fixed as part of the forecast-math audit (see
                    # docs/known_issues/forecast_ito_double_correction_and_horizon_units.md).
                    # The full ForecastingEngine blew up (ml_err above) and
                    # this whole row is a coarse single-model Monte Carlo
                    # recovery, not the real ARIMA/HW/CNN-LSTM/Prophet
                    # ensemble -- honestly disclosed as a fallback on every
                    # horizon, same as generate_forecast's own per-horizon
                    # flag above.
                    'Forecast_10_Is_Fallback': True,
                    'Forecast_30_Is_Fallback': True,
                    'Forecast_60_Is_Fallback': True,
                    'Forecast_90_Is_Fallback': True,
                }

        workers = max(1, int(getattr(settings, "FORECAST_MAX_CONCURRENCY", 8)))
        rows = ctx.dashboard_df.to_dict('records')
        if workers == 1 or len(rows) <= 1:
            pairs = [_forecast_one(r) for r in rows]
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(rows))) as pool:
                pairs = list(pool.map(_forecast_one, rows))
        forecast_results = {tk: fc for tk, fc in pairs if fc is not None}

        # Forecasting rebuild F2: one aggregated line per cycle for the safety
        # guards (per-drop detail is at DEBUG inside the engine).
        _pop_guard_stats = getattr(fe, "pop_guard_stats", None)
        if callable(_pop_guard_stats):
            try:
                _guard_stats = _pop_guard_stats()
                if any(not k.startswith("gate_") and v for k, v in _guard_stats.items()):
                    telemetry.info(
                        "Forecast guards this cycle: %d clamped, %d input-price drops, "
                        "%d symbol-horizons with every model dropped, %d symbols with no "
                        "GARCH sigma (clamp skipped), %d symbols with no price history "
                        "(input check skipped).",
                        _guard_stats.get("dropped_clamp", 0),
                        _guard_stats.get("dropped_input_price", 0),
                        _guard_stats.get("all_dropped_horizons", 0),
                        _guard_stats.get("sigma_unavailable", 0),
                        _guard_stats.get("no_reference_close", 0),
                    )
                _gate_total = (_guard_stats.get("gate_naive_horizons", 0)
                               + _guard_stats.get("gate_admitted_horizons", 0))
                if _gate_total:
                    telemetry.info(
                        "Forecast naive gate this cycle (%s): %d symbol-horizons admitted "
                        "at least one model, %d fell back to naive, %d with no usable "
                        "ledger stats.",
                        "LIVE" if getattr(settings, "FORECAST_NAIVE_GATE_ENABLED", False) else "shadow",
                        _guard_stats.get("gate_admitted_horizons", 0),
                        _guard_stats.get("gate_naive_horizons", 0),
                        _guard_stats.get("gate_stats_unavailable", 0),
                    )
            except Exception as _guard_exc:  # noqa: BLE001 - logging only
                telemetry.debug("Forecast guard stats unavailable: %s", _guard_exc)

        # Universe-funnel diagnostic, final stage (see AsyncDataFetchStep /
        # ProcessingStep above and
        # docs/known_issues/universe_count_reporting_mismatch.md).
        # `forecasted_count` counts every ticker that got ANY forecast entry
        # (a real ForecastingEngine result OR its Monte-Carlo fallback on a
        # ForecastingEngine exception) -- `_forecast_one` returns None ONLY
        # when Price was falsy/zero, so `dashboard_rows - forecasted_count`
        # is exactly the count of rows skipped for that reason this cycle.
        universe_funnel = ctx.context_extras.get("universe_funnel")
        if universe_funnel is not None:
            universe_funnel["forecasted_count"] = len(forecast_results)
            universe_funnel["skipped_missing_price_count"] = len(rows) - len(forecast_results)
            _warn_on_universe_funnel_drop(
                len(rows), len(forecast_results), "forecasting (rows with a usable Price)",
            )

        _apply_forecast_columns(ctx.dashboard_df, forecast_results, forecast_cols)


def _apply_forecast_columns(
    dashboard_df: pd.DataFrame, forecast_results: dict, forecast_cols: list,
) -> None:
    """Map ``ForecastingStep``'s per-ticker ``forecast_results`` dict onto
    ``dashboard_df``'s Target_Days/ARIMA/MC_*/Forecast_* columns.

    NaN-fills every column first, then overlays whatever each ticker actually
    produced. A ticker absent from ``forecast_results`` (price was 0/missing,
    so ``_forecast_one`` short-circuited without ever calling the forecasting
    engine) or missing an individual key -- e.g. ``Forecast_30_Prophet_Lower``/
    ``_Upper``, which ``ForecastingEngine.generate_forecast`` only sets when
    Prophet actually produced output for that ticker this cycle -- stays NaN
    for that cell. "Unforecastable"/"Prophet unavailable" must never read as
    "$0.00 target price" (CONSTRAINT #4); a fabricated 0.0 there is
    indistinguishable from a genuinely-computed, near-zero forecast.

    Deliberately a module-level function (same NaN-fill-first pattern as
    ``_apply_sector_heat_factor`` below) so it's testable without going
    through ``ForecastingStep.run()``'s heavy ``main_orchestrator`` import
    chain.
    """
    nan = float("nan")
    for col in forecast_cols:
        dashboard_df[col] = nan
    for col in forecast_cols:
        dashboard_df[col] = dashboard_df['Symbol'].map(
            lambda x: forecast_results.get(x, {}).get(col, nan)
        )


# Realized_Vol_Rank / True_IVR / VRP used to be mapped here too; they were
# options-desk columns (always NaN since step 3d) and left COLUMN_SCHEMA in
# the 2026-09 settings/schema trim (step 4f).
_TREND_VOL_COLUMN_MAP = (
    ('GARCH_Vol', 'GARCH_Vol'),
    ('Aroon Oscillator', 'Aroon_Oscillator'),
    ('Coppock Curve', 'Coppock_Curve'),
    ('Chandelier Exit', 'Chandelier_Long'),
)


def _apply_trend_vol_columns(dashboard_df: pd.DataFrame, trend_vol_indicators: dict) -> None:
    """Map TrendVolatilityStep's per-ticker ``trend_vol_indicators`` dict onto
    ``dashboard_df``'s GARCH/Aroon/Coppock/Chandelier columns.

    NaN-fills every column first, then overlays whatever each ticker actually
    has in ``trend_vol_indicators``. A ticker absent from that dict (its
    ``TrendVolatilityStep._trend_vol_one()`` call failed or was dead-lettered
    this cycle) or missing an individual key stays NaN for that cell --
    "uncomputable" must never read as a computed zero (CONSTRAINT #4); a fabricated 0.0 there is indistinguishable from a
    genuinely-computed, legitimately-zero value.

    Deliberately a module-level function (same pattern as
    ``_apply_sector_heat_factor`` above) so it's testable without going
    through ``ProcessingStep.run()``'s heavy ``main_orchestrator`` import
    chain.
    """
    nan = float("nan")
    for col_key, _ in _TREND_VOL_COLUMN_MAP:
        dashboard_df[col_key] = nan
    for col_key, mapped_key in _TREND_VOL_COLUMN_MAP:
        dashboard_df[col_key] = dashboard_df['Symbol'].map(
            lambda x: trend_vol_indicators.get(x, {}).get(mapped_key, nan)
        )


def _record_symbol_ratings(dashboard_df: Optional[pd.DataFrame], cycle_id: str) -> None:
    """Best-effort write of this cycle's per-symbol GOOD/BAD rating
    (``rating.symbol_rating.classify_tier``) to the durable
    ``rating.symbol_rating_store.SymbolRatingStore``.

    A module-level function (same pattern as ``_apply_sector_heat_factor``
    above) so it's testable directly, without
    going through ``StrategyEvalStep.run()``'s heavy import chain. No-ops
    when ``settings.SYMBOL_RATING_ENABLED`` is off or ``dashboard_df`` is
    empty/``None`` -- mirrors the CAP-EVENT AUDIT LOG block's own guard.
    Write failures intentionally propagate (``SymbolRatingStore.record_ratings``'s
    own documented contract) -- the caller (``StrategyEvalStep.run()``) wraps
    this in its own best-effort try/except so a DB hiccup never affects the
    run's own scoring/sizing decisions (CONSTRAINT #6).
    """
    if not settings.SYMBOL_RATING_ENABLED or dashboard_df is None or dashboard_df.empty:
        return

    from rating.symbol_rating import classify_tier
    from rating.symbol_rating_store import SymbolRatingStore

    events = []
    for row in dashboard_df.to_dict('records'):
        # A missing Score/Action Signal means this ticker's strategy
        # evaluation never reached the 'results' stage this cycle
        # (dead-lettered) -- skip it rather than writing a fabricated
        # rating (CONSTRAINT #4).
        _raw_score = row.get("Score")
        _raw_action = row.get("Action Signal")
        if _raw_score is None or (isinstance(_raw_score, float) and pd.isna(_raw_score)):
            continue
        if _raw_action is None or (isinstance(_raw_action, float) and pd.isna(_raw_action)):
            continue
        _score = float(_raw_score)
        events.append({
            "symbol": row["Symbol"],
            "score": _score,
            "action_signal": str(_raw_action) or None,
            "tier": classify_tier(_score, settings.SYMBOL_RATING_BAD_SCORE_THRESHOLD),
            "is_held": float(row.get("Robinhood Shares", 0.0) or 0.0) > 0,
            "cycle_id": cycle_id,
        })
    SymbolRatingStore().record_ratings(events, cycle_id=cycle_id)


def _apply_symbol_rating_columns(dashboard_df: pd.DataFrame) -> None:
    """Populate the ``config.COLUMN_SCHEMA``-registered
    ``Symbol_Rating_Consecutive_Bad_Cycles`` / ``Symbol_Rating_Excluded``
    columns for every ticker in ``dashboard_df``.

    Defaults ("0 consecutive bad cycles" / ``"No"``) are set FIRST, same
    pattern as ``Attention_Score``'s NaN pre-fill above -- pandera's
    ``config.DashboardSchema`` requires both columns to exist on every row
    regardless of whether the rating store is reachable, and "0"/"No" is
    never a fabricated value here: it's the exact same floor
    ``SymbolRatingStore.get_consecutive_bad_cycles`` itself already returns
    for both "genuinely no bad-cycle streak" AND "store read failed"
    (CONSTRAINT #4 is not violated -- a failed read and "no history" are
    behaviorally identical for this purpose: neither should ever read as
    excluded).

    Deliberately INDEPENDENT of ``settings.SYMBOL_RATING_AUTO_DROP_ENABLED``
    -- these are diagnostic-only columns (HTML report/state snapshot),
    so the operator can see which symbols WOULD be excluded before ever
    opting into the auto-drop behavior. Only ``settings.SYMBOL_RATING_ENABLED``
    (default True) gates whether there's any rating history to read at all.
    """
    dashboard_df['Symbol_Rating_Consecutive_Bad_Cycles'] = 0.0
    dashboard_df['Symbol_Rating_Excluded'] = "No"

    if not settings.SYMBOL_RATING_ENABLED or dashboard_df.empty:
        return

    from rating.symbol_rating_store import SymbolRatingStore

    store = SymbolRatingStore(readonly=True)
    threshold = settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES

    # Vectorized (F5 fix, docs/module_efficiency_redundancy_audit.md): this
    # used to be a dashboard_df['Symbol'].map(_cycles) closure calling
    # get_consecutive_bad_cycles(symbol) once per ticker -- one SELECT +
    # one session open/close per row, every pipeline cycle, purely for a
    # diagnostic display column. get_consecutive_bad_cycles_bulk() issues
    # exactly ONE query for the whole universe (the same windowed-query
    # technique get_excluded_symbols already used for the real auto-drop
    # decision), matching this function's own docstring, which already
    # promised "0"/no-history and "read failed" are behaviorally identical
    # here -- a missing dict key defaults to 0.0 via fillna, same floor
    # get_consecutive_bad_cycles itself returns for either case.
    symbols_upper = dashboard_df['Symbol'].astype(str).str.upper()
    cycles_by_symbol = store.get_consecutive_bad_cycles_bulk(symbols_upper.unique().tolist())
    dashboard_df['Symbol_Rating_Consecutive_Bad_Cycles'] = (
        symbols_upper.map(cycles_by_symbol).astype(float).fillna(0.0)
    )

    # Vectorized (was dashboard_df.apply(_excluded, axis=1)). NaN-safe by
    # construction: pandas' `NaN > 0` and `NaN.notna()`-guarded comparisons
    # both evaluate False the same way the original per-row `float(x) or 0.0`
    # / `pd.notna(cycles)` guards did -- no behavior change, see the PR
    # description for the full equivalence argument.
    shares_col = (
        dashboard_df["Robinhood Shares"]
        if "Robinhood Shares" in dashboard_df.columns
        else pd.Series(0.0, index=dashboard_df.index)
    )
    is_held = pd.to_numeric(shares_col, errors="coerce").fillna(0.0) > 0
    cycles = dashboard_df['Symbol_Rating_Consecutive_Bad_Cycles']
    dashboard_df['Symbol_Rating_Excluded'] = np.where(
        is_held, "No", np.where(cycles >= threshold, "Yes", "No")
    )


def _apply_sector_heat_factor(dashboard_df: pd.DataFrame) -> None:
    """Compute the GDELT-based "Sector Heat Factor" once per distinct SECTOR
    present in ``dashboard_df`` and map it onto every ticker row via its
    ``sector`` column -- NOT one GDELT query per ticker (see
    data/sentiment_sources.py::compute_sector_heat_factors's rate-limit
    framing).

    NaN-fills the column FIRST (same pattern as the Value_Z/Quality_Z/etc
    multifactor writeback and Credibility_Weighted_Sentiment blocks above)
    so every exit path -- disabled gate, empty universe, total failure, or a
    ticker whose sector never got a heat value -- leaves genuinely-missing
    cells NaN rather than a fabricated default (CONSTRAINT #4). Never raises
    (CONSTRAINT #6): a computation failure degrades the whole column back to
    NaN instead of aborting the pipeline.

    Deliberately a module-level function (not inlined in StrategyEvalStep.run)
    so it stays importable/testable without pulling in main_orchestrator's
    heavy top-level import chain -- the only imports here are pd (already a
    module-level import) and a single lazy import of
    data.sentiment_sources.compute_sector_heat_factors. Logs via this
    module's own plain `logger` rather than the `telemetry` proxy used
    elsewhere in this file -- `telemetry.__getattr__` lazily imports
    `main_orchestrator` (and therefore its whole heavy engine chain) on
    first attribute access, which would defeat the point of keeping this
    function's own import footprint light.
    """
    dashboard_df['Sector_Heat_Factor'] = float('nan')
    if not settings.SECTOR_HEAT_ENABLED:
        return
    try:
        from data.sentiment_sources import compute_sector_heat_factors

        if 'sector' not in dashboard_df.columns:
            return
        sectors = sorted({
            str(s).strip() for s in dashboard_df['sector'].dropna().unique()
            if s and str(s).strip() and str(s).strip().lower() != "unknown"
        })
        if not sectors:
            return
        sector_heat_map = compute_sector_heat_factors(sectors)
        if not sector_heat_map:
            return
        dashboard_df['Sector_Heat_Factor'] = dashboard_df['sector'].map(sector_heat_map)
    except Exception as exc:
        logger.warning("Sector Heat Factor computation failed (non-fatal): %s", exc)
        dashboard_df['Sector_Heat_Factor'] = float('nan')


def _apply_google_trends_asvi(dashboard_df: pd.DataFrame) -> None:
    """Compute Google Trends Abnormal Search Volume Index (ASVI) for each ticker.
    
    NaN-fills the column FIRST so every exit path leaves genuinely-missing
    cells NaN rather than a fabricated default. Never raises.
    """
    dashboard_df['Google_Trends_ASVI'] = float('nan')
    if not getattr(settings, "GOOGLE_TRENDS_ENABLED", False):
        return
    try:
        from data.trends_store import TrendsStore
        from data.trends_stitcher import ASVICalculator
        import time as _time

        if 'Symbol' not in dashboard_df.columns:
            return
        symbols = sorted({
            str(s).strip().upper() for s in dashboard_df['Symbol'].dropna()
            if str(s).strip()
        })
        if not symbols:
            return

        store = TrendsStore(readonly=True)
        asvi_map = {}

        max_seconds = max(0.0, float(settings.GOOGLE_TRENDS_MAX_SECONDS_PER_CYCLE))
        deadline = _time.monotonic() + max_seconds
        budget_exhausted = False

        for sym in symbols:
            if not budget_exhausted and _time.monotonic() >= deadline:
                budget_exhausted = True
                logger.warning(
                    "Google Trends ASVI: cycle time budget (%.0fs) reached; "
                    "remaining symbols served from NaN only this cycle.",
                    max_seconds,
                )

            if budget_exhausted:
                continue

            try:
                stitched_data = store.get_stitched_series(sym)
                if stitched_data:
                    dates = [d["date"] for d in stitched_data]
                    values = [float(d["value"]) for d in stitched_data]
                    svi_series = pd.Series(values, index=dates)
                    
                    asvi_series = ASVICalculator.compute_asvi(svi_series)
                    if not asvi_series.empty:
                        valid_asvi = asvi_series.dropna()
                        if not valid_asvi.empty:
                            asvi_map[sym] = float(valid_asvi.iloc[-1])
            except Exception as e:
                logger.warning("Google Trends ASVI failed for %s: %s", sym, e)

        if asvi_map:
            dashboard_df['Google_Trends_ASVI'] = dashboard_df['Symbol'].str.upper().map(asvi_map)
    except Exception as exc:
        logger.warning("Google Trends ASVI computation failed (non-fatal): %s", exc)
        dashboard_df['Google_Trends_ASVI'] = float('nan')


def _apply_sector_selection(dashboard_df: pd.DataFrame) -> None:
    """Compute and durably persist each tracked symbol's semantic Related
    Sector Selection ranking (``sector_selection_engine.run_sector_selection``)
    so the webapp's Sector Selection screen (``GET /sector/selection``,
    ``pilots/sector_selection.py``) has something to read.

    Before this function existed, nothing in either orchestrator ever called
    ``run_sector_selection`` -- ``data/sector_correlation_store.py``'s
    ``sector_correlations`` table was never written in production, so the
    screen always rendered its honest-but-permanent "nothing computed yet"
    empty state for every symbol regardless of ``SECTOR_SELECTION_ENABLED``.
    See ``docs/signals/sector_selection.md``.

    No-op (CONSTRAINT #6: never raises, no side effect) when
    ``settings.SECTOR_SELECTION_ENABLED`` is False -- mirrors
    ``run_sector_selection``'s own gate, checked again here so this function
    never even constructs a ``HistoricalStore``/``SectorCorrelationStore``
    when the feature is off.

    Only recomputes a symbol whose most-recently-persisted ``as_of`` isn't
    TODAY's trading day: ``SectorCorrelationStore.record_correlations()``
    appends rows with no de-dup, so calling this every cycle under
    ``main.py --interval N`` without this check would insert duplicate
    per-sector rows for the same day on every pass. After the first cycle of
    a trading day this degrades to one cheap ``get_latest()`` read per
    symbol and zero SBERT/heat computation.

    Deliberately a module-level function (same pattern as
    ``_apply_sector_heat_factor`` above): stays importable/testable without
    ``main_orchestrator``'s heavy top-level import chain, and logs via this
    module's own plain ``logger`` rather than the ``telemetry`` proxy (whose
    ``__getattr__`` lazily imports ``main_orchestrator`` on first attribute
    access, defeating the light import footprint).
    """
    if not settings.SECTOR_SELECTION_ENABLED:
        return
    if dashboard_df is None or dashboard_df.empty or 'Symbol' not in dashboard_df.columns:
        return
    try:
        from sector_selection_engine import run_sector_selection, _build_correlation_store
        from data.historical_store import HistoricalStore

        symbols = sorted({
            str(s).strip().upper() for s in dashboard_df['Symbol'].dropna()
            if str(s).strip()
        })
        if not symbols:
            return

        correlation_store = _build_correlation_store()
        today = HistoricalStore.resolve_trading_day(datetime.now(timezone.utc))

        stale_targets = []
        for sym in symbols:
            latest = correlation_store.get_latest(sym)
            if not latest or latest[0].get("as_of") != today:
                stale_targets.append(sym)
        if not stale_targets:
            return

        run_sector_selection(stale_targets, correlation_store=correlation_store)
    except Exception as exc:
        logger.warning("Sector Selection computation failed (non-fatal): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Financial Modeling Prep diagnostic feeds
#
# Four writers for the eight FMP columns in config.COLUMN_SCHEMA's "FMP
# DIAGNOSTIC FEEDS" section. All four follow the ``_apply_sector_heat_factor``
# template above EXACTLY, and the ordering discipline is load-bearing:
#
#   1. NaN-fill the columns FIRST, before ANY branch. Every early return --
#      gate off, empty universe, missing column, total failure -- must leave
#      genuinely-missing cells NaN rather than a fabricated default
#      (CONSTRAINT #4), and the only way to guarantee that across every exit
#      path is to write the NaNs before the first `if`.
#   2. Then the settings gate, read via ``getattr(settings, ..., False)``.
#   3. Then a LAZY import of the feed module inside the try block.
#   4. Plain module ``logger``, never the ``telemetry`` proxy -- ``telemetry
#      .__getattr__`` lazily imports ``main_orchestrator`` (and its whole heavy
#      engine chain) on first attribute access, defeating the light import
#      footprint that makes these functions unit-testable in isolation.
#   5. ``except Exception`` that RE-NaNs the columns. Never raises
#      (CONSTRAINT #6): a feed failure degrades the column, not the cycle.
#
# **Diagnostic only, structurally.** No SignalModule reads any of these
# columns, none has a SIGNAL_WEIGHTS entry, and none enters dto_models.py or
# signals/ -- see the COLUMN_SCHEMA section comment for why that is also the
# no-lookahead guarantee mechanism for this whole series.
#
# As of this commit these are COMPLETE, WORKING NO-OPS: every gate defaults
# False, so each function NaN-fills and returns having performed zero I/O.
# The fetch/populate logic lands in wave 1 at the marked TODO in each body.
# ─────────────────────────────────────────────────────────────────────────────

_FMP_ANALYST_COLUMNS = (
    'Analyst_Target_Consensus',
    'Analyst_Target_Upside',
    'Analyst_Grade_Score',
)

_FMP_EARNINGS_COLUMNS = (
    'Days_To_Earnings',
    'Last_EPS_Surprise_Pct',
)

_FMP_INSIDER_COLUMNS = (
    'Insider_Buy_Sell_Ratio',
)

_FMP_SECTOR_COLUMNS = (
    'Sector_PE',
    'Sector_1D_Change',
)

_FMP_ECON_CALENDAR_COLUMNS = (
    'Next_Macro_Event',
    'Next_Macro_Event_Date',
)

# Minimum per-feed reservation (seconds) the shared FMP budget tries to
# guarantee each per-symbol diagnostic feed (analyst/earnings/insider) --
# see _fmp_next_feed_deadline's docstring for the full contract.
FMP_FEED_MIN_RESERVATION_SECONDS = 15.0


def _fmp_next_feed_deadline(
    total_deadline: float, min_reservation: float, feeds_left: int
) -> float:
    """Compute ONE FMP per-symbol diagnostic feed's own sub-deadline out of
    the shared per-cycle wall-clock budget (``settings.FMP_MAX_SECONDS_PER_CYCLE``).

    Call this fresh, immediately before each feed runs, with ``feeds_left``
    counting the feed about to run plus every feed still queued behind it
    (3 before analyst, 2 before earnings, 1 before insider). This is the
    single choke point every feed's sub-deadline goes through, so the
    guarantee below applies uniformly -- no feed is special-cased to reuse
    the raw total deadline unprotected, which is what silently starved the
    LAST-queued feed to zero before this fix (an earlier feed's floor claim
    could exhaust the whole remaining budget, and the last feed had no
    floor of its own to fall back on).

    Two regimes, chosen based on whether the remaining budget can actually
    support every still-queued feed getting its floor:

    - **Plenty left** (``remaining >= min_reservation * feeds_left``): this
      feed may take up to its even fair share (``remaining / feeds_left``)
      or the floor, whichever is larger -- but is capped so it can never
      eat into the floor reserved for the feeds still queued behind it
      (``remaining - min_reservation * (feeds_left - 1)``). This is what
      guarantees every later feed still gets at least its own floor.
    - **Budget too small for everyone's floor**: degrades to a plain even
      split (``remaining / feeds_left``) so no single feed can monopolize
      what's left. Both regimes reduce to giving the LAST feed
      (``feeds_left == 1``) 100% of whatever genuinely remains, by
      construction -- there is nothing left to reserve for after it.

    If the shared budget is already fully exhausted by the time this is
    called (``remaining <= 0``), every feed correctly gets a deadline of
    "now" (0 share) -- an honest wall-clock-ceiling-reached degradation
    (CONSTRAINT #6 style), not the unfair zero-while-budget-remains
    starvation this function replaces.
    """
    import time as _time

    remaining = max(0.0, total_deadline - _time.monotonic())
    if feeds_left <= 0:
        return total_deadline
    if remaining >= min_reservation * feeds_left:
        share = min(
            max(remaining / feeds_left, min_reservation),
            remaining - min_reservation * (feeds_left - 1),
        )
    else:
        share = remaining / feeds_left
    return min(_time.monotonic() + share, total_deadline)


def _apply_fmp_analyst(dashboard_df: pd.DataFrame, deadline: Optional[float] = None) -> None:
    """Populate the three FMP analyst-consensus columns.

    ``Analyst_Target_Consensus`` (currency), ``Analyst_Target_Upside``
    (percent, consensus vs. current Price), ``Analyst_Grade_Score`` (number).
    Sources: FMP ``/price-target-consensus`` and ``/grades-summary``, on a
    ``settings.FMP_ANALYST_REFRESH_HOURS`` (24h) cadence so a steady-state
    cycle spends zero requests here.

    **NO POINT-IN-TIME GUARANTEE, and that is enforced structurally rather
    than by convention:** FMP serves only the CURRENT consensus, and price
    targets get revised, so a target read today for a past date is the
    post-revision number and not what the market saw. Nothing may promote
    these columns into ``dto_models.py`` or ``signals/`` until
    ``HistoricalStore.analyst_history`` has accumulated its own real forward
    archive (see that table's DDL comment).

    NaN on every failure path, never 0.0 (CONSTRAINT #4) -- "no analyst
    coverage" and "a consensus target of zero" are different facts. Never
    raises (CONSTRAINT #6).
    """
    for col in _FMP_ANALYST_COLUMNS:
        dashboard_df[col] = float('nan')

    if not getattr(settings, "FMP_ANALYST_ENABLED", False):
        return
    if dashboard_df.empty or 'Symbol' not in dashboard_df.columns:
        return

    try:
        import time as _time

        from data.fmp_feeds_company import fetch_analyst_snapshot
        from data.historical_store import HistoricalStore

        store = HistoricalStore()
        refresh_hours = float(getattr(settings, "FMP_ANALYST_REFRESH_HOURS", 24) or 24)
        raw_budget = getattr(settings, "FMP_MAX_SECONDS_PER_CYCLE", None)
        max_seconds = max(0.0, float(120.0 if raw_budget is None else raw_budget))
        if deadline is None:
            deadline = _time.monotonic() + max_seconds

        symbols = sorted({
            str(s).strip().upper() for s in dashboard_df['Symbol'].dropna()
            if str(s).strip()
        })
        if not symbols:
            return

        today_str = datetime.now(timezone.utc).date().isoformat()

        def _hours_since_as_of(as_of_str: str) -> Optional[float]:
            try:
                as_of_date = datetime.strptime(str(as_of_str)[:10], "%Y-%m-%d")
            except (TypeError, ValueError):
                return None
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            return (now_naive - as_of_date).total_seconds() / 3600.0

        consensus_map: dict[str, float] = {}
        grade_map: dict[str, float] = {}
        budget_exhausted = False

        for sym in symbols:
            if not budget_exhausted and _time.monotonic() >= deadline:
                budget_exhausted = True
                logger.warning(
                    "FMP analyst feed: cycle time budget (%.0fs) reached; "
                    "remaining symbols served from archive/NaN only this cycle.",
                    max_seconds,
                )

            latest_as_of = store.latest_analyst_as_of(sym)
            age_hours = _hours_since_as_of(latest_as_of) if latest_as_of else None
            fresh_enough = age_hours is not None and age_hours < refresh_hours

            if not fresh_enough and not budget_exhausted:
                fetched = fetch_analyst_snapshot(sym)
                if fetched:
                    store.upsert_analyst_snapshot(
                        sym,
                        today_str,
                        target_consensus=fetched.get("target_consensus"),
                        target_median=fetched.get("target_median"),
                        target_high=fetched.get("target_high"),
                        target_low=fetched.get("target_low"),
                        grade_score=fetched.get("grade_score"),
                        source=fetched.get("source", "fmp"),
                    )
                else:
                    # Persist a no-data sentinel so fresh_enough evaluates
                    # True on subsequent cycles — prevents endlessly
                    # re-fetching symbols with no analyst coverage.
                    store.upsert_analyst_snapshot(
                        sym, today_str, source="fmp-no-data",
                    )

            snapshot = store.get_analyst_snapshot(sym)
            if not snapshot:
                continue
            tc = snapshot.get("target_consensus")
            if tc is not None:
                consensus_map[sym] = float(tc)
            gs = snapshot.get("grade_score")
            if gs is not None:
                grade_map[sym] = float(gs)

        upper_symbol = dashboard_df['Symbol'].astype(str).str.upper().str.strip()
        dashboard_df['Analyst_Target_Consensus'] = upper_symbol.map(consensus_map)
        dashboard_df['Analyst_Grade_Score'] = upper_symbol.map(grade_map)

        if 'Price' in dashboard_df.columns:
            def _upside(sym: str, price: Any) -> float:
                tc = consensus_map.get(sym)
                if tc is None:
                    return float('nan')
                try:
                    price_f = float(price)
                except (TypeError, ValueError):
                    return float('nan')
                if price_f <= 0:
                    return float('nan')
                return (tc / price_f) - 1.0

            dashboard_df['Analyst_Target_Upside'] = [
                _upside(sym, price)
                for sym, price in zip(upper_symbol, dashboard_df['Price'])
            ]
    except Exception as exc:
        logger.warning("FMP analyst feed failed (non-fatal): %s", exc)
        for col in _FMP_ANALYST_COLUMNS:
            dashboard_df[col] = float('nan')


def _apply_fmp_earnings(dashboard_df: pd.DataFrame, deadline: Optional[float] = None) -> None:
    """Populate the FMP earnings columns.

    ``Days_To_Earnings`` (number) and ``Last_EPS_Surprise_Pct`` (percent) are
    new; the EXISTING ``Earnings_Date`` column is ALSO written when this gate
    is on, making FMP a SECOND source for it alongside Finnhub's news-catalyst
    path (and unlike Finnhub, FMP is not limited to a 30-day forward window).
    That column is deliberately shared rather than duplicated -- see
    config.COLUMN_SCHEMA's FMP section.

    **Why a future-dated row is not lookahead.** A scheduled earnings DATE is
    publicly announced in advance, so knowing it is legitimate; knowing the
    RESULT is not. The four rules that keep those apart (each with a test in
    the wave-1 feed module, and restated on the ``earnings_events`` DDL):

      1. A row counts as "actual" IFF ``eps_actual`` is not None. NULL is
         never read as 0.0 -- that would turn an unreported quarter into a
         100% miss (CONSTRAINT #4).
      2. ``Last_EPS_Surprise_Pct`` uses only rows with ``event_date <= as_of``
         AND a non-null actual -- BOTH, so a vendor bug populating an actual
         on a future row cannot slip through the date filter alone.
      3. ``Days_To_Earnings`` / the next date come from ``event_date > as_of``.
         Deliberate. Do not "fix" this later.
      4. The vendor's ``lastUpdated`` is persisted verbatim to make a future
         PIT replay possible -- an IMPERFECT defense, not a guarantee (a
         backfilled actual with a stale stamp defeats it).

    Never raises (CONSTRAINT #6); every failure path leaves NaN.
    """
    for col in _FMP_EARNINGS_COLUMNS:
        dashboard_df[col] = float('nan')

    if not getattr(settings, "FMP_EARNINGS_ENABLED", False):
        return
    if dashboard_df.empty or 'Symbol' not in dashboard_df.columns:
        return

    try:
        import time as _time

        from data.fmp_feeds_company import fetch_earnings_rows
        from data.historical_store import HistoricalStore

        store = HistoricalStore()
        refresh_hours = float(getattr(settings, "FMP_EARNINGS_REFRESH_HOURS", 12) or 12)
        raw_budget = getattr(settings, "FMP_MAX_SECONDS_PER_CYCLE", None)
        max_seconds = max(0.0, float(120.0 if raw_budget is None else raw_budget))
        if deadline is None:
            deadline = _time.monotonic() + max_seconds

        symbols = sorted({
            str(s).strip().upper() for s in dashboard_df['Symbol'].dropna()
            if str(s).strip()
        })
        if not symbols:
            return

        as_of = datetime.now(timezone.utc).date().isoformat()

        def _hours_since_fetched_at(fetched_at_str: str) -> Optional[float]:
            try:
                fetched_dt = datetime.fromisoformat(str(fetched_at_str))
            except (TypeError, ValueError):
                return None
            if fetched_dt.tzinfo is None:
                now_cmp = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                now_cmp = datetime.now(timezone.utc)
            return (now_cmp - fetched_dt).total_seconds() / 3600.0

        days_map: dict[str, float] = {}
        surprise_map: dict[str, float] = {}
        next_date_map: dict[str, str] = {}
        budget_exhausted = False

        for sym in symbols:
            if not budget_exhausted and _time.monotonic() >= deadline:
                budget_exhausted = True
                logger.warning(
                    "FMP earnings feed: cycle time budget (%.0fs) reached; "
                    "remaining symbols served from archive/NaN only this cycle.",
                    max_seconds,
                )

            latest_fetched_at = store.latest_earnings_fetched_at(sym)
            age_hours = (
                _hours_since_fetched_at(latest_fetched_at) if latest_fetched_at else None
            )
            fresh_enough = age_hours is not None and age_hours < refresh_hours

            if not fresh_enough and not budget_exhausted:
                rows = fetch_earnings_rows(sym)
                if rows:
                    store.upsert_earnings_events(rows)
                else:
                    # Persist a no-data sentinel so fresh_enough evaluates
                    # True on subsequent cycles — prevents endlessly
                    # re-fetching symbols with no earnings data.
                    store.mark_earnings_fetched(sym)

            # Trailing surprise: rules 1 + 2 -- event_date <= as_of AND a
            # non-null actual, BOTH filters, so a vendor bug populating an
            # actual on a future row cannot slip through the date filter
            # alone. Never treat a null actual as 0.0 (CONSTRAINT #4).
            past_rows = store.get_earnings_events(
                sym, on_or_before=as_of, actuals_only=True, limit=1,
            )
            if past_rows:
                row = past_rows[0]
                eps_actual = row.get("eps_actual")
                eps_estimated = row.get("eps_estimated")
                if eps_actual is not None and eps_estimated not in (None, 0, 0.0):
                    try:
                        surprise_map[sym] = (
                            (float(eps_actual) - float(eps_estimated))
                            / abs(float(eps_estimated))
                        )
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass

            # Next scheduled date: rule 3 -- event_date > as_of. A publicly
            # announced future date is not lookahead; the RESULT would be.
            future_rows = store.get_earnings_events(sym, after=as_of, limit=1)
            if future_rows:
                row = future_rows[0]
                event_date = row.get("event_date")
                if event_date and not str(event_date).startswith("1900"):
                    next_date_map[sym] = str(event_date)
                    try:
                        d_next = datetime.strptime(str(event_date)[:10], "%Y-%m-%d").date()
                        d_as_of = datetime.strptime(as_of, "%Y-%m-%d").date()
                        days_map[sym] = float((d_next - d_as_of).days)
                    except (TypeError, ValueError):
                        pass

        upper_symbol = dashboard_df['Symbol'].astype(str).str.upper().str.strip()
        dashboard_df['Days_To_Earnings'] = upper_symbol.map(days_map)
        dashboard_df['Last_EPS_Surprise_Pct'] = upper_symbol.map(surprise_map)

        # Rule 4 / the shared-column discipline: only overwrite 'Earnings_Date'
        # for symbols FMP actually covered (a real next-event date) this
        # cycle -- a NaN/absent FMP row must never blank a date the Finnhub
        # news-catalyst write-back already resolved earlier in this same
        # cycle. If the column doesn't exist yet (unit tests calling this
        # function standalone), there is nothing to preserve or overwrite.
        if next_date_map and 'Earnings_Date' in dashboard_df.columns:
            dashboard_df['Earnings_Date'] = [
                next_date_map.get(sym, current)
                for sym, current in zip(upper_symbol, dashboard_df['Earnings_Date'])
            ]
    except Exception as exc:
        logger.warning("FMP earnings feed failed (non-fatal): %s", exc)
        for col in _FMP_EARNINGS_COLUMNS:
            dashboard_df[col] = float('nan')


def _apply_fmp_insider(dashboard_df: pd.DataFrame, deadline: Optional[float] = None) -> None:
    """Populate ``Insider_Buy_Sell_Ratio`` from FMP insider-trading statistics.

    Source: ``/insider-trading/statistics``, quarterly aggregates keyed
    ``(symbol, year, quarter)``, on a ``settings.FMP_INSIDER_REFRESH_DAYS``
    (7d) cadence.

    **The leakage trap here is not the date filter.** A quarter's aggregate
    KEEPS CHANGING after the quarter ends, because Form 4s continue to land
    (late filings, amendments) for weeks afterwards. Reading the most recent
    quarter therefore reads a number that did not exist in that form at the
    time. The consumer must apply a minimum-lag filter -- only consume a
    quarter that ended at least ``settings.FMP_INSIDER_MIN_LAG_DAYS`` (default
    45) days ago -- rather than simply taking the latest stored quarter.
    ``HistoricalStore.get_insider_stats`` deliberately does NOT apply that
    filter itself (a storage helper that silently dropped rows would make the
    archive un-auditable); it belongs here.

    That 45 is a conservative judgment call, not a constant derived from any
    SEC rule. A symbol with no sufficiently-lagged quarter gets NaN, never a
    fabricated ratio. Never raises (CONSTRAINT #6).

    Wall-clock budget: ``settings.FMP_MAX_SECONDS_PER_CYCLE`` bounds the
    whole per-symbol loop (measured via ``time.monotonic()``, matching the
    ``legacy/data/etf_holdings.py`` precedent). Once the budget is spent the loop
    stops outright and every symbol not yet reached that cycle stays NaN --
    an honest gap, never a fabricated value.
    """
    for col in _FMP_INSIDER_COLUMNS:
        dashboard_df[col] = float('nan')

    if not getattr(settings, "FMP_INSIDER_ENABLED", False):
        return
    if dashboard_df.empty or 'Symbol' not in dashboard_df.columns:
        return

    try:
        import time as _time
        from datetime import date as _date

        from data.fmp_feeds_market import fetch_insider_stats
        from data.historical_store import HistoricalStore

        symbols = sorted({
            str(s).strip().upper() for s in dashboard_df['Symbol'].dropna()
            if str(s).strip()
        })
        if not symbols:
            return

        store = HistoricalStore()
        refresh_days = int(getattr(settings, "FMP_INSIDER_REFRESH_DAYS", 7) or 7)
        min_lag_days = int(getattr(settings, "FMP_INSIDER_MIN_LAG_DAYS", 45) or 45)
        max_seconds = float(getattr(settings, "FMP_MAX_SECONDS_PER_CYCLE", 120.0) or 120.0)
        # (month, day) of the last calendar day of each fiscal quarter --
        # the causal anchor the minimum-lag filter measures from.
        quarter_end = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}

        def _to_int(value: Any) -> Optional[int]:
            if value is None:
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        as_of = _date.today()
        if deadline is None:
            deadline = _time.monotonic() + max_seconds
        ratio_map: dict = {}

        for sym in symbols:
            if _time.monotonic() >= deadline:
                logger.warning(
                    "FMP insider feed: wall-clock ceiling (%.0fs) reached "
                    "after %d/%d symbols; remaining symbols stay NaN this "
                    "cycle.", max_seconds, len(ratio_map), len(symbols),
                )
                break

            # ── Cadence gate (per symbol, in DAYS -- not the hour-based
            # cadence the analyst/earnings feeds use) ──────────────────────
            due = True
            try:
                last_fetched_str = store.latest_insider_fetched_at(sym)
            except Exception:
                last_fetched_str = None
            if last_fetched_str:
                try:
                    last_fetched = datetime.fromisoformat(str(last_fetched_str))
                    if last_fetched.tzinfo is None:
                        last_fetched = last_fetched.replace(tzinfo=timezone.utc)
                    age_days = (
                        datetime.now(timezone.utc) - last_fetched
                    ).total_seconds() / 86400.0
                    due = age_days >= refresh_days
                except Exception:
                    due = True

            if due:
                fetched_rows = fetch_insider_stats(sym)
                if fetched_rows:
                    store.upsert_insider_stats(fetched_rows)
                else:
                    # Persist a no-data sentinel so the cadence gate
                    # evaluates as fresh on subsequent cycles — prevents
                    # endlessly re-fetching symbols with no insider data.
                    store.mark_insider_fetched(sym)

            # ── Read back the FULL archive and apply the minimum-lag filter
            # ourselves -- get_insider_stats() deliberately does not, so the
            # archive stays a complete, auditable record of what was
            # fetched. Rows come back newest-quarter-first, so the first row
            # whose quarter ended >= min_lag_days ago IS the most recent
            # surviving quarter. ──────────────────────────────────────────
            stored_rows = store.get_insider_stats(sym)
            for row in stored_rows:
                month_day = quarter_end.get(_to_int(row.get('quarter')))
                year = _to_int(row.get('year'))
                if month_day is None or year is None:
                    continue
                try:
                    q_end = _date(year, month_day[0], month_day[1])
                except (TypeError, ValueError):
                    continue
                if (as_of - q_end).days >= min_lag_days:
                    ratio = row.get('acquired_disposed_ratio')
                    ratio_map[sym] = float(ratio) if ratio is not None else float('nan')
                    break
            else:
                ratio_map[sym] = float('nan')

        _upper = dashboard_df['Symbol'].astype(str).str.upper().str.strip()
        dashboard_df['Insider_Buy_Sell_Ratio'] = _upper.map(ratio_map)
    except Exception as exc:
        logger.warning("FMP insider feed failed (non-fatal): %s", exc)
        for col in _FMP_INSIDER_COLUMNS:
            dashboard_df[col] = float('nan')


def _apply_fmp_sector(dashboard_df: pd.DataFrame) -> None:
    """Populate ``Sector_PE`` and ``Sector_1D_Change`` from FMP sector snapshots.

    Sources: ``/sector-pe-snapshot`` and ``/sector-performance-snapshot`` --
    2 requests per CYCLE total for the whole universe, not per symbol, which
    is why this carries its own settings gate separate from the per-symbol
    insider feed. Values are mapped onto every ticker row via its ``sector``
    column, exactly like ``_apply_sector_heat_factor`` above.

    **The one new feed with a real point-in-time story.** Both endpoints are
    DATE-PARAMETERIZED, so a dated request returns that date's figures rather
    than today's; the dated form must ALWAYS be used, and the stored ``date``
    is the source's own snapshot date, never the fetch time. That makes this
    the only plausible future signal candidate of the four -- but it is still
    diagnostic-only in v1, because "could be backtested in principle" is not
    the same as "has accumulated history and has been".

    A symbol whose ``sector`` is missing/unknown, or a sector the snapshot did
    not cover, gets NaN -- never a universe-average stand-in. Never raises
    (CONSTRAINT #6).

    **Cadence gate, and the setting that does not exist.** This feed is
    cycle-wide (2 requests total), so it is gated ONCE per cycle via
    ``HistoricalStore.latest_sector_snapshot_date()`` rather than per symbol.
    There is no dedicated ``FMP_SECTOR_*_REFRESH_*`` setting in this series --
    this function was written by an agent that does not own ``settings.py``
    and is not authorized to add one -- so the cadence used here is a fixed
    "once per calendar day": if the most recent stored snapshot date is not
    TODAY, fetch; otherwise read the archive only. This is a deliberate
    substitute for a missing setting, not a discovered constant, and it should
    be promoted to a real ``FMP_SECTOR_SNAPSHOT_REFRESH_HOURS`` setting if an
    operator ever wants intraday sector-snapshot refreshes.
    """
    for col in _FMP_SECTOR_COLUMNS:
        dashboard_df[col] = float('nan')

    if not getattr(settings, "FMP_SECTOR_SNAPSHOT_ENABLED", False):
        return
    if dashboard_df.empty or 'sector' not in dashboard_df.columns:
        return

    try:
        from datetime import date as _date

        from data.fmp_feeds_market import fetch_sector_snapshot
        from data.historical_store import HistoricalStore

        store = HistoricalStore()
        today_str = _date.today().isoformat()

        # Cadence gate ONCE per cycle (not per symbol) -- see the docstring
        # for why "once per calendar day" rather than a settings-driven
        # refresh window.
        latest_date = store.latest_sector_snapshot_date()
        if latest_date != today_str:
            fetched_rows = fetch_sector_snapshot(today_str)
            if fetched_rows:
                store.upsert_sector_snapshots(fetched_rows)

        snapshot_map = store.get_sector_snapshots(as_of=today_str)
        if not snapshot_map:
            return

        def _to_float(value: Any) -> float:
            if value is None:
                return float('nan')
            try:
                return float(value)
            except (TypeError, ValueError):
                return float('nan')

        pe_by_sector = {
            sector: _to_float(data.get('pe')) for sector, data in snapshot_map.items()
        }
        change_by_sector = {
            sector: _to_float(data.get('change_pct'))
            for sector, data in snapshot_map.items()
        }

        # Exactly the _apply_sector_heat_factor write-back idiom: map the
        # existing 'sector' column through a {sector: value} dict. A symbol
        # whose sector is missing/unknown, or not present in the snapshot,
        # is not a key in either dict and .map() naturally leaves it NaN
        # (CONSTRAINT #4) -- never a universe-average or neighboring-sector
        # stand-in.
        dashboard_df['Sector_PE'] = dashboard_df['sector'].map(pe_by_sector)
        dashboard_df['Sector_1D_Change'] = dashboard_df['sector'].map(change_by_sector)
    except Exception as exc:
        logger.warning("FMP sector snapshot feed failed (non-fatal): %s", exc)
        for col in _FMP_SECTOR_COLUMNS:
            dashboard_df[col] = float('nan')


def _apply_fmp_econ_calendar(dashboard_df: pd.DataFrame) -> None:
    """Populate ``Next_Macro_Event`` and ``Next_Macro_Event_Date`` from FMP economics calendar.

    Source: ``/economics-calendar`` via :func:`data.fmp_feeds_market.fetch_economics_calendar`.
    1 request per CYCLE total (broadcast to all tickers). Diagnostic only,
    never a SignalModule.

    CONSTRAINT #6: Never raises.
    """
    for col in _FMP_ECON_CALENDAR_COLUMNS:
        dashboard_df[col] = float('nan')

    if not getattr(settings, "FMP_ECON_CALENDAR_ENABLED", False):
        return
    if dashboard_df.empty:
        return

    try:
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo
        from data.fmp_feeds_market import fetch_economics_calendar

        # Use US Eastern — FMP economic calendar dates are in ET, not UTC.
        # Using UTC would roll over to "tomorrow" during US evening hours,
        # incorrectly filtering out same-day events.
        today = datetime.now(ZoneInfo("America/New_York")).date()
        today_str = today.isoformat()
        # Bound the request to a 60-day lookahead window. Only the earliest
        # sorted event is ever consumed below, so this is defense-in-depth,
        # not a correctness fix -- FMP's own behavior for an unbounded `to`
        # was never confirmed, and this feed defaults ON, so an explicit cap
        # avoids depending on an unverified vendor default.
        to_date_str = (today + timedelta(days=60)).isoformat()
        events = fetch_economics_calendar(from_date=today_str, to_date=to_date_str)
        if not events:
            return

        # Filter for upcoming events (date >= today)
        valid_events = []
        for ev in events:
            ev_date = str(ev.get("date") or "")[:10]
            if ev_date >= today_str:
                valid_events.append(ev)

        if not valid_events:
            return

        # Sort ascending by date
        valid_events.sort(key=lambda x: str(x.get("date") or ""))

        # Look for US / High impact events first, falling back to first upcoming event
        us_high = [
            e for e in valid_events
            if str(e.get("country", "")).upper() == "US" and str(e.get("impact", "")).lower() == "high"
        ]
        high_impact = [e for e in valid_events if str(e.get("impact", "")).lower() == "high"]
        selected = us_high[0] if us_high else (high_impact[0] if high_impact else valid_events[0])

        event_name = selected.get("event")
        event_date = selected.get("date")

        if event_name and event_date:
            dashboard_df['Next_Macro_Event'] = str(event_name)
            dashboard_df['Next_Macro_Event_Date'] = str(event_date)[:10]
    except Exception as exc:
        logger.warning("FMP economics calendar feed failed (non-fatal): %s", exc)
        for col in _FMP_ECON_CALENDAR_COLUMNS:
            dashboard_df[col] = float('nan')


def _apply_portfolio_gross_cap(dashboard_df: pd.DataFrame) -> None:
    """Portfolio-level gross exposure cap (``settings.MAX_PORTFOLIO_GROSS``).

    Scales every name's ``Kelly Target`` uniformly via
    ``sizing.position_sizer.apply_portfolio_gross_cap`` (the sum-of-|weight|
    path, ``cov_matrix=None``) and, for each name whose weight actually
    moved, overrides its guardrail telemetry to ``"portfolio_gross"``.

    Split out of ``StrategyEvalStep.run()`` in step 4d. It used to share a
    ``try`` with the ETF-transmission covariance build, so any failure in
    that ETF code (including an ImportError once the module moved to
    legacy/) would have skipped this live risk limit. It now depends on
    nothing but ``sizing.position_sizer`` and settings.

    Failures propagate; the caller logs them. Tested directly in
    ``tests/test_production_steps_portfolio_gross_cap.py``.
    """
    from sizing.position_sizer import apply_portfolio_gross_cap

    per_name = dict(zip(dashboard_df["Symbol"], dashboard_df["Kelly Target"]))
    cap_result = apply_portfolio_gross_cap(
        per_name, max_gross=settings.MAX_PORTFOLIO_GROSS, cov_matrix=None,
    )
    if cap_result.was_capped:
        telemetry.info(
            "Portfolio gross cap bound this cycle: scale_factor=%.4f "
            "(max_gross=%.2f, method=%s).",
            cap_result.scale_factor, settings.MAX_PORTFOLIO_GROSS, cap_result.method,
        )
        dashboard_df["Kelly Target"] = dashboard_df["Symbol"].map(
            lambda x: cap_result.scaled_weights.get(x, per_name.get(x, 0.0))
        )
        # Only mark names whose weight actually moved (a 0.0 name is
        # trivially unaffected by a uniform scalar) -- avoids fabricating
        # a "capped" flag on a name that never had exposure to cap.
        _affected = dashboard_df["Symbol"].map(lambda x: abs(per_name.get(x, 0.0)) > 1e-9)
        dashboard_df.loc[_affected, "Sizing_Was_Capped"] = "Yes"
        dashboard_df.loc[_affected, "Sizing_Binding_Constraint"] = "portfolio_gross"


def _compute_xsec_momentum(
    tech_raw: dict,
    skip_days: int = 22,
    lookback_days: int = 252,
) -> tuple[dict, "pd.Series"]:
    """Single source for both the raw 12-1m cross-sectional momentum returns
    AND their percentile ranks (Finding 15 fix).

    Before this fix, StrategyEvalStep computed the percentile ranks via
    main_orchestrator.compute_xsec_momentum_ranks() and SEPARATELY
    re-derived the raw XSec_12_1M return with its own hand-inlined loop
    using hardcoded -23/-253 iloc offsets -- two independent
    implementations of the exact same Jegadeesh-Titman 12-1m formula that
    could silently diverge if skip_days/lookback_days were ever changed in
    only one of the two places. This helper computes both from ONE pass
    over ``tech_raw`` so ``XSec_12_1M`` and ``XSec_Momentum_Rank`` can never
    disagree about which tickers/returns they're built from.

    Mirrors main_orchestrator.compute_xsec_momentum_ranks()'s exact
    formula, defaults, and insufficient-history exclusion -- duplicated
    here (not imported) because this fix's file scope is deliberately
    restricted to pipeline/production_steps.py.

    NOTE: this formula is actually hand-duplicated a THIRD time, in
    pipeline/advisory_inputs.py::build_context_extras (its own inline copy,
    for the advisory path -- search for ``SKIP_DAYS = 22``). Keep all three
    (main_orchestrator.py::compute_xsec_momentum_ranks, this function, and
    advisory_inputs.build_context_extras) in lockstep if skip_days/lookback_days
    ever change in any one of them. This is no longer just a hand-maintained
    comment: tests/test_xsec_momentum_advisory_parity.py numerically
    cross-checks all three at their shared default constants and will fail
    CI the moment one of them drifts from the other two (tests/test_xsec_
    momentum.py separately covers the first two, including custom
    skip_days/lookback_days).

    Parameters
    ----------
    tech_raw : dict[str, pd.DataFrame]
        OHLCV DataFrames keyed by ticker.
    skip_days : int
        Trading days to skip at the end (default 22 ~= 1 month).
    lookback_days : int
        Total lookback window in trading days (default 252 ~= 12 months).

    Returns
    -------
    tuple[dict[str, float], pd.Series]
        (raw 12-1m returns keyed by ticker, percentile rank Series in
        [0, 1] indexed by ticker). Both share the exact same universe of
        eligible tickers (those with >= lookback_days + skip_days + 1
        valid Close observations).
    """
    returns: dict = {}
    required = lookback_days + skip_days + 1

    for ticker, df in tech_raw.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        close = df["Close"].dropna()
        if len(close) < required:
            continue
        p_recent = float(close.iloc[-(skip_days + 1)])   # price at t - skip_days
        p_old = float(close.iloc[-(lookback_days + 1)])  # price at t - lookback_days
        if p_old <= 0:
            continue
        returns[ticker] = p_recent / p_old - 1.0

    if not returns:
        return returns, pd.Series(dtype=float)

    ranks = pd.Series(returns).rank(pct=True, ascending=True)
    return returns, ranks


class StrategyEvalStep(PipelineStep):
    """Evaluates strategy and overlaying advisory logic."""
    name = "strategy"
    
    def run(self, ctx: RunContext) -> None:
        """Evaluate the strategy and apply the holding-aware advisory overlay."""
        from main_orchestrator import StrategyEngine, EvaluationEngine, MarketBarDTO, FundamentalDataDTO, global_registry, SignalContext, DualMomentumAllocator

        telemetry.info("Routing data through Strategy and Evaluation Engines...")
        if ctx.progress is not None:
            ctx.progress.start_stage("strategy", symbols_total=len(ctx.dashboard_df))

        # Populate the six paper_* ML-inference features on ctx.dashboard_df
        # as early as possible in this step -- BEFORE both of this cycle's
        # real consumers: (1) global_registry.run_pre_compute() below, which
        # reaches signals/lgbm_ranker.py::LGBMRankerSignal.pre_compute ->
        # build_pit_feature_matrix(universe_df=ctx.dashboard_df, ...) directly,
        # and (2) the PIT snapshot's `pit_df = ctx.dashboard_df.copy()`
        # further down, which would otherwise snapshot a copy taken before
        # these columns existed. Previously this call sat AFTER both
        # consumers (and inside settings.PIT_CAPTURE_ENABLED, a flag that
        # only controls whether today's PIT snapshot is written to disk for
        # future retrains -- see that field's own settings.py description --
        # and has nothing to do with live inference), making it a pure
        # train/serve-skew no-op: the columns it wrote were never read by
        # anything (CONSTRAINT #4). Dead-lettered (CONSTRAINT #6): a failure
        # here logs and never aborts the pipeline.
        pit_as_of = pd.Timestamp(datetime.now(timezone.utc)).normalize()
        try:
            from ml.training_data import populate_live_paper_features
            populate_live_paper_features(ctx.dashboard_df, pit_as_of)
        except Exception as exc:
            telemetry.warning(f"Failed to populate live paper features: {exc}")

        engines = ctx.engine_context
        se = (engines.strategy_engine if engines is not None and engines.strategy_engine is not None
              else StrategyEngine())
        ee = (engines.evaluation_engine if engines is not None and engines.evaluation_engine is not None
              else EvaluationEngine())

        ctx.dashboard_df['XSec_12_1M'] = float('nan')
        ctx.dashboard_df['XSec_Momentum_Rank'] = float('nan')

        # Finding 15: raw returns and percentile ranks are now sourced from
        # ONE helper (_compute_xsec_momentum) instead of two independent
        # formula implementations that could silently diverge.
        xsec_return_dict, xsec_rank_series = _compute_xsec_momentum(ctx.tech_raw)

        ctx.dashboard_df['XSec_12_1M'] = ctx.dashboard_df['Symbol'].map(xsec_return_dict)
        ctx.dashboard_df['XSec_Momentum_Rank'] = ctx.dashboard_df['Symbol'].map(xsec_rank_series)

        stub_bar = MarketBarDTO(datetime.now(), "__UNIVERSE__", 100.0, 100.0, 100.0, 100.0, 0)
        stub_fund = FundamentalDataDTO(
            ticker="__UNIVERSE__", pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
            book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
            payout_ratio=0.0, sector="Unknown", company_name="Unknown"
        )
        shared_context = SignalContext(
            bar=stub_bar,
            fundamentals=stub_fund,
            macro=ctx.macro_dto,
        )

        try:
            from ml.meta_bootstrap import bootstrap_meta_registry
            bootstrap_meta_registry()
        except Exception as meta_exc:
            telemetry.warning("Meta-labeler bootstrap failed (%s); continuing.", meta_exc)

        global_registry.run_pre_compute(ctx.dashboard_df, shared_context)
        ctx.context_extras["shared_context"] = shared_context

        # PIT snapshot capture
        if settings.PIT_CAPTURE_ENABLED:
            try:
                from ml.feature_engineering import build_pit_feature_matrix
                from ml.data.store import PITFeatureStore

                # pit_as_of/populate_live_paper_features already ran at the top
                # of this step's run() (before run_pre_compute() and before
                # this copy is taken) -- reusing the same timestamp here keeps
                # the written PIT snapshot's as_of_date consistent with what
                # the paper_* features were computed against.
                pit_df = ctx.dashboard_df.copy()
                if 'Symbol' in pit_df.columns:
                    pit_df = pit_df.set_index('Symbol')
                pit_vix = getattr(ctx.macro_dto, 'vix_value', None)

                pit_feat = build_pit_feature_matrix(
                    pit_df, as_of_date=pit_as_of, macro_vix=pit_vix,
                )
                pit_feat = pit_feat.copy()
                pit_feat.attrs = {}
                PITFeatureStore().write(pit_as_of, pit_feat)
            except Exception as pit_exc:
                telemetry.warning("PIT snapshot capture failed (non-fatal): %s", pit_exc)

        for col in ('Value_Z', 'Quality_Z', 'LowVol_Z', 'Size_Z', 'Multifactor_Composite'):
            ctx.dashboard_df[col] = float('nan')
        for col in ('Value_Z', 'Quality_Z', 'LowVol_Z', 'Size_Z', 'Multifactor_Composite'):
            ctx.dashboard_df[col] = ctx.dashboard_df['Symbol'].map(
                lambda x: shared_context.multifactor_scores.get(x, {}).get(col, float('nan'))
                if shared_context.multifactor_scores else float('nan')
            )

        ctx.dashboard_df['News_Sentiment'] = float('nan')
        ctx.dashboard_df['Earnings_Date'] = ""
        if shared_context.news_sentiment_scores:
            ctx.dashboard_df['News_Sentiment'] = ctx.dashboard_df['Symbol'].map(
                lambda x: shared_context.news_sentiment_scores.get(str(x).upper(), float('nan'))
            )
        if shared_context.earnings_dates:
            ctx.dashboard_df['Earnings_Date'] = ctx.dashboard_df['Symbol'].map(
                lambda x: shared_context.earnings_dates.get(str(x).upper(), "")
            )

        # Sentiment Pipeline Phase 4 -- multi-source credibility-weighted
        # aggregate, keyed by symbol with keys "credibility_weighted_sentiment"
        # (-> Credibility_Weighted_Sentiment), "bot_activity_ratio"
        # (-> Bot_Activity_Ratio), "aggregated_source_credibility"
        # (-> Aggregated_Source_Credibility). NaN when no multi-source social
        # documents exist for a symbol this trading day (distinct from
        # News_Sentiment, which is Finnhub-headline-only) -- same write-back
        # pattern as the Value_Z/etc multifactor columns above.
        _SENTIMENT_CREDIBILITY_COLS = {
            'Credibility_Weighted_Sentiment': 'credibility_weighted_sentiment',
            'Bot_Activity_Ratio': 'bot_activity_ratio',
            'Aggregated_Source_Credibility': 'aggregated_source_credibility',
        }
        for col in _SENTIMENT_CREDIBILITY_COLS:
            ctx.dashboard_df[col] = float('nan')
        for col, context_key in _SENTIMENT_CREDIBILITY_COLS.items():
            ctx.dashboard_df[col] = ctx.dashboard_df['Symbol'].map(
                lambda x: shared_context.sentiment_credibility_scores.get(str(x).upper(), {}).get(
                    context_key, float('nan')
                )
                if shared_context.sentiment_credibility_scores else float('nan')
            )

        ctx.dashboard_df['Correlation_Cluster'] = float('nan')

        # Sector Heat Factor (GDELT article-volume attention proxy, PR #416
        # scaffolding + this follow-on branch) -- one GDELT query per
        # distinct sector present this cycle, Gaussian-smoothed, mapped onto
        # every ticker row via its `sector` column. NaN-filled (never
        # fabricated -- CONSTRAINT #4) when settings.SECTOR_HEAT_ENABLED is
        # False (byte-identical to the prior placeholder behavior), on any
        # computation failure, or for a sector the GDELT query didn't cover.
        # See data/sentiment_sources.py::compute_sector_heat_factors and
        # docs/signals/sector_heat_factor.md.
        _apply_sector_heat_factor(ctx.dashboard_df)

        # Google Trends Abnormal Search Volume Index (ASVI)
        _apply_google_trends_asvi(ctx.dashboard_df)

        # Semantic Related Sector Selection (sector_selection_engine.py) --
        # persists each tracked symbol's top-N related-sector ranking so the
        # webapp's Sector Selection screen has data to read. A no-op when
        # settings.SECTOR_SELECTION_ENABLED is False (the default). See
        # _apply_sector_selection's own docstring for the daily-refresh gate
        # that keeps this from inserting duplicate rows under --interval.
        _apply_sector_selection(ctx.dashboard_df)

        # Financial Modeling Prep diagnostic feeds -- eight columns across
        # four independently-gated feeds (analyst / earnings / insider /
        # sector snapshot). Complete no-ops (zero network calls, every column
        # NaN) while their FMP_*_ENABLED gates are False, which is the
        # default, so this block is byte-identical to the pre-feature
        # behavior until an operator flips a flag in .env. Placed here, next
        # to the other _apply_* feature writers and AFTER the Earnings_Date
        # write-back above, because _apply_fmp_earnings becomes a SECOND
        # source for that shared column. None of these eight is read by
        # scoring, sizing, or execution -- see config.COLUMN_SCHEMA's "FMP
        # DIAGNOSTIC FEEDS" section for why that is also the no-lookahead
        # guarantee for this series.
        # Shared monotonic wall-clock budget for per-symbol FMP diagnostic
        # feeds, split via _fmp_next_feed_deadline (see its docstring) so
        # EVERY feed -- including the last-queued one -- is guaranteed at
        # least FMP_FEED_MIN_RESERVATION_SECONDS whenever the total budget
        # can support it, degrading to a fair even split otherwise. Fixed
        # 2026-08 (PR #737 follow-up): the prior version special-cased
        # insider to reuse the raw total deadline with no floor of its own,
        # which let analyst+earnings jointly exhaust the whole budget and
        # silently starve insider to zero symbols every cycle whenever
        # FMP_MAX_SECONDS_PER_CYCLE was configured below ~45s (the webapp
        # settings slider permits values as low as 1.0, with no warning).
        import time as _time
        _raw_fmp_budget = getattr(settings, "FMP_MAX_SECONDS_PER_CYCLE", 120.0)
        _fmp_max_seconds = max(0.0, float(120.0 if _raw_fmp_budget is None else _raw_fmp_budget))
        _fmp_total_deadline = _time.monotonic() + _fmp_max_seconds
        _fmp_min_reservation = min(FMP_FEED_MIN_RESERVATION_SECONDS, _fmp_max_seconds)

        _apply_fmp_analyst(
            ctx.dashboard_df,
            deadline=_fmp_next_feed_deadline(_fmp_total_deadline, _fmp_min_reservation, 3),
        )
        _apply_fmp_earnings(
            ctx.dashboard_df,
            deadline=_fmp_next_feed_deadline(_fmp_total_deadline, _fmp_min_reservation, 2),
        )
        _apply_fmp_insider(
            ctx.dashboard_df,
            deadline=_fmp_next_feed_deadline(_fmp_total_deadline, _fmp_min_reservation, 1),
        )
        _apply_fmp_sector(ctx.dashboard_df)
        _apply_fmp_econ_calendar(ctx.dashboard_df)

        # Wikipedia-pageviews investor-attention feature (follow-on branch
        # to PR #416/#417) -- data/attention_sources.py
        # ::compute_attention_scores_for_universe() returns {} (zero network
        # calls) whenever settings.WIKIPEDIA_ATTENTION_ENABLED is False, so
        # the NaN-fill immediately below reproduces today's exact disabled
        # behavior byte-identically. Same dict-then-.map() write-back
        # pattern as the Value_Z/etc multifactor columns and
        # Credibility_Weighted_Sentiment block above. company_name (when
        # available from this cycle's fundamentals) improves Wikipedia
        # article-title resolution -- see that module's docstring for the
        # documented ticker->title-resolution limitation.
        ctx.dashboard_df['Attention_Score'] = float('nan')
        try:
            from data.attention_sources import compute_attention_scores_for_universe
            _attn_fund_dtos = ctx.context_extras.get("fund_dtos", {}) or {}
            _attn_company_names = {
                sym: dto.company_name for sym, dto in _attn_fund_dtos.items() if dto is not None
            }
            attention_scores = compute_attention_scores_for_universe(
                ctx.dashboard_df['Symbol'].tolist(), _attn_company_names,
            )
        except Exception as attention_exc:
            telemetry.warning("Attention score computation failed (non-fatal): %s", attention_exc)
            attention_scores = {}
        if attention_scores:
            ctx.dashboard_df['Attention_Score'] = ctx.dashboard_df['Symbol'].map(
                lambda x: attention_scores.get(x, float('nan'))
            )

        # docs/plans/CONFIG_SCHEMA_PLAN.md Phase C1 — five ADVISORY METADATA columns
        # (config.COLUMN_SCHEMA's "# --- ADVISORY METADATA ---" section) are
        # populated only by the advisory path (engine/advisory.py's
        # Recommendation; the Sheet sink that mapped it was archived in step
        # 4e); this orchestrator
        # path has no equivalent per-symbol conviction/data-quality concept,
        # so blank/NaN-fill them here — same pattern already used above for
        # "Correlation_Cluster" / "News_Sentiment" — so DashboardSchema.validate()
        # keeps passing (every declared column must be present) without
        # fabricating advisory-only values (CONSTRAINT #4).
        ctx.dashboard_df['Score'] = float('nan')
        ctx.dashboard_df['Forecast_30_Pct'] = float('nan')
        ctx.dashboard_df['Advisory_Conviction'] = float('nan')
        ctx.dashboard_df['Advisory_Position_Pct'] = float('nan')
        ctx.dashboard_df['Advisory_Data_Quality'] = ""

        # Strategy evaluation loop
        strategy_cols = ['Action Signal', 'Advice', 'Actionable Advice Signal', 'Kelly Target',
                         'Sizing_Was_Capped', 'Sizing_Binding_Constraint',
                         'buyRange', 'sellRange', 'Strategy Explainer Notes',
                         'Robinhood Shares', 'Robinhood Avg Cost', 'Robinhood Dividends', 'Robinhood Advice']
        for col in strategy_cols:
            ctx.dashboard_df[col] = ""
        ctx.dashboard_df['Kelly Target'] = 0.0
        ctx.dashboard_df['Edge Ratio'] = 0.0
        ctx.dashboard_df['Robinhood Shares'] = 0.0
        ctx.dashboard_df['Robinhood Avg Cost'] = 0.0
        ctx.dashboard_df['Robinhood Dividends'] = 0.0

        eval_results = {}
        dead_letter_entries = []
        fund_dtos = ctx.context_extras.get("fund_dtos", {})
        trend_vol_indicators = ctx.context_extras.get("trend_vol_indicators", {})
        robinhood_positions = ctx.context_extras.get("robinhood_positions", {})

        # -- Vectorized Signal Aggregation --
        vec_df = pd.DataFrame(index=ctx.dashboard_df['Symbol'].values)
        vec_df['forecast_price'] = ctx.dashboard_df.get('Forecast_30', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        # Forecasting rebuild F2: forecast_alignment scores a fallback forecast
        # as neutral, so it needs the engine's disclosure flag. Only a real
        # bool counts; NaN (row skipped forecasting) stays "unknown" (False).
        vec_df['forecast_is_fallback'] = ctx.dashboard_df.get(
            'Forecast_30_Is_Fallback', pd.Series(False, index=ctx.dashboard_df.index)
        ).map(lambda v: v is True or (isinstance(v, (bool, np.bool_)) and bool(v))).values
        vec_df['trend_strength'] = ctx.dashboard_df.get('Aroon Up', pd.Series(50.0, index=ctx.dashboard_df.index)).fillna(50.0).values
        vec_df['atr'] = ctx.dashboard_df.get('ATR', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['macd_line'] = ctx.dashboard_df.get('MACD_Line', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['macd_signal'] = ctx.dashboard_df.get('MACD_Signal', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['aroon_osc'] = ctx.dashboard_df.get('Aroon Oscillator', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['rsi'] = ctx.dashboard_df.get('RSI', pd.Series(50.0, index=ctx.dashboard_df.index)).fillna(50.0).values
        
        sortino = ctx.dashboard_df.get('Sortino Ratio', ctx.dashboard_df.get('Sortino_Ratio', pd.Series(0.0, index=ctx.dashboard_df.index)))
        vec_df['sortino_ratio'] = sortino.fillna(0.0).values
        
        drawdown = ctx.dashboard_df.get('Max Drawdown', ctx.dashboard_df.get('Max_Drawdown', pd.Series(0.0, index=ctx.dashboard_df.index)))
        vec_df['max_drawdown'] = drawdown.fillna(0.0).values
        
        rs = ctx.dashboard_df.get('Relative_Strength', ctx.dashboard_df.get('RS vs SPY', ctx.dashboard_df.get('Relative Strength', pd.Series(0.0, index=ctx.dashboard_df.index))))
        vec_df['relative_strength'] = rs.fillna(0.0).values
        
        vec_df['garch_vol'] = ctx.dashboard_df.get('GARCH_Vol', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['GARCH_Vol'] = vec_df['garch_vol']
        
        edge = ctx.dashboard_df.get('Edge Ratio', ctx.dashboard_df.get('Edge_Ratio', pd.Series(0.0, index=ctx.dashboard_df.index)))
        vec_df['edge_ratio'] = edge.fillna(0.0).values
        
        vec_df['chandelier_long'] = ctx.dashboard_df['Symbol'].map(lambda x: trend_vol_indicators.get(x, {}).get('Chandelier_Long', 0.0)).values
        vec_df['chandelier_short'] = ctx.dashboard_df['Symbol'].map(lambda x: trend_vol_indicators.get(x, {}).get('Chandelier_Short', 0.0)).values
        
        vec_df['current_price'] = ctx.dashboard_df.get('Price', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['Close'] = vec_df['current_price']
        vec_df['ticker'] = ctx.dashboard_df['Symbol'].values
        # Added alongside 'ticker' above (Finding 2) -- CrossSectionalMomentumSignal
        # .compute()'s `row.get("Symbol", "")` lookup needs this exact column
        # name; without it every ticker resolved to "" and the module was
        # silently dead (contributing 0.0) in this vectorized path. 'ticker'
        # is left untouched since other code may still depend on it.
        vec_df['Symbol'] = ctx.dashboard_df['Symbol'].values
        vec_df['sector'] = ctx.dashboard_df['Symbol'].map(lambda x: fund_dtos.get(x).sector if fund_dtos.get(x) else "Unknown").values
        
        vec_df['roc_12m'] = ctx.dashboard_df.get('ROC_12M', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['ROC_12M'] = vec_df['roc_12m']
        vec_df['SMA_200'] = ctx.dashboard_df.get('SMA_200', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['RSI_2'] = ctx.dashboard_df.get('RSI_2', pd.Series(50.0, index=ctx.dashboard_df.index)).fillna(50.0).values
        
        sma_5_raw = ctx.dashboard_df.get('SMA_5', pd.Series(float('nan'), index=ctx.dashboard_df.index))
        vec_df['SMA_5'] = sma_5_raw.fillna(ctx.dashboard_df['Price']).values
        
        vec_df['dividend_yield'] = ctx.dashboard_df['Symbol'].map(lambda x: fund_dtos.get(x).dividend_yield if fund_dtos.get(x) and fund_dtos.get(x).dividend_yield else 0.0).values
        vec_df['is_dividend_sustainable'] = ctx.dashboard_df['Symbol'].map(lambda x: fund_dtos.get(x).is_dividend_sustainable if fund_dtos.get(x) else False).values
        vec_df['graham_number'] = ctx.dashboard_df['Symbol'].map(lambda x: fund_dtos.get(x).graham_number if fund_dtos.get(x) and fund_dtos.get(x).graham_number else 0.0).values


        from signals.base import SignalContext
        from signals import global_registry, SignalAggregator
        from dto_models import MarketBarDTO, FundamentalDataDTO
        dummy_bar = MarketBarDTO(date=datetime.now(), ticker="DUMMY", open_price=0.0, high_price=0.0, low_price=0.0, close_price=0.0, volume=0)
        dummy_fund = FundamentalDataDTO(ticker="DUMMY", pe_ratio=None, pb_ratio=None, dividend_yield=0.0, book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0, payout_ratio=0.0, sector="Unknown", company_name="Unknown")
        sig_ctx = SignalContext(
            bar=dummy_bar, fundamentals=dummy_fund, macro=ctx.macro_dto,
            multifactor_scores=shared_context.multifactor_scores,
            # Finding 2: without this, context.xsec_percentile_ranks was
            # always the SignalContext default empty dict in this vectorized
            # path, so cross_sectional_momentum.compute() always hit its
            # "ticker not in ranks" branch and returned score=0.0 regardless
            # of the Symbol-column fix above.
            xsec_percentile_ranks=shared_context.xsec_percentile_ranks,
        )
        aggregator = SignalAggregator(global_registry)
        try:
            vectorized_results = aggregator.aggregate_vectorized(vec_df, sig_ctx)
        except Exception as vec_exc:
            # Dead-letter, don't crash: a bug in any one vectorized signal
            # module must not abort the whole cycle. Falling back to {} makes
            # every ticker's precomputed_signal_tuple=None below, which is
            # the pre-existing default that routes evaluate_security() back
            # through the proven-safe per-ticker aggregator.aggregate() path.
            telemetry.warning(
                "aggregate_vectorized failed universe-wide (%s); falling back to per-ticker aggregate() for this cycle.",
                vec_exc,
            )
            vectorized_results = {}
        # -----------------------------------

        for row in ctx.dashboard_df.to_dict('records'):
            ticker = row['Symbol']
            price = row['Price']
            if not price or price == 0:
                continue

            stage = "dto_construction"
            try:
                history_df = ctx.tech_raw.get(ticker)
                if history_df is not None and not history_df.empty:
                    latest_row = history_df.iloc[-1]
                    bar_dto = MarketBarDTO(
                        date=datetime.now(),
                        ticker=ticker,
                        open_price=latest_row.get('Open', price),
                        high_price=latest_row.get('High', price),
                        low_price=latest_row.get('Low', price),
                        close_price=latest_row.get('Close', price),
                        volume=int(latest_row.get('Volume', 0))
                    )
                else:
                    bar_dto = MarketBarDTO(datetime.now(), ticker, price, price, price, price, 0)

                fund_dto = fund_dtos.get(ticker)
                if fund_dto is None:
                    fund_dto = FundamentalDataDTO(
                        ticker=ticker, pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
                        book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
                        payout_ratio=0.0, sector="Unknown", company_name="Unknown"
                    )

                rh_position = robinhood_positions.get(ticker) if robinhood_positions else None

                stage = "strategy"
                atr_val = float(row.get('ATR', 0.0))
                aroon_val = float(row.get('Aroon Up', 50.0))
                macd_line_val = float(row.get('MACD_Line', 0.0))
                macd_signal_val = float(row.get('MACD_Signal', 0.0))
                aroon_osc_val = float(row.get('Aroon Oscillator', 0.0))
                rsi_val = float(row.get('RSI', 50.0))
                sortino_val = float(row.get('Sortino Ratio', row.get('Sortino_Ratio', 0.0)))
                drawdown_val = float(row.get('Max Drawdown', row.get('Max_Drawdown', 0.0)))
                rs_val = float(row.get('Relative_Strength', row.get('RS vs SPY', row.get('Relative Strength', 0.0))))
                garch_val = float(row.get('GARCH_Vol', 0.0))
                edge_val = float(row.get('Edge Ratio', row.get('Edge_Ratio', 0.0)))
                rsi_2_val = float(row.get('RSI_2', 50.0)) if pd.notna(row.get('RSI_2', 50.0)) else 50.0
                sma_5_val = float(row.get('SMA_5')) if pd.notna(row.get('SMA_5')) else None
                roc_6m_val = float(row.get('ROC_6M')) if pd.notna(row.get('ROC_6M')) else 0.0
                vol_20_val = float(row.get('Vol_20')) if pd.notna(row.get('Vol_20')) else None
                vol_50_val = float(row.get('Vol_50')) if pd.notna(row.get('Vol_50')) else None
                vol_ratio_val = float(row.get('Vol_Ratio')) if pd.notna(row.get('Vol_Ratio')) else None
                roc_5_val = float(row.get('ROC_5')) if pd.notna(row.get('ROC_5')) else 0.0
                roc_20_val = float(row.get('ROC_20')) if pd.notna(row.get('ROC_20')) else 0.0

                chan_long = 0.0
                chan_short = 0.0
                if ticker in trend_vol_indicators:
                    chan_long = trend_vol_indicators[ticker].get('Chandelier_Long', 0.0)
                    chan_short = trend_vol_indicators[ticker].get('Chandelier_Short', 0.0)

                strategy_output = se.evaluate_security(
                    bar=bar_dto,
                    fundamentals=fund_dto,
                    macro=ctx.macro_dto,
                    forecast_price=row.get('Forecast_30', 0.0),
                    forecast_is_fallback=(
                        bool(row.get('Forecast_30_Is_Fallback'))
                        if isinstance(row.get('Forecast_30_Is_Fallback'), (bool, np.bool_)) else None
                    ),
                    trend_strength=aroon_val,
                    atr=atr_val,
                    macd_line=macd_line_val,
                    macd_signal=macd_signal_val,
                    aroon_osc=aroon_osc_val,
                    rsi=rsi_val,
                    sortino_ratio=sortino_val,
                    max_drawdown=drawdown_val,
                    relative_strength=rs_val,
                    garch_vol=garch_val,
                    edge_ratio=edge_val,
                    chandelier_long=chan_long,
                    chandelier_short=chan_short,
                    roc_12m=float(row.get('ROC_12M') if pd.notna(row.get('ROC_12M')) else 0.0),
                    sma_200=float(row.get('SMA_200') if pd.notna(row.get('SMA_200')) else 0.0),
                    rsi_2=rsi_2_val,
                    sma_5=sma_5_val,
                    roc_6m=roc_6m_val,
                    vol_20=vol_20_val,
                    vol_50=vol_50_val,
                    vol_ratio=vol_ratio_val,
                    roc_5=roc_5_val,
                    roc_20=roc_20_val,
                    robinhood_position=rh_position,
                    precomputed_signal_tuple=vectorized_results.get(ticker)
                )

                stage = "edge_ratio"
                # The real MAE/MFE/Edge Ratio — from an actual TransactionsStore
                # trade's genuine intra-trade OHLC path, not a fictional window —
                # are computed once for every ticker by ee.evaluate_portfolio()
                # below and overwrite this placeholder unconditionally. A
                # synthetic "entry 15 bars ago, exit today" pseudo-trade used to
                # be computed here and appended to the Strategy Explainer Notes
                # as if it were a real post-trade evaluation — misleading, since
                # no real trade exists over that window, and wasted since the
                # value was never read before being overwritten (removed).
                edge_ratio_val = 0.0

                stage = "results"
                eval_results[ticker] = {
                    'Edge Ratio': edge_ratio_val,
                    'Action Signal': strategy_output['Action Signal'],
                    'Advice': strategy_output['Advice'],
                    'Actionable Advice Signal': strategy_output['Actionable Advice Signal'],
                    'is_dividend_sustainable': int(fund_dto.is_dividend_sustainable),
                    'eps_trailing': fund_dto.eps_trailing,
                    'book_value': fund_dto.book_value,
                    'graham_number': fund_dto.graham_number,
                    'Kelly Target': float(strategy_output['Kelly Target']),
                    # Guardrail telemetry (sizing/position_sizer.py) -- schema-driven
                    # ("format": "string" in config.COLUMN_SCHEMA), so serialize the
                    # bool/Optional[str] into the plain-text convention
                    # ("Yes"/"No" + the constraint name or "") that every other
                    # string strategy_col in this loop already defaults to.
                    'Sizing_Was_Capped': "Yes" if strategy_output.get('Sizing_Was_Capped') else "No",
                    'Sizing_Binding_Constraint': strategy_output.get('Sizing_Binding_Constraint') or "",
                    'buyRange': strategy_output['buyRange'],
                    'sellRange': strategy_output['sellRange'],
                    'Strategy Explainer Notes': strategy_output['Strategy Explainer Notes'],
                    'Robinhood Shares': float(strategy_output.get('Robinhood Shares', 0.0)),
                    'Robinhood Avg Cost': float(strategy_output.get('Robinhood Avg Cost', 0.0)),
                    'Robinhood Dividends': float(strategy_output.get('Robinhood Dividends', 0.0)),
                    'Robinhood Advice': str(strategy_output.get('Robinhood Advice', 'N/A')),
                    # Per-module weighted score breakdown (strategy_engine.py
                    # evaluate_security()'s Score_Components dict) — threaded
                    # through so _write_state_snapshot can surface it the same
                    # way reporting/state_snapshot.py's advisory writer already
                    # does. {} (never fabricated) when the strategy engine
                    # didn't produce a breakdown for this ticker.
                    'Score_Components': strategy_output.get('Score_Components') or {},
                    # Position-sizing decomposition (strategy_engine.py
                    # evaluate_security() lines ~388-408) — threaded through so
                    # _write_state_snapshot can surface the pre/post-regime Kelly
                    # breakdown the same way reporting/state_snapshot.py's
                    # advisory writer already does. Bare .get() — NEVER `or 1.0`/
                    # `or 0.0` here: a genuine 0.0 (e.g. a MetaLabeler hard-gating
                    # the signal below settings.META_LABEL_MIN_CONFIDENCE) must
                    # survive, and an absent key must stay None, not be coerced
                    # into a fabricated no-op (CONSTRAINT #4).
                    'Meta_Label_Composite': strategy_output.get('Meta_Label_Composite'),
                    'Regime_Multiplier': strategy_output.get('Regime_Multiplier'),
                    'Kelly_Target_Pre_Regime': strategy_output.get('Kelly_Target_Pre_Regime'),
                    'Kelly_Target_Post_Regime': strategy_output.get('Kelly_Target_Post_Regime'),
                }

            except Exception as ticker_exc:
                dead_letter_entries.append({
                    "symbol": ticker,
                    "stage": stage,
                    "error": str(ticker_exc),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                telemetry.error(
                    "Dead-lettered %s at stage=%s: %s", ticker, stage, ticker_exc,
                    exc_info=True,
                )

        # Write dead-letter
        dl_path = settings.OUTPUT_DIR / "dead_letter.json"
        try:
            import json as _json
            dl_payload = {
                "run_id": datetime.now(timezone.utc).isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "entries": dead_letter_entries,
            }
            dl_tmp = dl_path.with_suffix(".tmp")
            dl_path.parent.mkdir(parents=True, exist_ok=True)
            dl_tmp.write_text(_json.dumps(dl_payload, indent=2), encoding="utf-8")
            dl_tmp.replace(dl_path)
            if dead_letter_entries:
                telemetry.warning(
                    "Dead-letter report: %d symbol(s) failed — see %s",
                    len(dead_letter_entries), dl_path,
                )
            else:
                telemetry.info("All symbols processed cleanly — dead_letter.json cleared.")
        except Exception as dl_exc:
            telemetry.warning("Failed to write dead-letter report: %s", dl_exc)

        _SIZING_DECOMPOSITION_COLS = (
            'Meta_Label_Composite', 'Regime_Multiplier',
            'Kelly_Target_Pre_Regime', 'Kelly_Target_Post_Regime',
        )
        # Guardrail telemetry (sizing/position_sizer.py) -- UNLIKE
        # _SIZING_DECOMPOSITION_COLS these ARE real config.COLUMN_SCHEMA
        # columns ("format": "string", nullable=True), but the same CONSTRAINT
        # #4 concern applies: a ticker missing from eval_results (dead-lettered
        # -- its strategy evaluation raised this cycle, see dead_letter.json)
        # must NOT default to "" here, because "" is downstream coerced into
        # the ACTIVE FALSE CLAIM "No sizing ceiling bound" (main_orchestrator.py
        # / the GUI) rather than "not computed". None is the honest default;
        # every ticker that DID reach the 'results' stage still gets its real
        # "Yes"/"No" + constraint-name string via eval_results.get(x, {}).
        _SIZING_GUARDRAIL_COLS = ('Sizing_Was_Capped', 'Sizing_Binding_Constraint')
        for col in [
            'Edge Ratio', 'Action Signal', 'Advice', 'Actionable Advice Signal',
            'is_dividend_sustainable', 'eps_trailing', 'book_value', 'graham_number',
            'Kelly Target', 'Sizing_Was_Capped', 'Sizing_Binding_Constraint',
            'buyRange', 'sellRange',
            'Strategy Explainer Notes', 'Robinhood Shares', 'Robinhood Avg Cost',
            'Robinhood Dividends', 'Robinhood Advice', 'Score_Components',
            *_SIZING_DECOMPOSITION_COLS,
        ]:
            if col in ['Edge Ratio', 'Kelly Target', 'Robinhood Shares', 'Robinhood Avg Cost', 'Robinhood Dividends', 'is_dividend_sustainable', 'eps_trailing', 'book_value', 'graham_number']:
                default_val = 0.0 if col != 'is_dividend_sustainable' else 0
                ctx.dashboard_df[col] = ctx.dashboard_df['Symbol'].map(lambda x: eval_results.get(x, {}).get(col, default_val))
            elif col == 'Score_Components':
                # Dict-valued column — "" is not a sensible default (CONSTRAINT #4:
                # an empty breakdown, not a fabricated one).
                ctx.dashboard_df[col] = ctx.dashboard_df['Symbol'].map(lambda x: eval_results.get(x, {}).get(col, {}))
            elif col in _SIZING_GUARDRAIL_COLS:
                # None (never "" -- see the comment on _SIZING_GUARDRAIL_COLS
                # above) for a ticker missing from eval_results entirely.
                ctx.dashboard_df[col] = ctx.dashboard_df['Symbol'].map(lambda x: eval_results.get(x, {}).get(col, None))
            elif col in _SIZING_DECOMPOSITION_COLS:
                # Position-sizing decomposition (Meta_Label_Composite/
                # Regime_Multiplier/Kelly_Target_{Pre,Post}_Regime) — deliberately
                # NOT in config.COLUMN_SCHEMA (like Score_Components above): these
                # are read-only diagnostic fields for the webapp's Strategy Matrix/
                # Symbol Detail screens, not an HTML-report column or a
                # quant_platform.db field. Adding them to COLUMN_SCHEMA would
                # trigger a DailySignals DDL migration for no reason (pandera
                # is strict=False so a non-schema column here is already safe).
                # Default None (-> NaN), NEVER 0.0/1.0 — a fabricated sizing
                # value is actively misleading (CONSTRAINT #4), and 0.0 is a
                # real, operationally significant value (a MetaLabeler hard
                # gate) that must never be confused with "not computed".
                ctx.dashboard_df[col] = ctx.dashboard_df['Symbol'].map(lambda x: eval_results.get(x, {}).get(col, None))
            else:
                ctx.dashboard_df[col] = ctx.dashboard_df['Symbol'].map(lambda x: eval_results.get(x, {}).get(col, ""))

        if 'Avg Cost' in ctx.dashboard_df.columns:
            ctx.dashboard_df['Entry_Price'] = ctx.dashboard_df['Avg Cost']

        if 'Shares' in ctx.dashboard_df.columns and 'Price' in ctx.dashboard_df.columns:
            ctx.dashboard_df['position_size'] = ctx.dashboard_df['Shares'] * ctx.dashboard_df['Price']
            zero_mask = ctx.dashboard_df['position_size'] <= 0.0
            if zero_mask.any():
                ctx.dashboard_df.loc[zero_mask, 'position_size'] = 10000.0
        elif 'position_size' not in ctx.dashboard_df.columns:
            ctx.dashboard_df['position_size'] = 10000.0

        if 'VaR 95' in ctx.dashboard_df.columns:
            ctx.dashboard_df['stop_loss_pct'] = ctx.dashboard_df['VaR 95'].abs()
        elif 'VaR_95' in ctx.dashboard_df.columns:
            ctx.dashboard_df['stop_loss_pct'] = ctx.dashboard_df['VaR_95'].abs()
        elif 'stop_loss_pct' not in ctx.dashboard_df.columns:
            ctx.dashboard_df['stop_loss_pct'] = 0.05

        if 'Sector' in ctx.dashboard_df.columns and 'sector' not in ctx.dashboard_df.columns:
            ctx.dashboard_df['sector'] = ctx.dashboard_df['Sector']
        
        if 'RS vs SPY' in ctx.dashboard_df.columns and 'Relative_Strength' not in ctx.dashboard_df.columns:
            ctx.dashboard_df['Relative_Strength'] = ctx.dashboard_df['RS vs SPY']
        elif 'Relative Strength' in ctx.dashboard_df.columns and 'Relative_Strength' not in ctx.dashboard_df.columns:
            ctx.dashboard_df['Relative_Strength'] = ctx.dashboard_df['Relative Strength']
        elif 'Relative_Strength' not in ctx.dashboard_df.columns:
            ctx.dashboard_df['Relative_Strength'] = 0.0

        if 'sector' in ctx.dashboard_df.columns:
            unique_sectors = ctx.dashboard_df['sector'].dropna().unique()
            if len(unique_sectors) > 0:
                benchmark_df = pd.DataFrame({
                    'sector': unique_sectors,
                    'weight': 1.0 / len(unique_sectors),
                    'return': 0.02
                })
            else:
                benchmark_df = pd.DataFrame()
        else:
            benchmark_df = pd.DataFrame()

        ctx.dashboard_df = ee.evaluate_portfolio(ctx.dashboard_df, benchmark_df, data_provider=ctx.tech_raw)

        export_keys = ['MAE', 'MFE', 'Edge Ratio', 'Portfolio_Heat', 'BF_Allocation', 'BF_Selection', 'BF_Interaction']
        for key in export_keys:
            if key in ctx.dashboard_df.columns:
                if key not in ['MAE', 'MFE', 'Edge Ratio']:
                    ctx.dashboard_df[key] = ctx.dashboard_df[key].fillna(0.0)
            else:
                if key not in ['MAE', 'MFE', 'Edge Ratio']:
                    ctx.dashboard_df[key] = 0.0
                else:
                    ctx.dashboard_df[key] = np.nan

        # Dual Momentum Overlay
        if settings.USE_DUAL_MOMENTUM_OVERLAY:
            telemetry.info("Running Dual Momentum Overlay...")
            try:
                dm = DualMomentumAllocator(
                    risky_assets=list(settings.DUAL_MOMENTUM_RISKY_ASSETS),
                    safe_asset=settings.DUAL_MOMENTUM_SAFE_ASSET,
                )
                dm_alloc = dm.decide(
                    as_of_date=datetime.now(timezone.utc).date(),
                    price_data=ctx.tech_raw,
                )
                dm_winner = next(iter(dm_alloc))
                telemetry.info(f"Dual Momentum decision: {dm_winner} ({dm_alloc})")
                if dm_winner == settings.DUAL_MOMENTUM_SAFE_ASSET:
                    risky_set = set(settings.DUAL_MOMENTUM_RISKY_ASSETS)
                    mask = ctx.dashboard_df["Symbol"].isin(risky_set)
                    ctx.dashboard_df.loc[mask, "Kelly Target"] = 0.0
                    telemetry.info(
                        f"Dual Momentum: safe-asset regime. Kelly Target zeroed for "
                        f"{list(risky_set & set(ctx.dashboard_df['Symbol'].tolist()))}"
                    )
                ctx.dashboard_df["DualMomentum_Signal"] = dm_winner
            except Exception as dm_err:
                telemetry.warning(f"Dual Momentum Overlay failed (non-critical): {dm_err}")
                ctx.dashboard_df["DualMomentum_Signal"] = "N/A"
        else:
            ctx.dashboard_df["DualMomentum_Signal"] = "disabled"

        # ---------------------------------------------------------------------
        # PORTFOLIO-LEVEL GROSS EXPOSURE CAP (sizing/position_sizer.py)
        # ---------------------------------------------------------------------
        # Applied ACROSS the whole cycle's universe, AFTER every name's own
        # per-symbol sizing (Kelly/vol-target + MAX_POSITION_WEIGHT clamp +
        # regime/meta-label composition) and after the Dual Momentum overlay
        # above, so it sees the FINAL per-name weights. Scales every name
        # uniformly (never alters relative sizing between names) via
        # apply_portfolio_gross_cap(); this is the new constraint layered on
        # top of -- not instead of -- the existing per-name ceiling. A name
        # whose weight is reduced here has its guardrail telemetry overridden
        # to "portfolio_gross": applied chronologically last, it is the most
        # authoritative reason a position ended up smaller than its raw
        # Kelly/vol-target recommendation for this cycle.
        #
        # Runs unconditionally, in its own try: it no longer shares one with
        # any optional feature (see _apply_portfolio_gross_cap's docstring).
        try:
            _apply_portfolio_gross_cap(ctx.dashboard_df)
        except Exception as portfolio_cap_exc:
            telemetry.warning(f"Portfolio gross cap application failed (non-critical): {portfolio_cap_exc}")

        # ---------------------------------------------------------------------
        # CAP-EVENT AUDIT LOG + THRESHOLD ALERT (sizing/cap_audit_store.py)
        # ---------------------------------------------------------------------
        # Persist this cycle's FINAL guardrail telemetry (after the portfolio
        # cap above) to the durable sizing_cap_events table, and (opt-in, see
        # settings.SIZING_CAP_ALERT_ENABLED below) fire a WARNING alert if an
        # unusually large fraction of names were capped this cycle. Both are
        # best-effort: a DB/alert-channel hiccup only logs a warning, never
        # affects the run's own sizing decisions (CONSTRAINT #6) or its
        # SUCCEEDED/FAILED state.
        #
        # cycle_id is hoisted above both this try block and the symbol-rating
        # audit block below it so the two share one identical cycle
        # identity regardless of which of SIZING_CAP_AUDIT_ENABLED /
        # SYMBOL_RATING_ENABLED is on -- it must not live only inside the
        # SIZING_CAP_AUDIT_ENABLED-gated branch, or referencing it from the
        # symbol-rating block below would raise UnboundLocalError whenever
        # cap-event auditing is disabled but symbol rating isn't.
        cycle_id = datetime.now(timezone.utc).isoformat()
        try:
            if settings.SIZING_CAP_AUDIT_ENABLED and not ctx.dashboard_df.empty:
                from sizing.cap_audit_store import CapAuditStore

                events = []
                for row in ctx.dashboard_df.to_dict('records'):
                    # None (never "" -- see _SIZING_GUARDRAIL_COLS above) means
                    # this ticker's strategy evaluation never reached the
                    # 'results' stage this cycle (dead-lettered). Skip it
                    # entirely rather than writing a fabricated was_capped=False
                    # row -- CONSTRAINT #4: no event recorded is honest; a
                    # false "not capped" event would corrupt the escalation
                    # rule's consecutive-capped-cycles read for this symbol.
                    _raw_capped = row.get("Sizing_Was_Capped")
                    if _raw_capped is None or (isinstance(_raw_capped, float) and pd.isna(_raw_capped)):
                        continue
                    events.append({
                        "symbol": row["Symbol"],
                        "raw_weight": None,  # not retained at this cycle-wide stage; see per-symbol Kelly_Target_Pre_Regime
                        "final_weight": float(row["Kelly Target"]) if pd.notna(row["Kelly Target"]) else None,
                        "binding_constraint": (row.get("Sizing_Binding_Constraint") or None),
                        "was_capped": str(_raw_capped).strip().lower() == "yes",
                        "cycle_id": cycle_id,
                    })
                CapAuditStore().record_cap_events(events, cycle_id=cycle_id)
        except Exception as audit_exc:
            telemetry.warning(f"Sizing cap-event audit write failed (non-critical): {audit_exc}")

        # ---------------------------------------------------------------------
        # SYMBOL-RATING AUDIT LOG (rating/symbol_rating_store.py)
        # ---------------------------------------------------------------------
        # Records this cycle's GOOD/BAD verdict (rating.symbol_rating.classify_tier)
        # for every symbol that actually reached the 'results' stage this
        # cycle, mirroring the CAP-EVENT AUDIT LOG block directly above:
        # best-effort, a DB hiccup only logs a warning and never affects the
        # run's own scoring/sizing decisions or its SUCCEEDED/FAILED state
        # (CONSTRAINT #6). The write itself lives in the module-level
        # _record_symbol_ratings() helper below (same pattern as
        # _apply_sector_heat_factor) so it can be
        # exercised directly in tests without going through the whole of
        # StrategyEvalStep.run().
        try:
            _record_symbol_ratings(ctx.dashboard_df, cycle_id)
        except Exception as rating_exc:
            telemetry.warning(f"Symbol-rating audit write failed (non-critical): {rating_exc}")

        # Populate the two config.COLUMN_SCHEMA-registered rating columns on
        # the dashboard itself (HTML report/state snapshot) -- a
        # SEPARATE try/except from the write above so a read-back failure
        # can never suppress the write, and vice versa. Always runs
        # (independent of SYMBOL_RATING_AUTO_DROP_ENABLED -- see
        # _apply_symbol_rating_columns's own docstring) so
        # config.DashboardSchema.validate() always finds both columns, even
        # when the rating store itself is unreachable this cycle.
        try:
            if not ctx.dashboard_df.empty:
                _apply_symbol_rating_columns(ctx.dashboard_df)
        except Exception as rating_col_exc:
            telemetry.warning(f"Symbol-rating column population failed (non-critical): {rating_col_exc}")
            ctx.dashboard_df['Symbol_Rating_Consecutive_Bad_Cycles'] = 0.0
            ctx.dashboard_df['Symbol_Rating_Excluded'] = "No"

        try:
            if settings.SIZING_CAP_ALERT_ENABLED and not ctx.dashboard_df.empty:
                _capped_mask = ctx.dashboard_df["Sizing_Was_Capped"].astype(str).str.strip().str.lower() == "yes"
                _capped_frac = float(_capped_mask.mean())
                if _capped_frac >= settings.SIZING_CAP_ALERT_THRESHOLD_PCT:
                    from observability.alerts import send_alert as _sizing_cap_alert

                    _capped_symbols = ctx.dashboard_df.loc[_capped_mask, "Symbol"].tolist()
                    _sizing_cap_alert(
                        "WARNING",
                        f"Position sizing: {_capped_frac:.0%} of names capped this cycle "
                        f"(>= {settings.SIZING_CAP_ALERT_THRESHOLD_PCT:.0%} threshold): "
                        f"{', '.join(_capped_symbols[:20])}"
                        + (f" (+{len(_capped_symbols) - 20} more)" if len(_capped_symbols) > 20 else ""),
                        extra={
                            "type": "sizing_cap_threshold",
                            "capped_fraction": _capped_frac,
                            "threshold": settings.SIZING_CAP_ALERT_THRESHOLD_PCT,
                            "capped_symbols": _capped_symbols,
                        },
                        dedup_key="sizing_cap_threshold",
                    )
        except Exception as alert_exc:
            telemetry.warning(f"Sizing cap-threshold alert failed (non-critical): {alert_exc}")


# ---------------------------------------------------------------------------
# Advisory overlay + agentic queue (step 5.2 of
# .claude/shrink_step5_retire_main_py_implementation_plan.md)
# ---------------------------------------------------------------------------

_ADVISORY_COLUMNS = (
    'Advisory_Action', 'Advisory_Conviction', 'Advisory_Rationale',
    'Advisory_Position_Pct', 'Advisory_Data_Quality',
)
_ADVISORY_NUMERIC_COLUMNS = ('Advisory_Conviction', 'Advisory_Position_Pct')


def _select_precomputed_for_row(row: Optional[dict], reuse_pipeline_compute: bool) -> tuple:
    """Return ``(precomputed_garch, precomputed_forecast, precomputed_forecast_is_fallback)``
    for one ticker's ``engine.advisory.evaluate()`` call.

    ``settings.ADVISORY_REUSE_PIPELINE_COMPUTE`` off (or no dashboard row for
    the ticker): all three are None, so evaluate() refits GARCH and the
    forecast itself, exactly as main.py does. On: the row's own
    ``GARCH_Vol``/``Forecast_30`` are passed through, and
    ``Forecast_30_Is_Fallback`` only when it is an actual bool -- the cell can
    also be NaN (the row skipped forecasting) or absent, neither of which says
    anything about fallback status.
    """
    if not reuse_pipeline_compute or row is None:
        return None, None, None
    raw_fallback = row.get('Forecast_30_Is_Fallback')
    return (
        row.get('GARCH_Vol'),
        row.get('Forecast_30'),
        raw_fallback if isinstance(raw_fallback, bool) else None,
    )


def _empty_account_snapshot():
    """The empty account main.py's AccountStep evaluates with when the
    Robinhood snapshot is unavailable (pipeline/steps.py)."""
    from data.robinhood_portfolio import AccountSnapshot

    return AccountSnapshot(
        positions={},
        buying_power=0.0,
        total_equity=0.0,
        total_dividends=0.0,
        fetched_at=datetime.now(timezone.utc),
    )


class AdvisoryOverlayStep(PipelineStep):
    """Runs ``engine.advisory.evaluate()`` for every universe symbol, the same
    way main.py's run_once() does, and keeps the full ``Recommendation``
    objects in ``ctx.recommendations``.

    Split out of ``BrokerExecutionStep`` in step 5.2. It is a SYNC step so
    ``AsyncPipelineRunner`` runs it under ``PIPELINE_STEP_TIMEOUT_SECONDS``
    (the async broker step has no timeout). Its inputs match main.py's:

    * the universe is ``ctx.symbols`` (the same ``build_universe_detailed``
      main.py uses, since step 5.1), in the same order;
    * the account snapshot is the one ``AsyncDataFetchStep`` already fetched
      (``ctx.snapshot``; before 5.2 this step fetched it a second time), or
      main.py's empty snapshot when that fetch failed;
    * the context extras come from ``pipeline.advisory_inputs``'s
      ``fetch_bars_for_universe`` + ``build_context_extras`` -- the same
      functions main.py calls. Before 5.2 this step passed only the pipeline's
      xsec ranks and multifactor scores.

    ``ctx.macro_dto`` is the daemon's own (built by ``RunPipelineStep``), so a
    difference from main.py's macro inputs still shows up here; see the plan's
    section 1 "Macro".

    It writes the same five ``Advisory_*`` dashboard columns as before.
    ``settings.ADVISORY_REUSE_PIPELINE_COMPUTE`` is honoured exactly as the old
    block did. Never raises: a failure is logged, the columns stay at their
    blank defaults, and ``advisory_overlay_ok`` is not set, which makes
    ``AgenticQueueStep`` skip the cycle.
    """

    # Same progress-stage label the advisory loop always reported under.
    name = "execution"

    def run(self, ctx: RunContext) -> None:
        """Evaluate every symbol and fill ctx.recommendations + the Advisory_* columns."""
        if ctx.dashboard_df is None or ctx.dashboard_df.empty:
            return
        # Captured once per step: the daemon can apply runtime_flags.json to
        # the shared settings object between (and during) cycles.
        reuse_pipeline_compute = bool(getattr(settings, 'ADVISORY_REUSE_PIPELINE_COMPUTE', False))
        max_workers = int(getattr(settings, 'ADVISORY_MAX_CONCURRENCY', 8))
        try:
            self._evaluate(ctx, reuse_pipeline_compute, max_workers)
        except Exception as adv_loop_err:
            telemetry.warning(
                "Advisory evaluation loop failed (non-critical): %s", adv_loop_err
            )
            return
        ctx.context_extras["advisory_overlay_ok"] = True

    def _evaluate(self, ctx: RunContext, reuse_pipeline_compute: bool, max_workers: int) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from data.market_data import get_provider as _get_market_provider
        from engine.advisory import evaluate as _advisory_evaluate
        from pipeline.advisory_inputs import build_context_extras, fetch_bars_for_universe

        dashboard_df = ctx.dashboard_df
        for col in _ADVISORY_COLUMNS:
            dashboard_df[col] = ""
        for col in _ADVISORY_NUMERIC_COLUMNS:
            dashboard_df[col] = 0.0

        if ctx.snapshot is None:
            telemetry.warning(
                "Advisory: Robinhood account snapshot unavailable — evaluating "
                "with an empty account (main.py's fallback); Kelly sizing still runs."
            )
            ctx.snapshot = _empty_account_snapshot()
        snapshot = ctx.snapshot

        symbols = [str(s) for s in ctx.symbols if s]
        # Defensive: the dashboard is built from ctx.symbols, so this is
        # normally empty. A row that isn't in ctx.symbols still gets its
        # Advisory_* columns, as it did before step 5.2.
        known = set(symbols)
        dashboard_only = [
            s for s in (str(v).upper() for v in dashboard_df['Symbol'].tolist())
            if s and s not in known
        ]
        if dashboard_only:
            telemetry.warning(
                "Advisory: %d dashboard symbol(s) not in the cycle universe "
                "(evaluated anyway): %s", len(dashboard_only), ", ".join(dashboard_only[:10]),
            )
            symbols.extend(dict.fromkeys(dashboard_only))

        market = _get_market_provider()
        bars_dict = fetch_bars_for_universe(symbols, market)
        context_extras = build_context_extras(symbols, bars_dict, ctx.macro_dto, market)
        ctx.bars_dict = bars_dict
        ctx.context_extras["advisory_context_extras"] = context_extras

        rows_by_symbol = {}
        if reuse_pipeline_compute:
            for row in dashboard_df.to_dict('records'):
                ticker = str(row.get('Symbol', '')).upper()
                if ticker:
                    rows_by_symbol[ticker] = row

        if ctx.progress is not None:
            ctx.progress.start_stage("execution", symbols_total=len(symbols))

        def _eval_one(symbol: str) -> tuple:
            """('ok', Recommendation) or ('err', error_dict). Never raises."""
            try:
                garch, forecast, forecast_is_fallback = _select_precomputed_for_row(
                    rows_by_symbol.get(symbol.upper()), reuse_pipeline_compute,
                )
                rec = _advisory_evaluate(
                    symbol=symbol,
                    position=snapshot.positions.get(symbol),
                    market=market,
                    snapshot=snapshot,
                    macro_dto=ctx.macro_dto,
                    context_extras=context_extras,
                    precomputed_garch=garch,
                    precomputed_forecast=forecast,
                    precomputed_forecast_is_fallback=forecast_is_fallback,
                )
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Advisory: {symbol}")
                return "ok", rec
            except Exception as exc:
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Advisory: {symbol} (failed)")
                return "err", {
                    "symbol": symbol,
                    "stage": "advisory_evaluate",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

        workers = max(1, max_workers)
        if workers == 1 or len(symbols) <= 1:
            results_by_symbol = {sym: _eval_one(sym) for sym in symbols}
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as pool:
                results_by_symbol = dict(zip(symbols, pool.map(_eval_one, symbols)))

        # Assemble in universe order so the recommendations (and so the queue
        # source) are deterministic regardless of worker completion order.
        recommendations = []
        column_values: dict = {}
        for symbol in symbols:
            kind, payload = results_by_symbol[symbol]
            if kind != "ok":
                telemetry.warning("Advisory failed for %s: %s", symbol, payload["message"])
                ctx.errors.append(payload)
                continue
            rec = payload
            recommendations.append(rec)
            telemetry.info(
                "  %-6s  %-10s  conviction=%.2f  quality=%-7s  pos=%.1f%%",
                symbol, rec.action, rec.conviction, rec.data_quality,
                rec.suggested_position_pct * 100.0,
            )
            column_values[symbol.upper()] = {
                'Advisory_Action': rec.action,
                'Advisory_Conviction': round(rec.conviction, 4),
                'Advisory_Rationale': rec.rationale,
                'Advisory_Position_Pct': round(rec.suggested_position_pct, 6),
                'Advisory_Data_Quality': rec.data_quality,
            }
        ctx.recommendations = recommendations

        for col in _ADVISORY_COLUMNS:
            blank = 0.0 if col in _ADVISORY_NUMERIC_COLUMNS else ""
            dashboard_df[col] = dashboard_df['Symbol'].map(
                lambda x, _c=col, _b=blank: column_values.get(str(x).upper(), {}).get(_c, _b)
            )

        telemetry.info(
            "Advisory evaluation complete for %d tickers (%d recommendations).",
            len(symbols), len(recommendations),
        )


DAEMON_AGENTIC_QUEUE_MODES = ("off", "shadow", "primary")
SHADOW_OUTPUT_SUBDIR = "shadow"


def resolve_daemon_agentic_queue_mode(value: Any) -> str:
    """Normalise a ``DAEMON_AGENTIC_QUEUE_MODE`` value; anything unknown is ``off``."""
    mode = str(value or "").strip().lower()
    return mode if mode in DAEMON_AGENTIC_QUEUE_MODES else "off"


def shadow_output_dir(output_dir: Any) -> Path:
    """Where the shadow queue lives: ``<OUTPUT_DIR>/shadow``."""
    return Path(output_dir) / SHADOW_OUTPUT_SUBDIR


def shadow_collision_reason(real_dir: Any, shadow_dir: Any) -> Optional[str]:
    """Return why a shadow write could land on a real queue file, or None.

    ``shadow_dir`` is a subdirectory of ``real_dir``, so the plain paths never
    collide. A symlink or hard link could still make them the same file (for
    example ``OUTPUT_DIR/shadow`` pointing back at ``OUTPUT_DIR``), so every
    path the shadow writer touches is compared with its real counterpart after
    resolving links.
    """
    real_dir = Path(real_dir)
    shadow_dir = Path(shadow_dir)
    pairs = (
        ("output dir", real_dir, shadow_dir),
        ("queue_sources dir", real_dir / "queue_sources", shadow_dir / "queue_sources"),
        ("advisory source", real_dir / "queue_sources" / "advisory.json",
         shadow_dir / "queue_sources" / "advisory.json"),
        ("execution queue", real_dir / "execution_queue.json",
         shadow_dir / "execution_queue.json"),
    )
    for label, real_path, shadow_path in pairs:
        try:
            if real_path.resolve() == shadow_path.resolve():
                return f"shadow {label} resolves to the real one ({real_path.resolve()})"
            if real_path.exists() and shadow_path.exists() and os.path.samefile(real_path, shadow_path):
                return f"shadow {label} is the same file as the real one ({real_path})"
        except OSError as exc:
            return f"could not verify the shadow {label} path ({exc})"
    return None


SHADOW_HISTORY_SUBDIR = "history"
# Two files per cycle; 480 files is ~10 days of hourly cycles, enough for
# the 5-trading-day comparison (scripts/compare_shadow_queue.py).
SHADOW_HISTORY_MAX_FILES = 480


def archive_shadow_run(
    shadow_dir: Path, now: datetime, source_path: Optional[Path], queue_path: Optional[Path],
) -> None:
    """Keep a timestamped copy of this cycle's shadow files under
    ``shadow/history/`` so ``scripts/compare_shadow_queue.py`` can find the
    shadow run nearest after main.py's morning queue (the live shadow files
    are overwritten every cycle). Only files written THIS cycle are copied: a
    ``None`` queue path means compose wrote nothing, so the previous shadow
    queue is not re-archived under a new timestamp. Best effort; never raises.
    """
    try:
        history = Path(shadow_dir) / SHADOW_HISTORY_SUBDIR
        history.mkdir(parents=True, exist_ok=True)
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for kind, path in (("advisory", source_path), ("execution_queue", queue_path)):
            if path is not None and Path(path).exists():
                (history / f"{stamp}_{kind}.json").write_bytes(Path(path).read_bytes())
        files = sorted(p for p in history.glob("*.json") if p.is_file())
        for old in files[:-SHADOW_HISTORY_MAX_FILES]:
            old.unlink()
    except Exception as exc:  # noqa: BLE001 - archive is diagnostics only
        telemetry.warning("Shadow agentic queue: history archive failed (%s).", exc)


class AgenticQueueStep(PipelineStep):
    """Writes the daemon's copy of the Robinhood execution queue from
    ``ctx.recommendations``, behind ``settings.DAEMON_AGENTIC_QUEUE_MODE``.

    * ``off`` (default): does nothing. main.py stays the only queue writer.
    * ``shadow``: writes ``queue_sources/advisory.json`` and
      ``execution_queue.json`` under ``OUTPUT_DIR/shadow/`` ONLY, with the same
      ``write_advisory_source`` + ``compose_and_emit`` calls main.py's
      ``_run_cycle`` makes. ``side_effects=False`` means no push notification,
      no risk-gate alert and no ``risk_gate_blocks.jsonl`` entry. Refuses to
      write if a link makes a shadow path resolve to a real queue path. A
      timestamped copy of each cycle's shadow files goes to
      ``shadow/history/`` for ``scripts/compare_shadow_queue.py``.
    * ``primary``: not implemented until step 5.3. Behaves exactly like
      ``shadow`` and logs a warning; it never writes the real queue here.

    Skips (and logs why) when the cycle stopped, the data is synthetic
    (MockDataEngine fallback), the advisory overlay did not finish, or there
    are no recommendations. The mode and ``ROBINHOOD_EXECUTION_MODE`` are
    captured once at step start and passed explicitly, because the daemon can
    hot-reload runtime flags while a cycle runs.

    A separate, short, sync step on purpose: if ``AdvisoryOverlayStep`` times
    out, the runner raises and this step never runs for that cycle, so a
    still-running advisory thread can't write a queue after its cycle has been
    marked failed. Never raises (a native crash would take the daemon's APIs
    down with it; Python errors are logged and swallowed).
    """

    name = "agentic_queue"

    def __init__(self, *, clock: Optional[Any] = None) -> None:
        # ``clock`` (a zero-arg callable returning an aware datetime) exists
        # for the frozen-input equivalence test; production uses UTC now.
        self._clock = clock

    def run(self, ctx: RunContext) -> None:
        """Write (or skip) the shadow queue for this cycle."""
        try:
            mode = resolve_daemon_agentic_queue_mode(
                getattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "off")
            )
            execution_mode = str(getattr(settings, "ROBINHOOD_EXECUTION_MODE", "off") or "off")
            output_dir = settings.OUTPUT_DIR
            self.write_queue(ctx, mode=mode, execution_mode=execution_mode, output_dir=output_dir)
        except Exception as exc:  # noqa: BLE001 - must never fail the cycle
            telemetry.warning("Agentic queue step failed (non-critical): %s", exc)

    @staticmethod
    def skip_reason(ctx: RunContext) -> Optional[str]:
        """Why this cycle must not produce a queue, or None."""
        if ctx.stopped:
            return f"the cycle stopped ({ctx.stop_reason or 'no reason recorded'})"
        if ctx.context_extras.get("data_is_synthetic"):
            return "this cycle fell back to synthetic MockDataEngine data"
        if not ctx.context_extras.get("advisory_overlay_ok"):
            return "the advisory overlay did not complete this cycle"
        if not ctx.recommendations:
            return "there are no recommendations"
        return None

    def write_queue(
        self,
        ctx: RunContext,
        *,
        mode: str,
        execution_mode: str,
        output_dir: Any,
    ) -> Optional[Path]:
        """Write the shadow advisory source and queue for ``mode``.

        Returns the shadow ``execution_queue.json`` path, or None when nothing
        was written (mode off, a skip reason, a refused path, or
        ``compose_and_emit`` writing nothing, e.g. ``execution_mode=off``).
        """
        mode = resolve_daemon_agentic_queue_mode(mode)
        if mode == "off":
            telemetry.debug("DAEMON_AGENTIC_QUEUE_MODE=off — daemon writes no execution queue.")
            return None
        if mode == "primary":
            telemetry.warning(
                "DAEMON_AGENTIC_QUEUE_MODE=primary is not implemented until step 5.3; "
                "writing the SHADOW queue only. The real execution_queue.json is untouched "
                "and main.py is still its writer."
            )

        reason = self.skip_reason(ctx)
        if reason is not None:
            telemetry.info("Shadow agentic queue skipped: %s.", reason)
            return None

        real_dir = Path(output_dir)
        shadow_dir = shadow_output_dir(real_dir)
        shadow_dir.mkdir(parents=True, exist_ok=True)
        collision = shadow_collision_reason(real_dir, shadow_dir)
        if collision is not None:
            telemetry.error("Shadow agentic queue refused: %s.", collision)
            return None

        from execution.compose import compose_and_emit, write_advisory_source

        now = self._clock() if self._clock is not None else datetime.now(timezone.utc)
        source_path = write_advisory_source(ctx.recommendations, output_dir=shadow_dir, now=now)
        if source_path is None:
            telemetry.warning("Shadow agentic queue: advisory source write failed; no queue composed.")
            return None
        queue_path = compose_and_emit(
            ctx.snapshot,
            output_dir=shadow_dir,
            mode=execution_mode,
            now=now,
            macro_dto=ctx.macro_dto,
            side_effects=False,
        )
        if queue_path is None:
            telemetry.info(
                "Shadow agentic queue: advisory source written to %s; no queue composed "
                "(ROBINHOOD_EXECUTION_MODE=%s, or nothing composable -- any previous shadow "
                "queue is left in place, as main.py leaves the real one).",
                source_path, execution_mode,
            )
        else:
            telemetry.info("Shadow agentic queue written → %s", queue_path)
        archive_shadow_run(shadow_dir, now, source_path, queue_path)
        return queue_path


class BrokerExecutionStep(PipelineStep):
    """Executes gated paper/live orders with the Alpaca-API broker surface
    (``main_orchestrator._execute_broker_orders``) from the strategy Kelly
    targets. Separate from the Robinhood queue (``AgenticQueueStep``).

    Before step 5.2 this step also ran the advisory overlay; that now lives in
    ``AdvisoryOverlayStep``, which runs first.
    """
    name = "execution"

    async def run(self, ctx: RunContext) -> None:
        """Execute gated BUY/SELL orders through the broker (skipped without credentials)."""
        import main_orchestrator

        if ctx.dashboard_df is None or ctx.dashboard_df.empty:
            return

        # 6. Broker Execution
        effective_dry_run = ctx.force_account # Or pass it in context

        # Synthetic-data gate (Finding 1): a cycle that fell back to
        # MockDataEngine (AsyncDataFetchStep's fail-safe branch, triggered by
        # a total market-data outage) must NEVER submit a live/paper broker
        # order priced off flat $10 fabricated data. Checked BEFORE the
        # ADVISORY_ONLY/broker-credential branches below, and returns
        # unconditionally without calling main_orchestrator._execute_broker_
        # orders. This never touches broker-selection code -- that selection
        # happens one layer deeper inside _execute_broker_orders, keyed off
        # settings.BROKER_BACKEND -- so it protects AlpacaBroker and
        # FMPPaperBroker identically.
        if ctx.context_extras.get("data_is_synthetic"):
            telemetry.critical(
                "Synthetic (MockDataEngine) data detected for this cycle -- "
                "skipping broker execution entirely regardless of "
                "ADVISORY_ONLY/broker credentials to avoid submitting orders "
                "off fabricated prices."
            )
            return

        if getattr(settings, "ADVISORY_ONLY", True):
            telemetry.info(
                "📋 ADVISORY_ONLY=True — pipeline produced %d signals; broker "
                "execution is disabled for this run.",
                0 if ctx.dashboard_df is None else len(ctx.dashboard_df),
            )
        elif not ctx.dashboard_df.empty and settings.ALPACA_API_KEY and settings.ALPACA_SECRET_KEY:
            await main_orchestrator._execute_broker_orders(ctx.dashboard_df, effective_dry_run, macro_dto=ctx.macro_dto)
        elif not ctx.dashboard_df.empty:
            telemetry.info(
                "ALPACA_API_KEY/SECRET_KEY not configured; skipping broker execution. "
                "Set them in .env to enable live/paper order submission."
            )


class StateSnapshotStep(PipelineStep):
    """Saves state snapshots for telemetry and UI, and renders Jinja reports."""
    name = "snapshot"
    
    def run(self, ctx: RunContext) -> None:
        """Write state snapshots for telemetry/UI and render the Jinja HTML report."""
        from main_orchestrator import _write_state_snapshot, generate_plotly_volatility_bands, generate_html_report, json
        
        out_dir = str(settings.OUTPUT_DIR)
        if ctx.symbols:
            primary_ticker = ctx.symbols[0]
            primary_hist = ctx.tech_raw.get(primary_ticker)
            if primary_hist is not None and not primary_hist.empty:
                try:
                    plotly_df = primary_hist.copy()
                    plotly_df.columns = [col.lower() for col in plotly_df.columns]
                    generate_plotly_volatility_bands(plotly_df, primary_ticker, os.path.join(out_dir, "volatility_bands_dashboard.html"))
                except Exception as plot_err:
                    telemetry.warning(f"Failed to generate interactive Plotly chart: {plot_err}")

        _write_state_snapshot(
            ctx.macro_raw, ctx.dashboard_df, ctx.symbols,
            macro_kill_switch=getattr(ctx.macro_dto, "killSwitch", None),
            hmm_regime_state=getattr(ctx.macro_dto, "hmm_regime_state", None),
            universe_funnel=ctx.context_extras.get("universe_funnel"),
        )

        # Persist the optional Pilots-PWA pairs radar artifact. Opt-in
        # (settings.PAIRS_SNAPSHOT_ENABLED, default False) and
        # dead-letter-guarded: a failure here NEVER affects the pipeline
        # (CONSTRAINT #6). The options premium matrix used to be written here
        # too; it left core with the options desk (2026-09, step 3d).
        try:
            from reporting.pairs_snapshot import write_pairs_snapshot

            write_pairs_snapshot(ctx.symbols)
        except Exception as pairs_err:  # noqa: BLE001
            telemetry.warning(f"Pairs radar snapshot skipped: {pairs_err}")

        # Jinja HTML report
        try:
            portfolio_dicts = ctx.dashboard_df.to_dict(orient="records")
            for row in portfolio_dicts:
                if "Max Drawdown" in row:
                    row["Max_Drawdown"] = row["Max Drawdown"]
            yield_curve_val = float(ctx.macro_raw.get('T10Y2Y', 0.5))
            credit_spread_val = float(ctx.macro_raw.get('BAMLH0A0HYM2', 3.5))
            sahm_rule_val = float(ctx.macro_raw.get('SAHMREALTIME', 0.3))
            real_yield_val = float(ctx.macro_raw.get('DGS10', 4.0)) - float(ctx.macro_raw.get('CPIAUCSL_YoY', 2.0))
            regime_val = ctx.dashboard_df["Macro Status"].iloc[0] if "Macro Status" in ctx.dashboard_df.columns else "NEUTRAL"
            
            snapshot_diff_payload = None
            try:
                from scripts.snapshot_diff import compute_diff_from_history
                diff = compute_diff_from_history(
                    settings.OUTPUT_DIR,
                    conviction_delta_threshold=settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD,
                )
                if diff.prev_ts is not None or diff.curr_ts is not None:
                    snapshot_diff_payload = diff.to_dict()
            except Exception as diff_exc:
                telemetry.debug("Δ-band diff unavailable: %s", diff_exc)

            generate_html_report(
                portfolio_dicts,
                regime_val,
                os.path.join(out_dir, "daily_report_dashboard.html"),
                yield_curve=yield_curve_val,
                credit_spread=credit_spread_val,
                sahm_rule=sahm_rule_val,
                real_yield=real_yield_val,
                snapshot_diff=snapshot_diff_payload,
            )
        except Exception as html_err:
            telemetry.warning(f"Failed to generate daily HTML report: {html_err}")

        # Export Final JSON Payload Representation
        if not ctx.dashboard_df.empty:
            # "Option Strategy" / "True_IVR" left this payload with the 2026-09
            # schema trim (step 4f); both were always blank/NaN by then.
            payload_cols = ["Symbol", "Price", "Action Signal", "buyRange", "sellRange",
                            "Kelly Target", "GARCH_Vol"]
            for ac in ("Advisory_Action", "Advisory_Conviction",
                        "Advisory_Rationale", "Advisory_Position_Pct", "Advisory_Data_Quality"):
                if ac in ctx.dashboard_df.columns:
                    payload_cols.append(ac)
            output_payload = ctx.dashboard_df[payload_cols].to_dict(orient="records")
            print("\n=== FINAL ACTIONABLE PAYLOAD REPRESENTATION ===")
            print(json.dumps(output_payload, indent=4))
            print("================================================\n")

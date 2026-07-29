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

class AsyncDataFetchStep(PipelineStep):
    """Fetches macro, fundamentals, and technicals concurrently."""
    name = "data"
    
    async def run(self, ctx: RunContext) -> None:
        """Fetch macro, fundamentals, and technicals concurrently into the RunContext."""
        import main_orchestrator
        from data_engine import DataEngine, MockDataEngine
        from dto_models import RobinhoodPositionDTO

        settings.warn_if_fred_key_leaked(telemetry)

        # Initialize data engine
        de = ctx.market
        if de is None:
            creds_exist = os.path.exists("credentials.json")
            if creds_exist:
                settings.ensure_fred_configured()
                de = DataEngine(settings.FRED_API_KEY)
                ctx.symbols = list(settings.DEFAULT_TICKERS)
            else:
                telemetry.warning("credentials.json not found. Operating with deterministic MockDataEngine.")
                de = MockDataEngine()
                ctx.symbols = ["AAPL"]
            ctx.market = de
        else:
            ctx.symbols = list(settings.DEFAULT_TICKERS)

        # Integrate Robinhood Holdings
        rh_positions = {}
        try:
            snapshot = await asyncio.to_thread(main_orchestrator.fetch_account_snapshot)
            rh_positions = main_orchestrator.account_snapshot_to_robinhood_positions(snapshot)
            if rh_positions:
                for tk in rh_positions.keys():
                    if tk not in ctx.symbols:
                        ctx.symbols.append(tk)
        except Exception as rh_exc:
            telemetry.warning(
                f"Robinhood account snapshot unavailable: {rh_exc}; "
                "proceeding without holdings-aware overlay."
            )
        ctx.context_extras["robinhood_positions"] = rh_positions

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
        else:
            # Real (non-mock) data landed — stamp the cross-cycle freshness
            # marker so the daemon's interval gate (DATA_FRESHNESS_TTL_SECONDS)
            # can skip the next few pulls. The mock fallback above deliberately
            # does NOT stamp it, so an offline blip re-tries on the next cycle
            # rather than being treated as a fresh pull.
            main_orchestrator._mark_data_refreshed()

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


class OptionsAnalysisStep(PipelineStep):
    """Calculates options and GARCH."""
    name = "macro_options"
    
    def run(self, ctx: RunContext) -> None:
        """Compute per-ticker options metrics and GJR-GARCH volatility."""
        from main_orchestrator import MacroEngine, TechnicalOptionsEngine, IVHistoryStore, get_30d_atm_iv, calculate_true_ivr, get_vrp, MacroEconomicDTO
        from concurrent.futures import ThreadPoolExecutor

        if ctx.progress is not None:
            ctx.progress.start_stage("macro_options", symbols_total=len(ctx.symbols))

        engines = ctx.engine_context

        # Macro economic analysis
        telemetry.info("Routing data through Macro Engine...")
        me = (engines.macro_engine if engines is not None and engines.macro_engine is not None
              else MacroEngine(data_engine=ctx.market))
        sahm_val = me.calculate_sahm_rule()
        macro_data = me.run_macro_killswitch(ctx.macro_raw, sahm_val)
        
        hmm_risk_on_probability = me.compute_hmm_risk_on_probability(ctx.tech_raw.get('SPY'))
        ctx.macro_dto = MacroEconomicDTO(
            yield_curve_10y_2y=float(ctx.macro_raw.get('T10Y2Y', 0.5)),
            high_yield_oas=float(ctx.macro_raw.get('BAMLH0A0HYM2', 3.5)),
            inflation_rate=float(ctx.macro_raw.get('CPIAUCSL_YoY', 2.0)),
            nominal_10y=float(ctx.macro_raw.get('DGS10', 4.0)),
            vix_value=float(ctx.macro_raw.get('VIXCLS', 15.0)),
            sahm_rule_indicator=sahm_val,
            hmm_risk_on_probability=hmm_risk_on_probability,
        )

        # Technical Options Analysis
        telemetry.info("Routing data through Technical Options Engine...")
        toe = (engines.technical_options_engine
               if engines is not None and engines.technical_options_engine is not None
               else TechnicalOptionsEngine())
        iv_store = (engines.iv_history_store
                    if engines is not None and engines.iv_history_store is not None
                    else IVHistoryStore())
        
        tech_opt_indicators = {}

        def _options_one(ticker):
            df_hist = ctx.tech_raw.get(ticker)
            if df_hist is None or df_hist.empty:
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Options: {ticker} (no data)")
                return ticker, None, None
            try:
                indicators = toe.calculate_indicators(df_hist)
                vol = toe.estimate_gjr_garch_volatility(df_hist)
                realized_vol_rank = toe.calculate_realized_vol_rank(df_hist, vol)

                as_of_date = df_hist.index[-1].strftime("%Y-%m-%d")
                price_val = float(df_hist['Close'].iloc[-1])

                current_iv = float('nan')
                iv_record = None
                if ctx.market is not None:
                    current_iv = get_30d_atm_iv(ctx.market, ticker, as_of_date, spot_price=price_val)
                    if not np.isnan(current_iv):
                        iv_record = (ticker, as_of_date, current_iv)

                true_ivr = calculate_true_ivr(ticker, current_iv, as_of_date, iv_store)
                vrp = get_vrp(ticker, current_iv, vol)

                opt_strategy = toe.generate_option_strategy_matrix(
                    true_ivr=true_ivr if not np.isnan(true_ivr) else 50.0,
                    aroon_osc=indicators["Aroon_Oscillator"],
                    coppock_val=indicators["Coppock_Curve"],
                    stock_price=price_val,
                    current_iv=current_iv if not np.isnan(current_iv) else vol,
                    vrp=vrp,
                    macro_dto=ctx.macro_dto
                )
                result = {
                    "Aroon_Oscillator": indicators["Aroon_Oscillator"],
                    "Coppock_Curve": indicators["Coppock_Curve"],
                    "Chandelier_Long": indicators["Chandelier_Long"],
                    "Chandelier_Short": indicators["Chandelier_Short"],
                    "GARCH_Vol": vol,
                    "Realized_Vol_Rank": realized_vol_rank,
                    "True_IVR": true_ivr,
                    "VRP": vrp,
                    "Option_Strategy_Matrix": opt_strategy
                }
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Options: {ticker}")
                return ticker, result, iv_record
            except Exception as opt_exc:
                telemetry.warning(
                    f"Technical Options Analysis failed for {ticker}: {opt_exc}. "
                    f"Skipping options metrics for this ticker this cycle."
                )
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Options: {ticker} (failed)")
                return ticker, None, None

        opt_workers = min(int(getattr(settings, "FORECAST_MAX_CONCURRENCY", 8)), max(1, len(ctx.symbols)))
        if opt_workers <= 1 or len(ctx.symbols) <= 1:
            opt_results = [_options_one(t) for t in ctx.symbols]
        else:
            with ThreadPoolExecutor(max_workers=opt_workers) as opt_pool:
                opt_results = list(opt_pool.map(_options_one, ctx.symbols))

        for tk, res, iv_rec in opt_results:
            if iv_rec is not None:
                iv_store.record_iv(iv_rec[0], iv_rec[1], iv_rec[2])
            if res is not None:
                tech_opt_indicators[tk] = res
        
        ctx.context_extras["tech_opt_indicators"] = tech_opt_indicators


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
            if data and 'info' in data:
                fund_dtos[ticker] = FundamentalDataDTO.from_raw_dict(ticker, data['info'], dividends=data.get('dividends'))

        ctx.context_extras["fund_dtos"] = fund_dtos

        realized_vol_60d_map = {
            ticker: metrics.get('Realized_Vol_60D', float('nan'))
            for ticker, metrics in tech_metrics.items()
        }
        fund_metrics = pe.calculate_fundamental_metrics(fund_dtos, realized_vol_60d_map=realized_vol_60d_map)
        
        ctx.dashboard_df = pe.compile_dashboard(tech_metrics, fund_metrics, regime_metrics)

        tech_opt_indicators = ctx.context_extras.get("tech_opt_indicators", {})
        ctx.dashboard_df['GARCH_Vol'] = 0.0
        ctx.dashboard_df['Realized_Vol_Rank'] = 0.0
        ctx.dashboard_df['True_IVR'] = 0.0
        ctx.dashboard_df['VRP'] = 0.0
        ctx.dashboard_df['Aroon Oscillator'] = 0.0
        ctx.dashboard_df['Coppock Curve'] = 0.0
        ctx.dashboard_df['Chandelier Exit'] = 0.0
        
        for col_key, mapped_key in [
            ('GARCH_Vol', 'GARCH_Vol'),
            ('Realized_Vol_Rank', 'Realized_Vol_Rank'),
            ('True_IVR', 'True_IVR'),
            ('VRP', 'VRP'),
            ('Aroon Oscillator', 'Aroon_Oscillator'),
            ('Coppock Curve', 'Coppock_Curve'),
            ('Chandelier Exit', 'Chandelier_Long')
        ]:
            ctx.dashboard_df[col_key] = ctx.dashboard_df['Symbol'].map(lambda x: tech_opt_indicators.get(x, {}).get(mapped_key, 0.0))


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
        fallback_tracker = ForecastTracker() if settings.FORECAST_SKILL_WEIGHTING_ENABLED else None
        fe = (engines.forecasting_engine if engines is not None and engines.forecasting_engine is not None
              else ForecastingEngine(tracker=fallback_tracker))
        
        forecast_cols = ['Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
                         'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90',
                         'Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper']

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
                precomputed_garch = float(row.get('GARCH_Vol', 0.0))
                forecasts = fe.generate_forecast(
                    row, price, history_series, history_df=history_df,
                    precomputed_garch_annual_vol=precomputed_garch,
                )
                if ctx.progress is not None:
                    ctx.progress.advance_symbol(f"Forecasting: {ticker}")
                return ticker, forecasts
            except Exception as ml_err:
                telemetry.warning(f"Forecasting Engine failure for {ticker}: {ml_err}. Reverting to baseline default.")
                mu = 0.0002
                sigma = 0.015
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
                    'Forecast_30_Prophet_Lower': mc_low,
                    'Forecast_30_Prophet_Upper': mc_high
                }

        workers = max(1, int(getattr(settings, "FORECAST_MAX_CONCURRENCY", 8)))
        rows = [row for _, row in ctx.dashboard_df.iterrows()]
        if workers == 1 or len(rows) <= 1:
            pairs = [_forecast_one(r) for r in rows]
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(rows))) as pool:
                pairs = list(pool.map(_forecast_one, rows))
        forecast_results = {tk: fc for tk, fc in pairs if fc is not None}

        for col in forecast_cols:
            ctx.dashboard_df[col] = ctx.dashboard_df['Symbol'].map(lambda x: forecast_results.get(x, {}).get(col, 0.0))


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


_ETF_TRANSMISSION_COLUMNS = (
    'ETF_Ownership_Pct',
    'ETF_Comovement_R2',
    'ETF_Primary_Wrapper',
)


def _apply_etf_transmission(
    dashboard_df: pd.DataFrame, tech_raw: dict[str, pd.DataFrame],
) -> None:
    """Populate the three ETF volatility-transmission MEASUREMENT columns.

    Ben-David, Franzoni & Moussawi (2018), "Do ETFs Increase Volatility?",
    *Journal of Finance* 73(6). ETF arbitrage transmits a shock in one
    constituent to its healthy basket peers, so a heavily ETF-wrapped name
    carries extra non-fundamental, non-diversifiable variance. The math lives
    in ``risk/etf_transmission.py`` (pure, zero-I/O); this function owns every
    network call and every settings gate.

    **Diagnostic only.** Nothing in scoring, sizing, or execution reads these
    columns as of this commit -- a sibling PR wires them into position sizing.

    Deliberately a module-level function (not inlined in ``StrategyEvalStep.run``)
    following the ``_apply_sector_heat_factor`` template directly above, for the
    same two reasons: it stays importable/testable without ``main_orchestrator``'s
    heavy top-level import chain, and it logs via this module's own plain
    ``logger`` rather than the ``telemetry`` proxy (whose ``__getattr__`` lazily
    imports ``main_orchestrator`` and therefore its whole engine chain on first
    attribute access, defeating the light import footprint).

    NaN-fills all three columns FIRST, before any branch, so every early-return
    path -- disabled gate, missing market proxy, holdings-provider absent,
    total failure -- leaves genuinely-missing cells NaN rather than a fabricated
    default (CONSTRAINT #4). Never raises (CONSTRAINT #6).

    Honesty contract -- every one of these is NaN, never 0.0: ticker in no
    covered ETF; holdings fetch failed; gate off; ``Market Cap`` is the
    fabricated ``0.0`` that ``FundamentalDataDTO`` defaults to; fewer than
    ``ETF_TRANSMISSION_MIN_OBS`` overlapping bars; the composite is market-proxy-
    only (residual identically zero); the ticker is ITSELF an ETF. The
    fallback count is logged ONCE per cycle at INFO -- never once per name,
    because 40 warnings a cycle is how a real signal gets ignored.
    """
    for col in _ETF_TRANSMISSION_COLUMNS:
        dashboard_df[col] = float('nan')

    if not getattr(settings, "ETF_TRANSMISSION_ENABLED", False):
        return
    if dashboard_df.empty or 'Symbol' not in dashboard_df.columns:
        return

    try:
        from data.etf_holdings import get_etf_holdings
        from risk.etf_transmission import (
            build_etf_return_composite,
            compute_etf_ownership,
            compute_market_residual_r2,
            filter_holdings_as_of,
            primary_wrapper,
        )

        market_proxy = str(getattr(settings, "ETF_HOLDINGS_MARKET_PROXY", "SPY")).upper().strip()
        wrappers = [
            str(s).upper().strip()
            for s in (getattr(settings, "ETF_TRANSMISSION_WRAPPERS", None) or [])
            if str(s).strip()
        ]
        if market_proxy and market_proxy not in wrappers:
            wrappers.append(market_proxy)
        if not wrappers:
            return

        universe = [
            str(s).upper().strip() for s in dashboard_df['Symbol'].dropna().tolist()
            if str(s).strip()
        ]
        # A ticker that is ITSELF an ETF scores 1.0/1.0 against its own basket
        # -- maximum derate for a trivially wrong reason. Explicit exclusion.
        excluded = set(wrappers) | {
            str(s).upper().strip()
            for s in (getattr(settings, "ETF_TRANSMISSION_EXCLUDED_SYMBOLS", None) or [])
            if str(s).strip()
        }
        measurable = [s for s in universe if s not in excluded]
        if not measurable:
            return

        market_df = (tech_raw or {}).get(market_proxy)
        if market_df is None or getattr(market_df, "empty", True):
            logger.info(
                "ETF transmission: market proxy %s absent from tech_raw; "
                "all %d measurable symbols degrade to NaN this cycle.",
                market_proxy, len(measurable),
            )
            return

        as_of = pd.Timestamp(datetime.now(timezone.utc)).date()
        raw_holdings = get_etf_holdings(wrappers, as_of=as_of) or {}
        # Belt-and-suspenders causality: a basket row stamped after this
        # cycle's as-of date must never enter the measurement regardless of
        # what the provider returned. Also collapses duplicate/multi-snapshot
        # rows so ownership can't be double-counted.
        holdings = filter_holdings_as_of(raw_holdings, as_of=as_of)
        # Only the operator universe matters -- SPY alone carries ~500 rows.
        measurable_set = set(measurable)
        holdings = {
            etf: [
                row for row in rows
                if str(getattr(row, "holding_symbol", "") or "").upper().strip() in measurable_set
            ]
            for etf, rows in holdings.items()
        }
        covered_etfs = sorted({etf for etf, rows in holdings.items() if rows})
        if not covered_etfs:
            logger.info(
                "ETF transmission: no covered basket rows for any of %d measurable "
                "symbols; all three columns stay NaN this cycle.", len(measurable),
            )
            return

        # ── ETF_Ownership_Pct ────────────────────────────────────────────────
        # shares_out ~= Market Cap / Price. GUARDED on both being > 0:
        # FundamentalDataDTO.market_cap defaults to a fabricated 0.0, so a
        # naive divide yields inf on exactly the names whose fundamentals
        # failed. Follow-up (deliberately NOT built here):
        # dei:EntityCommonStockSharesOutstanding is already parsed by
        # data/edgar_fundamentals.py::extract_shares and is PIT-dated.
        shares_out: dict = {}
        _mcap_col = 'Market Cap' if 'Market Cap' in dashboard_df.columns else None
        _price_col = 'Price' if 'Price' in dashboard_df.columns else None
        if _mcap_col and _price_col:
            for _, row in dashboard_df.iterrows():
                sym = str(row.get('Symbol', '') or '').upper().strip()
                if not sym:
                    continue
                try:
                    mcap = float(row.get(_mcap_col))
                    price = float(row.get(_price_col))
                except (TypeError, ValueError):
                    continue
                if mcap > 0.0 and price > 0.0:
                    shares_out[sym] = mcap / price

        ownership = compute_etf_ownership(
            holdings, shares_out, exclude_symbols=frozenset(excluded),
        )

        # ── ETF_Comovement_R2 ────────────────────────────────────────────────
        # ETF price bars go through the existing fetch_technical_raw_cached
        # path (HistoricalStore-backed, incremental) -- deliberately NOT a
        # second batched yf.download; research_engine.fetch_returns_for_clustering
        # is the only one of those in the repo, on purpose.
        etf_bars = {e: (tech_raw or {}).get(e) for e in covered_etfs if (tech_raw or {}).get(e) is not None}
        missing_bars = [e for e in covered_etfs if e not in etf_bars and e != market_proxy]
        if missing_bars:
            from data_engine import DataEngine

            fetched = DataEngine(getattr(settings, "FRED_API_KEY", "")).fetch_technical_raw_cached(
                missing_bars
            ) or {}
            etf_bars.update({str(k).upper().strip(): v for k, v in fetched.items() if v is not None})

        composites = build_etf_return_composite(
            holdings, etf_bars, market_proxy=market_proxy,
        )

        window = int(getattr(settings, "ETF_TRANSMISSION_WINDOW_DAYS", 60))
        min_obs = int(getattr(settings, "ETF_TRANSMISSION_MIN_OBS", 60))
        r2_map: dict = {}
        no_composite = 0
        insufficient = 0
        for sym in measurable:
            composite = composites.get(sym)
            if composite is None or len(composite) == 0:
                no_composite += 1
                continue
            stock_df = (tech_raw or {}).get(sym)
            if stock_df is None or getattr(stock_df, "empty", True):
                insufficient += 1
                continue
            value = compute_market_residual_r2(
                stock_df, composite, market_df, window=window, min_obs=min_obs,
            )
            if value != value:  # NaN
                insufficient += 1
                continue
            r2_map[sym] = value

        wrappers_map = primary_wrapper(holdings)

        _upper = dashboard_df['Symbol'].astype(str).str.upper().str.strip()
        dashboard_df['ETF_Ownership_Pct'] = _upper.map(ownership)
        dashboard_df['ETF_Comovement_R2'] = _upper.map(r2_map)
        dashboard_df['ETF_Primary_Wrapper'] = _upper.map(wrappers_map)

        # ONE INFO line per cycle with counts -- never one warning per name.
        logger.info(
            "ETF transmission: %d/%d symbols measured (R2); %d with no covered "
            "non-market wrapper, %d with insufficient/degenerate overlap; "
            "%d ownership values, %d primary-wrapper labels; %d symbols excluded "
            "as funds.",
            len(r2_map), len(measurable), no_composite, insufficient,
            sum(1 for v in ownership.values() if v == v), len(wrappers_map),
            len(universe) - len(measurable),
        )

    except Exception as exc:
        logger.warning("ETF transmission computation failed (non-fatal): %s", exc)
        for col in _ETF_TRANSMISSION_COLUMNS:
            dashboard_df[col] = float('nan')

def _apply_etf_transmission_multiplier(dashboard_df: pd.DataFrame) -> None:
    """Populate the ``ETF_Transmission_Multiplier`` column from the measured
    ``ETF_Ownership_Pct`` / ``ETF_Comovement_R2`` columns.

    NaN-fills the column FIRST (identical pattern to
    ``_apply_sector_heat_factor`` above), so the DISABLED state is an honest
    "never computed" rather than a fabricated 1.0 that a reader would
    mistake for a measured "no transmission risk here" (CONSTRAINT #4).

    Where the feature IS enabled, every row gets a real float and NEVER a
    NaN -- a name with no ETF coverage this cycle gets exactly ``1.0``, the
    no-op. That asymmetry is deliberate and load-bearing: a NaN multiplier
    would make ``final_weight`` non-finite, and
    ``sizing.position_sizer.apply_portfolio_gross_cap`` EXCLUDES non-finite
    weights from its gross-exposure sum -- so a broad coverage gap would
    shrink the gross denominator and silently LOOSEN the portfolio-wide cap
    for every name that DID have coverage. A data outage must never relax a
    risk limit. See ``risk/etf_transmission.py``.

    Coverage-gap logging is once per cycle with a COUNT, never per name --
    a 500-name universe with the feature newly enabled and no holdings data
    yet would otherwise emit 500 identical log lines every refresh.

    Never raises (CONSTRAINT #6): any failure degrades the whole column back
    to NaN, which every consumer then reads as the 1.0 no-op.
    """
    dashboard_df['ETF_Transmission_Multiplier'] = float('nan')
    if not settings.ETF_TRANSMISSION_SIZING_ENABLED:
        return
    try:
        from risk.etf_transmission import transmission_multiplier

        # Missing measurement columns (e.g. ETF holdings ingestion not
        # configured) are treated exactly like present-but-NaN cells: every
        # row resolves to the 1.0 no-op, never NaN.
        ownership = (
            dashboard_df['ETF_Ownership_Pct'] if 'ETF_Ownership_Pct' in dashboard_df.columns
            else pd.Series(float('nan'), index=dashboard_df.index)
        )
        comovement = (
            dashboard_df['ETF_Comovement_R2'] if 'ETF_Comovement_R2' in dashboard_df.columns
            else pd.Series(float('nan'), index=dashboard_df.index)
        )
        multipliers = [
            transmission_multiplier(
                own, r2,
                max_derate=settings.ETF_TRANSMISSION_MAX_DERATE,
                ownership_reference=settings.ETF_TRANSMISSION_OWNERSHIP_REFERENCE,
                floor=settings.ETF_TRANSMISSION_MIN_MULTIPLIER,
            )
            for own, r2 in zip(ownership, comovement)
        ]
        dashboard_df['ETF_Transmission_Multiplier'] = multipliers

        uncovered = sum(
            1 for own, r2 in zip(ownership, comovement)
            if pd.isna(own) or pd.isna(r2)
        )
        if uncovered:
            logger.info(
                "ETF transmission derate: %d of %d symbol(s) had no ETF "
                "ownership/co-movement coverage this cycle and fall back to "
                "the 1.0 no-op multiplier (never NaN -- a NaN would exclude "
                "them from the portfolio gross-exposure sum and loosen the "
                "cap for every covered name).",
                uncovered, len(dashboard_df),
            )
    except Exception as exc:
        logger.warning("ETF transmission multiplier computation failed (non-fatal): %s", exc)
        dashboard_df['ETF_Transmission_Multiplier'] = float('nan')


def _build_etf_transmission_cov_matrix(
    symbols: list, tech_raw: dict,
) -> Optional[pd.DataFrame]:
    """ETF-co-ownership-inflated covariance matrix for the portfolio gross cap.

    Feeds ``sizing.position_sizer.apply_portfolio_gross_cap``'s EXISTING
    ``cov_matrix``/``target_vol`` risk-aware path (built for exactly this
    purpose, previously unreachable from production -- see
    ``sizing/position_sizer.py``'s "Reduction-only guarantee" section)
    rather than building a second portfolio-cap mechanism.

    Gated on ``settings.ETF_TRANSMISSION_PORTFOLIO_ENABLED`` (default
    ``False``): returns ``None`` immediately when off, which the caller reads
    as "use the existing sum-of-|weight| fallback", byte-identical to
    pre-feature behavior. Never raises (CONSTRAINT #6) -- any failure
    degrades to ``None``, the same fallback signal as the gate being off.

    Deliberately does NOT return a partially-covered matrix. A symbol
    missing from ``cov_matrix`` is not merely excluded from
    ``portfolio_vol_target``'s risk estimate -- it is explicitly zeroed out
    of the returned weights (that function's own documented, correct
    behavior for an unknowable-risk name). Silently zeroing a coverage-gapped
    name's ENTIRE position because it lacked 60 days of overlapping bars
    would be a far harsher, more surprising outcome than the existing
    sum-of-|weight| fallback this feature is opt-in to replace, so this
    function insists on FULL coverage across ``symbols`` before returning
    anything other than ``None``.
    """
    if not getattr(settings, "ETF_TRANSMISSION_PORTFOLIO_ENABLED", False):
        return None
    try:
        from data.etf_holdings import get_etf_holdings
        from risk.etf_transmission import build_transmission_adjusted_cov, filter_holdings_as_of

        market_proxy = str(getattr(settings, "ETF_HOLDINGS_MARKET_PROXY", "SPY")).upper().strip()
        wrappers = [
            str(s).upper().strip()
            for s in (getattr(settings, "ETF_TRANSMISSION_WRAPPERS", None) or [])
            if str(s).strip()
        ]
        if market_proxy and market_proxy not in wrappers:
            wrappers.append(market_proxy)
        if not wrappers:
            return None

        universe = [str(s).upper().strip() for s in symbols if str(s).strip()]
        if len(universe) < 2:
            return None

        as_of = pd.Timestamp(datetime.now(timezone.utc)).date()
        raw_holdings = get_etf_holdings(wrappers, as_of=as_of) or {}
        holdings = filter_holdings_as_of(raw_holdings, as_of=as_of)
        if not any(rows for rows in holdings.values()):
            logger.info(
                "ETF transmission portfolio cov: no covered basket rows for "
                "any of %d symbols; falling back to the sum-of-|weight| "
                "gross cap this cycle.", len(universe),
            )
            return None

        # Every symbol in `universe` needs an aligned Close series -- a
        # partial matrix would let portfolio_vol_target zero out whichever
        # names lack one, which is worse than just not using the cov path
        # this cycle. Inner-join across the whole requested universe, not
        # per-pair, so coverage is all-or-nothing and easy to reason about.
        closes = {}
        for sym in universe:
            df = (tech_raw or {}).get(sym)
            if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
                continue
            closes[sym] = df["Close"]
        missing = sorted(set(universe) - set(closes))
        if missing:
            logger.info(
                "ETF transmission portfolio cov: %d of %d symbols lack price "
                "bars in tech_raw (%s); falling back to the sum-of-|weight| "
                "gross cap this cycle rather than zeroing their exposure via "
                "a partially-covered covariance matrix.",
                len(missing), len(universe), missing,
            )
            return None

        window = int(getattr(settings, "ETF_TRANSMISSION_COV_WINDOW_DAYS", 60))
        price_df = pd.concat(closes, axis=1, join="inner").sort_index()
        returns_df = price_df.pct_change().dropna(how="any")
        if len(returns_df) < window:
            logger.info(
                "ETF transmission portfolio cov: only %d overlapping return "
                "observations across %d symbols (need >= %d); falling back "
                "to the sum-of-|weight| gross cap this cycle rather than "
                "estimating a covariance matrix off a short, noisy sample.",
                len(returns_df), len(closes), window,
            )
            return None

        inflation = float(getattr(settings, "ETF_TRANSMISSION_COV_INFLATION", 0.25))
        cov_matrix = build_transmission_adjusted_cov(
            returns_df, holdings, inflation=inflation, window=window,
        )
        if cov_matrix is None:
            logger.info(
                "ETF transmission portfolio cov: covariance build declined "
                "(see risk.etf_transmission.build_transmission_adjusted_cov); "
                "falling back to the sum-of-|weight| gross cap this cycle."
            )
        return cov_matrix
    except Exception as exc:
        logger.warning("ETF transmission portfolio covariance build failed (non-fatal): %s", exc)
        return None


class StrategyEvalStep(PipelineStep):
    """Evaluates strategy and overlaying advisory logic."""
    name = "strategy"
    
    def run(self, ctx: RunContext) -> None:
        """Evaluate the strategy and apply the holding-aware advisory overlay."""
        from main_orchestrator import StrategyEngine, EvaluationEngine, MarketBarDTO, FundamentalDataDTO, compute_xsec_momentum_ranks, global_registry, SignalContext, DualMomentumAllocator

        telemetry.info("Routing data through Strategy and Evaluation Engines...")
        if ctx.progress is not None:
            ctx.progress.start_stage("strategy", symbols_total=len(ctx.dashboard_df))

        engines = ctx.engine_context
        se = (engines.strategy_engine if engines is not None and engines.strategy_engine is not None
              else StrategyEngine())
        ee = (engines.evaluation_engine if engines is not None and engines.evaluation_engine is not None
              else EvaluationEngine())

        ctx.dashboard_df['XSec_12_1M'] = float('nan')
        ctx.dashboard_df['XSec_Momentum_Rank'] = float('nan')

        xsec_rank_series = compute_xsec_momentum_ranks(ctx.tech_raw)

        xsec_return_dict: dict = {}
        for ticker_i, df_i in ctx.tech_raw.items():
            if df_i is None or df_i.empty or 'Close' not in df_i.columns:
                continue
            close_i = df_i['Close'].dropna()
            required_i = 252 + 22 + 1
            if len(close_i) < required_i:
                continue
            p_recent_i = float(close_i.iloc[-23])   # t - 22
            p_old_i = float(close_i.iloc[-253])      # t - 252
            if p_old_i > 0:
                xsec_return_dict[ticker_i] = p_recent_i / p_old_i - 1.0

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

                pit_df = ctx.dashboard_df.copy()
                if 'Symbol' in pit_df.columns:
                    pit_df = pit_df.set_index('Symbol')
                pit_vix = getattr(ctx.macro_dto, 'vix_value', None)
                pit_as_of = pd.Timestamp(datetime.now(timezone.utc)).normalize()
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

        # ETF volatility transmission (Ben-David, Franzoni & Moussawi 2018) --
        # three measurement columns (ETF_Ownership_Pct / ETF_Comovement_R2 /
        # ETF_Primary_Wrapper). Placed here rather than before
        # run_pre_compute() above deliberately: nothing in pre_compute
        # consumes these columns, so moving a networked call earlier in the
        # critical path buys nothing. A complete no-op (zero network calls,
        # all three columns NaN) while settings.ETF_TRANSMISSION_ENABLED is
        # False. See risk/etf_transmission.py and
        # docs/signals/etf_transmission.md.
        _apply_etf_transmission(ctx.dashboard_df, ctx.tech_raw)

        # ETF-arbitrage volatility-transmission SIZING DERATE, built on the
        # measurement columns just populated above -- must run AFTER
        # _apply_etf_transmission() so ETF_Ownership_Pct/ETF_Comovement_R2 are
        # already resolved for this cycle, and BEFORE the per-ticker
        # evaluate_security loop below so each row can simply read its own
        # already-resolved multiplier. NaN-filled (never fabricated --
        # CONSTRAINT #4) when settings.ETF_TRANSMISSION_SIZING_ENABLED is
        # False, which every consumer reads as the exact 1.0 no-op, making
        # the disabled path byte-identical to the pre-feature behavior.
        _apply_etf_transmission_multiplier(ctx.dashboard_df)

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

        # docs/CONFIG_SCHEMA_PLAN.md Phase C1 — five ADVISORY METADATA columns
        # (config.COLUMN_SCHEMA's "# --- ADVISORY METADATA ---" section) are
        # populated only by the advisory path (engine/advisory.py via
        # reporting/sheet_publisher.py::rec_to_sheet_row); this orchestrator
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
                         'Option Strategy', 'buyRange', 'sellRange', 'Strategy Explainer Notes',
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
        tech_opt_indicators = ctx.context_extras.get("tech_opt_indicators", {})
        robinhood_positions = ctx.context_extras.get("robinhood_positions", {})

        # -- Vectorized Signal Aggregation --
        vec_df = pd.DataFrame(index=ctx.dashboard_df['Symbol'].values)
        vec_df['forecast_price'] = ctx.dashboard_df.get('Forecast_30', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
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
        
        vec_df['chandelier_long'] = ctx.dashboard_df['Symbol'].map(lambda x: tech_opt_indicators.get(x, {}).get('Chandelier_Long', 0.0)).values
        vec_df['chandelier_short'] = ctx.dashboard_df['Symbol'].map(lambda x: tech_opt_indicators.get(x, {}).get('Chandelier_Short', 0.0)).values
        
        vec_df['current_price'] = ctx.dashboard_df.get('Price', pd.Series(0.0, index=ctx.dashboard_df.index)).fillna(0.0).values
        vec_df['Close'] = vec_df['current_price']
        vec_df['ticker'] = ctx.dashboard_df['Symbol'].values
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
        sig_ctx = SignalContext(bar=dummy_bar, fundamentals=dummy_fund, macro=ctx.macro_dto, multifactor_scores=shared_context.multifactor_scores)
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

        for idx, row in ctx.dashboard_df.iterrows():
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

                chan_long = 0.0
                chan_short = 0.0
                if ticker in tech_opt_indicators:
                    chan_long = tech_opt_indicators[ticker].get('Chandelier_Long', 0.0)
                    chan_short = tech_opt_indicators[ticker].get('Chandelier_Short', 0.0)

                strategy_output = se.evaluate_security(
                    bar=bar_dto,
                    fundamentals=fund_dto,
                    macro=ctx.macro_dto,
                    forecast_price=row.get('Forecast_30', 0.0),
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
                    # Per-name ETF volatility-transmission derate resolved by
                    # _apply_etf_transmission_multiplier() before this loop.
                    # Bare .get() -- a missing column / NaN cell is passed
                    # through verbatim and sanitized to the exact 1.0 no-op
                    # inside size_position(), the single place that decides
                    # what "missing" means here (CONSTRAINT #7).
                    etf_transmission_multiplier=row.get('ETF_Transmission_Multiplier'),
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
                    # bool/Optional[str] into the Sheet-friendly text convention
                    # ("Yes"/"No" + the constraint name or "") that every other
                    # string strategy_col in this loop already defaults to.
                    'Sizing_Was_Capped': "Yes" if strategy_output.get('Sizing_Was_Capped') else "No",
                    'Sizing_Binding_Constraint': strategy_output.get('Sizing_Binding_Constraint') or "",
                    'Option Strategy': tech_opt_indicators[ticker].get('Option_Strategy_Matrix', '') if ticker in tech_opt_indicators else strategy_output['Option Strategy'],
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
            'Option Strategy', 'buyRange', 'sellRange',
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
                # Symbol Detail screens, not a Sheets/HTML-report column or a
                # quant_platform.db field. Adding them to COLUMN_SCHEMA would
                # trigger a Sheets column + a DailySignals DDL migration for no
                # reason (config.get_headers() drives both; pandera is
                # strict=False so a non-schema column here is already safe).
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

        export_keys = ['MAE', 'MFE', 'Edge Ratio', 'Portfolio_Heat', 'BF_Allocation', 'BF_Selection']
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
        # ETF-transmission-adjusted covariance (settings.ETF_TRANSMISSION_
        # PORTFOLIO_ENABLED, default False): when enabled and full-coverage
        # data is available, routes through apply_portfolio_gross_cap's
        # EXISTING risk-aware cov_matrix/target_vol path instead of the
        # sum-of-|weight| fallback -- reuses settings.VOL_TARGET as
        # target_vol (the same setting the per-name vol-target sizing
        # fallback already uses) rather than introducing a second,
        # redundant target-vol setting. _build_etf_transmission_cov_matrix
        # returns None (byte-identical fallback) whenever the gate is off,
        # holdings/bars are unavailable, or coverage across this cycle's
        # universe is incomplete -- see that function's docstring for why a
        # partially-covered matrix is never substituted in its place.
        try:
            from sizing.position_sizer import apply_portfolio_gross_cap

            per_name = dict(zip(ctx.dashboard_df["Symbol"], ctx.dashboard_df["Kelly Target"]))
            cov_matrix = _build_etf_transmission_cov_matrix(
                list(per_name.keys()), ctx.tech_raw,
            )
            if cov_matrix is not None:
                cap_result = apply_portfolio_gross_cap(
                    per_name, max_gross=settings.MAX_PORTFOLIO_GROSS,
                    cov_matrix=cov_matrix, target_vol=settings.VOL_TARGET,
                )
            else:
                cap_result = apply_portfolio_gross_cap(per_name, max_gross=settings.MAX_PORTFOLIO_GROSS)
            if cap_result.was_capped:
                telemetry.info(
                    "Portfolio gross cap bound this cycle: scale_factor=%.4f "
                    "(max_gross=%.2f, method=%s).",
                    cap_result.scale_factor, settings.MAX_PORTFOLIO_GROSS, cap_result.method,
                )
                ctx.dashboard_df["Kelly Target"] = ctx.dashboard_df["Symbol"].map(
                    lambda x: cap_result.scaled_weights.get(x, per_name.get(x, 0.0))
                )
                # Only mark names whose weight actually moved (a 0.0 name is
                # trivially unaffected by a uniform scalar) -- avoids fabricating
                # a "capped" flag on a name that never had exposure to cap.
                _affected = ctx.dashboard_df["Symbol"].map(lambda x: abs(per_name.get(x, 0.0)) > 1e-9)
                ctx.dashboard_df.loc[_affected, "Sizing_Was_Capped"] = "Yes"
                ctx.dashboard_df.loc[_affected, "Sizing_Binding_Constraint"] = "portfolio_gross"
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
        try:
            if settings.SIZING_CAP_AUDIT_ENABLED and not ctx.dashboard_df.empty:
                from sizing.cap_audit_store import CapAuditStore

                cycle_id = datetime.now(timezone.utc).isoformat()
                events = []
                for _, row in ctx.dashboard_df.iterrows():
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


class BrokerExecutionStep(PipelineStep):
    """Executes trades with the broker."""
    name = "execution"
    
    async def run(self, ctx: RunContext) -> None:
        """Execute gated BUY/SELL orders through the broker (skipped without credentials)."""
        import main_orchestrator
        from data.market_data import get_provider as _get_market_provider
        from data.robinhood_portfolio import fetch_account_snapshot as _fetch_rh_snapshot
        from engine.advisory import evaluate as _advisory_evaluate
        from concurrent.futures import ThreadPoolExecutor

        if ctx.dashboard_df.empty:
            return

        # 3b. Advisory Evaluation
        try:
            _market_provider = _get_market_provider()
            shared_context = ctx.context_extras.get("shared_context")
            _context_extras = {
                'xsec_percentile_ranks': shared_context.xsec_percentile_ranks if shared_context else {},
                'multifactor_scores':    shared_context.multifactor_scores if shared_context else {},
            }

            _rh_snapshot = None
            try:
                _rh_snapshot = _fetch_rh_snapshot(max_age_hours=20.0)
            except Exception as _rh_exc:
                telemetry.warning(
                    "Advisory: Robinhood account snapshot unavailable (%s) — "
                    "position=None for all tickers; Kelly sizing still runs.", _rh_exc
                )

            for _col in ('Advisory_Action', 'Advisory_Conviction',
                         'Advisory_Rationale', 'Advisory_Position_Pct',
                         'Advisory_Data_Quality'):
                ctx.dashboard_df[_col] = ""
            ctx.dashboard_df['Advisory_Conviction'] = 0.0
            ctx.dashboard_df['Advisory_Position_Pct'] = 0.0

            _reuse_pipeline_compute = bool(
                getattr(settings, 'ADVISORY_REUSE_PIPELINE_COMPUTE', False)
            )

            def _eval_one(_ticker, _row):
                try:
                    _position = (
                        _rh_snapshot.positions.get(_ticker)
                        if _rh_snapshot is not None else None
                    )
                    _precomputed_garch = None
                    _precomputed_forecast = None
                    if _reuse_pipeline_compute:
                        _precomputed_garch = _row.get('GARCH_Vol')
                        _precomputed_forecast = _row.get('Forecast_30')
                    _rec = _advisory_evaluate(
                        symbol=_ticker,
                        position=_position,
                        market=_market_provider,
                        snapshot=_rh_snapshot,
                        macro_dto=ctx.macro_dto,
                        context_extras=_context_extras,
                        precomputed_garch=_precomputed_garch,
                        precomputed_forecast=_precomputed_forecast,
                    )
                    if ctx.progress is not None:
                        ctx.progress.advance_symbol(f"Advisory: {_ticker}")
                    return _ticker, {
                        'Advisory_Action': _rec.action,
                        'Advisory_Conviction': round(_rec.conviction, 4),
                        'Advisory_Rationale': _rec.rationale,
                        'Advisory_Position_Pct': round(_rec.suggested_position_pct, 6),
                        'Advisory_Data_Quality': _rec.data_quality
                    }
                except Exception as _adv_exc:
                    telemetry.warning("Advisory failed for %s: %s", _ticker, _adv_exc)
                    if ctx.progress is not None:
                        ctx.progress.advance_symbol(f"Advisory: {_ticker} (failed)")
                    return _ticker, None

            _adv_rows = []
            for _idx, _row in ctx.dashboard_df.iterrows():
                _ticker = str(_row.get('Symbol', '')).upper()
                if not _ticker:
                    continue
                _adv_rows.append((_ticker, _row))

            if ctx.progress is not None:
                ctx.progress.start_stage("execution", symbols_total=len(_adv_rows))

            _adv_workers = min(
                int(getattr(settings, 'ADVISORY_MAX_CONCURRENCY', 8)),
                max(1, len(ctx.dashboard_df)),
            )
            if _adv_workers <= 1 or len(_adv_rows) <= 1:
                _adv_pairs = [_eval_one(_t, _r) for _t, _r in _adv_rows]
            else:
                with ThreadPoolExecutor(max_workers=_adv_workers) as _adv_pool:
                    _adv_pairs = list(_adv_pool.map(lambda _tr: _eval_one(*_tr), _adv_rows))

            advisory_results = {_t: _res for _t, _res in _adv_pairs if _res is not None}

            for _col in ('Advisory_Action', 'Advisory_Conviction',
                         'Advisory_Rationale', 'Advisory_Position_Pct',
                         'Advisory_Data_Quality'):
                if _col in ('Advisory_Conviction', 'Advisory_Position_Pct'):
                    ctx.dashboard_df[_col] = ctx.dashboard_df['Symbol'].map(lambda x: advisory_results.get(str(x).upper(), {}).get(_col, 0.0))
                else:
                    ctx.dashboard_df[_col] = ctx.dashboard_df['Symbol'].map(lambda x: advisory_results.get(str(x).upper(), {}).get(_col, ""))

            telemetry.info(
                "Advisory evaluation complete for %d tickers.", len(ctx.dashboard_df)
            )
        except Exception as _adv_loop_err:
            telemetry.warning(
                "Advisory evaluation loop failed (non-critical): %s", _adv_loop_err
            )

        # 6. Broker Execution
        effective_dry_run = ctx.force_account # Or pass it in context
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

        _write_state_snapshot(ctx.macro_raw, ctx.dashboard_df, ctx.symbols)

        # Persist the optional Pilots-PWA analytics artifacts (options premium
        # matrix + pairs radar). Both are opt-in (settings.*_ENABLED, default
        # False) and dead-letter-guarded: a failure here NEVER affects the
        # pipeline (CONSTRAINT #6). Heavy engine imports live in reporting/*,
        # never in the AST-guarded api/pilots_api.py.
        try:
            from reporting.options_snapshot import write_options_matrix

            write_options_matrix(
                ctx.symbols,
                vix=float(ctx.macro_raw.get("VIXCLS", 0.0) or 0.0),
                market_regime=str(ctx.macro_raw.get("market_regime", "RISK ON")),
            )
        except Exception as opt_err:  # noqa: BLE001
            telemetry.warning(f"Options matrix snapshot skipped: {opt_err}")
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
            payload_cols = ["Symbol", "Price", "Action Signal", "buyRange", "sellRange",
                            "Kelly Target", "Option Strategy", "GARCH_Vol", "True_IVR"]
            for ac in ("Advisory_Action", "Advisory_Conviction",
                        "Advisory_Rationale", "Advisory_Position_Pct", "Advisory_Data_Quality"):
                if ac in ctx.dashboard_df.columns:
                    payload_cols.append(ac)
            output_payload = ctx.dashboard_df[payload_cols].to_dict(orient="records")
            print("\n=== FINAL ACTIONABLE PAYLOAD REPRESENTATION ===")
            print(json.dumps(output_payload, indent=4))
            print("================================================\n")

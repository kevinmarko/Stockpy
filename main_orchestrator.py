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
from strategy_engine import StrategyEngine
from evaluation_engine import EvaluationEngine
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from data.robinhood_client import RobinhoodClient
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

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("MasterOrchestrator")


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
    tech_task = asyncio.to_thread(de.fetch_technical_raw, list(set(tickers + ["SPY"])))
    
    macro_raw, fund_raw, tech_raw = await asyncio.gather(macro_task, fund_task, tech_task)
    telemetry.info("Data fetching completed successfully.")
    return macro_raw, fund_raw, tech_raw


def run_pipeline(tickers: list, macro_raw: dict, fund_raw: dict, tech_raw: dict,
                  data_engine: Optional[Any] = None,
                  robinhood_positions: Optional[dict] = None,
) -> tuple:
    """
    Synchronous execution of the quantitative engines:
    Macro -> Technical Options -> Processing -> Forecasting -> Strategy & Evaluation.

    Parameters
    ----------
    data_engine : IDataProvider, optional
        Used by MacroEngine.compute_hmm_risk_on_probability() to fetch
        historical VIX/yield-curve series (regime/hmm_regime.py's second
        opinion). None (the default) disables the HMM second opinion --
        market_regime/killSwitch then behave exactly as before this feature
        existed (no fabricated probability).

    Returns
    -------
    tuple[pd.DataFrame, MacroEconomicDTO, SignalContext]
        ``(dashboard_df, macro_dto, shared_context)``

        * ``dashboard_df`` — fully compiled per-ticker signal table.
        * ``macro_dto`` — the authoritative MacroEconomicDTO for this cycle
          (carries ``hmm_risk_on_probability``); callers should use this
          instead of reconstructing from ``macro_raw`` so the HMM field
          is never accidentally dropped.
        * ``shared_context`` — SignalContext whose ``xsec_percentile_ranks``
          and ``multifactor_scores`` dicts were populated by
          ``global_registry.run_pre_compute()``.  Pass these to
          ``engine.advisory.evaluate(context_extras=...)`` so the advisory
          path scores cross-sectional / multifactor signals correctly.
    """
    # 1. Macro Economic Regime Analysis
    telemetry.info("Routing data through Macro Engine...")
    me = MacroEngine(data_engine=data_engine)
    # BUG-FIX: was `me._fallback_sentiment("")` which always returns 0.0 (NLP
    # sentiment helper on empty text). The Sahm Rule indicator is a FRED-derived
    # recession signal and must be fetched via calculate_sahm_rule(). Calling
    # the wrong method silently disabled the RECESSION kill-switch path
    # (sahm_rule_indicator >= 0.5 could never trigger).
    sahm_val = me.calculate_sahm_rule()
    macro_data = me.run_macro_killswitch(macro_raw, sahm_val)
    market_regime = macro_data["market_regime"].iloc[0]

    # HMM second opinion (regime/hmm_regime.py): uses real SPY price history
    # already fetched into tech_raw -- never fabricated, None if unavailable.
    hmm_risk_on_probability = me.compute_hmm_risk_on_probability(tech_raw.get('SPY'))

    macro_dto = MacroEconomicDTO(
        yield_curve_10y_2y=float(macro_raw.get('T10Y2Y', 0.5)),
        high_yield_oas=float(macro_raw.get('BAMLH0A0HYM2', 3.5)),
        inflation_rate=float(macro_raw.get('CPIAUCSL_YoY', 2.0)),
        nominal_10y=float(macro_raw.get('DGS10', 4.0)),
        vix_value=float(macro_raw.get('VIXCLS', 15.0)),
        # BUG-FIX: sahm_rule_indicator was never wired in, so macro_dto always
        # had sahm_rule_indicator=0.0 (the default). This structurally prevented
        # macro_dto.killSwitch from ever firing via the Sahm Rule branch
        # (self.sahm_rule_indicator >= 0.5). Now passes the FRED-derived value.
        sahm_rule_indicator=sahm_val,
        hmm_risk_on_probability=hmm_risk_on_probability,
    )

    # 2. Technical Options Analysis
    telemetry.info("Routing data through Technical Options Engine...")
    toe = TechnicalOptionsEngine()
    tech_opt_indicators = {}
    iv_store = IVHistoryStore()
    for ticker in tickers:
        df_hist = tech_raw.get(ticker)
        if df_hist is not None and not df_hist.empty:
            # Dead-letter resilience (CONSTRAINT #6): a single ticker's options
            # analysis (GARCH vol, IV fetch, Black-Scholes strategy matrix) must
            # never abort the whole pipeline run. One bad/degenerate input here
            # (e.g. a zero-volatility read) previously crashed the entire
            # main_orchestrator.py process uncaught.
            try:
                indicators = toe.calculate_indicators(df_hist)
                vol = toe.estimate_gjr_garch_volatility(df_hist)
                realized_vol_rank = toe.calculate_realized_vol_rank(df_hist, vol)

                # Fetch options chain / compute true 30d ATM IV
                as_of_date = df_hist.index[-1].strftime("%Y-%m-%d")
                price_val = float(df_hist['Close'].iloc[-1])

                current_iv = float('nan')
                if data_engine is not None:
                    current_iv = get_30d_atm_iv(data_engine, ticker, as_of_date, spot_price=price_val)
                    if not np.isnan(current_iv):
                        iv_store.record_iv(ticker, as_of_date, current_iv)

                true_ivr = calculate_true_ivr(ticker, current_iv, as_of_date, iv_store)
                vrp = get_vrp(ticker, current_iv, vol)

                # Call strategy matrix with true_ivr, vrp, and macro_dto
                opt_strategy = toe.generate_option_strategy_matrix(
                    true_ivr=true_ivr if not np.isnan(true_ivr) else 50.0,
                    aroon_osc=indicators["Aroon_Oscillator"],
                    coppock_val=indicators["Coppock_Curve"],
                    stock_price=price_val,
                    current_iv=current_iv if not np.isnan(current_iv) else vol,
                    vrp=vrp,
                    macro_dto=macro_dto
                )
                tech_opt_indicators[ticker] = {
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
            except Exception as opt_exc:
                telemetry.warning(
                    f"Technical Options Analysis failed for {ticker}: {opt_exc}. "
                    f"Skipping options metrics for this ticker this cycle."
                )

    # 3. Core Processing
    telemetry.info("Routing data through Computational Core (Processing)...")
    pe = ProcessingEngine()
    regime_metrics = pe.process_macro_regime(macro_dto)
    tech_metrics = pe.calculate_technical_metrics(tech_raw, transactions_df=None)
    
    # Map raw fundamentals to DTOs
    fund_dtos = {}
    for ticker, data in fund_raw.items():
        if data and 'info' in data:
            fund_dtos[ticker] = FundamentalDataDTO.from_raw_dict(ticker, data['info'], dividends=data.get('dividends'))

    # Realized_Vol_60D feeds the multifactor low-volatility factor input
    # (signals/multifactor.py); sourced from tech_metrics, not fabricated.
    realized_vol_60d_map = {
        ticker: metrics.get('Realized_Vol_60D', float('nan'))
        for ticker, metrics in tech_metrics.items()
    }
    fund_metrics = pe.calculate_fundamental_metrics(fund_dtos, realized_vol_60d_map=realized_vol_60d_map)
    dashboard_df = pe.compile_dashboard(tech_metrics, fund_metrics, regime_metrics)

    # Explicitly map GARCH_Vol, Realized_Vol_Rank, True_IVR, VRP, and advanced indicators from tech_opt_indicators to dashboard_df
    dashboard_df['GARCH_Vol'] = 0.0
    dashboard_df['Realized_Vol_Rank'] = 0.0
    dashboard_df['True_IVR'] = 0.0
    dashboard_df['VRP'] = 0.0
    dashboard_df['Aroon Oscillator'] = 0.0
    dashboard_df['Coppock Curve'] = 0.0
    dashboard_df['Chandelier Exit'] = 0.0
    # Vectorized mapping to avoid iterrows mutation (Constraint #3)
    for col_key, mapped_key in [
        ('GARCH_Vol', 'GARCH_Vol'),
        ('Realized_Vol_Rank', 'Realized_Vol_Rank'),
        ('True_IVR', 'True_IVR'),
        ('VRP', 'VRP'),
        ('Aroon Oscillator', 'Aroon_Oscillator'),
        ('Coppock Curve', 'Coppock_Curve'),
        ('Chandelier Exit', 'Chandelier_Long')
    ]:
        dashboard_df[col_key] = dashboard_df['Symbol'].map(lambda x: tech_opt_indicators.get(x, {}).get(mapped_key, 0.0))

    # 4. Multi-Horizon Forecasting (with robust ML exception safety)
    telemetry.info("Routing data through Forecasting Engine...")
    fe = ForecastingEngine()
    forecast_cols = ['Target_Days', 'ARIMA', 'MC_Target', 'MC_Lower', 'MC_Upper',
                     'Forecast_10', 'Forecast_30', 'Forecast_60', 'Forecast_90',
                     'Forecast_30_Prophet_Lower', 'Forecast_30_Prophet_Upper']
    forecast_results = {}
    for idx, row in dashboard_df.iterrows():
        ticker = row['Symbol']
        price = row['Price']
        if not price or price == 0:
            continue
        
        history_df = tech_raw.get(ticker)
        history_series = history_df['Close'] if history_df is not None else None

        # ML Fallback wrapping: ensures CNN-LSTM failure does not crash execution
        try:
            forecasts = fe.generate_forecast(row, price, history_series, history_df=history_df)
            forecast_results[ticker] = forecasts
        except Exception as ml_err:
            telemetry.warning(f"Forecasting Engine failure for {ticker}: {ml_err}. Reverting to baseline default.")
            # Calculate standard Monte Carlo and ARIMA manually as fallback
            mu = 0.0002
            sigma = 0.015
            if history_series is not None and len(history_series) > 1:
                returns = np.log(history_series / history_series.shift(1)).dropna()
                mu = float(returns.mean())
                sigma = float(returns.std())
            
            mc_target, mc_low, mc_high = fe.run_monte_carlo(price, mu, sigma, 30)
            # BUG-FIX: Forecast_10/60/90 previously used a naive linear formula
            # `price * (1 + mu * N)` which is not a valid GBM estimate and
            # fabricates a trend without variance (Constraint #4). Use Monte Carlo
            # for all horizons so the fallback path is consistent with the happy path.
            mc_10, _, _ = fe.run_monte_carlo(price, mu, sigma, 10)
            mc_60, _, _ = fe.run_monte_carlo(price, mu, sigma, 60)
            mc_90, _, _ = fe.run_monte_carlo(price, mu, sigma, 90)
            forecast_results[ticker] = {
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

    # Vectorized mapping to avoid iterrows mutation (Constraint #3)
    for col in forecast_cols:
        dashboard_df[col] = dashboard_df['Symbol'].map(lambda x: forecast_results.get(x, {}).get(col, 0.0))

    # 5. Cross-Sectional Momentum Pre-Compute (Jegadeesh-Titman 1993)
    # Must run BEFORE the per-ticker strategy loop so pre_compute populates
    # context.xsec_percentile_ranks for all tickers at once.
    telemetry.info("Computing cross-sectional momentum ranks (Jegadeesh-Titman)...")
    dashboard_df['XSec_12_1M'] = float('nan')
    dashboard_df['XSec_Momentum_Rank'] = float('nan')

    # Compute vectorized 12-1m returns for the full universe
    xsec_rank_series = compute_xsec_momentum_ranks(tech_raw)

    # Write 12-1m returns and ranks back to dashboard_df before pre_compute
    xsec_return_dict: dict = {}
    for ticker_i, df_i in tech_raw.items():
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

    # Vectorized mapping to avoid iterrows mutation (Constraint #3)
    dashboard_df['XSec_12_1M'] = dashboard_df['Symbol'].map(xsec_return_dict)
    dashboard_df['XSec_Momentum_Rank'] = dashboard_df['Symbol'].map(xsec_rank_series)

    # Build a shared SignalContext stub (bar/fundamentals populated per-ticker below)
    # The xsec_percentile_ranks dict lives in the shared context and is read-only per ticker
    _shared_macro_dto = macro_dto  # already built above
    _stub_bar = MarketBarDTO(datetime.now(), "__UNIVERSE__", 100.0, 100.0, 100.0, 100.0, 0)
    _stub_fund = FundamentalDataDTO(
        ticker="__UNIVERSE__", pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
        book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
        payout_ratio=0.0, sector="Unknown", company_name="Unknown"
    )
    shared_context = SignalContext(
        bar=_stub_bar,
        fundamentals=_stub_fund,
        macro=_shared_macro_dto,
    )
    # Meta-labeler runtime registration (once per pipeline run, before signals
    # run). Loads any trained meta-labeler pickles into global_meta_registry so
    # the SignalAggregator's meta_hard_gate can fire. Strict no-op (logged) when
    # no saved model exists -- preserves the exact pre-model behavior. Lazy
    # import mirrors HistoricalStore's lazy-import pattern; dead-letter resilient.
    try:
        from ml.meta_bootstrap import bootstrap_meta_registry
        bootstrap_meta_registry()
    except Exception as _meta_exc:  # never let meta-label wiring crash the run
        telemetry.warning("Meta-labeler bootstrap failed (%s); continuing.", _meta_exc)

    # Trigger pre_compute on all signal modules (most are no-ops; XSec fills rank
    # dict; MultifactorSignal fills multifactor_scores -- see signals/multifactor.py)
    global_registry.run_pre_compute(dashboard_df, shared_context)
    telemetry.info(
        "Cross-sectional pre_compute complete. %d tickers ranked.",
        len(shared_context.xsec_percentile_ranks),
    )

    # Write multifactor Z-scores back into dashboard_df (computed by
    # MultifactorSignal.pre_compute above; not available before this point).
    for col in ('Value_Z', 'Quality_Z', 'LowVol_Z', 'Size_Z', 'Multifactor_Composite'):
        dashboard_df[col] = float('nan')
    # Vectorized mapping to avoid iterrows mutation (Constraint #3)
    for col in ('Value_Z', 'Quality_Z', 'LowVol_Z', 'Size_Z', 'Multifactor_Composite'):
        dashboard_df[col] = dashboard_df['Symbol'].map(
            lambda x: shared_context.multifactor_scores.get(x, {}).get(col, float('nan'))
            if shared_context.multifactor_scores else float('nan')
        )

    # Write news sentiment and earnings dates (Tier 2.4 — NewsCatalystSignal.pre_compute).
    # These fields are populated only when FINNHUB_API_KEY is configured.
    # NaN / "" are safe defaults that degrade gracefully in all downstream consumers.
    dashboard_df['News_Sentiment'] = float('nan')
    dashboard_df['Earnings_Date'] = ""
    if shared_context.news_sentiment_scores:
        dashboard_df['News_Sentiment'] = dashboard_df['Symbol'].map(
            lambda x: shared_context.news_sentiment_scores.get(str(x).upper(), float('nan'))
        )
    if shared_context.earnings_dates:
        dashboard_df['Earnings_Date'] = dashboard_df['Symbol'].map(
            lambda x: shared_context.earnings_dates.get(str(x).upper(), "")
        )
    # Correlation_Cluster column is computed on-demand in the GUI Reports tab
    # (fetch_returns_for_clustering + compute_correlation_clusters) rather than
    # in the main pipeline, because it requires simultaneous historical returns
    # for all symbols.  Default NaN here so the column exists in the schema.
    dashboard_df['Correlation_Cluster'] = float('nan')

    # 6. Strategy & Sizing Evaluations
    telemetry.info("Routing data through Strategy and Evaluation Engines...")
    se = StrategyEngine()
    ee = EvaluationEngine()
    
    # 'sellRange' is the dedicated sell-side execution band (strategy_engine.
    # apply_sell_side_range) — always populated alongside buyRange, never empty
    # for a valid Action Signal. See config.COLUMN_SCHEMA for the dashboard header.
    strategy_cols = ['Action Signal', 'Advice', 'Actionable Advice Signal', 'Kelly Target',
                     'Option Strategy', 'buyRange', 'sellRange', 'Strategy Explainer Notes',
                     'Robinhood Shares', 'Robinhood Avg Cost', 'Robinhood Dividends', 'Robinhood Advice']
    for col in strategy_cols:
        dashboard_df[col] = ""
    dashboard_df['Kelly Target'] = 0.0
    dashboard_df['Edge Ratio'] = 0.0
    dashboard_df['Robinhood Shares'] = 0.0
    dashboard_df['Robinhood Avg Cost'] = 0.0
    dashboard_df['Robinhood Dividends'] = 0.0

    # dead_letter_entries accumulates per-symbol failures (Constraint #6).
    # Written atomically to output/dead_letter.json after the loop so the GUI
    # can display failed symbols and offer targeted retry without a full restart.
    eval_results = {}
    dead_letter_entries: list[dict] = []

    for idx, row in dashboard_df.iterrows():
        ticker = row['Symbol']
        price = row['Price']
        if not price or price == 0:
            continue

        # Track which processing stage we are in so dead-letter entries carry
        # actionable context (e.g. "strategy" vs "dto_construction").
        _stage = "dto_construction"
        try:
            # MarketBar DTO
            history_df = tech_raw.get(ticker)
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

            # Fundamentals DTO
            fund_dto = fund_dtos.get(ticker)
            if fund_dto is None:
                fund_dto = FundamentalDataDTO(
                    ticker=ticker, pe_ratio=None, pb_ratio=None, dividend_yield=0.0,
                    book_value=0.0, eps_trailing=0.0, dividend_growth_rate=0.0,
                    payout_ratio=0.0, sector="Unknown", company_name="Unknown"
                )

            # Robinhood Position DTO
            rh_position = robinhood_positions.get(ticker) if robinhood_positions else None

            # Generate action signal
            _stage = "strategy"
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
                macro=macro_dto,
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
                robinhood_position=rh_position
            )

            # Calculate Edge Ratio (Post-trade evaluation)
            _stage = "edge_ratio"
            edge_ratio_val = 0.0
            if history_df is not None and len(history_df) >= 20:
                # Evaluate a mock hold period for the last 15 trading days
                entry_d = history_df.index[-15]
                exit_d = history_df.index[-1]
                trade_entry_p = float(history_df["Close"].iloc[-15])

                edge_data = ee.calculate_edge_ratio(history_df, trade_entry_p, entry_d, exit_d)
                edge_ratio_val = float(edge_data['Edge Ratio'])
                # Add Edge Ratio to explainer notes for completeness
                strategy_output["Strategy Explainer Notes"] += (
                    f"\nPOST-TRADE EDGE RATIO: {edge_data['Edge Ratio']:.2f} "
                    f"(MFE: {edge_data['MFE']*100:.1f}%, MAE: {edge_data['MAE']*100:.1f}%)"
                )

            _stage = "results"
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
                'Option Strategy': tech_opt_indicators[ticker].get('Option_Strategy_Matrix', '') if ticker in tech_opt_indicators else strategy_output['Option Strategy'],
                'buyRange': strategy_output['buyRange'],
                'sellRange': strategy_output['sellRange'],
                'Strategy Explainer Notes': strategy_output['Strategy Explainer Notes'],
                'Robinhood Shares': float(strategy_output.get('Robinhood Shares', 0.0)),
                'Robinhood Avg Cost': float(strategy_output.get('Robinhood Avg Cost', 0.0)),
                'Robinhood Dividends': float(strategy_output.get('Robinhood Dividends', 0.0)),
                'Robinhood Advice': str(strategy_output.get('Robinhood Advice', 'N/A'))
            }

        except Exception as _ticker_exc:
            # Dead-letter this symbol: record stage + error, continue to next ticker.
            dead_letter_entries.append({
                "symbol": ticker,
                "stage": _stage,
                "error": str(_ticker_exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            telemetry.error(
                "Dead-lettered %s at stage=%s: %s", ticker, _stage, _ticker_exc,
                exc_info=True,
            )

    # Persist dead-letter report (always written — empty entries = clean run).
    # Written inline to avoid importing gui.* from the pipeline layer.
    _dl_path = settings.OUTPUT_DIR / "dead_letter.json"
    try:
        import json as _json
        _dl_payload = {
            "run_id": datetime.now(timezone.utc).isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "entries": dead_letter_entries,
        }
        _dl_tmp = _dl_path.with_suffix(".tmp")
        _dl_path.parent.mkdir(parents=True, exist_ok=True)
        _dl_tmp.write_text(_json.dumps(_dl_payload, indent=2), encoding="utf-8")
        _dl_tmp.replace(_dl_path)
        if dead_letter_entries:
            telemetry.warning(
                "Dead-letter report: %d symbol(s) failed — see %s",
                len(dead_letter_entries), _dl_path,
            )
        else:
            telemetry.info("All symbols processed cleanly — dead_letter.json cleared.")
    except Exception as _dl_exc:
        telemetry.warning("Failed to write dead-letter report: %s", _dl_exc)

    # Vectorized mapping to avoid iterrows mutation (Constraint #3)
    for col in [
        'Edge Ratio', 'Action Signal', 'Advice', 'Actionable Advice Signal',
        'is_dividend_sustainable', 'eps_trailing', 'book_value', 'graham_number',
        'Kelly Target', 'Option Strategy', 'buyRange', 'sellRange',
        'Strategy Explainer Notes', 'Robinhood Shares', 'Robinhood Avg Cost',
        'Robinhood Dividends', 'Robinhood Advice'
    ]:
        if col in ['Edge Ratio', 'Kelly Target', 'Robinhood Shares', 'Robinhood Avg Cost', 'Robinhood Dividends', 'is_dividend_sustainable', 'eps_trailing', 'book_value', 'graham_number']:
            default_val = 0.0 if col != 'is_dividend_sustainable' else 0
            dashboard_df[col] = dashboard_df['Symbol'].map(lambda x: eval_results.get(x, {}).get(col, default_val))
        else:
            dashboard_df[col] = dashboard_df['Symbol'].map(lambda x: eval_results.get(x, {}).get(col, ""))

    # Map 'Avg Cost' to 'Entry_Price' if present
    if 'Avg Cost' in dashboard_df.columns:
        dashboard_df['Entry_Price'] = dashboard_df['Avg Cost']

    # Map 'Shares' to 'position_size' for Portfolio Heat.
    # Watchlist-only tickers have 0 shares, so Shares * Price = 0 for every row.
    # Replace zero-valued position sizes with the $10k notional default so downstream
    # calculations (portfolio heat, Brinson-Fachler sector weights) never divide by zero.
    if 'Shares' in dashboard_df.columns and 'Price' in dashboard_df.columns:
        dashboard_df['position_size'] = dashboard_df['Shares'] * dashboard_df['Price']
        zero_mask = dashboard_df['position_size'] <= 0.0
        if zero_mask.any():
            dashboard_df.loc[zero_mask, 'position_size'] = 10000.0
    elif 'position_size' not in dashboard_df.columns:
        dashboard_df['position_size'] = 10000.0  # Default $10k assumption

    # Map VaR 95 to stop loss percentage
    if 'VaR 95' in dashboard_df.columns:
        dashboard_df['stop_loss_pct'] = dashboard_df['VaR 95'].abs()
    elif 'VaR_95' in dashboard_df.columns:
        dashboard_df['stop_loss_pct'] = dashboard_df['VaR_95'].abs()
    elif 'stop_loss_pct' not in dashboard_df.columns:
        dashboard_df['stop_loss_pct'] = 0.05

    # Align Sector string casing
    if 'Sector' in dashboard_df.columns and 'sector' not in dashboard_df.columns:
        dashboard_df['sector'] = dashboard_df['Sector']
    
    if 'RS vs SPY' in dashboard_df.columns and 'Relative_Strength' not in dashboard_df.columns:
        dashboard_df['Relative_Strength'] = dashboard_df['RS vs SPY']
    elif 'Relative Strength' in dashboard_df.columns and 'Relative_Strength' not in dashboard_df.columns:
        dashboard_df['Relative_Strength'] = dashboard_df['Relative Strength']
    elif 'Relative_Strength' not in dashboard_df.columns:
        dashboard_df['Relative_Strength'] = 0.0

    # CRITICAL FIX 3: Generate Benchmark and Execute Evaluation
    if 'sector' in dashboard_df.columns:
        unique_sectors = dashboard_df['sector'].dropna().unique()
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

    dashboard_df = ee.evaluate_portfolio(dashboard_df, benchmark_df, data_provider=tech_raw)

    # CRITICAL FIX 4: Handle NaNs/values before Google Sheets Export
    export_keys = ['MAE', 'MFE', 'Edge Ratio', 'Portfolio_Heat', 'BF_Allocation', 'BF_Selection']
    for key in export_keys:
        if key in dashboard_df.columns:
            if key not in ['MAE', 'MFE', 'Edge Ratio']:
                dashboard_df[key] = dashboard_df[key].fillna(0.0)
        else:
            if key not in ['MAE', 'MFE', 'Edge Ratio']:
                dashboard_df[key] = 0.0
            else:
                dashboard_df[key] = np.nan

    # ---- Dual Momentum Overlay (optional, gated by settings flag) ----
    if settings.USE_DUAL_MOMENTUM_OVERLAY:
        telemetry.info("Running Dual Momentum Overlay...")
        try:
            dm = DualMomentumAllocator(
                risky_assets=list(settings.DUAL_MOMENTUM_RISKY_ASSETS),
                safe_asset=settings.DUAL_MOMENTUM_SAFE_ASSET,
            )
            dm_alloc = dm.decide(
                as_of_date=datetime.now(timezone.utc).date(),
                price_data=tech_raw,
            )
            dm_winner = next(iter(dm_alloc))  # Single-asset allocation
            telemetry.info(f"Dual Momentum decision: {dm_winner} ({dm_alloc})"
                           )
            # If safe asset selected, zero out Kelly for all risky universe tickers
            if dm_winner == settings.DUAL_MOMENTUM_SAFE_ASSET:
                risky_set = set(settings.DUAL_MOMENTUM_RISKY_ASSETS)
                mask = dashboard_df["Symbol"].isin(risky_set)
                dashboard_df.loc[mask, "Kelly Target"] = 0.0
                telemetry.info(
                    f"Dual Momentum: safe-asset regime. Kelly Target zeroed for "
                    f"{list(risky_set & set(dashboard_df['Symbol'].tolist()))}"
                )
            # Record the DM decision in a new column for reporting
            dashboard_df["DualMomentum_Signal"] = dm_winner
        except Exception as dm_err:
            telemetry.warning(f"Dual Momentum Overlay failed (non-critical): {dm_err}")
            dashboard_df["DualMomentum_Signal"] = "N/A"
    else:
        dashboard_df["DualMomentum_Signal"] = "disabled"

    return dashboard_df, macro_dto, shared_context


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
        from execution.broker_base import OrderIntent, OrderSide, OrderType
        from execution.kill_switch import KillSwitchActiveError
        from execution.order_manager import OrderManager
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

        prices: dict[str, float] = {
            str(row.get("Symbol", "")).upper(): float(row.get("Price", 0.0) or 0.0)
            for _, row in final_df.iterrows()
        }

        risk_ctx = RiskContext(
            macro=macro_dto,
            open_positions=open_pos,
            account=account,
            current_prices=prices,
            is_premium_sell_strategy=False,
        )

        now = datetime.now(timezone.utc)
        for _, row in final_df.iterrows():
            symbol = str(row.get("Symbol", "")).upper()
            signal = str(row.get("Action Signal", "")).upper()
            kelly = float(row.get("Kelly Target", 0.0) or 0.0)

            if not symbol:
                continue

            try:
                if "BUY" in signal and kelly > 0 and symbol not in open_symbols:
                    intent = OrderIntent(
                        strategy_id="main_pipeline",
                        symbol=symbol,
                        side=OrderSide.BUY,
                        qty=1.0,
                        order_type=OrderType.MARKET,
                    )
                    result = await om.submit_order_with_idempotency(
                        intent, timestamp=now, risk_context=risk_ctx
                    )
                    telemetry.info(
                        "Order submitted: BUY %s -> status=%s broker_id=%s",
                        symbol, result.status.value, result.broker_order_id,
                    )

                elif signal in ("SELL", "TRIM") and symbol in open_symbols:
                    sell_qty = abs(open_symbols[symbol])
                    intent = OrderIntent(
                        strategy_id="main_pipeline",
                        symbol=symbol,
                        side=OrderSide.SELL,
                        qty=sell_qty,
                        order_type=OrderType.MARKET,
                    )
                    result = await om.submit_order_with_idempotency(
                        intent, timestamp=now, risk_context=risk_ctx
                    )
                    telemetry.info(
                        "Order submitted: SELL %s x %.4f -> status=%s broker_id=%s",
                        symbol, sell_qty, result.status.value, result.broker_order_id,
                    )

            except KillSwitchActiveError as ks_err:
                telemetry.critical(
                    "Kill switch is ACTIVE — aborting all remaining order submission. %s", ks_err
                )
                return  # bail out of the entire loop; no further submissions this cycle

            except Exception as order_err:
                telemetry.error(
                    "Order submission failed for %s: %s", symbol, order_err, exc_info=True
                )

    except Exception as exc:
        telemetry.error(
            "_execute_broker_orders crashed (non-fatal): %s", exc, exc_info=True
        )


def _safe_float_or_none(val) -> Optional[float]:
    """Coerce *val* to float, or ``None`` when missing/NaN.

    Used for optional analytics fields (cross-sectional momentum,
    multifactor z-scores) written into ``state_snapshot.json`` — CONSTRAINT
    #4 forbids fabricating a ``0.0`` default when a signal module simply
    didn't produce a value for this ticker (e.g. microcap-excluded from
    multifactor scoring, or insufficient history for the XSec rank).
    ``json.dumps`` serialises ``None`` as JSON ``null``, which the GUI reader
    treats identically to a missing key.
    """
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


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
    * **CI / --strict (strict=True):** a validation failure is FATAL
      (``sys.exit(1)``) so schema drift can never silently ship.

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
            sys.exit(1)
        return False
    except Exception as schema_err:
        telemetry.error(f"❌ Final compiled DataFrame failed DashboardSchema validation: {schema_err}")
        if strict:
            telemetry.critical("Strict mode (--strict): aborting on schema validation failure.")
            sys.exit(1)
        return False


async def _main_body(effective_dry_run: bool, strict: bool = False) -> None:
    """Core pipeline logic — separated from main() so the heartbeat try/finally is clean."""
    # Surface a CRITICAL alert if the previously leaked FRED key is still in use.
    settings.warn_if_fred_key_leaked(telemetry)

    # Initialize real or mock data engine based on credentials
    creds_exist = os.path.exists("credentials.json")
    if creds_exist:
        settings.ensure_fred_configured()
        de = DataEngine(settings.FRED_API_KEY)
        tickers = settings.DEFAULT_TICKERS
    else:
        telemetry.warning("credentials.json not found. Operating with deterministic MockDataEngine.")
        de = MockDataEngine()
        tickers = ["AAPL"]  # Use mock ticker

    # Integrate Robinhood Holdings
    rh_client = RobinhoodClient()
    rh_positions = {}
    if rh_client.login():
        rh_positions = rh_client.fetch_positions()
        # Merge unique tickers
        for tk in rh_positions.keys():
            if tk not in tickers:
                tickers.append(tk)

    # 1. Asynchronous concurrent data fetching
    try:
        macro_raw, fund_raw, tech_raw = await fetch_all_data_async(de, tickers)
    except Exception as fetch_err:
        telemetry.critical(f"Asynchronous data gathering crashed: {fetch_err}")
        sys.exit(1)

    # Fail-safe check: If offline or data is empty, fall back to MockDataEngine for verification
    if not tech_raw or all(df.empty for df in tech_raw.values()):
        telemetry.warning("Fetched pricing data is empty (likely due to network offline). Falling back to MockDataEngine for verification.")
        de = MockDataEngine()
        macro_raw = de.fetch_macro_raw()
        fund_raw = de.fetch_fundamentals_raw(tickers)
        tech_raw = de.fetch_technical_raw(tickers)

    # 1b. Kill-switch advisory pause gate
    # Checked after data fetch but before the expensive pipeline so the
    # observability dashboard continues displaying the last written snapshot.
    _ks = GlobalKillSwitch()
    if _ks.is_active():
        _ks_reason = _ks.reason() or "(no reason recorded)"
        telemetry.info(
            "Advisory paused by kill-switch sentinel — skipping pipeline. "
            "Reason: %s  |  Deactivate with: "
            "python -m execution.kill_switch --deactivate",
            _ks_reason,
        )
        return

    # 2. Run Pipeline
    try:
        final_df, macro_dto, shared_context = run_pipeline(
            tickers, macro_raw, fund_raw, tech_raw,
            data_engine=de, robinhood_positions=rh_positions,
        )
    except Exception as pipe_err:
        # exc_info=True logs the full traceback so future crashes are diagnosable
        # from the log alone rather than requiring a debugger attach.
        telemetry.critical(f"Platform execution pipeline crashed: {pipe_err}", exc_info=True)
        sys.exit(1)

    # 3. Schema Validation (two-tier: lazy/non-fatal in prod, fatal under --strict)
    _validate_dashboard(final_df, strict=strict)

    # 3b. Advisory Evaluation — holding-aware BUY/SELL/HOLD overlay
    # Uses the full pipeline's macro_dto (with HMM probability), xsec ranks,
    # and multifactor composites from shared_context so advisory scores all
    # signal modules with real pre-computed data instead of neutral/0 defaults.
    if not final_df.empty:
        try:
            from data.market_data import get_provider as _get_market_provider
            from data.robinhood_portfolio import fetch_account_snapshot as _fetch_rh_snapshot
            from engine.advisory import evaluate as _advisory_evaluate

            _market_provider = _get_market_provider()
            _context_extras = {
                'xsec_percentile_ranks': shared_context.xsec_percentile_ranks,
                'multifactor_scores':    shared_context.multifactor_scores,
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
                final_df[_col] = ""
            final_df['Advisory_Conviction'] = 0.0
            final_df['Advisory_Position_Pct'] = 0.0

            advisory_results = {}
            for _idx, _row in final_df.iterrows():
                _ticker = str(_row.get('Symbol', '')).upper()
                if not _ticker:
                    continue
                try:
                    _position = (
                        _rh_snapshot.positions.get(_ticker)
                        if _rh_snapshot is not None else None
                    )
                    _rec = _advisory_evaluate(
                        symbol=_ticker,
                        position=_position,
                        market=_market_provider,
                        snapshot=_rh_snapshot,
                        macro_dto=macro_dto,
                        context_extras=_context_extras,
                    )
                    advisory_results[_ticker] = {
                        'Advisory_Action': _rec.action,
                        'Advisory_Conviction': round(_rec.conviction, 4),
                        'Advisory_Rationale': _rec.rationale,
                        'Advisory_Position_Pct': round(_rec.suggested_position_pct, 6),
                        'Advisory_Data_Quality': _rec.data_quality
                    }
                except Exception as _adv_exc:
                    telemetry.warning("Advisory failed for %s: %s", _ticker, _adv_exc)

            # Vectorized mapping to avoid iterrows mutation (Constraint #3)
            for _col in ('Advisory_Action', 'Advisory_Conviction',
                         'Advisory_Rationale', 'Advisory_Position_Pct',
                         'Advisory_Data_Quality'):
                if _col in ('Advisory_Conviction', 'Advisory_Position_Pct'):
                    final_df[_col] = final_df['Symbol'].map(lambda x: advisory_results.get(str(x).upper(), {}).get(_col, 0.0))
                else:
                    final_df[_col] = final_df['Symbol'].map(lambda x: advisory_results.get(str(x).upper(), {}).get(_col, ""))

            telemetry.info(
                "Advisory evaluation complete for %d tickers.", len(final_df)
            )
        except Exception as _adv_loop_err:
            telemetry.warning(
                "Advisory evaluation loop failed (non-critical): %s", _adv_loop_err
            )

    # 4. Reporting & Visualization Output
        # Output directory is centrally configured (created on settings load).
        out_dir = str(settings.OUTPUT_DIR)

        primary_ticker = tickers[0]
        primary_hist = tech_raw.get(primary_ticker)
        if primary_hist is not None and not primary_hist.empty:
            try:
                # Plotly expects lowercase column names in diagnostics_and_visuals
                plotly_df = primary_hist.copy()
                plotly_df.columns = [col.lower() for col in plotly_df.columns]
                generate_plotly_volatility_bands(plotly_df, primary_ticker, os.path.join(out_dir, "volatility_bands_dashboard.html"))
            except Exception as plot_err:
                telemetry.warning(f"Failed to generate interactive Plotly chart: {plot_err}")

        # State snapshot rotation precedes BOTH the HTML report and the JSON
        # payload so the Δ Since Last Run band always reflects "this run vs.
        # previous run", never "previous run vs. one-before-that". Wrapped
        # try/except inside _write_state_snapshot so a failure here cannot
        # abort report generation.
        _write_state_snapshot(macro_raw, final_df, tickers)

        # Jinja2 HTML Report Generation
        try:
            portfolio_dicts = final_df.to_dict(orient="records")
            for row in portfolio_dicts:
                if "Max Drawdown" in row:
                    row["Max_Drawdown"] = row["Max Drawdown"]
            yield_curve_val = float(macro_raw.get('T10Y2Y', 0.5))
            credit_spread_val = float(macro_raw.get('BAMLH0A0HYM2', 3.5))
            sahm_rule_val = float(macro_raw.get('SAHMREALTIME', 0.3))
            real_yield_val = float(macro_raw.get('DGS10', 4.0)) - float(macro_raw.get('CPIAUCSL_YoY', 2.0))
            regime_val = final_df["Macro Status"].iloc[0] if "Macro Status" in final_df.columns else "NEUTRAL"
            # Δ Since Last Run band: read the two most-recent rotated snapshots
            # (the just-rotated current run + the previous one). Degrades to
            # None on first ever run or any error so the template hides the band.
            snapshot_diff_payload: Optional[Dict[str, Any]] = None
            try:
                from scripts.snapshot_diff import compute_diff_from_history
                _diff = compute_diff_from_history(
                    settings.OUTPUT_DIR,
                    conviction_delta_threshold=settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD,
                )
                if _diff.prev_ts is not None or _diff.curr_ts is not None:
                    snapshot_diff_payload = _diff.to_dict()
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

    # 5. Export Final JSON Payload Representation
    if not final_df.empty:
        # Include 'sellRange' so downstream JSON consumers (alerts, custom
        # dashboards, paper-trading harness) receive the same sell-side
        # take-profit/stop instructions the HTML report renders.
        _payload_cols = ["Symbol", "Price", "Action Signal", "buyRange", "sellRange",
                         "Kelly Target", "Option Strategy", "GARCH_Vol", "True_IVR"]
        # Include advisory columns when present (added by advisory loop above)
        for _ac in ("Advisory_Action", "Advisory_Conviction",
                    "Advisory_Rationale", "Advisory_Position_Pct", "Advisory_Data_Quality"):
            if _ac in final_df.columns:
                _payload_cols.append(_ac)
        output_payload = final_df[_payload_cols].to_dict(orient="records")
        print("\n=== FINAL ACTIONABLE PAYLOAD REPRESENTATION ===")
        print(json.dumps(output_payload, indent=4))
        print("================================================\n")

    # 6. Broker Execution — submit delta orders and reconcile state
    # Tier 5.1: when ADVISORY_ONLY is True (the default) the broker surface is
    # quarantined entirely — we do not even check Alpaca credentials, so an
    # operator who happens to have keys in .env from an earlier paper-trading
    # phase does NOT trigger any broker import.  Only when ADVISORY_ONLY is
    # explicitly False AND Alpaca credentials are configured do we reach
    # ``_execute_broker_orders``.  Uses macro_dto returned by run_pipeline()
    # (carries hmm_risk_on_probability for the HMM regime gate).
    if getattr(settings, "ADVISORY_ONLY", True):
        telemetry.info(
            "📋 ADVISORY_ONLY=True — pipeline produced %d signals; broker "
            "execution is disabled for this run.",
            0 if final_df is None else len(final_df),
        )
    elif not final_df.empty and settings.ALPACA_API_KEY and settings.ALPACA_SECRET_KEY:
        await _execute_broker_orders(final_df, effective_dry_run, macro_dto=macro_dto)
    elif not final_df.empty:
        telemetry.info(
            "ALPACA_API_KEY/SECRET_KEY not configured; skipping broker execution. "
            "Set them in .env to enable live/paper order submission."
        )

    # State snapshot for the observability dashboard is written above (line
    # ~1076) so the Δ-band diff sees this run BEFORE the report renders.
    telemetry.info("✅ Master Orchestration finished successfully.")


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
    asyncio.run(main(dry_run=_args.dry_run, strict=_args.strict))

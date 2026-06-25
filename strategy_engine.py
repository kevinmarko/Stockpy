"""
InvestYo Quant Platform - Core Strategy Engine (Calibrated & Tactical Edition)
=============================================================================
Defines the institutional trade-signal generator, allocation optimization 
(Kelly Criterion), options overlays, and verbose explainability logs.

UPDATES IN THIS VERSION:
1. Calibrated Momentum/Forecast Thresholds: Fixed algorithmic pessimism by 
   lowering the 30-day target hurdle from 5.0% to 1.5%.
2. Tactical Ranges: Calculates 'Buy Zones', 'Hold Corridors', and 'Exit/Trim'
   levels across all risk regimes using ATR-based standard deviations.
"""

import logging
import math
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime

# Import type-safe data transfer containers
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO
from settings import settings
from sizing.kelly import (
    estimate_win_rate_and_payoff,
    fractional_kelly,
    kelly_sizing_for_strategy,
    MIN_TRADES_REQUIRED,
)
from sizing.vol_target import volatility_target_weight

logger = logging.getLogger(__name__)


def apply_tactical_ranges(signal: str, current_price: float, safe_atr: float, chandelier_long: float, chandelier_short: float, graham_val: float = 0.0) -> str:
    """
    Uses the Chandelier Exit to define dynamic, trailing Actionable Advice ranges.
    """
    tactical_range = ""

    if signal in ["STRONG BUY", "BUY"]:
        # Standard ATR-based entry zone for pullbacks
        support = current_price - (1.5 * safe_atr)
        resistance = current_price - (0.5 * safe_atr)
        if graham_val > 0 and resistance > graham_val:
            resistance = graham_val
        if support > resistance:
            support = current_price * 0.95
            resistance = current_price
        tactical_range = f"Buy Zone: ${support:.2f} - ${resistance:.2f}"

    elif signal == "HOLD":
        # Uses Chandelier Exit for dynamic trailing
        # Instead of static boundaries, we anchor to the Chandelier Long value
        support = chandelier_long if chandelier_long > 0 else current_price - (2.0 * safe_atr)
        resistance = current_price + (2.0 * safe_atr)
        tactical_range = f"Hold Range: ${support:.2f} - ${resistance:.2f}"

    else: # RISK REDUCE / AVOID
        # Tighten stops aggressively
        trim_point = current_price + (0.5 * safe_atr)
        # Hard stop tied directly to Chandelier Short for bearish trades, or Chandelier Long failure
        stop_loss = max(0.01, chandelier_long) if chandelier_long > 0 else max(0.01, current_price - (1.0 * safe_atr))
        tactical_range = f"Trim @ ${trim_point:.2f} | Stop @ ${stop_loss:.2f}"

    return tactical_range


def apply_sell_side_range(
    signal: str,
    current_price: float,
    safe_atr: float,
    chandelier_long: float,
    chandelier_short: float,
    forecast_price: float = 0.0,
) -> str:
    """Compute the dedicated sell-side execution range.

    Whereas ``apply_tactical_ranges`` returns a SINGLE signal-conditional
    corridor ("Buy Zone" XOR "Hold Range" XOR "Trim @"), this helper produces
    a FIRST-CLASS sell-side band that is populated for EVERY action signal.
    This is what a position manager needs to place a resting limit-sell /
    take-profit order regardless of whether the strategy is currently
    accumulating, holding, or reducing.

    Range construction (all lookahead-free — inputs are the same already-causal
    ATR, Chandelier Exit, and ``Forecast_30`` already flowing into
    ``StrategyEngine.evaluate_security``):

    * BUY / STRONG BUY / HOLD — produce a two-leg sell envelope:
        - take-profit lower  = current_price + 1.5 * ATR  (first profit-harvest leg)
        - take-profit upper  = max(current_price + 3.0 * ATR, forecast_price)
          (the forecast wins when fair-value upside exceeds 3 σ — captures
           the bullish-forecast scenario without fabricating a price level
           when forecast_price is zero / unavailable)
        - trailing stop      = chandelier_long if > 0 else current_price - 2.5 * ATR
          (looser than the RISK REDUCE 1.0 ATR stop because this leg is for a
           healthy long that we are NOT trying to flatten)
        Returned as: ``"Sell Zone: $LO - $HI | Stop @ $STOP"``.

    * RISK REDUCE / AVOID — the take-profit envelope is no longer the
      operating concern; emit an immediate-exit instruction:
        ``"Sell Now @ market | Stop @ $STOP"`` where STOP is chandelier_long
        if available else ``current_price - 1.0 * ATR`` (matches the existing
        ``apply_tactical_ranges`` RISK REDUCE stop policy).

    Failure-closed contract (CONSTRAINT #4 / #5):
      * ``safe_atr`` is the caller-provided fallback (already ``max(atr,
        current_price * 0.02)`` upstream), so the function never divides by /
        consumes a raw ``0.0`` ATR.
      * If both ``chandelier_long == 0`` AND the ATR fallback floor produces
        a negative stop, the stop is clamped to ``max(0.01, ...)`` so the
        emitted string is always parseable and the level is never negative or
        zero — matching the existing ``apply_tactical_ranges`` invariant.

    Parameters
    ----------
    signal :
        One of ``"STRONG BUY"``, ``"BUY"``, ``"HOLD"``, ``"RISK REDUCE"``.
        Unknown signals fall through to the RISK REDUCE branch (fail-closed).
    current_price :
        Latest close from the per-ticker ``MarketBarDTO``.
    safe_atr :
        Already-fallback-protected ATR (caller computes
        ``atr if atr > 0 else current_price * 0.02`` before passing in).
    chandelier_long :
        Per-ticker Chandelier Exit for long positions
        (``technical_options_engine.py``). ``0.0`` indicates unavailable.
    chandelier_short :
        Currently unused; kept in the signature for symmetry with
        ``apply_tactical_ranges`` so both helpers have identical call sites.
    forecast_price :
        ``Forecast_30`` from ``forecasting_engine.py``. ``0.0`` means
        "no forecast available" — in that case the take-profit upper bound
        falls back to the pure ATR-derived level (never fabricated).

    Returns
    -------
    str
        Sell-side range formatted as either
        ``"Sell Zone: $LO - $HI | Stop @ $STOP"`` (active long) or
        ``"Sell Now @ market | Stop @ $STOP"`` (exit / avoid).
    """
    if signal in ("STRONG BUY", "BUY", "HOLD"):
        take_profit_lo = current_price + (1.5 * safe_atr)
        atr_resistance = current_price + (3.0 * safe_atr)
        # forecast_price wins ONLY when it represents real upside above the
        # ATR-derived resistance; never fabricated when forecast is missing (0.0)
        take_profit_hi = max(atr_resistance, forecast_price) if forecast_price > 0 else atr_resistance

        if chandelier_long > 0:
            trailing_stop = chandelier_long
        else:
            trailing_stop = max(0.01, current_price - (2.5 * safe_atr))

        return (
            f"Sell Zone: ${take_profit_lo:.2f} - ${take_profit_hi:.2f} "
            f"| Stop @ ${trailing_stop:.2f}"
        )

    # RISK REDUCE / AVOID / unknown — fail-closed to immediate-exit instruction
    if chandelier_long > 0:
        stop_loss = chandelier_long
    else:
        stop_loss = max(0.01, current_price - (1.0 * safe_atr))
    return f"Sell Now @ market | Stop @ ${stop_loss:.2f}"


class StrategyEngine:
    """
    Multi-phase quantitative engine that translates validated technical, fundamental,
    and macroeconomic parameters into high-conviction allocation instructions.
    """
    
    def __init__(self, risk_free_rate: float = 0.0425, transactions_store: Optional[Any] = None):
        """
        Args:
            risk_free_rate: Annualized risk-free rate used elsewhere in the engine.
            transactions_store: Optional injected TransactionsStore (for testing
                with an in-memory DB). Defaults to a real TransactionsStore()
                instance lazily constructed on first use.
        """
        self.risk_free_rate = risk_free_rate
        self._transactions_store = transactions_store

    # =============================================================================
    # 1. CORE STRATEGY KERNEL
    # =============================================================================
    def evaluate_security(self, 
                          bar: MarketBarDTO, 
                          fundamentals: FundamentalDataDTO, 
                          macro: MacroEconomicDTO,
                          forecast_price: float,
                          trend_strength: float,
                          atr: float = 0.0,
                          macd_line: float = 0.0,
                          macd_signal: float = 0.0,
                          aroon_osc: Optional[float] = None,
                          rsi: Optional[float] = None,
                          sortino_ratio: Optional[float] = None,
                          max_drawdown: Optional[float] = None,
                          relative_strength: Optional[float] = None,
                          garch_vol: Optional[float] = None,
                          edge_ratio: Optional[float] = None,
                          chandelier_long: float = 0.0,
                          chandelier_short: float = 0.0,
                          roc_12m: float = 0.0,
                          sma_200: float = 0.0,
                          rsi_2: float = 50.0,
                          sma_5: Optional[float] = None,
                          strategy_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes multi-phase quantitative scoring across the security.
        Synthesizes technical, fundamental, macro, and volatility factors to produce
        high-precision signals, custom action ranges, options hedging, and explainability notes.

        Parameters
        ----------
        strategy_id : str or None
            When provided, activates the per-strategy bootstrap-conservative
            Kelly path (Stage 1.7): trades are filtered to this strategy,
            bootstrapped (n=1_000), and the 5th-percentile Kelly fraction is
            used as the sizing weight instead of the global aggregate point
            estimate. Pass None (default) to use the existing global pool path
            (backward-compatible).
        """
        current_price = bar.close
        ticker = bar.ticker
        sector = fundamentals.sector
        graham_val = fundamentals.graham_number
        # RSI(2) mean reversion (signals/rsi2_mean_reversion.py) needs the
        # already-reverted guard (Close > SMA(5)); default to current_price
        # (i.e. "at" SMA5) when unavailable so the guard fails closed (score 0)
        # rather than firing on missing data.
        sma_5_resolved = sma_5 if sma_5 is not None else current_price

        # 1. Package inputs into pd.Series and SignalContext
        row = pd.Series({
            "forecast_price": forecast_price,
            "trend_strength": trend_strength,
            "atr": atr,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "aroon_osc": aroon_osc,
            "rsi": rsi,
            "sortino_ratio": sortino_ratio,
            "max_drawdown": max_drawdown,
            "relative_strength": relative_strength,
            "garch_vol": garch_vol,
            "GARCH_Vol": garch_vol,
            "edge_ratio": edge_ratio,
            "chandelier_long": chandelier_long,
            "chandelier_short": chandelier_short,
            "current_price": current_price,
            "Close": current_price,
            "ticker": ticker,
            "sector": sector,
            "roc_12m": roc_12m,
            "ROC_12M": roc_12m,
            "SMA_200": sma_200,
            "RSI_2": rsi_2,
            "SMA_5": sma_5_resolved,
        })
        
        from signals.base import SignalContext
        from signals import global_registry, SignalAggregator
        
        context = SignalContext(bar=bar, fundamentals=fundamentals, macro=macro)

        # 2. Run weighted aggregation
        aggregator = SignalAggregator(global_registry)
        # aggregate() returns a 6-tuple; the 6th element (meta_label_composite)
        # is a Stage 4 placeholder — geometric mean of active modules'
        # meta_label_proba values, always 1.0 until real meta-labels are wired.
        final_score_raw, score_log, warnings, details, outputs, meta_label_composite = aggregator.aggregate(row, context)
        final_score = int(round(final_score_raw))

        # Determine trend direction for options and sizing
        if aroon_osc is not None and not pd.isna(aroon_osc):
            is_uptrend = aroon_osc >= 50
        else:
            is_uptrend = trend_strength >= 50.0

        # Options overlay uses lookahead-free strong uptrend filter
        if roc_12m != 0.0:
            if sma_200 > 0:
                is_strong_uptrend = (roc_12m > 0) and (current_price > sma_200)
            else:
                is_strong_uptrend = roc_12m > 0
        else:
            # Fallback to legacy trend filter in unit tests when roc_12m is not provided
            is_strong_uptrend = is_uptrend

        # ---------------------------------------------------------------------
        # PHASE 5: ACTION ADVICE GENERATOR
        # ---------------------------------------------------------------------
        if final_score >= 75:
            # Downgrade to BUY if market is choppy
            if aroon_osc is not None and abs(aroon_osc) < 50:
                signal = "BUY"
                advice = f"Favorable setup. Scale in on minor intraday pullbacks (Choppy Market filter active)."
            else:
                signal = "STRONG BUY"
                advice = f"High-conviction entry. Intrinsic value (${graham_val:.2f}) and trend confirm accumulation."
        elif 55 <= final_score < 75:
            signal = "BUY"
            advice = f"Favorable setup. Scale in on minor intraday pullbacks."
        elif 35 <= final_score < 55:
            signal = "HOLD"
            advice = f"Consolidation pattern. Hold existing exposure; harvest dividends. Do not allocate fresh capital."
        else:
            signal = "RISK REDUCE"
            advice = f"CRITICAL RISK. Structural deterioration or macro headwinds. Trim position or deploy hedges."

        # Hard overlay override for killSwitch: force BUY/STRONG BUY signals to HOLD
        if hasattr(macro, 'killSwitch') and macro.killSwitch:
            if signal in ["STRONG BUY", "BUY"]:
                signal = "HOLD"
                advice = "Fresh capital halted. Systemic Risk Overlay Active (Sahm/VIX Breach)."

        # ---------------------------------------------------------------------
        # PHASE 6: MULTI-TIER TACTICAL RANGES (Buy, Hold, Exit)
        # ---------------------------------------------------------------------
        safe_atr = atr if atr > 0 else (current_price * 0.02)
        tactical_range = apply_tactical_ranges(
            signal, current_price, safe_atr, chandelier_long, chandelier_short, graham_val
        )
        # Dedicated sell-side range — populated for EVERY signal regardless of
        # buy/hold/reduce action so a position manager always has explicit
        # take-profit + trailing-stop levels (vs. the single-corridor
        # ``tactical_range`` which only emits a sell hint on RISK REDUCE).
        # Surfaced as ``sellRange`` in the return dict, COLUMN_SCHEMA,
        # dashboard_df, JSON payload, state snapshot, and the HTML report.
        sell_side_range = apply_sell_side_range(
            signal, current_price, safe_atr, chandelier_long, chandelier_short,
            forecast_price=forecast_price,
        )

        # ---------------------------------------------------------------------
        # PHASE 7 & 8: OPTIONS & SIZING
        # ---------------------------------------------------------------------
        option_strategy, option_details = self._select_options_overlay(bar, fundamentals, signal, is_strong_uptrend, atr)
        kelly_fraction, sizing_path_tag = self._calculate_kelly_sizing(garch_vol, strategy_id=strategy_id)

        # HMM regime second opinion (signals/regime_multiplier.py) scales the
        # final Kelly Target down when the HMM's risk_on_probability is low --
        # it never adds directional alpha (its own score contribution is
        # always 0.0; see settings.SIGNAL_WEIGHTS['regime_multiplier']=0.0).
        # Defaults to 1.0 (no-op) if the signal didn't run or HMM is unavailable.
        regime_multiplier_output = outputs.get('regime_multiplier')
        regime_multiplier = regime_multiplier_output.confidence if regime_multiplier_output else 1.0

        # meta_label_composite is the geometric mean of active signal modules'
        # meta_label_proba values (Stage 4 placeholder, always 1.0 currently).
        # Applied multiplicatively alongside the HMM regime multiplier.
        kelly_fraction = max(0.0, min(
            kelly_fraction * regime_multiplier * meta_label_composite,
            settings.MAX_POSITION_WEIGHT
        ))

        # ---------------------------------------------------------------------
        # PHASE 9: COMPILE VERBOSE NOTES
        # ---------------------------------------------------------------------
        trend_status = "Uptrend" if is_uptrend else "No Uptrend"
        actionable_advice_signal = f"{signal}: {advice} (Regime: {macro.market_regime}, Trend: {trend_status})"

        verbose_notes = [
            f"SCORE {final_score}/100: {'; '.join(score_log)}.",
            f"MACD ENV: {macro.market_regime} | Ticker: {ticker}.",
            f"RISK FRAME: Sizing target {kelly_fraction * 100:.1f}% based on win probability models [{sizing_path_tag}].",
            f"OPTIONS HEDGE: {option_strategy} - {option_details}"
        ]
        if warnings:
            verbose_notes.append(f"CRITICAL WARNINGS: {', '.join(warnings)}")

        return {
            "Symbol": ticker,
            "Price": current_price,
            "Action Signal": signal,
            "Advice": advice,
            "Actionable Advice Signal": actionable_advice_signal,
            "Score": final_score,
            "Kelly Target": kelly_fraction,
            "Option Strategy": option_strategy,
            "buyRange": tactical_range,
            # NEW: first-class sell-side range surfaced alongside buyRange.
            # See ``apply_sell_side_range`` docstring for construction details.
            "sellRange": sell_side_range,
            "Strategy Explainer Notes": "\n".join(verbose_notes)
        }

    # =============================================================================
    # OPTION STRATEGY OVERLAY SELECTION MATRIX
    # =============================================================================
    def _select_options_overlay(self, 
                                 bar: MarketBarDTO, 
                                 fundamentals: FundamentalDataDTO, 
                                 signal: str, 
                                 is_uptrend: bool,
                                 atr: float = 0.0) -> Tuple[str, str]:
        """
        Determines the optimal derivatives hedge or income overlay based on volatility.
        """
        sector = fundamentals.sector
        price = bar.close
        safe_atr = atr if atr > 0 else (price * 0.02)
        is_yield_asset = "Real Estate" in sector or "Financial" in sector
        
        if signal in ["STRONG BUY", "BUY"]:
            if is_uptrend:
                strike = math.ceil(price + (1.5 * safe_atr))
                delta = "delta-15" if is_yield_asset else "delta-20"
                return (
                    f"OTM Covered Call ({delta})", 
                    f"Sell 30-day Call at strike ${strike:.2f} to capture premium while allowing upside."
                )
            else:
                strike = math.floor(price - (1.25 * safe_atr))
                return (
                    "Cash Secured Put", 
                    f"Sell 45-day Put at strike ${strike:.2f} (delta-30) to acquire shares at deep discount."
                )
        elif signal == "HOLD":
            upper_strike = math.ceil(price + (2.0 * safe_atr))
            lower_strike = math.floor(price - (2.0 * safe_atr))
            return (
                "Iron Condor / Strangle", 
                f"Sell credit spreads at ${lower_strike:.2f} Put and ${upper_strike:.2f} Call to capture volatility."
            )
        else: # RISK REDUCE / BEARISH
            if is_yield_asset:
                strike = math.floor(price + (0.5 * safe_atr))
                return (
                    "Defensive Covered Call", 
                    f"Sell near-the-money 15-day Call at strike ${strike:.2f} to buffer downward capital drag."
                )
            else:
                strike = math.floor(price * 0.90)
                return (
                    "Protective Collar", 
                    f"Purchase protective Put at strike ${strike:.2f} financed by selling near-the-money Covered Calls."
                )

    # =============================================================================
    # POSITION SIZING: VOLATILITY TARGETING + ESTIMATED-p FRACTIONAL KELLY
    # =============================================================================
    @property
    def transactions_store(self):
        """Lazily constructs a real TransactionsStore if none was injected."""
        if self._transactions_store is None:
            from transactions_store import TransactionsStore
            self._transactions_store = TransactionsStore()
        return self._transactions_store

    def _calculate_kelly_sizing(
        self,
        realized_vol: Optional[float] = None,
        strategy_id: Optional[str] = None,
    ) -> Tuple[float, str]:
        """
        Single source-of-truth position sizing call. Returns (weight, sizing_path_tag).

        When ``strategy_id`` is provided (Stage 1.7 per-strategy bootstrap path):
          - Delegates to ``kelly_sizing_for_strategy(transactions_store, strategy_id,
            realized_vol)`` which:
              1. Filters closed trades to ``strategy_id``.
              2. If < 30 per-strategy trades: falls back to vol-target-only,
                 tagged "vol_target_fallback".
              3. Otherwise bootstraps 1_000 resamples and takes the
                 5th-percentile Kelly fraction -- the conservative/epistemic-
                 humility sizing -- tagged "bootstrap_kelly_5th_pct(...)".
          - Either path is then clamped to ``settings.MAX_POSITION_WEIGHT``
            in the caller (``evaluate_security``).

        When ``strategy_id`` is None (backward-compatible global aggregate path):
          - Calls ``estimate_win_rate_and_payoff(all_closed_trades)`` on the
            global pool (no strategy filtering) and takes the point-estimate
            fractional Kelly. Falls back to vol-target-only when fewer than 30
            total closed trades exist. Tagged "aggregate_kelly" or
            "vol_target_fallback" accordingly.

        The final weight is NOT clamped here; ``evaluate_security()`` applies
        ``min(weight, settings.MAX_POSITION_WEIGHT)`` after multiplying by the
        regime and meta-label composites.
        """
        raw_weight, path_tag = self._raw_kelly_or_vol_target_sizing(
            realized_vol, strategy_id=strategy_id
        )
        # Clamp: enforce single-name ceiling before returning.
        return max(0.0, min(raw_weight, settings.MAX_POSITION_WEIGHT)), path_tag

    def _raw_kelly_or_vol_target_sizing(
        self,
        realized_vol: Optional[float],
        strategy_id: Optional[str] = None,
    ) -> Tuple[float, str]:
        """Unclamped sizing weight -- see _calculate_kelly_sizing for the clamp.

        Dispatches to the per-strategy bootstrap path (Stage 1.7) when
        ``strategy_id`` is provided, otherwise uses the global aggregate
        point-estimate path (Stage 1.6 backward-compatible).
        """
        # --- Stage 1.7: per-strategy bootstrap path ---
        if strategy_id is not None:
            weight, tag = kelly_sizing_for_strategy(
                self.transactions_store,
                strategy_id=strategy_id,
                realized_vol=realized_vol,
                min_trades=MIN_TRADES_REQUIRED,
                n_bootstraps=1_000,
                fraction=settings.KELLY_FRACTION,
                cap=settings.KELLY_CAP,
                target_vol=settings.VOL_TARGET,
                max_leverage=settings.MAX_LEVERAGE,
            )
            return weight, tag

        # --- Stage 1.6 (legacy): global aggregate point-estimate path ---
        try:
            closed_trades_df = self.transactions_store.closed_trades_df()
        except Exception as e:
            logger.error(f"Kelly sizing: failed to read transactions store: {e}")
            closed_trades_df = pd.DataFrame()

        p, b, n_trades = estimate_win_rate_and_payoff(closed_trades_df, lookback_trades=100)

        if not (math.isnan(p) or math.isnan(b)):
            return fractional_kelly(p, b, fraction=settings.KELLY_FRACTION, cap=settings.KELLY_CAP), "aggregate_kelly"

        logger.warning(
            f"Kelly sizing disabled (n_trades={n_trades} closed trades; need >= 30 for an "
            f"estimate, >= 50 for confidence). Falling back to volatility-target-only sizing "
            f"until 50 trades are recorded."
        )

        if realized_vol is None or (isinstance(realized_vol, float) and math.isnan(realized_vol)) or realized_vol <= 0:
            logger.warning("Kelly sizing fallback: realized_vol unavailable/non-positive; sizing weight = 0.0.")
            return 0.0, "cold_start_no_vol"

        return volatility_target_weight(
            realized_vol, target_vol=settings.VOL_TARGET, max_leverage=settings.MAX_LEVERAGE
        ), "vol_target_fallback"


# =============================================================================
# OPERATIONAL DEMONSTRATION
# =============================================================================
def test_strategy_engine_runs():
    """Deterministic validation runner showing top-down engine evaluation."""
    print("--- 🧠 RUNNING SYSTEMATIC STRATEGY ENGINE VALIDATION ---")
    
    # Instance 1: High Quality Stock in Hostile Macro regime
    print("\n[Scenario A: High Yield Asset (e.g. AGNC) during hostile spread spikes]")
    bar_a = MarketBarDTO(datetime.now(), "AGNC", 9.80, 10.05, 9.75, 9.85, 2500000)
    fund_a = FundamentalDataDTO(
        ticker="AGNC", company_name="AGNC Investment Corp", sector="Real Estate (mREIT)",
        pe_ratio=11.5, pb_ratio=0.88, book_value=11.20, eps_trailing=0.85,
        dividend_yield=0.145, dividend_growth_rate=-0.02, payout_ratio=0.92,
    )
    # Hostile macro regime with elevated corporate high-yield spreads
    macro_hostile = MacroEconomicDTO(0.05, 5.80, 2.80, 4.0)
    
    engine = StrategyEngine()
    result_a = engine.evaluate_security(
        bar=bar_a, fundamentals=fund_a, macro=macro_hostile, 
        forecast_price=9.20, trend_strength=45.0, atr=0.15
    )
    print(f"Ticker: {result_a['Symbol']}")
    print(f"Action Signal: {result_a['Action Signal']}")
    print(f"Buy Range: {result_a['buyRange']}")
    print(f"Advice: {result_a['Advice']}")
    print(f"Portfolio Sizing Target: {result_a['Kelly Target'] * 100:.2f}%")
    print(f"Action Notes:\n{result_a['Strategy Explainer Notes']}")
 
    # Instance 2: High Quality Asset in Risk-On regime
    print("\n[Scenario B: Defensive Asset (e.g. JNJ) in Bull Market / Risk-On]")
    bar_b = MarketBarDTO(datetime.now(), "JNJ", 155.00, 158.00, 154.50, 157.50, 4500000)
    fund_b = FundamentalDataDTO(
        ticker="JNJ", company_name="Johnson & Johnson", sector="Healthcare",
        pe_ratio=16.5, pb_ratio=1.45, book_value=110.00, eps_trailing=9.50,
        dividend_yield=0.0310, dividend_growth_rate=0.065, payout_ratio=0.52,
    )
    macro_safe = MacroEconomicDTO(0.45, 2.50, 2.10, 4.0)

    result_b = engine.evaluate_security(
        bar=bar_b, fundamentals=fund_b, macro=macro_safe, 
        forecast_price=168.00, trend_strength=72.0, atr=2.50
    )
    print(f"\nTicker: {result_b['Symbol']}")
    print(f"Action Signal: {result_b['Action Signal']}")
    print(f"Buy Range: {result_b['buyRange']}")
    print(f"Portfolio Sizing Target: {result_b['Kelly Target'] * 100:.2f}%")
    print(f"Action Notes:\n{result_b['Strategy Explainer Notes']}")


if __name__ == "__main__":
    test_strategy_engine_runs()
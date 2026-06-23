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

import math
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime

# Import type-safe data transfer containers
from dto_models import MarketBarDTO, FundamentalDataDTO, MacroEconomicDTO


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


class StrategyEngine:
    """
    Multi-phase quantitative engine that translates validated technical, fundamental,
    and macroeconomic parameters into high-conviction allocation instructions.
    """
    
    def __init__(self, risk_free_rate: float = 0.0425):
        self.risk_free_rate = risk_free_rate

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
                          chandelier_short: float = 0.0) -> Dict[str, Any]:
        """
        Executes multi-phase quantitative scoring across the security.
        Synthesizes technical, fundamental, macro, and volatility factors to produce
        high-precision signals, custom action ranges, options hedging, and explainability notes.
        """
        score = 50  # Baseline neutral score
        score_log: List[str] = []
        warnings: List[str] = []
        details: List[str] = []

        current_price = bar.close
        ticker = bar.ticker
        sector = fundamentals.sector

        # ---------------------------------------------------------------------
        # PHASE 1: SYSTEMIC MACRO OVERRIDES (TOP-DOWN RISK WINDOWS)
        # ---------------------------------------------------------------------
        regime = macro.market_regime
        if regime == "RECESSION":
            score_log.append("-15pts: Recession Regime Active (Inverted Yield Curve)")
            score -= 15
            warnings.append("Systemic recession warning.")
        elif regime == "CREDIT EVENT":
            score_log.append("-25pts: Hostile Credit Event (HY OAS Spreads Elevated)")
            score -= 25
            warnings.append("High debt distress window.")
        elif regime == "RISK ON":
            score_log.append("+10pts: Favorable Macro Regime")
            score += 10

        # Systemic killSwitch check (triggers if Sahm >= 0.5 or VIX > 30)
        # F-04 FIX: Mandate specifies a -5 LOCALIZED penalty, not a -50 hard score freeze.
        # The Phase 5 hard HOLD cap on BUY signals below serves as the secondary safety rail.
        if hasattr(macro, 'killSwitch') and macro.killSwitch:
            score_log.append("-5pts: Systemic Risk Overlay Active (Sahm/VIX Breach) — localized penalty applied")
            score -= 5
            warnings.append("SYSTEMIC KILLSWITCH ACTIVE: Fresh equity allocations halted.")

        # ---------------------------------------------------------------------
        # PHASE 2: SECTOR ROTATION & INTEREST SENSITIVITY
        # ---------------------------------------------------------------------
        if regime in ["RECESSION", "CREDIT EVENT"]:
            if "Financial" in sector or "Real Estate" in sector:
                score_log.append("-15pts: Macro headwind penalty on highly leveraged asset")
                score -= 15
            elif "Consumer Staples" in sector or "Healthcare" in sector:
                score_log.append("+10pts: Defensive sector premium")
                score += 10

        # ---------------------------------------------------------------------
        # PHASE 3: FUNDAMENTAL VALUATION CONFLUENCE
        # ---------------------------------------------------------------------
        graham_val = fundamentals.graham_number
        if graham_val > 0:
            if graham_val > current_price:
                score_log.append(f"+15pts: Undervalued vs Graham (${graham_val:.2f})")
                score += 15
                details.append("Value Anchor Met")
            else:
                score_log.append(f"-10pts: Overvalued vs Graham (${graham_val:.2f})")
                score -= 10
        else:
            score_log.append("-5pts: No Intrinsic Graham Value possible")
            score -= 5

        if fundamentals.dividend_yield > 0:
            if fundamentals.is_dividend_sustainable:
                score_log.append("+10pts: Sustainable Dividend")
                score += 10
            else:
                score_log.append("-25pts: Yield Trap Warning (Payout > 100%)")
                score -= 25
                warnings.append("Dividend Sustainability Failure")

        # ---------------------------------------------------------------------
        # PHASE 4: MOMENTUM, TREND, & FORECAST (CALIBRATED)
        # ---------------------------------------------------------------------
        if aroon_osc is not None:
            # 1. MACD Momentum
            if macd_line > macd_signal:
                score_log.append("+10pts: MACD Bullish")
                score += 10
            else:
                score_log.append("-15pts: MACD Bearish Crossover")
                score -= 15

            # 2. Aroon Oscillator Chop Filter
            if abs(aroon_osc) < 50:
                score_log.append(f"-15pts: Choppy Market via Aroon Oscillator ({aroon_osc:.1f}) - High False Positive Risk")
                score -= 15
                is_uptrend = False
            elif aroon_osc >= 50:
                score_log.append(f"+15pts: Strong Aroon Oscillator Uptrend ({aroon_osc:.1f})")
                score += 15
                is_uptrend = True
            else: # aroon_osc <= -50
                score_log.append(f"-15pts: Strong Aroon Oscillator Downtrend ({aroon_osc:.1f})")
                score -= 15
                is_uptrend = False
        else:
            # Fallback to legacy Trend Strength (Aroon Up) if Aroon Oscillator not provided
            # Calibrated Trend: >= 50 is bullish, 30-50 is neutral/consolidation, < 30 is bearish
            if trend_strength >= 50.0:
                score_log.append("+10pts: Bullish technical trend (Aroon >= 50)")
                score += 10
                is_uptrend = True
            elif 30.0 <= trend_strength < 50.0:
                score_log.append("-5pts: Weakening trend momentum")
                score -= 5
                is_uptrend = False
            else:
                score_log.append("-15pts: Bearish pricing structure")
                score -= 15
                is_uptrend = False

        # Calibrated Forecast: 1.5% in 30 days is an excellent 18% annualized return
        if forecast_price > current_price:
            expected_gain = ((forecast_price - current_price) / current_price) * 100
            if expected_gain >= 1.5:
                score_log.append(f"+10pts: Strong forecast projection (+{expected_gain:.1f}%)")
                score += 10
            elif expected_gain > 0:
                score_log.append(f"+5pts: Moderate positive forecast (+{expected_gain:.1f}%)")
                score += 5
        else:
            score_log.append("-10pts: Forecast suggests structural price erosion")
            score -= 10

        # ---------------------------------------------------------------------
        # PHASE 4B: RELATIVE STRENGTH & OVERBOUGHT/OVERSOLD (RSI)
        # ---------------------------------------------------------------------
        if relative_strength is not None:
            if relative_strength > 0:
                score_log.append(f"+10pts: Outperforming S&P 500 (RS: {relative_strength:.2f})")
                score += 10
            else:
                score_log.append(f"-10pts: Underperforming S&P 500 (RS: {relative_strength:.2f})")
                score -= 10

        if rsi is not None:
            if rsi < 30:
                score_log.append("+20pts: RSI < 30 (Mean Reversion)")
                score += 20
            elif rsi > 70:
                score_log.append("-20pts: RSI > 70 (Overbought)")
                score -= 20

        # ---------------------------------------------------------------------
        # PHASE 4C: RISK ADJUSTED RETURNS
        # ---------------------------------------------------------------------
        if sortino_ratio is not None and sortino_ratio > 2.0:
            score_log.append(f"+10pts: High Sortino ({sortino_ratio:.2f})")
            score += 10
        if max_drawdown is not None and max_drawdown < -0.25:
            score_log.append(f"-10pts: Steep Drawdown ({max_drawdown*100:.1f}%)")
            score -= 10

        # ---------------------------------------------------------------------
        # PHASE 4D: EDGE RATIO & GARCH VOLATILITY OVERLAYS
        # ---------------------------------------------------------------------
        if edge_ratio is not None and edge_ratio > 0.0:
            if edge_ratio >= 1.2:
                score_log.append(f"+15pts: Strong Mathematical Edge ({edge_ratio:.2f})")
                score += 15
            elif edge_ratio < 0.8:
                score_log.append(f"-15pts: Negative Mathematical Edge ({edge_ratio:.2f})")
                score -= 15

        if garch_vol is not None and garch_vol > 0.40:
            score_log.append(f"-20pts: Extreme GARCH Volatility ({garch_vol*100:.1f}%) - High Tail Risk")
            score -= 20

        # Enforce analytical scoring boundaries [0, 100]
        final_score = max(0, min(100, score))

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

        # ---------------------------------------------------------------------
        # PHASE 7 & 8: OPTIONS & SIZING
        # ---------------------------------------------------------------------
        option_strategy, option_details = self._select_options_overlay(bar, fundamentals, signal, is_uptrend, atr)
        kelly_fraction = self._calculate_kelly_sizing(final_score, is_uptrend, sortino_ratio, edge_ratio)

        # ---------------------------------------------------------------------
        # PHASE 9: COMPILE VERBOSE NOTES
        # ---------------------------------------------------------------------
        trend_status = "Uptrend" if is_uptrend else "No Uptrend"
        actionable_advice_signal = f"{signal}: {advice} (Regime: {regime}, Trend: {trend_status})"

        verbose_notes = [
            f"SCORE {final_score}/100: {'; '.join(score_log)}.",
            f"MACD ENV: {regime} | Ticker: {ticker}.",
            f"RISK FRAME: Sizing target {kelly_fraction * 100:.1f}% based on win probability models.",
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
    # MATHEMATICAL KELLY CRITERION MODEL
    # =============================================================================
    def _calculate_kelly_sizing(self, score: int, is_uptrend: bool, sortino_ratio: Optional[float] = None, edge_ratio: Optional[float] = None) -> float:
        """
        Implements the Kelly Criterion allocation formula: f* = p - (q / b)
        """
        win_probability = 0.35 + (score / 100.0) * 0.40
        b = 2.0  # Assumed standard risk-reward payout ratio of 2:1
        q = 1.0 - win_probability
        
        raw_kelly = win_probability - (q / b)
        half_kelly = raw_kelly * 0.5
        
        # Kelly sizing execution logic updates:
        # Require a positive Edge Ratio (edge_ratio >= 1.0) and sortino > 1.0 before allowing max 25% "STRONG BUY" allocation.
        # Otherwise, follow scoring brackets: 15% (uptrend) or 5% (no uptrend) for BUY, 5% for HOLD, 0% for RISK REDUCE.
        if score >= 75 and sortino_ratio is not None and sortino_ratio > 1.0 and edge_ratio is not None and edge_ratio >= 1.0:
            max_allocation = 0.25
        elif score >= 55:
            max_allocation = 0.15 if is_uptrend else 0.05
        elif score >= 35:
            max_allocation = 0.05
        else:
            max_allocation = 0.00
            
        return max(0.0, min(half_kelly, max_allocation))


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
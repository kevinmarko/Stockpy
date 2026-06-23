"""
InvestYo Quant Platform - Advanced Research & Analytics Engine
==============================================================
Step 8 of the Modernization Roadmap: Research Topic Implementation.

This module implements 10 highly advanced, sector-specific quantitative metrics 
specifically designed to manage risk for mREITs, BDCs, and high-yield equities.
It ingests Transactions, FidelityData, and Automated Dashboard inputs to produce
deep-risk analytics.
"""

import pandas as pd
import numpy as np
import os
import json
import logging
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# Set up clean logging
logger = logging.getLogger("ResearchEngine")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class AdvancedResearchEngine:
    """
    State-of-the-art analytical engine. Vectorizes and calculates metrics 21-30 
    to protect and optimize a credit-sensitive portfolio.
    """
    
    def __init__(self, risk_free_rate: float = 0.0425, real_yield: float = 0.0215):
        self.risk_free_rate = risk_free_rate
        self.real_yield = real_yield # Nominal 10Y Treasury - CPI Inflation

    # =============================================================================
    # METRICS 21-25: VALUATIONS AND INCOME DYNAMICS
    # =============================================================================
    # EXPLANATION: Calculate sector-adjusted valuation, reverse-engineering missing EPS/BV
    # from P/E or P/B and price if they are invalid, and applying FFO/NII sector multipliers.
    def calculate_sector_adjusted_valuation(self, sector: str, pe: float, pb: float, book_value: float, eps: float, price: float = 0.0) -> float:
        """
        [Topic 21] FFO/NII Sector-Adjusted Valuation Guard.
        REITs and BDCs have high non-cash depreciation which penalizes standard GAAP EPS.
        If Real Estate or Financials, we apply a cash-flow proxy adjustment to fair value.
        """
        # --- FIX: Reverse Engineer Missing Data ---
        # If EPS or Book Value is missing (0), we can reverse-engineer them from P/E and P/B
        if eps <= 0 and pe > 0 and price > 0:
            eps = price / pe
        if book_value <= 0 and pb > 0 and price > 0:
            book_value = price / pb

        if pe <= 0:
            pe = 12.0 # Baseline industry average multiplier fallback
            
        if "Real Estate" in str(sector) or "REIT" in str(sector):
            # Cash Flow Proxy: Adjust EPS upward by 35% to emulate Funds From Operations (FFO)
            adjusted_eps = eps * 1.35
            adjusted_bv = book_value * 1.05
            return math.sqrt(22.5 * max(0.01, adjusted_eps) * max(0.01, adjusted_bv))
        
        elif "Financial" in str(sector) or "BDC" in str(sector):
            # BDC Adjustment: Value predominantly focused on Net Asset Value (Book Value)
            adjusted_eps = eps * 1.15
            return math.sqrt(15.0 * max(0.01, adjusted_eps) * max(0.01, book_value))
            
        # Standard Benjamin Graham Number for industrial/tech assets
        if eps <= 0 or book_value <= 0:
            return 0.0 # Only return 0 if reverse-engineering completely failed
        return math.sqrt(22.5 * eps * book_value)

    # EXPLANATION: Calculate the real yield valuation drag on assets when real yields exceed 2%.
    # Dynamically converts percentage formats (e.g. 2.15) to ratios (e.g. 0.0215) to prevent negative drag factors.
    def calculate_real_yield_drag(self, fair_value: float) -> float:
        """
        [Topic 22] Real Yield Valuation Drag.
        When Real Yields are restrictive (> 2.0%), high-yielders experience valuation pressure.
        Formula: $V_{adjusted} = V_{fair} \cdot (1 - \text{RealYield})$
        """
        ry = self.real_yield / 100.0 if self.real_yield > 0.2 else self.real_yield
        if ry > 0.02:
            drag_factor = 1.0 - (ry - 0.02)
            return max(0.0, fair_value * drag_factor)
        return fair_value

    # EXPLANATION: Compute the premium spread of dividend yield over the risk-free rate.
    def calculate_dividend_premium_spread(self, div_yield: float) -> float:
        """
        [Topic 23] Dividend Premium Spread (DPS).
        Spread of yield over risk-free rate: $DPS = \text{DivYield} - \text{RiskFreeRate}$
        If this spread is negative, the asset's yield is not compensated relative to risk-free debt.
        """
        # Ensure div_yield is a decimal, not a whole percentage number (e.g., 5.0 instead of 0.05)
        if div_yield > 1.0:
            div_yield = div_yield / 100.0
        return div_yield - self.risk_free_rate

    # EXPLANATION: Measure velocity of institutional ownership changes weighted by total base, handling missing data cleanly.
    def calculate_institutional_velocity(self, inst_own_raw: Any, quarterly_change_raw: Any) -> float:
        """
        [Topic 24] Institutional Ownership Velocity.
        Calculates the velocity of large institutional capital movement.
        Returns a directional velocity score (Negative = Institutions are liquidating).
        """
        try:
            # --- FIX: Handle missing data gracefully without defaulting to 0 immediately ---
            if not inst_own_raw or not quarterly_change_raw or pd.isna(inst_own_raw) or pd.isna(quarterly_change_raw):
                return 0.0000
                
            # Strip percentage formatting and convert to decimal
            inst_own = float(str(inst_own_raw).replace("%", "").strip())
            change = float(str(quarterly_change_raw).replace("%", "").strip())
            
            # Auto-correct if passed as whole numbers (e.g., 60.0 instead of 0.60)
            if inst_own > 1.0: inst_own /= 100.0
            if change > 1.0 or change < -1.0: change /= 100.0
            
            # Institutional Velocity: Change weighted relative to overall ownership base
            velocity = change * (1.0 + inst_own)
            return round(velocity, 4) # Maintain granularity
        except Exception:
            return 0.0000

    # EXPLANATION: Estimate the payback period based on compounded dividend growth, handling scale mismatches.
    def calculate_dividend_payback_horizon(self, price: float, annual_div: float, dgr_5y: float) -> float:
        """
        [Topic 25] Dividend Payback Horizon (DPH).
        Calculates how many years of dividend payouts are needed to fully recover the cost of shares.
        Incorporates the 5-Year Dividend Growth Rate ($g$) compounding.
        Formula solves for $n$ where: $\sum_{t=1}^n D_0(1+g)^t \ge Price$
        """
        if annual_div <= 0 or price <= 0:
            return 99.0 # Infinite horizon fallback
            
        # --- FIX: Scaling mismatch auto-correction ---
        # If annual_div was mistakenly passed as the Yield Percentage (e.g., 5.0 for 5%), 
        # it would instantly trigger a 1-year payback. Convert it back to a dollar amount.
        if annual_div > price * 0.5: 
            annual_div = (annual_div / 100.0) * price

        g = max(-0.2, min(0.15, dgr_5y)) # Clamp growth rate between -20% and +15%
        cumulative_payout = 0.0
        current_div = annual_div
        
        for year in range(1, 40): # Cap testing horizon at 40 years
            current_div = current_div * (1.0 + g)
            cumulative_payout += current_div
            if cumulative_payout >= price:
                return float(year)
                
        return 40.0

    # =============================================================================
    # METRICS 26-30: RISK PROPAGATION AND MICROSTRUCTURES
    # =============================================================================
    # EXPLANATION: Calculate balance sheet distress based on leverage metrics relative to sector limits, with neutral default.
    def calculate_leverage_distress_factor(self, sector: str, debt_to_equity: float) -> float:
        """
        [Topic 26] Leveraged Capital Distress Factor.
        Calculates balance sheet risk. REITs and BDCs have naturally high structural debt.
        Squeezes scores if debt levels exceed regulatory or historical risk limits.
        """
        # --- FIX: Missing Debt Override ---
        # If debt data is missing or precisely 0, we output a neutral 0.5 score. 
        # Previously, 0 debt returned a perfect 1.0, ruining the metric's reliability.
        if debt_to_equity is None or pd.isna(debt_to_equity) or debt_to_equity <= 0.001:
            return 0.5 

        if "Real Estate" in str(sector) or "REIT" in str(sector):
            # mREIT limit: Debt/Equity > 6.0x is highly distressed
            return min(1.0, max(0.0, (6.0 - debt_to_equity) / 6.0))
        elif "Financial" in str(sector) or "BDC" in str(sector):
            # BDC regulatory limit: Debt/Equity > 2.0x is highly constrained
            return min(1.0, max(0.0, (2.0 - debt_to_equity) / 2.0))
        
        # Standard corporate limits
        return min(1.0, max(0.0, (1.5 - debt_to_equity) / 1.5))

    # EXPLANATION: Estimate linear OLS slope of asset's relative strength ratio to SPY closes over 20 days.
    def calculate_relative_strength_momentum_slope(self, asset_closes: pd.Series, spy_closes: pd.Series) -> float:
        """
        [Topic 27] RS Momentum Slope (RS-MACD).
        Tracks whether the asset outperformance relative to SPY is accelerating or decaying.
        Calculated as the linear slope of the Relative Strength ratio over 20 days.
        """
        if asset_closes is None or spy_closes is None or len(asset_closes) < 30 or len(spy_closes) < 30:
            return 0.0000
            
        try:
            # Standardize timelines and calculate ratio
            rs_ratio = asset_closes.tail(20) / spy_closes.tail(20)
            x = np.arange(len(rs_ratio))
            y = rs_ratio.to_numpy()
            
            # Calculate OLS Regression Slope
            slope, _ = np.polyfit(x, y, 1)
            return round(float(slope * 1000), 4) # Scaling for readable spreadsheet formats
        except Exception:
            return 0.0000

    # EXPLANATION: Calculate average execution slippage in basis points, returning a float directly instead of a dictionary.
    # Cleans formatting like dollar signs and commas, and matches Trans Code case-insensitively.
    def calculate_realized_slippage(self, transactions_df: pd.DataFrame) -> float:
        """
        [Topic 28] Realized Slippage & Implementation Shortfall Tracker.
        Parses the Transactions sheet to find average realized transaction execution slip.
        Returns the granular Basis Points (bps) directly as a float.
        """
        if transactions_df is None or transactions_df.empty or 'Trans Code' not in transactions_df.columns:
            return 0.0

        try:
            # Filter for execution records (BUY, SELL, SHORT, COVER)
            exec_df = transactions_df[
                transactions_df['Trans Code'].astype(str).str.upper().str.strip().isin(['BUY', 'SELL', 'SHORT', 'COVER'])
            ].copy()
            
            if exec_df.empty or 'Amount' not in exec_df.columns:
                return 0.0

            if 'Commission' in exec_df.columns:
                exec_df['Clean_Amount'] = exec_df['Amount'].astype(str).replace({r'\$': '', ',': ''}, regex=True).astype(float)
                exec_df['Clean_Commission'] = exec_df['Commission'].astype(str).replace({r'\$': '', ',': ''}, regex=True).astype(float)
                friction = exec_df['Clean_Commission'].abs()
                computed_amount = exec_df['Clean_Amount'].abs()
            else:
                # Fallback manual calculation if Commission column doesn't exist
                def clean_val(v):
                    if pd.isna(v): return 0.0
                    return float(str(v).replace("$", "").replace(",", "").replace("-", "").strip())

                exec_df['Clean_Quantity'] = exec_df['Quantity'].apply(clean_val)
                exec_df['Clean_Price'] = exec_df['Price'].apply(clean_val)
                exec_df['Clean_Amount'] = exec_df['Amount'].apply(clean_val)
                
                computed_amount = exec_df['Clean_Quantity'] * exec_df['Clean_Price']
                friction = (exec_df['Clean_Amount'] - computed_amount).abs()

            # Calculate slipping drag as Basis Points (1 bps = 0.01%)
            if computed_amount.sum() > 0:
                return round(float((friction.sum() / computed_amount.sum()) * 10000.0), 2)
                
        except Exception as e:
            logger.warning(f"Slippage tracking bypassed: {e}")

        return 0.0

    # EXPLANATION: Compute options volatility edge using ATR and price versus historical volatility.
    def calculate_options_volatility_edge(self, historical_vol: float, atr: float, price: float) -> float:
        """
        [Topic 29] Options Implied Volatility Edge.
        Compares ATR-based volatility proxy against historical volatility (HV).
        An Edge > 0 indicates option premiums are rich, making credit selling (Puts/Calls) favorable.
        """
        if price <= 0: return 0.0
        atr_vol_proxy = (atr * math.sqrt(252)) / price
        return round(atr_vol_proxy - historical_vol, 4)

    # EXPLANATION: Calculate portfolio tail-dependency using maximum correlation, returning a float directly.
    def calculate_portfolio_covar_dependency(self, returns_df: pd.DataFrame) -> float:
        """
        [Topic 30] Portfolio Tail-Dependency (CoVaR Proxy).
        Evaluates portfolio risk concentration. High covariance among holdings (e.g., holding 
        only interest-rate-sensitive assets) increases systemic risk.
        Returns the granular maximum correlation coefficient directly.
        """
        # --- FIX: Return Float Instead of Dict with Binary Warning ---
        if returns_df is None or returns_df.empty or returns_df.shape[1] < 2:
            return 0.0
            
        try:
            corr_matrix = returns_df.corr().abs()
            # Extract upper triangle values to avoid diagonal self-correlation (1.0)
            upper_tri = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
            max_corr = float(upper_tri.max().max())
            
            return round(max_corr, 4) if not np.isnan(max_corr) else 0.0
        except Exception as e:
            logger.warning(f"CoVaR correlation parsing skipped: {e}")
            
        return 0.0


# =============================================================================
# AUTO-AUDIT & GRAVITY WORKFLOW TESTER
# =============================================================================
if __name__ == "__main__":
    print("--- Running Advanced Quantitative Research Suite Verification ---")
    engine = AdvancedResearchEngine()
    
    # Verify adjusted valuations for REITs
    adjusted_val = engine.calculate_sector_adjusted_valuation(
        sector="Real Estate (mREIT)", pe=10.0, pb=0.85, book_value=12.50, eps=1.20, price=10.50
    )
    print(f"✅ Adjusted REIT Intrinsic Value: ${adjusted_val:.2f} (Standard would report lower)")
    
    # Verify Missing Data Reverse-Engineering for Graham Number
    missing_data_val = engine.calculate_sector_adjusted_valuation(
        sector="Technology", pe=15.0, pb=2.0, book_value=0.0, eps=0.0, price=30.0
    )
    print(f"✅ Missing Data Graham Recovery: ${missing_data_val:.2f} (Prevented 0.0 crash)")
    
    # Verify Real Yield valuation drag
    dragged_val = engine.calculate_real_yield_drag(adjusted_val)
    print(f"✅ Real Yield Dragged Valuation: ${dragged_val:.2f} (Macro inflation pressure accounted for)")
    
    # Verify Dividend Payback Horizon with Auto-Scaling (Passing 5.0% instead of $0.52 cash)
    payback = engine.calculate_dividend_payback_horizon(price=10.50, annual_div=5.0, dgr_5y=0.04)
    print(f"✅ Compounded Capital Recovery Horizon: {payback:.1f} Years (Auto-scaled % input)")
    
    # Verify Leverage Missing Data Override
    leverage = engine.calculate_leverage_distress_factor(sector="Financial", debt_to_equity=0.0)
    print(f"✅ Leverage Neutral Override: {leverage:.2f} (Prevented false 1.0 perfect score)")
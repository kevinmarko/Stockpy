# ==============================================================================
# MODULE: TECHNICAL INDICATORS & OPTIONS STRATEGY ENGINE
# File: technical_options_engine.py
# Description: Implements advanced indicators (Aroon Oscillator, Coppock Curve, 
#              Chandelier Exit), scaled GJR-GARCH(1,1) volatility models,
#              Implied Volatility Rank (IVR), and Option Strategy Matrix matching config.py.
# ==============================================================================

import logging
import numpy as np
import pandas as pd
import pandas_ta_classic as ta
from typing import Dict, Any, Optional
from scipy.stats import norm
from scipy.optimize import brentq

from settings import settings

# --- GLOBAL CONSTANTS ---
RISK_FREE_RATE = settings.RISK_FREE_RATE
TRADING_DAYS_PER_YEAR = 252


# Try importing arch library for GJR-GARCH modeling
try:
    from arch import arch_model  # type: ignore
    ARCH_AVAILABLE = True
except ImportError:
    ARCH_AVAILABLE = False

# Set up module logger
logger = logging.getLogger("TechnicalOptionsEngine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# Monkey patch chandelier_exit if not present in pandas_ta_classic
if not hasattr(ta, "chandelier_exit"):
    def chandelier_exit_patch(self, length=22, multiplier=3.0, **kwargs):
        """
        Custom chandelier_exit implementation registered to pandas_ta.
        Uses a lookback period and ATR multiplier to compute long and short exits.
        """
        df = self._df
        atr_val = self.atr(length=length)
        highest_high = df['High'].rolling(window=length).max()
        lowest_low = df['Low'].rolling(window=length).min()
        long = highest_high - (multiplier * atr_val)
        short = lowest_low + (multiplier * atr_val)
        return pd.DataFrame({
            "CHANDELIER_EXIT_LONG": long,
            "CHANDELIER_EXIT_SHORT": short
        }, index=df.index)
        
    ta.chandelier_exit = chandelier_exit_patch
    try:
        from pandas_ta_classic.core import AnalysisIndicators
        setattr(AnalysisIndicators, "chandelier_exit", chandelier_exit_patch)
    except Exception as e:
        logger.warning(f"Failed to bind chandelier_exit to AnalysisIndicators: {e}")


class OptionsPricingRecommender:
    def __init__(self, stock_price: float, risk_free_rate: float = RISK_FREE_RATE):
        """
        Initializes the options pricing engine.
        
        Variables:
        stock_price (float): The current spot price of the underlying asset.
        risk_free_rate (float): The annualized risk-free interest rate.
        """
        self.S = float(stock_price)
        self.r = float(risk_free_rate)

    def black_scholes_pricing_and_greeks(self, K: float, T: float, sigma: float, option_type: str = 'call') -> dict:
        """
        Analytically computes the theoretical option price and Greeks using the Black-Scholes PDE.
        
        Variables:
        K (float): Strike Price
        T (float): Time to Expiration (in years)
        sigma (float): Annualized Implied Volatility
        option_type (str): 'call' or 'put'
        """
        # Prevent division by zero errors for expired options
        if T <= 0:
            return {'Price': max(0.0, self.S - K) if option_type == 'call' else max(0.0, K - self.S),
                    'Delta': 0.0, 'Gamma': 0.0, 'Vega': 0.0, 'Theta_Daily': 0.0}

        # Prevent division by zero / NaN propagation when volatility is zero,
        # negative, or unavailable (e.g. a degenerate upstream IV/GARCH read).
        # Degrades to the same intrinsic-value, zero-Greeks shape as the T<=0
        # branch above rather than crashing (CONSTRAINT #6).
        if sigma <= 0 or np.isnan(sigma):
            return {'Price': max(0.0, self.S - K) if option_type == 'call' else max(0.0, K - self.S),
                    'Delta': 0.0, 'Gamma': 0.0, 'Vega': 0.0, 'Theta_Daily': 0.0}

        d1 = (np.log(self.S / K) + (self.r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        
        if option_type.lower() == 'call':
            price = self.S * norm.cdf(d1) - K * np.exp(-self.r * T) * norm.cdf(d2)
            delta = norm.cdf(d1)
        elif option_type.lower() == 'put':
            price = K * np.exp(-self.r * T) * norm.cdf(-d2) - self.S * norm.cdf(-d1)
            delta = norm.cdf(d1) - 1.0
        else:
            raise ValueError("option_type must be 'call' or 'put'")

        gamma = norm.pdf(d1) / (self.S * sigma * np.sqrt(T))
        vega = self.S * norm.pdf(d1) * np.sqrt(T)
        
        # Theta calculated as annual decay, divided by 252 for daily representation
        theta_annual = -(self.S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
        if option_type.lower() == 'call':
            theta_annual -= self.r * K * np.exp(-self.r * T) * norm.cdf(d2)
        else:
            theta_annual += self.r * K * np.exp(-self.r * T) * norm.cdf(-d2)
            
        theta_daily = theta_annual / TRADING_DAYS_PER_YEAR

        return {
            'Price': price, 
            'Delta': delta, 
            'Gamma': gamma, 
            'Vega': vega, 
            'Theta_Daily': theta_daily
        }

    def find_strike_for_delta(self, target_delta: float, T: float, sigma: float, option_type: str = 'call') -> float:
        """
        Uses SciPy's Brentq root-finding algorithm to find the exact strike price (K) 
        that corresponds to the target Delta parameter.
        """
        def delta_difference(K_guess):
            greeks = self.black_scholes_pricing_and_greeks(K_guess, T, sigma, option_type)
            return greeks['Delta'] - target_delta

        # Establish bracketing boundaries for the solver (10% of stock price to 300% of stock price)
        lower_bound = self.S * 0.10
        upper_bound = self.S * 3.00

        try:
            # Brentq finds the root (where delta_difference == 0)
            optimal_strike = brentq(delta_difference, lower_bound, upper_bound)
            # Round to the nearest $0.50 strike standard interval
            return round(optimal_strike * 2) / 2
        except ValueError:
            # Fallback to current spot price if algorithm fails to converge
            return round(self.S * 2) / 2

    def calculate_realizable_theta(self, theoretical_theta: float, dte: int) -> float:
        """
        Applies empirical execution friction to theoretical theta.
        Based on institutional decay hair-cuts varying inversely with DTE.
        """
        if dte <= 1:
            haircut = 0.40 # 40% drag on 1 DTE
        elif dte <= 7:
            haircut = 0.22 # 22% drag on 7 DTE
        elif dte <= 30:
            haircut = 0.12 # 12% drag on 30 DTE
        else:
            haircut = 0.05 # 5% baseline drag

        return theoretical_theta * (1.0 - haircut)

    def generate_strategy_pricing_matrix(
        self, 
        true_ivr: float, 
        current_iv: float, 
        trend_bias: str, 
        target_dte: int = 30,
        vrp: Optional[float] = None,
        macro_dto: Optional[Any] = None
    ) -> dict:
        """
        Deterministic Options Matrix synthesizing Trend, True IVR, and Target Deltas 
        to output specific recommended Call and Put prices, gated by Volatility Risk Premium (VRP).
        """
        T = target_dte / 365.0
        sigma = current_iv
        
        # The ultimate returned dictionary payload
        directive = {
            "Strategy": "Cash",
            "Action": "Wait",
            "Legs": [],
            "Net_Premium": 0.0,
            "Realizable_Daily_Theta": 0.0
        }

        # Defined Risk Parameters (Standard Target Deltas)
        SHORT_DELTA_TARGET = 0.30
        LONG_DELTA_TARGET = 0.15
        CONDOR_SHORT_TARGET = 0.16
        CONDOR_LONG_TARGET = 0.05
        ATM_DELTA_TARGET = 0.50

        # Enforce VRP regime gate: only sell premium if true_ivr > 50, vrp > 0.02, vix < 30, not CREDIT EVENT
        sell_premium_allowed = True
        if vrp is not None and vrp <= 0.02:
            sell_premium_allowed = False
        if macro_dto is not None:
            vix = getattr(macro_dto, 'vix', 15.0)
            regime = getattr(macro_dto, 'market_regime', 'RISK ON')
            if vix >= 30.0 or regime == 'CREDIT EVENT':
                sell_premium_allowed = False

        if true_ivr > 50.0:
            if not sell_premium_allowed:
                return directive  # high IV but gated -> Cash / Wait (do not buy expensive options)
            # HIGH IVR REGIME: Premium Selling Environment
            if trend_bias == 'Bullish':
                directive["Strategy"] = "Put Credit Spread"
                directive["Action"] = "Sell to Open"
                
                # Leg 1: Short Put
                k_short = self.find_strike_for_delta(-SHORT_DELTA_TARGET, T, sigma, 'put')
                short_metrics = self.black_scholes_pricing_and_greeks(k_short, T, sigma, 'put')
                
                # Leg 2: Long Put (Protection)
                k_long = self.find_strike_for_delta(-LONG_DELTA_TARGET, T, sigma, 'put')
                long_metrics = self.black_scholes_pricing_and_greeks(k_long, T, sigma, 'put')

                directive["Legs"] = [
                    {"Side": "Short", "Type": "Put", "Strike": k_short, "Price": round(short_metrics['Price'], 2), "Delta": round(short_metrics['Delta'], 2)},
                    {"Side": "Long", "Type": "Put", "Strike": k_long, "Price": round(long_metrics['Price'], 2), "Delta": round(long_metrics['Delta'], 2)}
                ]
                directive["Net_Premium"] = round(short_metrics['Price'] - long_metrics['Price'], 2)
                raw_theta = short_metrics['Theta_Daily'] - long_metrics['Theta_Daily']
                directive["Realizable_Daily_Theta"] = round(self.calculate_realizable_theta(raw_theta, target_dte), 4)

            elif trend_bias == 'Bearish':
                directive["Strategy"] = "Call Credit Spread"
                directive["Action"] = "Sell to Open"
                
                # Leg 1: Short Call
                k_short = self.find_strike_for_delta(SHORT_DELTA_TARGET, T, sigma, 'call')
                short_metrics = self.black_scholes_pricing_and_greeks(k_short, T, sigma, 'call')
                
                # Leg 2: Long Call (Protection)
                k_long = self.find_strike_for_delta(LONG_DELTA_TARGET, T, sigma, 'call')
                long_metrics = self.black_scholes_pricing_and_greeks(k_long, T, sigma, 'call')

                directive["Legs"] = [
                    {"Side": "Short", "Type": "Call", "Strike": k_short, "Price": round(short_metrics['Price'], 2), "Delta": round(short_metrics['Delta'], 2)},
                    {"Side": "Long", "Type": "Call", "Strike": k_long, "Price": round(long_metrics['Price'], 2), "Delta": round(long_metrics['Delta'], 2)}
                ]
                directive["Net_Premium"] = round(short_metrics['Price'] - long_metrics['Price'], 2)
                raw_theta = short_metrics['Theta_Daily'] - long_metrics['Theta_Daily']
                directive["Realizable_Daily_Theta"] = round(self.calculate_realizable_theta(raw_theta, target_dte), 4)

            else: # Neutral Trend Bias
                directive["Strategy"] = "Iron Condor"
                directive["Action"] = "Sell to Open"
                
                # Put Spread Side
                k_short_put = self.find_strike_for_delta(-CONDOR_SHORT_TARGET, T, sigma, 'put')
                short_put_metrics = self.black_scholes_pricing_and_greeks(k_short_put, T, sigma, 'put')
                k_long_put = self.find_strike_for_delta(-CONDOR_LONG_TARGET, T, sigma, 'put')
                long_put_metrics = self.black_scholes_pricing_and_greeks(k_long_put, T, sigma, 'put')
                
                # Call Spread Side
                k_short_call = self.find_strike_for_delta(CONDOR_SHORT_TARGET, T, sigma, 'call')
                short_call_metrics = self.black_scholes_pricing_and_greeks(k_short_call, T, sigma, 'call')
                k_long_call = self.find_strike_for_delta(CONDOR_LONG_TARGET, T, sigma, 'call')
                long_call_metrics = self.black_scholes_pricing_and_greeks(k_long_call, T, sigma, 'call')

                directive["Legs"] = [
                    {"Side": "Short", "Type": "Put", "Strike": k_short_put, "Price": round(short_put_metrics['Price'], 2)},
                    {"Side": "Long", "Type": "Put", "Strike": k_long_put, "Price": round(long_put_metrics['Price'], 2)},
                    {"Side": "Short", "Type": "Call", "Strike": k_short_call, "Price": round(short_call_metrics['Price'], 2)},
                    {"Side": "Long", "Type": "Call", "Strike": k_long_call, "Price": round(long_call_metrics['Price'], 2)}
                ]
                
                credit_put = short_put_metrics['Price'] - long_put_metrics['Price']
                credit_call = short_call_metrics['Price'] - long_call_metrics['Price']
                directive["Net_Premium"] = round(credit_put + credit_call, 2)
                
                raw_theta = (short_put_metrics['Theta_Daily'] - long_put_metrics['Theta_Daily']) + (short_call_metrics['Theta_Daily'] - long_call_metrics['Theta_Daily'])
                directive["Realizable_Daily_Theta"] = round(self.calculate_realizable_theta(raw_theta, target_dte), 4)

        elif true_ivr < 30.0:
            # LOW IVR REGIME: Premium Buying Environment
            if trend_bias == 'Bullish':
                directive["Strategy"] = "Call Debit Spread"
                directive["Action"] = "Buy to Open"
                
                # Leg 1: Long Call (ATM)
                k_long = self.find_strike_for_delta(ATM_DELTA_TARGET, T, sigma, 'call')
                long_metrics = self.black_scholes_pricing_and_greeks(k_long, T, sigma, 'call')
                
                # Leg 2: Short Call (Out of the Money to finance)
                k_short = self.find_strike_for_delta(SHORT_DELTA_TARGET, T, sigma, 'call')
                short_metrics = self.black_scholes_pricing_and_greeks(k_short, T, sigma, 'call')
                
                directive["Legs"] = [
                    {"Side": "Long", "Type": "Call", "Strike": k_long, "Price": round(long_metrics['Price'], 2)},
                    {"Side": "Short", "Type": "Call", "Strike": k_short, "Price": round(short_metrics['Price'], 2)}
                ]
                directive["Net_Premium"] = round((long_metrics['Price'] - short_metrics['Price']) * -1.0, 2)

            elif trend_bias == 'Bearish':
                directive["Strategy"] = "Put Debit Spread"
                directive["Action"] = "Buy to Open"
                
                k_long = self.find_strike_for_delta(-ATM_DELTA_TARGET, T, sigma, 'put')
                long_metrics = self.black_scholes_pricing_and_greeks(k_long, T, sigma, 'put')
                
                k_short = self.find_strike_for_delta(-SHORT_DELTA_TARGET, T, sigma, 'put')
                short_metrics = self.black_scholes_pricing_and_greeks(k_short, T, sigma, 'put')
                
                directive["Legs"] = [
                    {"Side": "Long", "Type": "Put", "Strike": k_long, "Price": round(long_metrics['Price'], 2)},
                    {"Side": "Short", "Type": "Put", "Strike": k_short, "Price": round(short_metrics['Price'], 2)}
                ]
                directive["Net_Premium"] = round((long_metrics['Price'] - short_metrics['Price']) * -1.0, 2)
            else:
                directive["Strategy"] = "Cash"
                directive["Action"] = "Wait"

        else:
            # NEUTRAL IVR REGIME
            if trend_bias == 'Bullish':
                if sell_premium_allowed:
                    directive["Strategy"] = "Covered Call"
                    directive["Action"] = "Sell to Open"
                    
                    k_short = self.find_strike_for_delta(SHORT_DELTA_TARGET, T, sigma, 'call')
                    short_metrics = self.black_scholes_pricing_and_greeks(k_short, T, sigma, 'call')
                    
                    directive["Legs"] = [
                        {"Side": "Short", "Type": "Call", "Strike": k_short, "Price": round(short_metrics['Price'], 2), "Delta": round(short_metrics['Delta'], 2)}
                    ]
                    directive["Net_Premium"] = round(short_metrics['Price'], 2)
                else:
                    directive["Strategy"] = "Cash"
                    directive["Action"] = "Wait"
            elif trend_bias == 'Bearish':
                directive["Strategy"] = "Put Debit Spread"
                directive["Action"] = "Buy to Open"
                
                k_long = self.find_strike_for_delta(-ATM_DELTA_TARGET, T, sigma, 'put')
                long_metrics = self.black_scholes_pricing_and_greeks(k_long, T, sigma, 'put')
                
                k_short = self.find_strike_for_delta(-SHORT_DELTA_TARGET, T, sigma, 'put')
                short_metrics = self.black_scholes_pricing_and_greeks(k_short, T, sigma, 'put')
                
                directive["Legs"] = [
                    {"Side": "Long", "Type": "Put", "Strike": k_long, "Price": round(long_metrics['Price'], 2)},
                    {"Side": "Short", "Type": "Put", "Strike": k_short, "Price": round(short_metrics['Price'], 2)}
                ]
                directive["Net_Premium"] = round((long_metrics['Price'] - short_metrics['Price']) * -1.0, 2)
            else:
                directive["Strategy"] = "Cash"
                directive["Action"] = "Wait"

        return directive


class TechnicalOptionsEngine:
    """
    Orchestrates calculation of advanced technical indicators, GJR-GARCH volatility modeling,
    Implied Volatility Rank (IVR), and option strategy recommendations.
    """

    @staticmethod
    def sanitize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        """
        Cleans and sanitizes raw OHLCV DataFrame inputs by removing NaN values
        and ensuring sorted chronological index.
        """
        if df is None or df.empty:
            return pd.DataFrame()
        
        # Sort index chronologically (ascending)
        df_sorted = df.sort_index()
        
        # Drop rows where any essential pricing column is NaN
        df_clean = df_sorted.dropna(subset=['Open', 'High', 'Low', 'Close', 'Volume'])
        return df_clean

    def calculate_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Calculates Aroon Oscillator, Coppock Curve, and Chandelier Exit.
        Returns the latest values as a dictionary.
        """
        df_clean = self.sanitize_ohlcv(df)
        if len(df_clean) < 22:
            logger.warning("Insufficient data points (< 22) for technical indicator calculations.")
            return {
                "Aroon_Oscillator": 0.0,
                "Coppock_Curve": 0.0,
                "Chandelier_Long": 0.0,
                "Chandelier_Short": 0.0
            }

        # 1. Aroon Oscillator using pandas-ta
        aroon_df = df_clean.ta.aroon(length=14)
        if aroon_df is not None and not aroon_df.empty:
            # Column name is typically AROONOSC_14
            osc_col = [col for col in aroon_df.columns if "AROONOSC" in col]
            aroon_osc = float(aroon_df[osc_col[0]].iloc[-1]) if osc_col else 0.0
        else:
            aroon_osc = 0.0

        # 2. Coppock Curve using pandas-ta with length=10, fast=11, slow=14
        coppock_series = df_clean.ta.coppock(length=10, fast=11, slow=14)
        coppock_val = float(coppock_series.iloc[-1]) if (coppock_series is not None and not coppock_series.empty) else 0.0

        # 3. Chandelier Exit (ta.chandelier_exit using a 22-day lookback and 3.0 ATR multiplier)
        chandelier_df = df_clean.ta.chandelier_exit(length=22, multiplier=3.0)
        if chandelier_df is not None and not chandelier_df.empty:
            long_col = [col for col in chandelier_df.columns if "LONG" in col]
            short_col = [col for col in chandelier_df.columns if "SHORT" in col]
            chandelier_long = float(chandelier_df[long_col[0]].iloc[-1]) if long_col else 0.0
            chandelier_short = float(chandelier_df[short_col[0]].iloc[-1]) if short_col else 0.0
        else:
            # Fallback manual calculation of Chandelier Exit
            atr_series = df_clean.ta.atr(length=22)
            if atr_series is not None and not atr_series.empty:
                atr_val = atr_series.iloc[-1]
                highest_high = df_clean['High'].rolling(window=22).max().iloc[-1]
                lowest_low = df_clean['Low'].rolling(window=22).min().iloc[-1]
                chandelier_long = highest_high - (3.0 * atr_val)
                chandelier_short = lowest_low + (3.0 * atr_val)
            else:
                chandelier_long = 0.0
                chandelier_short = 0.0

        return {
            "Aroon_Oscillator": aroon_osc,
            "Coppock_Curve": coppock_val,
            "Chandelier_Long": float(chandelier_long),
            "Chandelier_Short": float(chandelier_short)
        }

    def estimate_gjr_garch_volatility(self, df: pd.DataFrame) -> float:
        """
        Deploys a GJR-GARCH(1,1) model to extract day-ahead annualized volatility.
        Uses the arch library to deploy a GJR-GARCH(1,1) model via arch_model(returns, vol='GARCH', p=1, o=1, q=1).
        If optimization fails or arch library is missing, falls back to standard 20-day historical annualized volatility.
        """
        df_clean = self.sanitize_ohlcv(df)
        if len(df_clean) < 22:
            return 0.20  # Neutral 20% default fallback

        returns = df_clean['Close'].pct_change().dropna()
        if len(returns) < 10:
            return 0.20

        # Try GJR-GARCH fitting if arch library is available
        if ARCH_AVAILABLE:
            try:
                # Scale returns by 100 to prevent poor data scaling issues
                scaled_returns = returns * 100
                
                # GJR-GARCH(1,1): p=1, o=1, q=1. Use Student's t-distribution for fat tails
                model = arch_model(scaled_returns, vol='GARCH', p=1, o=1, q=1, dist='t')
                # arch ≥ 8.0 removed the `method` kwarg; default optimizer (SLSQP) works correctly
                res = model.fit(update_freq=0, disp='off')
                
                # Forecast the next day ahead variance
                forecast = res.forecast(horizon=1)
                next_day_variance = forecast.variance.iloc[-1].values[0]
                
                # Annualize the standard deviation (volatility) and scale back down by 100
                annualized_vol = (np.sqrt(next_day_variance) * np.sqrt(252)) / 100.0
                
                # Sanity bound the forecast to realistic levels (e.g. between 2% and 300%)
                return float(max(0.02, min(3.0, annualized_vol)))
                
            except Exception as e:
                logger.warning(f"GJR-GARCH failed to converge: {e}. Falling back to 20-day historical standard deviation.")
        else:
            logger.warning("arch library is not installed/available. Using 20-day historical standard deviation fallback.")
            
        # Fallback to standard 20-day historical annualized volatility
        daily_vol = returns.tail(20).std()
        annualized_vol = daily_vol * np.sqrt(252)
        return float(max(0.02, min(3.0, annualized_vol)))

    def calculate_realized_vol_rank(self, df: pd.DataFrame, current_vol: float) -> float:
        """
        Calculates the Realized Volatility Rank proxy by comparing current annualized
        volatility against the 52-week historical rolling annualized volatility range (252 trading days).
        """
        df_clean = self.sanitize_ohlcv(df)
        if len(df_clean) < 22:
            return 50.0

        returns = df_clean['Close'].pct_change().dropna()
        if len(returns) < 20:
            return 50.0

        # Calculate 20-day rolling historical annualized volatility series over the last 252 days
        rolling_vol = returns.rolling(window=20).std() * np.sqrt(252)
        rolling_vol = rolling_vol.dropna().tail(252)

        if rolling_vol.empty:
            return 50.0

        vol_min = rolling_vol.min()
        vol_max = rolling_vol.max()

        if vol_max == vol_min:
            return 50.0

        # Rank the current volatility within the range
        realized_vol_rank = ((current_vol - vol_min) / (vol_max - vol_min)) * 100.0
        return float(max(0.0, min(100.0, realized_vol_rank)))


    def generate_option_strategy_matrix(
        self, 
        true_ivr: float, 
        aroon_osc: float, 
        coppock_val: float, 
        stock_price: float = 100.0, 
        current_iv: float = 0.20,
        target_dte: int = 30,
        risk_free_rate: float = RISK_FREE_RATE,
        vrp: Optional[float] = None,
        macro_dto: Optional[Any] = None
    ) -> str:
        """
        Automated Option Strategy Matrix upgraded to Quantitative Option Pricing and Strike Recommendation.
        Returns detailed quantitative recommendations based on Black-Scholes pricing and Delta root-finding.
        """
        # Determine Trend
        if aroon_osc > 0 and coppock_val > 0:
            trend_bias = "Bullish"
        elif aroon_osc < 0 and coppock_val < 0:
            trend_bias = "Bearish"
        else:
            trend_bias = "Neutral"

        # Instantiate recommender
        recommender = OptionsPricingRecommender(stock_price=stock_price, risk_free_rate=risk_free_rate)
        
        # Get option directive dictionary
        directive = recommender.generate_strategy_pricing_matrix(
            true_ivr=true_ivr, current_iv=current_iv, trend_bias=trend_bias, target_dte=target_dte,
            vrp=vrp, macro_dto=macro_dto
        )
        
        strategy = directive.get("Strategy", "Cash")
        action = directive.get("Action", "Wait")
        net_prem = directive.get("Net_Premium", 0.0)
        theta = directive.get("Realizable_Daily_Theta", 0.0)
        legs = directive.get("Legs", [])
        
        if not legs or strategy == "Cash":
            return f"Cash (Wait)"
            
        legs_str = ", ".join([
            f"{leg['Side']} {leg['Type']} K={leg['Strike']:.2f} @ ${leg['Price']:.2f}"
            for leg in legs
        ])
        
        if theta != 0.0:
            return f"{action} {strategy}: {legs_str} (Net Premium: ${net_prem:.2f}, Realizable Daily Theta: ${theta:.4f})"
        else:
            return f"{action} {strategy}: {legs_str} (Net Premium: ${net_prem:.2f})"


# =============================================================================
# PREMIUM DIRECTIVE HELPER
# Public, GUI- and audit-friendly wrapper that fuses GJR-GARCH sigma, realized-
# vol IVR proxy, Aroon+Coppock trend bias, full ATM Black-Scholes Greeks, and
# the deterministic strategy matrix into a single dict suitable for hydrating a
# DataGrid row.  Centralised here (rather than duplicated in gui/panels.py) so
# the Gravity AI Review Suite can call the *exact same* code path and assert
# the matrix integrity invariants (strike grid, delta targets, regime gate).
# =============================================================================

# Conventional Black-Scholes deltas for each (strategy, side, type) tuple.
# Iron Condor short/long deltas (0.16 / 0.05) and credit-spread deltas
# (0.30 / 0.15) mirror the targets used inside
# ``OptionsPricingRecommender.generate_strategy_pricing_matrix``.
EXPECTED_DELTA_TARGETS: Dict[tuple, float] = {
    ("Put Credit Spread", "Short", "Put"): -0.30,
    ("Put Credit Spread", "Long", "Put"): -0.15,
    ("Call Credit Spread", "Short", "Call"): 0.30,
    ("Call Credit Spread", "Long", "Call"): 0.15,
    ("Iron Condor", "Short", "Put"): -0.16,
    ("Iron Condor", "Long", "Put"): -0.05,
    ("Iron Condor", "Short", "Call"): 0.16,
    ("Iron Condor", "Long", "Call"): 0.05,
    ("Covered Call", "Short", "Call"): 0.30,
    ("Call Debit Spread", "Long", "Call"): 0.50,
    ("Call Debit Spread", "Short", "Call"): 0.30,
    ("Put Debit Spread", "Long", "Put"): -0.50,
    ("Put Debit Spread", "Short", "Put"): -0.30,
}

# Standard equity option strike grid in USD.  Real exchange grids vary by
# underlying price (often $1 / $2.50 / $5 above $100), but $0.50 is the
# tightest grid commonly listed for liquid names; any rounded strike that
# satisfies the $0.50 grid is, by definition, also on every coarser grid.
STRIKE_GRID_USD: float = 0.50


def _on_strike_grid(strike: float, grid: float = STRIKE_GRID_USD) -> bool:
    """True iff ``strike`` lies on a multiple of ``grid`` (within FP epsilon)."""
    if not np.isfinite(strike) or grid <= 0:
        return False
    remainder = abs(strike / grid - round(strike / grid))
    return remainder < 1e-6


def validate_directive_integrity(
    directive: Dict[str, Any],
    *,
    delta_tolerance: float = 0.05,
    strike_grid: float = STRIKE_GRID_USD,
) -> Dict[str, Any]:
    """Audit a strategy directive against the matrix-integrity invariants.

    Returns a dict ``{"ok": bool, "issues": list[str], "checks": dict}`` so
    callers can both branch on the boolean and display a per-leg breakdown.
    Cash / Wait directives are treated as trivially valid.

    Invariants enforced:
      * Every leg strike lies on the ``strike_grid`` (default $0.50).
      * Where the leg carries a resolved ``Delta`` and the strategy/side/type
        combination has a conventional target in
        :data:`EXPECTED_DELTA_TARGETS`, ``|delta - target| <= delta_tolerance``.

    The delta check is skipped (not failed) when the leg dict omits ``Delta``
    — Iron Condor legs in the current engine intentionally omit it.  This
    means callers should treat the function as a strict *upper-bound* check:
    a PASS guarantees correctness for the present leg payload, but never
    fabricates a verdict against a missing field (CONSTRAINT #4).
    """
    issues: list[str] = []
    checks: list[Dict[str, Any]] = []
    strategy = directive.get("Strategy", "Cash")
    legs = directive.get("Legs", []) or []

    if strategy == "Cash" or not legs:
        return {"ok": True, "issues": [], "checks": []}

    for leg in legs:
        strike = float(leg.get("Strike", float("nan")))
        side = str(leg.get("Side", ""))
        typ = str(leg.get("Type", ""))
        on_grid = _on_strike_grid(strike, strike_grid)
        delta_ok: Optional[bool] = None
        target = EXPECTED_DELTA_TARGETS.get((strategy, side, typ))
        if "Delta" in leg and target is not None:
            delta = float(leg["Delta"])
            delta_ok = abs(delta - target) <= delta_tolerance
            if not delta_ok:
                issues.append(
                    f"{strategy} {side} {typ} K={strike:.2f}: delta {delta:+.3f} "
                    f"deviates from target {target:+.2f} by > {delta_tolerance:.2f}"
                )
        if not on_grid:
            issues.append(
                f"{strategy} {side} {typ} K={strike:.4f} is off the ${strike_grid:.2f} grid"
            )
        checks.append(
            {
                "Side": side,
                "Type": typ,
                "Strike": strike,
                "OnGrid": on_grid,
                "DeltaOK": delta_ok,
                "Delta": leg.get("Delta"),
                "Target": target,
            }
        )

    return {"ok": not issues, "issues": issues, "checks": checks}


def _determine_trend_bias(aroon_osc: float, coppock_val: float) -> str:
    """Deterministic trend bias from Aroon Oscillator + Coppock Curve sign."""
    if aroon_osc > 0 and coppock_val > 0:
        return "Bullish"
    if aroon_osc < 0 and coppock_val < 0:
        return "Bearish"
    return "Neutral"


def build_premium_directive(
    symbol: str,
    bars: pd.DataFrame,
    *,
    spot_price: float,
    is_stale: bool = False,
    target_dte: int = 30,
    macro_dto: Optional[Any] = None,
    vrp: Optional[float] = None,
    risk_free_rate: float = RISK_FREE_RATE,
) -> Dict[str, Any]:
    """Compute a fully-hydrated premium-selling row for one symbol.

    Wraps :class:`TechnicalOptionsEngine` + :class:`OptionsPricingRecommender`
    into a single dict containing both the diagnostic feature columns the
    Command Center renders (sigma, IVR proxy, trend bias, ATM Greeks) and the
    actionable strategy directive (legs, net premium, realizable daily theta).

    Parameters
    ----------
    symbol :
        Ticker label (used only for display / dead-letter logging).
    bars :
        OHLCV DataFrame with at least 22 rows; the same shape returned by
        :meth:`data.market_data.CompositeProvider.get_intraday_bars`.
    spot_price :
        Latest mid price for the underlying.
    is_stale :
        Surfaced verbatim into the row so the GUI can flag delayed quotes.
    target_dte, macro_dto, vrp, risk_free_rate :
        Forwarded to
        :meth:`OptionsPricingRecommender.generate_strategy_pricing_matrix`.

    Returns
    -------
    dict
        Always a *complete* row; numeric fields are ``float('nan')`` when the
        underlying primitive could not be computed (never fabricated as 0.0,
        CONSTRAINT #4).  The ``"integrity"`` sub-dict is the output of
        :func:`validate_directive_integrity` so callers can show pass/fail
        without re-walking the legs.
    """
    toe = TechnicalOptionsEngine()
    nan = float("nan")
    row: Dict[str, Any] = {
        "Symbol": symbol,
        "Price": float(spot_price) if np.isfinite(spot_price) else nan,
        "Stale": bool(is_stale),
        "Sigma_GARCH": nan,
        "IVR_Proxy": nan,
        "Aroon_Oscillator": nan,
        "Coppock_Curve": nan,
        "Trend_Bias": "Neutral",
        "Strategy": "Cash",
        "Action": "Wait",
        "Net_Premium": nan,
        "Realizable_Daily_Theta": nan,
        "ATM_Delta": nan,
        "ATM_Gamma": nan,
        "ATM_Vega": nan,
        "ATM_Theta_Daily": nan,
        "Short_Strike": nan,
        "Long_Strike": nan,
        "Short_Delta": nan,
        "Long_Delta": nan,
        "Legs": [],
        "Integrity_OK": True,
        "Integrity_Issues": [],
    }

    # 1) Volatility (GJR-GARCH) — falls back to 20-day realized inside the engine.
    try:
        sigma = float(toe.estimate_gjr_garch_volatility(bars))
    except Exception as exc:  # noqa: BLE001
        logger.warning("GJR-GARCH failed for %s: %s", symbol, exc)
        sigma = nan
    row["Sigma_GARCH"] = sigma

    # 2) Realized-Vol IVR proxy (true IVR requires an options chain).
    if np.isfinite(sigma):
        try:
            row["IVR_Proxy"] = float(toe.calculate_realized_vol_rank(bars, sigma))
        except Exception as exc:  # noqa: BLE001
            logger.warning("IVR proxy failed for %s: %s", symbol, exc)

    # 3) Trend bias (Aroon + Coppock).
    try:
        indicators = toe.calculate_indicators(bars)
        row["Aroon_Oscillator"] = float(indicators.get("Aroon_Oscillator", nan))
        row["Coppock_Curve"] = float(indicators.get("Coppock_Curve", nan))
        row["Trend_Bias"] = _determine_trend_bias(
            row["Aroon_Oscillator"], row["Coppock_Curve"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Trend indicators failed for %s: %s", symbol, exc)

    # If we have no price or no sigma we cannot price anything — return the
    # diagnostic columns and let the caller render a Cash/Wait row.
    if not np.isfinite(row["Price"]) or not np.isfinite(sigma):
        return row

    # 4) ATM Greeks (informational; independent of the directive).
    recommender = OptionsPricingRecommender(
        stock_price=row["Price"], risk_free_rate=risk_free_rate
    )
    T = max(int(target_dte), 1) / 365.0
    try:
        atm = recommender.black_scholes_pricing_and_greeks(
            K=row["Price"], T=T, sigma=sigma, option_type="call"
        )
        row["ATM_Delta"] = float(atm.get("Delta", nan))
        row["ATM_Gamma"] = float(atm.get("Gamma", nan))
        row["ATM_Vega"] = float(atm.get("Vega", nan))
        row["ATM_Theta_Daily"] = float(atm.get("Theta_Daily", nan))
    except Exception as exc:  # noqa: BLE001
        logger.warning("ATM Greeks failed for %s: %s", symbol, exc)

    # 5) Strategy directive (already VRP / regime-gated inside the engine).
    ivr_value = row["IVR_Proxy"] if np.isfinite(row["IVR_Proxy"]) else 50.0
    try:
        directive = recommender.generate_strategy_pricing_matrix(
            true_ivr=float(ivr_value),
            current_iv=float(sigma),
            trend_bias=row["Trend_Bias"],
            target_dte=int(target_dte),
            vrp=vrp,
            macro_dto=macro_dto,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Strategy directive failed for %s: %s", symbol, exc)
        return row

    row["Strategy"] = str(directive.get("Strategy", "Cash"))
    row["Action"] = str(directive.get("Action", "Wait"))
    row["Net_Premium"] = float(directive.get("Net_Premium", nan))
    row["Realizable_Daily_Theta"] = float(
        directive.get("Realizable_Daily_Theta", nan)
    )
    legs = directive.get("Legs", []) or []
    row["Legs"] = legs

    short_legs = [l for l in legs if l.get("Side") == "Short"]
    long_legs = [l for l in legs if l.get("Side") == "Long"]
    if short_legs:
        row["Short_Strike"] = float(short_legs[0].get("Strike", nan))
        row["Short_Delta"] = float(short_legs[0].get("Delta", nan))
    if long_legs:
        row["Long_Strike"] = float(long_legs[0].get("Strike", nan))
        row["Long_Delta"] = float(long_legs[0].get("Delta", nan))

    # 6) Matrix integrity (strike grid + delta-target tolerance).
    integrity = validate_directive_integrity(directive)
    row["Integrity_OK"] = bool(integrity["ok"])
    row["Integrity_Issues"] = list(integrity["issues"])
    return row

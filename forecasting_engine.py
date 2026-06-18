# =============================================================================
# MODULE: FORECASTING ENGINE
# File: forecasting_engine.py
# Description: Handles advanced quantitative modeling (ARIMA, Monte Carlo, HW).
#              Returns a FLAT dictionary matching config.py Schema keys.
# =============================================================================

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing
import logging
import warnings

# Suppress harmless warnings from statsmodels optimization
warnings.filterwarnings("ignore")

class ForecastingEngine:
    def __init__(self):
        # Configuration: Target Days by Sector
        self.sector_configs = {
            "Technology": {"days": 30, "model": "MC"},
            "Consumer Cyclical": {"days": 30, "model": "MC"},
            "Communication Services": {"days": 30, "model": "MC"},
            "Healthcare": {"days": 90, "model": "MC"},
            "Energy": {"days": 60, "model": "MC"},
            "Financial Services": {"days": 60, "model": "ARIMA"},
            "Industrials": {"days": 60, "model": "ARIMA"},
            "Real Estate": {"days": 90, "model": "HW"},  
            "Utilities": {"days": 90, "model": "ARIMA"},
            "Consumer Defensive": {"days": 90, "model": "ARIMA"},
            "Basic Materials": {"days": 60, "model": "ARIMA"}
        }

    # =========================================================================
    # CORE MODELS
    # =========================================================================
    
    def run_monte_carlo(self, start_price, mu, sigma, days_forward, simulations=1000):
        """
        Runs Geometric Brownian Motion simulations.
        """
        try:
            dt = 1  # 1 day time step
            shock = np.random.normal(0, 1, (simulations, days_forward))
            drift = (mu - 0.5 * sigma**2) * dt
            diffusion = sigma * np.sqrt(dt) * shock
            
            cumulative_drift = drift * days_forward
            cumulative_diffusion = np.sum(diffusion, axis=1)
            
            terminal_prices = start_price * np.exp(cumulative_drift + cumulative_diffusion)
            
            return (
                np.mean(terminal_prices), 
                np.percentile(terminal_prices, 5), 
                np.percentile(terminal_prices, 95)
            )
        except Exception as e:
            return start_price, start_price, start_price

    def _get_last_forecast_value(self, forecast):
        """Extracts the last value from a forecast result."""
        if hasattr(forecast, 'iloc'):
            return forecast.iloc[-1]
        return forecast[-1]

    def run_arima(self, history, days_forward, order=(5,1,0)):
        """Runs ARIMA model. Returns forecast price."""
        if len(history) < 30: return 0
        try:
            model = ARIMA(history, order=order)
            model_fit = model.fit()
            forecast = model_fit.forecast(steps=days_forward)
            return self._get_last_forecast_value(forecast)
        except:
            return 0

    def run_holt_winters(self, history, days_forward):
        """Runs Exponential Smoothing (Holt-Winters)."""
        if len(history) < 30: return 0
        try:
            model = ExponentialSmoothing(history, trend="add", seasonal=None)
            fit = model.fit()
            forecast = fit.forecast(days_forward)
            return self._get_last_forecast_value(forecast)
        except:
            return 0

    # =========================================================================
    # ORCHESTRATOR
    # =========================================================================
    
    def generate_forecast(self, row, current_price, history_series=None):
        """
        Generates forecasts and maps them to SCHEMA KEYS.
        CRITICAL: history_series must be passed for ARIMA to work.
        """
        results = {
            'Target_Days': 60,
            'ARIMA': 0.0,
            'MC_Target': 0.0,
            'MC_Lower': 0.0,
            'MC_Upper': 0.0,
            'Forecast_10': 0.0,
            'Forecast_30': 0.0,
            'Forecast_60': 0.0,
            'Forecast_90': 0.0
        }

        if not current_price or current_price == 0:
            return results

        try:
            sector = row.get('sector', 'Unknown')
            
            # Config lookup
            config = self.sector_configs.get(sector, {"days": 60, "model": "MC"})
            target_days = config['days']
            preferred_model = config['model']
            
            results['Target_Days'] = target_days

            # 2. Prepare Data
            # Note: We extract .values here to ensure statsmodels gets a clean array
            if history_series is not None and not history_series.empty:
                history = history_series
                log_returns = np.log(history / history.shift(1)).dropna()
                mu = log_returns.mean()
                sigma = log_returns.std()
                close_prices = history.values # Converts to Numpy Array
            else:
                # Fallback
                mu = 0.0002 
                sigma = 0.015 
                close_prices = []

            # 3. Primary Forecasts
            if len(close_prices) > 30:
                results['ARIMA'] = self.run_arima(close_prices, days_forward=target_days)

            mc_mean, mc_low, mc_high = self.run_monte_carlo(current_price, mu, sigma, target_days)
            results['MC_Target'] = mc_mean
            results['MC_Lower'] = mc_low
            results['MC_Upper'] = mc_high
            
            # 4. Multi-Horizon Forecasts
            horizons = [10, 30, 60, 90]
            
            for h in horizons:
                a_res = 0
                h_res = 0
                
                # Only run expensive models if we have history
                if len(close_prices) > 30:
                    a_res = self.run_arima(close_prices, days_forward=h)
                    h_res = self.run_holt_winters(close_prices, days_forward=h)
                
                m_res, _, _ = self.run_monte_carlo(current_price, mu, sigma, days_forward=h)
                
                blended = 0
                if preferred_model == "HW" and h_res > 0:
                    blended = h_res
                elif preferred_model == "ARIMA" and a_res > 0:
                    blended = a_res
                else:
                    if a_res > 0 and m_res > 0:
                        blended = (a_res * 0.4) + (m_res * 0.6)
                    elif a_res > 0:
                        blended = a_res
                    else:
                        blended = m_res
                
                results[f'Forecast_{h}'] = blended

        except Exception as e:
            logging.error(f"Forecasting Engine Error for {row.get('Symbol', 'Unknown')}: {e}")
            
        return results

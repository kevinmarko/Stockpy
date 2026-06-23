# =============================================================================
# MODULE: FORECASTING ENGINE
# File: forecasting_engine.py
# Description: Handles advanced quantitative modeling (ARIMA, Monte Carlo, 
#              Holt-Winters grid search, Prophet, and Hybrid CNN-LSTM).
#              Returns a FLAT dictionary matching config.py Schema keys.
# =============================================================================

import logging
import warnings
import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from sklearn.preprocessing import MinMaxScaler
from typing import Dict, Any, Optional, Tuple

# Suppress harmless warnings from statsmodels optimization
warnings.filterwarnings("ignore")

# Setup module logger
logger = logging.getLogger("ForecastingEngine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Import libraries with robust fallback flags
try:
    from prophet import Prophet  # type: ignore
    PROPHET_AVAILABLE = True
except ImportError:
    PROPHET_AVAILABLE = False
    logger.debug("prophet library not available. Prophet forecasting will fall back.")

try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import Conv1D, LSTM, Dense, MaxPooling1D
    TENSORFLOW_AVAILABLE = True
except ImportError:
    TENSORFLOW_AVAILABLE = False
    logger.debug("tensorflow library not available. Hybrid CNN-LSTM will fall back.")


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
    
    def run_monte_carlo(self, start_price: float, mu: float, sigma: float, days_forward: int, simulations: int = 1000):
        """
        Runs Geometric Brownian Motion simulations.

        F-05 FIX: Explicit daily-units contract enforced.
        IMPORTANT: mu and sigma MUST be expressed as DAILY values (i.e. daily log-return
        mean and std). If annualized values are passed, drift will be 252x too large.

        Formula: S_T = S_0 * exp((mu - 0.5*sigma^2)*T + sigma*sqrt(T)*Z), Z ~ N(0,1)

        The Ito drift correction (mu - 0.5*sigma^2) is mandatory to prevent upward bias
        in the expected terminal price (prevents naive flatline / drift collapse).
        """
        try:
            if days_forward <= 0 or simulations <= 0:
                return start_price, start_price, start_price

            # F-05 GUARD: if mu looks annualized (|mu| >> typical daily range),
            # normalize to daily to prevent silent 252x drift explosion.
            if abs(mu) > 0.05:
                logger.warning(
                    f"Monte Carlo: mu={mu:.4f} appears annualized. "
                    f"Normalizing to daily (dividing by 252)."
                )
                mu    = mu    / 252
                sigma = sigma / np.sqrt(252)

            # dt = 1 trading day (mu and sigma are daily)
            dt = 1

            # Ito structural drift: (mu - 0.5*sigma^2) per day — prevents naive upward bias
            daily_drift     = (mu - 0.5 * sigma ** 2) * dt            # scalar, per day
            shock           = np.random.normal(0, 1, (simulations, days_forward))
            daily_diffusion = sigma * np.sqrt(dt) * shock             # shape: (sims, days)

            # Terminal log-return = sum over T days
            total_log_return = (daily_drift * days_forward) + np.sum(daily_diffusion, axis=1)
            terminal_prices  = start_price * np.exp(total_log_return)

            return (
                float(np.mean(terminal_prices)),
                float(np.percentile(terminal_prices, 5)),
                float(np.percentile(terminal_prices, 95)),
            )
        except Exception as e:
            logger.error(f"Monte Carlo simulation failed: {e}")
            return start_price, start_price, start_price

    def _get_last_forecast_value(self, forecast):
        """Extracts the last value from a forecast result."""
        if hasattr(forecast, 'iloc'):
            return float(forecast.iloc[-1])
        if isinstance(forecast, (list, np.ndarray)) and len(forecast) > 0:
            return float(forecast[-1])
        return float(forecast)

    def run_arima(self, history: np.ndarray, days_forward: int, order=(1,1,1)) -> float:
        """Runs ARIMA model. Returns forecast price."""
        if len(history) < 30: 
            return 0.0
        try:
            model = ARIMA(history, order=order, trend='t')
            model_fit = model.fit()
            forecast = model_fit.forecast(steps=days_forward)
            return self._get_last_forecast_value(forecast)
        except Exception:
            return 0.0

    def run_holt_winters_grid_search(self, history: np.ndarray, days_forward: int) -> float:
        """
        Runs Exponential Smoothing (Holt-Winters) using a grid search over
        trend and damping combinations to minimize Mean Squared Error (MSE).
        """
        if len(history) < 30:
            return 0.0

        trend_opts = ["add"]
        damped_opts = [True, False]
        
        # Validation split: hold out the last 5 days to measure validation MSE
        split_idx = max(int(len(history) * 0.8), len(history) - 5)
        train = history[:split_idx]
        val = history[split_idx:]
        
        best_mse = float('inf')
        best_trend = "add"
        best_damped = False
        
        for trend in trend_opts:
            for damped in damped_opts:
                if damped and not trend:
                    continue  # Damping requires a trend
                try:
                    # Fit model on training partition
                    model = ExponentialSmoothing(train, trend=trend, damped_trend=damped, seasonal=None)
                    fit_model = model.fit()
                    preds = fit_model.forecast(len(val))
                    mse = np.mean((val - preds) ** 2)
                    if mse < best_mse:
                        best_mse = mse
                        best_trend = trend
                        best_damped = damped
                except Exception:
                    continue

        # Fit best configuration on all historical data
        try:
            model = ExponentialSmoothing(history, trend=best_trend, damped_trend=best_damped, seasonal=None)
            fit_model = model.fit()
            forecast = fit_model.forecast(days_forward)
            return self._get_last_forecast_value(forecast)
        except Exception as e:
            logger.debug(f"Holt-Winters grid search fit failed: {e}. Falling back to default fit.")
            try:
                model = ExponentialSmoothing(history, trend="add", seasonal=None)
                fit_model = model.fit()
                forecast = fit_model.forecast(days_forward)
                return self._get_last_forecast_value(forecast)
            except Exception:
                return float(history[-1])

    def run_prophet_forecast(self, history_series: pd.Series, days_forward: int) -> Tuple[float, float, float]:
        """
        Deploys a Facebook Prophet implementation to generate 30-day baseline price predictions,
        extracting the yhat, yhat_lower, and yhat_upper confidence intervals.
        """
        if not PROPHET_AVAILABLE:
            last_price = float(history_series.iloc[-1])
            return last_price, last_price, last_price

        try:
            # Prepare data conforming to Prophet format
            df_prophet = pd.DataFrame({
                'ds': history_series.index,
                'y': history_series.values
            })
            # Ensure ds is timezone-naive
            df_prophet['ds'] = pd.to_datetime(df_prophet['ds']).dt.tz_localize(None)

            # Silence Prophet logger
            logging.getLogger('prophet').setLevel(logging.ERROR)

            model = Prophet(daily_seasonality=False, weekly_seasonality=True, yearly_seasonality=False)
            model.fit(df_prophet)

            future = model.make_future_dataframe(periods=days_forward)
            forecast = model.predict(future)
            
            latest = forecast.iloc[-1]
            return float(latest['yhat']), float(latest['yhat_lower']), float(latest['yhat_upper'])
        except Exception as e:
            logger.warning(f"Prophet baseline forecast failed: {e}. Returning history tail fallback.")
            last_price = float(history_series.iloc[-1])
            return last_price, last_price, last_price

    # =========================================================================
    # HYBRID CNN-LSTM MODEL PIPELINE
    # =========================================================================
    
    @staticmethod
    def slice_sequences(X_data: np.ndarray, y_data: np.ndarray, lookback: int = 60) -> Tuple[np.ndarray, np.ndarray]:
        """
        Dedicated helper function to slice the time-series data into 60-day rolling
        lookback sequences (t-60 to t-1) so the data is reshaped into 3D tensors:
        (samples, time_steps, features)
        """
        X_seq, y_seq = [], []
        for i in range(lookback, len(X_data)):
            X_seq.append(X_data[i-lookback:i])
            y_seq.append(y_data[i])
        return np.array(X_seq), np.array(y_seq)

    def run_cnn_lstm_forecast(self, history_df: pd.DataFrame, days_forward: int) -> float:
        """
        Fits a Hybrid CNN-LSTM neural network architecture on historical technical factors
        and forecasts the price at days_forward.
        Uses MinMaxScaler for inputs/targets, 60-day sequence windowing, and inverse-transforms predictions.
        """
        if not TENSORFLOW_AVAILABLE:
            return 0.0

        # Require a minimum history to construct sequences
        if history_df is None or len(history_df) < 70:
            return 0.0

        try:
            # 1. Feature Engineering
            from technical_options_engine import TechnicalOptionsEngine
            tech_engine = TechnicalOptionsEngine()
            
            # Sanitize input using the technical options engine method
            df_features = tech_engine.sanitize_ohlcv(history_df).copy()
            if len(df_features) < 70:
                return 0.0
                
            # Aroon Oscillator
            aroon_df = df_features.ta.aroon(length=14)
            if aroon_df is not None and not aroon_df.empty:
                osc_col = [col for col in aroon_df.columns if "AROONOSC" in col]
                df_features['Aroon_Oscillator'] = aroon_df[osc_col[0]] if osc_col else 0.0
            else:
                df_features['Aroon_Oscillator'] = 0.0
                
            # Coppock Curve
            coppock_series = df_features.ta.coppock()
            df_features['Coppock_Curve'] = coppock_series if coppock_series is not None else 0.0
            
            # Chandelier Exit (22-day lookback, 3.0 ATR multiplier)
            atr_series = df_features.ta.atr(length=22)
            if atr_series is not None and not atr_series.empty:
                highest_high = df_features['High'].rolling(window=22).max()
                lowest_low = df_features['Low'].rolling(window=22).min()
                df_features['Chandelier_Long'] = highest_high - (3.0 * atr_series)
                df_features['Chandelier_Short'] = lowest_low + (3.0 * atr_series)
            else:
                df_features['Chandelier_Long'] = 0.0
                df_features['Chandelier_Short'] = 0.0
                
            # 20-day historical annualized volatility
            returns = df_features['Close'].pct_change()
            df_features['Volatility_20_Annual'] = returns.rolling(20).std() * np.sqrt(252)
            
            # Fill missing lookback values to prevent sequence shortening
            df_features['Aroon_Oscillator'] = df_features['Aroon_Oscillator'].fillna(0.0)
            df_features['Coppock_Curve'] = df_features['Coppock_Curve'].fillna(0.0)
            df_features['Chandelier_Long'] = df_features['Chandelier_Long'].bfill().fillna(0.0)
            df_features['Chandelier_Short'] = df_features['Chandelier_Short'].bfill().fillna(0.0)
            df_features['Volatility_20_Annual'] = df_features['Volatility_20_Annual'].fillna(0.0)
            
            feature_cols = [
                'Close', 'Open', 'High', 'Low', 'Volume', 
                'Aroon_Oscillator', 'Coppock_Curve', 
                'Chandelier_Long', 'Chandelier_Short', 'Volatility_20_Annual'
            ]
            
            # Sanitize inputs for NaNs
            df_features = df_features.dropna(subset=feature_cols)
            
            if len(df_features) < 65:
                return 0.0

            
            # 2. Scaling
            scaler_X = MinMaxScaler(feature_range=(0, 1))
            scaler_y = MinMaxScaler(feature_range=(0, 1))
            
            scaled_X = scaler_X.fit_transform(df_features[feature_cols])
            scaled_y = scaler_y.fit_transform(df_features[['Close']])
            
            # 3. Create Sequences
            lookback = 60
            X_seq, y_seq = self.slice_sequences(scaled_X, scaled_y, lookback=lookback)
            
            if len(X_seq) == 0:
                return 0.0

            # Shape of X_seq: (samples, time_steps, features)
            num_samples, time_steps, num_features = X_seq.shape
            
            # 4. Build Model
            model = Sequential([
                Conv1D(filters=32, kernel_size=3, activation='relu', input_shape=(time_steps, num_features)),
                MaxPooling1D(pool_size=2),
                LSTM(units=30, activation='tanh', return_sequences=False),
                Dense(units=1)
            ])
            model.compile(optimizer='adam', loss='mse')
            
            # Fit model with minimal epochs for rapid execution
            model.fit(X_seq, y_seq, epochs=2, batch_size=16, verbose=0)
            
            # 5. Iterative / Recursive Forecast Loop
            # Start with the last lookback sequence of scaled data
            current_sequence = scaled_X[-lookback:].copy()
            
            predicted_scale_price = 0.0
            for step in range(days_forward):
                # Shape input to 3D tensor: (1, lookback, num_features)
                input_tensor = np.expand_dims(current_sequence, axis=0)
                pred_scaled = model.predict(input_tensor, verbose=0)[0][0]
                
                # Update current sequence: shift left and append predicted Close
                # (We hold other features constant or rolled forward for proxy future steps)
                next_row = current_sequence[-1].copy()
                next_row[0] = pred_scaled  # Close price is index 0
                
                current_sequence = np.vstack([current_sequence[1:], next_row])
                
                if step == days_forward - 1:
                    predicted_scale_price = pred_scaled

            # 6. Inverse Transform output back to standard price values
            prediction_array = np.array([[predicted_scale_price]])
            inverse_pred = scaler_y.inverse_transform(prediction_array)[0][0]
            
            return float(inverse_pred)

        except Exception as e:
            logger.warning(f"Hybrid CNN-LSTM forecast execution failed: {e}.")
            return 0.0

    # =========================================================================
    # ORCHESTRATOR
    # =========================================================================
    
    def generate_forecast(self, row: pd.Series, current_price: float, history_series: Optional[pd.Series] = None, history_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """
        Generates forecasts and maps them to SCHEMA KEYS:
        Forecast_10, Forecast_30, Forecast_60, Forecast_90.
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
            config_data = self.sector_configs.get(sector, {"days": 60, "model": "MC"})
            target_days = config_data['days']
            preferred_model = config_data['model']
            
            results['Target_Days'] = target_days

            # Construct history DataFrame if not passed but series is available
            if history_df is None and history_series is not None and not history_series.empty:
                history_df = pd.DataFrame({
                    'Open': history_series,
                    'High': history_series,
                    'Low': history_series,
                    'Close': history_series,
                    'Volume': [10000.0] * len(history_series)
                }, index=history_series.index)

            # Extract Close price series array
            if history_series is not None and not history_series.empty:
                history = history_series
                log_returns = np.log(history / history.shift(1)).dropna()
                mu = float(log_returns.mean())
                sigma = float(log_returns.std())
                close_prices = history.values
            else:
                mu = 0.0002 
                sigma = 0.015 
                close_prices = np.array([])

            # 1. Primary Forecasts
            if len(close_prices) > 30:
                results['ARIMA'] = self.run_arima(close_prices, days_forward=target_days)

            mc_mean, mc_low, mc_high = self.run_monte_carlo(current_price, mu, sigma, target_days)
            results['MC_Target'] = mc_mean
            results['MC_Lower'] = mc_low
            results['MC_Upper'] = mc_high
            
            # 2. Multi-Horizon Forecasts
            horizons = [10, 30, 60, 90]
            
            for h in horizons:
                a_res = 0.0
                h_res = 0.0
                lstm_res = 0.0
                
                # Run statistical time-series models
                if len(close_prices) > 30:
                    a_res = self.run_arima(close_prices, days_forward=h)
                    h_res = self.run_holt_winters_grid_search(close_prices, days_forward=h)
                
                m_res, _, _ = self.run_monte_carlo(current_price, mu, sigma, days_forward=h)

                # Run Hybrid CNN-LSTM if tensorflow is active and history is sufficient
                if TENSORFLOW_AVAILABLE and history_df is not None and len(history_df) >= 70:
                    lstm_res = self.run_cnn_lstm_forecast(history_df, days_forward=h)
                
                # Blend forecasts based on sector configurations
                blended = 0.0
                if preferred_model == "HW" and h_res > 0:
                    blended = h_res
                elif preferred_model == "ARIMA" and a_res > 0:
                    blended = a_res
                else:
                    # General blending
                    if lstm_res > 0.0:
                        # Incorporate deep learning forecast with statistical models
                        blended = (lstm_res * 0.4) + (a_res * 0.2) + (m_res * 0.4) if a_res > 0 else (lstm_res * 0.5) + (m_res * 0.5)
                    elif a_res > 0 and m_res > 0:
                        blended = (a_res * 0.4) + (m_res * 0.6)
                    elif a_res > 0:
                        blended = a_res
                    else:
                        blended = m_res
                
                results[f'Forecast_{h}'] = blended

            # Extract Facebook Prophet 30-day baseline forecasts if active
            if PROPHET_AVAILABLE and history_series is not None and len(history_series) > 30:
                p_yhat, p_lower, p_upper = self.run_prophet_forecast(history_series, days_forward=30)
                # Override baseline target Forecast_30 with Prophet forecasts if preferred
                results['Forecast_30_Prophet'] = p_yhat
                results['Forecast_30_Prophet_Lower'] = p_lower
                results['Forecast_30_Prophet_Upper'] = p_upper

        except Exception as e:
            logger.error(f"Forecasting Engine Error for {row.get('Symbol', 'Unknown')}: {e}")
            
        return results

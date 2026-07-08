# =============================================================================
# MODULE: FORECASTING ENGINE
# File: forecasting_engine.py
# Description: Handles advanced quantitative modeling (ARIMA, Monte Carlo, 
#              Holt-Winters grid search, Prophet, and Hybrid CNN-LSTM).
#              Returns a FLAT dictionary matching config.py Schema keys.
# =============================================================================

import logging
import warnings
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from sklearn.preprocessing import MinMaxScaler
from typing import Dict, Any, Optional, Tuple, Union

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
    """Quantitative forecasting engine wrapping ARIMA, Monte Carlo,
    Holt-Winters, and CNN-LSTM models.

    Tier 2.2 addition: an optional ``ForecastTracker`` instance wires in
    skill-based ensemble blending.  When provided, the engine:
    1. Updates actuals for past forecasts (``tracker.update_actuals``).
    2. Fetches normalized inverse-RMSE weights (``tracker.get_skill_weights``).
    3. Blends model outputs using those weights; falls back to the original
       sector-preference static blending when skill data is absent (cold start).
    4. Records the new forecast prices for future validation
       (``tracker.record_forecasts``).

    The tracker is optional: ``ForecastingEngine()`` (no args) reproduces the
    pre-Tier-2.2 behavior exactly — no DB writes, static blending unchanged.
    """

    def __init__(self, tracker=None):
        """
        Parameters
        ----------
        tracker : ForecastTracker or None
            Optional skill tracker.  When ``None`` (default), skill-weighted
            blending is disabled and the original static logic is used.
        """
        from forecasting.forecast_tracker import ForecastTracker  # local import avoids circularity
        self._tracker: Optional[ForecastTracker] = tracker if isinstance(tracker, ForecastTracker) else None

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

    # Canonical feature ordering for the CNN-LSTM. 'Close' MUST stay at index 0
    # (it is the prediction target column the y-scaler is fit on).
    LSTM_FEATURE_COLS = [
        'Close', 'Open', 'High', 'Low', 'Volume',
        'Aroon_Oscillator', 'Coppock_Curve',
        'Chandelier_Long', 'Chandelier_Short', 'Volatility_20_Annual'
    ]
    LSTM_LOOKBACK = 60

    def build_lstm_features(self, history_df: pd.DataFrame) -> pd.DataFrame:
        """Engineer the CNN-LSTM feature frame from raw OHLCV.

        Every feature is causal (rolling / backward-looking via pandas_ta), so a
        row at time t depends only on data with timestamp <= t. This is verified
        by tests/test_indicators_lookahead.py via the perturbation detector.
        Returns a frame containing at least ``LSTM_FEATURE_COLS`` with NaN rows
        (warm-up window) dropped.
        """
        from technical_options_engine import TechnicalOptionsEngine
        tech_engine = TechnicalOptionsEngine()
        df_features = tech_engine.sanitize_ohlcv(history_df).copy()

        # Aroon Oscillator (causal)
        aroon_df = df_features.ta.aroon(length=14)
        if aroon_df is not None and not aroon_df.empty:
            osc_col = [col for col in aroon_df.columns if "AROONOSC" in col]
            df_features['Aroon_Oscillator'] = aroon_df[osc_col[0]] if osc_col else 0.0
        else:
            df_features['Aroon_Oscillator'] = 0.0

        # Coppock Curve (causal)
        coppock_series = df_features.ta.coppock()
        df_features['Coppock_Curve'] = coppock_series if coppock_series is not None else 0.0

        # Chandelier Exit (22-day rolling extreme, 3.0 ATR multiplier — causal)
        atr_series = df_features.ta.atr(length=22)
        if atr_series is not None and not atr_series.empty:
            highest_high = df_features['High'].rolling(window=22).max()
            lowest_low = df_features['Low'].rolling(window=22).min()
            df_features['Chandelier_Long'] = highest_high - (3.0 * atr_series)
            df_features['Chandelier_Short'] = lowest_low + (3.0 * atr_series)
        else:
            df_features['Chandelier_Long'] = 0.0
            df_features['Chandelier_Short'] = 0.0

        # 20-day historical annualized volatility (causal rolling std)
        returns = df_features['Close'].pct_change()
        df_features['Volatility_20_Annual'] = returns.rolling(20).std() * np.sqrt(252)

        # Fill indicator warm-up gaps WITHOUT using future data:
        # forward-fill / zero only — never bfill price-derived bands (bfill leaks
        # a future value backward into earlier rows).
        df_features['Aroon_Oscillator'] = df_features['Aroon_Oscillator'].fillna(0.0)
        df_features['Coppock_Curve'] = df_features['Coppock_Curve'].fillna(0.0)
        df_features['Chandelier_Long'] = df_features['Chandelier_Long'].ffill()
        df_features['Chandelier_Short'] = df_features['Chandelier_Short'].ffill()
        df_features['Volatility_20_Annual'] = df_features['Volatility_20_Annual'].fillna(0.0)

        return df_features.dropna(subset=self.LSTM_FEATURE_COLS)

    @staticmethod
    def fit_scalers_on_train(
        df_features: pd.DataFrame,
        feature_cols: list,
        n_reserve: int,
    ) -> Tuple[MinMaxScaler, MinMaxScaler, pd.DataFrame]:
        """Fit MinMax scalers on the TRAINING span only (everything except the
        last ``n_reserve`` rows = lookback + max_horizon).

        F-06 FIX (lookahead/leakage): the previous implementation called
        ``fit_transform`` on the entire history, so the scaler's min/max — and
        therefore every scaled input the model saw — were contaminated by the
        very rows we then forecast. Fitting on train only makes the transform a
        function of past data exclusively. The reserved tail is transformed with
        these train-derived parameters (``.transform``, never ``.fit``).

        Returns (scaler_X, scaler_y, train_df).
        """
        if n_reserve <= 0:
            raise ValueError(f"n_reserve must be positive, got {n_reserve}")
        if n_reserve >= len(df_features):
            raise ValueError(
                f"Not enough history ({len(df_features)} rows) to reserve "
                f"{n_reserve} inference rows and still fit a scaler on train."
            )
        train_df = df_features.iloc[:-n_reserve]
        scaler_X = MinMaxScaler(feature_range=(0, 1)).fit(train_df[feature_cols].values)
        scaler_y = MinMaxScaler(feature_range=(0, 1)).fit(train_df[['Close']].values)
        return scaler_X, scaler_y, train_df

    @staticmethod
    def make_direct_multistep_windows(
        scaled_X: np.ndarray,
        scaled_close: np.ndarray,
        lookback: int,
        horizons: list,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Build supervised windows for DIRECT multi-step forecasting.

        For each window ending at index ``end-1`` (rows ``end-lookback .. end-1``)
        the target is the vector ``[close[end-1+h] for h in horizons]`` — i.e. we
        predict every horizon directly from one model output head. This replaces
        the old recursive loop that froze 9/10 features at their last value while
        only feeding Close back in (a self-inconsistent trajectory); direct
        multi-step needs no feature recursion at all.

        Returns X of shape (samples, lookback, n_features) and Y of shape
        (samples, len(horizons)).
        """
        max_h = max(horizons)
        X_seq, Y_seq = [], []
        for end in range(lookback, len(scaled_X) - max_h + 1):
            last = end - 1
            X_seq.append(scaled_X[end - lookback:end])
            Y_seq.append([scaled_close[last + h, 0] for h in horizons])
        if not X_seq:
            return np.empty((0, lookback, scaled_X.shape[1])), np.empty((0, len(horizons)))
        return np.array(X_seq), np.array(Y_seq)

    def run_cnn_lstm_forecast(
        self,
        history_df: pd.DataFrame,
        horizons: Tuple[int, ...] = (10, 30, 60, 90),
        days_forward: Optional[int] = None,
    ) -> Union[float, Dict[int, float]]:
        """Hybrid CNN-LSTM multi-horizon price forecaster.

        Architecture decisions:
        * Trained ONCE per ticker (not once per horizon) for >= 20 epochs with an
          EarlyStopping(patience=5) callback on a validation split.
        * DIRECT multi-step: a single Dense head of width ``len(horizons)`` emits
          all horizon prices at once. Chosen over recursive single-step rollout
          because recursion required freezing non-Close features at stale values,
          producing a Close path inconsistent with its own indicators.
        * Scalers are fit on the training span only (see ``fit_scalers_on_train``)
          to eliminate train/inference leakage.

        Returns a dict ``{horizon: predicted_price}``, or a float if days_forward
        is specified. When TensorFlow is absent the engine degrades gracefully
        to zeros (never fabricated values).

        Future direction (Stage 4): move to a single cross-ticker model.
        Per-ticker retraining is acceptable for ~4 tickers but will not scale to
        a large universe — tracked as a deliberate design decision, not a defect.
        """
        if days_forward is not None:
            horizons = (int(days_forward),)
            zero_result: Union[float, Dict[int, float]] = 0.0
        else:
            horizons = tuple(int(h) for h in horizons)
            zero_result = {h: 0.0 for h in horizons}

        if not TENSORFLOW_AVAILABLE:
            return zero_result
        if history_df is None or len(history_df) < 70:
            return zero_result

        try:
            from tensorflow.keras.callbacks import EarlyStopping

            df_features = self.build_lstm_features(history_df)
            feature_cols = self.LSTM_FEATURE_COLS
            lookback = self.LSTM_LOOKBACK
            max_h = max(horizons)
            n_reserve = lookback + max_h

            # Need the reserved inference tail PLUS enough train rows to build
            # at least a handful of supervised windows.
            if len(df_features) < n_reserve + lookback + 10:
                logger.debug(
                    "CNN-LSTM: insufficient history (%d rows) for lookback=%d, "
                    "max_horizon=%d. Skipping.", len(df_features), lookback, max_h
                )
                return zero_result

            # 1. Train-only scaler fit (no leakage), then transform everything.
            scaler_X, scaler_y, _train_df = self.fit_scalers_on_train(
                df_features, feature_cols, n_reserve
            )
            scaled_X_all = scaler_X.transform(df_features[feature_cols].values)
            scaled_close_all = scaler_y.transform(df_features[['Close']].values)

            # Split into train sets for supervised sequence building
            scaled_X_train = scaled_X_all[:-n_reserve]
            scaled_close_train = scaled_close_all[:-n_reserve]

            # 2. Direct multi-step supervised windows built strictly from train data.
            X_seq, Y_seq = self.make_direct_multistep_windows(
                scaled_X_train, scaled_close_train, lookback, list(horizons)
            )
            if len(X_seq) == 0:
                return zero_result

            _, time_steps, num_features = X_seq.shape

            # 3. Build & train ONCE with early stopping on a validation split.
            model = Sequential([
                Conv1D(filters=32, kernel_size=3, activation='relu',
                       input_shape=(time_steps, num_features)),
                MaxPooling1D(pool_size=2),
                LSTM(units=30, activation='tanh', return_sequences=False),
                Dense(units=len(horizons)),
            ])
            model.compile(optimizer='adam', loss='mse')
            early_stop = EarlyStopping(
                monitor='val_loss', patience=5, restore_best_weights=True
            )
            model.fit(
                X_seq, Y_seq,
                epochs=50, batch_size=16, verbose=0,
                validation_split=0.2, callbacks=[early_stop],
            )

            # 4. Forecast from the most recent lookback window (all real data).
            last_window = scaled_X_all[-lookback:][np.newaxis, ...]
            pred_scaled = model.predict(last_window, verbose=0)[0]  # (n_horizons,)

            out: Dict[int, float] = {}
            for i, h in enumerate(horizons):
                inv = scaler_y.inverse_transform([[float(pred_scaled[i])]])[0][0]
                out[h] = float(inv)

            if days_forward is not None:
                return out.get(int(days_forward), 0.0)
            return out

        except Exception as e:
            logger.warning(f"Hybrid CNN-LSTM forecast execution failed: {e}.")
            return zero_result

    # =========================================================================
    # SKILL-WEIGHTED BLENDING HELPER (Tier 2.2)
    # =========================================================================

    @staticmethod
    def _blend_with_skill(
        model_forecasts: Dict[str, float],
        skill_weights: Dict[str, float],
        preferred_model: str,
        current_price: float,
    ) -> float:
        """Blend model forecast prices using normalized inverse-RMSE skill weights.

        When ``skill_weights`` is non-empty and covers at least one model that
        produced output, the function computes a weighted average restricted to
        models in both ``model_forecasts`` and ``skill_weights``.

        Falls back to the original static sector-preference blending when:
        * ``skill_weights`` is empty (cold start / tracker not wired).
        * No model in ``skill_weights`` produced a valid forecast price.

        Parameters
        ----------
        model_forecasts : dict[str, float]
            Model name → forecast price (positive prices only; zeros excluded).
        skill_weights : dict[str, float]
            Normalized inverse-RMSE weights from ``ForecastTracker.get_skill_weights()``.
            Empty dict → cold-start equal weighting → static fallback.
        preferred_model : str
            Sector-preferred model key (``"MC"``, ``"ARIMA"``, or ``"HW"``).
        current_price : float
            Current price — returned as last-resort fallback when all models
            fail to produce output, to avoid returning 0.0 (CONSTRAINT #4).

        Returns
        -------
        float
            Blended forecast price.
        """
        if not model_forecasts:
            return current_price  # no models produced output; never return 0.0

        # Skill-weighted blend: restrict to models in both dicts
        if skill_weights:
            active: Dict[str, float] = {
                name: weight
                for name, weight in skill_weights.items()
                if name in model_forecasts
            }
            if active:
                total = sum(active.values())
                if total > 0:
                    return sum(model_forecasts[name] * (w / total) for name, w in active.items())
                # Degenerate: all weights zero → fall through to static logic

        # Static sector-preference fallback (original logic)
        from settings import settings as _settings

        hw_price = model_forecasts.get("holt_winters", 0.0)
        arima_price = model_forecasts.get("arima", 0.0)
        mc_price = model_forecasts.get("monte_carlo", 0.0)
        lstm_price = model_forecasts.get("cnn_lstm", 0.0)

        # Compute the original static blend result into `base` (unchanged weights).
        if preferred_model == "HW" and hw_price > 0:
            base = hw_price
        elif preferred_model == "ARIMA" and arima_price > 0:
            base = arima_price
        # General static blend (mirrors original hardcoded weights)
        elif lstm_price > 0.0:
            if arima_price > 0:
                base = lstm_price * 0.4 + arima_price * 0.2 + mc_price * 0.4
            else:
                base = lstm_price * 0.5 + mc_price * 0.5
        elif arima_price > 0 and mc_price > 0:
            base = arima_price * 0.4 + mc_price * 0.6
        else:
            base = arima_price if arima_price > 0 else (mc_price if mc_price > 0 else current_price)

        # Prophet overlay: fold the Prophet 30-day forecast into the static blend.
        # When prophet is absent (0/missing), `base` is byte-identical to the
        # original static return value.
        prophet_price = model_forecasts.get("prophet", 0.0)
        if prophet_price > 0:
            w = float(getattr(_settings, "FORECAST_PROPHET_WEIGHT", 0.25))
            w = min(max(w, 0.0), 1.0)
            base = base * (1.0 - w) + prophet_price * w
        return base

    def _estimate_daily_sigma(self, history_df, fallback_daily_sigma: float) -> float:
        """Return a DAILY volatility for Monte Carlo, sourced from the GJR-GARCH(1,1)
        estimator (forward-looking, fat-tailed) when available, else the caller's
        historical daily stdev.

        CRITICAL UNIT CONVERSION: estimate_gjr_garch_volatility returns ANNUALIZED
        vol; Monte Carlo needs DAILY. We divide by sqrt(252). See run_monte_carlo's
        guard (keys on mu only) -- it will NOT auto-correct an annualized sigma.

        Degrades to fallback_daily_sigma (never raises) when the GARCH flag is off,
        history_df is None/insufficient, or the estimator fails.
        """
        from settings import settings as _settings
        if not _settings.FORECAST_USE_GARCH_SIGMA:
            return fallback_daily_sigma
        if history_df is None or len(history_df) < 22:
            return fallback_daily_sigma
        try:
            from technical_options_engine import TechnicalOptionsEngine
            annual_sigma = float(TechnicalOptionsEngine().estimate_gjr_garch_volatility(history_df))
            daily = annual_sigma / np.sqrt(252.0)
            if not np.isfinite(daily) or daily <= 0:
                return fallback_daily_sigma
            return max(daily, 1e-6)
        except Exception as _exc:
            logger.debug("GJR-GARCH daily sigma estimation failed; using historical stdev: %s", _exc)
            return fallback_daily_sigma

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

            # GJR-GARCH(1,1) daily sigma for Monte Carlo (forward-looking, fat-tailed);
            # falls back to the historical log-return stdev above. Defined once here so
            # both run_monte_carlo call sites below use it on every code path.
            mc_sigma = self._estimate_daily_sigma(history_df, sigma)

            # 1. Primary Forecasts
            if len(close_prices) > 30:
                results['ARIMA'] = self.run_arima(close_prices, days_forward=target_days)

            mc_mean, mc_low, mc_high = self.run_monte_carlo(current_price, mu, mc_sigma, target_days)
            results['MC_Target'] = mc_mean
            results['MC_Lower'] = mc_low
            results['MC_Upper'] = mc_high
            
            # 2. Multi-Horizon Forecasts
            horizons = [10, 30, 60, 90]

            # Symbol and timestamp for skill tracker integration (Tier 2.2)
            symbol = str(row.get('Symbol', row.get('Ticker', 'UNKNOWN'))).upper()
            now_utc = datetime.now(timezone.utc)

            # Train the CNN-LSTM ONCE (direct multi-step) and reuse its per-horizon
            # outputs below, instead of retraining a fresh model per horizon.
            lstm_multi: Dict[int, float] = {h: 0.0 for h in horizons}
            if TENSORFLOW_AVAILABLE and history_df is not None and len(history_df) >= 70:
                lstm_multi = self.run_cnn_lstm_forecast(history_df, horizons=tuple(horizons))

            # Run Facebook Prophet ONCE (30-day only; it is expensive) and stash its
            # forecast so it can both feed the h=30 blend below AND populate the
            # Forecast_30_Prophet result columns after the loop without a second run.
            prophet_yhat_30 = 0.0
            prophet_30_lower = 0.0
            prophet_30_upper = 0.0
            if PROPHET_AVAILABLE and history_series is not None and len(history_series) > 30:
                prophet_yhat_30, prophet_30_lower, prophet_30_upper = self.run_prophet_forecast(
                    history_series, days_forward=30
                )

            # Step 2a: update actuals for all horizons BEFORE generating new forecasts.
            # This ensures the skill weights computed below reflect the latest error data.
            if self._tracker is not None:
                for h in horizons:
                    try:
                        self._tracker.update_actuals(symbol, h, current_price, now_utc)
                    except Exception as _exc:
                        logger.debug("ForecastTracker.update_actuals skipped for %s h=%d: %s", symbol, h, _exc)

            for h in horizons:
                a_res = 0.0
                h_res = 0.0
                lstm_res = lstm_multi.get(h, 0.0)

                # Run statistical time-series models
                if len(close_prices) > 30:
                    a_res = self.run_arima(close_prices, days_forward=h)
                    h_res = self.run_holt_winters_grid_search(close_prices, days_forward=h)

                m_res, _, _ = self.run_monte_carlo(current_price, mu, mc_sigma, days_forward=h)

                # Collect per-model prices for skill tracking and skill-weighted blend.
                # Only include models that produced a positive price (CONSTRAINT #4).
                model_forecasts: Dict[str, float] = {}
                if a_res > 0:
                    model_forecasts["arima"] = a_res
                if m_res > 0:
                    model_forecasts["monte_carlo"] = m_res
                if h_res > 0:
                    model_forecasts["holt_winters"] = h_res
                if lstm_res > 0:
                    model_forecasts["cnn_lstm"] = lstm_res
                # Prophet only participates in the 30-day horizon (it is computed
                # once for 30 days only above).
                if h == 30 and prophet_yhat_30 > 0:
                    model_forecasts["prophet"] = prophet_yhat_30

                # Step 2b: retrieve skill weights for this horizon (empty dict = cold start).
                skill_weights: Dict[str, float] = {}
                if self._tracker is not None:
                    try:
                        from settings import settings as _settings
                        skill_weights = self._tracker.get_skill_weights(
                            symbol, h,
                            window_days=_settings.FORECAST_SKILL_WINDOW_DAYS,
                            min_obs=_settings.FORECAST_SKILL_MIN_OBS,
                        )
                    except Exception as _exc:
                        logger.debug("ForecastTracker.get_skill_weights skipped for %s h=%d: %s", symbol, h, _exc)

                # Blend using skill weights (falls back to static blend when skill_weights={})
                blended = self._blend_with_skill(model_forecasts, skill_weights, preferred_model, current_price)

                # Step 2c: persist new forecasts for future validation.
                if self._tracker is not None and model_forecasts:
                    try:
                        self._tracker.record_forecasts(symbol, h, model_forecasts, now_utc)
                    except Exception as _exc:
                        logger.debug("ForecastTracker.record_forecasts skipped for %s h=%d: %s", symbol, h, _exc)

                results[f'Forecast_{h}'] = blended

            # Surface the Facebook Prophet 30-day baseline forecasts (already computed
            # ONCE before the horizon loop and folded into the h=30 blend above).
            # Only write the result keys when Prophet actually produced output, exactly
            # as before (keys stay unset when Prophet is unavailable).
            if prophet_yhat_30 > 0:
                results['Forecast_30_Prophet'] = prophet_yhat_30
                results['Forecast_30_Prophet_Lower'] = prophet_30_lower
                results['Forecast_30_Prophet_Upper'] = prophet_30_upper

        except Exception as e:
            logger.error(f"Forecasting Engine Error for {row.get('Symbol', 'Unknown')}: {e}")
            
        return results

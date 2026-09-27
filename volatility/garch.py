"""GJR-GARCH(1,1) volatility estimation — core equity-path module.

Moved verbatim out of ``technical_options_engine.TechnicalOptionsEngine``
(2026-09, step 3d) so the equity path no longer depends on the options desk.
The equity path uses it for three things:

* ``GARCH_Vol`` → ``StrategyEngine.evaluate_security(garch_vol=)``'s
  cold-start sizing, and ``engine/advisory.py``'s Kelly sizing;
* the per-horizon term structure ``ForecastingStep`` reuses for its Monte
  Carlo sigma (so GJR-GARCH is fit once per ticker per cycle);
* ``forecasting_engine.py``'s own fallback sigma estimation.

The numbers are byte-identical to the old ``TechnicalOptionsEngine`` methods,
which now delegate here.
"""

from __future__ import annotations

import logging
from typing import Dict, Sequence

import numpy as np
import pandas as pd

try:
    from arch import arch_model  # type: ignore
    ARCH_AVAILABLE = True
except ImportError:
    ARCH_AVAILABLE = False

logger = logging.getLogger(__name__)


class GarchVolatilityEstimator:
    """GJR-GARCH(1,1) day-ahead volatility and its multi-horizon term structure."""

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

    def estimate_gjr_garch_volatility(self, df: pd.DataFrame) -> float:
        """
        Deploys a GJR-GARCH(1,1) model to extract day-ahead annualized volatility.
        Uses the arch library to deploy a GJR-GARCH(1,1) model via arch_model(returns, vol='GARCH', p=1, o=1, q=1).
        If optimization fails or arch library is missing, falls back to standard 20-day historical annualized volatility.

        This is the 1-day-ahead point estimate only -- naively scaling it by
        sqrt(T) to approximate a T-day-ahead volatility throws away GARCH's
        actual value (conditional-variance mean-reversion toward the long-run
        unconditional level over the forecast window). Multi-day callers
        (forecasting_engine.py's Monte Carlo horizons) should use
        estimate_gjr_garch_volatility_term_structure() instead, which computes
        a genuine per-horizon effective vol from the model's own multi-step
        variance forecast. This method is now a thin horizon=1 wrapper around
        that one -- numerically identical to the pre-refactor implementation
        (verified: cumulative variance over a single step == the 1-step
        variance, same formula), so every existing horizon=1 caller (the
        GARCH_Vol dashboard column, the VRP gate, True IVR, position sizing)
        is unaffected by this refactor.

        Returns ``nan`` (never a fabricated 0.20 -- CONSTRAINT #4) when there
        isn't enough history to measure anything at all; see
        estimate_gjr_garch_volatility_term_structure's docstring for exactly
        when that is. Every current caller already wraps this in a broad
        try/except and treats the result as `nan`-safe (get_vrp propagates a
        NaN GARCH vol into a NaN VRP, which correctly gates the VRP leg
        closed; calculate_realized_vol_rank short-circuits to 50.0 before
        ever touching a NaN current_vol on the same insufficient-history
        condition) -- this explicit check just makes that contract direct
        instead of relying on a caller's except clause to catch an
        AttributeError/TypeError from subscripting None.
        """
        term_structure = self.estimate_gjr_garch_volatility_term_structure(df, horizons=(1,))
        if term_structure is None:
            return float("nan")
        return term_structure[1]

    def estimate_gjr_garch_volatility_term_structure(
        self, df: pd.DataFrame, horizons: Sequence[int] = (1,)
    ) -> Dict[int, float]:
        """
        Fits a GJR-GARCH(1,1) model ONCE and derives a genuine, mean-reversion
        -aware annualized volatility for EACH requested horizon, instead of
        forecasting only 1 day ahead and letting a caller naively scale that
        single number by sqrt(T).

        For horizon T, the returned value is the annualized vol that
        reproduces the model's own cumulative T-day-ahead variance forecast
        when fed back through the standard sigma_daily = sigma_annual/sqrt(252)
        conversion and iid sqrt(T) Monte Carlo scaling:

            var_T_cumulative = sum(res.forecast(horizon=max(horizons))
                                       .variance.iloc[-1].values[:T])
            annualized_vol_T = sqrt(var_T_cumulative / T) * sqrt(252) / 100

        (the /100 undoes the *100 return scaling applied before fitting).
        This differs from sigma_1 * sqrt(T) whenever current conditional
        variance is away from its long-run unconditional level -- exactly the
        case (elevated post-shock vol, or an unusually calm regime) where an
        accurate multi-day confidence band matters most.

        A single `res.forecast(horizon=max(horizons))` call produces the
        entire variance path needed for every requested horizon, so this
        costs the same ONE fit + ONE forecast as the original horizon=1-only
        implementation, regardless of how many horizons are requested.

        horizons=(1,) reproduces estimate_gjr_garch_volatility()'s existing
        output exactly.

        Returns ``None`` (never a fabricated 0.20 -- CONSTRAINT #4) when
        there isn't enough history to measure ANYTHING, GARCH or otherwise
        (< 22 rows, or < 10 usable daily returns -- the same floor a 20-day
        historical stdev itself needs, so there is no honest fallback number
        to fall back to either). When there IS enough history but arch is
        unavailable or the GJR-GARCH fit itself fails, this degrades to the
        20-day historical annualized stdev applied FLATLY to every requested
        horizon (a real measurement, just not GARCH's mean-reversion-aware
        one) -- with no fitted model there is no term structure to compute,
        so every horizon gets the same fallback value (never fabricated
        per-horizon variation). Callers that subscript the return value MUST
        check for ``None`` first (estimate_gjr_garch_volatility above and
        _estimate_daily_sigma_multi_horizon in forecasting_engine.py both do)
        rather than relying on the resulting AttributeError/TypeError to be
        caught somewhere up the call stack -- a caller with too broad a
        try/except around this call can otherwise silently drop unrelated
        computations that share its scope (see
        docs/known_issues/forecast_ito_double_correction_and_horizon_units.md).
        """
        horizons = sorted({int(h) for h in horizons}) or [1]
        max_h = horizons[-1]

        df_clean = self.sanitize_ohlcv(df)
        if len(df_clean) < 22:
            return None

        returns = df_clean['Close'].pct_change().dropna()
        if len(returns) < 10:
            return None

        # Try GJR-GARCH fitting if arch library is available
        if ARCH_AVAILABLE:
            try:
                # Scale returns by 100 to prevent poor data scaling issues
                scaled_returns = returns * 100

                # GJR-GARCH(1,1): p=1, o=1, q=1. Use Student's t-distribution for fat tails
                model = arch_model(scaled_returns, vol='GARCH', p=1, o=1, q=1, dist='t')
                # arch ≥ 8.0 removed the `method` kwarg; default optimizer (SLSQP) works correctly
                res = model.fit(update_freq=0, disp='off')

                # ONE multi-step forecast covers every requested horizon --
                # h.1 of this call is identical to res.forecast(horizon=1)'s
                # h.1 (the analytic recursion is invariant to how many further
                # steps are requested), so horizon=1 stays byte-identical.
                forecast = res.forecast(horizon=max_h)
                variance_path = forecast.variance.iloc[-1].values  # scaled units, length max_h

                term_structure: Dict[int, float] = {}
                for h in horizons:
                    cumulative_variance = float(np.sum(variance_path[:h]))
                    effective_daily_variance = cumulative_variance / h
                    annualized_vol = (np.sqrt(effective_daily_variance) * np.sqrt(252)) / 100.0
                    # Sanity bound the forecast to realistic levels (e.g. between 2% and 300%)
                    term_structure[h] = float(max(0.02, min(3.0, annualized_vol)))
                return term_structure

            except Exception as e:
                logger.warning(f"GJR-GARCH failed to converge: {e}. Falling back to 20-day historical standard deviation.")
        else:
            logger.warning("arch library is not installed/available. Using 20-day historical standard deviation fallback.")

        # Fallback to standard 20-day historical annualized volatility, applied
        # flatly to every horizon (no fitted model -> no term structure).
        daily_vol = returns.tail(20).std()
        annualized_vol = daily_vol * np.sqrt(252)
        bounded = float(max(0.02, min(3.0, annualized_vol)))
        return {h: bounded for h in horizons}

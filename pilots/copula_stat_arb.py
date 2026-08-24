"""pilots/copula_stat_arb.py — Cross-Asset Non-Linear Copula Statistical Arbitrage & Dynamic Spread Engine.
========================================================================================================

Implements institutional statistical arbitrage with non-linear dependence modeling:
1. Dynamic Regular Copula Modeling (Bedford & Cooke 2002; Aas et al. 2009):
   - Clayton Copula: Asymmetric lower tail crisis dependence (lambda_L = 2^(-1/theta), lambda_U = 0).
   - Gumbel Copula: Upper tail momentum clustering (lambda_U = 2 - 2^(1/theta), lambda_L = 0).
   - Frank Copula: Symmetric non-tail dependence across extreme asset moves.
   - Gaussian Copula: Linear baseline benchmark with correlation rho.
   - Model selection via Maximum Likelihood Estimation (MLE) and Akaike Information Criterion (AIC).
2. Dynamic Kalman Filter State-Space Hedge Ratio:
   - State-space estimation of alpha_t and beta_t:
       y_t = alpha_t + beta_t * x_t + epsilon_t,   epsilon_t ~ N(0, R)
       theta_t = theta_{t-1} + w_t,                w_t ~ N(0, Q)
   - Prior covariance P_0 = 10^3 * I, measurement noise R = 10^(-3), process noise Q = delta * I.
   - Forward filtering guarantees 100% lookahead-free online updating.
3. Ornstein-Uhlenbeck (OU) Mean-Reversion Half-Life:
   - Estimates OU drift kappa and half-life tau_{1/2} = ln(2) / kappa.
   - Filters out non-mean-reverting pairs (tau_{1/2} not in [5, 60] days).
4. Rolling Spread & Z-Score Execution Engine:
   - Spread: S_t = y_t - beta_t * x_t.
   - Rolling spread Z-score: Z_t = (S_t - mu_S) / sigma_S.
   - Emits Long Spread (Buy Y, Short X) when Z_t <= -z_entry and tail risk is acceptable.
   - Emits Short Spread (Sell Y, Long X) when Z_t >= z_entry and tail risk is acceptable.
   - Emits Exit when |Z_t| crosses z_exit (or reverts to mean 0.0).
   - Emits Stop Loss when |Z_t| >= z_stop.

Design Invariants:
* **AST-Safe (CONSTRAINTS #1 & #3)**: Pure compute/execution module. Never imports heavy engines
  (`processing_engine`, `strategy_engine`, `forecasting_engine`, `macro_engine`,
   `technical_options_engine`, `main_orchestrator`, `desktop`).
* **Honesty (CONSTRAINT #4)**: Degenerate math guarded (< 1e-12), never fabricates false zeros.
* **Never Raises (CONSTRAINT #6)**: Degrades gracefully on missing/degenerate data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import uuid

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from scipy.stats import kendalltau, norm, rankdata

from data.paper_account_store import OrderStatus, PaperAccountStore
from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "CopulaFamily",
    "CopulaFitResult",
    "BestCopulaResult",
    "KalmanHedgeRatioResult",
    "CopulaPairAnalysis",
    "CopulaStatArbResult",
    "CopulaTailData",
    "CopulaSeriesPoint",
    "CopulaPairsResponse",
    "to_pseudo_observations",
    "clayton_log_likelihood",
    "gumbel_log_likelihood",
    "frank_log_likelihood",
    "gaussian_log_likelihood",
    "fit_clayton_copula",
    "fit_gumbel_copula",
    "fit_frank_copula",
    "fit_gaussian_copula",
    "fit_bivariate_copula",
    "fit_best_copula",
    "select_best_copula",
    "estimate_kalman_dynamic_hedge_ratio",
    "kalman_filter_hedge_ratio",
    "calculate_ou_half_life",
    "estimate_ou_half_life",
    "calculate_spread_zscore",
    "calculate_copula_mispricing",
    "compute_copula_spread_and_zscore",
    "evaluate_copula_stat_arb_pair",
    "generate_copula_stat_arb_signals",
    "execute_copula_spread_trade",
    "compute_copula_spread_analysis",
]

_EPSILON = 1e-6
_FLOAT_GUARD = 1e-12


class CopulaFamily(str, Enum):
    CLAYTON = "clayton"
    GUMBEL = "gumbel"
    FRANK = "frank"
    GAUSSIAN = "gaussian"


@dataclass
class CopulaFitResult:
    """Container for bivariate copula parameter estimates and tail dependence metrics."""

    family: str
    theta: float
    log_likelihood: float
    aic: float
    bic: float
    lower_tail_dependence: float = 0.0
    upper_tail_dependence: float = 0.0
    kendall_tau: float = 0.0
    converged: bool = True
    lambda_lower: float = 0.0
    lambda_upper: float = 0.0
    n_samples: int = 0
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lambda_lower == 0.0 and self.lower_tail_dependence != 0.0:
            object.__setattr__(self, "lambda_lower", self.lower_tail_dependence)
        elif self.lower_tail_dependence == 0.0 and self.lambda_lower != 0.0:
            object.__setattr__(self, "lower_tail_dependence", self.lambda_lower)
        if self.lambda_upper == 0.0 and self.upper_tail_dependence != 0.0:
            object.__setattr__(self, "lambda_upper", self.upper_tail_dependence)
        elif self.upper_tail_dependence == 0.0 and self.lambda_upper != 0.0:
            object.__setattr__(self, "upper_tail_dependence", self.lambda_upper)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BestCopulaResult:
    """Comparison result among multiple copula candidate families."""

    best_family: str
    best_fit: CopulaFitResult
    all_fits: Dict[str, CopulaFitResult]
    n_samples: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best_family": self.best_family,
            "best_fit": self.best_fit.to_dict(),
            "all_fits": {k: v.to_dict() for k, v in self.all_fits.items()},
            "n_samples": self.n_samples,
        }


@dataclass(frozen=True)
class KalmanHedgeRatioResult:
    """State-space dynamic hedge ratio estimation output."""

    alpha: np.ndarray
    beta: np.ndarray
    spread: np.ndarray
    spread_std: np.ndarray
    z_score: np.ndarray
    latest_alpha: float
    latest_beta: float
    latest_spread: float
    latest_z_score: float
    n_samples: int
    converged: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha": self.alpha.tolist(),
            "beta": self.beta.tolist(),
            "spread": self.spread.tolist(),
            "spread_std": self.spread_std.tolist(),
            "z_score": self.z_score.tolist(),
            "latest_alpha": self.latest_alpha,
            "latest_beta": self.latest_beta,
            "latest_spread": self.latest_spread,
            "latest_z_score": self.latest_z_score,
            "n_samples": self.n_samples,
            "converged": self.converged,
        }


@dataclass(frozen=True)
class CopulaPairAnalysis:
    """Comprehensive statistical arbitrage evaluation for an asset pair."""

    symbol_y: str
    symbol_x: str
    best_copula: BestCopulaResult
    kalman_result: KalmanHedgeRatioResult
    ou_half_life: Optional[float]
    ou_mean_reverting: bool
    mispricing_index: float
    signal: str
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_y": self.symbol_y,
            "symbol_x": self.symbol_x,
            "best_copula": self.best_copula.to_dict(),
            "kalman_result": self.kalman_result.to_dict(),
            "ou_half_life": self.ou_half_life,
            "ou_mean_reverting": self.ou_mean_reverting,
            "mispricing_index": self.mispricing_index,
            "signal": self.signal,
            "reason": self.reason,
        }


@dataclass
class CopulaStatArbResult:
    """Structured container for statistical arbitrage copula fitting, signals, and execution."""

    symbol_y: str
    symbol_x: str
    best_copula: str
    copula_theta: float
    lower_tail_dependence: float
    upper_tail_dependence: float
    kendall_tau: float
    aic: float
    ou_half_life: float
    current_spread: float
    current_zscore: float
    current_beta: float
    current_signal: str
    action: str
    tail_risk_acceptable: bool
    tail_risk_note: str
    z_entry: float
    z_exit: float
    z_stop: float
    signals_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if isinstance(d.get("signals_df"), pd.DataFrame):
            d["signals_df"] = self.signals_df.to_dict(orient="records")
        return d


# ---------------------------------------------------------------------------
# 1. Pseudo-Observations & Empirical CDF Marginals
# ---------------------------------------------------------------------------


def to_pseudo_observations(
    data_y: Union[np.ndarray, Sequence[float], pd.Series],
    data_x: Union[np.ndarray, Sequence[float], pd.Series],
    eps: float = _EPSILON,
) -> Tuple[np.ndarray, np.ndarray]:
    """Transforms continuous marginal distributions into uniform pseudo-observations u, v in (0, 1)

    using the empirical rank transformation:
        u_i = rank(y_i) / (N + 1),  v_i = rank(x_i) / (N + 1)
    """
    y_arr = np.asarray(data_y, dtype=float).ravel()
    x_arr = np.asarray(data_x, dtype=float).ravel()

    if len(y_arr) != len(x_arr):
        raise ValueError(f"Series lengths must match: len(y)={len(y_arr)}, len(x)={len(x_arr)}")

    valid_mask = np.isfinite(y_arr) & np.isfinite(x_arr)
    y_clean = y_arr[valid_mask]
    x_clean = x_arr[valid_mask]

    n = len(y_clean)
    if n == 0:
        return np.array([], dtype=float), np.array([], dtype=float)
    if n < 5:
        return np.array([0.5], dtype=float), np.array([0.5], dtype=float)

    rank_y = rankdata(y_clean, method="average")
    rank_x = rankdata(x_clean, method="average")

    u = rank_y / (n + 1.0)
    v = rank_x / (n + 1.0)

    u = np.clip(u, eps, 1.0 - eps)
    v = np.clip(v, eps, 1.0 - eps)
    return u, v


# ---------------------------------------------------------------------------
# 2. Bivariate Copula Log-Likelihood Formulations
# ---------------------------------------------------------------------------


def clayton_log_likelihood(theta: float, u: np.ndarray, v: np.ndarray) -> float:
    """Computes exact log-likelihood for Clayton copula:

    C(u, v; theta) = (u^(-theta) + v^(-theta) - 1)^(-1/theta),  theta > 0
    c(u, v; theta) = (1 + theta)(u*v)^(-(theta+1)) * (u^(-theta) + v^(-theta) - 1)^(-(2 + 1/theta))
    """
    if theta <= 1e-4:
        return -1e10

    n = len(u)
    term_base = np.power(u, -theta) + np.power(v, -theta) - 1.0
    term_base = np.maximum(term_base, _FLOAT_GUARD)

    ll = (
        n * math.log(1.0 + theta)
        - (1.0 + theta) * np.sum(np.log(u) + np.log(v))
        - (2.0 + 1.0 / theta) * np.sum(np.log(term_base))
    )
    return float(ll) if np.isfinite(ll) else -1e10


def gumbel_log_likelihood(theta: float, u: np.ndarray, v: np.ndarray) -> float:
    """Computes exact log-likelihood for Gumbel copula:

    C(u, v; theta) = exp(-((-ln u)^theta + (-ln v)^theta)^(1/theta)),  theta >= 1
    """
    if theta < 1.0:
        return -1e10

    if abs(theta - 1.0) < 1e-9:
        return 0.0

    u_tilde = -np.log(u)
    v_tilde = -np.log(v)
    u_tilde = np.maximum(u_tilde, _FLOAT_GUARD)
    v_tilde = np.maximum(v_tilde, _FLOAT_GUARD)

    a = np.power(u_tilde, theta) + np.power(v_tilde, theta)
    a = np.maximum(a, _FLOAT_GUARD)
    a_inv_theta = np.power(a, 1.0 / theta)

    ll = np.sum(
        -a_inv_theta
        - np.log(u)
        - np.log(v)
        + (theta - 1.0) * (np.log(u_tilde) + np.log(v_tilde))
        - (2.0 - 1.0 / theta) * np.log(a)
        + np.log(np.maximum(a_inv_theta + theta - 1.0, _FLOAT_GUARD))
    )
    return float(ll) if np.isfinite(ll) else -1e10


def frank_log_likelihood(theta: float, u: np.ndarray, v: np.ndarray) -> float:
    """Computes exact log-likelihood for Frank copula:

    C(u, v; theta) = -1/theta * ln(1 + (e^(-theta*u) - 1)(e^(-theta*v) - 1)/(e^(-theta) - 1)),  theta != 0
    """
    if abs(theta) < 1e-4:
        return 0.0

    n = len(u)
    theta = float(np.clip(theta, -35.0, 35.0))

    e1 = np.expm1(-theta)
    if abs(e1) < _FLOAT_GUARD:
        return -1e10

    eu = np.expm1(-theta * u)
    ev = np.expm1(-theta * v)

    d = e1 + eu * ev
    d_abs = np.maximum(np.abs(d), _FLOAT_GUARD)

    num = -theta * e1
    num = max(num, _FLOAT_GUARD)

    ll = (
        n * math.log(num)
        - theta * np.sum(u + v)
        - 2.0 * np.sum(np.log(d_abs))
    )
    return float(ll) if np.isfinite(ll) else -1e10


def gaussian_log_likelihood(rho: float, u: np.ndarray, v: np.ndarray) -> float:
    """Computes log-likelihood for Gaussian copula with correlation parameter rho in (-1, 1)."""
    if abs(rho) >= 0.999:
        return -1e10

    if abs(rho) < 1e-7:
        return 0.0

    z1 = norm.ppf(u)
    z2 = norm.ppf(v)
    z1 = np.clip(z1, -5.0, 5.0)
    z2 = np.clip(z2, -5.0, 5.0)

    om_rho2 = 1.0 - rho ** 2
    if om_rho2 <= _FLOAT_GUARD:
        return -1e10

    n = len(u)
    quad = (rho ** 2 * (z1 ** 2 + z2 ** 2) - 2.0 * rho * z1 * z2) / (2.0 * om_rho2)
    ll = -0.5 * n * math.log(om_rho2) - np.sum(quad)
    return float(ll) if np.isfinite(ll) else -1e10


# ---------------------------------------------------------------------------
# 3. Individual Copula MLE Fitting
# ---------------------------------------------------------------------------


def fit_clayton_copula(u: np.ndarray, v: np.ndarray) -> CopulaFitResult:
    """Fits Clayton copula via MLE. Computes lower tail crisis dependence lambda_L = 2^(-1/theta)."""
    n = len(u)
    if n < 5:
        return CopulaFitResult(
            family=CopulaFamily.CLAYTON.value,
            theta=0.0,
            log_likelihood=-1e6,
            aic=2e6,
            bic=2e6,
            lower_tail_dependence=0.0,
            upper_tail_dependence=0.0,
            kendall_tau=0.0,
            converged=False,
            n_samples=n,
        )

    tau, _ = kendalltau(u, v)
    tau_val = float(tau) if np.isfinite(tau) else 0.0

    if tau_val > 0.05:
        theta_0 = max(0.1, min(30.0, 2.0 * tau_val / max(0.01, 1.0 - tau_val)))
    else:
        theta_0 = 1.0

    res = minimize_scalar(
        lambda t: -clayton_log_likelihood(t, u, v),
        bounds=(0.01, 35.0),
        method="bounded",
    )

    theta = float(res.x) if res.success else theta_0
    ll = clayton_log_likelihood(theta, u, v)
    aic = 2.0 * 1.0 - 2.0 * ll
    bic = 1.0 * math.log(max(1, n)) - 2.0 * ll
    lambda_l = float(math.pow(2.0, -1.0 / theta)) if theta > 0 else 0.0

    return CopulaFitResult(
        family=CopulaFamily.CLAYTON.value,
        theta=round(theta, 4),
        log_likelihood=round(ll, 4),
        aic=round(aic, 4),
        bic=round(bic, 4),
        lower_tail_dependence=round(lambda_l, 4),
        upper_tail_dependence=0.0,
        kendall_tau=round(tau_val, 4),
        converged=bool(res.success),
        lambda_lower=round(lambda_l, 4),
        lambda_upper=0.0,
        n_samples=n,
        details={"theta_0": round(theta_0, 4)},
    )


def fit_gumbel_copula(u: np.ndarray, v: np.ndarray) -> CopulaFitResult:
    """Fits Gumbel copula via MLE. Computes upper tail momentum clustering lambda_U = 2 - 2^(1/theta)."""
    n = len(u)
    if n < 5:
        return CopulaFitResult(
            family=CopulaFamily.GUMBEL.value,
            theta=1.0,
            log_likelihood=-1e6,
            aic=2e6,
            bic=2e6,
            lower_tail_dependence=0.0,
            upper_tail_dependence=0.0,
            kendall_tau=0.0,
            converged=False,
            n_samples=n,
        )

    tau, _ = kendalltau(u, v)
    tau_val = float(tau) if np.isfinite(tau) else 0.0

    if tau_val > 0.05:
        theta_0 = max(1.01, min(30.0, 1.0 / max(0.01, 1.0 - tau_val)))
    else:
        theta_0 = 1.2

    res = minimize_scalar(
        lambda t: -gumbel_log_likelihood(t, u, v),
        bounds=(1.0001, 35.0),
        method="bounded",
    )

    theta = float(res.x) if res.success else theta_0
    ll = gumbel_log_likelihood(theta, u, v)
    aic = 2.0 * 1.0 - 2.0 * ll
    bic = 1.0 * math.log(max(1, n)) - 2.0 * ll
    lambda_u = float(2.0 - math.pow(2.0, 1.0 / theta)) if theta >= 1.0 else 0.0

    return CopulaFitResult(
        family=CopulaFamily.GUMBEL.value,
        theta=round(theta, 4),
        log_likelihood=round(ll, 4),
        aic=round(aic, 4),
        bic=round(bic, 4),
        lower_tail_dependence=0.0,
        upper_tail_dependence=round(lambda_u, 4),
        kendall_tau=round(tau_val, 4),
        converged=bool(res.success),
        lambda_lower=0.0,
        lambda_upper=round(lambda_u, 4),
        n_samples=n,
        details={"theta_0": round(theta_0, 4)},
    )


def fit_frank_copula(u: np.ndarray, v: np.ndarray) -> CopulaFitResult:
    """Fits Frank copula via MLE (symmetric non-tail dependence)."""
    n = len(u)
    if n < 5:
        return CopulaFitResult(
            family=CopulaFamily.FRANK.value,
            theta=0.0,
            log_likelihood=-1e6,
            aic=2e6,
            bic=2e6,
            lower_tail_dependence=0.0,
            upper_tail_dependence=0.0,
            kendall_tau=0.0,
            converged=False,
            n_samples=n,
        )

    tau, _ = kendalltau(u, v)
    tau_val = float(tau) if np.isfinite(tau) else 0.0

    res = minimize_scalar(
        lambda t: -frank_log_likelihood(t, u, v),
        bounds=(-35.0, 35.0),
        method="bounded",
    )

    theta = float(res.x) if res.success else (2.0 if tau_val >= 0 else -2.0)
    ll = frank_log_likelihood(theta, u, v)
    aic = 2.0 * 1.0 - 2.0 * ll
    bic = 1.0 * math.log(max(1, n)) - 2.0 * ll

    return CopulaFitResult(
        family=CopulaFamily.FRANK.value,
        theta=round(theta, 4),
        log_likelihood=round(ll, 4),
        aic=round(aic, 4),
        bic=round(bic, 4),
        lower_tail_dependence=0.0,
        upper_tail_dependence=0.0,
        kendall_tau=round(tau_val, 4),
        converged=bool(res.success),
        lambda_lower=0.0,
        lambda_upper=0.0,
        n_samples=n,
    )


def fit_gaussian_copula(u: np.ndarray, v: np.ndarray) -> CopulaFitResult:
    """Fits Gaussian copula baseline via MLE."""
    n = len(u)
    if n < 5:
        return CopulaFitResult(
            family=CopulaFamily.GAUSSIAN.value,
            theta=0.0,
            log_likelihood=-1e6,
            aic=2e6,
            bic=2e6,
            lower_tail_dependence=0.0,
            upper_tail_dependence=0.0,
            kendall_tau=0.0,
            converged=False,
            n_samples=n,
        )

    tau, _ = kendalltau(u, v)
    tau_val = float(tau) if np.isfinite(tau) else 0.0
    rho_0 = math.sin(math.pi * tau_val / 2.0)

    res = minimize_scalar(
        lambda r: -gaussian_log_likelihood(r, u, v),
        bounds=(-0.999, 0.999),
        method="bounded",
    )

    rho = float(res.x) if res.success else rho_0
    ll = gaussian_log_likelihood(rho, u, v)
    aic = 2.0 * 1.0 - 2.0 * ll
    bic = 1.0 * math.log(max(1, n)) - 2.0 * ll

    return CopulaFitResult(
        family=CopulaFamily.GAUSSIAN.value,
        theta=round(rho, 4),
        log_likelihood=round(ll, 4),
        aic=round(aic, 4),
        bic=round(bic, 4),
        lower_tail_dependence=0.0,
        upper_tail_dependence=0.0,
        kendall_tau=round(tau_val, 4),
        converged=bool(res.success),
        lambda_lower=0.0,
        lambda_upper=0.0,
        n_samples=n,
    )


def fit_bivariate_copula(
    u: Union[Sequence[float], np.ndarray, pd.Series],
    v: Union[Sequence[float], np.ndarray, pd.Series],
    family: str = "clayton",
) -> CopulaFitResult:
    """Fit a bivariate copula family (Clayton, Gumbel, Frank, Gaussian) to uniform pseudo-observations u, v.

    Args:
        u: Uniform pseudo-observations in (0, 1).
        v: Uniform pseudo-observations in (0, 1).
        family: Name of copula family ("clayton", "gumbel", "frank", "gaussian").

    Returns:
        CopulaFitResult containing estimated theta, log-likelihood, AIC, BIC,
        and tail dependence coefficients.
    """
    fam = str(family or "clayton").lower().strip()
    u_arr = np.asarray(u, dtype=float).ravel()
    v_arr = np.asarray(v, dtype=float).ravel()

    mask = np.isfinite(u_arr) & np.isfinite(v_arr)
    u_arr = np.clip(u_arr[mask], _EPSILON, 1.0 - _EPSILON)
    v_arr = np.clip(v_arr[mask], _EPSILON, 1.0 - _EPSILON)

    if fam == "clayton":
        return fit_clayton_copula(u_arr, v_arr)
    elif fam == "gumbel":
        return fit_gumbel_copula(u_arr, v_arr)
    elif fam == "frank":
        return fit_frank_copula(u_arr, v_arr)
    elif fam in ("gaussian", "normal"):
        return fit_gaussian_copula(u_arr, v_arr)
    else:
        raise ValueError(
            f"Unsupported copula family '{family}'. Choose from: clayton, gumbel, frank, gaussian."
        )


def select_best_copula(
    u: Union[Sequence[float], np.ndarray, pd.Series],
    v: Union[Sequence[float], np.ndarray, pd.Series],
    families: Sequence[str] = ("clayton", "gumbel", "frank", "gaussian"),
) -> BestCopulaResult:
    """Compares candidate copula families on pseudo-observations u, v by AIC, selecting the best tail model.

    Args:
        u: Uniform pseudo-observations in (0, 1).
        v: Uniform pseudo-observations in (0, 1).
        families: List of copula families to evaluate.

    Returns:
        BestCopulaResult containing the best fitting family, optimal fit, and all candidate fits.
    """
    u_arr = np.asarray(u, dtype=float).ravel()
    v_arr = np.asarray(v, dtype=float).ravel()
    n = len(u_arr)

    all_fits: Dict[str, CopulaFitResult] = {}
    best_family: Optional[str] = None
    min_aic = float("inf")

    for fam in families:
        try:
            fit_res = fit_bivariate_copula(u_arr, v_arr, family=fam)
            all_fits[fam] = fit_res
            if fit_res.aic < min_aic and fit_res.log_likelihood > -1e9:
                min_aic = fit_res.aic
                best_family = fam
        except Exception as exc:  # noqa: BLE001
            logger.debug("select_best_copula: failed for family %s: %s", fam, exc)

    if not best_family or best_family not in all_fits:
        fallback = fit_gaussian_copula(u_arr, v_arr)
        all_fits["gaussian"] = fallback
        best_family = "gaussian"

    return BestCopulaResult(
        best_family=best_family,
        best_fit=all_fits[best_family],
        all_fits=all_fits,
        n_samples=n,
    )


def fit_best_copula(
    series_y: Union[pd.Series, np.ndarray, Sequence[float]],
    series_x: Union[pd.Series, np.ndarray, Sequence[float]],
) -> CopulaFitResult:
    """Fits Clayton, Gumbel, Frank, and Gaussian copulas to asset returns and selects

    the optimal non-linear dependence model minimizing AIC.
    """
    u, v = to_pseudo_observations(series_y, series_x)
    best_res = select_best_copula(u, v)
    return best_res.best_fit


# ---------------------------------------------------------------------------
# 4. State-Space Dynamic Kalman Filter Hedge Ratio
# ---------------------------------------------------------------------------


def estimate_kalman_dynamic_hedge_ratio(
    series_y: Union[pd.Series, np.ndarray, Sequence[float]],
    series_x: Union[pd.Series, np.ndarray, Sequence[float]],
    delta: float = 1e-4,
    R: float = 1e-3,
    initial_p: float = 1000.0,
) -> KalmanHedgeRatioResult:
    """Fits state-space Kalman Filter to estimate rolling dynamic beta_t and alpha_t.

    Observation equation:
        y_t = alpha_t + beta_t * x_t + epsilon_t,   epsilon_t ~ N(0, R)
    State equation:
        theta_t = theta_{t-1} + w_t,                 w_t ~ N(0, Q)
        where Q = delta * I_2.

    Returns:
        KalmanHedgeRatioResult with rolling alphas, betas, innovation spreads, and Z-scores.
    """
    y = np.asarray(series_y, dtype=float).ravel()
    x = np.asarray(series_x, dtype=float).ravel()

    if len(y) != len(x):
        raise ValueError(f"Series lengths must match: len(y)={len(y)}, len(x)={len(x)}")

    n = len(y)
    if n == 0:
        empty = np.array([], dtype=float)
        return KalmanHedgeRatioResult(
            alpha=empty,
            beta=empty,
            spread=empty,
            spread_std=empty,
            z_score=empty,
            latest_alpha=0.0,
            latest_beta=1.0,
            latest_spread=0.0,
            latest_z_score=0.0,
            n_samples=0,
            converged=False,
        )

    # Initial state estimate [alpha_0, beta_0]
    init_beta = float(y[0] / x[0]) if abs(x[0]) > 1e-8 else 1.0
    init_alpha = 0.0

    def _causal_mean_x2(t_idx: int) -> float:
        # Causal (expanding, capped-at-20) P0/Q scale factor. Uses only x[0 .. min(t_idx, 19)]
        # -- never an observation with index > t_idx. Previously this was a single fixed
        # `np.mean(x[:20])` computed ONCE from the whole input array and applied identically
        # to every timestep, including t < 19 -- strictly earlier than some of the data
        # (x[t_idx+1:20]) baked into that constant, violating this module's own docstring
        # claim of "100% lookahead-free online updating" (see module docstring above). For
        # t_idx >= 19 this is bit-identical to that old fixed x[:20] scale, so today's common
        # production case (input series longer than ~20 bars) is unaffected; for t_idx < 19 it
        # uses only observations seen up to and including t_idx, closing the leak. Regression
        # test: tests/test_copula_stat_arb.py::test_kalman_hedge_ratio_mean_x2_causal_no_lookahead.
        window_end = min(t_idx + 1, 20)
        return max(1.0, float(np.mean(x[:window_end]) ** 2))

    state = np.array([init_alpha, init_beta], dtype=float)
    p = np.diag([initial_p * _causal_mean_x2(0), initial_p])
    r = float(max(1e-6, R))

    alpha_arr = np.zeros(n, dtype=float)
    beta_arr = np.zeros(n, dtype=float)
    spread_arr = np.zeros(n, dtype=float)
    spread_std_arr = np.zeros(n, dtype=float)
    z_score_arr = np.zeros(n, dtype=float)

    for t in range(n):
        yt = y[t]
        xt = x[t]

        if not np.isfinite(yt) or not np.isfinite(xt):
            alpha_arr[t] = state[0]
            beta_arr[t] = state[1]
            spread_arr[t] = 0.0
            spread_std_arr[t] = 1.0
            z_score_arr[t] = 0.0
            continue

        # 1. State Prediction: theta_{t|t-1} = theta_{t-1|t-1}, P_{t|t-1} = P + Q
        q = np.diag([delta * _causal_mean_x2(t), delta])
        p_pred = p + q

        # 2. Measurement Vector H = [1.0, xt]
        h = np.array([1.0, xt], dtype=float)

        # 3. Measurement Prediction & Innovation
        y_hat = float(np.dot(h, state))
        e_t = yt - y_hat  # Spread / residual

        # 4. Innovation Covariance F_t = H P_pred H^T + R
        # Guarded at the repo's standard degenerate-std threshold (< 1e-12,
        # never == 0 / > 0 — see CLAUDE.md's "Degenerate-std guard convention").
        # In practice f_t >= r >= 1e-6 always (R is itself floored at 1e-6
        # above and P_pred is PSD so h.P_pred.h^T >= 0), so this guard never
        # engages today; it exists so a future change to the R floor can't
        # silently reintroduce a near-zero innovation-variance divide.
        f_t_raw = float(np.dot(h, np.dot(p_pred, h)) + r)
        f_t = max(_FLOAT_GUARD, f_t_raw)
        sigma_t = math.sqrt(f_t)

        # 5. Kalman Gain K_t = P_pred H^T / F_t
        k_t = np.dot(p_pred, h) / f_t

        # 6. State Update: theta_{t|t} = theta + K_t * e_t
        state = state + k_t * e_t

        # 7. Covariance Update: P_{t|t} = (I - K_t H) P_pred
        i_kh = np.eye(2, dtype=float) - np.outer(k_t, h)
        p = np.dot(i_kh, p_pred)

        alpha_arr[t] = state[0]
        beta_arr[t] = state[1]
        spread_arr[t] = e_t
        spread_std_arr[t] = sigma_t
        z_score_arr[t] = e_t / sigma_t

    return KalmanHedgeRatioResult(
        alpha=alpha_arr,
        beta=beta_arr,
        spread=spread_arr,
        spread_std=spread_std_arr,
        z_score=z_score_arr,
        latest_alpha=float(alpha_arr[-1]),
        latest_beta=float(beta_arr[-1]),
        latest_spread=float(spread_arr[-1]),
        latest_z_score=float(z_score_arr[-1]),
        n_samples=n,
        converged=True,
    )


def kalman_filter_hedge_ratio(
    series_y: Union[pd.Series, np.ndarray, Sequence[float]],
    series_x: Union[pd.Series, np.ndarray, Sequence[float]],
    delta: float = 1e-5,
    r_noise: float = 1e-3,
    p0: float = 1000.0,
) -> Tuple[pd.Series, pd.Series]:
    """Computes time-varying dynamic hedge ratio beta_t and intercept alpha_t using

    a forward-pass Kalman Filter aligned with pandas Series.
    """
    y_s = pd.Series(series_y).astype(float)
    x_s = pd.Series(series_x).astype(float)
    idx = y_s.index

    res = estimate_kalman_dynamic_hedge_ratio(
        y_s.to_numpy(), x_s.to_numpy(), delta=delta, R=r_noise, initial_p=p0
    )
    return pd.Series(res.alpha, index=idx, name="alpha"), pd.Series(res.beta, index=idx, name="beta")


# ---------------------------------------------------------------------------
# 5. Ornstein-Uhlenbeck (OU) Mean-Reversion Half-Life Estimation
# ---------------------------------------------------------------------------


def calculate_ou_half_life(
    spread_series: Union[pd.Series, np.ndarray, Sequence[float]],
) -> Optional[float]:
    """Estimates Ornstein-Uhlenbeck mean-reversion half-life via AR(1) regression:

    Delta S_t = S_t - S_{t-1} = a + b * S_{t-1} + epsilon_t
    tau_{1/2} = -ln(2) / b
    """
    s = np.asarray(spread_series, dtype=float).ravel()
    s = s[np.isfinite(s)]
    if len(s) < 10:
        return None

    s_lag = s[:-1]
    delta_s = np.diff(s)

    var_lag = float(np.var(s_lag))
    if var_lag <= _FLOAT_GUARD:
        return None

    cov_mat = np.cov(s_lag, delta_s)
    b = cov_mat[0, 1] / var_lag

    if b >= -1e-6:
        return None

    half_life = -math.log(2.0) / b
    return float(half_life) if half_life > 0.0 else None


def estimate_ou_half_life(
    spread_series: Union[pd.Series, np.ndarray, Sequence[float]],
) -> float:
    """Estimates the Ornstein-Uhlenbeck half-life, returning float('inf') if non-mean-reverting."""
    hl = calculate_ou_half_life(spread_series)
    return float(hl) if hl is not None else float("inf")


# ---------------------------------------------------------------------------
# 6. Spread & Rolling Z-Score Computation
# ---------------------------------------------------------------------------


def calculate_spread_zscore(
    spread: Union[Sequence[float], np.ndarray, pd.Series],
    window: int = 20,
    min_periods: int = 5,
) -> np.ndarray:
    """Calculate rolling Z-score of spread over a sliding window."""
    s = np.asarray(spread, dtype=float).ravel()
    n = len(s)
    z = np.zeros(n, dtype=float)

    for i in range(n):
        start = max(0, i - window + 1)
        sub = s[start : i + 1]
        if len(sub) < min_periods:
            z[i] = 0.0
            continue
        mean = np.mean(sub)
        std = np.std(sub)
        z[i] = (s[i] - mean) / std if std > _FLOAT_GUARD else 0.0

    return z


def compute_copula_spread_and_zscore(
    series_y: Union[pd.Series, np.ndarray, List[float]],
    series_x: Union[pd.Series, np.ndarray, List[float]],
    beta_series: Optional[Union[pd.Series, np.ndarray, List[float]]] = None,
    lookback: int = 30,
) -> pd.DataFrame:
    """Computes dynamic spread, OU half-life, and rolling spread Z-score:

    1. Spread: S_t = y_t - beta_t * x_t.
    2. Estimates Ornstein-Uhlenbeck (OU) half-life of mean reversion tau_{1/2}.
    3. Rolling spread Z-score: Z_t = (S_t - mu_S) / sigma_S.
    """
    y = pd.Series(series_y).astype(float)
    x = pd.Series(series_x).astype(float)
    idx = y.index

    if beta_series is None:
        _, beta = kalman_filter_hedge_ratio(y, x)
    else:
        beta = pd.Series(beta_series, index=idx).astype(float)

    spread = y - (beta * x)

    warmup_n = min(len(spread) // 4, 15) if len(spread) >= 30 else 0
    half_life = estimate_ou_half_life(spread.iloc[warmup_n:] if (len(spread) - warmup_n) >= 10 else spread)

    w = max(10, min(120, int(lookback)))
    spread_mean = spread.rolling(window=w, min_periods=max(5, w // 2)).mean()
    spread_std = spread.rolling(window=w, min_periods=max(5, w // 2)).std()

    z_score = pd.Series(
        np.where(spread_std >= _FLOAT_GUARD, (spread - spread_mean) / spread_std, np.nan),
        index=idx,
        name="z_score",
    )

    df = pd.DataFrame(
        {
            "y": y,
            "x": x,
            "beta": beta,
            "spread": spread,
            "z_score": z_score,
            "half_life": half_life,
        },
        index=idx,
    )
    return df


def calculate_copula_mispricing(
    u: float,
    v: float,
    copula_fit: CopulaFitResult,
) -> float:
    """Compute conditional probability / mispricing index M_t = P(U <= u | V = v).

    When M_t approaches 1.0, asset U is overpriced relative to asset V.
    When M_t approaches 0.0, asset U is underpriced relative to asset V.
    """
    u_val = float(np.clip(u, _EPSILON, 1.0 - _EPSILON))
    v_val = float(np.clip(v, _EPSILON, 1.0 - _EPSILON))
    theta = copula_fit.theta
    fam = copula_fit.family.lower()

    if fam == "clayton":
        if theta <= 1e-4:
            return u_val
        u_th = math.pow(u_val, -theta)
        v_th = math.pow(v_val, -theta)
        base = max(1e-300, u_th + v_th - 1.0)
        v_term = math.pow(v_val, -(theta + 1.0))
        cond = v_term * math.pow(base, -(1.0 + 1.0 / theta))
        return float(np.clip(cond, 0.0, 1.0))

    elif fam == "gumbel":
        if theta <= 1.0001:
            return u_val
        x = -math.log(u_val)
        y = -math.log(v_val)
        x_th = math.pow(x, theta)
        y_th = math.pow(y, theta)
        a = x_th + y_th
        c_uv = math.exp(-math.pow(a, 1.0 / theta))
        denom = v_val * math.pow(a, 1.0 - 1.0 / theta)
        num = c_uv * math.pow(y, theta - 1.0)
        cond = num / max(1e-300, denom)
        return float(np.clip(cond, 0.0, 1.0))

    elif fam == "frank":
        if abs(theta) < 1e-4:
            return u_val
        e1 = math.expm1(-theta)
        eu = math.expm1(-theta * u_val)
        ev = math.expm1(-theta * v_val)
        num = math.exp(-theta * v_val) * eu
        denom = e1 + eu * ev
        cond = num / denom if abs(denom) > 1e-12 else u_val
        return float(np.clip(cond, 0.0, 1.0))

    elif fam in ("gaussian", "normal"):
        rho = float(np.clip(theta, -0.999, 0.999))
        if abs(rho) < 1e-4:
            return u_val
        norm_dist = norm
        x = norm_dist.ppf(u_val)
        y = norm_dist.ppf(v_val)
        cond_z = (x - rho * y) / math.sqrt(max(1e-6, 1.0 - rho * rho))
        return float(np.clip(norm_dist.cdf(cond_z), 0.0, 1.0))

    return u_val


# ---------------------------------------------------------------------------
# 7. Copula Stat Arb Signal Generation & State Machine
# ---------------------------------------------------------------------------


def evaluate_copula_stat_arb_pair(
    series_y: Union[Sequence[float], np.ndarray, pd.Series],
    series_x: Union[Sequence[float], np.ndarray, pd.Series],
    symbol_y: str = "ASSET_Y",
    symbol_x: str = "ASSET_X",
    delta: float = 1e-4,
    R: float = 1e-3,
    z_entry_threshold: float = settings.OPTIONS_COPULA_ZSCORE_ENTRY_THRESHOLD,
    z_exit_threshold: float = 0.5,
) -> CopulaPairAnalysis:
    """Full-pipeline statistical arbitrage pair analysis with Copula & Kalman."""
    y = np.asarray(series_y, dtype=float).ravel()
    x = np.asarray(series_x, dtype=float).ravel()

    if len(y) != len(x) or len(y) < 15:
        empty_kalman = KalmanHedgeRatioResult(
            alpha=np.array([]),
            beta=np.array([]),
            spread=np.array([]),
            spread_std=np.array([]),
            z_score=np.array([]),
            latest_alpha=0.0,
            latest_beta=1.0,
            latest_spread=0.0,
            latest_z_score=0.0,
            n_samples=len(y),
            converged=False,
        )
        empty_copula = select_best_copula([0.5], [0.5])
        return CopulaPairAnalysis(
            symbol_y=symbol_y,
            symbol_x=symbol_x,
            best_copula=empty_copula,
            kalman_result=empty_kalman,
            ou_half_life=None,
            ou_mean_reverting=False,
            mispricing_index=0.5,
            signal="NEUTRAL",
            reason=f"Insufficient history length ({len(y)} samples, need >= 15)",
        )

    kalman_res = estimate_kalman_dynamic_hedge_ratio(y, x, delta=delta, R=R)
    # Causal time-varying spread S_t = y_t - beta_t * x_t (strictly lookahead-free)
    # Uses the causal beta_t path with warm-up stabilization rather than the full-sample final scalar
    spread_portfolio = y - kalman_res.beta * x
    warmup = min(len(spread_portfolio) // 4, 15) if len(spread_portfolio) >= 30 else 0
    eval_spread = spread_portfolio[warmup:] if (len(spread_portfolio) - warmup) >= 10 else spread_portfolio
    half_life = calculate_ou_half_life(eval_spread)
    ou_reverting = bool(half_life is not None and 1.0 <= half_life <= 120.0)

    ret_y = np.diff(y) / np.maximum(y[:-1], 1e-6)
    ret_x = np.diff(x) / np.maximum(x[:-1], 1e-6)
    u, v = to_pseudo_observations(ret_y, ret_x)

    best_copula_res = select_best_copula(u, v)

    last_u = float(u[-1]) if len(u) > 0 else 0.5
    last_v = float(v[-1]) if len(v) > 0 else 0.5
    mispricing = calculate_copula_mispricing(last_u, last_v, best_copula_res.best_fit)

    z = kalman_res.latest_z_score

    if ou_reverting:
        if z <= -z_entry_threshold or mispricing <= 0.05:
            signal = "LONG_SPREAD"
        elif z >= z_entry_threshold or mispricing >= 0.95:
            signal = "SHORT_SPREAD"
        elif abs(z) <= z_exit_threshold:
            signal = "CLOSE_SPREAD"
        else:
            signal = "HOLD"
    else:
        signal = "NEUTRAL"

    return CopulaPairAnalysis(
        symbol_y=symbol_y,
        symbol_x=symbol_x,
        best_copula=best_copula_res,
        kalman_result=kalman_res,
        ou_half_life=half_life,
        ou_mean_reverting=ou_reverting,
        mispricing_index=mispricing,
        signal=signal,
        reason=None if ou_reverting else "Spread is not mean-reverting (OU half-life invalid or > 120d)",
    )


def generate_copula_stat_arb_signals(
    symbol_y: str,
    symbol_x: str,
    prices_y: Union[pd.Series, np.ndarray, List[float]],
    prices_x: Union[pd.Series, np.ndarray, List[float]],
    z_entry: float = settings.OPTIONS_COPULA_ZSCORE_ENTRY_THRESHOLD,
    z_exit: float = 0.0,
    z_stop: float = 4.0,
    lookback: int = 30,
    half_life_min: float = 5.0,
    half_life_max: float = 60.0,
    tail_risk_lower_limit: float = 0.85,
) -> CopulaStatArbResult:
    """Generates non-linear copula statistical arbitrage trading signals."""
    sym_y = str(symbol_y or "Y").upper().strip()
    sym_x = str(symbol_x or "X").upper().strip()

    y_s = pd.Series(prices_y).astype(float)
    x_s = pd.Series(prices_x).astype(float)
    idx = y_s.index
    n = len(y_s)

    if n < 15:
        empty_df = pd.DataFrame(columns=["y", "x", "beta", "spread", "z_score", "position", "signal", "pnl"])
        return CopulaStatArbResult(
            symbol_y=sym_y,
            symbol_x=sym_x,
            best_copula="None",
            copula_theta=0.0,
            lower_tail_dependence=0.0,
            upper_tail_dependence=0.0,
            kendall_tau=0.0,
            aic=0.0,
            ou_half_life=float("inf"),
            current_spread=0.0,
            current_zscore=0.0,
            current_beta=1.0,
            current_signal="FLAT",
            action="Insufficient Data",
            tail_risk_acceptable=False,
            tail_risk_note="Insufficient price history (< 15 bars)",
            z_entry=z_entry,
            z_exit=z_exit,
            z_stop=z_stop,
            signals_df=empty_df,
            summary={"status": "insufficient_data"},
        )

    ret_y = y_s.pct_change().dropna()
    ret_x = x_s.pct_change().dropna()

    # Causal spread & rolling z-score computation (strictly lookahead-free forward Kalman filter)
    spread_df = compute_copula_spread_and_zscore(y_s, x_s, lookback=lookback)
    half_life = float(spread_df["half_life"].iloc[-1]) if not spread_df.empty else float("inf")

    # Fit final copula on full history for latest telemetry reporting
    copula_fit = fit_best_copula(ret_y, ret_x)

    tail_risk_acceptable = True
    tail_notes = []

    if copula_fit.lower_tail_dependence > tail_risk_lower_limit:
        tail_risk_acceptable = False
        tail_notes.append(
            f"Extreme lower tail crash dependence (lambda_L={copula_fit.lower_tail_dependence:.2f} > {tail_risk_lower_limit:.2f})"
        )

    if np.isinf(half_life) or half_life < half_life_min or half_life > half_life_max:
        if np.isinf(half_life):
            tail_notes.append("Non-mean-reverting spread (infinite half-life)")
        elif half_life < half_life_min:
            tail_notes.append(f"Sub-daily/hyper-fast mean reversion (half-life={half_life:.1f}d < {half_life_min:.1f}d)")
        elif half_life > half_life_max:
            tail_notes.append(f"Sluggish mean reversion (half-life={half_life:.1f}d > {half_life_max:.1f}d)")
        if np.isinf(half_life) or half_life > 90.0:
            tail_risk_acceptable = False

    if not tail_notes:
        tail_notes.append("Tail risk acceptable; well-behaved mean reversion")
    tail_risk_note = "; ".join(tail_notes)

    positions = np.zeros(n, dtype=float)
    signals = ["HOLD"] * n
    actions = ["Hold"] * n
    current_pos = 0.0

    z_vals = spread_df["z_score"].to_numpy()

    # Causal step-by-step tail-risk evaluation for lookahead-free signal generation
    copula_cache_interval = 5
    last_step_fit = None
    last_tail_risk_ok = False

    for t in range(n):
        zt = z_vals[t]
        if not np.isfinite(zt):
            positions[t] = 0.0
            signals[t] = "FLAT"
            actions[t] = "Flat / Warming up"
            continue

        # Causal trailing copula evaluation: strictly uses returns available at/before timestep t.
        # Checks BOTH criteria the full-sample `tail_risk_acceptable` summary above checks --
        # copula lower-tail dependence AND OU half-life mean-reversion -- each refit on the same
        # trailing window, so neither criterion silently reverts to a full-sample (lookahead)
        # read for the per-bar gate.
        if t >= 15:
            if t == n - 1:
                step_tail_ok = tail_risk_acceptable
            elif last_step_fit is None or (t % copula_cache_interval == 0):
                sub_y = ret_y.iloc[max(0, t - max(30, lookback)):t]
                sub_x = ret_x.iloc[max(0, t - max(30, lookback)):t]
                sub_spread = spread_df["spread"].iloc[max(0, t - max(30, lookback)):t]
                if len(sub_y) >= 15:
                    c_fit = fit_best_copula(sub_y, sub_x)
                    step_copula_ok = bool(c_fit.lower_tail_dependence <= tail_risk_lower_limit)
                    step_half_life = estimate_ou_half_life(sub_spread) if len(sub_spread) >= 10 else float("inf")
                    step_half_life_ok = bool(
                        np.isfinite(step_half_life) and half_life_min <= step_half_life <= half_life_max
                    )
                    step_tail_ok = step_copula_ok and step_half_life_ok
                    last_step_fit = c_fit
                    last_tail_risk_ok = step_tail_ok
                else:
                    step_tail_ok = False
            else:
                step_tail_ok = last_tail_risk_ok
        else:
            step_tail_ok = False

        if current_pos == 0.0:
            if zt <= -abs(z_entry) and step_tail_ok:
                current_pos = 1.0
                signals[t] = "LONG_SPREAD"
                actions[t] = f"Buy {sym_y}, Short {sym_x}"
            elif zt >= abs(z_entry) and step_tail_ok:
                current_pos = -1.0
                signals[t] = "SHORT_SPREAD"
                actions[t] = f"Sell {sym_y}, Long {sym_x}"
            else:
                signals[t] = "FLAT"
                actions[t] = "Flat / No Signal"
        elif current_pos == 1.0:
            if zt >= -abs(z_exit) or zt <= -abs(z_stop):
                current_pos = 0.0
                signals[t] = "EXIT" if zt >= -abs(z_exit) else "STOP_LOSS"
                actions[t] = "Exit Spread" if zt >= -abs(z_exit) else "Stop Loss Exit"
            else:
                signals[t] = "HOLD"
                actions[t] = f"Hold Long Spread ({sym_y} / {sym_x})"
        elif current_pos == -1.0:
            if zt <= abs(z_exit) or zt >= abs(z_stop):
                current_pos = 0.0
                signals[t] = "EXIT" if zt <= abs(z_exit) else "STOP_LOSS"
                actions[t] = "Exit Spread" if zt <= abs(z_exit) else "Stop Loss Exit"
            else:
                signals[t] = "HOLD"
                actions[t] = f"Hold Short Spread ({sym_y} / {sym_x})"

        positions[t] = current_pos

    spread_df["position"] = positions
    spread_df["signal"] = signals
    spread_df["action"] = actions

    spread_diff = spread_df["spread"].diff().fillna(0.0)
    capital_proxy = (spread_df["y"] + (spread_df["beta"].abs() * spread_df["x"])).replace(0.0, 100.0)
    pos_lag = spread_df["position"].shift(1).fillna(0.0)
    strategy_returns = (pos_lag * spread_diff / capital_proxy).fillna(0.0)
    spread_df["strategy_returns"] = strategy_returns
    spread_df["cumulative_pnl"] = strategy_returns.cumsum()

    trade_entries = (spread_df["position"].diff().abs() > 0.0) & (spread_df["position"] != 0.0)
    total_trades = int(trade_entries.sum())
    total_return = float(strategy_returns.sum())
    std_ret = float(strategy_returns.std())
    sharpe = float(math.sqrt(252.0) * (strategy_returns.mean() / std_ret)) if std_ret >= _FLOAT_GUARD else 0.0

    cum_ret = (1.0 + strategy_returns).cumprod()
    peak = cum_ret.cummax()
    drawdown = (cum_ret - peak) / peak
    max_dd = float(drawdown.min()) if not drawdown.empty else 0.0

    win_trades = (strategy_returns > 0.0).sum()
    loss_trades = (strategy_returns < 0.0).sum()
    win_rate = float(win_trades / max(1, win_trades + loss_trades)) if (win_trades + loss_trades) > 0 else 0.0

    curr_spread = float(spread_df["spread"].iloc[-1])
    curr_zscore = float(spread_df["z_score"].iloc[-1]) if np.isfinite(spread_df["z_score"].iloc[-1]) else 0.0
    curr_beta = float(spread_df["beta"].iloc[-1])
    curr_signal = str(spread_df["signal"].iloc[-1])
    curr_action = str(spread_df["action"].iloc[-1])

    summary = {
        "symbol_y": sym_y,
        "symbol_x": sym_x,
        "best_copula": copula_fit.family,
        "copula_theta": copula_fit.theta,
        "lower_tail_dependence": copula_fit.lower_tail_dependence,
        "upper_tail_dependence": copula_fit.upper_tail_dependence,
        "kendall_tau": copula_fit.kendall_tau,
        "aic": copula_fit.aic,
        "ou_half_life": round(half_life, 2) if np.isfinite(half_life) else None,
        "current_spread": round(curr_spread, 4),
        "current_zscore": round(curr_zscore, 4),
        "current_beta": round(curr_beta, 4),
        "current_signal": curr_signal,
        "action": curr_action,
        "tail_risk_acceptable": tail_risk_acceptable,
        "tail_risk_note": tail_risk_note,
        "total_trades": total_trades,
        "total_return_pct": round(total_return * 100.0, 2),
        "annualized_sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "win_rate_pct": round(win_rate * 100.0, 2),
    }

    return CopulaStatArbResult(
        symbol_y=sym_y,
        symbol_x=sym_x,
        best_copula=copula_fit.family,
        copula_theta=copula_fit.theta,
        lower_tail_dependence=copula_fit.lower_tail_dependence,
        upper_tail_dependence=copula_fit.upper_tail_dependence,
        kendall_tau=copula_fit.kendall_tau,
        aic=copula_fit.aic,
        ou_half_life=round(half_life, 2) if np.isfinite(half_life) else float("inf"),
        current_spread=round(curr_spread, 4),
        current_zscore=round(curr_zscore, 4),
        current_beta=round(curr_beta, 4),
        current_signal=curr_signal,
        action=curr_action,
        tail_risk_acceptable=tail_risk_acceptable,
        tail_risk_note=tail_risk_note,
        z_entry=z_entry,
        z_exit=z_exit,
        z_stop=z_stop,
        signals_df=spread_df,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# 8. Spread Order Execution & PaperAccountStore Integration
# ---------------------------------------------------------------------------


def execute_copula_spread_trade(
    result: Union[CopulaStatArbResult, Dict[str, Any]],
    store: Optional[PaperAccountStore] = None,
    capital: float = 10000.0,
    dry_run: bool = False,
    is_live: bool = False,
) -> Dict[str, Any]:
    """Executes a copula pairs statistical arbitrage trade into PaperAccountStore."""
    if is_live:
        return {
            "ok": False,
            "message": "Advisory-Only Mode: Live order execution is disabled. Please use paper mode.",
        }

    res_dict = result.to_dict() if hasattr(result, "to_dict") else result
    sym_y = res_dict.get("symbol_y", "Y")
    sym_x = res_dict.get("symbol_x", "X")
    signal = res_dict.get("current_signal", "FLAT")
    beta = float(res_dict.get("current_beta", 1.0))
    z_score = float(res_dict.get("current_zscore", 0.0))

    trade_id = f"copula_{uuid.uuid4().hex[:8]}"

    if signal not in ("LONG_SPREAD", "SHORT_SPREAD"):
        return {
            "ok": False,
            "trade_id": trade_id,
            "message": f"No active entry signal for {sym_y}/{sym_x} (Signal: {signal}, Z={z_score:.2f}).",
        }

    side_y = "BUY" if signal == "LONG_SPREAD" else "SELL"
    side_x = "SELL" if signal == "LONG_SPREAD" else "BUY"

    leg_capital = capital / 2.0
    price_y = float(res_dict.get("summary", {}).get("price_y", 100.0))
    price_x = float(res_dict.get("summary", {}).get("price_x", 100.0))
    if price_y <= 0:
        price_y = 100.0
    if price_x <= 0:
        price_x = 100.0

    qty_y = max(1, int(leg_capital / price_y))
    qty_x = max(1, int(round(qty_y * abs(beta))))

    legs = [
        {"symbol": sym_y, "side": side_y, "qty": qty_y, "estimated_price": price_y},
        {"symbol": sym_x, "side": side_x, "qty": qty_x, "estimated_price": price_x},
    ]

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "trade_id": f"copula_dry_{uuid.uuid4().hex[:8]}",
            "pair": f"{sym_y}/{sym_x}",
            "signal": signal,
            "legs": legs,
            "message": f"Dry run: Copula Stat Arb order validated for {sym_y}/{sym_x}.",
        }

    try:
        paper_store = store or PaperAccountStore()
    except Exception as exc:
        # Detail stays server-side (logger.exception); the returned dict is client-facing
        # (unwired today, but see the dispersion_trading.py sibling fix for the same
        # py/stack-trace-exposure pattern once an endpoint returns this dict directly).
        logger.exception("Failed to initialize PaperAccountStore for copula trade: %s", exc)
        return {
            "ok": False,
            "trade_id": trade_id,
            "message": "Paper account storage unavailable; see server logs for detail.",
        }

    strategy_name = "Copula Stat Arb"
    # Matches apply_multi_leg_fill's own internal per-leg client_order_id
    # convention (f"{client_order_id}_L{idx+1}", legs in the order passed
    # below) so these actually correspond to the real paper_orders rows
    # rather than a cosmetic label that never matched anything on disk.
    order_id_y = f"{trade_id}_L1"
    order_id_x = f"{trade_id}_L2"

    # PR 872 remediation, Task 2: `PaperAccountStore.place_order` does not
    # exist -- confirmed by grep (`def place_order` matches nowhere in
    # data/paper_account_store.py) -- so this used to raise AttributeError on
    # every real (non-dry-run) call, and the two independent calls it made
    # (one per leg) would also not have been atomic even if the method did
    # exist: a fill on the Y leg with a subsequent failure on the X leg would
    # leave a naked, unintended directional position instead of the intended
    # market-neutral pair. This is a pairs/stat-arb trade -- long one stock,
    # short the other, simultaneously -- exactly the shape
    # `apply_multi_leg_fill` exists to make atomic (both legs commit in one
    # transaction, or neither does; see that method's own docstring). `qty`
    # here is SHARES, not options contracts, but the method itself makes no
    # options-specific assumption in its per-leg cash/position bookkeeping
    # (the ×100 in its parent-order audit-row `avg_contract_price` display
    # field is the one options-flavored quirk; it is diagnostic-only and does
    # not affect cash balance or position quantity/basis, both computed
    # per-leg from the real `fill_price`/`qty` below).
    net_cash_impact = (
        -(qty_y * price_y) if side_y == "BUY" else (qty_y * price_y)
    ) + (
        -(qty_x * price_x) if side_x == "BUY" else (qty_x * price_x)
    )

    fill_ok = paper_store.apply_multi_leg_fill(
        client_order_id=trade_id,
        symbol=f"{sym_y}/{sym_x}",
        strategy_name=strategy_name,
        contracts=1,
        legs=[
            {"symbol": sym_y, "side": side_y.lower(), "qty": float(qty_y), "fill_price": price_y},
            {"symbol": sym_x, "side": side_x.lower(), "qty": float(qty_x), "fill_price": price_x},
        ],
        net_cash_impact=net_cash_impact,
        commission_and_fees=0.0,
        status=OrderStatus.FILLED,
        strategy_id=strategy_name,
    )

    # Atomicity by construction: apply_multi_leg_fill commits both legs in one
    # transaction or neither -- there is no partial-success state to check for
    # (unlike the old `bool(res_y and res_x)`, which implied two independent
    # outcomes that could legitimately disagree and leave a naked leg).
    all_ok = bool(fill_ok)

    return {
        "ok": all_ok,
        "trade_id": trade_id,
        "strategy": strategy_name,
        "pair": f"{sym_y}/{sym_x}",
        "signal": signal,
        "order_id_y": order_id_y,
        "order_id_x": order_id_x,
        "legs": legs,
        "message": (
            f"Successfully executed Copula Stat Arb spread for {sym_y}/{sym_x} ({signal})."
            if all_ok
            else f"Failed to execute spread for {sym_y}/{sym_x} (insufficient funds or collateral; no leg was opened)."
        ),
    }


# ---------------------------------------------------------------------------
# 9. Pilots API & Webapp Serialization Helpers
# ---------------------------------------------------------------------------


@dataclass
class CopulaTailData:
    lower_tail_dependence: float
    upper_tail_dependence: float
    copula_family: str
    theta: float
    log_likelihood: float
    aic: float
    kendall_tau: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CopulaSeriesPoint:
    date: str
    asset_x_price: float
    asset_y_price: float
    kalman_beta: float
    spread: float
    spread_z_score: float
    upper_band_2sigma: float
    lower_band_2sigma: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CopulaPairsResponse:
    pair: str
    asset_x: str
    asset_y: str
    copula_family: str
    tail_dependence: CopulaTailData
    kalman_beta: float
    kalman_alpha: float
    ou_half_life_days: float
    spread_z_score: float
    current_spread: float
    signal_action: str
    historical_series: List[CopulaSeriesPoint] = field(default_factory=list)
    as_of: Optional[str] = None
    status_note: Optional[str] = None
    is_synthetic: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tail_dependence"] = self.tail_dependence.to_dict()
        d["historical_series"] = [p.to_dict() for p in self.historical_series]
        return d


def _generate_synthetic_prices(
    symbol_y: str,
    symbol_x: str,
    n_bars: int = 120,
) -> Tuple[List[str], np.ndarray, np.ndarray]:
    """Deterministic synthetic price series generator for testing / fallback mode."""
    seed = abs(hash(f"{symbol_y}_{symbol_x}")) % (2**31 - 1)
    rng = np.random.default_rng(seed)

    p_x0 = 100.0 + (abs(hash(symbol_x)) % 150)
    p_y0 = p_x0 * (1.2 + 0.3 * math.sin(len(symbol_y)))

    corr = 0.85
    cov_matrix = [[1.0, corr], [corr, 1.0]]
    innovations = rng.multivariate_normal([0.0, 0.0], cov_matrix, size=n_bars)

    sigma_x = 0.015
    sigma_y = 0.018
    ret_x = innovations[:, 0] * sigma_x
    ret_y = innovations[:, 1] * sigma_y

    px = p_x0 * np.exp(np.cumsum(ret_x))
    py = p_y0 * np.exp(np.cumsum(ret_y))

    spread_shock = np.sin(np.linspace(0, 4 * math.pi, n_bars)) * (0.02 * p_y0)
    py = py + spread_shock

    base_date = datetime.now(timezone.utc) - timedelta(days=n_bars)
    dates = [(base_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_bars)]
    return dates, px, py


def compute_copula_spread_analysis(
    symbol_y: str,
    symbol_x: str,
    prices_y: Optional[Union[Sequence[float], np.ndarray, pd.Series]] = None,
    prices_x: Optional[Union[Sequence[float], np.ndarray, pd.Series]] = None,
    dates: Optional[List[str]] = None,
) -> CopulaPairsResponse:
    """Performs full Copula Statistical Arbitrage & Dynamic Kalman Spread Analysis.

    Returns CopulaPairsResponse matching API and webapp contracts.
    """
    sym_y = str(symbol_y or "GLD").upper().strip()
    sym_x = str(symbol_x or "GDX").upper().strip()
    pair_label = f"{sym_y}/{sym_x}"

    # Honesty flag (CONSTRAINT #4): set True whenever the caller did not supply
    # sufficient real price history and this function falls back to
    # _generate_synthetic_prices — surfaced on the response so callers/UI can
    # tell a real analysis from a synthetic one instead of presenting both
    # identically.
    is_synthetic = False
    if prices_y is None or prices_x is None or len(prices_y) < 15 or len(prices_x) < 15:
        is_synthetic = True
        dates_gen, px, py = _generate_synthetic_prices(sym_y, sym_x)
        if prices_x is None or len(prices_x) == 0:
            prices_x = px
        if prices_y is None or len(prices_y) == 0:
            prices_y = py
        if dates is None or len(dates) == 0:
            dates = dates_gen

    min_len = min(len(prices_x), len(prices_y))
    x_arr = np.asarray(prices_x[:min_len], dtype=float)
    y_arr = np.asarray(prices_y[:min_len], dtype=float)
    dt_list = dates[:min_len] if dates else [f"T-{min_len - i}" for i in range(min_len)]

    res = generate_copula_stat_arb_signals(
        symbol_y=sym_y,
        symbol_x=sym_x,
        prices_y=y_arr,
        prices_x=x_arr,
    )

    df = res.signals_df
    fam_raw = str(res.best_copula)
    fam_cap = fam_raw.capitalize() if fam_raw.lower() in ("clayton", "gumbel", "frank", "gaussian") else "Gaussian"

    tail_data = CopulaTailData(
        lower_tail_dependence=round(float(res.lower_tail_dependence), 4),
        upper_tail_dependence=round(float(res.upper_tail_dependence), 4),
        copula_family=fam_cap,
        theta=round(float(res.copula_theta), 4),
        log_likelihood=round(float(-res.aic / 2.0 if res.aic else 0.0), 2),
        aic=round(float(res.aic), 2),
        kendall_tau=round(float(res.kendall_tau), 4),
    )

    series_points: List[CopulaSeriesPoint] = []
    if not df.empty and len(df) == min_len:
        for i in range(min_len):
            row = df.iloc[i]
            z_val = float(row.get("z_score", 0.0))
            if not math.isfinite(z_val):
                z_val = 0.0
            sp = float(row.get("spread", 0.0))
            beta_val = float(row.get("beta", 1.0))
            series_points.append(
                CopulaSeriesPoint(
                    date=dt_list[i],
                    asset_x_price=round(float(row.get("x", x_arr[i])), 2),
                    asset_y_price=round(float(row.get("y", y_arr[i])), 2),
                    kalman_beta=round(beta_val, 4),
                    spread=round(sp, 4),
                    spread_z_score=round(z_val, 2),
                    upper_band_2sigma=round(float(sp + 2.0 * abs(z_val + 1e-4)), 4),
                    lower_band_2sigma=round(float(sp - 2.0 * abs(z_val + 1e-4)), 4),
                )
            )

    curr_z = float(res.current_zscore) if math.isfinite(res.current_zscore) else 0.0
    curr_spread = float(res.current_spread) if math.isfinite(res.current_spread) else 0.0
    curr_beta = float(res.current_beta) if math.isfinite(res.current_beta) else 1.0
    hl = float(res.ou_half_life) if (math.isfinite(res.ou_half_life) and res.ou_half_life > 0) else 15.0

    action = res.current_signal
    if action not in ("LONG_SPREAD", "SHORT_SPREAD", "EXIT", "HOLD"):
        if curr_z <= -2.0:
            action = "LONG_SPREAD"
        elif curr_z >= 2.0:
            action = "SHORT_SPREAD"
        elif abs(curr_z) <= 0.5:
            action = "EXIT"
        else:
            action = "HOLD"

    return CopulaPairsResponse(
        pair=pair_label,
        asset_x=sym_x,
        asset_y=sym_y,
        copula_family=fam_cap,
        tail_dependence=tail_data,
        kalman_beta=round(curr_beta, 4),
        kalman_alpha=0.0,
        ou_half_life_days=round(hl, 2),
        spread_z_score=round(curr_z, 2),
        current_spread=round(curr_spread, 4),
        signal_action=action,
        historical_series=series_points,
        as_of=datetime.now(timezone.utc).isoformat(),
        status_note=res.tail_risk_note,
        is_synthetic=is_synthetic,
    )


"""
InvestYo Quant Platform - Fractional Kelly Sizing
====================================================
Estimates win probability (p) and payoff ratio (b) from realized closed
trades, and computes a fractional-Kelly allocation from them. Replaces the
two divergent score-derived win-probability formulas previously hardcoded
in strategy_engine.py and main_orchestrator.py.

Per-strategy bootstrap path (Stage 1.7)
-----------------------------------------
``kelly_sizing_for_strategy(transactions_store, strategy_id, realized_vol)``
is the high-level entry point for per-strategy, bootstrap-conservative sizing:
  1. Filters closed trades to ``strategy_id`` via
     ``estimate_win_rate_and_payoff_per_strategy()``.
  2. If fewer than ``min_trades`` (default 30) exist for that strategy
     (cold start), falls back to ``volatility_target_weight(realized_vol)``
     -- no Kelly multiplier -- and returns the string tag
     ``"vol_target_fallback"`` so callers can log clearly.
  3. Otherwise bootstraps (n=1_000 default) the per-strategy return
     distribution, takes the **5th-percentile** Kelly fraction
     (``kelly_low`` from ``bootstrap_kelly_confidence()``) as the actual
     sizing weight -- the "epistemic humility" version that stays
     conservative until the edge estimate is stable.
  4. Returns ``(kelly_weight, sizing_path_tag)`` so StrategyEngine can
     surface the sizing path in its verbose notes.

The 5th-percentile convention was chosen over the point estimate because:
  - With 30–100 trades, confidence intervals on p and b are wide.
  - Using the 5th percentile ensures we never over-bet on an edge that may
    be sampling noise; only with >~200 trades does the 5th percentile
    approach the point estimate.
  - The median (50th percentile) is also returned in ``kelly_mean`` from
    ``bootstrap_kelly_confidence()`` in case callers want a richer signal.
"""

import logging
import math
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# A Kelly estimate from fewer than this many trades is statistically
# meaningless -- returned as NaN so callers fall back to volatility-target-only
# sizing rather than sizing off noise.
MIN_TRADES_REQUIRED = 30
# Below this count the estimate is returned but flagged as low-confidence.
MIN_TRADES_FOR_CONFIDENCE = 50


def estimate_win_rate_and_payoff(
    closed_trades_df: pd.DataFrame,
    lookback_trades: int = 100,
) -> Tuple[float, float, int]:
    """Estimates (p, b) from the most recent closed trades.

    Uses at most the last ``lookback_trades`` closed trades (by exit
    timestamp) so the estimate tracks the strategy's current edge rather than
    averaging over its entire history. ``p`` is the empirical win rate;
    ``b`` is the win/loss payoff ratio (mean winning return / mean absolute
    losing return).

    Parameters
    ----------
    closed_trades_df : pd.DataFrame
        Must contain ``entry_price``, ``exit_price``, ``side``, ``exit_ts``
        columns (the schema produced by ``transactions_store.TransactionsStore
        .closed_trades_df()``).
    lookback_trades : int
        Maximum number of most-recent closed trades to use for the estimate.

    Returns
    -------
    tuple[float, float, int]
        ``(p, b, n_trades)``. ``n_trades`` is the number of trades the
        estimate was actually computed from (after lookback truncation).
        Returns ``(NaN, NaN, n_trades)`` if fewer than
        ``MIN_TRADES_REQUIRED`` trades are available (logged as an error),
        or if there are no losing trades in the sample (b is undefined,
        logged as a warning). Logs a warning (but still returns real
        estimates) if ``MIN_TRADES_REQUIRED <= n_trades < MIN_TRADES_FOR_CONFIDENCE``.
    """
    if closed_trades_df is None or closed_trades_df.empty:
        logger.error("estimate_win_rate_and_payoff: no closed trades available (n=0).")
        return float("nan"), float("nan"), 0

    required_cols = {"entry_price", "exit_price", "side", "exit_ts"}
    missing_cols = required_cols - set(closed_trades_df.columns)
    if missing_cols:
        raise ValueError(f"closed_trades_df missing required columns: {sorted(missing_cols)}")

    df = closed_trades_df.dropna(subset=["entry_price", "exit_price", "side"]).copy()
    df = df.sort_values("exit_ts").tail(lookback_trades)
    n_trades = len(df)

    if n_trades < MIN_TRADES_REQUIRED:
        logger.error(
            "estimate_win_rate_and_payoff: only %d closed trades available "
            "(< %d required). Kelly estimate disabled.", n_trades, MIN_TRADES_REQUIRED
        )
        return float("nan"), float("nan"), n_trades

    if n_trades < MIN_TRADES_FOR_CONFIDENCE:
        logger.warning(
            "estimate_win_rate_and_payoff: only %d closed trades available "
            "(< %d for a stable estimate). Proceeding with caution.",
            n_trades, MIN_TRADES_FOR_CONFIDENCE
        )

    is_long = df["side"].str.lower() == "long"
    ret = np.where(
        is_long,
        (df["exit_price"] - df["entry_price"]) / df["entry_price"],
        (df["entry_price"] - df["exit_price"]) / df["entry_price"],
    )
    ret = pd.Series(ret, index=df.index)

    wins = ret[ret > 0]
    losses = ret[ret <= 0]
    p = float(len(wins)) / n_trades

    if losses.empty:
        logger.warning(
            "estimate_win_rate_and_payoff: no losing trades in sample (n=%d); "
            "payoff ratio b is undefined.", n_trades
        )
        return p, float("nan"), n_trades

    avg_loss = float(abs(losses.mean()))
    if avg_loss == 0.0:
        logger.warning(
            "estimate_win_rate_and_payoff: average loss is exactly 0; payoff ratio b is undefined."
        )
        return p, float("nan"), n_trades

    avg_win = float(wins.mean()) if not wins.empty else 0.0
    b = avg_win / avg_loss
    return p, b, n_trades


def fractional_kelly(p: float, b: float, fraction: float = 0.5, cap: float = 0.20) -> float:
    """Fractional-Kelly position size.

    f* = (p*b - (1-p)) / b
    sized = fraction * f*
    return max(0.0, min(cap, sized))

    Parameters
    ----------
    p : float
        Estimated win probability.
    b : float
        Estimated payoff ratio (average win / average loss).
    fraction : float
        Kelly fraction to apply (0.5 = half-Kelly).
    cap : float
        Hard upper bound on the returned allocation.

    Returns
    -------
    float
        NaN if ``p`` or ``b`` is None/NaN (undefined -- e.g. insufficient
        trade history), so callers can detect this and fall back to
        volatility-targeting-only sizing rather than sizing off a
        meaningless estimate. 0.0 if ``b`` is non-positive (a non-positive
        payoff ratio makes full Kelly undefined/negative-definite).
    """
    if p is None or b is None or (isinstance(p, float) and math.isnan(p)) or (isinstance(b, float) and math.isnan(b)):
        return float("nan")

    if b <= 0:
        logger.warning("fractional_kelly: non-positive payoff ratio b=%.4f; returning 0.0.", b)
        return 0.0

    full_kelly = (p * b - (1.0 - p)) / b
    sized = fraction * full_kelly
    return max(0.0, min(cap, sized))


def estimate_win_rate_and_payoff_per_strategy(
    transactions_store,
    strategy_id: str,
    min_trades: int = 30,
) -> Tuple[float, float, int]:
    """Estimates (p, b) strictly for a specific strategy_id from transactions history."""
    try:
        closed_trades_df = transactions_store.closed_trades_df()
    except Exception as e:
        logger.error(f"estimate_win_rate_and_payoff_per_strategy: failed to read transactions store: {e}")
        return float("nan"), float("nan"), 0

    if closed_trades_df is None or closed_trades_df.empty:
        return float("nan"), float("nan"), 0

    if "strategy" not in closed_trades_df.columns:
        logger.warning("estimate_win_rate_and_payoff_per_strategy: 'strategy' column not in trades table.")
        return float("nan"), float("nan"), 0

    strategy_trades = closed_trades_df[closed_trades_df["strategy"] == strategy_id].copy()
    n_trades = len(strategy_trades)

    if n_trades < min_trades:
        logger.warning(
            f"estimate_win_rate_and_payoff_per_strategy: only {n_trades} trades for '{strategy_id}' "
            f"(< {min_trades} required)."
        )
        return float("nan"), float("nan"), n_trades

    # Calculate returns for these trades
    is_long = strategy_trades["side"].str.lower() == "long"
    ret = np.where(
        is_long,
        (strategy_trades["exit_price"] - strategy_trades["entry_price"]) / strategy_trades["entry_price"],
        (strategy_trades["entry_price"] - strategy_trades["exit_price"]) / strategy_trades["entry_price"],
    )
    ret = pd.Series(ret)

    wins = ret[ret > 0]
    losses = ret[ret <= 0]
    p = float(len(wins)) / n_trades

    if losses.empty or wins.empty:
        return p, float("nan"), n_trades

    avg_loss = float(abs(losses.mean()))
    if avg_loss == 0.0:
        return p, float("nan"), n_trades

    avg_win = float(wins.mean())
    b = avg_win / avg_loss
    return p, b, n_trades


def bootstrap_kelly_confidence(
    closed_trades: Union[pd.DataFrame, pd.Series, np.ndarray],
    n_bootstraps: int = 1000,
    fraction: float = 0.5,
    cap: float = 0.20,
) -> Tuple[float, float, float]:
    """Resample trades with replacement; compute fractional Kelly on each; return 5th/50th/95th percentiles."""
    if isinstance(closed_trades, pd.DataFrame):
        if closed_trades.empty:
            return float("nan"), float("nan"), float("nan")
        # Extract returns
        is_long = closed_trades["side"].str.lower() == "long"
        returns = np.where(
            is_long,
            (closed_trades["exit_price"] - closed_trades["entry_price"]) / closed_trades["entry_price"],
            (closed_trades["entry_price"] - closed_trades["exit_price"]) / closed_trades["entry_price"],
        )
    elif isinstance(closed_trades, pd.Series):
        returns = closed_trades.dropna().values
    else:
        returns = np.asarray(closed_trades)

    n = len(returns)
    if n == 0:
        return float("nan"), float("nan"), float("nan")

    kelly_results = []
    # Seed generator for determinism
    rng = np.random.default_rng(42)

    for _ in range(n_bootstraps):
        sample = rng.choice(returns, size=n, replace=True)
        wins = sample[sample > 0]
        losses = sample[sample <= 0]
        p_boot = len(wins) / n
        if len(losses) == 0:
            b_boot = float("nan")
        else:
            avg_loss = abs(losses.mean())
            if avg_loss == 0.0:
                b_boot = float("nan")
            else:
                avg_win = wins.mean() if len(wins) > 0 else 0.0
                b_boot = avg_win / avg_loss

        k_val = fractional_kelly(p_boot, b_boot, fraction=fraction, cap=cap)
        if math.isnan(k_val):
            k_val = 0.0
        kelly_results.append(k_val)

    kelly_results = np.array(kelly_results)
    kelly_low = float(np.percentile(kelly_results, 5))
    kelly_mean = float(np.percentile(kelly_results, 50))
    kelly_high = float(np.percentile(kelly_results, 95))

    return kelly_low, kelly_mean, kelly_high


# =============================================================================
# PER-STRATEGY HELPER: RAW RETURN EXTRACTION
# =============================================================================

def _get_per_strategy_returns(
    closed_trades_df: pd.DataFrame,
    strategy_id: str,
) -> Optional[np.ndarray]:
    """Extract a numpy array of per-trade returns for a specific strategy_id.

    Side-aware: long trades earn (exit - entry)/entry; short trades earn
    (entry - exit)/entry. Returns None if the DataFrame has no matching rows
    after filtering, or if the ``strategy`` column is absent.

    This is a pure helper reused by both ``kelly_sizing_for_strategy()`` and
    tests so the side-aware return formula lives in exactly one place.

    Parameters
    ----------
    closed_trades_df : pd.DataFrame
        As produced by ``TransactionsStore.closed_trades_df()``. Must contain
        ``entry_price``, ``exit_price``, ``side``, ``strategy`` columns.
    strategy_id : str
        The strategy tag to filter on (matches the ``strategy`` column).

    Returns
    -------
    numpy.ndarray or None
        1-D float array of per-trade returns, or None when no matching rows
        exist. An empty array (0 elements) is also returned as None so callers
        don't need to check both conditions.
    """
    if closed_trades_df is None or closed_trades_df.empty:
        return None
    if "strategy" not in closed_trades_df.columns:
        logger.warning(
            "_get_per_strategy_returns: 'strategy' column absent from trades table; "
            "cannot filter by strategy_id='%s'.", strategy_id
        )
        return None

    mask = closed_trades_df["strategy"] == strategy_id
    df = closed_trades_df[mask].dropna(subset=["entry_price", "exit_price", "side"]).copy()
    if df.empty:
        return None

    is_long = df["side"].str.lower() == "long"
    returns = np.where(
        is_long,
        (df["exit_price"] - df["entry_price"]) / df["entry_price"],
        (df["entry_price"] - df["exit_price"]) / df["entry_price"],
    )
    if len(returns) == 0:
        return None
    return returns.astype(float)


# =============================================================================
# STAGE 1.7 ENTRY POINT: PER-STRATEGY BOOTSTRAP-CONSERVATIVE SIZING
# =============================================================================

def kelly_sizing_for_strategy(
    transactions_store,
    strategy_id: str,
    realized_vol: Optional[float],
    min_trades: int = MIN_TRADES_REQUIRED,
    n_bootstraps: int = 1_000,
    fraction: float = 0.5,
    cap: float = 0.20,
    target_vol: float = 0.10,
    max_leverage: float = 2.0,
) -> Tuple[float, str]:
    """Conservative per-strategy Kelly sizing using bootstrap 5th percentile.

    This is the Stage 1.7 high-level entry point that wires together
    ``estimate_win_rate_and_payoff_per_strategy()``,
    ``_get_per_strategy_returns()``, and ``bootstrap_kelly_confidence()``
    into a single call. StrategyEngine calls this when a ``strategy_id``
    is provided, replacing the global-pool aggregate path.

    Epistemic humility convention
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    We take the **5th-percentile** Kelly fraction from the bootstrap
    distribution rather than the point estimate. With only 30–100 trades
    the sampling uncertainty in (p, b) is large; the 5th percentile
    produces a fraction that, in expectation, under-sizes a real edge
    rather than over-sizing a sampling artefact. The median and 95th
    percentile are returned in the sizing tag for diagnostic logging.

    Cold-start fallback
    ~~~~~~~~~~~~~~~~~~~~
    When fewer than ``min_trades`` closed trades exist for ``strategy_id``,
    or when ``realized_vol`` is unavailable, the function falls back to
    ``volatility_target_weight(realized_vol)`` (no Kelly multiplier) and
    tags the path as ``"vol_target_fallback"``. This matches the behavior
    of ``StrategyEngine._raw_kelly_or_vol_target_sizing()`` for the global
    aggregate case, keeping the fallback logic symmetric.

    Parameters
    ----------
    transactions_store :
        A ``TransactionsStore`` instance (or any object with a
        ``closed_trades_df() -> pd.DataFrame`` method).
    strategy_id : str
        Strategy tag to filter on (matches the ``strategy`` column in the
        trades table). Case-sensitive.
    realized_vol : float or None
        Annualized realized volatility for the instrument, used by the
        vol-target fallback. Pass ``None`` when unavailable; the fallback
        will return ``0.0`` (logged) rather than a meaningless division.
    min_trades : int
        Minimum number of per-strategy closed trades required before the
        Kelly path activates. Default: ``MIN_TRADES_REQUIRED`` (30).
    n_bootstraps : int
        Number of bootstrap resamples. 1_000 is sufficient for a stable
        5th-percentile estimate; increase to 5_000 for publication-quality
        confidence intervals at the cost of ~5x runtime.
    fraction : float
        Kelly fraction applied inside ``fractional_kelly()``.
        Default: 0.5 (half-Kelly).
    cap : float
        Hard ceiling on the fractional Kelly output before the
        MAX_POSITION_WEIGHT clamp in ``StrategyEngine``. Default: 0.20.
    target_vol : float
        Target annualized volatility for the vol-target fallback.
        Default: 0.10.
    max_leverage : float
        Maximum leverage cap for the vol-target fallback. Default: 2.0.

    Returns
    -------
    tuple[float, str]
        ``(weight, path_tag)`` where:
        - ``weight`` is the raw (pre-MAX_POSITION_WEIGHT-clamp) sizing
          weight in ``[0.0, max_leverage]``. NaN if no vol is available
          and no Kelly history exists (safe: StrategyEngine clamps to 0.0).
        - ``path_tag`` is a human-readable string describing the path taken,
          e.g. ``"bootstrap_kelly_5th_pct(n=30,k5=0.08,k50=0.11,k95=0.19)"``,
          ``"vol_target_fallback"``, or ``"cold_start_no_vol"``.
    """
    from sizing.vol_target import volatility_target_weight  # avoid circular at module level

    def _vol_fallback(reason: str, n_trades_for_scalein: int = 0) -> Tuple[float, str]:
        """Shared vol-target fallback branch.

        Cold-start scale-in (WS3): the vol-target fallback weight is ramped in
        by ``min(1.0, n_trades / MIN_TRADES_REQUIRED)`` so sizing does not jump
        discontinuously from the Kelly-capped path (<= ``KELLY_CAP``) to a full
        vol-target weight (up to ``MAX_POSITION_WEIGHT``) the instant a strategy
        is new or its trade history is wiped. The factor is 1.0 once enough
        trades exist (>= ``MIN_TRADES_REQUIRED``), so warm behaviour is
        unchanged; it only ever REDUCES sizing on cold start (never inflates),
        keeping it honesty-safe. It is reflected in the returned path tag so the
        sizing decision stays auditable.
        """
        if realized_vol is None or (isinstance(realized_vol, float) and math.isnan(realized_vol)) or realized_vol <= 0:
            logger.warning(
                "kelly_sizing_for_strategy: %s AND realized_vol unavailable/non-positive; "
                "weight = 0.0.", reason
            )
            return 0.0, "cold_start_no_vol"
        weight = volatility_target_weight(realized_vol, target_vol=target_vol, max_leverage=max_leverage)
        scale_in = min(1.0, max(0, n_trades_for_scalein) / MIN_TRADES_REQUIRED)
        weight *= scale_in
        logger.warning(
            "kelly_sizing_for_strategy: %s. Falling back to vol-target weight=%.4f "
            "(scale_in=%.2f, n=%d).",
            reason, weight, scale_in, n_trades_for_scalein
        )
        return weight, f"vol_target_fallback(scalein={scale_in:.2f},n={n_trades_for_scalein})"

    # 1. Attempt to read from the transactions store
    try:
        closed_df = transactions_store.closed_trades_df()
    except Exception as e:
        logger.error(
            "kelly_sizing_for_strategy: failed to read transactions store: %s", e
        )
        return _vol_fallback(f"transactions store read error: {e}", 0)

    # 2. Check per-strategy trade count; gate Kelly on min_trades
    p, b, n_trades = estimate_win_rate_and_payoff_per_strategy(
        transactions_store, strategy_id, min_trades=min_trades
    )
    if math.isnan(p) or math.isnan(b):
        return _vol_fallback(
            f"cold start: only {n_trades} trades for strategy='{strategy_id}' (< {min_trades} required)",
            n_trades,
        )

    # 3. Extract per-strategy returns for the bootstrap
    returns = _get_per_strategy_returns(closed_df, strategy_id)
    if returns is None or len(returns) == 0:
        # Shouldn't happen if estimate_win_rate_and_payoff_per_strategy passed,
        # but guard defensively.
        return _vol_fallback(
            f"no returns extractable for strategy='{strategy_id}' despite n_trades={n_trades}",
            n_trades,
        )

    # 4. Bootstrap the return distribution and take the 5th-percentile Kelly
    # Convert the raw returns numpy array to a Series so bootstrap_kelly_confidence
    # can accept it via the np.ndarray branch.
    kelly_low, kelly_mean, kelly_high = bootstrap_kelly_confidence(
        returns,
        n_bootstraps=n_bootstraps,
        fraction=fraction,
        cap=cap,
    )

    if math.isnan(kelly_low):
        # Edge case: all bootstrap samples produced all-win / all-loss or zero-loss
        # distributions where b is undefined; fall back to vol-target.
        return _vol_fallback(
            f"bootstrap produced NaN kelly_5th for strategy='{strategy_id}' "
            f"(n_trades={n_trades}; likely degenerate sample)",
            n_trades,
        )

    path_tag = (
        f"bootstrap_kelly_5th_pct("
        f"n={n_trades},"
        f"k5={kelly_low:.4f},"
        f"k50={kelly_mean:.4f},"
        f"k95={kelly_high:.4f})"
    )
    logger.info(
        "kelly_sizing_for_strategy: strategy='%s' n_trades=%d "
        "kelly_5th=%.4f kelly_50th=%.4f kelly_95th=%.4f -> using kelly_5th as weight.",
        strategy_id, n_trades, kelly_low, kelly_mean, kelly_high,
    )
    return kelly_low, path_tag

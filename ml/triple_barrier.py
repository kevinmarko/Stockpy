"""
InvestYo Quant Platform - Triple-Barrier Labeling
==================================================
Implements Lopez de Prado's triple-barrier method (AFML Ch. 3) for generating
trade labels from price time-series.

Three barriers bound each event:
  * Upper (profit-take) : entry + pt_multiple * sigma * entry_price
  * Lower (stop-loss)   : entry - sl_multiple * sigma * entry_price
  * Vertical (timeout)  : entry_date + N trading days

sigma is the EWMA volatility computed from log-returns available ONLY at or
before the event timestamp (strictly point-in-time, no lookahead).

Public API
----------
get_volatility(close, span=100)  -> pd.Series   Daily EWMA vol, PIT.
cusum_filter(close, threshold)   -> pd.DatetimeIndex   Event sampler.
apply_triple_barrier(events, close, pt_sl_multiples, vertical_barrier_days)
    -> pd.DataFrame   Label each event: +1, -1, or 0.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("ML.TripleBarrier")


# ---------------------------------------------------------------------------
# 1. EWMA Volatility (strictly PIT)
# ---------------------------------------------------------------------------

def get_volatility(close: pd.Series, span: int = 100) -> pd.Series:
    """Daily EWMA volatility estimate from log-returns (strictly PIT).

    At each timestamp t the estimate uses log-returns up to and including t.
    The return at t is known after bar t closes; no future data is touched.

    Parameters
    ----------
    close : pd.Series
        Daily closing prices, DatetimeIndex.
    span : int
        EWM span (half-life ≈ span/2 days).

    Returns
    -------
    pd.Series
        Daily EWMA standard deviation of log-returns (same index as close).
        NaN for the first observation where no prior return exists.
    """
    if close.empty:
        return pd.Series(dtype=float)

    log_ret = np.log(close / close.shift(1))
    # adjust=False: recursive EWM (causal, no lookahead)
    ewm_std = log_ret.ewm(span=span, adjust=False, min_periods=2).std()
    return ewm_std


# ---------------------------------------------------------------------------
# 2. CUSUM Filter (sequential event sampler)
# ---------------------------------------------------------------------------

def cusum_filter(
    close: pd.Series,
    threshold: float,
) -> pd.DatetimeIndex:
    """Symmetric CUSUM event filter (Lopez de Prado AFML §17.1).

    Samples timestamps where the cumulative sum of signed log-returns first
    crosses ±threshold. On each crossing the cumulator resets to zero, so
    events are naturally separated in time (no burst of consecutive events).

    The CUSUM update is inherently sequential (each step depends on the
    previous state), so a scalar loop over timestamps is the correct
    implementation — this is not vectorisable without losing the sequential
    state. This loop iterates over dates (n_bars), not over a DataFrame's
    rows, so it does not violate the "no iterrows for DataFrame mutation"
    convention.

    Parameters
    ----------
    close : pd.Series
        Daily closing prices, DatetimeIndex.
    threshold : float
        Crossing level in log-return units. Typically set to the EWMA
        volatility estimate (one sigma) so the sampler adapts to volatility.

    Returns
    -------
    pd.DatetimeIndex
        Sorted timestamps of sampled events. Empty if none found.
    """
    if close.empty or threshold <= 0:
        raise ValueError("close must be non-empty and threshold must be > 0.")

    log_ret = np.log(close / close.shift(1)).dropna()

    s_pos = 0.0
    s_neg = 0.0
    events: list[pd.Timestamp] = []

    for dt, r in log_ret.items():
        s_pos = max(0.0, s_pos + r)
        s_neg = min(0.0, s_neg + r)
        if s_pos >= threshold:
            s_pos = 0.0
            events.append(dt)
        elif s_neg <= -threshold:
            s_neg = 0.0
            events.append(dt)

    return pd.DatetimeIndex(events)


# ---------------------------------------------------------------------------
# 3. Triple-Barrier Labeling
# ---------------------------------------------------------------------------

def apply_triple_barrier(
    events: pd.DatetimeIndex,
    close: pd.Series,
    pt_sl_multiples: Tuple[float, float] = (2.0, 1.0),
    vertical_barrier_days: int = 5,
    vol_span: int = 100,
) -> pd.DataFrame:
    """Apply triple-barrier labels to a set of event timestamps.

    For each event at time t₀:
      - sigma_t₀ is computed from ``get_volatility(close[:t₀], span=vol_span)``
        using ONLY prices up to and including t₀ (strictly no lookahead).
      - Upper barrier = entry_price * (1 + pt_multiple  * sigma_t₀)
      - Lower barrier = entry_price * (1 - sl_multiple  * sigma_t₀)
      - Vertical      = t₀ + vertical_barrier_days business days
      - We look for the FIRST touch in close[t₀+1 : vertical] (exclusive of t₀
        itself — we cannot exit the same bar we enter).

    Labels:
      +1  upper barrier hit first  (profit-take)
      -1  lower barrier hit first  (stop-loss)
       0  vertical barrier reached (timeout)

    Parameters
    ----------
    events : pd.DatetimeIndex
        Timestamps of trade entries (from ``cusum_filter`` or a signal).
    close : pd.Series
        Daily closing prices (full series). Must contain all event timestamps.
    pt_sl_multiples : (float, float)
        ``(profit_take_multiple, stop_loss_multiple)`` for barrier placement.
    vertical_barrier_days : int
        Number of business days for the vertical (timeout) barrier.
    vol_span : int
        EWM span passed to ``get_volatility``.

    Returns
    -------
    pd.DataFrame
        Indexed by event timestamp (t₀), columns:
        - ``t1``          : timestamp when a barrier was first touched
        - ``barrier_hit`` : ``"upper"``, ``"lower"``, or ``"vertical"``
        - ``label``       : +1, -1, or 0
        - ``entry``       : entry close price at t₀
        - ``upper_level`` : absolute price level of upper barrier
        - ``lower_level`` : absolute price level of lower barrier
    """
    if close.empty or len(events) == 0:
        return pd.DataFrame(columns=["t1", "barrier_hit", "label", "entry", "upper_level", "lower_level"])

    pt_mult, sl_mult = pt_sl_multiples
    biz_offset = pd.tseries.offsets.BDay(vertical_barrier_days)

    # Pre-compute PIT vol for the ENTIRE close series once.
    # vol[t] uses only returns up to t — no lookahead.
    vol_series = get_volatility(close, span=vol_span)

    records: list[dict] = []

    for t0 in events:
        # Require the event date to be in the close index
        if t0 not in close.index:
            logger.warning("apply_triple_barrier: event %s not in close index, skipping.", t0)
            continue

        entry = float(close[t0])
        sigma = vol_series.get(t0)

        if sigma is None or np.isnan(sigma) or sigma <= 0:
            logger.debug("apply_triple_barrier: no valid vol at %s, skipping.", t0)
            continue

        # Barriers are set in price space (sigma is a log-return fraction)
        upper_level = entry * (1.0 + pt_mult * sigma)
        lower_level = entry * (1.0 - sl_mult * sigma)
        t1_deadline = t0 + biz_offset

        # Future prices AFTER t0 (exclusive) up to the vertical barrier
        future = close.loc[(close.index > t0) & (close.index <= t1_deadline)]

        if future.empty:
            records.append({
                "t0": t0, "t1": t0, "barrier_hit": "vertical",
                "label": 0, "entry": entry,
                "upper_level": upper_level, "lower_level": lower_level,
            })
            continue

        # Find first touch times for upper and lower barriers
        touch: dict[str, pd.Timestamp] = {}
        upper_hits = future.index[future >= upper_level]
        lower_hits = future.index[future <= lower_level]
        if len(upper_hits) > 0:
            touch["upper"] = upper_hits[0]
        if len(lower_hits) > 0:
            touch["lower"] = lower_hits[0]

        if touch:
            # Earliest touch wins
            hit_side, hit_time = min(touch.items(), key=lambda kv: kv[1])
            label = 1 if hit_side == "upper" else -1
        else:
            # No barrier touched: vertical timeout
            hit_side = "vertical"
            hit_time = future.index[-1]
            label = 0

        records.append({
            "t0": t0, "t1": hit_time, "barrier_hit": hit_side,
            "label": label, "entry": entry,
            "upper_level": upper_level, "lower_level": lower_level,
        })

    if not records:
        return pd.DataFrame(columns=["t1", "barrier_hit", "label", "entry", "upper_level", "lower_level"])

    df = pd.DataFrame(records).set_index("t0")
    df.index.name = "t0"
    return df

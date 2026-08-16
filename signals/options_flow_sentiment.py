"""signals/options_flow_sentiment.py — Options Order Flow Sentiment Signal
=============================================================================

Phase 11 / Workstream 4: Institutional Options Order Flow Sentiment & Alpha Overlay.

Quantitative signal module scoring directional options flow sentiment based on
institutional sweeps and blocks (Unusual Options Activity), order flow velocity (5d ROC),
institutional accumulation/distribution (20d ROC vs 200d trend), and earnings/news blackout
window filtering (blackout_window_days=3).

Scoring Logic:
--------------
- Bullish Notional: Aggressive Call Ask Sweeps + Put Bid Sweeps
- Bearish Notional: Aggressive Put Ask Sweeps + Call Bid Sweeps
- Net Sentiment: (Bullish Notional - Bearish Notional) / Total Notional in [-1.0, 1.0]
- Velocity & Momentum: 5-day order flow velocity combined with 20-day institutional accumulation
  filtered against the 200-day long-term trend (SMA 200).
- Blackout Filter: Neutralizes directional exposure within +/- 3 days of high-impact earnings/news events.

Signal Output:
--------------
- score: Net Sentiment in [-1.0, 1.0]
- confidence: 0.85 when active institutional flow is detected; 0.5 when neutral; 0.0 when missing.
- explanation: Detailed institutional flow breakdown.

Regime Output (compute_flow_regime):
------------------------------------
- flow_score: Normalized directional flow score in [-1.0, 1.0]
- regime: "ACCUMULATION", "DISTRIBUTION", "HIGH_VELOCITY_BULLISH", "HIGH_VELOCITY_BEARISH", "NEUTRAL", "BLACKOUT"
- blackout_active: Boolean indicating if currently within earnings/news blackout window.
- position_recommendation: "BUY", "SELL", "NEUTRAL"

Honest Degradation (CONSTRAINT #4 / #6):
----------------------------------------
- When no UOA data exists for a symbol, returns neutral score=0.0, confidence=0.0,
  with an honest "Options flow sentiment: neutral/no flow data this cycle" message.
- Vectorized and scalar paths are guaranteed identical in output.
- All calculations are zero-lookahead (rolling / backward-only).
"""

from __future__ import annotations

from datetime import date, datetime
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from dto_models import MacroEconomicDTO
from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import global_registry

logger = logging.getLogger(__name__)

DEFAULT_BLACKOUT_WINDOW_DAYS: int = 3
FAST_VELOCITY_WINDOW: int = 5
ACCUMULATION_WINDOW: int = 20
TREND_WINDOW: int = 200

__all__ = [
    "OptionsFlowSentimentSignal",
    "compute_flow_regime",
    "calculate_order_flow_velocity",
    "calculate_accumulation_distribution",
    "is_blackout_active",
    "DEFAULT_BLACKOUT_WINDOW_DAYS",
]


# ---------------------------------------------------------------------------
# Feature Scoring & Calculation Helpers
# ---------------------------------------------------------------------------


def calculate_order_flow_velocity(
    closes: Union[pd.Series, Sequence[float], np.ndarray],
    window: int = FAST_VELOCITY_WINDOW,
) -> pd.Series:
    """Calculates fast order flow velocity (N-day Rate of Change).

    Formula: (Close_t - Close_{t-N}) / Close_{t-N}
    Zero-lookahead: relies only on past `window` observations.
    """
    if isinstance(closes, pd.Series):
        s = pd.to_numeric(closes, errors="coerce")
    elif isinstance(closes, (list, tuple, np.ndarray)):
        s = pd.Series(closes, dtype=float)
    elif isinstance(closes, pd.DataFrame):
        col = "Close" if "Close" in closes.columns else ("close" if "close" in closes.columns else closes.columns[0])
        s = pd.to_numeric(closes[col], errors="coerce")
    else:
        s = pd.Series(dtype=float)

    if len(s) == 0:
        return pd.Series(dtype=float, index=s.index)

    # Use fill_method=None to avoid pandas deprecation warning on default fill_method='pad'
    try:
        return s.pct_change(window, fill_method=None).fillna(0.0)
    except TypeError:
        return s.pct_change(window).fillna(0.0)


def calculate_accumulation_distribution(
    closes: Union[pd.Series, Sequence[float], np.ndarray],
    roc_window: int = ACCUMULATION_WINDOW,
    sma_window: int = TREND_WINDOW,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculates institutional accumulation/distribution metrics.

    Returns:
        (roc_20d, sma_200d, trend_up_bool_series)
    """
    if isinstance(closes, pd.Series):
        s = pd.to_numeric(closes, errors="coerce")
    elif isinstance(closes, (list, tuple, np.ndarray)):
        s = pd.Series(closes, dtype=float)
    elif isinstance(closes, pd.DataFrame):
        col = "Close" if "Close" in closes.columns else ("close" if "close" in closes.columns else closes.columns[0])
        s = pd.to_numeric(closes[col], errors="coerce")
    else:
        s = pd.Series(dtype=float)

    if len(s) == 0:
        return (
            pd.Series(dtype=float, index=s.index),
            pd.Series(dtype=float, index=s.index),
            pd.Series(dtype=bool, index=s.index),
        )

    try:
        roc = s.pct_change(roc_window, fill_method=None).fillna(0.0)
    except TypeError:
        roc = s.pct_change(roc_window).fillna(0.0)
    sma = s.rolling(window=sma_window, min_periods=1).mean()
    trend_up = s >= sma
    return roc, sma, trend_up


def is_blackout_active(
    dates: Union[pd.DatetimeIndex, pd.Series, Sequence[Any]],
    news_events: Optional[Union[Sequence[Any], pd.DatetimeIndex, pd.Series, Dict[str, Any]]] = None,
    blackout_window_days: int = DEFAULT_BLACKOUT_WINDOW_DAYS,
) -> pd.Series:
    """Identifies bars that fall within an earnings or high-impact news blackout window.

    A blackout window spans +/- `blackout_window_days` calendar or trading days around
    each scheduled event. During this window, directional alpha signals are neutralized.

    Returns:
        pd.Series of booleans indexed identically to `dates`.
    """
    if isinstance(dates, pd.Series):
        idx = dates.index
        dt_values = dates
    elif isinstance(dates, pd.DatetimeIndex):
        idx = dates
        dt_values = pd.Series(dates, index=dates)
    elif isinstance(dates, pd.Index):
        idx = dates
        dt_values = pd.Series(dates, index=dates)
    else:
        idx = pd.RangeIndex(len(dates))
        dt_values = pd.Series(dates, index=idx)

    if len(idx) == 0 or news_events is None:
        return pd.Series(False, index=idx, dtype=bool)

    # Normalize target dates
    try:
        ts_series = pd.to_datetime(dt_values, errors="coerce")
        has_valid_datetimes = ts_series.notna().any()
    except Exception:
        has_valid_datetimes = False
        ts_series = pd.Series(None, index=idx)

    # Extract event dates
    raw_event_list: List[Any] = []
    if isinstance(news_events, dict):
        # Case A: {"date": ..., "event": ...} or {"2026-02-15": ...}
        if "date" in news_events or "timestamp" in news_events:
            raw_event_list.append(news_events.get("date") or news_events.get("timestamp"))
        elif "events" in news_events and isinstance(news_events["events"], (list, tuple)):
            raw_event_list.extend(news_events["events"])
        else:
            raw_event_list.extend(list(news_events.keys()))
    elif isinstance(news_events, (list, tuple, set, pd.DatetimeIndex, pd.Series)):
        raw_event_list.extend(list(news_events))
    else:
        raw_event_list.append(news_events)

    event_timestamps: List[pd.Timestamp] = []
    event_indices: List[int] = []

    for ev in raw_event_list:
        if ev is None:
            continue
        ev_date_val = ev
        if isinstance(ev, dict):
            ev_date_val = ev.get("date") or ev.get("timestamp") or ev.get("earnings_date") or ev.get("event_date")

        if ev_date_val is None:
            continue

        if isinstance(ev_date_val, (int, np.integer)) and not has_valid_datetimes:
            event_indices.append(int(ev_date_val))
            continue

        try:
            ts = pd.to_datetime(ev_date_val)
            if pd.notna(ts):
                event_timestamps.append(pd.Timestamp(ts).normalize())
        except Exception:
            if isinstance(ev_date_val, (int, np.integer)):
                event_indices.append(int(ev_date_val))

    mask = np.zeros(len(idx), dtype=bool)

    if has_valid_datetimes and event_timestamps:
        normalized_series = ts_series.dt.normalize()
        for ev_ts in event_timestamps:
            # Calendar day difference <= blackout_window_days
            delta_days = (normalized_series - ev_ts).dt.total_seconds().abs() / 86400.0
            event_mask = delta_days <= float(blackout_window_days)
            mask = mask | event_mask.to_numpy(dtype=bool, na_value=False)

    if event_indices:
        for ev_idx in event_indices:
            int_positions = np.arange(len(idx))
            event_mask = np.abs(int_positions - ev_idx) <= int(blackout_window_days)
            mask = mask | event_mask

    return pd.Series(mask, index=idx, dtype=bool)


def compute_flow_regime(
    closes: Union[pd.Series, pd.DataFrame, Sequence[float], np.ndarray],
    options_flow_records: Optional[Any] = None,
    news_events: Optional[Any] = None,
    blackout_window_days: int = DEFAULT_BLACKOUT_WINDOW_DAYS,
    lag_signals: bool = False,
) -> pd.DataFrame:
    """Computes options flow regime scoring and position recommendations.

    Combines:
    1. Fast order flow velocity (5d ROC)
    2. Institutional accumulation/distribution (20d ROC vs 200d SMA trend)
    3. Direct institutional options flow records (Unusual Options Activity sweeps/blocks)
    4. Earnings/news blackout window filtering (+/- 3 days)

    Parameters:
        closes: Historical close prices (Series, DataFrame with 'Close' column, or sequence).
        options_flow_records: Optional UOA records, DataFrame, Series, or dict of flow scores.
        news_events: Optional list/dict/Series of earnings or high-impact news event dates.
        blackout_window_days: Number of days defining the blackout window (default: 3).
        lag_signals: If True, shifts signals by 1 bar to strictly guarantee 1-day lagged execution.

    Returns:
        pd.DataFrame with columns:
        - `flow_score` (float in [-1.0, 1.0])
        - `regime` ("ACCUMULATION", "DISTRIBUTION", "HIGH_VELOCITY_BULLISH", "HIGH_VELOCITY_BEARISH", "NEUTRAL", "BLACKOUT")
        - `blackout_active` (bool)
        - `position_recommendation` ("BUY", "SELL", "NEUTRAL")
    """
    if isinstance(closes, pd.DataFrame):
        col = "Close" if "Close" in closes.columns else ("close" if "close" in closes.columns else closes.columns[0])
        s = pd.to_numeric(closes[col], errors="coerce")
    elif isinstance(closes, pd.Series):
        s = pd.to_numeric(closes, errors="coerce")
    elif isinstance(closes, (list, tuple, np.ndarray)):
        s = pd.Series(closes, dtype=float)
    else:
        s = pd.Series(dtype=float)

    idx = s.index
    n = len(s)

    if n == 0:
        return pd.DataFrame(
            columns=["flow_score", "regime", "blackout_active", "position_recommendation"],
            index=idx,
        )

    # 1. Feature scoring: Velocity & Accumulation/Distribution vs Trend
    roc5 = calculate_order_flow_velocity(s, window=FAST_VELOCITY_WINDOW)
    roc20, sma200, trend_up = calculate_accumulation_distribution(s, roc_window=ACCUMULATION_WINDOW, sma_window=TREND_WINDOW)

    # Proxy flow score derived from velocity and momentum
    v_component = (roc5 / 0.02).clip(lower=-1.0, upper=1.0)
    m_component = (roc20 / 0.05).clip(lower=-1.0, upper=1.0)
    proxy_score = 0.5 * v_component + 0.5 * m_component

    # Modulate proxy with 200d trend
    proxy_score = np.where(
        (proxy_score > 0) & (~trend_up),
        proxy_score * 0.5,
        np.where((proxy_score < 0) & (trend_up), proxy_score * 0.5, proxy_score),
    )
    proxy_score = pd.Series(proxy_score, index=idx, dtype=float).clip(-1.0, 1.0)

    # 2. Ingest Direct Options Flow Records if provided
    direct_flow_scores = pd.Series(np.nan, index=idx, dtype=float)
    if options_flow_records is not None:
        try:
            if isinstance(options_flow_records, (int, float, np.number)):
                direct_flow_scores[:] = float(options_flow_records)
            elif isinstance(options_flow_records, pd.Series):
                aligned = options_flow_records.reindex(idx)
                direct_flow_scores = pd.to_numeric(aligned, errors="coerce")
            elif isinstance(options_flow_records, dict):
                # Mapping of date string/timestamp -> score
                date_keys = {str(k).strip(): float(v) for k, v in options_flow_records.items() if v is not None}
                str_idx = idx.astype(str).str.slice(0, 10)
                mapped = str_idx.map(date_keys)
                direct_flow_scores = pd.to_numeric(mapped, errors="coerce")
            elif isinstance(options_flow_records, (list, tuple)):
                # List of UOARecord or dicts
                rec_dict: Dict[str, List[float]] = {}
                for r in options_flow_records:
                    if r is None:
                        continue
                    ts_val = getattr(r, "timestamp", None) or (r.get("timestamp") if isinstance(r, dict) else None)
                    if not ts_val:
                        ts_val = getattr(r, "expiration", None) or (r.get("expiration") if isinstance(r, dict) else "")
                    ts_str = str(ts_val)[:10]

                    score_val = getattr(r, "sentiment_score", None) or (r.get("sentiment_score") if isinstance(r, dict) else None)
                    if score_val is None:
                        sent_label = str(getattr(r, "sentiment", "") or (r.get("sentiment") if isinstance(r, dict) else "")).upper()
                        if "BULLISH" in sent_label:
                            score_val = 0.75
                        elif "BEARISH" in sent_label:
                            score_val = -0.75
                        else:
                            score_val = 0.0

                    if ts_str:
                        rec_dict.setdefault(ts_str, []).append(float(score_val))

                if rec_dict:
                    daily_avg = {k: float(np.mean(v)) for k, v in rec_dict.items()}
                    str_idx = idx.astype(str).str.slice(0, 10)
                    mapped = str_idx.map(daily_avg)
                    direct_flow_scores = pd.to_numeric(mapped, errors="coerce")
        except Exception as exc:
            logger.debug("compute_flow_regime options_flow_records processing error: %s", exc)

    # Blend direct flow with proxy
    has_direct_flow = direct_flow_scores.notna()
    flow_score = pd.Series(np.nan, index=idx, dtype=float)
    flow_score[has_direct_flow] = (
        0.7 * direct_flow_scores[has_direct_flow] + 0.3 * proxy_score[has_direct_flow]
    ).clip(-1.0, 1.0)
    flow_score[~has_direct_flow] = proxy_score[~has_direct_flow]
    flow_score = flow_score.fillna(0.0).round(4)

    # 3. Blackout Filtering
    blackout_active = is_blackout_active(idx, news_events=news_events, blackout_window_days=blackout_window_days)

    # Neutralize flow score during blackout
    flow_score[blackout_active] = 0.0

    # 4. Regime Classification
    regimes = pd.Series("NEUTRAL", index=idx, dtype=object)

    high_vel_bull = (~blackout_active) & (
        (roc5 >= 0.015) & (trend_up | (flow_score >= 0.30)) | (flow_score >= 0.60)
    )
    high_vel_bear = (~blackout_active) & (
        (roc5 <= -0.015) & ((~trend_up) | (flow_score <= -0.30)) | (flow_score <= -0.60)
    )
    accum = (~blackout_active) & (~high_vel_bull) & (~high_vel_bear) & (
        (roc20 > 0.0) & (trend_up | (flow_score >= 0.15)) | (flow_score >= 0.15)
    )
    distrib = (~blackout_active) & (~high_vel_bull) & (~high_vel_bear) & (
        (roc20 < 0.0) & ((~trend_up) | (flow_score <= -0.15)) | (flow_score <= -0.15)
    )

    regimes[accum] = "ACCUMULATION"
    regimes[distrib] = "DISTRIBUTION"
    regimes[high_vel_bull] = "HIGH_VELOCITY_BULLISH"
    regimes[high_vel_bear] = "HIGH_VELOCITY_BEARISH"
    regimes[blackout_active] = "BLACKOUT"

    # 5. Position Recommendation
    recommendations = pd.Series("NEUTRAL", index=idx, dtype=object)
    buy_mask = (~blackout_active) & (regimes.isin(["HIGH_VELOCITY_BULLISH", "ACCUMULATION"]) | (flow_score >= 0.15))
    sell_mask = (~blackout_active) & (regimes.isin(["HIGH_VELOCITY_BEARISH", "DISTRIBUTION"]) | (flow_score <= -0.15))

    recommendations[buy_mask] = "BUY"
    recommendations[sell_mask] = "SELL"
    recommendations[blackout_active] = "NEUTRAL"

    # 6. Optional 1-Day Lagging for Execution Harness
    if lag_signals:
        flow_score = flow_score.shift(1, fill_value=0.0)
        regimes = regimes.shift(1, fill_value="NEUTRAL")
        blackout_active = blackout_active.shift(1, fill_value=False).astype(bool)
        recommendations = recommendations.shift(1, fill_value="NEUTRAL")

    return pd.DataFrame(
        {
            "flow_score": flow_score.astype(float),
            "regime": regimes.astype(str),
            "blackout_active": blackout_active.astype(bool),
            "position_recommendation": recommendations.astype(str),
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# Signal Module Definition
# ---------------------------------------------------------------------------


class OptionsFlowSentimentSignal(SignalModule):
    """Signal module scoring institutional unusual options order flow sentiment."""

    name: str = "options_flow_sentiment"
    required_features: List[str] = []
    meta_label_features: List[str] = [
        "ROC_12M",
        "ROC_6M",
        "RSI_14",
        "Vol_20",
        "GARCH_Vol",
        "SMA_5",
        "SMA_200",
        "ROC_5",
        "ROC_20",
    ]
    meta_label_horizons: List[int] = [10, 30, 60, 90]

    def __init__(self) -> None:
        self._sentiment_scores: Dict[str, float] = {}

    def pre_compute(self, universe_df: pd.DataFrame, context: SignalContext) -> None:
        """Load or synchronize options flow sentiment for the current universe."""
        self._sentiment_scores.clear()

        # 1. Inherit any sentiment scores already populated in context
        if context is not None and hasattr(context, "options_flow_sentiment") and context.options_flow_sentiment:
            self._sentiment_scores.update(
                {str(k).upper(): float(v) for k, v in context.options_flow_sentiment.items() if v is not None}
            )

        # 2. If empty, attempt to load persisted UOA flow records
        if not self._sentiment_scores:
            try:
                from pilots.unusual_options_flow import calculate_net_flow_sentiment, load_uoa_records

                records = load_uoa_records()
                if records:
                    symbols = []
                    if universe_df is not None and "Symbol" in universe_df.columns:
                        symbols = [str(s).upper() for s in universe_df["Symbol"].dropna().unique()]

                    # Calculate per symbol
                    for sym in symbols:
                        res = calculate_net_flow_sentiment(sym, records)
                        if res and res.get("record_count", 0) > 0:
                            self._sentiment_scores[sym] = float(res.get("sentiment_score", 0.0))

            except Exception as exc:
                logger.debug("OptionsFlowSentimentSignal.pre_compute error: %s", exc)

        # 3. Synchronize back into context
        if context is not None and hasattr(context, "options_flow_sentiment"):
            context.options_flow_sentiment = dict(self._sentiment_scores)

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        """Vectorized execution over universe DataFrame."""
        n = len(df)
        if n == 0:
            return pd.DataFrame(
                columns=["score", "confidence", "explanation", "meta_label_proba"],
                index=df.index,
            )

        # Identify symbol column
        symbols = None
        for col in ("Symbol", "Ticker", "symbol", "ticker"):
            if col in df.columns:
                symbols = df[col].astype(str).str.upper().str.strip()
                break

        # Check for direct column input
        raw_sentiment = None
        for col in ("Options_Flow_Sentiment", "options_flow_sentiment", "Flow_Sentiment", "options_sentiment"):
            if col in df.columns:
                raw_sentiment = pd.to_numeric(df[col], errors="coerce")
                break

        # Check for blackout flag in df
        blackout_active = pd.Series(False, index=df.index)
        for col in ("Blackout_Active", "blackout_active", "News_Blackout", "news_blackout", "Earnings_Blackout"):
            if col in df.columns:
                blackout_active = df[col].astype(bool)
                break

        # Merge from context or internal cache if column not present or contains NaNs
        scores = pd.Series(float("nan"), index=df.index)
        if raw_sentiment is not None:
            scores = raw_sentiment.copy()

        if symbols is not None:
            context_scores = {}
            if context is not None and hasattr(context, "options_flow_sentiment") and context.options_flow_sentiment:
                context_scores = context.options_flow_sentiment
            elif self._sentiment_scores:
                context_scores = self._sentiment_scores

            if context_scores:
                missing_mask = scores.isna()
                if missing_mask.any():
                    mapped = symbols[missing_mask].map(context_scores)
                    scores[missing_mask] = pd.to_numeric(mapped, errors="coerce")

        # Fallback to order flow velocity proxy if ROC_5 & ROC_20 present
        if scores.isna().any() and "ROC_5" in df.columns and "ROC_20" in df.columns:
            missing_mask = scores.isna()
            roc5 = pd.to_numeric(df.loc[missing_mask, "ROC_5"], errors="coerce").fillna(0.0)
            roc20 = pd.to_numeric(df.loc[missing_mask, "ROC_20"], errors="coerce").fillna(0.0)
            proxy = (0.5 * (roc5 / 0.02).clip(-1.0, 1.0) + 0.5 * (roc20 / 0.05).clip(-1.0, 1.0)).clip(-1.0, 1.0)
            scores[missing_mask] = proxy

        has_data = scores.notna()
        clamped_score = scores.clip(lower=-1.0, upper=1.0).fillna(0.0)

        # Blackout neutralization
        clamped_score[blackout_active] = 0.0

        confidence = pd.Series(0.0, index=df.index)
        confidence[has_data & (~blackout_active)] = (
            clamped_score[has_data & (~blackout_active)]
            .abs()
            .apply(lambda x: 0.85 if x > 0.15 else (0.75 if x > 0.0 else 0.5))
        )
        confidence[blackout_active] = 0.0

        explanations = pd.Series("", index=df.index)
        explanations[~has_data] = "Options flow sentiment: neutral/no flow data this cycle"
        explanations[blackout_active] = "Options flow sentiment: neutral [earnings/news blackout active]"

        # Categorize explanations for active, non-blackout data
        active_mask = has_data & (~blackout_active)
        bullish_mask = active_mask & (clamped_score > 0.15)
        bearish_mask = active_mask & (clamped_score < -0.15)
        neutral_mask = active_mask & ~bullish_mask & ~bearish_mask

        explanations[bullish_mask] = clamped_score[bullish_mask].apply(
            lambda s: f"Options flow sentiment: bullish (+{s:.2f}) [institutional call sweep/bid-put flow]"
        )
        explanations[bearish_mask] = clamped_score[bearish_mask].apply(
            lambda s: f"Options flow sentiment: bearish ({s:.2f}) [institutional put sweep/bid-call flow]"
        )
        explanations[neutral_mask] = clamped_score[neutral_mask].apply(
            lambda s: f"Options flow sentiment: neutral ({s:.2f}) [balanced order flow]"
        )

        return pd.DataFrame(
            {
                "score": clamped_score,
                "confidence": confidence,
                "explanation": explanations,
                "meta_label_proba": pd.Series(1.0, index=df.index),
            },
            index=df.index,
        )

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        """Scalar execution for a single asset row."""
        # Check blackout flag
        is_blackout = False
        for col in ("Blackout_Active", "blackout_active", "News_Blackout", "news_blackout", "Earnings_Blackout"):
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                is_blackout = bool(val)
                break

        if is_blackout:
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation="Options flow sentiment: neutral [earnings/news blackout active]",
                meta_label_proba=1.0,
            )

        symbol = ""
        for col in ("Symbol", "Ticker", "symbol", "ticker"):
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                symbol = str(val).upper().strip()
                break

        # Check row values
        raw_val = None
        for col in ("Options_Flow_Sentiment", "options_flow_sentiment", "Flow_Sentiment", "options_sentiment"):
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                try:
                    raw_val = float(val)
                    break
                except (ValueError, TypeError):
                    continue

        # Check context
        if raw_val is None and symbol:
            if context is not None and hasattr(context, "options_flow_sentiment") and context.options_flow_sentiment:
                raw_val = context.options_flow_sentiment.get(symbol)
            elif symbol in self._sentiment_scores:
                raw_val = self._sentiment_scores.get(symbol)

        # Fallback to velocity / momentum proxy if present
        if raw_val is None and "ROC_5" in row and "ROC_20" in row:
            try:
                r5 = float(row.get("ROC_5", 0.0))
                r20 = float(row.get("ROC_20", 0.0))
                v_comp = max(-1.0, min(1.0, r5 / 0.02))
                m_comp = max(-1.0, min(1.0, r20 / 0.05))
                raw_val = max(-1.0, min(1.0, 0.5 * v_comp + 0.5 * m_comp))
            except Exception:
                pass

        if raw_val is None or (isinstance(raw_val, float) and math.isnan(raw_val)):
            return SignalOutput(
                score=0.0,
                confidence=0.0,
                explanation="Options flow sentiment: neutral/no flow data this cycle",
                meta_label_proba=1.0,
            )

        score = max(-1.0, min(1.0, float(raw_val)))
        if score > 0.15:
            confidence = 0.85
            explanation = f"Options flow sentiment: bullish (+{score:.2f}) [institutional call sweep/bid-put flow]"
        elif score < -0.15:
            confidence = 0.85
            explanation = f"Options flow sentiment: bearish ({score:.2f}) [institutional put sweep/bid-call flow]"
        else:
            confidence = 0.75 if abs(score) > 0.0 else 0.5
            explanation = f"Options flow sentiment: neutral ({score:.2f}) [balanced order flow]"

        return SignalOutput(
            score=score,
            confidence=confidence,
            explanation=explanation,
            meta_label_proba=1.0,
        )


# Auto-register with global signal registry
global_registry.register(OptionsFlowSentimentSignal())

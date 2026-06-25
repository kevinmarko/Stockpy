"""
InvestYo Quant Platform - Antonacci Dual Momentum Allocator
============================================================
Reference: Gary Antonacci (2012, 2014), "Dual Momentum Investing."
Portfolio: http://www.optimalmomentum.com/

STRATEGY OVERVIEW
-----------------
Dual Momentum uses *two* momentum filters applied sequentially each month:

  1. Absolute (Time-Series) Momentum  –  Is SPY's 12-month total return
     above T-Bill yield?  If not → go defensive (BIL / AGG).

  2. Relative (Cross-Sectional) Momentum – Among risky assets (SPY vs VEU),
     which had the better 12-month return?  Rotate there.

The canonical implementation (Antonacci 2014) uses:
  - SPY  : S&P 500 (US equities)
  - VEU  : Vanguard FTSE All-World ex-US (International equities)
  - BIL  : SPDR Bloomberg 1-3 Month T-Bill (cash proxy)

LOOKAHEAD PREVENTION
--------------------
Every return is calculated using `.shift(lookback)` BEFORE the comparison so
that the *decision* on day T is based on information available through day T-1.
This matches real-world end-of-month rebalancing on the next open.

CONSTRAINTS
-----------
- All data fetched via yfinance (free, open-source).
- No iterrows() or inplace DataFrame mutation over price series.
- Returns NaN, not a fabricated value, when history is insufficient.
- The allocator produces a dict of {ticker: weight} whose weights sum to 1.0.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level defaults
# ---------------------------------------------------------------------------
_PROXY_US        = "SPY"
_PROXY_INTL      = "VEU"
_PROXY_SAFE      = "BIL"
_LOOKBACK_MONTHS = 12


class DualMomentumAllocator:
    """
    Portfolio-level allocator implementing Antonacci's Dual Momentum.

    Parameters
    ----------
    risky_assets : list[str]
        The two competing risky ETFs. Default: ["SPY", "VEU"].
    safe_asset : str
        The defensive holding when absolute momentum is negative. Default: "BIL".
    lookback_months : int
        Rolling return window in calendar months. Default: 12.
    min_history_days : int
        Minimum trading-day history required before a decision is made.
    """

    def __init__(
        self,
        risky_assets: Optional[List[str]] = None,
        safe_asset: str = _PROXY_SAFE,
        lookback_months: int = _LOOKBACK_MONTHS,
        min_history_days: int = 252,
    ) -> None:
        self.risky_assets: List[str] = risky_assets or [_PROXY_US, _PROXY_INTL]
        self.safe_asset: str = safe_asset
        self.lookback_months: int = lookback_months
        self.min_history_days: int = min_history_days
        self._all_tickers: List[str] = list(set(self.risky_assets + [self.safe_asset]))

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    def decide(
        self,
        as_of_date: date,
        price_data: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Dict[str, float]:
        """
        Return the portfolio allocation as of ``as_of_date``.

        Decision logic (strictly lookahead-free):
          - Prices through ``as_of_date - 1 business day`` are used.
          - 12-month (~252 trading-day) total return is computed per proxy.
          - Absolute momentum filter: if best risky return <= safe return -> safe.
          - Relative momentum filter: 100% to whichever risky asset won.

        Parameters
        ----------
        as_of_date : date
            The calendar date on which the allocation decision is made.
        price_data : dict[str, pd.DataFrame], optional
            Pre-loaded OHLCV DataFrames keyed by ticker. Fetched if None.

        Returns
        -------
        dict[str, float]
            A weight dictionary summing to 1.0, e.g. ``{"SPY": 1.0}``.
        """
        prices = self._get_close_series(as_of_date, price_data)
        if prices is None:
            logger.warning(
                "DualMomentumAllocator: insufficient price data for %s; "
                "defaulting to safe asset (%s).",
                as_of_date,
                self.safe_asset,
            )
            return {self.safe_asset: 1.0}

        returns_12m = self._compute_lookback_returns(prices, as_of_date)
        if returns_12m is None:
            logger.warning(
                "DualMomentumAllocator: could not compute 12M returns for %s; "
                "defaulting to safe asset (%s).",
                as_of_date,
                self.safe_asset,
            )
            return {self.safe_asset: 1.0}

        return self._apply_dual_momentum_rule(returns_12m)

    def backtest(
        self,
        start: date,
        end: date,
        price_data: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> pd.DataFrame:
        """
        Walk-forward monthly backtester.

        Generates end-of-month allocation decisions between ``start`` and
        ``end`` and returns a DataFrame of monthly returns, allocations, and
        cumulative wealth.

        Parameters
        ----------
        start, end : date
            Inclusive date range for the simulation.
        price_data : dict[str, pd.DataFrame], optional
            Pre-loaded price history. Fetched if not provided.

        Returns
        -------
        pd.DataFrame
            Columns: date, allocation, return, cumulative_return.
        """
        if price_data is None:
            price_data = self._fetch_history(start, end)

        # Build {ticker: Close Series} for return computation
        close_series: Dict[str, pd.Series] = {}
        for ticker, df in price_data.items():
            if "Close" in df.columns and not df.empty:
                close_series[ticker] = df["Close"].sort_index()

        rebalance_dates = self._monthly_eom_dates(start, end)
        records = []
        prev_date: Optional[date] = None
        current_alloc: Dict[str, float] = {self.safe_asset: 1.0}

        for rebal_date in rebalance_dates:
            try:
                alloc = self.decide(rebal_date, price_data=price_data)
            except Exception as exc:
                logger.warning(
                    "Error computing allocation for %s: %s; holding previous.",
                    rebal_date, exc,
                )
                alloc = current_alloc

            if prev_date is not None:
                port_ret = self._portfolio_return(
                    current_alloc, close_series, prev_date, rebal_date
                )
                records.append({
                    "date": rebal_date,
                    "allocation": _alloc_label(current_alloc),
                    "return": port_ret,
                })

            current_alloc = alloc
            prev_date = rebal_date

        if not records:
            return pd.DataFrame(columns=["date", "allocation", "return", "cumulative_return"])

        result = pd.DataFrame(records)
        result["cumulative_return"] = (1.0 + result["return"]).cumprod() - 1.0
        return result

    # ------------------------------------------------------------------
    # PRIVATE HELPERS
    # ------------------------------------------------------------------

    def _get_close_series(
        self,
        as_of_date: date,
        price_data: Optional[Dict[str, pd.DataFrame]],
    ) -> Optional[Dict[str, pd.Series]]:
        """Return {ticker: Close series up to as_of_date - 1 bday} or None."""
        cutoff = pd.Timestamp(as_of_date) - pd.offsets.BDay(1)

        if price_data is not None:
            series: Dict[str, pd.Series] = {}
            for ticker in self._all_tickers:
                df = price_data.get(ticker)
                if df is None or df.empty or "Close" not in df.columns:
                    continue
                s = df["Close"].sort_index()
                # Normalise timezone so slicing works uniformly
                if s.index.tz is not None:
                    s.index = s.index.tz_localize(None)
                s = s[s.index <= cutoff]
                if len(s) >= self.min_history_days:
                    series[ticker] = s
            # Need at least one risky asset + safe to make a decision
            return series if len(series) >= 2 else None

        # Fallback: live yfinance fetch for the specific as_of_date window
        fetch_start = as_of_date - timedelta(days=self.lookback_months * 31 + 60)
        result: Dict[str, pd.Series] = {}
        for ticker in self._all_tickers:
            try:
                t = yf.Ticker(ticker)
                df = t.history(
                    start=fetch_start.isoformat(),
                    end=(as_of_date + timedelta(days=1)).isoformat(),
                )
                if df.empty or "Close" not in df.columns:
                    continue
                s = df["Close"].sort_index()
                if s.index.tz is not None:
                    s.index = s.index.tz_localize(None)
                s = s[s.index <= cutoff]
                if len(s) >= self.min_history_days:
                    result[ticker] = s
            except Exception as exc:
                logger.error("Failed to fetch %s: %s", ticker, exc)
        return result if len(result) >= 2 else None

    def _fetch_history(self, start: date, end: date) -> Dict[str, pd.DataFrame]:
        """Batch-fetch full OHLCV history for backtesting."""
        fetch_start = start - timedelta(days=self.lookback_months * 31 + 60)
        result: Dict[str, pd.DataFrame] = {}
        for ticker in self._all_tickers:
            try:
                t = yf.Ticker(ticker)
                df = t.history(
                    start=fetch_start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                )
                if not df.empty:
                    if df.index.tz is not None:
                        df.index = df.index.tz_localize(None)
                    result[ticker] = df
            except Exception as exc:
                logger.error("Failed to fetch history for %s: %s", ticker, exc)
        return result

    def _compute_lookback_returns(
        self,
        prices: Dict[str, pd.Series],
        as_of_date: date,
    ) -> Optional[Dict[str, float]]:
        """
        Compute rolling lookback return for each ticker, strictly lookahead-free.

        Vectorized: uses pd.Series.shift(lookback) so that at position i the
        reference price is prices[i - lookback].  The decision date uses only
        the last index value at or before as_of_date - 1 business day.
        """
        cutoff = pd.Timestamp(as_of_date) - pd.offsets.BDay(1)
        # Convert to ~trading days (21 trading days per calendar month)
        lookback = self.lookback_months * 21
        results: Dict[str, float] = {}

        for ticker, s in prices.items():
            s_sorted = s.sort_index()
            if len(s_sorted) <= lookback:
                continue
            # Fully vectorized rate-of-change: no iterrows
            shifted = s_sorted.shift(lookback)
            roc = (s_sorted - shifted) / shifted
            roc_sliced = roc[roc.index <= cutoff].dropna()
            if roc_sliced.empty:
                continue
            results[ticker] = float(roc_sliced.iloc[-1])

        return results if results else None

    def _apply_dual_momentum_rule(
        self, returns_12m: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Core Antonacci logic:
          Step 1 – Absolute momentum: best risky return vs. safe return.
          Step 2 – Relative momentum: which risky asset had the higher return?
        """
        safe_ret = returns_12m.get(self.safe_asset, 0.0)

        risky_rets = {
            t: returns_12m[t]
            for t in self.risky_assets
            if t in returns_12m
        }

        if not risky_rets:
            logger.info("No risky asset return data – defaulting to safe asset.")
            return {self.safe_asset: 1.0}

        best_risky_ticker = max(risky_rets, key=lambda t: risky_rets[t])
        best_risky_ret = risky_rets[best_risky_ticker]

        # Absolute momentum filter
        if best_risky_ret <= safe_ret:
            logger.info(
                "Absolute momentum NEGATIVE (best risky=%.4f <= safe=%.4f) -> %s",
                best_risky_ret, safe_ret, self.safe_asset,
            )
            return {self.safe_asset: 1.0}

        # Relative momentum filter
        logger.info(
            "Absolute momentum POSITIVE -> Relative winner: %s (%.4f)",
            best_risky_ticker, best_risky_ret,
        )
        return {best_risky_ticker: 1.0}

    @staticmethod
    def _monthly_eom_dates(start: date, end: date) -> List[date]:
        """Generate end-of-month business dates between start and end inclusive."""
        rng = pd.date_range(start=start, end=end, freq="BME")
        return [ts.date() for ts in rng if start <= ts.date() <= end]

    @staticmethod
    def _portfolio_return(
        alloc: Dict[str, float],
        close_series: Dict[str, pd.Series],
        from_date: date,
        to_date: date,
    ) -> float:
        """
        Compute weighted portfolio return between two calendar dates.

        Uses the last available price on or before each boundary date.
        Returns NaN when data is unavailable.
        """
        from_ts = pd.Timestamp(from_date)
        to_ts = pd.Timestamp(to_date)
        total_ret = 0.0

        for ticker, weight in alloc.items():
            if weight == 0.0:
                continue
            s = close_series.get(ticker)
            if s is None or s.empty:
                return float("nan")
            before = s[s.index <= from_ts]
            after = s[s.index <= to_ts]
            if before.empty or after.empty:
                return float("nan")
            p_from = float(before.iloc[-1])
            p_to = float(after.iloc[-1])
            if p_from <= 0.0:
                return float("nan")
            total_ret += weight * ((p_to - p_from) / p_from)

        return total_ret


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _alloc_label(alloc: Dict[str, float]) -> str:
    """Human-readable allocation label, e.g. 'SPY(100%)'."""
    parts = [f"{t}({w * 100:.0f}%)" for t, w in alloc.items() if w > 0]
    return ", ".join(parts) if parts else "NONE"

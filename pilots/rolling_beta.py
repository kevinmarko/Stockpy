"""pilots/rolling_beta.py — rolling beta vs SPY for the PWA SymbolDetail screen.
================================================================================

Surfaces a TIME SERIES of a symbol's beta vs SPY (as opposed to the single
point-in-time static ``Beta`` column in ``config.COLUMN_SCHEMA``, sourced from
``data/yahoo_fundamentals.py``) for the mobile SymbolDetail screen
(``GET /symbols/{ticker}/rolling-beta``). This is the same concept the old
Streamlit GUI rendered on-demand in its Analytics tab
(``gui/panels/analytics.py::_render_rolling_beta_chart``), reimplemented here
rather than imported — the math is a few lines of pandas, not worth routing
through ``processing_engine`` (which ``api/pilots_api.py`` is AST-guarded
against importing; see ``tests/test_pilots_api.py::
test_pilots_api_never_imports_heavy_engines``).

Formula (identical to ``processing_engine.calculate_rolling_beta``)
--------------------------------------------------------------------
``Cov(returns, spy_returns) / Var(spy_returns)`` over a rolling window of daily
returns, inner-joined by date (dates missing from either side are dropped,
NEVER forward-filled — CONSTRAINT #4). Lookahead-free by construction: each
value at row *i* uses only rows ``[i-window+1, i]``.

Design invariants (identical to the rest of the Pilots read layer):

* **Data source** — ``data.historical_store.HistoricalStore(readonly=True)
  .get_bars(...)``, the same incrementally-cached bars source the rest of the
  platform already uses (see ``GET /portfolio/attribution``'s
  ``_attribution_returns_df`` in ``api/pilots_api.py`` for the precedent).
  ``HistoricalStore`` and ``pandas`` are imported LAZILY inside the function
  body (this codebase's established convention for ``HistoricalStore`` — see
  ``processing_engine.py`` / ``macro_engine.py`` /
  ``data/robinhood_portfolio.py``), so this module's own top-level imports
  stay minimal. Like ``pilots/attribution.py``, this module is deliberately
  NOT on the ultra-light "stdlib + settings only" allowlist the pure JSON
  readers (``pilots/scoring.py``, ``pilots/strategy_matrix.py``, ...) promise —
  the rolling-covariance math genuinely needs vectorized pandas, which cannot
  be reproduced in pure stdlib.
* **Honesty (CONSTRAINT #4)** — insufficient overlapping history (fewer than
  ``window`` rows after the inner join) degrades to an empty ``series`` + an
  honest ``reason`` string, never a fabricated flat line or forward-filled
  value. The first ``window`` rows of any computed series are dropped (they
  would be NaN) rather than surfaced as a fabricated 0.0/None-shaped point.
* **Never raises (CONSTRAINT #6)** — every failure (missing bars, a
  ``HistoricalStore``/provider error, a malformed frame) degrades to the empty
  view.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["rolling_beta_view"]

_DEFAULT_WINDOW = 60
_MIN_WINDOW = 5
_MAX_WINDOW = 252


def _clamp_window(window: Any) -> int:
    """Coerce to an int and clamp to a sane range; never raises."""
    try:
        w = int(window)
    except (TypeError, ValueError):
        w = _DEFAULT_WINDOW
    return max(_MIN_WINDOW, min(_MAX_WINDOW, w))


def _finite_or_none(value: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (NaN/inf → ``null``, CONSTRAINT #4)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _empty_view(symbol: str, window: int, reason: str) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "window": int(window),
        "series": [],
        "reason": reason,
    }


def rolling_beta_view(
    symbol: str,
    window: int = _DEFAULT_WINDOW,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return ``{symbol, window, series: [{date, beta}], reason}``.

    ``series`` is empty + ``reason`` is an honest explanation when: the ticker
    is blank, there are no cached bars for the symbol or SPY yet, or the
    date-aligned overlap between the two has fewer than ``window`` rows.
    ``reason`` is ``None`` on a normal hit. Never raises (CONSTRAINT #6).

    ``db_path`` is test-only dependency injection (defaults to
    ``HistoricalStore``'s own default ``"quant_platform.db"``); production
    callers never pass it.
    """
    sym = str(symbol or "").upper().strip()
    win = _clamp_window(window)
    if not sym:
        return _empty_view(sym, win, "No ticker supplied.")

    try:
        from data.historical_store import HistoricalStore

        kwargs: Dict[str, Any] = {"readonly": True}
        if db_path is not None:
            kwargs["db_path"] = db_path
        store = HistoricalStore(**kwargs)

        lookback_days = max(504, win * 3)
        price_df = store.get_bars(sym, lookback_days=lookback_days)
        spy_df = store.get_bars("SPY", lookback_days=lookback_days)
    except Exception as exc:  # noqa: BLE001 — dead-letter (CONSTRAINT #6)
        logger.debug("rolling_beta_view(%s): get_bars failed: %s", sym, exc)
        return _empty_view(sym, win, f"Price history unavailable for {sym} right now.")

    if price_df is None or price_df.empty or "Close" not in price_df.columns:
        return _empty_view(sym, win, f"No cached price history for {sym} yet.")
    if spy_df is None or spy_df.empty or "Close" not in spy_df.columns:
        return _empty_view(sym, win, "No cached SPY price history yet.")

    try:
        import pandas as pd

        aligned = pd.concat(
            [price_df["Close"].rename("ticker"), spy_df["Close"].rename("spy")],
            axis=1,
            join="inner",  # dates missing from either side dropped, never forward-filled
        ).sort_index()

        if len(aligned) < win:
            return _empty_view(
                sym, win,
                f"Not enough overlapping history to compute a {win}-day rolling "
                f"beta for {sym} yet ({len(aligned)} overlapping trading days, "
                f"need at least {win}).",
            )

        returns = aligned["ticker"].pct_change()
        spy_returns = aligned["spy"].pct_change()
        rolling_cov = returns.rolling(win).cov(spy_returns)
        rolling_var = spy_returns.rolling(win).var()
        beta = (rolling_cov / rolling_var).dropna()

        series: List[Dict[str, Any]] = []
        for ts, value in beta.items():
            v = _finite_or_none(value)
            if v is None:
                continue
            date_str = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            series.append({"date": date_str, "beta": v})

        if not series:
            return _empty_view(
                sym, win,
                f"Not enough overlapping history to compute a {win}-day rolling "
                f"beta for {sym} yet.",
            )

        return {"symbol": sym, "window": win, "series": series, "reason": None}
    except Exception as exc:  # noqa: BLE001 — dead-letter (CONSTRAINT #6)
        logger.debug("rolling_beta_view(%s): compute failed: %s", sym, exc)
        return _empty_view(sym, win, "Rolling beta computation failed.")

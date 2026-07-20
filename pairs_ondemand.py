"""pairs_ondemand.py — on-demand (operator-triggered, synchronous) pairs-
trading compute for the Pilots PWA's "recompute with custom parameters"
actions on ``PairsRadar.tsx`` (webapp porting backlog item 8a).

Distinct from ``reporting/pairs_snapshot.py``, which is the PIPELINE-side
writer: gated behind ``settings.PAIRS_SNAPSHOT_ENABLED``, always operates over
the operator's configured universe, and persists ``output/pairs.json`` for the
read-only ``GET /pairs`` endpoint. This module is invoked directly from an
HTTP POST handler (``api/data_api.py``) with an arbitrary, REQUEST-scoped
symbol list or named pair — it never reads or writes ``output/pairs.json``,
and it is intentionally NOT gated by ``PAIRS_SNAPSHOT_ENABLED`` (that flag
controls only the automatic pipeline artifact, not an explicit operator
action).

Row shape mirrors ``reporting.pairs_snapshot.write_pairs_snapshot``'s
persisted ``pairs[]`` entries 1:1 (``ticker1``/``ticker2``/``p_value``/
``half_life``/``z_score``/``beta``/``rolling_p``/``position``/``signal``) so
the webapp can reuse its existing pair-row rendering (``PairRow`` in
``webapp/src/api/types.ts``) for on-demand results too. This is the THIRD
independent local port of the ``_signal_label``/``_align_closes`` display
logic in this codebase (``gui/panels/pairs.py``, ``reporting/pairs_snapshot.py``,
here) — an accepted pattern in this repo for small, stable, engine-adjacent
display helpers (see ``docs/VALIDATION_STRATEGY_FIX_LOG.md`` /
``project_webapp_porting_backlog.md`` item 7 for precedent), rather than
importing a private (leading-underscore) helper across an unrelated module
boundary.

Advisory only (CONSTRAINT: no order code — a signal label is displayed, never
acted on). Honesty (CONSTRAINT #4): every numeric leaf is ``None`` (never
fabricated) when the underlying primitive is unavailable. Dead-letter
resilient (CONSTRAINT #6): one bad symbol/pair degrades honestly; nothing here
raises.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

__all__ = [
    "ANALYZE_LOOKBACK_DAYS",
    "ENTRY_THRESHOLD",
    "EXIT_THRESHOLD",
    "STOP_LOSS_THRESHOLD",
    "ADF_EXIT_THRESHOLD",
    "MIN_HALF_LIFE_DAYS",
    "MAX_HALF_LIFE_DAYS",
    "SCAN_MIN_SYMBOLS",
    "SCAN_MAX_SYMBOLS",
    "analyze_pair",
    "scan_pairs",
]

# Canonical pairs-trading rule thresholds — mirror the defaults in
# signals.pairs_trading.generate_pairs_signals, gui/panels/pairs.py's module
# constants, and reporting/pairs_snapshot.py's _ENTRY/_STOP/_EXIT/_ADF_EXIT.
ENTRY_THRESHOLD = 2.0
EXIT_THRESHOLD = 0.0
STOP_LOSS_THRESHOLD = 4.0
ADF_EXIT_THRESHOLD = 0.10
MIN_HALF_LIFE_DAYS = 5
MAX_HALF_LIFE_DAYS = 60
ANALYZE_LOOKBACK_DAYS = 252

# On-demand full-scan cap. The cointegration scan is O(n^2) Engle-Granger
# tests plus a Kalman-filtered signal per surviving candidate, so this stays
# well below "whole universe" scale (the gated pipeline writer's job). 2 is
# the minimum meaningful scan (one candidate pair); 15 symbols is 105 pairwise
# tests — still comfortably synchronous for a single HTTP request.
SCAN_MIN_SYMBOLS = 2
SCAN_MAX_SYMBOLS = 15


def _finite_or_none(value: Any) -> Optional[float]:
    """Coerce to a finite float, else ``None`` (CONSTRAINT #4: never NaN/inf)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _signal_label(position: Any, z: Any, rolling_p: Any) -> str:
    """Advisory display label for the current spread state.

    ADVISORY DISPLAY ONLY — returns a *string* describing the current pairs
    state; never places, modifies, or proposes a trade. Mirrors
    ``reporting/pairs_snapshot.py::_signal_label`` /
    ``gui/panels/pairs.py::_signal_label``.
    """
    zf, pf = _finite_or_none(z), _finite_or_none(rolling_p)
    if zf is None or pf is None:
        return "No signal — insufficient history"
    if pf > ADF_EXIT_THRESHOLD:
        return "No signal — not cointegrated (ADF p>0.10)"
    abs_z = abs(zf)
    pos = _finite_or_none(position) or 0.0
    if pos > 0:
        if abs_z > STOP_LOSS_THRESHOLD:
            return "STOP — |z|>4 (exit long spread)"
        if zf >= EXIT_THRESHOLD:
            return "Exit — z-score crossed 0"
        return "Hold LONG spread (long Y / short X)"
    if pos < 0:
        if abs_z > STOP_LOSS_THRESHOLD:
            return "STOP — |z|>4 (exit short spread)"
        if zf <= EXIT_THRESHOLD:
            return "Exit — z-score crossed 0"
        return "Hold SHORT spread (short Y / long X)"
    if abs_z > ENTRY_THRESHOLD:
        return "ENTER SHORT spread" if zf > 0 else "ENTER LONG spread"
    return "Flat — no entry (|z|<2)"


def _align_closes(series_by_symbol: Dict[str, pd.Series]) -> pd.DataFrame:
    """Inner-join ``{symbol: Close series}`` on common dates; drop any-NaN rows.

    Empty/``None`` series are dropped. Returns an empty DataFrame when fewer
    than two usable series remain. Never raises.
    """
    frame = {
        s: ser
        for s, ser in series_by_symbol.items()
        if ser is not None and len(ser) > 0
    }
    if len(frame) < 2:
        return pd.DataFrame()
    return pd.DataFrame(frame).dropna(how="any")


def _fetch_close(
    provider: Any, symbol: str, lookback_days: int = ANALYZE_LOOKBACK_DAYS
) -> pd.Series:
    """Fetch one symbol's Close series via the market-data provider.

    Returns an empty Series (never raises) so one dead symbol can't abort a
    scan/analyze call — callers drop empties in :func:`_align_closes`.
    """
    try:
        bars = provider.get_intraday_bars(symbol, lookback_days=lookback_days)
        if bars is None or bars.empty or "Close" not in bars.columns:
            return pd.Series(dtype=float, name=symbol)
        close = bars["Close"].copy()
        close.name = symbol
        return close
    except Exception as exc:  # noqa: BLE001 - dead-letter per symbol
        logger.debug("pairs_ondemand: close fetch failed for %s: %s", symbol, exc)
        return pd.Series(dtype=float, name=symbol)


def analyze_pair(symbol_y: str, symbol_x: str, provider: Any) -> Dict[str, Any]:
    """Cointegration + current spread-signal state for ONE named pair.

    Ports ``gui/panels/pairs.py::_render_analyze_mode`` to a stateless,
    synchronous call. Returns a dict shaped like one
    ``reporting.pairs_snapshot`` pair row (``ticker1``/``ticker2``/``p_value``/
    ``half_life``/``z_score``/``beta``/``rolling_p``/``position``/``signal``)
    PLUS a ``found``/``reason`` honesty envelope, ``half_life_tradeable``, and
    a ``z_score_series`` for the frontend's own mini chart (the server has no
    rendering surface of its own). Never raises (CONSTRAINT #6).
    """
    sym_y = str(symbol_y or "").upper().strip()
    sym_x = str(symbol_x or "").upper().strip()
    base: Dict[str, Any] = {
        "ticker1": sym_y,
        "ticker2": sym_x,
        "p_value": None,
        "half_life": None,
        "half_life_tradeable": None,
        "z_score": None,
        "beta": None,
        "rolling_p": None,
        "position": None,
        "signal": "No signal — insufficient history",
        "z_score_series": [],
        "aligned_bars": 0,
        "found": False,
        "reason": None,
    }
    if not sym_y or not sym_x:
        base["reason"] = "Both Symbol Y and Symbol X are required."
        return base
    if sym_y == sym_x:
        base["reason"] = "Symbol Y and Symbol X must be different tickers."
        return base

    y_close = _fetch_close(provider, sym_y)
    x_close = _fetch_close(provider, sym_x)
    aligned = _align_closes({sym_y: y_close, sym_x: x_close})
    if aligned.shape[0] < 60 or aligned.shape[1] < 2:
        base["reason"] = (
            f"Insufficient aligned history for {sym_y}/{sym_x} — need ~60+ "
            "overlapping daily bars. One or both symbols may be unavailable "
            "from the provider."
        )
        return base

    try:
        from signals.pairs_trading import generate_pairs_signals

        signals_df = generate_pairs_signals(aligned[sym_y], aligned[sym_x])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pairs_ondemand: signal generation failed for %s/%s: %s", sym_y, sym_x, exc
        )
        base["reason"] = (
            f"Could not generate a pairs signal for {sym_y}/{sym_x} "
            "(a degenerate or too-short pair)."
        )
        return base

    if signals_df is None or signals_df.empty:
        base["reason"] = "No signal rows produced for this pair."
        return base

    base["aligned_bars"] = int(aligned.shape[0])
    last = signals_df.iloc[-1]
    base["z_score"] = _finite_or_none(last.get("z_score"))
    base["beta"] = _finite_or_none(last.get("beta"))
    base["rolling_p"] = _finite_or_none(last.get("rolling_p"))
    base["position"] = _finite_or_none(last.get("position"))

    half_life: Optional[float] = None
    try:
        from pairs.cointegration import compute_half_life

        half_life = _finite_or_none(compute_half_life(signals_df["spread"]))
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "pairs_ondemand: half-life failed for %s/%s: %s", sym_y, sym_x, exc
        )
    base["half_life"] = half_life
    base["half_life_tradeable"] = (
        half_life is not None and MIN_HALF_LIFE_DAYS <= half_life <= MAX_HALF_LIFE_DAYS
    )

    base["signal"] = _signal_label(
        last.get("position"), last.get("z_score"), last.get("rolling_p")
    )

    z = signals_df["z_score"].dropna()
    series: List[Dict[str, Any]] = []
    for idx, val in z.items():
        fv = _finite_or_none(val)
        if fv is None:
            continue
        date_str = idx.isoformat() if hasattr(idx, "isoformat") else str(idx)
        series.append({"date": date_str, "z_score": fv})
    base["z_score_series"] = series
    base["found"] = True
    return base


def scan_pairs(
    symbols: List[str],
    provider: Any,
    *,
    p_threshold: float = 0.05,
    max_pairs: int = 20,
) -> Dict[str, Any]:
    """Cointegration scan across an operator-chosen, request-scoped symbol list.

    Ports ``gui/panels/pairs.py::_render_scan_mode``. Returns ``{"pairs": [...],
    "missing": [...], "aligned_symbols": N, "aligned_bars": N, "reason":
    Optional[str]}``; ``pairs`` rows match ``reporting.pairs_snapshot``'s
    persisted shape exactly. A symbol that fails to fetch is dead-lettered
    into ``missing`` rather than aborting the whole scan. Never raises
    (CONSTRAINT #6).
    """
    syms = sorted({str(s).upper().strip() for s in symbols if str(s or "").strip()})
    series_by_symbol: Dict[str, pd.Series] = {
        sym: _fetch_close(provider, sym) for sym in syms
    }

    fetched = [s for s, ser in series_by_symbol.items() if not ser.empty]
    missing = [s for s in syms if s not in fetched]

    price_df = _align_closes(series_by_symbol)
    if price_df.shape[1] < 2 or price_df.shape[0] < 60:
        return {
            "pairs": [],
            "missing": missing,
            "aligned_symbols": int(price_df.shape[1]) if not price_df.empty else 0,
            "aligned_bars": int(price_df.shape[0]) if not price_df.empty else 0,
            "reason": (
                "Insufficient aligned history to scan — need at least two "
                "symbols with ~60+ overlapping daily bars after the inner-join."
            ),
        }

    try:
        from pairs.cointegration import find_cointegrated_pairs

        candidates = find_cointegrated_pairs(
            price_df, p_threshold=p_threshold, max_pairs=max_pairs
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("pairs_ondemand: cointegration scan failed: %s", exc)
        return {
            "pairs": [],
            "missing": missing,
            "aligned_symbols": int(price_df.shape[1]),
            "aligned_bars": int(price_df.shape[0]),
            "reason": f"Cointegration scan failed: {exc}",
        }

    try:
        from signals.pairs_trading import generate_pairs_signals
    except Exception as exc:  # noqa: BLE001
        logger.debug("pairs_ondemand: generate_pairs_signals unavailable: %s", exc)
        generate_pairs_signals = None  # type: ignore[assignment]

    rows: List[Dict[str, Any]] = []
    for pair in candidates or []:
        t1 = str(getattr(pair, "ticker1", "") or "").upper()
        t2 = str(getattr(pair, "ticker2", "") or "").upper()
        row: Dict[str, Any] = {
            "ticker1": t1,
            "ticker2": t2,
            "p_value": _finite_or_none(getattr(pair, "p_value", None)),
            "half_life": _finite_or_none(getattr(pair, "half_life", None)),
            "z_score": None,
            "beta": None,
            "rolling_p": None,
            "position": None,
            "signal": "No signal — insufficient history",
        }
        if generate_pairs_signals is not None:
            try:
                sig = generate_pairs_signals(price_df[t1], price_df[t2])
                if sig is not None and not sig.empty:
                    last = sig.iloc[-1]
                    row["z_score"] = _finite_or_none(last.get("z_score"))
                    row["beta"] = _finite_or_none(last.get("beta"))
                    row["rolling_p"] = _finite_or_none(last.get("rolling_p"))
                    row["position"] = _finite_or_none(last.get("position"))
                    row["signal"] = _signal_label(
                        last.get("position"), last.get("z_score"), last.get("rolling_p")
                    )
            except Exception as exc:  # noqa: BLE001 - one bad pair never aborts
                logger.debug(
                    "pairs_ondemand: signal failed for %s/%s: %s", t1, t2, exc
                )
        rows.append(row)

    return {
        "pairs": rows,
        "missing": missing,
        "aligned_symbols": int(price_df.shape[1]),
        "aligned_bars": int(price_df.shape[0]),
        "reason": None
        if rows
        else (
            "No cointegrated pairs found for this universe at the selected "
            "p-value with a 5–60 day half-life. Statistical arbitrage "
            "candidates are genuinely rare — this is an honest empty result."
        ),
    }

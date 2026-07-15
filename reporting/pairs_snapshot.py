"""reporting/pairs_snapshot.py — persist the pairs-trading radar to JSON.
========================================================================

The pairs-trading engine (``pairs/cointegration.py`` +
``signals/pairs_trading.py``) is computed live in the Streamlit GUI
(``gui/panels/pairs.py``) and persisted nowhere. It is heavy (an O(n²)
Engle-Granger cointegration scan + per-pair Kalman filtering + rolling ADF unit
-root tests over ``statsmodels``), so the mobile PWA cannot compute it on a
request path — and the API's read-path purity means it should not.

This module persists the pairs radar the same way the state snapshot works: a
pipeline-side writer (heavy imports fine here in ``reporting/``, not the
AST-guarded API) ranks the cointegrated candidate pairs and captures each pair's
CURRENT spread state (z-score, hedge ratio, half-life, rolling ADF p-value, and
the advisory signal label), writing ``output/pairs.json``. The PWA reads that
artifact through the pure ``pilots.pairs`` reader.

Invariants:

* **Opt-in** — gated behind ``settings.PAIRS_SNAPSHOT_ENABLED`` (default
  ``False``); returns ``None`` when disabled. The O(n²) scan is expensive, so it
  only runs when explicitly enabled.
* **Advisory only** — captures a *display* label; NO order code.
* **Honesty (CONSTRAINT #4)** — NaN leaves → ``null``, never fabricated.
* **Dead-letter resilient (CONSTRAINT #6)** — one bad pair is skipped; a total
  failure writes nothing and never raises.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["write_pairs_snapshot", "PAIRS_FILENAME"]

PAIRS_FILENAME = "pairs.json"

# Engine convention thresholds (mirror signals/pairs_trading defaults + the GUI).
_ENTRY = 2.0
_STOP = 4.0
_EXIT = 0.0
_ADF_EXIT = 0.10


def _finite_or_none(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _signal_label(position: Any, z: Any, rolling_p: Any) -> str:
    """Advisory display label for the current spread state (never a trade)."""
    zf, pf = _finite_or_none(z), _finite_or_none(rolling_p)
    if zf is None or pf is None:
        return "No signal — insufficient history"
    if pf > _ADF_EXIT:
        return "No signal — not cointegrated (ADF p>0.10)"
    abs_z = abs(zf)
    pos = _finite_or_none(position) or 0.0
    if pos > 0:
        if abs_z > _STOP:
            return "STOP — |z|>4 (exit long spread)"
        if zf >= _EXIT:
            return "Exit — z-score crossed 0"
        return "Hold LONG spread (long Y / short X)"
    if pos < 0:
        if abs_z > _STOP:
            return "STOP — |z|>4 (exit short spread)"
        if zf <= _EXIT:
            return "Exit — z-score crossed 0"
        return "Hold SHORT spread (short Y / long X)"
    if abs_z > _ENTRY:
        return "ENTER SHORT spread" if zf > 0 else "ENTER LONG spread"
    return "Flat — no entry (|z|<2)"


def _align_closes(series_by_symbol: Dict[str, pd.Series]) -> pd.DataFrame:
    """Inner-join ``{symbol: Close series}`` on common dates; drop NaN rows."""
    frame = {s: ser for s, ser in series_by_symbol.items() if ser is not None and len(ser) > 0}
    if len(frame) < 2:
        return pd.DataFrame()
    df = pd.DataFrame(frame).dropna(how="any")
    return df


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_pairs_snapshot(
    symbols: List[str],
    *,
    max_pairs: Optional[int] = None,
    lookback_days: int = 252,
    provider: Any = None,
    output_dir: Optional[Path] = None,
) -> Optional[str]:
    """Rank cointegrated pairs over ``symbols`` + capture each pair's current
    spread state, persisting ``output/pairs.json``.

    Returns the written path (str), or ``None`` when disabled / nothing usable.
    Never raises (CONSTRAINT #6).
    """
    if not getattr(settings, "PAIRS_SNAPSHOT_ENABLED", False):
        return None
    syms = sorted({str(s).upper().strip() for s in (symbols or []) if str(s).strip()})
    if len(syms) < 2:
        return None
    if max_pairs is None:
        max_pairs = getattr(settings, "PAIRS_SNAPSHOT_MAX_PAIRS", 20)

    try:
        from data.market_data import get_provider
        from pairs.cointegration import find_cointegrated_pairs
        from signals.pairs_trading import generate_pairs_signals
    except Exception as exc:  # noqa: BLE001
        logger.warning("pairs snapshot writer unavailable: %s", exc)
        return None

    if provider is None:
        try:
            provider = get_provider()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pairs snapshot: provider construction failed: %s", exc)
            return None

    # Fetch each symbol's Close series (dead-letter per symbol).
    series_by_symbol: Dict[str, pd.Series] = {}
    for symbol in syms:
        try:
            bars = provider.get_intraday_bars(symbol, lookback_days=lookback_days)
            if bars is None or bars.empty or "Close" not in bars.columns:
                continue
            close = bars["Close"].copy()
            close.name = symbol
            series_by_symbol[symbol] = close
        except Exception as exc:  # noqa: BLE001
            logger.debug("pairs snapshot: close fetch failed for %s: %s", symbol, exc)

    price_df = _align_closes(series_by_symbol)
    if price_df.shape[1] < 2 or price_df.shape[0] < 60:
        logger.info("pairs snapshot: insufficient aligned history; nothing written.")
        return None

    try:
        candidates = find_cointegrated_pairs(price_df, max_pairs=int(max_pairs))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pairs snapshot: cointegration scan failed: %s", exc)
        return None

    pairs_out: List[Dict[str, Any]] = []
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
        except Exception as exc:  # noqa: BLE001 — one bad pair never aborts
            logger.debug("pairs snapshot: signal failed for %s/%s: %s", t1, t2, exc)
        pairs_out.append(row)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "universe": syms,
        "pairs": pairs_out,
    }
    out_dir = Path(output_dir) if output_dir is not None else settings.OUTPUT_DIR
    path = out_dir / PAIRS_FILENAME
    try:
        _atomic_write(path, payload)
        logger.info("Wrote pairs radar (%d pairs) → %s", len(pairs_out), path)
        return str(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pairs snapshot write failed: %s", exc)
        return None

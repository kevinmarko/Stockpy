"""reporting/options_snapshot.py — persist the options premium matrix to JSON.
==============================================================================

The options premium-selling matrix is computed live in the Streamlit GUI
(``gui/panels/options_matrix.py`` → ``technical_options_engine.build_premium_directive``),
but it is NOT persisted anywhere, so the mobile PWA — whose API
(``api/pilots_api.py``) is AST-guarded against importing
``technical_options_engine`` — has no way to read it.

This module closes that gap the same way the state snapshot works: a
pipeline-side writer (heavy imports are fine here — this lives in ``reporting/``,
not the AST-guarded API) computes each symbol's premium directive and persists
the hydrated matrix to ``output/options_matrix.json``. The PWA then reads that
artifact through the pure ``pilots.options`` reader.

Invariants:

* **Opt-in** — gated behind ``settings.OPTIONS_MATRIX_ENABLED`` (default
  ``False``); returns ``None`` (writes nothing) when disabled, so fresh clones /
  CI are unaffected.
* **Honesty (CONSTRAINT #4)** — every uncomputable numeric leaf is persisted as
  ``null`` (NaN → ``None``), never a fabricated ``0.0``.
* **Dead-letter resilient (CONSTRAINT #6)** — one bad symbol degrades to an
  error-stub row; a total failure writes nothing and never raises into the
  pipeline.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["write_options_matrix", "OPTIONS_MATRIX_FILENAME"]

OPTIONS_MATRIX_FILENAME = "options_matrix.json"


class _MacroProxy:
    """MacroEconomicDTO-shaped stub (``.vix`` / ``.market_regime`` only) so the
    directive's VRP regime gate (VIX ≥ 30 ∨ CREDIT EVENT) fires identically to the
    live GUI path. Mirrors ``gui/panels/options_matrix.py::_MacroProxy``."""

    def __init__(self, vix: float, market_regime: str):
        self.vix = vix
        self.market_regime = market_regime


def _json_safe(value: Any) -> Any:
    """Recursively null-shape a directive for honest JSON (NaN/inf → ``None``)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    """Write-then-rename JSON (mirrors ``execution/kill_switch.py::activate``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_options_matrix(
    symbols: List[str],
    *,
    vix: float = 15.0,
    market_regime: str = "RISK ON",
    target_dte: int = 30,
    provider: Any = None,
    output_dir: Optional[Path] = None,
) -> Optional[str]:
    """Compute + persist the premium-directive matrix for ``symbols``.

    Returns the written path (str) on success, or ``None`` when the feature is
    disabled or nothing could be written. Fetches quotes/bars via the shared
    market-data provider (like the GUI panel) — the provider's short-TTL bars
    cache means an in-cycle refetch is cheap. Never raises (CONSTRAINT #6).
    """
    if not getattr(settings, "OPTIONS_MATRIX_ENABLED", False):
        return None
    syms = [str(s).upper().strip() for s in (symbols or []) if str(s).strip()]
    if not syms:
        return None

    try:
        from technical_options_engine import build_premium_directive
        from data.market_data import get_provider, MarketDataError
    except Exception as exc:  # noqa: BLE001 — engine/provider unavailable
        logger.warning("options matrix writer unavailable: %s", exc)
        return None

    if provider is None:
        try:
            provider = get_provider()
        except Exception as exc:  # noqa: BLE001
            logger.warning("options matrix: provider construction failed: %s", exc)
            return None

    macro_proxy = _MacroProxy(float(vix), str(market_regime))
    directives: List[Dict[str, Any]] = []
    for symbol in syms:
        try:
            quote = provider.get_latest_quote(symbol)
            bars = provider.get_intraday_bars(symbol, lookback_days=252)
            row = build_premium_directive(
                symbol,
                bars,
                spot_price=float(quote.price),
                is_stale=bool(quote.is_stale),
                target_dte=int(target_dte),
                macro_dto=macro_proxy,
                vrp=None,  # VRP needs an options chain — skip that gate here
            )
            directives.append(_json_safe(row))
        except MarketDataError as exc:  # noqa: PERF203
            logger.debug("options matrix: market data error for %s: %s", symbol, exc)
            directives.append(
                {"Symbol": symbol, "Strategy": None, "Action": None,
                 "Integrity_OK": False, "Integrity_Issues": [str(exc)]}
            )
        except Exception as exc:  # noqa: BLE001 — one bad symbol never aborts
            logger.debug("options matrix failed for %s: %s", symbol, exc)
            directives.append(
                {"Symbol": symbol, "Strategy": None, "Action": None,
                 "Integrity_OK": False, "Integrity_Issues": [str(exc)]}
            )

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_dte": int(target_dte),
        "vix": float(vix) if math.isfinite(float(vix)) else None,
        "market_regime": str(market_regime),
        "directives": directives,
    }
    out_dir = Path(output_dir) if output_dir is not None else settings.OUTPUT_DIR
    path = out_dir / OPTIONS_MATRIX_FILENAME
    try:
        _atomic_write(path, payload)
        logger.info("Wrote options matrix (%d directives) → %s", len(directives), path)
        return str(path)
    except Exception as exc:  # noqa: BLE001 — write failure is non-fatal
        logger.warning("options matrix write failed: %s", exc)
        return None

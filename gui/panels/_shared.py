"""
gui/panels/_shared.py
=====================
Shared constants and utility helpers used by every panel module.  Extracted
from ``gui/panels/__init__.py`` so individual tab modules can import exactly
what they need without pulling in the full panels namespace.

**NOT in this file**: ``load_state_snapshot`` and ``_load_state_snapshot_cached``
remain in ``gui/panels/__init__.py`` so the test suite can monkeypatch them on
the ``gui.panels`` namespace without chasing module-reference indirection.

Imports here must remain a strict subset of stdlib + third-party + ``settings``.
No imports from other ``gui.panels.*`` sub-modules — this file is the base of
the dependency tree.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from settings import settings

logger = logging.getLogger("gui.panels")

# Repo root: gui/panels/_shared.py → gui/panels/ → gui/ → repo/
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# GICS 11 sector seed for the Brinson-Fachler attribution editor.
# ---------------------------------------------------------------------------
GICS_SECTORS = (
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
)

# Column names used by the BF editor table AND EvaluationEngine's compat path.
_BF_EDITOR_COLUMNS = (
    "Sector",
    "Portfolio Weight (%)",
    "Portfolio Return (%)",
    "Benchmark Weight (%)",
    "Benchmark Return (%)",
)

# ===========================================================================
# File-backed loaders and utility helpers
# NOTE: load_state_snapshot / _load_state_snapshot_cached intentionally stay
# in gui/panels/__init__.py so tests can monkeypatch them on the gui.panels
# namespace without chasing module-reference indirection.
# ===========================================================================


def load_block_log(n: int = 100) -> List[dict]:
    """Load the most recent ``n`` risk-gate block entries (newest first)."""
    log_path = settings.OUTPUT_DIR / "risk_gate_blocks.jsonl"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        rows: List[dict] = []
        for line in lines[-n:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(rows))
    except Exception:
        return []


def _kill_switch():
    """Construct a GlobalKillSwitch pointed at the configured output dir."""
    from execution.kill_switch import GlobalKillSwitch

    return GlobalKillSwitch(sentinel_file=settings.OUTPUT_DIR / "KILL_SWITCH")


def _signal_symbols(snap: dict) -> List[str]:
    """Active symbols from the last snapshot, falling back to DEFAULT_TICKERS."""
    syms = [s.get("symbol") for s in snap.get("signals", []) if s.get("symbol")]
    if syms:
        return syms
    return list(settings.DEFAULT_TICKERS)


def _watchlist_symbols() -> List[str]:
    """Tickers from the ``WATCHLIST`` env var or ``watchlist.txt``."""
    import os

    env_val = os.environ.get("WATCHLIST", "").strip()
    if env_val:
        return [t.strip().upper() for t in env_val.split(",") if t.strip()]

    wl = _REPO_ROOT / "watchlist.txt"
    if wl.exists():
        try:
            return [
                line.strip().upper()
                for line in wl.read_text().splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("watchlist.txt read failed: %s", exc)
    return []


def _held_symbols() -> List[str]:
    """Robinhood-held tickers from the daily JSON cache (no live login)."""
    cache = _REPO_ROOT / "cache" / "account_snapshot.json"
    if not cache.exists():
        return []
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        positions = data.get("positions", {})
        return sorted(positions.keys())
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_snapshot.json read failed: %s", exc)
        return []


def _active_symbols(snap: dict) -> List[str]:
    """Union of held positions, watchlist, and last pipeline signals.

    Falls back to :data:`settings.DEFAULT_TICKERS` only when all three
    sources are empty.
    """
    universe: List[str] = []
    seen: set = set()
    for src in (_held_symbols(), _watchlist_symbols(), _signal_symbols(snap)):
        for s in src:
            if s not in seen:
                seen.add(s)
                universe.append(s)
    if not universe:
        return list(settings.DEFAULT_TICKERS)
    return universe

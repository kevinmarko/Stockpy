"""Pure, snapshot-driven scoring for Stockpy "Pilots".

Every function here is a **read helper**: it derives a Pilot's live holdings,
sector allocation, and recent signal-change trades from already-persisted state
(``output/state_snapshot.json`` and the rotated history under
``output/history/``). It NEVER imports a heavy calculation engine
(``strategy_engine`` / ``processing_engine`` / ``forecasting_engine`` / …), so it
is cheap and safe to import on the API read path. The only project imports are
``pilots.catalog`` (the ``Pilot`` type), ``settings`` (``SIGNAL_WEIGHTS`` /
``PILOTS_TOP_N``), and the rotation-filename convention from
``scripts.snapshot_diff`` (a pure-stdlib module).

The core arithmetic (Wave 1 handoff)
------------------------------------
Each snapshot ``signals[]`` entry carries
``score_components: dict[module -> weighted_contribution]`` where::

    weighted_contribution = raw_score[-1, 1] * settings.SIGNAL_WEIGHTS[module]

so a module's raw score is backed out by dividing::

    raw_score[module] = score_components[module] / SIGNAL_WEIGHTS[module]

**Divide-by-zero guard** — ``regime_multiplier`` (and any future module) can carry
a ``SIGNAL_WEIGHTS`` weight of ``0.0``; those are skipped entirely (their raw
score is undefined, never fabricated). A module absent from a symbol's
``score_components`` contributes exactly ``0`` (never fabricated).

A Pilot re-blends those raw scores under its own weight vector::

    blended[symbol] = sum( raw_score[m] * pilot.weights[m]  for m in pilot.weights )

Dead-letter resilience (CONSTRAINT #6): every public function degrades to an
empty result / ``None`` on missing or malformed input and never raises.
No fabricated metrics (CONSTRAINT #4): a missing price/sector/score stays absent
rather than being invented.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pilots.catalog import Pilot
from settings import settings

# Reuse the exact rotation-filename convention the pipeline writes under
# ``output/history/`` (see ``scripts/snapshot_diff.py``) so this module stays in
# lockstep with the writer without re-typing the format string.
from scripts.snapshot_diff import (
    SNAPSHOT_FILENAME_PREFIX,
    SNAPSHOT_FILENAME_SUFFIX,
)

logger = logging.getLogger(__name__)

__all__ = [
    "load_snapshot",
    "pilot_holdings",
    "sector_allocation",
    "pilot_trades",
]

# Default on-disk locations (relative to the process CWD, matching the rest of
# the pipeline's ``output/`` convention).
_DEFAULT_SNAPSHOT_PATH = "output/state_snapshot.json"
_DEFAULT_HISTORY_DIR = "output/history"

# Below this absolute weight change a same-symbol day-over-day move is treated as
# noise rather than a REWEIGHT event.
_REWEIGHT_EPSILON = 1e-4

_UNKNOWN_SECTOR = "Unknown"


# ---------------------------------------------------------------------------
# Snapshot I/O
# ---------------------------------------------------------------------------

def load_snapshot(path: Optional[str] = None) -> Optional[dict]:
    """Load ``output/state_snapshot.json`` (or ``path``) as a dict.

    Returns ``None`` when the file is missing, empty, unreadable, or does not
    parse to a JSON object — never raises (CONSTRAINT #6).
    """
    target = Path(path) if path else Path(_DEFAULT_SNAPSHOT_PATH)
    return _read_json_object(target)


def _read_json_object(target: Path) -> Optional[dict]:
    """Read ``target`` and return a dict, or ``None`` on any failure."""
    try:
        if not target.exists() or not target.is_file():
            return None
        raw = target.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        data = json.loads(raw)
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError
        logger.debug("Snapshot %s unreadable: %s", target, exc)
        return None
    if not isinstance(data, dict):
        logger.debug("Snapshot %s is not a JSON object — ignoring.", target)
        return None
    return data


# ---------------------------------------------------------------------------
# Raw-score back-out + Pilot blend
# ---------------------------------------------------------------------------

def _coerce_float(value: Any) -> Optional[float]:
    """Coerce ``value`` to a finite float, or ``None`` when not possible."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def _raw_score(components: Dict[str, Any], module: str) -> float:
    """Back out a module's raw ``[-1, 1]`` score from its weighted contribution.

    ``raw = score_components[module] / SIGNAL_WEIGHTS[module]``, with two guards:

    * a module whose ``SIGNAL_WEIGHTS`` weight is ``0.0`` (or is absent from
      ``SIGNAL_WEIGHTS`` entirely) is un-backoutable → contributes ``0``;
    * a module absent from this symbol's ``score_components`` contributes ``0``
      (never fabricated).
    """
    weight = settings.SIGNAL_WEIGHTS.get(module)
    if not weight:  # None or 0.0 → skip (divide-by-zero / not a real module)
        return 0.0
    contrib = _coerce_float(components.get(module))
    if contrib is None:
        return 0.0
    try:
        return contrib / float(weight)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _blended_score(sig: Dict[str, Any], pilot: Pilot) -> float:
    """Compute a Pilot's blended score for one ``signals[]`` entry."""
    components = sig.get("score_components")
    if not isinstance(components, dict):
        return 0.0
    total = 0.0
    for module, pilot_weight in pilot.weights.items():
        pw = _coerce_float(pilot_weight)
        if not pw:  # None or 0.0 pilot weight contributes nothing
            continue
        total += _raw_score(components, module) * pw
    return total


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------

def pilot_holdings(
    pilot: Pilot,
    snapshot: dict,
    top_n: Optional[int] = None,
) -> List[dict]:
    """Compute a Pilot's target holdings from a single snapshot.

    For every ``signals[]`` entry the Pilot's blended score is computed; only
    symbols with a **strictly positive** blend are kept (a Pilot never
    "recommends" a name its blend scores at or below zero). The kept names are
    sorted by score descending, truncated to ``top_n`` (default
    ``settings.PILOTS_TOP_N``), and the survivors' scores are normalized into a
    target ``weight`` that sums to ``1.0`` — so the advertised weights always
    describe exactly the holdings shown.

    Returns a list of ``{"symbol", "weight", "score", "price", "sector"}`` dicts
    (empty when the snapshot has no usable signals). Never raises.
    """
    if top_n is None:
        top_n = settings.PILOTS_TOP_N
    if not isinstance(snapshot, dict):
        return []
    signals = snapshot.get("signals") or []
    if not isinstance(signals, list):
        return []

    scored: List[dict] = []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        symbol = str(sig.get("symbol") or "").upper().strip()
        if not symbol:
            continue
        score = _blended_score(sig, pilot)
        if score <= 0.0:
            continue
        sector = str(sig.get("sector") or "").strip()
        scored.append({
            "symbol": symbol,
            "score": score,
            "price": _coerce_float(sig.get("price")),
            "sector": sector,
        })

    if not scored:
        return []

    # Sort by score desc, tie-break by symbol asc for determinism.
    scored.sort(key=lambda d: (-d["score"], d["symbol"]))

    # Truncate to top_n BEFORE normalizing so weights sum to 1.0 over exactly
    # the holdings surfaced (a negative top_n is treated as "no cap").
    if top_n is not None and top_n >= 0:
        scored = scored[:top_n]
    if not scored:
        return []

    total = sum(d["score"] for d in scored)
    for d in scored:
        d["weight"] = (d["score"] / total) if total > 0 else 0.0

    # Stable, explicit key order for the returned dicts.
    return [
        {
            "symbol": d["symbol"],
            "weight": d["weight"],
            "score": d["score"],
            "price": d["price"],
            "sector": d["sector"],
        }
        for d in scored
    ]


# ---------------------------------------------------------------------------
# Sector allocation
# ---------------------------------------------------------------------------

def sector_allocation(holdings: List[dict]) -> List[dict]:
    """Group ``holdings`` by sector and sum their weights.

    Returns ``[{"sector", "weight"}]`` sorted by weight descending (sector name
    ascending as a tie-break). A missing/blank ``sector`` is bucketed under
    ``"Unknown"``. Never raises.
    """
    buckets: Dict[str, float] = {}
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        sector = str(h.get("sector") or "").strip() or _UNKNOWN_SECTOR
        weight = _coerce_float(h.get("weight")) or 0.0
        buckets[sector] = buckets.get(sector, 0.0) + weight

    out = [{"sector": s, "weight": w} for s, w in buckets.items()]
    out.sort(key=lambda d: (-d["weight"], d["sector"]))
    return out


# ---------------------------------------------------------------------------
# Recent trades (day-over-day holdings diff across rotated history)
# ---------------------------------------------------------------------------

def _parse_history_timestamp(name: str) -> Optional[datetime]:
    """Parse a rotated-snapshot filename into a UTC datetime.

    Mirrors ``scripts.snapshot_diff``'s convention
    (``state_snapshot_<YYYYmmddTHHMMSSZ>.json``); returns ``None`` when ``name``
    does not match the rotation pattern.
    """
    if not (name.startswith(SNAPSHOT_FILENAME_PREFIX)
            and name.endswith(SNAPSHOT_FILENAME_SUFFIX)):
        return None
    core = name[len(SNAPSHOT_FILENAME_PREFIX):-len(SNAPSHOT_FILENAME_SUFFIX)]
    try:
        return datetime.strptime(core, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _load_history(directory: Path, lookback_days: int) -> List[tuple]:
    """Return ``(timestamp, snapshot_dict)`` pairs, oldest → newest.

    Only files matching the rotation convention are considered; unreadable files
    are skipped. When ``lookback_days > 0`` the window is measured relative to
    the NEWEST available snapshot's timestamp (deterministic and replay-safe —
    independent of when this runs).
    """
    entries: List[tuple] = []
    for p in directory.iterdir():
        if not p.is_file():
            continue
        ts = _parse_history_timestamp(p.name)
        if ts is None:
            continue
        data = _read_json_object(p)
        if data is None:
            continue
        entries.append((ts, data))

    entries.sort(key=lambda x: x[0])
    if not entries:
        return []

    if lookback_days and lookback_days > 0:
        cutoff = entries[-1][0] - timedelta(days=lookback_days)
        entries = [(ts, d) for ts, d in entries if ts >= cutoff]
    return entries


def pilot_trades(
    pilot: Pilot,
    lookback_days: int = 30,
    history_dir: Optional[str] = None,
) -> List[dict]:
    """Diff a Pilot's holdings across consecutive historical snapshots.

    Loads the rotated snapshots under ``history_dir`` (default
    ``output/history/``), recomputes :func:`pilot_holdings` for each, and diffs
    the resulting symbol sets day-over-day into events:

    * ``ENTER``    — symbol newly in the top-N (``weight_delta`` = its new weight);
    * ``EXIT``     — symbol dropped from the top-N (``weight_delta`` = ``-old weight``);
    * ``REWEIGHT`` — symbol present in both but its weight moved by
      ``>= _REWEIGHT_EPSILON`` (``weight_delta`` = new − old).

    Each event is ``{"date", "symbol", "side", "weight_delta"}`` where ``date`` is
    the *later* snapshot's timestamp. Returns ``[]`` when the history directory is
    missing, empty, or holds fewer than two usable snapshots. Never raises.
    """
    try:
        directory = Path(history_dir) if history_dir else Path(_DEFAULT_HISTORY_DIR)
        if not directory.exists() or not directory.is_dir():
            return []

        snaps = _load_history(directory, lookback_days)
        if len(snaps) < 2:
            return []

        events: List[dict] = []
        for (_prev_ts, prev), (curr_ts, curr) in zip(snaps, snaps[1:]):
            prev_h = {h["symbol"]: h["weight"] for h in pilot_holdings(pilot, prev)}
            curr_h = {h["symbol"]: h["weight"] for h in pilot_holdings(pilot, curr)}
            date = curr.get("timestamp") or curr_ts.strftime("%Y-%m-%dT%H:%M:%SZ")

            for sym in sorted(set(prev_h) | set(curr_h)):
                pw = prev_h.get(sym)
                cw = curr_h.get(sym)
                if pw is None and cw is not None:
                    events.append({
                        "date": date,
                        "symbol": sym,
                        "side": "ENTER",
                        "weight_delta": round(cw, 6),
                    })
                elif cw is None and pw is not None:
                    events.append({
                        "date": date,
                        "symbol": sym,
                        "side": "EXIT",
                        "weight_delta": round(-pw, 6),
                    })
                else:
                    delta = cw - pw
                    if abs(delta) >= _REWEIGHT_EPSILON:
                        events.append({
                            "date": date,
                            "symbol": sym,
                            "side": "REWEIGHT",
                            "weight_delta": round(delta, 6),
                        })
        return events
    except Exception as exc:  # pragma: no cover - defensive dead-letter
        logger.debug("pilot_trades failed for %s: %s", getattr(pilot, "id", "?"), exc)
        return []

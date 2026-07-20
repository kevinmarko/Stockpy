"""
gui/circuit_breakers.py — Circuit-breaker derivation for the Gravity Audit tab.

What this module is
-------------------
A *derivation* layer over the file-backed state the platform already writes:

* ``output/KILL_SWITCH`` — sentinel for the global kill switch
  (``execution.kill_switch.GlobalKillSwitch``).
* ``output/risk_gate_blocks.jsonl`` — append-only log of every
  :class:`execution.risk_gate.PreTradeRiskGate` veto, one JSON line per block.
* ``output/state_snapshot.json`` — the orchestrator's last-run snapshot.

Rather than introducing a parallel breaker engine (which would inevitably drift
from the real risk-gate code), this module reads what the engine already
records and projects it into a handful of typed events the Streamlit panel can
render. Adding a new breaker therefore means adding a check inside
``execution/risk_gate.py`` and tagging its emitted block — never editing this
file's logic.

Constraints honoured
--------------------
* CONSTRAINT #5 (no fabricated metrics) — every breaker reports the underlying
  threshold AND the observed value; a missing observation surfaces as
  ``observed=None`` rather than as a fake zero.
* CONSTRAINT #6 (dead-letter) — a corrupt block-log line is dropped + logged,
  never raised to the caller. The panel renders what it can.
* CONSTRAINT #9 (type hints) on every public function.
* CONSTRAINT #10 (logging) — module-level ``logger``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CircuitBreakerTrip:
    """One tripped breaker, ready for render.

    Attributes
    ----------
    name:
        Short stable identifier, e.g. ``"global_kill_switch"``,
        ``"daily_loss_limit"``, ``"max_correlation"``. Used as the row key in
        the panel table.
    severity:
        ``"CRITICAL"`` for a halt-everything trip (kill switch);
        ``"WARNING"`` for a per-strategy / per-symbol block.
    summary:
        One-line operator-facing description ("Strategy X stopped due to 5%
        daily loss limit").
    triggered_at:
        UTC datetime when the breaker tripped, when known. ``None`` for
        events that don't carry a timestamp (e.g. the kill-switch sentinel
        file with no mtime).
    threshold:
        The configured limit (e.g. ``0.05`` for 5%). ``None`` when the
        underlying record didn't carry it.
    observed:
        The observed value that crossed the threshold. ``None`` when the
        record didn't carry it (CONSTRAINT #5 — we never fabricate this).
    detail:
        Raw payload for the "🔬 Inspect" expander.
    """

    name: str
    severity: str
    summary: str
    triggered_at: Optional[datetime] = None
    threshold: Optional[float] = None
    observed: Optional[float] = None
    detail: Mapping[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Block-log reader
# ---------------------------------------------------------------------------

def read_block_log(path: Path, max_lines: int = 500) -> List[dict]:
    """Return the most recent ``max_lines`` JSON-line blocks from ``path``.

    Missing file → empty list. Corrupt lines are skipped + logged at DEBUG.
    Newest first (chronological tail reversed) so the GUI can show the
    fresh entries at the top.
    """
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("read_block_log: failed to read %s: %s", path, exc)
        return []

    rows: List[dict] = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.debug("read_block_log: dropping malformed line: %s (%s)", line[:80], exc)
            continue
    return list(reversed(rows))


# ---------------------------------------------------------------------------
# Trip derivation
# ---------------------------------------------------------------------------

# Map risk-gate `check_name` values to operator-facing breaker names + severity.
# Adding a new risk-gate check elsewhere means appending one row here, NOT
# editing the trip logic below.
_KNOWN_CHECKS: Mapping[str, tuple[str, str, str]] = {
    # name -> (breaker_name, severity, summary_template)
    "max_position_size":     ("max_position_size",   "WARNING",
                              "Position size limit blocked {symbol}"),
    "portfolio_heat":        ("portfolio_heat",      "CRITICAL",
                              "Portfolio heat exceeded {threshold:.0%}"),
    "max_correlation":       ("max_correlation",     "WARNING",
                              "Correlation cap blocked {symbol}"),
    "daily_loss_limit":      ("daily_loss_limit",    "CRITICAL",
                              "Daily loss limit {threshold:.0%} hit"),
    "macro_kill_switch":     ("macro_kill_switch",   "CRITICAL",
                              "Macro kill switch vetoed {symbol}"),
    "hmm_regime":            ("hmm_regime",          "WARNING",
                              "HMM risk-off threshold blocked {symbol}"),
    "stress_scenario":       ("stress_scenario",     "WARNING",
                              "Stress scenario gate blocked {symbol}"),
    "market_hours":          ("market_hours",        "WARNING",
                              "Outside NYSE RTH — {symbol}"),
    "minimum_validation":    ("minimum_validation",  "CRITICAL",
                              "Strategy {strategy} not deployable — validation gate"),
    "max_order_rate":        ("max_order_rate",      "WARNING",
                              "Order-rate budget exhausted"),
}


def _coerce_dt(raw: object) -> Optional[datetime]:
    """Parse an ISO-8601 string into a UTC-aware datetime; return None on miss."""
    if not isinstance(raw, str):
        return None
    try:
        # Tolerate naive timestamps by promoting to UTC.
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except ValueError:
        return None


def _coerce_float(raw: object) -> Optional[float]:
    if raw is None:
        return None
    try:
        f = float(raw)  # type: ignore[arg-type]
        return f if f == f else None  # drop NaN
    except (TypeError, ValueError):
        return None


def derive_kill_switch_trip(
    sentinel_path: Path, reason: Optional[str] = None,
) -> Optional[CircuitBreakerTrip]:
    """Return a CRITICAL trip when the kill switch sentinel file is present.

    The sentinel's contents (written by ``GlobalKillSwitch.activate(reason)``)
    are surfaced as the trip detail when readable.
    """
    if not sentinel_path.exists():
        return None
    stored_reason = reason
    try:
        text = sentinel_path.read_text(encoding="utf-8").strip()
        if text and stored_reason is None:
            stored_reason = text[:200]
    except OSError as exc:
        logger.debug("derive_kill_switch_trip: read failed: %s", exc)

    triggered_at: Optional[datetime] = None
    try:
        triggered_at = datetime.fromtimestamp(
            sentinel_path.stat().st_mtime, tz=timezone.utc,
        )
    except OSError:
        pass

    summary = "Global kill switch ACTIVE"
    if stored_reason:
        summary = f"{summary} — {stored_reason}"
    return CircuitBreakerTrip(
        name="global_kill_switch",
        severity="CRITICAL",
        summary=summary,
        triggered_at=triggered_at,
        threshold=None,
        observed=None,
        detail={"sentinel_path": str(sentinel_path),
                "reason": stored_reason or ""},
    )


def derive_block_log_trips(
    block_log: Sequence[Mapping[str, object]],
    *,
    window: timedelta = timedelta(hours=24),
    now: Optional[datetime] = None,
) -> List[CircuitBreakerTrip]:
    """Project recent risk-gate blocks into :class:`CircuitBreakerTrip` events.

    Only the **most recent** trip per ``(check_name, strategy_id)`` is kept —
    a chatty block log shouldn't fill the dashboard with duplicates of the
    same breaker. Anything older than ``window`` is dropped.

    Unknown ``check_name`` values still produce a trip (so a future risk-gate
    check that hasn't been registered above still surfaces) — they're tagged
    severity ``WARNING`` with a generic summary so the operator can see it
    and decide whether to add an entry to ``_KNOWN_CHECKS``.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - window
    seen: dict[tuple[str, str], CircuitBreakerTrip] = {}

    for row in block_log:
        check = str(row.get("check_name") or row.get("name") or "")
        if not check:
            continue
        ts = _coerce_dt(row.get("timestamp"))
        if ts is not None and ts < cutoff:
            continue

        strategy = str(row.get("strategy_id") or row.get("strategy") or "")
        symbol = str(row.get("symbol") or "")
        threshold = _coerce_float(row.get("threshold"))
        observed = _coerce_float(row.get("observed") or row.get("value"))

        defaults = _KNOWN_CHECKS.get(check)
        if defaults is None:
            name, severity, template = (
                check, "WARNING", "Risk-gate block: {check}",
            )
            summary = template.format(check=check)
        else:
            name, severity, template = defaults
            try:
                if threshold is None and "{threshold" in template:
                    # No observed threshold to report. Don't fall through to
                    # `template.format(threshold=float("nan"))`: Python's
                    # "{:.0%}".format(float("nan")) renders the literal string
                    # "nan%" without raising, so the `except` below would
                    # never catch it and a fabricated "nan%" would silently
                    # reach the operator (CONSTRAINT #4). Raise here instead
                    # so this case takes the same honest generic-summary path
                    # as a genuine formatting failure.
                    raise ValueError("no threshold recorded for this trip")
                summary = template.format(
                    symbol=symbol or "—",
                    strategy=strategy or "—",
                    threshold=threshold,
                )
            except (KeyError, ValueError):
                summary = f"{name} blocked {symbol or strategy or 'order'}"

        trip = CircuitBreakerTrip(
            name=name, severity=severity, summary=summary,
            triggered_at=ts, threshold=threshold, observed=observed,
            detail=dict(row),
        )
        key = (name, strategy)
        # Keep the newest trip per (name, strategy) key.
        prev = seen.get(key)
        if prev is None or (
            trip.triggered_at is not None
            and (prev.triggered_at is None or trip.triggered_at >= prev.triggered_at)
        ):
            seen[key] = trip

    # Order newest first; trips without a timestamp sort last.
    return sorted(
        seen.values(),
        key=lambda t: (t.triggered_at or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )


def collect_circuit_breaker_trips(
    *,
    kill_switch_sentinel: Path,
    block_log_path: Path,
    window: timedelta = timedelta(hours=24),
    now: Optional[datetime] = None,
) -> List[CircuitBreakerTrip]:
    """Top-level helper: assemble every active breaker trip for the panel.

    Kill switch (if active) is always first because it halts everything; the
    rest are sorted newest-first by ``derive_block_log_trips``.
    """
    trips: List[CircuitBreakerTrip] = []
    ks = derive_kill_switch_trip(kill_switch_sentinel)
    if ks is not None:
        trips.append(ks)
    blocks = read_block_log(block_log_path, max_lines=500)
    trips.extend(derive_block_log_trips(blocks, window=window, now=now))
    return trips


def summarise_trips(trips: Iterable[CircuitBreakerTrip]) -> dict[str, int]:
    """Return ``{CRITICAL: n, WARNING: m, TOTAL: n+m}`` for the KPI strip."""
    out = {"CRITICAL": 0, "WARNING": 0, "TOTAL": 0}
    for t in trips:
        out[t.severity] = out.get(t.severity, 0) + 1
        out["TOTAL"] += 1
    return out

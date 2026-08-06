"""market_data_latency.py — automatic, in-process quote-latency instrumentation.

Replaces the legacy Streamlit Command Center's per-symbol "Data Latency
Heatmap" (``gui/panels/observability.py::_render_observability_latency_heatmap``,
backed by ``gui.observability_telemetry.LatencySampleStore``), which cannot be
honestly ported as-is: that store lives ONLY in Streamlit ``st.session_state``,
populated only when the operator manually clicks "Fetch quotes" on the GUI's
Market Data tab — a stateless FastAPI process has no equivalent session to read
from, and there is no durable history a `GET` could serve without fabricating
one (the exact CONSTRAINT #4 problem already documented for the Heartbeat Age
Trend section — see ``pilots/observability.py``'s module docstring, section 10).

This module is the honest replacement, not a literal port:

* Recording is AUTOMATIC — every real (non-cache-hit) quote fetch through
  ``data.market_data.CompositeProvider.get_latest_quote`` records a sample,
  rather than requiring the operator to remember to click a button. This is
  strictly more useful than the legacy panel's manual-trigger design, not a
  downgrade.
* Storage is a fixed-capacity, in-process ring buffer (``collections.deque``),
  matching ``gui.observability_telemetry.LatencySampleStore``'s own explicitly
  documented rationale for staying in-memory ("latency is a live signal that
  should reset each session ... stale samples across runs would muddy the
  heatmap without adding insight") — NOT a new SQLite table. It clears on
  every process restart; ``pilots/observability.py::latency_heatmap_summary``
  surfaces this honestly ("samples since this process last started"), the
  same framing System Telemetry and Heartbeat already use for point-in-time
  data (CONSTRAINT #4 — never fabricate a cross-restart trend that isn't real).
* Gated behind ``settings.MARKET_DATA_LATENCY_TRACKING_ENABLED`` (default
  ``False``) — zero recording, zero overhead on the quote-fetch hot path,
  until explicitly enabled.

Zero project imports (stdlib only, `settings` excepted for the stale-flag
reuse note below) — safe to import from `data/market_data.py` (a data-layer
module) with no circular-import risk, matching this codebase's "flat, modular
Engine architecture" convention (no package directories).
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, List, Optional

_DEFAULT_RING_SIZE = 500


@dataclass(frozen=True)
class LatencySample:
    symbol: str
    source: str
    quote_timestamp: datetime
    ingested_at: datetime
    latency_seconds: float
    is_stale: bool


class LatencySampleRing:
    """Thread-safe, fixed-capacity ring buffer of the most recent latency
    samples. A FastAPI process handles requests on a thread pool, so writes
    (from a quote fetch) and reads (from a GET /observability/summary
    request) can race — the lock keeps both sides consistent without ever
    blocking the quote fetch on anything but a few in-memory operations."""

    def __init__(self, maxlen: int = _DEFAULT_RING_SIZE) -> None:
        self._lock = threading.Lock()
        self._samples: Deque[LatencySample] = deque(maxlen=maxlen)

    def record(self, sample: LatencySample) -> None:
        with self._lock:
            self._samples.append(sample)

    def samples(self) -> List[LatencySample]:
        with self._lock:
            return list(self._samples)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


# Module-level singleton -- one ring per process, matching every other
# in-process cache in data/market_data.py (the quote/bars caches are also
# module-level, not per-request).
_RING = LatencySampleRing()


def get_ring() -> LatencySampleRing:
    """The process-wide ring singleton. Exposed as a function (not the bare
    module attribute) so tests can monkeypatch/reset it without reaching
    into this module's internals."""
    return _RING


def record_quote_latency(
    symbol: str,
    source: str,
    quote_timestamp: Optional[datetime],
    is_stale: bool,
) -> None:
    """Record one sample. Best-effort: NEVER raises (CONSTRAINT #6) — a
    latency-tracking bug must never break a live quote fetch, mirroring
    ``pipeline/production_steps.py``'s identical try/except-log-and-continue
    shape around ``CapAuditStore.record_cap_events``. Silently skips (never
    fabricates a latency figure) when ``quote_timestamp`` is missing —
    CONSTRAINT #4."""
    if quote_timestamp is None:
        return
    try:
        ingested_at = datetime.now(timezone.utc)
        qts = quote_timestamp if quote_timestamp.tzinfo else quote_timestamp.replace(tzinfo=timezone.utc)
        latency = max(0.0, (ingested_at - qts).total_seconds())
        _RING.record(
            LatencySample(
                symbol=symbol,
                source=source,
                quote_timestamp=qts,
                ingested_at=ingested_at,
                latency_seconds=latency,
                is_stale=bool(is_stale),
            )
        )
    except Exception:  # noqa: BLE001 — best-effort, never blocks a quote fetch
        pass


def summarize_latency(samples: List[LatencySample]) -> dict:
    """p50/p95 latency + the single worst-p95 symbol, mirroring
    ``gui.observability_telemetry.summarise_latency``'s exact shape so the
    webapp's KPI strip reads the same way the legacy panel's did. Returns an
    honest all-``None``/zero-count shape on an empty list — never a
    fabricated 0.0 latency (CONSTRAINT #4)."""
    if not samples:
        return {"count": 0, "p50": None, "p95": None, "worst_symbol": None, "worst_p95": None}

    latencies = sorted(s.latency_seconds for s in samples)
    n = len(latencies)

    def _percentile(p: float) -> float:
        if n == 1:
            return latencies[0]
        idx = min(n - 1, max(0, round(p * (n - 1))))
        return latencies[idx]

    by_symbol: dict[str, List[float]] = {}
    for s in samples:
        by_symbol.setdefault(s.symbol, []).append(s.latency_seconds)

    worst_symbol: Optional[str] = None
    worst_p95 = -1.0
    for sym, vals in by_symbol.items():
        vals_sorted = sorted(vals)
        p95 = _percentile_of(vals_sorted, 0.95)
        if p95 > worst_p95:
            worst_p95 = p95
            worst_symbol = sym

    return {
        "count": n,
        "p50": _percentile(0.50),
        "p95": _percentile(0.95),
        "worst_symbol": worst_symbol,
        "worst_p95": worst_p95 if worst_symbol is not None else None,
    }


def _percentile_of(sorted_vals: List[float], p: float) -> float:
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_vals[0]
    idx = min(n - 1, max(0, round(p * (n - 1))))
    return sorted_vals[idx]

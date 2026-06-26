"""
gui/observability_telemetry.py — Health-tab telemetry helpers.

Why this module exists
----------------------
The Observability tab used to surface only macro/regime/P&L state. Three
new operator-facing health views needed self-contained, headlessly testable
helpers (no Streamlit imports here) so the Streamlit code in
``gui/panels.py::render_observability`` stays declarative:

1. ``collect_system_telemetry()`` — CPU, memory, and disk usage of the current
   Python process plus host totals. Backed by ``psutil`` (already a project
   dependency). Falls back to NaN-shaped dict when ``psutil`` is unavailable
   (CONSTRAINT #4 — no fabricated zeros) so the panel can still render.

2. ``LatencySampleStore`` + ``build_latency_heatmap`` — bounded ring buffer of
   per-symbol fetch latency samples (provider timestamp → ingest wall clock).
   Surfaces "is the platform being fed stale information?" without re-walking
   provider APIs. Wired by ``render_market_data`` (Market Data tab) so a single
   fetch populates the heatmap in the Observability tab too.

3. ``parse_log_lines`` + ``filter_log_entries`` — read ``logs/investyo.log``
   (written by ``alerting.setup_logging()``), parse each line against the
   formatter pattern
   ``%(asctime)s  %(levelname)-8s  %(name)s — %(message)s`` and return typed
   ``LogEntry`` records the panel can filter by level (CRITICAL / ERROR /
   WARNING / INFO / DEBUG) and free-text substring.

Constraints honoured
--------------------
* CONSTRAINT #1 (no paid deps): only ``psutil`` (already pinned) + stdlib.
* CONSTRAINT #4 (no fabricated metrics): missing psutil / missing log file /
  unparsable line is reported as such — never zeroed.
* CONSTRAINT #5 (no bare except): every failure path logs context.
* CONSTRAINT #9 (Python 3.10+ type hints): all public functions annotated.
* CONSTRAINT #10 (logging): module-level ``logger = logging.getLogger(__name__)``.
"""

from __future__ import annotations

import logging
import math
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. System telemetry
# ===========================================================================

@dataclass(frozen=True)
class SystemTelemetry:
    """Snapshot of host + process resource usage.

    All percentages are 0-100 floats. ``available_bytes`` / ``total_bytes`` are
    raw byte counts. Any field that could not be sampled is ``float('nan')``
    or ``-1`` (for ints) so the GUI can render "—" without ever mistaking
    "couldn't read" for "zero load" (CONSTRAINT #4).
    """

    cpu_percent: float
    cpu_count_logical: int
    load_avg_1m: float
    memory_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    disk_percent: float
    disk_used_bytes: int
    disk_total_bytes: int
    process_rss_bytes: int
    process_cpu_percent: float
    process_threads: int
    sampled_at: datetime
    psutil_available: bool


def _nan_telemetry(reason: str) -> SystemTelemetry:
    logger.warning("collect_system_telemetry: psutil unavailable (%s)", reason)
    nan = float("nan")
    return SystemTelemetry(
        cpu_percent=nan, cpu_count_logical=-1, load_avg_1m=nan,
        memory_percent=nan, memory_used_bytes=-1, memory_total_bytes=-1,
        disk_percent=nan, disk_used_bytes=-1, disk_total_bytes=-1,
        process_rss_bytes=-1, process_cpu_percent=nan, process_threads=-1,
        sampled_at=datetime.now(timezone.utc),
        psutil_available=False,
    )


def collect_system_telemetry(disk_path: str | Path = "/") -> SystemTelemetry:
    """Sample CPU / memory / disk for the host and the current process.

    Parameters
    ----------
    disk_path:
        Filesystem path whose volume is queried via ``psutil.disk_usage``.
        Default ``"/"`` — the platform root, which is what matters when the
        SQLite DB / Parquet caches live on the system volume.

    Returns
    -------
    SystemTelemetry
        Always returns; sampling failures are reported via NaN/-1 fields and
        ``psutil_available=False``.
    """
    try:
        import psutil  # type: ignore
    except ImportError as exc:
        return _nan_telemetry(f"ImportError: {exc}")

    # ``cpu_percent(interval=None)`` returns the average since the previous
    # call; the first invocation is therefore meaningless. We deliberately
    # accept that — the panel auto-refreshes, so the second render is
    # accurate. Forcing ``interval=0.1`` here would block the Streamlit loop.
    try:
        cpu_pct = float(psutil.cpu_percent(interval=None))
        cpu_logical = int(psutil.cpu_count(logical=True) or -1)
        try:
            load1 = float(psutil.getloadavg()[0])
        except (AttributeError, OSError):
            load1 = float("nan")

        vm = psutil.virtual_memory()
        du = psutil.disk_usage(str(disk_path))
        proc = psutil.Process()
        proc_rss = int(proc.memory_info().rss)
        proc_cpu = float(proc.cpu_percent(interval=None))
        proc_threads = int(proc.num_threads())

        return SystemTelemetry(
            cpu_percent=cpu_pct,
            cpu_count_logical=cpu_logical,
            load_avg_1m=load1,
            memory_percent=float(vm.percent),
            memory_used_bytes=int(vm.used),
            memory_total_bytes=int(vm.total),
            disk_percent=float(du.percent),
            disk_used_bytes=int(du.used),
            disk_total_bytes=int(du.total),
            process_rss_bytes=proc_rss,
            process_cpu_percent=proc_cpu,
            process_threads=proc_threads,
            sampled_at=datetime.now(timezone.utc),
            psutil_available=True,
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the panel
        logger.warning("collect_system_telemetry sampling failed: %s", exc)
        return _nan_telemetry(f"sample failed: {exc}")


def format_bytes(n: int) -> str:
    """Format ``n`` bytes as a human-readable string (B / KiB / MiB / GiB)."""
    if n < 0:
        return "—"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:,.1f} PiB"


# ===========================================================================
# 2. Data-latency heatmap
# ===========================================================================

@dataclass(frozen=True)
class LatencySample:
    """One end-to-end latency observation for a market-data fetch.

    ``latency_seconds`` is computed by the store from
    ``(ingested_at - quote_timestamp).total_seconds()``; values < 0 indicate
    a clock skew or future-dated quote and are clamped to 0 on render but
    preserved here for forensic value.
    """

    symbol: str
    source: str
    quote_timestamp: datetime
    ingested_at: datetime
    latency_seconds: float
    is_stale: bool


class LatencySampleStore:
    """Bounded ring buffer of :class:`LatencySample` events.

    Sized to ``max_samples`` — older observations roll off. Thread-unsafe by
    design (the GUI is single-threaded; the orchestrator does not touch this
    store).

    The store deliberately does NOT persist to disk: latency is a live signal
    that should reset each session. Stale samples across runs would muddy
    the heatmap without adding insight.

    Parameters
    ----------
    max_samples:
        Buffer capacity. Default 500 keeps the heatmap responsive while
        still covering a multi-symbol watchlist sync.
    """

    def __init__(self, max_samples: int = 500) -> None:
        if max_samples < 1:
            raise ValueError("max_samples must be >= 1")
        self._samples: Deque[LatencySample] = deque(maxlen=max_samples)

    def record(
        self,
        symbol: str,
        source: str,
        quote_timestamp: datetime,
        ingested_at: Optional[datetime] = None,
        is_stale: bool = False,
    ) -> LatencySample:
        """Compute and store one latency sample.

        ``ingested_at`` defaults to ``datetime.now(timezone.utc)`` so callers
        can pass the sample-time when they have one or omit it otherwise.
        Both timestamps are normalised to UTC.
        """
        ingested = ingested_at or datetime.now(timezone.utc)
        if ingested.tzinfo is None:
            ingested = ingested.replace(tzinfo=timezone.utc)
        if quote_timestamp.tzinfo is None:
            quote_timestamp = quote_timestamp.replace(tzinfo=timezone.utc)
        latency = (ingested - quote_timestamp).total_seconds()
        sample = LatencySample(
            symbol=symbol.upper(),
            source=source,
            quote_timestamp=quote_timestamp,
            ingested_at=ingested,
            latency_seconds=latency,
            is_stale=is_stale,
        )
        self._samples.append(sample)
        return sample

    def samples(self) -> List[LatencySample]:
        """Return a list copy of the current buffer (oldest first)."""
        return list(self._samples)

    def clear(self) -> None:
        """Drop every sample (e.g. on session reset)."""
        self._samples.clear()

    def __len__(self) -> int:
        return len(self._samples)


def summarise_latency(samples: Sequence[LatencySample]) -> Dict[str, Any]:
    """Aggregate per-symbol stats for the heatmap caption.

    Returns a dict with overall p50/p95, sample count, and the worst symbol
    (highest p95). Empty input → all-NaN dict.
    """
    if not samples:
        return {
            "count": 0, "p50": float("nan"), "p95": float("nan"),
            "worst_symbol": None, "worst_p95": float("nan"),
        }

    latencies = sorted(max(0.0, s.latency_seconds) for s in samples)
    n = len(latencies)
    p50 = latencies[n // 2]
    p95 = latencies[min(n - 1, int(n * 0.95))]

    by_symbol: dict[str, list[float]] = {}
    for s in samples:
        by_symbol.setdefault(s.symbol, []).append(max(0.0, s.latency_seconds))
    worst_symbol: Optional[str] = None
    worst_p95 = float("-inf")
    for sym, lats in by_symbol.items():
        lats_sorted = sorted(lats)
        sym_p95 = lats_sorted[min(len(lats_sorted) - 1, int(len(lats_sorted) * 0.95))]
        if sym_p95 > worst_p95:
            worst_p95 = sym_p95
            worst_symbol = sym

    return {
        "count": n,
        "p50": p50,
        "p95": p95,
        "worst_symbol": worst_symbol,
        "worst_p95": worst_p95 if worst_p95 > float("-inf") else float("nan"),
    }


# ===========================================================================
# 3. Error / log aggregation
# ===========================================================================

VALID_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass(frozen=True)
class LogEntry:
    """One parsed line from ``logs/investyo.log``.

    ``raw`` preserves the source line verbatim so unparseable continuation
    lines (multi-line tracebacks) can still surface in the panel.
    """

    timestamp: Optional[datetime]
    level: str
    logger_name: str
    message: str
    raw: str

    @property
    def parsed(self) -> bool:
        return self.timestamp is not None and self.level in VALID_LEVELS


# Matches the alerting.py formatter:
#   "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
# asctime default: "2026-06-26 08:40:28,615" (comma-millis).
_LOG_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)"
    r"\s+(?P<lvl>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<name>\S+)\s+[—-]\s+(?P<msg>.*)$"
)


def _parse_timestamp(raw_ts: str) -> Optional[datetime]:
    raw = raw_ts.replace(",", ".")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_log_lines(lines: Iterable[str]) -> List[LogEntry]:
    """Parse an iterable of raw log lines into :class:`LogEntry` records.

    Lines that do not match the expected formatter pattern (e.g. multi-line
    traceback continuations) are still returned, with ``level=""`` and
    ``timestamp=None``, so the operator can scroll the full transcript in
    the GUI without holes.
    """
    out: List[LogEntry] = []
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        m = _LOG_LINE.match(line)
        if not m:
            out.append(LogEntry(
                timestamp=None, level="", logger_name="", message=line, raw=line,
            ))
            continue
        out.append(LogEntry(
            timestamp=_parse_timestamp(m.group("ts")),
            level=m.group("lvl"),
            logger_name=m.group("name"),
            message=m.group("msg"),
            raw=line,
        ))
    return out


def read_log_tail(path: Path, max_lines: int = 500) -> List[str]:
    """Return the last ``max_lines`` lines of ``path`` as raw strings.

    Missing file → empty list (caller surfaces "no logs yet" hint).
    """
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()[-max_lines:]
    except OSError as exc:
        logger.warning("read_log_tail: failed to read %s: %s", path, exc)
        return []


def filter_log_entries(
    entries: Sequence[LogEntry],
    *,
    min_level: str = "INFO",
    contains: Optional[str] = None,
) -> List[LogEntry]:
    """Filter parsed log entries by minimum level and free-text substring.

    ``min_level`` is treated ordinally (DEBUG < INFO < WARNING < ERROR <
    CRITICAL). Unparseable lines are KEPT regardless of level (their level is
    empty) so the operator never loses traceback continuations when filtering
    to "ERROR and above" — but their ``parsed=False`` flag lets the GUI
    render them differently.
    """
    if min_level not in VALID_LEVELS:
        raise ValueError(
            f"min_level must be one of {VALID_LEVELS}, got {min_level!r}"
        )
    needle = contains.lower() if contains else None
    threshold = VALID_LEVELS.index(min_level)

    kept: List[LogEntry] = []
    for e in entries:
        # Keep unparseable continuations so multi-line tracebacks stay intact.
        if e.parsed:
            if VALID_LEVELS.index(e.level) < threshold:
                continue
        if needle and needle not in e.raw.lower():
            continue
        kept.append(e)
    return kept


def tally_levels(entries: Sequence[LogEntry]) -> Dict[str, int]:
    """Count entries per level (and an ``UNPARSED`` bucket for continuations).

    Used by the panel's KPI strip — at a glance "27 INFO, 3 WARNING, 1 ERROR".
    """
    tally = {lvl: 0 for lvl in VALID_LEVELS}
    tally["UNPARSED"] = 0
    for e in entries:
        if e.parsed:
            tally[e.level] += 1
        else:
            tally["UNPARSED"] += 1
    return tally

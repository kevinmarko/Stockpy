"""
gui/market_data_diagnostics.py — Operator-visible diagnostics for the Market Data tab.

Why this module exists
----------------------
The Streamlit "Market Data" panel previously surfaced raw provider exceptions as
opaque "None" cells: when a yfinance fetch was rate-limited or a symbol was
delisted, the operator saw a blank row with no actionable signal. This module
adds the four pieces of feedback the operator needs to act, without coupling the
GUI to provider internals:

1. ``classify_market_error(exc) -> ErrorCategory`` — maps a
   ``MarketDataError`` (or any unwrapped exception text) to one of five typed
   categories: rate-limit, not-found, network-timeout, malformed-response,
   unknown. The classification is pattern-based (substring match on the
   exception chain) so it works against yfinance, Alpaca, and Finnhub error
   strings without importing their exception types.

2. ``validate_quote(quote) -> QuoteValidation`` — pure data check that flags a
   Quote with NaN price, missing bid/ask, or a non-positive price as
   ``QuoteValidation.degraded`` with a human-readable reason list. Used to
   render a warning icon (⚠) in the table rather than silently passing bad
   numbers downstream into the quant pipeline (CONSTRAINT #4 — no fabricated
   metrics).

3. ``FetchHealthTracker`` — sliding-window success/failure ledger. The panel
   renders one of three connection badges (🟢 Healthy / 🟡 Degraded / 🔴 Down)
   from the last N attempts (default 20). Independent of provider TTL, because
   "the cache hasn't expired" is not the same as "the provider responded last
   time we asked".

4. ``BatchQuoteFetcher`` — generator-based batched fetcher with a configurable
   per-call spacing (default 0.1 s) so a 50-symbol watchlist sync does not
   fire 50 simultaneous yfinance round-trips. Yields ``BatchResult`` events the
   Streamlit panel can stream into a progress bar.

All helpers are pure-Python / standard library + ``data.market_data`` types —
no Streamlit imports here so the module is unit-testable headlessly.

Constraints honoured
--------------------
* No paid dependencies (CONSTRAINT #1).
* No fabricated metrics: invalid quotes are flagged, not coerced (CONSTRAINT #4).
* No bare ``except Exception: return 0.0``: every classification path records
  the underlying error text (CONSTRAINT #5).
* Type hints on all public functions (CONSTRAINT #9).
* Module-level logger via ``logging.getLogger(__name__)`` (CONSTRAINT #10).
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Iterable, Iterator, List, Optional, Sequence

from data.market_data import MarketDataError, Quote

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Error classification
# ---------------------------------------------------------------------------

class ErrorCategory(str, Enum):
    """Coarse, operator-facing category for a market-data fetch failure.

    Values are strings (``str, Enum``) so they JSON-serialise cleanly and are
    safe to embed directly in pandas cells without conversion.
    """

    RATE_LIMIT = "rate_limit"
    NOT_FOUND = "not_found"
    NETWORK_TIMEOUT = "network_timeout"
    MALFORMED = "malformed"
    UNKNOWN = "unknown"


_HUMAN_LABELS: dict[ErrorCategory, str] = {
    ErrorCategory.RATE_LIMIT: "API Rate Limited",
    ErrorCategory.NOT_FOUND: "Symbol Not Found",
    ErrorCategory.NETWORK_TIMEOUT: "Network Timeout",
    ErrorCategory.MALFORMED: "Malformed Response",
    ErrorCategory.UNKNOWN: "Unknown Error",
}


def category_label(cat: ErrorCategory) -> str:
    """Return the operator-facing label for ``cat`` (used in the GUI table)."""
    return _HUMAN_LABELS[cat]


# Substring patterns are checked in priority order (rate-limit first because
# yfinance often nests "429" inside a longer "HTTP Error" wrapper).
_PATTERNS: List[tuple[ErrorCategory, tuple[str, ...]]] = [
    (ErrorCategory.RATE_LIMIT,
     ("429", "rate limit", "too many requests", "rate-limited", "quota")),
    (ErrorCategory.NOT_FOUND,
     ("404", "not found", "no data found", "delisted", "unknown symbol",
      "invalid symbol", "no price data found", "empty bars")),
    (ErrorCategory.NETWORK_TIMEOUT,
     ("timeout", "timed out", "connection refused", "connection reset",
      "max retries exceeded", "name or service not known",
      "temporary failure in name resolution", "remote disconnected")),
    (ErrorCategory.MALFORMED,
     ("malformed", "parse", "json", "decode", "unexpected token",
      "could not convert", "cannot reindex")),
]


def classify_market_error(exc: BaseException) -> ErrorCategory:
    """Classify a fetch exception into an :class:`ErrorCategory`.

    The classifier walks the exception's ``__cause__`` / ``__context__`` chain
    so that a ``MarketDataError`` wrapping a ``requests.Timeout`` resolves to
    ``NETWORK_TIMEOUT`` rather than falling through to ``UNKNOWN``. Matching is
    case-insensitive substring against the priority-ordered ``_PATTERNS``
    table; the first hit wins.

    Parameters
    ----------
    exc:
        Any exception raised by a ``MarketDataProvider`` call. Typically a
        :class:`MarketDataError`, but raw exceptions (e.g. from a third-party
        SDK) are tolerated.

    Returns
    -------
    ErrorCategory
        One of the five enum members. Never raises.
    """
    # Build the lower-cased haystack from the exception chain.
    parts: List[str] = []
    cur: Optional[BaseException] = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(str(cur))
        # str() on some exceptions hides status_code; surface it explicitly.
        status = getattr(cur, "status_code", None)
        if status is not None:
            parts.append(f"status_code={status}")
            if status == 429:
                parts.append("rate limit")
            elif status == 404:
                parts.append("not found")
        cur = cur.__cause__ or cur.__context__

    haystack = " | ".join(parts).lower()

    for category, needles in _PATTERNS:
        for needle in needles:
            if needle in haystack:
                return category

    return ErrorCategory.UNKNOWN


# ---------------------------------------------------------------------------
# 2. Quote validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QuoteValidation:
    """Outcome of validating a :class:`Quote` for downstream consumption.

    ``ok=True`` only when every required field is finite and positive. ``ok``
    being False does NOT mean the quote should be discarded — the operator
    might choose to keep it visible with a ⚠ icon. It DOES mean the quote
    should not silently feed into a sizing / signal calculation (CONSTRAINT
    #4): callers must explicitly opt in.
    """

    ok: bool
    issues: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """One-line GUI label: ``"OK"`` or ``"⚠ <comma-joined issues>"``."""
        if self.ok:
            return "OK"
        return "⚠ " + "; ".join(self.issues) if self.issues else "⚠ invalid"


def _is_finite_positive(v: float) -> bool:
    """True when v is a finite, strictly positive float."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0.0


def validate_quote(quote: Quote) -> QuoteValidation:
    """Inspect a Quote and report any structural issues.

    Checked invariants:

    * ``price`` is finite and > 0.
    * If both ``bid`` and ``ask`` are present, ``bid <= ask``.
    * ``timestamp`` is set.

    Missing-only bid OR ask is NOT flagged as a hard issue (some providers
    legitimately deliver only the last trade outside RTH). A missing PRICE
    however always flags — without a price the quote is unusable.

    Parameters
    ----------
    quote:
        A :class:`Quote` returned from any provider.

    Returns
    -------
    QuoteValidation
        With ``ok`` False and a populated ``issues`` tuple when any invariant
        fails.
    """
    issues: List[str] = []

    if not _is_finite_positive(quote.price):
        issues.append("price missing/NaN")

    bid_ok = _is_finite_positive(quote.bid)
    ask_ok = _is_finite_positive(quote.ask)
    if bid_ok and ask_ok and quote.bid > quote.ask:
        issues.append(f"bid {quote.bid} > ask {quote.ask}")

    if quote.timestamp is None:
        issues.append("timestamp missing")

    return QuoteValidation(ok=not issues, issues=tuple(issues))


# ---------------------------------------------------------------------------
# 3. Sliding-window health tracker
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    """Three-tier connectivity badge for the operator."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True)
class HealthReport:
    """Snapshot returned by :meth:`FetchHealthTracker.status`."""

    status: HealthStatus
    successes: int
    failures: int
    last_success_ts: Optional[float]
    last_failure_ts: Optional[float]

    @property
    def total(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        """Fraction in [0, 1]. NaN when no observations exist yet."""
        if self.total == 0:
            return float("nan")
        return self.successes / self.total

    def badge(self) -> str:
        """Compact emoji + label used by the Streamlit panel."""
        glyph = {
            HealthStatus.HEALTHY: "🟢 Healthy",
            HealthStatus.DEGRADED: "🟡 Degraded",
            HealthStatus.DOWN: "🔴 Down",
        }[self.status]
        if self.total == 0:
            return f"{glyph} (no fetches yet)"
        return f"{glyph} ({self.successes}/{self.total} ok)"


class FetchHealthTracker:
    """Sliding-window success/failure ledger for one market-data provider.

    The tracker keeps the most recent ``window`` (success, failure, timestamp)
    events. The status is derived from the success rate of those events:

    * success_rate >= ``healthy_threshold``  → HEALTHY
    * success_rate >= ``degraded_threshold`` → DEGRADED
    * otherwise                              → DOWN

    With zero observations the status is HEALTHY (neutral; nothing has failed
    yet) so the GUI doesn't render a scary red badge on first paint.

    Parameters
    ----------
    window:
        Number of recent attempts to retain. Default 20 keeps the badge
        responsive without over-weighting a single failure.
    healthy_threshold:
        Minimum success rate for HEALTHY. Default 0.9 (one failure in ten).
    degraded_threshold:
        Minimum success rate for DEGRADED. Default 0.5.
    """

    def __init__(
        self,
        window: int = 20,
        healthy_threshold: float = 0.9,
        degraded_threshold: float = 0.5,
    ) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        if not 0.0 <= degraded_threshold <= healthy_threshold <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0 <= degraded <= healthy <= 1"
            )
        self._window = window
        self._healthy = healthy_threshold
        self._degraded = degraded_threshold
        # Each entry: (is_success: bool, monotonic_ts: float)
        self._events: Deque[tuple[bool, float]] = deque(maxlen=window)
        self._last_success_ts: Optional[float] = None
        self._last_failure_ts: Optional[float] = None

    def record_success(self) -> None:
        """Record one successful fetch (call after every provider response)."""
        ts = time.monotonic()
        self._events.append((True, ts))
        self._last_success_ts = ts

    def record_failure(self) -> None:
        """Record one failed fetch (call inside the except clause)."""
        ts = time.monotonic()
        self._events.append((False, ts))
        self._last_failure_ts = ts

    def status(self) -> HealthReport:
        """Compute a :class:`HealthReport` from the current event window."""
        successes = sum(1 for ok, _ in self._events if ok)
        failures = len(self._events) - successes
        total = successes + failures

        if total == 0:
            status = HealthStatus.HEALTHY
        else:
            rate = successes / total
            if rate >= self._healthy:
                status = HealthStatus.HEALTHY
            elif rate >= self._degraded:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.DOWN

        return HealthReport(
            status=status,
            successes=successes,
            failures=failures,
            last_success_ts=self._last_success_ts,
            last_failure_ts=self._last_failure_ts,
        )


# ---------------------------------------------------------------------------
# 4. Batched + throttled fetcher
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BatchResult:
    """One streamed result from :class:`BatchQuoteFetcher.iter_fetch`.

    Exactly one of ``quote`` / ``error`` is populated. ``index`` is the 0-based
    position within the symbol batch — handy for progress-bar maths.
    """

    index: int
    symbol: str
    quote: Optional[Quote]
    validation: Optional[QuoteValidation]
    error: Optional[BaseException]
    category: Optional[ErrorCategory]

    @property
    def ok(self) -> bool:
        return self.quote is not None and (self.validation is None or self.validation.ok)


class BatchQuoteFetcher:
    """Yield :class:`BatchResult` events for a symbol list, with throttling.

    Why a separate class rather than a plain for-loop in the panel: keeping the
    spacing + per-symbol exception handling here lets the Streamlit code stay
    declarative (``for result in fetcher.iter_fetch(symbols): update_ui(...)``)
    and the test suite verify spacing without a Streamlit harness.

    Parameters
    ----------
    fetch_fn:
        Callable that takes one symbol and returns a Quote, or raises.
        Typically ``provider.get_latest_quote`` from
        :func:`data.market_data.get_provider`.
    spacing_seconds:
        Minimum monotonic delay between two consecutive ``fetch_fn`` calls.
        Default 0.1 s (10 calls/second) — well under yfinance's known
        throttling threshold and trivially within Alpaca's 200 calls/min limit.
    health_tracker:
        Optional :class:`FetchHealthTracker` to update on each result.
    sleep_fn:
        Pluggable sleep (defaults to ``time.sleep``). Tests inject a fake to
        verify spacing without real waits.
    """

    def __init__(
        self,
        fetch_fn: Callable[[str], Quote],
        spacing_seconds: float = 0.1,
        health_tracker: Optional[FetchHealthTracker] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if spacing_seconds < 0:
            raise ValueError("spacing_seconds must be >= 0")
        self._fetch_fn = fetch_fn
        self._spacing = spacing_seconds
        self._health = health_tracker
        self._sleep = sleep_fn
        # Track per-call spacing across iter_fetch invocations so back-to-back
        # batches share one rolling clock.
        self._last_call_ts: Optional[float] = None

    def iter_fetch(self, symbols: Sequence[str]) -> Iterator[BatchResult]:
        """Stream a :class:`BatchResult` per symbol, in order.

        Each yielded result lets the caller update a progress bar before the
        next ``fetch_fn`` call begins.
        """
        for i, raw in enumerate(symbols):
            sym = raw.strip().upper()
            self._throttle()
            try:
                quote = self._fetch_fn(sym)
                validation = validate_quote(quote)
                if self._health is not None:
                    self._health.record_success()
                yield BatchResult(
                    index=i, symbol=sym, quote=quote, validation=validation,
                    error=None, category=None,
                )
            except MarketDataError as exc:
                if self._health is not None:
                    self._health.record_failure()
                cat = classify_market_error(exc)
                logger.warning(
                    "BatchQuoteFetcher: %s failed (%s): %s", sym, cat.value, exc,
                )
                yield BatchResult(
                    index=i, symbol=sym, quote=None, validation=None,
                    error=exc, category=cat,
                )
            except Exception as exc:  # noqa: BLE001 — wrap & continue
                if self._health is not None:
                    self._health.record_failure()
                cat = classify_market_error(exc)
                logger.error(
                    "BatchQuoteFetcher: %s raised %s (%s): %s",
                    sym, type(exc).__name__, cat.value, exc,
                )
                yield BatchResult(
                    index=i, symbol=sym, quote=None, validation=None,
                    error=exc, category=cat,
                )

    def fetch_all(self, symbols: Sequence[str]) -> List[BatchResult]:
        """Convenience wrapper that materialises the generator."""
        return list(self.iter_fetch(symbols))

    def _throttle(self) -> None:
        """Sleep until at least ``spacing_seconds`` have elapsed since the last call."""
        now = time.monotonic()
        if self._last_call_ts is not None:
            elapsed = now - self._last_call_ts
            wait = self._spacing - elapsed
            if wait > 0:
                self._sleep(wait)
                now = time.monotonic()
        self._last_call_ts = now


# ---------------------------------------------------------------------------
# Convenience export for the Streamlit panel
# ---------------------------------------------------------------------------

def summarise_categories(results: Iterable[BatchResult]) -> dict[str, int]:
    """Tally categories across a batch — used for the post-fetch summary line.

    Returns a dict like ``{"ok": 12, "rate_limit": 3, "not_found": 1}`` ready
    for ``st.toast`` / ``st.caption`` rendering.
    """
    tally: dict[str, int] = {}
    for r in results:
        if r.ok:
            tally["ok"] = tally.get("ok", 0) + 1
        elif r.category is not None:
            tally[r.category.value] = tally.get(r.category.value, 0) + 1
        else:
            # Quote present but validation failed.
            tally["malformed"] = tally.get("malformed", 0) + 1
    return tally

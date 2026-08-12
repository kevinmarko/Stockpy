"""Financial Modeling Prep (FMP) HTTP client — the single network seam every
FMP consumer in this platform goes through.

Why one module, one limiter
---------------------------
The FMP rate limit is **per-account**, not per-concern. Six planned consumers
(fundamentals, quotes, bars, analyst, earnings, macro, insider/sector) share
one budget, so a per-consumer limiter would blow that budget *by construction*
— the same reasoning that keeps ONE module-level GDELT limiter in
``data/sentiment_sources.py`` for both of its consumers, and that makes
``data/etf_holdings.py`` reuse ``data/edgar_fundamentals._throttle`` instead of
opening a second SEC client.

This module is deliberately thin: base URL, credential, throttle/retry/breaker,
and per-endpoint wrappers that return the **raw parsed JSON**. No pandas, no
key mapping, no unit conversion, no math. Every scale decision lives in the
consumer modules so it can be unit-tested with zero mocking.

Credential handling — ``settings.FMP_API_KEY``, NEVER ``os.environ``
-------------------------------------------------------------------
pydantic-settings' ``env_file=".env"`` populates the ``settings`` singleton
directly; it does **not** also copy the value into the process's real
``os.environ``. An operator whose only source for a key is ``.env`` (the
documented, normal case) therefore gets ``None`` from ``os.environ.get()``,
with no error and no warning — indistinguishable from "this source genuinely
has nothing". ``signals/news_catalyst.py::build_finnhub_client`` got exactly
this wrong and silently produced zero Finnhub documents for six months
(confirmed live 2026-07-29). The read below is a lazy
``from settings import settings`` **inside** the function, matching
``data/sentiment_sources.py::_gdelt_get``, so a test can monkeypatch the
singleton and so import of this module never touches configuration.

Three bounds, not one
---------------------
1. **Minimum spacing** (``FMP_MIN_REQUEST_INTERVAL_SECONDS``) between request
   ISSUANCE, with the lock held **across the sleep**. That is what serializes
   issuance: releasing before sleeping lets every waiting thread compute the
   same gap and wake together — a thundering herd that breaks the limit
   precisely when concurrency is added. ``time.monotonic`` (not
   ``time.time``) so an NTP step cannot make ``elapsed`` go negative and skip
   the delay. This matters here specifically because
   ``data_engine.py`` calls ``get_fundamentals`` under an 8-thread pool.
2. **Bounded retry with exponential backoff** on a 429/5xx ONLY, honouring a
   ``Retry-After`` header when the server sends one. A transport error is
   deliberately NOT retried (an immediate retry of a read timeout just times
   out again at full cost) and a 404 is a bad symbol, not an overloaded host.
3. **Cooldown circuit breaker** — after ``FMP_COOLDOWN_THRESHOLD``
   CONSECUTIVE failed requests (429, 5xx, or transport error alike), FMP calls
   are SKIPPED outright, no sleep and no request, for ``FMP_COOLDOWN_SECONDS``.
   From the caller's side "the host is refusing us" and "the host is not
   answering us" have identical cost and identical remedy, so both count.
   Requiring CONSECUTIVE failures is what keeps one flaky socket from opening
   the cooldown; a single served response clears the run and any open cooldown.

Status-code matrix (implemented in :func:`_fmp_get`)
----------------------------------------------------
=====================================  =======  ==============  ==============================
Condition                              Retry?   Breaker++?      Behaviour
=====================================  =======  ==============  ==============================
429 / 5xx                              yes      yes             retry, then ``FMPUnavailable``
transport error (timeout/DNS/reset)    no       yes             ``FMPUnavailable``
401                                    no       **no**          ``FMPUnavailable``; ERROR once per process
403, or a 200 body that is an          no       **no**          ``FMPUnavailable``; endpoint marked dead
  entitlement/"ACCESS DENIED" error                             for the process; ERROR once per endpoint
404                                    no       no              ``FMPUnavailable``
200, empty ``[]`` / ``{}``             no       no              returned as-is; callers decide
breaker open / no API key /            —        —               ``FMPUnavailable``, ZERO network
  dead endpoint
=====================================  =======  ==============  ==============================

401 and 403 deliberately do NOT advance the breaker: neither is evidence that
the host is unhealthy, and letting a rejected key or a plan entitlement open a
five-minute cooldown would conflate "we are not allowed" with "FMP is down"
— two conditions with completely different remedies. A 403/entitlement body is
instead latched **per endpoint path** for the remainder of the process, because
a plan entitlement does not change mid-run; every later call to that path
short-circuits with zero network cost. This is the Starter-tier degradation
path: an Ultimate-only endpoint costs exactly one request per process.

Honesty note: FMP's published Starter limit is 300 req/min, but the actual
enforcement semantics (per-key vs. per-IP, burst-tolerant vs. strict) were not
verifiable from this sandbox. The defaults are a conservative choice targeting
~240/min, not a documented contract — which is exactly why they are settings.
``FMP_MIN_REQUEST_INTERVAL_SECONDS=0`` with ``FMP_MAX_RETRIES=0`` and
``FMP_COOLDOWN_THRESHOLD=0`` reproduces un-throttled behaviour exactly.

Verified endpoint paths (base ``https://financialmodelingprep.com/stable``,
all take ``?apikey=``), probed live against a Starter account:
``quote``, ``batch-quote``, ``historical-price-eod/{light,full,
dividend-adjusted,non-split-adjusted}``, ``historical-chart/1hour``,
``profile``, ``shares-float``, ``key-metrics-ttm``, ``ratios-ttm``,
``income-statement-ttm``, ``dividends``, ``price-target-consensus``,
``grades-summary``, ``earnings``, ``treasury-rates``, ``economic-indicators``,
``insider-trading/statistics``, ``sector-pe-snapshot``,
``sector-performance-snapshot``.

CONSTRAINT #6: ``FMPUnavailable`` is an INTERNAL signal. Callers convert it to
``{}`` / ``[]`` / ``NaN``; it must never escape into the pipeline.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared limiter / breaker state (module-level on purpose — see the docstring:
# the budget is per-ACCOUNT, so every consumer must share one clock).
# ---------------------------------------------------------------------------
# Two locks, never held nested, so a stats update during another thread's
# throttle sleep cannot block: `_fmp_throttle_lock` guards the spacing clock
# and the breaker counters (and IS held across the sleep, deliberately);
# `_fmp_state_lock` guards the per-process latches and the call counters.
_fmp_throttle_lock = threading.Lock()
_fmp_state_lock = threading.Lock()

_fmp_last_request_time: float = 0.0
_fmp_consecutive_failures: int = 0
_fmp_cooldown_until: float = 0.0
_fmp_cooldown_logged: bool = False

# Latched once per process: an ERROR for a rejected key would otherwise repeat
# on every symbol of every cycle, drowning the log in a message whose remedy is
# a single one-time action.
_fmp_auth_error_logged: bool = False
# Endpoint paths that returned 403 / an entitlement body. A plan entitlement
# does not change mid-run, so re-asking costs a guaranteed-failing request.
_fmp_dead_endpoints: set[str] = set()

# Per-endpoint operator telemetry. `calls` counts requests actually ISSUED to
# the network; `skipped` counts zero-network short-circuits (no key, dead
# endpoint, open cooldown) — keeping them separate is the whole point, since a
# high `skipped` and a low `calls` is a healthy breaker, not a healthy feed.
_fmp_call_stats: Dict[str, Dict[str, int]] = {}

# EOD price variants FMP exposes. Validated rather than interpolated blind: a
# typo'd variant would otherwise become a 404 against a path we constructed
# ourselves, and the caller would read that as "no data for this symbol".
# NOTE the adjustment semantics, which are the highest-corruption risk in the
# whole FMP integration: `light` and `full` are SPLIT-ONLY, while the incumbent
# yfinance path uses `auto_adjust=True` (split AND dividend adjusted).
# `dividend-adjusted` is the matching variant — see settings.FMP_BARS_ADJUSTMENT.
_EOD_VARIANTS: frozenset[str] = frozenset(
    {"light", "full", "dividend-adjusted", "non-split-adjusted"}
)

# Intraday chart intervals FMP documents. Only `1hour` was probed live against
# the Starter account; the rest are accepted on the documented list alone and
# will surface as a 403/404 through the normal path if the plan excludes them.
_INTRADAY_INTERVALS: frozenset[str] = frozenset(
    {"1min", "5min", "15min", "30min", "1hour", "4hour"}
)

# Case-insensitive markers FMP uses in an entitlement refusal. Matched against
# the PARSED payload (not the raw text) so the check is deterministic and does
# not depend on transport-level encoding; FMP returns these as JSON — either a
# bare string or a small dict such as {"Error Message": "Exclusive Endpoint…"}.
_ACCESS_DENIED_MARKERS: tuple[str, ...] = (
    "access denied",
    "exclusive endpoint",
    "special endpoint",
    "upgrade your plan",
    "not available under your current subscription",
    "premium endpoint",
    "legacy endpoint",
)


class FMPUnavailable(Exception):
    """Raised when an FMP request could not be served.

    Named for the CONDITION (FMP is not serving us this call) rather than for
    any one cause of it: a rejected key, an out-of-plan endpoint, an open
    cooldown, an exhausted retry budget and a read timeout all leave the caller
    with the same fact and the same remedy — fall back, or degrade to NaN.

    CONSTRAINT #6: callers convert this to ``{}`` / ``[]`` / ``NaN``. It never
    escapes into the pipeline.
    """


def reset_fmp_rate_limiter() -> None:
    """Clear ALL module-level client state: the spacing clock, the
    consecutive-failure run, any open cooldown, the once-per-process log
    latches, the dead-endpoint set, and the call counters.

    Required by the root ``conftest.py`` autouse fixture. This state is
    module-level by design, which means it leaks across tests: a test that
    exercises the breaker would otherwise leave an open cooldown (or a latched
    dead endpoint) behind and silently turn every LATER test's FMP calls into
    zero-network skips — a whole file of tests passing for the wrong reason.
    Same rationale as ``reset_gdelt_rate_limiter``.

    Also useful for a long-lived process (the orchestrator daemon) that wants
    to give a recovered/upgraded account a fresh budget without a restart;
    never needed on the normal path.
    """
    global _fmp_last_request_time, _fmp_consecutive_failures
    global _fmp_cooldown_until, _fmp_cooldown_logged, _fmp_auth_error_logged
    with _fmp_throttle_lock:
        _fmp_last_request_time = 0.0
        _fmp_consecutive_failures = 0
        _fmp_cooldown_until = 0.0
        _fmp_cooldown_logged = False
    with _fmp_state_lock:
        _fmp_auth_error_logged = False
        _fmp_dead_endpoints.clear()
        _fmp_call_stats.clear()


def get_fmp_call_stats() -> Dict[str, Dict[str, int]]:
    """Per-endpoint call telemetry, as a snapshot copy.

    ``{path: {"calls", "successes", "failures", "skipped"}}`` where ``calls``
    counts HTTP requests ACTUALLY ISSUED (so retries are counted individually —
    they each spend budget) and ``skipped`` counts zero-network short-circuits.
    Reported separately on purpose: a high ``skipped`` with a low ``calls`` is
    the breaker doing its job, and collapsing the two would make an outage look
    like a quiet cycle.
    """
    with _fmp_state_lock:
        return {path: dict(counts) for path, counts in _fmp_call_stats.items()}


def _bump(path: str, field: str) -> None:
    """Increment one per-endpoint counter (thread-safe)."""
    with _fmp_state_lock:
        entry = _fmp_call_stats.setdefault(
            path, {"calls": 0, "successes": 0, "failures": 0, "skipped": 0}
        )
        entry[field] = entry.get(field, 0) + 1


def _fmp_throttle(min_interval: float) -> None:
    """Space request ISSUANCE by at least ``min_interval`` seconds.

    The lock is deliberately held across the sleep (see the module docstring).
    ``time.monotonic`` — not ``time.time`` — so an NTP step cannot make the
    elapsed gap go negative and skip the delay.
    """
    global _fmp_last_request_time
    if min_interval <= 0:
        return
    with _fmp_throttle_lock:
        now = time.monotonic()
        elapsed = now - _fmp_last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _fmp_last_request_time = time.monotonic()


def _fmp_in_cooldown() -> bool:
    """True when the limiter is inside a post-failure cooldown, so the caller
    must skip the request entirely rather than issue one that will almost
    certainly fail."""
    global _fmp_cooldown_logged
    with _fmp_throttle_lock:
        if _fmp_cooldown_until <= 0.0:
            return False
        remaining = _fmp_cooldown_until - time.monotonic()
        if remaining <= 0:
            return False
        if not _fmp_cooldown_logged:
            logger.warning(
                "FMP cooldown active for another %.0fs after %d consecutive "
                "failed requests (429/5xx/transport); skipping FMP calls until "
                "it expires (every other data source is unaffected).",
                remaining, _fmp_consecutive_failures,
            )
            _fmp_cooldown_logged = True
        return True


def _fmp_note_failure(threshold: int, cooldown_seconds: float) -> None:
    """Record one failed request — a 429, a 5xx, or a transport error, all of
    which mean the host is not serving us. Opens the cooldown once ``threshold``
    CONSECUTIVE ones have been seen."""
    global _fmp_consecutive_failures, _fmp_cooldown_until, _fmp_cooldown_logged
    with _fmp_throttle_lock:
        _fmp_consecutive_failures += 1
        if threshold > 0 and _fmp_consecutive_failures >= threshold:
            _fmp_cooldown_until = time.monotonic() + max(0.0, cooldown_seconds)
            _fmp_cooldown_logged = False


def _fmp_note_answered() -> None:
    """Record that the host gave us a DEFINITE answer, clearing the
    consecutive-failure run and any open cooldown.

    Deliberately called for 200, 401, 403 and 404 alike: the breaker's question
    is "is this host answering us?", and a 401 or a 404 answers it just as
    squarely as a 200 does. Conflating "we are not allowed" or "that symbol
    doesn't exist" with "FMP is down" would open a five-minute cooldown for a
    condition a cooldown cannot possibly fix.
    """
    global _fmp_consecutive_failures, _fmp_cooldown_until, _fmp_cooldown_logged
    with _fmp_throttle_lock:
        _fmp_consecutive_failures = 0
        _fmp_cooldown_until = 0.0
        _fmp_cooldown_logged = False


def _fmp_retry_after_seconds(resp: Any, fallback: float) -> float:
    """Seconds to wait before retrying, preferring the server's own
    ``Retry-After`` header over our computed backoff when it is present and
    parseable as a delta-seconds value (the form FMP/its CDN emits)."""
    try:
        raw = resp.headers.get("Retry-After")
    except Exception:
        return fallback
    if not raw:
        return fallback
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return fallback


def _is_access_denied(payload: Any) -> bool:
    """True when a 200-status payload is really an entitlement refusal.

    FMP answers an out-of-plan endpoint with HTTP 200 and an error BODY at
    least as often as with a 403, so a status-only check would hand the caller
    a dict that looks like data. Only small scalar/dict payloads are inspected:
    a populated list is real data, and stringifying a 500-row response on every
    successful call to grep it would be pure waste.
    """
    if isinstance(payload, str):
        text = payload
    elif isinstance(payload, dict):
        # An entitlement refusal is a handful of keys ("Error Message"), never
        # a full data record — bounding this keeps a legitimate dict response
        # with a long text field from being scanned on every call.
        if len(payload) > 4:
            return False
        text = " ".join(str(v) for v in payload.values())
    else:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _ACCESS_DENIED_MARKERS)


def _mark_endpoint_dead(path: str, reason: str) -> None:
    """Latch ``path`` as unavailable for the remainder of the process, logging
    an ERROR exactly once for it.

    Per ENDPOINT, not globally: Starter serves ``/quote`` perfectly well while
    refusing Form 13F, and one refusal must not disable the feeds that work.
    """
    with _fmp_state_lock:
        if path in _fmp_dead_endpoints:
            return
        _fmp_dead_endpoints.add(path)
    logger.error(
        "FMP endpoint '%s' is not available on this account's plan (%s). "
        "It is now skipped for the rest of this process — a plan entitlement "
        "does not change mid-run. Every consumer of it degrades to NaN/empty "
        "rather than a fabricated default.",
        path, reason,
    )


def _fmp_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """Issue one throttled, retrying, breaker-guarded GET against FMP.

    Returns the parsed JSON payload (which may legitimately be an empty ``[]``
    or ``{}`` — callers decide what that means). Raises :class:`FMPUnavailable`
    for every failure mode; see the module docstring's status matrix.
    """
    global _fmp_auth_error_logged

    from settings import settings as _settings

    api_key = getattr(_settings, "FMP_API_KEY", None)
    if not api_key:
        # Zero network. Not an error worth logging per call: an operator with
        # no key has simply not enabled FMP, and every FMP_*_ENABLED gate
        # defaults False, so this path is normal for a fresh clone.
        _bump(path, "skipped")
        raise FMPUnavailable(
            "FMP_API_KEY is not set (settings.FMP_API_KEY); request skipped."
        )

    with _fmp_state_lock:
        endpoint_is_dead = path in _fmp_dead_endpoints
    if endpoint_is_dead:
        _bump(path, "skipped")
        raise FMPUnavailable(f"FMP endpoint '{path}' is out of plan; request skipped.")

    if _fmp_in_cooldown():
        _bump(path, "skipped")
        raise FMPUnavailable("FMP cooldown is open; request skipped.")

    base = str(getattr(_settings, "FMP_BASE_URL", "")).rstrip("/")
    timeout = float(getattr(_settings, "FMP_TIMEOUT_SECONDS", 10.0))
    min_interval = float(getattr(_settings, "FMP_MIN_REQUEST_INTERVAL_SECONDS", 0.25))
    max_retries = int(getattr(_settings, "FMP_MAX_RETRIES", 2))
    backoff = float(getattr(_settings, "FMP_RETRY_BACKOFF_SECONDS", 2.0))
    threshold = int(getattr(_settings, "FMP_COOLDOWN_THRESHOLD", 5))
    cooldown = float(getattr(_settings, "FMP_COOLDOWN_SECONDS", 300.0))

    url = f"{base}/{path.lstrip('/')}"
    # Copy rather than mutate: the caller's dict may be reused across symbols,
    # and this function runs under an 8-thread pool (data_engine.py).
    query: Dict[str, Any] = dict(params or {})
    query["apikey"] = api_key

    last_exc: Optional[Exception] = None
    for attempt in range(max(0, max_retries) + 1):
        _fmp_throttle(min_interval)
        _bump(path, "calls")
        try:
            resp = requests.get(url, params=query, timeout=timeout)
        except Exception as exc:
            # A transport error (read timeout / DNS / connection reset) is NOT
            # retried — an immediate retry of a timeout just times out again at
            # full cost — but it DOES count toward the cooldown, because from
            # here "the host is refusing us" and "the host is not answering us"
            # have identical cost and identical remedy.
            _fmp_note_failure(threshold, cooldown)
            _bump(path, "failures")
            raise FMPUnavailable(f"FMP transport error on '{path}': {exc}") from exc

        try:
            status = int(getattr(resp, "status_code", 200))
        except (TypeError, ValueError):
            status = 200

        if status == 429 or 500 <= status < 600:
            _fmp_note_failure(threshold, cooldown)
            _bump(path, "failures")
            last_exc = FMPUnavailable(f"FMP returned HTTP {status} for '{path}'.")
            if attempt >= max_retries or _fmp_in_cooldown():
                break
            wait = _fmp_retry_after_seconds(resp, backoff * (2 ** attempt))
            logger.info(
                "FMP HTTP %d on '%s' — retrying in %.0fs (attempt %d/%d).",
                status, path, wait, attempt + 1, max_retries,
            )
            if wait > 0:
                time.sleep(wait)
            continue

        # Everything below is a DEFINITE answer from a responsive host, so the
        # consecutive-failure run is cleared regardless of what the answer is.
        _fmp_note_answered()

        if status == 401:
            _bump(path, "failures")
            with _fmp_state_lock:
                should_log = not _fmp_auth_error_logged
                _fmp_auth_error_logged = True
            if should_log:
                # Once per process: repeating this per symbol per cycle would
                # bury a message whose remedy is a single one-time action.
                logger.error(
                    "FMP_API_KEY rejected (HTTP 401). Every FMP feed will "
                    "degrade to its fallback/NaN path until the key is fixed "
                    "in .env. Logged once per process."
                )
            raise FMPUnavailable(f"FMP rejected the API key (HTTP 401) on '{path}'.")

        if status == 403:
            _bump(path, "failures")
            _mark_endpoint_dead(path, "HTTP 403")
            raise FMPUnavailable(f"FMP endpoint '{path}' returned HTTP 403.")

        if status == 404:
            # A bad symbol / bad query, not an unhealthy host. No retry, no
            # breaker, no endpoint latch — the NEXT symbol may be fine.
            _bump(path, "failures")
            raise FMPUnavailable(f"FMP returned HTTP 404 for '{path}'.")

        try:
            payload = resp.json()
        except Exception as exc:
            # A 200 whose body will not parse is a content problem, not a host
            # availability problem, so it does not advance the breaker.
            _bump(path, "failures")
            raise FMPUnavailable(
                f"FMP returned an unparseable body for '{path}': {exc}"
            ) from exc

        if _is_access_denied(payload):
            # FMP answers an out-of-plan endpoint with 200 + an error body at
            # least as often as with a 403. Same treatment as the 403 above,
            # for the same reason.
            _bump(path, "failures")
            _mark_endpoint_dead(path, "HTTP 200 with an entitlement error body")
            raise FMPUnavailable(
                f"FMP endpoint '{path}' returned an entitlement error body."
            )

        # An empty [] / {} is returned as-is, NOT raised: "this symbol has no
        # dividends" and "this endpoint is broken" are different facts, and only
        # the caller knows which one matters for its column.
        _bump(path, "successes")
        return payload

    # Reached only when the retry budget was exhausted against a 429/5xx —
    # every one of those attempts already incremented `failures` above.
    raise last_exc or FMPUnavailable(f"FMP request to '{path}' failed.")


# ---------------------------------------------------------------------------
# Thin typed wrappers.
#
# Each is 2-5 lines and returns the RAW parsed JSON — no key mapping, no unit
# conversion, no pandas. That boundary is the point: every scale decision (the
# x100 debtToEquity contract, the dividendYield fraction guard, the NaN-vs-0.0
# choices) lives in a pure, I/O-free consumer module that can be tested without
# a single mock. A wrapper that "helpfully" normalised a field here would move
# the highest-risk code in the integration behind an HTTP mock.
# ---------------------------------------------------------------------------

def _sym(symbol: str) -> str:
    """Normalise a symbol for the wire (FMP uses upper-case tickers)."""
    return str(symbol).strip().upper()


def quote(symbol: str) -> Any:
    """Latest quote for one symbol (``/quote``)."""
    return _fmp_get("quote", {"symbol": _sym(symbol)})


def batch_quote(symbols: List[str]) -> Any:
    """Latest quotes for MANY symbols in one request (``/batch-quote``).

    The single largest rate-limit saving available: a 33-symbol universe costs
    one request here instead of 33.
    """
    joined = ",".join(_sym(s) for s in symbols if str(s).strip())
    return _fmp_get("batch-quote", {"symbols": joined})


def historical_eod(
    symbol: str,
    *,
    variant: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Any:
    """Daily OHLCV bars (``/historical-price-eod/{variant}``).

    ``variant`` is validated against :data:`_EOD_VARIANTS` rather than
    interpolated blind — a typo would otherwise build a path that 404s, which
    the caller would read as "this symbol has no history".

    **Adjustment conventions are load-bearing.** ``light`` and ``full`` are
    split-only; the incumbent yfinance path is ``auto_adjust=True`` (split AND
    dividend adjusted), so ``dividend-adjusted`` is the matching variant.
    Mixing them corrupts every return series, indicator, GARCH fit and
    backtest — plausibly, so nothing fails loudly. See
    ``settings.FMP_BARS_ADJUSTMENT``.
    """
    if variant not in _EOD_VARIANTS:
        raise ValueError(
            f"Unknown FMP EOD variant {variant!r}; expected one of "
            f"{sorted(_EOD_VARIANTS)}."
        )
    params: Dict[str, Any] = {"symbol": _sym(symbol)}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return _fmp_get(f"historical-price-eod/{variant}", params)


def intraday(
    symbol: str,
    interval: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Any:
    """Intraday OHLCV bars (``/historical-chart/{interval}``).

    Only ``1hour`` was probed live against the Starter account; the other
    documented intervals are accepted here and will surface as a 403/404
    through the normal path if the plan excludes them.
    """
    if interval not in _INTRADAY_INTERVALS:
        raise ValueError(
            f"Unknown FMP intraday interval {interval!r}; expected one of "
            f"{sorted(_INTRADAY_INTERVALS)}."
        )
    params: Dict[str, Any] = {"symbol": _sym(symbol)}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return _fmp_get(f"historical-chart/{interval}", params)


def profile(symbol: str) -> Any:
    """Company profile — name, sector, industry, market cap (``/profile``)."""
    return _fmp_get("profile", {"symbol": _sym(symbol)})


def shares_float(symbol: str) -> Any:
    """Float and shares-outstanding counts (``/shares-float``)."""
    return _fmp_get("shares-float", {"symbol": _sym(symbol)})


def key_metrics_ttm(symbol: str) -> Any:
    """Trailing-twelve-month key metrics (``/key-metrics-ttm``)."""
    return _fmp_get("key-metrics-ttm", {"symbol": _sym(symbol)})


def ratios_ttm(symbol: str) -> Any:
    """Trailing-twelve-month financial ratios (``/ratios-ttm``)."""
    return _fmp_get("ratios-ttm", {"symbol": _sym(symbol)})


def income_statement_ttm(symbol: str) -> Any:
    """Trailing-twelve-month income statement (``/income-statement-ttm``)."""
    return _fmp_get("income-statement-ttm", {"symbol": _sym(symbol)})


def dividends(symbol: str) -> Any:
    """Historical dividend record (``/dividends``).

    The real-history replacement for the fabricated 2% constant that
    ``dto_models.py`` currently falls back to for ``dividend_growth_rate``.
    """
    return _fmp_get("dividends", {"symbol": _sym(symbol)})


def price_target_consensus(symbol: str) -> Any:
    """Analyst price-target consensus (``/price-target-consensus``).

    NOT point-in-time: FMP serves only the CURRENT consensus, and targets get
    revised. Diagnostic use only.
    """
    return _fmp_get("price-target-consensus", {"symbol": _sym(symbol)})


def grades_summary(symbol: str) -> Any:
    """Aggregated analyst grade counts (``/grades-summary``). Not point-in-time."""
    return _fmp_get("grades-summary", {"symbol": _sym(symbol)})


def earnings(symbol: str, limit: Optional[int] = None) -> Any:
    """Earnings calendar + surprises for one symbol (``/earnings``).

    Rows are BOTH historical and future-dated. A row is "actual" **iff**
    ``epsActual is not None`` — never treat ``null`` as ``0``. Knowing a
    scheduled FUTURE date is not lookahead (it is publicly announced in
    advance); knowing the future RESULT would be. That distinction is the
    consumer's to enforce, not this wrapper's.
    """
    params: Dict[str, Any] = {"symbol": _sym(symbol)}
    if limit is not None:
        params["limit"] = int(limit)
    return _fmp_get("earnings", params)


def stock_news(
    symbols: str,
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: Optional[int] = None,
    limit: Optional[int] = None,
) -> Any:
    """Company news headlines for one or more symbols (``/news/stock``).

    ``symbols`` is a single ticker or a comma-separated list (FMP's own
    convention — this wrapper does not validate or split it). ``from_date``/
    ``to_date`` are ``"YYYY-MM-DD"`` strings; both are optional but SHOULD be
    passed together to bound a window (an unbounded call returns only the
    most recent articles). ``page``/``limit`` paginate — verified live
    2026-08 against a real FMP key: a single page returns up to ~100
    articles for a multi-day window, so a wide backfill window needs several
    pages (the caller's job, not this wrapper's — mirrors :func:`earnings`'s
    "raw rows, no pagination-looping" contract).

    Returns a list of dicts with (at least) ``symbol``, ``publishedDate``
    (a naive ``"YYYY-MM-DD HH:MM:SS"`` string — see
    ``signals/news_catalyst.py``'s FMP dispatch path for the verified
    timezone), ``publisher``, ``site``, ``title``, ``text``, ``url``. A
    symbol with no news in the window returns ``[]``, not an error — same
    "empty is not failure" contract as every other wrapper in this module.

    Deliberately NOT wrapping ``/news/press-releases``: that endpoint
    returned "Restricted Endpoint" (a plan-entitlement rejection) against
    the account this integration was verified with — see
    ``docs/FMP_INTEGRATION.md`` for the full verification note.
    """
    params: Dict[str, Any] = {"symbols": symbols}
    if from_date is not None:
        params["from"] = from_date
    if to_date is not None:
        params["to"] = to_date
    if page is not None:
        params["page"] = int(page)
    if limit is not None:
        params["limit"] = int(limit)
    return _fmp_get("news/stock", params)


# ``publishedDate`` is a NAIVE "YYYY-MM-DD HH:MM:SS" string with no timezone
# marker. Verified live 2026-08 by cross-referencing a real article: FMP
# reported ``publishedDate: "2026-08-02 14:51:00"`` for a GlobeNewswire
# release whose OWN page states "August 02, 2026 14:51 ET" -- an exact
# match. FMP's news timestamps are therefore US EASTERN TIME, not UTC.
# ``ZoneInfo("America/New_York")`` (not a fixed UTC-4/UTC-5 offset) handles
# EDT/EST daylight-saving transitions correctly year-round. Lives HERE
# (rather than in a consumer module) because it's a property of FMP's own
# wire format, and both ``data/sentiment_sources.py`` (FMPNewsSource) and
# ``signals/news_catalyst.py`` (fetch_company_headlines) need it without
# importing from each other, which would be circular (sentiment_sources.py
# already imports FROM news_catalyst.py).
NEWS_TZ = ZoneInfo("America/New_York")


def parse_news_published_date(raw: str) -> Optional[datetime]:
    """Parse a ``stock_news()`` article's ``publishedDate`` into a UTC-aware
    ``datetime``. Returns ``None`` on any parse failure (a malformed/missing
    timestamp is a data-quality gap, not a crash)."""
    if not raw:
        return None
    try:
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return naive.replace(tzinfo=NEWS_TZ).astimezone(timezone.utc)


def treasury_rates(from_date: str, to_date: str) -> Any:
    """Full daily Treasury yield curve over a date range (``/treasury-rates``).

    As-of and unrevised, so genuinely point-in-time safe — unlike the revised
    macro series from :func:`economic_indicator`.
    """
    return _fmp_get("treasury-rates", {"from": from_date, "to": to_date})


def economic_indicator(name: str, from_date: str, to_date: str) -> Any:
    """One named macro series over a date range (``/economic-indicators``).

    NOT point-in-time: GDP/CPI/unemployment ARE revised and FMP serves the
    latest vintage (the same limitation FRED already has on this path).
    """
    return _fmp_get(
        "economic-indicators", {"name": name, "from": from_date, "to": to_date}
    )


def insider_trade_statistics(symbol: str) -> Any:
    """Aggregated insider buy/sell statistics (``/insider-trading/statistics``).

    Keyed by ``(year, quarter)``, and a quarter's aggregate keeps CHANGING as
    late Form 4s land — the consumer must only read quarters that ended far
    enough in the past (``settings.FMP_INSIDER_MIN_LAG_DAYS``).
    """
    return _fmp_get("insider-trading/statistics", {"symbol": _sym(symbol)})


def sector_pe_snapshot(date: str, exchange: Optional[str] = None) -> Any:
    """Sector P/E ratios as of a specific date (``/sector-pe-snapshot``).

    Always call the DATED form: this is the one new FMP feed with a real
    point-in-time story, and an undated call would throw that away.
    """
    params: Dict[str, Any] = {"date": date}
    if exchange:
        params["exchange"] = exchange
    return _fmp_get("sector-pe-snapshot", params)


def sector_performance_snapshot(date: str, exchange: Optional[str] = None) -> Any:
    """Sector performance as of a specific date (``/sector-performance-snapshot``).

    Dated form, for the same point-in-time reason as :func:`sector_pe_snapshot`.
    """
    params: Dict[str, Any] = {"date": date}
    if exchange:
        params["exchange"] = exchange
    return _fmp_get("sector-performance-snapshot", params)


def financial_scores(symbol: str) -> Any:
    """Financial health & solvency scores — Altman Z and Piotroski F (``/financial-scores``)."""
    return _fmp_get("financial-scores", {"symbol": _sym(symbol)})


# NOTE: stock_news is NOT redefined here — the richer, tested implementation
# above (with from_date/to_date/page/limit, no case-transformation of
# `symbols`) is the one and only definition. A near-duplicate second
# `def stock_news(symbol, limit=10)` briefly existed here (a merge artifact:
# two independent PRs each added a same-named function to this file, and a
# textual merge doesn't detect a duplicate top-level def) and silently shadowed
# the real one, breaking tests/test_fmp_news.py. data/fmp_feeds_company.py's
# fetch_stock_news() calls the one true stock_news(symbol, limit=limit) above.


def economics_calendar(from_date: Optional[str] = None, to_date: Optional[str] = None) -> Any:
    """Macroeconomic events calendar (``/economics-calendar``)."""
    params: Dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return _fmp_get("economics-calendar", params)


def earnings_calendar(from_date: Optional[str] = None, to_date: Optional[str] = None) -> Any:
    """Upcoming earnings release calendar (``/earnings-calendar``)."""
    params: Dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return _fmp_get("earnings-calendar", params)


def batch_index_quotes() -> Any:
    """Quotes for major market volatility indices (``/batch-index-quotes``)."""
    return _fmp_get("batch-index-quotes", {})


def historical_sp500_changes() -> Any:
    """Full history of S&P 500 constituent additions/removals
    (``/historical-sp-500``). Used by ``data/fmp_universe.py`` as the primary
    source for ``universe_engine.py``'s point-in-time survivorship-bias
    reconstruction, with the legacy Wikipedia changes-table scrape demoted to
    a fallback. NOTE: the exact path segment and response field names
    (candidates: ``date``, ``symbol``/``addedSecurity``, ``removedTicker``)
    have not been confirmed against a live account in this environment — see
    ``docs/FMP_INTEGRATION.md`` §8 for verification status before relying on
    this as the *working* primary path rather than a safe no-op."""
    return _fmp_get("historical-sp-500", {})


def peers(symbol: str) -> Any:
    """Stock peer group comparison tickers (``/peers``)."""
    return _fmp_get("peers", {"symbol": _sym(symbol)})


def standard_deviation(symbol: str) -> Any:
    """Technical rolling standard deviation and realized volatility (``/standard-deviation``)."""
    return _fmp_get("standard-deviation", {"symbol": _sym(symbol)})


"""
InvestYo Quant Platform - Point-in-Time (PIT) Fundamentals Audit
=================================================================
Fundamentals (P/E, book value, EPS, dividend yield, ROE, etc.) are fetched
"as of today" by ``data/historical_store.py`` / ``data/market_data.py``. The
platform's existing lookahead tests (``tests/test_indicators_lookahead.py``,
``tests/test_forecasting_lookahead.py``) only verify *price/technical
indicator* causality via ``.shift(1)`` perturbation — they say nothing about
whether a **fundamentals** value used in a historical decision was actually
PUBLIC KNOWLEDGE on that decision date.

THE GAP
-------
Earnings announcements, dividend ex-dates, and stock splits create genuine
look-ahead risk that is invisible to price-based checks: a strategy replaying
history could effectively "know" a just-reported EPS/ROE/P/E before the
market had a chance to react to it, inflating backtested edge with data that
was not knowable at the time.

WHAT THIS MODULE DOES (AND DOES NOT DO)
----------------------------------------
yfinance/Finnhub `.info`-style snapshots are CURRENT-STATE dumps — they do
not carry a genuine "as-of" date per individual field (no field-level
provenance). What they DO sometimes carry is a coarse company-level
"most recent quarter" / "last fiscal year end" timestamp
(yfinance: ``mostRecentQuarter``, ``lastFiscalYearEnd``; Finnhub metrics
payloads carry no such field at all as of this writing — see
``_extract_report_date`` for the exact keys checked).

This module therefore checks ONLY what is honestly derivable:
  1. If a report/quarter-end date is present in the raw provider payload,
     assert it is <= the decision date being evaluated. A date AFTER the
     decision date is unambiguous look-ahead and FAILS.
  2. If no such date is present at all, the check FAILS CLOSED as
     "unverifiable" — it never assumes the data was legitimately available
     just because no contradicting evidence was found. This mirrors
     CONSTRAINT #4's spirit ("never fabricate/assume legitimacy") even
     though this isn't itself a numeric-fabrication case.

This is a DIAGNOSTIC/AUDIT tool, not a data-repair tool: it does not mutate
the fundamentals snapshot, it only reports whether the snapshot's timing
claims can be verified against the requested decision date.

Dead-letter resilience (CONSTRAINT #6): any exception during evaluation
is caught and converted into a FAILED ``PITAuditResult`` with the
exception message as the reason — this function must never raise, so a
single bad snapshot never aborts a broader audit sweep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider raw-payload keys that carry a genuine report/quarter-end date.
# Checked in order; the first present (non-None) key wins. Sourced from
# yfinance's Ticker.info schema — Finnhub's company_basic_financials payload
# (see data/market_data.py::FinnhubProvider) does not expose an equivalent
# per-metric or per-report date field as of this writing, so a Finnhub-only
# snapshot will legitimately fall through to "unverifiable" below.
# ---------------------------------------------------------------------------
REPORT_DATE_KEYS: List[str] = [
    "mostRecentQuarter",   # yfinance: epoch seconds of the most recent 10-Q
    "lastFiscalYearEnd",   # yfinance: epoch seconds of the last fiscal year-end (10-K)
    "report_date",         # platform-added column (see data/historical_store.py)
    "earningsTimestamp",   # yfinance (occasionally present): next/last earnings call
]


@dataclass
class PITAuditResult:
    """Outcome of a single point-in-time fundamentals audit check.

    Mirrors the ``StressResult`` dataclass pattern in
    ``validation/stress_scenarios.py``: a plain dataclass with a ``passed``
    verdict, the fields inspected, and a human-readable reason, so callers
    (the Gravity suite, ad-hoc scripts, tests) can introspect without
    re-deriving the verdict logic.

    Attributes
    ----------
    symbol : str
        Ticker the snapshot belongs to.
    decision_date : str
        ISO date ("YYYY-MM-DD") the fundamentals were being used to inform.
    verdict : str
        One of ``"PASS"``, ``"FAIL"``, ``"UNVERIFIABLE"``. Kept as an
        explicit string (not just a bool) because "unverifiable" is a
        distinct, fail-closed state from either PASS or FAIL — a caller
        that only checks ``passed`` still gets the conservative behavior
        (both FAIL and UNVERIFIABLE evaluate ``passed=False``).
    report_date : Optional[str]
        The report/quarter-end date recovered from the payload (ISO date),
        or ``None`` if no usable date field was found.
    report_date_source_key : Optional[str]
        Which key in ``REPORT_DATE_KEYS`` supplied ``report_date``.
    fields_checked : List[str]
        Names of fundamentals fields this audit run was evaluating
        (informational — the date check is company-level, not per-field,
        since the provider payload does not carry per-field provenance).
    reason : str
        Human-readable explanation of the verdict.
    error : Optional[str]
        Exception message if the check failed closed due to an exception.
    """
    symbol: str
    decision_date: str
    verdict: str
    report_date: Optional[str] = None
    report_date_source_key: Optional[str] = None
    fields_checked: List[str] = field(default_factory=list)
    reason: str = ""
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        """True iff the verdict is a clean PASS. Both FAIL and UNVERIFIABLE
        (and any exception-driven degraded result) evaluate to False —
        fail-closed, matching ``StressResult.passed``'s convention."""
        return self.verdict == "PASS"


def _coerce_decision_date(decision_date) -> date:
    """Normalize a decision date argument (str/date/datetime/Timestamp) to a
    plain ``datetime.date``. Raises ValueError on an unparsable input —
    callers (audit_fundamentals_snapshot) catch this and fail closed."""
    if isinstance(decision_date, date) and not isinstance(decision_date, datetime):
        return decision_date
    if isinstance(decision_date, datetime):
        return decision_date.date()
    if isinstance(decision_date, pd.Timestamp):
        return decision_date.date()
    if isinstance(decision_date, str):
        # Accept "YYYY-MM-DD" (the platform's canonical date string format).
        return datetime.strptime(decision_date[:10], "%Y-%m-%d").date()
    raise ValueError(f"Unparsable decision_date: {decision_date!r}")


def _extract_report_date(raw_payload: Dict[str, Any]) -> tuple[Optional[date], Optional[str]]:
    """Return ``(report_date, source_key)`` recovered from *raw_payload*.

    Checks ``REPORT_DATE_KEYS`` in order; returns the first key present with
    a non-None, parseable value. Handles both epoch-seconds (yfinance's
    convention for ``mostRecentQuarter``/``lastFiscalYearEnd``) and ISO date
    strings (the platform-added ``report_date`` column). Returns
    ``(None, None)`` if no usable key/value is found — this is NOT an error,
    it is the expected "provider doesn't expose this" case that drives the
    UNVERIFIABLE verdict.
    """
    if not raw_payload:
        return None, None

    for key in REPORT_DATE_KEYS:
        val = raw_payload.get(key)
        if val is None:
            continue
        try:
            if isinstance(val, (int, float)):
                # yfinance epoch-seconds convention.
                return datetime.fromtimestamp(float(val), tz=timezone.utc).date(), key
            if isinstance(val, str):
                return datetime.strptime(val[:10], "%Y-%m-%d").date(), key
            if isinstance(val, (date, datetime)):
                d = val.date() if isinstance(val, datetime) else val
                return d, key
        except Exception as exc:  # noqa: BLE001 - malformed value, try next key
            logger.debug(
                "PIT fundamentals: could not parse report-date key %r=%r: %s",
                key, val, exc,
            )
            continue

    return None, None


def audit_fundamentals_snapshot(
    symbol: str,
    decision_date,
    raw_payload: Optional[Dict[str, Any]],
    *,
    fields_checked: Optional[List[str]] = None,
) -> PITAuditResult:
    """Audit whether *raw_payload*'s fundamentals were knowable as of
    *decision_date*.

    Parameters
    ----------
    symbol:
        Ticker the snapshot belongs to (used only for reporting).
    decision_date:
        The date the fundamentals were being used to inform a decision.
        Accepts ``str`` ("YYYY-MM-DD"), ``datetime.date``,
        ``datetime.datetime``, or ``pandas.Timestamp``.
    raw_payload:
        The raw provider dict (yfinance ``.info``, Finnhub metrics dict, or
        the ``raw_json``-deserialized dict from
        ``HistoricalStore.fundamentals_history``). May be ``None`` or ``{}``.
    fields_checked:
        Optional list of the typed fundamentals field names this call cares
        about (e.g. ``["pe_ratio", "eps"]``) — purely informational, carried
        through to the result for reporting; the date check itself is
        company-level (see module docstring).

    Returns
    -------
    PITAuditResult
        ``verdict="PASS"`` iff a report date was found AND
        ``report_date <= decision_date``.
        ``verdict="FAIL"`` iff a report date was found AND it is AFTER
        ``decision_date`` (unambiguous look-ahead).
        ``verdict="UNVERIFIABLE"`` iff no usable report-date field was found
        in the payload at all (fail-closed — never assumes legitimacy).
        Any exception during evaluation degrades to a FAILED result with
        ``error`` set — this function never raises (CONSTRAINT #6).
    """
    fields_checked = list(fields_checked) if fields_checked else []

    try:
        decision_d = _coerce_decision_date(decision_date)
    except Exception as exc:
        logger.warning(
            "PIT fundamentals audit(%s): unparsable decision_date=%r: %s",
            symbol, decision_date, exc,
        )
        return PITAuditResult(
            symbol=symbol,
            decision_date=str(decision_date),
            verdict="FAIL",
            fields_checked=fields_checked,
            reason=f"decision_date could not be parsed: {exc}",
            error=str(exc),
        )

    try:
        payload = raw_payload or {}
        report_d, source_key = _extract_report_date(payload)

        if report_d is None:
            return PITAuditResult(
                symbol=symbol,
                decision_date=decision_d.isoformat(),
                verdict="UNVERIFIABLE",
                report_date=None,
                report_date_source_key=None,
                fields_checked=fields_checked,
                reason=(
                    "No report/quarter-end date field found in the provider "
                    f"payload (checked {REPORT_DATE_KEYS}). Cannot verify the "
                    "fundamentals were public knowledge as of the decision "
                    "date — flagged unverifiable rather than assumed safe."
                ),
            )

        if report_d <= decision_d:
            return PITAuditResult(
                symbol=symbol,
                decision_date=decision_d.isoformat(),
                verdict="PASS",
                report_date=report_d.isoformat(),
                report_date_source_key=source_key,
                fields_checked=fields_checked,
                reason=(
                    f"Report date {report_d.isoformat()} (from {source_key}) is "
                    f"on/before decision date {decision_d.isoformat()} — no "
                    "look-ahead detected."
                ),
            )

        return PITAuditResult(
            symbol=symbol,
            decision_date=decision_d.isoformat(),
            verdict="FAIL",
            report_date=report_d.isoformat(),
            report_date_source_key=source_key,
            fields_checked=fields_checked,
            reason=(
                f"Report date {report_d.isoformat()} (from {source_key}) is AFTER "
                f"decision date {decision_d.isoformat()} — fundamentals were "
                "NOT public knowledge at the decision date (look-ahead bias)."
            ),
        )

    except Exception as exc:  # noqa: BLE001 - dead-letter resilience, CONSTRAINT #6
        logger.error(
            "PIT fundamentals audit(%s) raised unexpectedly: %s", symbol, exc,
        )
        return PITAuditResult(
            symbol=symbol,
            decision_date=str(decision_date),
            verdict="FAIL",
            fields_checked=fields_checked,
            reason=f"Audit raised an unexpected exception: {exc}",
            error=str(exc),
        )


def audit_from_historical_store(
    store,
    symbol: str,
    decision_date,
    *,
    fields_checked: Optional[List[str]] = None,
) -> PITAuditResult:
    """Convenience wrapper: pull the newest cached raw payload for *symbol*
    out of a ``data.historical_store.HistoricalStore`` instance and audit it
    against *decision_date*.

    Uses the store's private ``_read_fundamentals_row`` helper (returns
    ``(as_of_str, typed_dict, raw_json_str)``) rather than the public
    ``get_fundamentals()`` because the public method may trigger a live
    refetch — this audit is meant to run purely against whatever is already
    cached, offline. Dead-letter resilient: any failure (missing row,
    corrupt JSON, DB error) degrades to an UNVERIFIABLE/FAILED result rather
    than raising.
    """
    try:
        row = store._read_fundamentals_row(symbol.upper())
    except Exception as exc:
        logger.warning(
            "audit_from_historical_store(%s): DB read failed: %s", symbol, exc,
        )
        return PITAuditResult(
            symbol=symbol,
            decision_date=str(decision_date),
            verdict="UNVERIFIABLE",
            fields_checked=list(fields_checked) if fields_checked else [],
            reason=f"Could not read cached fundamentals row: {exc}",
            error=str(exc),
        )

    if row is None:
        return PITAuditResult(
            symbol=symbol,
            decision_date=str(decision_date),
            verdict="UNVERIFIABLE",
            fields_checked=list(fields_checked) if fields_checked else [],
            reason=f"No cached fundamentals_history row found for {symbol.upper()}.",
        )

    _as_of_str, _typed_dict, raw_json_str = row

    # Prefer the dedicated report_date column (persisted by
    # HistoricalStore._upsert_fundamentals) when available — avoids
    # re-parsing raw_json on every audit call. Fall back to raw_json
    # directly (e.g. rows written before this column existed).
    stored_report_date: Optional[str] = None
    try:
        stored_report_date = store._read_fundamentals_report_date(symbol.upper())
    except Exception as exc:
        logger.debug(
            "audit_from_historical_store(%s): report_date column read failed: %s",
            symbol, exc,
        )

    if stored_report_date:
        raw_payload: Dict[str, Any] = {"report_date": stored_report_date}
    else:
        raw_payload = {}
        if raw_json_str:
            try:
                import json
                raw_payload = json.loads(raw_json_str)
            except Exception as exc:
                logger.warning(
                    "audit_from_historical_store(%s): raw_json decode failed: %s",
                    symbol, exc,
                )
                raw_payload = {}

    return audit_fundamentals_snapshot(
        symbol, decision_date, raw_payload, fields_checked=fields_checked,
    )


def format_pit_audit_summary(results: List[PITAuditResult]) -> str:
    """Human-readable summary block for a batch of PIT audit results,
    intended for the Gravity suite / CLI reporting — mirrors
    ``format_stress_summary()`` in ``validation/stress_scenarios.py``.
    """
    lines: List[str] = []
    lines.append("=" * 64)
    lines.append(" POINT-IN-TIME (PIT) FUNDAMENTALS AUDIT")
    lines.append("=" * 64)

    if not results:
        lines.append(" NO RESULTS — nothing was audited.")
        lines.append("=" * 64)
        return "\n".join(lines)

    n_pass = sum(1 for r in results if r.verdict == "PASS")
    n_fail = sum(1 for r in results if r.verdict == "FAIL")
    n_unverifiable = sum(1 for r in results if r.verdict == "UNVERIFIABLE")
    lines.append(
        f" {len(results)} snapshot(s) audited: {n_pass} PASS, {n_fail} FAIL, "
        f"{n_unverifiable} UNVERIFIABLE"
    )
    lines.append("-" * 64)
    header = f" {'Symbol':<8} {'Decision':>10} {'ReportDate':>12} {'Verdict':>13}"
    lines.append(header)
    for r in results:
        lines.append(
            f" {r.symbol:<8} {r.decision_date:>10} "
            f"{(r.report_date or 'n/a'):>12} {r.verdict:>13}"
        )
    lines.append("=" * 64)
    return "\n".join(lines)

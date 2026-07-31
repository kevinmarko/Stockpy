"""
data/fmp_macro.py — FMP macro data, shaped for the EXISTING FRED pipeline
==========================================================================
Translates two Financial Modeling Prep endpoints (``/treasury-rates`` and
``/economic-indicators``, both wrapped by ``data/fmp_client.py``) into
``(series_id, date, value)`` rows keyed by **FRED-compatible series IDs**, so
every downstream consumer of ``macro_history`` — ``HistoricalStore.get_macro``,
``MacroEngine.calculate_sahm_rule``, ``compute_hmm_risk_on_probability`` — keeps
working completely unchanged. This module never writes to the database and
never checks a settings gate; both of those responsibilities belong to the
call site (``data_engine.py``'s ``fetch_macro_raw`` / ``fetch_macro_history``),
per this repo's `pure, I/O-thin translation layer` convention (mirrors
``data/fmp_fundamentals.py``'s split from ``data/market_data.py``).

Two series only — explicitly and permanently out of scope
-----------------------------------------------------------
FMP's Starter plan has **no equivalent** for ``VIXCLS`` (VIX) or
``BAMLH0A0HYM2`` (ICE BofA High-Yield OAS credit spread). FMP therefore
**supplements** FRED for ``T10Y2Y`` and ``UNRATE`` ONLY — it never becomes a
source for VIX or HY OAS, and ``regime/hmm_regime.py``'s
``compute_hmm_risk_on_probability`` (which needs ``VIXCLS``) is untouched by
this module. Do not extend this module to synthesize either series from some
FMP proxy; there isn't a faithful one on this plan, and CONSTRAINT #4 forbids
fabricating one.

Point-in-time honesty, per series
----------------------------------
* **Treasury rates are as-of-date and NOT revised** — a daily par-yield curve
  is a genuinely point-in-time-safe series, so ``fetch_treasury_curve``'s
  ``T10Y2Y`` output carries no PIT caveat beyond FRED's own.
* **Unemployment rate IS revised**; FMP (like FRED) serves only the LATEST
  vintage of each historical month, never the as-first-published value. This
  is **not a regression** — FRED's ``UNRATE`` series already has this exact
  limitation in this codebase (see ``fetch_macro_history``'s docstring) — but
  it means ``fetch_unemployment_rate``'s output must stay OUT of any future
  point-in-time audit that assumes as-first-published data.

Failure contract (CONSTRAINT #6)
---------------------------------
Every function here wraps its ``data.fmp_client`` call in
``try/except FMPUnavailable`` (plus a defensive broad ``except Exception`` for
anything a malformed payload could raise while parsing) and degrades to ``[]``.
Nothing in this module ever raises into the pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from data.fmp_client import FMPUnavailable, economic_indicator, treasury_rates

logger = logging.getLogger(__name__)


def fetch_treasury_curve(from_date: str, to_date: str) -> List[Dict[str, Any]]:
    """Daily 10Y-2Y Treasury spread from FMP's ``/treasury-rates``.

    Returns a list of ``{'series_id': 'T10Y2Y', 'date': ..., 'value':
    year10 - year2, 'source': 'fmp'}`` rows, sorted ascending by date (so the
    caller's ``[-1]`` is always the most recent observation). ``[]`` on any
    failure — missing key, network error, entitlement refusal, or a row
    missing either leg of the curve — never raises.

    Treasury rates are as-of-date and NOT revised: genuinely point-in-time
    safe, unlike :func:`fetch_unemployment_rate` below.
    """
    try:
        payload = treasury_rates(from_date, to_date)
    except FMPUnavailable as exc:
        logger.info("fetch_treasury_curve: FMP unavailable: %s", exc)
        return []
    except Exception as exc:  # pragma: no cover - defensive, CONSTRAINT #6
        logger.warning("fetch_treasury_curve: unexpected error: %s", exc)
        return []

    if not isinstance(payload, list):
        return []

    rows: List[Dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date")
        year10 = entry.get("year10")
        year2 = entry.get("year2")
        if date is None or year10 is None or year2 is None:
            # A row missing either leg cannot produce a real T10Y2Y spread —
            # skip it rather than fabricate a partial value (CONSTRAINT #4).
            continue
        try:
            value = float(year10) - float(year2)
        except (TypeError, ValueError):
            continue
        rows.append({"series_id": "T10Y2Y", "date": date, "value": value, "source": "fmp"})

    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_unemployment_rate(from_date: str, to_date: str) -> List[Dict[str, Any]]:
    """Monthly U.S. unemployment rate from FMP's
    ``/economic-indicators?name=unemploymentRate``.

    Returns a list of ``{'series_id': 'UNRATE', 'date': ..., 'value': ...,
    'source': 'fmp'}`` rows, sorted ascending by date. ``[]`` on any failure
    or a malformed payload — never raises.

    NOTE (state honestly, unlike treasury rates above): UNRATE gets REVISED
    over time and FMP serves only the LATEST vintage of each historical
    month — this is NOT point-in-time-safe. That is the same limitation FRED
    already has for this series in this codebase (see
    ``DataEngine.fetch_macro_history``'s docstring), so using FMP here is not
    a regression — but it means this series must stay OUT of any future PIT
    audit that assumes as-first-published values.
    """
    try:
        payload = economic_indicator("unemploymentRate", from_date, to_date)
    except FMPUnavailable as exc:
        logger.info("fetch_unemployment_rate: FMP unavailable: %s", exc)
        return []
    except Exception as exc:  # pragma: no cover - defensive, CONSTRAINT #6
        logger.warning("fetch_unemployment_rate: unexpected error: %s", exc)
        return []

    if not isinstance(payload, list):
        return []

    rows: List[Dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date")
        value = entry.get("value")
        if date is None or value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        rows.append({"series_id": "UNRATE", "date": date, "value": value, "source": "fmp"})

    rows.sort(key=lambda r: r["date"])
    return rows

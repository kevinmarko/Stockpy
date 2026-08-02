"""Per-symbol Financial Modeling Prep company feeds: analyst price-target
consensus + grade summary (``/price-target-consensus``, ``/grades-summary``)
and the earnings calendar of historical actuals + future-dated scheduled
events (``/earnings``).

Boundary
--------
This module is **pure fetch + shape**, matching ``data/fmp_fundamentals.py``'s
template: it owns the mapping from FMP's raw JSON to the kwarg/row shapes the
two ``HistoricalStore`` writers expect, and nothing else.

* **No settings gate here.** ``settings.FMP_ANALYST_ENABLED`` /
  ``settings.FMP_EARNINGS_ENABLED`` are checked by
  ``pipeline/production_steps.py``'s ``_apply_fmp_analyst`` /
  ``_apply_fmp_earnings`` *before* either function below is ever called —
  by the time control reaches here, the gate is already known to be on.
* **No cadence logic here.** The 24h/12h refresh-hours cadence check against
  ``HistoricalStore.latest_analyst_as_of`` / ``latest_earnings_fetched_at``
  also lives in ``production_steps.py``; this module has no opinion on when
  it is called, only on what a single call returns.
* **No DataFrame write-back.** Mapping the returned values onto
  ``dashboard_df`` (upper-cased ``Symbol`` match) is
  ``production_steps.py``'s job, following the ``_apply_etf_transmission``
  idiom.

CONSTRAINT #6 — never raises. Every FMP call is wrapped; ``FMPUnavailable``
(and, defensively, any other exception a malformed payload could raise) is
caught and logged, and the function degrades to ``{}`` / ``[]`` for that leg.
A partial failure (one endpoint down, the other up) still returns whatever
was actually obtained rather than discarding it.

CONSTRAINT #4 — a missing/null numeric field is *never* coerced to ``0.0``.
``_safe_float`` returns ``None`` (not ``0.0``) for anything absent, non-finite,
or uncoercible; ``0.0`` is only ever returned when the vendor payload itself
said ``0``, which is a real, reportable value (e.g. a company that guided to
break-even EPS) and must not be confused with "not reported".

``grades-summary`` field-name assumption — READ THIS BEFORE TRUSTING
``grade_score``
----------------------------------------------------------------------------
FMP's ``/stable/grades-summary`` endpoint could not be probed against live
data from this sandboxed environment (no live network access here; the
wave-0 plan's MCP probe confirmed the endpoint *exists* on this account's
Starter plan but did not capture a raw payload for this feed). Based on
FMP's documented "Grades Consensus" response shape, this module assumes a
JSON array of (at most) one record per symbol carrying five integer grade
counts: ``strongBuy``, ``buy``, ``hold``, ``sell``, ``strongSell``.
``grade_score`` is then

    (strongBuy + buy - sell - strongSell) / total

where ``total = strongBuy + buy + hold + sell + strongSell``, and ``None``
(surfaces as NaN) when ``total <= 0`` or none of the five fields are present.

**This is a documented assumption, not a verified fact.** If FMP's actual
field names differ, or the endpoint returns a different shape entirely (e.g.
a single ``consensus`` string rather than counts), ``_derive_grade_score``
simply finds nothing to sum and returns ``None`` — the CONSTRAINT #4-safe
failure mode: a wrong assumption here degrades to "no analyst grade
coverage" (honest NaN) rather than a fabricated score. Confidence: LOW-MEDIUM
— stated explicitly in the wave-1 agent's final report per the task brief.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from data import fmp_client
from data.fmp_client import FMPUnavailable

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort float coercion. ``None`` — never ``0.0`` (CONSTRAINT #4) —
    when *value* is ``None``, non-finite, or not coercible to ``float``.
    A genuine ``0`` in the payload correctly round-trips to ``0.0``."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / +-inf guard
        return None
    return out


def _first_record(payload: Any) -> Dict[str, Any]:
    """FMP's per-symbol single-record endpoints (price-target-consensus,
    grades-summary) are documented as a JSON array with at most one element
    for a single-symbol query. A bare dict is also accepted defensively (a
    plan/version difference could serve one directly) — this is a shape
    tolerance, not a claim about which shape is primary."""
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _derive_grade_score(record: Dict[str, Any]) -> Optional[float]:
    """``(strongBuy + buy - sell - strongSell) / total`` from one
    grades-summary record. ``None`` when ``total <= 0`` (no coverage, or the
    assumed field names are wrong for this account's payload — see the
    module docstring) rather than a fabricated 0.0/neutral score."""
    strong_buy = _safe_float(record.get("strongBuy"))
    buy = _safe_float(record.get("buy"))
    hold = _safe_float(record.get("hold"))
    sell = _safe_float(record.get("sell"))
    strong_sell = _safe_float(record.get("strongSell"))
    total = sum(v for v in (strong_buy, buy, hold, sell, strong_sell) if v is not None)
    if total <= 0:
        return None
    return (
        (strong_buy or 0.0) + (buy or 0.0) - (sell or 0.0) - (strong_sell or 0.0)
    ) / total


def fetch_analyst_snapshot(symbol: str) -> Dict[str, Any]:
    """Fetch price-target consensus + grade summary for one symbol, shaped
    for ``HistoricalStore.upsert_analyst_snapshot``'s kwargs.

    Two independent legs (``/price-target-consensus``, ``/grades-summary``);
    a failure on one does not suppress the other — the caller gets back
    whatever was actually obtained. ``{}`` only when BOTH legs failed / had
    nothing to report (CONSTRAINT #6: never raises).

    Returns a dict with keys ``target_consensus``, ``target_median``,
    ``target_high``, ``target_low``, ``grade_score`` (each ``float`` or
    ``None`` — never a fabricated ``0.0``), and ``source="fmp"``.
    """
    target_consensus: Optional[float] = None
    target_median: Optional[float] = None
    target_high: Optional[float] = None
    target_low: Optional[float] = None
    try:
        payload = fmp_client.price_target_consensus(symbol)
        record = _first_record(payload)
        target_consensus = _safe_float(record.get("targetConsensus"))
        target_median = _safe_float(record.get("targetMedian"))
        target_high = _safe_float(record.get("targetHigh"))
        target_low = _safe_float(record.get("targetLow"))
    except FMPUnavailable as exc:
        logger.info("FMP price-target-consensus unavailable for %s: %s", symbol, exc)
    except Exception as exc:  # pragma: no cover - defensive, malformed payload
        logger.warning("FMP price-target-consensus fetch failed for %s: %s", symbol, exc)

    grade_score: Optional[float] = None
    try:
        payload = fmp_client.grades_summary(symbol)
        record = _first_record(payload)
        grade_score = _derive_grade_score(record)
    except FMPUnavailable as exc:
        logger.info("FMP grades-summary unavailable for %s: %s", symbol, exc)
    except Exception as exc:  # pragma: no cover - defensive, malformed payload
        logger.warning("FMP grades-summary fetch failed for %s: %s", symbol, exc)

    if (
        target_consensus is None
        and target_median is None
        and target_high is None
        and target_low is None
        and grade_score is None
    ):
        return {}

    return {
        "target_consensus": target_consensus,
        "target_median": target_median,
        "target_high": target_high,
        "target_low": target_low,
        "grade_score": grade_score,
        "source": "fmp",
    }


def fetch_earnings_rows(symbol: str, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fetch the earnings calendar for one symbol via ``fmp_client.earnings``,
    shaped as a list of dicts matching
    ``HistoricalStore.upsert_earnings_events``'s row schema — ``symbol``,
    ``event_date``, ``eps_actual``, ``eps_estimated``, ``revenue_actual``,
    ``revenue_estimated``, ``last_updated``, ``source="fmp"``.

    Rows are BOTH historical and future-dated; that is expected, not
    filtered here (the lookahead-safety rules — which rows may be read as a
    "trailing surprise" vs. a "next scheduled date" — are a *read-side*
    concern the ``HistoricalStore.get_earnings_events`` caller enforces via
    ``on_or_before``/``after``/``actuals_only``, not something this fetch
    function can or should decide).

    A row is left with ``eps_actual=None`` whenever FMP's own payload has a
    null/missing actual — NEVER coerced to ``0.0`` (CONSTRAINT #4): a null
    actual on a future-dated row is normal and expected (the quarter hasn't
    happened yet), not a data-quality problem.

    A row missing its ``date`` field is skipped (no anchor to key it on).
    ``[]`` on ANY failure, including a payload that isn't the expected list
    shape (CONSTRAINT #6: never raises).
    """
    try:
        payload = fmp_client.earnings(symbol, limit=limit)
    except FMPUnavailable as exc:
        logger.info("FMP earnings unavailable for %s: %s", symbol, exc)
        return []
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("FMP earnings fetch failed for %s: %s", symbol, exc)
        return []

    if not isinstance(payload, list):
        return []

    sym_upper = str(symbol).strip().upper()
    rows: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        event_date = item.get("date")
        if not event_date:
            continue
        last_updated = item.get("lastUpdated")
        rows.append(
            {
                "symbol": sym_upper,
                "event_date": str(event_date),
                "eps_actual": _safe_float(item.get("epsActual")),
                "eps_estimated": _safe_float(item.get("epsEstimated")),
                "revenue_actual": _safe_float(item.get("revenueActual")),
                "revenue_estimated": _safe_float(item.get("revenueEstimated")),
                "last_updated": str(last_updated) if last_updated else None,
                "source": "fmp",
            }
        )
    return rows


def fetch_financial_scores(symbol: str) -> Dict[str, Any]:
    """Fetch Altman Z-Score and Piotroski F-Score for a symbol (``/financial-scores``).

    CONSTRAINT #4: Never coerces missing scores to 0.0 — returns None if missing.
    CONSTRAINT #6: Never raises.
    """
    try:
        payload = fmp_client.financial_scores(symbol)
        record = _first_record(payload)
        return {
            "altman_z_score": _safe_float(record.get("altmanZScore")),
            "piotroski_f_score": int(record["piotroskiScore"]) if record.get("piotroskiScore") is not None else None,
            "source": "fmp",
        }
    except FMPUnavailable as exc:
        logger.info("FMP financial-scores unavailable for %s: %s", symbol, exc)
    except Exception as exc:
        logger.warning("FMP financial-scores fetch failed for %s: %s", symbol, exc)
    return {"altman_z_score": None, "piotroski_f_score": None, "source": "fmp"}


def fetch_key_ratios_ttm(symbol: str) -> Dict[str, Any]:
    """Fetch key TTM financial ratios: Net Debt/EBITDA, FCF Yield, Debt-to-Equity (``/ratios-ttm``).

    CONSTRAINT #4: Missing fields return None.
    CONSTRAINT #6: Never raises.
    """
    try:
        payload = fmp_client.ratios_ttm(symbol)
        record = _first_record(payload)
        return {
            "net_debt_ebitda": _safe_float(record.get("netDebtToEBITDATTM")),
            "fcf_yield": _safe_float(record.get("freeCashFlowYieldTTM")),
            "debt_to_equity": _safe_float(record.get("debtEquityRatioTTM")),
            "pe_ratio": _safe_float(record.get("priceEarningsRatioTTM")),
            "source": "fmp",
        }
    except FMPUnavailable as exc:
        logger.info("FMP ratios-ttm unavailable for %s: %s", symbol, exc)
    except Exception as exc:
        logger.warning("FMP ratios-ttm fetch failed for %s: %s", symbol, exc)
    return {"net_debt_ebitda": None, "fcf_yield": None, "debt_to_equity": None, "pe_ratio": None, "source": "fmp"}


def fetch_stock_news(symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Fetch real-time stock news headline snippets for a symbol (``/news/stock``).

    CONSTRAINT #6: Never raises.
    """
    try:
        payload = fmp_client.stock_news(symbol, limit=limit)
        if not isinstance(payload, list):
            return []
        items: List[Dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                items.append({
                    "title": str(item.get("title", "")),
                    "url": str(item.get("url", "")),
                    "published_date": str(item.get("publishedDate", "")),
                    "site": str(item.get("site", "")),
                    "text": str(item.get("text", "")),
                })
        return items
    except FMPUnavailable as exc:
        logger.info("FMP stock news unavailable for %s: %s", symbol, exc)
    except Exception as exc:
        logger.warning("FMP stock news fetch failed for %s: %s", symbol, exc)
    return []


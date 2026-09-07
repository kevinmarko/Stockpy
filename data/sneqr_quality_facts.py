"""Point-in-time (PIT) Sector-Neutral Earnings-Quality Rank (SNEQR) raw
inputs -- ``accrual_ratio`` and ``gross_profitability``, sourced directly
from SEC EDGAR XBRL company facts via ``data/edgar_fundamentals.py``.

Extracted (verbatim, byte-identical logic) from
``scripts/refresh_validations.py::_fetch_sneqr_quality_facts`` so this real,
live-EDGAR-verified PIT computation has exactly ONE implementation shared by
both the strategy-validation harness (``scripts/refresh_validations.py``,
the original caller -- see ``docs/VALIDATION_STRATEGY_FIX_LOG.md``'s
2026-08-22 entry and ``docs/signals/sector_quality_rank.md`` for that
module's live-EDGAR verification results, ~99% coverage on a 100-name S&P
slice) and ``ml/forecast_backfill.py`` (the Forecast Backfill screen's
meta-labeler training pipeline, see
``docs/plans/FORECAST_BACKFILL_PLAN.md``'s WP3 section) -- never two
independently-drifting copies of the same SEC-fact-extraction math.

See ``signals/sector_quality_rank.py``'s own module docstring for the full
Sloan (1996)/Novy-Marx (2013) academic grounding and this signal's sign
convention. ``accrual_ratio``/``gross_profitability`` here are the RAW,
per-``filed``-date inputs this module produces -- the per-date, within-sector
z-score/rank/composite step that turns them into a score is a SEPARATE,
downstream computation (``signals/sector_quality_rank.py::pre_compute`` in
production; ``scripts/refresh_validations.py::_build_sector_quality_rank_adapter``
in the validation harness), not duplicated here.
"""

import logging
from typing import Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# SEC's structured XBRL company-facts data is unreliable before ~2009-2010
# (mandated in phases through 2009-2011) -- see
# ``scripts/refresh_validations.py``'s own ``SNEQR_BACKTEST_START`` comment
# for the original context. Kept as the same literal value here (not
# imported from that script -- this module must not import FROM
# ``scripts/refresh_validations.py``, only the other way around, or callers
# gain an accidental dependency on that script's own module-level
# universe-loading side effects, e.g. its real network-touching
# ``universe_engine.get_sp500_constituents()`` call at import time).
SNEQR_BACKTEST_START = "2010-01-01"


def fetch_sneqr_quality_facts(ticker: str, since: str = SNEQR_BACKTEST_START) -> pd.DataFrame:
    """Real point-in-time accrual_ratio / gross_profitability history for
    *ticker*, sourced directly from SEC EDGAR XBRL company facts.

    accrual_ratio = -((NetIncomeLoss - OperatingCashFlow) / Assets) -- Sloan
    (1996), sign-flipped so higher = better (matches
    signals/sector_quality_rank.py's own documented sign convention).
    OperatingCashFlow prefers ``NetCashProvidedByUsedInOperatingActivities``,
    falling back to the ...ContinuingOperations variant some filers use
    instead.

    gross_profitability = GrossProfit / Assets -- Novy-Marx (2013). Prefers
    the filer's own ``GrossProfit`` tag; when absent, derives it as
    ``Revenues - CostOfRevenue`` (both with their own documented fallback
    tags) rather than leaving it NaN outright, since Revenue and Cost-of-
    Revenue are near-universally reported even by filers that don't tag
    ``GrossProfit`` itself.

    Each ratio is computed independently per SEC ``filed`` date (the SEC's
    own point-in-time availability timestamp -- the same PIT convention
    ``scripts/backfill_edgar_fundamentals.py`` already uses, there called
    ``report_date``) using ``extract_latest_fact(..., max_date=filed)``, so a
    date where only ONE of the two ratios' underlying facts happened to be
    refiled still gets a real, non-lookahead value for the other from
    whatever was filed most recently as of that date.

    Returns a DataFrame indexed by ``filed`` date (as ``pd.Timestamp``) with
    columns ``accrual_ratio``/``gross_profitability`` (NaN where the
    underlying facts are unavailable -- never fabricated, CONSTRAINT #4).
    Empty DataFrame (never raises -- CONSTRAINT #6) when the ticker's CIK
    can't be resolved or EDGAR returns no us-gaap facts at all.
    """
    from data import edgar_fundamentals as ef

    cik = ef.get_cik(ticker)
    if not cik:
        logger.warning("fetch_sneqr_quality_facts: no CIK for %s", ticker)
        return pd.DataFrame(columns=["accrual_ratio", "gross_profitability"])

    facts = ef.fetch_companyfacts(cik)
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        logger.warning("fetch_sneqr_quality_facts: no us-gaap facts for %s", ticker)
        return pd.DataFrame(columns=["accrual_ratio", "gross_profitability"])

    accrual_tags = [
        "NetIncomeLoss",
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "Assets",
    ]
    gp_tags = [
        "GrossProfit", "Revenues", "SalesRevenueNet",
        "CostOfRevenue", "CostOfGoodsAndServicesSold",
    ]
    filed_dates: set = set()
    for tag in set(accrual_tags + gp_tags):
        fact = us_gaap.get(tag)
        if not fact:
            continue
        for unit_arr in fact.get("units", {}).values():
            for point in unit_arr:
                filed = point.get("filed")
                if filed and filed >= since:
                    filed_dates.add(filed)

    rows: Dict[str, Dict[str, float]] = {}
    for filed in sorted(filed_dates):
        net_income = ef.extract_latest_fact(us_gaap, "NetIncomeLoss", filed)
        cfo = ef.extract_latest_fact(
            us_gaap, "NetCashProvidedByUsedInOperatingActivities", filed
        )
        if cfo is None:
            cfo = ef.extract_latest_fact(
                us_gaap,
                "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
                filed,
            )
        assets = ef.extract_latest_fact(us_gaap, "Assets", filed)

        accrual_ratio = float("nan")
        if net_income is not None and cfo is not None and assets and float(assets) != 0.0:
            accruals = (float(net_income) - float(cfo)) / float(assets)
            accrual_ratio = -accruals

        gross_profit = ef.extract_latest_fact(us_gaap, "GrossProfit", filed)
        if gross_profit is None:
            revenue = ef.extract_latest_fact(us_gaap, "Revenues", filed)
            if revenue is None:
                revenue = ef.extract_latest_fact(us_gaap, "SalesRevenueNet", filed)
            cost_of_revenue = ef.extract_latest_fact(us_gaap, "CostOfRevenue", filed)
            if cost_of_revenue is None:
                cost_of_revenue = ef.extract_latest_fact(
                    us_gaap, "CostOfGoodsAndServicesSold", filed
                )
            if revenue is not None and cost_of_revenue is not None:
                gross_profit = float(revenue) - float(cost_of_revenue)

        gross_profitability = float("nan")
        if gross_profit is not None and assets and float(assets) != 0.0:
            gross_profitability = float(gross_profit) / float(assets)

        rows[filed] = {
            "accrual_ratio": accrual_ratio,
            "gross_profitability": gross_profitability,
        }

    if not rows:
        return pd.DataFrame(columns=["accrual_ratio", "gross_profitability"])

    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    return out


def load_ticker_sectors() -> Dict[str, str]:
    """Read ``forecasting/data/ticker_sectors.csv`` (symbol -> yfinance-style
    sector). Verbatim logic mirroring
    ``scripts/refresh_validations.py::_load_ticker_sectors`` -- see that
    function's own docstring for the "current sector snapshot applied across
    full history" accepted-approximation caveat (also documented at length in
    ``signals/sector_quality_rank.py``'s module docstring). A ticker absent
    from the CSV simply has no sector entry (never fabricated).
    """
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "forecasting" / "data" / "ticker_sectors.csv"
    try:
        df = pd.read_csv(path)
        return dict(zip(df["symbol"].astype(str), df["sector"].astype(str)))
    except Exception as exc:  # noqa: BLE001 -- dead-letter: missing/malformed file
        logger.warning("load_ticker_sectors: failed to read %s: %s", path, exc)
        return {}

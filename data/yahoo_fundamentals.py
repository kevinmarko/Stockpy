"""
data/yahoo_fundamentals.py — Pure Fundamental-Metrics Computation Engine
========================================================================
Compute 15 equity fundamental metrics from free Yahoo Finance
financial-statement data (income statement, balance sheet, cashflow, dividends,
institutional holders) that the *caller* has already fetched via ``yfinance``.

This module is deliberately **I/O-free**: it never imports ``yfinance``, never
touches the network, and never reads a file.  Callers pass every frame/series in
as an argument, so the math core is fully offline-testable and deterministic.

Output contract — yfinance ``.info``-style KEY NAMES
----------------------------------------------------
``compute_fundamentals`` returns a ``dict[str, float]`` whose keys mirror the
names yfinance's ``Ticker.info`` uses (``trailingPE``, ``priceToBook``,
``returnOnEquity``, …).  Three downstream consumers already read those key names
off ``FundamentalDataDTO.from_raw_dict()``, so emitting them keeps every
consumer unchanged.

Two SCALE-CRITICAL rules (do not "fix" these — downstream depends on them)
-------------------------------------------------------------------------
* ``dividendYield`` is emitted **as a FRACTION** (0.0257, NOT 2.57).  The
  platform consumes it as a fraction and does *not* normalize this engine's
  output.
* ``debtToEquity`` is emitted **multiplied by 100** (e.g. 150.0, NOT 1.5) — two
  downstream consumers divide by 100.  Ratios like ``returnOnEquity``,
  ``grossMargins``, ``operatingMargins``, ``payoutRatio`` are plain fractions.

NaN-not-zero discipline (CONSTRAINT #4)
---------------------------------------
Every metric is computed in its OWN try/except and degrades independently to
``float("nan")`` on any missing/bad input.  A missing statement row never
fabricates a ``0.0`` and never nukes the other 14 metrics.  ``0.0`` is a real,
meaningful value; ``NaN`` is the honest "unknown" sentinel.

``netPercentInstitutionsSharesOut`` is deliberately NOT emitted so a downstream
short-interest fallback fires instead of a fabricated 0.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Emitted-key reference (for downstream consumers / tests).  Order is display
# convenience only.  Straight-through keys first, then computed metrics.
# --------------------------------------------------------------------------- #
FUNDAMENTAL_KEYS: List[str] = [
    "currentPrice",
    "shortName",
    "sector",
    "trailingEps",
    "trailingPE",
    "bookValue",
    "priceToBook",
    "dividendYield",
    "payoutRatio",
    "marketCap",
    "beta",
    "returnOnEquity",
    "revenueGrowth",
    "debtToEquity",
    "grossMargins",
    "operatingMargins",
    "currentRatio",
    "heldPercentInstitutions",
]

# --------------------------------------------------------------------------- #
# Alias tables — MODULE CONSTANTS.
# yfinance renames statement line-items across versions; keeping the aliases as
# ordered data means a version drift is a one-line edit here rather than a code
# change scattered through the metric formulas.  Aliases are matched
# case-insensitively with whitespace normalized (see ``_normalize_label``).
# --------------------------------------------------------------------------- #
EQUITY: List[str] = [
    "Stockholders Equity",
    "Total Equity Gross Minority Interest",
    "Total Stockholders Equity",
    "Common Stock Equity",
]
NET_INCOME: List[str] = [
    "Net Income",
    "Net Income Common Stockholders",
    "Net Income From Continuing Operation Net Minority Interest",
]
TOTAL_REVENUE: List[str] = [
    "Total Revenue",
    "Operating Revenue",
]
GROSS_PROFIT: List[str] = [
    "Gross Profit",
]
COST_OF_REVENUE: List[str] = [
    "Cost Of Revenue",
    "Cost Of Goods Sold",
]
OPERATING_INCOME: List[str] = [
    "Operating Income",
    "Total Operating Income As Reported",
    "EBIT",
]
DILUTED_EPS: List[str] = [
    "Diluted EPS",
    "Basic EPS",
]
TOTAL_DEBT: List[str] = [
    "Total Debt",
]
# Fallback components for TOTAL_DEBT when the pre-summed "Total Debt" row is
# absent (see _total_debt).
LONG_TERM_DEBT: List[str] = [
    "Long Term Debt",
]
CURRENT_DEBT: List[str] = [
    "Current Debt",
    "Current Debt And Capital Lease Obligation",
]
# Last-resort COARSE PROXY for total debt: total liabilities overstates debt
# (it includes payables, deferred revenue, etc.) — used only when nothing
# better is present.
TOTAL_LIABILITIES: List[str] = [
    "Total Liabilities Net Minority Interest",
]
CURRENT_ASSETS: List[str] = [
    "Current Assets",
    "Total Current Assets",
]
CURRENT_LIABILITIES: List[str] = [
    "Current Liabilities",
    "Total Current Liabilities",
]
CASH_DIVIDENDS_PAID: List[str] = [
    "Cash Dividends Paid",
    "Common Stock Dividends Paid",
    "Cash Dividend Paid",
]

_NAN = float("nan")


# --------------------------------------------------------------------------- #
# Defensive helpers — pure, unit-testable, NEVER raise.
# --------------------------------------------------------------------------- #
def _normalize_label(label: object) -> str:
    """Lowercase + collapse internal whitespace for case/space-insensitive match."""
    try:
        return " ".join(str(label).split()).lower()
    except Exception:  # pragma: no cover - defensive
        return ""


def _to_float(value: object) -> float:
    """Best-effort float coercion; NaN on anything non-finite or uncoercible."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return _NAN
    if not np.isfinite(out):
        return _NAN
    return out


def _match_row(df: Optional[pd.DataFrame], aliases: List[str]) -> Optional[object]:
    """Return the actual df.index label for the first alias present, else None."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    try:
        norm_to_actual: Dict[str, object] = {}
        for actual in df.index:
            key = _normalize_label(actual)
            # First occurrence wins (top-most row) if labels collide.
            norm_to_actual.setdefault(key, actual)
        for alias in aliases:
            actual = norm_to_actual.get(_normalize_label(alias))
            if actual is not None:
                return actual
    except Exception:  # pragma: no cover - defensive
        return None
    return None


def _row_latest(df: Optional[pd.DataFrame], aliases: List[str]) -> float:
    """
    Newest (leftmost / max-date column) non-NaN value for the first matched alias.

    Columns are period dates in descending order (yfinance convention), so the
    leftmost column is the most recent period.  To be robust against unsorted or
    reversed frames we also try to select the max-date column.  Missing df, no
    alias match, or an all-NaN row → NaN.  Never raises.
    """
    try:
        actual = _match_row(df, aliases)
        if actual is None:
            return _NAN
        row = df.loc[actual]
        if isinstance(row, pd.DataFrame):
            # Duplicate index labels → collapse to the first row.
            row = row.iloc[0]
        row = row.dropna()
        if row.empty:
            return _NAN
        # Prefer the true newest column when columns are datetime-like.
        try:
            cols = pd.to_datetime(pd.Index(row.index), errors="coerce")
            if cols.notna().any():
                newest_pos = int(np.nanargmax(cols.view("int64").astype("float64")))
                return _to_float(row.iloc[newest_pos])
        except Exception:
            pass
        # Fallback: leftmost column (yfinance descending-date convention).
        return _to_float(row.iloc[0])
    except Exception:  # pragma: no cover - defensive
        return _NAN


def _ttm(
    df_quarterly: Optional[pd.DataFrame],
    aliases: List[str],
    df_annual: Optional[pd.DataFrame] = None,
) -> float:
    """
    Trailing-twelve-month sum for a FLOW item (income/cashflow line).

    Sum the trailing 4 quarterly column values of the matched row.  If quarterly
    is unavailable/empty or has < 4 columns, fall back to the latest ANNUAL
    column value from ``df_annual``.  All-missing → NaN.  Never raises.
    """
    try:
        actual = _match_row(df_quarterly, aliases)
        if actual is not None and df_quarterly is not None:
            row = df_quarterly.loc[actual]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            # Order columns newest→oldest so "trailing 4" is the 4 most recent.
            try:
                cols = pd.to_datetime(pd.Index(row.index), errors="coerce")
                if cols.notna().all():
                    order = np.argsort(cols.view("int64"))[::-1]
                    row = row.iloc[order]
            except Exception:
                pass  # assume already descending
            trailing = row.iloc[:4]
            vals = [_to_float(v) for v in trailing.values]
            vals = [v for v in vals if np.isfinite(v)]
            if len(vals) >= 4:
                return float(np.sum(vals[:4]))
            # Fewer than 4 real quarters → fall through to annual fallback.
    except Exception:  # pragma: no cover - defensive
        pass

    # Annual fallback: latest annual column value.
    return _row_latest(df_annual, aliases)


def _prior_annual(df: Optional[pd.DataFrame], aliases: List[str]) -> float:
    """Second-newest annual column value for the matched alias (for growth calcs)."""
    try:
        actual = _match_row(df, aliases)
        if actual is None or df is None:
            return _NAN
        row = df.loc[actual]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        # Order newest→oldest, then take index 1 (the prior period).
        try:
            cols = pd.to_datetime(pd.Index(row.index), errors="coerce")
            if cols.notna().all():
                order = np.argsort(cols.view("int64"))[::-1]
                row = row.iloc[order]
        except Exception:
            pass
        row = row.dropna()
        if row.shape[0] < 2:
            return _NAN
        return _to_float(row.iloc[1])
    except Exception:  # pragma: no cover - defensive
        return _NAN


def _total_debt(balance_sheet: Optional[pd.DataFrame]) -> float:
    """
    Total debt with graceful fallbacks:
      1. "Total Debt" pre-summed row.
      2. Long Term Debt + Current Debt (if either present).
      3. Total Liabilities Net Minority Interest — COARSE PROXY (overstates debt;
         includes payables/deferred items).  Last resort only.
    NaN when none available.  Never raises.
    """
    try:
        td = _row_latest(balance_sheet, TOTAL_DEBT)
        if np.isfinite(td):
            return td
        ltd = _row_latest(balance_sheet, LONG_TERM_DEBT)
        cd = _row_latest(balance_sheet, CURRENT_DEBT)
        parts = [p for p in (ltd, cd) if np.isfinite(p)]
        if parts:
            return float(np.sum(parts))
        # Coarse proxy — total liabilities.
        return _row_latest(balance_sheet, TOTAL_LIABILITIES)
    except Exception:  # pragma: no cover - defensive
        return _NAN


# --------------------------------------------------------------------------- #
# Public API — FROZEN CONTRACT.
# --------------------------------------------------------------------------- #
def compute_fundamentals(
    ticker: str,
    *,
    price: float,
    shares_current: float,
    shares_diluted: float,
    income_stmt: pd.DataFrame,
    income_stmt_quarterly: pd.DataFrame,
    balance_sheet: pd.DataFrame,
    cashflow: pd.DataFrame,
    cashflow_quarterly: pd.DataFrame,
    dividends: Optional[pd.Series],
    inst_holders: Optional[pd.DataFrame],
    stock_returns: Optional[pd.Series],
    market_returns: Optional[pd.Series],
    sector: str = "N/A",
    company_name: str = "",
) -> Dict[str, float]:
    """
    Compute 15 fundamental metrics + 3 straight-through fields.

    Returns a dict keyed by yfinance ``.info`` names.  Every metric degrades
    independently to ``float("nan")`` on missing/bad input (CONSTRAINT #4);
    this function NEVER raises.

    See module docstring for the two scale-critical rules (dividendYield is a
    fraction; debtToEquity is ×100).
    """
    out: Dict[str, float] = {}

    price_f = _to_float(price)
    shares_cur_f = _to_float(shares_current)
    shares_dil_f = _to_float(shares_diluted)

    # --- Straight-through fields (typed as object in the dict; that's fine). --
    out["currentPrice"] = price_f
    out["shortName"] = company_name  # type: ignore[assignment]
    out["sector"] = sector  # type: ignore[assignment]

    # Shared intermediates (each guarded; NaN-safe downstream).
    equity_latest = _row_latest(balance_sheet, EQUITY)
    net_income_ttm = _ttm(income_stmt_quarterly, NET_INCOME, income_stmt)
    revenue_ttm = _ttm(income_stmt_quarterly, TOTAL_REVENUE, income_stmt)

    # --- 1. trailingEps = TTM diluted EPS ---------------------------------- #
    trailing_eps = _NAN
    try:
        eps = _ttm(income_stmt_quarterly, DILUTED_EPS, income_stmt)
        if not np.isfinite(eps):
            # Fallback: TTM net income / diluted shares.
            if np.isfinite(net_income_ttm) and np.isfinite(shares_dil_f) and shares_dil_f > 0:
                eps = net_income_ttm / shares_dil_f
        trailing_eps = eps if np.isfinite(eps) else _NAN
    except Exception:
        trailing_eps = _NAN
    out["trailingEps"] = trailing_eps

    # --- 2. trailingPE = price / EPS  (NaN when EPS <= 0, mirrors Yahoo) ---- #
    try:
        if np.isfinite(trailing_eps) and trailing_eps > 0 and np.isfinite(price_f) and price_f > 0:
            out["trailingPE"] = price_f / trailing_eps
        else:
            out["trailingPE"] = _NAN
    except Exception:
        out["trailingPE"] = _NAN

    # --- 3. bookValue = equity / shares_current ---------------------------- #
    book_value = _NAN
    try:
        if np.isfinite(equity_latest) and equity_latest > 0 and np.isfinite(shares_cur_f) and shares_cur_f > 0:
            book_value = equity_latest / shares_cur_f
    except Exception:
        book_value = _NAN
    out["bookValue"] = book_value

    # --- 4. priceToBook = price / bookValue (NaN when bookValue <= 0) ------ #
    try:
        if np.isfinite(book_value) and book_value > 0 and np.isfinite(price_f) and price_f > 0:
            out["priceToBook"] = price_f / book_value
        else:
            out["priceToBook"] = _NAN
    except Exception:
        out["priceToBook"] = _NAN

    # --- 5. dividendYield = trailing-365d dividends / price  (FRACTION) ----- #
    try:
        dy = _NAN
        if (
            dividends is not None
            and isinstance(dividends, pd.Series)
            and not dividends.empty
            and np.isfinite(price_f)
            and price_f > 0
        ):
            div = dividends.dropna()
            if not div.empty:
                idx = pd.to_datetime(div.index, errors="coerce")
                div = div[idx.notna()]
                idx = idx[idx.notna()]
                if len(idx) > 0:
                    last_date = idx.max()
                    window_start = last_date - pd.Timedelta(days=365)
                    mask = (idx > window_start) & (idx <= last_date)
                    ttm_div = float(np.nansum(div.values[np.asarray(mask)]))
                    if ttm_div > 0:
                        dy = ttm_div / price_f  # fraction — do NOT ×100
        out["dividendYield"] = dy
    except Exception:
        out["dividendYield"] = _NAN

    # --- 6. payoutRatio = abs(TTM dividends paid) / TTM net income --------- #
    try:
        pr = _NAN
        div_paid_ttm = _ttm(cashflow_quarterly, CASH_DIVIDENDS_PAID, cashflow)
        if np.isfinite(div_paid_ttm) and np.isfinite(net_income_ttm) and net_income_ttm > 0:
            # abs() is MANDATORY: Cash Dividends Paid is a negative cash outflow.
            pr = abs(div_paid_ttm) / net_income_ttm
        out["payoutRatio"] = pr
    except Exception:
        out["payoutRatio"] = _NAN

    # --- 7. marketCap = price * shares_current ----------------------------- #
    try:
        if np.isfinite(price_f) and price_f > 0 and np.isfinite(shares_cur_f) and shares_cur_f > 0:
            out["marketCap"] = price_f * shares_cur_f
        else:
            out["marketCap"] = _NAN
    except Exception:
        out["marketCap"] = _NAN

    # --- 8. beta = Cov(stock, mkt) / Var(mkt), >= 60 overlapping obs -------- #
    try:
        beta = _NAN
        if (
            stock_returns is not None
            and market_returns is not None
            and isinstance(stock_returns, pd.Series)
            and isinstance(market_returns, pd.Series)
            and not stock_returns.empty
            and not market_returns.empty
        ):
            joined = pd.concat(
                [stock_returns.rename("s"), market_returns.rename("m")],
                axis=1,
                join="inner",
            ).dropna()
            if joined.shape[0] >= 60:
                s = joined["s"].to_numpy(dtype="float64")
                m = joined["m"].to_numpy(dtype="float64")
                cov = np.cov(s, m)  # sample covariance matrix (ddof=1)
                var_m = cov[1, 1]
                if np.isfinite(var_m) and var_m > 0:
                    b = cov[0, 1] / var_m  # self-consistent ddof
                    if np.isfinite(b):
                        beta = float(b)
        out["beta"] = beta
    except Exception:
        out["beta"] = _NAN

    # --- 9. returnOnEquity = TTM net income / equity  (FRACTION) ----------- #
    try:
        if np.isfinite(net_income_ttm) and np.isfinite(equity_latest) and equity_latest > 0:
            out["returnOnEquity"] = net_income_ttm / equity_latest
        else:
            out["returnOnEquity"] = _NAN
    except Exception:
        out["returnOnEquity"] = _NAN

    # --- 10. revenueGrowth = (TTM rev - prior-TTM rev) / prior-TTM rev ------ #
    try:
        rg = _NAN
        # Approx prior-TTM as the second annual Total Revenue column (quarterly
        # 8-quarter history is rarely complete on the free tier).
        prior_rev = _prior_annual(income_stmt, TOTAL_REVENUE)
        cur_rev = revenue_ttm
        if not np.isfinite(cur_rev):
            cur_rev = _row_latest(income_stmt, TOTAL_REVENUE)
        if np.isfinite(cur_rev) and np.isfinite(prior_rev) and prior_rev > 0:
            rg = (cur_rev - prior_rev) / prior_rev
        out["revenueGrowth"] = rg
    except Exception:
        out["revenueGrowth"] = _NAN

    # --- 11. debtToEquity = (total_debt / equity) * 100  (×100!) ----------- #
    try:
        dte = _NAN
        total_debt = _total_debt(balance_sheet)
        if np.isfinite(total_debt) and np.isfinite(equity_latest) and equity_latest > 0:
            dte = (total_debt / equity_latest) * 100.0  # emit e.g. 150.0
        out["debtToEquity"] = dte
    except Exception:
        out["debtToEquity"] = _NAN

    # --- 12. grossMargins = gross_profit / revenue  (FRACTION) ------------- #
    try:
        gm = _NAN
        # Prefer TTM revenue for consistency; fall back to latest annual.
        rev = revenue_ttm if np.isfinite(revenue_ttm) else _row_latest(income_stmt, TOTAL_REVENUE)
        gross_profit = _ttm(income_stmt_quarterly, GROSS_PROFIT, income_stmt)
        if not np.isfinite(gross_profit):
            # Fallback: Total Revenue - Cost Of Revenue.
            cogs = _ttm(income_stmt_quarterly, COST_OF_REVENUE, income_stmt)
            if np.isfinite(rev) and np.isfinite(cogs):
                gross_profit = rev - cogs
        if np.isfinite(gross_profit) and np.isfinite(rev) and rev > 0:
            gm = gross_profit / rev
        out["grossMargins"] = gm
    except Exception:
        out["grossMargins"] = _NAN

    # --- 13. operatingMargins = operating_income / revenue  (FRACTION) ----- #
    try:
        om = _NAN
        rev = revenue_ttm if np.isfinite(revenue_ttm) else _row_latest(income_stmt, TOTAL_REVENUE)
        op_income = _ttm(income_stmt_quarterly, OPERATING_INCOME, income_stmt)
        if np.isfinite(op_income) and np.isfinite(rev) and rev > 0:
            om = op_income / rev
        out["operatingMargins"] = om
    except Exception:
        out["operatingMargins"] = _NAN

    # --- 14. currentRatio = current_assets / current_liabilities ----------- #
    try:
        cr = _NAN
        ca = _row_latest(balance_sheet, CURRENT_ASSETS)
        cl = _row_latest(balance_sheet, CURRENT_LIABILITIES)
        if np.isfinite(ca) and np.isfinite(cl) and cl > 0:
            cr = ca / cl
        out["currentRatio"] = cr
    except Exception:
        out["currentRatio"] = _NAN

    # --- 15. heldPercentInstitutions = sum of inst ownership % (FRACTION) --- #
    # NOTE: yfinance returns only ~top-10 institutional holders, so this is a
    # TOP-N APPROXIMATION of true institutional ownership, not the exact figure.
    try:
        hpi = _NAN
        if (
            inst_holders is not None
            and isinstance(inst_holders, pd.DataFrame)
            and not inst_holders.empty
        ):
            col = None
            wanted = {"% out", "pctheld", "% held", "percent held", "pct held"}
            for c in inst_holders.columns:
                if _normalize_label(c) in wanted:
                    col = c
                    break
            if col is not None:
                series = pd.to_numeric(inst_holders[col], errors="coerce").dropna()
                if not series.empty:
                    total = float(series.sum())
                    # Detect percent-vs-fraction: if the max single holder value
                    # is > 1 it is expressed as a percent (e.g. 8.5) → /100.
                    if float(series.max()) > 1.0:
                        total = total / 100.0
                    if np.isfinite(total):
                        hpi = total
        out["heldPercentInstitutions"] = hpi
    except Exception:
        out["heldPercentInstitutions"] = _NAN

    return out


if __name__ == "__main__":  # pragma: no cover - manual smoke trace
    # Synthetic sanity check per the frozen spec:
    #   equity=1000, shares=100 -> bookValue=10; price=150 -> priceToBook=15
    #   total_debt=1500 -> debtToEquity=150.0 (NOT 1.5)
    #   net income TTM=200, equity=1000 -> returnOnEquity=0.20
    #   dividends 4.00/yr, price=150 -> dividendYield ~= 0.0267
    _dates_a = pd.to_datetime(["2025-12-31", "2024-12-31"])
    _dates_q = pd.to_datetime(
        ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"]
    )
    _bs = pd.DataFrame(
        {_dates_a[0]: [1000.0, 1500.0, 800.0, 400.0], _dates_a[1]: [900.0, 1400.0, 700.0, 350.0]},
        index=["Stockholders Equity", "Total Debt", "Current Assets", "Current Liabilities"],
    )
    _is_q = pd.DataFrame(
        {d: [50.0, 250.0, 30.0] for d in _dates_q},
        index=["Net Income", "Total Revenue", "Operating Income"],
    )
    _is_a = pd.DataFrame(
        {_dates_a[0]: [200.0, 1000.0], _dates_a[1]: [180.0, 900.0]},
        index=["Net Income", "Total Revenue"],
    )
    _div = pd.Series([1.0, 1.0, 1.0, 1.0], index=pd.to_datetime(
        ["2025-02-01", "2025-05-01", "2025-08-01", "2025-11-01"]))
    _res = compute_fundamentals(
        "TEST",
        price=150.0,
        shares_current=100.0,
        shares_diluted=100.0,
        income_stmt=_is_a,
        income_stmt_quarterly=_is_q,
        balance_sheet=_bs,
        cashflow=pd.DataFrame(),
        cashflow_quarterly=pd.DataFrame(),
        dividends=_div,
        inst_holders=None,
        stock_returns=None,
        market_returns=None,
        sector="Technology",
        company_name="Test Co",
    )
    for _k in FUNDAMENTAL_KEYS:
        print(f"{_k:28s} = {_res.get(_k)}")

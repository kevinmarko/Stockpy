"""
data/fmp_fundamentals.py — Pure, I/O-free FMP → yfinance-key mapping
======================================================================
Maps Financial Modeling Prep (FMP) REST responses (already fetched by the
caller via ``data/fmp_client.py``) onto the SAME yfinance ``.info``-style key
set that ``data/yahoo_fundamentals.py::compute_fundamentals`` emits, so every
downstream consumer of ``FundamentalDataDTO.from_raw_dict()`` is unchanged
regardless of which fundamentals backend actually served a given cycle.

This module is a direct structural mirror of ``data/yahoo_fundamentals.py``
(read that file's module docstring first) and inherits its two governing
rules:

Two SCALE-CRITICAL rules (do not "fix" these — downstream depends on them)
-------------------------------------------------------------------------
* ``dividendYield`` is emitted **as a FRACTION** (0.0257, NOT 2.57). Unlike
  the Yahoo path (which computes this itself from a raw dividend series),
  FMP hands us an already-computed ``dividendYieldTTM``. If that value is
  ``> 1.0`` it is emitted as ``NaN`` **and logged at ERROR** rather than
  silently divided by 100 — a wrong guess here is a 100x error feeding
  straight into Gordon Fair Value and the ``> 0.03`` yield score gate.
* ``debtToEquity`` is emitted **multiplied by 100** (e.g. 150.0, NOT 1.5) —
  two downstream consumers (``processing_engine.py``,
  ``data/historical_store.py::_raw_to_typed_fundamentals``) divide by 100.

NaN-not-zero discipline (CONSTRAINT #4)
---------------------------------------
Every metric is computed in its OWN try/except and degrades independently to
``float("nan")`` on any missing/bad input. A missing field never fabricates a
``0.0`` and never nukes any other metric. This is the highest-scrutiny module
in the whole FMP integration: a wrong scale conversion here corrupts Gordon
Fair Value, the multifactor value z-score, and every downstream sizing
decision built on top of it.

Never imports ``requests`` or ``data.fmp_client`` — never touches the network.
Callers (``FMPProvider`` in ``data/market_data.py``) fetch every payload and
pass it in as a plain dict/list, so this module is testable with zero mocking.

Response-shape normalisation
-----------------------------
``data/fmp_client.py``'s wrappers return the RAW parsed JSON, which for every
endpoint consumed here is typically a list wrapping one dict (e.g.
``[{"symbol": "AAPL", ...}]``), occasionally a bare dict. :func:`_first`
handles both uniformly: a non-empty list yields its first element, a dict
passes through unchanged, anything else (``None``, ``[]``, ``{}``) is treated
as "no data" and every field sourced from it degrades to its own default.

Emitted key set
----------------
Exactly ``yahoo_fundamentals.FUNDAMENTAL_KEYS`` **minus**
``"heldPercentInstitutions"`` (no institutional-ownership feed exists on FMP
Starter — see ``risk/etf_transmission.py``'s existing fallback for that gap)
**plus** ``"sharesOutstanding"`` (real float-share count from
``/shares-float``, additive) and ``"_source"`` (constant ``"fmp"``, consumed
by ``data/historical_store.py::_source_name`` for per-response provenance).
``FUNDAMENTAL_KEYS`` is imported from ``data.yahoo_fundamentals`` — never
hand-copied — so the two modules cannot silently drift apart.

A LIVE CORRECTION to the original design doc, found by hand
-------------------------------------------------------------
The wave-1 design brief specified ``bookValue`` as
``key_metrics_ttm["bookValuePerShareTTM"]``. Probed live against a real
Starter-tier FMP account (2026-07-31, via the FMP MCP connector, symbol
AAPL): ``key-metrics-ttm`` carries NO ``bookValue*`` field at all on this
plan tier — every book-value field (``bookValuePerShareTTM``,
``tangibleBookValuePerShareTTM``, ``shareholdersEquityPerShareTTM``) lives on
the RATIOS-TTM payload instead. Implementing the brief literally would make
``bookValue``/``priceToBook`` — and therefore ``Quality_Z`` — permanently NaN
in production. This module reads ``bookValuePerShareTTM`` from
``ratios_ttm``, not ``key_metrics_ttm``, based on that live verification.

A SECOND live correction, found the same way (2026-07-31, symbols KO/JNJ)
------------------------------------------------------------------------
``/income-statement-ttm`` — the sole source ``trailingEps`` was designed to
read (``epsDiluted``) — returns ``ACCESS DENIED: requires Ultimate or
Enterprise`` on a Starter-tier account. Left as originally designed, this
means ``trailingEps`` is ALWAYS ``NaN`` in production, which in turn means
``trailingPE`` is ALWAYS ``NaN`` too (the sign-gate at
``np.isfinite(trailing_eps) and trailing_eps > 0`` unconditionally fails on
NaN) — for every symbol, not just loss-makers, even though ``ratios-ttm``'s
own ``priceToEarningsRatioTTM`` is sitting right there and IS
Starter-accessible. That silently defeats ``earnings_yield`` (the multifactor
value z-score's second leg) for the whole fundamentals pipeline whenever
FMP is the active source. Confirmed by running this module's real code
against real KO/JNJ ``ratios-ttm``/``income-statement-ttm`` responses: with
``income_statement_ttm=None`` (the genuine Starter-tier condition),
``trailingEps``/``trailingPE`` both came back NaN despite KO's real P/E
(26.30) and EPS-equivalent (3.33) being present in the ``ratios_ttm`` payload
this module already receives.

Fix: ``trailingEps`` tries ``income_statement_ttm["epsDiluted"]`` FIRST
(kept as primary — it is the more precisely-correct "diluted EPS" concept,
and a higher account tier, or a future FMP plan change, may make it
available) and falls back to ``ratios_ttm["netIncomePerShareTTM"]`` only
when that is unavailable. The fallback is net income per share, not
literally diluted EPS — they can differ slightly where dilutive securities
exist — but it is Starter-accessible, real, and a far closer approximation
than a value that is permanently and unconditionally NaN. Both legs still
independently degrade to NaN (never a fabricated 0.0) if neither source
supplies a value.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from data.yahoo_fundamentals import FUNDAMENTAL_KEYS

logger = logging.getLogger(__name__)

_NAN = float("nan")

# --------------------------------------------------------------------------- #
# Emitted-key reference, derived programmatically from yahoo_fundamentals's
# own list so the two modules cannot silently drift apart (CLAUDE.md/plan
# requirement — never hand-copy this set).
# --------------------------------------------------------------------------- #
FMP_FUNDAMENTAL_KEYS: List[str] = [
    k for k in FUNDAMENTAL_KEYS if k != "heldPercentInstitutions"
] + ["sharesOutstanding"]

_SOURCE = "fmp"


# --------------------------------------------------------------------------- #
# Defensive helpers — pure, unit-testable, NEVER raise.
# --------------------------------------------------------------------------- #
def _first(payload: Any) -> Optional[Dict[str, Any]]:
    """Normalise an FMP response to a single dict, or ``None`` when empty.

    FMP's REST wrappers return the raw parsed JSON, which for every endpoint
    consumed here is typically a list wrapping one dict, occasionally a bare
    dict. A non-empty list → its first element; a dict → itself; anything
    else (``None``, ``[]``, ``{}``, a string, ...) → ``None`` ("no data" for
    every field sourced from it). Never raises.
    """
    try:
        if isinstance(payload, list):
            return payload[0] if payload and isinstance(payload[0], dict) else None
        if isinstance(payload, dict):
            return payload if payload else None
    except Exception:  # pragma: no cover - defensive
        return None
    return None


def _to_float(value: object) -> float:
    """Best-effort float coercion; NaN on anything non-finite or uncoercible."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return _NAN
    if not np.isfinite(out):
        return _NAN
    return out


def _dividends_to_series(dividends: Optional[List[Any]]) -> pd.Series:
    """Convert FMP's ``/dividends`` payload (a list of ``{date, dividend}``
    -shaped records) into a ``pandas.Series`` indexed by date, ascending.

    Rows missing either field, or with an unparseable date, are skipped
    individually rather than aborting the whole conversion. Never raises —
    returns an empty float Series on any structural problem.
    """
    try:
        dates: List[Any] = []
        values: List[Any] = []
        for row in dividends or []:
            if not isinstance(row, dict):
                continue
            d = row.get("date")
            v = row.get("dividend")
            if d is None or v is None:
                continue
            dates.append(d)
            values.append(v)
        if not dates:
            return pd.Series(dtype="float64")
        idx = pd.to_datetime(pd.Index(dates), errors="coerce")
        ser = pd.Series(values, index=idx, dtype="float64")
        ser = ser[~ser.index.isna()]
        return ser.sort_index()
    except Exception:  # pragma: no cover - defensive
        return pd.Series(dtype="float64")


# --------------------------------------------------------------------------- #
# Public API — beta, mirroring yahoo_fundamentals's Cov/Var definition.
# --------------------------------------------------------------------------- #
def compute_beta(
    stock_returns: Optional[pd.Series],
    market_returns: Optional[pd.Series],
    *,
    min_obs: int = 60,
) -> float:
    """``Cov(stock, market) / Var(market)`` over the INNER-JOINED overlap of
    the two return series, requiring at least ``min_obs`` overlapping dates.

    Deliberate duplication of ``data/yahoo_fundamentals.py``'s beta
    computation (same alignment convention — ``pd.concat(..., join="inner")``
    then ``.dropna()``, same >= observation floor, same sample covariance via
    ``np.cov`` with ddof=1) rather than a cross-file refactor, to keep this
    module's diff isolated from a file this agent does not own. A dedicated
    parity test feeds an identical synthetic fixture through both
    implementations and asserts numeric equality to 1e-9.

    Returns ``float("nan")`` — never raises — when either series is missing,
    empty, wrongly typed, or the overlap is smaller than ``min_obs``.
    """
    try:
        if stock_returns is None or market_returns is None:
            return _NAN
        if not isinstance(stock_returns, pd.Series) or not isinstance(market_returns, pd.Series):
            return _NAN
        if stock_returns.empty or market_returns.empty:
            return _NAN
        joined = pd.concat(
            [stock_returns.rename("s"), market_returns.rename("m")],
            axis=1,
            join="inner",
        ).dropna()
        if joined.shape[0] < min_obs:
            return _NAN
        s = joined["s"].to_numpy(dtype="float64")
        m = joined["m"].to_numpy(dtype="float64")
        cov = np.cov(s, m)  # sample covariance matrix (ddof=1)
        var_m = cov[1, 1]
        if not np.isfinite(var_m) or var_m <= 0:
            return _NAN
        b = cov[0, 1] / var_m
        return float(b) if np.isfinite(b) else _NAN
    except Exception:  # pragma: no cover - defensive
        return _NAN


# --------------------------------------------------------------------------- #
# Public API — FROZEN CONTRACT.
# --------------------------------------------------------------------------- #
def map_fundamentals(
    ticker: str,
    *,
    quote: Optional[dict],
    profile: Optional[dict],
    key_metrics_ttm: Optional[dict],
    ratios_ttm: Optional[dict],
    income_statement_ttm: Optional[dict],
    shares_float: Optional[dict] = None,
    dividends: Optional[list] = None,
    beta: Optional[float] = None,
) -> Dict[str, Any]:
    """Map raw FMP REST payloads onto the yfinance ``.info``-style key set.

    Every argument is the RAW parsed JSON returned by the matching
    ``data/fmp_client.py`` wrapper (``quote()``, ``profile()``,
    ``key_metrics_ttm()``, ``ratios_ttm()``, ``income_statement_ttm()``,
    ``shares_float()``, ``dividends()``) — this function performs no I/O and
    never imports ``requests`` or ``data.fmp_client``. ``beta`` is a
    precomputed float (see :func:`compute_beta`), passed straight through:
    this module does NOT read ``profile["beta"]`` (a vendor 5y-monthly
    number using a different definition than the platform's own
    ``BETA_LOOKBACK_DAYS`` Cov/Var beta — swapping definitions would move the
    ``Beta`` column, the Quality Score bump, and the low-vol factor well past
    the platform's 1e-5 drift budget).

    Returns a dict keyed by :data:`FMP_FUNDAMENTAL_KEYS` plus ``"_source"``
    (always ``"fmp"``) and, only when ``dividends`` was supplied,
    ``"_dividends_series"`` (a ``pandas.Series`` of real dividend history —
    leading underscore marks it as internal plumbing, not part of the
    yfinance-mirroring key contract; callers may hand it to
    ``FundamentalDataDTO``'s dividend-growth path instead of the fabricated
    2% fallback in ``dto_models.py``). Every metric degrades independently to
    ``float("nan")`` on missing/bad input (CONSTRAINT #4); this function
    NEVER raises.
    """
    out: Dict[str, Any] = {}

    q = _first(quote)
    p = _first(profile)
    km = _first(key_metrics_ttm)
    r = _first(ratios_ttm)
    inc = _first(income_statement_ttm)
    sf = _first(shares_float)

    # --- currentPrice = quote.price, else profile.price ---------------- #
    try:
        price_raw = None
        if q is not None:
            price_raw = q.get("price")
        if price_raw is None and p is not None:
            price_raw = p.get("price")
        out["currentPrice"] = _to_float(price_raw)
    except Exception:
        out["currentPrice"] = _NAN

    # --- shortName = profile.companyName (NOT longName) ----------------- #
    try:
        out["shortName"] = ((p or {}).get("companyName") or "")  # type: ignore[assignment]
    except Exception:
        out["shortName"] = ""  # type: ignore[assignment]

    # --- sector = profile.sector, "N/A" if absent ------------------------ #
    # Deliberately NOT validated against sector_descriptions.yaml here --
    # that is a diagnostic check for the operator, not this module's job.
    try:
        sector_raw = (p or {}).get("sector")
        out["sector"] = sector_raw if sector_raw else "N/A"  # type: ignore[assignment]
    except Exception:
        out["sector"] = "N/A"  # type: ignore[assignment]

    # --- trailingEps = TTM DILUTED eps (not basic) ----------------------- #
    # income_statement_ttm.epsDiluted is Ultimate/Enterprise-only on a real
    # Starter account (live-confirmed 2026-07-31 -- see the module
    # docstring's second LIVE CORRECTION) and is therefore always None/{}
    # there; ratios_ttm.netIncomePerShareTTM is Starter-accessible and used
    # as a fallback so trailingPE/earnings_yield aren't permanently NaN.
    trailing_eps = _NAN
    try:
        trailing_eps = _to_float((inc or {}).get("epsDiluted"))
    except Exception:
        trailing_eps = _NAN
    if not np.isfinite(trailing_eps):
        try:
            trailing_eps = _to_float((r or {}).get("netIncomePerShareTTM"))
        except Exception:
            trailing_eps = _NAN
    out["trailingEps"] = trailing_eps

    # --- trailingPE = ratios_ttm.priceToEarningsRatioTTM,               -- #
    # --- NaN when resolved trailingEps <= 0 (mirrors Yahoo; FMP returns  -- #
    # --- a NEGATIVE PE for loss-makers instead of NaN)                   -- #
    try:
        pe = _to_float((r or {}).get("priceToEarningsRatioTTM"))
        if np.isfinite(trailing_eps) and trailing_eps > 0 and np.isfinite(pe):
            out["trailingPE"] = pe
        else:
            out["trailingPE"] = _NAN
    except Exception:
        out["trailingPE"] = _NAN

    # --- bookValue = ratios_ttm.bookValuePerShareTTM (NOT tangible) ----- #
    # See the module docstring's "LIVE CORRECTION" note: this field does
    # NOT exist on key_metrics_ttm on a live Starter-tier account.
    book_value = _NAN
    try:
        book_value = _to_float((r or {}).get("bookValuePerShareTTM"))
    except Exception:
        book_value = _NAN
    out["bookValue"] = book_value

    # --- priceToBook = ratios_ttm.priceToBookRatioTTM,                  -- #
    # --- NaN when resolved bookValue <= 0                                -- #
    try:
        ptb = _to_float((r or {}).get("priceToBookRatioTTM"))
        if np.isfinite(book_value) and book_value > 0 and np.isfinite(ptb):
            out["priceToBook"] = ptb
        else:
            out["priceToBook"] = _NAN
    except Exception:
        out["priceToBook"] = _NAN

    # --- dividendYield = ratios_ttm.dividendYieldTTM (FRACTION),        -- #
    # --- unit guard: > 1.0 -> NaN + ERROR, NEVER auto-divide by 100      -- #
    try:
        dy = _to_float((r or {}).get("dividendYieldTTM"))
        if not np.isfinite(dy):
            out["dividendYield"] = _NAN
        elif dy > 1.0:
            logger.error(
                "fmp_fundamentals(%s): ratios_ttm.dividendYieldTTM=%.6f is "
                "> 1.0 -- looks like a PERCENT, not the fraction this "
                "platform expects. Refusing to silently divide by 100 (a "
                "wrong guess here is a 100x error into Gordon Fair Value "
                "and the yield score gate); emitting NaN instead.",
                ticker, dy,
            )
            out["dividendYield"] = _NAN
        else:
            out["dividendYield"] = dy
    except Exception:
        out["dividendYield"] = _NAN

    # --- payoutRatio = ratios_ttm.dividendPayoutRatioTTM (fraction),    -- #
    # --- no guard: >1.0 is a legitimate unsustainable payer              -- #
    try:
        out["payoutRatio"] = _to_float((r or {}).get("dividendPayoutRatioTTM"))
    except Exception:
        out["payoutRatio"] = _NAN

    # --- marketCap = profile.marketCap (prefer over /market-cap: --------- #
    # --- profile is already fetched, saves a request per symbol) -------- #
    try:
        out["marketCap"] = _to_float((p or {}).get("marketCap"))
    except Exception:
        out["marketCap"] = _NAN

    # --- beta = the precomputed kwarg, passed straight through ---------- #
    # --- (NOT profile.beta -- see the function docstring) ---------------- #
    try:
        out["beta"] = _to_float(beta) if beta is not None else _NAN
    except Exception:
        out["beta"] = _NAN

    # --- returnOnEquity = key_metrics_ttm.returnOnEquityTTM (fraction) -- #
    try:
        out["returnOnEquity"] = _to_float((km or {}).get("returnOnEquityTTM"))
    except Exception:
        out["returnOnEquity"] = _NAN

    # --- debtToEquity = ratios_ttm.debtToEquityRatioTTM * 100 (x100!) -- #
    try:
        dte = _to_float((r or {}).get("debtToEquityRatioTTM"))
        out["debtToEquity"] = dte * 100.0 if np.isfinite(dte) else _NAN
    except Exception:
        out["debtToEquity"] = _NAN

    # --- grossMargins = ratios_ttm.grossProfitMarginTTM (fraction) ----- #
    try:
        out["grossMargins"] = _to_float((r or {}).get("grossProfitMarginTTM"))
    except Exception:
        out["grossMargins"] = _NAN

    # --- operatingMargins = ratios_ttm.operatingProfitMarginTTM -------- #
    try:
        out["operatingMargins"] = _to_float((r or {}).get("operatingProfitMarginTTM"))
    except Exception:
        out["operatingMargins"] = _NAN

    # --- currentRatio = ratios_ttm.currentRatioTTM ---------------------- #
    try:
        out["currentRatio"] = _to_float((r or {}).get("currentRatioTTM"))
    except Exception:
        out["currentRatio"] = _NAN

    # --- sharesOutstanding = shares_float.outstandingShares (additive) - #
    # Verified live (2026-07-31, FMP MCP connector, symbol AAPL) that
    # "outstandingShares" is the correct field name on /shares-float.
    try:
        out["sharesOutstanding"] = _to_float((sf or {}).get("outstandingShares"))
    except Exception:
        out["sharesOutstanding"] = _NAN

    # --- _source: constant, always emitted ------------------------------- #
    out["_source"] = _SOURCE

    # --- _dividends_series: internal plumbing, only when dividends given - #
    if dividends is not None:
        out["_dividends_series"] = _dividends_to_series(dividends)

    return out


if __name__ == "__main__":  # pragma: no cover - manual smoke trace
    # Synthetic sanity check mirroring yahoo_fundamentals.py's own trace:
    #   debtToEquityRatioTTM=1.5 -> debtToEquity=150.0 (NOT 1.5)
    #   dividendYieldTTM=0.0257 -> dividendYield=0.0257 (fraction, unchanged)
    _res = map_fundamentals(
        "TEST",
        quote={"price": 150.0},
        profile={
            "companyName": "Test Co", "sector": "Technology",
            "marketCap": 1_500_000.0, "price": 150.0,
        },
        key_metrics_ttm={"returnOnEquityTTM": 0.20},
        ratios_ttm={
            "priceToEarningsRatioTTM": 15.0,
            "bookValuePerShareTTM": 10.0,
            "priceToBookRatioTTM": 15.0,
            "dividendYieldTTM": 0.0257,
            "dividendPayoutRatioTTM": 0.30,
            "grossProfitMarginTTM": 0.45,
            "operatingProfitMarginTTM": 0.25,
            "debtToEquityRatioTTM": 1.5,
            "currentRatioTTM": 1.8,
        },
        income_statement_ttm={"epsDiluted": 10.0},
        shares_float={"outstandingShares": 100_000.0},
        beta=1.1,
    )
    for _k in FMP_FUNDAMENTAL_KEYS:
        print(f"{_k:28s} = {_res.get(_k)}")

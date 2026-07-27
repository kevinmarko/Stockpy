"""
data/etf_holdings.py — ETF Constituent Holdings Ingestion
==========================================================
Grounded in Ben-David, Franzoni & Moussawi (2018), "Do ETFs Increase
Volatility?", *Journal of Finance* 73(6):2471-2535 — ETF arbitrage
propagates a shock in one constituent to its otherwise-healthy peers via
the creation/redemption basket, so a heavily ETF-wrapped name carries extra
non-diversifiable variance that its own fundamentals do not explain. The
paper's own exposure measure is a quarterly ownership share (ETF-held
shares / shares outstanding), which is exactly the variable this module
supplies.

**Nothing in the platform consumes this yet.** This is a self-contained
data-layer capability: no `SignalModule`, no `settings.SIGNAL_WEIGHTS`
entry, no `config.COLUMN_SCHEMA` column, no orchestrator call site. It
populates a cache table and returns a typed result; a later change wires a
consumer on top.

Master gate
-----------
`settings.ETF_HOLDINGS_ENABLED` (default **False**) is checked FIRST, before
any other work: `get_etf_holdings()` returns `{}` immediately with ZERO
network calls and zero DB reads. A fresh clone, CI, and every existing
operator therefore never touch EDGAR unless they explicitly opt in.

PRIMARY source: SEC N-PORT (NPORT-P) via EDGAR
-----------------------------------------------
Registered funds file Form N-PORT monthly and disclose the third month of
each quarter publicly (`NPORT-P`), as a fixed-schema XML document. This is a
regulatory filing, not a vendor convenience file: the schema is stable, the
data is free and in the public domain, and access is governed by SEC's
published fair-access policy rather than a scrape-tolerance budget.

**Honest freshness limitation — do not oversell this.** An N-PORT filing
covers three month-ends and is published roughly 60 days after the quarter
ends. Holdings served by this module therefore run **1-5 months stale**,
always. That is acceptable for the shares-held / ownership-share stock
variable the Ben-David et al. measure is built from (their own measure is
quarterly) and it is *not* acceptable as a real-time basket composition
feed. Any consumer that needs today's basket must not use this module.

All SEC HTTP goes through `data/edgar_fundamentals.py`'s existing client —
`_throttle()`, `_http_get()`, `get_cik()`. This is mandatory, not stylistic:
that module holds a process-wide throttle lock that serializes request
*issuance* across threads to stay inside SEC's ~10 req/s courtesy limit. A
second, independently-throttled SEC client in the same process would not
just risk this module getting rate-limited — it would blow the shared budget
and throttle the platform's EXISTING fundamentals-backfill and 8-K paths
too. Reuse, never re-implement.

Network-call budget (only when enabled)
----------------------------------------
Per process lifetime: 1 fetch of `company_tickers_mf.json` (ticker → CIK +
series id), plus at most 1 fetch of `company_tickers.json` via
`edgar_fundamentals.get_cik` for symbols absent from the fund file. Per ETF
per refresh: 1 submissions-index fetch + at most `_MAX_FILINGS_PROBED` (4)
primary-document fetches — a trust files one NPORT-P per series, so several
may be probed before the requested series is found. A cache hit inside
`ETF_HOLDINGS_REFRESH_DAYS` makes ZERO requests. The whole loop is bounded
by `settings.ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE` and
`settings.ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD`.

Causality / no-lookahead guarantee
------------------------------------
`as_of` is a HARD filter applied in two independent places:

1. Filing selection drops any filing whose **report date** is after `as_of`
   *and* any filing whose **acceptance/filing date** is after `as_of`. The
   second check is the one that actually matters: a filing covering
   2024-03-31 that was not published until 2024-05-30 was unknowable on
   2024-04-15, and serving it would be textbook lookahead.
2. `HistoricalStore.get_etf_holdings(..., as_of_date=X)` re-applies the
   filter in SQL, so a row written by any past cycle can never surface in a
   backtest dated before it existed.

With `as_of=None` (live use) the caller is asking for "the latest known",
and the newest available filing is served.

SECONDARY source: iShares CSV (opt-in, never the default)
-----------------------------------------------------------
Behind `settings.ETF_HOLDINGS_ISSUER_CSV_ENABLED` (default False) and
consulted only when N-PORT produced nothing for a symbol. iShares publishes
plain, stable CSV (a handful of junk header rows, then a real header row
starting with `Ticker,`). **Family constraint:** iShares means IVV plus the
iShares sector suite — the iShares and SPDR families must NEVER be mixed
inside one composite ownership measure, because their S&P sector definitions
overlap differently and a mixed basket double-counts the same underlying
exposure at inconsistent weights. Pick one family per composite.

**SPDR/SSGA is deliberately NOT implemented**: it serves `.xlsx` rather than
CSV, sits behind an Akamai bot-check that returns 403 to a plain `urllib`
User-Agent, and its download slug changes. Also explicitly rejected:
`yfinance`'s `.funds_data` / `get_top_holdings()` (top-10 only — useless for
an ownership-share measure that needs the full basket — undocumented, and
scrape-fragile), ETF.com / etfdb scraping (ToS-hostile and unstable), and
every paid holdings vendor (out of scope for this free-first platform).

Honesty contracts
-------------------
* **CONSTRAINT #4** — an unreported `weight` or `shares_held` is `NaN`,
  never a fabricated `0.0`. A zero weight and an unreported weight are
  different facts and must stay distinguishable. Likewise a symbol whose
  holdings could not be resolved is **absent** from the returned dict — it
  is never present with an empty list, because "we don't know" and "this
  ETF holds nothing" are different claims.
* **CONSTRAINT #6** — `get_etf_holdings()` never raises. Any failure
  (network, parse, DB, bad config) degrades to `{}` (or to the cached rows
  that are available) and logs a warning. Each ETF is wrapped in its own
  try/except so one bad symbol can never abort the batch, matching this
  codebase's per-ticker convention in `data_engine.py` and the orchestrators.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

# MANDATORY reuse — see the module docstring. These are the platform's single
# throttled SEC client; a second one would blow the shared courtesy budget.
from data.edgar_fundamentals import USER_AGENT, _http_get, _throttle, get_cik  # noqa: F401

if TYPE_CHECKING:  # pragma: no cover - typing only
    from data.historical_store import HistoricalStore

logger = logging.getLogger(__name__)

_NAN = float("nan")

SEC_MF_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE_PRIMARY_DOC_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/primary_doc.xml"
)

SOURCE_SEC_NPORT = "sec_nport"
SOURCE_ISHARES_CSV = "ishares_csv"

# A trust files one NPORT-P per series, so the submissions index for e.g.
# "iShares Trust" lists dozens of filings that are not the requested fund.
# Probing is bounded so a series that is missing (or whose header shape
# changed) costs a fixed handful of requests rather than an unbounded scan.
_MAX_FILINGS_PROBED = 4

# ticker -> (10-digit CIK, series id). Built once per process from
# company_tickers_mf.json, guarded exactly like edgar_fundamentals._cik_cache.
_series_cache: Dict[str, Tuple[str, str]] = {}
_series_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Frozen public contract
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ETFHolding:
    """One ETF's position in one underlying security, as of one report date.

    ``weight`` is a FRACTION of NAV (0.0651 = 6.51%), not a percentage —
    N-PORT's own ``pctVal`` and the iShares CSV's ``Weight (%)`` are both
    percentages and are divided by 100 at parse time so every consumer sees
    one scale.

    ``weight`` and ``shares_held`` are ``NaN`` when the source did not report
    them, NEVER ``0.0`` (CONSTRAINT #4) — a genuinely zero position and an
    unreported field must stay distinguishable. ``shares_held`` in particular
    is only meaningful for share-denominated positions (N-PORT ``units ==
    "NS"``); a bond or derivative leg reports a principal amount instead and
    is served as ``NaN`` rather than a mis-scaled share count.

    ``as_of_date`` is the POINT-IN-TIME anchor — the report/holdings date the
    source itself stamped, never the date this row was fetched. It is what
    every causality filter in this module and in
    ``HistoricalStore.get_etf_holdings`` compares against.
    """

    etf_symbol: str
    holding_symbol: str
    weight: float           # fraction of NAV; NaN if not reported
    shares_held: float      # NaN if not reported
    as_of_date: date        # report date -- the PIT anchor
    source: str             # "sec_nport" | "ishares_csv"


class ETFHoldingsProvider(ABC):
    """Provider abstraction for one holdings source.

    Structurally analogous to ``data/market_data.py``'s ``MarketDataProvider``
    and ``data/sentiment_sources.py``'s ``SentimentSource``, but deliberately
    not a subclass of either — this returns basket composition, not quotes and
    not documents.

    Implementations MUST NOT raise out of ``fetch_holdings``: an empty list is
    the failure sentinel (CONSTRAINT #6). An empty list means "nothing
    resolved", never "this ETF holds nothing".
    """

    name: str = "abstract"

    @abstractmethod
    def fetch_holdings(self, etf_symbol: str) -> List[ETFHolding]:
        """Return every resolvable holding for *etf_symbol*, or ``[]``."""
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Small parsing helpers (never raise)
# ─────────────────────────────────────────────────────────────────────────────


def _local_name(tag: str) -> str:
    """Strip an XML namespace from a tag: ``{ns}invstOrSec`` -> ``invstOrSec``."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _iter_local(root: ET.Element, name: str):
    """Yield every descendant whose LOCAL tag name matches *name*.

    N-PORT documents are namespaced, and the namespace URI has changed
    across schema revisions, so matching on the local name keeps the parser
    working across filing vintages without a hardcoded namespace map.
    """
    for elem in root.iter():
        if _local_name(elem.tag) == name:
            yield elem


def _first_local_text(elem: ET.Element, name: str) -> Optional[str]:
    """Return the stripped text of the first descendant named *name*, or None."""
    for child in _iter_local(elem, name):
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def _to_float(value: Any) -> float:
    """Parse a numeric string to float, returning NaN (never 0.0) on failure."""
    if value is None:
        return _NAN
    try:
        text = str(value).strip().replace(",", "").replace("$", "")
        if not text or text in {"-", "--", "N/A", "NA", "null"}:
            return _NAN
        return float(text)
    except (TypeError, ValueError):
        return _NAN


def _parse_date(value: Any) -> Optional[date]:
    """Parse a date from the handful of formats these sources emit, else None.

    Returns ``None`` rather than a fabricated fallback: a holdings row with no
    trustworthy PIT anchor is dropped, never stamped with "today" (which would
    silently convert stale data into apparently-fresh data).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().strip('"')
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # ISO timestamp with a time component (submissions index acceptance dates).
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SEC N-PORT — ticker/series resolution
# ─────────────────────────────────────────────────────────────────────────────


def _load_series_map() -> Dict[str, Tuple[str, str]]:
    """Build ``{ticker: (cik10, series_id)}`` from ``company_tickers_mf.json``.

    Fund tickers are NOT in ``company_tickers.json`` in a usable form for this
    purpose — a trust CIK maps to many funds, and only the mutual-fund file
    carries the series id that identifies WHICH fund a given NPORT-P filing
    belongs to. Cached for the process lifetime behind a double-checked lock,
    exactly like ``edgar_fundamentals.get_cik``'s CIK cache, so W concurrent
    callers pull the file once rather than W times.

    Never raises: a fetch/parse failure leaves the cache empty and callers
    fall back to ``get_cik``.
    """
    if _series_cache:
        return _series_cache
    with _series_lock:
        if _series_cache:
            return _series_cache
        try:
            payload = json.loads(_http_get(SEC_MF_TICKERS_URL).decode("utf-8"))
            fields = [str(f) for f in payload.get("fields", [])]
            rows = payload.get("data", []) or []
            try:
                i_cik = fields.index("cik")
                i_series = fields.index("seriesId")
                i_symbol = fields.index("symbol")
            except ValueError:
                # Field order is documented but not guaranteed; fall back to
                # the historical positional layout rather than guessing wrong.
                i_cik, i_series, i_symbol = 0, 1, 3
            for row in rows:
                try:
                    symbol = str(row[i_symbol]).strip().upper()
                    if not symbol:
                        continue
                    _series_cache[symbol] = (
                        str(row[i_cik]).zfill(10),
                        str(row[i_series]).strip(),
                    )
                except (IndexError, TypeError, ValueError):
                    continue
        except Exception as exc:
            logger.warning("etf_holdings: failed to fetch SEC fund tickers: %s", exc)
    return _series_cache


def resolve_fund_identity(etf_symbol: str) -> Optional[Tuple[str, Optional[str]]]:
    """Resolve *etf_symbol* to ``(cik10, series_id_or_None)``, or ``None``.

    Prefers the mutual-fund ticker file (gives the series id, which is what
    disambiguates one fund's NPORT-P from its sibling funds under the same
    trust CIK); falls back to ``edgar_fundamentals.get_cik`` for wrappers
    organised as unit investment trusts, which file a single series and so
    appear in the ordinary company-ticker file instead.
    """
    symbol = (etf_symbol or "").strip().upper()
    if not symbol:
        return None
    entry = _load_series_map().get(symbol)
    if entry:
        return entry[0], entry[1] or None
    cik = get_cik(symbol)
    if cik:
        return cik, None
    return None


def _fetch_nport_filing_index(
    cik: str, *, as_of: Optional[date]
) -> List[Tuple[str, date]]:
    """Return ``[(accession_number, report_date), ...]``, newest report first.

    Applies the FIRST half of the causality guarantee: a filing is a candidate
    only if BOTH its report date and its filing (publication) date are on or
    before *as_of*. The filing-date half is the load-bearing one — an N-PORT
    covering a quarter-end is not published for ~60 more days, so filtering on
    report date alone would happily serve a document that did not exist yet.

    Never raises: any failure returns ``[]``.
    """
    try:
        payload = json.loads(
            _http_get(SEC_SUBMISSIONS_URL_TEMPLATE.format(cik=cik)).decode("utf-8")
        )
    except Exception as exc:
        logger.warning("etf_holdings: submissions fetch failed for CIK %s: %s", cik, exc)
        return []

    recent = (payload.get("filings", {}) or {}).get("recent", {}) or {}
    forms = recent.get("form", []) or []
    accessions = recent.get("accessionNumber", []) or []
    report_dates = recent.get("reportDate", []) or []
    filing_dates = recent.get("filingDate", []) or []

    out: List[Tuple[str, date]] = []
    for idx, form in enumerate(forms):
        if not str(form).upper().startswith("NPORT-P"):
            continue
        try:
            accession = str(accessions[idx])
            report_date = _parse_date(report_dates[idx])
            filing_date = _parse_date(filing_dates[idx])
        except (IndexError, TypeError):
            continue
        if report_date is None:
            continue
        if as_of is not None:
            if report_date > as_of:
                continue
            # Publication-date causality: unknowable on as_of => not a candidate.
            if filing_date is not None and filing_date > as_of:
                continue
        out.append((accession, report_date))

    out.sort(key=lambda pair: pair[1], reverse=True)
    return out


def _primary_doc_url(cik: str, accession: str) -> str:
    """Build the EDGAR archive URL for a filing's ``primary_doc.xml``."""
    return SEC_ARCHIVE_PRIMARY_DOC_TEMPLATE.format(
        cik_int=int(cik), accession_nodash=accession.replace("-", "")
    )


# ─────────────────────────────────────────────────────────────────────────────
# SEC N-PORT — XML parsing
# ─────────────────────────────────────────────────────────────────────────────


def extract_nport_series_id(xml_bytes: bytes) -> Optional[str]:
    """Return the ``seriesId`` a NPORT-P document belongs to, or ``None``.

    Used to confirm that a filing pulled from a multi-fund trust's submissions
    index is actually the requested fund. Never raises.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None
    return _first_local_text(root, "seriesId")


def parse_nport_holdings(xml_bytes: bytes, etf_symbol: str) -> List[ETFHolding]:
    """Parse an N-PORT ``primary_doc.xml`` into ``ETFHolding`` rows.

    Never raises (CONSTRAINT #6) — malformed XML, a missing report date, or a
    schema shape this parser does not recognise all degrade to ``[]``.

    Field mapping, and why each is what it is:

    * ``as_of_date``  ← ``genInfo/repPdDate`` (the holdings month-end), falling
      back to ``repPdEnd`` (the reporting period end). If NEITHER is present
      the whole document is dropped: without a trustworthy PIT anchor the rows
      cannot be causality-filtered, and stamping them with "today" would turn
      months-old data into apparently-fresh data.
    * ``holding_symbol`` ← ``identifiers/ticker@value``. A position with no
      ticker (most bonds, repos, cash equivalents, and swap legs report only a
      CUSIP/ISIN/LEI) is SKIPPED rather than keyed by CUSIP — this module's
      consumers join on equity tickers, and inventing a ticker would be
      fabrication. This is why a parsed basket is legitimately smaller than the
      filing's raw position count.
    * ``weight``      ← ``pctVal`` / 100 (N-PORT reports a percentage; the
      dataclass contract is a fraction). NaN when absent.
    * ``shares_held`` ← ``balance`` when ``units == "NS"`` (number of shares).
      Any other unit (``PA`` principal amount, ``NC`` notional, ...) yields
      NaN — a principal amount is not a share count and must not be passed off
      as one.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as exc:
        logger.warning(
            "etf_holdings: malformed N-PORT XML for %s: %s", etf_symbol, exc
        )
        return []

    report_date = _parse_date(_first_local_text(root, "repPdDate"))
    if report_date is None:
        report_date = _parse_date(_first_local_text(root, "repPdEnd"))
    if report_date is None:
        logger.warning(
            "etf_holdings: N-PORT document for %s has no report date; dropping "
            "(a holdings row with no point-in-time anchor is unusable).",
            etf_symbol,
        )
        return []

    symbol = (etf_symbol or "").strip().upper()
    holdings: List[ETFHolding] = []
    seen: set[str] = set()

    for position in _iter_local(root, "invstOrSec"):
        try:
            ticker = None
            for identifiers in _iter_local(position, "identifiers"):
                for candidate in _iter_local(identifiers, "ticker"):
                    value = (candidate.get("value") or "").strip().upper()
                    if value and value not in {"N/A", "NA", "NONE"}:
                        ticker = value
                        break
                if ticker:
                    break
            if not ticker:
                continue

            pct = _to_float(_first_local_text(position, "pctVal"))
            weight = pct / 100.0 if not math.isnan(pct) else _NAN

            units = (_first_local_text(position, "units") or "").strip().upper()
            balance = _to_float(_first_local_text(position, "balance"))
            shares_held = balance if units == "NS" else _NAN

            # One ETF can list the same issuer across several legs (multiple
            # share classes, a long and a short leg). Keep the first — merging
            # would require assumptions about sign and unit conventions that
            # the filing does not state.
            if ticker in seen:
                continue
            seen.add(ticker)

            holdings.append(
                ETFHolding(
                    etf_symbol=symbol,
                    holding_symbol=ticker,
                    weight=weight,
                    shares_held=shares_held,
                    as_of_date=report_date,
                    source=SOURCE_SEC_NPORT,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive per-position guard
            logger.debug("etf_holdings: skipped an N-PORT position: %s", exc)
            continue

    return holdings


class SECNPortProvider(ETFHoldingsProvider):
    """PRIMARY provider — SEC N-PORT (NPORT-P) filings via EDGAR.

    Every request goes through ``data/edgar_fundamentals.py``'s shared,
    lock-held throttle (see the module docstring for why that is mandatory).

    ``as_of`` is supplied at construction rather than per call so the provider
    ABC stays a one-argument contract; ``get_etf_holdings`` builds a
    correctly-dated provider for the cycle it is serving.
    """

    name = SOURCE_SEC_NPORT

    def __init__(self, *, as_of: Optional[date] = None) -> None:
        self._as_of = as_of

    def fetch_holdings(self, etf_symbol: str) -> List[ETFHolding]:
        """Resolve, download and parse the newest usable NPORT-P. ``[]`` on failure."""
        symbol = (etf_symbol or "").strip().upper()
        if not symbol:
            return []
        try:
            identity = resolve_fund_identity(symbol)
            if identity is None:
                logger.warning(
                    "etf_holdings: could not resolve %s to a SEC CIK/series; "
                    "no holdings served (never fabricated).",
                    symbol,
                )
                return []
            cik, series_id = identity

            filings = _fetch_nport_filing_index(cik, as_of=self._as_of)
            if not filings:
                logger.warning(
                    "etf_holdings: no NPORT-P filing for %s (CIK %s) at or "
                    "before the requested date.",
                    symbol,
                    cik,
                )
                return []

            for accession, _report_date in filings[:_MAX_FILINGS_PROBED]:
                try:
                    xml_bytes = _http_get(_primary_doc_url(cik, accession))
                except Exception as exc:
                    logger.warning(
                        "etf_holdings: primary_doc fetch failed for %s/%s: %s",
                        symbol,
                        accession,
                        exc,
                    )
                    continue

                if series_id:
                    filing_series = extract_nport_series_id(xml_bytes)
                    if filing_series and filing_series != series_id:
                        # Sibling fund under the same trust CIK — keep probing.
                        continue

                holdings = parse_nport_holdings(xml_bytes, symbol)
                if holdings:
                    return holdings

            logger.warning(
                "etf_holdings: probed %d NPORT-P filing(s) for %s without "
                "finding a parseable basket.",
                min(len(filings), _MAX_FILINGS_PROBED),
                symbol,
            )
            return []
        except Exception as exc:
            logger.warning("etf_holdings: SEC N-PORT fetch failed for %s: %s", symbol, exc)
            return []


# ─────────────────────────────────────────────────────────────────────────────
# iShares CSV — OPT-IN secondary source
# ─────────────────────────────────────────────────────────────────────────────

# iShares download URLs embed an opaque per-fund numeric product id that
# CANNOT be derived from the ticker. This sandbox has no network access, so
# no entry here could be verified against the live endpoint — the map is
# therefore deliberately minimal and operator-extensible via
# ``ISharesCSVProvider(url_map=...)`` rather than shipping a long list of
# guessed ids that would 404 (or, worse, silently return another fund).
#
# To add one: open the fund's iShares product page, click "Detailed Holdings
# and Analytics (CSV)", and copy the resulting URL verbatim.
#
# FAMILY CONSTRAINT: everything in this map must stay within the iShares
# family. Never mix an iShares basket and an SPDR basket inside one composite
# ownership measure — their S&P sector definitions overlap differently and the
# same underlying exposure gets double-counted at inconsistent weights.
_ISHARES_PRODUCT_URLS: Dict[str, str] = {
    # NOT verified against the live endpoint (no network access in this
    # environment). Treat as a starting point, not a guarantee.
    "IVV": (
        "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf/"
        "1467271812596.ajax?fileType=csv&fileName=IVV_holdings&dataType=fund"
    ),
}

_ISHARES_ASOF_PATTERN = re.compile(r"holdings\s+as\s+of", re.IGNORECASE)


def parse_ishares_csv(text: str, etf_symbol: str) -> List[ETFHolding]:
    """Parse an iShares holdings CSV into ``ETFHolding`` rows.

    The file is a few junk lines (fund name, inception date, and a
    ``Fund Holdings as of,"<date>"`` line), then a real header row beginning
    with ``Ticker``, then the positions, then occasionally more trailing junk.
    This parser scans for the header row rather than assuming a fixed skip
    count, because the junk-line count has changed over time.

    The ``Fund Holdings as of`` date is REQUIRED: with no PIT anchor the rows
    cannot be causality-filtered, so the whole file is dropped rather than
    stamped with today's date. Never raises — ``[]`` on any failure.
    """
    symbol = (etf_symbol or "").strip().upper()
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except Exception as exc:
        logger.warning("etf_holdings: unreadable iShares CSV for %s: %s", symbol, exc)
        return []

    as_of: Optional[date] = None
    header_idx: Optional[int] = None
    header: List[str] = []

    for idx, row in enumerate(rows):
        if not row:
            continue
        first = (row[0] or "").strip()
        if as_of is None and _ISHARES_ASOF_PATTERN.search(first) and len(row) > 1:
            as_of = _parse_date(row[1])
        if first.lower() == "ticker":
            header_idx = idx
            header = [(cell or "").strip() for cell in row]
            break

    if header_idx is None:
        logger.warning(
            "etf_holdings: iShares CSV for %s has no 'Ticker' header row.", symbol
        )
        return []
    if as_of is None:
        logger.warning(
            "etf_holdings: iShares CSV for %s has no 'holdings as of' date; "
            "dropping (rows with no point-in-time anchor are unusable).",
            symbol,
        )
        return []

    def _col(*names: str) -> Optional[int]:
        lowered = [cell.lower() for cell in header]
        for name in names:
            if name.lower() in lowered:
                return lowered.index(name.lower())
        return None

    i_ticker = _col("ticker")
    i_weight = _col("weight (%)", "weight(%)", "weight")
    i_shares = _col("shares", "quantity")

    holdings: List[ETFHolding] = []
    seen: set[str] = set()
    for row in rows[header_idx + 1 :]:
        if not row or i_ticker is None or len(row) <= i_ticker:
            continue
        ticker = (row[i_ticker] or "").strip().upper()
        if not ticker or ticker in {"-", "--"} or ticker in seen:
            continue
        # Cash/FX/futures lines carry placeholder tickers; keep only rows that
        # look like an equity symbol rather than guessing at the asset class.
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker):
            continue
        seen.add(ticker)

        pct = _to_float(row[i_weight]) if i_weight is not None and len(row) > i_weight else _NAN
        weight = pct / 100.0 if not math.isnan(pct) else _NAN
        shares = _to_float(row[i_shares]) if i_shares is not None and len(row) > i_shares else _NAN

        holdings.append(
            ETFHolding(
                etf_symbol=symbol,
                holding_symbol=ticker,
                weight=weight,
                shares_held=shares,
                as_of_date=as_of,
                source=SOURCE_ISHARES_CSV,
            )
        )
    return holdings


class ISharesCSVProvider(ETFHoldingsProvider):
    """SECONDARY provider — iShares issuer CSV. Opt-in, never the default.

    Gated by ``settings.ETF_HOLDINGS_ISSUER_CSV_ENABLED`` at the
    ``get_etf_holdings`` call site, and consulted only when N-PORT produced
    nothing. Symbols absent from the URL map return ``[]`` with a warning
    rather than a guessed URL.

    See ``_ISHARES_PRODUCT_URLS`` for the family constraint and for why the
    map is short.
    """

    name = SOURCE_ISHARES_CSV

    def __init__(self, url_map: Optional[Dict[str, str]] = None) -> None:
        self._url_map = dict(_ISHARES_PRODUCT_URLS)
        if url_map:
            self._url_map.update({k.strip().upper(): v for k, v in url_map.items()})

    def fetch_holdings(self, etf_symbol: str) -> List[ETFHolding]:
        symbol = (etf_symbol or "").strip().upper()
        url = self._url_map.get(symbol)
        if not url:
            logger.warning(
                "etf_holdings: no iShares CSV URL configured for %s (the "
                "product id cannot be derived from the ticker); skipping.",
                symbol,
            )
            return []
        try:
            import urllib.request

            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
            return parse_ishares_csv(raw.decode("utf-8-sig", errors="replace"), symbol)
        except Exception as exc:
            logger.warning("etf_holdings: iShares CSV fetch failed for %s: %s", symbol, exc)
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Public batch entry point
# ─────────────────────────────────────────────────────────────────────────────


def _rows_to_holdings(etf_symbol: str, rows: Sequence[Dict[str, Any]]) -> List[ETFHolding]:
    """Rehydrate ``HistoricalStore`` dicts into ``ETFHolding`` objects.

    A row whose ``as_of_date`` will not parse is DROPPED, not defaulted — the
    PIT anchor is the one field that cannot be reconstructed. NULL weight /
    shares columns come back as ``NaN`` (that is precisely how NaN was
    persisted; see ``HistoricalStore.save_etf_holdings``).
    """
    out: List[ETFHolding] = []
    for row in rows:
        as_of = _parse_date(row.get("as_of_date"))
        if as_of is None:
            continue
        holding_symbol = str(row.get("holding_symbol") or "").strip().upper()
        if not holding_symbol:
            continue
        weight = row.get("weight")
        shares = row.get("shares_held")
        out.append(
            ETFHolding(
                etf_symbol=(row.get("etf_symbol") or etf_symbol or "").strip().upper(),
                holding_symbol=holding_symbol,
                weight=_NAN if weight is None else _to_float(weight),
                shares_held=_NAN if shares is None else _to_float(shares),
                as_of_date=as_of,
                source=str(row.get("source") or ""),
            )
        )
    return out


def _cache_is_fresh(rows: Sequence[Dict[str, Any]], refresh_days: int) -> bool:
    """True when the newest cached row was fetched within *refresh_days*.

    A row with an unparseable ``fetched_at`` is treated as stale (re-fetch),
    never as fresh — the conservative direction is one extra SEC request, not
    silently serving something of unknown age.
    """
    if not rows:
        return False
    newest: Optional[datetime] = None
    for row in rows:
        raw = row.get("fetched_at")
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if newest is None or stamp > newest:
            newest = stamp
    if newest is None:
        return False
    return (datetime.now(timezone.utc) - newest) <= timedelta(days=max(refresh_days, 0))


def get_etf_holdings(
    etf_symbols: Sequence[str],
    *,
    as_of: Optional[date] = None,
    provider: Optional[ETFHoldingsProvider] = None,
    store: Optional["HistoricalStore"] = None,
) -> Dict[str, List[ETFHolding]]:
    """Batch entry point — ``{etf_symbol: [ETFHolding, ...]}``.

    Complete no-op — returns ``{}``, makes ZERO network calls and ZERO DB
    reads — when ``settings.ETF_HOLDINGS_ENABLED`` is False. The gate is
    checked before any other work, including before a ``HistoricalStore`` is
    constructed.

    Parameters
    ----------
    etf_symbols:
        Wrappers to resolve. Typically ``settings.ETF_HOLDINGS_TICKERS``.
    as_of:
        Point-in-time cutoff. Rows dated after this are NEVER served, and
        filings published after it are never even downloaded (see
        ``_fetch_nport_filing_index``). ``None`` means "latest known" — the
        live-use case.
    provider:
        Injected source. Defaults to ``SECNPortProvider(as_of=as_of)``. When
        ``settings.ETF_HOLDINGS_ISSUER_CSV_ENABLED`` is True and the default
        provider yields nothing for a symbol, ``ISharesCSVProvider`` is tried
        as a secondary — an explicitly injected provider is used alone, with
        no fallback, so tests and callers get exactly what they asked for.
    store:
        ``HistoricalStore`` used for the read-through cache. Defaults to a
        fresh one on the standard DB path. Cached rows fresher than
        ``settings.ETF_HOLDINGS_REFRESH_DAYS`` are served without any network
        call.

    Returns
    -------
    A dict containing only the symbols whose holdings could actually be
    resolved. A symbol with no data is ABSENT — never present with an empty
    list, because "unknown" and "holds nothing" are different claims
    (CONSTRAINT #4). Returns ``{}`` on ANY failure, and never raises
    (CONSTRAINT #6).

    Bounded by two independent per-cycle guards, mirroring
    ``data/attention_sources.py``:

    * ``settings.ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE`` (default 60s) — once
      elapsed, remaining symbols are served from cache only, with no
      network call.
    * ``settings.ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD`` (default 3
      consecutive no-holdings outcomes) — the live source is skipped for the
      rest of the cycle; cached rows are still served.
    """
    try:
        from settings import settings as _settings

        if not _settings.ETF_HOLDINGS_ENABLED:
            return {}
    except Exception as exc:  # pragma: no cover - settings import is not expected to fail
        logger.warning("etf_holdings: settings unavailable, staying disabled: %s", exc)
        return {}

    symbols = [str(sym).strip().upper() for sym in (etf_symbols or []) if str(sym).strip()]
    if not symbols:
        return {}

    try:
        refresh_days = int(getattr(_settings, "ETF_HOLDINGS_REFRESH_DAYS", 7) or 7)
        # `is None` rather than a falsy `or` default: 0.0 is a MEANINGFUL value
        # here (cache-only, no live fetches this cycle) and must not be
        # silently rewritten to 60s.
        raw_seconds = getattr(_settings, "ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE", None)
        max_seconds = max(0.0, float(60.0 if raw_seconds is None else raw_seconds))
        # Clamped to >= 1 so a misconfigured 0 trips the breaker before the
        # first attempt, which would silently disable ingestion entirely.
        raw_threshold = getattr(_settings, "ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD", None)
        breaker_threshold = max(1, int(3 if raw_threshold is None else raw_threshold))
        issuer_csv_enabled = bool(
            getattr(_settings, "ETF_HOLDINGS_ISSUER_CSV_ENABLED", False)
        )

        if store is None:
            try:
                from data.historical_store import HistoricalStore as _Store

                store = _Store()
            except Exception as exc:
                # Cacheless operation is degraded but still correct — every
                # symbol simply pays a live fetch this cycle.
                logger.warning("etf_holdings: HistoricalStore unavailable: %s", exc)
                store = None

        explicit_provider = provider is not None
        primary: ETFHoldingsProvider = provider or SECNPortProvider(as_of=as_of)
        secondary: Optional[ETFHoldingsProvider] = None
        if not explicit_provider and issuer_csv_enabled:
            secondary = ISharesCSVProvider()

        as_of_iso = as_of.isoformat() if as_of is not None else None
        deadline = time.monotonic() + max_seconds
        consecutive_failures = 0
        breaker_tripped = False
        budget_exhausted = False

        results: Dict[str, List[ETFHolding]] = {}

        for symbol in symbols:
            try:
                cached_rows: List[Dict[str, Any]] = []
                if store is not None:
                    cached_rows = store.get_etf_holdings(symbol, as_of_date=as_of_iso) or []

                if cached_rows and _cache_is_fresh(cached_rows, refresh_days):
                    hydrated = _rows_to_holdings(symbol, cached_rows)
                    if hydrated:
                        results[symbol] = hydrated
                    continue

                if not budget_exhausted and time.monotonic() >= deadline:
                    budget_exhausted = True
                    logger.warning(
                        "etf_holdings: wall-clock ceiling (%.0fs) reached; "
                        "remaining ETFs served from cache only this cycle.",
                        max_seconds,
                    )

                if budget_exhausted or breaker_tripped:
                    hydrated = _rows_to_holdings(symbol, cached_rows)
                    if hydrated:
                        results[symbol] = hydrated
                    continue

                fetched = primary.fetch_holdings(symbol)
                if not fetched and secondary is not None:
                    fetched = secondary.fetch_holdings(symbol)

                if fetched:
                    consecutive_failures = 0
                    if store is not None:
                        store.save_etf_holdings(fetched)
                    # Re-read through the store so the as_of causality filter
                    # is applied by exactly one authority (SQL), rather than
                    # trusting the freshly-parsed rows to be in range.
                    if store is not None:
                        rows = store.get_etf_holdings(symbol, as_of_date=as_of_iso) or []
                        hydrated = _rows_to_holdings(symbol, rows)
                    else:
                        hydrated = [
                            h for h in fetched if as_of is None or h.as_of_date <= as_of
                        ]
                    if hydrated:
                        results[symbol] = hydrated
                    continue

                consecutive_failures += 1
                if consecutive_failures >= breaker_threshold:
                    breaker_tripped = True
                    logger.warning(
                        "etf_holdings: circuit breaker tripped after %d "
                        "consecutive no-holdings outcomes; skipping the live "
                        "source for the remainder of this cycle.",
                        consecutive_failures,
                    )
                # Stale cache beats nothing — an old basket is still a real
                # basket, and its as_of_date says exactly how old it is.
                hydrated = _rows_to_holdings(symbol, cached_rows)
                if hydrated:
                    results[symbol] = hydrated
            except Exception as exc:
                # Per-ETF dead-lettering: one bad symbol never aborts the batch.
                logger.warning("etf_holdings: %s failed: %s", symbol, exc)
                continue

        return results
    except Exception as exc:
        logger.warning("etf_holdings.get_etf_holdings failed: %s", exc)
        return {}

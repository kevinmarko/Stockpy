"""
tests/test_etf_holdings.py — ETF constituent-holdings ingestion
================================================================
Covers ``data/etf_holdings.py`` and the three ``HistoricalStore`` methods
backing it (``save_etf_holdings`` / ``get_etf_holdings`` /
``latest_etf_holdings_date``).

**Every test here is fixture-driven and makes ZERO network calls.** This
sandbox has no live-market and no live-internet access, so nothing in this
file has been (or could be) verified against a live SEC N-PORT fetch or a
live iShares CSV download — the N-PORT fixture is a trimmed real filing
excerpt (public-domain US-government work) and the iShares CSV is
hand-written in the documented shape. Several tests assert that the HTTP
function is never called, which is both the gate contract and a guard
against a future edit quietly introducing a real request into the suite.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from data.etf_holdings import (
    ETFHolding,
    ETFHoldingsProvider,
    ISharesCSVProvider,
    SECNPortProvider,
    get_etf_holdings,
    parse_ishares_csv,
    parse_nport_holdings,
)
from data.historical_store import HistoricalStore

_NPORT_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "nport_sample.xml"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _nport_bytes() -> bytes:
    return _NPORT_FIXTURE_PATH.read_bytes()


# Hand-written synthetic iShares holdings CSV. Deliberately NOT a captured
# issuer file (those are not clearly redistributable) — this reproduces the
# documented SHAPE: junk preamble lines, a "Fund Holdings as of" line, the
# real header row starting with "Ticker", then positions, then trailing junk.
_ISHARES_CSV = '''iShares Sample Equity ETF
Fund Holdings as of,"Mar 28, 2024"
Inception Date,"May 15, 2000"
Shares Outstanding,"900,000,000.00"
Stock,"-"
Total Net Assets,"$500,000,000.00"

Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares,CUSIP,ISIN,Price,Location,Exchange,Currency
AAPL,APPLE INC,Information Technology,Equity,"$32,550,000.00",6.51,"$32,550,000.00","190,000",037833100,US0378331005,171.32,United States,NASDAQ,USD
MSFT,MICROSOFT CORP,Information Technology,Equity,"$36,250,000.00",7.25,"$36,250,000.00","86,000",594918104,US5949181045,420.72,United States,NASDAQ,USD
NVDA,NVIDIA CORP,Information Technology,Equity,"$28,000,000.00",-,"$28,000,000.00","31,000",67066G104,US67066G1040,903.56,United States,NASDAQ,USD
XTSLA,BLK CSH FND TREASURY SL AGENCY,Cash and/or Derivatives,Money Market,"$1,200,000.00",0.24,"$1,200,000.00","1,200,000",-,-,1.00,United States,-,USD
-,USD CASH,Cash and/or Derivatives,Cash,"$150,000.00",0.03,"$150,000.00","150,000",-,-,1.00,United States,-,USD

The content contained herein is owned or licensed by BlackRock.
'''


class _StubProvider(ETFHoldingsProvider):
    """Deterministic in-memory provider. Records every symbol it was asked for."""

    name = "stub"

    def __init__(self, by_symbol=None, raises_for=()):
        self._by_symbol = by_symbol or {}
        self._raises_for = set(raises_for)
        self.calls = []

    def fetch_holdings(self, etf_symbol):
        self.calls.append(etf_symbol)
        if etf_symbol in self._raises_for:
            raise RuntimeError(f"synthetic provider failure for {etf_symbol}")
        return list(self._by_symbol.get(etf_symbol, []))


def _holding(etf, sym, *, weight=0.05, shares=1000.0, as_of=date(2024, 3, 31)):
    return ETFHolding(
        etf_symbol=etf,
        holding_symbol=sym,
        weight=weight,
        shares_held=shares,
        as_of_date=as_of,
        source="sec_nport",
    )


@pytest.fixture()
def store(tmp_path):
    return HistoricalStore(db_path=str(tmp_path / "etf.db"))


@pytest.fixture()
def enabled():
    """Turn the master gate ON for tests that exercise the ingestion path."""
    with mock.patch("settings.settings.ETF_HOLDINGS_ENABLED", True):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# The master gate — the single most important contract in this module
# ─────────────────────────────────────────────────────────────────────────────


class TestMasterGate:
    def test_disabled_returns_empty_with_zero_network_calls(self, store):
        """Gate OFF => {} immediately, and the SEC HTTP client is never touched."""
        with mock.patch("settings.settings.ETF_HOLDINGS_ENABLED", False), \
             mock.patch("data.etf_holdings._http_get") as http_get:
            result = get_etf_holdings(["SPY", "QQQ"], store=store)

        assert result == {}
        http_get.assert_not_called()

    def test_disabled_short_circuits_before_touching_the_provider(self):
        """The gate is checked BEFORE any provider work, even an injected one."""
        provider = _StubProvider({"SPY": [_holding("SPY", "AAPL")]})
        with mock.patch("settings.settings.ETF_HOLDINGS_ENABLED", False):
            assert get_etf_holdings(["SPY"], provider=provider) == {}
        assert provider.calls == []

    def test_enabled_but_empty_symbol_list_makes_no_network_call(self, store, enabled):
        with mock.patch("data.etf_holdings._http_get") as http_get:
            assert get_etf_holdings([], store=store) == {}
        http_get.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# N-PORT XML parsing
# ─────────────────────────────────────────────────────────────────────────────


class TestNPortParsing:
    def test_parses_expected_holdings(self):
        holdings = parse_nport_holdings(_nport_bytes(), "IVV")
        by_symbol = {h.holding_symbol: h for h in holdings}

        # The treasury-bill position carries no <ticker> and is skipped rather
        # than keyed by CUSIP.
        assert set(by_symbol) == {"AAPL", "MSFT", "NVDA", "AMZN"}

        aapl = by_symbol["AAPL"]
        assert aapl.etf_symbol == "IVV"
        assert aapl.source == "sec_nport"
        assert aapl.as_of_date == date(2024, 3, 31)
        # pctVal is a PERCENT in the filing; the dataclass contract is a fraction.
        assert aapl.weight == pytest.approx(0.0651)
        assert aapl.shares_held == pytest.approx(1_250_000.0)

    def test_unreported_weight_is_nan_not_zero(self):
        """CONSTRAINT #4 — NVDA has no <pctVal> in the fixture."""
        nvda = {h.holding_symbol: h for h in parse_nport_holdings(_nport_bytes(), "IVV")}["NVDA"]
        assert math.isnan(nvda.weight)
        assert nvda.weight != 0.0
        # Its share count IS reported, so it must survive intact.
        assert nvda.shares_held == pytest.approx(310_000.0)

    def test_non_share_units_yield_nan_shares_not_a_principal_amount(self):
        """A bond leg reports principal (units=PA); passing 5,000,000 off as a
        share count would be a fabricated measurement."""
        amzn = {h.holding_symbol: h for h in parse_nport_holdings(_nport_bytes(), "IVV")}["AMZN"]
        assert math.isnan(amzn.shares_held)
        assert amzn.shares_held != 0.0
        assert amzn.weight == pytest.approx(0.0049)

    def test_malformed_xml_returns_empty_and_never_raises(self):
        assert parse_nport_holdings(b"<edgarSubmission><notClosed>", "IVV") == []
        assert parse_nport_holdings(b"", "IVV") == []
        assert parse_nport_holdings(b"not xml at all", "IVV") == []

    def test_document_without_report_date_is_dropped(self):
        """No PIT anchor => unusable. Never stamped with today's date."""
        xml = (
            b"<edgarSubmission><formData><invstOrSecs><invstOrSec>"
            b"<identifiers><ticker value='AAPL'/></identifiers>"
            b"<pctVal>5.0</pctVal><units>NS</units><balance>100</balance>"
            b"</invstOrSec></invstOrSecs></formData></edgarSubmission>"
        )
        assert parse_nport_holdings(xml, "IVV") == []

    def test_provider_degrades_to_empty_when_symbol_unresolvable(self):
        provider = SECNPortProvider(as_of=None)
        with mock.patch("data.etf_holdings.resolve_fund_identity", return_value=None):
            assert provider.fetch_holdings("NOPE") == []

    def test_provider_parses_a_fetched_filing(self):
        """Full provider path with every HTTP call stubbed — no network."""
        provider = SECNPortProvider(as_of=None)
        with mock.patch(
            "data.etf_holdings.resolve_fund_identity",
            return_value=("0001100663", "S000004310"),
        ), mock.patch(
            "data.etf_holdings._fetch_nport_filing_index",
            return_value=[("0001752724-24-000001", date(2024, 3, 31))],
        ), mock.patch(
            "data.etf_holdings._http_get", return_value=_nport_bytes()
        ):
            holdings = provider.fetch_holdings("IVV")

        assert {h.holding_symbol for h in holdings} == {"AAPL", "MSFT", "NVDA", "AMZN"}

    def test_provider_skips_a_sibling_series_under_the_same_trust(self):
        """A trust CIK lists one NPORT-P per fund; the wrong series is skipped."""
        provider = SECNPortProvider(as_of=None)
        with mock.patch(
            "data.etf_holdings.resolve_fund_identity",
            return_value=("0001100663", "S000099999"),  # NOT the fixture's series
        ), mock.patch(
            "data.etf_holdings._fetch_nport_filing_index",
            return_value=[("0001752724-24-000001", date(2024, 3, 31))],
        ), mock.patch(
            "data.etf_holdings._http_get", return_value=_nport_bytes()
        ):
            assert provider.fetch_holdings("IVV") == []


# ─────────────────────────────────────────────────────────────────────────────
# iShares CSV parsing (opt-in secondary source)
# ─────────────────────────────────────────────────────────────────────────────


class TestISharesCSVParsing:
    def test_parses_synthetic_csv_skipping_junk_header_rows(self):
        holdings = parse_ishares_csv(_ISHARES_CSV, "IVV")
        by_symbol = {h.holding_symbol: h for h in holdings}

        # The "-" placeholder cash row has no usable ticker and is dropped.
        assert "AAPL" in by_symbol and "MSFT" in by_symbol
        assert "-" not in by_symbol

        aapl = by_symbol["AAPL"]
        assert aapl.source == "ishares_csv"
        assert aapl.as_of_date == date(2024, 3, 28)
        assert aapl.weight == pytest.approx(0.0651)
        assert aapl.shares_held == pytest.approx(190_000.0)

    def test_unreported_weight_is_nan_not_zero(self):
        nvda = {h.holding_symbol: h for h in parse_ishares_csv(_ISHARES_CSV, "IVV")}["NVDA"]
        assert math.isnan(nvda.weight)
        assert nvda.weight != 0.0

    def test_missing_as_of_date_drops_the_whole_file(self):
        stripped = "\n".join(
            line for line in _ISHARES_CSV.splitlines() if "Holdings as of" not in line
        )
        assert parse_ishares_csv(stripped, "IVV") == []

    def test_missing_header_row_returns_empty(self):
        assert parse_ishares_csv("junk\nmore junk\n", "IVV") == []

    def test_unmapped_symbol_makes_no_request(self):
        """No guessed URLs — an unknown product id means no fetch at all."""
        provider = ISharesCSVProvider()
        with mock.patch("urllib.request.urlopen") as urlopen:
            assert provider.fetch_holdings("XLK") == []
        urlopen.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# HistoricalStore round-trip
# ─────────────────────────────────────────────────────────────────────────────


class TestHistoricalStoreRoundTrip:
    def test_table_and_index_created_on_init(self, tmp_path):
        db = str(tmp_path / "schema.db")
        HistoricalStore(db_path=db)
        with sqlite3.connect(db) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            indexes = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()}
        assert "etf_holdings" in tables
        assert "idx_etf_holdings_holding" in indexes

    def test_save_then_get_round_trip(self, store):
        written = store.save_etf_holdings(
            [
                _holding("IVV", "AAPL", weight=0.0651, shares=1_250_000.0),
                _holding("IVV", "MSFT", weight=0.0725, shares=640_000.0),
            ]
        )
        assert written == 2

        rows = store.get_etf_holdings("IVV")
        assert [r["holding_symbol"] for r in rows] == ["AAPL", "MSFT"]
        assert rows[0]["as_of_date"] == "2024-03-31"
        assert rows[0]["weight"] == pytest.approx(0.0651)
        assert rows[0]["shares_held"] == pytest.approx(1_250_000.0)
        assert rows[0]["source"] == "sec_nport"
        assert rows[0]["fetched_at"]

    def test_nan_persists_as_null_and_reads_back_as_nan_not_zero(self, store):
        """CONSTRAINT #4 across the storage boundary."""
        store.save_etf_holdings(
            [_holding("IVV", "NVDA", weight=float("nan"), shares=float("nan"))]
        )
        row = store.get_etf_holdings("IVV")[0]
        assert row["weight"] is None
        assert row["shares_held"] is None

        # And the module-level rehydration turns that NULL into NaN, not 0.0.
        from data.etf_holdings import _rows_to_holdings

        holding = _rows_to_holdings("IVV", [row])[0]
        assert math.isnan(holding.weight)
        assert math.isnan(holding.shares_held)

    def test_save_is_idempotent_on_the_primary_key(self, store):
        store.save_etf_holdings([_holding("IVV", "AAPL", weight=0.06)])
        store.save_etf_holdings([_holding("IVV", "AAPL", weight=0.07)])
        rows = store.get_etf_holdings("IVV")
        assert len(rows) == 1
        assert rows[0]["weight"] == pytest.approx(0.07)

    def test_empty_and_unknown_reads_return_empty(self, store):
        assert store.save_etf_holdings([]) == 0
        assert store.get_etf_holdings("IVV") == []
        assert store.get_etf_holdings("") == []
        assert store.latest_etf_holdings_date("IVV") is None
        assert store.latest_etf_holdings_date("") is None

    def test_latest_etf_holdings_date(self, store):
        store.save_etf_holdings([_holding("IVV", "AAPL", as_of=date(2023, 12, 31))])
        assert store.latest_etf_holdings_date("IVV") == "2023-12-31"
        store.save_etf_holdings([_holding("IVV", "AAPL", as_of=date(2024, 3, 31))])
        assert store.latest_etf_holdings_date("IVV") == "2024-03-31"
        # Case-insensitive on the ETF symbol.
        assert store.latest_etf_holdings_date("ivv") == "2024-03-31"

    def test_read_never_raises_on_a_db_failure(self, store):
        """CONSTRAINT #6 — every method degrades to its empty sentinel."""
        with mock.patch(
            "db_config.session_scope", side_effect=RuntimeError("db is gone")
        ):
            assert store.get_etf_holdings("IVV") == []
            assert store.latest_etf_holdings_date("IVV") is None
            assert store.save_etf_holdings([_holding("IVV", "AAPL")]) == 0


# ─────────────────────────────────────────────────────────────────────────────
# as_of causality — the lookahead guarantee
# ─────────────────────────────────────────────────────────────────────────────


class TestAsOfCausality:
    def test_store_never_returns_rows_dated_after_the_cutoff(self, store):
        store.save_etf_holdings(
            [
                _holding("IVV", "AAPL", weight=0.05, as_of=date(2023, 12, 31)),
                _holding("IVV", "AAPL", weight=0.09, as_of=date(2024, 6, 30)),
            ]
        )
        rows = store.get_etf_holdings("IVV", as_of_date="2024-03-31")
        assert len(rows) == 1
        assert rows[0]["as_of_date"] == "2023-12-31"
        assert rows[0]["weight"] == pytest.approx(0.05)

    def test_store_returns_a_single_basket_not_a_union_of_quarters(self, store):
        store.save_etf_holdings(
            [
                _holding("IVV", "AAPL", as_of=date(2023, 12, 31)),
                _holding("IVV", "MSFT", as_of=date(2023, 12, 31)),
                _holding("IVV", "AAPL", as_of=date(2024, 3, 31)),
            ]
        )
        rows = store.get_etf_holdings("IVV", as_of_date="2024-03-31")
        assert {r["as_of_date"] for r in rows} == {"2024-03-31"}
        assert [r["holding_symbol"] for r in rows] == ["AAPL"]

    def test_cutoff_before_every_stored_row_returns_nothing(self, store):
        store.save_etf_holdings([_holding("IVV", "AAPL", as_of=date(2024, 3, 31))])
        assert store.get_etf_holdings("IVV", as_of_date="2024-01-01") == []

    def test_get_etf_holdings_drops_future_dated_rows(self, store, enabled):
        """End-to-end: a provider that hands back a future-dated basket must
        not have it served for an earlier as_of."""
        provider = _StubProvider(
            {
                "IVV": [
                    _holding("IVV", "AAPL", as_of=date(2023, 12, 31)),
                    _holding("IVV", "TSLA", as_of=date(2024, 6, 30)),
                ]
            }
        )
        result = get_etf_holdings(
            ["IVV"], as_of=date(2024, 3, 31), provider=provider, store=store
        )
        assert [h.holding_symbol for h in result["IVV"]] == ["AAPL"]

    def test_filing_index_drops_filings_published_after_the_cutoff(self):
        """The load-bearing half: an N-PORT covering 2024-03-31 that was not
        FILED until 2024-05-30 was unknowable on 2024-04-15."""
        from data.etf_holdings import _fetch_nport_filing_index

        payload = (
            b'{"filings": {"recent": {'
            b'"form": ["NPORT-P", "NPORT-P", "10-K"],'
            b'"accessionNumber": ["0001-24-000002", "0001-24-000001", "0001-24-000003"],'
            b'"reportDate": ["2024-03-31", "2023-12-31", "2023-12-31"],'
            b'"filingDate": ["2024-05-30", "2024-02-28", "2024-02-01"]'
            b"}}}"
        )
        with mock.patch("data.etf_holdings._http_get", return_value=payload):
            candidates = _fetch_nport_filing_index("0001100663", as_of=date(2024, 4, 15))

        assert candidates == [("0001-24-000001", date(2023, 12, 31))]

    def test_filing_index_returns_empty_on_a_fetch_failure(self):
        from data.etf_holdings import _fetch_nport_filing_index

        with mock.patch("data.etf_holdings._http_get", side_effect=RuntimeError("503")):
            assert _fetch_nport_filing_index("0001100663", as_of=None) == []


# ─────────────────────────────────────────────────────────────────────────────
# Batch behavior: dead-lettering, caching, budget, circuit breaker
# ─────────────────────────────────────────────────────────────────────────────


class TestBatchBehavior:
    def test_one_bad_symbol_does_not_abort_the_batch(self, store, enabled):
        provider = _StubProvider(
            by_symbol={"IVV": [_holding("IVV", "AAPL")], "XLK": [_holding("XLK", "MSFT")]},
            raises_for={"BAD"},
        )
        result = get_etf_holdings(["IVV", "BAD", "XLK"], provider=provider, store=store)

        assert set(result) == {"IVV", "XLK"}
        assert provider.calls == ["IVV", "BAD", "XLK"]

    def test_unresolvable_symbol_is_absent_not_an_empty_list(self, store, enabled):
        """"Unknown" and "holds nothing" are different claims (CONSTRAINT #4)."""
        provider = _StubProvider({"IVV": [_holding("IVV", "AAPL")]})
        result = get_etf_holdings(["IVV", "GHOST"], provider=provider, store=store)

        assert "GHOST" not in result
        assert result.get("GHOST") is None

    def test_fresh_cache_is_served_without_calling_the_provider(self, store, enabled):
        seed = _StubProvider({"IVV": [_holding("IVV", "AAPL")]})
        first = get_etf_holdings(["IVV"], provider=seed, store=store)
        assert first["IVV"]

        second_provider = _StubProvider({"IVV": [_holding("IVV", "AAPL")]})
        second = get_etf_holdings(["IVV"], provider=second_provider, store=store)

        assert second_provider.calls == []
        assert [h.holding_symbol for h in second["IVV"]] == ["AAPL"]

    def test_stale_cache_triggers_a_refetch(self, store, enabled):
        store.save_etf_holdings([_holding("IVV", "AAPL")])
        # Backdate fetched_at well past ETF_HOLDINGS_REFRESH_DAYS.
        stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        with sqlite3.connect(store._db_path) as conn:
            conn.execute("UPDATE etf_holdings SET fetched_at = ?", (stale,))

        provider = _StubProvider({"IVV": [_holding("IVV", "MSFT")]})
        result = get_etf_holdings(["IVV"], provider=provider, store=store)

        assert provider.calls == ["IVV"]
        assert {h.holding_symbol for h in result["IVV"]} == {"AAPL", "MSFT"}

    def test_stale_cache_is_still_served_when_the_live_fetch_fails(self, store, enabled):
        store.save_etf_holdings([_holding("IVV", "AAPL")])
        stale = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        with sqlite3.connect(store._db_path) as conn:
            conn.execute("UPDATE etf_holdings SET fetched_at = ?", (stale,))

        provider = _StubProvider({})  # every fetch yields nothing
        result = get_etf_holdings(["IVV"], provider=provider, store=store)

        assert provider.calls == ["IVV"]
        assert [h.holding_symbol for h in result["IVV"]] == ["AAPL"]

    def test_circuit_breaker_stops_calling_a_dead_source(self, store, enabled):
        provider = _StubProvider({"GOOD": [_holding("GOOD", "AAPL")]})
        with mock.patch("settings.settings.ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD", 2):
            result = get_etf_holdings(
                ["A", "B", "C", "GOOD"], provider=provider, store=store
            )

        # A and B fail, tripping the breaker; C and GOOD are never attempted.
        assert provider.calls == ["A", "B"]
        assert result == {}

    def test_wall_clock_budget_stops_live_fetches(self, store, enabled):
        provider = _StubProvider({"IVV": [_holding("IVV", "AAPL")]})
        with mock.patch("settings.settings.ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE", 0.0):
            result = get_etf_holdings(["IVV"], provider=provider, store=store)

        assert provider.calls == []
        assert result == {}

    def test_injected_provider_is_used_alone_even_with_issuer_csv_enabled(
        self, store, enabled
    ):
        """An explicitly injected provider gets no silent iShares fallback."""
        provider = _StubProvider({})
        with mock.patch("settings.settings.ETF_HOLDINGS_ISSUER_CSV_ENABLED", True), \
             mock.patch("urllib.request.urlopen") as urlopen:
            assert get_etf_holdings(["IVV"], provider=provider, store=store) == {}
        urlopen.assert_not_called()

    def test_works_without_a_store(self, enabled):
        """Cacheless operation is degraded but correct — and still applies as_of."""
        provider = _StubProvider(
            {
                "IVV": [
                    _holding("IVV", "AAPL", as_of=date(2023, 12, 31)),
                    _holding("IVV", "TSLA", as_of=date(2024, 6, 30)),
                ]
            }
        )
        with mock.patch(
            "data.historical_store.HistoricalStore", side_effect=RuntimeError("no db")
        ):
            result = get_etf_holdings(
                ["IVV"], as_of=date(2024, 3, 31), provider=provider
            )
        assert [h.holding_symbol for h in result["IVV"]] == ["AAPL"]


# ─────────────────────────────────────────────────────────────────────────────
# Frozen contract shape
# ─────────────────────────────────────────────────────────────────────────────


class TestFrozenContract:
    def test_etf_holding_is_frozen_with_the_agreed_fields(self):
        holding = _holding("IVV", "AAPL")
        assert (
            holding.etf_symbol,
            holding.holding_symbol,
            holding.weight,
            holding.shares_held,
            holding.as_of_date,
            holding.source,
        ) == ("IVV", "AAPL", 0.05, 1000.0, date(2024, 3, 31), "sec_nport")

        with pytest.raises(Exception):
            holding.weight = 0.99  # frozen dataclass

    def test_providers_implement_the_abc(self):
        assert issubclass(SECNPortProvider, ETFHoldingsProvider)
        assert issubclass(ISharesCSVProvider, ETFHoldingsProvider)
        assert SECNPortProvider().name == "sec_nport"
        assert ISharesCSVProvider().name == "ishares_csv"

    def test_reuses_the_shared_sec_client_rather_than_a_second_one(self):
        """A second, unthrottled SEC client would blow the shared courtesy
        budget and throttle the platform's existing fundamentals/8-K paths."""
        import data.edgar_fundamentals as edgar
        import data.etf_holdings as etf

        assert etf._http_get is edgar._http_get
        assert etf._throttle is edgar._throttle
        assert etf.get_cik is edgar.get_cik

"""
tests/test_m3_explain_adversarial_stress.py
============================================
Adversarial stress-testing suite for Milestone M3:
- company_profile wrapper in data/fmp_client.py (exotic symbols, circuit-breaker states, malformed responses)
- GET /data/explain/{symbol} in api/data_api.py (SQL injection, case insensitivity, subsystem crashes, empty symbols)
- scripts/verify_fmp_profile.py (exit code matrix 0, 1, 2)
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date
from types import SimpleNamespace
from unittest import mock
import urllib.parse

import pandas as pd
import pytest
import requests
from fastapi.testclient import TestClient

from api import data_api
from data import fmp_client
from data.fmp_client import company_profile, FMPUnavailable
from scripts import verify_fmp_profile
from settings import settings

client = TestClient(data_api.app, client=("127.0.0.1", 54123))


# ==============================================================================
# 1. Adversarial Tests for company_profile
# ==============================================================================

class TestCompanyProfileAdversarial:
    """Stress-test company_profile with exotic symbols, network errors, and payloads."""

    @pytest.mark.parametrize(
        "exotic_symbol,expected_wire_symbol",
        [
            ("BRK.B", "BRK.B"),
            ("BF/B", "BF/B"),
            ("  aapl  ", "AAPL"),
            ("brk.a", "BRK.A"),
            ("12345", "12345"),
            ("T-P-O", "T-P-O"),
            (None, "NONE"),
        ],
    )
    def test_exotic_symbols_normalization(self, exotic_symbol, expected_wire_symbol, monkeypatch):
        """Check that exotic symbols are normalized to uppercase stripped string without crashing."""
        recorded_symbols = []

        def mock_profile(sym):
            recorded_symbols.append(fmp_client._sym(sym))
            return [{"companyName": f"Entity {sym}", "description": "Valid company description here."}]

        monkeypatch.setattr(fmp_client, "profile", mock_profile)
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_API_KEY", "test_key")

        result = company_profile(exotic_symbol)
        assert result is not None
        assert recorded_symbols[-1] == expected_wire_symbol

    @pytest.mark.parametrize(
        "failing_exception",
        [
            FMPUnavailable("503 Service Unavailable"),
            requests.exceptions.ConnectionError("Connection refused"),
            requests.exceptions.Timeout("Request timed out"),
            requests.exceptions.HTTPError("429 Too Many Requests"),
            json.JSONDecodeError("Expecting value", "doc", 0),
            RuntimeError("Unexpected thread panic"),
            ValueError("Malformed float"),
        ],
    )
    def test_circuit_breaker_and_exception_handling(self, failing_exception, monkeypatch):
        """Verify company_profile never lets network/parse/system exceptions escape."""
        def mock_profile_raise(sym):
            raise failing_exception

        monkeypatch.setattr(fmp_client, "profile", mock_profile_raise)
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_API_KEY", "test_key")

        result = company_profile("FAIL")
        assert result is None, f"Expected None on {type(failing_exception).__name__}, got {result}"

    @pytest.mark.parametrize(
        "malformed_payload,expected_is_dict",
        [
            ([], False),                         # Empty list -> None
            ([None], False),                     # List of None -> None
            (["string_instead_of_dict"], False), # List of primitives -> None
            ([12345], False),
            ({}, True),                          # Empty dict -> {}
            (42, False),                         # Bare int -> None
            ("plain string", False),             # Bare str -> None
            (None, False),                       # Bare None -> None
            ([[], {}], True),                    # List with dict item -> {}
        ],
    )
    def test_malformed_raw_payloads(self, malformed_payload, expected_is_dict, monkeypatch):
        """Verify malformed JSON responses degrade to None (or dict) without uncaught exceptions."""
        monkeypatch.setattr(fmp_client, "profile", lambda sym: malformed_payload)
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)
        monkeypatch.setattr(settings, "FMP_API_KEY", "test_key")

        result = company_profile("MALFORMED")
        if expected_is_dict:
            assert isinstance(result, dict)
        else:
            assert result is None


# ==============================================================================
# 2. Adversarial Tests for GET /data/explain/{symbol}
# ==============================================================================

class TestExplainEndpointAdversarial:
    """Stress-test GET /data/explain/{symbol} for injection, degradation, and edge cases."""

    def test_case_insensitivity(self, monkeypatch):
        """Verify lower, mixed, and upper case symbols produce consistent normalized responses."""
        monkeypatch.setattr(data_api, "company_profile", lambda sym: {"companyName": "Test Co", "description": "Desc"})
        monkeypatch.setattr(data_api, "build_sync_report", lambda snap, **kwargs: SimpleNamespace(symbols={}))
        monkeypatch.setattr(data_api, "_query_daily_signals", lambda sym: None)

        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            for variant in ["aapl", "AaPl", "AAPL"]:
                resp = client.get(f"/data/explain/{variant}")
                assert resp.status_code == 200
                assert resp.json()["symbol"] == "AAPL"

    @pytest.mark.parametrize(
        "sql_payload",
        [
            "AAPL'; DROP TABLE DailySignals; --",
            "' OR '1'='1",
            "AAPL' UNION SELECT 1,2,3 --",
            "AAPL\"; DROP TABLE price_bars; --",
            "admin'--",
        ],
    )
    def test_sql_injection_resilience(self, sql_payload, monkeypatch):
        """Probe endpoint with adversarial SQL injection strings without slashes.
        Ensures queries are parameterized and returns 200 or 422 with zero 500s.
        """
        monkeypatch.setattr(data_api, "company_profile", lambda sym: None)
        monkeypatch.setattr(data_api, "build_sync_report", lambda snap, **kwargs: SimpleNamespace(symbols={}))

        encoded = urllib.parse.quote(sql_payload, safe="")
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get(f"/data/explain/{encoded}")

        assert resp.status_code in (200, 422), f"Expected 200/422 on SQL payload, got {resp.status_code}"
        if resp.status_code == 200:
            data = resp.json()
            assert "symbol" in data
            assert data["tracking"]["tracked"] is False
            assert data["factor_breakdown"]["available"] is False

    def test_slash_in_symbol_returns_404_due_to_path_routing(self):
        """Observe that symbols containing slashes (e.g. BF/B or SQL with /*)
        are treated as multi-segment paths by Starlette router, returning 404.
        """
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp1 = client.get("/data/explain/BF/B")
            assert resp1.status_code == 404
            resp2 = client.get("/data/explain/BF%2FB")
            assert resp2.status_code == 404
            resp3 = client.get("/data/explain/'%20OR%201=1%20%2F*")
            assert resp3.status_code == 404

    @pytest.mark.parametrize(
        "bad_input,expected_code",
        [
            ("%20%20", 422),
            ("%20", 422),
            ("%09%0A", 422), # Tab and newline
            ("   ", 422),
        ],
    )
    def test_whitespace_and_empty_validation(self, bad_input, expected_code):
        """Verify pure whitespace symbols are rejected with HTTP 422."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get(f"/data/explain/{bad_input}")
        assert resp.status_code == expected_code
        assert "Symbol cannot be empty" in resp.json()["detail"]

    def test_database_operational_error_in_daily_signals(self, monkeypatch):
        """Verify that when sqlite3 raises OperationalError (e.g. database locked or corrupted),
        _query_daily_signals catches it and explain_ticker cleanly reports factor_breakdown unavailable.
        """
        monkeypatch.setattr(data_api, "company_profile", lambda s: {"companyName": "OK Co"})
        monkeypatch.setattr(data_api, "build_sync_report", lambda s, **k: SimpleNamespace(symbols={}))

        def mock_sqlite_connect(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(sqlite3, "connect", mock_sqlite_connect)

        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/data/explain/LOCKED")

        assert resp.status_code == 200
        data = resp.json()
        assert data["factor_breakdown"]["available"] is False
        assert "No signals recorded" in data["factor_breakdown"]["reason"]

    def test_untracked_nonexistent_ticker_honesty(self, monkeypatch):
        """A ticker that has never existed anywhere must cleanly report untracked without errors."""
        monkeypatch.setattr(data_api, "company_profile", lambda s: None)
        monkeypatch.setattr(data_api, "build_sync_report", lambda s, **k: SimpleNamespace(symbols={}))
        monkeypatch.setattr(data_api, "_query_daily_signals", lambda s: None)

        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/data/explain/NONEXISTENT999")

        assert resp.status_code == 200
        data = resp.json()
        assert data["tracking"]["tracked"] is False
        assert data["tracking"]["held"] is False
        assert data["tracking"]["quantity"] is None
        assert data["tracking"]["avg_cost"] is None
        assert data["tracking"]["market_value"] is None
        assert data["tracking"]["coverage_status"] == "untracked"
        assert data["factor_breakdown"]["available"] is False
        assert data["price_history_status"]["available"] is False
        assert data["price_history_status"]["status"] == "no_data"

    def test_malformed_quantity_type_vulnerability(self, monkeypatch):
        """Adversarially probe line 1010 of api/data_api.py where float(status_entry.quantity)
        is unshielded by a try-except block. Demonstrates that non-numeric quantity strings
        cause an uncaught ValueError.
        """
        bad_status = SimpleNamespace(
            symbol="MALFORMED_QTY",
            held=True,
            quantity="not-a-number",
            avg_cost=100.0,
            market_value=1000.0,
            coverage=SimpleNamespace(value="full"),
            watchlists=(),
        )
        monkeypatch.setattr(data_api, "company_profile", lambda s: None)
        monkeypatch.setattr(
            data_api,
            "build_sync_report",
            lambda snap, **k: SimpleNamespace(symbols={"MALFORMED_QTY": bad_status}),
        )
        monkeypatch.setattr(data_api, "_query_daily_signals", lambda s: None)

        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with pytest.raises(ValueError, match="could not convert string to float"):
                # Direct call to endpoint function demonstrates unhandled exception
                data_api.explain_ticker("MALFORMED_QTY")


# ==============================================================================
# 3. Adversarial Tests for scripts/verify_fmp_profile.py
# ==============================================================================

class TestVerifyFmpProfileAdversarial:
    """Stress-test CLI verifier script across exit codes 0, 1, 2."""

    def test_exit_code_2_when_key_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "")
        code = verify_fmp_profile.main([])
        assert code == 2

    def test_exit_code_2_when_profile_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "valid_key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", False)
        code = verify_fmp_profile.main([])
        assert code == 2

    def test_exit_code_2_when_symbols_empty(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "valid_key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)
        code = verify_fmp_profile.main(["--symbols", " , ,  "])
        assert code == 2

    def test_exit_code_1_when_description_missing_or_short(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "valid_key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)

        def mock_profile(sym):
            if sym == "AAPL":
                return {"companyName": "Apple", "description": "Short desc"} # < 20 chars
            return {"companyName": "MSFT", "description": "This is a sufficiently long description that passes."}

        monkeypatch.setattr(verify_fmp_profile, "company_profile", mock_profile)
        code = verify_fmp_profile.main(["--symbols", "AAPL,MSFT"])
        assert code == 1

    def test_exit_code_1_when_profile_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "valid_key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)
        monkeypatch.setattr(verify_fmp_profile, "company_profile", lambda sym: None)

        code = verify_fmp_profile.main(["--symbols", "UNKNOWN"])
        assert code == 1

    def test_exit_code_0_when_all_pass(self, monkeypatch):
        monkeypatch.setattr(settings, "FMP_API_KEY", "valid_key")
        monkeypatch.setattr(settings, "FMP_PROFILE_ENABLED", True)

        def mock_profile(sym):
            return {
                "companyName": f"{sym} Corporation",
                "sector": "Technology",
                "description": f"Detailed and genuine enterprise description for {sym} exceeding twenty characters.",
            }

        monkeypatch.setattr(verify_fmp_profile, "company_profile", mock_profile)
        code = verify_fmp_profile.main(["--symbols", "AAPL,MSFT,GOOGL"])
        assert code == 0

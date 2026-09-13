"""tests/test_challenger_m4_adversarial.py — Empirical Challenger Adversarial Test Suite
=======================================================================================
Adversarial challenge test suite for Milestone 4 (FastAPI retrospective endpoints and contracts):
1. Pathological trade IDs (negative, zero, float strings, enormous integers, non-numeric strings, SQL injection, XSS).
2. Query parameter boundary attacks (limit <= 0, limit > 1000, SQL injection and long strings in symbol and strategy_id).
3. Unauthorized and malformed Bearer tokens (missing, bad scheme, malformed header, oversized token, remote fail-closed).
4. Data integrity: cohort isolation, absence of blended metrics under hostile conditions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.pilots_api import app
from settings import settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = getattr(settings, "STATE_API_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


# =============================================================================
# 1. Pathological Trade ID Adversarial Challenge
# =============================================================================

class TestPathologicalTradeIds:
    """Adversarial testing of GET /pilots/paper-broker/trades/{trade_id}/retrospective."""

    @pytest.mark.parametrize(
        "bad_id",
        [
            "not_an_int",
            "abc",
            "1.5",
            "-0.5",
            "1e5",
            "NaN",
            "Infinity",
            "-Infinity",
            "null",
            "undefined",
            "' OR 1=1 --",
            "1; DROP TABLE paper_closed_trades; --",
            "<script>alert(1)",
            "%3Cscript%3Ealert(1)%3C%2Fscript%3E",
            "true",
            "false",
            "[]",
            "{}",
        ],
    )
    def test_non_integer_trade_ids_rejected_with_422(
        self, client: TestClient, auth_headers: dict[str, str], bad_id: str
    ) -> None:
        """Any non-integer trade_id in path must be rejected by FastAPI with 422 (or 404 if path routing splits)."""
        resp = client.get(f"/pilots/paper-broker/trades/{bad_id}/retrospective", headers=auth_headers)
        assert resp.status_code in (404, 422), f"Expected 422 or 404 for bad_id={bad_id!r}, got {resp.status_code}: {resp.text}"

    def test_float_zero_string_handled_safely(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Pydantic may lax-coerce '0.0' to integer 0; must safely yield 404 (not found) or 422 without 500."""
        resp = client.get("/pilots/paper-broker/trades/0.0/retrospective", headers=auth_headers)
        assert resp.status_code in (404, 422), f"Expected 404 or 422 for '0.0', got {resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        "boundary_id",
        [
            0,
            -1,
            -999999,
            -2147483648,
            2147483647,
            999999999,
        ],
    )
    def test_boundary_integer_trade_ids_return_404_cleanly(
        self, client: TestClient, auth_headers: dict[str, str], boundary_id: int
    ) -> None:
        """Negative, zero, or nonexistent integer trade IDs must gracefully return 404 without 500 crash."""
        resp = client.get(f"/pilots/paper-broker/trades/{boundary_id}/retrospective", headers=auth_headers)
        assert resp.status_code == 404, f"Expected 404 for boundary_id={boundary_id}, got {resp.status_code}"
        assert f"Paper trade {boundary_id} not found" in resp.json().get("detail", "")

    def test_enormous_integer_trade_id_does_not_crash_server(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Enormous integer (beyond int64) must not raise unhandled OverflowError or SQLite crash."""
        enormous_id = "99999999999999999999999999999999999999999999999999999999999999999999999999999999"
        resp = client.get(f"/pilots/paper-broker/trades/{enormous_id}/retrospective", headers=auth_headers)
        # Should be 404 (or 422 if int parsing rejects huge int), but NEVER 500
        assert resp.status_code in (404, 422), f"Expected 404 or 422 for huge integer, got {resp.status_code}: {resp.text}"


# =============================================================================
# 2. Query Parameter Boundaries & Injection Adversarial Challenge
# =============================================================================

class TestQueryParameterAdversarial:
    """Adversarial testing of GET /pilots/paper-broker/retrospective/insights."""

    @pytest.mark.parametrize(
        "bad_limit",
        [
            0,
            -1,
            -100,
            1001,
            2000,
            999999,
            "abc",
            "1.5",
            "0.5",
            "-0.1",
        ],
    )
    def test_invalid_limit_query_rejected_with_422(
        self, client: TestClient, auth_headers: dict[str, str], bad_limit: Any
    ) -> None:
        """Limit values outside [1, 1000] or non-integer must be rejected with 422."""
        resp = client.get(f"/pilots/paper-broker/retrospective/insights?limit={bad_limit}", headers=auth_headers)
        assert resp.status_code == 422, f"Expected 422 for limit={bad_limit!r}, got {resp.status_code}"

    @pytest.mark.parametrize("valid_limit", [1, 50, 100, 500, 1000])
    def test_valid_boundary_limits_accepted(
        self, client: TestClient, auth_headers: dict[str, str], valid_limit: int
    ) -> None:
        """Boundary limits 1 and 1000 must be accepted (200)."""
        mock_insights = {
            "automated_cohort": {"total_trades": 0, "win_rate": None},
            "manual_cohort": {"total_trades": 0, "win_rate": None},
            "unrecorded_cohort": {"total_trades": 0},
            "contrastive_insights": [],
            "bridge_health": {"status": "healthy"},
        }
        with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_retrospectives_batch", return_value=[]), \
             patch("pilots.retrospective_insights.generate_batch_retrospective_insights", return_value=mock_insights):
            resp = client.get(f"/pilots/paper-broker/retrospective/insights?limit={valid_limit}", headers=auth_headers)
            assert resp.status_code == 200, f"Expected 200 for valid_limit={valid_limit}, got {resp.status_code}"

    @pytest.mark.parametrize(
        "injected_symbol",
        [
            "' OR '1'='1",
            "'; DROP TABLE paper_closed_trades; --",
            "UNION SELECT * FROM paper_closed_trades",
            "<script>alert('xss')</script>",
            "A" * 5000,  # Buffer stress
            "SYM\x00NULL",  # Null byte
        ],
    )
    def test_hostile_symbol_parameter_handled_safely(
        self, client: TestClient, auth_headers: dict[str, str], injected_symbol: str
    ) -> None:
        """Hostile strings in symbol query parameter must not execute SQL injection or crash."""
        resp = client.get(
            "/pilots/paper-broker/retrospective/insights",
            params={"symbol": injected_symbol, "limit": 10},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400, 422), f"Hostile symbol crashed with {resp.status_code}: {resp.text}"

    @pytest.mark.parametrize(
        "injected_strategy",
        [
            "' OR 1=1 --",
            "'; DELETE FROM paper_closed_trades; --",
            "<svg onload=alert(1)>",
            "S" * 5000,
        ],
    )
    def test_hostile_strategy_id_handled_safely(
        self, client: TestClient, auth_headers: dict[str, str], injected_strategy: str
    ) -> None:
        """Hostile strings in strategy_id query parameter must not crash or leak data."""
        resp = client.get(
            "/pilots/paper-broker/retrospective/insights",
            params={"strategy_id": injected_strategy, "limit": 10},
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400, 422), f"Hostile strategy_id crashed with {resp.status_code}: {resp.text}"


# =============================================================================
# 3. Authentication & Authorization Adversarial Attacks
# =============================================================================

ENDPOINTS = [
    "/pilots/paper-broker/trades/1/retrospective",
    "/pilots/paper-broker/retrospective/insights",
    "/pilots/paper-broker/bridge/metrics",
]


class TestAuthenticationAdversarial:
    """Stress-test bearer token authentication and loopback policies."""

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_missing_bearer_token_when_token_configured_rejected_401(self, endpoint: str) -> None:
        """When STATE_API_TOKEN is configured, omitting Authorization header gets 401 across all endpoints."""
        with patch.object(settings, "STATE_API_TOKEN", "prod_secret_token_xyz"):
            remote_client = TestClient(app, client=("127.0.0.1", 50000))
            resp = remote_client.get(endpoint)
            assert resp.status_code == 401
            assert resp.json().get("detail") == "Invalid or missing bearer token"

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    @pytest.mark.parametrize(
        "bad_auth_header",
        [
            "Bearer ",  # Empty token
            "Bearer   ",  # Whitespace only
            "Bearer invalid_secret_token",
            "Bearer prod_secret_token_xy",  # Off-by-one (prefix)
            "Bearer prod_secret_token_xyza",  # Off-by-one (suffix)
            "Basic dXNlcjpwYXNz",  # Wrong scheme
            "Token prod_secret_token_xyz",  # Wrong scheme
            "prod_secret_token_xyz",  # Raw token without Bearer prefix
            "Bearer token1 token2",  # Extra arguments
            f"Bearer {'A' * 50000}",  # Oversized token (DoS check)
        ],
    )
    def test_malformed_or_invalid_tokens_rejected_401(
        self, endpoint: str, bad_auth_header: str
    ) -> None:
        """Malformed or invalid bearer tokens must be rejected with 401 without crashing or hanging."""
        with patch.object(settings, "STATE_API_TOKEN", "prod_secret_token_xyz"):
            test_client = TestClient(app, client=("127.0.0.1", 50000))
            resp = test_client.get(endpoint, headers={"Authorization": bad_auth_header})
            assert resp.status_code == 401
            assert resp.json().get("detail") == "Invalid or missing bearer token"

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_remote_host_without_token_fails_closed_503(self, endpoint: str) -> None:
        """When STATE_API_TOKEN is unset and client is non-loopback (e.g. 192.168.1.50), must return 503."""
        with patch.object(settings, "STATE_API_TOKEN", ""):
            remote_client = TestClient(app, client=("192.168.1.50", 50000))
            resp = remote_client.get(endpoint)
            assert resp.status_code == 503, f"Expected 503 for non-loopback IP, got {resp.status_code}: {resp.text}"

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_valid_bearer_token_always_succeeds(self, endpoint: str) -> None:
        """When correct token is provided, authentication succeeds regardless of client IP."""
        valid_token = "ultra_secure_token_999"
        with patch.object(settings, "STATE_API_TOKEN", valid_token):
            client = TestClient(app, client=("10.0.0.1", 50000))
            with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_trade_retrospective", return_value={"trade_id": 1}), \
                 patch("pilots.retrospective_composer.RetrospectiveComposer.compose_retrospectives_batch", return_value=[]), \
                 patch("pilots.retrospective_insights.generate_batch_retrospective_insights", return_value={"automated_cohort": {}, "manual_cohort": {}, "unrecorded_cohort": {}, "contrastive_insights": [], "bridge_health": {}}), \
                 patch("data.paper_account_store.PaperAccountStore.get_bridge_completeness_metrics", return_value={"status": "healthy"}):
                resp = client.get(endpoint, headers={"Authorization": f"Bearer {valid_token}"})
                assert resp.status_code == 200


# =============================================================================
# 4. Anti-Fabrication & Cohort Isolation Under Hostile Responses
# =============================================================================

class TestAntiFabricationIntegrity:
    """Ensure no blended metrics can ever leak from GET /pilots/paper-broker/retrospective/insights."""

    def test_zero_blended_metrics_under_empty_data(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Even with empty cohorts, root level must contain ZERO blended metrics."""
        empty_insights = {
            "automated_cohort": {
                "cohort_name": "Automated (Signal-Driven)",
                "total_trades": 0,
                "win_rate": None,
                "mean_edge_ratio": None,
            },
            "manual_cohort": {
                "cohort_name": "Manual (Discretionary)",
                "total_trades": 0,
                "win_rate": None,
                "mean_edge_ratio": None,
            },
            "unrecorded_cohort": {
                "cohort_name": "Unrecorded (Pre-Feature)",
                "total_trades": 0,
            },
            "contrastive_insights": [],
            "bridge_health": {
                "total_closed_trades": 0,
                "bridged_count": 0,
                "failed_count": 0,
                "completeness_pct": 100.0,
                "status": "healthy",
            },
        }

        with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_retrospectives_batch", return_value=[]), \
             patch("pilots.retrospective_insights.generate_batch_retrospective_insights", return_value=empty_insights):
            resp = client.get("/pilots/paper-broker/retrospective/insights", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()

            # Verify structural isolation
            assert "automated_cohort" in data
            assert "manual_cohort" in data
            assert "unrecorded_cohort" in data

            # Verify no aggregate performance metrics
            forbidden = [
                "win_rate", "profit_factor", "total_pnl", "total_realized_pnl",
                "mean_edge_ratio", "blended_win_rate", "overall_win_rate",
            ]
            for key in forbidden:
                assert key not in data, f"Fabrication violation: root key '{key}' found in insights response!"

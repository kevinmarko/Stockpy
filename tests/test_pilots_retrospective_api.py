"""tests/test_pilots_retrospective_api.py — Unit and Contract Tests for Retrospective Endpoints
=============================================================================================

Authoritative Requirements:
- .agents/ORIGINAL_REQUEST.md (§ R6, WP-G, WP-H)
- .agents/PROJECT.md (§ 4 Interface Contracts, Milestone 4)
- .agents/worker_m4/DISPATCH.md

Covers:
1. GET /pilots/paper-broker/trades/{trade_id}/retrospective
   - 200 on existing trade returning full composed record
   - 404 on nonexistent trade
   - 422 on non-integer trade_id path parameter
2. GET /pilots/paper-broker/retrospective/insights
   - 200 returning isolated automated_cohort, manual_cohort, unrecorded_cohort
   - Quantitative integrity check: ZERO blended aggregate metrics at root level
   - Query filters (symbol, strategy_id, limit boundary validation)
3. GET /pilots/paper-broker/bridge/metrics
   - 200 returning completeness percentage, total, bridged, and failed counts
4. Authentication & Authorization contract
   - Rejects unauthenticated/invalid tokens when token configured
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
# 0. Authentication & Authorization Enforcement
# =============================================================================

class TestEndpointAuthentication:
    """Tests for require_read_token behavior across retrospective endpoints."""

    def test_authenticated_token_required_when_configured(self) -> None:
        """When STATE_API_TOKEN is set, requests without bearer token get 401, with valid token get 200."""
        with patch.object(settings, "STATE_API_TOKEN", "secret_token_123"):
            test_client = TestClient(app, client=("127.0.0.1", 50000))

            # Missing token -> 401
            r_no_auth = test_client.get("/pilots/paper-broker/bridge/metrics")
            assert r_no_auth.status_code == 401

            # Invalid token -> 401
            r_bad_auth = test_client.get(
                "/pilots/paper-broker/bridge/metrics",
                headers={"Authorization": "Bearer wrong_token"}
            )
            assert r_bad_auth.status_code == 401

            # Valid token -> 200
            with patch("data.paper_account_store.PaperAccountStore.get_bridge_completeness_metrics", return_value={"status": "healthy"}):
                r_good_auth = test_client.get(
                    "/pilots/paper-broker/bridge/metrics",
                    headers={"Authorization": "Bearer secret_token_123"}
                )
                assert r_good_auth.status_code == 200

    def test_non_loopback_without_token_returns_503(self) -> None:
        """When STATE_API_TOKEN is unset and client is non-loopback, returns 503."""
        with patch.object(settings, "STATE_API_TOKEN", ""):
            remote_client = TestClient(app, client=("192.168.1.100", 50000))
            res = remote_client.get("/pilots/paper-broker/bridge/metrics")
            assert res.status_code == 503


# =============================================================================
# 1. Per-Trade Retrospective Endpoint
# =============================================================================

class TestPerTradeRetrospectiveEndpoint:
    """Tests for GET /pilots/paper-broker/trades/{trade_id}/retrospective."""

    def test_trade_not_found_returns_404(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Querying a nonexistent trade ID returns 404 with standard error detail."""
        with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_trade_retrospective", return_value=None):
            resp = client.get("/pilots/paper-broker/trades/999999/retrospective", headers=auth_headers)
            assert resp.status_code == 404
            assert "999999 not found" in resp.json().get("detail", "")

    def test_malformed_trade_id_returns_422(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Passing a non-integer trade_id yields 422 Unprocessable Entity."""
        resp = client.get("/pilots/paper-broker/trades/not_an_int/retrospective", headers=auth_headers)
        assert resp.status_code == 422

    def test_existing_trade_returns_composed_record_200(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Existing trade returns 200 with full composed schema."""
        mock_record: dict[str, Any] = {
            "trade_id": 42,
            "symbol": "AAPL",
            "strategy_id": "trend_following",
            "pilot_id": "pilot_alpha",
            "side": "BUY",
            "qty": 100.0,
            "entry_ts": "2026-09-01T10:00:00Z",
            "entry_price": 150.0,
            "exit_ts": "2026-09-05T15:30:00Z",
            "exit_price": 160.0,
            "commission": 0.0,
            "realized_pnl": 1000.0,
            "realized_pnl_pct": 0.0667,
            "holding_period_days": 4.2,
            "close_reason": "take_profit",
            "provenance": "signal_driven",
            "snapshot": {
                "captured": True,
                "decision_context_status": "captured",
                "conviction": 0.85,
                "macro_regime": "BULLISH_TREND",
                "signal_score": 0.78,
                "raw_forecast": 0.045,
            },
            "bridge_status": "bridged",
            "bridged_trade_id": 1042,
            "bridge_error": None,
            "bridged_at": "2026-09-05T15:30:01Z",
            "excursion": {
                "evaluation_status": "available",
                "status": "available",
                "bridge_reached": True,
                "mae": -120.0,
                "mfe": 1150.0,
                "edge_ratio": 9.58,
                "realized_slippage": 0.02,
                "reason": None,
            },
            "calibration": {
                "status": "scored",
                "calibration_status": "scored",
                "conviction": 0.85,
                "bin_range": "[0.8, 1.0]",
                "bin_win_rate": 0.80,
                "historical_bin_win_rate": 0.80,
                "bin_trade_count": 15,
                "calibration_error": 0.05,
                "reason": None,
            },
            "narrative": "Executed signal-driven BUY on AAPL with 0.85 conviction in BULLISH_TREND regime.",
        }

        with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_trade_retrospective", return_value=mock_record):
            resp = client.get("/pilots/paper-broker/trades/42/retrospective", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["trade_id"] == 42
            assert data["symbol"] == "AAPL"
            assert data["provenance"] == "signal_driven"
            assert data["bridge_status"] == "bridged"
            assert data["excursion"]["evaluation_status"] == "available"
            assert data["excursion"]["edge_ratio"] == 9.58
            assert data["calibration"]["status"] == "scored"
            assert "BULLISH_TREND" in data["narrative"]

    def test_pre_feature_trade_reports_not_captured(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Historical trade without snapshot returns honest not_captured state."""
        mock_record: dict[str, Any] = {
            "trade_id": 7,
            "symbol": "MSFT",
            "strategy_id": None,
            "pilot_id": None,
            "side": "BUY",
            "qty": 50.0,
            "entry_ts": "2026-01-10T14:00:00Z",
            "entry_price": 280.0,
            "exit_ts": "2026-01-12T15:00:00Z",
            "exit_price": 285.0,
            "commission": 0.0,
            "realized_pnl": 250.0,
            "realized_pnl_pct": 0.0179,
            "holding_period_days": 2.04,
            "close_reason": "closed",
            "provenance": "unknown",
            "snapshot": {
                "captured": False,
                "decision_context_status": "not_captured",
                "reason": "not captured",
            },
            "bridge_status": "bridged",
            "bridged_trade_id": 1007,
            "bridge_error": None,
            "bridged_at": None,
            "excursion": {
                "evaluation_status": "available",
                "status": "available",
                "bridge_reached": True,
                "mae": -50.0,
                "mfe": 300.0,
                "edge_ratio": 6.0,
                "realized_slippage": None,
                "reason": None,
            },
            "calibration": {
                "status": "not_applicable",
                "calibration_status": "not_applicable",
                "conviction": None,
                "bin_range": None,
                "bin_win_rate": None,
                "historical_bin_win_rate": None,
                "bin_trade_count": 0,
                "calibration_error": None,
                "reason": "Model calibration not applicable for manual or uncalibrated trades",
            },
            "narrative": "Executed BUY on MSFT; entry context not captured.",
        }

        with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_trade_retrospective", return_value=mock_record):
            resp = client.get("/pilots/paper-broker/trades/7/retrospective", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["provenance"] == "unknown"
            assert data["snapshot"]["decision_context_status"] == "not_captured"
            assert data["snapshot"]["captured"] is False
            assert data["calibration"]["status"] == "not_applicable"

    def test_bridge_failed_trade_reports_evaluation_unavailable(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Unbridged or failed bridge trade returns null excursion metrics with honest unavailable status."""
        mock_record: dict[str, Any] = {
            "trade_id": 99,
            "symbol": "TSLA",
            "strategy_id": "momentum",
            "pilot_id": None,
            "side": "BUY",
            "qty": 20.0,
            "entry_ts": "2026-08-01T10:00:00Z",
            "entry_price": 200.0,
            "exit_ts": "2026-08-03T16:00:00Z",
            "exit_price": 210.0,
            "commission": 0.0,
            "realized_pnl": 200.0,
            "realized_pnl_pct": 0.05,
            "holding_period_days": 2.25,
            "close_reason": "closed",
            "provenance": "signal_driven",
            "snapshot": {"captured": True, "conviction": 0.70},
            "bridge_status": "failed",
            "bridged_trade_id": None,
            "bridge_error": "Disk full error during bridge write",
            "bridged_at": None,
            "excursion": {
                "evaluation_status": "evaluation data unavailable",
                "status": "evaluation data unavailable",
                "bridge_reached": False,
                "mae": None,
                "mfe": None,
                "edge_ratio": None,
                "realized_slippage": None,
                "reason": "Evaluation data unavailable: bridge status 'failed'",
            },
            "calibration": {"status": "scored", "conviction": 0.70},
            "narrative": "Executed signal-driven BUY on TSLA; evaluation data unavailable.",
        }

        with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_trade_retrospective", return_value=mock_record):
            resp = client.get("/pilots/paper-broker/trades/99/retrospective", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["bridge_status"] == "failed"
            assert data["excursion"]["evaluation_status"] == "evaluation data unavailable"
            assert data["excursion"]["mae"] is None
            assert data["excursion"]["mfe"] is None
            assert data["excursion"]["edge_ratio"] is None


# =============================================================================
# 2. Batch Retrospective Insights Endpoint
# =============================================================================

class TestBatchRetrospectiveInsightsEndpoint:
    """Tests for GET /pilots/paper-broker/retrospective/insights."""

    def test_batch_insights_strictly_isolates_cohorts(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Batch insights partitions automated, manual, and unrecorded cohorts with ZERO root aggregates."""
        mock_insights = {
            "automated_cohort": {
                "cohort_name": "Automated (Signal-Driven)",
                "total_trades": 10,
                "trade_count": 10,
                "winning_trades": 7,
                "losing_trades": 3,
                "breakeven_trades": 0,
                "win_rate": 0.70,
                "total_realized_pnl": 3500.0,
                "profit_factor": 2.5,
                "mean_holding_period_days": 3.4,
                "mean_edge_ratio": 4.2,
                "mean_mae": -150.0,
                "mean_mfe": 630.0,
                "symbols": ["AAPL", "NVDA"],
                "calibration_brier_score": 0.12,
                "strategies": {},
            },
            "signal_driven_cohort": {
                "total_trades": 10,
                "win_rate": 0.70,
            },
            "manual_cohort": {
                "cohort_name": "Manual (Discretionary)",
                "total_trades": 5,
                "trade_count": 5,
                "winning_trades": 2,
                "losing_trades": 3,
                "breakeven_trades": 0,
                "win_rate": 0.40,
                "total_realized_pnl": -400.0,
                "profit_factor": 0.6,
                "mean_holding_period_days": 1.1,
                "mean_edge_ratio": 1.2,
                "mean_mae": -220.0,
                "mean_mfe": 260.0,
                "symbols": ["TSLA"],
                "calibration_status": "not_applicable",
            },
            "unrecorded_cohort": {
                "cohort_name": "Unrecorded (Pre-Feature / Missing Snapshot)",
                "total_trades": 2,
                "trade_count": 2,
                "winning_trades": 1,
                "losing_trades": 1,
                "breakeven_trades": 0,
                "win_rate": 0.50,
                "total_realized_pnl": 50.0,
                "profit_factor": 1.1,
                "mean_holding_period_days": 2.0,
                "mean_edge_ratio": 2.0,
                "mean_mae": -100.0,
                "mean_mfe": 200.0,
                "symbols": ["SPY"],
                "note": "Historical trades without entry snapshot; excluded from systematic model evaluation.",
            },
            "contrastive_insights": [
                "Automated strategies achieved a 70.0% win rate compared to manual discretionary trading's 40.0% win rate."
            ],
            "bridge_health": {
                "total_closed_trades": 17,
                "bridged_count": 17,
                "failed_count": 0,
                "disabled_count": 0,
                "completeness_pct": 100.0,
                "status": "healthy",
            },
        }

        with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_retrospectives_batch", return_value=[]), \
             patch("pilots.retrospective_insights.generate_batch_retrospective_insights", return_value=mock_insights):
            resp = client.get("/pilots/paper-broker/retrospective/insights", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()

            # Structural verification of isolated cohorts
            assert "automated_cohort" in data
            assert "manual_cohort" in data
            assert "unrecorded_cohort" in data
            assert "contrastive_insights" in data
            assert "bridge_health" in data

            # Strict anti-fabrication: ZERO top-level blended metrics
            forbidden_aggregate_keys = [
                "win_rate",
                "profit_factor",
                "total_realized_pnl",
                "total_pnl",
                "mean_edge_ratio",
                "edge_ratio",
                "blended_win_rate",
                "aggregate_win_rate",
            ]
            for key in forbidden_aggregate_keys:
                assert key not in data, f"Quant integrity breach: blended key '{key}' found at root level"

            # Cohorts report independent rates
            assert data["automated_cohort"]["win_rate"] == 0.70
            assert data["manual_cohort"]["win_rate"] == 0.40

    def test_batch_insights_query_parameters(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Query parameters symbol, strategy_id, limit are passed through and validated."""
        with patch("pilots.retrospective_composer.RetrospectiveComposer.compose_retrospectives_batch", return_value=[]) as mock_batch, \
             patch("pilots.retrospective_insights.generate_batch_retrospective_insights", return_value={"automated_cohort": {"total_trades": 0}, "manual_cohort": {"total_trades": 0}, "unrecorded_cohort": {"total_trades": 0}, "contrastive_insights": [], "bridge_health": {}}):

            resp = client.get(
                "/pilots/paper-broker/retrospective/insights?symbol=AAPL&strategy_id=momentum&limit=50",
                headers=auth_headers,
            )
            assert resp.status_code == 200
            mock_batch.assert_called_once_with(symbol="AAPL", strategy_id="momentum", limit=50)

    def test_batch_insights_limit_boundary_validation(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Limit parameter <= 0 or > 1000 produces 422 Unprocessable Entity."""
        resp_neg = client.get("/pilots/paper-broker/retrospective/insights?limit=-5", headers=auth_headers)
        assert resp_neg.status_code == 422

        resp_zero = client.get("/pilots/paper-broker/retrospective/insights?limit=0", headers=auth_headers)
        assert resp_zero.status_code == 422

        resp_huge = client.get("/pilots/paper-broker/retrospective/insights?limit=5000", headers=auth_headers)
        assert resp_huge.status_code == 422


# =============================================================================
# 3. Bridge Reliability Metrics Endpoint
# =============================================================================

class TestBridgeMetricsEndpoint:
    """Tests for GET /pilots/paper-broker/bridge/metrics."""

    def test_bridge_metrics_healthy_state(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Bridge metrics returns 200 with completeness percentage and health status."""
        mock_metrics = {
            "bridge_enabled": True,
            "total_closed_trades": 25,
            "attempted_count": 25,
            "bridged_count": 25,
            "failed_count": 0,
            "disabled_count": 0,
            "completeness_pct": 100.0,
            "status": "healthy",
            "last_failure": None,
        }

        with patch("data.paper_account_store.PaperAccountStore.get_bridge_completeness_metrics", return_value=mock_metrics):
            resp = client.get("/pilots/paper-broker/bridge/metrics", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["bridge_enabled"] is True
            assert data["total_closed_trades"] == 25
            assert data["bridged_count"] == 25
            assert data["failed_count"] == 0
            assert data["completeness_pct"] == 100.0
            assert data["status"] == "healthy"

    def test_bridge_metrics_degraded_state(self, client: TestClient, auth_headers: dict[str, str]) -> None:
        """Bridge metrics reports degraded status when failures exist."""
        mock_metrics = {
            "bridge_enabled": True,
            "total_closed_trades": 20,
            "attempted_count": 20,
            "bridged_count": 18,
            "failed_count": 2,
            "disabled_count": 0,
            "completeness_pct": 90.0,
            "status": "degraded",
            "last_failure": {
                "trade_id": 19,
                "symbol": "GOOGL",
                "timestamp": "2026-09-10T18:00:00Z",
                "error": "Simulated disk error",
            },
        }

        with patch("data.paper_account_store.PaperAccountStore.get_bridge_completeness_metrics", return_value=mock_metrics):
            resp = client.get("/pilots/paper-broker/bridge/metrics", headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "degraded"
            assert data["failed_count"] == 2
            assert data["completeness_pct"] == 90.0
            assert data["last_failure"]["trade_id"] == 19

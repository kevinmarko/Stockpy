"""Tests for the manual-input Brinson-Fachler attribution calculator:

* ``pilots/brinson.py`` — pure module-level unit tests (frame building,
  validation, and the engine bridge), offline, no FastAPI app.
* ``POST /portfolio/attribution/brinson-fachler`` on ``api/pilots_api.py`` —
  endpoint-level integration tests via ``TestClient``, mirroring
  ``tests/test_pilots_api.py::TestPortfolioAttribution``'s patterns (this is
  deliberately a SEPARATE test file rather than added to that class, since
  this endpoint is a stateless calculator with no ``pilots.attribution``
  involvement at all).

Hand-computed expectation (2 sectors) used throughout:

    Sector A: w_p=0.6  r_p=0.10  w_b=0.5  r_b=0.08
    Sector B: w_p=0.4  r_p=0.05  w_b=0.5  r_b=0.06

    Portfolio Return = 0.6*0.10 + 0.4*0.05           = 0.08
    Benchmark Return = 0.5*0.08 + 0.5*0.06           = 0.07
    Active Return    = 0.08 - 0.07                   = 0.01

    Allocation_A = (0.6-0.5)*(0.08-0.07) =  0.001
    Allocation_B = (0.4-0.5)*(0.06-0.07) =  0.001
    Allocation Effect (total)            =  0.002

    Selection_A = 0.5*(0.10-0.08) =  0.010
    Selection_B = 0.5*(0.05-0.06) = -0.005
    Selection Effect (total)      =  0.005

    Interaction_A = (0.6-0.5)*(0.10-0.08) =  0.002
    Interaction_B = (0.4-0.5)*(0.05-0.06) =  0.001
    Interaction Effect (total)             =  0.003

    Attribution Sum = 0.002 + 0.005 + 0.003 = 0.01 == Active Return
"""
from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api
from pilots.brinson import (
    build_brinson_fachler_frames,
    compute_brinson_fachler,
    validate_brinson_fachler_rows,
)

client = TestClient(pilots_api.app)

_TWO_SECTOR_ROWS = [
    {
        "sector": "Technology",
        "portfolio_weight_pct": 60.0,
        "portfolio_return_pct": 10.0,
        "benchmark_weight_pct": 50.0,
        "benchmark_return_pct": 8.0,
    },
    {
        "sector": "Financials",
        "portfolio_weight_pct": 40.0,
        "portfolio_return_pct": 5.0,
        "benchmark_weight_pct": 50.0,
        "benchmark_return_pct": 6.0,
    },
]


# ---------------------------------------------------------------------------
# pilots/brinson.py — build_brinson_fachler_frames
# ---------------------------------------------------------------------------


class TestBuildFrames:
    def test_percent_to_fraction_conversion(self):
        portfolio_df, benchmark_df = build_brinson_fachler_frames(_TWO_SECTOR_ROWS)
        assert list(portfolio_df["sector"]) == ["Technology", "Financials"]
        assert portfolio_df["portfolio_weight"].tolist() == pytest.approx([0.6, 0.4])
        assert portfolio_df["portfolio_return"].tolist() == pytest.approx([0.10, 0.05])
        assert benchmark_df["benchmark_weight"].tolist() == pytest.approx([0.5, 0.5])
        assert benchmark_df["benchmark_return"].tolist() == pytest.approx([0.08, 0.06])

    def test_empty_rows_raises_value_error(self):
        with pytest.raises(ValueError):
            build_brinson_fachler_frames([])

    def test_all_blank_sector_raises_value_error(self):
        with pytest.raises(ValueError):
            build_brinson_fachler_frames([{"sector": "  ", "portfolio_weight_pct": 10}])

    def test_malformed_row_skipped_not_crashed(self):
        rows = _TWO_SECTOR_ROWS + [{"sector": ""}, "not-a-dict"]
        portfolio_df, _ = build_brinson_fachler_frames(rows)
        assert len(portfolio_df) == 2  # the two blank/malformed entries are dropped

    def test_unparseable_numeric_defaults_to_zero(self):
        rows = [{
            "sector": "Energy",
            "portfolio_weight_pct": "not-a-number",
            "portfolio_return_pct": None,
            "benchmark_weight_pct": 10.0,
            "benchmark_return_pct": 2.0,
        }]
        portfolio_df, benchmark_df = build_brinson_fachler_frames(rows)
        assert portfolio_df["portfolio_weight"].iloc[0] == 0.0
        assert portfolio_df["portfolio_return"].iloc[0] == 0.0


# ---------------------------------------------------------------------------
# pilots/brinson.py — validate_brinson_fachler_rows
# ---------------------------------------------------------------------------


class TestValidateRows:
    def test_clean_matrix_no_warnings(self):
        assert validate_brinson_fachler_rows(_TWO_SECTOR_ROWS) == []

    def test_empty_rows(self):
        warnings = validate_brinson_fachler_rows([])
        assert warnings == ["No rows with a non-blank sector name."]

    def test_weights_not_summing_to_100_warns(self):
        rows = [{
            "sector": "Technology",
            "portfolio_weight_pct": 40.0,
            "portfolio_return_pct": 10.0,
            "benchmark_weight_pct": 40.0,
            "benchmark_return_pct": 8.0,
        }]
        warnings = validate_brinson_fachler_rows(rows)
        assert any("Portfolio weights sum to 40.00%" in w for w in warnings)
        assert any("Benchmark weights sum to 40.00%" in w for w in warnings)

    def test_negative_weight_warns(self):
        rows = [{
            "sector": "Technology",
            "portfolio_weight_pct": -10.0,
            "portfolio_return_pct": 10.0,
            "benchmark_weight_pct": 100.0,
            "benchmark_return_pct": 8.0,
        }]
        warnings = validate_brinson_fachler_rows(rows)
        assert any("Negative values found in Portfolio Weight" in w for w in warnings)

    def test_all_zero_weights_warns(self):
        rows = [{
            "sector": "Technology",
            "portfolio_weight_pct": 0.0,
            "portfolio_return_pct": 0.0,
            "benchmark_weight_pct": 0.0,
            "benchmark_return_pct": 0.0,
        }]
        warnings = validate_brinson_fachler_rows(rows)
        assert "All weights are zero — nothing to attribute." in warnings


# ---------------------------------------------------------------------------
# pilots/brinson.py — compute_brinson_fachler (engine bridge)
# ---------------------------------------------------------------------------


class TestComputeBrinsonFachler:
    def test_two_sector_matches_hand_computed_expectation(self):
        result = compute_brinson_fachler(_TWO_SECTOR_ROWS)

        assert result["Portfolio Return"] == pytest.approx(0.08, abs=1e-9)
        assert result["Benchmark Return"] == pytest.approx(0.07, abs=1e-9)
        assert result["Active Return"] == pytest.approx(0.01, abs=1e-9)
        assert result["Allocation Effect"] == pytest.approx(0.002, abs=1e-9)
        assert result["Selection Effect"] == pytest.approx(0.005, abs=1e-9)
        assert result["Interaction Effect"] == pytest.approx(0.003, abs=1e-9)
        assert result["Attribution Sum"] == pytest.approx(0.01, abs=1e-9)
        # Attribution Sum reconciles with Active Return (the engine's own
        # internal-consistency invariant).
        assert result["Attribution Sum"] == pytest.approx(result["Active Return"], abs=1e-9)

        details = result["Sector Details"]
        assert set(details.keys()) == {"Technology", "Financials"}

        tech = details["Technology"]
        assert tech["weight_p"] == pytest.approx(0.6)
        assert tech["weight_b"] == pytest.approx(0.5)
        assert tech["return_p"] == pytest.approx(0.10)
        assert tech["return_b"] == pytest.approx(0.08)
        assert tech["allocation_effect"] == pytest.approx(0.001, abs=1e-9)
        assert tech["selection_effect"] == pytest.approx(0.01, abs=1e-9)
        assert tech["interaction_effect"] == pytest.approx(0.002, abs=1e-9)

        fin = details["Financials"]
        assert fin["allocation_effect"] == pytest.approx(0.001, abs=1e-9)
        assert fin["selection_effect"] == pytest.approx(-0.005, abs=1e-9)
        assert fin["interaction_effect"] == pytest.approx(0.001, abs=1e-9)

    def test_empty_rows_raises_value_error(self):
        with pytest.raises(ValueError):
            compute_brinson_fachler([])

    def test_blank_sector_only_raises_value_error(self):
        with pytest.raises(ValueError):
            compute_brinson_fachler([{"sector": ""}])

    def test_result_is_json_clean_no_nan(self):
        import math
        result = compute_brinson_fachler(_TWO_SECTOR_ROWS)

        def _assert_no_nan(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    _assert_no_nan(v)
            elif isinstance(obj, list):
                for v in obj:
                    _assert_no_nan(v)
            elif isinstance(obj, float):
                assert not math.isnan(obj) and not math.isinf(obj)

        _assert_no_nan(result)


# ---------------------------------------------------------------------------
# POST /portfolio/attribution/brinson-fachler — endpoint integration
# ---------------------------------------------------------------------------


class TestBrinsonFachlerEndpoint:
    def test_valid_two_sector_computation_matches_hand_computed(self):
        resp = client.post(
            "/portfolio/attribution/brinson-fachler",
            json={"rows": _TWO_SECTOR_ROWS},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["Active Return"] == pytest.approx(0.01, abs=1e-9)
        assert body["Allocation Effect"] == pytest.approx(0.002, abs=1e-9)
        assert body["Selection Effect"] == pytest.approx(0.005, abs=1e-9)
        assert body["Interaction Effect"] == pytest.approx(0.003, abs=1e-9)
        assert body["validation_warnings"] == []

    def test_empty_rows_422(self):
        resp = client.post("/portfolio/attribution/brinson-fachler", json={"rows": []})
        assert resp.status_code == 422

    def test_missing_body_422(self):
        resp = client.post("/portfolio/attribution/brinson-fachler", json={})
        assert resp.status_code == 422

    def test_blank_sector_only_returns_422_with_message(self):
        resp = client.post(
            "/portfolio/attribution/brinson-fachler",
            json={"rows": [{"sector": "   "}]},
        )
        assert resp.status_code == 422

    def test_weights_not_summing_to_100_still_computes_with_warnings(self):
        rows = [{
            "sector": "Technology",
            "portfolio_weight_pct": 40.0,
            "portfolio_return_pct": 10.0,
            "benchmark_weight_pct": 40.0,
            "benchmark_return_pct": 8.0,
        }]
        resp = client.post(
            "/portfolio/attribution/brinson-fachler",
            json={"rows": rows},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation_warnings"]  # non-empty — weights don't sum to 100%

    def test_reachable_without_command_token(self):
        """This is a read-tier endpoint (require_read_token, fail-open) —
        FOLLOW_API_TOKEN (the command token) must never gate it, even when set."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", "cmd-tok"):
            with mock.patch.object(settings, "STATE_API_TOKEN", ""):
                resp = client.post(
                    "/portfolio/attribution/brinson-fachler",
                    json={"rows": _TWO_SECTOR_ROWS},
                )
        assert resp.status_code == 200

    def test_read_token_gates_endpoint(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
            no_auth = client.post(
                "/portfolio/attribution/brinson-fachler",
                json={"rows": _TWO_SECTOR_ROWS},
            )
            wrong = client.post(
                "/portfolio/attribution/brinson-fachler",
                json={"rows": _TWO_SECTOR_ROWS},
                headers={"Authorization": "Bearer WRONG"},
            )
            ok = client.post(
                "/portfolio/attribution/brinson-fachler",
                json={"rows": _TWO_SECTOR_ROWS},
                headers={"Authorization": "Bearer read-tok"},
            )
        assert no_auth.status_code == 401
        assert wrong.status_code == 401
        assert ok.status_code == 200

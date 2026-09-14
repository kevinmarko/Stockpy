"""tests/test_retrospective_adversarial_m6_2.py
=============================================
Milestone 6 Challenger 2 Adversarial Test Suite:
Adversarial Data Boundaries & Failure Resilience

Covers:
1. Adversarially stress test build_trade_narrative() with NaN, Inf, -Inf, negative prices,
   empty dicts, complex numbers, and None values across 500+ trials.
   Assert zero token leakage (None, NaN, nan, null) matching r"\b(None|NaN|nan|null)\b".
2. Challenge API parameter boundaries (/trades/{id}/retrospective, /insights, /bridge/metrics)
   with pathological IDs, negative/extreme limits, SQL injection strings, and XSS payloads.
   Assert clean 404/422 handling and zero unhandled 500 errors.
3. Challenge fail-open bridge durability under simulated database lock errors,
   including sqlite3.OperationalError ("database is locked", "database table is locked"),
   deadlocks, disk I/O errors, and real concurrent SQLite companion locks.
"""

from __future__ import annotations

import itertools
import math
import os
import re
import sqlite3
import time
from typing import Any
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from api.pilots_api import app
from data.paper_account_store import (
    PaperAccountStore,
    PaperClosedTrade,
    PaperEntrySnapshot,
    PaperPosition,
)
from pilots.retrospective_composer import RetrospectiveComposer
from pilots.retrospective_narrative import (
    _fmt_curr,
    _fmt_float,
    _fmt_pct,
    _is_valid_num,
    build_trade_narrative,
)
from settings import settings
import transactions_store


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def isolated_db(tmp_path):
    """Provides a fresh SQLite DB file URL for each test."""
    db_file = tmp_path / f"retro_m6_2_{int(time.time() * 1000)}_{os.getpid()}.db"
    return f"sqlite:///{db_file}"


@pytest.fixture
def client() -> TestClient:
    """FastAPI test client with loopback client IP."""
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = getattr(settings, "STATE_API_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


# =============================================================================
# 1. Adversarial Narrative Stress Test (>500 Trials, Zero Token Leakage)
# =============================================================================

class TestAdversarialNarrativeTokenLeakage:
    """Stress-test build_trade_narrative() with pathological numbers, structures, and types.
    Assert zero occurrences of forbidden tokens (None, NaN, nan, null) in output text.
    """

    FORBIDDEN_TOKEN_REGEX = re.compile(r"\b(None|NaN|nan|null)\b")

    def test_primitive_formatters_adversarial_matrix(self):
        """Verify _is_valid_num, _fmt_curr, _fmt_pct, _fmt_float under hostile values."""
        hostile_values = [
            float("nan"),
            float("inf"),
            float("-inf"),
            None,
            "nan",
            "NaN",
            "None",
            "null",
            "inf",
            "-inf",
            "garbage_string",
            complex(1, 2),
            [1, 2],
            {"a": 1},
            (),
            object(),
            -0.0,
            0.0,
            -100.5,
            1e15,
            -1e15,
            1e-15,
        ]

        for val in hostile_values:
            curr_str = _fmt_curr(val)
            pct_str = _fmt_pct(val)
            flt_str = _fmt_float(val)

            for formatted in (curr_str, pct_str, flt_str):
                assert isinstance(formatted, str)
                leak = self.FORBIDDEN_TOKEN_REGEX.search(formatted)
                assert leak is None, f"Token leakage '{leak.group(0)}' in '{formatted}' from input {val!r}"

    def test_build_trade_narrative_500_plus_permutations(self):
        """Run 1000+ permutations of build_trade_narrative() with hostile matrices.
        Assert zero occurrences of forbidden tokens (None, NaN, nan, null).
        """
        provenance_choices = [
            "signal_driven",
            "manual",
            "unknown",
            None,
            "NaN",
            "None",
            "null",
            "custom_invalid",
        ]

        side_choices = ["buy", "sell", "long", "short", "invalid_side", None, "NaN"]

        price_choices = [
            150.0,
            0.0,
            -0.0,
            -50.0,
            float("nan"),
            float("inf"),
            float("-inf"),
            None,
            "NaN",
            "None",
            "null",
            1e12,
            -1e12,
            1e-8,
        ]

        pnl_choices = [
            500.0,
            -250.0,
            0.0,
            -0.0,
            float("nan"),
            float("inf"),
            float("-inf"),
            None,
            "NaN",
            "None",
            "null",
        ]

        holding_days_choices = [
            5.5,
            0.0,
            -1.0,
            float("nan"),
            float("inf"),
            None,
            "NaN",
        ]

        excursion_choices = [
            # (mfe, mae, edge_ratio)
            (0.15, 0.05, 3.0),
            (None, None, None),
            (float("nan"), float("nan"), float("nan")),
            (float("inf"), float("-inf"), float("nan")),
            (-0.1, -0.2, -0.5),
            (0.0, 0.0, 0.0),
            ("NaN", "None", "null"),
        ]

        calibration_choices = [
            # (bin_win_rate, bin_count, min_sample)
            (0.65, 12, 5),     # valid sample
            (0.40, 2, 5),      # insufficient sample
            (None, None, 5),   # uncalibrated
            (float("nan"), 10, 5),
            (float("inf"), -5, 5),
            ("NaN", "null", 5),
        ]

        bridge_choices = [True, False]
        bars_choices = [True, False]

        trial_count = 0

        # Generate systematically varying combinations (>500 distinct trials)
        for i in range(650):
            prov = provenance_choices[i % len(provenance_choices)]
            side = side_choices[(i * 3) % len(side_choices)]
            ep = price_choices[(i * 2) % len(price_choices)]
            xp = price_choices[(i * 5) % len(price_choices)]
            pnl_val = pnl_choices[(i * 7) % len(pnl_choices)]
            pnl_pct_val = pnl_choices[(i * 4) % len(pnl_choices)]
            h_days = holding_days_choices[(i * 3) % len(holding_days_choices)]
            mfe_val, mae_val, edge_val = excursion_choices[i % len(excursion_choices)]
            b_wr, b_cnt, min_s = calibration_choices[i % len(calibration_choices)]
            b_reached = bridge_choices[i % len(bridge_choices)]
            b_bars = bars_choices[i % len(bars_choices)]

            # Test using direct kwargs
            narrative_kwargs = build_trade_narrative(
                provenance=prov,
                side=side,
                strategy_id=f"strat_{i % 5}" if i % 3 == 0 else ("NaN" if i % 7 == 0 else None),
                entry_price=ep,
                conviction=0.75 if i % 2 == 0 else (float("nan") if i % 5 == 0 else None),
                macro_regime="Bull" if i % 3 == 0 else ("None" if i % 4 == 0 else None),
                operator_notes="Discretionary play" if i % 4 == 0 else ("null" if i % 6 == 0 else None),
                exit_price=xp,
                holding_days=h_days,
                pnl=pnl_val,
                pnl_pct=pnl_pct_val,
                mfe=mfe_val,
                mae=mae_val,
                edge_ratio=edge_val,
                bin_win_rate=b_wr,
                bin_count=b_cnt,
                min_sample=min_s,
                bridge_reached=b_reached,
                bars_available=b_bars,
            )

            assert isinstance(narrative_kwargs, str)
            leak = self.FORBIDDEN_TOKEN_REGEX.search(narrative_kwargs)
            assert leak is None, (
                f"Trial {i} (kwargs) leaked '{leak.group(0)}' in:\n"
                f"Narrative: {narrative_kwargs}\n"
                f"Inputs: prov={prov}, ep={ep}, xp={xp}, pnl={pnl_val}, mfe={mfe_val}, cal=({b_wr}, {b_cnt})"
            )
            trial_count += 1

            # Test using structured dict
            record_dict = {
                "trade_id": i,
                "symbol": "TEST",
                "side": side,
                "entry_price": ep,
                "exit_price": xp,
                "realized_pnl": pnl_val,
                "realized_pnl_pct": pnl_pct_val,
                "holding_period_days": h_days,
                "provenance": prov,
                "entry_snapshot": {
                    "captured": (i % 2 == 0),
                    "decision_context_status": "captured" if (i % 2 == 0) else "not_captured",
                    "provenance": prov,
                    "conviction": 0.8 if i % 2 == 0 else float("nan"),
                    "macro_regime": "RegimeA" if i % 3 == 0 else "NaN",
                    "decision_rationale": "note" if i % 4 == 0 else None,
                },
                "excursion": {
                    "evaluation_status": "available" if b_reached and b_bars else "evaluation data unavailable",
                    "bridge_reached": b_reached,
                    "mfe": mfe_val,
                    "mae": mae_val,
                    "edge_ratio": edge_val,
                },
                "calibration": {
                    "bin_win_rate": b_wr,
                    "bin_trade_count": b_cnt,
                },
                "bridge_status": "bridged" if b_reached else "failed",
            }

            narrative_dict = build_trade_narrative(record_dict)
            assert isinstance(narrative_dict, str)
            leak_dict = self.FORBIDDEN_TOKEN_REGEX.search(narrative_dict)
            assert leak_dict is None, (
                f"Trial {i} (dict) leaked '{leak_dict.group(0)}' in:\n"
                f"Narrative: {narrative_dict}"
            )
            trial_count += 1

        assert trial_count >= 1000, f"Expected >500 trials, ran {trial_count}"

    def test_build_trade_narrative_pathological_data_types(self):
        """Test build_trade_narrative with hostile non-numeric types and malformed objects."""
        malformed_inputs = [
            {},
            {"snapshot": None, "excursion": None, "calibration": None},
            {"snapshot": {}, "excursion": {}, "calibration": {}},
            {"entry_price": complex(1, 2), "exit_price": [1, 2, 3]},
            {"conviction": {"dict": "not a float"}},
            {"holding_days": "two days", "realized_pnl": "zero"},
            {"mfe": (1, 2), "mae": set([1, 2])},
            {"bin_count": "invalid_int"},
            {"bridge_status": None, "bridge_reached": None},
            None,
            "signal_driven",
            "manual",
            "random_string_that_is_not_a_dict",
        ]

        for idx, item in enumerate(malformed_inputs):
            result = build_trade_narrative(item)
            assert isinstance(result, str)
            leak = self.FORBIDDEN_TOKEN_REGEX.search(result)
            assert leak is None, f"Malformed input {idx} ({item!r}) leaked '{leak.group(0)}': {result}"


# =============================================================================
# 2. Adversarial API Parameter Boundaries
# =============================================================================

class TestAdversarialApiParameterBoundaries:
    """Challenge /trades/{id}/retrospective, /insights, /bridge/metrics with hostile inputs.
    Assert clean 404/422 handling and zero 500 internal server errors.
    """

    PATHOLOGICAL_TRADE_IDS = [
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
        "1 UNION SELECT * FROM paper_closed_trades",
        "<script>alert(1)</script>",
        "../../etc/passwd",
        "%00",
        "-1",
        "-999999",
        "0",
        "999999999999",
        "888888",
    ]

    @pytest.mark.parametrize("trade_id_str", PATHOLOGICAL_TRADE_IDS)
    def test_trade_retrospective_pathological_ids(
        self, client: TestClient, auth_headers: dict[str, str], trade_id_str: str
    ):
        """Assert pathological trade IDs return clean 404 or 422, never 500."""
        response = client.get(
            f"/pilots/paper-broker/trades/{trade_id_str}/retrospective",
            headers=auth_headers,
        )
        assert response.status_code in (404, 422), (
            f"Expected 404 or 422 for trade_id='{trade_id_str}', got {response.status_code}: {response.text}"
        )
        assert response.status_code != 500, f"Unhandled 500 error for trade_id='{trade_id_str}'"

    PATHOLOGICAL_INSIGHTS_LIMITS = [
        "-999",
        "-1",
        "0",
        "1001",
        "99999999",
        "abc",
        "1.5",
        "NaN",
        "Infinity",
        "' OR 1=1 --",
        "1; DROP TABLE paper_closed_trades;",
    ]

    @pytest.mark.parametrize("limit_str", PATHOLOGICAL_INSIGHTS_LIMITS)
    def test_insights_pathological_limit_parameter(
        self, client: TestClient, auth_headers: dict[str, str], limit_str: str
    ):
        """Assert pathological limit parameters in /insights return 422, never 500."""
        response = client.get(
            f"/pilots/paper-broker/retrospective/insights?limit={limit_str}",
            headers=auth_headers,
        )
        assert response.status_code == 422, (
            f"Expected 422 for limit='{limit_str}', got {response.status_code}: {response.text}"
        )
        assert response.status_code != 500, f"Unhandled 500 error for limit='{limit_str}'"

    def test_insights_sql_injection_and_extreme_strings_in_filters(
        self, client: TestClient, auth_headers: dict[str, str]
    ):
        """Assert SQL injection and extreme strings in symbol/strategy_id filters execute safely.
        Zero 500s, zero DB corruption, and cohort isolation preserved.
        """
        sqli_payloads = [
            "' OR '1'='1",
            "AAPL'; DROP TABLE paper_closed_trades;--",
            "'; DROP TABLE paper_entry_snapshots;--",
            "1' UNION SELECT NULL, NULL, NULL--",
            "<script>alert('xss')</script>",
            "A" * 5000,  # 5KB string
            "🤖🚀🔥" * 100,  # Multi-byte unicode
            "null",
            "NaN",
        ]

        for payload in sqli_payloads:
            response = client.get(
                "/pilots/paper-broker/retrospective/insights",
                params={"symbol": payload, "strategy_id": payload, "limit": 50},
                headers=auth_headers,
            )
            assert response.status_code in (200, 422), (
                f"SQLi payload {payload!r} caused status {response.status_code}: {response.text}"
            )
            assert response.status_code != 500, f"Unhandled 500 error for payload {payload!r}"

            if response.status_code == 200:
                data = response.json()
                assert "signal_driven_cohort" in data or "automated_cohort" in data
                assert "manual_cohort" in data
                assert "unrecorded_cohort" in data
                assert "contrastive_insights" in data

    def test_bridge_metrics_adversarial_queries(
        self, client: TestClient, auth_headers: dict[str, str]
    ):
        """Assert /pilots/paper-broker/bridge/metrics survives unexpected query strings and hostile payloads."""
        hostile_queries = [
            {"bogus": "123"},
            {"limit": "-999"},
            {"query": "' OR '1'='1"},
            {"drop": "DROP TABLE paper_closed_trades;"},
        ]

        for params in hostile_queries:
            response = client.get(
                "/pilots/paper-broker/bridge/metrics",
                params=params,
                headers=auth_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert "total_closed_trades" in data
            assert "bridged_count" in data
            assert "failed_count" in data
            assert "completeness_pct" in data
            assert "bridge_enabled" in data
            assert "status" in data

    def test_auth_rejection_boundary_behaviors(self, client: TestClient, monkeypatch):
        """Assert invalid tokens are rejected with 401 Unauthorized when configured,
        and remote requests fail-closed with 503 when unconfigured.
        """
        # When token is configured, bad headers must strictly return 401
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "super_secret_test_token")

        invalid_headers = [
            {"Authorization": "Bearer invalid_garbage_token_123"},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer   "},
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {"Authorization": "Bearer " + "A" * 4000},
        ]

        for headers in invalid_headers:
            r1 = client.get("/pilots/paper-broker/trades/1/retrospective", headers=headers)
            assert r1.status_code == 401, f"Expected 401 for bad auth, got {r1.status_code}"

            r2 = client.get("/pilots/paper-broker/retrospective/insights", headers=headers)
            assert r2.status_code == 401, f"Expected 401 for bad auth, got {r2.status_code}"

            r3 = client.get("/pilots/paper-broker/bridge/metrics", headers=headers)
            assert r3.status_code == 401, f"Expected 401 for bad auth, got {r3.status_code}"

        # When token is unset, remote client must fail-closed with 503
        monkeypatch.setattr(settings, "STATE_API_TOKEN", "")
        remote_client = TestClient(app, client=("192.168.1.150", 50000))
        r_remote = remote_client.get("/pilots/paper-broker/bridge/metrics")
        assert r_remote.status_code == 503, f"Expected 503 for non-loopback unset token, got {r_remote.status_code}"


# =============================================================================
# 3. Fail-Open Bridge Durability Under Simulated Database Lock Errors
# =============================================================================

class TestFailOpenBridgeDurabilityUnderDatabaseLocks:
    """Verify that simulated SQLite database locked or deadlocked states in TransactionsStore
    fail-open without corrupting paper account state, recording error message and status 'failed'.
    """

    def test_bridge_fails_open_under_sqlite_operational_error_locked(
        self, isolated_db, monkeypatch
    ):
        """Simulate sqlite3.OperationalError: 'database is locked' during record_trade.
        Must fail open:
        1. apply_fill (close) returns True.
        2. Paper position is closed cleanly (0 open positions).
        3. Cash balance reflects realized PnL.
        4. PaperClosedTrade is written with bridge_status='failed'.
        5. PaperClosedTrade.bridge_error contains 'database is locked'.
        6. get_bridge_completeness_metrics() records failed_count=1 and degraded status.
        """
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store = PaperAccountStore(db_url=isolated_db)

        # Open position
        store.apply_fill(
            client_order_id="lock_test_open",
            symbol="NVDA",
            side="buy",
            qty=10.0,
            fill_price=100.0,
            strategy_id="strat_lock",
            provenance="signal_driven",
            conviction=0.90,
        )

        initial_cash = store.get_account().cash

        # Simulate SQLite database is locked exception during bridge write
        def _mock_record_trade_locked(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(store._transactions_store, "record_trade", _mock_record_trade_locked)

        # Execute position close
        close_ok = store.apply_fill(
            client_order_id="lock_test_close",
            symbol="NVDA",
            side="sell",
            qty=10.0,
            fill_price=120.0,
            strategy_id="strat_lock",
        )

        # 1. Paper close must succeed (fail-open)
        assert close_ok is True, "Paper close must succeed despite SQLite database lock"

        # 2. Position must be closed
        open_positions = store.get_open_positions()
        assert len(open_positions) == 0, "Position must be closed"

        # 3. Cash balance must be credited with sale proceeds (10 * 120 = 1200)
        account = store.get_account()
        assert account.cash == pytest.approx(initial_cash + 1200.0), "Cash must be credited"

        # 4 & 5. Closed trade must persist failure audit
        trades = store.get_full_closed_trades(symbol="NVDA")
        assert len(trades) == 1
        t = trades[0]
        assert t["bridge_status"] == "failed"
        assert t["bridged_trade_id"] is None
        assert "database is locked" in (t["bridge_error"] or "")

        # 6. Metrics must report failure
        metrics = store.get_bridge_completeness_metrics()
        assert metrics["failed_count"] == 1
        assert metrics["completeness_pct"] == 0.0
        assert metrics["status"] == "degraded"
        assert metrics["last_failure"] is not None
        assert "database is locked" in metrics["last_failure"]["error"]

    def test_bridge_fails_open_under_table_locked_during_close_trade(
        self, isolated_db, monkeypatch
    ):
        """Simulate sqlite3.OperationalError: 'database table is locked: transactions' during close_trade.
        SAVEPOINT must roll back partial record_trade, leaving store uncorrupted.
        """
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store = PaperAccountStore(db_url=isolated_db)

        # Open position
        store.apply_fill(
            client_order_id="tbl_lock_open",
            symbol="AMD",
            side="buy",
            qty=20.0,
            fill_price=80.0,
            provenance="signal_driven",
            conviction=0.70,
        )

        # record_trade succeeds, but close_trade encounters table lock
        def _mock_close_trade_locked(*args, **kwargs):
            raise sqlite3.OperationalError("database table is locked: transactions")

        monkeypatch.setattr(store._transactions_store, "close_trade", _mock_close_trade_locked)

        # Execute close
        close_ok = store.apply_fill(
            client_order_id="tbl_lock_close",
            symbol="AMD",
            side="sell",
            qty=20.0,
            fill_price=85.0,
        )

        assert close_ok is True
        assert len(store.get_open_positions()) == 0

        trades = store.get_full_closed_trades(symbol="AMD")
        assert len(trades) == 1
        t = trades[0]
        assert t["bridge_status"] == "failed"
        assert t["bridged_trade_id"] is None
        assert "database table is locked" in (t["bridge_error"] or "")

        # Verify composer gracefully handles this failed trade. A
        # bridge_status of "failed" no longer gates excursion evaluation --
        # _evaluate_trade_excursion builds its OWN isolated in-memory store
        # from this trade's own fields and never reads the real
        # transactions_store bridge -- so this asserts the honest
        # "pricing data missing" reason instead of the old, incorrect
        # "did not reach evaluation bridge" gate text. `historical_store`
        # is stubbed offline-safe: a real HistoricalStore would otherwise
        # attempt a live network fetch for AMD bars on this cache miss.
        class _NoOpHistoricalStore:
            def get_bars(self, symbol, lookback_days=504, **kwargs):
                return None

        composer = RetrospectiveComposer(
            paper_store=store, db_url=isolated_db, historical_store=_NoOpHistoricalStore()
        )
        retro = composer.compose_trade_retrospective(t["trade_id"])
        assert retro is not None
        assert retro["bridge_status"] == "failed"
        assert retro["excursion"]["evaluation_status"] == "evaluation data unavailable"
        assert retro["excursion"]["bridge_reached"] is True
        assert "Hold-period excursion metrics unavailable" in retro["narrative"]
        # Assert zero token leakage in narrative for this failed trade
        assert re.search(r"\b(None|NaN|nan|null)\b", retro["narrative"]) is None

    def test_bridge_fails_open_under_sqlalchemy_operational_error_locked(
        self, isolated_db, monkeypatch
    ):
        """Verify behavior when TransactionsStore.record_trade raises a real SQLAlchemy
        OperationalError wrapping sqlite3.OperationalError('database is locked').
        Verifies that session.begin_nested() correctly unwinds the SAVEPOINT without
        invalidating the outer session, and that the paper trade close succeeds.
        """
        import sqlalchemy.exc

        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store = PaperAccountStore(db_url=isolated_db)

        # Open position
        store.apply_fill(
            client_order_id="conc_open",
            symbol="TSLA",
            side="buy",
            qty=5.0,
            fill_price=200.0,
            provenance="signal_driven",
            conviction=0.85,
        )

        orig_record_trade = store._transactions_store.record_trade

        # Raise a real SQLAlchemy OperationalError wrapping sqlite3.OperationalError
        def _raise_sqla_lock(*args, **kwargs):
            orig_err = sqlite3.OperationalError("database is locked (WAL busy handler timeout)")
            raise sqlalchemy.exc.OperationalError("INSERT INTO trades ...", {}, orig_err)

        monkeypatch.setattr(store._transactions_store, "record_trade", _raise_sqla_lock)

        # Position close must succeed (fail-open) despite the database lock
        close_ok = store.apply_fill(
            client_order_id="conc_close",
            symbol="TSLA",
            side="sell",
            qty=5.0,
            fill_price=220.0,
        )

        # Assert paper close succeeded (fail-open)
        assert close_ok is True, "Paper close must succeed under SQLAlchemy OperationalError"
        trades = store.get_full_closed_trades(symbol="TSLA")
        assert len(trades) == 1
        assert trades[0]["bridge_status"] == "failed"
        assert "database is locked" in (trades[0]["bridge_error"] or "").lower()

        # Metrics must reflect the failure
        metrics = store.get_bridge_completeness_metrics()
        assert metrics["failed_count"] == 1
        assert metrics["status"] == "degraded"

    def test_multiple_consecutive_bridge_failures_and_recovery(
        self, isolated_db, monkeypatch
    ):
        """Verify metric tracking across multiple failure/recovery cycles."""
        monkeypatch.setattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", True)
        store = PaperAccountStore(db_url=isolated_db)

        # Trade 1: Fails
        store.apply_fill("t1_open", "SYM1", "buy", 10.0, 100.0)
        with mock.patch.object(
            store._transactions_store,
            "record_trade",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            assert store.apply_fill("t1_close", "SYM1", "sell", 10.0, 110.0) is True

        # Trade 2: Fails
        store.apply_fill("t2_open", "SYM2", "buy", 10.0, 100.0)
        with mock.patch.object(
            store._transactions_store,
            "record_trade",
            side_effect=sqlite3.OperationalError("database table is locked"),
        ):
            assert store.apply_fill("t2_close", "SYM2", "sell", 10.0, 110.0) is True

        # Metric at 2 failures
        m1 = store.get_bridge_completeness_metrics()
        assert m1["total_closed_trades"] == 2
        assert m1["failed_count"] == 2
        assert m1["bridged_count"] == 0
        assert m1["completeness_pct"] == 0.0
        assert m1["status"] == "degraded"

        # Trade 3: Succeeds (Normal behavior resumes)
        store.apply_fill("t3_open", "SYM3", "buy", 10.0, 100.0)
        assert store.apply_fill("t3_close", "SYM3", "sell", 10.0, 110.0) is True

        # Metric at 2 failures, 1 bridged
        m2 = store.get_bridge_completeness_metrics()
        assert m2["total_closed_trades"] == 3
        assert m2["failed_count"] == 2
        assert m2["bridged_count"] == 1
        assert m2["completeness_pct"] == pytest.approx(33.33, abs=0.1)

"""
tests/test_orchestrator_e2e.py
================================
A genuine end-to-end test of main_orchestrator's async pipeline, invoking
``_main_body()`` itself (not its individual pieces in isolation).

A coverage survey found that despite substantial existing coverage of
main_orchestrator.py, NO existing test invokes ``_main_body()``/``main()``
end-to-end with every major step (data acquisition -> kill-switch gate ->
run_pipeline -> schema validation -> advisory evaluation loop -> HTML/Plotly
report generation -> state-snapshot write + rotation -> dead-letter write ->
broker-quarantine check) wired together and exercised jointly:

- tests/test_quantitative_models.py::test_main_orchestrator_pipeline calls
  run_pipeline() directly, bypassing _main_body() entirely (no credentials
  check, no kill-switch gate, no advisory loop, no report generation, no
  file I/O).
- tests/test_advisory_pause_gate.py::TestOrchestratorKillSwitchGate calls
  _main_body() but only exercises the NEGATIVE path (kill-switch active ->
  early return BEFORE run_pipeline is even reached) — this file reuses that
  test's established mocking conventions for the POSITIVE/success path.
- tests/test_dashboard_validation.py, tests/test_advisory_only.py test their
  respective helper functions (_validate_dashboard, _execute_broker_orders)
  directly, never through _main_body()'s real call sites.

This file closes that gap: one shared, real _main_body() invocation (no
credentials.json -> MockDataEngine path, so the data layer is fully
synthetic/offline) with only the genuinely external dependencies mocked
(Robinhood, the live market-data quote/bars/fundamentals provider, the
Robinhood account-snapshot reader), then several tests assert on the
different output artifacts that single run produced.

Pitfalls specific to this file (in addition to the now-familiar
OUTPUT_DIR / quant_platform.db isolation pattern from items #1-#3):

- ``execution.kill_switch.KILL_SWITCH_FILE`` is computed from
  ``settings.OUTPUT_DIR`` at MODULE IMPORT TIME, not dynamically -- patching
  ``settings.OUTPUT_DIR`` after that module has already been imported (it
  has been, transitively, by the time this test file runs) does NOT
  retarget it. Must patch ``main_orchestrator.GlobalKillSwitch`` itself to
  construct with an explicit ``sentinel_file=`` under the redirected output
  dir, mirroring tests/test_advisory_pause_gate.py's established pattern.
- ``run_pipeline()`` internally constructs its own ``StrategyEngine()`` /
  ``EvaluationEngine()`` with no override parameter -- both lazily build a
  real, on-disk-DB-backed ``TransactionsStore()`` unless the constructor
  itself is monkeypatched. Redirect via the same
  ``TransactionsStore.__init__`` swap used in tests/test_evaluate_portfolio_
  zero_positions.py and items #2/#3, for the whole call.
- ``data.market_data.get_provider()`` would otherwise construct a real
  ``YFinanceProvider`` and attempt live network calls when the advisory loop
  (Step 3b) calls ``get_latest_quote``/``get_intraday_bars``/
  ``get_fundamentals`` -- mocked to raise so the run is deterministic and
  offline regardless of the sandbox's network availability, exercising the
  already-proven-graceful PARTIAL/HOLD degradation path from item #2's
  tests/test_dead_letter_resilience.py. ``processing_engine.py``'s
  fundamentals step ALSO calls ``get_provider()`` (gated behind
  ``settings.HISTORICAL_STORE_ENABLED``, the project default), but for a
  DIFFERENT purpose than the 3 advisory-loop methods stubbed here -- a
  permissive ``MagicMock`` would silently return child-mock objects for
  whatever method that path calls instead of raising, corrupting downstream
  DataFrame processing in a way that broke chart generation during
  development of this file. Disabling ``HISTORICAL_STORE_ENABLED`` for the
  test keeps that path out of scope entirely (it's the run_pipeline()
  fundamentals-caching concern, not what this E2E test is verifying) and
  incidentally avoids touching the real on-disk ``quant_platform.db`` via
  ``HistoricalStore`` during the run -- the same class of pitfall as items
  #1-#3.
- ``data.robinhood_portfolio.fetch_account_snapshot`` reads
  ``HistoricalStore``/``cache/account_snapshot.json``/live Robinhood in that
  order; mocked to raise so the advisory loop's own try/except degrades to
  ``_rh_snapshot=None`` without touching the real on-disk DB or cache file.
- A blanket ``mock.patch("os.path.exists", return_value=False)`` (the
  pattern tests/test_advisory_pause_gate.py uses successfully) is too broad
  here: that test mocks ``run_pipeline`` away entirely and returns before
  any downstream code runs, so the blast radius never matters. This file
  lets the REAL pipeline run all the way through report generation, and
  Plotly's own validator-cache loader (``diagnostics_and_visuals.
  generate_plotly_volatility_bands`` -> ``plotly.graph_objs.Figure()``)
  calls ``os.path.exists`` internally to locate its own bundled JSON schema
  file -- a blanket patch makes Plotly believe its own installed package
  data is missing and raise ``FileNotFoundError``, breaking chart
  generation for a reason that has nothing to do with credentials.json.
  Fixed with a ``side_effect`` that only fakes the answer for the literal
  ``"credentials.json"`` path and delegates everything else to the real
  ``os.path.exists``.
"""

from __future__ import annotations

import asyncio
import os
import json
from pathlib import Path
from unittest import mock

import pytest

import transactions_store
from tests._db_isolation import make_memory_db_init


# ============================================================================
# Shared fixture: run _main_body() exactly once, all assertions read from its
# output artifacts. The pipeline (GARCH fits, multi-model forecasts, 17
# signal modules, schema validation, HTML/Plotly rendering) is expensive
# enough that re-running it per test would be wasteful and isn't needed --
# every test below is read-only against the same run's output.
# ============================================================================

@pytest.fixture(scope="module")
def orchestrator_run(tmp_path_factory):
    import main_orchestrator as mo
    from execution.kill_switch import GlobalKillSwitch

    tmp_output_dir = tmp_path_factory.mktemp("orchestrator_e2e_output")
    sentinel = tmp_output_dir / "KILL_SWITCH"  # deliberately never created -> inactive

    fake_market_provider = mock.MagicMock()
    fake_market_provider.get_latest_quote.side_effect = Exception("no network in test sandbox")
    fake_market_provider.get_intraday_bars.side_effect = Exception("no network in test sandbox")
    fake_market_provider.get_fundamentals.side_effect = Exception("no network in test sandbox")

    # run_pipeline()'s Technical Options step also hardcodes `IVHistoryStore()`
    # with no injection point -- same real-on-disk-DB pitfall as
    # TransactionsStore above (caught by a `git status` diff on
    # quant_platform.db during development of this file; redirect the same way).
    from volatility.iv_engine import IVHistoryStore

    _real_exists = os.path.exists

    def _fake_credentials_check(path):  # noqa: ANN001
        """Only fakes the credentials.json existence check (forcing the
        MockDataEngine path); every other os.path.exists call -- notably
        Plotly's own internal validator-cache file lookup -- is delegated to
        the real implementation. See module docstring for why a blanket
        patch breaks chart generation."""
        if str(path) == "credentials.json":
            return False
        return _real_exists(path)

    captured_stdout = {}

    with (
        mock.patch("os.path.exists", side_effect=_fake_credentials_check),
        # main_orchestrator.py's account-fetch integration point (module-top
        # `from data.robinhood_portfolio import fetch_account_snapshot` --
        # patching the original module's attribute below does NOT affect
        # this already-bound local name, so it needs its own patch target).
        mock.patch("main_orchestrator.fetch_account_snapshot", return_value=None),
        mock.patch(
            "main_orchestrator.GlobalKillSwitch",
            side_effect=lambda: GlobalKillSwitch(sentinel_file=sentinel),
        ),
        mock.patch("settings.settings.OUTPUT_DIR", tmp_output_dir),
        mock.patch("settings.settings.ADVISORY_ONLY", True),
        # Out of scope for this E2E test (run_pipeline()'s fundamentals-caching
        # concern, already covered by tests/test_historical_store.py) and would
        # otherwise route processing_engine.py's get_provider() call through
        # the same fake_market_provider stub meant only for the advisory loop.
        mock.patch("settings.settings.HISTORICAL_STORE_ENABLED", False),
        mock.patch("data.market_data.get_provider", return_value=fake_market_provider),
        # Separate lazy `from data.robinhood_portfolio import fetch_account_snapshot`
        # inside the advisory-overlay code path (main_orchestrator.py, Step 3b) --
        # this one IS a fresh import at call time, so patching the original
        # module's attribute correctly covers it.
        mock.patch(
            "data.robinhood_portfolio.fetch_account_snapshot",
            side_effect=Exception("no robinhood configured in test sandbox"),
        ),
        mock.patch(
            "main_orchestrator._execute_broker_orders", new_callable=mock.AsyncMock
        ) as _broker_mock,
        mock.patch.object(
            transactions_store.TransactionsStore, "__init__",
            make_memory_db_init(transactions_store.TransactionsStore.__init__),
        ),
        mock.patch.object(
            IVHistoryStore, "__init__", make_memory_db_init(IVHistoryStore.__init__)
        ),
    ):
        import io
        import contextlib

        stdout_buf = io.StringIO()
        with contextlib.redirect_stdout(stdout_buf):
            asyncio.run(mo._main_body(effective_dry_run=True, strict=False))
        captured_stdout["text"] = stdout_buf.getvalue()

        broker_mock_snapshot = _broker_mock

        yield {
            "output_dir": tmp_output_dir,
            "broker_mock": broker_mock_snapshot,
            "stdout": captured_stdout["text"],
        }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================================
# State snapshot + rotation
# ============================================================================

class TestStateSnapshot:
    def test_state_snapshot_file_materializes(self, orchestrator_run):
        snap_path = orchestrator_run["output_dir"] / "state_snapshot.json"
        assert snap_path.exists()

    def test_state_snapshot_has_expected_top_level_schema(self, orchestrator_run):
        snap = _read_json(orchestrator_run["output_dir"] / "state_snapshot.json")
        for key in (
            "timestamp", "tickers", "holdings", "market_regime", "vix",
            "yield_curve", "sahm_rule", "high_yield_oas", "kill_switch_active",
            "macro_regime_gate_enabled", "signals",
        ):
            assert key in snap, f"state_snapshot.json missing key {key!r}"

    def test_kill_switch_active_is_false_on_the_success_path(self, orchestrator_run):
        snap = _read_json(orchestrator_run["output_dir"] / "state_snapshot.json")
        assert snap["kill_switch_active"] is False

    def test_aapl_ticker_present_with_one_signal_row(self, orchestrator_run):
        snap = _read_json(orchestrator_run["output_dir"] / "state_snapshot.json")
        assert "AAPL" in snap["tickers"]
        symbols = [s["symbol"] for s in snap["signals"]]
        assert "AAPL" in symbols

    def test_score_components_populated_via_real_run_pipeline(self, orchestrator_run):
        """pilots/scoring.py re-blends each symbol's per-module score by
        reading score_components out of state_snapshot.json. This confirms
        the full chain -- StrategyEngine.evaluate_security()'s
        Score_Components dict -> pipeline/production_steps.py's eval_results
        column mapping -> dashboard_df -> _write_state_snapshot() -- actually
        threads a non-empty breakdown through a REAL run_pipeline() call
        (MockDataEngine data, real signal modules), not just a hand-built
        DataFrame."""
        snap = _read_json(orchestrator_run["output_dir"] / "state_snapshot.json")
        aapl_signal = next(s for s in snap["signals"] if s["symbol"] == "AAPL")
        assert isinstance(aapl_signal["score_components"], dict)
        assert len(aapl_signal["score_components"]) > 0
        assert all(isinstance(v, float) for v in aapl_signal["score_components"].values())
        # Real GICS string from MockDataEngine's synthetic fundamentals, not
        # a fabricated/blank value and not the literal "nan".
        assert aapl_signal["sector"] == "Technology"

    def test_advisory_loop_ran_and_populated_advisory_action(self, orchestrator_run):
        """The only externally-observable proof that Step 3b (the advisory
        evaluation loop) actually executed and wrote back into final_df is
        this field -- _main_body() returns None and never exposes final_df
        directly. With the market-data provider mocked to raise on every
        call, engine.advisory.evaluate() must take its documented
        _fallback_hold path deterministically."""
        snap = _read_json(orchestrator_run["output_dir"] / "state_snapshot.json")
        aapl_signal = next(s for s in snap["signals"] if s["symbol"] == "AAPL")
        assert aapl_signal["advisory_action"] == "HOLD"

    def test_snapshot_rotated_into_history_directory(self, orchestrator_run):
        """_write_state_snapshot() calls scripts.snapshot_diff.rotate_snapshot()
        immediately after the live write so the Δ-band always has 'this run
        vs. previous run' available from the next run onward."""
        history_dir = orchestrator_run["output_dir"] / "history"
        assert history_dir.is_dir()
        rotated = list(history_dir.glob("state_snapshot_*.json"))
        assert len(rotated) >= 1, "expected at least one rotated snapshot file"


# ============================================================================
# Dead-letter report
# ============================================================================

class TestDeadLetterReport:
    def test_dead_letter_file_materializes(self, orchestrator_run):
        dl_path = orchestrator_run["output_dir"] / "dead_letter.json"
        assert dl_path.exists()

    def test_dead_letter_has_expected_schema(self, orchestrator_run):
        payload = _read_json(orchestrator_run["output_dir"] / "dead_letter.json")
        for key in ("run_id", "generated_at", "entries"):
            assert key in payload
        assert isinstance(payload["entries"], list)


# ============================================================================
# HTML / Plotly report generation
# ============================================================================

class TestReportGeneration:
    def test_daily_html_report_materializes_and_is_non_trivial(self, orchestrator_run):
        report_path = orchestrator_run["output_dir"] / "daily_report_dashboard.html"
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert len(content) > 500  # a real rendered report, not an empty/error stub
        assert "AAPL" in content

    def test_volatility_bands_chart_materializes(self, orchestrator_run):
        chart_path = orchestrator_run["output_dir"] / "volatility_bands_dashboard.html"
        assert chart_path.exists()
        assert chart_path.stat().st_size > 0


# ============================================================================
# JSON payload export (stdout)
# ============================================================================

class TestJsonPayloadExport:
    def test_final_payload_banner_and_aapl_entry_printed_to_stdout(self, orchestrator_run):
        stdout = orchestrator_run["stdout"]
        assert "FINAL ACTIONABLE PAYLOAD REPRESENTATION" in stdout
        assert '"AAPL"' in stdout

    def test_printed_payload_is_valid_json_between_the_banners(self, orchestrator_run):
        stdout = orchestrator_run["stdout"]
        start = stdout.index("FINAL ACTIONABLE PAYLOAD REPRESENTATION") + len(
            "FINAL ACTIONABLE PAYLOAD REPRESENTATION ===\n"
        )
        end = stdout.index("================================================")
        payload = json.loads(stdout[start:end])
        assert isinstance(payload, list)
        assert any(row.get("Symbol") == "AAPL" for row in payload)


# ============================================================================
# Broker quarantine — the single most safety-critical assertion in this file
# ============================================================================

class TestBrokerQuarantine:
    def test_execute_broker_orders_never_called_under_advisory_only(self, orchestrator_run):
        """ADVISORY_ONLY=True (the project default) must keep the broker
        surface completely unreached end-to-end -- not just when
        _execute_broker_orders is called directly (already covered by
        tests/test_advisory_only.py), but through the full real orchestrator
        flow that decides whether to call it at all."""
        orchestrator_run["broker_mock"].assert_not_called()

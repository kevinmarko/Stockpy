"""
tests/test_synthetic_data_gate_keyless.py -- the synthetic-data gate covers keyless cycles
==========================================================================================

Plan: ``.claude/synthetic_data_gate_keyless_implementation_plan.md``.

Before this fix ``ctx.context_extras["data_is_synthetic"]`` was set only on
``AsyncDataFetchStep``'s empty-fetch fallback. A daemon with no
``FRED_API_KEY`` (or whose FRED check failed at start) runs on a
``MockDataEngine`` that ``OrchestratorDaemon._build_data_engine`` builds once
and injects, so the flag was never set and a ``primary`` cycle wrote the real
queue, advisory source, watch state, pushes, snapshot, dashboards and the
freshness marker from fabricated data. Now the flag is derived from the
engine's identity (``data_engine.is_synthetic_provider``) and every durable /
outward writer honors it (``pipeline/synthetic_gate.py``).

Numbering follows the plan's section 3.4 (T1-T8, T11, T12). T9/T10 (verify
helper, MCP report tool) live next to their existing tests in
``tests/test_step5_4_verify_cycle.py`` and ``tests/test_investyo_mcp_server.py``.
"""
from __future__ import annotations

import ast
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, List
from unittest import mock

import pandas as pd
import pytest

import data_engine
from data_engine import MockDataEngine
from pipeline import synthetic_gate as sg
from pipeline.context import RunContext
from tests.advisory_frozen_inputs import _BARS, _NOW, _QUEUE_GOLDEN, _SOURCE_GOLDEN
from tests.test_daemon_advisory_shadow_equivalence import _frozen_fetch_all_data_async, _snapshot
from tests.test_daemon_agentic_queue_primary import (  # noqa: F401 (fixture)
    _REAL_FILES,
    _files,
    _frozen_macro,
    daemon_primary_env,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SentinelRealEngine:
    """A non-Mock engine standing in for the real ``DataEngine`` (the
    ``tests/test_live_data_gate.py`` pattern): never asked for data, because
    ``fetch_all_data_async`` is patched."""

    last_macro_raw_fabricated_keys = frozenset()


def _ctx(market: Any = None) -> RunContext:
    ctx = RunContext(
        force_account=False,
        started_at=datetime.now(),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **k: None,
        build_universe_fn=lambda *a, **k: [],
        build_macro_dto_fn=lambda *a, **k: None,
        get_provider_fn=lambda *a, **k: None,
        fetch_bars_fn=lambda *a, **k: {},
        build_context_extras_fn=lambda *a, **k: {},
        advisory_evaluate_fn=lambda *a, **k: None,
    )
    ctx.market = market
    return ctx


def _bars(symbols) -> dict:
    idx = pd.date_range(end=datetime.now(), periods=30)
    return {s: pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
                             "Volume": 1_000_000}, index=idx) for s in symbols}


def _run_fetch(ctx: RunContext, tmp_path: Path, *, empty: bool = False) -> dict:
    """Run the real ``AsyncDataFetchStep`` offline; return what it touched."""
    import main_orchestrator as mo
    from pipeline.production_steps import AsyncDataFetchStep

    seen: dict = {"fetch_engine": None}
    marker = tmp_path / "last_data_refresh.txt"

    async def _fake_fetch(de, symbols):
        seen["fetch_engine"] = de
        if empty:
            return {}, {}, {s: pd.DataFrame() for s in symbols}
        return {}, {}, _bars(symbols)

    with mock.patch("main_orchestrator.fetch_account_snapshot", return_value=mock.MagicMock(positions={})), \
         mock.patch("main_orchestrator.account_snapshot_to_robinhood_positions", return_value={}), \
         mock.patch("main_orchestrator.fetch_all_data_async", side_effect=_fake_fetch), \
         mock.patch("main_orchestrator.GlobalKillSwitch") as m_ks, \
         mock.patch.object(mo, "_DATA_REFRESH_MARKER", str(marker)):
        m_ks.return_value.is_active.return_value = False
        asyncio.run(AsyncDataFetchStep().run(ctx))
    seen["marker_written"] = marker.exists()
    return seen


# ---------------------------------------------------------------------------
# T1-T4, T6: the flag is decided once, in AsyncDataFetchStep
# ---------------------------------------------------------------------------

class TestFlagDerivation:
    def test_t1_keyless_standalone_cycle_is_flagged_and_keeps_its_universe(self, tmp_path):
        # conftest forces live_data_configured() False: the step builds MockDataEngine itself.
        ctx = _ctx(market=None)
        seen = _run_fetch(ctx, tmp_path)
        assert isinstance(ctx.market, MockDataEngine)
        assert ctx.context_extras[sg.SYNTHETIC_FLAG_KEY] is True
        assert ctx.context_extras[sg.SYNTHETIC_REASON_KEY] == sg.REASON_MOCK_ENGINE
        assert ctx.symbols == ["AAPL"]  # Mock mode's pre-5.1 universe, unchanged
        assert seen["marker_written"] is False  # T6: never stamped on synthetic data

    def test_t2_daemon_injected_mock_engine_is_flagged(self, tmp_path):
        # The daemon case: ctx.market preset, so the step takes the `else` branch.
        ctx = _ctx(market=MockDataEngine())
        seen = _run_fetch(ctx, tmp_path)
        assert ctx.context_extras.get(sg.SYNTHETIC_FLAG_KEY) is True
        assert ctx.context_extras[sg.SYNTHETIC_REASON_KEY] == sg.REASON_MOCK_ENGINE
        assert seen["marker_written"] is False

    @pytest.mark.live_data_engine
    @pytest.mark.parametrize("fred_key, check_fails", [("", False), ("abc123", True)],
                             ids=["keyless", "fred-check-failed"])
    def test_t2_engine_built_by_the_daemon_is_flagged(self, tmp_path, monkeypatch, fred_key, check_fails):
        from desktop import daemon_runtime
        from settings import settings

        monkeypatch.setattr(settings, "FRED_API_KEY", fred_key, raising=False)
        if check_fails:
            # The daemon's except branch: a key IS set but building the real
            # engine raises, so it falls back to MockDataEngine (plan C2).
            class _FailingDataEngine:
                def __init__(self, *_a, **_k):
                    raise RuntimeError("FRED check failed")

            monkeypatch.setattr(daemon_runtime, "DataEngine", _FailingDataEngine)
        engine = daemon_runtime.OrchestratorDaemon._build_data_engine(None)
        assert isinstance(engine, MockDataEngine)

        ctx = _ctx(market=engine)
        _run_fetch(ctx, tmp_path)
        assert ctx.context_extras.get(sg.SYNTHETIC_FLAG_KEY) is True

    def test_t3_real_engine_is_not_flagged_and_stamps_the_marker(self, tmp_path):
        engine = _SentinelRealEngine()
        ctx = _ctx(market=engine)
        seen = _run_fetch(ctx, tmp_path)
        assert sg.SYNTHETIC_FLAG_KEY not in ctx.context_extras
        assert sg.SYNTHETIC_REASON_KEY not in ctx.context_extras
        assert ctx.market is engine and seen["fetch_engine"] is engine
        assert seen["marker_written"] is True  # T6 control

    def test_t3_magicmock_engines_stay_real(self):
        assert data_engine.is_synthetic_provider(mock.MagicMock()) is False
        assert data_engine.is_synthetic_provider(None) is False
        assert data_engine.is_synthetic_provider(_SentinelRealEngine()) is False
        assert data_engine.is_synthetic_provider(MockDataEngine()) is True
        assert data_engine.is_synthetic_provider(mock.MagicMock(spec=MockDataEngine)) is True

    def test_t4_empty_fetch_fallback_still_flags_with_its_own_reason(self, tmp_path):
        ctx = _ctx(market=_SentinelRealEngine())
        seen = _run_fetch(ctx, tmp_path, empty=True)
        assert isinstance(ctx.market, MockDataEngine)
        assert ctx.context_extras[sg.SYNTHETIC_FLAG_KEY] is True
        assert ctx.context_extras[sg.SYNTHETIC_REASON_KEY] == sg.REASON_EMPTY_PRICE_FETCH
        assert seen["marker_written"] is False


class TestMarkCycleSynthetic:
    def test_logs_once_and_first_reason_wins(self, caplog):
        ctx = _ctx()
        with caplog.at_level(logging.CRITICAL, logger="ProductionPipeline"):
            sg.mark_cycle_synthetic(ctx, sg.REASON_MOCK_ENGINE)
            sg.mark_cycle_synthetic(ctx, sg.REASON_EMPTY_PRICE_FETCH)
        assert ctx.context_extras[sg.SYNTHETIC_REASON_KEY] == sg.REASON_MOCK_ENGINE
        lines = [r for r in caplog.records if "SYNTHETIC DATA" in r.getMessage()]
        assert len(lines) == 1 and lines[0].levelno == logging.CRITICAL
        assert "FRED_API_KEY" in lines[0].getMessage()

    @pytest.mark.real_synthetic_cycle_alert
    def test_sends_one_deduplicated_critical_alert(self, monkeypatch):
        import observability.alerts as alerts

        sent: List[tuple] = []
        monkeypatch.setattr(alerts, "send_alert",
                            lambda level, message, **kw: sent.append((level, message, kw)))
        ctx = _ctx()
        sg.mark_cycle_synthetic(ctx, sg.REASON_MOCK_ENGINE)
        sg.mark_cycle_synthetic(ctx, sg.REASON_MOCK_ENGINE)
        assert len(sent) == 1
        level, message, kw = sent[0]
        assert level == "CRITICAL"
        assert kw == {"dedup_key": "synthetic_data_cycle"}
        assert "SYNTHETIC data" in message and "FRED_API_KEY" in message

    @pytest.mark.real_synthetic_cycle_alert
    def test_alert_failure_never_raises(self, monkeypatch):
        import observability.alerts as alerts

        def _boom(*a, **k):
            raise RuntimeError("webhook down")

        monkeypatch.setattr(alerts, "send_alert", _boom)
        ctx = _ctx()
        sg.mark_cycle_synthetic(ctx, sg.REASON_EMPTY_PRICE_FETCH)
        assert sg.is_cycle_synthetic(ctx)

    def test_conftest_stub_keeps_tests_from_alerting(self, monkeypatch):
        import observability.alerts as alerts

        called = []
        monkeypatch.setattr(alerts, "send_alert", lambda *a, **k: called.append(a))
        sg.mark_cycle_synthetic(_ctx(), sg.REASON_MOCK_ENGINE)
        assert called == []  # _stub_synthetic_cycle_alert_in_tests

    def test_readers_never_raise_on_odd_contexts(self):
        assert sg.is_cycle_synthetic(None) is False
        assert sg.is_cycle_synthetic(object()) is False
        assert sg.synthetic_reason(_ctx()) is None
        assert sg.describe_reason("nope").startswith("unrecognised reason")


# ---------------------------------------------------------------------------
# T5: StateSnapshotStep writes nothing on a synthetic cycle
# ---------------------------------------------------------------------------

def _snapshot_ctx(synthetic: bool) -> RunContext:
    ctx = _ctx(market=MockDataEngine())
    ctx.symbols = ["AAPL"]
    ctx.tech_raw = _bars(["AAPL"])
    ctx.macro_raw = {"T10Y2Y": 0.5}
    ctx.dashboard_df = pd.DataFrame({
        "Symbol": ["AAPL"], "Price": [10.0], "Action Signal": ["HOLD"], "buyRange": [""],
        "sellRange": [""], "Kelly Target": [0.0], "GARCH_Vol": [0.2],
    })
    if synthetic:
        sg.mark_cycle_synthetic(ctx, sg.REASON_MOCK_ENGINE)
    return ctx


class TestStateSnapshotStep:
    def test_t5_synthetic_cycle_writes_no_snapshot_dashboards_or_payload(self, tmp_path, monkeypatch, capsys, caplog):
        from pipeline.production_steps import StateSnapshotStep
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        before = {
            name: f"REAL {name}".encode()
            for name in ("state_snapshot.json", "daily_report_dashboard.html",
                         "volatility_bands_dashboard.html")
        }
        for name, data in before.items():
            (tmp_path / name).write_bytes(data)
        (tmp_path / "history").mkdir()

        writer = mock.MagicMock(name="_write_state_snapshot")
        report = mock.MagicMock(name="generate_html_report")
        plotly = mock.MagicMock(name="generate_plotly_volatility_bands")
        with mock.patch("main_orchestrator._write_state_snapshot", writer), \
             mock.patch("main_orchestrator.generate_html_report", report), \
             mock.patch("main_orchestrator.generate_plotly_volatility_bands", plotly), \
             caplog.at_level(logging.CRITICAL):
            StateSnapshotStep().run(_snapshot_ctx(synthetic=True))

        writer.assert_not_called()
        report.assert_not_called()
        plotly.assert_not_called()
        assert {n: (tmp_path / n).read_bytes() for n in before} == before
        assert list((tmp_path / "history").iterdir()) == []
        assert "FINAL ACTIONABLE PAYLOAD" not in capsys.readouterr().out
        assert any("skipping the state snapshot" in r.getMessage() for r in caplog.records)

    def test_t5_control_real_cycle_writes(self, tmp_path, monkeypatch, capsys):
        from pipeline.production_steps import StateSnapshotStep
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        writer = mock.MagicMock(name="_write_state_snapshot")
        report = mock.MagicMock(name="generate_html_report")
        plotly = mock.MagicMock(name="generate_plotly_volatility_bands")
        with mock.patch("main_orchestrator._write_state_snapshot", writer), \
             mock.patch("main_orchestrator.generate_html_report", report), \
             mock.patch("main_orchestrator.generate_plotly_volatility_bands", plotly):
            StateSnapshotStep().run(_snapshot_ctx(synthetic=False))

        writer.assert_called_once()
        report.assert_called_once()
        plotly.assert_called_once()
        assert "FINAL ACTIONABLE PAYLOAD" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# T7: a whole cycle on the Mock engine in shadow/primary writes nothing;
#     the same cycle on a real engine is unchanged.
# ---------------------------------------------------------------------------

def _run_full_cycle(monkeypatch, out: Path, engine: Any, *, queue_mode: str) -> dict:
    """``main_orchestrator._main_body`` exactly as the daemon calls it
    (``data_engine=`` injected), on the frozen advisory inputs. Only
    ``RunPipelineStep`` (the GARCH/forecast/signal compute) is replaced by a
    minimal dashboard, and the side-effect sinks are recorders."""
    import main_orchestrator as mo
    from pipeline.production_steps import AgenticQueueStep, RunPipelineStep
    from reporting.progress import read_progress
    from settings import settings

    monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", queue_mode, raising=False)
    monkeypatch.setattr(settings, "ADVISORY_ONLY", False, raising=False)  # broker step would trade
    monkeypatch.setattr(mo, "fetch_account_snapshot", lambda *a, **kw: _snapshot())
    monkeypatch.setattr(mo, "fetch_all_data_async", _frozen_fetch_all_data_async)
    monkeypatch.setattr(mo, "_DATA_REFRESH_MARKER", str(out / "last_data_refresh.txt"))
    ks = mock.MagicMock()
    ks.return_value.is_active.return_value = False
    monkeypatch.setattr(mo, "GlobalKillSwitch", ks)

    macro = _frozen_macro()

    def _fake_run_pipeline(self, ctx):
        n = len(ctx.symbols)
        ctx.dashboard_df = pd.DataFrame({
            "Symbol": list(ctx.symbols),
            "Price": [float(_BARS[s]["Close"].iloc[-1]) if s in _BARS else float("nan")
                      for s in ctx.symbols],
            "Action Signal": ["HOLD"] * n, "buyRange": [""] * n, "sellRange": [""] * n,
            "Kelly Target": [0.0] * n, "GARCH_Vol": [float("nan")] * n,
        })
        ctx.macro_dto = macro

    monkeypatch.setattr(RunPipelineStep, "run", _fake_run_pipeline)
    # The frozen clock the goldens were written with (_main_body builds the
    # step with no clock argument).
    monkeypatch.setattr(AgenticQueueStep, "_now", lambda self: _NOW)
    broker = mock.AsyncMock(name="_execute_broker_orders")
    monkeypatch.setattr(mo, "_execute_broker_orders", broker)
    writer = mock.MagicMock(name="_write_state_snapshot")
    report = mock.MagicMock(name="generate_html_report")
    plotly = mock.MagicMock(name="generate_plotly_volatility_bands")
    monkeypatch.setattr(mo, "_write_state_snapshot", writer)
    monkeypatch.setattr(mo, "generate_html_report", report)
    monkeypatch.setattr(mo, "generate_plotly_volatility_bands", plotly)
    summaries: List[tuple] = []
    monkeypatch.setattr("observability.alerts.send_daily_summary",
                        lambda pnl, warnings: summaries.append((pnl, list(warnings))))

    before = _files(out)
    asyncio.run(mo._main_body(False, data_engine=engine, mode="full", force=True))
    progress = read_progress(out)
    return {
        "before": before,
        "after": _files(out),
        "broker": broker,
        "writer": writer,
        "report": report,
        "plotly": plotly,
        "summaries": summaries,
        "progress_message": progress.message if progress else None,
        "progress_state": progress.state if progress else None,
    }


class TestFullCycleOnMockEngine:
    @pytest.mark.parametrize("queue_mode", ["primary", "shadow"])
    def test_t7_mock_engine_cycle_writes_no_queue_and_places_no_orders(
        self, daemon_primary_env, monkeypatch, queue_mode,  # noqa: F811
    ):
        out, pushes = daemon_primary_env
        r = _run_full_cycle(monkeypatch, out, MockDataEngine(), queue_mode=queue_mode)

        # No queue, advisory source, notified-intents or watch state change.
        assert r["after"] == r["before"]
        assert not (out / "queue_sources").exists()
        assert not (out / "execution_queue.json").exists()
        assert not (out / "shadow").exists()
        assert pushes == []  # no summary / watch / new-intent push
        r["broker"].assert_not_called()  # no paper order, even with ADVISORY_ONLY=False
        r["writer"].assert_not_called()  # no state_snapshot.json / history/
        r["report"].assert_not_called()
        r["plotly"].assert_not_called()
        assert not (out / "last_data_refresh.txt").exists()
        # Q4: still "succeeded", with the reason in the run's progress message.
        assert r["progress_state"] == "succeeded"
        assert r["progress_message"].startswith(sg.SYNTHETIC_PROGRESS_MESSAGE_PREFIX)
        assert "MockDataEngine" in r["progress_message"]
        # T8 (end to end): the daily summary carries the warning.
        assert len(r["summaries"]) == 1
        assert any(w.startswith("SYNTHETIC DATA") for w in r["summaries"][0][1])

    def test_t7_real_engine_cycle_is_unchanged(self, daemon_primary_env, monkeypatch):  # noqa: F811
        out, pushes = daemon_primary_env
        r = _run_full_cycle(monkeypatch, out, mock.MagicMock(name="real_engine"), queue_mode="primary")

        files = r["after"]
        assert files["queue_sources/advisory.json"] == _SOURCE_GOLDEN.read_bytes()
        assert files["execution_queue.json"] == _QUEUE_GOLDEN.read_bytes()
        assert files["watch_state.json"] != r["before"]["watch_state.json"]
        assert any(p[0] == "InvestYo ✓ Refresh Complete" for p in pushes)
        r["broker"].assert_awaited_once()
        r["writer"].assert_called_once()
        r["report"].assert_called_once()
        assert (out / "last_data_refresh.txt").exists()
        assert r["progress_state"] == "succeeded"
        assert not (r["progress_message"] or "").startswith(sg.SYNTHETIC_PROGRESS_MESSAGE_PREFIX)
        assert not any(w.startswith("SYNTHETIC DATA") for w in r["summaries"][0][1])

    def test_real_files_list_is_what_primary_writes(self):
        # Guard on the assertion above: _REAL_FILES must name the queue files.
        assert "execution_queue.json" in _REAL_FILES
        assert "queue_sources/advisory.json" in _REAL_FILES


# ---------------------------------------------------------------------------
# T8: the daily summary
# ---------------------------------------------------------------------------

class TestDailySummary:
    def test_t8_warning_only_when_flagged(self):
        import main_orchestrator as mo

        flagged = _ctx()
        sg.mark_cycle_synthetic(flagged, sg.REASON_EMPTY_PRICE_FETCH)
        flagged.dashboard_df = pd.DataFrame({"Symbol": ["AAPL"]})
        _, warnings = mo._build_daily_summary(flagged)
        assert [w for w in warnings if w.startswith("SYNTHETIC DATA")]
        assert "market-data outage" in " ".join(warnings)

        clean = _ctx()
        clean.dashboard_df = pd.DataFrame({"Symbol": ["AAPL"]})
        _, warnings = mo._build_daily_summary(clean)
        assert not [w for w in warnings if "SYNTHETIC" in w]


# ---------------------------------------------------------------------------
# Daemon start-up line
# ---------------------------------------------------------------------------

class TestDaemonStartupLog:
    @pytest.mark.parametrize("queue_mode, engine_factory, expect_error", [
        ("primary", MockDataEngine, True),
        ("shadow", MockDataEngine, True),
        ("off", MockDataEngine, False),
        ("primary", _SentinelRealEngine, False),
    ])
    def test_error_when_queue_mode_is_on_and_engine_is_synthetic(
        self, monkeypatch, caplog, queue_mode, engine_factory, expect_error,
    ):
        import main_orchestrator
        from desktop import daemon_runtime
        from settings import settings

        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", queue_mode, raising=False)
        monkeypatch.setattr(daemon_runtime.OrchestratorDaemon, "_build_data_engine",
                            lambda self: engine_factory())
        monkeypatch.setattr(main_orchestrator.EngineContext, "build",
                            classmethod(lambda cls, **kw: mock.MagicMock()))
        d = daemon_runtime.OrchestratorDaemon()
        with caplog.at_level(logging.ERROR):
            d.start()
        try:
            hits = [r for r in caplog.records
                    if r.levelno == logging.ERROR and "synthetic" in r.getMessage()]
            assert bool(hits) is expect_error
        finally:
            d.shutdown(timeout=2.0)


# ---------------------------------------------------------------------------
# T11: every data provider is classified real or synthetic
# ---------------------------------------------------------------------------

_PROVIDER_BASES = {"IDataProvider", "DataEngine", "MockDataEngine"}
#: Production data providers and how ``is_synthetic_provider`` must treat them.
_CLASSIFIED = {
    ("data_engine.py", "DataEngine"): "real",
    ("data_engine.py", "MockDataEngine"): "synthetic",
    # Audit-only launcher with its OWN local IDataProvider; not importable
    # (space in the filename) and never handed to the pipeline.
    ("Gravity AI Review Suite.py", "GravityTestEngine"): "audit_only",
}
_SKIP_DIRS = {".venv", "venv", "legacy", "tests", "node_modules", ".git", ".claude", ".agents",
              "worktrees", "webapp", "__pycache__", "site-packages"}


def _provider_classes() -> set:
    found = set()
    for path in _REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(_REPO_ROOT)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)
                if name in _PROVIDER_BASES:
                    found.add((rel.as_posix(), node.name))
    return found


class TestProviderClassification:
    def test_t11_every_data_provider_is_classified(self):
        unclassified = _provider_classes() - set(_CLASSIFIED)
        assert not unclassified, (
            f"New data provider(s) {sorted(unclassified)}: decide whether they serve "
            "synthetic data, update data_engine.is_synthetic_provider if so, and "
            "classify them in _CLASSIFIED."
        )

    def test_t11_classification_matches_is_synthetic_provider(self):
        assert data_engine.is_synthetic_provider(MockDataEngine()) is True
        assert data_engine.is_synthetic_provider(object.__new__(data_engine.DataEngine)) is False

        class _Sub(MockDataEngine):
            pass

        assert data_engine.is_synthetic_provider(_Sub()) is True


# ---------------------------------------------------------------------------
# T12: the gate is never switched off suite-wide
# ---------------------------------------------------------------------------

class TestNoAutouseOptOut:
    def test_t12_no_autouse_fixture_disables_the_gate(self):
        offenders = []
        for path in [_REPO_ROOT / "conftest.py", *(_REPO_ROOT / "tests").rglob("conftest.py")]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                autouse = any(
                    isinstance(dec, ast.Call)
                    and any(kw.arg == "autouse" and getattr(kw.value, "value", False) is True
                            for kw in dec.keywords)
                    for dec in node.decorator_list
                )
                if not autouse:
                    continue
                body = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
                if "is_synthetic_provider" in body:
                    offenders.append(f"{path.name}::{node.name}")
                # The marker-keyed fixture may only apply the opt-in under its marker.
                if "allow_synthetic_outputs" in body and "synthetic_outputs_allowed" not in body:
                    offenders.append(f"{path.name}::{node.name}")
        assert not offenders, offenders

    @pytest.mark.synthetic_outputs_allowed
    def test_marker_opt_in_treats_a_mock_engine_as_real(self):
        assert data_engine.is_synthetic_provider(MockDataEngine()) is False

    def test_without_the_marker_the_gate_is_on(self):
        assert data_engine.is_synthetic_provider(MockDataEngine()) is True

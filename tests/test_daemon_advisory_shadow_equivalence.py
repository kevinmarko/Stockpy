"""
tests/test_daemon_advisory_shadow_equivalence.py -- step 5.2 gate (i) and the
shadow queue's safety properties
==========================================================================

Step 5.2 of ``.claude/shrink_step5_retire_main_py_implementation_plan.md``
splits the daemon's advisory overlay into ``AdvisoryOverlayStep`` and adds
``AgenticQueueStep`` behind ``settings.DAEMON_AGENTIC_QUEUE_MODE``.

Gate (i): with frozen inputs, ``ADVISORY_REUSE_PIPELINE_COMPUTE`` off and the
same universe and macro, the daemon's shadow ``queue_sources/advisory.json``
and ``execution_queue.json`` must equal what ``main.run_once()`` + ``_run_cycle``
write, byte for byte, and every per-symbol ``Recommendation`` field must match.

How the daemon side is driven:

* ``AsyncDataFetchStep`` runs for real, so the universe and the account
  snapshot come from the daemon's own step. Only its account fetch, bulk data
  fetch and freshness marker are stubbed with the frozen fixtures.
* ``RunPipelineStep`` is NOT run: it would fit the full forecasting ensemble.
  Its outputs are stood in for: ``ctx.macro_dto`` is main.py's macro DTO (the
  gate says "same macro"), and ``ctx.dashboard_df`` holds the universe symbols.
  With reuse off, ``AdvisoryOverlayStep`` reads nothing else from it.
* ``AdvisoryOverlayStep`` and ``AgenticQueueStep`` run through the real
  ``AsyncPipelineRunner``, so they take the same ``to_thread`` +
  ``PIPELINE_STEP_TIMEOUT_SECONDS`` path they take in the daemon.

The frozen inputs are the ones ``tests/test_run_once_advisory_golden.py``
uses (fake provider with seeded bars, fake forecasting engine, fake macro
engines, in-memory trade history, network blocked, ``ADVISORY_MAX_CONCURRENCY=1``,
a fixed ``now``).
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pandas as pd
import pytest

from tests.test_run_once_advisory_golden import (
    _BARS,
    _NOW,
    _QUEUE_GOLDEN,
    _REC_GOLDEN,
    _SOURCE_GOLDEN,
    _assert_recommendations_match,
    _install_frozen_inputs,
    _recommendation_bytes,
    _snapshot,
)


def _make_daemon_ctx():
    from pipeline.context import RunContext

    # Same construction as main_orchestrator._main_body_impl.
    ctx = RunContext(
        force_account=False,
        started_at=datetime.now(),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **kw: None,
        build_universe_fn=lambda *a: [],
        build_macro_dto_fn=lambda: None,
        get_provider_fn=lambda: None,
        fetch_bars_fn=lambda *a: {},
        build_context_extras_fn=lambda *a: {},
        advisory_evaluate_fn=lambda *a, **kw: None,
    )
    # A preset data engine makes AsyncDataFetchStep use the built universe as
    # is (the live-data branch), without constructing a DataEngine.
    ctx.market = mock.MagicMock(name="frozen_data_engine")
    return ctx


async def _frozen_fetch_all_data_async(de, symbols):
    _ = de
    return {}, {}, {s: _BARS[s].copy() for s in symbols if s in _BARS}


def _run_daemon_fetch(ctx, monkeypatch) -> None:
    import main_orchestrator
    from pipeline.production_steps import AsyncDataFetchStep

    monkeypatch.setattr(main_orchestrator, "fetch_account_snapshot", lambda *a, **kw: _snapshot())
    monkeypatch.setattr(main_orchestrator, "fetch_all_data_async", _frozen_fetch_all_data_async)
    monkeypatch.setattr(main_orchestrator, "_mark_data_refreshed", lambda: None)
    asyncio.run(AsyncDataFetchStep().run(ctx))


def _run_advisory_and_queue(ctx) -> None:
    from pipeline.production_steps import AdvisoryOverlayStep, AgenticQueueStep
    from pipeline.runner import AsyncPipelineRunner

    asyncio.run(AsyncPipelineRunner([
        AdvisoryOverlayStep(),
        AgenticQueueStep(clock=lambda: _NOW),
    ]).run(ctx))


def _run_main(out: Path):
    """main.run_once() then _run_cycle's queue block, into the REAL OUTPUT_DIR."""
    import main
    from execution.compose import compose_and_emit, write_advisory_source

    try:
        result = main.run_once()
    finally:
        main._reset_macro_engine_cache()
    assert not result.errors, result.errors
    write_advisory_source(result.recommendations, output_dir=out, now=_NOW)
    assert compose_and_emit(result.snapshot, output_dir=out, now=_NOW,
                            macro_dto=result.macro_dto) is not None
    return result


def _real_queue_files(out: Path) -> Dict[str, bytes]:
    files = {}
    for rel in ("queue_sources/advisory.json", "execution_queue.json",
                "execution_queue_notified.json", "risk_gate_blocks.jsonl"):
        p = out / rel
        files[rel] = p.read_bytes() if p.exists() else b"<absent>"
    return files


class TestGateIDaemonShadowEqualsMainPy:
    def test_shadow_queue_and_recommendations_equal_main_run_once(self, tmp_path, monkeypatch):
        from settings import settings

        out = _install_frozen_inputs(tmp_path, monkeypatch)
        monkeypatch.setattr(settings, "ADVISORY_REUSE_PIPELINE_COMPUTE", False, raising=False)
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "shadow", raising=False)
        pushes: List[Any] = []
        monkeypatch.setattr("alerting.notify", lambda *a, **kw: pushes.append((a, kw)))

        main_result = _run_main(out)
        pushes_after_main = len(pushes)
        real_before = _real_queue_files(out)

        ctx = _make_daemon_ctx()
        _run_daemon_fetch(ctx, monkeypatch)
        # Same universe: the daemon's own AsyncDataFetchStep built it.
        assert ctx.symbols == [r.symbol for r in main_result.recommendations]
        assert ctx.snapshot is not None and ctx.snapshot.total_equity == 100_000.0
        # Stand-ins for RunPipelineStep's outputs (see the module docstring).
        ctx.macro_dto = main_result.macro_dto
        ctx.dashboard_df = pd.DataFrame({
            "Symbol": list(ctx.symbols),
            "Price": [float(_BARS[s]["Close"].iloc[-1]) for s in ctx.symbols],
        })
        _run_advisory_and_queue(ctx)

        assert ctx.context_extras.get("advisory_overlay_ok") is True
        assert not ctx.errors, ctx.errors
        # Per-symbol Recommendation fields (action, conviction, sizing, exit,
        # rationale, the full key_indicators dict). Both sides ran in this
        # process, so even the GJR-GARCH-derived keys must match exactly; the
        # tolerance only applies to the cross-platform committed golden.
        assert _recommendation_bytes(ctx.recommendations) == \
            _recommendation_bytes(main_result.recommendations)
        _assert_recommendations_match(
            _recommendation_bytes(ctx.recommendations), _REC_GOLDEN.read_bytes(),
        )
        # The shadow files equal main.py's real ones byte for byte...
        shadow = out / "shadow"
        assert (shadow / "queue_sources" / "advisory.json").read_bytes() == \
            (out / "queue_sources" / "advisory.json").read_bytes()
        assert (shadow / "execution_queue.json").read_bytes() == \
            (out / "execution_queue.json").read_bytes()
        # ...and the committed run_once golden.
        assert (shadow / "queue_sources" / "advisory.json").read_bytes() == _SOURCE_GOLDEN.read_bytes()
        assert (shadow / "execution_queue.json").read_bytes() == _QUEUE_GOLDEN.read_bytes()
        # The Advisory_* dashboard columns carry the same recommendations.
        by_symbol = {r.symbol: r for r in main_result.recommendations}
        for _, row in ctx.dashboard_df.iterrows():
            rec = by_symbol[row["Symbol"]]
            assert row["Advisory_Action"] == rec.action
            assert row["Advisory_Conviction"] == round(rec.conviction, 4)
            assert row["Advisory_Position_Pct"] == round(rec.suggested_position_pct, 6)
            assert row["Advisory_Rationale"] == rec.rationale

        # The shadow run touched none of the real queue files and sent no push.
        assert _real_queue_files(out) == real_before
        assert len(pushes) == pushes_after_main
        assert pushes_after_main >= 1, "main.py's compose should have pushed (fixture sanity)"
        assert not (shadow / "execution_queue_notified.json").exists()
        assert not (shadow / "risk_gate_blocks.jsonl").exists()

        # The gate-(iii) diff script, run on these files, sees no difference.
        from scripts import compare_shadow_queue as csq

        comparison = csq.compare(out)
        assert comparison.ok and comparison.shadow_origin.startswith("history/")
        assert comparison.n_differences == 0
        assert comparison.intents, "the comparison must cover real intents"


# ---------------------------------------------------------------------------
# AgenticQueueStep behaviour (no main.py run needed)
# ---------------------------------------------------------------------------

def _rec(symbol: str, action: str = "BUY", conviction: float = 0.9, pct: float = 0.05):
    from engine.advisory import Recommendation

    return Recommendation(
        symbol=symbol, action=action, conviction=conviction,
        suggested_position_pct=pct, suggested_exit_pct=0.0,
        rationale=f"{symbol} test", key_indicators={}, data_quality="OK", forecast=None,
        strategy="test",
    )


def _queue_ctx(recs=None, *, ok=True, synthetic=False, stopped=False):
    ctx = _make_daemon_ctx()
    ctx.recommendations = list(recs if recs is not None else [_rec("AAPL")])
    ctx.snapshot = _snapshot()
    if ok:
        ctx.context_extras["advisory_overlay_ok"] = True
    if synthetic:
        ctx.context_extras["data_is_synthetic"] = True
    if stopped:
        ctx.stopped = True
        ctx.stop_reason = "kill_switch"
    return ctx


@pytest.fixture
def queue_env(tmp_path, monkeypatch):
    """A tmp OUTPUT_DIR, network off, notify recorded, kill switch clear."""
    import socket

    import execution.kill_switch as ks
    from settings import settings

    def _no_network(*_a, **_kw):
        raise OSError("network disabled")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(settings, "OUTPUT_DIR", out, raising=False)
    monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0, raising=False)
    monkeypatch.setattr(ks, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    monkeypatch.setattr(ks, "SOFT_HALT_FILE", tmp_path / "SOFT_HALT")
    pushes: List[Any] = []
    monkeypatch.setattr("alerting.notify", lambda *a, **kw: pushes.append(a))
    return out, pushes


def _write(step, ctx, out, mode="shadow", execution_mode="review"):
    return step.write_queue(ctx, mode=mode, execution_mode=execution_mode, output_dir=out)


class TestAgenticQueueStepModes:
    def test_off_writes_nothing(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        assert _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out, mode="off") is None
        assert not (out / "shadow").exists()
        assert not (out / "execution_queue.json").exists()

    def test_unknown_mode_is_off(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        assert _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out, mode="bogus") is None
        assert not (out / "shadow").exists()

    def test_shadow_writes_only_under_shadow_and_sends_no_push(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep

        out, pushes = queue_env
        path = _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out)
        assert path == out / "shadow" / "execution_queue.json"
        assert (out / "shadow" / "queue_sources" / "advisory.json").exists()
        assert not (out / "execution_queue.json").exists()
        assert not (out / "queue_sources").exists()
        assert pushes == []
        assert not (out / "shadow" / "execution_queue_notified.json").exists()
        payload = json.loads(path.read_text())
        assert payload["mode"] == "review" and payload["n_intents"] == 1

    def test_primary_behaves_like_shadow_and_warns(self, queue_env, caplog):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        (out / "execution_queue.json").write_text("REAL QUEUE", encoding="utf-8")
        with caplog.at_level("WARNING"):
            path = _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out, mode="primary")
        assert path == out / "shadow" / "execution_queue.json"
        assert (out / "execution_queue.json").read_text(encoding="utf-8") == "REAL QUEUE"
        assert "not implemented until step 5.3" in caplog.text

    def test_execution_mode_off_writes_source_but_no_queue(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        assert _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out,
                      execution_mode="off") is None
        assert (out / "shadow" / "queue_sources" / "advisory.json").exists()
        assert not (out / "shadow" / "execution_queue.json").exists()

    @pytest.mark.parametrize("kwargs, needle", [
        ({"synthetic": True}, "synthetic"),
        ({"ok": False}, "advisory overlay did not complete"),
        ({"recs": []}, "no recommendations"),
        ({"stopped": True}, "cycle stopped"),
    ])
    def test_skip_reasons_write_nothing_and_are_logged(self, queue_env, caplog, kwargs, needle):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        with caplog.at_level("INFO"):
            assert _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(**kwargs), out) is None
        assert needle in caplog.text
        assert not (out / "shadow" / "execution_queue.json").exists()
        assert not (out / "shadow" / "queue_sources").exists()


class TestAgenticQueueStepRun:
    def test_run_captures_settings_and_passes_them_explicitly(self, queue_env, monkeypatch):
        from pipeline.production_steps import AgenticQueueStep
        from settings import settings

        out, _ = queue_env
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "shadow", raising=False)
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        step = AgenticQueueStep(clock=lambda: _NOW)
        with mock.patch.object(step, "write_queue", wraps=step.write_queue) as spy:
            step.run(_queue_ctx())
        spy.assert_called_once()
        assert spy.call_args.kwargs == {
            "mode": "shadow", "execution_mode": "review", "output_dir": out,
        }
        assert (out / "shadow" / "execution_queue.json").exists()

    def test_default_setting_is_off(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep
        from settings import Settings

        out, _ = queue_env
        assert Settings.model_fields["DAEMON_AGENTIC_QUEUE_MODE"].default == "off"
        # settings.DAEMON_AGENTIC_QUEUE_MODE is whatever the test env loaded;
        # force the default explicitly.
        with mock.patch("settings.settings.DAEMON_AGENTIC_QUEUE_MODE", "off"):
            AgenticQueueStep(clock=lambda: _NOW).run(_queue_ctx())
        assert not (out / "shadow").exists()

    @pytest.mark.parametrize("raw, expected", [
        ("off", "off"), ("SHADOW", "shadow"), (" primary ", "primary"),
        ("live", "off"), ("", "off"),
    ])
    def test_settings_validator_collapses_unknown_to_off(self, raw, expected):
        from settings import Settings

        assert Settings(DAEMON_AGENTIC_QUEUE_MODE=raw).DAEMON_AGENTIC_QUEUE_MODE == expected

    def test_run_never_raises(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep

        step = AgenticQueueStep(clock=lambda: _NOW)
        with mock.patch.object(step, "write_queue", side_effect=RuntimeError("boom")), \
             mock.patch("settings.settings.DAEMON_AGENTIC_QUEUE_MODE", "shadow"):
            step.run(_queue_ctx())  # must not raise


class TestShadowCannotClobberTheRealQueue:
    """In no configuration may a shadow write land on the real queue files."""

    def test_shadow_dir_symlinked_to_output_dir_is_refused(self, queue_env, caplog):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        (out / "execution_queue.json").write_text("REAL QUEUE", encoding="utf-8")
        (out / "queue_sources").mkdir()
        (out / "queue_sources" / "advisory.json").write_text("REAL SOURCE", encoding="utf-8")
        os.symlink(out, out / "shadow", target_is_directory=True)
        with caplog.at_level("ERROR"):
            for mode in ("shadow", "primary"):
                assert _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out, mode=mode) is None
        assert "refused" in caplog.text
        assert (out / "execution_queue.json").read_text(encoding="utf-8") == "REAL QUEUE"
        assert (out / "queue_sources" / "advisory.json").read_text(encoding="utf-8") == "REAL SOURCE"

    def test_shadow_queue_sources_symlinked_to_real_is_refused(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        (out / "queue_sources").mkdir()
        (out / "queue_sources" / "advisory.json").write_text("REAL SOURCE", encoding="utf-8")
        (out / "shadow").mkdir()
        os.symlink(out / "queue_sources", out / "shadow" / "queue_sources", target_is_directory=True)
        assert _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out) is None
        assert (out / "queue_sources" / "advisory.json").read_text(encoding="utf-8") == "REAL SOURCE"

    def test_shadow_queue_file_hardlinked_to_real_is_refused(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        (out / "execution_queue.json").write_text("REAL QUEUE", encoding="utf-8")
        (out / "shadow").mkdir()
        os.link(out / "execution_queue.json", out / "shadow" / "execution_queue.json")
        assert _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out) is None
        assert (out / "execution_queue.json").read_text(encoding="utf-8") == "REAL QUEUE"

    def test_normal_layout_leaves_existing_real_files_untouched(self, queue_env):
        from pipeline.production_steps import AgenticQueueStep

        out, _ = queue_env
        (out / "execution_queue.json").write_text("REAL QUEUE", encoding="utf-8")
        (out / "queue_sources").mkdir()
        (out / "queue_sources" / "advisory.json").write_text("REAL SOURCE", encoding="utf-8")
        assert _write(AgenticQueueStep(clock=lambda: _NOW), _queue_ctx(), out) is not None
        assert (out / "execution_queue.json").read_text(encoding="utf-8") == "REAL QUEUE"
        assert (out / "queue_sources" / "advisory.json").read_text(encoding="utf-8") == "REAL SOURCE"

    def test_shadow_output_dir_is_a_strict_subdirectory(self, tmp_path):
        from pipeline.production_steps import shadow_collision_reason, shadow_output_dir

        for base in (tmp_path, tmp_path / "a" / "b", Path("relative/out")):
            shadow = shadow_output_dir(base)
            assert shadow.parent == Path(base) and shadow != Path(base)
        assert shadow_collision_reason(tmp_path, shadow_output_dir(tmp_path)) is None


class TestShadowRiskGateHasNoSideEffects:
    def test_side_effects_false_skips_alerts_and_block_log(self, tmp_path, monkeypatch):
        from execution.risk_gate import PreTradeRiskGate, RiskCheckResult, RiskContext
        from settings import settings

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
        sent: List[Any] = []
        monkeypatch.setattr("observability.alerts.send_alert", lambda *a, **kw: sent.append(a))
        gate = PreTradeRiskGate(side_effects=False)
        intent = mock.MagicMock(symbol="AAPL", qty=1.0, strategy_id="s")
        intent.side.value = "buy"
        gate._alert_halt("HARD_HALT", "r", "AAPL")
        gate._alert_portfolio_heat(0.5, "AAPL")
        gate._alert_correlation_concentration("AAPL", "MSFT", 0.95)
        gate._append_block_log(RiskCheckResult("x", False, "r"), intent, RiskContext())
        assert sent == []
        assert not (tmp_path / "risk_gate_blocks.jsonl").exists()

        loud = PreTradeRiskGate()
        assert loud.side_effects is True
        loud._append_block_log(RiskCheckResult("x", False, "r"), intent, RiskContext())
        assert (tmp_path / "risk_gate_blocks.jsonl").exists()


# ---------------------------------------------------------------------------
# AdvisoryOverlayStep behaviour
# ---------------------------------------------------------------------------

def _overlay_ctx(symbols=("AAPL", "MSFT")):
    ctx = _make_daemon_ctx()
    ctx.symbols = list(symbols)
    ctx.snapshot = _snapshot()
    ctx.dashboard_df = pd.DataFrame({
        "Symbol": list(symbols),
        "GARCH_Vol": [0.31] * len(symbols),
        "Forecast_30": [123.0] * len(symbols),
        "Forecast_30_Is_Fallback": [False] * len(symbols),
    })
    return ctx


@pytest.fixture
def overlay_env(monkeypatch):
    calls: Dict[str, Any] = {"evaluate": []}

    def _fake_evaluate(**kw):
        calls["evaluate"].append(kw)
        if kw["symbol"] == "BAD":
            raise RuntimeError("bad symbol")
        return _rec(kw["symbol"], conviction=0.71234567, pct=0.0123456789)

    def _fake_bars(symbols, market):
        calls["bars"] = list(symbols)
        return {}

    def _fake_extras(symbols, bars, macro, market):
        calls["extras"] = list(symbols)
        return {"xsec_percentile_ranks": {"AAPL": 0.9}}

    monkeypatch.setattr("engine.advisory.evaluate", _fake_evaluate)
    monkeypatch.setattr("pipeline.advisory_inputs.fetch_bars_for_universe", _fake_bars)
    monkeypatch.setattr("pipeline.advisory_inputs.build_context_extras", _fake_extras)
    monkeypatch.setattr("data.market_data.get_provider", lambda: "provider")
    return calls


class TestAdvisoryOverlayStep:
    def test_fills_recommendations_and_columns_from_main_py_inputs(self, overlay_env, monkeypatch):
        from pipeline.production_steps import AdvisoryOverlayStep

        monkeypatch.setattr("settings.settings.ADVISORY_REUSE_PIPELINE_COMPUTE", False)
        ctx = _overlay_ctx()
        AdvisoryOverlayStep().run(ctx)
        assert [r.symbol for r in ctx.recommendations] == ["AAPL", "MSFT"]
        assert overlay_env["bars"] == overlay_env["extras"] == ["AAPL", "MSFT"]
        for kw in overlay_env["evaluate"]:
            assert kw["context_extras"] == {"xsec_percentile_ranks": {"AAPL": 0.9}}
            assert kw["snapshot"] is ctx.snapshot
            assert kw["market"] == "provider"
            assert kw["precomputed_garch"] is None and kw["precomputed_forecast"] is None
        assert list(ctx.dashboard_df["Advisory_Conviction"]) == [0.7123, 0.7123]
        assert list(ctx.dashboard_df["Advisory_Position_Pct"]) == [0.012346, 0.012346]
        assert ctx.context_extras["advisory_overlay_ok"] is True

    def test_reuse_on_threads_the_dashboard_values(self, overlay_env, monkeypatch):
        from pipeline.production_steps import AdvisoryOverlayStep

        monkeypatch.setattr("settings.settings.ADVISORY_REUSE_PIPELINE_COMPUTE", True)
        AdvisoryOverlayStep().run(_overlay_ctx())
        for kw in overlay_env["evaluate"]:
            assert kw["precomputed_garch"] == pytest.approx(0.31)
            assert kw["precomputed_forecast"] == pytest.approx(123.0)
            assert kw["precomputed_forecast_is_fallback"] is False

    def test_per_symbol_failure_is_dead_lettered(self, overlay_env):
        from pipeline.production_steps import AdvisoryOverlayStep

        ctx = _overlay_ctx(("AAPL", "BAD", "MSFT"))
        AdvisoryOverlayStep().run(ctx)
        assert [r.symbol for r in ctx.recommendations] == ["AAPL", "MSFT"]
        assert [e["symbol"] for e in ctx.errors] == ["BAD"]
        row = ctx.dashboard_df.set_index("Symbol").loc["BAD"]
        assert row["Advisory_Action"] == "" and row["Advisory_Conviction"] == 0.0
        assert ctx.context_extras["advisory_overlay_ok"] is True

    def test_missing_snapshot_uses_main_py_empty_account(self, overlay_env):
        from pipeline.production_steps import AdvisoryOverlayStep

        ctx = _overlay_ctx()
        ctx.snapshot = None
        AdvisoryOverlayStep().run(ctx)
        assert ctx.snapshot is not None
        assert ctx.snapshot.positions == {} and ctx.snapshot.total_equity == 0.0

    def test_empty_dashboard_is_a_no_op(self, overlay_env):
        from pipeline.production_steps import AdvisoryOverlayStep

        ctx = _overlay_ctx()
        ctx.dashboard_df = pd.DataFrame()
        AdvisoryOverlayStep().run(ctx)
        assert ctx.recommendations == []
        assert "advisory_overlay_ok" not in ctx.context_extras
        assert overlay_env["evaluate"] == []

    def test_loop_failure_is_contained_and_not_marked_ok(self, overlay_env, monkeypatch):
        from pipeline.production_steps import AdvisoryOverlayStep

        def _boom(*a, **kw):
            raise RuntimeError("provider down")

        monkeypatch.setattr("data.market_data.get_provider", _boom)
        ctx = _overlay_ctx()
        AdvisoryOverlayStep().run(ctx)  # must not raise
        assert "advisory_overlay_ok" not in ctx.context_extras
        assert list(ctx.dashboard_df["Advisory_Action"]) == ["", ""]

    @pytest.mark.parametrize("row", [
        {"GARCH_Vol": 0.35, "Forecast_30": 150.0, "Forecast_30_Is_Fallback": True},
        {"GARCH_Vol": 0.35, "Forecast_30": 150.0, "Forecast_30_Is_Fallback": float("nan")},
        {"Symbol": "AAPL"},
    ])
    @pytest.mark.parametrize("reuse", [False, True])
    def test_precompute_selection_matches_the_pinned_reproduction(self, row, reuse):
        """tests/test_advisory_dedup_wiring.py pins a copy of the old inline
        selection logic; the extracted helper must still behave the same."""
        from pipeline.production_steps import _select_precomputed_for_row
        from tests.test_advisory_dedup_wiring import _select_precomputed

        got = _select_precomputed_for_row(dict(row), reuse)
        want = _select_precomputed(pd.Series(row), reuse)
        # pd.Series hands back numpy scalars; compare values, not reprs.
        assert [None if v is None else (v if isinstance(v, bool) else float(v)) for v in got] == \
            [None if v is None else (v if isinstance(v, bool) else float(v)) for v in want]

    def test_step_is_sync_so_the_runner_times_it(self):
        from pipeline.production_steps import AdvisoryOverlayStep, AgenticQueueStep

        assert not asyncio.iscoroutinefunction(AdvisoryOverlayStep().run)
        assert not asyncio.iscoroutinefunction(AgenticQueueStep().run)

"""
tests/test_daemon_advisory_reuse_diff.py -- step 5.2 gate (ii)
================================================================

Gate (ii) of ``.claude/shrink_step5_retire_main_py_implementation_plan.md``:
the frozen run from ``tests/test_daemon_advisory_shadow_equivalence.py`` with
``ADVISORY_REUSE_PIPELINE_COMPUTE`` ON, to show the operator the INTENDED diff
(operator decision 2 keeps reuse on in the live env). This is a report, not an
equivalence pin: reuse on is supposed to change things.

What reuse changes: ``AdvisoryOverlayStep`` passes each symbol's pipeline
``GARCH_Vol`` / ``Forecast_30`` / ``Forecast_30_Is_Fallback`` into
``engine.advisory.evaluate()`` instead of letting it refit GJR-GARCH and the
30-day forecast on its own 252-bar window. Here those pipeline values come
from the daemon's REAL ``TrendVolatilityStep`` + ``ForecastingStep`` code run
on the frozen full bar history (320 bars in this fixture).

Two variants:

* ``fake`` (runs in the normal suite): both sides use the golden's fake
  forecasting engine, so the forecast is identical and only the GARCH source
  differs. Pins that reuse moves ONLY ``garch_vol`` and the Kelly telemetry
  derived from it, and that actions, conviction, sizing and the queue don't
  move on this fixture (the 5% advisory position cap binds before Kelly does).
* ``real`` (``-m slow``, run with ``-s`` to see the table): both sides use the
  real ``ForecastingEngine``; the forecast then differs too, because the
  pipeline fits on the full history with the GARCH term structure while the
  advisory refit uses 252 bars. Reproduce the walkthrough table with
  ``pytest -m slow tests/test_daemon_advisory_reuse_diff.py -s -p no:randomly``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

import tests.test_daemon_advisory_shadow_equivalence as eq
from tests.test_run_once_advisory_golden import (
    _BARS,
    _FakeForecastingEngine,
    _install_frozen_inputs,
    _recommendation_dicts,
)

_GARCH_KEYS = {"garch_vol", "kelly_raw", "kelly_target_pre_regime", "kelly_target_post_regime"}


class _PipelineFakeForecastingEngine(_FakeForecastingEngine):
    """The golden's fake, accepting ForecastingStep's extra arguments."""

    def generate_forecast(self, row, current_price, *_a, **_kw):
        return super().generate_forecast(row, current_price)


def _run(tmp_path: Path, monkeypatch, *, reuse: bool, engine_kind: str) -> Dict[str, Any]:
    import engine.advisory as adv
    import main
    from main_orchestrator import EngineContext
    from pipeline.advisory_inputs import build_macro_dto
    from pipeline.production_steps import (
        ForecastingStep,
        TrendVolatilityStep,
        _apply_trend_vol_columns,
    )
    from settings import settings

    tmp_path.mkdir(parents=True, exist_ok=True)
    out = _install_frozen_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "ADVISORY_REUSE_PIPELINE_COMPUTE", reuse, raising=False)
    monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "shadow", raising=False)
    monkeypatch.setattr(settings, "FORECAST_MAX_CONCURRENCY", 1, raising=False)
    monkeypatch.setattr("alerting.notify", lambda *a, **kw: None)
    if engine_kind == "real":
        from forecasting_engine import ForecastingEngine

        engine = ForecastingEngine()
        monkeypatch.setattr(adv, "_get_forecasting_engine", lambda: engine)
    else:
        engine = _PipelineFakeForecastingEngine()

    macro = build_macro_dto()
    main._reset_macro_engine_cache()

    ctx = eq._make_daemon_ctx()
    eq._run_daemon_fetch(ctx, monkeypatch)
    ctx.macro_dto = macro
    ctx.dashboard_df = pd.DataFrame({
        "Symbol": list(ctx.symbols),
        "Price": [float(_BARS[s]["Close"].iloc[-1]) for s in ctx.symbols],
    })
    # The daemon's own GARCH + forecasting code, on the full frozen history.
    ctx.tech_raw = {s: _BARS[s].copy() for s in ctx.symbols}
    ctx.engine_context = EngineContext(forecasting_engine=engine)
    TrendVolatilityStep().run(ctx)
    _apply_trend_vol_columns(ctx.dashboard_df, ctx.context_extras["trend_vol_indicators"])
    ForecastingStep().run(ctx)

    eq._run_advisory_and_queue(ctx)
    queue_path = out / "shadow" / "execution_queue.json"
    return {
        "recs": _recommendation_dicts(ctx.recommendations),
        "pipeline": ctx.dashboard_df[["Symbol", "GARCH_Vol", "Forecast_30"]].to_dict("records"),
        "queue": json.loads(queue_path.read_text()) if queue_path.exists() else None,
    }


def _diff(off: Dict[str, Any], on: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for a, b in zip(off["recs"], on["recs"]):
        assert a["symbol"] == b["symbol"]
        ka, kb = a["key_indicators"], b["key_indicators"]
        rows.append({
            "symbol": a["symbol"],
            "fields": [f for f in ("action", "conviction", "suggested_position_pct",
                                   "suggested_exit_pct", "rationale") if a[f] != b[f]],
            "key_indicators": sorted(k for k in set(ka) | set(kb)
                                     if json.dumps(ka.get(k)) != json.dumps(kb.get(k))),
            "off": a, "on": b,
        })
    return rows


def _intents(run: Dict[str, Any]) -> List[tuple]:
    q = run["queue"]
    return [] if q is None else [(i["symbol"], i["action"], i["conviction"], i["target_notional"])
                                 for i in q["intents"]]


def _render(rows: List[Dict[str, Any]], off: Dict[str, Any], on: Dict[str, Any]) -> str:
    lines = ["symbol  action(off->on)  conviction  pos_pct  score  fcst30%  garch_vol"]
    for r in rows:
        a, b = r["off"], r["on"]
        ka, kb = a["key_indicators"], b["key_indicators"]
        lines.append(
            f"{r['symbol']:<6}  {a['action']}->{b['action']:<9}  "
            f"{a['conviction']}->{b['conviction']}  "
            f"{a['suggested_position_pct']}->{b['suggested_position_pct']}  "
            f"{ka.get('score')}->{kb.get('score')}  "
            f"{ka.get('forecast_30d_pct')}->{kb.get('forecast_30d_pct')}  "
            f"{ka.get('garch_vol')}->{kb.get('garch_vol')}"
        )
    lines.append(f"queue OFF: {_intents(off)}")
    lines.append(f"queue ON:  {_intents(on)}")
    return "\n".join(lines)


def test_reuse_on_moves_only_the_garch_source_with_identical_forecasts(tmp_path, monkeypatch):
    off = _run(tmp_path / "off", monkeypatch, reuse=False, engine_kind="fake")
    on = _run(tmp_path / "on", monkeypatch, reuse=True, engine_kind="fake")
    rows = _diff(off, on)
    print("\n" + _render(rows, off, on))
    # The pipeline GARCH (full history) differs from the advisory refit (252
    # bars) for every symbol, and that is ALL reuse changes when the forecast
    # is the same: no action, conviction, sizing, exit or rationale moves.
    assert all(r["key_indicators"] and set(r["key_indicators"]) <= _GARCH_KEYS for r in rows), rows
    assert all(r["fields"] == [] for r in rows), rows
    assert _intents(off) == _intents(on) and _intents(off)
    # And the value reused really is the pipeline's.
    by_symbol = {p["Symbol"]: p for p in on["pipeline"]}
    for r in rows:
        assert r["on"]["key_indicators"]["garch_vol"] == pytest.approx(
            by_symbol[r["symbol"]]["GARCH_Vol"], rel=1e-5)


@pytest.mark.slow
def test_reuse_on_diff_with_the_real_forecasting_engine(tmp_path, monkeypatch):
    off = _run(tmp_path / "off", monkeypatch, reuse=False, engine_kind="real")
    on = _run(tmp_path / "on", monkeypatch, reuse=True, engine_kind="real")
    rows = _diff(off, on)
    print("\n" + _render(rows, off, on))
    for r in rows:
        if r["fields"]:
            print(f"\n{r['symbol']} OFF: {r['off']['rationale']}\n{r['symbol']} ON:  {r['on']['rationale']}")
    assert len(rows) == 7

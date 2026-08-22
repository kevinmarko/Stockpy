"""
tests/test_options_analysis_step_macro_dto.py
================================================
Regression coverage for pipeline/production_steps.py::OptionsAnalysisStep.run()'s
MacroEconomicDTO construction -- the async-orchestrator (main_orchestrator.py)
production path whose ctx.macro_dto is what execution/risk_gate.py's
PreTradeRiskGate actually reads for real order approval.

Fixing macro_engine.py's run_macro_killswitch()/dto_models.py's
MacroEconomicDTO alone is not enough -- this file proves the fix is actually
wired into the live construction site: ctx.macro_raw missing T10Y2Y/
BAMLH0A0HYM2/VIXCLS, or MacroEngine.calculate_sahm_rule's own fallback firing,
must set ctx.macro_dto.data_unavailable=True (and therefore killSwitch=True),
never silently compute off substituted benign defaults (CONSTRAINT #4/#6).

ctx.symbols is deliberately empty in every test here -- OptionsAnalysisStep's
per-ticker options/GARCH analysis loop is out of scope for this file (already
covered by tests/test_production_steps_options_columns.py's
_apply_options_columns tests); an empty symbol list exercises the macro-DTO
construction path (the first ~20 lines of .run()) while making the
per-ticker ThreadPoolExecutor loop a guaranteed no-op.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from data_engine import MockDataEngine
from macro_engine import MacroEngine
from main_orchestrator import EngineContext
from pipeline.context import RunContext
from pipeline.production_steps import OptionsAnalysisStep


class _FakeFred:
    """Minimal stand-in for fredapi.Fred with a controllable get_series."""

    def __init__(self, series_map=None, raise_on=None):
        self._series_map = series_map or {}
        self._raise_on = raise_on or set()

    def get_series(self, series_id, limit=None):
        if series_id in self._raise_on:
            raise RuntimeError(f"FRED unavailable for {series_id}")
        return self._series_map.get(series_id, pd.Series(dtype=float))


class _FakeEngineWithFred:
    def __init__(self, fred):
        self.fred = fred


def _make_ctx(macro_raw, macro_engine) -> RunContext:
    """Minimal RunContext exercising only OptionsAnalysisStep.run()'s macro
    DTO construction -- symbols=[] keeps every downstream engine untouched."""
    return RunContext(
        force_account=False,
        started_at=datetime.now(timezone.utc),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **k: None,
        build_universe_fn=lambda *a, **k: [],
        build_macro_dto_fn=lambda: None,
        get_provider_fn=lambda: None,
        fetch_bars_fn=lambda *a, **k: {},
        build_context_extras_fn=lambda *a, **k: {},
        advisory_evaluate_fn=lambda *a, **k: None,
        symbols=[],
        market=None,
        tech_raw={},
        macro_raw=macro_raw,
        engine_context=EngineContext(macro_engine=macro_engine),
    )


class TestOptionsAnalysisStepMacroDataUnavailable:
    def test_healthy_macro_raw_and_real_sahm_reading_is_available(self):
        """The byte-identical-when-healthy regression guard: a fully populated
        macro_raw plus a real (non-fallback) Sahm reading must NOT set
        data_unavailable -- this is the case the original fix's landmine
        (an always-True formula) would have silently broken."""
        fred = _FakeFred(series_map={"SAHMREALTIME": pd.Series([0.1, 0.2, 0.15])})
        me = MacroEngine(data_engine=_FakeEngineWithFred(fred))
        macro_raw = {"T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0, "VIXCLS": 16.0}

        ctx = _make_ctx(macro_raw, me)
        OptionsAnalysisStep().run(ctx)

        assert ctx.macro_dto.data_unavailable is False
        assert ctx.macro_dto.killSwitch is False

    def test_empty_macro_raw_sets_data_unavailable(self):
        fred = _FakeFred(series_map={"SAHMREALTIME": pd.Series([0.1, 0.2, 0.15])})
        me = MacroEngine(data_engine=_FakeEngineWithFred(fred))

        ctx = _make_ctx({}, me)
        OptionsAnalysisStep().run(ctx)

        assert ctx.macro_dto.data_unavailable is True
        assert ctx.macro_dto.killSwitch is True
        assert ctx.macro_dto.market_regime == "RECESSION"

    def test_missing_vixcls_alone_sets_data_unavailable(self):
        fred = _FakeFred(series_map={"SAHMREALTIME": pd.Series([0.1, 0.2, 0.15])})
        me = MacroEngine(data_engine=_FakeEngineWithFred(fred))
        macro_raw = {"T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0}  # no VIXCLS

        ctx = _make_ctx(macro_raw, me)
        OptionsAnalysisStep().run(ctx)

        assert ctx.macro_dto.data_unavailable is True
        assert ctx.macro_dto.killSwitch is True

    def test_sahm_fallback_alone_sets_data_unavailable_even_with_complete_macro_raw(self):
        """calculate_sahm_rule's own internal fallback firing (FRED
        unreachable for the Sahm read specifically) must set
        data_unavailable even when macro_raw itself is fully populated --
        the two signals are independent and either alone is sufficient."""
        me = MacroEngine(data_engine=MockDataEngine())  # no .fred attribute at all
        macro_raw = {"T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0, "VIXCLS": 16.0}

        ctx = _make_ctx(macro_raw, me)
        OptionsAnalysisStep().run(ctx)

        assert ctx.macro_dto.data_unavailable is True
        assert ctx.macro_dto.killSwitch is True

    def test_sahm_rule_indicator_reflects_real_fred_value_not_fallback(self):
        """ctx.macro_dto.sahm_rule_indicator must carry the actual FRED-derived
        reading when available, not a hardcoded 0.0 -- confirms
        OptionsAnalysisStep correctly threads _calculate_sahm_rule_detailed's
        value through, mirroring the wiring already fixed in main.py."""
        fred = _FakeFred(series_map={"SAHMREALTIME": pd.Series([0.1, 0.2, 0.37])})
        me = MacroEngine(data_engine=_FakeEngineWithFred(fred))
        macro_raw = {"T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0, "VIXCLS": 16.0}

        ctx = _make_ctx(macro_raw, me)
        OptionsAnalysisStep().run(ctx)

        assert ctx.macro_dto.sahm_rule_indicator == 0.37
        assert ctx.macro_dto.data_unavailable is False

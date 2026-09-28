"""
tests/test_production_steps_portfolio_gross_cap.py
==================================================
Step 4d archived ETF volatility transmission to ``legacy/``. Before that, the
live portfolio gross cap (``settings.MAX_PORTFOLIO_GROSS``) sat in the SAME
``try`` block as the ETF covariance build inside ``StrategyEvalStep.run()``,
so any failure in the ETF code (an ImportError once the module moved, say)
was logged as "non-critical" and the gross cap was silently skipped.

This file proves the cap no longer depends on the ETF code:

* ``TestGrossCapBindsWithEtfModulesUnimportable`` runs the extracted
  ``_apply_portfolio_gross_cap`` helper on a fixture whose gross exceeds
  ``MAX_PORTFOLIO_GROSS`` with ``risk.etf_transmission`` and
  ``data.etf_holdings`` blocked via ``sys.modules[...] = None``.
* ``TestRunCallSite`` is an AST guard on ``StrategyEvalStep.run()``: the
  helper is called from a ``try`` that contains nothing else, and ``run()``
  references none of the archived ETF names.
* ``TestEtfColumnPrefill`` shows the four ETF schema columns (kept until
  the step-4f schema trim) are still written as NaN, so Pandera's
  ``DashboardSchema`` keeps validating.

``StrategyEvalStep.run()`` itself isn't invoked end to end (it imports
``main_orchestrator`` and its whole engine chain), matching the other
production-steps wiring tests.
"""
from __future__ import annotations

import ast
import inspect
import math
import sys
import textwrap
from unittest.mock import MagicMock

import pandas as pd
import pandera as pa
import pytest

import pipeline.production_steps as ps_mod
from config import COLUMN_SCHEMA, DashboardSchema
from settings import settings

_ETF_MODULES = ("risk.etf_transmission", "data.etf_holdings")
_ETF_COLUMNS = (
    "ETF_Ownership_Pct",
    "ETF_Comovement_R2",
    "ETF_Primary_Wrapper",
    "ETF_Transmission_Multiplier",
)


@pytest.fixture
def etf_modules_unimportable(monkeypatch):
    for name in _ETF_MODULES:
        monkeypatch.setitem(sys.modules, name, None)
    yield


@pytest.fixture
def quiet_telemetry(monkeypatch):
    # The module's telemetry proxy lazily imports main_orchestrator on first
    # use; a mock keeps this test light and lets us assert on the log call.
    tele = MagicMock()
    monkeypatch.setattr(ps_mod, "telemetry", tele)
    return tele


def _dashboard(kellys):
    syms = [f"S{i}" for i in range(len(kellys))]
    return pd.DataFrame({
        "Symbol": syms,
        "Kelly Target": kellys,
        "Sizing_Was_Capped": ["No"] * len(syms),
        "Sizing_Binding_Constraint": [None] * len(syms),
    })


class TestGrossCapBindsWithEtfModulesUnimportable:
    def test_etf_modules_really_are_blocked(self, etf_modules_unimportable):
        for name in _ETF_MODULES:
            with pytest.raises(ImportError):
                __import__(name)

    def test_cap_binds_when_gross_exceeds_max(
        self, etf_modules_unimportable, quiet_telemetry, monkeypatch,
    ):
        monkeypatch.setattr(settings, "MAX_PORTFOLIO_GROSS", 2.0)
        kellys = [0.9, 0.8, 0.6, 0.4, 0.3, 0.0]  # gross 3.0 > 2.0
        df = _dashboard(kellys)

        ps_mod._apply_portfolio_gross_cap(df)

        assert df["Kelly Target"].abs().sum() == pytest.approx(2.0, rel=1e-9)
        # Uniform scaling: relative sizing between names is unchanged.
        scale = 2.0 / 3.0
        for before, after in zip(kellys, df["Kelly Target"]):
            assert after == pytest.approx(before * scale, rel=1e-9)
        # Guardrail telemetry is overridden only for names that moved.
        assert list(df["Sizing_Binding_Constraint"]) == ["portfolio_gross"] * 5 + [None]
        assert list(df["Sizing_Was_Capped"]) == ["Yes"] * 5 + ["No"]
        quiet_telemetry.info.assert_called_once()

    def test_nan_weight_does_not_loosen_the_cap(
        self, etf_modules_unimportable, quiet_telemetry, monkeypatch,
    ):
        monkeypatch.setattr(settings, "MAX_PORTFOLIO_GROSS", 2.0)
        df = _dashboard([0.9, float("nan"), 0.8, 0.7])  # finite gross 2.4

        ps_mod._apply_portfolio_gross_cap(df)

        finite = [w for w in df["Kelly Target"] if not math.isnan(w)]
        assert sum(abs(w) for w in finite) == pytest.approx(2.0, rel=1e-9)

    def test_under_cap_is_left_untouched(
        self, etf_modules_unimportable, quiet_telemetry, monkeypatch,
    ):
        monkeypatch.setattr(settings, "MAX_PORTFOLIO_GROSS", 2.0)
        df = _dashboard([0.2, 0.1, 0.05])
        before = df.copy()

        ps_mod._apply_portfolio_gross_cap(df)

        pd.testing.assert_frame_equal(df, before)
        quiet_telemetry.info.assert_not_called()


class TestRunCallSite:
    def _run_tree(self):
        return ast.parse(textwrap.dedent(inspect.getsource(ps_mod.StrategyEvalStep.run)))

    def test_cap_helper_is_called_from_its_own_try(self):
        tries = [
            node for node in ast.walk(self._run_tree())
            if isinstance(node, ast.Try)
            and any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_apply_portfolio_gross_cap"
                for n in ast.walk(node)
            )
        ]
        assert len(tries) == 1
        # The try body is exactly the one call: nothing optional can fail
        # before it and skip the cap.
        assert len(tries[0].body) == 1
        call = tries[0].body[0].value
        assert isinstance(call, ast.Call) and call.func.id == "_apply_portfolio_gross_cap"

    def test_cap_call_is_not_nested_under_any_conditional(self):
        tree = self._run_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "_apply_portfolio_gross_cap":
                        pytest.fail("gross cap must run unconditionally")

    def test_run_references_no_archived_etf_code(self):
        src = inspect.getsource(ps_mod.StrategyEvalStep.run)
        for name in (
            "_build_etf_transmission_cov_matrix",
            "_apply_etf_transmission",
            "risk.etf_transmission",
            "data.etf_holdings",
            "etf_transmission_multiplier=",
        ):
            assert name not in src
        assert not hasattr(ps_mod, "_build_etf_transmission_cov_matrix")

    def test_helper_passes_no_covariance_matrix(self):
        src = inspect.getsource(ps_mod._apply_portfolio_gross_cap)
        assert "cov_matrix=None" in src
        assert "etf" not in src.lower().split('"""')[-1]  # code body, not the docstring


class TestEtfColumnPrefill:
    def _full_dashboard_without_etf(self):
        row = {}
        for col in COLUMN_SCHEMA:
            key = col["key"]
            if key in _ETF_COLUMNS:
                continue
            if key == "Symbol":
                row[key] = "AAA"
            elif col["format"] in ("currency", "currency_large", "percent", "number"):
                row[key] = float("nan")
            else:
                row[key] = None
        return pd.DataFrame([row])

    def test_all_four_columns_are_nan(self):
        df = pd.DataFrame({"Symbol": ["AAA", "BBB"]})
        ps_mod._prefill_etf_transmission_columns(df)
        for col in _ETF_COLUMNS:
            assert col in df.columns
            assert df[col].isna().all()

    def test_empty_frame_does_not_raise(self):
        df = pd.DataFrame({"Symbol": pd.Series([], dtype=str)})
        ps_mod._prefill_etf_transmission_columns(df)
        assert set(_ETF_COLUMNS) <= set(df.columns)

    def test_dashboard_schema_needs_the_prefill(self):
        df = self._full_dashboard_without_etf()
        with pytest.raises((pa.errors.SchemaError, pa.errors.SchemaErrors)):
            DashboardSchema.validate(df)
        ps_mod._prefill_etf_transmission_columns(df)
        DashboardSchema.validate(df)

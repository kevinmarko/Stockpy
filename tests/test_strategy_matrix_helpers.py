"""
tests/test_strategy_matrix_helpers.py
======================================
Pure-function unit coverage for gui/panels/strategy_matrix.py helpers that
don't require a Streamlit runtime.

TestOrNeutral is a regression guard for a fixed CONSTRAINT #4 bug: the
Regime-Multiplier Sizing Impact section previously read
``sig.get("regime_multiplier", 1.0) or 1.0`` -- since 0.0 is falsy in
Python, a genuine 0.0 (e.g. a MetaLabeler hard gate, or HMM
risk_on_probability=0.0) was silently fabricated into a neutral 1.0 in the
display layer, even though the very same 0.0 is correctly preserved
everywhere else (dashboard_df, state_snapshot.json -- see
tests/test_state_snapshot_parity.py::TestSizingQuartetNullHonesty).
"""
from gui.panels.strategy_matrix import _or_neutral


class TestOrNeutral:
    def test_genuine_zero_survives(self):
        assert _or_neutral(0.0) == 0.0

    def test_none_falls_back_to_neutral(self):
        assert _or_neutral(None) == 1.0

    def test_nan_falls_back_to_neutral(self):
        assert _or_neutral(float("nan")) == 1.0

    def test_ordinary_value_passes_through(self):
        assert _or_neutral(0.75) == 0.75

    def test_custom_neutral_default(self):
        assert _or_neutral(None, neutral=0.5) == 0.5
        assert _or_neutral(0.0, neutral=0.5) == 0.0

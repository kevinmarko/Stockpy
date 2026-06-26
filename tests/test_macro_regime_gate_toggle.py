"""
tests/test_macro_regime_gate_toggle.py
=======================================
Tests for the Macro Regime Gate toggle introduced in settings.py,
execution/risk_gate.py, and scripts/preflight_check.py.

Coverage
--------
*   ``macro_kill_switch_check`` passes unconditionally when
    ``MACRO_REGIME_GATE_ENABLED=False`` (hybrid / operator-override mode).
*   ``macro_kill_switch_check`` blocks a BUY when the gate is enabled and
    ``MacroEconomicDTO.killSwitch`` is True (nominal autonomous mode).
*   ``macro_kill_switch_check`` never blocks a SELL regardless of gate state.
*   ``check_macro_regime_gate_enabled`` passes + warns when gate is off and
    paper-trading is on (acceptable during development).
*   ``check_macro_regime_gate_enabled`` FAILS when gate is off AND live trading
    (ALPACA_PAPER=False) — the unsafe combination.
*   ``check_macro_regime_gate_enabled`` passes silently when gate is on.
*   ``gui.env_io.ALLOWED_KEYS`` includes ``MACRO_REGIME_GATE_ENABLED`` so the
    GUI Observability tab can write it.
*   ``gui.env_io.write_setting`` correctly serialises True/False as "true"/"false".

Constraints honoured
---------------------
*   All network I/O is avoided — only in-process settings mutation with
    monkeypatching.  No real .env file is written.
*   CONSTRAINT #2: no lookahead surface in this module; no perturbation test needed.
*   CONSTRAINT #5: tests assert on raised exceptions, not bare 0.0 returns.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_macro_dto(kill_switch_should_fire: bool = True):
    """Return a MacroEconomicDTO configured to fire / not fire killSwitch."""
    from dto_models import MacroEconomicDTO

    if kill_switch_should_fire:
        # Sahm ≥ 0.5 → killSwitch is True
        return MacroEconomicDTO(
            yield_curve_10y_2y=-0.5,
            high_yield_oas=7.0,
            inflation_rate=0.04,
            vix_value=35.0,
            sahm_rule_indicator=0.55,
        )
    else:
        # All indicators below threshold → killSwitch is False
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.5,
            high_yield_oas=3.0,
            inflation_rate=0.02,
            vix_value=15.0,
            sahm_rule_indicator=0.1,
        )


def _make_context(kill_switch_fires: bool = True):
    """Return a minimal RiskContext with a macro DTO."""
    from execution.risk_gate import RiskContext
    from execution.broker_base import AccountSnapshot

    return RiskContext(
        account=AccountSnapshot(
            buying_power=10_000.0,
            equity=50_000.0,
            cash=10_000.0,
        ),
        open_positions=[],
        macro=_make_macro_dto(kill_switch_fires),
        returns_df=None,
        start_of_day_equity=50_000.0,
        validation_reports={},
        is_premium_sell_strategy=False,
        current_prices={},
        timestamp=None,
    )


def _make_buy_intent():
    from execution.broker_base import OrderIntent, OrderSide, OrderType

    return OrderIntent(
        strategy_id="test_strat",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=1,
        order_type=OrderType.MARKET,
        limit_price=None,
        dry_run=True,
    )


def _make_sell_intent():
    from execution.broker_base import OrderIntent, OrderSide, OrderType

    return OrderIntent(
        strategy_id="test_strat",
        symbol="SPY",
        side=OrderSide.SELL,
        qty=1,
        order_type=OrderType.MARKET,
        limit_price=None,
        dry_run=True,
    )


# ---------------------------------------------------------------------------
# risk_gate.macro_kill_switch_check
# ---------------------------------------------------------------------------

class TestMacroKillSwitchCheck:
    """Tests for PreTradeRiskGate.macro_kill_switch_check."""

    def _gate(self):
        from execution.risk_gate import PreTradeRiskGate
        return PreTradeRiskGate()

    def test_blocks_buy_when_gate_enabled_and_kill_switch_fires(self, monkeypatch):
        """Gate ON + killSwitch True → BUY blocked."""
        from settings import settings as _s
        monkeypatch.setattr(_s, "MACRO_REGIME_GATE_ENABLED", True)
        result = self._gate().macro_kill_switch_check(
            _make_buy_intent(), _make_context(kill_switch_fires=True)
        )
        assert result.passed is False
        assert "macro kill switch active" in result.reason

    def test_passes_buy_when_gate_disabled(self, monkeypatch):
        """Gate OFF → BUY always passes regardless of killSwitch state."""
        from settings import settings as _s
        monkeypatch.setattr(_s, "MACRO_REGIME_GATE_ENABLED", False)
        result = self._gate().macro_kill_switch_check(
            _make_buy_intent(), _make_context(kill_switch_fires=True)
        )
        assert result.passed is True
        assert "disabled by operator" in result.reason

    def test_passes_sell_regardless_of_gate_state(self, monkeypatch):
        """SELL orders are never blocked by the macro kill-switch gate."""
        from settings import settings as _s
        for gate_state in (True, False):
            monkeypatch.setattr(_s, "MACRO_REGIME_GATE_ENABLED", gate_state)
            result = self._gate().macro_kill_switch_check(
                _make_sell_intent(), _make_context(kill_switch_fires=True)
            )
            assert result.passed is True, f"SELL should pass when gate={gate_state}"

    def test_passes_buy_when_gate_enabled_but_macro_benign(self, monkeypatch):
        """Gate ON but killSwitch False → BUY passes."""
        from settings import settings as _s
        monkeypatch.setattr(_s, "MACRO_REGIME_GATE_ENABLED", True)
        result = self._gate().macro_kill_switch_check(
            _make_buy_intent(), _make_context(kill_switch_fires=False)
        )
        assert result.passed is True
        assert "inactive" in result.reason

    def test_passes_conservatively_when_no_macro_context(self, monkeypatch):
        """No macro context → conservative pass (never block on missing data)."""
        from settings import settings as _s
        from execution.risk_gate import RiskContext
        from execution.broker_base import AccountSnapshot

        monkeypatch.setattr(_s, "MACRO_REGIME_GATE_ENABLED", True)
        ctx = RiskContext(
            account=AccountSnapshot(buying_power=10_000.0, equity=50_000.0, cash=10_000.0),
            open_positions=[],
            macro=None,
            returns_df=None,
            start_of_day_equity=50_000.0,
            validation_reports={},
            is_premium_sell_strategy=False,
            current_prices={},
            timestamp=None,
        )
        result = self._gate().macro_kill_switch_check(_make_buy_intent(), ctx)
        assert result.passed is True
        assert "skipping" in result.reason


# ---------------------------------------------------------------------------
# scripts/preflight_check.check_macro_regime_gate_enabled
# ---------------------------------------------------------------------------

class TestPreflightMacroGate:
    """Tests for check_macro_regime_gate_enabled."""

    def test_passes_when_gate_on(self, monkeypatch):
        from settings import settings as _s
        monkeypatch.setattr(_s, "MACRO_REGIME_GATE_ENABLED", True)
        monkeypatch.setattr(_s, "ALPACA_PAPER", True)
        from scripts.preflight_check import check_macro_regime_gate_enabled
        r = check_macro_regime_gate_enabled()
        assert r.passed is True
        assert r.warning is False

    def test_warns_when_gate_off_in_paper_mode(self, monkeypatch):
        """Gate disabled + paper → warning, not fail."""
        from settings import settings as _s
        monkeypatch.setattr(_s, "MACRO_REGIME_GATE_ENABLED", False)
        monkeypatch.setattr(_s, "ALPACA_PAPER", True)
        from scripts.preflight_check import check_macro_regime_gate_enabled
        r = check_macro_regime_gate_enabled()
        assert r.passed is True
        assert r.warning is True
        assert "hybrid mode" in r.reason

    def test_fails_when_gate_off_in_live_mode(self, monkeypatch):
        """Gate disabled + live → blocking failure."""
        from settings import settings as _s
        monkeypatch.setattr(_s, "MACRO_REGIME_GATE_ENABLED", False)
        monkeypatch.setattr(_s, "ALPACA_PAPER", False)
        from scripts.preflight_check import check_macro_regime_gate_enabled
        r = check_macro_regime_gate_enabled()
        assert r.passed is False
        assert "not allowed" in r.reason


# ---------------------------------------------------------------------------
# gui/env_io integration
# ---------------------------------------------------------------------------

class TestEnvIoMacroGateKey:
    """The GUI must be able to write MACRO_REGIME_GATE_ENABLED."""

    def test_allowed_keys_contains_macro_regime_gate(self):
        from gui import env_io
        assert "MACRO_REGIME_GATE_ENABLED" in env_io.ALLOWED_KEYS

    def test_write_true_roundtrip(self, tmp_path, monkeypatch):
        from gui import env_io
        env_file = tmp_path / ".env"
        env_file.write_text("MACRO_REGIME_GATE_ENABLED=false\n", encoding="utf-8")
        monkeypatch.setattr(env_io, "ENV_PATH", env_file)
        env_io.write_setting("MACRO_REGIME_GATE_ENABLED", True)
        assert env_io.get_value("MACRO_REGIME_GATE_ENABLED") == "true"

    def test_write_false_roundtrip(self, tmp_path, monkeypatch):
        from gui import env_io
        env_file = tmp_path / ".env"
        env_file.write_text("MACRO_REGIME_GATE_ENABLED=true\n", encoding="utf-8")
        monkeypatch.setattr(env_io, "ENV_PATH", env_file)
        env_io.write_setting("MACRO_REGIME_GATE_ENABLED", False)
        assert env_io.get_value("MACRO_REGIME_GATE_ENABLED") == "false"

    def test_macro_gate_key_not_classified_as_secret(self):
        from gui import env_io
        assert env_io.is_secret("MACRO_REGIME_GATE_ENABLED") is False

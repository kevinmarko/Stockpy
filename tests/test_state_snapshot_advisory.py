"""
tests/test_state_snapshot_advisory.py
=====================================
Pins the Wave-1 schema additions to
``reporting/state_snapshot.py::write_state_snapshot`` (the advisory writer used
by ``main.py``). The writer must now surface, for GUI Observability / Strategy
Matrix parity with the ``main_orchestrator`` writer:

  Top-level (from macro_dto):
    * ``sahm_rule``               <- macro_dto.sahm_rule_indicator
    * ``high_yield_oas``          <- macro_dto.credit_spread
    * ``yield_curve``             <- macro_dto.yield_curve
    * ``hmm_risk_on_probability`` <- macro_dto.hmm_risk_on_probability

  Per-signal (from Recommendation.key_indicators):
    * ``garch_vol``               <- key_indicators["garch_vol"]      (real value round-trips)
    * ``hmm_risk_on``             <- macro-wide hmm probability
    * multifactor ``value_z`` / ``quality_z`` / ``lowvol_z`` / ``size_z`` /
      ``multifactor_composite``   -> JSON ``null`` (NOT 0.0) when absent from
      key_indicators (CONSTRAINT #4 — no fabricated zeros).

Uses lightweight ``SimpleNamespace`` stubs for RunResult / positions / macro_dto
(the writer reads them via ``getattr`` / dict access), and points
``settings.OUTPUT_DIR`` at a tmp dir so nothing touches the real ``output/``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import reporting.state_snapshot as ss
from settings import settings


def _position(qty: float, price: float) -> SimpleNamespace:
    return SimpleNamespace(quantity=qty, current_price=price)


def _recommendation(symbol: str, *, sector: str = "", **extra_ki) -> SimpleNamespace:
    key_indicators = {"score": 1.2, "garch_vol": 0.28}
    key_indicators.update(extra_ki)
    return SimpleNamespace(
        symbol=symbol,
        action="BUY",
        conviction=0.7,
        suggested_position_pct=0.03,
        rationale="test rationale",
        key_indicators=key_indicators,
        score_components={"momentum": 0.5},
        buy_range="Buy: $10 - $11",
        sell_range="Sell: $12 - $13",
        suggested_exit_pct=0.5,
        sector=sector,
    )


def _macro() -> SimpleNamespace:
    return SimpleNamespace(
        market_regime="RISK ON",
        vix_value=18.5,
        yield_curve=0.25,
        sahm_rule_indicator=0.12,
        credit_spread=4.1,
        hmm_risk_on_probability=0.82,
    )


@pytest.fixture()
def written_snapshot(tmp_path, monkeypatch):
    """Run write_state_snapshot against a tmp OUTPUT_DIR and return parsed JSON.

    One held position (AAPL, with a multifactor value_z present) and one
    unheld symbol (MSFT, with NO multifactor keys) so we can assert both the
    round-trip and the null-not-zero behavior in a single write.
    """
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)

    rec_held = _recommendation("AAPL", sector="Technology", garch_vol=0.28, value_z=1.5)
    rec_unheld = _recommendation("MSFT")  # no multifactor keys, no sector

    result = SimpleNamespace(
        snapshot=SimpleNamespace(positions={"AAPL": _position(10.0, 150.0)}),
        recommendations=[rec_held, rec_unheld],
    )

    ss.write_state_snapshot(result, _macro())

    snap_path = tmp_path / "state_snapshot.json"
    assert snap_path.exists(), "write_state_snapshot must materialize the JSON file"
    return json.loads(snap_path.read_text(encoding="utf-8"))


def _signal(snap: dict, symbol: str) -> dict:
    return next(s for s in snap["signals"] if s["symbol"] == symbol)


class TestTopLevelMacroFields:
    def test_recession_and_regime_telemetry_present(self, written_snapshot):
        assert written_snapshot["sahm_rule"] == pytest.approx(0.12)
        assert written_snapshot["high_yield_oas"] == pytest.approx(4.1)
        assert written_snapshot["yield_curve"] == pytest.approx(0.25)
        assert written_snapshot["hmm_risk_on_probability"] == pytest.approx(0.82)

    def test_existing_top_level_schema_retained(self, written_snapshot):
        for key in ("timestamp", "market_regime", "vix", "signals", "holdings"):
            assert key in written_snapshot
        assert written_snapshot["market_regime"] == "RISK ON"


class TestPerSignalTelemetry:
    def test_garch_vol_round_trips(self, written_snapshot):
        sig = _signal(written_snapshot, "AAPL")
        assert sig["garch_vol"] == pytest.approx(0.28)

    def test_hmm_risk_on_carried_per_signal(self, written_snapshot):
        sig = _signal(written_snapshot, "AAPL")
        assert sig["hmm_risk_on"] == pytest.approx(0.82)

    def test_present_multifactor_value_round_trips(self, written_snapshot):
        sig = _signal(written_snapshot, "AAPL")
        assert sig["value_z"] == pytest.approx(1.5)

    def test_missing_multifactor_serializes_as_null_not_zero(self, written_snapshot):
        """CONSTRAINT #4: an absent multifactor score is JSON null, never a
        fabricated 0.0 the GUI would misread as a genuine zero exposure."""
        sig = _signal(written_snapshot, "MSFT")
        for key in ("value_z", "quality_z", "lowvol_z", "size_z", "multifactor_composite"):
            assert sig[key] is None, f"{key} must serialize as null when unavailable"
            assert sig[key] != 0.0

    def test_sector_present_and_round_trips(self, written_snapshot):
        """Sector string from Recommendation.sector is threaded into each
        per-signal record (feeds the downstream sector-allocation view)."""
        sig = _signal(written_snapshot, "AAPL")
        assert "sector" in sig
        assert sig["sector"] == "Technology"

    def test_sector_defaults_to_empty_string_when_absent(self, written_snapshot):
        """CONSTRAINT #4: a Recommendation with no sector emits "" (never
        fabricated), and the key is always present for a consistent schema."""
        sig = _signal(written_snapshot, "MSFT")
        assert "sector" in sig
        assert sig["sector"] == ""

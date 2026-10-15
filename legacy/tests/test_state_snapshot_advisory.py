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


class TestSymbolDetailParityFields:
    """The advisory writer emits the SymbolDetail parity fields
    (xsec_12_1m/xsec_momentum_rank + excursion mfe/mae/edge_ratio from
    key_indicators, macro_status from Recommendation.macro_regime) so the Pilots
    /symbol/{ticker} page matches the orchestrator writer's per-signal schema."""

    def test_new_numeric_fields_present_and_null_when_absent(self, written_snapshot):
        # The base fixture recs carry no xsec/excursion in key_indicators → null,
        # never a fabricated 0.0.
        for symbol in ("AAPL", "MSFT"):
            sig = _signal(written_snapshot, symbol)
            for key in ("xsec_12_1m", "xsec_momentum_rank", "mfe", "mae", "edge_ratio"):
                assert key in sig, f"{key} missing for {symbol}"
                assert sig[key] is None, f"{key} must be null when absent"
                assert sig[key] != 0.0

    def test_macro_status_defaults_empty_when_recommendation_has_none(self, written_snapshot):
        # Fixture recs don't set macro_regime → "" (schema key always present).
        assert _signal(written_snapshot, "AAPL")["macro_status"] == ""

    def test_new_numeric_fields_round_trip_from_key_indicators(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        rec = _recommendation(
            "AAPL", xsec_12_1m=0.185, xsec_momentum_rank=0.9,
            mfe=0.12, mae=0.04, edge_ratio=3.0,
        )
        rec.macro_regime = "RISK ON"
        result = SimpleNamespace(
            snapshot=SimpleNamespace(positions={}), recommendations=[rec]
        )
        ss.write_state_snapshot(result, _macro())
        sig = json.loads(
            (tmp_path / "state_snapshot.json").read_text(encoding="utf-8")
        )["signals"][0]
        assert sig["xsec_12_1m"] == pytest.approx(0.185)
        assert sig["xsec_momentum_rank"] == pytest.approx(0.9)
        assert sig["mfe"] == pytest.approx(0.12)
        assert sig["mae"] == pytest.approx(0.04)
        assert sig["edge_ratio"] == pytest.approx(3.0)
        assert sig["macro_status"] == "RISK ON"


class TestAtomicWrite:
    """2026-07 fix: write_state_snapshot() used to write state_snapshot.json
    with a bare write_text() -- a process killed mid-write (e.g. main.py
    --interval's routine 5s SIGKILL of a mid-cycle process, see
    desktop/engine_supervisor.py's backend-aware stop_engine timeout) left a
    truncated, unparseable file that every reader (pilots/run_status.py,
    api/state_api.py) then treated as MISSING rather than merely stale.
    Fixed to the same write-then-rename idiom already used by
    execution/kill_switch.py and desktop/orchestrator_daemon.py."""

    def test_uses_write_then_rename_not_a_bare_write_text(self, tmp_path, monkeypatch):
        from pathlib import Path
        from unittest import mock

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        result = SimpleNamespace(
            snapshot=SimpleNamespace(positions={}),
            recommendations=[_recommendation("AAPL")],
        )

        with mock.patch.object(Path, "replace", autospec=True) as mock_replace:
            mock_replace.side_effect = lambda self_path, target: self_path.rename(target)
            ss.write_state_snapshot(result, _macro())

        # rotate_snapshot() (already atomic) ALSO calls Path.replace for its
        # own history/ write, so this asserts the main state_snapshot.json
        # write specifically used the idiom, not merely that SOME call did.
        main_write_calls = [
            c for c in mock_replace.call_args_list
            if c.args[1].name == "state_snapshot.json"
        ]
        assert len(main_write_calls) == 1
        # The final file must exist under its real name (not left as a .tmp
        # sibling) and must be valid, complete JSON.
        snap_path = tmp_path / "state_snapshot.json"
        assert snap_path.exists()
        assert not (tmp_path / "state_snapshot.tmp").exists()
        json.loads(snap_path.read_text(encoding="utf-8"))  # must not raise

    def test_a_failed_rename_never_leaves_a_corrupt_final_file(self, tmp_path, monkeypatch):
        """If the final rename step itself fails (disk full, permissions),
        the pre-existing state_snapshot.json (if any) must be left INTACT --
        never partially overwritten -- and the failure must be swallowed
        (write_state_snapshot's own try/except), never propagated."""
        from pathlib import Path
        from unittest import mock

        monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
        snap_path = tmp_path / "state_snapshot.json"
        snap_path.write_text('{"previous": true}', encoding="utf-8")

        result = SimpleNamespace(
            snapshot=SimpleNamespace(positions={}),
            recommendations=[_recommendation("AAPL")],
        )

        with mock.patch.object(Path, "replace", side_effect=OSError("disk full")):
            ss.write_state_snapshot(result, _macro())  # must not raise

        # The old file is untouched -- a write-then-rename failure can only
        # ever leave a stray .tmp sibling, never a half-written final file.
        assert json.loads(snap_path.read_text(encoding="utf-8")) == {"previous": True}

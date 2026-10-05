"""
tests/test_compose.py — advisory execution-queue composer
===========================================================
Covers ``execution/compose.py`` — the single writer of
``output/execution_queue.json``. It reads the advisory pipeline's own source
file, filters it against the advisory conviction floor, and hands the result
to the EXISTING (unchanged) ``execution.queue_builder.build_execution_queue``/
``emit_execution_queue``.

Follow-a-Pilot (the per-Pilot follow sources this composer used to net
against advisory) was archived to ``legacy/`` in 2026-09 (step 4c); its
netting tests moved with it. The byte-for-byte pin that the advisory-only
queue is unchanged by that removal lives in
``tests/test_compose_advisory_only_golden.py``.

Fully offline — no broker, no MCP, no network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pytest

import execution.compose as compose
from execution.compose import (
    AdvisorySourceClaims,
    compose_and_emit,
    compose_targets,
    read_source,
    write_advisory_source,
    write_source,
)
from execution.queue_builder import build_execution_queue

_NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Duck-typed shapes (mirrors tests/test_queue_builder.py's conventions)
# ---------------------------------------------------------------------------


@dataclass
class _Pos:
    symbol: str
    quantity: float
    current_price: float
    market_value: float
    average_cost: float = 0.0
    unrealized_pl: float = 0.0


@dataclass
class _Snap:
    positions: Dict[str, _Pos] = field(default_factory=dict)
    total_equity: float = 100_000.0
    buying_power: float = 100_000.0


def _snap(equity: float, positions: Optional[Dict[str, _Pos]] = None) -> _Snap:
    return _Snap(positions=positions or {}, total_equity=equity, buying_power=equity)


def _advisory_target(symbol, action, *, conviction=0.9, pct=0.05,
                      strategy="advisory", rationale="because") -> dict:
    return {
        "symbol": symbol, "action": action, "conviction": conviction,
        "suggested_position_pct": pct, "strategy": strategy, "rationale": rationale,
    }


@pytest.fixture(autouse=True)
def _no_cap(monkeypatch):
    """Default every test to an UNSET per-order cap unless it sets one itself."""
    from settings import settings
    monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0, raising=False)


# ---------------------------------------------------------------------------
# compose_targets — advisory filtering / shaping
# ---------------------------------------------------------------------------


class TestComposeTargets:
    def test_advisory_buy_is_shaped_with_advisory_attribution(self):
        advisory = AdvisorySourceClaims(targets=[_advisory_target("NVDA", "BUY", pct=0.05)])
        composed = compose_targets(advisory=advisory, account_snapshot=_snap(100_000.0, {}))
        assert len(composed) == 1
        ci = composed[0]
        assert ci.action == "BUY"
        assert ci.strategy_id == "advisory"
        assert ci.suggested_position_pct == pytest.approx(0.05)
        assert ci.sources == [{"source_id": "advisory", "target_notional": 5000.0}]

    def test_low_conviction_advisory_rec_is_filtered(self):
        advisory = AdvisorySourceClaims(targets=[
            _advisory_target("NVDA", "BUY", conviction=0.1, pct=0.05)  # well below 0.85
        ])
        assert compose_targets(advisory=advisory, account_snapshot=_snap(100_000.0, {})) == []

    def test_first_claim_per_symbol_wins_and_output_is_sorted(self):
        advisory = AdvisorySourceClaims(targets=[
            _advisory_target("NVDA", "BUY", rationale="first"),
            _advisory_target("AAPL", "BUY"),
            _advisory_target("NVDA", "SELL", rationale="second"),
        ])
        composed = compose_targets(advisory=advisory, account_snapshot=_snap(100_000.0, {}))
        assert [c.symbol for c in composed] == ["AAPL", "NVDA"]
        assert composed[1].rationale == "first"

    def test_no_equity_composes_nothing(self):
        advisory = AdvisorySourceClaims(targets=[_advisory_target("NVDA", "BUY")])
        assert compose_targets(advisory=advisory, account_snapshot=_snap(0.0, {})) == []

    def test_no_advisory_source_composes_nothing(self):
        assert compose_targets(advisory=None, account_snapshot=_snap(100_000.0, {})) == []

    def test_client_order_id_is_byte_identical_to_direct_build(self):
        """A composed advisory intent must produce the SAME client_order_id a
        direct (non-composed) build_execution_queue call would have."""
        from dataclasses import dataclass as _dc

        @_dc
        class _DirectRec:
            symbol: str
            action: str
            conviction: float
            suggested_position_pct: float
            strategy: str = "advisory"
            rationale: str = "because"

        @_dc
        class _DirectRR:
            snapshot: _Snap
            recommendations: list

        account = _snap(100_000.0, {})
        direct_rec = _DirectRec(symbol="NVDA", action="BUY", conviction=0.9, suggested_position_pct=0.05)
        direct_payload = build_execution_queue(
            _DirectRR(snapshot=account, recommendations=[direct_rec]),
            mode="review", config={"strategy_id": "advisory"}, now=_NOW,
        )

        advisory = AdvisorySourceClaims(targets=[
            _advisory_target("NVDA", "BUY", conviction=0.9, pct=0.05)
        ])
        composed = compose_targets(advisory=advisory, account_snapshot=account)
        composed_run = compose._ComposedRunResult(recommendations=composed, snapshot=account)
        composed_payload = build_execution_queue(
            composed_run, mode="review", config={"strategy_id": "composed", "min_conviction": 0.0}, now=_NOW,
        )

        assert direct_payload["intents"][0]["client_order_id"] == composed_payload["intents"][0]["client_order_id"]
        assert composed_payload["intents"][0]["overridden"] == []


# ---------------------------------------------------------------------------
# Source file I/O
# ---------------------------------------------------------------------------


class TestSourceReadWrite:
    def test_write_then_read_round_trips(self, tmp_path):
        path = write_source("advisory", [_advisory_target("NVDA", "BUY")], output_dir=tmp_path, now=_NOW)
        assert path is not None
        assert path.exists()
        r = read_source("advisory", output_dir=tmp_path)
        assert r.present is True
        assert r.corrupt is False
        assert r.targets == [_advisory_target("NVDA", "BUY")]
        assert r.generated_at == _NOW

    def test_missing_source_is_present_false_not_corrupt(self, tmp_path):
        r = read_source("advisory", output_dir=tmp_path)
        assert r.present is False
        assert r.corrupt is False
        assert r.stale is False
        assert r.targets == []

    def test_corrupt_json_is_flagged(self, tmp_path):
        d = tmp_path / "queue_sources"
        d.mkdir(parents=True)
        (d / "advisory.json").write_text("not json {{{", encoding="utf-8")
        r = read_source("advisory", output_dir=tmp_path)
        assert r.present is True
        assert r.corrupt is True

    def test_missing_generated_at_is_flagged_corrupt(self, tmp_path):
        d = tmp_path / "queue_sources"
        d.mkdir(parents=True)
        (d / "advisory.json").write_text(json.dumps({"targets": []}), encoding="utf-8")
        r = read_source("advisory", output_dir=tmp_path)
        assert r.corrupt is True

    def test_stale_source_is_flagged(self, tmp_path):
        old = _NOW - timedelta(days=30)
        write_source("advisory", [], output_dir=tmp_path, now=old)
        r = read_source("advisory", output_dir=tmp_path, max_age_seconds=604800.0, now=_NOW)
        assert r.corrupt is False
        assert r.stale is True

    def test_fresh_source_is_not_stale(self, tmp_path):
        write_source("advisory", [], output_dir=tmp_path, now=_NOW)
        r = read_source("advisory", output_dir=tmp_path, max_age_seconds=604800.0,
                         now=_NOW + timedelta(hours=1))
        assert r.stale is False

    def test_write_advisory_source_filters_to_actionable_only(self, tmp_path):
        from dataclasses import dataclass as _dc

        @_dc
        class _R:
            symbol: str
            action: str
            conviction: float = 0.9
            suggested_position_pct: float = 0.05
            strategy: str = "advisory"
            rationale: str = "why"

        recs = [_R("NVDA", "BUY"), _R("MSFT", "HOLD"), _R("AAPL", "SELL")]
        write_advisory_source(recs, output_dir=tmp_path, now=_NOW)
        r = read_source("advisory", output_dir=tmp_path)
        assert {t["symbol"] for t in r.targets} == {"NVDA", "AAPL"}


# ---------------------------------------------------------------------------
# compose_and_emit — dead-letter posture (corrupt/stale -> refuse the compose)
# ---------------------------------------------------------------------------


class TestComposeAndEmitDeadLetter:
    def test_corrupt_advisory_source_writes_nothing_leaves_prior_queue(self, tmp_path, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        # Seed an existing queue file that must survive untouched.
        existing = tmp_path / "execution_queue.json"
        existing.write_text('{"sentinel": "do-not-touch"}', encoding="utf-8")

        d = tmp_path / "queue_sources"
        d.mkdir(parents=True)
        (d / "advisory.json").write_text("not json", encoding="utf-8")

        account = _snap(100_000.0, {})
        result = compose_and_emit(account, output_dir=tmp_path, now=_NOW)

        assert result is None
        assert existing.read_text(encoding="utf-8") == '{"sentinel": "do-not-touch"}'

    def test_stale_advisory_source_writes_nothing(self, tmp_path, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        old = _NOW - timedelta(days=30)
        write_source("advisory", [_advisory_target("NVDA", "BUY")], output_dir=tmp_path, now=old)

        account = _snap(100_000.0, {})
        result = compose_and_emit(account, output_dir=tmp_path, now=_NOW, max_age_seconds=604800.0)

        assert result is None
        assert not (tmp_path / "execution_queue.json").exists()

    def test_leftover_follow_state_on_disk_is_ignored(self, tmp_path, monkeypatch):
        """A pre-archive install can still have follows.json and a
        queue_sources/follow-<id>.json (even a corrupt one) on disk. The
        composer no longer reads either: the queue is advisory-only and a
        corrupt leftover follow file can no longer block it."""
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        write_source("advisory", [_advisory_target("NVDA", "BUY")], output_dir=tmp_path, now=_NOW)
        (tmp_path / "follows.json").write_text(
            json.dumps({"follows": [{"pilot_id": "trend-following", "amount": 5000.0,
                                     "status": "active"}]}),
            encoding="utf-8",
        )
        (tmp_path / "queue_sources" / "follow-trend-following.json").write_text(
            "{{not json", encoding="utf-8",
        )

        result = compose_and_emit(_snap(100_000.0, {}), output_dir=tmp_path, now=_NOW)

        assert result is not None
        payload = json.loads(result.read_text(encoding="utf-8"))
        assert [i["symbol"] for i in payload["intents"]] == ["NVDA"]
        assert payload["intents"][0]["sources"] == [
            {"source_id": "advisory", "target_notional": 5000.0}
        ]


class TestMacroDtoThreading:
    """Proves compose_and_emit's macro_dto plumbing (_ComposedRunResult →
    execution.queue_builder._build_risk_context → PreTradeRiskGate) actually
    reaches the shared risk gate, not just build_execution_queue in
    isolation. See execution/macro_snapshot.py's module docstring for the
    full history."""

    def test_compose_and_emit_threads_macro_dto_to_the_risk_gate(self, tmp_path, monkeypatch):
        from settings import settings
        from dto_models import MacroEconomicDTO
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        write_source("advisory", [_advisory_target("NVDA", "BUY")], output_dir=tmp_path, now=_NOW)
        killswitch_macro = MacroEconomicDTO(
            yield_curve_10y_2y=-0.5, high_yield_oas=7.0, inflation_rate=3.0,
            sahm_rule_indicator=0.6, vix_value=35.0,
        )

        account = _snap(100_000.0, {})
        result = compose_and_emit(
            account, output_dir=tmp_path, now=_NOW, macro_dto=killswitch_macro,
        )

        assert result is not None
        payload = json.loads(result.read_text(encoding="utf-8"))
        intent = payload["intents"][0]
        assert intent["symbol"] == "NVDA"
        assert intent["gate_allowed"] is False
        assert any(r.startswith("macro_kill_switch:") for r in intent["gate_reasons"])

    def test_compose_and_emit_falls_open_with_no_macro_dto_and_no_cache(
        self, tmp_path, monkeypatch,
    ):
        """Backward-compat pin: no macro_dto AND no cached macro data
        anywhere (a fresh DB) reproduces the pre-fix fail-open behaviour
        exactly, never a spurious veto."""
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        monkeypatch.setattr(
            settings, "DATABASE_URL", f"sqlite:///{tmp_path / 'never_written.db'}",
            raising=False,
        )

        write_source("advisory", [_advisory_target("NVDA", "BUY")], output_dir=tmp_path, now=_NOW)
        account = _snap(100_000.0, {})
        result = compose_and_emit(account, output_dir=tmp_path, now=_NOW)

        assert result is not None
        payload = json.loads(result.read_text(encoding="utf-8"))
        intent = payload["intents"][0]
        assert not any(r.startswith("macro_kill_switch:") for r in intent["gate_reasons"])


class TestComposeHasNoFollowDependency:
    """Trap 2 of the step-4 plan: compose_and_emit used to import from
    pilots.mirror / pilots.follows_store inside its try/except, so archiving
    Follow-a-Pilot could have silently stopped execution_queue.json from ever
    being written. These tests pin the independence."""

    def test_min_conviction_floor_is_a_literal_and_no_pilot_imports(self):
        import inspect

        src = inspect.getsource(compose)
        assert '"min_conviction": 0.0' in src
        assert "from pilots" not in src
        assert "import pilots" not in src

    def test_queue_is_written_with_follow_modules_unimportable(self, tmp_path, monkeypatch):
        import sys
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        for name in ("pilots.mirror", "pilots.follows_store", "pilots.portfolio_attribution"):
            monkeypatch.setitem(sys.modules, name, None)
        write_source("advisory", [_advisory_target("NVDA", "BUY")], output_dir=tmp_path, now=_NOW)

        account = _snap(100_000.0, {})
        result = compose_and_emit(account, output_dir=tmp_path, now=_NOW)

        assert result is not None
        assert (tmp_path / "execution_queue.json").exists()

    def test_no_sources_at_all_writes_nothing(self, tmp_path, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        account = _snap(100_000.0, {})
        result = compose_and_emit(account, output_dir=tmp_path, now=_NOW)
        assert result is None
        assert not (tmp_path / "execution_queue.json").exists()

    def test_off_mode_writes_nothing(self, tmp_path, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "off", raising=False)
        write_advisory_source(
            [type("R", (), {"symbol": "NVDA", "action": "BUY", "conviction": 0.9,
                            "suggested_position_pct": 0.05, "strategy": "advisory",
                            "rationale": "why"})()],
            output_dir=tmp_path, now=_NOW,
        )
        account = _snap(100_000.0, {})
        result = compose_and_emit(account, output_dir=tmp_path, now=_NOW)
        assert result is None
        assert not (tmp_path / "execution_queue.json").exists()

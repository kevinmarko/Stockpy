"""
tests/test_compose.py — cross-Pilot + advisory queue composer (Tier 8 / Piece 1)
==================================================================================
Covers ``execution/compose.py`` — the single writer of
``output/execution_queue.json``, unioning the advisory pipeline's own source
with every actively-followed Pilot's source, netting overlapping claims on
the same symbol, and handing the result to the EXISTING (unchanged)
``execution.queue_builder.build_execution_queue``/``emit_execution_queue``.

Fully offline — no broker, no MCP, no network. ``compose_targets`` is
exercised directly with hand-built ``AdvisorySourceClaims``/
``FollowSourceClaims`` for exact control over the numbers (the money test,
the band, the Q7 cap table); the I/O layer (``read_source``/``write_source``/
``write_advisory_source``/``write_follow_source``/``compose_and_emit``) is
exercised against ``tmp_path`` with the committed fixture snapshot for the
follow-source writer (mirroring ``tests/test_pilots_mirror.py``'s pattern).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pytest

import execution.compose as compose
from execution.compose import (
    AdvisorySourceClaims,
    FollowSourceClaims,
    compose_and_emit,
    compose_targets,
    follow_source_id,
    read_source,
    write_advisory_source,
    write_follow_source,
    write_source,
)
from execution.queue_builder import build_execution_queue
from pilots.catalog import get_pilot
from pilots.mirror import build_follow_intents
from pilots.scoring import load_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "state_snapshot.json"
_PILOT_ID = "trend-following"
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


def _follow_target(symbol, *, weight=0.3, target_notional=3000.0,
                    score=0.5, price=100.0, rationale="ranked") -> dict:
    return {
        "symbol": symbol, "weight": weight, "target_notional": target_notional,
        "score": score, "price": price, "rationale": rationale,
    }


def _follow_source(source_id, *, targets=None, dropped=None) -> FollowSourceClaims:
    return FollowSourceClaims(source_id=source_id, targets=targets or [], dropped_targets=dropped or [])


@pytest.fixture(autouse=True)
def _no_cap(monkeypatch):
    """Default every test to an UNSET per-order cap unless it sets one itself."""
    from settings import settings
    monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0, raising=False)


# ---------------------------------------------------------------------------
# Q2 — the money test: net TARGETS, not deltas
# ---------------------------------------------------------------------------


class TestMoneyTestNetTargetsNotDeltas:
    def test_two_follows_net_once_not_sum_of_deltas(self):
        """A wants NVDA $3750, B wants $2000, follower holds $8000.
        Sum-of-deltas would be (3750-8000)+(2000-8000)=-10250 (sell $10250 of
        an $8000 position — a sign-magnitude catastrophe: more than 100% of
        the position, and MORE than either Pilot's own claim). Net targets:
        net=5750, delta=-2250 -> a single $2250 trim, correctly leaving
        $5750 held (exactly what both Pilots combined still want)."""
        a = _follow_source("follow-a", targets=[_follow_target("NVDA", target_notional=3750.0)])
        b = _follow_source("follow-b", targets=[_follow_target("NVDA", target_notional=2000.0)])
        account = _snap(100_000.0, {"NVDA": _Pos("NVDA", 10, 800.0, 8000.0)})

        composed = compose_targets(advisory=None, follows=[a, b], account_snapshot=account)

        assert len(composed) == 1
        ci = composed[0]
        assert ci.symbol == "NVDA"
        assert ci.action == "SELL"
        # NOT the sum-of-deltas -10250 (which isn't even a valid sell size
        # against an $8000 position) -- the correctly-netted $2250 trim.
        assert ci.target_notional == pytest.approx(2250.0, abs=0.01)
        assert ci.target_notional != pytest.approx(10250.0, abs=0.01)
        assert ci.strategy_id == "composed"
        assert {s["source_id"] for s in ci.sources} == {"follow-a", "follow-b"}

    def test_q2_example_numbers_fall_within_the_no_trade_band(self):
        """The plan's own illustrative numbers (A=$3750, B=$2000, held=$6000
        -> net=$5750, delta=-$250) happen to land inside Q4's band
        (max($1, 5%*$5750)=$287.50) -- the netting math is still correct
        (proven above with different numbers that clear the band); this
        pins that the band is honestly applied to the NET target, not
        bypassed for this illustrative case."""
        a = _follow_source("follow-a", targets=[_follow_target("NVDA", target_notional=3750.0)])
        b = _follow_source("follow-b", targets=[_follow_target("NVDA", target_notional=2000.0)])
        account = _snap(100_000.0, {"NVDA": _Pos("NVDA", 10, 600.0, 6000.0)})
        composed = compose_targets(advisory=None, follows=[a, b], account_snapshot=account)
        assert composed == []

    def test_two_follows_underweight_buys_the_combined_delta(self):
        a = _follow_source("follow-a", targets=[_follow_target("NVDA", target_notional=3000.0)])
        b = _follow_source("follow-b", targets=[_follow_target("NVDA", target_notional=2000.0)])
        account = _snap(100_000.0, {"NVDA": _Pos("NVDA", 10, 100.0, 1000.0)})

        composed = compose_targets(advisory=None, follows=[a, b], account_snapshot=account)

        assert len(composed) == 1
        ci = composed[0]
        assert ci.action == "BUY"
        # net_target = 5000, current = 1000 -> delta = 4000
        assert ci.target_notional == pytest.approx(4000.0, abs=0.01)


# ---------------------------------------------------------------------------
# Q4 — no-trade band scales with the NET target
# ---------------------------------------------------------------------------


class TestBandScalesWithNetTarget:
    def test_within_band_is_skipped(self):
        # net_target=200, current=195 -> |delta|=5, band=max(1, 0.05*200)=10 -> skip
        a = _follow_source("follow-a", targets=[_follow_target("AAPL", target_notional=200.0)])
        account = _snap(100_000.0, {"AAPL": _Pos("AAPL", 1, 195.0, 195.0)})
        composed = compose_targets(advisory=None, follows=[a], account_snapshot=account)
        assert composed == []

    def test_zero_net_target_uses_the_dollar_floor_never_suppressed(self):
        """net_target=0 (a full drop) -> band=max(1,0)=1 -- a 5% band must never
        suppress a genuine exit just because the base it's a percent of is 0."""
        a = _follow_source("follow-a", dropped=[{"symbol": "AAPL", "target_notional": 2.0}])
        account = _snap(100_000.0, {"AAPL": _Pos("AAPL", 1, 2.0, 2.0)})
        composed = compose_targets(advisory=None, follows=[a], account_snapshot=account)
        assert len(composed) == 1
        assert composed[0].action == "SELL"
        assert composed[0].target_notional == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Q7 — force-exit under the union, capped by source_claim
# ---------------------------------------------------------------------------


class TestSourceClaimCapTable:
    def test_trim_no_prior_capped_at_current_target(self):
        """prior=0 (no drop, first-time claim), target=3750, current=6000 ->
        claim=max(0,3750)=3750 -> sell=min(2250,3750)=2250 (uncapped in
        practice, matches today's single-source trim)."""
        a = _follow_source("follow-a", targets=[_follow_target("NVDA", target_notional=3750.0)])
        account = _snap(100_000.0, {"NVDA": _Pos("NVDA", 10, 600.0, 6000.0)})
        composed = compose_targets(advisory=None, follows=[a], account_snapshot=account)
        assert len(composed) == 1
        assert composed[0].action == "SELL"
        assert composed[0].target_notional == pytest.approx(2250.0, abs=0.01)

    def test_all_dropped_capped_at_attribution_leaves_operators_shares(self):
        """prior=3750 (dropped), target=0 (no longer claimed), current=6000 ->
        claim=3750 -> sell=min(6000,3750)=3750, leaving the operator's own
        $2250 untouched."""
        a = _follow_source("follow-a", dropped=[{"symbol": "NVDA", "target_notional": 3750.0}])
        account = _snap(100_000.0, {"NVDA": _Pos("NVDA", 10, 600.0, 6000.0)})
        composed = compose_targets(advisory=None, follows=[a], account_snapshot=account)
        assert len(composed) == 1
        ci = composed[0]
        assert ci.action == "SELL"
        assert ci.target_notional == pytest.approx(3750.0, abs=0.01)
        # $2250 of the $6000 position is left alone (not oversold).
        assert ci.target_notional < 6000.0

    def test_legacy_row_no_attribution_sells_nothing(self):
        """No source claims or previously claimed this symbol at all ->
        claim=0 -> nothing sold, matches today's no-attribution rule."""
        account = _snap(100_000.0, {"TSLA": _Pos("TSLA", 10, 600.0, 6000.0)})
        composed = compose_targets(advisory=None, follows=[], account_snapshot=account)
        assert composed == []


# ---------------------------------------------------------------------------
# Q3 / advisory-wins — the product decision confirmed with the operator
# ---------------------------------------------------------------------------


class TestAdvisoryAlwaysWinsOutright:
    def test_advisory_sell_beats_follow_buy_wash_trade_is_one_row(self):
        """advisory SELL (full exit) + a follow wanting to BUY the same
        symbol -> exactly ONE emitted intent (advisory's), never two rows —
        the wash-trade case the composer exists to prevent."""
        advisory = AdvisorySourceClaims(targets=[
            _advisory_target("NVDA", "SELL", conviction=0.9, pct=0.0, rationale="momentum broke down")
        ])
        follow = _follow_source("follow-a", targets=[_follow_target("NVDA", target_notional=3750.0, rationale="ranked #1")])
        account = _snap(100_000.0, {"NVDA": _Pos("NVDA", 10, 600.0, 6000.0)})

        composed = compose_targets(advisory=advisory, follows=[follow], account_snapshot=account)

        assert len(composed) == 1
        ci = composed[0]
        assert ci.action == "SELL"
        assert ci.strategy_id == "advisory"
        assert ci.rationale == "momentum broke down"
        assert len(ci.overridden) == 1
        assert ci.overridden[0]["source_id"] == "follow-a"
        assert ci.overridden[0]["rationale"] == "ranked #1"

    def test_advisory_buy_also_wins_over_a_follow_wanting_the_same_symbol(self):
        """Same-direction overlap (both want exposure) still resolves to
        advisory's own number, never additive -- confirmed decision: advisory
        always wins outright when present, regardless of direction, to avoid
        stacking two independent signals into an oversized position."""
        advisory = AdvisorySourceClaims(targets=[
            _advisory_target("MSFT", "BUY", conviction=0.9, pct=0.05, rationale="strong setup")
        ])
        follow = _follow_source("follow-a", targets=[_follow_target("MSFT", target_notional=5000.0, rationale="ranked #2")])
        account = _snap(100_000.0, {})

        composed = compose_targets(advisory=advisory, follows=[follow], account_snapshot=account)

        assert len(composed) == 1
        ci = composed[0]
        assert ci.action == "BUY"
        assert ci.strategy_id == "advisory"
        assert ci.suggested_position_pct == pytest.approx(0.05)
        assert ci.rationale == "strong setup"
        assert len(ci.overridden) == 1
        assert ci.overridden[0]["source_id"] == "follow-a"

    def test_low_conviction_advisory_rec_does_not_suppress_a_follow(self):
        """An advisory rec that would itself be filtered out by
        queue_builder.CONFIG['min_conviction'] must not count as "advisory
        has an opinion" -- the follow proceeds normally, netted alone."""
        advisory = AdvisorySourceClaims(targets=[
            _advisory_target("NVDA", "BUY", conviction=0.1, pct=0.05)  # well below 0.85
        ])
        follow = _follow_source("follow-a", targets=[_follow_target("NVDA", target_notional=2000.0)])
        account = _snap(100_000.0, {})

        composed = compose_targets(advisory=advisory, follows=[follow], account_snapshot=account)

        assert len(composed) == 1
        ci = composed[0]
        assert ci.strategy_id == "follow-a"  # the follow's own claim, unblocked
        assert ci.action == "BUY"

    def test_advisory_only_no_follow_claim_no_overridden(self):
        advisory = AdvisorySourceClaims(targets=[_advisory_target("NVDA", "BUY", pct=0.05)])
        account = _snap(100_000.0, {})
        composed = compose_targets(advisory=advisory, follows=[], account_snapshot=account)
        assert len(composed) == 1
        assert composed[0].overridden == []


# ---------------------------------------------------------------------------
# Q5 — strategy_id / sources: single-owner byte-identity, composed = "composed"
# ---------------------------------------------------------------------------


class TestStrategyIdAndSources:
    def test_single_follow_owner_gets_its_own_strategy_id(self):
        a = _follow_source(follow_source_id("trend-following"),
                            targets=[_follow_target("NVDA", target_notional=3000.0)])
        account = _snap(100_000.0, {})
        composed = compose_targets(advisory=None, follows=[a], account_snapshot=account)
        assert composed[0].strategy_id == "follow-trend-following"
        assert composed[0].sources == [{"source_id": "follow-trend-following", "target_notional": 3000.0}]

    def test_multi_follow_owner_gets_composed_strategy_id(self):
        a = _follow_source("follow-a", targets=[_follow_target("NVDA", target_notional=3000.0)])
        b = _follow_source("follow-b", targets=[_follow_target("NVDA", target_notional=1000.0)])
        account = _snap(100_000.0, {})
        composed = compose_targets(advisory=None, follows=[a, b], account_snapshot=account)
        assert composed[0].strategy_id == "composed"
        assert {s["source_id"] for s in composed[0].sources} == {"follow-a", "follow-b"}

    def test_single_owner_client_order_id_is_byte_identical_to_direct_build(self):
        """A symbol with exactly one owning source must produce the SAME
        client_order_id a direct (non-composed) build_execution_queue call
        for that owner alone would have produced -- the safety net for the
        whole strategy_id override."""
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
        composed = compose_targets(advisory=advisory, follows=[], account_snapshot=account)
        composed_run = compose._ComposedRunResult(recommendations=composed, snapshot=account)
        composed_payload = build_execution_queue(
            composed_run, mode="review", config={"strategy_id": "composed", "min_conviction": 0.0}, now=_NOW,
        )

        assert direct_payload["intents"][0]["client_order_id"] == composed_payload["intents"][0]["client_order_id"]


# ---------------------------------------------------------------------------
# Single-source byte-identity (the safety net for the whole refactor)
# ---------------------------------------------------------------------------


class TestSingleSourceByteIdentity:
    def test_compose_one_follow_matches_build_follow_intents(self):
        """compose_targets([one follow source]) must reproduce EXACTLY what
        pilots.mirror.build_follow_intents (today's single-pilot path, which
        is itself now a thin wrapper over this same engine) returns."""
        pilot = get_pilot(_PILOT_ID)
        snapshot = load_snapshot(str(FIXTURE))
        account = _snap(250_000.0, {})

        legacy = build_follow_intents(pilot, 10_000.0, account, snapshot=snapshot)

        from pilots.mirror import build_follow_targets
        targets = build_follow_targets(pilot, 10_000.0, snapshot)
        source = FollowSourceClaims(source_id=follow_source_id(_PILOT_ID), targets=targets)
        composed = compose_targets(advisory=None, follows=[source], account_snapshot=account)

        assert len(legacy) == len(composed)
        legacy_by_symbol = {i.symbol: i for i in legacy}
        for ci in composed:
            li = legacy_by_symbol[ci.symbol]
            assert li.action == ci.action
            assert li.target_notional == pytest.approx(ci.target_notional, abs=0.01)
            assert li.conviction == pytest.approx(ci.conviction, abs=1e-6)
            assert li.rationale == ci.rationale


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
        r = read_source("follow-nobody", output_dir=tmp_path)
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

    def test_write_follow_source_computes_dropped_targets(self, tmp_path):
        pilot = get_pilot(_PILOT_ID)
        snapshot = load_snapshot(str(FIXTURE))
        prior = [{"symbol": "ZZZZ_NOT_A_REAL_HOLDING", "weight": 0.1, "target_notional": 500.0}]
        write_follow_source(pilot, 10_000.0, snapshot, prior_mirrored=prior, output_dir=tmp_path, now=_NOW)
        r = read_source(follow_source_id(_PILOT_ID), output_dir=tmp_path)
        assert r.present is True
        assert any(d["symbol"] == "ZZZZ_NOT_A_REAL_HOLDING" for d in r.dropped_targets)
        assert all(t["symbol"] != "ZZZZ_NOT_A_REAL_HOLDING" for t in r.targets)


# ---------------------------------------------------------------------------
# compose_and_emit — dead-letter posture (corrupt/stale -> refuse the WHOLE
# compose; missing -> skip that one source and proceed)
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

    def test_corrupt_follow_source_writes_nothing(self, tmp_path, monkeypatch):
        from settings import settings
        from pilots.follows_store import FollowsStore
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        write_advisory_source([], output_dir=tmp_path, now=_NOW)
        FollowsStore(path=str(tmp_path / "follows.json")).upsert(_PILOT_ID, 5000.0)
        d = tmp_path / "queue_sources"
        (d / f"{follow_source_id(_PILOT_ID)}.json").write_text("{{not json", encoding="utf-8")

        account = _snap(100_000.0, {})
        result = compose_and_emit(account, output_dir=tmp_path, now=_NOW)

        assert result is None
        assert not (tmp_path / "execution_queue.json").exists()

    def test_missing_follow_source_is_skipped_advisory_still_composes(self, tmp_path, monkeypatch):
        """A Pilot that's active (upserted) but never explicitly followed via
        plan_follow yet has no source file -- MISSING, not corrupt -- compose
        proceeds using whatever else is present."""
        from settings import settings
        from pilots.follows_store import FollowsStore
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        write_advisory_source(
            [type("R", (), {"symbol": "NVDA", "action": "BUY", "conviction": 0.9,
                            "suggested_position_pct": 0.05, "strategy": "advisory",
                            "rationale": "why"})()],
            output_dir=tmp_path, now=_NOW,
        )
        FollowsStore(path=str(tmp_path / "follows.json")).upsert(_PILOT_ID, 5000.0)
        # No follow-trend-following.json written -- legitimately missing.

        account = _snap(100_000.0, {})
        result = compose_and_emit(account, output_dir=tmp_path, now=_NOW)

        assert result is not None
        payload = json.loads(result.read_text(encoding="utf-8"))
        assert payload["intents"][0]["symbol"] == "NVDA"

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

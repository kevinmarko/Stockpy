"""Unit tests for ``pilots/mirror.py`` — the gated Pilot auto-mirror.

Fully offline: the committed ``tests/fixtures/state_snapshot.json`` supplies the
Pilot holdings, a tiny fake ``AccountSnapshot`` supplies ``total_equity``, and a
``tmp_path`` stands in for ``OUTPUT_DIR`` so the execution-queue file (when
written at all) lands in a scratch dir. No network, no broker, no heavy engines.

Coverage
--------
* proportional ``target_notional`` per holding sums to ~amount (pre-clamp);
* the per-order notional clamp applies;
* ``plan_follow`` writes ``output/execution_queue.json`` ONLY in review/live mode
  and writes NOTHING in ``off`` mode (preview still returned);
* ``allow_place`` is ``False`` when not live, and when the kill switch is active;
* an AST self-check that ``pilots/mirror.py`` defines no forbidden order-symbol
  names (the same guard as ``tests/test_pipeline_smoke.py::TestNoOrderFunctions``).
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from pilots.catalog import get_pilot
from pilots.mirror import (
    FOLLOW_MIN_CONVICTION,
    FollowIntent,
    build_follow_intents,
    plan_follow,
)
from pilots.scoring import load_snapshot, pilot_holdings

# Captured at module-import time, before any per-test monkeypatching of
# sizing.kelly.kelly_sizing_for_strategy can occur -- a `from sizing.kelly
# import kelly_sizing_for_strategy` done INSIDE a test body would instead
# pick up whatever the autouse `_kelly_ceiling_unbounded` fixture already
# patched it to (fixture setup runs before the test body).
from sizing.kelly import kelly_sizing_for_strategy as _REAL_KELLY_SIZING_FOR_STRATEGY
from sizing.kelly import (
    estimate_win_rate_and_payoff_per_strategy as _REAL_ESTIMATE_WIN_RATE_AND_PAYOFF,
)

FIXTURE = Path(__file__).parent / "fixtures" / "state_snapshot.json"
MIRROR_SRC = Path(__file__).parent.parent / "pilots" / "mirror.py"

# The Pilot used across the proportionality tests — a single-module momentum
# blend that yields several positive-blend holdings in the fixture.
_PILOT_ID = "trend-following"
_AMOUNT = 10_000.0


class _FakePosition:
    """Minimal stand-in for ``data.robinhood_portfolio.PortfolioPosition`` — the
    fields ``pilots.mirror._current_market_value`` and ``queue_builder`` read."""

    def __init__(self, symbol: str, quantity: float, current_price: float,
                 market_value=None) -> None:
        self.symbol = symbol
        self.quantity = quantity
        self.current_price = current_price
        self.market_value = (
            market_value if market_value is not None else quantity * current_price
        )
        self.average_cost = current_price
        self.unrealized_pl = 0.0


class _FakeSnapshot:
    """Minimal stand-in for ``data.robinhood_portfolio.AccountSnapshot``."""

    def __init__(self, total_equity: float, positions=None) -> None:
        self.total_equity = total_equity
        self.buying_power = total_equity
        self.positions = positions or {}


class _PassingGate:
    """A ``PreTradeRiskGate`` stub whose ``run_all`` always passes — lets a live
    ``allow_place`` test be deterministic regardless of wall-clock market hours."""

    def run_all(self, intent, context):  # noqa: D401,ANN001 - test stub
        return True, []


class _InactiveKillSwitch:
    def is_active(self) -> bool:
        return False


@pytest.fixture()
def snapshot() -> dict:
    snap = load_snapshot(str(FIXTURE))
    assert snap is not None, "committed fixture snapshot must load"
    return snap


@pytest.fixture()
def pilot():
    p = get_pilot(_PILOT_ID)
    assert p is not None
    return p


@pytest.fixture()
def account() -> _FakeSnapshot:
    return _FakeSnapshot(total_equity=250_000.0)


@pytest.fixture(autouse=True)
def _no_cap(monkeypatch):
    """Default every test to an UNSET per-order cap unless it sets one itself."""
    from settings import settings
    monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0, raising=False)


@pytest.fixture(autouse=True)
def _kelly_ceiling_unbounded(monkeypatch):
    """Neutralize ``plan_follow``'s Kelly-ceiling sizing block for every test in
    this file except the dedicated Kelly-sizing tests (``TestKellyCeilingSizing``),
    which restore the real functions locally.

    ``plan_follow`` sizes each follow against ``strategy_id=f"Follow:{pilot.id}"``
    -- a strategy_id that, by construction, is brand-new (zero closed trades) in
    every test environment reachable from this suite, so
    ``plan_follow`` always takes its cold-start branch and never even reaches
    ``kelly_sizing_for_strategy`` (it calls
    ``estimate_win_rate_and_payoff_per_strategy`` first to decide which branch
    to take -- see the cold-start-bypass comment in ``pilots/mirror.py``).
    Patch that first-decision call to report a fully warm strategy so the
    (also-stubbed) unbounded ``kelly_sizing_for_strategy`` below is what
    actually runs, keeping every test in this file that predates Kelly sizing
    (proportional-split / retention / alert / mode behavior) unaffected by it.
    """
    import sizing.kelly as kelly_mod
    monkeypatch.setattr(
        kelly_mod, "estimate_win_rate_and_payoff_per_strategy",
        lambda *a, **k: (0.6, 1.5, 999),
        raising=True,
    )
    monkeypatch.setattr(
        kelly_mod, "kelly_sizing_for_strategy",
        lambda *a, **k: (1.0, "test_stub_unbounded"),
        raising=True,
    )


# ---------------------------------------------------------------------------
# build_follow_intents — proportional target notional
# ---------------------------------------------------------------------------

class TestBuildFollowIntents:
    def test_target_notional_sums_to_amount_preclamp(self, pilot, account, snapshot):
        intents = build_follow_intents(pilot, _AMOUNT, account, snapshot=snapshot)
        assert intents, "trend-following must yield holdings from the fixture"
        total = sum(i.target_notional for i in intents)
        assert total == pytest.approx(_AMOUNT, rel=1e-3)

    def test_target_notional_is_proportional_to_weight(self, pilot, account, snapshot):
        holdings = {h["symbol"]: h["weight"] for h in pilot_holdings(pilot, snapshot)}
        intents = build_follow_intents(pilot, _AMOUNT, account, snapshot=snapshot)
        assert {i.symbol for i in intents} == set(holdings)
        for i in intents:
            assert i.target_notional == pytest.approx(_AMOUNT * holdings[i.symbol], rel=1e-3)

    def test_pct_reproduces_target_notional(self, pilot, account, snapshot):
        """suggested_position_pct * equity must reproduce target_notional so the
        queue builder's own ``notional = equity * pct`` math is faithful."""
        intents = build_follow_intents(pilot, _AMOUNT, account, snapshot=snapshot)
        for i in intents:
            # target_notional is cent-rounded; pct is unrounded -> compare to a cent.
            assert i.suggested_position_pct * account.total_equity == pytest.approx(
                i.target_notional, abs=0.01
            )

    def test_all_intents_are_buys(self, pilot, account, snapshot):
        intents = build_follow_intents(pilot, _AMOUNT, account, snapshot=snapshot)
        assert all(i.action == "BUY" for i in intents)
        assert all(i.strategy == f"Follow:{pilot.id}" for i in intents)

    def test_rationale_is_an_honest_ranking_not_a_bare_label(self, pilot, account, snapshot):
        """Bug D: a follow intent's rationale must be a per-name ranking built
        from real numbers, not the strategy label (which lives on `.strategy`)."""
        intents = build_follow_intents(pilot, _AMOUNT, account, snapshot=snapshot)
        assert intents
        for i in intents:
            # The old behavior set rationale == the bare label; it must not.
            assert i.rationale != i.strategy
            assert "ranked #" in i.rationale
            assert "target weight" in i.rationale
            # Reads as a ranking, never a fabricated discretionary thesis.
            assert "believe" not in i.rationale.lower()
            assert "think" not in i.rationale.lower()

    def test_conviction_equals_normalized_weight(self, pilot, account, snapshot):
        """Decision D3: honest per-name conviction == the Pilot's target weight."""
        intents = build_follow_intents(pilot, _AMOUNT, account, snapshot=snapshot)
        for i in intents:
            assert i.conviction == pytest.approx(i.weight)

    def test_per_order_clamp_applies(self, pilot, account, snapshot, monkeypatch):
        from settings import settings
        cap = 1_000.0
        monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", cap, raising=False)
        intents = build_follow_intents(pilot, _AMOUNT, account, snapshot=snapshot)
        assert intents
        assert all(i.target_notional <= cap + 1e-9 for i in intents)
        # At least one name would have exceeded the cap pre-clamp (NVDA ~3750).
        assert any(i.target_notional == pytest.approx(cap) for i in intents)

    # ---- dead-letter / degenerate inputs ---------------------------------

    def test_non_positive_amount_returns_empty(self, pilot, account, snapshot):
        assert build_follow_intents(pilot, 0.0, account, snapshot=snapshot) == []
        assert build_follow_intents(pilot, -5.0, account, snapshot=snapshot) == []

    def test_zero_equity_returns_empty(self, pilot, snapshot):
        assert build_follow_intents(pilot, _AMOUNT, _FakeSnapshot(0.0), snapshot=snapshot) == []

    def test_no_snapshot_returns_empty(self, pilot, account):
        assert build_follow_intents(pilot, _AMOUNT, account, snapshot={}) == []

    def test_never_raises_on_garbage(self, pilot):
        # Missing total_equity attribute entirely -> empty, not a crash.
        assert build_follow_intents(pilot, _AMOUNT, object(), snapshot={"signals": []}) == []


# ---------------------------------------------------------------------------
# plan_follow — gated queue emission
# ---------------------------------------------------------------------------

class TestPlanFollow:
    def test_off_mode_writes_nothing_but_previews(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "off", raising=False)
        result = plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)
        assert result["mode"] == "off"
        assert result["queue_written"] is False
        assert result["planned_intents"], "preview intents returned even in off mode"
        assert not (tmp_path / "execution_queue.json").exists()

    def test_review_mode_writes_queue(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        result = plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)
        assert result["mode"] == "review"
        assert result["queue_written"] is True

        queue_path = tmp_path / "execution_queue.json"
        assert queue_path.exists()
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        assert payload["mode"] == "review"
        assert payload["n_intents"] == len(result["planned_intents"])

        # Not live => nothing is placeable.
        assert all(i["allow_place"] is False for i in payload["intents"])
        assert payload["n_placeable"] == 0

        # Queue target_notional reproduces the proportional build.
        built = {i["symbol"]: i["target_notional"] for i in result["planned_intents"]}
        for qi in payload["intents"]:
            assert qi["action"] == "BUY"
            assert qi["target_notional"] == pytest.approx(built[qi["symbol"]], rel=1e-3)

    def test_review_mode_respects_kill_switch(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        from settings import settings
        import execution.queue_builder as qb

        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        class _ActiveKillSwitch:
            def is_active(self) -> bool:
                return True

        monkeypatch.setattr(qb, "GlobalKillSwitch", _ActiveKillSwitch)

        result = plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)
        payload = json.loads((tmp_path / "execution_queue.json").read_text(encoding="utf-8"))
        assert payload["kill_switch_active"] is True
        assert all(i["allow_place"] is False for i in payload["intents"])

    def test_config_passes_low_min_conviction(self):
        """Sanity: the follow floor is at/below every possible normalized weight
        so no proportional holding is dropped by the conviction gate."""
        assert FOLLOW_MIN_CONVICTION == 0.0

    def test_empty_holdings_returns_preview_only(self, account, tmp_path, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        pilot = get_pilot(_PILOT_ID)
        # A snapshot with no signals -> no holdings -> nothing to write.
        result = plan_follow(pilot, _AMOUNT, account, snapshot={"signals": []}, output_dir=tmp_path)
        assert result["planned_intents"] == []
        assert result["queue_written"] is False
        assert not (tmp_path / "execution_queue.json").exists()


# ---------------------------------------------------------------------------
# Part C — bidirectional rebalance (BUY to add, SELL to trim)
# ---------------------------------------------------------------------------

class TestBidirectionalRebalance:
    """The follow is a rebalance-to-target: net off the follower's current market
    value per name and size the order to the delta (BUY under, SELL over, skip
    within the no-trade band). Fixture ``trend-following`` targets at $10k:
    NVDA 3750 / AAPL 2500 / MSFT 1667 / JPM 1250 / XOM 833.
    """

    def test_underweight_held_name_buys_only_the_delta(self, pilot, snapshot):
        # MSFT target 1667, already hold $500 -> BUY ~1167 (not the full target).
        acct = _FakeSnapshot(250_000.0, positions={
            "MSFT": _FakePosition("MSFT", quantity=1.0, current_price=500.0),
        })
        intents = {i.symbol: i for i in build_follow_intents(pilot, _AMOUNT, acct, snapshot=snapshot)}
        assert "MSFT" in intents
        msft = intents["MSFT"]
        assert msft.action == "BUY"
        assert msft.target_notional == pytest.approx(1667.0 - 500.0, rel=1e-2)
        # unheld names still buy their full target
        assert intents["JPM"].action == "BUY"
        assert intents["JPM"].target_notional == pytest.approx(1250.0, rel=1e-2)

    def test_overweight_held_name_trims_with_a_sell(self, pilot, snapshot):
        # NVDA target 3750, hold $6000 -> SELL trim of ~2250.
        acct = _FakeSnapshot(250_000.0, positions={
            "NVDA": _FakePosition("NVDA", quantity=1.0, current_price=6000.0),
        })
        intents = {i.symbol: i for i in build_follow_intents(pilot, _AMOUNT, acct, snapshot=snapshot)}
        assert "NVDA" in intents
        nvda = intents["NVDA"]
        assert nvda.action == "SELL"
        assert nvda.target_notional == pytest.approx(6000.0 - 3750.0, rel=1e-2)
        # pct reproduces the trim notional through the builder's equity*pct math
        assert nvda.suggested_position_pct * acct.total_equity == pytest.approx(
            nvda.target_notional, abs=0.01
        )

    def test_within_band_is_skipped(self, pilot, snapshot):
        # AAPL target 2500, hold $2450 -> |delta|=50 < band(=max(1,125)) -> no order.
        acct = _FakeSnapshot(250_000.0, positions={
            "AAPL": _FakePosition("AAPL", quantity=1.0, current_price=2450.0),
        })
        symbols = {i.symbol for i in build_follow_intents(pilot, _AMOUNT, acct, snapshot=snapshot)}
        assert "AAPL" not in symbols
        # other names still rebalance
        assert "NVDA" in symbols

    def test_sell_trim_is_clamped_by_per_order_cap(self, pilot, snapshot, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 1_000.0, raising=False)
        acct = _FakeSnapshot(250_000.0, positions={
            "NVDA": _FakePosition("NVDA", quantity=1.0, current_price=6000.0),
        })
        intents = {i.symbol: i for i in build_follow_intents(pilot, _AMOUNT, acct, snapshot=snapshot)}
        assert intents["NVDA"].action == "SELL"
        assert intents["NVDA"].target_notional == pytest.approx(1_000.0)

    def test_names_outside_pilot_holdings_are_left_untouched(self, pilot, snapshot):
        # A position the follower holds that the Pilot does NOT hold produces no
        # intent (rebalance scope is the Pilot's holding set only).
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        symbols = {i.symbol for i in build_follow_intents(pilot, _AMOUNT, acct, snapshot=snapshot)}
        assert "TSLA" not in symbols

    def test_review_queue_emits_partial_trim_sell(
        self, pilot, snapshot, tmp_path, monkeypatch
    ):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        acct = _FakeSnapshot(250_000.0, positions={
            "NVDA": _FakePosition("NVDA", quantity=1.0, current_price=6000.0),
        })
        plan_follow(pilot, _AMOUNT, acct, snapshot=snapshot, output_dir=tmp_path)
        payload = json.loads((tmp_path / "execution_queue.json").read_text(encoding="utf-8"))
        by_symbol = {i["symbol"]: i for i in payload["intents"]}
        assert by_symbol["NVDA"]["action"] == "SELL"
        # Partial trim: notional-sized, qty resolved downstream (null), not a full exit.
        assert by_symbol["NVDA"]["qty"] is None
        assert by_symbol["NVDA"]["target_notional"] == pytest.approx(2250.0, rel=1e-2)
        # review mode: nothing placeable
        assert by_symbol["NVDA"]["allow_place"] is False


# ---------------------------------------------------------------------------
# Part D — force-exit of names the Pilot has fully dropped (per-follow attribution)
# ---------------------------------------------------------------------------

class TestForceExitDroppedNames:
    """A name the follow previously mirrored that the Pilot has since dropped is
    force-sold — sized to the FOLLOW-ATTRIBUTED quantity only, capped at what is
    actually held, never the follower's whole position. Requires a prior mirrored
    set (per-follow attribution); with none, the pre-existing behavior holds and
    nothing is force-sold.
    """

    def _prior(self, **overrides):
        # TSLA is NOT a trend-following holding in the fixture -> "dropped".
        row = {"symbol": "TSLA", "weight": 0.2, "target_notional": 2000.0}
        row.update(overrides)
        return [row]

    def test_dropped_name_is_force_sold_at_attributed_notional(self, pilot, snapshot):
        # Follower holds TSLA worth $2500; attribution is $2000 -> SELL $2000.
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        intents = build_follow_intents(
            pilot, _AMOUNT, acct, snapshot=snapshot, prior_mirrored=self._prior()
        )
        by_symbol = {i.symbol: i for i in intents}
        assert "TSLA" in by_symbol
        tsla = by_symbol["TSLA"]
        assert tsla.action == "SELL"
        # Attributed ($2000) < held ($2500) -> sell exactly the attributed notional.
        assert tsla.target_notional == pytest.approx(2000.0)
        # pct reproduces the notional through the builder's equity*pct math.
        assert tsla.suggested_position_pct * acct.total_equity == pytest.approx(
            tsla.target_notional, abs=0.01
        )
        # Exactly one SELL for the dropped name; the still-held pilot names are BUYs.
        sells = [i for i in intents if i.action == "SELL"]
        assert [i.symbol for i in sells] == ["TSLA"]

    def test_force_sell_is_capped_at_currently_held_value(self, pilot, snapshot):
        # Attribution $2000 but follower only holds $1500 -> cap at held ($1500),
        # never oversell / never touch shares beyond the held amount.
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=6.0, current_price=250.0),
        })
        intents = {i.symbol: i for i in build_follow_intents(
            pilot, _AMOUNT, acct, snapshot=snapshot, prior_mirrored=self._prior()
        )}
        assert intents["TSLA"].action == "SELL"
        assert intents["TSLA"].target_notional == pytest.approx(1500.0)

    def test_force_sell_clamped_by_per_order_cap(self, pilot, snapshot, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 1_000.0, raising=False)
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        intents = {i.symbol: i for i in build_follow_intents(
            pilot, _AMOUNT, acct, snapshot=snapshot, prior_mirrored=self._prior()
        )}
        assert intents["TSLA"].action == "SELL"
        assert intents["TSLA"].target_notional == pytest.approx(1_000.0)

    def test_no_prior_mirrored_means_no_force_exit(self, pilot, snapshot):
        # Same held TSLA, but NO attribution passed -> honest fallback: untouched.
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        symbols = {i.symbol for i in build_follow_intents(
            pilot, _AMOUNT, acct, snapshot=snapshot, prior_mirrored=None
        )}
        assert "TSLA" not in symbols

    def test_still_held_pilot_name_is_not_force_exited(self, pilot, snapshot):
        # NVDA is both a current Pilot holding AND in the prior mirrored set:
        # it must be rebalanced (BUY the delta), never force-sold.
        prior = [
            {"symbol": "TSLA", "weight": 0.2, "target_notional": 2000.0},
            {"symbol": "NVDA", "weight": 0.375, "target_notional": 3750.0},
        ]
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        intents = {i.symbol: i for i in build_follow_intents(
            pilot, _AMOUNT, acct, snapshot=snapshot, prior_mirrored=prior
        )}
        assert intents["NVDA"].action == "BUY"  # rebalanced, not exited
        assert intents["TSLA"].action == "SELL"  # dropped -> force-exit

    def test_dropped_name_not_actually_held_yields_no_intent(self, pilot, snapshot):
        # Attribution exists but the follower no longer holds the name ->
        # nothing to sell (no fabricated position).
        acct = _FakeSnapshot(250_000.0, positions={})
        symbols = {i.symbol for i in build_follow_intents(
            pilot, _AMOUNT, acct, snapshot=snapshot, prior_mirrored=self._prior()
        )}
        assert "TSLA" not in symbols

    def test_attribution_without_target_notional_is_skipped(self, pilot, snapshot):
        # A legacy mirrored row lacking target_notional gives no usable
        # attribution -> no fabricated exit size, no force-sell.
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        prior = [{"symbol": "TSLA", "weight": 0.2}]  # no target_notional
        symbols = {i.symbol for i in build_follow_intents(
            pilot, _AMOUNT, acct, snapshot=snapshot, prior_mirrored=prior
        )}
        assert "TSLA" not in symbols

    def test_full_drop_of_all_names_still_force_exits(self, pilot):
        # Pilot yields NO current holdings (empty snapshot), but the follow
        # previously mirrored a name the follower still holds -> it is exited even
        # though the normal rebalance produces nothing.
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        intents = build_follow_intents(
            pilot, _AMOUNT, acct, snapshot={"signals": []},
            prior_mirrored=self._prior(),
        )
        assert [i.symbol for i in intents] == ["TSLA"]
        assert intents[0].action == "SELL"


class TestForceExitEndToEnd:
    """plan_follow wires attribution end-to-end: it loads the follow's prior
    mirrored set from the store, force-exits a dropped name, and persists the
    Pilot's current holdings back — RETAINING any dropped name the follower
    still holds market value in (Bug A fix), so the exit keeps being
    re-derivable on every subsequent call until it is actually gone from the
    account, regardless of mode or whether a queue was ever written."""

    def test_dropped_name_still_held_is_retained_and_reexits_on_next_call(
        self, pilot, snapshot, tmp_path, monkeypatch
    ):
        """The exact Bug A regression: in `off` mode (the default) NOTHING
        is ever written to execution_queue.json, so gating retention on
        `queue_written` (or on mode at all) meant a force-exit computed on
        one call was shown only in that call's ephemeral preview and then
        immediately forgotten -- the docstring's "emitted once, when the
        drop is first observed" was in fact emitted to nowhere durable. The
        held-ness axis fixes this: the still-held name survives the persist
        step and is force-exited again on the very next call."""
        from settings import settings
        from pilots.follows_store import FollowsStore
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "off", raising=False)

        store = FollowsStore(path=str(tmp_path / "follows.json"))
        store.upsert(_PILOT_ID, _AMOUNT)
        store.set_mirrored(_PILOT_ID, [
            {"symbol": "TSLA", "weight": 0.2, "target_notional": 2000.0},
        ])

        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })

        # Call 1, off mode: nothing written, but the force-exit IS computed
        # in the ephemeral preview.
        result1 = plan_follow(pilot, _AMOUNT, acct, snapshot=snapshot, output_dir=tmp_path)
        assert result1["queue_written"] is False
        assert not (tmp_path / "execution_queue.json").exists()
        assert "TSLA" in {i["symbol"] for i in result1["planned_intents"]}

        after_call1 = FollowsStore(path=str(tmp_path / "follows.json")).get_mirrored(_PILOT_ID)
        after_call1_symbols = {m["symbol"] for m in after_call1}
        assert "TSLA" in after_call1_symbols, (
            "a dropped-but-still-held name must be RETAINED in the "
            "persisted mirrored set, not silently discarded on the first "
            "persist -- this is the exact Bug A regression"
        )
        # Current Pilot holdings still advance unconditionally alongside it.
        assert after_call1_symbols - {"TSLA"}
        # The retained row is carried forward UNCHANGED (still this follow's
        # original claim), never recomputed.
        tsla_row = next(m for m in after_call1 if m["symbol"] == "TSLA")
        assert tsla_row["target_notional"] == pytest.approx(2000.0)
        assert tsla_row["weight"] == pytest.approx(0.2)

        # Call 2, still held: the exit is re-derivable because attribution
        # survived. Switch to review mode to see it actually land in a queue.
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        plan_follow(pilot, _AMOUNT, acct, snapshot=snapshot, output_dir=tmp_path)
        payload = json.loads((tmp_path / "execution_queue.json").read_text(encoding="utf-8"))
        by_symbol = {i["symbol"]: i for i in payload["intents"]}
        assert by_symbol["TSLA"]["action"] == "SELL"
        assert by_symbol["TSLA"]["qty"] is None
        assert by_symbol["TSLA"]["target_notional"] == pytest.approx(2000.0, rel=1e-2)
        assert by_symbol["TSLA"]["allow_place"] is False  # review mode: never places

        # Still retained after call 2 -- still held.
        after_call2 = FollowsStore(path=str(tmp_path / "follows.json")).get_mirrored(_PILOT_ID)
        assert "TSLA" in {m["symbol"] for m in after_call2}

    def test_review_mode_queue_written_does_not_change_retention(
        self, pilot, snapshot, tmp_path, monkeypatch
    ):
        """queue_written=True in review mode does not mean the exit was
        acted on -- the robinhood-execution skill contractually never
        places from a review-mode queue. Retention must not key off mode or
        queue_written at all, only held-ness -- so this behaves identically
        to the off-mode case above."""
        from settings import settings
        from pilots.follows_store import FollowsStore
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        store = FollowsStore(path=str(tmp_path / "follows.json"))
        store.upsert(_PILOT_ID, _AMOUNT)
        store.set_mirrored(_PILOT_ID, [
            {"symbol": "TSLA", "weight": 0.2, "target_notional": 2000.0},
        ])
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        result = plan_follow(pilot, _AMOUNT, acct, snapshot=snapshot, output_dir=tmp_path)
        assert result["queue_written"] is True

        updated = FollowsStore(path=str(tmp_path / "follows.json")).get_mirrored(_PILOT_ID)
        assert "TSLA" in {m["symbol"] for m in updated}

    def test_dropped_name_once_no_longer_held_drops_out_of_retained_set(
        self, pilot, snapshot, tmp_path, monkeypatch
    ):
        """Once the follower's held market value in the dropped name reaches
        zero -- however that exit actually happened -- it stops being
        retained, so it is not re-prompted forever."""
        from settings import settings
        from pilots.follows_store import FollowsStore
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        store = FollowsStore(path=str(tmp_path / "follows.json"))
        store.upsert(_PILOT_ID, _AMOUNT)
        store.set_mirrored(_PILOT_ID, [
            {"symbol": "TSLA", "weight": 0.2, "target_notional": 2000.0},
        ])

        # Follower no longer holds TSLA at all.
        acct = _FakeSnapshot(250_000.0, positions={})
        plan_follow(pilot, _AMOUNT, acct, snapshot=snapshot, output_dir=tmp_path)

        updated = FollowsStore(path=str(tmp_path / "follows.json")).get_mirrored(_PILOT_ID)
        assert "TSLA" not in {m["symbol"] for m in updated}

    def test_first_follow_without_prior_set_does_not_force_exit(
        self, pilot, snapshot, account, tmp_path, monkeypatch
    ):
        from settings import settings
        from pilots.follows_store import FollowsStore
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)

        # A never-before-followed pilot: no mirrored set exists.
        acct = _FakeSnapshot(250_000.0, positions={
            "TSLA": _FakePosition("TSLA", quantity=10.0, current_price=250.0),
        })
        plan_follow(pilot, _AMOUNT, acct, snapshot=snapshot, output_dir=tmp_path)

        payload = json.loads((tmp_path / "execution_queue.json").read_text(encoding="utf-8"))
        assert "TSLA" not in {i["symbol"] for i in payload["intents"]}
        # But the current holdings ARE now persisted for next time.
        persisted = FollowsStore(path=str(tmp_path / "follows.json")).get_mirrored(_PILOT_ID)
        assert persisted  # attribution now seeded


# ---------------------------------------------------------------------------
# Part B — conviction floor is load-bearing + live-mode placeability
# ---------------------------------------------------------------------------

class TestConvictionFloorAndLiveMode:
    def test_low_conviction_holdings_all_survive_the_gate(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        """Regression guard for the D3 floor: every follow intent's conviction is
        the (sub-1.0) normalized weight, well below the builder's DEFAULT 0.85
        min_conviction. plan_follow injects FOLLOW_MIN_CONVICTION=0.0, so NONE are
        dropped. If that injection were ever removed, this test fails."""
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "review", raising=False)
        preview = build_follow_intents(pilot, _AMOUNT, account, snapshot=snapshot)
        # Precondition: every conviction is below the builder's default gate.
        assert preview and all(i.conviction < 0.85 for i in preview)
        plan = plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)
        payload = json.loads((tmp_path / "execution_queue.json").read_text(encoding="utf-8"))
        # Not one holding was truncated by the conviction gate.
        assert payload["n_intents"] == len(preview)
        assert {i["symbol"] for i in payload["intents"]} == {i.symbol for i in preview}

    def test_live_mode_with_cap_yields_placeable_intents(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        import execution.queue_builder as qb
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "live", raising=False)
        monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 5_000.0, raising=False)
        monkeypatch.setattr(qb, "PreTradeRiskGate", _PassingGate)
        monkeypatch.setattr(qb, "GlobalKillSwitch", _InactiveKillSwitch)

        plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)
        payload = json.loads((tmp_path / "execution_queue.json").read_text(encoding="utf-8"))
        assert payload["mode"] == "live"
        assert payload["n_placeable"] >= 1
        assert any(i["allow_place"] is True for i in payload["intents"])

    def test_live_without_notional_cap_blocks_and_flags_reason(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        import execution.queue_builder as qb
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "live", raising=False)
        # cap stays 0.0 via the autouse _no_cap fixture.
        monkeypatch.setattr(qb, "PreTradeRiskGate", _PassingGate)
        monkeypatch.setattr(qb, "GlobalKillSwitch", _InactiveKillSwitch)

        plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)
        payload = json.loads((tmp_path / "execution_queue.json").read_text(encoding="utf-8"))
        assert payload["n_placeable"] == 0
        assert all(i["allow_place"] is False for i in payload["intents"])
        assert all(
            any("notional_cap_unset" in r for r in i["gate_reasons"])
            for i in payload["intents"]
        )


# ---------------------------------------------------------------------------
# Pilot-scoped alerting — plan_follow emits one pilot-attributed INFO alert
# ---------------------------------------------------------------------------

class TestPilotScopedAlert:
    """``plan_follow`` emits exactly one pilot-attributed ``follow_planned`` alert
    per plan. The lazy ``from observability.alerts import send_alert`` inside
    ``plan_follow`` resolves the module attribute at call time, so monkeypatching
    ``observability.alerts.send_alert`` intercepts the call."""

    def test_emits_one_pilot_attributed_alert(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        from settings import settings
        import observability.alerts as alerts_mod
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "off", raising=False)

        calls = []
        monkeypatch.setattr(
            alerts_mod, "send_alert",
            lambda *a, **k: calls.append((a, k)), raising=True,
        )

        result = plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)

        # Exactly one alert, and it is pilot-attributed.
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[0] == "INFO"
        extra = kwargs["extra"]
        assert extra["pilot_id"] == pilot.id
        assert extra["type"] == "follow_planned"
        assert extra["mode"] == result["mode"]
        assert extra["intent_count"] == len(result["planned_intents"])
        assert extra["queue_written"] == result["queue_written"]

    def test_alert_failure_does_not_break_plan_follow(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        """Dead-letter (CONSTRAINT #6): a raising ``send_alert`` is swallowed and
        ``plan_follow`` still returns its normal result dict."""
        from settings import settings
        import observability.alerts as alerts_mod
        monkeypatch.setattr(settings, "ROBINHOOD_EXECUTION_MODE", "off", raising=False)

        def _boom(*a, **k):
            raise RuntimeError("alert channel down")

        monkeypatch.setattr(alerts_mod, "send_alert", _boom, raising=True)

        result = plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)
        assert set(result.keys()) == {"planned_intents", "mode", "queue_written", "sizing_path", "kelly_weight"}
        assert result["mode"] == "off"
        assert result["planned_intents"]  # preview still returned despite alert failure


# ---------------------------------------------------------------------------
# Kelly-ceiling sizing (plan_follow) — regression coverage for two bugs:
#
# 1. `execution.transactions_store` -> `transactions_store` import-path bug
#    that previously made this entire block silently no-op (caught by the
#    surrounding `except Exception`, always yielding sizing_path="kelly_error",
#    kelly_weight=0.0 -- never actually clamping anything).
# 2. Once (1) was fixed, a second bug surfaced: kelly_sizing_for_strategy's
#    own cold-start scale-in (min(1.0, n_trades / MIN_TRADES_REQUIRED))
#    permanently zeroed the ceiling on every first-ever Follow of any Pilot
#    (n_trades=0 by definition for a brand-new Follow relationship), so no
#    position could ever be opened and n_trades could never grow -- a
#    deadlock. plan_follow now bypasses that scale-in for cold-start Follows
#    and caps on the raw vol-target weight instead (a Pilot's edge is already
#    proven by the deployability gate before it's followable, unlike the
#    brand-new untested autonomous strategy that derate was designed for).
# ---------------------------------------------------------------------------

class _ClosedTradesStore:
    """A ``TransactionsStore`` stand-in with a controllable ``closed_trades_df()``."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def closed_trades_df(self) -> pd.DataFrame:
        return self._df


def _cold_start_trades(strategy_id: str, n: int = 15) -> pd.DataFrame:
    """``n`` (< MIN_TRADES_REQUIRED=30, i.e. still "cold start") closed trades
    for ``strategy_id``: 10 winners at +5%, 5 losers at -2%, all long."""
    rows = []
    for i in range(n):
        win = i < (2 * n) // 3
        entry = 100.0
        exit_ = entry * (1.05 if win else 0.98)
        rows.append({
            "symbol": "NVDA",
            "side": "long",
            "strategy": strategy_id,
            "entry_price": entry,
            "exit_price": exit_,
        })
    return pd.DataFrame(rows)


def _snapshot_with_vol(snapshot: dict, vol: float = 0.20) -> dict:
    """A deep copy of ``snapshot`` with ``Realized_Vol_60D`` populated on every
    signal row -- the committed fixture carries no vol data at all, which
    would otherwise always route ``kelly_sizing_for_strategy``'s cold-start
    fallback straight to the vol-unavailable branch (``cold_start_no_vol``,
    weight forced to 0.0) regardless of trade count."""
    snap = copy.deepcopy(snapshot)
    for sig in snap.get("signals", []):
        sig["Realized_Vol_60D"] = vol
    return snap


class TestKellyCeilingSizing:
    def test_correct_transactions_store_module_is_imported(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        """Regression test for the import-path bug: pilots/mirror.py must
        construct `transactions_store.TransactionsStore` (the real module,
        class at repo root) -- NOT the nonexistent `execution.transactions_store`.
        A wrong import raises ModuleNotFoundError on every call, silently
        caught, and always yields sizing_path="kelly_error". Patching the
        correct module path and asserting it was actually called is the only
        way to catch a regression back to the wrong path (a wrong path would
        never call this mock at all -- the exception would fire first)."""
        import sizing.kelly as kelly_mod

        monkeypatch.setattr(
            kelly_mod, "kelly_sizing_for_strategy",
            _REAL_KELLY_SIZING_FOR_STRATEGY, raising=True,
        )

        calls = []

        class _SpyStore(_ClosedTradesStore):
            def __init__(self):
                super().__init__(pd.DataFrame())
                calls.append(True)

        monkeypatch.setattr("transactions_store.TransactionsStore", _SpyStore, raising=True)

        result = plan_follow(pilot, _AMOUNT, account, snapshot=snapshot, output_dir=tmp_path)

        assert calls, "transactions_store.TransactionsStore was never constructed"
        # The real sizing function ran to completion (no exception swallowed):
        # a wrong import path always produces sizing_path == "kelly_error".
        assert result["sizing_path"] != "kelly_error"
        assert result["kelly_weight"] is not None

    def test_cold_start_vol_target_fallback_clamps_amount(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        """The cold-start (< MIN_TRADES_REQUIRED) vol-target fallback path
        actually runs (not kelly_error) and its resulting kelly_weight genuinely
        hard-caps a requested amount that exceeds kelly_weight * total_equity,
        while leaving a smaller request untouched.

        Cold-start Follows deliberately bypass kelly_sizing_for_strategy's own
        n_trades-based scale-in (see pilots/mirror.py's plan_follow comment):
        that ramp is designed for a brand-new AUTONOMOUS strategy earning trust
        from zero, but a Pilot's edge is already proven by the deployability
        gate before it's ever followable, and a Follow starts at n_trades=0 by
        definition -- so the ramp would permanently zero the ceiling on every
        first-ever follow. The raw (unscaled) vol-target weight is used
        instead."""
        import sizing.kelly as kelly_mod

        monkeypatch.setattr(
            kelly_mod, "kelly_sizing_for_strategy",
            _REAL_KELLY_SIZING_FOR_STRATEGY, raising=True,
        )
        # Also restore the real cold/warm decision function -- the autouse
        # `_kelly_ceiling_unbounded` fixture stubs it to always report "warm"
        # (n=999) so every other test in this file is unaffected by Kelly
        # sizing. This test needs the REAL decision against the n=15-trade
        # spy store below to actually exercise the cold-start branch.
        monkeypatch.setattr(
            kelly_mod, "estimate_win_rate_and_payoff_per_strategy",
            _REAL_ESTIMATE_WIN_RATE_AND_PAYOFF, raising=True,
        )

        strategy_id = f"Follow:{pilot.id}"
        trades = _cold_start_trades(strategy_id, n=15)
        vol_snapshot = _snapshot_with_vol(snapshot, vol=0.20)

        monkeypatch.setattr(
            "transactions_store.TransactionsStore",
            lambda: _ClosedTradesStore(trades),
            raising=True,
        )

        # weight = volatility_target_weight(0.20, target_vol=0.10, max_leverage=2.0)
        #        = 0.10 / 0.20 = 0.5  (no cold-start scale-in applied)
        # kelly_weight = 0.5 -> ceiling = 0.5 * 250_000 = 125_000
        expected_ceiling = 125_000.0

        # Case 1: requested amount ($200k) exceeds the ceiling -> clamped.
        over_result = plan_follow(
            pilot, 200_000.0, account, snapshot=vol_snapshot, output_dir=tmp_path
        )
        assert over_result["sizing_path"] is not None
        assert over_result["sizing_path"] != "kelly_error"
        assert over_result["sizing_path"].startswith("vol_target_fallback_no_scalein")
        assert over_result["kelly_weight"] == pytest.approx(0.5, rel=1e-6)
        over_total = sum(i["target_notional"] for i in over_result["planned_intents"])
        assert over_total == pytest.approx(expected_ceiling, rel=1e-3)
        assert over_total < 200_000.0

        # Case 2: requested amount ($10k) is under the ceiling -> untouched.
        under_result = plan_follow(
            pilot, _AMOUNT, account, snapshot=vol_snapshot, output_dir=tmp_path
        )
        assert under_result["kelly_weight"] == pytest.approx(0.5, rel=1e-6)
        under_total = sum(i["target_notional"] for i in under_result["planned_intents"])
        assert under_total == pytest.approx(_AMOUNT, rel=1e-3)

    def test_warm_strategy_uses_real_bootstrap_kelly_not_vol_target(
        self, pilot, account, snapshot, tmp_path, monkeypatch
    ):
        """Once >= MIN_TRADES_REQUIRED real Follow trades exist, plan_follow
        must route through the actual bootstrap-Kelly path (not the vol-target
        bypass, and not the scale-in-derated fallback) -- the cold-start bypass
        above must not leak into the warm case."""
        import sizing.kelly as kelly_mod

        monkeypatch.setattr(
            kelly_mod, "kelly_sizing_for_strategy",
            _REAL_KELLY_SIZING_FOR_STRATEGY, raising=True,
        )
        monkeypatch.setattr(
            kelly_mod, "estimate_win_rate_and_payoff_per_strategy",
            _REAL_ESTIMATE_WIN_RATE_AND_PAYOFF, raising=True,
        )

        strategy_id = f"Follow:{pilot.id}"
        # 40 trades, evenly winning, to guarantee a valid (non-NaN) bootstrap.
        trades = _cold_start_trades(strategy_id, n=40)
        vol_snapshot = _snapshot_with_vol(snapshot, vol=0.20)

        monkeypatch.setattr(
            "transactions_store.TransactionsStore",
            lambda: _ClosedTradesStore(trades),
            raising=True,
        )

        result = plan_follow(
            pilot, _AMOUNT, account, snapshot=vol_snapshot, output_dir=tmp_path
        )
        assert result["sizing_path"] is not None
        assert result["sizing_path"] != "kelly_error"
        assert not result["sizing_path"].startswith("vol_target_fallback_no_scalein")


# ---------------------------------------------------------------------------
# AST self-guard — no order-submission symbol names in pilots/mirror.py
# ---------------------------------------------------------------------------

class TestNoOrderSymbols:
    _FORBIDDEN_EXACT = frozenset({
        "submit_order",
        "buy_order",
        "sell_order",
        "place_order",
        "place_equity_order",
        "place_option_order",
    })

    def _defined_names(self):
        tree = ast.parse(MIRROR_SRC.read_text(encoding="utf-8"), filename=str(MIRROR_SRC))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
        return names

    def test_no_forbidden_function_or_class_names(self):
        for name in self._defined_names():
            assert name not in self._FORBIDDEN_EXACT, f"forbidden name: {name}"
            assert not name.startswith("place_"), f"place_* name: {name}"
            assert not name.endswith("_order"), f"*_order name: {name}"

    def test_no_forbidden_assignment_names(self):
        """Module-level assigned symbols must also avoid the order tokens."""
        tree = ast.parse(MIRROR_SRC.read_text(encoding="utf-8"), filename=str(MIRROR_SRC))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                n = node.id
                assert n not in self._FORBIDDEN_EXACT
                assert not n.startswith("place_")
                assert not n.endswith("_order")

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
import json
from pathlib import Path

import pytest

from pilots.catalog import get_pilot
from pilots.mirror import (
    FOLLOW_MIN_CONVICTION,
    FollowIntent,
    build_follow_intents,
    plan_follow,
)
from pilots.scoring import load_snapshot, pilot_holdings

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

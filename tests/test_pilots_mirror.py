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


class _FakeSnapshot:
    """Minimal stand-in for ``data.robinhood_portfolio.AccountSnapshot``."""

    def __init__(self, total_equity: float, positions=None) -> None:
        self.total_equity = total_equity
        self.buying_power = total_equity
        self.positions = positions or {}


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

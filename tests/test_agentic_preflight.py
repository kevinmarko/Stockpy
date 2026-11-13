"""Tests for execution/agentic_preflight.py — Agent 3 (Portfolio & Risk Manager).

Fully offline. MCP payloads below copy the real response shapes observed on
2026-11-03 (get_portfolio, get_equity_positions, get_equity_quotes,
get_trade_approvals), with made-up numbers.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from hypothesis import example, given, settings as hsettings, strategies as st

from execution import agentic_preflight as ap
from execution.receipts_store import make_dedup_key

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def portfolio(total="1000", equity="0", bp="1000"):
    return {"data": {"total_value": total, "equity_value": equity, "cash": bp,
                     "buying_power": {"buying_power": bp, "display_currency": "USD"}}}


def positions(*rows, avg=None):
    """rows are (symbol, qty, sellable[, avg_cost]); a missing cost basis defaults to
    ``avg`` and, if that is None too, to a very low basis so the holding is never a loser."""
    out = []
    for r in rows:
        s, q, sell = r[:3]
        cost = r[3] if len(r) > 3 else (avg if avg is not None else 0.01)
        out.append({"symbol": s, "quantity": str(q), "shares_available_for_sells": str(sell),
                    "average_buy_price": str(cost)})
    return {"data": {"positions": out}}


def quotes(**prices):
    return {"data": {"results": [
        {"quote": {"symbol": s, "bid_price": str(p), "ask_price": str(p), "last_trade_price": str(p),
                   "state": "active", "has_traded": True, "venue_last_trade_time": "2026-11-03T14:59:00Z"}}
        for s, p in prices.items()
    ]}}


def intent(symbol, side="buy", *, notional=200.0, qty=None, conviction=0.9, allow=True,
           order_type="market", bps=None, coid=None):
    d = {"client_order_id": coid or f"coid-{symbol}-{side}", "symbol": symbol, "side": side,
         "qty": qty, "target_notional": notional, "order_type": order_type,
         "conviction": conviction, "allow_place": allow, "gate_allowed": allow}
    if bps is not None:
        d["limit_offset_bps"] = bps
    return d


def queue(*intents, mode="live", generated=NOW - timedelta(minutes=5), kill=False):
    return {"mode": mode, "generated_at": generated.isoformat() if generated else None,
            "kill_switch_active": kill, "intents": list(intents)}


def run(q, *, acct=None, qts=None, **kw):
    kw.setdefault("kill_switch_active", False)
    kw.setdefault("max_notional", 500.0)
    kw.setdefault("max_position_weight", 1.0)
    kw.setdefault("max_portfolio_gross", 2.0)
    acct = acct or ap.AccountState.from_mcp(portfolio(), positions())
    qts = qts if qts is not None else ap.quotes_from_mcp(quotes(AAPL=100.0, MSFT=50.0, F=12.34))
    return ap.preflight(q, acct, qts, now=NOW, **kw)


def only(report):
    (v,) = report.verdicts
    return v


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

class TestParsers:
    def test_account_from_real_shapes(self):
        a = ap.AccountState.from_mcp(portfolio(total="250", equity="0", bp="250.0000"),
                                     positions(("F", 3, 2)))
        assert (a.total_value, a.equity_value, a.buying_power) == (250.0, 0.0, 250.0)
        assert a.positions["F"] == ap.Holding(qty=3.0, sellable=2.0, avg_cost=0.01)

    def test_empty_positions_is_known_empty_not_unknown(self):
        assert ap.AccountState.from_mcp(portfolio(), {"data": {"positions": []}}).positions == {}

    def test_unreadable_positions_are_unknown(self):
        assert ap.AccountState.from_mcp(portfolio(), {"data": {"oops": 1}}).positions is None
        assert ap.AccountState.from_mcp(portfolio(), None).positions is None

    def test_paged_positions(self):
        pages = [positions(("A", 1, 1)), positions(("B", 2, 2))]
        assert set(ap.AccountState.from_mcp(portfolio(), pages).positions) == {"A", "B"}

    def test_missing_portfolio_values_are_none_not_zero(self):
        a = ap.AccountState.from_mcp({"data": {}}, positions())
        assert a.total_value is None and a.buying_power is None and a.equity_value is None

    def test_quotes_drop_halted_and_zero_prices(self):
        payload = quotes(F=12.0)
        payload["data"]["results"].append({"quote": {"symbol": "HALT", "last_trade_price": "5",
                                                     "state": "halted", "has_traded": True}})
        payload["data"]["results"].append({"quote": {"symbol": "ZERO", "bid_price": "0", "ask_price": "0",
                                                     "last_trade_price": "0", "state": "active"}})
        assert set(ap.quotes_from_mcp(payload)) == {"F"}

    def test_quote_side_choice(self):
        q = ap.Quote(bid=9.9, ask=10.1, last=10.0, as_of=None)
        assert q.price_for("buy") == 10.1 and q.price_for("sell") == 9.9
        assert ap.Quote(bid=None, ask=None, last=10.0, as_of=None).price_for("buy") == 10.0

    def test_approvals_empty_real_shape(self):
        assert ap.approvals_from_mcp({"data": {"approvals": []}}) == ([], 0)

    def test_approvals_identified_and_unidentified(self):
        payload = {"data": {"approvals": [
            {"approval_id": "a1", "symbol": "aapl", "side": "BUY"},
            {"approval_id": "a2", "order": {"symbol": "MSFT", "side": "SIDE_SELL"}},
            {"approval_id": "a3"},
        ]}}
        assert ap.approvals_from_mcp(payload) == ([("AAPL", "buy"), ("MSFT", "sell")], 1)


class TestCostBasisParsing:
    @staticmethod
    def _acct(row):
        base = {"symbol": "F", "quantity": "3", "shares_available_for_sells": "3"}
        return ap.AccountState.from_mcp(portfolio(), {"data": {"positions": [{**base, **row}]}})

    def test_average_buy_price_parsed(self):
        assert self._acct({"average_buy_price": "12.5000"}).positions["F"].avg_cost == 12.5

    def test_average_cost_fallback(self):
        assert self._acct({"average_cost": "11.25"}).positions["F"].avg_cost == 11.25

    def test_average_buy_price_wins_over_average_cost(self):
        assert self._acct({"average_buy_price": "10", "average_cost": "99"}).positions["F"].avg_cost == 10.0

    @pytest.mark.parametrize("bad", ["abc", "0", "-3", None, ""])
    def test_garbage_or_non_positive_is_none(self, bad):
        assert self._acct({"average_buy_price": bad}).positions["F"].avg_cost is None

    def test_absent_is_none(self):
        assert self._acct({}).positions["F"].avg_cost is None


class TestPortfolioHeat:
    @staticmethod
    def _acct(*rows, total="1000"):
        return ap.AccountState.from_mcp(portfolio(total=total), positions(*rows))

    def test_empty_account_is_zero(self):
        assert ap.portfolio_heat(self._acct(), {}) == 0.0

    def test_winners_only_is_zero(self):
        qts = ap.quotes_from_mcp(quotes(F=20.0, AAPL=100.0))
        assert ap.portfolio_heat(self._acct(("F", 10, 10, 15.0), ("AAPL", 1, 1, 100.0)), qts) == 0.0

    def test_one_loser_exact_fraction_and_winners_do_not_offset(self):
        qts = ap.quotes_from_mcp(quotes(F=12.0, AAPL=200.0))
        acct = self._acct(("F", 10, 10, 20.0), ("AAPL", 5, 5, 100.0))  # F loses 80, AAPL wins 500
        assert ap.portfolio_heat(acct, qts) == pytest.approx(0.08, abs=1e-9)

    def test_priced_off_bid_when_no_last(self):
        payload = {"data": {"results": [{"quote": {"symbol": "F", "bid_price": "12", "ask_price": "12.1",
                                                   "state": "active", "has_traded": True}}]}}
        qts = ap.quotes_from_mcp(payload)
        assert ap.portfolio_heat(self._acct(("F", 10, 10, 20.0)), qts) == pytest.approx(0.08, abs=1e-9)

    def test_missing_cost_basis_is_none(self):
        acct = ap.AccountState.from_mcp(portfolio(), {"data": {"positions": [
            {"symbol": "F", "quantity": "3", "shares_available_for_sells": "3"}]}})
        assert ap.portfolio_heat(acct, ap.quotes_from_mcp(quotes(F=12.0))) is None

    def test_missing_quote_for_held_symbol_is_none(self):
        assert ap.portfolio_heat(self._acct(("F", 10, 10, 20.0)), ap.quotes_from_mcp(quotes(AAPL=1.0))) is None

    def test_positions_unreadable_is_none(self):
        acct = ap.AccountState.from_mcp(portfolio(), None)
        assert ap.portfolio_heat(acct, {}) is None

    def test_losses_with_unknown_total_value_is_none(self):
        acct = ap.AccountState.from_mcp({"data": {}}, positions(("F", 10, 10, 20.0)))
        assert ap.portfolio_heat(acct, ap.quotes_from_mcp(quotes(F=12.0))) is None


class TestPreflightHeat:
    """BUY-only heat veto on the live Agentic account. The cap is always passed
    explicitly so the operator's real MAX_PORTFOLIO_HEAT never leaks in."""

    @staticmethod
    def _acct(avg):  # F: 10 sh quoted 12.34 -> loss (avg-12.34)*10 on a 1000 account
        return ap.AccountState.from_mcp(portfolio(total="1000", bp="1000"), positions(("F", 10, 10, avg)))

    def _run(self, q, acct, cap):
        return run(q, acct=acct, max_portfolio_heat=cap)

    def test_heat_under_cap_buy_passes(self):
        r = self._run(queue(intent("AAPL", notional=100.0)), self._acct(12.34), 0.10)
        v = only(r)
        assert ap.PORTFOLIO_HEAT_OVER_LIMIT not in v.reasons
        assert ap.PORTFOLIO_HEAT_UNAVAILABLE not in v.reasons
        assert v.passed

    def test_heat_over_cap_blocks_buy_but_not_sell(self):
        acct = self._acct(22.34)  # loss 100 -> heat 0.10
        q = queue(intent("AAPL", "buy", notional=100.0), intent("F", "sell", notional=None, qty=2))
        r = self._run(q, acct, 0.05)
        buy, sell = r.verdicts
        assert ap.PORTFOLIO_HEAT_OVER_LIMIT in buy.reasons and not buy.passed
        assert ap.PORTFOLIO_HEAT_OVER_LIMIT not in sell.reasons
        assert ap.PORTFOLIO_HEAT_UNAVAILABLE not in sell.reasons

    def test_heat_none_blocks_buy_as_unavailable(self):
        acct = ap.AccountState.from_mcp(portfolio(), {"data": {"positions": [
            {"symbol": "F", "quantity": "10", "shares_available_for_sells": "10"}]}})  # no cost basis
        v = only(self._run(queue(intent("AAPL", notional=100.0)), acct, 0.10))
        assert ap.PORTFOLIO_HEAT_UNAVAILABLE in v.reasons and not v.passed

    def test_cap_none_blocks_buy_as_unavailable(self, monkeypatch):
        from settings import settings
        monkeypatch.setattr(settings, "MAX_PORTFOLIO_HEAT", None, raising=False)
        v = only(self._run(queue(intent("AAPL", notional=100.0)), self._acct(12.34), None))
        assert ap.PORTFOLIO_HEAT_UNAVAILABLE in v.reasons

    def test_limits_carry_heat_and_cap(self):
        r = self._run(queue(intent("AAPL", notional=100.0)), self._acct(22.34), 0.05)
        assert r.limits["portfolio_heat"] == pytest.approx(0.10, abs=1e-9)
        assert r.limits["max_portfolio_heat"] == 0.05


# ---------------------------------------------------------------------------
# Run-level blockers
# ---------------------------------------------------------------------------

class TestRunBlockers:
    @pytest.mark.parametrize("q,kw,code", [
        (queue(intent("AAPL")), {"kill_switch_active": True}, ap.KILL_SWITCH_ACTIVE),
        (queue(intent("AAPL"), kill=True), {}, ap.KILL_SWITCH_ACTIVE),
        (queue(intent("AAPL"), generated=NOW - timedelta(minutes=31)), {}, ap.QUEUE_STALE),
        (queue(intent("AAPL"), generated=None), {}, ap.QUEUE_TIMESTAMP_UNAVAILABLE),
        (queue(intent("AAPL"), mode="off"), {}, ap.MODE_OFF),
        (queue(intent("AAPL")), {"unidentified_pending_approvals": 1}, ap.PENDING_APPROVAL_UNIDENTIFIED),
    ])
    def test_blocker_vetoes_everything(self, q, kw, code):
        r = run(q, **kw)
        assert code in r.run_blockers
        assert not any(v.passed or v.placeable for v in r.verdicts)

    def test_clean_run_has_no_blockers_and_passes(self):
        r = run(queue(intent("AAPL")))
        assert r.run_blockers == []
        v = only(r)
        assert v.passed and v.placeable and v.reasons == []
        assert (v.qty, v.est_notional) == (2.0, 200.0)


# ---------------------------------------------------------------------------
# Per-intent checks
# ---------------------------------------------------------------------------

class TestPerIntent:
    def test_gate_blocked_in_live(self):
        assert ap.GATE_BLOCKED in only(run(queue(intent("AAPL", allow=False)))).reasons

    def test_review_mode_uses_gate_allowed_and_is_never_placeable(self):
        v = only(run(queue(intent("AAPL"), mode="review")))
        assert v.passed and not v.placeable

    def test_already_submitted_today(self):
        r = run(queue(intent("AAPL")), placed_dedup_keys=[make_dedup_key("AAPL", "buy", NOW)])
        assert ap.ALREADY_SUBMITTED_TODAY in only(r).reasons

    def test_yesterdays_submission_does_not_block(self):
        r = run(queue(intent("AAPL")), placed_dedup_keys=[make_dedup_key("AAPL", "buy", NOW - timedelta(days=1))])
        assert only(r).passed

    def test_pending_approval_same_symbol_side(self):
        r = run(queue(intent("AAPL")), pending_approvals=[("AAPL", "buy")])
        assert ap.APPROVAL_ALREADY_PENDING in only(r).reasons
        assert only(run(queue(intent("AAPL")), pending_approvals=[("AAPL", "sell")])).passed

    def test_quote_unavailable(self):
        v = only(run(queue(intent("TSLA"))))
        assert v.reasons == [ap.QUOTE_UNAVAILABLE] and v.qty is None and v.est_notional is None

    def test_qty_below_one_share_is_not_rounded_up(self):
        v = only(run(queue(intent("AAPL", notional=99.0))))
        assert ap.QTY_BELOW_ONE_SHARE in v.reasons and v.qty == 0.0

    def test_limit_buy_sized_off_the_limit_price(self):
        v = only(run(queue(intent("AAPL", notional=303.0, order_type="limit", bps=100))))
        assert v.limit_price == 101.0           # 100 * 1.01
        assert v.qty == 3.0                     # floor(303 / 101)
        assert v.est_notional == 303.0 and v.passed

    def test_market_intent_has_no_limit_price(self):
        assert only(run(queue(intent("AAPL")))).limit_price is None

    @pytest.mark.parametrize("cap", [0.0, -1.0])
    def test_notional_cap_unset(self, cap):
        assert ap.NOTIONAL_CAP_UNSET in only(run(queue(intent("AAPL")), max_notional=cap)).reasons

    def test_over_notional_cap(self):
        assert ap.OVER_NOTIONAL_CAP in only(run(queue(intent("AAPL", notional=600)), max_notional=500)).reasons

    def test_over_position_weight_uses_live_holding(self):
        acct = ap.AccountState.from_mcp(portfolio(total="1000", equity="300", bp="700"), positions(("AAPL", 3, 3)))
        # 3 held @100 = 300, +200 buy = 500 / 1000 = 0.5 > 0.4
        r = run(queue(intent("AAPL")), acct=acct, max_position_weight=0.4)
        assert ap.OVER_POSITION_WEIGHT in only(r).reasons
        assert only(run(queue(intent("AAPL")), acct=acct, max_position_weight=0.5)).passed

    def test_buy_with_unknown_account_values_vetoes(self):
        acct = ap.AccountState.from_mcp({"data": {}}, positions())
        assert ap.ACCOUNT_VALUES_UNAVAILABLE in only(run(queue(intent("AAPL")), acct=acct)).reasons

    def test_sell_full_exit_exact_qty_including_fraction(self):
        acct = ap.AccountState.from_mcp(portfolio(), positions(("AAPL", 2.5, 2.5)))
        v = only(run(queue(intent("AAPL", "sell", qty=2.5, notional=250)), acct=acct))
        assert v.passed and v.qty == 2.5

    def test_sell_exceeds_sellable(self):
        acct = ap.AccountState.from_mcp(portfolio(), positions(("AAPL", 5, 2)))
        v = only(run(queue(intent("AAPL", "sell", qty=5, notional=500)), acct=acct))
        assert ap.SELL_EXCEEDS_SELLABLE in v.reasons

    def test_sell_of_unheld_symbol(self):
        assert ap.SELL_EXCEEDS_SELLABLE in only(run(queue(intent("AAPL", "sell", qty=1)))).reasons

    def test_sell_with_unknown_positions(self):
        acct = ap.AccountState.from_mcp(portfolio(), None)
        assert ap.POSITIONS_UNAVAILABLE in only(run(queue(intent("AAPL", "sell", qty=1)), acct=acct)).reasons

    def test_trim_capped_at_sellable(self):
        acct = ap.AccountState.from_mcp(portfolio(), positions(("AAPL", 10, 3)))
        v = only(run(queue(intent("AAPL", "sell", qty=None, notional=1000)), acct=acct))
        assert v.qty == 3.0 and v.passed

    def test_malformed_intent(self):
        v = only(run(queue({"symbol": "AAPL", "side": "hold", "client_order_id": "x"})))
        assert v.reasons == [ap.INTENT_MALFORMED]

    def test_earnings_blackout_off_by_default(self):
        assert only(run(queue(intent("AAPL")))).passed

    def test_earnings_blackout(self):
        q = queue(intent("AAPL"))
        soon = {"AAPL": NOW.date() + timedelta(days=2)}
        assert ap.EARNINGS_WITHIN_BLACKOUT in only(run(q, earnings_dates=soon, earnings_blackout_days=3)).reasons
        assert only(run(q, earnings_dates=soon, earnings_blackout_days=1)).passed
        assert only(run(q, earnings_dates={"AAPL": None}, earnings_blackout_days=3)).passed
        assert ap.EARNINGS_DATE_UNAVAILABLE in only(run(q, earnings_dates={}, earnings_blackout_days=3)).reasons

    def test_sells_never_blocked_by_earnings(self):
        acct = ap.AccountState.from_mcp(portfolio(), positions(("AAPL", 1, 1)))
        r = run(queue(intent("AAPL", "sell", qty=1)), acct=acct, earnings_dates={}, earnings_blackout_days=3)
        assert only(r).passed


# ---------------------------------------------------------------------------
# Cycle-wide buying power / gross
# ---------------------------------------------------------------------------

class TestCycleWide:
    def test_buys_together_must_fit_buying_power_highest_conviction_first(self):
        acct = ap.AccountState.from_mcp(portfolio(total="250", equity="0", bp="250"), positions())
        q = queue(intent("AAPL", notional=200, conviction=0.80),
                  intent("MSFT", notional=200, conviction=0.95))
        by = {v.symbol: v for v in run(q, acct=acct).verdicts}
        assert by["MSFT"].passed                                 # higher conviction wins
        assert ap.BUYING_POWER_EXHAUSTED in by["AAPL"].reasons    # each alone fits; together they don't

    def test_tie_break_is_deterministic(self):
        acct = ap.AccountState.from_mcp(portfolio(total="250", equity="0", bp="250"), positions())
        q = queue(intent("AAPL", notional=200, coid="b"), intent("MSFT", notional=200, coid="a"))
        by = {v.client_order_id: v.passed for v in run(q, acct=acct).verdicts}
        assert by == {"a": True, "b": False}

    def test_sells_do_not_consume_buying_power(self):
        acct = ap.AccountState.from_mcp(portfolio(total="250", equity="100", bp="200"), positions(("F", 10, 10)))
        q = queue(intent("F", "sell", qty=10), intent("AAPL", notional=200))
        assert all(v.passed for v in run(q, acct=acct).verdicts)

    def test_over_portfolio_gross(self):
        acct = ap.AccountState.from_mcp(portfolio(total="1000", equity="900", bp="1000"), positions())
        r = run(queue(intent("AAPL", notional=200)), acct=acct, max_portfolio_gross=1.0)
        assert ap.OVER_PORTFOLIO_GROSS in only(r).reasons

    def test_vetoed_buy_does_not_consume_buying_power(self):
        acct = ap.AccountState.from_mcp(portfolio(total="250", equity="0", bp="250"), positions())
        q = queue(intent("AAPL", notional=200, conviction=0.99, allow=False),
                  intent("MSFT", notional=200, conviction=0.5))
        by = {v.symbol: v for v in run(q, acct=acct).verdicts}
        assert by["MSFT"].passed and not by["AAPL"].passed


# ---------------------------------------------------------------------------
# Invariants (property-based)
# ---------------------------------------------------------------------------

_intent_st = st.builds(
    lambda i, side, notional, qty, conv, allow, bps: intent(
        f"S{i}", side, notional=notional, qty=qty if side == "sell" else None,
        conviction=conv, allow=allow, order_type="limit" if bps else "market", bps=bps or None,
        coid=f"c{i}{side}"),
    i=st.integers(0, 5), side=st.sampled_from(["buy", "sell"]),
    notional=st.floats(1, 5_000), qty=st.one_of(st.none(), st.floats(0.01, 50)),
    conv=st.floats(0, 1), allow=st.booleans(), bps=st.sampled_from([0, 10, 50]),
)


@hsettings(max_examples=150, deadline=None)
@example(intents=[intent("S0", "buy", notional=1.375, conviction=0.0, allow=False, coid="c0buy")],
         price=1.375, bp=0.0, held=0.0)  # round(1 * 1.375, 2) == 1.38: cent rounding, not an upsize
@given(intents=st.lists(_intent_st, max_size=6, unique_by=lambda d: d["client_order_id"]),
       price=st.floats(0.5, 900), bp=st.floats(0, 5_000), held=st.floats(0, 50))
def test_never_adds_flips_or_upsizes(intents, price, bp, held):
    acct = ap.AccountState.from_mcp(
        portfolio(total=str(bp + 1), equity="0", bp=str(bp)),
        positions(*[(f"S{i}", held, held) for i in range(6)]))
    qts = ap.quotes_from_mcp(quotes(**{f"S{i}": price for i in range(6)}))
    r = ap.preflight(queue(*intents), acct, qts, now=NOW, kill_switch_active=False,
                     max_notional=10_000, max_position_weight=10.0, max_portfolio_gross=10.0)
    assert [(v.client_order_id, v.side) for v in r.verdicts] == [(i["client_order_id"], i["side"]) for i in intents]
    for v, i in zip(r.verdicts, intents):
        if v.side == "buy" and v.est_notional is not None:
            # est_notional is rounded to cents, so allow half a cent of rounding
            assert v.est_notional <= i["target_notional"] + 0.005 + 1e-9
        if v.side == "sell" and v.qty is not None:
            assert v.qty <= (i["qty"] if i["qty"] is not None else held) + 1e-9
    spent = sum(v.est_notional for v in r.verdicts if v.passed and v.side == "buy")
    assert spent <= bp + 1e-6
    again = ap.preflight(queue(*intents), acct, qts, now=NOW, kill_switch_active=False,
                         max_notional=10_000, max_position_weight=10.0, max_portfolio_gross=10.0)
    assert again.to_dict() == r.to_dict()


def test_caps_default_to_settings(monkeypatch):
    from settings import settings
    monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 150.0, raising=False)
    r = ap.preflight(queue(intent("AAPL")), ap.AccountState.from_mcp(portfolio(), positions()),
                     ap.quotes_from_mcp(quotes(AAPL=100.0)), now=NOW, kill_switch_active=False)
    assert r.limits["max_notional_per_order"] == 150.0
    assert ap.OVER_NOTIONAL_CAP in only(r).reasons


# ---------------------------------------------------------------------------
# Persistence + CLI
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_write_and_read_back_for_same_queue_only(self, tmp_path):
        q = queue(intent("AAPL"))
        r = run(q)
        path = ap.write_preflight(r, output_dir=tmp_path)
        assert path.name == ap.PREFLIGHT_FILENAME and not list(path.parent.glob("*.tmp"))
        back = ap.read_preflight_for_queue(q, output_dir=tmp_path)
        assert back["n_passed"] == 1 and back["queue_generated_at"] == q["generated_at"]
        other = queue(intent("AAPL"), generated=NOW - timedelta(minutes=1))
        assert ap.read_preflight_for_queue(other, output_dir=tmp_path) is None

    def test_read_missing_or_corrupt_is_none(self, tmp_path):
        q = queue(intent("AAPL"))
        assert ap.read_preflight_for_queue(q, output_dir=tmp_path) is None
        folder = tmp_path / "agentic" / ap.run_id_for(run(q))
        folder.mkdir(parents=True)
        (folder / ap.PREFLIGHT_FILENAME).write_text("{not json")
        assert ap.read_preflight_for_queue(q, output_dir=tmp_path) is None

    def test_cli_round_trip(self, tmp_path, monkeypatch, capsys):
        from settings import settings
        monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 500.0, raising=False)
        gen = datetime.now(timezone.utc) - timedelta(minutes=2)
        (tmp_path / "execution_queue.json").write_text(json.dumps(queue(intent("F", notional=100), generated=gen)))
        files = {"portfolio": portfolio(total="250", bp="250"), "positions": positions(),
                 "quotes": quotes(F=12.34), "approvals": {"data": {"approvals": []}}}
        args = ["--output-dir", str(tmp_path)]
        for name, payload in files.items():
            p = tmp_path / f"{name}.json"
            p.write_text(json.dumps(payload))
            args += [f"--{name}", str(p)]
        import execution.kill_switch as ks
        monkeypatch.setattr(ks, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH", raising=False)
        assert ap.main(args) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["n_passed"] == 1
        (v,) = out["verdicts"]
        assert v["qty"] == 8.0 and v["placeable"] is True       # floor(100 / 12.34)
        assert (tmp_path / "agentic").exists()

    def test_cli_unreadable_queue(self, tmp_path, capsys):
        args = ["--output-dir", str(tmp_path)]
        for name in ("portfolio", "positions", "quotes", "approvals"):
            p = tmp_path / f"{name}.json"
            p.write_text("{}")
            args += [f"--{name}", str(p)]
        assert ap.main(args) == 2
        assert "queue unreadable" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Plan check 10 (Phase 3): Analyst thesis / critical data gaps — opt-in
# ---------------------------------------------------------------------------

class TestCheck10:
    T_OK = {"coid-AAPL-buy": {"data_gaps": []}}

    def test_off_by_default_even_without_theses(self):
        assert only(run(queue(intent("AAPL")))).passed

    def test_missing_thesis_vetoes_when_on(self):
        r = run(queue(intent("AAPL")), critical_gap_fields=["forecast_30d"])
        assert only(r).reasons == [ap.THESIS_MISSING]
        assert r.limits["critical_gap_fields"] == ["forecast_30d"]

    def test_valid_thesis_passes(self):
        assert only(run(queue(intent("AAPL")), theses=self.T_OK, critical_gap_fields=["x"])).passed

    def test_invalid_thesis_vetoes(self):
        r = run(queue(intent("AAPL")), theses=self.T_OK, invalid_thesis_ids=["coid-AAPL-buy"],
                critical_gap_fields=["x"])
        assert ap.THESIS_INVALID in only(r).reasons

    def test_critical_gap_vetoes_noncritical_does_not(self):
        t = {"coid-AAPL-buy": {"data_gaps": ["forecast_30d", "analyst_ratings"]}}
        assert ap.CRITICAL_DATA_GAP in only(run(queue(intent("AAPL")), theses=t,
                                                 critical_gap_fields=["forecast_30d"])).reasons
        assert only(run(queue(intent("AAPL")), theses=t, critical_gap_fields=["regime"])).passed

    def test_cli_uses_research_earnings_and_theses(self, tmp_path, monkeypatch, capsys):
        from settings import settings
        from execution import agentic_research as ar
        monkeypatch.setattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 500.0, raising=False)
        import execution.kill_switch as ks
        monkeypatch.setattr(ks, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH", raising=False)
        gen = datetime.now(timezone.utc) - timedelta(minutes=2)
        q = queue(intent("F", notional=100), intent("T", notional=100), generated=gen)
        (tmp_path / "execution_queue.json").write_text(json.dumps(q))
        run_dir = ar.run_dir_for_queue(q, tmp_path)
        run_dir.mkdir(parents=True)
        soon = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
        (run_dir / ar.RESEARCH_FILENAME).write_text(json.dumps({"queue_generated_at": q["generated_at"], "symbols": {
            "F": {"next_earnings": {"date": soon, "source": "cal"}, "analyst_ratings": None, "data_gaps": ["analyst_ratings"]},
            "T": {"next_earnings": {"date": None, "reason": "none", "source": "cal"}, "analyst_ratings": None,
                  "data_gaps": ["analyst_ratings"]}}}))
        (run_dir / ar.THESES_FILENAME).write_text(json.dumps({"queue_generated_at": q["generated_at"], "theses": {
            "coid-T-buy": {"symbol": "T", "side": "buy", "thesis": "t", "invalidation": "i", "drivers": [],
                           "data_gaps": []}}}))
        args = ["--output-dir", str(tmp_path), "--blackout-days", "3", "--critical-gaps", "forecast_30d"]
        files = {"portfolio": portfolio(total="1000", bp="1000"), "positions": positions(),
                 "quotes": quotes(F=12.0, T=21.0), "approvals": {"data": {"approvals": []}}}
        for name, payload in files.items():
            p = tmp_path / f"{name}.json"
            p.write_text(json.dumps(payload))
            args += [f"--{name}", str(p)]
        assert ap.main(args) == 0
        by = {v["symbol"]: v for v in json.loads(capsys.readouterr().out)["verdicts"]}
        assert ap.EARNINGS_WITHIN_BLACKOUT in by["F"]["reasons"]    # Scout's date fed the blackout
        assert ap.THESIS_MISSING in by["F"]["reasons"]              # no thesis for F
        assert by["T"]["passed"] is True                           # thesis present, no earnings

"""Tests for execution/agentic_review.py — Agent 5 (Reviewer) and the burn-in scorecard.

Fully offline. MCP payloads are hand-built in the shapes Robinhood documents
(`get_equity_orders` rows carry `symbol`, `state`, `average_price`,
`cumulative_quantity`, `placed_agent`, `agent_initiated`).
"""

from __future__ import annotations

import io
import json
import plistlib
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from execution import agentic_cycle as ac
from execution import agentic_review as ar
from execution import receipts_store as rs

REPO = Path(__file__).resolve().parent.parent
DAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 20, 21, 40, tzinfo=timezone.utc)
FILL_TS = "2026-11-10T15:20:00+00:00"


def order(sym="T", side="buy", state="filled", qty="7", price="21.30", ts=FILL_TS, **over):
    o = {"id": f"o-{sym}-{side}", "symbol": sym, "side": side, "state": state,
         "cumulative_quantity": qty, "average_price": price, "last_transaction_at": ts,
         "created_at": ts, "placed_agent": "user", "agent_initiated": True}
    o.update(over)
    return o


def orders_payload(*rows):
    return {"data": {"orders": list(rows)}}


def quotes_payload(**last):
    return {"data": {"results": [{"quote": {"symbol": s, "last_trade_price": str(p), "state": "active",
                                            "has_traded": True}} for s, p in last.items()]}}


def make_run(tmp, run_id, mode="review", status="completed", verdicts=(), failed_stage=None,
             valid_ids=(), theses=None):
    d = tmp / "agentic" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "cycle.json").write_text(json.dumps({
        "run_id": run_id, "mode": mode, "status": status, "failed_stage": failed_stage,
        "valid_thesis_ids": list(valid_ids)}))
    (d / "preflight.json").write_text(json.dumps({"run_blockers": [], "verdicts": list(verdicts)}))
    if theses is not None:
        (d / "theses.json").write_text(json.dumps({"theses": theses}))


def place(tmp, sym="T", side="buy", qty=7, coid="c-T", run_id="20261110T150000Z", aid="a-T",
          ref="ref-1", ts=FILL_TS):
    rs.append_placed({"symbol": sym, "side": side, "qty": qty, "client_order_id": coid,
                      "run_id": run_id, "approval_id": aid, "ref_id": ref, "ts": ts}, output_dir=tmp)


PASS_T = {"client_order_id": "c-T", "symbol": "T", "side": "buy", "passed": True, "qty": 7, "reasons": []}


def save_inputs(tmp, orders=None, quotes=None, approvals_on=True):
    ar.save_input("orders", orders if orders is not None else orders_payload(), day=DAY, output_dir=tmp)
    if quotes is not None:
        ar.save_input("quotes", quotes, day=DAY, output_dir=tmp)
    if approvals_on is not None:
        # The real shape, captured from the live account on 2026-11-10.
        ar.save_input("approval_setting", {"data": {"setting": {
                          "account_number": "619354848", "human_must_approve_trades": approvals_on,
                          "updated_at": "2026-10-13T14:55:38Z"}}},
                      day=DAY, output_dir=tmp)


# ---------------------------------------------------------------------------
# orders → fills
# ---------------------------------------------------------------------------

class TestFills:
    def test_filled_rows_become_fills(self):
        # A cancelled order that filled nothing is not a fill; neither is a queued one.
        fills, unresolved = ar.fills_from_mcp([order(), order("F", state="cancelled", qty="0"),
                                               order("G", state="queued")])
        assert unresolved == 0 and len(fills) == 1
        f = fills[0]
        assert (f.symbol, f.side, f.quantity, f.price) == ("T", "buy", 7.0, 21.30)

    @pytest.mark.parametrize("state", ["cancelled", "canceled", "partially_filled"])
    def test_partial_fill_counts_its_filled_shares(self, state):
        """K4: shares that traded before a cancel (or while still working) are a fill."""
        fills, unresolved = ar.fills_from_mcp([order("F", state=state, qty="3", price="10.50")])
        assert unresolved == 0 and len(fills) == 1
        assert (fills[0].symbol, fills[0].quantity, fills[0].price) == ("F", 3.0, 10.50)

    def test_partial_fill_without_average_price_is_skipped_not_priced_from_limit(self):
        row = order("F", state="cancelled", qty="3", price=None)
        row["price"] = "10.00"   # the limit price: not an execution price
        fills, _ = ar.fills_from_mcp([row])
        assert fills == []

    def test_partial_fill_without_symbol_counted_not_guessed(self):
        fills, unresolved = ar.fills_from_mcp([order(symbol=None, state="cancelled", qty="2"),
                                               order(symbol=None, state="cancelled", qty="0")])
        assert fills == [] and unresolved == 1

    def test_partial_fill_reconciles_against_the_ledger(self, tmp_path):
        """A 7-share intent cancelled after 3 shares: the 3 reach reconcile, not 'no fill'."""
        make_run(tmp_path, "20261110T150000Z", mode="live", verdicts=[PASS_T])
        place(tmp_path, qty=None)
        rs.append_approval_event({"approval_id": "a-T", "state": "approved"}, output_dir=tmp_path)
        save_inputs(tmp_path, orders_payload(order(state="cancelled", qty="3")), quotes_payload(T=22.0))
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        assert res.review["orders"]["fills"] == 1
        assert res.review["outcomes"][0]["status"] != "no_fill_found"
        assert res.review["outcomes"][0]["entry_price"] == 21.30

    def test_filled_row_without_symbol_counted_not_guessed(self):
        fills, unresolved = ar.fills_from_mcp([order(symbol=None)])
        assert fills == [] and unresolved == 1

    def test_pages_list_and_bare_dict(self):
        assert len(ar.orders_from_mcp([orders_payload(order()), orders_payload(order("F"))])) == 2
        assert len(ar.orders_from_mcp({"orders": [order()]})) == 1
        assert ar.orders_from_mcp(None) == []

    def test_agent_filter_fails_closed(self):
        assert ar._is_agent_initiated({"agent_initiated": True})
        assert ar._is_agent_initiated({})               # field missing → counted
        assert not ar._is_agent_initiated({"agent_initiated": False})


# ---------------------------------------------------------------------------
# invariants
# ---------------------------------------------------------------------------

def _inv(tmp, orders=(), cycles=None):
    return ar.check_invariants(orders=list(orders), placed=rs.read_placed_ledger(tmp),
                               cycles=cycles if cycles is not None else ar.load_cycles(tmp),
                               preflights=ar.load_preflights(tmp))


class TestInvariants:
    def test_clean_live_placement_passes(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z", mode="live", verdicts=[PASS_T])
        place(tmp_path)
        inv = _inv(tmp_path, [order()])
        assert all(v == [] for v in inv.values()), inv

    def test_agent_submitted_order_is_a_failure(self, tmp_path):
        inv = _inv(tmp_path, [order(placed_agent="agentic")])
        assert inv["order_without_phone_approval"][0]["symbol"] == "T"

    def test_placed_in_review_cycle(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z", mode="review", verdicts=[PASS_T])
        place(tmp_path)
        assert _inv(tmp_path)["placed_in_review_cycle"]

    def test_missing_ref_id(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z", mode="live", verdicts=[PASS_T])
        place(tmp_path, ref=None)
        assert _inv(tmp_path)["placed_without_ref_id"]

    def test_vetoed_intent_placed(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z", mode="live",
                 verdicts=[dict(PASS_T, passed=False, reasons=["over_notional_cap"])])
        place(tmp_path)
        assert _inv(tmp_path)["placed_vetoed_or_oversized"][0]["why"] == "no passing preflight verdict"

    def test_no_preflight_at_all(self, tmp_path):
        place(tmp_path)
        assert _inv(tmp_path, cycles=[{"run_id": "20261110T150000Z", "mode": "live"}])[
            "placed_vetoed_or_oversized"]

    def test_oversized(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z", mode="live", verdicts=[PASS_T])
        place(tmp_path, qty=9)
        assert "9 > preflight 7" in _inv(tmp_path)["placed_vetoed_or_oversized"][0]["why"]

    def test_duplicate_placement(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z", mode="live", verdicts=[PASS_T])
        place(tmp_path)
        place(tmp_path, aid="a-T2")
        assert _inv(tmp_path)["duplicate_placement"][0]["count"] == 2

    def test_manual_placement_without_run_is_not_judged(self, tmp_path):
        rs.append_placed({"symbol": "MSFT", "side": "buy", "qty": 1, "ts": FILL_TS}, output_dir=tmp_path)
        inv = _inv(tmp_path)
        assert not inv["placed_without_ref_id"] and not inv["placed_vetoed_or_oversized"]


# ---------------------------------------------------------------------------
# health, outcomes, readiness
# ---------------------------------------------------------------------------

class TestHealth:
    def test_counts(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z", verdicts=[PASS_T,
                 {"client_order_id": "c-F", "passed": False, "reasons": ["buying_power_exhausted"]}])
        make_run(tmp_path, "20261111T150000Z", status="failed", failed_stage="risk")
        make_run(tmp_path, "20261112T150000Z", mode="live")
        rs.append_approval_event({"approval_id": "a1", "state": "declined"}, output_dir=tmp_path)
        h = ar.cycle_health(ar.load_cycles(tmp_path), ar.load_preflights(tmp_path),
                            rs.latest_approval_states(tmp_path))
        assert h["cycles"] == {"completed": 2, "failed": 1}
        assert h["failed_by_stage"] == {"risk": 1}
        assert h["review_days"] == ["2026-11-10"] and h["live_days"] == ["2026-11-12"]
        assert h["n_would_pass"] == 1 and h["vetoes"] == {"buying_power_exhausted": 1}
        assert h["approvals"] == {"declined": 1}

    def test_two_runs_same_day_is_one_day(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z")
        make_run(tmp_path, "20261110T153000Z")
        assert ar.cycle_health(ar.load_cycles(tmp_path), {}, {})["review_days"] == ["2026-11-10"]


class TestOutcomes:
    def _rows(self, tmp, last=22.0, now=NOW, thesis=None, state="approved"):
        make_run(tmp, "20261110T150000Z", mode="live", verdicts=[PASS_T],
                 valid_ids=["c-T"] if thesis else [], theses={"c-T": thesis} if thesis else None)
        place(tmp)
        rs.append_approval_event({"approval_id": "a-T", "state": state}, output_dir=tmp)
        fills, _ = ar.fills_from_mcp([order()])
        return ar.outcomes(placed=rs.read_placed_ledger(tmp), approval_states=rs.latest_approval_states(tmp),
                           fills=fills, last_prices={"T": last}, now=now, output_dir=tmp)

    def test_too_early_before_horizon(self, tmp_path):
        r = self._rows(tmp_path, thesis={"thesis": "Why.", "invalidation": "Below X.", "horizon": "30d"})[0]
        assert r["status"] == "too_early" and r["horizon_days"] == 30
        assert r["entry_price"] == 21.3 and r["move_pct_in_trade_direction"] == pytest.approx(3.2864, abs=1e-3)
        assert r["thesis"] == "Why."

    def test_horizon_reached(self, tmp_path):
        r = self._rows(tmp_path, thesis={"thesis": "W", "horizon": "5d"})[0]
        assert r["status"] == "horizon_reached"

    def test_missing_quote_stays_none(self, tmp_path):
        r = self._rows(tmp_path, last=None)[0]
        assert r["last_price"] is None and r["move_pct_in_trade_direction"] is None

    def test_unvalidated_thesis_not_quoted(self, tmp_path):
        make_run(tmp_path, "20261110T150000Z", mode="live", verdicts=[PASS_T],
                 valid_ids=[], theses={"c-T": {"thesis": "Invented 99."}})
        place(tmp_path)
        rs.append_approval_event({"approval_id": "a-T", "state": "approved"}, output_dir=tmp_path)
        r = ar.outcomes(placed=rs.read_placed_ledger(tmp_path), approval_states=rs.latest_approval_states(tmp_path),
                        fills=[], last_prices={}, now=NOW, output_dir=tmp_path)[0]
        assert r["thesis"] is None and r["status"] == "no_fill_found" and r["entry_price"] is None

    def test_declined_orders_have_no_outcome(self, tmp_path):
        assert self._rows(tmp_path, state="declined") == []

    def test_sell_move_sign(self, tmp_path):
        rs.append_placed({"symbol": "F", "side": "sell", "qty": 10, "client_order_id": "c-F",
                          "approval_id": "a-F", "ts": FILL_TS}, output_dir=tmp_path)
        rs.append_approval_event({"approval_id": "a-F", "state": "approved"}, output_dir=tmp_path)
        fills, _ = ar.fills_from_mcp([order("F", "sell", qty="10", price="12.00")])
        r = ar.outcomes(placed=rs.read_placed_ledger(tmp_path), approval_states=rs.latest_approval_states(tmp_path),
                        fills=fills, last_prices={"F": 11.4}, now=NOW, output_dir=tmp_path)[0]
        assert r["move_pct_in_trade_direction"] == pytest.approx(5.0)   # price fell after a sell: good


def _status(**over):
    args = dict(invariants={k: [] for k in ar.INVARIANTS},
                health={"review_days": [f"2026-11-{d:02d}" for d in range(1, 11)]},
                cycles=[{"run_id": "20261110T150000Z", "status": "completed"}],
                reconciliation={"ok": True}, max_notional=200.0)
    args.update(over)
    return ar.burnin_status(**args)


class TestReadiness:
    def test_ready(self):
        s = _status()
        assert s["ready"] and s["unmet"] == [] and s["review_days"] == 10

    def test_each_unmet_item(self):
        s = _status(invariants={**{k: [] for k in ar.INVARIANTS}, "duplicate_placement": [{"x": 1}]},
                    health={"review_days": ["2026-11-10"]},
                    cycles=[{"run_id": "20261110T150000Z", "status": "failed"}],
                    reconciliation={"ok": False, "error": "boom"}, max_notional=0.0)
        assert not s["ready"] and s["safety_failures"] == ["duplicate_placement"]
        text = " | ".join(s["unmet"])
        for frag in ("duplicate_placement", "9 more day(s)", "1 failed cycle(s)", "boom",
                     "ROBINHOOD_MAX_NOTIONAL_PER_ORDER"):
            assert frag in text

    def test_old_failure_outside_recent_window_is_fine(self):
        cycles = [{"run_id": f"202611{d:02d}T150000Z", "status": "completed"} for d in range(1, 11)]
        cycles[0]["status"] = "failed"
        assert _status(cycles=cycles)["ready"]

    @pytest.mark.parametrize("cap", [None, float("nan"), -5.0])
    def test_bad_cap(self, cap):
        assert not _status(max_notional=cap)["ready"]


# ---------------------------------------------------------------------------
# run_review end to end + CLI
# ---------------------------------------------------------------------------

class TestRunReview:
    def test_missing_orders_input_is_unusable(self, tmp_path):
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        assert res.exit_code == ar.EXIT_UNUSABLE and res.path is None

    def test_clean_live_day(self, tmp_path):
        for d in range(1, 11):
            make_run(tmp_path, f"202611{d:02d}T150000Z", mode="review")
        make_run(tmp_path, "20261112T160000Z", mode="live", verdicts=[PASS_T])
        place(tmp_path, run_id="20261112T160000Z", ts="2026-11-12T16:05:00+00:00")
        rs.append_approval_event({"approval_id": "a-T", "state": "approved"}, output_dir=tmp_path)
        save_inputs(tmp_path, orders_payload(order(ts="2026-11-12T16:10:00+00:00")),
                    quotes_payload(T=22.0))
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        r = res.review
        assert res.exit_code == ar.EXIT_READY, r["burnin"]
        assert r["reconciliation"]["ok"] and r["reconciliation"]["filled_matched"] == 1
        assert r["outcomes"][0]["status"] == "too_early"
        assert json.loads(res.path.read_text())["burnin"]["ready"] is True

    def test_approvals_off_blocks_ready(self, tmp_path):
        save_inputs(tmp_path, approvals_on=False)
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        assert res.exit_code == ar.EXIT_NOT_READY
        assert any("OFF" in u for u in res.review["burnin"]["unmet"])

    def test_approval_setting_missing_blocks_ready(self, tmp_path):
        save_inputs(tmp_path, approvals_on=None)
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        assert any("not confirmed" in u for u in res.review["burnin"]["unmet"])

    def test_unexpected_fill_breaks_reconciliation(self, tmp_path):
        save_inputs(tmp_path, orders_payload(order("AAPL")))
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        assert not res.review["reconciliation"]["ok"]
        assert res.review["reconciliation"]["unexpected_fills"][0]["symbol"] == "AAPL"

    def test_manual_orders_not_reconciled(self, tmp_path):
        save_inputs(tmp_path, orders_payload(order("AAPL", agent_initiated=False)))
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        assert res.review["reconciliation"]["ok"]
        assert res.review["orders"] == {"total": 1, "agent_initiated": 0, "fills": 0,
                                        "unresolved_filled_orders": 0, "from_cache": 0}

    def test_never_touches_the_shared_order_cache(self, tmp_path):
        save_inputs(tmp_path, orders_payload(order()))
        with mock.patch("data.robinhood_orders._write_cache") as w, \
                mock.patch("data.robinhood_orders.fetch_filled_orders") as f:
            ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        w.assert_not_called()
        f.assert_not_called()

    def test_latest_review_skips_unusable(self, tmp_path):
        save_inputs(tmp_path)
        ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        bad = ar.review_dir(date(2026, 11, 21), tmp_path)
        bad.mkdir(parents=True)
        (bad / "review.json").write_text("{bad")
        assert ar.latest_review(tmp_path)["day"] == "2026-11-20"
        assert ar.latest_review(tmp_path / "none") is None


DAY0 = date(2026, 11, 18)   # an earlier completed review


def _prior_review(tmp, day=DAY0, rows=(), *, merged=True, complete=True, raw_cache=None):
    """A completed review on ``day`` with its order cache."""
    folder = ar.review_dir(day, tmp)
    (folder / "inputs").mkdir(parents=True, exist_ok=True)
    (folder / ar.REVIEW_FILENAME).write_text(json.dumps({"day": day.isoformat(), "usable": True}))
    if raw_cache is not None:
        (folder / "inputs" / ar.ORDERS_CACHE_FILENAME).write_text(raw_cache)
    elif merged:
        (folder / "inputs" / ar.ORDERS_CACHE_FILENAME).write_text(
            json.dumps({"orders": list(rows), "complete": complete}))
    else:
        (folder / "inputs" / "orders.json").write_text(json.dumps(orders_payload(*rows)))
    return folder


class TestSince:
    def test_no_previous_review_fetches_everything(self, tmp_path):
        assert ar.fetch_since(DAY, tmp_path)["since"] is None

    def test_since_is_the_last_completed_reviews_day(self, tmp_path):
        _prior_review(tmp_path, rows=[order(ts="2026-11-12T15:00:00+00:00")])
        _prior_review(tmp_path, date(2026, 11, 10), rows=[])
        ans = ar.fetch_since(DAY, tmp_path)
        assert ans["since"] == "2026-11-18" and ans["cache_day"] == "2026-11-18"

    def test_todays_own_review_is_not_the_cache(self, tmp_path):
        _prior_review(tmp_path, DAY, rows=[])
        assert ar.fetch_since(DAY, tmp_path)["since"] is None

    def test_unusable_review_is_skipped(self, tmp_path):
        _prior_review(tmp_path, date(2026, 11, 10), rows=[])
        bad = _prior_review(tmp_path, DAY0, rows=[])
        (bad / ar.REVIEW_FILENAME).write_text(json.dumps({"usable": False}))
        assert ar.fetch_since(DAY, tmp_path)["since"] == "2026-11-10"

    def test_open_order_reaches_back_to_its_creation_day(self, tmp_path):
        """An order still queued at the last review may have filled since: re-fetch it."""
        _prior_review(tmp_path, rows=[
            order("A", state="queued", created_at="2026-11-14T15:00:00+00:00"),
            order("B", state="partially_filled", created_at="2026-11-16T15:00:00+00:00"),
            order("C", state="filled", created_at="2026-11-01T15:00:00+00:00")])
        assert ar.fetch_since(DAY, tmp_path)["since"] == "2026-11-14"

    def test_open_order_without_created_at_fetches_everything(self, tmp_path):
        _prior_review(tmp_path, rows=[order("A", state="queued", created_at=None)])
        assert ar.fetch_since(DAY, tmp_path)["since"] is None

    @pytest.mark.parametrize("kw", [{"raw_cache": "{corrupt"}, {"complete": False}])
    def test_unusable_or_incomplete_cache_fetches_everything(self, tmp_path, kw):
        _prior_review(tmp_path, rows=[order()], **kw)
        assert ar.fetch_since(DAY, tmp_path)["since"] is None

    def test_legacy_orders_json_counts_as_a_full_cache(self, tmp_path):
        _prior_review(tmp_path, rows=[order()], merged=False)
        assert ar.fetch_since(DAY, tmp_path)["since"] == "2026-11-18"

    def test_cli_prints_and_records(self, tmp_path, capsys):
        _prior_review(tmp_path, rows=[order()])
        assert ar.main(["--output-dir", str(tmp_path), "since", "--day", DAY.isoformat()]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["since"] == "2026-11-18"
        saved = json.loads((ar.review_dir(DAY, tmp_path) / "inputs" / ar.SINCE_FILENAME).read_text())
        assert saved["since"] == "2026-11-18"

    def test_cli_null_when_nothing_cached(self, tmp_path, capsys):
        assert ar.main(["--output-dir", str(tmp_path), "since", "--day", DAY.isoformat()]) == 0
        assert json.loads(capsys.readouterr().out)["since"] is None


class TestMergeOrders:
    def test_today_wins_and_cache_fills_the_rest(self):
        today = [order("T", state="filled", id="o1")]
        cached = [order("T", state="queued", id="o1"), order("F", id="o2"), order("G", id=None)]
        merged, n_cache, n_no_id = ar.merge_orders(today, cached)
        assert [o["id"] for o in merged] == ["o1", "o2"]
        assert merged[0]["state"] == "filled"
        assert (n_cache, n_no_id) == (1, 1)

    def test_run_merges_previous_cache_and_records_it(self, tmp_path):
        old = order("F", side="buy", id="o-old", ts="2026-11-12T15:00:00+00:00")
        changed = order("T", state="queued", id="o-T", ts="2026-11-17T15:00:00+00:00")
        _prior_review(tmp_path, rows=[old, changed])
        ar.record_since(DAY, ar.fetch_since(DAY, tmp_path), tmp_path)
        save_inputs(tmp_path, orders_payload(order("T", state="filled", id="o-T",
                                                   ts="2026-11-17T15:00:00+00:00")))
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        r = res.review
        assert r["orders"]["total"] == 2 and r["orders"]["from_cache"] == 1
        assert r["orders_cache"]["status"] == "merged" and r["orders_cache"]["cache_day"] == "2026-11-18"
        assert r["orders"]["fills"] == 2      # today's filled T + the cached filled F
        cache = json.loads((ar.review_dir(DAY, tmp_path) / "inputs" / ar.ORDERS_CACHE_FILENAME).read_text())
        assert cache["complete"] is True
        assert {o["id"]: o["state"] for o in cache["orders"]} == {"o-T": "filled", "o-old": "filled"}
        # ...and it is the next day's cache.
        assert ar.fetch_since(date(2026, 11, 21), tmp_path)["since"] == DAY.isoformat()

    def test_unreadable_previous_cache_uses_todays_fetch_and_says_so(self, tmp_path):
        _prior_review(tmp_path, raw_cache="{corrupt")
        (ar.review_dir(DAY, tmp_path) / "inputs").mkdir(parents=True)
        ar.record_since(DAY, {"since": "2026-11-18"}, tmp_path)   # an incremental fetch...
        save_inputs(tmp_path, orders_payload(order()))
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        oc = res.review["orders_cache"]
        assert res.review["usable"] and oc["status"] == "unreadable" and oc["n_from_cache"] == 0
        assert "unreadable" in oc["note"] and "missing" in oc["note"]
        assert res.review["orders"]["total"] == 1
        # ...so today's cache is incomplete and tomorrow fetches everything.
        assert oc["complete"] is False
        assert ar.fetch_since(date(2026, 11, 21), tmp_path)["since"] is None

    def test_unreadable_cache_after_a_full_fetch_is_still_complete(self, tmp_path):
        _prior_review(tmp_path, raw_cache="{corrupt")
        save_inputs(tmp_path, orders_payload(order()))      # no since.json: a full fetch
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        assert res.review["orders_cache"]["complete"] is True

    def test_no_previous_review(self, tmp_path):
        save_inputs(tmp_path, orders_payload(order()))
        res = ar.run_review(day=DAY, output_dir=tmp_path, now=NOW, max_notional=200)
        assert res.review["orders_cache"]["status"] == "none"
        assert res.review["orders"]["from_cache"] == 0


class TestPush:
    def test_not_ready_lists_unmet(self):
        title, body, prio = ar.build_push({"burnin": {"ready": False, "review_days": 4, "required_days": 10,
                                                      "unmet": ["6 more day(s)"], "safety_failures": []},
                                           "health": {"cycles": {"completed": 6}, "n_would_pass": 11,
                                                      "vetoes": {"qty_below_one_share": 3}, "approvals": {}}})
        assert title == "Burn-in day 4/10: NOT READY" and prio == "default"
        assert "• 6 more day(s)" in body and "qty_below_one_share 3" in body

    def test_safety_failure_is_high_priority(self):
        title, _, prio = ar.build_push({"burnin": {"safety_failures": ["placed_in_review_cycle"]}})
        assert "SAFETY" in title and prio == "high"

    def test_ready_says_its_your_call(self):
        title, _, _ = ar.build_push({"burnin": {"ready": True, "review_days": 10, "required_days": 10,
                                                "safety_failures": []}})
        assert "your call" in title


class TestCli:
    def test_save_from_stdin_and_status(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(orders_payload())))
        assert ar.main(["--output-dir", str(tmp_path), "save", "orders", "--day", DAY.isoformat()]) == 0
        assert (ar.review_dir(DAY, tmp_path) / "inputs" / "orders.json").exists()
        assert ar.main(["--output-dir", str(tmp_path), "status"]) == ar.EXIT_UNUSABLE
        with mock.patch.object(ar, "datetime") as dt:
            dt.now.return_value = NOW
            dt.strptime = datetime.strptime
            rc = ar.main(["--output-dir", str(tmp_path), "run", "--day", DAY.isoformat()])
        assert rc == ar.EXIT_NOT_READY
        assert ar.main(["--output-dir", str(tmp_path), "status"]) == ar.EXIT_NOT_READY

    def test_save_rejects_bad_json_and_unknown_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        assert ar.main(["--output-dir", str(tmp_path), "save", "orders"]) == ar.EXIT_UNUSABLE
        with pytest.raises(ValueError):
            ar.save_input("secrets", {}, day=DAY, output_dir=tmp_path)


class _Proc:
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


_AUTH_ERR = "Failed to authenticate: OAuth session expired and could not be refreshed"


def _write_review(out, *, ready=False, usable=True, day=None):
    d = day or datetime.now(timezone.utc).date()
    folder = ar.review_dir(d, out)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ar.REVIEW_FILENAME).write_text(json.dumps(
        {"day": d.isoformat(), "usable": usable, "burnin": {"ready": ready}}))


class TestLaunch:
    """Success is a usable review.json written during the launch, never
    claude's exit code (2026-11-13: an expired login exited 1, which the old
    launcher read as a normal NOT READY day, so no alert was sent)."""

    def test_runs_review_with_timeout_and_reviewer_tools(self, tmp_path):
        seen = {}

        def runner(cmd, **kw):
            seen.update(cmd=cmd, kw=kw)
            _write_review(tmp_path, ready=False)
            return _Proc(0)
        notifier = mock.Mock()
        assert ar.launch(runner=runner, notifier=notifier, output_dir=tmp_path) == ar.EXIT_NOT_READY
        assert seen["cmd"][:3] == ["claude", "-p", "/rh-agentic-review"]
        assert seen["cmd"][5:] == ["--model", "sonnet"] and ar.COORDINATOR_MODEL == "sonnet"
        assert seen["kw"]["timeout"] == ar.LAUNCH_TIMEOUT_SECONDS
        tools = set(seen["cmd"][4].split(","))
        assert tools == set(ac.agent_tools("rh-reviewer")) | set(ar.COORDINATOR_TOOLS)
        assert not [t for t in tools if any(w in t for w in ("place_", "decline_", "cancel_", "Write"))]
        notifier.assert_not_called()

    def test_ready_review_returns_ready(self, tmp_path):
        def runner(cmd, **kw):
            _write_review(tmp_path, ready=True)
            return _Proc(0)
        notifier = mock.Mock()
        assert ar.launch(runner=runner, notifier=notifier, output_dir=tmp_path) == ar.EXIT_READY
        notifier.assert_not_called()

    def test_expired_login_alerts_with_claudes_message(self, tmp_path, capsys):
        """The 2026-11-13 failure: exit 1, no review written."""
        notifier = mock.Mock()
        rc = ar.launch(runner=mock.Mock(return_value=_Proc(1, stdout=_AUTH_ERR)),
                       notifier=notifier, output_dir=tmp_path)
        assert rc == ar.EXIT_UNUSABLE
        notifier.assert_called_once()
        title, body = notifier.call_args.args[:2]
        assert title == "Agentic burn-in review did not run"
        assert "exited with code 1" in body and _AUTH_ERR in body
        assert notifier.call_args.kwargs.get("priority") == "high"
        line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert line["review_recorded"] is False and line["claude_exit"] == 1
        assert _AUTH_ERR in line["output_tail"]

    def test_alert_quotes_claudes_message_not_stderr_noise(self, tmp_path, capsys):
        """2026-11-13 second run: claude exited 0 with its real explanation on
        stdout and MCP SDK warnings trailing on stderr."""
        proc = _Proc(0, stdout="The review did not run. The Robinhood MCP server failed to connect.\n",
                      stderr="[mcp-sdk] SEP-2352: stored OAuth credential has no 'issuer' stamp\n" * 3)
        notifier = mock.Mock()
        assert ar.launch(runner=mock.Mock(return_value=proc), notifier=notifier,
                         output_dir=tmp_path) == ar.EXIT_UNUSABLE
        body = notifier.call_args.args[1]
        assert "Robinhood MCP server failed to connect" in body and "SEP-2352" not in body
        line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert line["claude_said"].startswith("The review did not run")

    def test_clean_exit_without_a_review_still_alerts(self, tmp_path):
        notifier = mock.Mock()
        assert ar.launch(runner=mock.Mock(return_value=_Proc(0)), notifier=notifier,
                         output_dir=tmp_path) == ar.EXIT_UNUSABLE
        assert "without recording a review" in notifier.call_args.args[1]

    def test_stale_review_from_before_the_launch_does_not_count(self, tmp_path):
        _write_review(tmp_path, ready=True)
        later = datetime.now(timezone.utc).timestamp() + 3600
        notifier = mock.Mock()
        rc = ar.launch(runner=mock.Mock(return_value=_Proc(0)), notifier=notifier, output_dir=tmp_path,
                       clock=lambda: datetime.fromtimestamp(later, tz=timezone.utc))
        assert rc == ar.EXIT_UNUSABLE
        notifier.assert_called_once()

    def test_unusable_review_does_not_count(self, tmp_path):
        def runner(cmd, **kw):
            _write_review(tmp_path, usable=False)
            return _Proc(0)
        notifier = mock.Mock()
        assert ar.launch(runner=runner, notifier=notifier, output_dir=tmp_path) == ar.EXIT_UNUSABLE
        notifier.assert_called_once()

    def test_review_written_even_if_claude_then_errors_counts(self, tmp_path):
        def runner(cmd, **kw):
            _write_review(tmp_path, ready=False)
            return _Proc(1, stderr="some late error")
        notifier = mock.Mock()
        assert ar.launch(runner=runner, notifier=notifier, output_dir=tmp_path) == ar.EXIT_NOT_READY
        notifier.assert_not_called()

    @pytest.mark.parametrize("effect,frag", [
        (subprocess.TimeoutExpired(["claude"], 1, output=_AUTH_ERR), "timed out"),
        (FileNotFoundError(), "not found"),
    ])
    def test_failures_push_one_alert(self, tmp_path, effect, frag):
        notifier = mock.Mock()
        assert ar.launch(runner=mock.Mock(side_effect=effect), notifier=notifier,
                         output_dir=tmp_path) == ar.EXIT_UNUSABLE
        notifier.assert_called_once()
        assert frag in notifier.call_args.args[1]

    def test_notifier_failure_never_raises(self, tmp_path):
        notifier = mock.Mock(side_effect=RuntimeError("ntfy down"))
        assert ar.launch(runner=mock.Mock(return_value=_Proc(1)), notifier=notifier,
                         output_dir=tmp_path) == ar.EXIT_UNUSABLE

    def test_cli_passes_output_dir(self, tmp_path):
        with mock.patch.object(ar, "launch", return_value=0) as launch:
            assert ar.main(["--output-dir", str(tmp_path), "launch"]) == 0
        assert launch.call_args.kwargs["output_dir"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Agent definition, command, plist
# ---------------------------------------------------------------------------

AGENT = REPO / ".claude" / "agents" / "rh-reviewer.md"
COMMAND = REPO / ".claude" / "commands" / "rh-agentic-review.md"
PLIST = REPO / "scripts" / "com.investyo.rh-agentic-review.plist"


def test_reviewer_has_exactly_its_read_tools():
    read = {"get_accounts", "get_equity_orders", "get_equity_positions", "get_equity_quotes",
            "get_trade_approvals", "get_trade_approval_setting"}
    tools = ac.agent_tools("rh-reviewer")
    mcp = {t for t in tools if t.startswith("mcp__")}
    assert mcp == {f"mcp__{s}__{t}"
                   for s in ("robinhood-trading", "Robinhood", "claude_ai_Robinhood") for t in read}
    assert set(tools) - mcp == {"Read", "Grep", "Glob", "Bash"}


@pytest.mark.parametrize("phrase", [
    "you never override it",
    "python3 -m execution.agentic_review run",
    "python3 -m execution.agentic_review save orders",
    "agent_initiated=true",
    "Never act on the main account",
])
def test_agent_pinned_phrases(phrase):
    assert phrase in AGENT.read_text()


def test_command_never_changes_settings():
    text = COMMAND.read_text()
    assert "`rh-reviewer`" in text and "--notify" in text
    assert "Never change `ROBINHOOD_EXECUTION_MODE`" in text


def test_plist_runs_launcher_and_is_not_auto_installed():
    data = plistlib.loads(PLIST.read_bytes())
    assert data["Label"] == "com.investyo.rh-agentic-review"
    assert data["ProgramArguments"][-3:] == ["-m", "execution.agentic_review", "launch"]
    assert len(data["StartCalendarInterval"]) == 5 and not data.get("RunAtLoad")
    assert "rh-agentic-review" not in (REPO / "scripts" / "install_schedule.command").read_text()


@pytest.mark.parametrize("payload,expected", [
    ({"data": {"setting": {"human_must_approve_trades": True}}, "guide": "..."}, True),   # live shape
    ({"data": {"setting": {"human_must_approve_trades": False}}}, False),
    ({"data": {"human_must_approve_trades": True}}, True),                               # flat
    ({"data": {"setting": {"human_must_approve_trades": "true"}}}, None),                # not a bool
    ({"data": {"setting": {}}}, None),
    (None, None),
    ("garbage", None),
])
def test_approvals_required_from_mcp(payload, expected):
    assert ar.approvals_required_from_mcp(payload) is expected

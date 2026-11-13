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
        fills, unresolved = ar.fills_from_mcp([order(), order("F", state="cancelled")])
        assert unresolved == 0 and len(fills) == 1
        f = fills[0]
        assert (f.symbol, f.side, f.quantity, f.price) == ("T", "buy", 7.0, 21.30)

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
                                        "unresolved_filled_orders": 0}

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
    read = {"get_accounts", "get_equity_orders", "get_equity_quotes", "get_trade_approvals",
            "get_trade_approval_setting"}
    tools = ac.agent_tools("rh-reviewer")
    mcp = {t for t in tools if t.startswith("mcp__")}
    assert mcp == {f"mcp__{s}__{t}" for s in ("robinhood-trading", "Robinhood") for t in read}
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

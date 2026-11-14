"""Tests for execution/agentic_cycle.py — the unattended four-agent cycle (Phase 4).

Fully offline: no real `claude` CLI, no network, no Robinhood. The launcher's
subprocess runner and the ntfy notifier are injected fakes.
"""

from __future__ import annotations

import json
import plistlib
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from execution import agentic_cycle as ac
from execution import receipts_store as rs

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 10, 15, 10, tzinfo=timezone.utc)
GEN = "2026-11-10T15:00:00+00:00"
RUN_ID = "20261110T150000Z"


def queue(mode="live", gen=GEN, **over):
    q = {"mode": mode, "generated_at": gen, "kill_switch_active": False, "intents": [
        {"client_order_id": "c-T", "symbol": "T", "side": "buy", "conviction": 0.95,
         "target_notional": 150.0, "allow_place": True, "gate_allowed": True},
        {"client_order_id": "c-F", "symbol": "F", "side": "buy", "conviction": 0.8,
         "target_notional": 120.0, "allow_place": False, "gate_allowed": True},
    ]}
    q.update(over)
    return q


def run_gate(q=None, **kw):
    args = dict(now=NOW, settings_mode="live", kill_switch_active=False, market_open=True)
    args.update(kw)
    return ac.gate(queue() if q is None else q, **args)


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------

class TestGate:
    def test_runs_on_fresh_actionable_queue(self):
        g = run_gate()
        assert g.run and g.reason is None
        assert g.run_id == RUN_ID and g.mode == "live" and g.n_actionable == 1

    @pytest.mark.parametrize("mode", ["off", "", "nonsense", None])
    def test_settings_mode_off_skips(self, mode):
        assert run_gate(settings_mode=mode).reason == ac.SKIP_MODE_OFF

    def test_queue_mode_off_skips(self):
        assert run_gate(queue(mode="off")).reason == ac.SKIP_MODE_OFF

    def test_kill_switch_file_skips(self):
        assert run_gate(kill_switch_active=True).reason == ac.SKIP_KILL_SWITCH

    def test_kill_switch_queue_flag_skips(self):
        assert run_gate(queue(kill_switch_active=True)).reason == ac.SKIP_KILL_SWITCH

    def test_market_closed_skips(self):
        assert run_gate(market_open=False).reason == ac.SKIP_MARKET_CLOSED

    def test_missing_queue_skips(self):
        assert ac.gate(None, now=NOW, settings_mode="live", kill_switch_active=False,
                       market_open=True).reason == ac.SKIP_QUEUE_MISSING

    def test_unreadable_queue_skips(self):
        assert ac.gate(None, now=NOW, settings_mode="live", kill_switch_active=False, market_open=True,
                       queue_error=ac.SKIP_QUEUE_UNREADABLE).reason == ac.SKIP_QUEUE_UNREADABLE

    @pytest.mark.parametrize("gen", [None, "", "not-a-time"])
    def test_missing_timestamp_skips(self, gen):
        assert run_gate(queue(gen=gen)).reason == ac.SKIP_QUEUE_TIMESTAMP

    def test_stale_queue_skips(self):
        assert run_gate(now=NOW + timedelta(minutes=31)).reason == ac.SKIP_QUEUE_STALE

    def test_nothing_actionable_in_live_skips(self):
        q = queue()
        q["intents"][0]["allow_place"] = False
        assert run_gate(q).reason == ac.SKIP_NO_ACTIONABLE

    def test_review_queue_uses_gate_allowed(self):
        q = queue(mode="review")
        for i in q["intents"]:
            i["allow_place"] = False
        g = run_gate(q, settings_mode="review")
        assert g.run and g.mode == "review" and g.n_actionable == 2

    def test_live_queue_with_review_settings_runs_as_review(self):
        g = run_gate(settings_mode="review")
        assert g.run and g.mode == "review"

    def test_review_queue_with_live_settings_stays_review(self):
        g = run_gate(queue(mode="review"), settings_mode="live")
        assert g.run and g.mode == "review"

    @pytest.mark.parametrize("status", ["running", "completed", "failed"])
    def test_already_handled_never_retried(self, status):
        assert run_gate(existing_cycle={"status": status}).reason == ac.SKIP_ALREADY_HANDLED

    def test_non_mapping_intents_ignored(self):
        q = queue()
        q["intents"] = ["junk", None]
        assert run_gate(q).reason == ac.SKIP_NO_ACTIONABLE

    # -- headroom (review #9) -------------------------------------------------
    def test_queue_near_stale_skips(self):
        # stale_after 30 - headroom 10 = 20 minutes of queue age is the last start
        assert run_gate(now=NOW + timedelta(minutes=10)).run          # age 20 min
        g = run_gate(now=NOW + timedelta(minutes=10, seconds=1))
        assert g.reason == ac.SKIP_QUEUE_NEAR_STALE and g.run_id == RUN_ID

    def test_fully_stale_still_reports_stale(self):
        assert run_gate(now=NOW + timedelta(minutes=21)).reason == ac.SKIP_QUEUE_STALE

    def test_near_close_skips(self):
        close = datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)   # 16:00 ET (EST)
        q = queue(gen=(close - timedelta(minutes=25)).isoformat())
        assert run_gate(q, now=close - timedelta(minutes=21)).run
        assert run_gate(q, now=close - timedelta(minutes=20)).reason == ac.SKIP_NEAR_CLOSE
        assert run_gate(q, now=close - timedelta(minutes=1)).reason == ac.SKIP_NEAR_CLOSE

    @pytest.mark.parametrize("when", [
        datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc),    # 09:00 ET, before the open
        datetime(2026, 11, 10, 21, 30, tzinfo=timezone.utc),   # 16:30 ET, after the close
        datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc),    # Saturday
    ])
    def test_outside_regular_hours_skips_even_if_market_flag_says_open(self, when):
        q = queue(gen=(when - timedelta(minutes=1)).isoformat())
        assert run_gate(q, now=when, market_open=True).reason == ac.SKIP_MARKET_CLOSED

    def test_minutes_to_regular_close(self):
        assert ac.minutes_to_regular_close(NOW) == pytest.approx(350.0)   # 10:10 ET
        assert ac.minutes_to_regular_close(datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)) is None
        assert ac.minutes_to_regular_close(datetime(2026, 11, 10, 15, 10)) == pytest.approx(350.0)  # naive = UTC


class TestEvaluateGate:
    def _patch(self, *, mode="live", kill=False, market=True):
        from settings import settings
        return [
            mock.patch.object(settings, "ROBINHOOD_EXECUTION_MODE", mode),
            mock.patch("execution.kill_switch.GlobalKillSwitch.is_active", return_value=kill),
            mock.patch("shared.market_hours.is_us_market_open_now", return_value=market),
        ]

    def test_reads_live_state(self, tmp_path):
        (tmp_path / "execution_queue.json").write_text(json.dumps(queue()))
        with self._patch()[0], self._patch()[1], self._patch()[2]:
            g = ac.evaluate_gate(tmp_path, now=NOW)
        assert g.run, g

    def test_headroom_applies_through_evaluate_gate(self, tmp_path):
        (tmp_path / "execution_queue.json").write_text(json.dumps(queue()))
        p = self._patch()
        with p[0], p[1], p[2]:
            assert ac.evaluate_gate(tmp_path, now=NOW + timedelta(minutes=15)).reason == ac.SKIP_QUEUE_NEAR_STALE

    def test_existing_cycle_detected(self, tmp_path):
        (tmp_path / "execution_queue.json").write_text(json.dumps(queue()))
        ac.start_cycle(ac.GateResult(True, None, run_id=RUN_ID, mode="live"), queue(), output_dir=tmp_path)
        p = self._patch()
        with p[0], p[1], p[2]:
            assert ac.evaluate_gate(tmp_path, now=NOW).reason == ac.SKIP_ALREADY_HANDLED

    def test_corrupt_queue_file_skips(self, tmp_path):
        (tmp_path / "execution_queue.json").write_text("{not json")
        p = self._patch()
        with p[0], p[1], p[2]:
            assert ac.evaluate_gate(tmp_path, now=NOW).reason == ac.SKIP_QUEUE_UNREADABLE

    def test_state_error_fails_closed(self, tmp_path):
        with mock.patch("execution.kill_switch.GlobalKillSwitch.is_active", side_effect=RuntimeError("x")):
            assert ac.evaluate_gate(tmp_path, now=NOW).reason == ac.SKIP_STATE_UNAVAILABLE


# ---------------------------------------------------------------------------
# lock
# ---------------------------------------------------------------------------

class TestLock:
    def test_second_holder_refused(self, tmp_path):
        with ac.cycle_lock(tmp_path) as first:
            assert first
            assert ac.lock_is_free(tmp_path) is False
        assert ac.lock_is_free(tmp_path) is True

    def test_launch_while_locked_skips_without_running(self, tmp_path):
        runner = mock.Mock()
        with ac.cycle_lock(tmp_path):
            assert ac.launch(output_dir=tmp_path, runner=runner) == ac.EXIT_SKIP
        runner.assert_not_called()

    def test_manual_begin_while_locked_skips(self, tmp_path, capsys):
        with ac.cycle_lock(tmp_path):
            assert ac.main(["--output-dir", str(tmp_path), "begin"]) == ac.EXIT_SKIP
        assert ac.SKIP_IN_PROGRESS in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cycle.json lifecycle
# ---------------------------------------------------------------------------

def started(tmp_path):
    return ac.start_cycle(ac.GateResult(True, None, run_id=RUN_ID, mode="live"), queue(),
                          now=NOW, output_dir=tmp_path)


class TestCycleState:
    def test_start_writes_pending_stages(self, tmp_path):
        started(tmp_path)
        s = ac.read_cycle(RUN_ID, tmp_path)
        assert s["status"] == "running" and s["queue_generated_at"] == GEN
        assert set(s["stages"]) == set(ac.STAGES)
        assert all(v["status"] == "pending" for v in s["stages"].values())
        assert not list((tmp_path / "agentic" / RUN_ID).glob("*.tmp"))

    def test_stage_order_is_risk_first(self):
        assert ac.STAGES == ("risk", "scout", "analyst", "trader")
        assert ac.STAGE_PREREQS["scout"] == ("risk",) and ac.STAGE_PREREQS["analyst"] == ("risk",)
        assert set(ac.STAGE_PREREQS["trader"]) == {"risk", "scout", "analyst"}

    @pytest.mark.parametrize("stage", ["scout", "analyst", "trader"])
    def test_nothing_starts_before_risk(self, tmp_path, stage):
        started(tmp_path)
        with pytest.raises(ValueError, match="before 'risk'"):
            ac.record_stage(RUN_ID, stage, "started", output_dir=tmp_path)

    @pytest.mark.parametrize("first,second", [("scout", "analyst"), ("analyst", "scout")])
    def test_scout_and_analyst_in_either_order_and_overlapping(self, tmp_path, first, second):
        started(tmp_path)
        ac.record_stage(RUN_ID, "risk", "started", output_dir=tmp_path)
        ac.record_stage(RUN_ID, "risk", "completed", output_dir=tmp_path)
        ac.record_stage(RUN_ID, first, "started", output_dir=tmp_path)
        ac.record_stage(RUN_ID, second, "started", output_dir=tmp_path)    # overlap
        ac.record_stage(RUN_ID, second, "completed", output_dir=tmp_path)
        with pytest.raises(ValueError, match=f"before '{first}'"):
            ac.record_stage(RUN_ID, "trader", "started", output_dir=tmp_path)
        ac.record_stage(RUN_ID, first, "completed", output_dir=tmp_path)
        state = ac.record_stage(RUN_ID, "trader", "started", output_dir=tmp_path)
        assert state["stages"]["trader"]["status"] == "running"

    def test_completed_stage_cannot_be_rerecorded(self, tmp_path):
        started(tmp_path)
        ac.record_stage(RUN_ID, "risk", "completed", output_dir=tmp_path)
        with pytest.raises(ValueError, match="already completed"):
            ac.record_stage(RUN_ID, "risk", "started", output_dir=tmp_path)

    def test_concurrent_scout_and_analyst_updates_are_not_lost(self, tmp_path):
        """Two agents record their stages at the same moment: the per-run lock
        serialises the read-modify-write, so neither update is lost (without
        it, the slow read below would make one overwrite the other)."""
        import threading
        started(tmp_path)
        ac.record_stage(RUN_ID, "risk", "completed", output_dir=tmp_path)
        real_read = ac.read_cycle

        def slow_read(*a, **kw):
            data = real_read(*a, **kw)
            time.sleep(0.05)
            return data

        barrier = threading.Barrier(2)
        errors = []

        def worker(stage):
            try:
                barrier.wait()
                ac.record_stage(RUN_ID, stage, "started", output_dir=tmp_path)
                barrier.wait()
                ac.record_stage(RUN_ID, stage, "completed", output_dir=tmp_path)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        with mock.patch.object(ac, "read_cycle", side_effect=slow_read):
            threads = [threading.Thread(target=worker, args=(s,)) for s in ("scout", "analyst")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        assert not errors
        stages = ac.read_cycle(RUN_ID, tmp_path)["stages"]
        assert stages["scout"]["status"] == "completed" and stages["analyst"]["status"] == "completed"
        assert not list((tmp_path / "agentic" / RUN_ID).glob("*.tmp"))

    def test_lock_timeout_is_an_error_not_a_lost_write(self, tmp_path):
        started(tmp_path)
        with ac._state_lock(RUN_ID, tmp_path):
            with pytest.raises(TimeoutError):
                with ac._state_lock(RUN_ID, tmp_path, timeout=0.05):
                    pass
        assert ac.main(["--output-dir", str(tmp_path), "stage", RUN_ID, "risk", "started"]) == ac.EXIT_RUN

    def test_full_run_completes_and_logs(self, tmp_path):
        started(tmp_path)
        for s in ac.STAGES:
            ac.record_stage(RUN_ID, s, "started", output_dir=tmp_path)
            ac.record_stage(RUN_ID, s, "completed", output_dir=tmp_path)
        state = ac.finish_cycle(RUN_ID, output_dir=tmp_path)
        assert state["status"] == "completed" and state["finished_at"]
        log = (tmp_path / "agentic" / ac.CYCLE_LOG_FILENAME).read_text().splitlines()
        assert json.loads(log[-1])["status"] == "completed"

    def test_finish_refuses_incomplete(self, tmp_path):
        started(tmp_path)
        with pytest.raises(ValueError, match="not completed"):
            ac.finish_cycle(RUN_ID, output_dir=tmp_path)

    def test_record_stage_refuses_after_failure(self, tmp_path):
        started(tmp_path)
        ac.fail_cycle(RUN_ID, "scout", "x", output_dir=tmp_path, notify=False)
        with pytest.raises(ValueError, match="failed"):
            ac.record_stage(RUN_ID, "risk", "started", output_dir=tmp_path)

    def test_unknown_stage_rejected(self, tmp_path):
        started(tmp_path)
        with pytest.raises(ValueError):
            ac.record_stage(RUN_ID, "bogus", "started", output_dir=tmp_path)


def _write_preflight(tmp_path, verdicts, gen=GEN, blockers=()):
    run = tmp_path / "agentic" / RUN_ID
    run.mkdir(parents=True, exist_ok=True)
    (run / "preflight.json").write_text(json.dumps(
        {"queue_generated_at": gen, "run_blockers": list(blockers), "verdicts": verdicts}))


VETOED_ALL = [
    {"client_order_id": "c-T", "symbol": "T", "side": "buy", "passed": False, "reasons": ["quote_stale"]},
    {"client_order_id": "c-F", "symbol": "F", "side": "buy", "passed": False, "reasons": ["buying_power_exhausted"]},
]


class TestNothingPassed:
    def _risk_done(self, tmp_path):
        started(tmp_path)
        ac.record_stage(RUN_ID, "risk", "started", output_dir=tmp_path)
        ac.record_stage(RUN_ID, "risk", "completed", output_dir=tmp_path)

    def test_finish_skips_the_rest(self, tmp_path):
        self._risk_done(tmp_path)
        _write_preflight(tmp_path, VETOED_ALL)
        state = ac.finish_cycle(RUN_ID, output_dir=tmp_path, nothing_passed=True)
        assert state["status"] == "completed" and state["reason"] == ac.REASON_NOTHING_PASSED
        for s in ("scout", "analyst", "trader"):
            assert state["stages"][s]["status"] == "skipped"
            assert state["stages"][s]["reason"] == ac.REASON_NOTHING_PASSED
        assert state["stages"]["risk"]["status"] == "completed"
        log = json.loads((tmp_path / "agentic" / ac.CYCLE_LOG_FILENAME).read_text().splitlines()[-1])
        assert log["status"] == "completed" and log["reason"] == ac.REASON_NOTHING_PASSED

    def test_run_blocker_counts_as_nothing_passed(self, tmp_path):
        self._risk_done(tmp_path)
        _write_preflight(tmp_path, VETOED_ALL, blockers=["market_closed"])
        assert ac.finish_cycle(RUN_ID, output_dir=tmp_path, nothing_passed=True)["status"] == "completed"

    def test_refused_when_something_passed(self, tmp_path):
        self._risk_done(tmp_path)
        _write_preflight(tmp_path, PREFLIGHT["verdicts"])
        with pytest.raises(ValueError, match="passed 1 intent"):
            ac.finish_cycle(RUN_ID, output_dir=tmp_path, nothing_passed=True)
        assert ac.read_cycle(RUN_ID, tmp_path)["status"] == "running"

    @pytest.mark.parametrize("setup", ["no_preflight", "other_queue", "risk_not_done"])
    def test_refused_without_proof(self, tmp_path, setup):
        if setup == "risk_not_done":
            started(tmp_path)
            _write_preflight(tmp_path, VETOED_ALL)
        else:
            self._risk_done(tmp_path)
            if setup == "other_queue":
                _write_preflight(tmp_path, VETOED_ALL, gen="2026-11-10T14:00:00+00:00")
        with pytest.raises(ValueError, match="nothing passed"):
            ac.finish_cycle(RUN_ID, output_dir=tmp_path, nothing_passed=True)

    def test_passed_cli(self, tmp_path, capsys):
        self._risk_done(tmp_path)
        assert ac.main(["--output-dir", str(tmp_path), "passed", RUN_ID]) == ac.EXIT_ERROR
        _write_preflight(tmp_path, PREFLIGHT["verdicts"])
        capsys.readouterr()
        assert ac.main(["--output-dir", str(tmp_path), "passed", RUN_ID]) == ac.EXIT_RUN
        out = json.loads(capsys.readouterr().out)
        assert out == {"n_passed": 1, "passed_client_order_ids": ["c-T"], "symbols": ["T"], "run_blockers": []}

    def test_finish_nothing_passed_cli_sends_summary(self, tmp_path, capsys):
        (tmp_path / "execution_queue.json").write_text(json.dumps(queue()))
        self._risk_done(tmp_path)
        _write_preflight(tmp_path, VETOED_ALL)
        notifier = mock.Mock(return_value=True)
        with mock.patch.object(ac, "_default_notifier", return_value=notifier):
            assert ac.main(["--output-dir", str(tmp_path), "finish", RUN_ID, "--nothing-passed",
                            "--notify"]) == ac.EXIT_RUN
        title, body = notifier.call_args.args
        assert title == "Agentic cycle: no orders sent" and "Vetoed:" in body
        assert ac.read_cycle(RUN_ID, tmp_path)["status"] == "completed"


class TestFailCycle:
    def test_marks_stage_and_pushes_once(self, tmp_path):
        started(tmp_path)
        for s in ("risk", "scout"):
            ac.record_stage(RUN_ID, s, "started", output_dir=tmp_path)
            ac.record_stage(RUN_ID, s, "completed", output_dir=tmp_path)
        notifier = mock.Mock(return_value=True)
        state = ac.fail_cycle(RUN_ID, None, "analyst crashed", output_dir=tmp_path, notifier=notifier)
        assert state["status"] == "failed" and state["failed_stage"] == "analyst"
        assert state["stages"]["analyst"]["status"] == "failed"
        notifier.assert_called_once()
        title, body = notifier.call_args.args
        assert "stopped" in title and "analyst crashed" in body
        log = (tmp_path / "agentic" / ac.CYCLE_LOG_FILENAME).read_text().splitlines()
        assert json.loads(log[-1])["failed_stage"] == "analyst"

    def test_unspecified_stage_prefers_the_running_one(self, tmp_path):
        started(tmp_path)
        ac.record_stage(RUN_ID, "risk", "completed", output_dir=tmp_path)
        ac.record_stage(RUN_ID, "analyst", "started", output_dir=tmp_path)   # scout still pending
        state = ac.fail_cycle(RUN_ID, None, "analyst hung", output_dir=tmp_path, notify=False)
        assert state["failed_stage"] == "analyst"

    def test_creates_state_when_cycle_never_started(self, tmp_path):
        state = ac.fail_cycle(RUN_ID, "launch", "no cli", output_dir=tmp_path, notify=False)
        assert ac.read_cycle(RUN_ID, tmp_path)["status"] == "failed"
        assert state["failed_stage"] == "launch"

    def test_notifier_error_never_raises(self, tmp_path):
        ac.fail_cycle(RUN_ID, "launch", "x", output_dir=tmp_path,
                      notifier=mock.Mock(side_effect=RuntimeError("down")))


def test_latest_cycle_picks_newest(tmp_path):
    for rid in ("20261110T140000Z", "20261110T150000Z"):
        ac.start_cycle(ac.GateResult(True, None, run_id=rid, mode="live"), queue(), output_dir=tmp_path)
    (tmp_path / "agentic" / "20261110T160000Z").mkdir()  # folder without cycle.json
    assert ac.latest_cycle(tmp_path)["run_id"] == "20261110T150000Z"
    assert ac.latest_cycle(tmp_path / "nowhere") is None


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------

PREFLIGHT = {"queue_generated_at": GEN, "run_blockers": [], "verdicts": [
    {"client_order_id": "c-T", "symbol": "T", "side": "buy", "passed": True, "qty": 7.0,
     "limit_price": 21.29, "reasons": []},
    {"client_order_id": "c-F", "symbol": "F", "side": "buy", "passed": False, "qty": None,
     "limit_price": None, "reasons": ["buying_power_exhausted"]},
]}


class TestBuildSummary:
    def test_live_lists_placed_with_thesis_and_vetoes(self):
        title, body = ac.build_summary(
            mode="live", preflight=PREFLIGHT,
            placed_records=[{"symbol": "T", "side": "buy", "qty": 7.0, "client_order_id": "c-T"}],
            valid_theses={"c-T": {"thesis": "Momentum leader."}})
        assert title == "1 trade awaiting your approval in Robinhood"
        assert "BUY 7 T, limit $21.29: Momentum leader." in body
        assert "F BUY (buying_power_exhausted)" in body

    def test_invalid_thesis_never_quoted(self):
        _, body = ac.build_summary(
            mode="live", preflight=PREFLIGHT,
            placed_records=[{"symbol": "T", "side": "buy", "qty": 7, "client_order_id": "c-T"}],
            valid_theses={})
        assert "thesis unavailable" in body

    def test_live_with_nothing_placed(self):
        title, _ = ac.build_summary(mode="live", preflight=PREFLIGHT, placed_records=[], valid_theses={})
        assert title == "Agentic cycle: no orders sent"

    def test_review_counts(self):
        title, body = ac.build_summary(mode="review", preflight=PREFLIGHT, placed_records=[],
                                       valid_theses={"c-T": {"thesis": "Why."}})
        assert title == "Review run: 1 would pass, 1 vetoed"
        assert "Would send BUY 7 T, limit $21.29: Why." in body

    def test_missing_preflight_and_blockers_stated(self):
        _, body = ac.build_summary(mode="live", preflight=None, placed_records=[], valid_theses={})
        assert "No preflight" in body
        _, body = ac.build_summary(mode="live", preflight={"run_blockers": ["queue_stale"], "verdicts": []},
                                   placed_records=[], valid_theses={})
        assert "queue_stale" in body

    def test_market_order_has_no_limit_text(self):
        pf = {"run_blockers": [], "verdicts": [dict(PREFLIGHT["verdicts"][0], limit_price=None)]}
        _, body = ac.build_summary(mode="live", preflight=pf, valid_theses={},
                                   placed_records=[{"symbol": "T", "side": "buy", "qty": 7, "client_order_id": "c-T"}])
        assert "limit" not in body

    def test_stale_pending_listed(self):
        _, body = ac.build_summary(mode="live", preflight=PREFLIGHT, placed_records=[], valid_theses={},
                                   stale_pending=[{"symbol": "AAPL", "side": "buy"}])
        assert "Still pending from earlier: buy AAPL" in body

    def test_long_thesis_shortened_before_warnings_are_dropped(self):
        long = {"c-T": {"thesis": "x" * 10_000, "invalidation": "y" * 5_000, "flags": ["earnings_in_2_days"]}}
        _, body = ac.build_summary(mode="live", preflight=PREFLIGHT, valid_theses=long,
                                   earnings={"T": "2026-11-12"},
                                   placed_records=[{"symbol": "T", "side": "buy", "qty": 7, "client_order_id": "c-T"}],
                                   stale_pending=[{"symbol": "AAPL", "side": "buy"}])
        assert len(body) <= ac.NTFY_MAX_CHARS
        assert "x" * 10_000 not in body and "…" in body
        # every warning survives
        for must in ("Flags: earnings_in_2_days", "Next earnings 2026-11-12",
                     "F BUY (buying_power_exhausted)", "Still pending from earlier: buy AAPL"):
            assert must in body

    def test_trimmed_to_ntfy_limit_as_last_resort(self):
        many = [{"client_order_id": f"v{i}", "symbol": f"S{i}", "side": "buy", "passed": False,
                 "reasons": ["buying_power_exhausted"]} for i in range(400)]
        _, body = ac.build_summary(mode="live", preflight={"run_blockers": [], "verdicts": many},
                                   placed_records=[], valid_theses={})
        assert len(body) <= ac.NTFY_MAX_CHARS and body.endswith("…")

    def test_order_line_has_invalidation_flags_and_earnings(self):
        _, body = ac.build_summary(
            mode="live", preflight=PREFLIGHT,
            placed_records=[{"symbol": "T", "side": "buy", "qty": 7.0, "client_order_id": "c-T"}],
            valid_theses={"c-T": {"thesis": "Momentum leader.", "invalidation": "Close below the 50-day.",
                                  "flags": ["earnings_in_2_days", "", None]}},
            earnings={"T": "2026-11-12"})
        assert ("BUY 7 T, limit $21.29: Momentum leader. | Invalid if: Close below the 50-day. | "
                "Flags: earnings_in_2_days | Next earnings 2026-11-12") in body

    def test_invalid_thesis_shows_no_invalidation_or_flags(self):
        _, body = ac.build_summary(
            mode="live", preflight=PREFLIGHT,
            placed_records=[{"symbol": "T", "side": "buy", "qty": 7, "client_order_id": "c-T"}],
            valid_theses={}, earnings={"T": "2026-11-12"})
        assert "thesis unavailable | Next earnings 2026-11-12" in body and "Invalid if" not in body

    def test_passed_but_not_sent_listed_in_live(self):
        title, body = ac.build_summary(mode="live", preflight=PREFLIGHT, placed_records=[],
                                       valid_theses={},
                                       not_sent_notes={"c-T": "skipped: in_chat_confirmation_unavailable"})
        assert title == "Agentic cycle: no orders sent"
        assert "Passed but not sent: BUY 7 T (skipped: in_chat_confirmation_unavailable)." in body
        _, body = ac.build_summary(mode="live", preflight=PREFLIGHT, placed_records=[], valid_theses={})
        assert "Passed but not sent: BUY 7 T (no send recorded by the Trader)." in body

    def test_review_mode_has_no_not_sent_section(self):
        _, body = ac.build_summary(mode="review", preflight=PREFLIGHT, placed_records=[], valid_theses={})
        assert "Passed but not sent" not in body

    def test_only_file_numbers_appear(self):
        """Every number in the push is a number in the inputs (no model prose)."""
        _, body = ac.build_summary(
            mode="live", preflight=PREFLIGHT,
            placed_records=[{"symbol": "T", "side": "buy", "qty": 7.0, "client_order_id": "c-T"}],
            valid_theses={"c-T": {"thesis": "Momentum leader."}})
        nums = set(re.findall(r"\d+(?:\.\d+)?", body))
        assert nums <= {"7", "21.29"}


class TestSummaryForRun:
    def _setup(self, tmp_path):
        (tmp_path / "execution_queue.json").write_text(json.dumps(queue()))
        run = tmp_path / "agentic" / RUN_ID
        (run / "inputs").mkdir(parents=True)
        (run / "preflight.json").write_text(json.dumps(PREFLIGHT))
        (run / "inputs" / "rec_T.json").write_text(json.dumps({"score": 0.42}))
        (run / "theses.json").write_text(json.dumps({"queue_generated_at": GEN, "theses": {
            "c-T": {"symbol": "T", "side": "buy", "thesis": "Score 0.42 leads the queue.",
                    "invalidation": "Score turns negative.", "horizon": "30d",
                    "drivers": [{"name": "score", "value": 0.42, "cite": "file:rec_T.json:score"}],
                    "flags": [], "data_gaps": []},
            "c-F": {"symbol": "F", "side": "buy", "thesis": "Target 99 is huge.",
                    "invalidation": "x", "horizon": "30d", "drivers": [], "flags": [], "data_gaps": []},
        }}))
        ac.start_cycle(ac.GateResult(True, None, run_id=RUN_ID, mode="live"), queue(), output_dir=tmp_path)
        rs.append_placed({"symbol": "T", "side": "buy", "qty": 7, "client_order_id": "c-T",
                          "ts": NOW.isoformat()}, output_dir=tmp_path)
        rs.append_placed({"symbol": "MSFT", "side": "buy", "qty": 1, "client_order_id": "other-run",
                          "ts": NOW.isoformat()}, output_dir=tmp_path)

    def test_uses_this_runs_files_only(self, tmp_path):
        self._setup(tmp_path)
        title, body, facts = ac.summary_for_run(RUN_ID, tmp_path)
        assert title.startswith("1 trade awaiting")
        assert "Score 0.42 leads the queue." in body
        assert "99" not in body          # the uncited thesis was rejected by the validator
        assert "MSFT" not in body        # another run's placement
        assert facts["n_placed"] == 1 and facts["has_preflight"] and facts["n_valid_theses"] == 1

    def test_stale_pending_from_approval_log(self, tmp_path):
        self._setup(tmp_path)
        rs.append_approval_event({"approval_id": "a1", "state": "pending", "symbol": "AAPL", "side": "buy"},
                                 output_dir=tmp_path)
        rs.append_approval_event({"approval_id": "a2", "state": "pending", "symbol": "T", "side": "buy"},
                                 output_dir=tmp_path)
        _, body, facts = ac.summary_for_run(RUN_ID, tmp_path)
        assert facts["n_stale_pending"] == 1 and "AAPL" in body

    def test_research_earnings_date_reaches_the_push(self, tmp_path):
        self._setup(tmp_path)
        (tmp_path / "agentic" / RUN_ID / "research.json").write_text(json.dumps({
            "queue_generated_at": GEN, "symbols": {
                "T": {"next_earnings": {"date": "2099-01-30", "source": "get_earnings_calendar"},
                      "analyst_ratings": None, "data_gaps": ["analyst_ratings"]},
                "F": {"next_earnings": {"date": "2000-01-01", "source": "get_earnings_calendar"},
                      "analyst_ratings": None, "data_gaps": ["analyst_ratings"]}}}))
        _, body, _ = ac.summary_for_run(RUN_ID, tmp_path)
        assert "Next earnings 2099-01-30" in body
        assert "2000-01-01" not in body       # a past date is a research error: never quoted

    def test_skipped_receipt_note_for_passed_order_not_sent(self, tmp_path):
        self._setup(tmp_path)
        (tmp_path / "execution_placed.jsonl").write_text("")   # nothing was sent after all
        (tmp_path / "execution_receipts.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"ts": (NOW - timedelta(days=1)).isoformat(), "symbol": "T", "side": "buy", "action": "skipped",
             "note": "old run"},
            {"ts": "2099-01-01T00:00:00+00:00", "symbol": "T", "side": "buy", "action": "skipped",
             "note": "in_chat_confirmation_unavailable"},
        ]) + "\n")
        _, body, facts = ac.summary_for_run(RUN_ID, tmp_path)
        assert facts["n_passed_not_sent"] == 1
        assert "Passed but not sent: BUY 7 T (skipped: in_chat_confirmation_unavailable)." in body
        assert "old run" not in body

    def test_preflight_for_other_queue_ignored(self, tmp_path):
        self._setup(tmp_path)
        (tmp_path / "agentic" / RUN_ID / "preflight.json").write_text(
            json.dumps(dict(PREFLIGHT, queue_generated_at="2026-11-10T14:00:00+00:00")))
        _, body, facts = ac.summary_for_run(RUN_ID, tmp_path)
        assert not facts["has_preflight"] and "No preflight" in body


# ---------------------------------------------------------------------------
# tool allowlist + launcher
# ---------------------------------------------------------------------------

class TestAllowedTools:
    def test_union_of_agent_frontmatter(self):
        tools = set(ac.allowed_tools())
        for name in ac.STAGE_AGENTS.values():
            assert set(ac.agent_tools(name)) <= tools
        assert set(ac.COORDINATOR_TOOLS) <= tools

    def test_order_tools_come_only_from_the_trader(self):
        others = set()
        for stage, name in ac.STAGE_AGENTS.items():
            if stage != "trader":
                others.update(ac.agent_tools(name))
        assert not [t for t in others if re.search(r"(place|cancel|decline|exercise)_", t)]

    def test_no_wildcards(self):
        assert not [t for t in ac.allowed_tools() if "*" in t]

    def test_missing_tools_line_raises(self, tmp_path):
        (tmp_path / "x.md").write_text("---\nname: x\n---\nbody")
        with pytest.raises(ValueError):
            ac.agent_tools("x", tmp_path)


class _Proc:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def _gate_ok(**over):
    return ac.GateResult(True, None, run_id=RUN_ID, mode="live", n_actionable=1, **over)


class TestLaunch:
    def test_skip_never_runs_claude(self, tmp_path):
        runner = mock.Mock()
        with mock.patch.object(ac, "evaluate_gate", return_value=ac.GateResult(False, ac.SKIP_MARKET_CLOSED)):
            assert ac.launch(output_dir=tmp_path, runner=runner) == ac.EXIT_SKIP
        runner.assert_not_called()

    def test_runs_coordinator_with_timeout_and_allowlist(self, tmp_path):
        def runner(cmd, **kw):
            assert cmd[:3] == ["claude", "-p", "/rh-agentic-cycle --launched"]
            assert cmd[3] == "--allowedTools" and set(cmd[4].split(",")) == set(ac.allowed_tools())
            assert kw["timeout"] == ac.LAUNCH_TIMEOUT_SECONDS
            # The coordinator ran the whole cycle.
            started(tmp_path)
            for s in ac.STAGES:
                ac.record_stage(RUN_ID, s, "started", output_dir=tmp_path)
                ac.record_stage(RUN_ID, s, "completed", output_dir=tmp_path)
            ac.finish_cycle(RUN_ID, output_dir=tmp_path)
            return _Proc(0)
        with mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            assert ac.launch(output_dir=tmp_path, runner=runner) == ac.EXIT_RUN

    def test_timeout_fails_the_running_cycle(self, tmp_path):
        notifier = mock.Mock(return_value=True)

        def runner(cmd, **kw):
            started(tmp_path)
            ac.record_stage(RUN_ID, "risk", "completed", output_dir=tmp_path)
            ac.record_stage(RUN_ID, "scout", "started", output_dir=tmp_path)
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        with mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            assert ac.launch(output_dir=tmp_path, runner=runner, notifier=notifier) == ac.EXIT_ERROR
        s = ac.read_cycle(RUN_ID, tmp_path)
        assert s["status"] == "failed" and s["failed_stage"] == "scout" and "timed out" in s["reason"]
        notifier.assert_called_once()

    def test_auth_failure_alert_says_why(self, tmp_path):
        """claude -p exits 1 with an expired login and never starts the cycle:
        the existing fail_cycle alert must carry claude's own message."""
        err = "Failed to authenticate: OAuth session expired and could not be refreshed"
        notifier = mock.Mock(return_value=True)
        with mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            rc = ac.launch(output_dir=tmp_path, runner=mock.Mock(return_value=_Proc(1, stdout=err)),
                           notifier=notifier)
        assert rc == ac.EXIT_ERROR
        state = ac.read_cycle(RUN_ID, tmp_path)
        assert state["failed_stage"] == "launch" and err in state["reason"]
        notifier.assert_called_once()
        assert err in " ".join(str(a) for a in notifier.call_args.args)

    def test_output_tail_helpers(self):
        p = _Proc(1, stdout="line one\n\nlast words  \n", stderr=b"stderr bytes".decode())
        assert ac._output_tail(p).endswith("stderr bytes")
        assert ac._last_line("a\n\n b \n") == "b"
        assert ac._output_tail(object()) == "" and ac._last_line("") == ""
        assert len(ac._output_tail(_Proc(1, stdout="x" * 2000))) == 500

    def test_said_prefers_claudes_stdout_over_stderr_warnings(self):
        noisy = _Proc(0, stdout="The review did not run: Robinhood MCP failed to connect.\n",
                      stderr="[mcp-sdk] SEP-2352: stored OAuth credential has no 'issuer' stamp\n")
        assert ac._said(noisy).startswith("The review did not run")
        assert ac._said(_Proc(1, stderr="only stderr")) == "only stderr"
        assert ac._said(object()) == ""

    def test_missing_cli_recorded(self, tmp_path):
        notifier = mock.Mock(return_value=True)
        with mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            rc = ac.launch(output_dir=tmp_path, runner=mock.Mock(side_effect=FileNotFoundError()),
                           notifier=notifier)
        assert rc == ac.EXIT_ERROR
        assert ac.read_cycle(RUN_ID, tmp_path)["failed_stage"] == "launch"

    def test_coordinator_left_cycle_running(self, tmp_path):
        def runner(cmd, **kw):
            started(tmp_path)
            return _Proc(0)
        with mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            rc = ac.launch(output_dir=tmp_path, runner=runner, notifier=mock.Mock())
        assert rc == ac.EXIT_ERROR and ac.read_cycle(RUN_ID, tmp_path)["status"] == "failed"

    def test_coordinator_skipped_itself(self, tmp_path, caplog):
        """claude exits 0 with no cycle.json and the re-run gate now skips
        (state changed in between): a quiet, logged skip."""
        notifier = mock.Mock()
        skip = ac.GateResult(False, ac.SKIP_QUEUE_NEAR_STALE, run_id=RUN_ID, mode="live")
        with caplog.at_level("INFO", logger=ac.logger.name), \
                mock.patch.object(ac, "evaluate_gate", side_effect=[_gate_ok(), skip]) as eg:
            rc = ac.launch(output_dir=tmp_path, runner=mock.Mock(return_value=_Proc(0)), notifier=notifier)
        assert rc == ac.EXIT_SKIP and ac.read_cycle(RUN_ID, tmp_path) is None
        assert eg.call_count == 2
        notifier.assert_not_called()
        assert any(ac.SKIP_QUEUE_NEAR_STALE in r.getMessage() and r.levelname == "INFO" for r in caplog.records)

    def test_coordinator_never_started_cycle_alerts(self, tmp_path, caplog):
        """Review #8: claude exits 0, no cycle.json, and the gate STILL says
        run: the coordinator failed to start the cycle. That is an alert and
        an error, never a silent skip."""
        notifier = mock.Mock(return_value=True)
        with caplog.at_level("WARNING", logger=ac.logger.name), \
                mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            rc = ac.launch(output_dir=tmp_path, notifier=notifier,
                           runner=mock.Mock(return_value=_Proc(0, stdout="I could not find the command.")))
        assert rc == ac.EXIT_ERROR
        state = ac.read_cycle(RUN_ID, tmp_path)
        assert state["status"] == "failed" and state["failed_stage"] == "launch"
        assert "without starting the cycle" in state["reason"] and "could not find" in state["reason"]
        notifier.assert_called_once()
        assert any(r.levelname == "WARNING" and "without starting the cycle" in r.getMessage()
                   for r in caplog.records)
        # the failed cycle.json marks the queue handled: the next tick won't retry it
        assert ac.gate(queue(), now=NOW, settings_mode="live", kill_switch_active=False, market_open=True,
                       existing_cycle=state).reason == ac.SKIP_ALREADY_HANDLED


class TestCli:
    def test_begin_stage_finish_round_trip(self, tmp_path, capsys):
        with mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            (tmp_path / "execution_queue.json").write_text(json.dumps(queue()))
            assert ac.main(["--output-dir", str(tmp_path), "begin", "--launched"]) == ac.EXIT_RUN
        for s in ac.STAGES:
            assert ac.main(["--output-dir", str(tmp_path), "stage", RUN_ID, s, "started"]) == ac.EXIT_RUN
            assert ac.main(["--output-dir", str(tmp_path), "stage", RUN_ID, s, "completed"]) == ac.EXIT_RUN
        capsys.readouterr()
        assert ac.main(["--output-dir", str(tmp_path), "finish", RUN_ID]) == ac.EXIT_RUN
        out = json.loads(capsys.readouterr().out)
        assert out["notified"] is None and "title" in out
        assert ac.read_cycle(RUN_ID, tmp_path)["status"] == "completed"

    def test_out_of_order_stage_is_error(self, tmp_path):
        started(tmp_path)
        assert ac.main(["--output-dir", str(tmp_path), "stage", RUN_ID, "trader", "started"]) == ac.EXIT_ERROR
        assert ac.main(["--output-dir", str(tmp_path), "stage", RUN_ID, "scout", "started"]) == ac.EXIT_ERROR

    def test_finish_early_fails_cycle(self, tmp_path):
        started(tmp_path)
        with mock.patch.object(ac, "_default_notifier", return_value=mock.Mock()):
            assert ac.main(["--output-dir", str(tmp_path), "finish", RUN_ID]) == ac.EXIT_ERROR
        assert ac.read_cycle(RUN_ID, tmp_path)["status"] == "failed"

    def test_fail_command(self, tmp_path):
        started(tmp_path)
        with mock.patch.object(ac, "_default_notifier", return_value=mock.Mock()):
            assert ac.main(["--output-dir", str(tmp_path), "fail", RUN_ID, "risk", "--reason", "boom"]) == 0
        assert ac.read_cycle(RUN_ID, tmp_path)["failed_stage"] == "risk"


# ---------------------------------------------------------------------------
# launchd plist + coordinator command
# ---------------------------------------------------------------------------

PLIST = REPO / "scripts" / "com.investyo.rh-agentic-cycle.plist"
COMMAND = REPO / ".claude" / "commands" / "rh-agentic-cycle.md"


class TestPlist:
    def test_parses_and_runs_the_launcher(self):
        data = plistlib.loads(PLIST.read_bytes())
        assert data["Label"] == "com.investyo.rh-agentic-cycle"
        assert data["ProgramArguments"][-3:] == ["-m", "execution.agentic_cycle", "launch"]
        assert data["StartInterval"] == 900
        assert not data.get("RunAtLoad") and not data.get("KeepAlive")

    def test_not_auto_installed(self):
        text = (REPO / "scripts" / "install_schedule.command").read_text()
        assert "rh-agentic-cycle" not in text


class TestCoordinatorCommand:
    text = COMMAND.read_text() if COMMAND.exists() else ""

    def test_stage_order(self):
        positions = [self.text.index(f"`{a}`") for a in ac.STAGE_AGENTS.values()]
        assert positions == sorted(positions)

    @pytest.mark.parametrize("phrase", [
        "python3 -m execution.agentic_cycle begin",
        "in_chat_confirmation_unavailable",
        "Never ask the operator in chat",
        "never call `decline_trade_approval`",
        "Do not trigger a daemon run",
        "execution.agentic_cycle fail",
        "execution.agentic_cycle finish",
        "stop the cycle",
    ])
    def test_pinned_phrases(self, phrase):
        assert phrase in self.text


@pytest.mark.parametrize("path", [
    REPO / ".claude" / "skills" / "robinhood-execution" / "SKILL.md",
    REPO / ".agents" / "skills" / "robinhood-execution" / "SKILL.md",
])
@pytest.mark.parametrize("phrase", [
    "## Unattended cycle (`/rh-agentic-cycle`)",
    "Do not trigger a daemon run",
    "`in_chat_confirmation_unavailable`",
    "Never call `decline_trade_approval` unattended",
])
def test_skill_unattended_rules_pinned(path, phrase):
    assert phrase in path.read_text()

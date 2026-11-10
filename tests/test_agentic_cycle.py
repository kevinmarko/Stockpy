"""Tests for execution/agentic_cycle.py — the unattended four-agent cycle (Phase 4).

Fully offline: no real `claude` CLI, no network, no Robinhood. The launcher's
subprocess runner and the ntfy notifier are injected fakes.
"""

from __future__ import annotations

import json
import plistlib
import re
import subprocess
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

    def test_stages_must_run_in_order(self, tmp_path):
        started(tmp_path)
        with pytest.raises(ValueError, match="before 'scout'"):
            ac.record_stage(RUN_ID, "analyst", "started", output_dir=tmp_path)

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
            ac.record_stage(RUN_ID, "scout", "started", output_dir=tmp_path)

    def test_unknown_stage_rejected(self, tmp_path):
        started(tmp_path)
        with pytest.raises(ValueError):
            ac.record_stage(RUN_ID, "bogus", "started", output_dir=tmp_path)


class TestFailCycle:
    def test_marks_stage_and_pushes_once(self, tmp_path):
        started(tmp_path)
        ac.record_stage(RUN_ID, "scout", "started", output_dir=tmp_path)
        ac.record_stage(RUN_ID, "scout", "completed", output_dir=tmp_path)
        notifier = mock.Mock(return_value=True)
        state = ac.fail_cycle(RUN_ID, None, "analyst crashed", output_dir=tmp_path, notifier=notifier)
        assert state["status"] == "failed" and state["failed_stage"] == "analyst"
        assert state["stages"]["analyst"]["status"] == "failed"
        notifier.assert_called_once()
        title, body = notifier.call_args.args
        assert "stopped" in title and "analyst crashed" in body
        log = (tmp_path / "agentic" / ac.CYCLE_LOG_FILENAME).read_text().splitlines()
        assert json.loads(log[-1])["failed_stage"] == "analyst"

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

    def test_trimmed_to_ntfy_limit(self):
        long = {"c-T": {"thesis": "x" * 10_000}}
        _, body = ac.build_summary(mode="live", preflight=PREFLIGHT, valid_theses=long,
                                   placed_records=[{"symbol": "T", "side": "buy", "qty": 7, "client_order_id": "c-T"}])
        assert len(body) <= ac.NTFY_MAX_CHARS and body.endswith("…")

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
    def __init__(self, rc=0):
        self.returncode = rc


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
            ac.record_stage(RUN_ID, "scout", "started", output_dir=tmp_path)
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        with mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            assert ac.launch(output_dir=tmp_path, runner=runner, notifier=notifier) == ac.EXIT_ERROR
        s = ac.read_cycle(RUN_ID, tmp_path)
        assert s["status"] == "failed" and s["failed_stage"] == "scout" and "timed out" in s["reason"]
        notifier.assert_called_once()

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

    def test_coordinator_skipped_itself(self, tmp_path):
        with mock.patch.object(ac, "evaluate_gate", return_value=_gate_ok()):
            rc = ac.launch(output_dir=tmp_path, runner=mock.Mock(return_value=_Proc(0)), notifier=mock.Mock())
        assert rc == ac.EXIT_SKIP and ac.read_cycle(RUN_ID, tmp_path) is None


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

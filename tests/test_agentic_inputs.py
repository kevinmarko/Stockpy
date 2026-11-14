"""Tests for execution/agentic_inputs.py — MCP response capture + assemble (K3 speed).

Fully offline: hook events are hand-built in the shapes Claude Code hands a
PostToolUse hook; no Robinhood, no claude CLI.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from execution import agentic_inputs as ai

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / ".claude" / "hooks" / "capture_agentic_mcp.sh"
NOW = datetime(2026, 11, 14, 15, 5, tzinfo=timezone.utc)
RUN_ID = "20261114T150000Z"
TOOL = "mcp__claude_ai_Robinhood__get_equity_positions"

POSITIONS = {"data": {"results": [{"symbol": "T", "quantity": "7"}], "next": None}, "guide": "..."}


def make_cycle(out, run_id=RUN_ID, status="running", started=NOW - timedelta(minutes=2)):
    d = out / "agentic" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "cycle.json").write_text(json.dumps({"run_id": run_id, "status": status,
                                              "started_at": started.isoformat()}))
    return d


def event(tool=TOOL, tool_input=None, response=None):
    return {"session_id": "s", "hook_event_name": "PostToolUse", "tool_name": tool,
            "tool_input": tool_input if tool_input is not None else {"account_number": "123"},
            "tool_response": response if response is not None else POSITIONS}


def raw_files(run_dir):
    raw = run_dir / "inputs" / "raw"
    return sorted(p.name for p in raw.glob("*.json")) if raw.exists() else []


# ---------------------------------------------------------------------------
# running cycle
# ---------------------------------------------------------------------------

class TestRunningRunDir:
    def test_none_when_nothing_runs(self, tmp_path):
        make_cycle(tmp_path, status="completed")
        assert ai.running_run_dir(tmp_path, now=NOW) is None
        assert ai.running_run_dir(tmp_path / "missing", now=NOW) is None

    def test_newest_started_at_wins(self, tmp_path):
        make_cycle(tmp_path, "20261114T140000Z", started=NOW - timedelta(minutes=50))
        make_cycle(tmp_path, "20261114T143000Z", started=NOW - timedelta(minutes=5))
        assert ai.running_run_dir(tmp_path, now=NOW).name == "20261114T143000Z"

    def test_zombie_running_cycle_ignored(self, tmp_path):
        make_cycle(tmp_path, started=NOW - ai.MAX_RUNNING_AGE - timedelta(minutes=1))
        assert ai.running_run_dir(tmp_path, now=NOW) is None

    def test_non_run_folders_and_bad_cycle_files_ignored(self, tmp_path):
        (tmp_path / "agentic" / "review").mkdir(parents=True)
        bad = tmp_path / "agentic" / "20261114T150500Z"
        bad.mkdir()
        (bad / "cycle.json").write_text("{bad")
        assert ai.running_run_dir(tmp_path, now=NOW) is None


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

class TestCapture:
    def test_saves_under_running_cycle_with_sequence(self, tmp_path):
        d = make_cycle(tmp_path)
        p1 = ai.capture_event(event(), tmp_path, now=NOW)
        p2 = ai.capture_event(event("mcp__claude_ai_Robinhood__get_equity_quotes", {"symbols": ["T"]}),
                              tmp_path, now=NOW)
        assert raw_files(d) == ["0001_get_equity_positions.json", "0002_get_equity_quotes.json"]
        rec = json.loads(p1.read_text())
        assert rec["tool_name"] == TOOL and rec["tool_input"] == {"account_number": "123"}
        assert rec["tool_response"] == POSITIONS and rec["captured_at"] == NOW.isoformat()
        assert p2.name.startswith("0002_")

    def test_no_running_cycle_is_a_no_op(self, tmp_path):
        d = make_cycle(tmp_path, status="completed")
        assert ai.capture_event(event(), tmp_path, now=NOW) is None
        assert raw_files(d) == []

    def test_non_mcp_tool_ignored(self, tmp_path):
        d = make_cycle(tmp_path)
        assert ai.capture_event(event(tool="Bash"), tmp_path, now=NOW) is None
        assert raw_files(d) == []

    def test_main_never_fails_and_prints_nothing(self, tmp_path, capsys):
        make_cycle(tmp_path)
        for text in ("", "not json", "[1, 2]", json.dumps(event())):
            assert ai.capture_main(io.StringIO(text), tmp_path) == 0
        assert capsys.readouterr().out == ""

    def test_main_logs_io_failure_and_still_exits_0(self, tmp_path, capsys, monkeypatch):
        make_cycle(tmp_path, started=datetime.now(timezone.utc))   # capture_main uses the real clock

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(ai, "_write_json_atomic", boom)
        assert ai.capture_main(io.StringIO(json.dumps(event())), tmp_path) == 0
        assert capsys.readouterr().out == ""
        assert "disk full" in (tmp_path / "agentic" / ai.ERROR_LOG_FILENAME).read_text()

    def test_cli_capture_exits_0_silently(self, tmp_path, capsys, monkeypatch):
        d = make_cycle(tmp_path, started=datetime.now(timezone.utc))
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event())))
        assert ai.main(["--output-dir", str(tmp_path), "capture"]) == 0
        assert capsys.readouterr().out == ""
        assert raw_files(d) == ["0001_get_equity_positions.json"]

    def test_short_tool_name(self):
        assert ai.short_tool_name(TOOL) == "get_equity_positions"
        assert ai.short_tool_name("mcp__investyo-platform__get_signal_breakdown") == "get_signal_breakdown"
        assert ai.short_tool_name("get_x/../../y") == "get_x_.._.._y"
        assert ai.short_tool_name(None) == "unknown"


# ---------------------------------------------------------------------------
# payload extraction
# ---------------------------------------------------------------------------

class TestExtractPayload:
    def test_dict_is_the_payload(self):
        assert ai.extract_payload(POSITIONS) == (True, POSITIONS)

    def test_json_string(self):
        assert ai.extract_payload(json.dumps(POSITIONS)) == (True, POSITIONS)

    def test_mcp_content_list(self):
        blocks = [{"type": "text", "text": json.dumps(POSITIONS)}]
        assert ai.extract_payload(blocks) == (True, POSITIONS)

    def test_call_tool_result_dict(self):
        assert ai.extract_payload({"content": [{"type": "text", "text": json.dumps(POSITIONS)}]}) == \
            (True, POSITIONS)
        assert ai.extract_payload({"content": [], "structuredContent": POSITIONS}) == (True, POSITIONS)

    def test_text_split_across_blocks(self):
        text = json.dumps(POSITIONS)
        blocks = [{"type": "text", "text": text[:10]}, {"type": "text", "text": text[10:]}]
        assert ai.extract_payload(blocks) == (True, POSITIONS)

    @pytest.mark.parametrize("resp", [
        "Error: rate limited",
        [{"type": "text", "text": "not json"}],
        {"isError": True, "content": [{"type": "text", "text": "{}"}]},
        None,
        42,
        json.dumps("just a string"),
    ])
    def test_unusable_is_never_guessed(self, resp):
        assert ai.extract_payload(resp)[0] is False


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------

def _capture(out, *events, start=NOW):
    for k, ev in enumerate(events):
        ai.capture_event(ev, out, now=start + timedelta(seconds=k))


class TestAssemble:
    def test_single_response(self, tmp_path):
        d = make_cycle(tmp_path)
        _capture(tmp_path, event(response=[{"type": "text", "text": json.dumps(POSITIONS)}]))
        res = ai.assemble(RUN_ID, "get_equity_positions", "positions.json", output_dir=tmp_path)
        assert res["n"] == 1 and res["written"] == str(d / "inputs" / "positions.json")
        assert json.loads((d / "inputs" / "positions.json").read_text()) == POSITIONS

    def test_pages_become_a_list_in_call_order(self, tmp_path):
        d = make_cycle(tmp_path)
        p1 = {"data": {"results": [{"symbol": "T"}], "next": "c2"}}
        p2 = {"data": {"results": [{"symbol": "F"}], "next": None}}
        _capture(tmp_path, event(tool_input={"account_number": "1"}, response=p1),
                 event("mcp__claude_ai_Robinhood__get_equity_quotes", {"symbols": ["T"]}, {"data": {}}),
                 event(tool_input={"account_number": "1", "cursor": "c2"}, response=p2))
        res = ai.assemble(RUN_ID, "get_equity_positions", "positions.json", output_dir=tmp_path)
        assert res["n"] == 2
        assert json.loads((d / "inputs" / "positions.json").read_text()) == [p1, p2]

    def test_retry_with_same_input_keeps_only_the_latest(self, tmp_path):
        """Two identical calls must not double a position."""
        d = make_cycle(tmp_path)
        old = {"data": {"results": [{"symbol": "T", "quantity": "5"}]}}
        _capture(tmp_path, event(response=old), event(response=POSITIONS))
        res = ai.assemble(RUN_ID, "get_equity_positions", "positions", output_dir=tmp_path)
        assert res["n"] == 1 and res["matched"] == 2
        assert json.loads((d / "inputs" / "positions.json").read_text()) == POSITIONS

    def test_where_filters_on_tool_input(self, tmp_path):
        d = make_cycle(tmp_path)
        _capture(tmp_path,
                 event(tool_input={"account_number": "MAIN"}, response={"data": {"who": "main"}}),
                 event(tool_input={"account_number": "AGENTIC"}, response={"data": {"who": "agentic"}}),
                 event("mcp__Robinhood__get_equity_quotes", {"symbols": ["T", "F"], "bounded": True},
                       {"data": {"q": 1}}))
        ai.assemble(RUN_ID, "get_equity_positions", "positions.json", where=["account_number=AGENTIC"],
                    output_dir=tmp_path)
        assert json.loads((d / "inputs" / "positions.json").read_text()) == {"data": {"who": "agentic"}}
        assert ai.assemble(RUN_ID, "get_equity_quotes", "q.json", where=["symbols=F", "bounded=true"],
                           output_dir=tmp_path)["n"] == 1
        assert "error" in ai.assemble(RUN_ID, "get_equity_quotes", "q.json", where=["symbols=MSFT"],
                                      output_dir=tmp_path)

    def test_mtime_is_the_oldest_capture_time(self, tmp_path):
        """The preflight's inputs_stale check reads mtime: it must be the data's age."""
        d = make_cycle(tmp_path)
        _capture(tmp_path, event(tool_input={"p": 1}), event(tool_input={"p": 2}))
        ai.assemble(RUN_ID, "get_equity_positions", "positions.json", output_dir=tmp_path)
        assert os.stat(d / "inputs" / "positions.json").st_mtime == pytest.approx(NOW.timestamp())

    def test_nothing_usable_writes_nothing(self, tmp_path):
        d = make_cycle(tmp_path)
        _capture(tmp_path, event(response="Error: upstream timeout"))
        res = ai.assemble(RUN_ID, "get_equity_positions", "positions.json", output_dir=tmp_path)
        assert "error" in res and res["unusable"] == 1
        assert not (d / "inputs" / "positions.json").exists()
        assert "error" in ai.assemble(RUN_ID, "get_portfolio", "p.json", output_dir=tmp_path)
        assert "error" in ai.assemble("20261114T090000Z", "get_portfolio", "p.json", output_dir=tmp_path)

    @pytest.mark.parametrize("bad", ["../x.json", "sub/x.json", ".hidden.json", ""])
    def test_out_must_be_a_plain_name(self, tmp_path, bad):
        make_cycle(tmp_path)
        with pytest.raises(ValueError):
            ai.assemble(RUN_ID, "get_equity_positions", bad, output_dir=tmp_path)

    def test_bad_run_id_refused(self, tmp_path):
        assert "error" in ai.assemble("../etc", "get_equity_positions", "x.json", output_dir=tmp_path)

    def test_cli_exit_codes_and_output(self, tmp_path, capsys):
        d = make_cycle(tmp_path)
        args = ["--output-dir", str(tmp_path), "assemble", RUN_ID, "--tool", "get_equity_positions",
                "--out", "positions.json"]
        assert ai.main(args) == ai.EXIT_NOTHING
        assert "error" in json.loads(capsys.readouterr().out)
        _capture(tmp_path, event())
        assert ai.main(args) == ai.EXIT_OK
        out = json.loads(capsys.readouterr().out)
        assert out["written"] == str(d / "inputs" / "positions.json") and out["n"] == 1
        assert ai.main(args[:-2] + ["--out", "../x.json"]) == ai.EXIT_NOTHING


# ---------------------------------------------------------------------------
# the hook script and its registration
# ---------------------------------------------------------------------------

def test_hook_registered_for_robinhood_and_investyo_mcp_tools():
    import re
    cfg = json.loads((REPO / ".claude" / "settings.json").read_text())
    entries = [e for e in cfg["hooks"]["PostToolUse"]
               if any("capture_agentic_mcp.sh" in h.get("command", "") for h in e["hooks"])]
    assert len(entries) == 1
    pat = re.compile(entries[0]["matcher"])
    for name in ("mcp__claude_ai_Robinhood__get_equity_positions", "mcp__robinhood-trading__get_portfolio",
                 "mcp__Robinhood__get_equity_orders", "mcp__investyo-platform__get_signal_breakdown",
                 "mcp__investyo__get_regime_status"):
        assert pat.fullmatch(name), name
    for name in ("Bash", "Edit", "mcp__claude-in-chrome__navigate"):
        assert not pat.fullmatch(name), name


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_hook_script_is_silent_and_exits_0_without_a_running_cycle(tmp_path):
    payload = json.dumps(event())
    proc = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True, text=True,
                          cwd=str(REPO), timeout=30)
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""

"""Tests for shared/agentic_approvals.py — the webapp's pending-approvals read side.

Offline; every file is written to a tmp OUTPUT_DIR.
"""

from __future__ import annotations

import json

from execution import agentic_cycle as ac
from execution import receipts_store as rs
from shared.agentic_approvals import build_approvals_view

RUN = "20261110T150000Z"
QUEUE = {"mode": "live", "generated_at": "2026-11-10T15:00:00+00:00", "intents": []}


def _cycle(tmp_path, valid_ids):
    ac.start_cycle(ac.GateResult(True, None, run_id=RUN, mode="live"), QUEUE, output_dir=tmp_path)
    ac.record_summary(RUN, "1 trade awaiting your approval in Robinhood", "body",
                      {"valid_thesis_ids": valid_ids}, tmp_path)
    (tmp_path / "agentic" / RUN / "theses.json").write_text(json.dumps({"theses": {
        "c-T": {"thesis": "Validated why.", "invalidation": "Below the 50-day."},
        "c-F": {"thesis": "Rejected text with 99.", "invalidation": "x"},
    }}))


def _placed(tmp_path, aid, sym, coid, run=RUN):
    rs.append_placed({"symbol": sym, "side": "buy", "qty": 7, "client_order_id": coid,
                      "approval_id": aid, "run_id": run, "ts": "2026-11-10T15:05:00+00:00"},
                     output_dir=tmp_path)


def test_empty_output_dir(tmp_path):
    v = build_approvals_view(tmp_path)
    assert v["pending"] == [] and v["recent"] == [] and v["n_pending"] == 0
    assert v["last_cycle"] is None and "last ones the Trader agent recorded" in v["note"]


def test_pending_with_validated_thesis_only(tmp_path):
    _cycle(tmp_path, ["c-T"])
    _placed(tmp_path, "a-T", "T", "c-T")
    _placed(tmp_path, "a-F", "F", "c-F")
    for aid, sym in (("a-T", "T"), ("a-F", "F")):
        rs.append_approval_event({"approval_id": aid, "state": "pending", "symbol": sym, "side": "buy",
                                  "ts": "2026-11-10T15:05:00+00:00"}, output_dir=tmp_path)
    v = build_approvals_view(tmp_path)
    by = {r["symbol"]: r for r in v["pending"]}
    assert v["n_pending"] == 2
    assert by["T"]["thesis"] == "Validated why." and by["T"]["invalidation"] == "Below the 50-day."
    assert by["F"]["thesis"] is None          # not in valid_thesis_ids -> never quoted
    assert by["T"]["qty"] == 7 and by["T"]["run_id"] == RUN
    assert v["last_cycle"]["run_id"] == RUN
    assert v["last_cycle"]["summary_title"].startswith("1 trade")


def test_latest_state_wins_and_moves_to_recent(tmp_path):
    rs.append_approval_event({"approval_id": "a1", "state": "pending", "symbol": "T", "side": "buy",
                              "ts": "2026-11-10T15:05:00+00:00"}, output_dir=tmp_path)
    rs.append_approval_event({"approval_id": "a1", "state": "STATE_APPROVED", "symbol": "T", "side": "buy",
                              "ts": "2026-11-10T15:20:00+00:00"}, output_dir=tmp_path)
    v = build_approvals_view(tmp_path)
    assert v["pending"] == []
    assert v["recent"][0]["state"] == "approved"
    assert v["recent"][0]["submitted_at"].startswith("2026-11-10T15:05")
    assert v["recent"][0]["recorded_at"].startswith("2026-11-10T15:20")
    assert v["recent"][0]["qty"] is None      # no placed record -> unknown, not 0


def test_recent_limit(tmp_path):
    for i in range(5):
        rs.append_approval_event({"approval_id": f"a{i}", "state": "declined", "symbol": "T",
                                  "ts": f"2026-11-10T15:0{i}:00+00:00"}, output_dir=tmp_path)
    v = build_approvals_view(tmp_path, recent_limit=3)
    assert [r["approval_id"] for r in v["recent"]] == ["a4", "a3", "a2"]


def test_corrupt_files_degrade(tmp_path):
    (tmp_path / rs.APPROVALS_FILENAME).write_text("{bad\n")
    (tmp_path / "agentic" / RUN).mkdir(parents=True)
    (tmp_path / "agentic" / RUN / "cycle.json").write_text("{bad")
    v = build_approvals_view(tmp_path)
    assert v["pending"] == [] and v["last_cycle"] is None


def test_burnin_block_from_latest_review(tmp_path):
    from datetime import date
    from execution import agentic_review as ar
    assert build_approvals_view(tmp_path)["burnin"] is None
    ar.save_input("orders", {"data": {"orders": []}}, day=date(2026, 11, 20), output_dir=tmp_path)
    ar.run_review(day=date(2026, 11, 20), output_dir=tmp_path, max_notional=200.0)
    b = build_approvals_view(tmp_path)["burnin"]
    assert b["day"] == "2026-11-20" and b["ready"] is False
    assert b["review_days"] == 0 and b["required_days"] == 10
    assert any("more day(s)" in u for u in b["unmet"]) and b["safety_failures"] == []

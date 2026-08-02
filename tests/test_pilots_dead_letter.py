"""Tests for pilots/dead_letter.py (the read helper) and GET /dead-letter,
POST /dead-letter/retry.

``pilots.dead_letter.read_dead_letter`` ports gui/dead_letter.py's read logic
into a plain, JSON-serializable dict resolved off ``settings.OUTPUT_DIR`` (so
it's testable the same way every other pilots_api reader is — via
``mock.patch.object(settings, "OUTPUT_DIR", tmp_path)`` — rather than the
repo-root-relative constant the Streamlit-side module uses). A missing/corrupt
file degrades to an honest empty shape with ``is_clean: None`` (CONSTRAINT #4:
"no run yet" is not the same claim as "the last run was clean") — never an
exception (CONSTRAINT #6).

``POST /dead-letter/retry`` reuses ``gui.orchestrator_runner.launch_symbol_retry``
(the SAME launcher the Streamlit Launcher tab's dead-letter Retry button
already calls) behind ``require_command_token`` STACKED with the dedicated
``DEAD_LETTER_RETRY_ENABLED`` master flag.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from settings import settings
from pilots import dead_letter as dead_letter_reader
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

_CMD_TOKEN = "cmd-tok"


# --------------------------------------------------------------------------- #
# read_dead_letter (reader-level)
# --------------------------------------------------------------------------- #
def test_read_dead_letter_missing_file_is_honest_not_fabricated(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        out = dead_letter_reader.read_dead_letter()
    assert out["entries"] == []
    assert out["is_clean"] is None  # "no run yet" -- not the same claim as "clean"
    assert out["reason"]


def test_read_dead_letter_clean_run(tmp_path: Path):
    (tmp_path / "dead_letter.json").write_text(
        json.dumps({
            "run_id": "run-2026-07-30",
            "generated_at": "2026-07-30T00:05:00+00:00",
            "entries": [],
        }),
        encoding="utf-8",
    )
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        out = dead_letter_reader.read_dead_letter()
    assert out["is_clean"] is True
    assert out["run_id"] == "run-2026-07-30"
    assert out["reason"] is None


def test_read_dead_letter_lists_failed_symbols(tmp_path: Path):
    (tmp_path / "dead_letter.json").write_text(
        json.dumps({
            "run_id": "run-1",
            "generated_at": "2026-07-30T00:05:00+00:00",
            "entries": [
                {"symbol": "HKIT", "stage": "strategy", "error": "boom", "timestamp": "2026-07-30T00:01:00+00:00"},
                {"symbol": "ZZZZ", "stage": "dto_construction", "error": "no data", "timestamp": "2026-07-30T00:02:00+00:00"},
            ],
        }),
        encoding="utf-8",
    )
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        out = dead_letter_reader.read_dead_letter()
    assert out["is_clean"] is False
    symbols = [e["symbol"] for e in out["entries"]]
    assert symbols == ["HKIT", "ZZZZ"]
    assert out["entries"][0]["stage"] == "strategy"


def test_read_dead_letter_corrupt_file_degrades_never_raises(tmp_path: Path):
    (tmp_path / "dead_letter.json").write_text("{not valid json,,,", encoding="utf-8")
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        out = dead_letter_reader.read_dead_letter()
    assert out["entries"] == []
    assert out["is_clean"] is None
    assert out["reason"]


def test_read_dead_letter_wrong_shape_degrades(tmp_path: Path):
    (tmp_path / "dead_letter.json").write_text(json.dumps({"entries": "not-a-list"}), encoding="utf-8")
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        out = dead_letter_reader.read_dead_letter()
    assert out["entries"] == []
    assert out["is_clean"] is None


# --------------------------------------------------------------------------- #
# GET /dead-letter
# --------------------------------------------------------------------------- #
def test_dead_letter_endpoint_shape(tmp_path: Path):
    (tmp_path / "dead_letter.json").write_text(
        json.dumps({"run_id": "r1", "generated_at": "2026-07-30T00:00:00+00:00", "entries": []}),
        encoding="utf-8",
    )
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/dead-letter")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_clean"] is True
    assert body["retry_enabled"] is False  # default off


def test_dead_letter_endpoint_reflects_retry_enabled_flag(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", True):
            resp = client.get("/dead-letter")
    assert resp.json()["retry_enabled"] is True


def test_dead_letter_endpoint_cold_start_is_honest_not_500(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/dead-letter")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entries"] == []
    assert body["reason"]


def test_dead_letter_endpoint_fail_open_no_token(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/dead-letter")
    assert resp.status_code == 200


def test_dead_letter_endpoint_401_on_wrong_token(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        resp = client.get("/dead-letter", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# POST /dead-letter/retry
# --------------------------------------------------------------------------- #
class TestDeadLetterRetryWrite:
    def _auth(self):
        return {"Authorization": f"Bearer {_CMD_TOKEN}"}

    def _post(self, symbol):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", True):
                with mock.patch("gui.orchestrator_runner.launch_symbol_retry") as m:
                    m.return_value = mock.Mock(pid=4242, log_path="output/gui_retry.log")
                    resp = client.post(
                        "/dead-letter/retry", json={"symbol": symbol}, headers=self._auth()
                    )
                    return resp, m

    def test_fails_closed_when_retry_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", False):
                resp = client.post(
                    "/dead-letter/retry", json={"symbol": "AAPL"}, headers=self._auth()
                )
        assert resp.status_code == 403

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", True):
                resp = client.post(
                    "/dead-letter/retry",
                    json={"symbol": "AAPL"},
                    headers={"Authorization": "Bearer anything"},
                )
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", True):
                resp = client.post(
                    "/dead-letter/retry",
                    json={"symbol": "AAPL"},
                    headers={"Authorization": "Bearer WRONG"},
                )
        assert resp.status_code == 401

    def test_happy_path_calls_launcher_exactly_once_with_uppercased_symbol(self):
        resp, m = self._post("aapl")
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["pid"] == 4242
        assert body["log_path"] == "output/gui_retry.log"
        assert body["applies"] == "immediately"
        m.assert_called_once_with("AAPL")

    def test_invalid_symbol_returns_422_and_never_launches(self):
        resp, m = self._post("NOT A TICKER!!")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_symbol"
        m.assert_not_called()

    def test_write_never_logs_token(self, caplog):
        with caplog.at_level("DEBUG"):
            self._post("AAPL")
        assert _CMD_TOKEN not in caplog.text


class TestDeadLetterRetryInvariants:
    def test_dead_letter_retry_enabled_is_not_gui_writable(self):
        """Mirrors test_automation_writes_enabled_is_not_gui_writable: a GUI
        bug must never flip this on. Neither allowlisted nor secret --
        hand-set in .env only."""
        assert "DEAD_LETTER_RETRY_ENABLED" not in pilots_api.env_io.ALLOWED_KEYS
        assert "DEAD_LETTER_RETRY_ENABLED" not in pilots_api.env_io.SECRET_KEYS

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

``POST /dead-letter/retry`` triggers ONE FULL orchestrator-daemon cycle through
``shared.daemon_client.trigger_run()`` (the same call ``POST /automation/run``
proxies, sharing its error -> HTTP mapping) behind ``require_command_token``
STACKED with the dedicated ``DEAD_LETTER_RETRY_ENABLED`` master flag. It is not
a per-symbol run (the old ``main.py`` subprocess never was either): the symbol
is validated and echoed but never reaches ``trigger_run``.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import settings
from pilots import dead_letter as dead_letter_reader
from shared.daemon_client import TriggerResponse
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

_CMD_TOKEN = "cmd-tok"

# Patch target named by the contract: the module attribute pilots_api calls
# through (``pilots_api.daemon_client`` is the ``shared.daemon_client`` module).
_TRIGGER = "shared.daemon_client.trigger_run"


def _trigger_response(**overrides) -> TriggerResponse:
    base = dict(
        ok=True,
        run_id="orch-1",
        state="queued",
        error=None,
        existing_run_id=None,
        kill_switch_reason=None,
    )
    base.update(overrides)
    return TriggerResponse(**base)


def _failure(error: str, **overrides) -> TriggerResponse:
    return _trigger_response(ok=False, run_id=None, state=None, error=error, **overrides)


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
    # Fail-open GET, no Authorization header sent -- STATE_API_TOKEN pinned
    # unset (matching test_dead_letter_endpoint_fail_open_no_token below) so
    # this doesn't depend on the machine's real .env leaving it unset.
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/dead-letter")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_clean"] is True
    assert body["retry_enabled"] is True  # default on


def test_dead_letter_endpoint_reflects_retry_enabled_flag(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", None):
        with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", False):
            resp = client.get("/dead-letter")
    assert resp.json()["retry_enabled"] is False


def test_dead_letter_endpoint_cold_start_is_honest_not_500(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", None):
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

    def _post(self, symbol, result=None):
        """POST with a valid command token and the flag on; ``trigger_run`` is
        patched (default: a successful trigger) and returned for assertions."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", True):
                with mock.patch(_TRIGGER) as m:
                    m.return_value = result if result is not None else _trigger_response()
                    resp = client.post(
                        "/dead-letter/retry", json={"symbol": symbol}, headers=self._auth()
                    )
                    return resp, m

    # -- auth / flag gates (nothing may reach the daemon when any gate fails) --
    def test_fails_closed_when_retry_disabled(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", False):
                with mock.patch(_TRIGGER) as m:
                    resp = client.post(
                        "/dead-letter/retry", json={"symbol": "AAPL"}, headers=self._auth()
                    )
        assert resp.status_code == 403
        m.assert_not_called()

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", True):
                with mock.patch(_TRIGGER) as m:
                    resp = client.post(
                        "/dead-letter/retry",
                        json={"symbol": "AAPL"},
                        headers={"Authorization": "Bearer anything"},
                    )
        assert resp.status_code == 403
        m.assert_not_called()

    def test_401_on_wrong_command_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", True):
                with mock.patch(_TRIGGER) as m:
                    resp = client.post(
                        "/dead-letter/retry",
                        json={"symbol": "AAPL"},
                        headers={"Authorization": "Bearer WRONG"},
                    )
        assert resp.status_code == 401
        m.assert_not_called()

    # -- success shape -------------------------------------------------------
    def test_happy_path_triggers_one_daemon_cycle_and_returns_run_id(self):
        resp, m = self._post("aapl")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"symbol", "run_id", "applies", "note"}
        assert body["symbol"] == "AAPL"
        assert body["run_id"] == "orch-1"
        assert body["applies"] == "immediately"
        assert isinstance(body["note"], str) and body["note"]
        m.assert_called_once_with()

    def test_success_has_no_pid_or_log_path(self):
        """The old subprocess launcher's fields are gone -- a daemon cycle has
        neither a child PID nor a per-retry log file."""
        resp, _ = self._post("AAPL")
        body = resp.json()
        assert "pid" not in body
        assert "log_path" not in body

    def test_run_id_is_null_not_fabricated_when_daemon_reports_none(self):
        resp, _ = self._post("AAPL", result=_trigger_response(run_id=None))
        assert resp.status_code == 200
        assert resp.json()["run_id"] is None

    @pytest.mark.parametrize("symbol", ["AAPL", "msft", " brk.b ", "ZZZZ"])
    def test_symbol_never_influences_what_is_triggered(self, symbol):
        """The retry is a full-universe cycle: trigger_run takes no symbol,
        so none may be passed, positionally or by keyword."""
        resp, m = self._post(symbol)
        assert resp.status_code == 200
        m.assert_called_once_with()
        assert m.call_args.args == ()
        assert m.call_args.kwargs == {}

    def test_note_is_honest_about_a_full_universe_cycle(self):
        resp, _ = self._post("aapl")
        note = resp.json()["note"]
        low = note.lower()
        assert "AAPL" in note
        assert "full" in low and "whole" in low  # whole universe, full cycle
        assert "not a retry of AAPL alone" in note
        assert "per-symbol" in low  # the old retry was never per-symbol either
        assert "dead_letter.json" in note  # a cycle is what rewrites it
        # The old endpoint's claim that no orders are placed is no longer true.
        assert "no orders placed" not in low
        assert "a full pipeline run" in low  # discloses the broader side effects

    # -- error mapping (shared with POST /automation/run) --------------------
    @pytest.mark.parametrize(
        "error,expected_status",
        [
            ("already_running", 409),
            ("kill_switch_active", 423),
            ("command_disabled", 503),
            ("unauthorized", 503),
            ("unavailable", 503),
            ("network_error", 503),
            ("unexpected_response", 503),
        ],
    )
    def test_each_trigger_error_maps_to_the_automation_run_status(self, error, expected_status):
        resp, m = self._post("AAPL", result=_failure(error))
        assert resp.status_code == expected_status
        m.assert_called_once_with()
        body = resp.json()
        assert "detail" in body
        # An error is never dressed up as a successful retry.
        assert "applies" not in body and "run_id" not in body and "note" not in body

    def test_409_surfaces_the_existing_run_id(self):
        resp, _ = self._post(
            "AAPL", result=_failure("already_running", existing_run_id="orch-old")
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["run_id"] == "orch-old"

    def test_423_surfaces_the_kill_switch_reason(self):
        resp, _ = self._post(
            "AAPL", result=_failure("kill_switch_active", kill_switch_reason="manual halt")
        )
        assert resp.status_code == 423
        assert resp.json()["detail"]["kill_switch_reason"] == "manual halt"

    def test_503_when_daemon_unreachable(self):
        resp, _ = self._post("AAPL", result=_failure("network_error"))
        assert resp.status_code == 503
        assert "not reachable" in resp.json()["detail"]

    def test_unauthorized_and_command_disabled_bodies_are_indistinguishable(self):
        """Never let a caller learn which side's token/config is wrong."""
        r1, _ = self._post("AAPL", result=_failure("unauthorized"))
        r2, _ = self._post("AAPL", result=_failure("command_disabled"))
        assert r1.status_code == r2.status_code == 503
        assert r1.json() == r2.json()

    def test_error_bodies_match_post_automation_run(self):
        """Reuse, not a re-implementation: for every trigger error the retry
        endpoint's response is byte-for-byte what /automation/run sends."""
        cases = [
            _failure("already_running", existing_run_id="orch-old"),
            _failure("kill_switch_active", kill_switch_reason="halt"),
            _failure("unauthorized"),
            _failure("unavailable"),
        ]
        for result in cases:
            retry, _ = self._post("AAPL", result=result)
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch(_TRIGGER, return_value=result):
                    run = client.post("/automation/run", headers=self._auth())
            assert retry.status_code == run.status_code
            assert retry.json() == run.json()

    # -- symbol validation ---------------------------------------------------
    def test_invalid_symbol_returns_422_and_never_triggers(self):
        resp, m = self._post("NOT A TICKER!!")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_symbol"
        m.assert_not_called()

    @pytest.mark.parametrize("symbol", ["../etc/passwd", "AAPL; rm -rf /", "$(id)", "A B"])
    def test_malformed_symbols_never_reach_the_daemon(self, symbol):
        resp, m = self._post(symbol)
        assert resp.status_code == 422
        m.assert_not_called()

    def test_empty_symbol_is_rejected_and_never_triggers(self):
        resp, m = self._post("")
        assert resp.status_code == 422
        m.assert_not_called()

    def test_write_never_logs_token(self, caplog):
        with caplog.at_level("DEBUG"):
            self._post("AAPL")
        assert _CMD_TOKEN not in caplog.text


class TestDeadLetterRetryNoLongerShellsOut:
    def test_pilots_api_has_no_reference_to_the_removed_subprocess_launcher(self):
        """launch_symbol_retry (a ``main.py`` subprocess) was deleted in shrink
        step 5.4a; a daemon cycle is the only path now."""
        assert "launch_symbol_retry" not in inspect.getsource(pilots_api)

    def test_endpoint_never_spawns_a_subprocess(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "DEAD_LETTER_RETRY_ENABLED", True):
                with mock.patch(_TRIGGER, return_value=_trigger_response()):
                    with mock.patch("subprocess.Popen") as popen, mock.patch(
                        "subprocess.run"
                    ) as run:
                        resp = client.post(
                            "/dead-letter/retry",
                            json={"symbol": "AAPL"},
                            headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                        )
        assert resp.status_code == 200
        popen.assert_not_called()
        run.assert_not_called()


class TestDeadLetterRetryInvariants:
    def test_dead_letter_retry_enabled_is_gui_writable(self):
        """2026-08-08 (PR #630 audit): reclassified into ALLOWED_KEYS by
        explicit operator decision -- not secret, so this no longer needs to
        be hand-set-only. Still a settings_keysets.DANGEROUS_KEYS member
        (typed confirmation required on write); the endpoint remains
        independently gated by FOLLOW_API_TOKEN regardless."""
        assert "DEAD_LETTER_RETRY_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "DEAD_LETTER_RETRY_ENABLED" not in pilots_api.env_io.SECRET_KEYS
        assert "DEAD_LETTER_RETRY_ENABLED" not in pilots_api.env_io.EXCLUDED_FROM_GUI

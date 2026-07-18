"""
tests/test_llm_setting_write.py
================================
Tests for ``PUT /llm/setting`` (api/pilots_api.py) — the AI Control Center's
write path: flipping a capability's ``toggle_key`` (e.g.
``LLM_COMMENTARY_ENABLED``) or a ``provider_selector_setting`` (e.g.
``LLM_COMMENTARY_RATIONALE_PROVIDER``) to ``.env``.

Mirrors ``tests/test_pilots_api.py::TestStrategyModulesWrite`` exactly: same
``TestClient``, same ``FOLLOW_API_TOKEN`` command-token fixture, same
fail-closed-master-flag-first assertion order. Kept in its own file (rather
than appended to the already-large ``test_pilots_api.py``) because it's a
self-contained, easily-reviewable slice — a pattern already used elsewhere in
this suite for focused write-endpoint coverage.

``env_io.write_setting`` is ALWAYS mocked in these tests — a real call would
write to the repo's actual ``.env`` file.
"""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api
from gui.env_io import DisallowedKeyError, SecretWriteError

client = TestClient(pilots_api.app)

_CMD_TOKEN = "cmd-tok"


def _put(key, value, token=_CMD_TOKEN):
    return client.put(
        "/llm/setting",
        json={"key": key, "value": value},
        headers={"Authorization": f"Bearer {token}"} if token is not None else {},
    )


class TestLlmSettingWriteAuth:
    def test_fails_closed_when_llm_writes_disabled(self):
        """Default posture: LLM_WRITES_ENABLED=False -> 403 even with a valid
        command token. This is the common case for most operators."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", False):
                resp = _put("LLM_COMMENTARY_ENABLED", True)
        assert resp.status_code == 403
        assert "LLM_WRITES_ENABLED" in resp.json()["detail"]

    def test_fails_closed_when_follow_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", True):
                resp = _put("LLM_COMMENTARY_ENABLED", True)
        assert resp.status_code == 403

    def test_401_on_wrong_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", True):
                resp = _put("LLM_COMMENTARY_ENABLED", True, token="wrong")
        assert resp.status_code == 401


class TestLlmSettingWriteHappyPath:
    def test_writes_bool_toggle_and_echoes_request(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", True):
                with mock.patch.object(
                    pilots_api.env_io, "write_setting", return_value="true"
                ) as w:
                    resp = _put("LLM_COMMENTARY_ENABLED", True)
        assert resp.status_code == 200
        w.assert_called_once_with("LLM_COMMENTARY_ENABLED", True)
        body = resp.json()
        assert body["written"] == ["LLM_COMMENTARY_ENABLED"]
        assert body["value"] is True
        assert body["applies"] == "next_daemon_restart"
        assert "not patched in-process" in body["note"]

    def test_writes_string_provider_selector(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", True):
                with mock.patch.object(
                    pilots_api.env_io, "write_setting", return_value="gemini"
                ) as w:
                    resp = _put("LLM_COMMENTARY_RATIONALE_PROVIDER", "gemini")
        assert resp.status_code == 200
        w.assert_called_once_with("LLM_COMMENTARY_RATIONALE_PROVIDER", "gemini")
        body = resp.json()
        assert body["written"] == ["LLM_COMMENTARY_RATIONALE_PROVIDER"]
        assert body["value"] == "gemini"

    def test_write_never_logs_token(self, caplog):
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(settings, "LLM_WRITES_ENABLED", True):
                    with mock.patch.object(pilots_api.env_io, "write_setting"):
                        _put("LLM_COMMENTARY_ENABLED", True)
        assert _CMD_TOKEN not in caplog.text


class TestLlmSettingWriteValidation:
    def test_rejects_secret_key_403(self):
        """A secret key (e.g. ANTHROPIC_API_KEY) is rejected via
        ai_control_center.validate_toggle_write's SecretWriteError (CONSTRAINT #3)
        BEFORE any env_io.write_setting call is attempted."""
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                    resp = _put("ANTHROPIC_API_KEY", "sk-ant-hijacked")
        assert resp.status_code == 403
        assert w.call_count == 0
        # Never echoes the attempted secret value back.
        assert "sk-ant-hijacked" not in resp.text

    def test_rejects_non_allowlisted_key_403(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", True):
                with mock.patch.object(pilots_api.env_io, "write_setting") as w:
                    resp = _put("NOT_A_REAL_SETTING", "whatever")
        assert resp.status_code == 403
        assert w.call_count == 0

    def test_validate_toggle_write_errors_are_env_io_classes(self):
        """Sanity: the exception classes the endpoint catches are literally the
        ones gui.ai_control_center.validate_toggle_write raises (not a
        lookalike defined elsewhere)."""
        from gui.ai_control_center import validate_toggle_write

        with pytest.raises(SecretWriteError):
            validate_toggle_write("ANTHROPIC_API_KEY")
        with pytest.raises(DisallowedKeyError):
            validate_toggle_write("NOT_A_REAL_SETTING")


class TestLlmSettingWriteInvariants:
    def test_llm_writes_enabled_is_not_gui_writable(self):
        """Mirrors test_strategy_writes_enabled_is_not_gui_writable /
        test_automation_writes_enabled_is_not_gui_writable: a GUI bug must
        never flip this on. Neither allowlisted nor secret — hand-set only."""
        assert "LLM_WRITES_ENABLED" not in pilots_api.env_io.ALLOWED_KEYS
        assert "LLM_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS

    def test_toggle_and_provider_keys_used_by_this_endpoint_are_allowlisted(self):
        for key in (
            "LLM_COMMENTARY_ENABLED",
            "LLM_COMMENTARY_RATIONALE_PROVIDER",
            "LLM_COMMENTARY_ALERT_PROVIDER",
            "GRAVITY_AI_RUNNER_ENABLED",
            "OPAL_RESEARCH_ENABLED",
            "OPAL_RESEARCH_PROVIDER",
        ):
            assert key in pilots_api.env_io.ALLOWED_KEYS


class TestLlmStatusWritableFlag:
    """GET /llm/status's additive `writable`/`writable_note` fields, added
    alongside this write endpoint so the PWA can show a read-only notice up
    front instead of waiting for a 403 (mirrors GET /automation/schedule's
    interval.writable and GET /strategy/matrix's writable)."""

    def test_writable_tracks_the_flag(self, tmp_path):
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", True):
                on = client.get("/llm/status").json()
            with mock.patch.object(settings, "LLM_WRITES_ENABLED", False):
                off = client.get("/llm/status").json()
        assert on["writable"] is True
        assert off["writable"] is False
        assert "LLM_WRITES_ENABLED=false" in off["writable_note"]

    def test_not_gated_by_read_token_absence(self, tmp_path):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
                resp = client.get("/llm/status")
        assert resp.status_code == 200
        assert "writable" in resp.json()

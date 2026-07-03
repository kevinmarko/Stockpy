"""
tests/test_gui_env_io_openai_key.py
=====================================
Pins the security boundary for the Opal (Tier 9 Scope 4) OpenAI credential.

* ``OPENAI_API_KEY`` MUST be in :data:`gui.env_io.SECRET_KEYS`.
* It MUST NOT be in :data:`gui.env_io.ALLOWED_KEYS`.
* Attempting to write it via the public ``write_setting`` API MUST raise
  :class:`gui.env_io.SecretWriteError` — it remains editable only via
  hand-editing ``.env`` (CONSTRAINT #3).

The three Opal non-secret toggles (``OPAL_RESEARCH_ENABLED``,
``OPAL_RESEARCH_PROVIDER``, ``OPAL_RESEARCH_MODEL``) MUST be in
``ALLOWED_KEYS`` so the AI Control Center tab can flip them without ever
touching the credential.
"""

from __future__ import annotations

import pytest

from gui import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, SecretWriteError, write_setting


_TOGGLES = (
    "OPAL_RESEARCH_ENABLED",
    "OPAL_RESEARCH_PROVIDER",
    "OPAL_RESEARCH_MODEL",
)


class TestSecretKeysContainOpenAIKey:
    def test_openai_api_key_is_secret(self):
        assert "OPENAI_API_KEY" in SECRET_KEYS, "OPENAI_API_KEY must be in SECRET_KEYS (CONSTRAINT #3)"


class TestAllowedKeysDoNotContainOpenAIKey:
    def test_openai_api_key_not_allowed(self):
        assert "OPENAI_API_KEY" not in ALLOWED_KEYS, "OPENAI_API_KEY must NOT be in ALLOWED_KEYS"


class TestAllowedKeysContainOpalToggles:
    @pytest.mark.parametrize("key", _TOGGLES)
    def test_toggle_is_allowed(self, key):
        assert key in ALLOWED_KEYS, f"{key} must be in ALLOWED_KEYS (operator-tunable)"

    @pytest.mark.parametrize("key", _TOGGLES)
    def test_toggle_not_secret(self, key):
        assert key not in SECRET_KEYS, f"{key} must NOT be in SECRET_KEYS"


class TestWriteSettingRejectsOpenAIKey:
    def test_write_setting_raises_secret_write_error(self, monkeypatch, tmp_path):
        # Redirect ENV_PATH to a tmp file so the test never touches the real .env.
        monkeypatch.setattr(env_io, "ENV_PATH", tmp_path / ".env", raising=False)
        with pytest.raises(SecretWriteError):
            write_setting("OPENAI_API_KEY", "sk-should-not-write")


class TestWriteSettingAllowsOpalToggles:
    @pytest.mark.parametrize("key,value", [
        ("OPAL_RESEARCH_ENABLED", "true"),
        ("OPAL_RESEARCH_PROVIDER", "openai"),
        ("OPAL_RESEARCH_MODEL", "gpt-4o"),
    ])
    def test_write_setting_round_trips_toggle(self, monkeypatch, tmp_path, key, value):
        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_io, "ENV_PATH", env_path, raising=False)
        write_setting(key, value)
        assert env_path.exists()
        contents = env_path.read_text(encoding="utf-8")
        assert key in contents


class TestReadSettingsMasksOpenAIKey:
    def test_read_settings_masks_openai_key_when_present(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("OPENAI_API_KEY=sk-real-secret-value\n", encoding="utf-8")
        monkeypatch.setattr(env_io, "ENV_PATH", env_path, raising=False)
        settings_view = env_io.read_settings()
        if "OPENAI_API_KEY" in settings_view:
            assert settings_view["OPENAI_API_KEY"] != "sk-real-secret-value"

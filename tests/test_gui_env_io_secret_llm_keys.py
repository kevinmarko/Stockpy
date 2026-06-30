"""
tests/test_gui_env_io_secret_llm_keys.py
=========================================
Pins the security boundary for Tier 9 LLM credentials.

* ``ANTHROPIC_API_KEY`` and ``GEMINI_API_KEY`` MUST be in
  :data:`gui.env_io.SECRET_KEYS`.
* They MUST NOT be in :data:`gui.env_io.ALLOWED_KEYS`.
* Attempting to write them via the public ``write_setting`` API MUST
  raise :class:`gui.env_io.SecretWriteError` — they remain editable
  only via hand-editing ``.env`` (CONSTRAINT #3).

The three non-secret toggles MUST be in ``ALLOWED_KEYS`` so the
Strategy Matrix tab can flip them without ever touching a credential.
"""

from __future__ import annotations

import pytest

from gui import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, SecretWriteError, write_setting


_API_KEYS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY")
_TOGGLES = (
    "LLM_COMMENTARY_ENABLED",
    "LLM_COMMENTARY_RATIONALE_PROVIDER",
    "LLM_COMMENTARY_ALERT_PROVIDER",
)


class TestSecretKeysContainAPIKeys:
    @pytest.mark.parametrize("key", _API_KEYS)
    def test_api_key_is_secret(self, key):
        assert key in SECRET_KEYS, f"{key} must be in SECRET_KEYS (CONSTRAINT #3)"


class TestAllowedKeysDoNotContainAPIKeys:
    @pytest.mark.parametrize("key", _API_KEYS)
    def test_api_key_not_allowed(self, key):
        assert key not in ALLOWED_KEYS, f"{key} must NOT be in ALLOWED_KEYS"


class TestAllowedKeysContainToggles:
    @pytest.mark.parametrize("key", _TOGGLES)
    def test_toggle_is_allowed(self, key):
        assert key in ALLOWED_KEYS, f"{key} must be in ALLOWED_KEYS (operator-tunable)"


class TestWriteSettingRejectsAPIKeys:
    @pytest.mark.parametrize("key", _API_KEYS)
    def test_write_setting_raises_secret_write_error(self, key, monkeypatch, tmp_path):
        # Redirect ENV_PATH to a tmp file so the test never touches the real .env.
        monkeypatch.setattr(env_io, "ENV_PATH", tmp_path / ".env", raising=False)
        with pytest.raises(SecretWriteError):
            write_setting(key, "should-not-write")

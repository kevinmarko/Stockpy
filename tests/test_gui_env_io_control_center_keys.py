"""
tests/test_gui_env_io_control_center_keys.py
============================================
Pins the AI Control Center env-write contract in ``gui/env_io.py``:

* The non-secret Control Center toggles (``GRAVITY_AI_RUNNER_ENABLED`` and the
  three ``OPAL_RESEARCH_*`` tunables) are in ``ALLOWED_KEYS`` so the Control
  Center can flip them.
* ``OPENAI_API_KEY`` stays in ``SECRET_KEYS`` (and NOT in ``ALLOWED_KEYS``);
  any write attempt raises ``SecretWriteError`` (CONSTRAINT #3).
"""

from __future__ import annotations

import pytest

from gui.env_io import (
    ALLOWED_KEYS,
    SECRET_KEYS,
    SecretWriteError,
    write_setting,
)


class TestControlCenterAllowedKeys:
    @pytest.mark.parametrize(
        "key",
        [
            "GRAVITY_AI_RUNNER_ENABLED",
            "OPAL_RESEARCH_ENABLED",
            "OPAL_RESEARCH_PROVIDER",
            "OPAL_RESEARCH_MODEL",
        ],
    )
    def test_toggle_in_allowed_keys(self, key: str) -> None:
        assert key in ALLOWED_KEYS

    def test_existing_llm_toggles_still_allowed(self) -> None:
        assert "LLM_COMMENTARY_ENABLED" in ALLOWED_KEYS


class TestOpenAIKeyIsSecretOnly:
    def test_openai_key_in_secret_keys(self) -> None:
        assert "OPENAI_API_KEY" in SECRET_KEYS

    def test_openai_key_not_in_allowed_keys(self) -> None:
        assert "OPENAI_API_KEY" not in ALLOWED_KEYS

    def test_write_openai_key_raises(self) -> None:
        # The secret guard fires before any file is touched (CONSTRAINT #3).
        with pytest.raises(SecretWriteError):
            write_setting("OPENAI_API_KEY", "sk-should-never-write")

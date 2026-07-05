"""
tests/test_gui_env_io_ntfy_topic.py
=====================================
Pins the security boundary for ``NTFY_TOPIC`` (alerting.notify()'s ntfy.sh
push topic, also used by the Tier 8 Robinhood execution-queue notifier in
execution/queue_builder.py).

* ``NTFY_TOPIC`` MUST be in :data:`gui.env_io.SECRET_KEYS`.
* It MUST NOT be in :data:`gui.env_io.ALLOWED_KEYS`.
* Attempting to write it via the public ``write_setting`` API MUST raise
  :class:`gui.env_io.SecretWriteError` — it remains editable only via
  hand-editing ``.env`` (CONSTRAINT #3).

A topic name functions like a bearer token for ntfy.sh (anyone who knows it
can publish to it or subscribe and read pushes) — ``alerting.py``'s own
docstring says to "keep the topic unguessable" — so it is classified
alongside the other webhook secrets (``DISCORD_WEBHOOK_URL`` etc.), not as a
plain operator tunable.
"""

from __future__ import annotations

import pytest

from gui import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, SecretWriteError, write_setting


class TestSecretKeysContainNtfyTopic:
    def test_ntfy_topic_is_secret(self):
        assert "NTFY_TOPIC" in SECRET_KEYS, "NTFY_TOPIC must be in SECRET_KEYS (CONSTRAINT #3)"


class TestAllowedKeysDoNotContainNtfyTopic:
    def test_ntfy_topic_not_allowed(self):
        assert "NTFY_TOPIC" not in ALLOWED_KEYS, "NTFY_TOPIC must NOT be in ALLOWED_KEYS"


class TestWriteSettingRejectsNtfyTopic:
    def test_write_setting_raises_secret_write_error(self, monkeypatch, tmp_path):
        # Redirect ENV_PATH to a tmp file so the test never touches the real .env.
        monkeypatch.setattr(env_io, "ENV_PATH", tmp_path / ".env", raising=False)
        with pytest.raises(SecretWriteError):
            write_setting("NTFY_TOPIC", "my-topic-should-not-write")


class TestReadSettingsMasksNtfyTopic:
    def test_read_settings_masks_ntfy_topic_when_present(self, monkeypatch, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("NTFY_TOPIC=my-real-unguessable-topic\n", encoding="utf-8")
        monkeypatch.setattr(env_io, "ENV_PATH", env_path, raising=False)
        settings_view = env_io.read_settings()
        assert settings_view["NTFY_TOPIC"] != "my-real-unguessable-topic"
        assert settings_view["NTFY_TOPIC"] == env_io.mask_secret("my-real-unguessable-topic")


class TestSecretsExpanderShowsNtfyTopic:
    def test_ntfy_topic_appears_in_secrets_table_source(self):
        # gui/panels/settings_manager.py's "🔒 Secrets (masked, read-only)"
        # expander iterates env_io.SECRET_KEYS directly — membership above is
        # sufficient for it to render a row, but pin the wiring explicitly so
        # a future refactor of that panel can't silently drop the iteration.
        from pathlib import Path

        source = Path("gui/panels/settings_manager.py").read_text(encoding="utf-8")
        assert "env_io.SECRET_KEYS" in source

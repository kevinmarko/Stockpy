"""
tests/test_gui_env_io_progress_key.py
======================================
Pins the addition of ``PROGRESS_POLL_SECONDS`` (the poll interval, in seconds,
for the Launcher tab's live pipeline-progress bar — see ``reporting/progress.py``
and ``gui/orchestrator_runner.py::compute_run_progress``) to ``gui/env_io.py``'s
``ALLOWED_KEYS``.

Contract asserted:
* ``PROGRESS_POLL_SECONDS`` is in ``ALLOWED_KEYS`` and is NOT a secret.
* It is not JSON-encoded (it is a plain scalar int).
* A round-trip through a temp ``.env`` via ``write_setting``/``get_value`` works
  and preserves unrelated lines.
* A disallowed key still raises ``DisallowedKeyError`` (the allowlist itself
  was not loosened).
* A genuine secret key still raises ``SecretWriteError`` (CONSTRAINT #3).

All writes are redirected to a temp ``.env`` via monkeypatching
``env_io.ENV_PATH`` so the real project ``.env`` is never touched.
"""

from __future__ import annotations

import pytest

from gui import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, DisallowedKeyError, SecretWriteError

KEY = "PROGRESS_POLL_SECONDS"


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# fixture\nRISK_FREE_RATE=0.045\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


def test_progress_poll_seconds_in_allowlist():
    assert KEY in ALLOWED_KEYS


def test_progress_poll_seconds_not_secret():
    assert KEY not in SECRET_KEYS
    assert env_io.is_secret(KEY) is False


def test_progress_poll_seconds_not_json_encoded():
    # Plain scalar int, never a JSON-serialized structure.
    assert KEY not in env_io._JSON_KEYS


def test_progress_poll_seconds_roundtrip(temp_env):
    encoded = env_io.write_setting(KEY, 10)
    assert encoded == "10"
    assert env_io.get_value(KEY) == "10"
    # Unrelated line preserved by set_key.
    assert "RISK_FREE_RATE=0.045" in temp_env.read_text(encoding="utf-8")


def test_disallowed_key_still_raises(temp_env):
    # The allowlist addition did not loosen the allowlist boundary itself.
    with pytest.raises(DisallowedKeyError):
        env_io.write_setting("SOME_RANDOM_UNLISTED_KEY", "value")
    assert "SOME_RANDOM_UNLISTED_KEY" not in temp_env.read_text(encoding="utf-8")


def test_secret_key_still_raises(temp_env):
    # A real secret must still be rejected.
    with pytest.raises(SecretWriteError):
        env_io.write_setting("FINNHUB_API_KEY", "should-never-write")
    assert "FINNHUB_API_KEY" not in temp_env.read_text(encoding="utf-8")

"""
tests/test_gui_env_io_daemon_shutdown_timeout_key.py
=====================================================
Pins the addition of ``DAEMON_SHUTDOWN_TIMEOUT_SECONDS`` (the persistent
orchestrator daemon's total graceful-shutdown budget — see ``settings.py``'s
field docstring and ``docs/RUNBOOK.md``'s §3.14 shutdown-budget ladder) to
``gui/env_io.py``'s ``ALLOWED_KEYS``. Mirrors
``tests/test_gui_env_io_progress_key.py``'s shape exactly.

Contract asserted:
* ``DAEMON_SHUTDOWN_TIMEOUT_SECONDS`` is in ``ALLOWED_KEYS`` and is NOT a secret.
* It is not JSON-encoded (it is a plain scalar float).
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

import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, DisallowedKeyError, SecretWriteError

KEY = "DAEMON_SHUTDOWN_TIMEOUT_SECONDS"


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# fixture\nRISK_FREE_RATE=0.045\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


def test_daemon_shutdown_timeout_in_allowlist():
    assert KEY in ALLOWED_KEYS


def test_daemon_shutdown_timeout_not_secret():
    assert KEY not in SECRET_KEYS
    assert env_io.is_secret(KEY) is False


def test_daemon_shutdown_timeout_not_json_encoded():
    # Plain scalar float, never a JSON-serialized structure.
    assert KEY not in env_io._JSON_KEYS


def test_daemon_shutdown_timeout_roundtrip(temp_env):
    encoded = env_io.write_setting(KEY, 30.0)
    assert encoded == "30.0"
    assert env_io.get_value(KEY) == "30.0"
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


def test_daemon_shutdown_timeout_has_a_settings_manager_widget():
    """Per this codebase's convention (CLAUDE.md's gui/env_io.py bullet):
    never add a GUI-writable setting without both ALLOWED_KEYS AND a
    _SETTINGS_LAYOUT widget."""
    from gui.panels.settings_manager import _SETTINGS_LAYOUT

    layout = dict(_SETTINGS_LAYOUT)
    assert KEY in layout
    assert layout[KEY] == "number"


def test_daemon_shutdown_timeout_widget_bounds_match_the_real_validator():
    """Regression guard: the Settings Manager widget's min/max for this key
    must stay in lockstep with settings.py's own field_validator bounds.
    Before this, the widget accepted any float -- submitting e.g. 0 would
    write successfully to .env, then fail Settings() construction on the
    daemon's next launch, potentially locking the operator out of this very
    UI needing to fix it."""
    from gui.panels.settings_manager import _NUMBER_BOUNDS
    from settings import (
        DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS,
        DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS,
    )

    assert _NUMBER_BOUNDS[KEY] == (
        DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS,
        DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS,
    )

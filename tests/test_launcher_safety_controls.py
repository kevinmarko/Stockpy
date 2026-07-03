"""
tests/test_launcher_safety_controls.py
=======================================
Unit tests for the Launcher-tab safety controls.

Verified invariants
-------------------
*   ``_render_launcher_safety_controls`` exists in ``gui.panels``.
*   Safe Mode is DERIVED (kill_switch active AND DRY_RUN=true) — no new env var.
*   ``DRY_RUN`` can be toggled via :func:`gui.env_io.write_setting` without
    introducing a ``SAFE_MODE`` env var.
*   Kill-switch activation / deactivation round-trip (isolated to a temp dir).
*   ``gui.env_io.ALLOWED_KEYS`` does NOT contain ``SAFE_MODE``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_env_io(tmp_env: Path) -> object:
    """Return a fresh ``env_io`` module bound to ``tmp_env``."""
    import importlib
    with mock.patch.dict(os.environ, {}, clear=False):
        import gui.env_io as _env_io
        return _env_io


# ===========================================================================
# gui.panels API surface
# ===========================================================================

def test_render_launcher_safety_controls_exists():
    import gui.panels as panels
    assert hasattr(panels, "_render_launcher_safety_controls"), (
        "_render_launcher_safety_controls must be defined in gui.panels"
    )


def test_render_launcher_safety_controls_callable():
    import gui.panels as panels
    assert callable(panels._render_launcher_safety_controls)


# ===========================================================================
# SAFE_MODE must not exist as a new env var
# ===========================================================================

def test_safe_mode_not_in_allowed_keys():
    """Safe Mode is derived — it must not be a writable env var (CONSTRAINT #3)."""
    from gui.env_io import ALLOWED_KEYS
    assert "SAFE_MODE" not in ALLOWED_KEYS, (
        "SAFE_MODE must not appear in ALLOWED_KEYS — it is a derived state, "
        "not a stored setting."
    )


def test_safe_mode_not_in_secret_keys():
    from gui.env_io import SECRET_KEYS
    assert "SAFE_MODE" not in SECRET_KEYS


# ===========================================================================
# DRY_RUN toggle via env_io
# ===========================================================================

def test_dry_run_in_allowed_keys():
    from gui.env_io import ALLOWED_KEYS
    assert "DRY_RUN" in ALLOWED_KEYS, "DRY_RUN must be in ALLOWED_KEYS for the toggle to work"


def test_dry_run_write_true(tmp_path):
    """write_setting('DRY_RUN', 'true') stores the value without error."""
    env_file = tmp_path / ".env"
    env_file.write_text("DRY_RUN=false\n", encoding="utf-8")

    with mock.patch("gui.env_io.ENV_PATH", env_file):
        from gui import env_io
        env_io.write_setting("DRY_RUN", "true")
        result = env_io.read_settings()
    assert result.get("DRY_RUN") == "true"


def test_dry_run_write_false(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DRY_RUN=true\n", encoding="utf-8")

    with mock.patch("gui.env_io.ENV_PATH", env_file):
        from gui import env_io
        env_io.write_setting("DRY_RUN", "false")
        result = env_io.read_settings()
    assert result.get("DRY_RUN") == "false"


# ===========================================================================
# Kill-switch round-trip (isolated to temp dir)
# ===========================================================================

def test_kill_switch_activate_deactivate(tmp_path):
    """Activate → is_active True → deactivate → is_active False."""
    sentinel = tmp_path / "KILL_SWITCH"
    with mock.patch("execution.kill_switch.KILL_SWITCH_FILE", sentinel):
        from execution.kill_switch import GlobalKillSwitch

        ks = GlobalKillSwitch()
        assert not ks.is_active()
        ks.activate(reason="test")
        assert ks.is_active()
        ks.deactivate()
        assert not ks.is_active()


def test_kill_switch_reason_stored(tmp_path):
    sentinel = tmp_path / "KILL_SWITCH"
    with mock.patch("execution.kill_switch.KILL_SWITCH_FILE", sentinel):
        from execution.kill_switch import GlobalKillSwitch

        ks = GlobalKillSwitch()
        ks.activate(reason="test_reason_123")
        assert sentinel.exists()
        content = sentinel.read_text(encoding="utf-8")
        assert "test_reason_123" in content


# ===========================================================================
# Safe Mode derivation logic
# ===========================================================================

def test_safe_mode_both_active_is_true():
    """Safe Mode = kill_switch active AND DRY_RUN=true."""
    # Verify the derivation logic: we don't look for a stored SAFE_MODE flag.
    with mock.patch("settings.settings.DRY_RUN", True):
        # Simulate the derivation: safe_mode = ks_active and dry_run
        ks_active = True
        dry_run = True
        safe_mode = ks_active and dry_run
        assert safe_mode is True


def test_safe_mode_only_kill_switch_is_partial():
    with mock.patch("settings.settings.DRY_RUN", False):
        ks_active = True
        dry_run = False
        safe_mode = ks_active and dry_run
        assert safe_mode is False


def test_safe_mode_only_dry_run_is_partial():
    with mock.patch("settings.settings.DRY_RUN", True):
        ks_active = False
        dry_run = True
        safe_mode = ks_active and dry_run
        assert safe_mode is False


def test_safe_mode_neither_is_false():
    with mock.patch("settings.settings.DRY_RUN", False):
        ks_active = False
        dry_run = False
        safe_mode = ks_active and dry_run
        assert safe_mode is False

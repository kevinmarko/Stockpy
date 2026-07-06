"""
tests/test_launcher_maintenance.py
==================================
Unit tests for the Launcher-tab **Maintenance & Diagnostics** section
(Agent 4 of the 5-workstream Command Center feature).

Verified invariants
-------------------
*   ``_render_maintenance_diagnostics`` exists in ``gui.panels.launcher`` and is
    callable (imported directly from the submodule — no ``__init__`` re-export
    is required, keeping ``gui/panels/__init__.py`` single-owner).
*   The function's no-click path (every ``st.button`` returns ``False``) runs
    without raising when Streamlit is stubbed with a minimal fake module — the
    defensive ``try/except ImportError`` guards mean it does not depend on the
    parallel-built ``gui.command_runner`` / ``launch_pytest`` / ``launch_verify``
    contracts existing yet.
*   ``render_launcher`` actually wires the section in (source-level guard).

These are deliberately lightweight: Streamlit UI functions are hard to unit-test
directly, so we assert existence + a stubbed no-click smoke run + the wiring
guard, per the task brief.
"""

from __future__ import annotations

import inspect
import sys
import types
from unittest import mock


# ---------------------------------------------------------------------------
# A minimal Streamlit stub sufficient for the no-click render path.
# ---------------------------------------------------------------------------

class _FakeExpander:
    """Context manager standing in for ``st.expander(...)``."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeColumn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _make_fake_streamlit() -> types.ModuleType:
    """Build a fake ``streamlit`` module whose widgets take the no-click path.

    Every ``button``/``checkbox`` returns ``False`` (nothing clicked), so the
    render function walks its idle branch without touching the (possibly
    absent) command-runner / launch-* modules.
    """
    st = types.ModuleType("streamlit")

    st.expander = lambda *a, **k: _FakeExpander()
    st.spinner = lambda *a, **k: _FakeExpander()
    st.columns = lambda spec, **k: [
        _FakeColumn() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    st.button = lambda *a, **k: False
    st.checkbox = lambda *a, **k: False
    st.caption = lambda *a, **k: None
    st.markdown = lambda *a, **k: None
    st.divider = lambda *a, **k: None
    st.code = lambda *a, **k: None
    st.success = lambda *a, **k: None
    st.error = lambda *a, **k: None
    st.info = lambda *a, **k: None
    st.warning = lambda *a, **k: None
    st.rerun = lambda *a, **k: None
    # A dict is a good-enough stand-in for st.session_state for .get()/.pop().
    st.session_state = {}
    return st


# ===========================================================================
# API surface
# ===========================================================================

def test_render_maintenance_diagnostics_importable():
    from gui.panels.launcher import _render_maintenance_diagnostics
    assert _render_maintenance_diagnostics is not None


def test_render_maintenance_diagnostics_callable():
    from gui.panels.launcher import _render_maintenance_diagnostics
    assert callable(_render_maintenance_diagnostics)


def test_launcher_module_imports_cleanly():
    """The whole submodule imports without error."""
    import gui.panels.launcher as launcher_mod
    assert launcher_mod is not None


# ===========================================================================
# No-click smoke run with a stubbed Streamlit
# ===========================================================================

def test_no_click_path_does_not_raise():
    """Calling the render fn with a stubbed streamlit + no clicks must not raise.

    The stubbed ``st.button`` returns ``False`` for every control, so none of
    the click branches (which import the parallel-built runner modules) run —
    exercising the idle render path deterministically and offline.
    """
    import gui.panels.launcher as launcher_mod

    fake_st = _make_fake_streamlit()
    with mock.patch.object(launcher_mod, "st", fake_st):
        # Must not raise even when session_state has no maintenance handle.
        launcher_mod._render_maintenance_diagnostics()


def test_no_click_path_with_finished_handle_does_not_raise():
    """A finished (not-running) handle in session_state renders without raising."""
    import gui.panels.launcher as launcher_mod

    fake_st = _make_fake_streamlit()

    fake_handle = mock.Mock()
    fake_handle.is_running.return_value = False
    fake_handle.returncode.return_value = 0
    fake_handle.mode = "pytest"
    fake_handle.pid = 4321
    fake_st.session_state = {"maintenance_run_handle": fake_handle}

    with mock.patch.object(launcher_mod, "st", fake_st):
        launcher_mod._render_maintenance_diagnostics()


# ===========================================================================
# Wiring guard — render_launcher calls the section
# ===========================================================================

def test_render_launcher_wires_maintenance_section():
    """render_launcher must invoke _render_maintenance_diagnostics()."""
    from gui.panels.launcher import render_launcher

    src = inspect.getsource(render_launcher)
    assert "_render_maintenance_diagnostics()" in src, (
        "render_launcher must call _render_maintenance_diagnostics() so the "
        "Maintenance & Diagnostics section is rendered on the Launcher tab."
    )

"""tests/test_progress_ui.py
=============================
Unit tests for :mod:`gui.progress_ui` — the shared "busy/working" indicator
helper — plus an import-smoke test for every panel this task edited.

``busy()`` must be safe to use OUTSIDE a live Streamlit script run (no
Streamlit server, no ``ScriptRunContext``) so a plain ``pytest`` process can
exercise it directly, and it must never swallow an exception raised inside
its ``with`` block (CONSTRAINT #6 — dead-letter: a busy indicator must never
mask the real error or crash the panel silently).
"""

from __future__ import annotations

import importlib

import pytest

from gui.progress_ui import busy


# ---------------------------------------------------------------------------
# Headless behavior (no Streamlit script-run context active — the normal
# state for a pytest process). This is the path exercised by default because
# tests run outside a live `streamlit run` invocation.
# ---------------------------------------------------------------------------


class TestBusyHeadless:
    def test_yields_control(self):
        """The context manager must actually run the wrapped block."""
        ran = False
        with busy("doing work"):
            ran = True
        assert ran is True

    def test_normal_block_completes_without_error(self):
        """A successful block should not raise or alter its return value."""
        result = None
        with busy("computing", done="done computing"):
            result = 1 + 1
        assert result == 2

    def test_exception_propagates_not_swallowed(self):
        """CONSTRAINT #6: an exception inside the block must be re-raised,
        never swallowed by the busy-indicator bookkeeping."""
        with pytest.raises(ValueError, match="boom"):
            with busy("risky work"):
                raise ValueError("boom")

    def test_exception_type_and_message_preserved(self):
        """The exact exception instance/type must survive the wrapper."""
        sentinel = RuntimeError("specific failure detail")
        with pytest.raises(RuntimeError) as excinfo:
            with busy("risky work 2"):
                raise sentinel
        assert excinfo.value is sentinel

    def test_no_streamlit_runtime_does_not_crash(self):
        """Calling busy() with no active Streamlit script-run context (the
        default outside `streamlit run`) must not raise merely because
        there's no UI to render into."""
        # This IS the default test environment already, but assert it
        # explicitly and directly via the internal probe too.
        from gui import progress_ui

        assert progress_ui._has_script_run_ctx() is False
        with busy("no-op in headless mode"):
            pass  # must not raise

    def test_nested_busy_blocks_headless(self):
        """Nesting should not deadlock or error in the headless fallback."""
        order = []
        with busy("outer"):
            order.append("outer-start")
            with busy("inner"):
                order.append("inner")
            order.append("outer-end")
        assert order == ["outer-start", "inner", "outer-end"]


# ---------------------------------------------------------------------------
# Simulated "live" Streamlit runtime — monkeypatch the two internal probes so
# we can assert the st.status(...) -> status.update(state=...) state machine
# without needing a real Streamlit server.
# ---------------------------------------------------------------------------


class _FakeStatus:
    """Minimal stand-in for the object returned by ``st.status(...)``."""

    def __init__(self, label: str, expanded: bool = True):
        self.initial_label = label
        self.expanded = expanded
        self.updates: list[dict] = []
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        # Mimic a real context manager: don't swallow exceptions.
        return False

    def update(self, *, label=None, state=None):
        self.updates.append({"label": label, "state": state})


class _FakeStreamlit:
    """Minimal stand-in for the ``streamlit`` module, exposing only
    ``.status(...)`` (the only attribute ``busy()`` touches)."""

    def __init__(self):
        self.last_status: "_FakeStatus | None" = None

    def status(self, label, expanded=True):
        self.last_status = _FakeStatus(label, expanded=expanded)
        return self.last_status


class TestBusySimulatedStreamlit:
    def _patch_live(self, monkeypatch, fake_st: "_FakeStreamlit"):
        from gui import progress_ui

        monkeypatch.setattr(progress_ui, "_try_import_streamlit", lambda: fake_st)
        monkeypatch.setattr(progress_ui, "_has_script_run_ctx", lambda: True)

    def test_success_marks_complete(self, monkeypatch):
        fake_st = _FakeStreamlit()
        self._patch_live(monkeypatch, fake_st)

        with busy("Fetching thing…"):
            pass

        status = fake_st.last_status
        assert status is not None
        assert status.entered and status.exited
        assert status.updates[-1]["state"] == "complete"
        assert status.updates[-1]["label"] == "✅ Fetching thing…"

    def test_success_uses_custom_done_label(self, monkeypatch):
        fake_st = _FakeStreamlit()
        self._patch_live(monkeypatch, fake_st)

        with busy("Fetching thing…", done="All fetched"):
            pass

        status = fake_st.last_status
        assert status.updates[-1] == {"label": "All fetched", "state": "complete"}

    def test_error_marks_error_state_then_reraises(self, monkeypatch):
        fake_st = _FakeStreamlit()
        self._patch_live(monkeypatch, fake_st)

        with pytest.raises(ValueError):
            with busy("Fetching thing…"):
                raise ValueError("network down")

        status = fake_st.last_status
        assert status is not None
        assert status.updates[-1]["state"] == "error"
        assert status.updates[-1]["label"] == "❌ Fetching thing…"

    def test_status_update_failure_does_not_mask_original_error(self, monkeypatch):
        """Even if status.update() itself blows up while recording the error
        state, the ORIGINAL exception must still propagate (never a
        secondary bookkeeping error masking the real one)."""
        from gui import progress_ui

        class _BrokenStatus(_FakeStatus):
            def update(self, *, label=None, state=None):
                raise RuntimeError("status backend exploded")

        class _BrokenStreamlit:
            def status(self, label, expanded=True):
                return _BrokenStatus(label, expanded=expanded)

        monkeypatch.setattr(progress_ui, "_try_import_streamlit", lambda: _BrokenStreamlit())
        monkeypatch.setattr(progress_ui, "_has_script_run_ctx", lambda: True)

        with pytest.raises(ValueError, match="original failure"):
            with busy("Fetching thing…"):
                raise ValueError("original failure")

    def test_st_status_construction_failure_degrades_to_noop(self, monkeypatch):
        """If st.status(...) itself raises when constructing (e.g. an
        unusual embedding), busy() must degrade to running the block plainly
        rather than crashing the panel."""
        from gui import progress_ui

        class _BadStreamlit:
            def status(self, label, expanded=True):
                raise RuntimeError("st.status unavailable")

        monkeypatch.setattr(progress_ui, "_try_import_streamlit", lambda: _BadStreamlit())
        monkeypatch.setattr(progress_ui, "_has_script_run_ctx", lambda: True)

        ran = False
        with busy("Fetching thing…"):
            ran = True
        assert ran is True

    def test_streamlit_not_importable_degrades_to_noop(self, monkeypatch):
        """When streamlit itself isn't importable, busy() must still run the
        block (headless-safe import path)."""
        from gui import progress_ui

        monkeypatch.setattr(progress_ui, "_try_import_streamlit", lambda: None)

        ran = False
        with busy("Fetching thing…"):
            ran = True
        assert ran is True


# ---------------------------------------------------------------------------
# Import-smoke test: every panel this task edited (to wrap silent buttons in
# `busy()`) must still import cleanly. Streamlit is a hard requirement per
# requirements.txt, so these imports are expected to succeed in this repo's
# test environment; a failure here means a wrapping edit broke the module.
# ---------------------------------------------------------------------------


EDITED_PANEL_MODULES = [
    "gui.panels.paper_monitor",
    "gui.panels.live_inventory",
    "gui.panels.settings_manager",
    "gui.panels.strategy_matrix",
    "gui.panels.report_viewer",
    "gui.panels.market_data",
    "gui.panels.analytics",
    "gui.panels.reports_library",
]


@pytest.mark.parametrize("module_name", EDITED_PANEL_MODULES)
def test_edited_panel_imports_cleanly(module_name):
    mod = importlib.import_module(module_name)
    assert mod is not None

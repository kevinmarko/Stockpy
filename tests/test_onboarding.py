"""
tests/test_onboarding.py
========================
Offline unit tests for ``gui/onboarding.py`` (§5.3 of the GUI Help
Explainers plan, Prompt 5 deliverable).

All tests are headless — no Streamlit runtime, no network.  ``gui/onboarding``
imports zero Streamlit, so it can be exercised with plain pytest.

Coverage
--------
*   Module is importable; public callables and constants exist.
*   ``OnboardingState`` — frozen dataclass with ``should_show`` + ``marker_path``.
*   ``read_onboarding_state`` — factory that derives state from session + FS.
*   ``should_show_tour``:
    - Returns ``True`` on a fresh session with no marker file.
    - Returns ``False`` when the marker file exists on disk.
    - Returns ``False`` when ``session_state[SESSION_KEY]`` is truthy.
    - Never raises — returns a bool even when ``marker_path`` points to a
      non-existent or non-writable path.
    - Returns a plain ``bool`` (not None, not an exception object).
*   ``mark_onboarded``:
    - Creates the marker file.
    - Writes atomically via a ``.tmp`` sibling file (the final path is present
      after the call; the ``.tmp`` file is gone).
    - Creates parent directories as needed.
    - Swallows failures silently (passing a bad path never raises).
    - Subsequent ``should_show_tour`` returns ``False`` after ``mark_onboarded``.
    - Calling it a second time is idempotent (no exception, file still present).
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class TestImport:
    def test_module_importable(self) -> None:
        import gui.onboarding  # noqa: F401

    def test_public_api_callable(self) -> None:
        from gui.onboarding import (
            mark_onboarded,
            read_onboarding_state,
            should_show_tour,
        )

        assert callable(should_show_tour)
        assert callable(mark_onboarded)
        assert callable(read_onboarding_state)

    def test_session_key_is_string(self) -> None:
        from gui.onboarding import SESSION_KEY

        assert isinstance(SESSION_KEY, str) and SESSION_KEY

    def test_default_marker_is_path(self) -> None:
        from gui.onboarding import DEFAULT_MARKER

        assert isinstance(DEFAULT_MARKER, Path)


# ---------------------------------------------------------------------------
# OnboardingState dataclass
# ---------------------------------------------------------------------------


class TestOnboardingState:
    def test_importable_and_frozen(self) -> None:
        from gui.onboarding import OnboardingState

        state = OnboardingState(should_show=True, marker_path=Path("output/.gui_onboarded"))
        # frozen dataclass must reject mutation
        import pytest as _pytest
        with _pytest.raises((TypeError, AttributeError)):
            state.should_show = False  # type: ignore[misc]

    def test_fields_exist(self) -> None:
        from gui.onboarding import OnboardingState

        state = OnboardingState(should_show=False, marker_path=Path("test.marker"))
        assert state.should_show is False
        assert state.marker_path == Path("test.marker")

    def test_read_onboarding_state_fresh(self, tmp_path: Path) -> None:
        """Factory returns should_show=True when no marker file exists."""
        from gui.onboarding import read_onboarding_state

        marker = tmp_path / ".gui_onboarded"
        state = read_onboarding_state({}, marker)
        assert state.should_show is True
        assert state.marker_path == marker

    def test_read_onboarding_state_after_mark(self, tmp_path: Path) -> None:
        """Factory returns should_show=False once marker has been written."""
        from gui.onboarding import mark_onboarded, read_onboarding_state

        marker = tmp_path / ".gui_onboarded"
        mark_onboarded(marker)
        state = read_onboarding_state({}, marker)
        assert state.should_show is False
        assert state.marker_path == marker

    def test_read_onboarding_state_session_key_set(self, tmp_path: Path) -> None:
        """Factory respects SESSION_KEY in session_state (no marker needed)."""
        from gui.onboarding import SESSION_KEY, read_onboarding_state

        marker = tmp_path / ".gui_onboarded"  # does not exist
        state = read_onboarding_state({SESSION_KEY: True}, marker)
        assert state.should_show is False

    def test_read_onboarding_state_never_raises(self) -> None:
        """Factory handles a totally bogus marker path without raising."""
        from gui.onboarding import read_onboarding_state

        bad = Path("/this/path/does/not/exist/.gui_onboarded")
        state = read_onboarding_state({}, bad)
        assert isinstance(state.should_show, bool)


# ---------------------------------------------------------------------------
# should_show_tour
# ---------------------------------------------------------------------------


class TestShouldShowTour:
    def test_fresh_session_no_marker_shows_tour(self, tmp_path: Path) -> None:
        from gui.onboarding import should_show_tour

        marker = tmp_path / ".gui_onboarded"
        assert not marker.exists(), "precondition: marker must not exist"
        assert should_show_tour({}, marker) is True

    def test_marker_exists_no_tour(self, tmp_path: Path) -> None:
        from gui.onboarding import should_show_tour

        marker = tmp_path / ".gui_onboarded"
        marker.write_text("onboarded", encoding="utf-8")
        assert should_show_tour({}, marker) is False

    def test_session_key_truthy_no_tour(self, tmp_path: Path) -> None:
        from gui.onboarding import SESSION_KEY, should_show_tour

        marker = tmp_path / ".gui_onboarded"
        # Marker absent — but session says already dismissed.
        assert not marker.exists()
        session = {SESSION_KEY: True}
        assert should_show_tour(session, marker) is False

    def test_session_key_falsy_defers_to_marker(self, tmp_path: Path) -> None:
        """SESSION_KEY present but falsy should NOT short-circuit to False."""
        from gui.onboarding import SESSION_KEY, should_show_tour

        marker = tmp_path / ".gui_onboarded"
        session = {SESSION_KEY: False}
        # Marker also absent → tour should still show.
        assert should_show_tour(session, marker) is True

    def test_returns_bool_type(self, tmp_path: Path) -> None:
        from gui.onboarding import should_show_tour

        marker = tmp_path / ".gui_onboarded"
        result = should_show_tour({}, marker)
        assert isinstance(result, bool)

    def test_never_raises_on_bad_path(self) -> None:
        """A marker path that can't be stat'd should not raise."""
        from gui.onboarding import should_show_tour

        bad = Path("/this/path/does/not/exist/at/all/.gui_onboarded")
        result = should_show_tour({}, bad)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# mark_onboarded
# ---------------------------------------------------------------------------


class TestMarkOnboarded:
    def test_creates_marker_file(self, tmp_path: Path) -> None:
        from gui.onboarding import mark_onboarded

        marker = tmp_path / ".gui_onboarded"
        assert not marker.exists()
        mark_onboarded(marker)
        assert marker.exists()

    def test_atomic_write_no_tmp_leftover(self, tmp_path: Path) -> None:
        """After mark_onboarded the .tmp sibling must be gone."""
        from gui.onboarding import mark_onboarded

        marker = tmp_path / ".gui_onboarded"
        tmp_sibling = marker.with_suffix(".tmp")
        mark_onboarded(marker)
        assert marker.exists(), "marker file should exist"
        assert not tmp_sibling.exists(), ".tmp file should be cleaned up atomically"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        from gui.onboarding import mark_onboarded

        marker = tmp_path / "nested" / "dir" / ".gui_onboarded"
        mark_onboarded(marker)
        assert marker.exists()

    def test_failure_is_swallowed(self) -> None:
        """Passing a path that can't be written must never raise."""
        from gui.onboarding import mark_onboarded

        # Use a path whose directory we can't create (/proc on macOS doesn't exist
        # or isn't writable; use a clearly-impossible path instead).
        bad = Path("/dev/null/impossible_subdir/.gui_onboarded")
        mark_onboarded(bad)  # must not raise

    def test_idempotent_second_call(self, tmp_path: Path) -> None:
        from gui.onboarding import mark_onboarded

        marker = tmp_path / ".gui_onboarded"
        mark_onboarded(marker)
        mark_onboarded(marker)  # second call must not raise
        assert marker.exists()

    def test_tour_false_after_mark(self, tmp_path: Path) -> None:
        """should_show_tour returns False after mark_onboarded has run."""
        from gui.onboarding import mark_onboarded, should_show_tour

        marker = tmp_path / ".gui_onboarded"
        assert should_show_tour({}, marker) is True  # precondition
        mark_onboarded(marker)
        assert should_show_tour({}, marker) is False

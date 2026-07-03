"""
gui/onboarding.py
=================
First-run onboarding state for the InvestYo Command Center.

This module has **zero Streamlit imports** so it is unit-testable headlessly
and the rendering layer can import it without pulling in the full Streamlit
runtime.  All state is derived from two sources:

*   ``st.session_state`` (fast path, per-browser-session) — avoids re-showing
    the tour after the user dismissed it within the same session.
*   A marker file (``output/.gui_onboarded``) — persists the dismissal across
    restarts so first-time users don't keep seeing the banner.

All I/O is wrapped in try/except (CONSTRAINT #6): a missing ``output/`` dir or
a read-only filesystem never raises — it just means the tour appears again next
time, which is harmless.

Public API
----------
``OnboardingState``
    Immutable snapshot of derived first-run state.  Construct via
    :func:`read_onboarding_state`.

``read_onboarding_state(session_state, marker_path) -> OnboardingState``
    Derive onboarding state from session + filesystem in one call.  The
    returned ``should_show`` flag drives both the Help-tab banner and the
    auto-expand of the Launcher ``explain()`` block on first visit.

``should_show_tour(session_state, marker_path) -> bool``
    Pure, testable function.  Returns ``True`` when the operator has not yet
    dismissed the onboarding tour.

``mark_onboarded(marker_path)``
    Atomic write-then-rename to the marker file.  Swallows failures silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Key used in st.session_state to short-circuit the marker-file check after
# the user dismisses the tour within the same browser session.
SESSION_KEY: str = "gui_onboarded"

# Default marker file path (relative to repo root at runtime).
DEFAULT_MARKER: Path = Path("output") / ".gui_onboarded"


@dataclass(frozen=True)
class OnboardingState:
    """Immutable snapshot of first-run onboarding state.

    Derived purely from ``st.session_state`` and the marker file — no network
    calls, no Streamlit imports.  Construct via :func:`read_onboarding_state`
    rather than directly so the derivation logic is centralised.

    Attributes
    ----------
    should_show:
        ``True`` when the operator has not yet dismissed the tour (either this
        session or in a previous launch).  Drives two UI effects:

        *   The Help-tab banner (4-step "Start here" checklist) is rendered.
        *   The Launcher tab's ``explain()`` expander is auto-opened so new
            operators land on actionable guidance immediately.
    marker_path:
        The filesystem path that was consulted when this state was derived.
        Stored so callers can pass it to :func:`mark_onboarded` without
        duplicating the path constant.
    """

    should_show: bool
    marker_path: Path


def read_onboarding_state(
    session_state: Dict[str, Any],
    marker_path: Path = DEFAULT_MARKER,
) -> OnboardingState:
    """Derive :class:`OnboardingState` from session and filesystem.

    Wraps :func:`should_show_tour` into a named container so callers can
    retrieve both the boolean flag and the marker path in a single call.
    Never raises (CONSTRAINT #6).

    Parameters
    ----------
    session_state:
        Mapping-like object (e.g. ``st.session_state``) checked for
        :data:`SESSION_KEY`.
    marker_path:
        Path to the persistent dismissal marker file.
    """
    return OnboardingState(
        should_show=should_show_tour(session_state, marker_path),
        marker_path=marker_path,
    )


def should_show_tour(
    session_state: Dict[str, Any],
    marker_path: Path = DEFAULT_MARKER,
) -> bool:
    """Return ``True`` when the onboarding tour should be shown.

    Check order (fastest first):
    1. If ``session_state[SESSION_KEY]`` is truthy → already dismissed this
       session → return ``False``.
    2. If ``marker_path`` exists on disk → previously dismissed → return
       ``False``.
    3. Otherwise → first run → return ``True``.

    Always returns a ``bool``; never raises (CONSTRAINT #6).

    Parameters
    ----------
    session_state:
        Mapping-like object (e.g. ``st.session_state``) checked for
        :data:`SESSION_KEY`.
    marker_path:
        Path to the persistent dismissal marker file.
    """
    try:
        if session_state.get(SESSION_KEY):
            return False
        return not marker_path.exists()
    except Exception as exc:
        logger.debug("should_show_tour: unexpected error, defaulting to False: %s", exc)
        return False


def mark_onboarded(marker_path: Path = DEFAULT_MARKER) -> None:
    """Write the onboarded marker file atomically (write-then-rename).

    Creates parent directories if needed.  Swallows all failures (CONSTRAINT
    #6) — if the write fails, the tour will simply reappear next launch, which
    is harmless.

    Parameters
    ----------
    marker_path:
        Destination path for the marker file.
    """
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker_path.with_suffix(".tmp")
        tmp.write_text("onboarded", encoding="utf-8")
        tmp.rename(marker_path)
        logger.debug("mark_onboarded: wrote %s", marker_path)
    except Exception as exc:
        logger.debug("mark_onboarded: failed to write %s: %s", marker_path, exc)

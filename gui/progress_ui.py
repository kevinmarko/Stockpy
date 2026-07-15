"""gui/progress_ui.py
=====================
Shared "busy/working" indicator helper for Streamlit button handlers.

Many buttons across the Command Center trigger real work (a Robinhood
network fetch, an ``.env`` write, an on-demand attribution/edge-ratio
computation, ...) but previously gave the operator no in-progress feedback —
only a terminal ``st.success``/``st.error`` once the handler finished, which
made the app look frozen while the work ran. :func:`busy` standardizes the
ad-hoc ``st.status(...)`` -> ``status.update(state=...)`` pattern already
used by hand in ``gui/panels/gravity_audit.py`` and
``gui/panels/live_inventory.py`` so every silent button can opt into the same
indeterminate spinner/status affordance with a single ``with`` statement.

This is intentionally NOT a percentage/progress-bar helper — these buttons
run indeterminate work (an unknown number of network calls, an unknown-sized
DataFrame computation), so :func:`busy` only ever shows a spinner/status
label, never a numeric percentage. A sibling module (``reporting/progress.py``,
built independently) covers the percentage-based pipeline-progress use case;
this module does not depend on it and should not import it.

CONSTRAINT #6 (dead-letter): :func:`busy` must never swallow an exception
raised inside its ``with`` block. On failure it marks the status as an error
and then RE-RAISES so the caller's own ``try/except`` (which every existing
button handler already has) still sees and handles the real error exactly as
before this helper was introduced.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


def _try_import_streamlit():
    """Best-effort ``import streamlit as st``.

    Returns ``None`` (never raises) when Streamlit is not importable at all,
    so this module can be imported by plain unit tests / tooling without
    Streamlit installed. In this codebase Streamlit *is* always installed
    (it's in requirements.txt), so this mainly guards against exotic
    import-time failures rather than a missing package.
    """
    try:
        import streamlit as st  # noqa: PLC0415 - intentionally lazy/guarded

        return st
    except Exception as exc:  # noqa: BLE001 - import failure is not fatal here
        logger.debug("streamlit import unavailable in gui.progress_ui: %s", exc)
        return None


def _has_script_run_ctx() -> bool:
    """True only when executing inside an active Streamlit script run.

    Guards against two distinct headless scenarios that must NOT attempt to
    render UI: (1) Streamlit isn't installed (handled by
    :func:`_try_import_streamlit` returning ``None`` first), and (2)
    Streamlit *is* installed but there is no active script-run context — a
    plain pytest process that merely imports a panel module, an ad-hoc REPL,
    or any other headless import. ``get_script_run_ctx()`` is Streamlit's own
    internal probe for exactly this check; it returns ``None`` outside a
    real app run instead of raising.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx  # noqa: PLC0415

        return get_script_run_ctx() is not None
    except Exception as exc:  # noqa: BLE001 - treat any probe failure as "not running"
        logger.debug("get_script_run_ctx() unavailable: %s", exc)
        return False


@contextmanager
def busy(label: str, *, done: Optional[str] = None, spinner: bool = True) -> Iterator[None]:
    """Show a working indicator for the duration of the wrapped block.

    Usage::

        with busy("Fetching Robinhood snapshot…"):
            snapshot_obj = fetch_account_snapshot()

    Parameters
    ----------
    label:
        Status text shown while the block runs (e.g.
        ``"Fetching Robinhood snapshot…"``).
    done:
        Optional label to show once the block completes successfully. When
        omitted, ``label`` is reused with a "✅ " prefix.
    spinner:
        Forwarded to ``st.status(..., expanded=spinner)`` — controls whether
        the status block starts expanded. ``st.status`` always shows a
        spinner icon while its state is ``"running"`` regardless of this
        flag; it is not a toggle for whether a spinner appears at all.

    Behavior
    --------
    - Always yields exactly once so the caller's block body runs normally.
    - On success, marks the status ``state="complete"`` with ``done`` (or a
      checkmarked ``label``).
    - On exception, marks the status ``state="error"`` and then RE-RAISES —
      it never swallows the exception (CONSTRAINT #6). Callers keep whatever
      try/except they already have around the ``with busy(...):`` block.
    - Degrades to a trivial no-op context (runs the body with no UI chrome)
      when there is no active Streamlit script-run context, or when
      ``st.status`` itself is unavailable/fails to construct — so importing
      or unit-testing this module never requires a live Streamlit server.
    """
    st = _try_import_streamlit()
    if st is None or not _has_script_run_ctx():
        # Headless: no live Streamlit runtime to render into. Just run the
        # body and let any exception propagate unmodified.
        yield
        return

    try:
        status_cm = st.status(label, expanded=spinner)
    except Exception as exc:  # noqa: BLE001 - st.status itself is unavailable/broken
        logger.debug("st.status() unavailable, degrading to plain context: %s", exc)
        yield
        return

    with status_cm as status:
        try:
            yield
        except Exception:
            try:
                status.update(label=f"❌ {label}", state="error")
            except Exception as update_exc:  # noqa: BLE001 - bookkeeping only
                logger.debug("status.update(error) failed: %s", update_exc)
            raise
        else:
            try:
                status.update(label=done or f"✅ {label}", state="complete")
            except Exception as update_exc:  # noqa: BLE001 - cosmetic only
                logger.debug("status.update(complete) failed: %s", update_exc)


def tracked_progress(iterable, text: str = "Working…") -> Iterator[Any]:
    """A determinate progress bar wrapper for iterables.
    
    Usage::
    
        for item in tracked_progress(items, text="Processing…"):
            # work on item
            pass
    
    Yields each item from the iterable while updating an `st.progress` bar with a 
    completion percentage. The progress bar is automatically removed when the 
    loop finishes or raises an exception.
    """
    st = _try_import_streamlit()
    if st is None or not _has_script_run_ctx():
        yield from iterable
        return

    try:
        # Convert to list if it's an iterator so we know the length, 
        # but typical usage is with a list/tuple.
        items = list(iterable)
        total = len(items)
    except TypeError:
        # Fallback if we somehow can't get a list
        yield from iterable
        return
        
    if total == 0:
        return

    try:
        progress_bar = st.progress(0.0, text=text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("st.progress() unavailable, degrading: %s", exc)
        yield from items
        return

    try:
        for i, item in enumerate(items):
            # Yield before updating progress so 0% represents start of first item
            yield item
            try:
                pct = (i + 1) / total
                pct_text = f"{int(pct * 100)}%"
                progress_bar.progress(pct, text=f"{text} ({i + 1}/{total} - {pct_text})")
            except Exception as update_exc:  # noqa: BLE001
                logger.debug("progress_bar.progress() failed: %s", update_exc)
    finally:
        try:
            progress_bar.empty()
        except Exception:  # noqa: BLE001
            pass

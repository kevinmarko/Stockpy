"""
gui/panels/reports_library.py
=============================
📁 **Report Library** tab — browse and view (inline) every generated report
file from within the Command Center.

Today the GUI has no inline report viewing anywhere; the only report affordance
is a single download button in ``gui/panels/report_viewer.py``. This tab adds
on-demand inline rendering (large HTML files render ONLY when their expander is
opened, never on every rerun) plus download buttons for:

1.  **Daily report**            — ``OUTPUT_DIR/daily_report.html``
2.  **Orchestrator dashboards**  — ``daily_report_dashboard.html`` /
    ``volatility_bands_dashboard.html`` in ``OUTPUT_DIR``
3.  **Daily briefings**          — ``OUTPUT_DIR/briefing_*.md`` (+ a
    "Generate today's briefing" button when ``gui.command_runner`` is present)
4.  **Validation reports**       — ``reports/*_validation_summary.json`` and
    ``reports/validation_*.html``

Every section is wrapped in try/except so one failing section renders an inline
``st.error`` instead of taking down the whole tab (dead-letter UI; CONSTRAINT #6).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from settings import settings
from gui import help_widgets
from gui.panels._shared import _REPO_ROOT, list_report_files, logger


def _mtime_caption(path: Path, *, label: str = "Last modified") -> None:
    """Render a freshness caption for ``path``, preferring the shared
    ``freshness_badge`` styling and degrading to a plain mtime string."""
    try:
        ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("mtime unavailable for %s: %s", path, exc)
        st.caption(f"{label}: unknown")
        return
    try:
        from gui.styling import freshness_badge

        st.caption(
            freshness_badge(
                ts, ttl_seconds=settings.DASHBOARD_REFRESH_SECONDS, label=label
            )
        )
    except Exception:  # noqa: BLE001 — cosmetic only
        st.caption(f"{label}: {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")


def _close_inline_view(view_key: str) -> None:
    """``on_click`` callback: clear a report's inline-view checkbox via
    session_state before the rerun, so the ``st.button`` below the iframe can
    close it without the operator needing to scroll back up past the report."""
    st.session_state[view_key] = False


def _html_file_block(path: Path, *, download_label: str, height: int = 800) -> None:
    """Render one HTML report: mtime caption, an on-demand inline viewer, and
    a download button.

    The inline viewer is gated behind a checkbox's VALUE (an ``if``), not a
    container like ``st.expander`` — Streamlit re-executes a script's full
    body on every rerun regardless of whether an expander is collapsed or
    open (only the visual disclosure differs), so wrapping the read/render
    call in ``with st.expander(...):`` alone does NOT skip it. Checking the
    checkbox's boolean return value is what actually defers the ~1.9 MB
    `read_text()` + `components.html()` call until the operator opts in.

    The page uses ``layout="wide"`` (gui/app.py), so there is little/no
    margin outside the embedded iframe to scroll the outer page. Once a tall
    report is open, hovering it and scrolling the mouse wheel scrolls INSIDE
    the iframe, not the page — the operator can't wheel back up to the
    checkbox to close it. A "Hide report" button rendered right below the
    iframe gives a close affordance reachable without leaving the report.
    """
    _mtime_caption(path)
    view_key = f"view_{path.name}"
    if st.checkbox("🔎 View inline", key=view_key):
        import streamlit.components.v1 as components

        html_text = path.read_text(encoding="utf-8", errors="replace")
        components.html(html_text, height=height, scrolling=True)
        st.button(
            "❌ Hide report",
            key=f"hide_{path.name}",
            on_click=_close_inline_view,
            args=(view_key,),
        )
    st.download_button(
        download_label,
        data=path.read_bytes(),
        file_name=path.name,
        mime="text/html",
        width="stretch",
        key=f"dl_{path.name}",
    )


def _render_daily_report_section() -> None:
    st.markdown("### 📰 Daily report")
    path = settings.OUTPUT_DIR / "daily_report.html"
    if path.exists():
        _html_file_block(path, download_label="⬇️ Download daily_report.html")
    else:
        st.caption("No daily report yet — generated every advisory cycle.")


def _render_orchestrator_dashboards_section() -> None:
    st.markdown("### 📊 Orchestrator dashboards")
    st.caption(
        "These only refresh on a manual `main_orchestrator.py` run, so their "
        "modified time may lag the latest advisory cycle. "
        "`daily_report_dashboard.html` can be ~1.9 MB — inline render is "
        "on-demand only (open the expander)."
    )
    any_found = False
    for name in ("daily_report_dashboard.html", "volatility_bands_dashboard.html"):
        path = settings.OUTPUT_DIR / name
        if path.exists():
            any_found = True
            st.markdown(f"**{name}**")
            _html_file_block(path, download_label=f"⬇️ Download {name}")
    if not any_found:
        st.caption(
            "No orchestrator dashboards yet — run `main_orchestrator.py` "
            "(Launcher tab) to generate them."
        )


def _render_briefings_section() -> None:
    st.markdown("### 📝 Daily briefings")

    # --- Generate today's briefing (defensive import of command_runner) ---
    if st.button("📝 Generate today's briefing", key="gen_briefing"):
        try:
            from gui.command_runner import run_daily_briefing
        except ImportError:
            st.info("Briefing generator not available in this build.")
        else:
            with st.status(
                "Generating today's briefing…", expanded=True
            ) as status:
                result = run_daily_briefing()
                ok = getattr(result, "ok", None)
                error = getattr(result, "error", None)
                stdout = getattr(result, "stdout", "") or ""
                if error:
                    status.update(
                        label="❌ Briefing generation failed", state="error"
                    )
                    st.error(error)
                elif ok is False:
                    status.update(
                        label="❌ Briefing generation failed", state="error"
                    )
                    st.error(stdout or "Briefing generation failed.")
                else:
                    status.update(label="✅ Briefing generated", state="complete")
                    if stdout:
                        st.code(stdout)
            if not error and ok is not False:
                st.rerun()

    briefings = list_report_files(
        settings.OUTPUT_DIR, "briefing_*.md", newest_first=True
    )
    if briefings:
        names = [p.name for p in briefings]
        selected = st.selectbox(
            "Select a briefing (newest first)",
            options=names,
            key="briefing_select",
        )
        chosen = next((p for p in briefings if p.name == selected), briefings[0])
        _mtime_caption(chosen)
        st.markdown(chosen.read_text(encoding="utf-8", errors="replace"))
    else:
        st.caption("No briefings yet — generate one with the button above.")


def _reports_dir() -> Path:
    """Repo-root ``reports/`` directory (resolved off ``_REPO_ROOT``)."""
    return _REPO_ROOT / "reports"


def _render_validation_reports_section() -> None:
    st.markdown("### ✅ Validation reports")
    reports_dir = _reports_dir()

    summaries = list_report_files(reports_dir, "*_validation_summary.json")
    html_reports = list_report_files(reports_dir, "validation_*.html")

    if not summaries and not html_reports:
        st.info(
            "No validation reports yet — none are generated until a strategy "
            "runs through the validation harness."
        )
        return

    for path in summaries:
        with st.expander(f"🧾 {path.name}"):
            _mtime_caption(path)
            try:
                st.json(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not parse {path.name}: {exc}")

    for path in html_reports:
        st.markdown(f"**{path.name}**")
        _html_file_block(path, download_label=f"⬇️ Download {path.name}")


def render_reports_library() -> None:
    """📁 Report Library tab — inline-viewable browser over all generated reports."""
    help_widgets.explain("report_library")
    st.subheader("📁 Report Library")

    for section in (
        _render_daily_report_section,
        _render_orchestrator_dashboards_section,
        _render_briefings_section,
        _render_validation_reports_section,
    ):
        try:
            section()
        except Exception as exc:  # noqa: BLE001 — dead-letter per section (CONSTRAINT #6)
            logger.exception("Report Library section %s failed", section.__name__)
            st.error(f"⚠️ This section hit an error: {exc}")
        st.divider()

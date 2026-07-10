"""
gui/panels/validation_lab.py
============================
🔬 **Validation Lab** tab — run strategy-validation reports on demand and read
the results back, all from inside the Command Center.

Today the only way to produce a validation report is to run
``python -m scripts.refresh_validations`` (or the ``validation.harness`` CLI)
from a terminal; the Report Library tab can only *display* whatever summaries
already happen to be on disk. This tab closes that loop: pick one or more
registered strategies + a backtest window, kick off the harness as a
non-blocking subprocess (never in-process — CONSTRAINT #5, the GUI is
file-backed and launches work as subprocesses), tail the live log, and then
read the per-strategy deployable verdict + PBO / DSR / Sharpe / MaxDD gates
straight from the ``reports/*_validation_summary.json`` the run wrote.

All deployability thresholds are imported from :mod:`validation.thresholds`
(never re-typed here) so the pass/fail colouring can never drift from the
harness's own ``ValidationReport.deployable`` gate.

Every section is wrapped in its own try/except so one failing section renders an
inline ``st.error`` instead of taking down the whole tab (dead-letter UI;
CONSTRAINT #6).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Dict, List

import pandas as pd
import streamlit as st

from gui import help_widgets, orchestrator_runner
from gui.panels._shared import _REPO_ROOT, list_report_files, logger
from gui.panels.reports_library import _html_file_block, _mtime_caption
from validation import thresholds

# Session-state key for this tab's run handle — deliberately distinct from the
# Launcher tab's ``run_handle`` so a validation run and a pipeline run can be in
# flight at the same time without clobbering each other's status polling.
_RUN_HANDLE_KEY = "validation_run_handle"


def _reports_dir() -> Path:
    """Repo-root ``reports/`` directory (resolved off ``_REPO_ROOT``)."""
    return _REPO_ROOT / "reports"


def _strategy_options() -> List[str]:
    """Return the registered strategy-id strings, or an empty list on any import
    failure (Agent A's runner module absent / mid-refactor — dead-letter)."""
    from scripts.refresh_validations import STRATEGY_REGISTRY

    return list(STRATEGY_REGISTRY.keys())


def _render_controls() -> None:
    """Strategy multiselect + date window + the run button (with a double-launch
    guard) — the top control strip of the tab."""
    st.markdown("### 1. Choose strategies & window")

    try:
        options = _strategy_options()
    except Exception as exc:  # noqa: BLE001 — dead-letter: runner module optional
        logger.debug("Could not import STRATEGY_REGISTRY: %s", exc)
        options = []
        st.warning(
            "Could not load the strategy registry from "
            "`scripts.refresh_validations` — the validation runner may not be "
            "available in this build yet."
        )

    selected = st.multiselect(
        "Strategies to validate",
        options=options,
        default=options,
        key="validation_strategies",
        help="Each selected strategy is run through the full validation harness "
        "(walk-forward + CPCV) and written to `reports/`.",
    )

    col_a, col_b = st.columns(2)
    start_input = col_a.date_input(
        "Backtest start",
        value=date(2010, 1, 1),
        key="validation_start",
    )
    end_input = col_b.date_input(
        "Backtest end",
        value=date.today(),
        key="validation_end",
    )
    start_str = start_input.strftime("%Y-%m-%d")
    end_str = end_input.strftime("%Y-%m-%d")

    handle = st.session_state.get(_RUN_HANDLE_KEY)
    already_running = bool(handle and handle.is_running())
    if already_running:
        st.warning(
            f"🟢 A validation run is already in flight (PID {handle.pid}). "
            "Wait for it to finish before launching another."
        )

    if st.button(
        "▶️ Run validation",
        type="primary",
        disabled=already_running or not selected,
        key="validation_run_button",
    ):
        try:
            new_handle = orchestrator_runner.launch_validation_run(
                list(selected), start_str, end_str
            )
        except Exception as exc:  # noqa: BLE001 — launch failure must not crash the tab
            logger.exception("launch_validation_run failed")
            st.error(f"⚠️ Could not launch validation run: {exc}")
        else:
            st.session_state[_RUN_HANDLE_KEY] = new_handle
            st.success(f"🚀 Launched validation run (PID {new_handle.pid}).")
            st.rerun()


def _render_run_status() -> None:
    """Live status + log tail for the most recently launched validation run."""
    st.markdown("### 2. Run status")
    handle = st.session_state.get(_RUN_HANDLE_KEY)
    if handle is None:
        st.caption("No validation run launched yet this session.")
        return

    running = handle.is_running()
    if running:
        st.success(f"🟢 Running — PID {handle.pid}")
    else:
        rc = handle.returncode()
        if rc == 0:
            st.success("✅ Validation run finished cleanly (exit 0).")
        elif rc is None:
            st.info("⚪ Validation run state unknown (no exit code recorded yet).")
        else:
            st.error(f"❌ Validation run exited with code {rc} — see the log below.")

    if st.button("🔄 Refresh", key="validation_refresh"):
        st.rerun()

    st.code(orchestrator_runner.read_log_tail(handle=handle), language="text")


def _summary_row(summary: dict) -> Dict[str, object]:
    """Flatten one ``*_validation_summary.json`` dict into a display row with a
    deployable ✅/❌ verdict and the four standard gate values."""
    deployable = bool(summary.get("deployable"))
    return {
        "Strategy": summary.get("strategy_id", "—"),
        "Deployable": "✅" if deployable else "❌",
        "PBO": summary.get("pbo"),
        "DSR": summary.get("dsr"),
        "Sharpe": summary.get("sharpe"),
        "MaxDD": summary.get("max_drawdown"),
        "Report date": summary.get("report_date", "—"),
        "Error": summary.get("error", ""),
    }


def _render_results() -> None:
    """Per-strategy deployable table + the latest validation HTML reports."""
    st.markdown("### 3. Results")
    st.caption(
        f"Deployability gates (from `validation.thresholds`): "
        f"PBO < {thresholds.PBO_MAX}, DSR > {thresholds.DSR_MIN}, "
        f"net Sharpe > {thresholds.NET_SHARPE_MIN}, "
        f"MaxDD < {thresholds.MAX_DRAWDOWN_MAX:.0%}."
    )

    reports_dir = _reports_dir()
    summaries = list_report_files(reports_dir, "*_validation_summary.json")
    html_reports = list_report_files(reports_dir, "validation_*.html")

    if not summaries and not html_reports:
        st.info(
            "No validation reports on disk yet — run a validation above, or "
            "none have been generated for any strategy."
        )
        return

    # --- Per-strategy deployable table ---------------------------------------
    rows: List[Dict[str, object]] = []
    for path in summaries:
        try:
            rows.append(_summary_row(json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:  # noqa: BLE001 — skip one bad file, keep the rest
            logger.debug("Could not parse %s: %s", path.name, exc)
            rows.append({"Strategy": path.name, "Deployable": "❓", "Error": str(exc)})

    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption(
            "The preflight `validation_reports` check turns green once EVERY "
            "selected strategy is deployable and < 30 days old."
        )

    # --- Latest HTML reports --------------------------------------------------
    if html_reports:
        st.markdown("#### 📄 Validation report(s)")
        for path in html_reports:
            st.markdown(f"**{path.name}**")
            _mtime_caption(path)
            _html_file_block(path, download_label=f"⬇️ Download {path.name}")


def render_validation_lab() -> None:
    """🔬 Validation Lab tab — run the validation harness and view results."""
    help_widgets.explain("validation_lab")
    st.subheader("🔬 Strategy Validation Lab")
    st.caption(
        "Run the validation harness for one or more strategies and read the "
        "deployable verdict back — all read-only and file-backed; this tab "
        "launches work as a subprocess and never calls the broker."
    )

    for section in (_render_controls, _render_run_status, _render_results):
        try:
            section()
        except Exception as exc:  # noqa: BLE001 — dead-letter per section (CONSTRAINT #6)
            logger.exception("Validation Lab section %s failed", section.__name__)
            st.error(f"⚠️ This section hit an error: {exc}")
        st.divider()

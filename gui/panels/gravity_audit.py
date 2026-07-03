from __future__ import annotations

from __future__ import annotations
import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import streamlit as st
from settings import settings
from gui import env_io, orchestrator_runner, help_widgets
from gui.symbol_search import filter_by_symbol
from gui.orchestrator_runner import StageStatus
from gui.panels._shared import (  # noqa: E402
    GICS_SECTORS,
    _BF_EDITOR_COLUMNS,
    _REPO_ROOT,
    _active_symbols,
    _held_symbols,
    _kill_switch,
    _signal_symbols,
    _watchlist_symbols,
    load_block_log,
    logger,
)


def _render_circuit_breaker_dashboard() -> None:
    """Render every tripped breaker — kill switch + recent risk-gate blocks.

    Read-only derivation via :mod:`gui.circuit_breakers`. Adding a new
    breaker means adding a check inside ``execution/risk_gate.py``; this
    panel auto-picks-up the new tag via the ``_KNOWN_CHECKS`` table over there.
    """
    from gui.circuit_breakers import (
        collect_circuit_breaker_trips,
        summarise_trips,
    )

    st.markdown("### 🚧 Circuit Breaker Dashboard")
    st.caption(
        "Trips derived from `output/KILL_SWITCH` and `output/risk_gate_blocks.jsonl` "
        "(last 24 h). Most recent per (breaker, strategy) shown."
    )

    trips = collect_circuit_breaker_trips(
        kill_switch_sentinel=settings.OUTPUT_DIR / "KILL_SWITCH",
        block_log_path=settings.OUTPUT_DIR / "risk_gate_blocks.jsonl",
    )
    summary = summarise_trips(trips)

    k1, k2, k3 = st.columns(3)
    k1.metric("CRITICAL trips", summary["CRITICAL"])
    k2.metric("WARNING trips", summary["WARNING"])
    k3.metric("Total", summary["TOTAL"])

    if not trips:
        st.success("✅ No active circuit-breaker trips in the last 24 h.")
        return

    rows = []
    for t in trips:
        rows.append({
            "Severity": ("🔴 CRITICAL" if t.severity == "CRITICAL"
                         else "🟡 WARNING"),
            "Breaker": t.name,
            "Summary": t.summary,
            "Triggered (UTC)": (t.triggered_at.isoformat(timespec="seconds")
                                if t.triggered_at else "—"),
            "Threshold": (f"{t.threshold:.4g}" if t.threshold is not None else "—"),
            "Observed": (f"{t.observed:.4g}" if t.observed is not None else "—"),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    with st.expander("🔬 Inspect raw trip payloads"):
        for t in trips:
            st.markdown(f"**{t.name}** — {t.severity}")
            st.json(dict(t.detail))



def _render_dependency_map() -> None:
    """Pick degraded sources → list every impacted strategy/tab/report."""
    from gui.dependency_map import (
        CONSUMERS,
        DataSource,
        impacted_consumers,
        render_edges,
    )

    st.markdown("### 🕸️ Dependency Map")
    st.caption(
        "Declarative source → consumer graph. Pick the sources that are "
        "degraded right now and the panel projects which strategies, tabs, "
        "and reports lose coverage. The map itself lives in "
        "`gui/dependency_map.py`; extend it there as new consumers come online."
    )

    options = [s for s in DataSource if s is not DataSource.UNKNOWN]
    labels = {s.label: s for s in options}
    chosen_labels = st.multiselect(
        "Degraded data sources",
        options=list(labels.keys()),
        default=[],
        help="Pick zero or more sources to simulate / acknowledge an outage.",
    )
    chosen = [labels[name] for name in chosen_labels]

    if chosen:
        impact = impacted_consumers(chosen)
        rows = []
        for record in impact:
            for c in record.consumers:
                rows.append({
                    "Degraded source": record.source.label,
                    "Impacted": c.name,
                    "Kind": c.kind,
                    "Why": c.description,
                })
        if rows:
            st.warning(
                f"⚠️ {len(rows)} downstream consumer(s) impacted across "
                f"{len(impact)} source(s).",
                icon="⚠️",
            )
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("Selected source(s) have no registered consumers — "
                    "verify `gui/dependency_map.py` is current.")
    else:
        st.caption("No degraded sources selected. Showing the full graph below.")

    with st.expander("🔬 Full dependency graph"):
        edges = render_edges()
        edge_df = pd.DataFrame(edges, columns=["Source", "Consumer", "Kind"])
        st.dataframe(edge_df, width="stretch", hide_index=True)



def _render_strategy_health() -> None:
    """Strategy Health view from ``output/gravity_verification_report.json``.

    Reads the verification report written by :func:`Gravity AI Review
    Suite._write_gravity_verification_report` and evaluates each strategy
    against :mod:`validation.thresholds` — the canonical single source of
    truth shared with :mod:`validation.harness`.

    Missing file → informational hint (CONSTRAINT #4 — no fabricated rows).
    Corrupt JSON → same hint. Each strategy shows a gate-by-gate table with
    the observed value, threshold, direction, and pass/fail status.
    """
    from gui.strategy_health import DeployabilityGate, evaluate_gate, read_gravity_report
    from validation.thresholds import DSR_MIN, MAX_DRAWDOWN_MAX, NET_SHARPE_MIN, PBO_MAX

    st.markdown("### 📊 Strategy Health — Deployability Gates")
    st.caption(
        "Sourced from `output/gravity_verification_report.json` (written by the "
        "Gravity AI Review Suite). Evaluated against thresholds in "
        "`validation/thresholds.py` — the same constants used by "
        "`validation/harness.py`."
    )

    strategies = read_gravity_report()

    if not strategies:
        st.info(
            "No strategy health data yet. Run the Gravity AI Review Suite below "
            "to populate `output/gravity_verification_report.json`."
        )
        return

    # Summary row
    total = len(strategies)
    deployable_count = sum(1 for s in strategies if s.get("deployable") is True)
    not_deployable = sum(1 for s in strategies if s.get("deployable") is False)
    unknown = total - deployable_count - not_deployable

    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Strategies", total)
    sc2.metric("✅ Deployable", deployable_count)
    sc3.metric("❌ Not Deployable", not_deployable)
    sc4.metric("❓ Unknown", unknown)

    for strategy_dict in strategies:
        health = evaluate_gate(strategy_dict)
        with st.expander(
            f"{'✅' if health.deployable else ('❌' if health.deployable is False else '❓')} "
            f"`{health.strategy_id}` — "
            f"{'Deployable' if health.deployable else ('NOT deployable' if health.deployable is False else 'Unknown')}",
            expanded=health.deployable is False,
        ):
            if health.last_audited_at:
                st.caption(f"Last audited: {health.last_audited_at}")

            gate_rows = []
            for g in health.gates:
                icon = "✅" if g.passed is True else ("❌" if g.passed is False else "—")
                gate_rows.append({
                    "Metric": g.metric,
                    "Observed": f"{g.value:.4f}" if g.value is not None else "—",
                    "Threshold": f"{g.threshold}",
                    "Direction": f"must be {g.direction} {g.threshold}",
                    "Gate": f"{icon} {'PASS' if g.passed else ('FAIL' if g.passed is False else 'N/A')}",
                })
            st.dataframe(pd.DataFrame(gate_rows), width="stretch", hide_index=True)

            if health.is_options_selling:
                stress_label = (
                    "✅ Stress passed"
                    if health.stress_passed is True
                    else ("❌ Stress FAILED" if health.stress_passed is False else "— Not run")
                )
                st.caption(f"Options-selling strategy — tail-scenario stress gate: {stress_label}")


# ===========================================================================
# Tier 9 Scope 2 — AI Gravity audit runner section (Safety tab)
# ===========================================================================


def _render_gravity_ai_runner_section() -> None:
    """Render the Safety-tab section that surfaces ``engine.gravity_ai_runner``.

    Four render paths driven by :func:`gui.gravity_ai_panel.runner_status`:

    * ``disabled`` — master switch off.  Renders an info caption with the
      ``.env`` knob needed to enable; no button.
    * ``missing_key`` — switch on but neither key set.  Renders a warning
      + a disabled button so the seam is visible.
    * ``partial_key`` — exactly one of the two keys set.  Renders a
      yellow caution + an enabled button (the runner soft-fails the
      missing side and records it as ``skipped``).
    * ``ready`` — both keys + switch on.  Renders the full panel:
      health colour band + 5-metric KPI strip + "▶️ Run AI Gravity audit"
      button + per-step table with Claude vs Gemini badges +
      raw-report expander.

    Soft-fail (CONSTRAINT #6): every code path that touches the runner
    or the on-disk report is wrapped in try/except.  A missing /
    corrupt / wrong-shape report renders as the "no audit yet"
    sentinel — never an exception bubble.
    """
    st.markdown("### 🤖 AI Gravity audit — Claude auditor + Gemini cross-checker")
    try:
        from gui.gravity_ai_panel import (
            health_caption,
            load_audit_report,
            runner_status,
            step_rows,
            summarise_run,
        )
    except Exception as exc:  # pragma: no cover - import-time degrade
        st.caption(f"(AI Gravity helpers unavailable: {exc})")
        return

    status = runner_status(settings)

    if status == "disabled":
        st.caption(
            "AI Gravity runner is off.  Set `GRAVITY_AI_RUNNER_ENABLED=true` plus "
            "`ANTHROPIC_API_KEY` AND `GEMINI_API_KEY` in `.env`, then relaunch the "
            "GUI.  The structural Python-only Gravity audit above is unaffected."
        )
        return

    if status == "missing_key":
        st.warning(
            "`GRAVITY_AI_RUNNER_ENABLED=true` but neither `ANTHROPIC_API_KEY` nor "
            "`GEMINI_API_KEY` is set — provide at least one and relaunch."
        )
        st.button(
            "▶️ Run AI Gravity audit",
            key="gravity_ai_run_btn",
            disabled=True,
            width="stretch",
        )
        return

    if status == "partial_key":
        st.warning(
            "Only one provider key is configured.  The runner will record the "
            "missing side as `skipped` — disagreement detection requires both."
        )

    # status ∈ {"ready", "partial_key"}
    report = load_audit_report()
    summary = summarise_run(report)

    # Health colour band.
    caption = health_caption(summary)
    if summary.health == "fail":
        st.error(caption)
    elif summary.health == "warn":
        st.warning(caption)
    elif summary.health == "clean":
        st.success(caption)
    else:
        st.info(caption)

    # KPI strip.
    cols = st.columns(5)
    cols[0].metric("Steps", summary.total_steps)
    cols[1].metric("Claude ✅", summary.claude_passed,
                   delta=(-summary.claude_failed) if summary.claude_failed else None,
                   delta_color="inverse")
    cols[2].metric("Gemini ✅", summary.gemini_passed,
                   delta=(-summary.gemini_failed) if summary.gemini_failed else None,
                   delta_color="inverse")
    cols[3].metric("⚠ Disagreements", summary.disagreements)
    cols[4].metric("Last run (UTC)", summary.generated_at[:19] if summary.generated_at else "—")

    if st.button("▶️ Run AI Gravity audit (Claude + Gemini)",
                 key="gravity_ai_run_btn", type="primary", width="stretch"):
        with st.spinner("Calling Claude + Gemini for each of the 7 audit steps…"):
            try:
                from engine.gravity_ai_runner import run_all, write_report  # noqa: PLC0415

                fresh = run_all()
                write_report(fresh)
                # Refresh the loaded view from disk so the table updates in-place.
                report = load_audit_report()
                summary = summarise_run(report)
            except Exception as exc:
                st.error(f"AI Gravity runner failed: {exc}")

    rows = step_rows(report)
    if rows:
        df = pd.DataFrame(rows)
        # Friendlier column titles for the operator-facing table.
        df = df.rename(columns={
            "step_number": "Step",
            "step_title": "Title",
            "claude": "Claude",
            "gemini": "Gemini",
            "disagreement": "⚠ Disagree",
            "score_claude": "Score (C)",
            "score_gemini": "Score (G)",
            "notes": "Notes",
        })
        st.dataframe(df, width="stretch", hide_index=True)
        with st.expander("🔬 Full AI audit JSON"):
            st.json(report)
    else:
        st.caption(
            "No AI Gravity audit yet — click ▶️ above to run all 7 steps.  "
            "Results persist to `output/gravity_ai_audit.json`."
        )



def render_gravity_audit() -> None:
    """Render the Safety tab: Circuit Breakers + Dependency Map + Gravity audit.

    Sections (top to bottom):

    1.  **Strategy Health** — deployability gate table from
        ``output/gravity_verification_report.json``.
    2.  **Circuit Breaker Dashboard** — every tripped breaker derived from the
        existing kill-switch sentinel + risk-gate block log. See
        :mod:`gui.circuit_breakers`.
    3.  **Dependency Map** — declarative source → consumer graph from
        :mod:`gui.dependency_map`. The operator picks the degraded sources
        and the panel shows which strategies / tabs / reports lose coverage.
    4.  **Gravity AI Review Suite** — full audit subprocess (the original
        behavior, kept verbatim).
    """
    help_widgets.explain("gravity")
    st.subheader("🛡️ Safety — Circuit Breakers, Dependencies, Gravity Audit")

    _render_strategy_health()
    st.divider()
    _render_circuit_breaker_dashboard()
    st.divider()
    _render_dependency_map()
    st.divider()
    _render_gravity_ai_runner_section()
    st.divider()

    st.markdown("### 🧪 Gravity AI Review Suite")
    st.caption(
        "Runs `Gravity AI Review Suite.py` — Pandera schema conformance, "
        "lookahead-bias perturbation, signal-registry health, sizing/risk gates. "
        "Review before authorizing a live run."
    )

    if st.button("▶️ Run Gravity audit", type="primary"):
        with st.spinner("Running Gravity AI Review Suite (this can take a minute)…"):
            try:
                import subprocess
                import sys

                proc = subprocess.run(
                    [sys.executable, "Gravity AI Review Suite.py"],
                    cwd=str(_REPO_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                st.session_state["gravity_stdout"] = proc.stdout
                st.session_state["gravity_returncode"] = proc.returncode
            except Exception as exc:
                st.session_state["gravity_stdout"] = ""
                st.session_state["gravity_error"] = str(exc)

    stdout = st.session_state.get("gravity_stdout", "")
    if st.session_state.get("gravity_error"):
        st.error(f"Audit failed to launch: {st.session_state['gravity_error']}")

    if stdout:
        report = _parse_trailing_json(stdout)
        if report is None:
            st.warning("Could not parse a JSON report from the audit output.")
            st.code(stdout[-4000:], language="text")
            return

        rows = []
        for key, val in report.items():
            if not isinstance(val, dict):
                continue
            status = str(val.get("status", "—"))
            ok = status.upper().startswith("PASS")
            rows.append({"Step": key, "Status": ("✅ " if ok else "❌ ") + status})
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch")
            failed = [r for r in rows if "✅" not in r["Status"]]
            if failed:
                st.error(f"{len(failed)} audit step(s) failed — NOT cleared for live.")
            else:
                st.success("All audit steps passed — cleared for live readiness review.")
        with st.expander("🔬 Full audit JSON"):
            st.json(report)



def _parse_trailing_json(text: str) -> Optional[dict]:
    """Extract the last top-level JSON object from arbitrary stdout."""
    end = text.rfind("}")
    if end == -1:
        return None
    depth = 0
    start = -1
    for i in range(end, -1, -1):
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


# ===========================================================================
# Tab 7 — Technical Options Matrix
# ===========================================================================



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


# ---------------------------------------------------------------------------
# Cached loaders (PR B — GUI panel caching)
#
# Streamlit reruns the whole script on every interaction, so these two
# file-backed loads fired on every render of the Safety tab: (1) the Gravity
# verification-report JSON read behind Strategy Health, and (2) the
# glob-and-parse of every ``reports/*_validation_summary.json`` behind the
# Validation & Stress section. Both are now routed through ``@st.cache_data``
# loaders keyed on file **mtime** (the codebase convention — see
# ``gui.panels.load_state_snapshot``). Behaviour-preserving: WHAT renders is
# identical; a changed mtime is a cache miss, so a fresh Gravity/harness run is
# reflected on the next render. Dead-letter intact — a missing/corrupt file
# degrades to ``[]`` (CONSTRAINT #6), never a fabricated row (CONSTRAINT #4).
# ---------------------------------------------------------------------------


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_gravity_report_cached(path_str: str, _mtime: float) -> List[Dict[str, Any]]:
    """mtime-keyed cached read of the Gravity verification report's
    ``strategies`` list (delegates to :func:`gui.strategy_health.read_gravity_report`)."""
    from gui.strategy_health import read_gravity_report

    return read_gravity_report(Path(path_str))


def _load_gravity_report() -> List[Dict[str, Any]]:
    """Load the Gravity verification report's ``strategies`` list (``[]`` when
    absent), via the mtime-keyed cached loader so a rerun no longer re-reads the
    JSON unless it changed."""
    from gui.strategy_health import _DEFAULT_REPORT_PATH

    p = _DEFAULT_REPORT_PATH
    try:
        mtime = p.stat().st_mtime if p.exists() else 0.0
    except OSError:
        mtime = 0.0
    return _load_gravity_report_cached(str(p), mtime)


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_validation_summaries_cached(reports_dir_str: str, _signature: str) -> List[Dict[str, Any]]:
    """Signature-keyed cached glob+parse of ``*_validation_summary.json`` files.

    ``_signature`` (a ``name:mtime`` digest computed by the wrapper) participates
    in the cache key only — an added/removed/modified summary file changes it and
    forces a fresh parse. Malformed files are skipped rather than aborting the
    load (CONSTRAINT #6)."""
    summaries: List[Dict[str, Any]] = []
    d = Path(reports_dir_str)
    if d.exists():
        for f in d.glob("*_validation_summary.json"):
            try:
                summaries.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001 — dead-letter, never raise
                logger.debug("Could not parse %s: %s", f, exc)
    return summaries


def _load_validation_summaries(reports_dir: Path) -> List[Dict[str, Any]]:
    """Load all validation-summary dicts under *reports_dir* via the cached
    signature-keyed loader (fresh parse only when a file changes/appears)."""
    try:
        files = sorted(reports_dir.glob("*_validation_summary.json")) if reports_dir.exists() else []
        signature = "|".join(f"{f.name}:{f.stat().st_mtime}" for f in files)
    except Exception as exc:  # noqa: BLE001 — dead-letter, never raise into UI
        logger.debug("validation-summary signature failed: %s", exc)
        signature = ""
    return _load_validation_summaries_cached(str(reports_dir), signature)


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
    help_widgets.section_caption("circuit_breaker_dashboard")

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
    help_widgets.section_caption("dependency_map")

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
    from gui.strategy_health import DeployabilityGate, evaluate_gate
    from validation.thresholds import DSR_MIN, MAX_DRAWDOWN_MAX, NET_SHARPE_MIN, PBO_MAX

    st.markdown("### 📊 Strategy Health — Deployability Gates")
    help_widgets.section_caption("strategy_health_gates")

    strategies = _load_gravity_report()

    # Freshness badge (Task C5): the report is only refreshed when the
    # operator runs the Gravity AI Review Suite below, so its mtime — not a
    # rolling TTL — is the honest "as of" signal here.
    try:
        from gui.styling import freshness_badge
        _report_path = settings.OUTPUT_DIR / "gravity_verification_report.json"
        _report_mtime = (
            datetime.fromtimestamp(_report_path.stat().st_mtime, tz=timezone.utc)
            if _report_path.exists() else None
        )
        st.caption(freshness_badge(
            _report_mtime, ttl_seconds=settings.DASHBOARD_REFRESH_SECONDS,
            label="Gravity verification report",
        ))
    except Exception as exc:  # noqa: BLE001 — cosmetic only
        logger.debug("strategy health freshness badge unavailable: %s", exc)

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
                    # Numeric twin of "Observed" used only for Sharpe severity
                    # coloring (Task C5) — dropped from the displayed frame below.
                    "_observed_num": g.value if g.metric.lower() == "sharpe" else None,
                    "Threshold": f"{g.threshold}",
                    "Direction": f"must be {g.direction} {g.threshold}",
                    "Gate": f"{icon} {'PASS' if g.passed else ('FAIL' if g.passed is False else 'N/A')}",
                })
            gate_df = pd.DataFrame(gate_rows)
            has_sharpe_row = gate_df["_observed_num"].notna().any()
            gate_df = gate_df.drop(columns=["_observed_num"])
            if has_sharpe_row:
                # Recompute the numeric column post-drop so style_severity has
                # a real column to target — apply coloring directly to the
                # "Observed" text column via a Sharpe-aware mask instead.
                try:
                    from gui.styling import _color_sharpe

                    def _color_observed(row) -> list:
                        styles = [""] * len(row)
                        if str(row.get("Metric", "")).lower() == "sharpe":
                            try:
                                val = float(str(row["Observed"]))
                                styles[gate_df.columns.get_loc("Observed")] = _color_sharpe(val)
                            except (ValueError, TypeError):
                                pass
                        return styles

                    st.dataframe(
                        gate_df.style.apply(_color_observed, axis=1),
                        width="stretch", hide_index=True,
                    )
                except Exception as exc:  # noqa: BLE001 — styling is cosmetic only
                    logger.debug("Sharpe severity styling unavailable: %s", exc)
                    st.dataframe(gate_df, width="stretch", hide_index=True)
            else:
                st.dataframe(gate_df, width="stretch", hide_index=True)

            if health.is_options_selling:
                stress_label = (
                    "✅ Stress passed"
                    if health.stress_passed is True
                    else ("❌ Stress FAILED" if health.stress_passed is False else "— Not run")
                )
                st.caption(f"Options-selling strategy — tail-scenario stress gate: {stress_label}")


# ===========================================================================
# Task C6 — Validation & stress trend + regime timeline (Safety tab)
# ===========================================================================


def _render_validation_stress_regime_section() -> None:
    """Cross-strategy validation snapshot + stress-scenario gate + regime timeline.

    Three sub-sections, each with an honest data-availability caveat rather
    than a fabricated trend (CONSTRAINT #4):

    1. **Validation snapshot across strategies** — ``reports/*_validation_summary.json``
       is written ONE FILE PER STRATEGY, overwritten every harness run
       (``StrategyValidationHarness._write_json_summary``); this section
       shows the current cross-strategy snapshot ("how does strategy A
       compare to strategy B right now?"). A separate, append-only
       ``reports/history/<strategy>_validation_history.jsonl`` (written by
       ``StrategyValidationHarness._append_validation_history``, one row per
       run, capped at ``MAX_VALIDATION_HISTORY_ROWS``) is read via
       ``validation.harness.read_validation_history`` to render the
       run-over-run PBO/DSR/Sharpe/MaxDD trend beneath the snapshot table.
       When a strategy has no accumulated history yet (fewer than 2 runs
       since this feature shipped), that is stated plainly rather than
       inventing points.
    2. **Stress-scenario gate** — ``ValidationReport.to_summary_dict()`` only
       persists the AGGREGATE ``stress_gate_passed`` boolean, not a per-scenario
       (OCT_2008/FEB_2018/MAR_2020/AUG_2024) breakdown — the per-scenario
       ``StressResult`` objects live only in-memory during a harness run and
       are rendered into the (separate, non-JSON) HTML report via Jinja, never
       serialized to the JSON summary the GUI reads. So this table shows the
       real STRATEGY-level pass/fail (from disk) with an explicit note that
       per-scenario granularity is not currently persisted for the GUI to read.
    3. **Macro regime timeline** — same ``output/history/`` rotated-snapshot
       source as the Observability dashboard's equity-curve regime overlay.
    """
    from validation.stress_scenarios import STRESS_SCENARIOS
    from validation.harness import MAX_VALIDATION_HISTORY_ROWS

    st.markdown("### 📐 Validation & Stress Trend")

    # ── 1. Cross-strategy validation snapshot ────────────────────────────────
    reports_dir = _REPO_ROOT / "reports"
    summaries = _load_validation_summaries(reports_dir)

    st.markdown("**Current validation snapshot (all strategies)**")
    if not summaries:
        st.info(
            "No `reports/*_validation_summary.json` files found yet. Run "
            "`python -m validation.harness --strategy <name> --start ... --end ...` "
            "to generate one."
        )
    else:
        snap_df = pd.DataFrame(summaries)
        show_cols = [c for c in
                     ["strategy_id", "deployable", "pbo", "dsr", "sharpe",
                      "max_drawdown", "is_options_selling", "stress_gate_passed",
                      "report_date"]
                     if c in snap_df.columns]
        st.dataframe(snap_df[show_cols] if show_cols else snap_df,
                     width="stretch", hide_index=True)

        # ── 1b. Run-over-run trend from reports/history/*.jsonl ─────────────
        st.markdown("**Validation trend across runs**")
        from validation.harness import read_validation_history

        history_dir = reports_dir / "history"
        history_by_strategy: Dict[str, List[Dict[str, Any]]] = {}
        for s in summaries:
            sid = s.get("strategy_id")
            if not sid:
                continue
            hist = read_validation_history(sid, history_dir=str(history_dir))
            if len(hist) >= 2:
                history_by_strategy[sid] = hist

        if not history_by_strategy:
            st.info(
                "No run-over-run history yet. `validation/harness.py` now "
                "appends one row per run to "
                "`reports/history/<strategy>_validation_history.jsonl`; a "
                "trend line appears here once a strategy has at least 2 "
                "recorded runs."
            )
        else:
            metric_labels = {
                "dsr": "DSR", "pbo": "PBO",
                "sharpe": "Sharpe", "max_drawdown": "Max Drawdown",
            }
            metric = st.selectbox(
                "Metric", list(metric_labels.keys()),
                format_func=lambda m: metric_labels[m],
                key="validation_trend_metric",
            )
            trend_df = pd.DataFrame()
            for sid, hist in history_by_strategy.items():
                hdf = pd.DataFrame(hist)
                if "report_date" not in hdf.columns or metric not in hdf.columns:
                    continue
                series = (
                    hdf[["report_date", metric]]
                    .dropna()
                    .groupby("report_date")[metric]
                    .last()
                )
                trend_df[sid] = series
            if trend_df.empty:
                st.info(f"No `{metric}` history recorded yet for any strategy.")
            else:
                st.line_chart(trend_df)
            st.caption(
                "One point per harness run (grouped by `report_date`, most "
                f"recent {MAX_VALIDATION_HISTORY_ROWS} runs retained per "
                "strategy); strategies with a single run are omitted above "
                "until a second run accumulates."
            )

    st.divider()

    # ── 2. Stress-scenario gate table ────────────────────────────────────────
    st.markdown("**Tail-Scenario Stress Gate — options-selling strategies**")
    st.caption(
        "Canonical shock windows (`validation/stress_scenarios.STRESS_SCENARIOS`): "
        + ", ".join(f"`{name}` ({s.start}–{s.end})" for name, s in STRESS_SCENARIOS.items())
    )

    options_selling = [s for s in summaries if s.get("is_options_selling")]
    if not options_selling:
        st.info(
            "No options-selling strategy summaries found. This table populates "
            "when a harness run is constructed with `is_options_selling=True`."
        )
    else:
        stress_rows = [
            {
                "Strategy": s.get("strategy_id", "?"),
                "Stress Gate": ("✅ PASSED" if s.get("stress_gate_passed") is True
                                 else ("❌ FAILED" if s.get("stress_gate_passed") is False
                                       else "— Not run")),
                "Deployable": "✅" if s.get("deployable") else "❌",
            }
            for s in options_selling
        ]
        st.dataframe(pd.DataFrame(stress_rows), width="stretch", hide_index=True)
        st.caption(
            "⚠️ **Per-scenario (OCT_2008/FEB_2018/MAR_2020/AUG_2024) breakdown is "
            "not persisted to the JSON summary** — only this AGGREGATE pass/fail "
            "is written by `ValidationReport.to_summary_dict()`. The per-scenario "
            "`StressResult` detail exists only in-memory during a harness run and "
            "is rendered solely into that run's standalone HTML report. A "
            "4-scenario × strategy heatmap would require persisting "
            "`stress_test_results` into the JSON summary — not fabricated here."
        )

    st.divider()

    # ── 3. Macro regime timeline (reuses output/history/, same as C2) ───────
    st.markdown("**Macro Regime Timeline**")
    try:
        from scripts.snapshot_diff import list_rotated_snapshots, load_snapshot

        rotated_paths = list_rotated_snapshots(settings.OUTPUT_DIR)
        regime_points = []
        for p in rotated_paths:
            snap_hist = load_snapshot(p)
            if not snap_hist:
                continue
            ts_raw = snap_hist.get("timestamp")
            regime_raw = snap_hist.get("market_regime")
            if ts_raw and regime_raw:
                regime_points.append({"timestamp": ts_raw, "market_regime": str(regime_raw)})

        if len(regime_points) >= 2:
            regime_df = pd.DataFrame(regime_points)
            regime_df["timestamp"] = pd.to_datetime(regime_df["timestamp"])
            regime_df = regime_df.sort_values("timestamp")
            # Only show rows where the regime actually changed from the prior
            # rotated snapshot — a "transition timeline", not every raw row.
            regime_df["changed"] = regime_df["market_regime"].ne(regime_df["market_regime"].shift())
            transitions = regime_df[regime_df["changed"]].drop(columns=["changed"])
            st.dataframe(
                transitions.rename(columns={"timestamp": "Timestamp (UTC)",
                                             "market_regime": "Market Regime"}),
                width="stretch", hide_index=True,
            )
            st.caption(
                f"{len(regime_points)} rotated snapshot(s) available "
                f"(retained {settings.SNAPSHOT_HISTORY_DAYS} days); "
                f"{len(transitions)} regime transition(s) shown."
            )
        else:
            st.info(
                f"Regime timeline needs ≥ 2 rotated snapshots in "
                f"`output/history/` — currently {len(regime_points)}. "
                "This accumulates automatically each time the orchestrator "
                "or advisory loop runs; not fabricated here."
            )
    except Exception as exc:
        st.caption(f"(regime timeline unavailable: {exc})")


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
        with st.status("Running AI Gravity audit (0/7 steps)…", expanded=True) as status:
            try:
                from engine.gravity_ai_runner import run_all, write_report  # noqa: PLC0415

                def _on_step(n, result):
                    # CONSTRAINT #4: only report real per-step progress —
                    # step_title is the StepRunResult field (see step_rows()
                    # usage below, which maps "step_title" -> "Title").
                    title = getattr(result, "step_title", f"Step {n}")
                    status.update(label=f"Step {n}/7: {title} — done…")

                fresh = run_all(on_step=_on_step)
                write_report(fresh)
                # Refresh the loaded view from disk so the table updates in-place.
                report = load_audit_report()
                summary = summarise_run(report)
                status.update(
                    label=f"✅ AI Gravity audit complete — {summary.total_steps} steps.",
                    state="complete",
                )
            except Exception as exc:
                # CONSTRAINT #6: never swallow the failure silently — surface it
                # via the status label/state (equivalent to the prior st.error).
                status.update(label=f"❌ AI Gravity runner failed: {exc}", state="error")

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
    _render_validation_stress_regime_section()
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

    # Non-blocking launch: spawn the audit as a detached subprocess via
    # orchestrator_runner (streams to GRAVITY_LOG_PATH) and stash the RunHandle
    # in session_state so it survives Streamlit reruns.  This replaces the old
    # blocking subprocess.run(..., timeout=600) that froze the entire UI for up
    # to 10 minutes.
    if st.button("▶️ Run Gravity audit", type="primary"):
        existing = st.session_state.get("gravity_handle")
        if existing is not None and existing.is_running():
            st.warning(f"A Gravity audit is already running (PID {existing.pid}).")
        else:
            try:
                handle = orchestrator_runner.launch_gravity_audit()
                st.session_state["gravity_handle"] = handle
                st.success(
                    f"Launched Gravity audit (PID {handle.pid}). Tailing below…"
                )
            except Exception as exc:  # noqa: BLE001 - dead-letter UI (CONSTRAINT #6)
                st.error(f"Audit failed to launch: {exc}")

    @st.fragment(run_every="3s")
    def _gravity_live_status() -> None:
        """Live status + log tail for the Gravity audit subprocess.

        Polls every 3 s.  While the audit runs it shows a status line + the
        streamed log tail; once finished it renders the pass/fail table from
        the FINISHED log via the unchanged ``_parse_trailing_json`` /
        ``_derive_step_status`` helpers.  When finished the redraw is a cheap
        static render — the 3 s cadence is harmless (no live process to poll),
        so we simply return the static finished/idle view rather than gate the
        fragment.

        CONSTRAINT #4 (fail-closed): a non-zero exit code OR an unparseable log
        renders as a FAILURE — never a fabricated success.
        CONSTRAINT #6: the whole body is try/except-guarded so a transient IO
        error degrades to an inline caption instead of crashing the tab.
        """
        handle = st.session_state.get("gravity_handle")
        if handle is None:
            st.caption("No audit run this session.")
            return

        try:
            if handle.is_running():
                st.info("🟡 Running…")
                with st.expander("📜 Live audit log", expanded=True):
                    st.code(
                        orchestrator_runner.read_log_tail(max_lines=200, handle=handle),
                        language="text",
                    )
                return

            # ── Finished ──────────────────────────────────────────────────────
            rc = handle.returncode()
            # Large max_lines == read the whole finished log for JSON parsing.
            log_text = orchestrator_runner.read_log_tail(max_lines=100_000, handle=handle)
            report = _parse_trailing_json(log_text)

            if report is None:
                # Fail-closed: no parseable report is NOT a pass.
                st.warning("Could not parse a JSON report from the audit output.")
                if rc is not None and rc != 0:
                    st.error(f"Audit process exited with code {rc} — NOT cleared for live.")
                st.code(log_text[-4000:], language="text")
                return

            rows = []
            for key, val in report.items():
                if not isinstance(val, dict):
                    continue
                ok, status = _derive_step_status(key, val)
                rows.append({"Step": key, "Status": ("✅ " if ok else "❌ ") + status})
            if rows:
                st.dataframe(pd.DataFrame(rows), width="stretch")
                failed = [r for r in rows if "✅" not in r["Status"]]
                # A non-zero exit fails closed even if every parsed step passed.
                if failed or (rc is not None and rc != 0):
                    n_failed = len(failed)
                    detail = (
                        f"{n_failed} audit step(s) failed"
                        if n_failed
                        else f"audit process exited with code {rc}"
                    )
                    st.error(f"{detail} — NOT cleared for live.")
                else:
                    st.success("All audit steps passed — cleared for live readiness review.")
            with st.expander("🔬 Full audit JSON"):
                st.json(report)
        except Exception as exc:  # noqa: BLE001 - dead-letter UI (CONSTRAINT #6)
            st.caption(f"(Gravity audit status unavailable: {exc})")

    _gravity_live_status()



def _derive_step_status(key: str, val: Dict[str, Any]) -> Tuple[bool, str]:
    """Best-effort PASS/FAIL derivation across every Gravity step-report shape.

    Most steps populate a top-level ``"status"`` string (``"PASSED"`` /
    ``"FAILED"`` / ``"ERROR"``). A handful of steps only set
    ``"overall_pass"`` (bool) instead. The original Steps 1-7 predate both
    conventions and report domain-specific conclusion fields — falling
    through to the ``"—"`` sentinel for any of these misreported a passing
    step as a failure in the audit table.
    """
    if "status" in val:
        status = str(val["status"])
        return status.upper().startswith("PASS"), status
    if "overall_pass" in val:
        ok = bool(val["overall_pass"])
        return ok, "PASSED" if ok else "FAILED"
    if key == "step_3_5_discrepancy_analysis":
        conclusion = str(val.get("conclusion", "UNKNOWN"))
        return conclusion == "Perfect Alignment", conclusion
    if key == "step_7_simulation_impact":
        sub_statuses = [
            str(val.get("vector_bt_status", "")),
            str(val.get("backtrader_status", "")),
        ]
        ok = not any("error" in s.lower() for s in sub_statuses if s)
        label = " / ".join(s for s in sub_statuses if s) or "UNKNOWN"
        return ok, label
    return False, "—"


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



"""
gui/app.py
==========
Entry point for the InvestYo **Command Center** — a local-first, on-demand
Streamlit operational suite spanning the full quant-trading lifecycle.

Launch
------
    streamlit run gui/app.py

or double-click ``launch_gui.command`` on macOS.

Tabs
----
1.  Launcher & Orchestration  — run main_orchestrator.py + live stage status
2.  Report Viewer             — evaluation/research analytics + report export
3.  Settings Manager          — edit non-secret .env tunables (secrets masked)
4.  Strategy Matrix           — enable/disable signal modules, weights, kill switch
5.  Paper-Trading Monitor     — Robinhood account truth vs. pipeline projection
6.  Gravity Audit Logs        — run the Gravity AI Review Suite, view pass/fail
7.  Options Matrix            — Black-Scholes greeks + IVR proxy per symbol
8.  Market Data               — active provider, quote freshness, cache controls
9.  Observability             — compact macro/regime/P&L summary
10. Live Inventory            — synchronized portfolio + watchlist coverage map
                                with on-demand "Sync Now" (Task 1.4)
11. Prompts                   — Prompt Registry: resolved version/source per ID,
                                🔄 Sync, diff viewer, ↩ Rollback/pin (Stage 7)
12. AI Insights                — Claude analyst note + Gemini chart-pattern vision +
                                aggregate Claude-vs-Gemini disagreement view (Tier 9 Scope 3)
13. AI Control Center          — one operator surface for every AI option: master-switch
                                toggles, on-demand per-symbol actions, Gravity AI audit,
                                and Start/Stop of an --interval/--agent scheduled run

Design
------
The GUI is read-only / file-backed and never calls async broker code directly
(see ``gui/__init__.py`` for the rationale).  Every tab body is wrapped by
:func:`safe_panel` so a single panel's failure renders an inline error box
instead of aborting the whole app (dead-letter UI; CONSTRAINT #6).
"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path
from typing import Callable

# Resolve repo root so `streamlit run gui/app.py` works from any CWD and the
# platform's top-level modules (settings, signals, execution, …) import cleanly.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env into os.environ so modules that read os.environ directly
# (data/robinhood_portfolio.py) see credentials. override=False so an explicit
# shell export always wins (mirrors the entry-point convention in main.py).
try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=False)
except Exception:  # pragma: no cover - dotenv always present per requirements.txt
    pass

import streamlit as st

from settings import settings
from gui import panels, run_mode
from gui.export_utils import dataframe_to_csv_bytes, signals_snapshot_to_dataframe
from gui.help_content import metric_help

logging.basicConfig(level=getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO))
logger = logging.getLogger("gui.app")


st.set_page_config(
    page_title="InvestYo Command Center",
    page_icon="🎛️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _canonical_regimes() -> list[str]:
    """Return the canonical market-regime set from ``macro_engine.MacroDataSchema``.

    Sourced from the Pandera ``isin`` check on the ``market_regime`` column so
    this list can never drift out of sync with the regime values the pipeline
    actually produces. Falls back to the historically stable 4-regime set if
    the schema is unreachable or its shape ever changes (dead-letter — a
    sidebar filter must never crash the whole app over this).
    """
    try:
        from macro_engine import MacroDataSchema

        schema = MacroDataSchema.to_schema()
        for check in schema.columns["market_regime"].checks:
            allowed = getattr(check, "statistics", {}).get("allowed_values")
            if allowed:
                return list(allowed)
    except Exception as exc:  # noqa: BLE001 - sidebar filter must degrade, never crash
        logger.debug("Could not read canonical regimes from MacroDataSchema: %s", exc)
    return ["RISK ON", "NEUTRAL", "RECESSION", "CREDIT EVENT"]


def safe_panel(render_fn: Callable[[], None]) -> None:
    """Render a panel, converting any exception into an inline error box.

    This is the dead-letter UI pattern: one tab raising (e.g. a missing optional
    dependency, an unreachable API) must never take down the entire command
    center.  The traceback is shown in a collapsed expander for debugging.
    """
    try:
        render_fn()
    except Exception as exc:  # noqa: BLE001 - intentional broad catch at UI boundary
        logger.exception("Panel %s failed", getattr(render_fn, "__name__", "?"))
        st.error(f"⚠️ This panel hit an error: {exc}")
        with st.expander("Traceback"):
            st.code("".join(traceback.format_exc()), language="text")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("🎛️ Command Center")
st.sidebar.caption("InvestYo / Stockpy quant platform")
st.sidebar.markdown(
    f"- **Output dir:** `{settings.OUTPUT_DIR}`\n"
    f"- **Dry run default:** `{settings.DRY_RUN}`\n"
    f"- **Refresh TTL:** `{settings.DASHBOARD_REFRESH_SECONDS}s`"
)
from gui.engine_status import engine_status
_engine_badge, _engine_text = engine_status()
st.sidebar.caption(f"{_engine_badge} {_engine_text}")
if settings.DISABLED_SIGNAL_MODULES:
    st.sidebar.warning(
        "Disabled modules: " + ", ".join(settings.DISABLED_SIGNAL_MODULES)
    )
st.sidebar.divider()
if st.sidebar.button("🔄 Clear cached reads"):
    st.cache_data.clear()
    st.rerun()

# ---------------------------------------------------------------------------
# Cross-tab regime-conditional filter
# ---------------------------------------------------------------------------
# Stored in st.session_state so any panel COULD read it in the future without
# needing to be wired today. Not yet consumed by any gui/panels/*.py render
# function — this is the first concrete proof-of-use, scoped to gui/app.py's
# own sidebar (a "N symbols match" count + CSV export), per the task's
# low-conflict boundary with the other in-flight agents' panel work.
st.sidebar.divider()
st.sidebar.subheader("🌐 Regime Filter")
_regime_options = ["All regimes"] + _canonical_regimes()
st.session_state.setdefault("regime_filter", "All regimes")
st.sidebar.selectbox(
    "Filter by macro regime",
    options=_regime_options,
    key="regime_filter",
    help=metric_help("sidebar.regime_filter"),
)

try:
    _snap = panels.load_state_snapshot()
    _signals = _snap.get("signals", []) if isinstance(_snap, dict) else []
    _selected_regime = st.session_state.get("regime_filter", "All regimes")
    if _selected_regime == "All regimes":
        _matching = _signals
    else:
        _matching = [
            s for s in _signals
            if str(s.get("macro_status", "")).strip().upper() == _selected_regime.upper()
        ]
    st.sidebar.caption(
        f"**{len(_matching)}** symbols match the selected regime "
        f"(of {len(_signals)} in the last run).",
        help=metric_help("sidebar.regime_match_count"),
    )
    if not _signals:
        st.sidebar.caption(
            "_No `output/state_snapshot.json` yet — run the pipeline "
            "(Launcher tab) to populate signals._"
        )

    # ------------------------------------------------------------------
    # CSV export — single, low-conflict download button.
    # ------------------------------------------------------------------
    _export_df = signals_snapshot_to_dataframe(_matching)
    st.sidebar.download_button(
        "⬇️ Download current signals as CSV",
        data=dataframe_to_csv_bytes(_export_df),
        file_name="investyo_signals_snapshot.csv",
        mime="text/csv",
        disabled=_export_df.empty,
        help=metric_help("export.download_signals_csv"),
    )
except Exception as _regime_exc:  # noqa: BLE001 - sidebar section must never crash the app
    logger.debug("Regime filter / export sidebar section soft-failed: %s", _regime_exc)
    st.sidebar.caption("Regime filter unavailable (no snapshot data yet).")

# Sidebar quick-help widget (3 most-common questions)
try:
    with st.sidebar.expander("❓ Quick help"):
        st.markdown(
            "**What do action signals mean?**  \n"
            "STRONG BUY / BUY / HOLD / RISK REDUCE / SELL — directional "
            "recommendations derived from a weighted blend of signal modules. "
            "All are *informational only* — no orders are submitted.\n\n"
            "**What is Kelly Target?**  \n"
            "Fractional-Kelly position size as a % of capital, calibrated from "
            "your actual closed-trade history and volatility. 0 % means "
            "insufficient data or a sell signal.\n\n"
            "**Why is everything in advisory mode?**  \n"
            "`ADVISORY_ONLY=true` in `.env` quarantines all broker execution "
            "(the default). Set it to `false` only when you are ready to connect "
            "a live or paper broker."
        )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Main title + persistent run-mode header
# ---------------------------------------------------------------------------
st.title("InvestYo Command Center")

# --- Tier 5.1: persistent advisory-only banner ---
# When settings.ADVISORY_ONLY is True (the project default) we render a single,
# unambiguous banner above the tab bar so the operator cannot miss the
# quarantine.  In that mode we deliberately suppress the simulation/paper/live
# mode badge because the DRY_RUN / ALPACA_PAPER toggle is no-op'd downstream;
# showing a Live badge while the broker is quarantined would be misleading.
if getattr(settings, "ADVISORY_ONLY", True):
    st.info(
        "📋 **ADVISORY MODE** — no orders will be submitted to any broker. "
        "The pipeline produces signals, sizing, and reports only. "
        "Set `ADVISORY_ONLY=false` in `.env` to re-enable broker execution.",
        icon="📋",
    )
else:
    # Persistent banner derived from DRY_RUN + ALPACA_PAPER + session run handle.
    # Shown above the tab bar so the operator always knows what mode they're in.
    _mode_state = run_mode.read_active_run_mode(session_state=st.session_state)
    _mode_colors = {"Simulation": "blue", "Paper": "orange", "Live": "red"}
    _banner_color = _mode_colors.get(_mode_state.mode, "gray")
    if _mode_state.mode == "Live":
        st.error(_mode_state.run_mode_label, icon="🔴")
    elif _mode_state.mode == "Paper":
        st.warning(_mode_state.run_mode_label, icon="🟡")
    else:
        st.info(_mode_state.run_mode_label, icon="⚪")

# --- Tier 8: Robinhood execution-mode banner ---
# Independent of ADVISORY_ONLY / DRY_RUN / ALPACA_PAPER — Robinhood's
# execution queue has its own staged posture (off | review | live).  In
# any non-``off`` posture there is a real proposed-order queue on disk
# that a Claude Code agent can act on, so we surface a persistent banner
# above the tab bar.  Rendered AFTER the ADVISORY MODE / Alpaca run-mode
# banner so the operator sees both when both apply.  Informational
# only — the actual guards live in ``execution/queue_builder.py``.
try:
    from gui.robinhood_mode import read_robinhood_execution_mode
    _rh_mode_state = read_robinhood_execution_mode(settings)
    if _rh_mode_state.variant == "error":
        st.error(_rh_mode_state.label, icon="🔴")
    elif _rh_mode_state.variant == "warning":
        st.warning(_rh_mode_state.label, icon="🟡")
    # variant == "hidden" (mode="off") → render nothing
except Exception as _rh_exc:  # noqa: BLE001 — informational banner never blocks the app
    logger.debug("Robinhood execution-mode banner soft-failed: %s", _rh_exc)

tab_labels = [
    "🚀 Launcher",
    "📈 Reports",
    "⚙️ Settings",
    "🧩 Strategy Matrix",
    "📒 Paper Monitor",
    "🛡️ Gravity Audit",
    "🧮 Options",
    "🛰️ Market Data",
    "📊 Observability",
    "📊 Analytics",
    "📡 Live Inventory",
    "❓ Help",
    "📝 Prompts",
    "🪄 AI Insights",
    "🎛️ AI Control Center",
    "📁 Report Library",
]
tabs = st.tabs(tab_labels)

with tabs[0]:
    safe_panel(panels.render_launcher)
with tabs[1]:
    safe_panel(panels.render_report_viewer)
with tabs[2]:
    safe_panel(panels.render_settings_manager)
with tabs[3]:
    safe_panel(panels.render_strategy_matrix)
with tabs[4]:
    safe_panel(panels.render_paper_monitor)
with tabs[5]:
    safe_panel(panels.render_gravity_audit)
with tabs[6]:
    safe_panel(panels.render_options_matrix)
with tabs[7]:
    safe_panel(panels.render_market_data)
with tabs[8]:
    safe_panel(panels.render_observability)
with tabs[9]:
    safe_panel(panels.render_analytics)
with tabs[10]:
    safe_panel(panels.render_live_inventory)
with tabs[11]:
    safe_panel(panels.render_help)
with tabs[12]:
    safe_panel(panels.render_prompt_registry)
with tabs[13]:
    safe_panel(panels.render_ai_insights)
with tabs[14]:
    safe_panel(panels.render_ai_control_center)
with tabs[15]:
    safe_panel(panels.render_reports_library)

st.caption(f"Rendered {panels.utcnow_str()} · read-only, file-backed · secrets stay in .env")

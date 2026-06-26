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
from gui import panels

logging.basicConfig(level=getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO))
logger = logging.getLogger("gui.app")


st.set_page_config(
    page_title="InvestYo Command Center",
    page_icon="🎛️",
    layout="wide",
    initial_sidebar_state="expanded",
)


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
if settings.DISABLED_SIGNAL_MODULES:
    st.sidebar.warning(
        "Disabled modules: " + ", ".join(settings.DISABLED_SIGNAL_MODULES)
    )
st.sidebar.divider()
if st.sidebar.button("🔄 Clear cached reads"):
    st.cache_data.clear()
    st.rerun()

# ---------------------------------------------------------------------------
# Main title + tabs
# ---------------------------------------------------------------------------
st.title("InvestYo Command Center")

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
    "📡 Live Inventory",
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
    safe_panel(panels.render_live_inventory)

st.caption(f"Rendered {panels.utcnow_str()} · read-only, file-backed · secrets stay in .env")

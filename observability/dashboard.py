"""
observability/dashboard.py
==========================
Streamlit paper-trading observability dashboard for the InvestYo platform.

Launch
------
    streamlit run observability/dashboard.py

Architecture
------------
The dashboard is intentionally *read-only* and *file-backed*.  It reads from
local files and SQLite rather than making live broker API calls for two reasons:

1.  **Event-loop isolation.**  Streamlit runs a synchronous render loop that
    conflicts with the ``asyncio``-based broker adapters (AlpacaBroker).
    Calling async broker methods from a Streamlit callback would require
    ``asyncio.run()`` on a fresh loop per widget interaction, which is both
    fragile and slow.

2.  **Resilience.**  The dashboard must remain usable even when the broker
    API is unreachable (e.g. during a market holiday, after hours, or during
    an Alpaca outage).  File-backed reads degrade gracefully to empty panels
    rather than crashing.

The orchestrator (``main_orchestrator.py``) owns the live data and writes it
to the output files after each pipeline run; the dashboard is a passive
consumer.

Data sources
------------
  output/state_snapshot.json          → macro regime, VIX, HMM probability,
                                        last pipeline signals
  output/risk_gate_blocks.jsonl       → risk gate failure log (append-only;
                                        dashboard reads last 100 rows)
  output/KILL_SWITCH                  → kill switch sentinel (presence = active)
  output/heartbeat.txt                → ISO timestamp of last orchestrator ping
  reports/*_validation_summary.json  → per-strategy deployability status
  quant_platform.db (TransactionsStore) → open/closed trades, realized P&L

Caching strategy
----------------
Every data-loading function is decorated with ``@st.cache_data(ttl=N)`` where
``N = settings.DASHBOARD_REFRESH_SECONDS`` (default 30).  This prevents Streamlit
from re-reading the files on every widget interaction (e.g. scrolling the data
table) while still picking up new orchestrator output within 30 seconds.  The
``ttl`` is the *maximum* staleness, not a polling interval — actual refresh is
driven by the ``time.sleep`` + ``st.rerun()`` at the bottom of the script.

Auto-refresh
------------
``time.sleep(refresh_secs)`` + ``st.rerun()`` re-executes the entire Streamlit
script, which invalidates the cache TTL and forces fresh reads.  This approach
was chosen over ``streamlit-autorefresh`` (a third-party library) to avoid
adding a dependency that is not in ``requirements.txt`` (CONSTRAINT #1).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

# Resolve repo root so this file can be ``streamlit run``-ed from any CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent

import sys
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from settings import settings
from transactions_store import TransactionsStore
from execution.kill_switch import GlobalKillSwitch


# ---------------------------------------------------------------------------
# Streamlit page config — must be the first Streamlit call in the script.
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="InvestYo Observability",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Data loaders (all cached with TTL)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_state_snapshot() -> dict:
    """Load the orchestrator's last state snapshot from JSON.

    The snapshot is written by ``main_orchestrator._write_state_snapshot()``
    after every successful pipeline run.  It contains the macro regime, VIX,
    HMM risk-on probability, and one entry per ticker signal.

    Returns an empty dict if the file does not exist or is malformed —
    callers handle missing keys gracefully with ``.get(..., "—")``.
    """
    snap = settings.OUTPUT_DIR / "state_snapshot.json"
    if snap.exists():
        try:
            return json.loads(snap.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_block_log(n: int = 100) -> list[dict]:
    """Load the most recent ``n`` risk gate block entries.

    The log file is append-only JSONL (one JSON object per line), written by
    ``execution.risk_gate.PreTradeRiskGate._append_block_log()`` each time an
    order is blocked.  We read the tail to bound memory usage regardless of
    how many blocks have been recorded historically.

    Entries are returned in reverse-chronological order (most recent first) so
    the Streamlit data table shows the latest blocks at the top without
    requiring column sorting.
    """
    log_path = settings.OUTPUT_DIR / "risk_gate_blocks.jsonl"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        rows = []
        for line in lines[-n:]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip malformed lines rather than aborting the entire load;
                # a corrupt line can occur if the process crashed mid-write.
                continue
        return list(reversed(rows))  # most-recent first
    except Exception:
        return []


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_validation_reports() -> list[dict]:
    """Load all strategy validation summary JSON files from ``reports/``.

    Each ``*_validation_summary.json`` file is written by
    ``validation.harness.StrategyValidationHarness._write_json_summary()``
    after a harness run.  The dashboard uses these to show the per-strategy
    deployability status table without requiring a live harness run.

    Returns an empty list if the ``reports/`` directory does not exist or
    contains no summary files, rather than raising so the panel gracefully
    shows a "run the harness" instruction instead of a traceback.
    """
    reports_dir = _REPO_ROOT / "reports"
    if not reports_dir.exists():
        return []
    summaries = []
    for f in reports_dir.glob("*_validation_summary.json"):
        try:
            summaries.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return summaries


@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_trades() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load open and closed trade records from the SQLite transactions store.

    Returns a 2-tuple of ``(open_trades_df, closed_trades_df)``.  Returns a
    pair of empty DataFrames if the database is unreachable or the schema has
    not been initialised yet, so the dashboard panels degrade gracefully to
    "no data" messages.

    Uses the default ``TransactionsStore()`` which reads
    ``settings.DATABASE_URL``.  Tests that need isolation inject an in-memory
    store — the dashboard always uses the real store.
    """
    try:
        ts = TransactionsStore()
        return ts.open_trades_df(), ts.closed_trades_df()
    except Exception:
        return pd.DataFrame(), pd.DataFrame()


def _kill_switch() -> GlobalKillSwitch:
    """Construct a ``GlobalKillSwitch`` pointing at the configured output dir.

    The explicit ``sentinel_file`` argument overrides the module-level
    ``KILL_SWITCH_FILE`` constant (which is evaluated at import time and may
    not reflect a runtime ``settings.OUTPUT_DIR`` override in tests).  This
    guarantees the dashboard always reads from the same path the orchestrator
    and ``OrderManager`` write to.
    """
    return GlobalKillSwitch(sentinel_file=settings.OUTPUT_DIR / "KILL_SWITCH")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

st.title("📊 InvestYo Observability Dashboard")

# Sidebar: let the operator override the refresh interval at runtime without
# touching .env.  The default is ``settings.DASHBOARD_REFRESH_SECONDS``.
st.sidebar.header("Settings")
refresh_secs = st.sidebar.number_input(
    "Auto-refresh (seconds)",
    min_value=5,
    max_value=300,
    value=settings.DASHBOARD_REFRESH_SECONDS,
    step=5,
)

# Load all data sources at the top of the render loop so each panel below
# only reads from already-loaded objects, not from disk/SQLite again.
snap = _load_state_snapshot()
open_df, closed_df = _load_trades()
block_log = _load_block_log()
val_reports = _load_validation_reports()
ks = _kill_switch()

last_updated = snap.get("timestamp", "—")
st.caption(
    f"Pipeline last run: **{last_updated}**  |  Dashboard refreshes every {refresh_secs}s"
)

# ── Row 1: Kill switch + macro regime ────────────────────────────────────────
# Four metrics in a row give an immediate "traffic light" view of system
# health before the operator scrolls into detailed panels.

col_ks, col_regime, col_vix, col_hmm = st.columns(4)

with col_ks:
    if ks.is_active():
        # Red banner is intentionally prominent — an active kill switch means
        # NO new orders are being submitted and the operator must take action.
        st.error(f"🚨 KILL SWITCH ACTIVE\n\n{ks.reason() or '(no reason stored)'}")
    else:
        st.success("✅ Kill Switch: INACTIVE")

with col_regime:
    regime = snap.get("market_regime", "—")
    # Colour-code by known regime strings from MacroEconomicDTO.  Unknown
    # regime strings (e.g. "NEUTRAL") get the yellow indicator.
    colour = (
        "🟢" if "RISK ON" in str(regime)
        else ("🔴" if "RECESSION" in str(regime) else "🟡")
    )
    st.metric("Macro Regime", f"{colour} {regime}")

with col_vix:
    vix_val = snap.get("vix", None)
    vix_display = f"{vix_val:.1f}" if vix_val else "—"
    st.metric("VIX", vix_display, delta=None)

with col_hmm:
    # The HMM probability is stored per-signal in the snapshot; take the
    # first non-null value (all tickers share the same macro HMM output for
    # a given pipeline run, so any one of them suffices).
    hmm_vals = [
        s.get("hmm_risk_on")
        for s in snap.get("signals", [])
        if s.get("hmm_risk_on") is not None
    ]
    hmm_display = f"{hmm_vals[0]:.1%}" if hmm_vals else "—"
    st.metric("HMM Risk-On", hmm_display)

st.divider()

# ── Row 2: P&L by strategy ───────────────────────────────────────────────────
# Grouped sum of realized P&L from closed trades.  The groupby is performed
# in-memory rather than as a SQL aggregate so it works even if the
# TransactionsStore schema changes (new columns are just ignored).

st.subheader("💰 P&L by Strategy")

if not closed_df.empty:
    if "realized_pnl" in closed_df.columns and "strategy_id" in closed_df.columns:
        pnl_by_strat = (
            closed_df.groupby("strategy_id")["realized_pnl"]
            .sum()
            .reset_index()
            .rename(columns={"realized_pnl": "Realized P&L ($)"})
        )
        pnl_by_strat["Realized P&L ($)"] = pnl_by_strat["Realized P&L ($)"].round(2)
        st.dataframe(pnl_by_strat, use_container_width=True)
    else:
        st.info("P&L columns not available — run trades through the pipeline first.")
else:
    st.info("No closed trades in the database yet.")

st.divider()

# ── Row 3: Open positions + last pipeline signals ────────────────────────────
# Side-by-side comparison lets the operator quickly spot reconciliation
# discrepancies: positions in the internal book that have no corresponding
# pipeline signal, or vice versa.

st.subheader("📂 Open Positions")

col_open, col_signals = st.columns(2)

with col_open:
    st.caption("From transactions_store (internal book)")
    if not open_df.empty:
        display_cols = [
            c for c in ["symbol", "strategy_id", "entry_price", "entry_ts", "qty"]
            if c in open_df.columns
        ]
        st.dataframe(open_df[display_cols] if display_cols else open_df, use_container_width=True)
    else:
        st.info("No open positions in the internal book.")

with col_signals:
    st.caption("Latest pipeline signals")
    signals = snap.get("signals", [])
    if signals:
        sigs_df = pd.DataFrame(signals)
        # Display Kelly Target as a percentage for readability.
        if "kelly_target" in sigs_df.columns:
            sigs_df["kelly_target"] = (
                sigs_df["kelly_target"] * 100
            ).round(1).astype(str) + "%"
        st.dataframe(sigs_df, use_container_width=True)
    else:
        st.info("No signals in last snapshot.")

st.divider()

# ── Row 4: Portfolio risk metrics ─────────────────────────────────────────────
# These three metrics correspond directly to the checks in
# ``execution.risk_gate.PreTradeRiskGate``:
#   • heat   → ``portfolio_heat_check`` (threshold: settings.MAX_PORTFOLIO_HEAT = 6%)
#   • gross  → informational only (no hard gate in the risk pipeline currently)
#   • net    → informational only

st.subheader("🌡️ Portfolio Risk Metrics")

col_heat, col_gross, col_net = st.columns(3)

with col_heat:
    if not open_df.empty and "unrealized_pnl" in open_df.columns:
        # Heat = sum of adverse (negative) unrealized P&L as a fraction of
        # starting equity.  The 100_000 denominator is a placeholder; in
        # production this should come from the broker account snapshot
        # (AccountSnapshot.equity) stored in the state snapshot.
        adverse = open_df[open_df["unrealized_pnl"] < 0]["unrealized_pnl"].abs().sum()
        heat_pct = adverse / 100_000
        heat_colour = (
            "🔴" if heat_pct > 0.05
            else ("🟡" if heat_pct > 0.03 else "🟢")
        )
        st.metric("Portfolio Heat", f"{heat_colour} {heat_pct:.1%}")
    else:
        st.metric("Portfolio Heat", "—")

with col_gross:
    if not open_df.empty and "market_value" in open_df.columns:
        gross = open_df["market_value"].abs().sum()
        st.metric("Gross Exposure", f"${gross:,.0f}")
    else:
        st.metric("Gross Exposure", "—")

with col_net:
    if not open_df.empty and "market_value" in open_df.columns:
        net = open_df["market_value"].sum()
        st.metric("Net Exposure", f"${net:,.0f}")
    else:
        st.metric("Net Exposure", "—")

st.divider()

# ── Row 5: Validation report status ──────────────────────────────────────────
# Mirrors the ``minimum_validation_check`` in the risk gate: if a report is
# not present or not deployable, the gate blocks BUY orders for that strategy.
# Surfacing it here lets the operator catch the issue before the market opens.

st.subheader("🏷️ Validation Report Status")

if val_reports:
    vr_df = pd.DataFrame(val_reports)
    # Translate the boolean ``deployable`` flag to a readable status string.
    status_col = vr_df["deployable"].map({True: "✅ DEPLOYABLE", False: "❌ REJECTED"})
    vr_df.insert(1, "Status", status_col)
    show_cols = [
        c for c in
        ["strategy_id", "Status", "pbo", "dsr", "sharpe", "max_drawdown", "report_date"]
        if c in vr_df.columns
    ]
    st.dataframe(vr_df[show_cols], use_container_width=True)
else:
    st.warning(
        "No validation summaries found in reports/.  "
        "Run: `python -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD`"
    )

st.divider()

# ── Row 6: Recent closed trades ───────────────────────────────────────────────
# Shows the 20 most recent exits, sorted newest-first, as a quick audit of
# what the strategy has been doing.  The full history lives in the database.

st.subheader("🗒️ Recent Closed Trades")

if not closed_df.empty:
    display = closed_df.tail(20).copy()
    if "exit_ts" in display.columns:
        display = display.sort_values("exit_ts", ascending=False)
    show_cols = [
        c for c in
        ["symbol", "strategy_id", "entry_price", "exit_price", "realized_pnl", "exit_ts"]
        if c in display.columns
    ]
    st.dataframe(display[show_cols] if show_cols else display, use_container_width=True)
else:
    st.info("No closed trades yet.")

st.divider()

# ── Row 7: Risk gate block log ────────────────────────────────────────────────
# Operators should review this panel daily.  A high rate of ``portfolio_heat``
# or ``hmm_regime`` blocks is normal during drawdowns; a ``minimum_validation``
# block means a strategy report is missing/expired and must be regenerated.

st.subheader("🚧 Risk Gate Block Log (last 100)")

if block_log:
    block_df = pd.DataFrame(block_log)
    st.dataframe(block_df, use_container_width=True)
else:
    st.success("No blocked orders in the log.")

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
# ``st.rerun()`` re-executes the *entire* script from the top, which causes
# Streamlit to re-evaluate all ``@st.cache_data`` calls.  If the TTL has not
# expired the cached values are returned immediately; if it has, fresh reads
# occur.  The sleep before rerun is what creates the effective refresh interval.
#
# This must be the LAST statement in the script because ``st.rerun()`` raises
# ``RerunException`` internally — any code after it would be unreachable.

time.sleep(refresh_secs)
st.rerun()

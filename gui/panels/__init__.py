"""
gui/panels/__init__.py
======================
Package root — re-exports all public ``render_*`` functions and helpers from
sub-modules.  External callers continue to use ``from gui import panels`` /
``from gui.panels import render_launcher`` unchanged.

Sub-modules extracted so far
-----------------------------
- ``_shared.py``  — shared file-backed loaders, constants, and utility helpers
  (extracted 2026-06-29).  Future extractions add more sub-modules here.
"""

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

# ---------------------------------------------------------------------------
# Shared loaders + utilities — now live in _shared.py; re-exported here for
# backward compatibility so all existing ``from gui.panels import X`` imports
# continue to resolve correctly without any changes at the call sites.
# ---------------------------------------------------------------------------
from gui.panels._shared import (  # noqa: E402
    GICS_SECTORS,
    _BF_EDITOR_COLUMNS,
    _REPO_ROOT,
    _active_symbols,
    _held_symbols,
    _kill_switch,
    _signal_symbols,
    _watchlist_symbols,
    apply_session_regime_filter,
    load_block_log,
    logger,
)

# ===========================================================================
# State-snapshot loaders — KEPT HERE (not in _shared.py) so tests can patch
# ``gui.panels._load_state_snapshot_cached`` on the panels namespace without
# chasing module-reference indirection through _shared.
# ===========================================================================


def load_state_snapshot(apply_filter: bool = True) -> dict:
    """Load the orchestrator's last ``state_snapshot.json`` (empty dict if absent).

    The cache is keyed on the file's **mtime** (not just a TTL), so a fresh
    orchestrator / advisory run is reflected on the NEXT render instead of after
    up to ``DASHBOARD_REFRESH_SECONDS`` (default 30 min) of staleness. The TTL
    remains as an upper bound for the case where mtime is unavailable.

    When ``apply_filter`` is ``True`` (the default), the cross-tab macro-regime
    filter selected in the sidebar (``st.session_state["regime_filter"]``) is
    applied to the ``signals`` list *outside* the cache so every panel reading
    the shared snapshot is automatically regime-filtered. The default
    "All regimes" selection is an identity no-op, so behavior is unchanged until
    the operator picks a concrete regime. Pass ``apply_filter=False`` to read the
    raw, unfiltered snapshot (e.g. to show an "of N total" denominator).
    """
    snap = settings.OUTPUT_DIR / "state_snapshot.json"
    try:
        mtime = snap.stat().st_mtime if snap.exists() else 0.0
    except OSError:
        mtime = 0.0
    data = _load_state_snapshot_cached(str(snap), mtime)
    if apply_filter:
        return apply_session_regime_filter(data)
    return data



@st.cache_data(ttl=settings.DASHBOARD_REFRESH_SECONDS)
def _load_state_snapshot_cached(path: str, _mtime: float) -> dict:
    """Read + parse the snapshot JSON. ``_mtime`` participates in the cache key
    only — a changed mtime is a cache miss and forces a fresh read."""
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ===========================================================================
# Brinson-Fachler attribution — pure helpers (testable without Streamlit)
# ===========================================================================



# Re-export all tabs and helpers
from .launcher import (render_launcher, _render_launcher_safety_controls, _render_preflight_panel, _render_dead_letter_queue, _render_report_provenance_banner)
from .report_viewer import (default_brinson_fachler_frame, parse_pasted_sector_matrix, build_brinson_fachler_inputs, compute_brinson_fachler, validate_brinson_fachler_weights, _render_llm_commentary_button, _render_decision_journal_section, _render_correlation_cluster_section, _render_recommendation_tracking_section, _render_calibration_section, _render_brinson_fachler_section, render_report_viewer)
from .settings_manager import (_current_scalar, render_settings_manager)
from .strategy_matrix import (_render_strategy_mode_toggle, _render_strategy_version_registry, _render_score_decomposition, _render_meta_label_distribution, _render_regime_multiplier_impact, _render_symbol_comparison, render_strategy_matrix)
from .paper_monitor import (render_paper_monitor)
from .gravity_audit import (_render_circuit_breaker_dashboard, _render_dependency_map, _render_strategy_health, _render_gravity_ai_runner_section, render_gravity_audit, _parse_trailing_json)
from .options_matrix import (render_options_matrix)
from .market_data import (render_market_data)
from .pairs import (render_pairs, _signal_label, _align_closes)
from .analytics import (render_analytics)
from .observability import (render_observability, _render_observability_heartbeat_trend, _render_observability_system_telemetry, _render_observability_latency_heatmap, _render_observability_error_log, _render_observability_account_holdings, _render_observability_open_positions_vs_signals, _render_observability_portfolio_risk_metrics, _render_observability_validation_status, _render_observability_recent_closed_trades, _render_observability_equity_curve, _render_observability_risk_gate_block_log, _load_account_snapshot_cache, _load_validation_reports)
from .live_inventory import (render_live_inventory)
from .help import (_load_guide_section, render_help)
from .ai_insights import (_render_gemini_chart_section, _render_opal_research_section, render_ai_insights)
from .ai_control_center import (render_ai_control_center)
from .prompt_registry import (_pr_source_badge, _pr_resolve_source, _pr_cached_versions, _pr_body_for_version, _pr_all_known_ids, render_prompt_registry, utcnow_str)
from .reports_library import render_reports_library
from .validation_lab import render_validation_lab

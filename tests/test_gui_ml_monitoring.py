"""
tests/test_gui_ml_monitoring.py — Analytics ML model freshness/deployability
============================================================================
Offline unit tests for the Phase M4 ML-model-monitoring helpers in
``gui/panels/analytics.py`` and their help-content wiring. Streamlit
``render_*`` functions can't run outside a runtime, so we test the pure helpers
(``_days_since`` / ``_needs_retrain`` / ``_parse_registry_rows`` /
``_deployable_chip`` / ``_fmt_ml_metric``) plus the fact that every new help key
resolves to non-empty text.

Conventions exercised:
- CONSTRAINT #4 (no fabricated values): a missing/malformed metric or date
  yields ``None`` / "—", never a fabricated 0 / False.
- CONSTRAINT #6 (dead-letter): a malformed registry yields ``[]``, never raises.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from gui.help_content import (
    MODEL_RETRAIN_WINDOW_DAYS,
    get_glossary,
    metric_help,
)
from gui.panels import analytics


# ── _days_since ──────────────────────────────────────────────────────────────

def test_days_since_known_string_date():
    ten_days_ago = (date.today() - timedelta(days=10)).isoformat()
    assert analytics._days_since(ten_days_ago) == 10


def test_days_since_accepts_date_object():
    d = date.today() - timedelta(days=45)
    assert analytics._days_since(d) == 45


def test_days_since_future_date_clamps_to_zero():
    future = (date.today() + timedelta(days=7)).isoformat()
    assert analytics._days_since(future) == 0


def test_days_since_none_and_malformed_return_none():
    assert analytics._days_since(None) is None
    assert analytics._days_since("not-a-date") is None
    assert analytics._days_since("") is None


# ── _needs_retrain ───────────────────────────────────────────────────────────

def test_needs_retrain_stale_and_fresh():
    # At the window boundary it is stale (>= window, mirroring MetaLabeler).
    assert analytics._needs_retrain(MODEL_RETRAIN_WINDOW_DAYS) is True
    assert analytics._needs_retrain(MODEL_RETRAIN_WINDOW_DAYS + 5) is True
    assert analytics._needs_retrain(MODEL_RETRAIN_WINDOW_DAYS - 1) is False
    assert analytics._needs_retrain(0) is False


def test_needs_retrain_unknown_age_is_none_not_false():
    # Unknown age must be None (renders "—"), never a fabricated False verdict.
    assert analytics._needs_retrain(None) is None


# ── _parse_registry_rows (dead-letter) ───────────────────────────────────────

_VALID_YAML = """\
models:
  lgbm_ranker:
    role: cross_sectional_ranker
    trained_date: '2026-07-06'
    cpcv_dsr: 0.98
    pbo: 0.20
    n_train: 260
    deployable: true
  meta_labeler:
    role: meta_labeler
    trained_date: null
    cpcv_dsr: null
    pbo: null
    deployable: false
"""


def test_parse_registry_rows_valid():
    rows = analytics._parse_registry_rows(_VALID_YAML)
    assert len(rows) == 2
    by_name = {r["model"]: r for r in rows}
    assert by_name["lgbm_ranker"]["deployable"] is True
    assert by_name["lgbm_ranker"]["cpcv_dsr"] == 0.98
    # null metrics preserved as None (never fabricated 0).
    assert by_name["meta_labeler"]["cpcv_dsr"] is None
    assert by_name["meta_labeler"]["trained_date"] is None


def test_parse_registry_rows_malformed_returns_empty_not_raise():
    # Unbalanced YAML → [] (dead-letter), never an exception.
    assert analytics._parse_registry_rows("models: [unbalanced") == []
    # Non-dict top level.
    assert analytics._parse_registry_rows("- just\n- a\n- list") == []
    # 'models' present but not a mapping.
    assert analytics._parse_registry_rows("models: 3") == []
    # Empty text.
    assert analytics._parse_registry_rows("") == []


def test_parse_registry_rows_skips_malformed_entry():
    rows = analytics._parse_registry_rows(
        "models:\n  good:\n    role: x\n  bad: 7\n"
    )
    assert [r["model"] for r in rows] == ["good"]


# ── _deployable_chip / _fmt_ml_metric ────────────────────────────────────────

def test_deployable_chip():
    assert analytics._deployable_chip(True).startswith("✅")
    assert analytics._deployable_chip(False).startswith("❌")
    assert analytics._deployable_chip(None) == "—"


def test_fmt_ml_metric_none_and_nan_are_dash():
    assert analytics._fmt_ml_metric(None) == "—"
    assert analytics._fmt_ml_metric(float("nan")) == "—"
    assert analytics._fmt_ml_metric("n/a") == "—"
    assert analytics._fmt_ml_metric(0.9876) == "0.9876"


# ── Help-content resolution ──────────────────────────────────────────────────

def test_analytics_metric_help_keys_resolve():
    for key in (
        "analytics.last_trained_age",
        "analytics.needs_retrain",
        "analytics.cpcv_dsr",
        "analytics.pbo",
        "analytics.deployable",
    ):
        assert metric_help(key), f"expected non-empty help for {key}"
    # Unknown key still returns "" (never raises).
    assert metric_help("analytics.does_not_exist") == ""


def test_freshness_glossary_terms_present():
    assert get_glossary("needs retrain") is not None
    assert get_glossary("model freshness") is not None


def test_load_ml_registry_rows_reads_shipped_registry():
    # The shipped ml/registry.yaml should yield rows via the real loader.
    rows = analytics._load_ml_registry_rows()
    assert isinstance(rows, list)
    if rows:  # registry.yaml is runtime-mutating; only assert shape when present
        assert any("lgbm" in str(r.get("model", "")).lower() for r in rows)
        assert all("trained_date" in r and "deployable" in r for r in rows)

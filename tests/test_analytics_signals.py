"""
tests/test_analytics_signals.py — owning tests for gui/panels/analytics_signals.py
===================================================================================
``gui.panels.analytics_signals`` (the read-only ML-registry / news-sentiment /
slippage-CoVaR Analytics-tab helpers) previously had no dedicated owning test —
its pure helpers were only exercised incidentally by
``tests/test_gui_analytics_panels.py``. This file is the owning test and, in
addition to the helper contracts, pins the PR B GUI-caching added to the
registry loader (mtime-keyed ``@st.cache_data``) so it can't silently regress
back to a per-rerun YAML read.

Streamlit ``render_*`` functions can't run outside a runtime, so we test the
pure helpers + cached loaders behind them. ``@st.cache_data`` falls back to a
direct call (with a harmless "No runtime found" warning) outside a live session.
"""

from __future__ import annotations

import os

import pytest

from gui.panels import analytics_signals as asig


# ── caching: the registry loader is @st.cache_data-wrapped ───────────────────

def _is_cached(fn) -> bool:
    """A ``@st.cache_data``-wrapped callable exposes a ``.clear`` method."""
    return callable(fn) and hasattr(fn, "clear")


def test_registry_cached_loader_is_cache_wrapped():
    assert _is_cached(asig._load_registry_rows_cached)


# ── _normalise_registry (pure shaping) ───────────────────────────────────────

def test_normalise_registry_preserves_null_metrics_as_none():
    raw = {"models": {
        "lgbm_ranker": {
            "role": "primary", "trained_date": "2026-07-06",
            "cpcv_dsr": 0.97, "pbo": 0.20, "n_train": 5000, "deployable": True,
        },
        "meta_untrained": {  # null metrics must stay None, never fabricated 0.0
            "role": "meta", "trained_date": None,
            "cpcv_dsr": None, "pbo": None, "n_train": None, "deployable": None,
        },
    }}
    rows = asig._normalise_registry(raw)
    by_model = {r["model"]: r for r in rows}
    assert by_model["lgbm_ranker"]["cpcv_dsr"] == 0.97
    assert by_model["lgbm_ranker"]["deployable"] is True
    # CONSTRAINT #4: absent/null metric preserved as None (not 0.0).
    assert by_model["meta_untrained"]["cpcv_dsr"] is None
    assert by_model["meta_untrained"]["pbo"] is None
    assert by_model["meta_untrained"]["deployable"] is None


def test_normalise_registry_skips_malformed_entries_and_bad_shapes():
    # Non-dict per-model entry is skipped rather than fabricating fields.
    raw = {"models": {"good": {"role": "primary"}, "bad": "not-a-dict"}}
    rows = asig._normalise_registry(raw)
    assert [r["model"] for r in rows] == ["good"]
    # Non-dict root / missing models → [].
    assert asig._normalise_registry(None) == []
    assert asig._normalise_registry([1, 2, 3]) == []
    assert asig._normalise_registry({"no_models_key": 1}) == []


# ── _load_registry_rows (cached, file-backed) ────────────────────────────────

def _write_registry(path, models: dict) -> None:
    import yaml

    path.write_text(yaml.safe_dump({"models": models}), encoding="utf-8")


def test_load_registry_rows_reads_a_tmp_file_through_the_cache(tmp_path):
    asig._load_registry_rows_cached.clear()
    reg = tmp_path / "registry.yaml"
    _write_registry(reg, {
        "lgbm_ranker": {"role": "primary", "cpcv_dsr": 0.96, "pbo": 0.3,
                        "deployable": True, "trained_date": "2026-07-01",
                        "n_train": 4000},
    })
    rows = asig._load_registry_rows(path=str(reg))
    assert len(rows) == 1
    assert rows[0]["model"] == "lgbm_ranker"
    assert {"role", "trained_date", "cpcv_dsr", "pbo", "deployable", "n_train",
            "notes"} <= set(rows[0])
    asig._load_registry_rows_cached.clear()


def test_load_registry_rows_missing_file_returns_empty(tmp_path):
    # Absolute nonexistent path → [] (dead-letter, never raises).
    assert asig._load_registry_rows(path=str(tmp_path / "nope.yaml")) == []
    # Relative nonexistent path resolves under repo root → still [].
    assert asig._load_registry_rows(path="does/not/exist.yaml") == []


def test_load_registry_rows_fresh_read_reflects_file_content(tmp_path):
    """A cleared cache re-reads the file (the loader parses live file content,
    not a stale constant). The mtime cache-key that drives automatic refresh
    inside a live Streamlit runtime is a design property verified by inspection;
    outside a runtime ``@st.cache_data`` memoizes without arg-keyed lookup, so
    the refresh is exercised here via an explicit ``.clear()``."""
    asig._load_registry_rows_cached.clear()
    reg = tmp_path / "registry.yaml"
    _write_registry(reg, {"model_a": {"role": "primary"}})
    first = asig._load_registry_rows(path=str(reg))
    assert [r["model"] for r in first] == ["model_a"]

    _write_registry(reg, {"model_b": {"role": "meta"}})
    future = os.stat(reg).st_mtime + 100
    os.utime(reg, (future, future))
    asig._load_registry_rows_cached.clear()  # simulate the mtime-driven miss

    second = asig._load_registry_rows(path=str(reg))
    assert [r["model"] for r in second] == ["model_b"]
    asig._load_registry_rows_cached.clear()


def test_load_registry_rows_reads_real_shipped_registry():
    rows = asig._load_registry_rows()  # default ml/registry.yaml
    assert rows, "expected the shipped registry to yield rows"
    assert any("lgbm" in str(r.get("model", "")).lower() for r in rows)


# ── _fmt_metric (pure formatting; CONSTRAINT #4) ─────────────────────────────

def test_fmt_metric_degrades_missing_to_dash():
    assert asig._fmt_metric(None) == "—"
    assert asig._fmt_metric(float("nan")) == "—"
    assert asig._fmt_metric("not-a-number") == "—"
    assert asig._fmt_metric(0.9712) == "0.9712"
    assert asig._fmt_metric(4000, "{:.0f}") == "4000"


# ── news sentiment prep ──────────────────────────────────────────────────────

def test_sentiment_label_thresholds():
    assert asig._sentiment_label(0.5) == "🟢 Positive"
    assert asig._sentiment_label(-0.5) == "🔴 Negative"
    assert asig._sentiment_label(0.0) == "🟡 Neutral"
    # Boundary: exactly 0.15 is NOT positive (strict >).
    assert asig._sentiment_label(0.15) == "🟡 Neutral"


def test_sentiment_rows_filters_null_and_nan_keeps_numeric():
    snap = {"signals": [
        {"symbol": "AAPL", "news_sentiment": 0.4},
        {"symbol": "MSFT", "news_sentiment": None},          # skipped
        {"symbol": "XOM"},                                   # absent → skipped
        {"symbol": "NVDA", "news_sentiment": float("nan")},  # NaN → skipped
        {"symbol": "TSLA", "news_sentiment": "oops"},        # non-numeric → skipped
    ]}
    rows = asig._sentiment_rows(snap)
    assert [r["Symbol"] for r in rows] == ["AAPL"]
    assert rows[0]["News Sentiment"] == 0.4
    assert rows[0]["Signal"] == "🟢 Positive"


def test_sentiment_rows_none_and_malformed_snapshots_are_empty():
    assert asig._sentiment_rows(None) == []
    assert asig._sentiment_rows({}) == []
    assert asig._sentiment_rows({"signals": "not-a-list"}) == []


# ── slippage / CoVaR prep ────────────────────────────────────────────────────

def test_risk_rows_keeps_partial_and_skips_both_null():
    snap = {"signals": [
        {"symbol": "AAPL", "realized_slippage": 0.001, "covar_proxy": None},  # kept
        {"symbol": "MSFT", "realized_slippage": None, "covar_proxy": 0.3},    # kept
        {"symbol": "XOM", "realized_slippage": None, "covar_proxy": None},    # skipped
        {"symbol": "IBM", "realized_slippage": float("nan"),
         "covar_proxy": float("nan")},                                        # skipped
    ]}
    rows = asig._risk_rows(snap)
    by_sym = {r["Symbol"]: r for r in rows}
    assert set(by_sym) == {"AAPL", "MSFT"}
    # CONSTRAINT #4: the missing member is None (rendered "—"), never fabricated 0.
    assert by_sym["AAPL"]["CoVaR Proxy"] is None
    assert by_sym["MSFT"]["Realized Slippage"] is None


def test_risk_rows_none_snapshot_safe():
    assert asig._risk_rows(None) == []
    assert asig._risk_rows({"signals": []}) == []

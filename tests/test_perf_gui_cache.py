"""
tests/test_perf_gui_cache.py — PR B GUI-caching invariants (behavior-preserving)
================================================================================
Locks in the Streamlit-layer caching added in PR B so it can't silently regress
back to per-rerun network/DB work:

* Analytics broker-P&L + equity-curve loaders and the Pairs Close fetch are all
  ``@st.cache_data``-wrapped (a full Robinhood login / fresh sqlite connect no
  longer fires on every rerun).
* The Observability forecast-skill table is served by ONE batched, cached loader
  (``_forecast_skill_rows``) instead of the old ~120-connections-per-rerun double
  loop — and it degrades to an empty result on a cold/empty DB (dead-letter,
  CONSTRAINT #6), never a fabricated row.
* The Analytics realized-performance loader distinguishes a hard fetch FAILURE
  (``None`` → "unavailable" message) from a genuinely-empty result (``{}`` →
  "no trades yet"), preserving the pre-cache failure-state semantics.

Streamlit ``render_*`` functions can't run outside a runtime, so we test the pure
cached loaders behind them. ``@st.cache_data`` falls back to a direct call (with a
harmless "No runtime found" warning) when invoked outside a live session.
"""

from __future__ import annotations

import pandas as pd

from gui.panels import (
    ai_insights,
    analytics,
    analytics_signals,
    gravity_audit,
    live_inventory,
    market_data,
    observability,
    options_matrix,
    pairs,
)


def _is_cached(fn) -> bool:
    """A ``@st.cache_data``-wrapped callable exposes a ``.clear`` method."""
    return callable(fn) and hasattr(fn, "clear")


# ── loaders are cache-wrapped ────────────────────────────────────────────────

def test_analytics_loaders_are_cache_wrapped():
    assert _is_cached(analytics._load_realized_performance)
    assert _is_cached(analytics._load_account_equity_history)


# ── PR B round 2: the 5 remaining panels' per-rerun loaders are cache-wrapped ─

def test_live_inventory_sync_report_loader_is_cache_wrapped():
    assert _is_cached(live_inventory._read_sync_report_cache_cached)


def test_options_matrix_directive_loader_is_cache_wrapped():
    assert _is_cached(options_matrix._compute_directive_row)


def test_market_data_default_symbols_loader_is_cache_wrapped():
    assert _is_cached(market_data._load_default_signal_symbols_cached)


def test_gravity_audit_loaders_are_cache_wrapped():
    assert _is_cached(gravity_audit._load_gravity_report_cached)
    assert _is_cached(gravity_audit._load_validation_summaries_cached)


def test_analytics_signals_registry_loader_is_cache_wrapped():
    assert _is_cached(analytics_signals._load_registry_rows_cached)


# ── new loaders preserve dead-letter / empty-state behaviour ─────────────────

def test_live_inventory_sync_report_cache_missing_is_none(tmp_path):
    """A missing cache file → None (unchanged read_cache contract), never raise."""
    live_inventory._read_sync_report_cache_cached.clear()
    out = live_inventory._read_sync_report_cache_cached(
        str(tmp_path / "sync_report.json"), 0.0
    )
    assert out is None
    live_inventory._read_sync_report_cache_cached.clear()


def test_market_data_default_symbols_missing_snapshot_is_empty(tmp_path):
    market_data._load_default_signal_symbols_cached.clear()
    out = market_data._load_default_signal_symbols_cached(
        str(tmp_path / "state_snapshot.json"), 0.0
    )
    assert out == []
    market_data._load_default_signal_symbols_cached.clear()


def test_gravity_validation_summaries_empty_dir_is_empty(tmp_path):
    gravity_audit._load_validation_summaries_cached.clear()
    out = gravity_audit._load_validation_summaries_cached(str(tmp_path), "")
    assert out == []
    gravity_audit._load_validation_summaries_cached.clear()


def test_gravity_report_loader_missing_file_is_empty(tmp_path):
    gravity_audit._load_gravity_report_cached.clear()
    out = gravity_audit._load_gravity_report_cached(
        str(tmp_path / "gravity_verification_report.json"), 0.0
    )
    assert out == []
    gravity_audit._load_gravity_report_cached.clear()


def test_ai_insights_bars_loader_is_cache_wrapped():
    assert _is_cached(ai_insights._load_bars_cached)


def test_pairs_fetch_close_is_cache_wrapped():
    assert _is_cached(pairs._fetch_close)


def test_observability_forecast_skill_loader_is_cache_wrapped():
    assert _is_cached(observability._forecast_skill_rows)


# ── failure-state semantics preserved ────────────────────────────────────────

def test_realized_performance_failure_returns_none(monkeypatch):
    """A hard fetch failure yields None (→ 'unavailable'), NOT {} ('no trades')."""
    analytics._load_realized_performance.clear()

    def _boom():
        raise RuntimeError("robinhood down")

    monkeypatch.setattr("data.robinhood_orders.realized_performance", _boom)
    assert analytics._load_realized_performance() is None
    analytics._load_realized_performance.clear()


def test_account_equity_history_failure_returns_empty_df(monkeypatch):
    analytics._load_account_equity_history.clear()

    class _BoomStore:
        def account_snapshot_history(self):
            raise RuntimeError("db gone")

    monkeypatch.setattr("data.historical_store.HistoricalStore", _BoomStore)
    out = analytics._load_account_equity_history()
    assert isinstance(out, pd.DataFrame) and out.empty
    analytics._load_account_equity_history.clear()


# ── batched forecast-skill loader degrades cleanly on an empty DB ─────────────

def test_forecast_skill_rows_empty_db_is_dead_letter(tmp_path):
    """A freshly-provisioned (row-less) forecast_errors DB → no *history*
    (``any_history`` False, which drives the render's empty-state), no raise, and
    no fabricated RMSE/counts. Placeholder ``ALL_MODEL_NAMES`` rows with 0
    pending/completed and ``—`` RMSE are the SAME shape the pre-cache inline loop
    produced, so they're allowed — but they must carry no real data."""
    from forecasting.forecast_tracker import ForecastTracker

    db = str(tmp_path / "ft.db")
    ForecastTracker(db_path=db)  # provisions the forecast_errors table, 0 rows

    observability._forecast_skill_rows.clear()
    out = observability._forecast_skill_rows(db, 0.0, ("AAPL", "MSFT"), 180, 10)
    assert isinstance(out, dict)
    assert out.get("any_history") is False
    for r in out.get("rows", []):
        assert r["Pending"] == 0 and r["Completed"] == 0
        assert r["RMSE ($)"] == "—"           # no fabricated 0.0 (CONSTRAINT #4)
    observability._forecast_skill_rows.clear()


def test_forecast_skill_rows_missing_db_is_dead_letter(tmp_path):
    """A nonexistent DB path must not raise into the UI and reports no history."""
    observability._forecast_skill_rows.clear()
    out = observability._forecast_skill_rows(
        str(tmp_path / "nope.db"), 0.0, ("AAPL",), 180, 10
    )
    assert isinstance(out, dict) and out.get("any_history") is False
    observability._forecast_skill_rows.clear()

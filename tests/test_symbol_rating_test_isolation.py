"""Regression tests for the 2026-10 symbol_rating_events live-DB test leakage.

Before ``conftest.py::_isolate_symbol_rating_db_in_tests`` existed, a bare
``SymbolRatingStore()`` resolved the operator's real shared DB and
``tests/test_run_once.py`` & co. wrote ~26.5k fixture rows into it. See
``docs/known_issues/symbol_rating_burst_rows_test_leakage.md``.
"""
from __future__ import annotations

from pathlib import Path

import settings
from rating.symbol_rating_store import SymbolRatingStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _event(symbol: str = "AAPL", score: float = 55.0) -> dict:
    return {"symbol": symbol, "score": score, "action_signal": "HOLD", "tier": "GOOD", "is_held": False}


def test_bare_store_resolves_under_tmp_path(tmp_path):
    store = SymbolRatingStore()
    db_path = Path(store.engine.url.database).resolve()
    assert str(db_path).startswith(str(tmp_path.resolve()))


def test_write_then_readonly_read_share_the_isolated_file():
    SymbolRatingStore().record_ratings([_event()], cycle_id="c1")
    rows = SymbolRatingStore(readonly=True).get_recent("AAPL")
    assert len(rows) == 1 and rows[0]["score"] == 55.0


def test_readonly_store_before_any_write_reads_empty_not_raises():
    reader = SymbolRatingStore(readonly=True)
    assert reader.get_recent() == []
    assert reader.get_consecutive_bad_cycles("AAPL") == 0
    assert reader.get_excluded_symbols(threshold_cycles=5) == set()


def test_readonly_store_on_missing_file_degrades_to_empty(tmp_path):
    """The case the fixture's pre-created schema avoids: a readonly store on a
    file that does not exist must still degrade (CONSTRAINT #6), not raise."""
    missing = tmp_path / "does_not_exist.db"
    reader = SymbolRatingStore(db_url=f"sqlite:///{missing}", readonly=True)
    assert reader.get_recent() == []
    assert reader.get_consecutive_bad_cycles("AAPL") == 0
    assert reader.get_excluded_symbols(threshold_cycles=5) == set()


def test_production_writer_lands_in_isolated_db_not_local_data_root(monkeypatch, tmp_path):
    """Drive the real ``_record_symbol_ratings`` writer with the flag on and
    LOCAL_DATA_ROOT pointed at a scratch dir: that dir must stay empty."""
    import pandas as pd

    from pipeline.production_steps import _record_symbol_ratings

    scratch = tmp_path / "would_be_live_root"
    scratch.mkdir()
    monkeypatch.setattr(settings.settings, "LOCAL_DATA_ROOT", str(scratch))
    monkeypatch.setattr(settings.settings, "SYMBOL_RATING_ENABLED", True)
    df = pd.DataFrame([{"Symbol": "AAPL", "Score": 55.0, "Action Signal": "HOLD", "Robinhood Shares": 0.0}])
    _record_symbol_ratings(df, "2026-01-01T00:00:00+00:00")
    assert list(scratch.iterdir()) == []
    assert len(SymbolRatingStore(readonly=True).get_recent("AAPL")) == 1


def test_conftest_registers_the_symbol_rating_isolation_fixture():
    text = (REPO_ROOT / "conftest.py").read_text(encoding="utf-8")
    assert "def _isolate_symbol_rating_db_in_tests" in text
    assert '(_srs, "resolve_database_url"' in text

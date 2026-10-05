"""Tests for scripts/cleanup_symbol_rating_test_rows.py against a tmp SQLite DB."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from rating.symbol_rating_store import SymbolRatingStore
from scripts import cleanup_symbol_rating_test_rows as cl


def _seed(db: Path) -> None:
    store = SymbolRatingStore(db_url=f"sqlite:///{db}")
    ev = lambda s, sc, held=False: {  # noqa: E731
        "symbol": s, "score": sc, "action_signal": "HOLD",
        "tier": "BAD" if sc < 35 else "GOOD", "is_held": held,
    }
    # genuine: AQN is BAD for 5 genuine cycles, then fake GOOD cycle resets it.
    for i in range(5):
        store.record_ratings([ev("AQN", 0.0), ev("XYZ", 70.0), ev("ABC", 20.0 + i)], cycle_id=f"real{i}")
    store.record_ratings([ev("AQN", 55.0), ev("MSFT", 55.0)], cycle_id="fake_good")
    store.record_ratings([ev("AAPL", 1.0), ev("MSFT", 1.0)], cycle_id="fake_bad")
    store.record_ratings([ev("AAPL", 20.0)], cycle_id="manual_reinclude")  # never matched
    store.engine.dispose()


def _count(db: Path) -> int:
    c = sqlite3.connect(db)
    try:
        return c.execute("select count(*) from symbol_rating_events").fetchone()[0]
    finally:
        c.close()


def test_dry_run_writes_nothing_and_reports_flip(tmp_path, capsys):
    db = tmp_path / "q.db"
    _seed(db)
    before = _count(db)
    assert cl.main(["--db-path", str(db)]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and _count(db) == before
    assert "matched test cycles: 2" in out
    assert "AQN" in out  # exclusion appears only AFTER the fake GOOD row is removed
    assert not (tmp_path / "backups").exists()


def test_apply_backs_up_then_deletes_only_single_score_cycles(tmp_path):
    db = tmp_path / "q.db"
    _seed(db)
    before = _count(db)
    assert cl.main(["--db-path", str(db), "--apply"]) == 0
    assert _count(db) == before - 4
    backups = list((tmp_path / "backups").glob("*.db"))
    assert len(backups) == 1
    b = sqlite3.connect(backups[0])
    assert b.execute("select count(*) from symbol_rating_events").fetchone()[0] == before
    b.close()
    c = sqlite3.connect(db)
    cycles = {r[0] for r in c.execute("select distinct cycle_id from symbol_rating_events")}
    c.close()
    assert "fake_good" not in cycles and "fake_bad" not in cycles
    assert "manual_reinclude" in cycles and "real0" in cycles


def test_max_id_bound_protects_newer_rows(tmp_path):
    db = tmp_path / "q.db"
    _seed(db)
    c = sqlite3.connect(db)
    last_real = c.execute("select max(id) from symbol_rating_events where cycle_id='real4'").fetchone()[0]
    c.close()
    assert cl.main(["--db-path", str(db), "--apply", "--max-id", str(last_real)]) == 0
    assert _count(db) == 15 + 2 + 2 + 1  # nothing beyond max-id deleted


def test_apply_is_idempotent(tmp_path):
    db = tmp_path / "q.db"
    _seed(db)
    assert cl.main(["--db-path", str(db), "--apply"]) == 0
    n = _count(db)
    assert cl.main(["--db-path", str(db), "--apply"]) == 0
    assert _count(db) == n


def test_exclusions_matches_store_semantics(tmp_path):
    db = tmp_path / "q.db"
    _seed(db)
    conn = sqlite3.connect(db)
    mine = set(cl.exclusions(conn, threshold=5, ignore_cycle_ids={"fake_good", "fake_bad"}))
    conn.close()
    real = SymbolRatingStore(db_url=f"sqlite:///{db}", readonly=True)
    before = real.get_excluded_symbols(threshold_cycles=5)
    cl.main(["--db-path", str(db), "--apply"])
    real2 = SymbolRatingStore(db_url=f"sqlite:///{db}", readonly=True)
    assert real2.get_excluded_symbols(threshold_cycles=5) == mine == {"AQN", "ABC"}
    # before cleanup the fake GOOD row had reset AQN's streak
    assert before == {"ABC"}


def test_missing_db_returns_error(tmp_path):
    assert cl.main(["--db-path", str(tmp_path / "nope.db")]) == 1


def test_non_sqlite_url_rejected():
    with pytest.raises(SystemExit):
        cl._sqlite_path_from_url("postgresql://x/y")

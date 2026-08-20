"""Tests for validation/validation_history_store.py -- the durable
validation_runs DB table backing pilots/validation_trend.py's cross-worktree
read path.

Mirrors tests/test_run_history_store.py's conventions (in-memory SQLite for
CRUD, a tmp_path-backed file DB for readonly=True, missing-table degrade)."""

from __future__ import annotations

import pytest

from validation.validation_history_store import ValidationHistoryStore


def _summary(strategy_id="dbtest", **overrides) -> dict:
    defaults = dict(
        strategy_id=strategy_id,
        deployable=True,
        family_deployable=None,
        family_bh_significant=None,
        pbo=0.1,
        dsr=0.98,
        sharpe=0.8,
        max_drawdown=0.15,
        is_options_selling=False,
        stress_gate_passed=True,
        start_date="2020-01-01",
        end_date="2020-12-31",
        report_date="2026-08-01",
        n_trials=10,
        family_multiple_testing=None,
        equity_curve=[],
        benchmark_curve=[],
        macro_benchmark_curve=[],
    )
    defaults.update(overrides)
    return defaults


def test_record_and_get_recent_round_trip():
    store = ValidationHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_summary())

    rows = store.get_recent(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy_id"] == "dbtest"
    assert row["deployable"] is True
    assert row["pbo"] == pytest.approx(0.1)
    assert row["dsr"] == pytest.approx(0.98)
    assert row["sharpe"] == pytest.approx(0.8)
    assert row["max_drawdown"] == pytest.approx(0.15)
    assert row["n_trials"] == 10
    assert row["recorded_at"] is not None


def test_get_recent_most_recent_first():
    store = ValidationHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_summary(report_date="2026-08-01", dsr=0.90))
    store.record_run(_summary(report_date="2026-08-08", dsr=0.98))

    rows = store.get_recent(limit=10)
    assert [r["report_date"] for r in rows] == ["2026-08-08", "2026-08-01"]


def test_get_recent_filters_by_strategy_id():
    store = ValidationHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_summary(strategy_id="a", dsr=0.9))
    store.record_run(_summary(strategy_id="b", dsr=0.5))

    rows = store.get_recent(strategy_id="a", limit=10)
    assert len(rows) == 1
    assert rows[0]["strategy_id"] == "a"


def test_get_recent_respects_limit():
    store = ValidationHistoryStore(db_url="sqlite:///:memory:")
    for i in range(5):
        store.record_run(_summary(report_date=f"2026-08-0{i}"))

    rows = store.get_recent(limit=2)
    assert len(rows) == 2


def test_record_run_never_upserts_always_appends():
    """Unlike RunHistoryStore.record_run (keyed on run_id), every call here
    inserts a NEW row -- matching reports/history/*.jsonl's append-only
    semantics: a strategy validated N times has N rows."""
    store = ValidationHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_summary())
    store.record_run(_summary())

    rows = store.get_recent(limit=10)
    assert len(rows) == 2


def test_get_latest_per_strategy_one_row_per_strategy():
    store = ValidationHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_summary(strategy_id="a", report_date="2026-08-01", dsr=0.5))
    store.record_run(_summary(strategy_id="a", report_date="2026-08-08", dsr=0.9))
    store.record_run(_summary(strategy_id="b", report_date="2026-08-01", dsr=0.3))

    latest = store.get_latest_per_strategy()
    assert set(latest.keys()) == {"a", "b"}
    assert latest["a"]["dsr"] == pytest.approx(0.9)  # the more recent "a" row
    assert latest["b"]["dsr"] == pytest.approx(0.3)


def test_missing_metric_stays_none_never_fabricated_zero():
    """CONSTRAINT #4: an unmeasured metric must round-trip as None, not 0.0."""
    store = ValidationHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_summary(sharpe=None, max_drawdown=None))

    row = store.get_recent(limit=1)[0]
    assert row["sharpe"] is None
    assert row["max_drawdown"] is None


def test_summary_json_fields_survive_round_trip():
    """Fields not promoted to their own column (e.g. equity_curve,
    family_multiple_testing) must still be recoverable from the persisted
    summary_json blob."""
    store = ValidationHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_summary(equity_curve=[{"date": "2026-01-01", "value": 100.0}]))

    row = store.get_recent(limit=1)[0]
    assert row["equity_curve"] == [{"date": "2026-01-01", "value": 100.0}]


# ---------------------------------------------------------------------------
# readonly=True
# ---------------------------------------------------------------------------


def test_readonly_store_reads_data_written_by_a_write_mode_store(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'validation.db'}"
    writer = ValidationHistoryStore(db_url=db_url)
    writer.record_run(_summary())

    reader = ValidationHistoryStore(db_url=db_url, readonly=True)
    rows = reader.get_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["strategy_id"] == "dbtest"


def test_readonly_store_write_raises_rather_than_fabricate_success(tmp_path):
    """CONSTRAINT #4: mirrors RunHistoryStore's contract -- a readonly
    instance must not silently no-op a write."""
    db_url = f"sqlite:///{tmp_path / 'validation.db'}"
    ValidationHistoryStore(db_url=db_url)  # write-mode: creates the schema first
    reader = ValidationHistoryStore(db_url=db_url, readonly=True)
    with pytest.raises(Exception):
        reader.record_run(_summary())


def test_readonly_store_degrades_to_empty_on_missing_table(tmp_path):
    """No prior write-mode store has ever run -> the validation_runs table
    doesn't exist. A readonly instance must degrade to []/{}, never crash
    (CONSTRAINT #6)."""
    db_path = tmp_path / "never_written.db"
    db_path.touch()
    reader = ValidationHistoryStore(db_url=f"sqlite:///{db_path}", readonly=True)
    assert reader.get_recent(limit=10) == []
    assert reader.get_latest_per_strategy() == {}


def test_readonly_store_construction_skips_ddl(tmp_path, monkeypatch):
    """readonly=True must not call Base.metadata.create_all -- a write a
    read-only engine would reject anyway."""
    import validation.validation_history_store as store_module

    calls = []
    monkeypatch.setattr(
        store_module.Base.metadata, "create_all",
        lambda *a, **k: calls.append("create_all"),
    )
    ValidationHistoryStore(db_url=f"sqlite:///{tmp_path / 'validation.db'}", readonly=True)
    assert calls == []

"""Tests for desktop/run_history_store.py -- the durable pipeline_runs DB
table backing the Pipeline Dashboard's "Full run history" section.

Mirrors tests/test_transactions_store.py's conventions (in-memory SQLite for
CRUD, a tmp_path-backed file DB for readonly=True, missing-table degrade)."""

from datetime import datetime, timezone

import pytest

from desktop.daemon_runtime import RunRecord, RunState
from desktop.run_history_store import RunHistoryStore


def _record(run_id="orch-1", state=RunState.SUCCEEDED, **overrides) -> RunRecord:
    defaults = dict(
        run_id=run_id,
        state=state,
        mode="full",
        started_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 18, 10, 1, tzinfo=timezone.utc),
        duration_seconds=41.8,
        error=None,
        reason="manual",
        progress=None,
    )
    defaults.update(overrides)
    return RunRecord(**defaults)


def test_record_and_get_recent_round_trip():
    store = RunHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_record())

    rows = store.get_recent(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "orch-1"
    assert row["state"] == "succeeded"
    assert row["mode"] == "full"
    assert row["reason"] == "manual"
    assert row["duration_seconds"] == 41.8
    assert row["error"] is None
    assert row["started_at"] is not None
    assert row["finished_at"] is not None


def test_get_recent_most_recent_first():
    store = RunHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_record("orch-old", started_at=datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)))
    store.record_run(_record("orch-new", started_at=datetime(2026, 7, 18, 11, 0, tzinfo=timezone.utc)))

    rows = store.get_recent(limit=10)
    assert [r["run_id"] for r in rows] == ["orch-new", "orch-old"]


def test_get_recent_respects_limit():
    store = RunHistoryStore(db_url="sqlite:///:memory:")
    for i in range(5):
        store.record_run(_record(f"orch-{i}", started_at=datetime(2026, 7, 18, i, 0, tzinfo=timezone.utc)))

    rows = store.get_recent(limit=2)
    assert len(rows) == 2


def test_record_run_upserts_same_run_id():
    """A RUNNING record later completing must update the same row, not
    duplicate it -- record_run is keyed on run_id."""
    store = RunHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_record("orch-1", state=RunState.RUNNING, finished_at=None, duration_seconds=None))
    store.record_run(_record("orch-1", state=RunState.SUCCEEDED))

    rows = store.get_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["state"] == "succeeded"


def test_progress_dict_round_trips_through_json():
    store = RunHistoryStore(db_url="sqlite:///:memory:")
    progress = {"run_id": "orch-1", "state": "running", "percent": 42}
    store.record_run(_record(progress=progress))

    rows = store.get_recent(limit=10)
    assert rows[0]["progress"] == progress


def test_failed_run_carries_its_real_error():
    store = RunHistoryStore(db_url="sqlite:///:memory:")
    store.record_run(_record(state=RunState.FAILED, error="ForecastingEngine: insufficient bars"))

    rows = store.get_recent(limit=10)
    assert rows[0]["state"] == "failed"
    assert rows[0]["error"] == "ForecastingEngine: insufficient bars"


# ---------------------------------------------------------------------------
# readonly=True
# ---------------------------------------------------------------------------


def test_readonly_store_reads_data_written_by_a_write_mode_store(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'runs.db'}"
    writer = RunHistoryStore(db_url=db_url)
    writer.record_run(_record())

    reader = RunHistoryStore(db_url=db_url, readonly=True)
    rows = reader.get_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["run_id"] == "orch-1"


def test_readonly_store_write_raises_rather_than_fabricate_success(tmp_path):
    """CONSTRAINT #4: mirrors TransactionsStore's contract -- a readonly
    instance must not silently no-op a write."""
    db_url = f"sqlite:///{tmp_path / 'runs.db'}"
    RunHistoryStore(db_url=db_url)  # write-mode: creates the schema first
    reader = RunHistoryStore(db_url=db_url, readonly=True)
    with pytest.raises(Exception):
        reader.record_run(_record())


def test_readonly_store_degrades_to_empty_list_on_missing_table(tmp_path):
    """No prior write-mode store has ever run -> the pipeline_runs table
    doesn't exist. A readonly instance must degrade to [], never crash
    (CONSTRAINT #6)."""
    db_path = tmp_path / "never_written.db"
    db_path.touch()
    reader = RunHistoryStore(db_url=f"sqlite:///{db_path}", readonly=True)
    assert reader.get_recent(limit=10) == []


def test_readonly_store_construction_skips_ddl(tmp_path, monkeypatch):
    """readonly=True must not call Base.metadata.create_all -- a write a
    read-only engine would reject anyway."""
    import desktop.run_history_store as store_module

    calls = []
    monkeypatch.setattr(
        store_module.Base.metadata, "create_all",
        lambda *a, **k: calls.append("create_all"),
    )
    RunHistoryStore(db_url=f"sqlite:///{tmp_path / 'runs.db'}", readonly=True)
    assert calls == []

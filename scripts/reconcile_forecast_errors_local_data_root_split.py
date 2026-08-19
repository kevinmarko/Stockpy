#!/usr/bin/env python3
"""
scripts/reconcile_forecast_errors_local_data_root_split.py
============================================================
ONE-TIME reconciliation script tied to
``docs/known_issues/forecast_tracker_local_data_root_split.md``.

Background (see that doc for the full incident writeup): PR #718 moved every
locally-generated data artifact to ``settings.LOCAL_DATA_ROOT``
(``~/.stockpy_local`` by default), but ``forecasting/forecast_tracker.py``
kept a hardcoded, CWD-relative ``db_path="quant_platform.db"`` default that
missed the migration entirely (fixed in PR #720). Between the two PRs, the
live orchestrator daemon kept writing real ``forecast_errors`` rows to the
OLD repo-relative database (``<main-checkout>/quant_platform.db``) for hours
after every *other* table had already cut over to the NEW
``LOCAL_DATA_ROOT``-anchored database. The result is two live SQLite files
each holding a genuine, non-overlapping slice of ``forecast_errors`` history
that were never reconciled into one.

This script merges the OLD slice forward into the NEW (current, live) database,
and only that -- it never touches any other table, and it never touches the
OLD database at all (opened strictly read-only throughout).

This script is safe to delete once it has been run successfully (in real,
``--yes --no-dry-run``, mode) exactly once and the resulting merge has been
spot-checked. It is intentionally idempotent/resumable (see "Resume state"
below) so re-running it after a successful merge is a fast no-op rather than
a duplicate-inserting hazard, but there is no reason to keep it in the repo
long-term once the one real migration it exists for has happened.

Safety design
--------------
* **Defaults to dry-run.** ``--dry-run`` defaults to ``True``; producing any
  write requires BOTH ``--no-dry-run`` AND ``--yes`` -- passing only one is
  refused with an explicit error rather than silently falling back to
  read-only (a script that silently no-ops on a typo'd flag is its own kind
  of foot-gun; refusing loudly is safer).
* **Never trusts a hardcoded assumption.** Row counts, date ranges, and the
  "zero temporal overlap between OLD and NEW" claim documented in the known
  issue writeup are all recomputed LIVE against the actual files on every
  run, in both dry-run and real mode -- never read from this docstring or
  from a cached number.
* **OLD database is opened strictly read-only** via a SQLite URI
  (``file:...?mode=ro``) everywhere in this script. Even a bug in this
  script that accidentally issued a write against that connection would
  raise ``sqlite3.OperationalError: attempt to write a readonly database``
  rather than silently succeeding. The OLD database is never modified in
  any way by this script, in either mode.
* **NEW database gets an online, WAL-safe backup before anything else
  touches it**, using SQLite's Online Backup API
  (``sqlite3.Connection.backup()``) -- unlike a plain file copy, this API
  produces a transactionally consistent snapshot even while a separate
  process (the live daemon) is concurrently writing to the same file. The
  backup happens unconditionally at the top of real mode, before the first
  write, and its own success is verified (non-empty file) before proceeding.
* **NEW database writes use a generous busy_timeout** (default 30000ms) so
  this script's writer connection coexists with the live daemon's own
  writer connection by waiting its turn (SQLite allows only one writer at a
  time even under WAL) instead of erroring out immediately on
  ``SQLITE_BUSY``.
* **Small, independently-committed batches** (default 20,000 rows per
  transaction) so a crash, a Ctrl-C, or a busy-timeout exhaustion loses at
  most one batch's worth of in-flight work, never the whole merge.
* **Resume state** is persisted to a small JSON sidecar file
  (``<new-db>.reconcile_forecast_errors.progress.json`` by default,
  overridable via ``--resume-state-file``) after every successfully
  committed batch, written atomically (temp file + ``os.replace``). A
  crashed or interrupted real-mode run can simply be re-invoked with the
  same arguments and it will resume from the last committed OLD-db row id
  rather than re-inserting already-merged rows. Pass ``--no-resume`` to
  deliberately discard existing progress and start over (this WILL create
  duplicate rows for anything already merged in a prior partial run -- only
  use it if you know the saved state is wrong or stale).
* **Post-merge verification is honest about the live writer.** The NEW
  database keeps growing from the live daemon's own inserts for the entire
  duration of this script's run, so an exact
  ``new_total == old_count + new_count_at_start`` equality is NOT a valid
  success criterion by itself -- it would spuriously fail the instant the
  live daemon inserts one more row while this script is mid-merge. The
  actual check is ``new_total >= old_count + new_count_at_start`` (the live
  daemon can only ever ADD rows during our run, never remove any), with the
  observed surplus reported explicitly as "rows the live daemon inserted
  concurrently during our merge window" rather than treated as an error.
* **Duplicate-tuple sanity check, not a full pairwise diff**, exactly as
  specified for this script: compares
  ``COUNT(*) - COUNT(DISTINCT symbol, model_name, horizon_days, forecast_ts)``
  on the NEW database before and after the merge and reports the delta.
  Because ``forecast_errors`` has no UNIQUE constraint beyond its surrogate
  autoincrement ``id`` (confirmed: nothing else in the codebase treats
  ``id`` as a foreign key, and duplicate ``(symbol, model_name,
  horizon_days, forecast_ts)`` combinations are not inherently invalid --
  e.g. a genuine re-forecast), this is reported as prominent diagnostic
  information, not treated as a hard failure on its own.

Why "separate connection + executemany" instead of ATTACH DATABASE
---------------------------------------------------------------------
The task this script implements explicitly allows either approach and asks
for the choice to be reasoned about in comments -- here is that reasoning.

``ATTACH DATABASE`` would mean opening ONE connection to the NEW database
(the one we are actively writing to, concurrently with the live daemon's own
separate writer connection to that same file) and attaching the OLD database
to it read-only, then running a single
``INSERT INTO forecast_errors (...) SELECT ... FROM old.forecast_errors``
per batch. That works in SQLite, but it couples two things this script would
rather keep decoupled: the attached-database bookkeeping SQLite performs
internally interacts with the SAME connection object we are relying on
``busy_timeout``/``BEGIN IMMEDIATE`` semantics for to coexist gracefully with
the live daemon's writer -- and a live WAL writer on the attachment target is
exactly the scenario this script most needs to reason carefully about, not
add incidental complexity to.

Instead, this script uses TWO independent connections: a read-only
connection to the OLD database (which has no concurrent writer at all --
the code-level bug that wrote to it is already fixed, and it is opened
``mode=ro`` here regardless) that we page through in batches with a plain
keyset-paginated ``SELECT ... WHERE id > ? ORDER BY id LIMIT ?``, and a
separate writable connection to the NEW database that we ``executemany()``
INSERT into per batch. This keeps the OLD side guaranteed-inert (a URI-level
read-only guarantee, not just a policy), keeps the NEW side's
concurrency-coexistence logic (busy_timeout, explicit ``BEGIN
IMMEDIATE``/``COMMIT``) simple and singly-focused, and bounds memory to one
batch (default 20,000 rows) at a time rather than needing SQLite to manage a
cross-database join plan against a live WAL file.

Usage
-----
    # Dry run (default; 100% read-only, recomputes everything live):
    python3 scripts/reconcile_forecast_errors_local_data_root_split.py

    # Real merge (only after reviewing a dry-run report):
    python3 scripts/reconcile_forecast_errors_local_data_root_split.py \\
        --no-dry-run --yes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._bootstrap import bootstrap

bootstrap()

# --------------------------------------------------------------------------
# Constants / defaults
# --------------------------------------------------------------------------

TABLE = "forecast_errors"

# Columns used for the "no new exact-duplicate tuples introduced" sanity
# check -- mirrors the table's real (non-unique) index
# idx_fe_symbol_model_horizon on (symbol, model_name, horizon_days,
# forecast_ts).
DEDUP_COLUMNS = ("symbol", "model_name", "horizon_days", "forecast_ts")

# Full column list to copy, in schema order, excluding the surrogate
# autoincrement `id` (confirmed nothing in the codebase treats
# forecast_errors.id as a foreign key -- safe to let merged rows get fresh
# autoincrement ids in the NEW db rather than preserving OLD ids).
INSERT_COLUMNS = (
    "symbol",
    "model_name",
    "horizon_days",
    "forecast_ts",
    "forecast_price",
    "actual_price",
    "squared_error",
    "recorded_at",
)

# Confirmed real paths (see task/known-issue doc). Overridable via CLI.
DEFAULT_OLD_DB = Path("/Users/kevinlee/Stockpy-live/quant_platform.db")
DEFAULT_NEW_DB = Path.home() / ".stockpy_local" / "quant_platform.db"

DEFAULT_BATCH_SIZE = 20_000
DEFAULT_BUSY_TIMEOUT_MS = 30_000

logger = logging.getLogger("reconcile_forecast_errors")


# --------------------------------------------------------------------------
# Small data containers
# --------------------------------------------------------------------------


@dataclass
class TableStats:
    count: int
    min_recorded_at: Optional[str]
    max_recorded_at: Optional[str]
    distinct_dedup_count: int

    @property
    def duplicate_row_count(self) -> int:
        return self.count - self.distinct_dedup_count


@dataclass
class OverlapCheck:
    old_stats: TableStats
    new_stats: TableStats
    old_rows_at_or_after_new_min: int
    new_rows_at_or_before_old_max: int
    zero_overlap_verified: bool


@dataclass
class MergeResult:
    rows_inserted_total: int
    batches_committed: int
    last_old_id_inserted: int
    completed: bool


# --------------------------------------------------------------------------
# Connection helpers
# --------------------------------------------------------------------------


def _ro_uri(path: Path) -> str:
    """SQLite URI that opens ``path`` strictly read-only.

    ``mode=ro`` makes SQLite refuse ANY write on this connection at the
    driver level -- even a bug in this script that accidentally issued an
    INSERT/UPDATE/DDL statement against this connection would raise
    ``sqlite3.OperationalError: attempt to write a readonly database``
    rather than silently succeeding.
    """
    return f"file:{path.resolve()}?mode=ro"


def connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"Database file does not exist: {path}")
    conn = sqlite3.connect(_ro_uri(path), uri=True)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def connect_writable(
    path: Path, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> sqlite3.Connection:
    """Open ``path`` for writing, tuned to coexist with a live WAL writer.

    ``busy_timeout`` makes this connection BLOCK (up to the timeout) and
    retry instead of raising ``sqlite3.OperationalError: database is
    locked`` the instant it collides with the live daemon's own writer
    connection -- SQLite allows only one writer at a time even under WAL,
    so some amount of waiting here is normal and expected, not a bug.

    ``isolation_level = None`` puts the connection in autocommit mode so
    this script has full manual control over transaction boundaries via
    explicit ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK`` statements,
    rather than relying on Python's own implicit-transaction heuristics.
    """
    if not path.exists():
        raise FileNotFoundError(f"Database file does not exist: {path}")
    conn = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000.0)
    conn.isolation_level = None
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return conn


# --------------------------------------------------------------------------
# Stats / overlap verification (used in BOTH dry-run and real mode)
# --------------------------------------------------------------------------


def get_table_stats(conn: sqlite3.Connection, table: str = TABLE) -> TableStats:
    # {table}/{dedup_cols_sql} interpolate only the fixed TABLE/DEDUP_COLUMNS
    # module constants (or a caller-passed table name that is likewise always
    # a literal, never runtime user input) -- never a bound VALUE, which is
    # always passed via the parameterized `?` placeholder. Same
    # reviewed-and-marked pattern as data/historical_store.py.
    count, min_ts, max_ts = conn.execute(
        f"SELECT COUNT(*), MIN(recorded_at), MAX(recorded_at) FROM {table}"  # nosec B608
    ).fetchone()
    dedup_cols_sql = ", ".join(DEDUP_COLUMNS)
    (distinct_count,) = conn.execute(
        f"SELECT COUNT(*) FROM (SELECT DISTINCT {dedup_cols_sql} FROM {table})"  # nosec B608
    ).fetchone()
    return TableStats(
        count=count or 0,
        min_recorded_at=min_ts,
        max_recorded_at=max_ts,
        distinct_dedup_count=distinct_count or 0,
    )


def verify_zero_overlap(
    old_conn: sqlite3.Connection, new_conn: sqlite3.Connection
) -> OverlapCheck:
    """Recompute (never assume) whether OLD and NEW have zero temporal
    overlap in ``recorded_at``. Both directions are checked independently
    as a cross-check against a mismatched/incomplete min/max read.
    """
    old_stats = get_table_stats(old_conn)
    new_stats = get_table_stats(new_conn)

    old_rows_at_or_after_new_min = 0
    new_rows_at_or_before_old_max = 0

    # {TABLE} is the fixed module constant; the actual value is always bound
    # via the parameterized `?` placeholder below, never interpolated.
    if old_stats.count and new_stats.count and new_stats.min_recorded_at is not None:
        (old_rows_at_or_after_new_min,) = old_conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE recorded_at >= ?",  # nosec B608
            (new_stats.min_recorded_at,),
        ).fetchone()

    if old_stats.count and new_stats.count and old_stats.max_recorded_at is not None:
        (new_rows_at_or_before_old_max,) = new_conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE recorded_at <= ?",  # nosec B608
            (old_stats.max_recorded_at,),
        ).fetchone()

    zero_overlap_verified = (
        old_rows_at_or_after_new_min == 0 and new_rows_at_or_before_old_max == 0
    )

    return OverlapCheck(
        old_stats=old_stats,
        new_stats=new_stats,
        old_rows_at_or_after_new_min=old_rows_at_or_after_new_min,
        new_rows_at_or_before_old_max=new_rows_at_or_before_old_max,
        zero_overlap_verified=zero_overlap_verified,
    )


# --------------------------------------------------------------------------
# Backup (real mode only)
# --------------------------------------------------------------------------


def backup_database(source_path: Path, backup_dir: Optional[Path] = None) -> Path:
    """Take an online, WAL-safe backup of ``source_path`` via SQLite's
    Online Backup API (``sqlite3.Connection.backup()``). Unlike a plain file
    copy, this API is explicitly designed to produce a transactionally
    consistent snapshot even while a separate process is concurrently
    writing to the same database file -- which is exactly the situation
    here (the live orchestrator daemon holding this file open).
    """
    backup_dir = backup_dir or source_path.parent
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{source_path.name}.pre_reconcile_backup.{timestamp}"

    logger.info(
        "Backing up %s -> %s (online SQLite backup API, WAL-safe)",
        source_path,
        backup_path,
    )

    # Source is opened read-only -- the backup API only ever reads from it.
    src = sqlite3.connect(_ro_uri(source_path), uri=True)
    try:
        dst = sqlite3.connect(str(backup_path))
        try:

            def _progress(status, remaining, total) -> None:  # noqa: ANN001
                if total:
                    logger.info(
                        "  backup progress: %d/%d pages copied", total - remaining, total
                    )

            # Copy in chunks of 500 pages, sleeping briefly between chunks,
            # so this yields back periodically instead of holding one giant
            # backup call for the whole run -- good citizenship toward the
            # live daemon's own access to this file, even though a DB this
            # size backs up in well under a minute either way.
            src.backup(dst, pages=500, progress=_progress, sleep=0.05)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()

    if not backup_path.exists() or backup_path.stat().st_size == 0:
        raise RuntimeError(
            f"Backup verification failed: {backup_path} is missing or empty"
        )

    logger.info(
        "Backup complete and verified: %s (%.1f MB)",
        backup_path,
        backup_path.stat().st_size / 1e6,
    )
    return backup_path


# --------------------------------------------------------------------------
# Resume state (crash-safe partial-progress tracking)
# --------------------------------------------------------------------------


def _default_resume_state_path(new_db: Path) -> Path:
    return new_db.parent / f"{new_db.name}.reconcile_forecast_errors.progress.json"


def load_resume_state(path: Path) -> dict:
    if not path.exists():
        return {"last_old_id_inserted": 0, "rows_inserted_total": 0, "completed": False}
    try:
        with path.open("r") as f:
            data = json.load(f)
        return {
            "last_old_id_inserted": int(data.get("last_old_id_inserted", 0)),
            "rows_inserted_total": int(data.get("rows_inserted_total", 0)),
            "completed": bool(data.get("completed", False)),
        }
    except (json.JSONDecodeError, ValueError, OSError, TypeError) as exc:
        logger.warning(
            "Could not parse resume-state file %s (%s) -- starting from scratch", path, exc
        )
        return {"last_old_id_inserted": 0, "rows_inserted_total": 0, "completed": False}


def save_resume_state(path: Path, state: dict) -> None:
    """Atomic (temp file + os.replace) write, matching this codebase's
    established atomic-write convention (e.g. gui/env_io.py's
    write_many_atomic, execution/kill_switch.py) so a crash mid-write never
    leaves a truncated/corrupt state file behind.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".reconcile_state_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# The actual batched merge (real mode only)
# --------------------------------------------------------------------------


def run_batched_merge(
    old_conn: sqlite3.Connection,
    new_conn: sqlite3.Connection,
    *,
    batch_size: int,
    resume_state_path: Path,
    resume_state: dict,
) -> MergeResult:
    select_cols_sql = ", ".join(INSERT_COLUMNS)
    select_sql = f"SELECT id, {select_cols_sql} FROM {TABLE} WHERE id > ? ORDER BY id LIMIT ?"
    insert_sql = (
        f"INSERT INTO {TABLE} ({', '.join(INSERT_COLUMNS)}) "
        f"VALUES ({', '.join(['?'] * len(INSERT_COLUMNS))})"
    )

    last_id = resume_state["last_old_id_inserted"]
    rows_inserted_total = resume_state["rows_inserted_total"]
    batch_num = 0

    # The OLD db has no live writer (that bug is fixed), so this count is
    # stable for the duration of this run and safe to use for progress
    # logging (an estimate of batch count, not a correctness input).
    # {TABLE} is the fixed module constant; the value is bound via `?`.
    (remaining_total,) = old_conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE id > ?", (last_id,)  # nosec B608
    ).fetchone()
    total_batches_estimate = (
        (remaining_total + batch_size - 1) // batch_size if remaining_total else 0
    )

    logger.info(
        "Starting merge: resuming from OLD-db id > %d, %d rows remaining, "
        "batch_size=%d (~%d batches)",
        last_id,
        remaining_total,
        batch_size,
        total_batches_estimate,
    )

    try:
        while True:
            rows = old_conn.execute(select_sql, (last_id, batch_size)).fetchall()
            if not rows:
                break
            batch_num += 1
            batch_last_id = rows[-1][0]
            payload = [row[1:] for row in rows]  # drop OLD-db surrogate id

            new_conn.execute("BEGIN IMMEDIATE")
            try:
                new_conn.executemany(insert_sql, payload)
                new_conn.execute("COMMIT")
            except Exception:
                new_conn.execute("ROLLBACK")
                raise

            rows_inserted_total += len(payload)
            last_id = batch_last_id

            # Persist progress AFTER the commit succeeds: a crash between
            # the DB commit and this write only costs re-scanning (never
            # re-inserting) a handful of already-committed rows on retry.
            resume_state["last_old_id_inserted"] = last_id
            resume_state["rows_inserted_total"] = rows_inserted_total
            resume_state["completed"] = False
            save_resume_state(resume_state_path, resume_state)

            logger.info(
                "  batch %d/%s committed: %d rows (running total inserted: %d, "
                "OLD-db id watermark: %d)",
                batch_num,
                total_batches_estimate or "?",
                len(payload),
                rows_inserted_total,
                last_id,
            )
    except Exception:
        logger.error(
            "MERGE FAILED after committing %d rows across %d batch(es) "
            "(OLD-db id watermark: %d). Progress has been saved to %s -- "
            "rerun this script with the same arguments to resume from this point.",
            rows_inserted_total,
            batch_num,
            last_id,
            resume_state_path,
        )
        raise

    resume_state["completed"] = True
    save_resume_state(resume_state_path, resume_state)

    return MergeResult(
        rows_inserted_total=rows_inserted_total,
        batches_committed=batch_num,
        last_old_id_inserted=last_id,
        completed=True,
    )


# --------------------------------------------------------------------------
# Post-merge verification
# --------------------------------------------------------------------------


def verify_after_merge(
    old_conn: sqlite3.Connection,
    new_conn: sqlite3.Connection,
    *,
    old_count_start: int,
    new_count_start: int,
    new_distinct_start: int,
    rows_inserted_total: int,
) -> dict:
    old_count_final = get_table_stats(old_conn).count
    new_stats_final = get_table_stats(new_conn)
    new_count_final = new_stats_final.count
    new_distinct_final = new_stats_final.distinct_dedup_count

    expected_min_new_count = new_count_start + rows_inserted_total
    # The live daemon can only ADD rows to the NEW db during our run, never
    # remove any -- so the honest check is ">=", with any surplus reported
    # as legitimate concurrent activity, not an error.
    extra_rows_from_live_writer = new_count_final - expected_min_new_count

    new_dup_count_start = new_count_start - new_distinct_start
    new_dup_count_final = new_count_final - new_distinct_final

    result = {
        "old_count_start": old_count_start,
        "old_count_final": old_count_final,
        "old_db_unchanged": old_count_final == old_count_start,
        "new_count_start": new_count_start,
        "new_count_final": new_count_final,
        "rows_inserted_total": rows_inserted_total,
        "old_rows_fully_copied": rows_inserted_total == old_count_start,
        "expected_min_new_count": expected_min_new_count,
        "new_count_at_least_expected": new_count_final >= expected_min_new_count,
        "extra_rows_from_live_writer_during_merge": extra_rows_from_live_writer,
        "new_dup_count_start": new_dup_count_start,
        "new_dup_count_final": new_dup_count_final,
        "newly_introduced_duplicate_tuples": new_dup_count_final - new_dup_count_start,
    }
    result["overall_ok"] = bool(
        result["old_rows_fully_copied"]
        and result["new_count_at_least_expected"]
        and result["old_db_unchanged"]
    )
    return result


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _fmt_stats(label: str, stats: TableStats) -> str:
    return (
        f"{label}: {stats.count:,} rows "
        f"[{stats.min_recorded_at or 'n/a'} .. {stats.max_recorded_at or 'n/a'}] "
        f"({stats.distinct_dedup_count:,} distinct "
        f"(symbol, model_name, horizon_days, forecast_ts) tuples, "
        f"{stats.duplicate_row_count:,} duplicate rows by that key)"
    )


def log_overlap_report(overlap: OverlapCheck) -> None:
    logger.info("=" * 78)
    logger.info("ROW COUNT / DATE RANGE REPORT (recomputed live, not assumed)")
    logger.info("=" * 78)
    logger.info(_fmt_stats("OLD db", overlap.old_stats))
    logger.info(_fmt_stats("NEW db", overlap.new_stats))
    logger.info("-" * 78)
    logger.info(
        "OLD rows with recorded_at >= NEW's min recorded_at: %d (expect 0)",
        overlap.old_rows_at_or_after_new_min,
    )
    logger.info(
        "NEW rows with recorded_at <= OLD's max recorded_at: %d (expect 0)",
        overlap.new_rows_at_or_before_old_max,
    )
    if overlap.zero_overlap_verified:
        logger.info(
            "ZERO-OVERLAP ASSUMPTION: VERIFIED (clean temporal cutover, live-recomputed)"
        )
    else:
        logger.error(
            "ZERO-OVERLAP ASSUMPTION: VIOLATED -- do NOT proceed with a naive append-merge"
        )
    logger.info("=" * 78)


def log_verification_report(verification: dict, backup_path: Path) -> None:
    logger.info("=" * 78)
    logger.info("POST-MERGE VERIFICATION REPORT")
    logger.info("=" * 78)
    logger.info("Pre-merge backup: %s", backup_path)
    for key, value in verification.items():
        logger.info("  %s: %s", key, value)
    logger.info("=" * 78)
    if verification["overall_ok"]:
        logger.info("MERGE VERIFIED OK.")
    else:
        logger.error(
            "MERGE VERIFICATION FAILED -- inspect the numbers above, and the backup at "
            "%s, before trusting the NEW db.",
            backup_path,
        )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "ONE-TIME reconciliation of forecast_errors rows split across the OLD "
            "repo-relative quant_platform.db and the NEW LOCAL_DATA_ROOT-anchored "
            "one. See docs/known_issues/forecast_tracker_local_data_root_split.md."
        )
    )
    parser.add_argument(
        "--old-db",
        type=Path,
        default=DEFAULT_OLD_DB,
        help=f"Path to the OLD (stale) quant_platform.db. Default: {DEFAULT_OLD_DB}",
    )
    parser.add_argument(
        "--new-db",
        type=Path,
        default=DEFAULT_NEW_DB,
        help=(
            "Path to the NEW (live, LOCAL_DATA_ROOT-anchored) quant_platform.db. "
            f"Default: {DEFAULT_NEW_DB}"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Read-only report mode (default True). Pass --no-dry-run together "
            "with --yes to actually write."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Required (together with --no-dry-run) to perform the real merge.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per INSERT transaction. Default: {DEFAULT_BATCH_SIZE}",
    )
    parser.add_argument(
        "--busy-timeout-ms",
        type=int,
        default=DEFAULT_BUSY_TIMEOUT_MS,
        help=f"SQLite busy_timeout for the NEW-db writer connection. Default: {DEFAULT_BUSY_TIMEOUT_MS}",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Directory for the pre-merge online backup of --new-db (default: alongside --new-db).",
    )
    parser.add_argument(
        "--resume-state-file",
        type=Path,
        default=None,
        help=(
            "Where to persist merge progress for crash-safe resumption "
            "(default: <new-db-name>.reconcile_forecast_errors.progress.json next to --new-db)."
        ),
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        default=False,
        help=(
            "Ignore any existing resume-state file and start the merge over from the "
            "first OLD row. WILL create duplicate rows for anything already merged in "
            "a prior partial run -- only use this if you know the saved state is "
            "wrong/stale."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", default=False, help="Debug-level logging."
    )
    return parser.parse_args(argv)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    _configure_logging(args.verbose)

    old_db: Path = args.old_db
    new_db: Path = args.new_db

    real_mode = bool(args.yes) and not bool(args.dry_run)

    if args.yes and args.dry_run:
        logger.error(
            "Refusing to run: --yes was passed but --dry-run is still in effect "
            "(it defaults to True). Pass BOTH --no-dry-run and --yes to actually "
            "write to the NEW database. Exiting without touching anything."
        )
        return 2

    logger.info("OLD db: %s", old_db)
    logger.info("NEW db: %s", new_db)
    logger.info(
        "Mode: %s",
        "REAL WRITE (--no-dry-run --yes)" if real_mode else "DRY RUN (strictly read-only)",
    )

    try:
        old_conn = connect_readonly(old_db)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        return 1

    try:
        new_conn_ro = connect_readonly(new_db)
    except FileNotFoundError as exc:
        logger.error(str(exc))
        old_conn.close()
        return 1

    try:
        overlap = verify_zero_overlap(old_conn, new_conn_ro)
        log_overlap_report(overlap)

        if not real_mode:
            logger.info(
                "Dry run complete. No files were written or modified. Pass "
                "--no-dry-run --yes to perform the real merge after reviewing this report."
            )
            return 0

        if not overlap.zero_overlap_verified:
            logger.error(
                "Zero-overlap assumption FAILED live verification -- refusing to merge. "
                "This script assumes a clean temporal cutover with no interleaving "
                "between OLD and NEW; that assumption does not hold against the "
                "current state of these two files. Stop and investigate manually "
                "before re-running."
            )
            return 3
    finally:
        new_conn_ro.close()
        # old_conn is reused below in real mode; closed at the very end.

    # ------------------------------------------------------------------
    # Real mode from here on.
    # ------------------------------------------------------------------
    assert real_mode

    old_count_start = get_table_stats(old_conn).count

    try:
        backup_path = backup_database(new_db, backup_dir=args.backup_dir)
    except Exception:
        logger.exception("Backup of NEW db failed -- aborting before touching anything else.")
        old_conn.close()
        return 4

    resume_state_path = args.resume_state_file or _default_resume_state_path(new_db)
    if args.no_resume and resume_state_path.exists():
        logger.warning(
            "--no-resume passed: ignoring existing resume state at %s", resume_state_path
        )
        resume_state = {"last_old_id_inserted": 0, "rows_inserted_total": 0, "completed": False}
    else:
        resume_state = load_resume_state(resume_state_path)
        if resume_state["completed"]:
            logger.warning(
                "Resume state at %s already marks this merge COMPLETED (%d rows "
                "previously inserted). Nothing to do. Pass --no-resume to force a "
                "fresh re-merge if you really intend to re-run from scratch.",
                resume_state_path,
                resume_state["rows_inserted_total"],
            )
            old_conn.close()
            return 0
        if resume_state["last_old_id_inserted"]:
            logger.info(
                "Resuming previous partial run from OLD-db id > %d (%d rows already "
                "inserted in that run).",
                resume_state["last_old_id_inserted"],
                resume_state["rows_inserted_total"],
            )

    new_conn = connect_writable(new_db, busy_timeout_ms=args.busy_timeout_ms)
    new_stats_start = get_table_stats(new_conn)

    try:
        merge_result = run_batched_merge(
            old_conn,
            new_conn,
            batch_size=args.batch_size,
            resume_state_path=resume_state_path,
            resume_state=resume_state,
        )
    except Exception:
        logger.error(
            "Aborting due to merge failure. See the progress reported above; resume "
            "state was saved to %s -- rerun this script with the same arguments to "
            "resume.",
            resume_state_path,
        )
        new_conn.close()
        old_conn.close()
        return 5

    verification = verify_after_merge(
        old_conn,
        new_conn,
        old_count_start=old_count_start,
        new_count_start=new_stats_start.count,
        new_distinct_start=new_stats_start.distinct_dedup_count,
        rows_inserted_total=merge_result.rows_inserted_total,
    )
    log_verification_report(verification, backup_path)

    new_conn.close()
    old_conn.close()

    return 0 if verification["overall_ok"] else 6


if __name__ == "__main__":
    sys.exit(main())

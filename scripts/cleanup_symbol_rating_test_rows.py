"""
scripts/cleanup_symbol_rating_test_rows.py
==========================================
Operator-approved cleanup of pytest-fixture rows that leaked into the live
``symbol_rating_events`` table (see
``docs/known_issues/symbol_rating_burst_rows_test_leakage.md``).

Identification rule: a *test cycle* is a ``cycle_id`` whose rows all share ONE
distinct score and that score is 55.0 or 1.0 (the hard-coded fixture values).
Genuine advisory cycles carry 27-32 symbols with many distinct scores.
``cycle_id = 'manual_reinclude'`` rows and NULL cycle_ids are never matched.

DEFAULT IS A DRY RUN (read-only ``mode=ro`` connection, writes nothing). It
prints what would be deleted, per-day counts, the review list, and the
before/after auto-drop exclusion set so you can see whether any symbol's
exclusion state would flip.

``--apply`` (a) writes a consistent backup with ``sqlite3.Connection.backup``
to ``<db dir>/backups/`` first and aborts if that fails or the file exists,
(b) deletes in ONE transaction, bounded by ``--max-id`` (default: max id seen at
start) so rows written concurrently by a live run are never touched, and
(c) rolls back if the deleted count differs from the dry-run prediction.
It never VACUUMs and never touches another table.

SQLite only (the platform's live backend). Exit 0 on success, 1 on error.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

FIXTURE_SCORES = (55.0, 1.0)
FIXTURE_SYMBOLS = {"AAPL", "MSFT", "GOOG", "SPY", "AGNC", "JNJ", "NVDA", "TSLA"}
MANUAL_CYCLE_ID = "manual_reinclude"

_MATCH_CYCLES_SQL = f"""
SELECT cycle_id FROM symbol_rating_events
WHERE id <= :max_id AND cycle_id IS NOT NULL AND cycle_id <> '{MANUAL_CYCLE_ID}'
GROUP BY cycle_id
HAVING COUNT(DISTINCT score) = 1 AND MIN(score) IN ({", ".join(str(s) for s in FIXTURE_SCORES)})
"""


def _sqlite_path_from_url(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise SystemExit(f"Only SQLite is supported, got: {url.split(':', 1)[0]}")
    return Path(url[len("sqlite:///"):])


def resolve_db_path(override: Optional[str]) -> Path:
    if override:
        return Path(override).expanduser()
    from db_config import resolve_database_url

    return _sqlite_path_from_url(resolve_database_url())


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    return sqlite3.connect(str(path), timeout=30)


def exclusions(conn: sqlite3.Connection, *, threshold: int, ignore_cycle_ids: Optional[set] = None) -> Dict[str, int]:
    """Replicates SymbolRatingStore.get_excluded_symbols: symbols whose
    consecutive-BAD streak (newest id first) >= threshold and whose newest
    row is not held. Returns {symbol: streak}."""
    ignore = ignore_cycle_ids or set()
    per: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for cycle_id, symbol, tier, is_held in conn.execute(
        "SELECT cycle_id, symbol, tier, is_held FROM symbol_rating_events ORDER BY id"
    ):
        if cycle_id in ignore:
            continue
        per[symbol].append((tier, int(is_held)))
    out: Dict[str, int] = {}
    for sym, rows in per.items():
        streak = 0
        for tier, _held in reversed(rows):
            if tier != "BAD":
                break
            streak += 1
        if streak >= threshold and not rows[-1][1]:
            out[sym] = streak
    return out


def plan(conn: sqlite3.Connection, max_id: int, threshold: int) -> Dict[str, Any]:
    cycles = [r[0] for r in conn.execute(_MATCH_CYCLES_SQL, {"max_id": max_id})]
    cycle_set = set(cycles)
    total = conn.execute("SELECT COUNT(*) FROM symbol_rating_events").fetchone()[0]
    rows = [
        r for r in conn.execute(
            "SELECT id, substr(timestamp,1,10), cycle_id, symbol, score FROM symbol_rating_events WHERE id <= ?",
            (max_id,),
        ) if r[2] in cycle_set
    ]
    per_day = Counter(r[1] for r in rows)
    sizes = Counter(r[2] for r in rows)
    syms: Dict[str, set] = defaultdict(set)
    for r in rows:
        syms[r[2]].add(r[3])
    review = sorted(c for c in cycle_set if sizes[c] > 8 or not syms[c] <= FIXTURE_SYMBOLS)
    return {
        "cycle_ids": cycle_set,
        "matched_rows": len(rows),
        "matched_cycles": len(cycle_set),
        "total_rows": total,
        "remaining_rows": total - len(rows),
        "per_day": dict(sorted(per_day.items())),
        "review_cycles": len(review),
        "review_rows": sum(sizes[c] for c in review),
        "excl_before": exclusions(conn, threshold=threshold),
        "excl_after": exclusions(conn, threshold=threshold, ignore_cycle_ids=cycle_set),
    }


def print_plan(p: Dict[str, Any], max_id: int, db_path: Path) -> None:
    print(f"DB: {db_path}")
    print(f"max-id bound: {max_id}")
    print(f"total rows now: {p['total_rows']}")
    print(f"matched test cycles: {p['matched_cycles']}  rows: {p['matched_rows']}")
    print(f"rows remaining after delete (genuine + newer than max-id): {p['remaining_rows']}")
    print("per-day matched rows:")
    for day, n in p["per_day"].items():
        print(f"  {day}  {n}")
    print(
        f"review list: {p['review_cycles']} matched cycles / {p['review_rows']} rows are larger than 8 rows or use "
        "non-fixture symbols (watchlist-symbol test bursts; all single-score 55.0/1.0)"
    )
    print(f"auto-drop exclusion BEFORE: {p['excl_before']}")
    print(f"auto-drop exclusion AFTER : {p['excl_after']}")
    flipped = sorted(set(p["excl_before"]) ^ set(p["excl_after"]))
    print(f"symbols whose exclusion state would flip: {flipped or 'none'}")


def apply_cleanup(db_path: Path, max_id: int, threshold: int) -> Dict[str, Any]:
    conn = _connect(db_path, read_only=False)
    try:
        predicted = plan(conn, max_id, threshold)
        if predicted["matched_rows"] == 0:
            return {"deleted": 0, "backup": None, "predicted": predicted}
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"{db_path.stem}_pre_symbol_rating_cleanup_{stamp}.db"
        if backup_path.exists():
            raise RuntimeError(f"backup already exists, refusing: {backup_path}")
        dest = sqlite3.connect(str(backup_path))
        try:
            conn.backup(dest)
        finally:
            dest.close()
        check = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
        try:
            n_backup = check.execute("SELECT COUNT(*) FROM symbol_rating_events").fetchone()[0]
        finally:
            check.close()
        if n_backup < predicted["total_rows"]:
            raise RuntimeError("backup row count is lower than the live table; aborting before any delete")

        conn.isolation_level = None  # manual transaction control
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                f"DELETE FROM symbol_rating_events WHERE id <= :max_id AND cycle_id IN ({_MATCH_CYCLES_SQL})",
                {"max_id": max_id},
            )
            deleted = cur.rowcount
            if deleted != predicted["matched_rows"]:
                raise RuntimeError(f"deleted {deleted} != predicted {predicted['matched_rows']}; rolled back")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return {"deleted": deleted, "backup": str(backup_path), "predicted": predicted}
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--max-id", type=int, default=None, help="only rows with id <= this (default: current max id)")
    ap.add_argument("--db-path", default=None, help="SQLite file (default: resolved via db_config)")
    ap.add_argument("--threshold", type=int, default=5, help="auto-drop streak used for the before/after report")
    args = ap.parse_args(argv)

    db_path = resolve_db_path(args.db_path)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1
    try:
        ro = _connect(db_path, read_only=True)
        try:
            max_id = args.max_id
            if max_id is None:
                max_id = ro.execute("SELECT COALESCE(MAX(id),0) FROM symbol_rating_events").fetchone()[0]
            p = plan(ro, max_id, args.threshold)
        finally:
            ro.close()
        print_plan(p, max_id, db_path)
        if not args.apply:
            print("\nDRY RUN: nothing was written. Re-run with --apply (and an operator go-ahead) to delete.")
            return 0
        res = apply_cleanup(db_path, max_id, args.threshold)
        print(f"\nAPPLIED: deleted {res['deleted']} rows. Backup: {res['backup']}")
        return 0
    except Exception as exc:  # noqa: BLE001 -- CLI boundary
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Venv re-exec + .env loading (placed here, not at module top, so tests can
    # import this module as a library).
    from scripts._bootstrap import bootstrap

    bootstrap()
    raise SystemExit(main())

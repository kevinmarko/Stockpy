"""
scripts/clean_forecast_ledger.py
================================
Forecasting rebuild F1: clean the ``forecast_errors`` ledger. **DRY-RUN BY
DEFAULT** -- without ``--apply`` it opens the database read-only and only
reports what it would delete.

Three categories:

(a) ``symbol = 'TEST'`` rows -- test data that leaked into the real ledger.

(b) The 2026-08-14 bad Monte Carlo seed rows. On that day some cycles
    seeded Monte Carlo at ~$100 regardless of the symbol's real price (root
    cause NOT established here; see the forecasting rebuild plan, F2).
    Identification rule (also printed with the report): a ``monte_carlo``
    row at ANY horizon whose US/Eastern forecast day is 2026-08-14 and whose
    ``forecast_price`` is more than ``3x`` or less than ``1/3`` of that
    cycle's ANCHOR. The anchor is the median ``forecast_price`` of the
    SAME cycle's (same ``symbol`` and ``forecast_ts``) 10-trading-day
    ``arima`` / ``holt_winters`` / ``naive`` rows -- a 10-day statistical
    forecast sits within a few percent of the price at forecast time
    (``naive`` did not exist yet on that day). A cycle with no such anchor
    row is left alone.
    Two simpler rules were tried against the real ledger and rejected:
    comparing each horizon with the median of the other models at that
    horizon flagged a healthy sub-$1 UWMC row (its 90-day ARIMA/HW had
    trended to $0.2), and requiring the row to be out of band against every
    other model missed real CMCL seed rows (its CNN-LSTM sat at $33-50).

(c) Intra-day duplicates: pre-F1, every hourly cycle appended a row, so each
    (symbol, model, horizon, US/Eastern day) key can hold ~20 correlated
    rows. All but the LAST (latest ``forecast_ts``, then highest ``id``) of
    each key are deleted. Computed after (a)/(b) are excluded, so a bad row
    is never the one kept.

The dry run also estimates, for the live skill-weight window
(``FORECAST_SKILL_WINDOW_DAYS``) and threshold (``FORECAST_SKILL_MIN_OBS``),
how many (symbol, model, horizon) keys would fall below the threshold after
the cleanup, and how many (symbol, horizon) pairs would lose their LAST
mature blend-eligible model -- i.e. whose live skill weights would drop back
to the equal-weight cold start.

``--apply`` (the operator runs this, not an agent):

1. Takes a timestamped backup with sqlite3's online ``.backup`` API to
   ``<LOCAL_DATA_ROOT>/backups/`` (``--backup-dir`` to override) and
   verifies its ``forecast_errors`` row count. Refuses to delete anything
   if the backup fails.
2. In ONE transaction: adds the ``forecast_day`` column if missing, deletes
   (a) + (b) + (c), and backfills ``forecast_day`` on the surviving legacy
   rows so future upserts key against them.

Stop the orchestrator daemon first: the delete holds the write lock for a
while and a cycle that cannot get it within its 5 s busy timeout loses that
cycle's forecast rows (logged, never raised). Run ``VACUUM`` afterwards to
reclaim the file space.

Usage::

    python scripts/clean_forecast_ledger.py                # dry run
    python scripts/clean_forecast_ledger.py --json         # dry run, JSON
    python scripts/clean_forecast_ledger.py --apply        # backup + delete
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TEST_SYMBOL = "TEST"
MC_SEED_BUG_DAY = "2026-08-14"
MC_SEED_RATIO = 3.0
MC_MODEL = "monte_carlo"
MC_ANCHOR_HORIZON = 10
MC_ANCHOR_MODELS = ("arima", "holt_winters", "naive")


# ---------------------------------------------------------------------------
# US/Eastern day as a SQL function
# ---------------------------------------------------------------------------

@lru_cache(maxsize=65536)
def _et_day_for_hour(hour_prefix: str, suffix: str) -> Optional[str]:
    from forecasting.forecast_tracker import eastern_trading_day

    return eastern_trading_day(f"{hour_prefix}:00:00{suffix}")


def et_day(ts: Optional[str]) -> Optional[str]:
    """US/Eastern calendar date of an ISO ``forecast_ts`` string.

    The ET date only depends on the UTC hour, so results are cached by the
    ``YYYY-MM-DDTHH`` prefix plus the UTC-offset suffix -- this is called
    once per row over a multi-million-row table. Falls back to a full parse
    for anything that is not a plain ISO string.
    """
    if ts is None:
        return None
    s = str(ts)
    if len(s) >= 13 and s[10] == "T":
        suffix = ""
        if s.endswith("Z"):
            suffix = "+00:00"
        elif len(s) >= 6 and s[-6] in "+-" and s[-3] == ":":
            suffix = s[-6:]
        if suffix == "+00:00" or suffix == "":
            return _et_day_for_hour(s[:13], suffix)
    from forecasting.forecast_tracker import eastern_trading_day

    return eastern_trading_day(s)


def _connect(db_path: str, readonly: bool) -> sqlite3.Connection:
    if readonly:
        from db_config import sqlite_readonly_uri

        conn = sqlite3.connect(sqlite_readonly_uri(db_path), uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.create_function("et_day", 1, et_day, deterministic=True)
    return conn


def _has_column(conn: sqlite3.Connection, column: str) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(forecast_errors)").fetchall()}
    return column in cols


def _day_expr(conn: sqlite3.Connection) -> str:
    if _has_column(conn, "forecast_day"):
        return "COALESCE(forecast_day, et_day(forecast_ts))"
    return "et_day(forecast_ts)"


# ---------------------------------------------------------------------------
# (a) + (b)
# ---------------------------------------------------------------------------

def find_test_rows(conn: sqlite3.Connection) -> List[int]:
    return [r[0] for r in conn.execute(
        "SELECT id FROM forecast_errors WHERE symbol = ?", (TEST_SYMBOL,)
    ).fetchall()]


def find_mc_seed_rows(conn: sqlite3.Connection) -> Tuple[List[int], Dict[str, int]]:
    """Return ``(ids, per_symbol_counts)`` for category (b). See the module
    docstring for the rule."""
    # Pre-filter on a UTC window that contains the whole ET day, then apply
    # the exact ET-day test in Python.
    day = datetime.strptime(MC_SEED_BUG_DAY, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    lo = (day - timedelta(days=1)).isoformat()
    hi = (day + timedelta(days=2)).isoformat()
    rows = conn.execute(
        """SELECT id, symbol, model_name, horizon_days, forecast_ts, forecast_price
           FROM forecast_errors
           WHERE forecast_ts >= ? AND forecast_ts < ?""",
        (lo, hi),
    ).fetchall()

    anchors: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    mc_rows: List[Tuple[int, str, str, float]] = []
    for row_id, sym, model, h, ts, price in rows:
        if et_day(ts) != MC_SEED_BUG_DAY:
            continue
        if model == MC_MODEL:
            mc_rows.append((row_id, sym, ts, price))
        elif h == MC_ANCHOR_HORIZON and model in MC_ANCHOR_MODELS and price is not None and price > 0:
            anchors[(sym, ts)].append(float(price))

    ids: List[int] = []
    per_symbol: Dict[str, int] = defaultdict(int)
    for row_id, sym, ts, price in mc_rows:
        anchor_prices = anchors.get((sym, ts))
        if not anchor_prices or price is None:
            continue
        anchor = statistics.median(anchor_prices)
        ratio = float(price) / anchor
        if ratio > MC_SEED_RATIO or ratio < 1.0 / MC_SEED_RATIO:
            ids.append(row_id)
            per_symbol[sym] += 1
    return sorted(ids), dict(sorted(per_symbol.items()))


# ---------------------------------------------------------------------------
# (c) duplicates, via a temp "survivors" table
# ---------------------------------------------------------------------------

def _build_ranked(conn: sqlite3.Connection, excluded_ids: Sequence[int]) -> None:
    """Create TEMP table ``fe_ranked`` (every row not in ``excluded_ids``)
    with ``rn`` = rank within its (symbol, model, horizon, ET day) key,
    newest first. ``rn = 1`` is the row a dedup keeps."""
    conn.execute("DROP TABLE IF EXISTS temp.fe_excluded")
    conn.execute("CREATE TEMP TABLE fe_excluded (id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO temp.fe_excluded (id) VALUES (?)", [(i,) for i in excluded_ids])
    conn.execute("DROP TABLE IF EXISTS temp.fe_ranked")
    day = _day_expr(conn)
    conn.execute(
        f"""CREATE TEMP TABLE fe_ranked AS
            SELECT id, symbol, model_name, horizon_days, forecast_ts,
                   actual_price IS NOT NULL AS completed,
                   ROW_NUMBER() OVER (
                       PARTITION BY symbol, model_name, horizon_days, {day}
                       ORDER BY forecast_ts DESC, id DESC
                   ) AS rn
            FROM forecast_errors
            WHERE id NOT IN (SELECT id FROM temp.fe_excluded)"""  # nosec B608 -- fixed expression
    )


def cold_start_estimate(
    conn: sqlite3.Connection, window_days: int, min_obs: int
) -> Dict[str, Any]:
    """Needs ``temp.fe_ranked``. Compares completed-row counts inside the
    skill window before (every row) and after (rn = 1 survivors)."""
    from forecasting.forecast_tracker import NON_BLEND_MODEL_NAMES

    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    before = {
        (s, m, h): n for s, m, h, n in conn.execute(
            """SELECT symbol, model_name, horizon_days, COUNT(*) FROM forecast_errors
               WHERE actual_price IS NOT NULL AND forecast_ts >= ?
               GROUP BY 1, 2, 3""",
            (since,),
        ).fetchall()
    }
    after = {
        (s, m, h): n for s, m, h, n in conn.execute(
            """SELECT symbol, model_name, horizon_days, COUNT(*) FROM temp.fe_ranked
               WHERE rn = 1 AND completed AND forecast_ts >= ?
               GROUP BY 1, 2, 3""",
            (since,),
        ).fetchall()
    }

    keys_mature_before = {k for k, n in before.items() if n >= min_obs}
    keys_drop = sorted(k for k in keys_mature_before if after.get(k, 0) < min_obs)

    def mature_real(counts: Dict[Tuple[str, str, int], int]) -> Dict[Tuple[str, int], int]:
        out: Dict[Tuple[str, int], int] = defaultdict(int)
        for (s, m, h), n in counts.items():
            if m not in NON_BLEND_MODEL_NAMES and n >= min_obs:
                out[(s, h)] += 1
        return out

    mb = mature_real(before)
    ma = mature_real(after)
    pairs_to_cold = sorted(k for k in mb if ma.get(k, 0) == 0)

    by_h_keys: Dict[int, int] = defaultdict(int)
    for _s, _m, h in keys_drop:
        by_h_keys[h] += 1
    by_h_pairs: Dict[int, int] = defaultdict(int)
    for _s, h in pairs_to_cold:
        by_h_pairs[h] += 1

    return {
        "window_days": window_days,
        "min_obs": min_obs,
        "keys_mature_before": len(keys_mature_before),
        "keys_falling_below_min_obs": len(keys_drop),
        "keys_falling_below_min_obs_by_horizon": dict(sorted(by_h_keys.items())),
        "symbol_horizon_pairs_with_mature_model_before": len(mb),
        "symbol_horizon_pairs_dropping_to_cold_start": len(pairs_to_cold),
        "symbol_horizon_pairs_dropping_to_cold_start_by_horizon": dict(sorted(by_h_pairs.items())),
        "sample_pairs_dropping_to_cold_start": [f"{s}@{h}" for s, h in pairs_to_cold[:20]],
    }


def analyze(
    conn: sqlite3.Connection, window_days: int, min_obs: int
) -> Tuple[Dict[str, Any], List[int]]:
    total = conn.execute("SELECT COUNT(*) FROM forecast_errors").fetchone()[0]
    test_ids = find_test_rows(conn)
    mc_ids, mc_per_symbol = find_mc_seed_rows(conn)
    excluded = sorted(set(test_ids) | set(mc_ids))
    _build_ranked(conn, excluded)
    dup_count = conn.execute("SELECT COUNT(*) FROM temp.fe_ranked WHERE rn > 1").fetchone()[0]
    kept = conn.execute("SELECT COUNT(*) FROM temp.fe_ranked WHERE rn = 1").fetchone()[0]
    return {
        "total_rows": total,
        "a_test_symbol_rows": len(test_ids),
        "b_mc_seed_rows": len(mc_ids),
        "b_mc_seed_symbols": len(mc_per_symbol),
        "b_mc_seed_rows_by_symbol": mc_per_symbol,
        "b_rule": (
            f"monte_carlo rows (any horizon) with US/Eastern forecast day {MC_SEED_BUG_DAY} whose "
            f"forecast_price is > {MC_SEED_RATIO:g}x or < 1/{MC_SEED_RATIO:g} of the cycle anchor = "
            f"median forecast_price of the same (symbol, forecast_ts) cycle's "
            f"{MC_ANCHOR_HORIZON}-day {'/'.join(MC_ANCHOR_MODELS)} rows"
        ),
        "a_b_overlap": len(set(test_ids) & set(mc_ids)),
        "c_intraday_duplicate_rows": dup_count,
        "rows_after_cleanup": kept,
        "rows_deleted_total": len(excluded) + dup_count,
        "cold_start": cold_start_estimate(conn, window_days, min_obs),
    }, excluded


# ---------------------------------------------------------------------------
# --apply
# ---------------------------------------------------------------------------

def make_backup(db_path: str, backup_dir: Path) -> Path:
    """sqlite3 online ``.backup`` of ``db_path`` into ``backup_dir``, verified
    by row count. Raises on any failure -- the caller must not delete then."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{Path(db_path).name}.backup-before-forecast-ledger-clean-{stamp}"
    if dest.exists():
        raise RuntimeError(f"backup target already exists: {dest}")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    chk_src = sqlite3.connect(db_path)
    chk_dst = sqlite3.connect(str(dest))
    try:
        n_src = chk_src.execute("SELECT COUNT(*) FROM forecast_errors").fetchone()[0]
        n_dst = chk_dst.execute("SELECT COUNT(*) FROM forecast_errors").fetchone()[0]
    finally:
        chk_src.close()
        chk_dst.close()
    if n_dst != n_src:
        raise RuntimeError(f"backup verification failed: {n_dst} rows in backup vs {n_src} live")
    return dest


def apply_cleanup(db_path: str, window_days: int, min_obs: int, backup_dir: Path) -> Dict[str, Any]:
    backup = make_backup(db_path, backup_dir)  # raises -> nothing deleted
    conn = _connect(db_path, readonly=False)
    try:
        conn.isolation_level = None  # explicit transaction control
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not _has_column(conn, "forecast_day"):
                conn.execute("ALTER TABLE forecast_errors ADD COLUMN forecast_day TEXT")
            summary, excluded = analyze(conn, window_days, min_obs)
            conn.execute("DELETE FROM forecast_errors WHERE id IN (SELECT id FROM temp.fe_excluded)")
            conn.execute(
                "DELETE FROM forecast_errors WHERE id IN (SELECT id FROM temp.fe_ranked WHERE rn > 1)"
            )
            conn.execute(
                "UPDATE forecast_errors SET forecast_day = et_day(forecast_ts) WHERE forecast_day IS NULL"
            )
            remaining = conn.execute("SELECT COUNT(*) FROM forecast_errors").fetchone()[0]
            if remaining != summary["rows_after_cleanup"]:
                raise RuntimeError(
                    f"row count mismatch after delete: {remaining} vs expected "
                    f"{summary['rows_after_cleanup']}"
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    summary["applied"] = True
    summary["backup_path"] = str(backup)
    summary["rows_remaining"] = remaining
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_db_path() -> str:
    from forecasting.forecast_tracker import ForecastTracker

    return ForecastTracker(readonly=True)._db_path  # noqa: SLF001 -- same resolution as the live tracker


def _render(summary: Dict[str, Any], db_path: str, applied: bool) -> str:
    cs = summary["cold_start"]
    head = "APPLIED" if applied else "DRY RUN (nothing deleted; pass --apply to delete)"
    lines = [
        f"forecast_errors cleanup -- {head}",
        f"ledger: {db_path}",
        f"total rows:                          {summary['total_rows']:>10,}",
        f"(a) symbol='TEST' rows:              {summary['a_test_symbol_rows']:>10,}",
        f"(b) 2026-08-14 MC seed rows:         {summary['b_mc_seed_rows']:>10,}  "
        f"({summary['b_mc_seed_symbols']} symbols)",
        f"    rule: {summary['b_rule']}",
        f"    by symbol: {summary['b_mc_seed_rows_by_symbol']}",
        f"    (a)/(b) overlap:                 {summary['a_b_overlap']:>10,}",
        f"(c) intra-day duplicate rows:        {summary['c_intraday_duplicate_rows']:>10,}",
        f"rows deleted in total:               {summary['rows_deleted_total']:>10,}",
        f"rows after cleanup:                  {summary['rows_after_cleanup']:>10,}",
        "",
        f"Skill-weight impact (window={cs['window_days']}d, min_obs={cs['min_obs']}):",
        f"  (symbol, model, horizon) keys mature now:            {cs['keys_mature_before']:,}",
        f"  ... falling below min_obs after cleanup:             {cs['keys_falling_below_min_obs']:,}"
        f"  by horizon {cs['keys_falling_below_min_obs_by_horizon']}",
        f"  (symbol, horizon) pairs with a mature blend model:   "
        f"{cs['symbol_horizon_pairs_with_mature_model_before']:,}",
        f"  ... dropping to equal-weight cold start:             "
        f"{cs['symbol_horizon_pairs_dropping_to_cold_start']:,}"
        f"  by horizon {cs['symbol_horizon_pairs_dropping_to_cold_start_by_horizon']}",
        f"  sample: {cs['sample_pairs_dropping_to_cold_start']}",
    ]
    if applied:
        lines += ["", f"backup: {summary['backup_path']}", f"rows remaining: {summary['rows_remaining']:,}",
                  "Run VACUUM (daemon stopped) to reclaim file space."]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Clean the forecast_errors ledger (dry run by default).")
    parser.add_argument("--db", default=None, help="SQLite path (default: the platform ledger).")
    parser.add_argument("--apply", action="store_true",
                        help="Back up, then delete (a)+(b)+(c) in one transaction.")
    parser.add_argument("--backup-dir", default=None,
                        help="Backup directory (default: <LOCAL_DATA_ROOT>/backups).")
    parser.add_argument("--window-days", type=int, default=None,
                        help="Skill window for the cold-start estimate (default: FORECAST_SKILL_WINDOW_DAYS).")
    parser.add_argument("--min-obs", type=int, default=None,
                        help="Maturity threshold (default: FORECAST_SKILL_MIN_OBS).")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    from settings import settings

    db_path = args.db or _default_db_path()
    if not Path(db_path).exists():
        print(f"no database at {db_path}", file=sys.stderr)
        return 2
    window = args.window_days if args.window_days is not None else int(settings.FORECAST_SKILL_WINDOW_DAYS)
    min_obs = args.min_obs if args.min_obs is not None else int(settings.FORECAST_SKILL_MIN_OBS)

    if args.apply:
        backup_dir = Path(args.backup_dir) if args.backup_dir else Path(settings.LOCAL_DATA_ROOT) / "backups"
        summary = apply_cleanup(db_path, window, min_obs, backup_dir)
    else:
        conn = _connect(db_path, readonly=True)
        try:
            summary, _ = analyze(conn, window, min_obs)
        finally:
            conn.close()
        summary["applied"] = False

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(_render(summary, db_path, applied=bool(args.apply)))
    return 0


if __name__ == "__main__":
    from scripts._bootstrap import bootstrap

    bootstrap()
    sys.exit(main())

"""
scripts/cleanup_pit_fundamentals_noise.py
==========================================
Explicit, re-runnable cleanup for the JPM PIT-fundamentals row-count anomaly:
JPM carried 135 ``fundamentals_history`` rows (``source='edgar'``) vs. ~47-54
for every comparable ticker in the same universe (AXP, CAT, IBM, MRK, T, VZ,
...). Root cause (fixed in ``data/edgar_fundamentals.py``'s
``FUNDAMENTALS_NAMESPACES`` + ``scripts/backfill_edgar_fundamentals.py``'s
``get_all_filed_dates``): the backfill script used to scan EVERY XBRL
namespace in a symbol's SEC ``companyfacts`` payload for a "filed" date,
including filer-specific extension namespaces that carry no fundamentals data
at all. JPM's real companyfacts payload has one such namespace (``ffd``, SEC's
Rule 456/457 fee-tagging facts) populated by its near-daily Rule 424(b)(2)
structured-note pricing supplements -- each pricing supplement contributed one
spurious "report date" with zero actual P/E, EPS, ROE, etc. content, ~90 of
them landing in a single 2026-08-14 backfill run.

This script does NOT re-derive those bad rows from anything already stored in
``fundamentals_history`` -- the table only persists the COMPUTED ratios
(``upsert_fundamentals_pit``'s docstring: raw XBRL is deliberately never
persisted), so there is no way to tell "was this report_date's fact from
``us-gaap`` or ``ffd``" after the fact from the DB alone. Instead it RE-FETCHES
each target symbol's live SEC companyfacts payload (the same
``data/edgar_fundamentals.fetch_companyfacts`` the backfill script itself
uses) and recomputes the CORRECT report-date set with the fixed
``get_all_filed_dates`` (now scoped to ``FUNDAMENTALS_NAMESPACES``), then
diffs that against what ``source='edgar'`` rows are actually stored for the
symbol. Every stored report_date NOT in the corrected set is flagged for
deletion; nothing in the corrected set is ever touched.

Safety rules (mirrors scripts/migrate_to_local_data_root.py's convention)
--------------------------------------------------------------------------
* Without ``--apply`` (the default), this script is 100% read-only: it opens
  ``settings.LOCAL_DATA_ROOT``'s live DB in READ-ONLY mode (SQLite URI
  ``mode=ro``) so a dry-run literally cannot write to the live shared
  database, and only issues live HTTPS reads to SEC EDGAR.
* ``--apply`` performs a real, targeted
  ``DELETE FROM fundamentals_history WHERE symbol=? AND source='edgar' AND
  report_date=?`` for each flagged row -- one row at a time, listed up front
  in the dry-run report so an operator can review the exact set before ever
  passing ``--apply``.
* A symbol whose live re-fetch fails (network error, no CIK, no facts) is
  skipped with a warning and the run continues -- it is never treated as "0
  legitimate rows -> delete everything" (that would be exactly the kind of
  failure-relaxes-a-check behavior CONSTRAINT #6 rules out).
* Only ``source='edgar'`` rows are ever considered. Other writers
  (``fmp``, ``yahoo_computed``, the daily ``_upsert_fundamentals`` snapshot,
  test fixtures like ``_fakemarket``/``audit_injection``) are out of scope for
  this script and are left untouched.

Usage
-----
    python3 scripts/cleanup_pit_fundamentals_noise.py                    # dry-run (default) on JPM
    python3 scripts/cleanup_pit_fundamentals_noise.py --tickers JPM,AXP  # dry-run on an explicit list
    python3 scripts/cleanup_pit_fundamentals_noise.py --apply            # perform the real deletes
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from data import edgar_fundamentals  # noqa: E402
from scripts.backfill_edgar_fundamentals import get_all_filed_dates  # noqa: E402
from settings import settings  # noqa: E402

DEFAULT_TICKERS = ["JPM"]
DEFAULT_SINCE = "2015-01-01"


@dataclass
class SymbolCleanupPlan:
    symbol: str
    action: str = ""  # "no_cik" | "no_facts" | "clean" | "dirty" | "error"
    stored_dates: List[str] = field(default_factory=list)
    correct_dates: List[str] = field(default_factory=list)
    stale_dates: List[str] = field(default_factory=list)
    error: Optional[str] = None
    deleted: int = 0


def _read_stored_edgar_dates(db_path: Path, symbol: str) -> List[str]:
    """Read-only: every ``report_date`` currently stored for *symbol* with
    ``source='edgar'``. Opened via SQLite's ``mode=ro`` URI so this function
    cannot write to the live shared DB even by accident."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT report_date FROM fundamentals_history "
            "WHERE symbol = ? AND source = 'edgar' AND report_date IS NOT NULL "
            "ORDER BY report_date",
            (symbol.upper(),),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def build_plan(symbol: str, db_path: Path, since: str) -> SymbolCleanupPlan:
    plan = SymbolCleanupPlan(symbol=symbol.upper())

    try:
        plan.stored_dates = _read_stored_edgar_dates(db_path, plan.symbol)
    except Exception as exc:
        plan.action = "error"
        plan.error = f"DB read failed: {exc}"
        return plan

    cik = edgar_fundamentals.get_cik(plan.symbol)
    if not cik:
        plan.action = "no_cik"
        return plan

    facts = edgar_fundamentals.fetch_companyfacts(cik)
    if not facts:
        plan.action = "no_facts"
        return plan

    plan.correct_dates = get_all_filed_dates(facts, since)
    correct_set = set(plan.correct_dates)
    plan.stale_dates = sorted(d for d in plan.stored_dates if d not in correct_set)
    plan.action = "dirty" if plan.stale_dates else "clean"
    return plan


def apply_plan(plan: SymbolCleanupPlan, db_path: Path) -> None:
    """Delete exactly the flagged stale rows for *plan.symbol*. Opens the DB
    read-write ONLY here, and ONLY reached when the caller passed --apply."""
    if not plan.stale_dates:
        return
    conn = sqlite3.connect(str(db_path))
    try:
        for report_date in plan.stale_dates:
            conn.execute(
                "DELETE FROM fundamentals_history "
                "WHERE symbol = ? AND source = 'edgar' AND report_date = ?",
                (plan.symbol, report_date),
            )
            plan.deleted += 1
        conn.commit()
    finally:
        conn.close()


def print_report(plans: List[SymbolCleanupPlan], *, applied: bool) -> None:
    print("Result:" if applied else "Plan (dry-run — nothing touched, DB opened read-only):")
    total_stale = 0
    for plan in plans:
        print(f"  [{plan.symbol}]")
        if plan.action == "error":
            print(f"      ERROR: {plan.error}")
            continue
        if plan.action == "no_cik":
            print("      SKIP — could not resolve CIK from SEC's ticker table.")
            continue
        if plan.action == "no_facts":
            print("      SKIP — SEC EDGAR companyfacts fetch returned nothing.")
            continue

        print(
            f"      stored source='edgar' rows: {len(plan.stored_dates)}   "
            f"corrected (dei+us-gaap only): {len(plan.correct_dates)}"
        )
        if plan.action == "clean":
            print("      CLEAN — every stored report_date is legitimate. Nothing to do.")
            continue

        total_stale += len(plan.stale_dates)
        verb = "deleted" if applied else "would delete"
        print(f"      {verb} {len(plan.stale_dates)} stale row(s):")
        for d in plan.stale_dates:
            print(f"        - {d}")

    print()
    print(f"Total stale rows {'deleted' if applied else 'flagged'}: {total_stale}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove PIT-fundamentals rows whose report_date came from a "
            "non-fundamentals XBRL namespace (the JPM ffd/424B2 pricing-"
            "supplement noise bug), by re-fetching each symbol's live SEC "
            "companyfacts and diffing against what's actually stored."
        ),
    )
    parser.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TICKERS),
        help=f"Comma-separated tickers to check (default: {','.join(DEFAULT_TICKERS)}).",
    )
    parser.add_argument(
        "--since", default=DEFAULT_SINCE,
        help=f"YYYY-MM-DD cutoff, matching the backfill script's own default ({DEFAULT_SINCE}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the real deletes. Without this flag the script is a dry-run: it "
        "opens the DB read-only and only reports what it would delete.",
    )
    args = parser.parse_args(argv)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        print("No tickers given.")
        return 1

    db_path = settings.LOCAL_DATA_ROOT / "quant_platform.db"
    print(f"DB path: {db_path}")
    print(f"Mode:    {'APPLY (deleting stale rows)' if args.apply else 'DRY-RUN (default, read-only DB open)'}")
    print(f"Tickers: {', '.join(tickers)}")
    print()

    plans = [build_plan(t, db_path, args.since) for t in tickers]

    if args.apply:
        for plan in plans:
            if plan.action == "dirty":
                apply_plan(plan, db_path)

    print_report(plans, applied=args.apply)

    any_errors = any(p.action == "error" for p in plans)
    return 1 if any_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

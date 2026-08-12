"""
scripts/migrate_to_local_data_root.py
======================================
One-time, explicit, re-runnable migration of the ~1.7GB of locally-generated
model/data artifacts (the SQLite trading DB, ``output/`` state, logs, trained
ML models, and various API/Robinhood caches) that used to live at fixed
repo-relative paths into ``settings.LOCAL_DATA_ROOT`` (default
``~/.stockpy_local``) — a single, machine-global folder shared by every git
worktree/checkout on this machine, entirely outside of git.

Why this is an explicit opt-in script, not automatic migration on process start
---------------------------------------------------------------------------------
The operator's live 624MB trading database (``quant_platform.db``) is squarely
in scope for this move. A bug in an *automatic* on-boot mover — triggered
implicitly the first time any process imports ``settings`` — would be far
harder to reason about, review, or recover from than one small, reviewable,
idempotent, re-runnable script invocation the operator runs deliberately and
can inspect with ``--dry-run`` (the default) before ever touching a real file.
Nothing in this codebase's normal import/startup path calls this module.

What it covers (11 source → destination pairs, relative to the repo root this
script is physically checked out under → relative to ``settings.LOCAL_DATA_ROOT``)
------------------------------------------------------------------------------------
  * ``output/``                                  → ``{root}/output/``
  * ``quant_platform.db`` (+ ``-wal`` / ``-shm``)  → ``{root}/quant_platform.db`` (+...)
  * ``logs/``                                     → ``{root}/logs/``
  * ``ml/models/*.pkl`` (files only, not the ``.py``
    modules / ``.gitkeep`` / ``__init__.py`` that live
    alongside them)                                → ``{root}/ml_models/``
  * ``ml/models/forecast_cache/``                  → ``{root}/ml_models/forecast_cache/``
  * ``ml/data/cache/``                             → ``{root}/ml_feature_cache/``
  * ``data/universe_cache.parquet``                → ``{root}/universe_cache.parquet``
  * ``cache/cache.db``                             → ``{root}/api_cache/cache.db``
  * ``cache/account_snapshot.json``                → ``{root}/robinhood_cache/account_snapshot.json``
  * ``cache/robinhood_orders.json``                → ``{root}/robinhood_cache/robinhood_orders.json``
  * ``cache/sync_report.json``                     → ``{root}/robinhood_cache/sync_report.json``

This script deliberately never touches ``.env``, ``credentials.json``,
``~/.tokens/``, ``ml/registry.yaml``, or ``tests/fixtures/`` — none of those
are even listed as candidate source paths above.

Safety rules
------------
* A source that doesn't exist is skipped with a log line and the run
  continues — normal for a fresh worktree that never generated that artifact.
* A destination that already has content (a file with size > 0, or a
  directory containing at least one file) is **never** silently overwritten —
  it's skipped with a "resolve manually" warning so an operator decision is
  required, not a script default.
* ``--apply`` performs a genuine ``shutil.move`` (not a copy) so an
  interrupted run is safely resumable: already-moved sources are simply gone
  on the next pass, and any file the previous pass didn't reach is retried.
* Without ``--apply`` (the default), this script is 100% read-only: it only
  ``stat()``s/walks paths to report what it *would* do.

Usage
-----
    python3 scripts/migrate_to_local_data_root.py                  # dry-run (default), touches nothing
    python3 scripts/migrate_to_local_data_root.py --apply           # perform the real move
    python3 scripts/migrate_to_local_data_root.py --verify          # dry-run + post-migration sanity report
    python3 scripts/migrate_to_local_data_root.py --apply --verify  # migrate, then verify, in one invocation

Run this from the checkout that actually holds the real data — typically the
**main checkout**, not a throwaway agent/PR-review worktree — since the
source root is resolved as ``Path(__file__).resolve().parent.parent``, i.e.
wherever this specific file is physically checked out, not some fixed
absolute path.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from settings import settings  # noqa: E402

# Categories used for the summary table, in display order.
CATEGORY_ORDER = ("DB", "output", "logs", "ml models", "caches")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MigrationItem:
    """One source → destination artifact this script knows how to relocate.

    ``pair_id`` groups items belonging to the same conceptual "source/dest
    pair" from the module docstring's list of 11 (a directory move is always
    exactly one item per pair; the ``ml/models/*.pkl`` glob can be zero, one,
    or many items sharing the same ``pair_id``).
    """

    category: str
    pair_id: str
    label: str
    source: Path
    dest: Path
    kind: str  # "file" | "dir" | "glob_placeholder" (never has real content)

    action: str = ""  # computed by compute_action(): "skip_no_source" | "skip_dest_exists" | "move"
    size_bytes: int = 0
    file_count: int = 0
    moved: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _safe_getsize(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_size(path: Path) -> int:
    """Recursive byte total of every file under *path* (du-style sum)."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            total += _safe_getsize(Path(root) / name)
    return total


def _dir_file_count(path: Path) -> int:
    count = 0
    for _root, _dirs, files in os.walk(path):
        count += len(files)
    return count


def _has_content(path: Path) -> bool:
    """True if *path* is a non-empty file, or a directory containing >= 1 file."""
    if path.is_file():
        return _safe_getsize(path) > 0
    if path.is_dir():
        for _root, _dirs, files in os.walk(path):
            if files:
                return True
        return False
    return False


def _human(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024.0
    return f"{size:.1f}PB"


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------


def build_items(repo_root: Path, local_root: Path) -> List[MigrationItem]:
    """Enumerate every candidate source/dest pair against *repo_root* / *local_root*.

    Pure and read-only (only existence/globbing checks) — safe to call in
    both dry-run and --apply modes, and repeatedly.
    """
    items: List[MigrationItem] = []

    # 1. output/ -> {root}/output/
    items.append(
        MigrationItem(
            category="output",
            pair_id="output",
            label="output/ directory",
            source=repo_root / "output",
            dest=local_root / "output",
            kind="dir",
        )
    )

    # 2. quant_platform.db (+ -wal / -shm) -> {root}/quant_platform.db(+...)
    for suffix in ("", "-wal", "-shm"):
        name = f"quant_platform.db{suffix}"
        items.append(
            MigrationItem(
                category="DB",
                pair_id="quant_platform_db",
                label=name,
                source=repo_root / name,
                dest=local_root / name,
                kind="file",
            )
        )

    # 3. logs/ -> {root}/logs/
    items.append(
        MigrationItem(
            category="logs",
            pair_id="logs",
            label="logs/ directory",
            source=repo_root / "logs",
            dest=local_root / "logs",
            kind="dir",
        )
    )

    # 4. ml/models/*.pkl (individually, flat) -> {root}/ml_models/<name>.pkl
    ml_models_dir = repo_root / "ml" / "models"
    ml_models_dest_dir = local_root / "ml_models"
    pkl_files = sorted(ml_models_dir.glob("*.pkl")) if ml_models_dir.is_dir() else []
    if pkl_files:
        for pkl_path in pkl_files:
            items.append(
                MigrationItem(
                    category="ml models",
                    pair_id="ml_models_pkl",
                    label=f"ml/models/{pkl_path.name}",
                    source=pkl_path,
                    dest=ml_models_dest_dir / pkl_path.name,
                    kind="file",
                )
            )
    else:
        # No .pkl files currently sitting directly in ml/models/ (already
        # migrated, or none were ever trained in this checkout). Register a
        # placeholder anyway so --verify always has something to report for
        # this pair; its source is a sentinel path that can never exist, so
        # it is always an inert "skip (no source)".
        items.append(
            MigrationItem(
                category="ml models",
                pair_id="ml_models_pkl",
                label="ml/models/*.pkl (none found)",
                source=ml_models_dir / "__no_pkl_files_found__",
                dest=ml_models_dest_dir,
                kind="glob_placeholder",
            )
        )

    # 5. ml/models/forecast_cache/ -> {root}/ml_models/forecast_cache/
    items.append(
        MigrationItem(
            category="ml models",
            pair_id="ml_forecast_cache",
            label="ml/models/forecast_cache/ directory",
            source=repo_root / "ml" / "models" / "forecast_cache",
            dest=ml_models_dest_dir / "forecast_cache",
            kind="dir",
        )
    )

    # 6. ml/data/cache/ -> {root}/ml_feature_cache/
    items.append(
        MigrationItem(
            category="caches",
            pair_id="ml_feature_cache",
            label="ml/data/cache/ directory",
            source=repo_root / "ml" / "data" / "cache",
            dest=local_root / "ml_feature_cache",
            kind="dir",
        )
    )

    # 7. data/universe_cache.parquet -> {root}/universe_cache.parquet
    items.append(
        MigrationItem(
            category="caches",
            pair_id="universe_cache",
            label="data/universe_cache.parquet",
            source=repo_root / "data" / "universe_cache.parquet",
            dest=local_root / "universe_cache.parquet",
            kind="file",
        )
    )

    # 8. cache/cache.db -> {root}/api_cache/cache.db
    items.append(
        MigrationItem(
            category="caches",
            pair_id="api_cache_db",
            label="cache/cache.db",
            source=repo_root / "cache" / "cache.db",
            dest=local_root / "api_cache" / "cache.db",
            kind="file",
        )
    )

    # 9-11. cache/{account_snapshot,robinhood_orders,sync_report}.json -> {root}/robinhood_cache/...
    for fname, pair_id in (
        ("account_snapshot.json", "rh_account_snapshot"),
        ("robinhood_orders.json", "rh_orders"),
        ("sync_report.json", "rh_sync_report"),
    ):
        items.append(
            MigrationItem(
                category="caches",
                pair_id=pair_id,
                label=f"cache/{fname}",
                source=repo_root / "cache" / fname,
                dest=local_root / "robinhood_cache" / fname,
                kind="file",
            )
        )

    return items


def compute_action(item: MigrationItem) -> None:
    """Fill in ``item.action``/``.size_bytes``/``.file_count`` from current fs state."""
    if not item.source.exists():
        item.action = "skip_no_source"
        item.size_bytes = 0
        item.file_count = 0
        return

    if item.kind == "dir":
        item.size_bytes = _dir_size(item.source)
        item.file_count = _dir_file_count(item.source)
    else:
        item.size_bytes = _safe_getsize(item.source)
        item.file_count = 1

    if item.dest.exists() and _has_content(item.dest):
        item.action = "skip_dest_exists"
        return

    item.action = "move"


# ---------------------------------------------------------------------------
# Applying the plan
# ---------------------------------------------------------------------------


def _move_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))


def _move_dir(source: Path, dest: Path) -> None:
    """Move *source* onto *dest*, merging into an already-existing (empty)
    *dest* rather than nesting *source* inside it.

    ``shutil.move(src, dst)`` moves *src* INSIDE *dst* when *dst* already
    exists as a directory (stdlib documented behavior) — which would produce
    ``dst/src_name/...`` instead of ``dst`` itself becoming the content. Every
    dest directory here (``{LOCAL_DATA_ROOT}/output``, etc.) is routinely
    pre-created empty by ``settings.py``'s own auto-mkdir the first time
    anything imports ``settings``, so this branch is the common case, not an
    edge case.
    """
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))
        return

    dest.mkdir(parents=True, exist_ok=True)
    for child in list(source.iterdir()):
        shutil.move(str(child), str(dest / child.name))
    try:
        source.rmdir()
    except OSError:
        # Not empty for some reason (e.g. a hidden file we didn't enumerate,
        # or a concurrent writer) -- leave it rather than raising; the
        # leftover is exactly what --verify is for.
        pass


def apply_items(items: List[MigrationItem]) -> None:
    """Perform the actual moves for every item whose computed action is "move".

    Mutates each item's ``.moved``/``.error`` in place. Never raises — a
    per-item failure is recorded on the item and the run continues with the
    rest (dead-letter style, matching this codebase's convention elsewhere).
    """
    for item in items:
        if item.action != "move":
            continue
        try:
            if item.kind == "dir":
                _move_dir(item.source, item.dest)
            else:
                _move_file(item.source, item.dest)
            item.moved = True
        except OSError as exc:
            item.error = str(exc)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_ACTION_LABELS = {
    "skip_no_source": "skip (no source)",
    "skip_dest_exists": "SKIP — destination already has content, resolve manually",
}


def _status_text(item: MigrationItem, *, applied: bool) -> str:
    if item.error:
        return f"ERROR: {item.error}"
    if item.action in _ACTION_LABELS:
        return _ACTION_LABELS[item.action]
    verb = "moved" if applied else "would move"
    return f"{verb} ({item.file_count} file(s), {_human(item.size_bytes)})"


def print_report(items: List[MigrationItem], *, applied: bool) -> None:
    print("Result:" if applied else "Plan (dry-run — nothing touched):")
    totals: dict = {}
    for item in items:
        print(f"  [{item.category:9s}] {item.label:45s} {item.source} -> {item.dest}")
        print(f"      {_status_text(item, applied=applied)}")
        if item.action == "move" and not item.error:
            bucket = totals.setdefault(item.category, [0, 0])
            bucket[0] += item.file_count
            bucket[1] += item.size_bytes

    print()
    print(("Summary (moved)" if applied else "Summary (would move)") + ":")
    grand_files = 0
    grand_bytes = 0
    for category in CATEGORY_ORDER:
        count, size = totals.get(category, [0, 0])
        grand_files += count
        grand_bytes += size
        print(f"  {category:10s}: {count:4d} file(s), {_human(size):>10s}")
    print(f"  {'TOTAL':10s}: {grand_files:4d} file(s), {_human(grand_bytes):>10s}")


def print_verification(items: List[MigrationItem], *, applied: bool) -> bool:
    """Print the post-migration sanity report for each of the 11 pairs.

    Returns True if any pair triggered a warning (used for the exit code).
    """
    print("Post-migration verification (11 source/dest pairs):")

    pairs: "OrderedDict[str, List[MigrationItem]]" = OrderedDict()
    for item in items:
        pairs.setdefault(item.pair_id, []).append(item)

    any_warning = False
    for pair_id, group in pairs.items():
        label = group[0].label if len(group) == 1 else f"{len(group)} files under {pair_id}"
        source_absent = all(not g.source.exists() for g in group)

        if group[0].kind == "dir":
            dest_exists = group[0].dest.exists()
        else:
            dest_exists = any(g.dest.exists() for g in group)

        bits = [
            "source ABSENT" if source_absent else "source STILL PRESENT",
            "dest EXISTS" if dest_exists else "dest missing",
        ]

        warning_line = None
        if not source_absent:
            # Anything still present that this run computed as "move" (i.e.
            # NOT a legitimate destination-already-has-content skip) either
            # failed to move (if we actually applied this run) or simply
            # hasn't been migrated yet (if this was a dry-run/--verify-only
            # invocation, which is expected and not itself a problem).
            unmoved = [g for g in group if g.source.exists() and g.action == "move"]
            if unmoved:
                if applied:
                    warning_line = (
                        "WARNING — expected to move but source still present "
                        "(the move may have failed; see per-item ERROR above)"
                    )
                    any_warning = True
                else:
                    bits.append("not yet applied (re-run with --apply)")

        print(f"  [{pair_id}] {label}: " + ", ".join(bits))
        if warning_line:
            print(f"      {warning_line}")

    print()
    print("Verification FOUND WARNINGS — see above." if any_warning else "Verification OK.")
    return any_warning


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate locally-generated model/data artifacts (DB, output/, logs, "
            "ML models, caches) from repo-relative paths into settings.LOCAL_DATA_ROOT."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the real move. Without this flag the script is a dry-run: it "
        "reports what it would do and touches nothing.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Print a post-migration sanity report for each of the 11 source/dest "
        "pairs. Can be combined with --apply (verify what was just moved) or run "
        "alone/standalone (e.g. after a previous --apply run).",
    )
    args = parser.parse_args(argv)

    repo_root = _REPO_ROOT
    local_root = settings.LOCAL_DATA_ROOT

    print(f"Repo root:       {repo_root}")
    print(f"LOCAL_DATA_ROOT: {local_root}")
    print(f"Mode:            {'APPLY (moving files)' if args.apply else 'DRY-RUN (default, no files touched)'}")
    print()

    items = build_items(repo_root, local_root)
    for item in items:
        compute_action(item)

    if args.apply:
        apply_items(items)

    print_report(items, applied=args.apply)

    warnings_found = False
    if args.verify:
        print()
        warnings_found = print_verification(items, applied=args.apply)

    return 1 if warnings_found else 0


if __name__ == "__main__":
    raise SystemExit(main())

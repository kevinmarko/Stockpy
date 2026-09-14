#!/usr/bin/env python3
"""Split the test suite into balanced shards for CI.

Why this exists
---------------
GitHub's ``ubuntu-latest`` runner has 2 vCPUs, so ``pytest -n auto`` gets two
workers and nothing more. Measured, the offline suite is very close to purely
CPU-bound: it runs in ~3 minutes on a 10-core machine and ~18 minutes on the
2-core runner. That ~6x gap is larger than every in-process tuning lever
combined (coverage entirely is worth ~2x; ``COVERAGE_CORE=sysmon`` ~5%), so the
only way to make CI meaningfully faster is to give it more cores -- i.e. run
several shards as separate parallel jobs.

Balance matters, measured rather than assumed
---------------------------------------------
Splitting 577 test files four ways:

    round-robin by filename : min 214s  max 334s  skew 1.56x
    greedy bin-pack         : min 252s  max 252s  skew 1.00x

A shard is only as fast as its slowest member, so a 1.56x skew throws away a
third of the benefit. The suite is genuinely lopsided -- ``test_train_lgbm.py``
alone is 98.9s against a 1009s total -- so the split is driven by measured
per-file durations (``.test_durations.json``), longest-first into the
currently-lightest shard.

The durations file is a hint, never a source of truth
-----------------------------------------------------
A file missing from it (a newly added test) is still assigned -- deterministically
and round-robin, after the weighted files -- so a stale durations map can only
make the split less balanced, never drop a test. ``tests/test_ci_shard.py``
pins that: for any N, every collected test file lands in exactly one shard.

Regenerate the durations map with::

    pytest -m "not network and not slow" --durations=0 --durations-min=0 ...

and re-derive per-file totals; it does not need to be exact or fresh to be
useful, only roughly right.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DURATIONS_FILE = REPO_ROOT / ".test_durations.json"
TESTS_DIR = REPO_ROOT / "tests"


def load_durations(path: Path | None = None) -> dict[str, float]:
    """Per-file measured durations. Missing/corrupt file degrades to ``{}``.

    Never raises: an unreadable hint file must not be able to break CI, since
    the shard assignment stays correct (just less balanced) without it.
    """
    path = path or DURATIONS_FILE
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, (int, float)) and value >= 0:
            out[key] = float(value)
    return out


def discover_test_files(tests_dir: Path | None = None) -> list[str]:
    """Every ``tests/test_*.py``, repo-relative, sorted for determinism."""
    tests_dir = tests_dir or TESTS_DIR
    return sorted(
        f"tests/{p.name}" for p in tests_dir.glob("test_*.py") if p.is_file()
    )


def assign_shards(
    files: list[str], num_shards: int, durations: dict[str, float] | None = None
) -> list[list[str]]:
    """Partition ``files`` into ``num_shards`` balanced groups.

    Weighted files are placed longest-first into whichever shard is currently
    lightest; unweighted files are then dealt round-robin starting from the
    lightest shard. Deterministic for a given (files, num_shards, durations).
    """
    if num_shards < 1:
        raise ValueError(f"num_shards must be >= 1, got {num_shards}")
    durations = durations or {}

    shards: list[list[str]] = [[] for _ in range(num_shards)]
    load = [0.0] * num_shards

    weighted = sorted(
        (f for f in files if f in durations),
        key=lambda f: (-durations[f], f),
    )
    unweighted = sorted(f for f in files if f not in durations)

    for f in weighted:
        i = load.index(min(load))
        shards[i].append(f)
        load[i] += durations[f]

    # Deal the unknowns round-robin from the lightest shard outward, so a
    # stale durations map degrades gracefully instead of piling every new
    # test onto shard 0.
    order = sorted(range(num_shards), key=lambda i: (load[i], i))
    for n, f in enumerate(unweighted):
        shards[order[n % num_shards]].append(f)

    return [sorted(s) for s in shards]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shard-id", type=int, required=True, help="1-based shard index")
    ap.add_argument("--num-shards", type=int, required=True)
    args = ap.parse_args()

    if not 1 <= args.shard_id <= args.num_shards:
        raise SystemExit(
            f"--shard-id must be in 1..{args.num_shards}, got {args.shard_id}"
        )

    shards = assign_shards(
        discover_test_files(), args.num_shards, load_durations()
    )
    print(" ".join(shards[args.shard_id - 1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

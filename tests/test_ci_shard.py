"""
tests/test_ci_shard.py
======================
Guards for ``scripts/ci_shard.py``, which splits the suite across parallel CI
jobs.

The failure this file exists to prevent is silent: if the split ever drops a
file, CI stays green while those tests simply never run, and nobody finds out
from the CI output. Coverage would sag slightly and that is all. So the
partition property -- every collected test file lands in exactly one shard --
is asserted directly, for several shard counts, against the REAL repo layout
rather than a fixture.
"""

from __future__ import annotations

import json

import pytest

from scripts.ci_shard import (
    assign_shards,
    discover_test_files,
    load_durations,
)


class TestPartitionIsExact:
    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 13])
    def test_every_file_lands_in_exactly_one_shard(self, n):
        files = discover_test_files()
        assert files, "no test files discovered -- the glob is wrong"

        shards = assign_shards(files, n, load_durations())

        assert len(shards) == n
        flat = [f for s in shards for f in s]
        assert sorted(flat) == sorted(files), "a file was dropped or duplicated"
        assert len(flat) == len(set(flat)), "a file appears in more than one shard"

    def test_this_very_file_is_included(self):
        """A self-check: if the glob regressed, this test would stop running."""
        files = discover_test_files()
        assert "tests/test_ci_shard.py" in files

    @pytest.mark.parametrize("n", [2, 4, 7])
    def test_assignment_is_deterministic(self, n):
        files = discover_test_files()
        durations = load_durations()
        assert assign_shards(files, n, durations) == assign_shards(files, n, durations)

    def test_rejects_a_nonsense_shard_count(self):
        with pytest.raises(ValueError):
            assign_shards(["tests/test_a.py"], 0)


class TestDegradesWithoutDurations:
    """The durations map is a hint. Losing it may cost balance, never a test."""

    @pytest.mark.parametrize("n", [1, 4, 9])
    def test_partition_still_exact_with_no_durations_at_all(self, n):
        files = discover_test_files()
        shards = assign_shards(files, n, {})
        flat = [f for s in shards for f in s]
        assert sorted(flat) == sorted(files)
        assert len(flat) == len(set(flat))

    def test_files_missing_from_the_map_are_still_assigned(self):
        files = [f"tests/test_{i}.py" for i in range(10)]
        durations = {"tests/test_0.py": 5.0}  # 9 of 10 unknown
        shards = assign_shards(files, 3, durations)
        flat = [f for s in shards for f in s]
        assert sorted(flat) == sorted(files)

    def test_unknown_files_are_spread_not_piled_onto_one_shard(self):
        files = [f"tests/test_{i:03d}.py" for i in range(30)]
        shards = assign_shards(files, 3, {})
        sizes = sorted(len(s) for s in shards)
        assert sizes[-1] - sizes[0] <= 1, f"uneven deal of unknown files: {sizes}"

    def test_corrupt_durations_file_is_ignored_not_fatal(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ this is not json", encoding="utf-8")
        assert load_durations(bad) == {}

        missing = tmp_path / "nope.json"
        assert load_durations(missing) == {}

        wrong_shape = tmp_path / "list.json"
        wrong_shape.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_durations(wrong_shape) == {}

    def test_non_numeric_entries_are_dropped_rather_than_crashing(self, tmp_path):
        p = tmp_path / "d.json"
        p.write_text(
            json.dumps({"tests/test_a.py": 1.5, "tests/test_b.py": "slow", "x": None}),
            encoding="utf-8",
        )
        assert load_durations(p) == {"tests/test_a.py": 1.5}


class TestBalance:
    def test_the_split_is_actually_balanced_on_real_durations(self):
        """Balance is the whole point -- a shard is as slow as its heaviest.

        Guarded loosely (1.5x) rather than at the measured 1.00x: this must
        fail on a genuinely broken split, not on ordinary drift as tests are
        added and the durations map ages.
        """
        files = discover_test_files()
        durations = load_durations()
        if not durations:
            pytest.skip(".test_durations.json is absent; balance is unmeasurable")

        shards = assign_shards(files, 4, durations)
        loads = [sum(durations.get(f, 0.0) for f in s) for s in shards]
        assert min(loads) > 0
        assert max(loads) / min(loads) < 1.5, f"shard loads are lopsided: {loads}"

    def test_a_single_dominant_file_cannot_be_split_apart(self):
        """Sanity: one huge file bounds the best achievable shard time."""
        files = ["tests/test_huge.py"] + [f"tests/test_{i}.py" for i in range(9)]
        durations = {"tests/test_huge.py": 100.0}
        durations.update({f"tests/test_{i}.py": 1.0 for i in range(9)})
        shards = assign_shards(files, 4, durations)
        holding = [s for s in shards if "tests/test_huge.py" in s]
        assert len(holding) == 1

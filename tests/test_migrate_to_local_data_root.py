"""
tests/test_migrate_to_local_data_root.py
=========================================
Tests for ``scripts/migrate_to_local_data_root.py`` -- the explicit, opt-in
migration script that relocates locally-generated artifacts (the SQLite DB,
``output/``, ``logs/``, ML models, and various caches) into
``settings.LOCAL_DATA_ROOT``.

Every test operates against ``tmp_path`` fixtures standing in for both the
repo root and ``settings.LOCAL_DATA_ROOT`` -- never the real repo checkout or
the operator's real ``~/.stockpy_local``. Where the CLI entry point
(``main()``) is exercised, the module's ``_REPO_ROOT`` and the shared
``settings.LOCAL_DATA_ROOT`` singleton are monkeypatched to point at
``tmp_path`` subdirectories, and restored automatically by pytest afterward.
"""

from __future__ import annotations

import scripts.migrate_to_local_data_root as migrate


# ---------------------------------------------------------------------------
# build_items / compute_action -- pure planning logic
# ---------------------------------------------------------------------------


class TestBuildItemsAndComputeAction:
    def test_missing_source_is_skip_no_source(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        repo_root.mkdir()

        items = migrate.build_items(repo_root, local_root)
        for item in items:
            migrate.compute_action(item)

        # Every item's source is absent in a totally empty repo tree.
        assert all(item.action == "skip_no_source" for item in items)
        assert all(item.size_bytes == 0 for item in items)
        assert all(item.file_count == 0 for item in items)

    def test_pkl_glob_only_matches_pkl_files_directly_in_ml_models(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        ml_models = repo_root / "ml" / "models"
        ml_models.mkdir(parents=True)
        (ml_models / "real_model.pkl").write_bytes(b"x" * 100)
        (ml_models / "__init__.py").write_text("# not a model")
        (ml_models / "base.py").write_text("# not a model")
        (ml_models / ".gitkeep").write_text("")
        nested = ml_models / "forecast_cache"
        nested.mkdir()
        (nested / "nested_would_not_count.pkl").write_bytes(b"y" * 50)

        items = migrate.build_items(repo_root, local_root)
        pkl_items = [i for i in items if i.pair_id == "ml_models_pkl"]

        assert len(pkl_items) == 1
        assert pkl_items[0].source.name == "real_model.pkl"

    def test_pkl_glob_registers_placeholder_when_no_pkl_files(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        ml_models = repo_root / "ml" / "models"
        ml_models.mkdir(parents=True)
        (ml_models / "base.py").write_text("# not a model")

        items = migrate.build_items(repo_root, local_root)
        pkl_items = [i for i in items if i.pair_id == "ml_models_pkl"]

        assert len(pkl_items) == 1
        assert pkl_items[0].kind == "glob_placeholder"
        migrate.compute_action(pkl_items[0])
        assert pkl_items[0].action == "skip_no_source"

    def test_dest_nonempty_file_triggers_skip(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        (repo_root / "data").mkdir(parents=True)
        (repo_root / "data" / "universe_cache.parquet").write_bytes(b"source-data")
        local_root.mkdir(parents=True)
        (local_root / "universe_cache.parquet").write_bytes(b"existing-dest-data")

        items = migrate.build_items(repo_root, local_root)
        item = next(i for i in items if i.pair_id == "universe_cache")
        migrate.compute_action(item)

        assert item.action == "skip_dest_exists"

    def test_dest_preexisting_empty_dir_does_not_trigger_skip(self, tmp_path):
        """A dest dir pre-created empty (settings.py's own auto-mkdir behavior
        for LOCAL_DATA_ROOT/output) must NOT count as 'already has content'."""
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        out_src = repo_root / "output"
        out_src.mkdir(parents=True)
        (out_src / "state_snapshot.json").write_text("{}")
        out_dest = local_root / "output"
        out_dest.mkdir(parents=True)  # pre-created, empty

        items = migrate.build_items(repo_root, local_root)
        item = next(i for i in items if i.pair_id == "output")
        migrate.compute_action(item)

        assert item.action == "move"

    def test_db_pair_produces_three_independent_items(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        repo_root.mkdir()

        items = migrate.build_items(repo_root, local_root)
        db_items = [i for i in items if i.pair_id == "quant_platform_db"]

        assert {i.label for i in db_items} == {
            "quant_platform.db",
            "quant_platform.db-wal",
            "quant_platform.db-shm",
        }


# ---------------------------------------------------------------------------
# Dry-run: no filesystem changes
# ---------------------------------------------------------------------------


class TestDryRunMakesNoChanges:
    def test_dry_run_touches_nothing(self, tmp_path, monkeypatch, capsys):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        (repo_root / "data").mkdir(parents=True)
        (repo_root / "data" / "universe_cache.parquet").write_bytes(b"payload")
        (repo_root / "cache").mkdir(parents=True)
        (repo_root / "cache" / "cache.db").write_bytes(b"sqlite-bytes")

        monkeypatch.setattr(migrate, "_REPO_ROOT", repo_root)
        monkeypatch.setattr(migrate.settings, "LOCAL_DATA_ROOT", local_root)

        exit_code = migrate.main([])

        assert exit_code == 0
        # Source files are untouched.
        assert (repo_root / "data" / "universe_cache.parquet").read_bytes() == b"payload"
        assert (repo_root / "cache" / "cache.db").read_bytes() == b"sqlite-bytes"
        # Nothing was created at the destination -- local_root shouldn't even exist.
        assert not local_root.exists()

        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert "would move" in out

    def test_dry_run_with_verify_never_flags_a_warning(self, tmp_path, monkeypatch, capsys):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        (repo_root / "data").mkdir(parents=True)
        (repo_root / "data" / "universe_cache.parquet").write_bytes(b"payload")

        monkeypatch.setattr(migrate, "_REPO_ROOT", repo_root)
        monkeypatch.setattr(migrate.settings, "LOCAL_DATA_ROOT", local_root)

        exit_code = migrate.main(["--verify"])

        assert exit_code == 0  # a dry-run's un-applied "move" items are never a warning
        assert not local_root.exists()
        out = capsys.readouterr().out
        assert "not yet applied" in out
        assert "Verification OK." in out


# ---------------------------------------------------------------------------
# --apply: real moves, byte-for-byte content match
# ---------------------------------------------------------------------------


class TestApplyMovesFiles:
    def test_apply_moves_file_and_matches_content_byte_for_byte(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        (repo_root / "data").mkdir(parents=True)
        payload = b"parquet-bytes-" + b"z" * 500
        (repo_root / "data" / "universe_cache.parquet").write_bytes(payload)

        monkeypatch.setattr(migrate, "_REPO_ROOT", repo_root)
        monkeypatch.setattr(migrate.settings, "LOCAL_DATA_ROOT", local_root)

        exit_code = migrate.main(["--apply"])

        assert exit_code == 0
        assert not (repo_root / "data" / "universe_cache.parquet").exists()
        dest = local_root / "universe_cache.parquet"
        assert dest.exists()
        assert dest.read_bytes() == payload

    def test_apply_moves_directory_merging_into_preexisting_empty_dest(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        src_dir = repo_root / "output"
        src_dir.mkdir(parents=True)
        (src_dir / "state_snapshot.json").write_text('{"a": 1}')
        (src_dir / "history").mkdir()
        (src_dir / "history" / "old.json").write_text("{}")

        dest_dir = local_root / "output"
        dest_dir.mkdir(parents=True)  # pre-created empty, like settings.py's auto-mkdir

        items = migrate.build_items(repo_root, local_root)
        for item in items:
            migrate.compute_action(item)
        migrate.apply_items(items)

        assert (dest_dir / "state_snapshot.json").read_text() == '{"a": 1}'
        assert (dest_dir / "history" / "old.json").read_text() == "{}"
        assert not src_dir.exists()  # emptied and removed, not nested inside dest

    def test_apply_moves_directory_when_dest_absent(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        src_dir = repo_root / "logs"
        src_dir.mkdir(parents=True)
        (src_dir / "investyo.log").write_text("hello\n")

        items = migrate.build_items(repo_root, local_root)
        for item in items:
            migrate.compute_action(item)
        migrate.apply_items(items)

        dest_dir = local_root / "logs"
        assert dest_dir.is_dir()
        assert (dest_dir / "investyo.log").read_text() == "hello\n"
        assert not src_dir.exists()

    def test_apply_moves_ml_pkl_files_individually_and_leaves_py_files_alone(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        ml_models = repo_root / "ml" / "models"
        ml_models.mkdir(parents=True)
        (ml_models / "model_a.pkl").write_bytes(b"aaa")
        (ml_models / "model_b.pkl").write_bytes(b"bbb")
        (ml_models / "base.py").write_text("# untouched")

        items = migrate.build_items(repo_root, local_root)
        for item in items:
            migrate.compute_action(item)
        migrate.apply_items(items)

        assert (local_root / "ml_models" / "model_a.pkl").read_bytes() == b"aaa"
        assert (local_root / "ml_models" / "model_b.pkl").read_bytes() == b"bbb"
        assert not (ml_models / "model_a.pkl").exists()
        assert not (ml_models / "model_b.pkl").exists()
        # Non-.pkl siblings are never touched.
        assert (ml_models / "base.py").read_text() == "# untouched"

    def test_verify_after_apply_reports_no_warnings(self, tmp_path, monkeypatch, capsys):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        (repo_root / "data").mkdir(parents=True)
        (repo_root / "data" / "universe_cache.parquet").write_bytes(b"payload")

        monkeypatch.setattr(migrate, "_REPO_ROOT", repo_root)
        monkeypatch.setattr(migrate.settings, "LOCAL_DATA_ROOT", local_root)

        exit_code = migrate.main(["--apply", "--verify"])

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Verification OK." in out


# ---------------------------------------------------------------------------
# Skip-if-destination-exists never overwrites
# ---------------------------------------------------------------------------


class TestSkipDestinationNeverOverwrites:
    def test_apply_does_not_overwrite_nonempty_dest_file(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        (repo_root / "cache").mkdir(parents=True)
        (repo_root / "cache" / "sync_report.json").write_text('{"source": true}')
        dest = local_root / "robinhood_cache" / "sync_report.json"
        dest.parent.mkdir(parents=True)
        dest.write_text('{"existing": true}')

        items = migrate.build_items(repo_root, local_root)
        item = next(i for i in items if i.pair_id == "rh_sync_report")
        migrate.compute_action(item)
        assert item.action == "skip_dest_exists"

        migrate.apply_items(items)

        # Source untouched, dest untouched -- no silent overwrite.
        assert (repo_root / "cache" / "sync_report.json").read_text() == '{"source": true}'
        assert dest.read_text() == '{"existing": true}'
        assert item.moved is False

    def test_apply_does_not_overwrite_nonempty_dest_dir(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        src_dir = repo_root / "logs"
        src_dir.mkdir(parents=True)
        (src_dir / "investyo.log").write_text("source log line\n")

        dest_dir = local_root / "logs"
        dest_dir.mkdir(parents=True)
        (dest_dir / "investyo.log").write_text("pre-existing dest log line\n")

        items = migrate.build_items(repo_root, local_root)
        item = next(i for i in items if i.pair_id == "logs")
        migrate.compute_action(item)
        assert item.action == "skip_dest_exists"

        migrate.apply_items(items)

        assert (src_dir / "investyo.log").read_text() == "source log line\n"
        assert (dest_dir / "investyo.log").read_text() == "pre-existing dest log line\n"

    def test_apply_with_verify_flags_warning_when_a_skip_leaves_source_and_move_expected_elsewhere(
        self, tmp_path, monkeypatch, capsys
    ):
        """Sanity check that --verify's warning path itself only fires for a
        genuinely-failed move, never for a legitimate destination-exists skip."""
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        (repo_root / "cache").mkdir(parents=True)
        (repo_root / "cache" / "sync_report.json").write_text('{"source": true}')
        dest = local_root / "robinhood_cache" / "sync_report.json"
        dest.parent.mkdir(parents=True)
        dest.write_text('{"existing": true}')

        monkeypatch.setattr(migrate, "_REPO_ROOT", repo_root)
        monkeypatch.setattr(migrate.settings, "LOCAL_DATA_ROOT", local_root)

        exit_code = migrate.main(["--apply", "--verify"])

        assert exit_code == 0  # a legitimate skip must never be reported as a warning
        out = capsys.readouterr().out
        assert "Verification OK." in out


# ---------------------------------------------------------------------------
# Missing source is skipped without error
# ---------------------------------------------------------------------------


class TestMissingSourceSkippedWithoutError:
    def test_missing_db_files_are_skipped(self, tmp_path):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        repo_root.mkdir()

        items = migrate.build_items(repo_root, local_root)
        db_items = [i for i in items if i.pair_id == "quant_platform_db"]
        for item in db_items:
            migrate.compute_action(item)
            assert item.action == "skip_no_source"

        # apply_items must be a safe no-op over missing sources.
        migrate.apply_items(items)
        for item in db_items:
            assert item.moved is False
            assert item.error is None

    def test_main_dry_run_over_fully_empty_tree_does_not_raise(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        repo_root.mkdir()

        monkeypatch.setattr(migrate, "_REPO_ROOT", repo_root)
        monkeypatch.setattr(migrate.settings, "LOCAL_DATA_ROOT", local_root)

        exit_code = migrate.main(["--verify"])
        assert exit_code == 0

    def test_main_apply_over_fully_empty_tree_does_not_raise(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        local_root = tmp_path / "local"
        repo_root.mkdir()

        monkeypatch.setattr(migrate, "_REPO_ROOT", repo_root)
        monkeypatch.setattr(migrate.settings, "LOCAL_DATA_ROOT", local_root)

        exit_code = migrate.main(["--apply", "--verify"])
        assert exit_code == 0

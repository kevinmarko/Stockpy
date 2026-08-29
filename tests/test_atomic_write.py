"""Tests for reporting/atomic_write.py::atomic_write_json -- the shared
write-then-rename JSON helper extracted for F11
(docs/module_efficiency_redundancy_audit.md), replacing two byte-identical
private copies previously in reporting/pairs_snapshot.py and
reporting/options_snapshot.py."""
import json
import threading
from pathlib import Path

import pytest

from reporting.atomic_write import atomic_write_json


class TestAtomicWriteJson:
    def test_writes_valid_json_readable_back(self, tmp_path):
        path = tmp_path / "out" / "snapshot.json"
        atomic_write_json(path, {"a": 1, "b": [1, 2, 3]})

        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "dir" / "out.json"
        atomic_write_json(path, {"x": 1})
        assert path.exists()

    def test_no_leftover_temp_file_after_a_successful_write(self, tmp_path):
        path = tmp_path / "out.json"
        atomic_write_json(path, {"x": 1})

        leftovers = [p for p in tmp_path.iterdir() if p.name != "out.json"]
        assert leftovers == []

    def test_overwrites_an_existing_file(self, tmp_path):
        path = tmp_path / "out.json"
        atomic_write_json(path, {"v": 1})
        atomic_write_json(path, {"v": 2})
        assert json.loads(path.read_text(encoding="utf-8")) == {"v": 2}

    def test_temp_filename_is_pid_and_thread_scoped_not_a_bare_suffix(self, tmp_path, monkeypatch):
        """The actual fix this helper exists for (F11): the two
        pre-migration copies used path.with_suffix(".tmp") -- NOT
        pid/tid-scoped, so two concurrent writers targeting the same path
        collided on the identical temp name. Confirms the real temp name
        used mid-write carries both, by capturing it via a patched
        Path.write_text."""
        path = tmp_path / "out.json"
        seen_tmp_names = []
        original_write_text = Path.write_text

        def _spy_write_text(self, *a, **kw):
            seen_tmp_names.append(self.name)
            return original_write_text(self, *a, **kw)

        monkeypatch.setattr(Path, "write_text", _spy_write_text)

        atomic_write_json(path, {"x": 1})

        assert len(seen_tmp_names) == 1
        name = seen_tmp_names[0]
        assert name.startswith("out.json.tmp.")
        import os
        assert f".tmp.{os.getpid()}." in name
        assert name.endswith(f".{threading.get_ident()}")

    def test_indent_defaults_to_two(self, tmp_path):
        path = tmp_path / "out.json"
        atomic_write_json(path, {"a": 1})
        text = path.read_text(encoding="utf-8")
        assert "\n  " in text  # 2-space indent, matching the pre-migration copies

    def test_indent_is_overridable(self, tmp_path):
        path = tmp_path / "out.json"
        atomic_write_json(path, {"a": 1}, indent=4)
        text = path.read_text(encoding="utf-8")
        assert "\n    " in text

    def test_raises_on_a_write_failure_rather_than_swallowing(self, tmp_path, monkeypatch):
        """Matches both pre-migration copies' implicit contract -- neither
        caught anything, so a caller's own dead-letter wrapper (or lack of
        one) determines what happens on failure, not this helper."""
        path = tmp_path / "out.json"

        def _boom(self, *a, **kw):
            raise OSError("disk full (simulated)")

        monkeypatch.setattr(Path, "write_text", _boom)

        with pytest.raises(OSError):
            atomic_write_json(path, {"a": 1})

    def test_concurrent_writers_to_the_same_path_never_collide_on_the_temp_name(self, tmp_path):
        """Two threads writing to the SAME destination path concurrently
        must never observe or clobber each other's temp file -- the exact
        race path.with_suffix(".tmp") was vulnerable to. Each thread's
        os.replace either fully wins or fully loses; the file is always
        valid JSON from ONE of the two writes, never a mix."""
        path = tmp_path / "shared.json"
        errors = []

        def _writer(value):
            try:
                for _ in range(20):
                    atomic_write_json(path, {"v": value})
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [
            threading.Thread(target=_writer, args=(1,)),
            threading.Thread(target=_writer, args=(2,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        final = json.loads(path.read_text(encoding="utf-8"))
        assert final["v"] in (1, 2)

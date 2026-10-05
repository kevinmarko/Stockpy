"""
tests/test_runtime_flags_test_isolation.py
==========================================
Regression tests for the test suite writing the operator's LIVE runtime-flags
store (docs/known_issues/runtime_flags_store_test_contamination_2026_10.md).

From 2026-09-07, ``tests/test_settings_reference.py`` drove ``PUT
/settings/reference`` with only the ``.env`` write mocked, so
``api/pilots_api.py::_apply_live_overrides`` called the real
``runtime_flags_writer.write_override`` against the machine-global
``~/.stockpy_local/output/runtime_flags.json`` on every suite run, under the
real API's actor name. From 2026-09-27 that included ``ADVISORY_ONLY=false``.

Two layers are pinned here:

1. The root ``conftest.py`` fixture ``_isolate_runtime_flags_store_in_tests``
   redirects the store for every test (the primary fix).
2. ``runtime_flags_writer`` refuses the live default store whenever pytest is
   loaded (the backstop), and that refusal is inert in a process that has not
   imported pytest.

Nothing here ever opens the real default store for writing. The one test that
looks at it only ``stat``s it.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

import api.pilots_api as pilots_api
import runtime_flags
import runtime_flags_writer as writer
import settings as settings_module
from settings import Settings, settings

REPO_ROOT = Path(__file__).resolve().parents[1]
# A live_safe int field with no safety meaning, reused from the writer tests.
FIELD = "BETA_LOOKBACK_DAYS"

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))
_CMD_TOKEN = "cmd-tok"


def _stat_signature(path: Path):
    """(exists, size, mtime_ns) — a read-only fingerprint of a file."""
    if not path.exists():
        return (False, None, None)
    st = path.stat()
    return (True, st.st_size, st.st_mtime_ns)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """A throwaway settings singleton so ``write_override``'s in-process apply
    never touches the real one (same pattern as the writer tests)."""
    monkeypatch.delenv(FIELD, raising=False)
    throwaway = Settings()
    monkeypatch.setattr(settings_module, "settings", throwaway)
    return throwaway


class TestConftestIsolation:
    def test_store_is_never_the_live_default_inside_a_test(self):
        assert runtime_flags.store_path() != runtime_flags.DEFAULT_STORE_PATH
        assert os.environ.get(runtime_flags.PATH_OVERRIDE_ENV_VAR)

    def test_audit_log_is_redirected_with_the_store(self):
        live_audit = runtime_flags.DEFAULT_STORE_PATH.with_name(writer.AUDIT_FILENAME)
        assert writer.audit_path() != live_audit.resolve()

    def test_confirmed_advisory_only_put_lands_in_the_tmp_store_not_the_live_one(self):
        """The exact request that leaked: PUT /settings/reference with a
        confirmed ADVISORY_ONLY=false, .env write mocked, writer left real."""
        live_store = runtime_flags.DEFAULT_STORE_PATH
        live_audit = live_store.with_name(writer.AUDIT_FILENAME)
        before = (_stat_signature(live_store), _stat_signature(live_audit))

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN))
            stack.enter_context(
                mock.patch.object(settings, "GENERAL_SETTINGS_WRITES_ENABLED", True)
            )
            stack.enter_context(mock.patch.object(pilots_api.env_io, "write_many_atomic"))
            # Keep the real singleton untouched by the in-process apply.
            stack.enter_context(
                mock.patch.object(settings_module, "settings", Settings())
            )
            resp = client.put(
                "/settings/reference",
                json={
                    "values": {"ADVISORY_ONLY": False},
                    "confirm": {"ADVISORY_ONLY": "ADVISORY_ONLY"},
                },
                headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
            )
        assert resp.status_code == 200
        assert resp.json()["written"] == {"ADVISORY_ONLY": False}

        after = (_stat_signature(live_store), _stat_signature(live_audit))
        assert after == before, "a test wrote the operator's live runtime-flags store"

        tmp_store = runtime_flags.store_path()
        if resp.json()["per_key_applies"].get("ADVISORY_ONLY") == "immediately":
            # Only when the liveness classifier routes the key through the
            # writer; then the write must be in the redirected store.
            flags = json.loads(tmp_store.read_text(encoding="utf-8"))["flags"]
            assert flags["ADVISORY_ONLY"]["value"] is False


class TestWriterBackstop:
    @pytest.fixture
    def fake_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Point DEFAULT_STORE_PATH at tmp and drop the conftest override, so a
        call with no path= resolves to "the default store" without that being
        the operator's real file."""
        fake = tmp_path / "live" / "runtime_flags.json"
        monkeypatch.setattr(runtime_flags, "DEFAULT_STORE_PATH", fake)
        monkeypatch.delenv(runtime_flags.PATH_OVERRIDE_ENV_VAR, raising=False)
        return fake

    def test_write_to_default_store_is_refused_under_pytest(
        self, fake_default: Path, live: Settings
    ):
        before_value = live.BETA_LOOKBACK_DAYS
        result = writer.write_override(FIELD, 300, actor="pilots_api")
        assert result.ok is False
        assert result.persisted is False
        assert result.applies == writer.APPLIES_REFUSED
        assert "pytest" in (result.reason or "")
        assert not fake_default.exists()
        # No audit line either: the audit log sits beside the live store.
        assert not fake_default.with_name(writer.AUDIT_FILENAME).exists()
        assert live.BETA_LOOKBACK_DAYS == before_value

    def test_delete_on_default_store_is_refused_under_pytest(self, fake_default: Path):
        fake_default.parent.mkdir(parents=True)
        payload = {"version": runtime_flags.SCHEMA_VERSION, "flags": {FIELD: {"value": 300}}}
        fake_default.write_text(json.dumps(payload), encoding="utf-8")
        before = fake_default.read_bytes()

        result = writer.delete_override(FIELD, actor="pilots_api")
        assert result.ok is False
        assert result.applies == writer.APPLIES_REFUSED
        assert fake_default.read_bytes() == before
        assert not fake_default.with_name(writer.AUDIT_FILENAME).exists()

    def test_explicit_non_default_path_is_unaffected(self, tmp_path: Path, live: Settings):
        store = tmp_path / "elsewhere.json"
        result = writer.write_override(FIELD, 300, actor="t", path=store)
        assert result.ok is True
        assert store.exists()

    def test_backstop_is_inert_when_pytest_is_not_loaded_in_process(
        self, fake_default: Path, live: Settings, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(writer, "_pytest_loaded", lambda: False)
        result = writer.write_override(FIELD, 300, actor="t")
        assert result.ok is True
        assert fake_default.exists()

    def test_backstop_is_inert_in_a_fresh_interpreter_without_pytest(self, tmp_path: Path):
        """The real check (``"pytest" in sys.modules``), in a process that
        never imported pytest: the write to (a tmp stand-in for) the default
        store must succeed, so production is never affected."""
        fake = tmp_path / "live" / "runtime_flags.json"
        code = textwrap.dedent(
            f"""
            import json, sys
            from pathlib import Path
            import runtime_flags
            runtime_flags.DEFAULT_STORE_PATH = Path({str(fake)!r})
            import runtime_flags_writer as writer
            loaded_before = "pytest" in sys.modules
            r = writer.write_override({FIELD!r}, 300, actor="fresh")
            print(json.dumps({{
                "pytest_loaded": loaded_before or "pytest" in sys.modules,
                "guard": writer._pytest_loaded(),
                "ok": r.ok,
                "persisted": r.persisted,
            }}))
            """
        )
        env = dict(os.environ)
        env.pop(runtime_flags.PATH_OVERRIDE_ENV_VAR, None)
        env.pop("PYTEST_CURRENT_TEST", None)
        env["NO_VENV_REEXEC"] = "1"
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out == {"pytest_loaded": False, "guard": False, "ok": True, "persisted": True}
        assert fake.exists()

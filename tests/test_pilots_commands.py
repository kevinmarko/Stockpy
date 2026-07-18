"""Tests for pilots/commands.py (the manifest reader) and GET /commands.

The reader mirrors pilots/run_status.py's honesty posture: a missing or corrupt
manifest degrades to an empty ``commands`` list plus a ``reason`` — never a
fabricated command list and never an exception. The endpoint is a fail-open
read (``require_read_token``) like every other GET on the Pilots API.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from settings import settings
from pilots import commands as commands_reader
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app)


# --------------------------------------------------------------------------- #
# Reader
# --------------------------------------------------------------------------- #
def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_reader_happy_path(tmp_path: Path):
    manifest = tmp_path / "m.json"
    _write(
        manifest,
        {
            "generated_at": "2026-07-17T00:00:00+00:00",
            "dead_letters": ["broken.py"],
            "commands": [{"name": "main.py", "invocation": "python3 main.py"}],
        },
    )
    out = commands_reader.command_manifest(path=manifest)
    assert out["reason"] is None
    assert out["command_count"] == 1
    assert out["dead_letters"] == ["broken.py"]
    assert out["commands"][0]["name"] == "main.py"


def test_reader_missing_file_is_honest_not_fabricated(tmp_path: Path):
    out = commands_reader.command_manifest(path=tmp_path / "nope.json")
    assert out["commands"] == []
    assert out["command_count"] == 0
    assert "build_command_manifest" in out["reason"]


def test_reader_corrupt_file_degrades(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    out = commands_reader.command_manifest(path=bad)
    assert out["commands"] == []
    assert out["reason"]


def test_reader_wrong_shape_degrades(tmp_path: Path):
    weird = tmp_path / "weird.json"
    _write(weird, {"commands": "not-a-list"})
    out = commands_reader.command_manifest(path=weird)
    assert out["commands"] == []
    assert out["reason"]


# --------------------------------------------------------------------------- #
# GET /commands
# --------------------------------------------------------------------------- #
def test_commands_endpoint_shape_from_committed_manifest():
    # Reads the real committed cli_introspect/command_manifest.json.
    resp = client.get("/commands")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] is None
    assert body["command_count"] >= 1
    names = {c["name"] for c in body["commands"]}
    assert "main.py" in names


def test_commands_endpoint_fail_open_no_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", ""):
        resp = client.get("/commands")
    assert resp.status_code == 200


def test_commands_endpoint_401_on_wrong_token():
    with mock.patch.object(settings, "STATE_API_TOKEN", "real-tok"):
        resp = client.get("/commands", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_commands_endpoint_cold_start_reason(monkeypatch, tmp_path: Path):
    # No manifest present → honest empty shape with a reason, still 200 (matches
    # /options and /pairs cold-start behavior; never a fabricated command list).
    monkeypatch.setattr(commands_reader, "_DEFAULT_MANIFEST", tmp_path / "absent.json")
    resp = client.get("/commands")
    assert resp.status_code == 200
    body = resp.json()
    assert body["commands"] == []
    assert body["reason"]

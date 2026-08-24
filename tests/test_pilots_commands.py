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

# Starlette's TestClient defaults request.client.host to the literal
# string "testclient" -- NOT loopback -- which would trip
# api.auth.require_read_token's new fail-closed-when-non-loopback branch
# on every one of this file's existing zero-config-behavior assertions.
# An explicit loopback host here is what these tests have always meant.
client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))


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
    # options_strategy_registry / paper_broker_options_strategy_registry both
    # default to [] when the manifest predates them (mirrors strategy_registry's
    # own backward-compat degrade).
    assert out["options_strategy_registry"] == []
    assert out["paper_broker_options_strategy_registry"] == []


def test_reader_options_strategy_registry_passed_through(tmp_path: Path):
    manifest = tmp_path / "m.json"
    _write(
        manifest,
        {
            "generated_at": "2026-08-21T00:00:00+00:00",
            "commands": [],
            "strategy_registry": ["rsi2_mean_reversion"],
            "options_strategy_registry": ["Iron Condor", "Put Credit Spread"],
            "paper_broker_options_strategy_registry": ["put_credit_spread", "vrp_premium_selling"],
        },
    )
    out = commands_reader.command_manifest(path=manifest)
    assert out["strategy_registry"] == ["rsi2_mean_reversion"]
    assert out["options_strategy_registry"] == ["Iron Condor", "Put Credit Spread"]
    assert out["paper_broker_options_strategy_registry"] == ["put_credit_spread", "vrp_premium_selling"]


def test_reader_missing_file_is_honest_not_fabricated(tmp_path: Path):
    out = commands_reader.command_manifest(path=tmp_path / "nope.json")
    assert out["commands"] == []
    assert out["command_count"] == 0
    assert out["strategy_registry"] == []
    assert out["options_strategy_registry"] == []
    assert out["paper_broker_options_strategy_registry"] == []
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
    # Fail-open GET, no Authorization header sent -- pinned STATE_API_TOKEN
    # unset (matching test_commands_endpoint_fail_open_no_token below) so
    # this doesn't depend on the machine's real .env leaving it unset.
    with mock.patch.object(settings, "STATE_API_TOKEN", ""):
        resp = client.get("/commands")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] is None
    assert body["command_count"] >= 1
    names = {c["name"] for c in body["commands"]}
    assert "main.py" in names
    assert "Iron Condor" in body["options_strategy_registry"]
    assert "put_credit_spread" in body["paper_broker_options_strategy_registry"]


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
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "")
    resp = client.get("/commands")
    assert resp.status_code == 200
    body = resp.json()
    assert body["commands"] == []
    assert body["reason"]


# --------------------------------------------------------------------------- #
# resolve_command
# --------------------------------------------------------------------------- #
# Mirrors the frontend's commandParse.ts::resolveCommand matching rules:
# case-insensitive match against a command's `name`, any `aliases` entry, or
# the last whitespace token of `invocation`; recurses into `subcommands` when
# a `subcommand` argument is given. Fixtures follow the same `_write` +
# `path=` override pattern used by the reader tests above rather than the
# real committed manifest, so each test controls exactly what shape it needs.
def _write_manifest(path: Path, commands) -> None:
    _write(path, {"generated_at": "2026-07-30T00:00:00+00:00", "commands": commands})


def test_resolve_command_by_exact_name(tmp_path: Path):
    manifest = tmp_path / "m.json"
    _write_manifest(manifest, [
        {"name": "main.py", "invocation": "python3 main.py", "aliases": [], "subcommands": []},
    ])
    resolved = commands_reader.resolve_command("main.py", path=manifest)
    assert resolved is not None
    assert resolved["name"] == "main.py"


def test_resolve_command_by_alias(tmp_path: Path):
    manifest = tmp_path / "m.json"
    _write_manifest(manifest, [
        {
            "name": "execution.kill_switch",
            "invocation": "python -m execution.kill_switch",
            "aliases": ["kill-switch", "ks"],
            "subcommands": [],
        },
    ])
    resolved = commands_reader.resolve_command("ks", path=manifest)
    assert resolved is not None
    assert resolved["name"] == "execution.kill_switch"


def test_resolve_command_by_last_invocation_token_when_it_differs_from_name(tmp_path: Path):
    # Synthetic fixture where the command's canonical `name` is deliberately
    # NOT the trailing whitespace token of its `invocation` -- proves the
    # matcher really checks the invocation tail as its own criterion, rather
    # than happening to work only because name == last token (as it does for
    # e.g. "execution.kill_switch" / "python -m execution.kill_switch").
    manifest = tmp_path / "m.json"
    _write_manifest(manifest, [
        {
            "name": "orchestrator.run",
            "invocation": "python3 main_orchestrator.py --mode run",
            "aliases": [],
            "subcommands": [],
        },
    ])
    by_name = commands_reader.resolve_command("orchestrator.run", path=manifest)
    by_last_token = commands_reader.resolve_command("run", path=manifest)
    assert by_name is not None
    assert by_last_token is not None
    assert by_name == by_last_token
    assert by_name["name"] == "orchestrator.run"
    # And a token that is neither the name, an alias, nor the invocation tail
    # must NOT resolve.
    assert commands_reader.resolve_command("main_orchestrator.py", path=manifest) is None


def test_resolve_command_subcommand(tmp_path: Path):
    manifest = tmp_path / "m.json"
    _write_manifest(manifest, [
        {
            "name": "prompt_registry",
            "invocation": "python -m prompt_registry",
            "aliases": [],
            "subcommands": [
                {
                    "name": "list",
                    "invocation": "python -m prompt_registry list",
                    "aliases": ["ls"],
                    "subcommands": [],
                },
                {
                    "name": "show",
                    "invocation": "python -m prompt_registry show",
                    "aliases": [],
                    "subcommands": [],
                },
            ],
        },
    ])
    resolved = commands_reader.resolve_command("prompt_registry", "list", path=manifest)
    assert resolved is not None
    assert resolved["name"] == "list"
    # The subcommand's own alias must resolve too.
    resolved_by_alias = commands_reader.resolve_command("prompt_registry", "ls", path=manifest)
    assert resolved_by_alias is not None
    assert resolved_by_alias["name"] == "list"


def test_resolve_command_unknown_top_level_returns_none(tmp_path: Path):
    manifest = tmp_path / "m.json"
    _write_manifest(manifest, [
        {"name": "main.py", "invocation": "python3 main.py", "aliases": [], "subcommands": []},
    ])
    assert commands_reader.resolve_command("not_a_real_command", path=manifest) is None


def test_resolve_command_known_top_level_unknown_subcommand_returns_none(tmp_path: Path):
    manifest = tmp_path / "m.json"
    _write_manifest(manifest, [
        {
            "name": "prompt_registry",
            "invocation": "python -m prompt_registry",
            "aliases": [],
            "subcommands": [
                {
                    "name": "list",
                    "invocation": "python -m prompt_registry list",
                    "aliases": [],
                    "subcommands": [],
                },
            ],
        },
    ])
    assert commands_reader.resolve_command("prompt_registry", "not_a_real_subcommand", path=manifest) is None


def test_resolve_command_is_case_insensitive(tmp_path: Path):
    manifest = tmp_path / "m.json"
    _write_manifest(manifest, [
        {
            "name": "execution.kill_switch",
            "invocation": "python -m execution.kill_switch",
            "aliases": ["kill-switch"],
            "subcommands": [
                {
                    "name": "Status",
                    "invocation": "python -m execution.kill_switch Status",
                    "aliases": [],
                    "subcommands": [],
                },
            ],
        },
    ])
    # Mixed-case name.
    assert commands_reader.resolve_command("EXECUTION.KILL_SWITCH", path=manifest) is not None
    # Mixed-case alias.
    assert commands_reader.resolve_command("Kill-Switch", path=manifest) is not None
    # Mixed-case subcommand token against a mixed-case fixture name.
    resolved = commands_reader.resolve_command("execution.kill_switch", "STATUS", path=manifest)
    assert resolved is not None
    assert resolved["name"] == "Status"

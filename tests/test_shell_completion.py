"""Tests for scripts/generate_shell_completion.py.

Assert the generated bash/zsh scripts embed the expected commands, subcommands,
aliases and flags, and — when the shells are on PATH — that they pass a syntax
check (``bash -n`` / ``zsh -n``).
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_shell_completion", _REPO_ROOT / "scripts" / "generate_shell_completion.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass introspection can resolve the module.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gen = _load_generator()

# A synthetic manifest exercising: a path CLI with flags, a -m module CLI, and a
# subcommand CLI with an alias.
_MANIFEST = [
    {
        "name": "main.py",
        "invocation": "python3 main.py",
        "aliases": [],
        "options": [
            {"name": "--interval", "aliases": ["--interval"], "description": "refresh cadence"},
            {"name": "--agent", "aliases": ["--agent"], "description": "autonomous loop"},
        ],
        "subcommands": [],
    },
    {
        "name": "execution.kill_switch",
        "invocation": "python -m execution.kill_switch",
        "aliases": [],
        "options": [{"name": "--status", "aliases": ["--status"], "description": "show state"}],
        "subcommands": [],
    },
    {
        "name": "prompt_registry",
        "invocation": "python -m prompt_registry",
        "aliases": [],
        "options": [],
        "subcommands": [
            {
                "name": "get",
                "aliases": ["g"],
                "description": "fetch one",
                "options": [
                    {"name": "--version", "aliases": ["--version", "-v"], "description": "pin"}
                ],
            },
            {"name": "list", "aliases": [], "description": "show all", "options": []},
        ],
    },
]


def test_bash_contains_commands_and_flags():
    script = gen.render_bash(_MANIFEST, gen.build_contexts(_MANIFEST))
    assert "complete -F _investyo_complete python python3" in script
    assert "main.py|*/main.py) cmd='main.py'" in script
    assert "cands=(--interval --agent)" in script
    # module target guarded by a preceding -m
    assert 'execution.kill_switch) [[ "$prevw" == "-m" ]]' in script
    # subcommands offered at the top level, options at the subcommand level
    assert "kind='subs'; cands=(get list)" in script
    assert "prompt_registry/get) kind='opts'; cands=(--version -v)" in script
    # the alias `g` maps to canonical `get` during detection
    assert "get|g) sub='get'" in script


def test_zsh_contains_descriptions():
    script = gen.render_zsh(_MANIFEST, gen.build_contexts(_MANIFEST))
    assert "compdef _investyo_complete python python3" in script
    assert "#compdef python python3" in script
    # zsh display strings carry the description after ' -- '
    assert "--interval  -- refresh cadence" in script
    assert "get  -- fetch one" in script


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_bash_syntax_valid(tmp_path: Path):
    script = gen.render_bash(_MANIFEST, gen.build_contexts(_MANIFEST))
    f = tmp_path / "c.bash"
    f.write_text(script, encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(f)]).returncode == 0


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not on PATH")
def test_zsh_syntax_valid(tmp_path: Path):
    script = gen.render_zsh(_MANIFEST, gen.build_contexts(_MANIFEST))
    f = tmp_path / "c.zsh"
    f.write_text(script, encoding="utf-8")
    assert subprocess.run(["zsh", "-n", str(f)]).returncode == 0


class TestCommittedArtifactIsFresh:
    """completions/investyo.{bash,zsh} are committed, machine-generated artifacts
    (scripts/generate_shell_completion.py, driven by the committed
    cli_introspect/command_manifest.json). Nothing previously asserted they were
    regenerated after a manifest change -- this closes that gap, matching the
    established pattern in tests/test_settings_liveness.py /
    tests/test_measure_settings_census.py's own TestCommittedArtifactIsFresh
    classes.
    """

    @staticmethod
    def _load_real_manifest():
        import json as _json

        return _json.loads((_REPO_ROOT / "cli_introspect" / "command_manifest.json").read_text(encoding="utf-8")).get(
            "commands", []
        )

    def test_committed_bash_matches_a_fresh_run(self):
        commands = self._load_real_manifest()
        expected = gen.render_bash(commands, gen.build_contexts(commands))
        committed = (_REPO_ROOT / "completions" / "investyo.bash").read_text(encoding="utf-8")
        assert committed == expected, (
            "completions/investyo.bash is stale relative to "
            "cli_introspect/command_manifest.json. Regenerate with "
            "`python scripts/generate_shell_completion.py` and commit the result."
        )

    def test_committed_zsh_matches_a_fresh_run(self):
        commands = self._load_real_manifest()
        expected = gen.render_zsh(commands, gen.build_contexts(commands))
        committed = (_REPO_ROOT / "completions" / "investyo.zsh").read_text(encoding="utf-8")
        assert committed == expected, (
            "completions/investyo.zsh is stale relative to "
            "cli_introspect/command_manifest.json. Regenerate with "
            "`python scripts/generate_shell_completion.py` and commit the result."
        )

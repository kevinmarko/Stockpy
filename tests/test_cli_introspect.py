"""Tests for cli_introspect — the argparse-introspection engine behind the
command manifest (shell completion + Pilots PWA command bar).

The pure ``walk_parser`` tests use synthetic parsers (fast, no subprocess). Two
end-to-end tests exercise the subprocess capture harness and its dead-letter
tolerance.
"""
from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

import pytest

from cli_introspect import (
    ARG_KIND_OPTIONAL,
    ARG_KIND_REQUIRED,
    ARG_KIND_VARIADIC,
    walk_parser,
)
from cli_introspect.capture import capture_command


def _spec(parser: argparse.ArgumentParser):
    return walk_parser(parser, name="demo", invocation="demo")


def _opt(spec, name):
    return next(o for o in spec.options if o.name == name)


def test_aliases_from_option_strings():
    p = argparse.ArgumentParser()
    p.add_argument("-v", "--verbose", action="store_true", help="be loud")
    spec = _spec(p)
    o = _opt(spec, "--verbose")  # canonical = longest --long form
    assert o.aliases == ["-v", "--verbose"]
    assert o.takes_value is False
    assert o.description == "be loud"


def test_required_option_and_default():
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", required=True, help="name")
    p.add_argument("--start", default="2020-01-01")
    spec = _spec(p)
    strat = _opt(spec, "--strategy")
    assert strat.required is True
    assert strat.arg_kind == ARG_KIND_REQUIRED
    assert _opt(spec, "--start").default == "2020-01-01"
    assert _opt(spec, "--start").arg_kind == ARG_KIND_OPTIONAL


def test_choices_captured():
    p = argparse.ArgumentParser()
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    spec = _spec(p)
    o = _opt(spec, "--format")
    assert o.choices == ["markdown", "json"]


@pytest.mark.parametrize(
    "nargs,expected",
    [("+", ARG_KIND_VARIADIC), ("*", ARG_KIND_VARIADIC), (3, ARG_KIND_VARIADIC)],
)
def test_variadic_option(nargs, expected):
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs=nargs)
    assert _opt(_spec(p), "--tickers").arg_kind == expected


def test_positional_kinds():
    p = argparse.ArgumentParser()
    p.add_argument("src")  # exactly one → required
    p.add_argument("dst", nargs="?")  # optional
    p.add_argument("extra", nargs="*")  # variadic
    spec = _spec(p)
    kinds = {a.name: a.arg_kind for a in spec.positionals}
    assert kinds == {
        "src": ARG_KIND_REQUIRED,
        "dst": ARG_KIND_OPTIONAL,
        "extra": ARG_KIND_VARIADIC,
    }


def test_suppress_default_is_none_not_fabricated():
    p = argparse.ArgumentParser()
    p.add_argument("--x", default=argparse.SUPPRESS)
    assert _opt(_spec(p), "--x").default is None


def test_subcommands_and_aliases():
    p = argparse.ArgumentParser(prog="tool")
    sub = p.add_subparsers(dest="command")
    g = sub.add_parser("get", aliases=["g"], help="fetch one")
    g.add_argument("id")
    sub.add_parser("list", help="show all")
    spec = walk_parser(p, name="tool", invocation="tool")

    names = {c.name for c in spec.subcommands}
    assert names == {"get", "list"}
    get = next(c for c in spec.subcommands if c.name == "get")
    assert get.aliases == ["g"]
    assert get.description == "fetch one"
    assert [a.name for a in get.positionals] == ["id"]
    assert get.invocation == "tool get"


# --------------------------------------------------------------------------- #
# Subprocess capture harness + dead-letter tolerance.
# --------------------------------------------------------------------------- #
def test_capture_command_end_to_end(tmp_path: Path):
    script = tmp_path / "toy_cli.py"
    script.write_text(
        textwrap.dedent(
            """
            import argparse
            def main():
                p = argparse.ArgumentParser(description="toy")
                p.add_argument("--count", type=int, default=5, help="how many")
                p.add_argument("-f", "--force", action="store_true")
                p.parse_args()
                raise SystemExit("should never reach here")  # body must NOT run
            if __name__ == "__main__":
                main()
            """
        ),
        encoding="utf-8",
    )
    spec = capture_command("path", str(script), "toy", "python toy_cli.py")
    assert spec is not None
    names = {o["name"] for o in spec["options"]}
    assert names == {"--count", "--force"}
    count = next(o for o in spec["options"] if o["name"] == "--count")
    assert count["default"] == 5 and count["description"] == "how many"
    force = next(o for o in spec["options"] if o["name"] == "--force")
    assert force["aliases"] == ["-f", "--force"] and force["takes_value"] is False


def test_capture_command_dead_letters_on_failure(tmp_path: Path):
    # A target that raises during import must degrade to None (dead-letter),
    # never propagate — the manifest build tolerates one broken entry point.
    boom = tmp_path / "boom.py"
    boom.write_text("raise RuntimeError('kaboom')\n", encoding="utf-8")
    assert capture_command("path", str(boom), "boom", "python boom.py") is None


def test_capture_command_dead_letters_on_missing_target():
    assert capture_command("module", "no.such.module.xyz", "missing", "python -m nope") is None

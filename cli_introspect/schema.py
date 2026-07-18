"""cli_introspect/schema.py — the flat, JSON-serializable command model.

Deliberately dependency-light (stdlib only) so ``pilots/commands.py`` can read
the serialized form without pulling anything heavy in. The three ``arg_kind``
values map straight onto the operator's spec:

  * ``required``  — must be supplied (a bare positional, or an option with
    ``required=True``).
  * ``optional``  — may be omitted (``nargs="?"`` positional, or a normal flag).
  * ``variadic``  — accepts many values (``nargs`` in ``{"*", "+"}`` or an int
    greater than 1).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, List, Optional

ARG_KIND_REQUIRED = "required"
ARG_KIND_OPTIONAL = "optional"
ARG_KIND_VARIADIC = "variadic"


@dataclass
class OptionSpec:
    """An optional/flag argument (``option_strings`` non-empty)."""

    name: str  # canonical display name (longest ``--long`` form, else first)
    aliases: List[str]  # every option string, e.g. ["-v", "--version"]
    description: Optional[str]
    default: Any
    choices: Optional[List[str]]
    required: bool
    arg_kind: str  # required | optional | variadic
    metavar: Optional[str]
    takes_value: bool  # False for store_true/false/count/const flags

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ArgSpec:
    """A positional argument (``option_strings`` empty)."""

    name: str
    description: Optional[str]
    default: Any
    choices: Optional[List[str]]
    arg_kind: str  # required | optional | variadic
    metavar: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CommandSpec:
    """A command (a top-level CLI, or one subcommand) and its arguments."""

    name: str  # subcommand name, or the top-level entry-point label
    invocation: str  # display prefix, e.g. "python3 main.py" / "python -m prompt_registry get"
    aliases: List[str]  # subcommand aliases (top-level commands: [])
    description: Optional[str]
    options: List[OptionSpec] = field(default_factory=list)
    positionals: List[ArgSpec] = field(default_factory=list)
    subcommands: List["CommandSpec"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

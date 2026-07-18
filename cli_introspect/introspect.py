"""cli_introspect/introspect.py — walk a *built* argparse parser into a CommandSpec.

Pure and side-effect-free: given an ``argparse.ArgumentParser`` object it reads
``parser._actions`` (and recurses through ``_SubParsersAction`` for subcommands)
and returns a :class:`CommandSpec`. It never calls ``parse_args`` and never
imports anything heavy — ``capture.py`` is responsible for obtaining the parser
object; this module only reads it.

argparse exposes everything the four functional specs need:

  * aliases           → ``action.option_strings`` (short + long) and, for
    subcommands, the extra keys in ``_SubParsersAction._name_parser_map``.
  * required/optional → ``action.required`` / positional ``nargs``.
  * variadic          → ``nargs`` in ``{"*", "+"}`` or an ``int > 1``.
  * descriptions      → ``action.help``; defaults → ``action.default``;
    choices → ``action.choices``.
"""
from __future__ import annotations

import argparse
from typing import Any, List, Optional

from .schema import (
    ARG_KIND_OPTIONAL,
    ARG_KIND_REQUIRED,
    ARG_KIND_VARIADIC,
    ArgSpec,
    CommandSpec,
    OptionSpec,
)

# Action types that consume NO value from the command line (pure flags).
_FLAG_ACTIONS = (
    argparse._StoreTrueAction,
    argparse._StoreFalseAction,
    argparse._StoreConstAction,
    argparse._CountAction,
    argparse._HelpAction,
    argparse._VersionAction,
)


def _is_variadic(nargs: Any) -> bool:
    return nargs in ("*", "+", argparse.REMAINDER) or (isinstance(nargs, int) and nargs > 1)


def _safe(value: Any) -> Any:
    """Coerce a default/choice into a JSON-safe scalar (never fabricate)."""
    if value is argparse.SUPPRESS:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _choices(action: argparse.Action) -> Optional[List[str]]:
    if not action.choices:
        return None
    # A _SubParsersAction's .choices is the subcommand map — handled elsewhere.
    return [str(c) for c in action.choices]


def _option_spec(action: argparse.Action) -> OptionSpec:
    takes_value = not isinstance(action, _FLAG_ACTIONS)
    if _is_variadic(action.nargs):
        kind = ARG_KIND_VARIADIC
    elif action.required:
        kind = ARG_KIND_REQUIRED
    else:
        kind = ARG_KIND_OPTIONAL

    opt_strings = list(action.option_strings)
    long_forms = [s for s in opt_strings if s.startswith("--")]
    canonical = max(long_forms, key=len) if long_forms else opt_strings[0]

    return OptionSpec(
        name=canonical,
        aliases=opt_strings,
        description=action.help,
        default=_safe(action.default),
        choices=_choices(action) if takes_value else None,
        required=bool(action.required),
        arg_kind=kind,
        metavar=action.metavar,
        takes_value=takes_value,
    )


def _positional_spec(action: argparse.Action) -> ArgSpec:
    nargs = action.nargs
    if _is_variadic(nargs):
        kind = ARG_KIND_VARIADIC
    elif nargs == "?":
        kind = ARG_KIND_OPTIONAL
    else:
        kind = ARG_KIND_REQUIRED  # nargs None / int 1 → exactly one, required
    return ArgSpec(
        name=action.dest,
        description=action.help,
        default=_safe(action.default),
        choices=_choices(action),
        arg_kind=kind,
        metavar=action.metavar,
    )


def _subcommands(sub_action: argparse._SubParsersAction, parent_invocation: str) -> List[CommandSpec]:
    """Recurse into a subparsers action, resolving canonical names + aliases.

    ``_name_parser_map`` maps every name (canonical AND alias) to its parser, so
    two names sharing one parser object identify an alias set. ``_choices_actions``
    gives the canonical name (its ``.dest``) and help text, in declaration order.
    """
    name_map = sub_action._name_parser_map  # name -> parser (incl. aliases)
    names_by_parser: dict[int, List[str]] = {}
    for nm, p in name_map.items():
        names_by_parser.setdefault(id(p), []).append(nm)

    commands: List[CommandSpec] = []
    seen: set[int] = set()
    for choice in sub_action._choices_actions:
        canonical = choice.dest
        parser = name_map.get(canonical)
        if parser is None or id(parser) in seen:
            continue
        seen.add(id(parser))
        aliases = [nm for nm in names_by_parser.get(id(parser), []) if nm != canonical]
        commands.append(
            walk_parser(
                parser,
                name=canonical,
                invocation=f"{parent_invocation} {canonical}",
                aliases=aliases,
                description=choice.help,
            )
        )
    return commands


def walk_parser(
    parser: argparse.ArgumentParser,
    *,
    name: str,
    invocation: str,
    aliases: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> CommandSpec:
    """Read a built parser into a CommandSpec (recursing through subparsers)."""
    options: List[OptionSpec] = []
    positionals: List[ArgSpec] = []
    subcommands: List[CommandSpec] = []

    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            subcommands = _subcommands(action, invocation)
            continue
        if action.option_strings:
            options.append(_option_spec(action))
        else:
            positionals.append(_positional_spec(action))

    return CommandSpec(
        name=name,
        invocation=invocation,
        aliases=aliases or [],
        description=description if description is not None else parser.description,
        options=options,
        positionals=positionals,
        subcommands=subcommands,
    )

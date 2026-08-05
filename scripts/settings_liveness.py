#!/usr/bin/env python3
"""
scripts/settings_liveness.py
=============================
Static per-key **liveness classifier** for ``settings.py``'s fields.

Answers exactly one question, per field: *if I ``setattr`` this on the live
``settings`` singleton right now, does the running process actually observe
the new value?*

Emits, for every ``Settings.model_fields`` name, one of:

``live_safe``
    Every attributable read is a *fresh* read — evaluated each time the value
    is needed. A live ``setattr`` takes effect immediately.
``restart_required``
    At least one read *captures* the value (import-time constant, constructor
    field, decorator argument, memoized call, ``os.environ`` read, ...). The
    report names every capturing site and the rule that fired.
``no_op``
    Zero production reads **and** the field's own name never appears as a
    string literal anywhere in production code. Setting it does nothing, ever.
    This is a deliberately narrow bucket — see "Name-literal fallback" below.

Why the distinction matters
---------------------------
Both misclassification directions are harmful, in different ways:

* Reporting a **captured** field as ``live_safe`` means an operator flips a
  toggle, the UI says it applied, and the running process keeps using the old
  value with no way to tell. That is the dangerous direction.
* Reporting a **live-safe** field as ``restart_required`` makes the whole
  runtime-settings feature look untrustworthy and discourages its use.

Where the two conflict this module is **fail-closed**: it prefers a false
``restart_required`` over a false ``live_safe``.

Fail-closed by construction
---------------------------
1. A parse failure on any scanned file aborts the whole run
   (``UnresolvedAnalysis``) rather than silently emitting a partial answer.
2. Any *snapshot* of the settings object (``settings.model_dump()``,
   ``dict(settings)``, ``copy.deepcopy(settings)``, ...) aborts the run: a
   snapshot detaches every field at once, so no per-key answer would be
   trustworthy.
3. A **dynamic** ``getattr(settings, <variable>)`` read that itself sits in a
   capture context is unattributable to any single key, so it marks *every*
   key captured. (This is blunt on purpose. If it ever fires, the JSON
   reports the offending sites under ``poisoned_dynamic_sites`` so the cause
   is visible rather than mysterious.)

Per-file problems never raise (CLAUDE.md CONSTRAINT #6 governs runtime
degradation); whole-run problems do, because a partial liveness answer is
worse than none.

Name-literal fallback (why ``no_op`` is narrow)
-----------------------------------------------
Several fields are read only through a *name-driven dispatcher* —
``getattr(settings, key, default)`` where ``key`` arrives as a variable
(``gui/panels/settings_manager.py``'s ``_SETTINGS_LAYOUT`` walk,
``api/pilots_api.py``'s settings-echo helpers, ...). No static analysis can
attribute those reads to a field. Calling such a field ``no_op`` would be a
lie of a particularly bad kind: "this knob does nothing" when in fact it does.

So a field with zero attributable reads whose *name* appears as an exact
string constant somewhere in production code is classified
``restart_required`` with the rule ``dynamic_name_literal_unattributable``
and the literal sites as evidence — i.e. "reached, but by a path this
analysis cannot prove is fresh". That is the conservative direction, and it
is flagged in ``caveats`` so a reader knows those entries are *bounded*, not
*measured*. ``settings.py`` and ``gui/env_io.py`` are excluded from the
literal scan (a field's own definition and the GUI allowlist name every key
by construction and would make the signal useless) — matching
``scripts/measure_settings_census.py``'s identical exclusion.

Guard/dependency factories (read form 4)
----------------------------------------
The opposite case. ``api/auth.py::make_command_token_guard(name)`` and
``api/data_api.py::require_ai_capability_enabled(flag_name)`` each take a
field name as a parameter and return a *nested* function that does the
``getattr`` at call time. The factory call passes a string constant, so the
key IS statically knowable even though the read site is dynamic. Those calls
are recorded as ``factory_param`` reads, carrying the capture rules computed
at the *inner* function's own read site (so an ``@lru_cache``'d inner
function still classifies captured). Without this rule the command-token
fields would look unread — and the classifier would report a live bearer
token as ``no_op``.

Usage
-----
::

    .venv/bin/python3 scripts/settings_liveness.py            # print JSON
    .venv/bin/python3 scripts/settings_liveness.py --write    # + docs/settings_liveness.json

The committed artifact is ``docs/settings_liveness.json``; a CI drift test
(``tests/test_settings_liveness.py``) re-runs this classifier and asserts the
live output matches it field-for-field, so a change in classification
behaviour fails loudly instead of letting the committed file go stale.

This module imports nothing outside the stdlib at module scope and performs
no I/O on import, so ``tests/test_settings_liveness.py`` can import it and
run it against synthetic fixture trees. ``bootstrap()`` is therefore called
inside ``if __name__ == "__main__":`` (matching ``scripts/snapshot_diff.py``),
never at module top.
"""

from __future__ import annotations

import argparse
import ast
import collections
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional

# Repo-root sys.path shim (stdlib-only; see module docstring for why the
# bootstrap() call itself lives under __main__ rather than here).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

JSON_OUT_REL = os.path.join("docs", "settings_liveness.json")

# ---------------------------------------------------------------------------
# File-set definition — deliberately identical to
# scripts/measure_settings_census.py's, so the two measurements' read-form
# counts are comparable without a file-set caveat.
# ---------------------------------------------------------------------------
SKIP_DIRS = {
    ".venv",
    ".git",
    "node_modules",
    "tests",
    "webapp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "output",
    "cache",
    ".claude",
    "build",
    "dist",
    ".ipynb_checkpoints",
}
SKIP_FILE_PATTERNS = (
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r"^conftest\.py$"),
)
# A field's own definition site and the GUI write-allowlist name every key by
# construction; counting those as "the name appears as a literal" would make
# the dynamic-dispatch signal fire for all 320 fields and mean nothing.
LITERAL_SCAN_SKIP = {"settings.py", os.path.join("gui", "env_io.py")}

INIT_METHODS = {"__init__", "__post_init__", "__new__"}
COERCIONS = {"int", "float", "bool", "str", "list", "dict", "set", "tuple"}
# Decorators that make a nested function outlive its defining call.
REGISTRARS = {"listens_for", "register", "on_event", "setter"}
MEMOIZERS = {"lru_cache", "cache", "cached_property"}
# How many call hops out from a constructor still count as "this read lands
# in a long-lived object built by an __init__".
INDIRECT_DEPTH = 3
SNAPSHOT_METHODS = ("model_dump", "model_dump_json", "model_copy", "dict", "copy")


def load_model_fields() -> frozenset[str]:
    """Import the real ``Settings`` model and return its field names.

    Deliberately a function, not a module-level constant: importing pydantic
    at module scope would run *before* ``bootstrap()``'s venv re-exec (see
    module docstring) and would make this module un-importable under a bare
    system interpreter.
    """
    from settings import Settings  # noqa: PLC0415 - see docstring

    return frozenset(Settings.model_fields)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class Read:
    """One statically-attributable read of one settings field."""

    key: str
    form: str  # attr | getattr_const | factory_param | os_environ
    file: str
    line: int
    rules: list[str] = field(default_factory=list)  # empty => fresh read
    via: list[int] = field(default_factory=list)  # provenance call lines
    discarded: bool = False  # import-time read whose value is never bound

    @property
    def site(self) -> str:
        return f"{self.file}:{self.line}"


class UnresolvedAnalysis(RuntimeError):
    """Raised when the whole run cannot be trusted (parse failure, snapshot)."""


# ---------------------------------------------------------------------------
# Per-module analysis
# ---------------------------------------------------------------------------
class ModuleAnalyzer:
    def __init__(self, rel: str, tree: ast.Module, model_fields: frozenset[str]) -> None:
        self.rel = rel
        self.tree = tree
        self.model_fields = model_fields
        self.parent: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parent[id(child)] = node

        # --- regions evaluated at *def* time, not at call time -------------
        self.default_ids: set[int] = set()
        self.decorator_ids: set[int] = set()
        self.default_owner: dict[int, ast.AST] = {}
        self.decorator_owner: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
                for d in defaults:
                    for nid in _ids(d):
                        self.default_ids.add(nid)
                        self.default_owner[nid] = node
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for d in node.decorator_list:
                    for nid in _ids(d):
                        self.decorator_ids.add(nid)
                        self.decorator_owner[nid] = node

        # --- Pass 1: resolve the singleton's local name(s) -----------------
        # The census found the singleton bound under 18 distinct local names
        # across this tree; grepping `settings\.` is not viable, aliases must
        # be resolved via AST. ast.walk (not tree.body) is deliberate: many
        # of those aliases come from function-local imports.
        self.singleton_names: set[str] = set()
        self.module_names: set[str] = set()
        self.imported: dict[str, tuple[str, str]] = {}  # local -> (module, orig)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "settings":
                for a in node.names:
                    if a.name == "settings":
                        self.singleton_names.add(a.asname or "settings")
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    self.imported[a.asname or a.name] = (node.module, a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "settings":
                        self.module_names.add(a.asname or "settings")

        # --- function inventory + intra-module call graph ------------------
        self.class_of: dict[int, ast.ClassDef] = {}
        self.key_of: dict[int, str] = {}
        self.func_by_key: dict[str, ast.AST] = {}
        self.class_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                self.class_names.add(node.name)
                for c in node.body:
                    if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        k = f"{node.name}.{c.name}"
                        self.class_of[id(c)] = node
                        self.key_of[id(c)] = k
                        self.func_by_key[k] = c
        for c in tree.body:
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.key_of[id(c)] = c.name
                self.func_by_key[c.name] = c

        self.callees: dict[str, set[str]] = {}
        self.call_line: dict[tuple[str, str], int] = {}
        for k, fn in self.func_by_key.items():
            cls = self.class_of.get(id(fn))
            out: set[str] = set()
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                target = self._resolve_callee(n.func, cls)
                if target:
                    out.add(target)
                    self.call_line.setdefault((k, target), n.lineno)
            self.callees[k] = out

        # --- BFS from every constructor, depth <= INDIRECT_DEPTH -----------
        # A naive "only reads directly inside __init__ capture" rule would
        # miss data/market_data.py's CompositeProvider.__init__ calling
        # self._select_quote_provider(), whose body reads MARKET_DATA_PROVIDER
        # into a long-lived attribute. That is the single most important
        # regression case in this classifier — getting it wrong reports a
        # captured provider selection as live-safe.
        self.depth: dict[str, int] = {}
        self.provenance: dict[str, list[int]] = {}
        frontier = [k for k, fn in self.func_by_key.items() if fn.name in INIT_METHODS]
        seen = set(frontier)
        d = 0
        while frontier and d < INDIRECT_DEPTH:
            d += 1
            nxt: list[str] = []
            for k in frontier:
                for callee in self.callees.get(k, ()):
                    if callee in seen:
                        continue
                    seen.add(callee)
                    self.depth[callee] = d
                    line = self.call_line.get((k, callee))
                    self.provenance[callee] = self.provenance.get(k, []) + ([line] if line else [])
                    nxt.append(callee)
            frontier = nxt

        # --- guard-factory params (read form 4) ----------------------------
        # outer function name -> (param name, positional index, inner rules)
        self.factories: dict[str, tuple[str, int, list[str]]] = {}
        for outer in ast.walk(tree):
            if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in outer.args.args] + [a.arg for a in outer.args.kwonlyargs]
            for n in ast.walk(outer):
                if not self._is_dynamic_getattr(n):
                    continue
                if not (isinstance(n.args[1], ast.Name) and n.args[1].id in params):
                    continue
                inner = self._innermost_function(n)
                if inner is not None and inner is not outer:
                    rules, _via = self.classify(n)
                    self.factories[outer.name] = (
                        n.args[1].id,
                        params.index(n.args[1].id),
                        rules,
                    )

    # -- helpers ---------------------------------------------------------
    def _resolve_callee(self, fx: ast.AST, cls: Optional[ast.ClassDef]) -> Optional[str]:
        if (
            isinstance(fx, ast.Attribute)
            and isinstance(fx.value, ast.Name)
            and fx.value.id in ("self", "cls")
            and cls is not None
        ):
            cand = f"{cls.name}.{fx.attr}"
            return cand if cand in self.func_by_key else None
        if isinstance(fx, ast.Name):
            if fx.id in self.func_by_key:
                return fx.id
            if fx.id in self.class_names and f"{fx.id}.__init__" in self.func_by_key:
                return f"{fx.id}.__init__"
        return None

    def _ancestors(self, node: ast.AST) -> Iterator[ast.AST]:
        cur = node
        while True:
            p = self.parent.get(id(cur))
            if p is None:
                return
            yield p
            cur = p

    def _innermost_function(self, node: ast.AST) -> Optional[ast.AST]:
        for p in self._ancestors(node):
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return p
        return None

    def scope_of(self, node: ast.AST) -> tuple[str, ast.AST]:
        """Innermost enclosing FunctionDef -- NOT 'does this run at import'.

        A node sitting in a function's *default args* or *decorators* is
        evaluated in that function's ENCLOSING scope, so that function is
        skipped. Everything else resolves to the nearest FunctionDef /
        Lambda / ClassDef / Module. This is what keeps a nested function
        produced by a factory invoked at module level (e.g. api/auth.py's
        make_command_token_guard -> _guard, api/data_api.py's dependency
        factory -> _dependency) classified FRESH instead of failing every
        dynamic-getattr site closed -- which would in turn delete the ability
        to ever report a dynamically-keyed setting as live-safe.
        """
        skip: set[int] = set()
        if id(node) in self.default_ids:
            skip.add(id(self.default_owner[id(node)]))
        if id(node) in self.decorator_ids:
            skip.add(id(self.decorator_owner[id(node)]))
        for p in self._ancestors(node):
            if id(p) in skip:
                continue
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return ("func", p)
            if isinstance(p, ast.Lambda):
                return ("lambda", p)
            if isinstance(p, ast.ClassDef):
                return ("class", p)
            if isinstance(p, ast.Module):
                return ("module", p)
        return ("module", self.tree)

    def is_settings_expr(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id in self.singleton_names:
            return True
        # bare `import settings` -> settings.settings.KEY
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "settings"
            and isinstance(node.value, ast.Name)
            and node.value.id in self.module_names
        )

    def _is_dynamic_getattr(self, n: ast.AST) -> bool:
        return (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "getattr"
            and len(n.args) >= 2
            and self.is_settings_expr(n.args[0])
            and not isinstance(n.args[1], ast.Constant)
        )

    def direct_assign_targets(self, node: ast.AST) -> Optional[list[ast.AST]]:
        """Targets of an Assign whose RHS *is* this read.

        Strict on purpose: walking up N hops and accepting any enclosing
        Assign treats a keyword argument buried in a big call as 'bound to
        the target', which produces false positives for the closure-capture
        rule (a call like ``some_func(x=settings.FOO, y=other)`` is NOT a
        capture of FOO into any local name).
        """
        cur, p = node, self.parent.get(id(node))
        for _ in range(3):
            if p is None:
                return None
            if isinstance(p, ast.Assign) and p.value is cur:
                return p.targets
            if isinstance(p, ast.AnnAssign) and p.value is cur:
                return [p.target]
            if (
                isinstance(p, ast.Call)
                and isinstance(p.func, ast.Name)
                and p.func.id in COERCIONS
                and p.args
                and p.args[0] is cur
            ):
                cur, p = p, self.parent.get(id(p))
                continue
            if isinstance(p, (ast.BoolOp, ast.UnaryOp)):  # `settings.X or default`
                cur, p = p, self.parent.get(id(p))
                continue
            return None
        return None

    def enclosing_self_attr_assign(self, node: ast.AST) -> bool:
        """True when this read's value flows into a ``self.<attr>`` assignment.

        Deliberately NOT routed through :meth:`direct_assign_targets`, whose
        three-hop walk sees through only a fixed coercion whitelist plus
        ``BoolOp``/``UnaryOp``. That is right for the closure/global rules
        (over-matching there produces false positives), but far too narrow
        here: a read wrapped in arithmetic (``time.monotonic() + float(X)``),
        an f-string, a ternary, or a container literal would slip through and
        the value would still land in a long-lived attribute. This instead
        walks up to the nearest enclosing STATEMENT and asks whether that
        statement assigns to an attribute of ``self``/``cls`` — a question
        that has one right answer regardless of the expression shape in
        between.
        """
        for p in self._ancestors(node):
            if isinstance(p, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                if p.value is None or not any(n is node for n in ast.walk(p.value)):
                    return False
                targets = p.targets if isinstance(p, ast.Assign) else [p.target]
                return any(_is_self_attr(t) for t in targets)
            if isinstance(p, ast.stmt):
                return False  # some other statement encloses this read first
        return False

    def is_import_time_discard(self, node: ast.AST) -> bool:
        """True when this read's value is *tested and thrown away* rather than
        bound to anything -- ``if not settings.X: logger.warning(...)`` or
        ``assert settings.X``.

        Purely an ANNOTATION, never a reclassification. The ``module_level``
        rule is right that such a read is evaluated exactly once at import;
        but nothing retains the value, so the only thing a later ``setattr``
        fails to affect is whether that one startup warning already fired.
        This tree's API modules use the pattern heavily (``api/control_api.py``
        and ``api/pilots_api.py`` each warn at import when their command token
        is unset, while the actual auth check reads the token freshly per
        request), so without this flag those fields look captured for a
        reason a reviewer cannot see. Reported as ``discarded`` on the site
        and rolled up into ``restart_required_only_import_time_discard``.
        """
        cur = node
        for p in self._ancestors(node):
            if isinstance(p, (ast.BoolOp, ast.UnaryOp, ast.Compare)):
                cur = p
                continue
            if isinstance(p, ast.If) and p.test is cur:
                return True
            if isinstance(p, ast.Assert) and p.test is cur:
                return True
            return False
        return False

    def closure_escapes(self, scope: ast.AST, name: str) -> bool:
        """True iff a nested function closing over ``name`` outlives the call.

        Escape means: returned, assigned to an attribute/subscript (so it
        lives past this call's stack frame), or registered via a decorator
        like ``@event.listens_for`` / ``@X.register`` / ``@X.on_event`` /
        ``@property.setter``. A nested function merely being DEFINED inside a
        scope and referencing a local variable is NOT capture on its own --
        it has to actually outlive the call, or this rule produces false
        positives on ordinary per-call worker closures (e.g. a
        ThreadPoolExecutor worker defined inside a method, referencing a
        value computed earlier in that same method -- that closure dies with
        the call, it never "captures" anything for the process lifetime).
        """
        for nf in ast.walk(scope):
            if not isinstance(nf, (ast.FunctionDef, ast.AsyncFunctionDef)) or nf is scope:
                continue
            if not any(
                isinstance(x, ast.Name) and x.id == name and isinstance(x.ctx, ast.Load)
                for x in ast.walk(nf)
            ):
                continue
            for n2 in ast.walk(scope):
                if (
                    isinstance(n2, ast.Return)
                    and n2.value is not None
                    and _mentions(n2.value, nf.name)
                ):
                    return True
                if (
                    isinstance(n2, ast.Assign)
                    and any(isinstance(t, (ast.Attribute, ast.Subscript)) for t in n2.targets)
                    and _mentions(n2.value, nf.name)
                ):
                    return True
            for dec in nf.decorator_list:
                t = dec.func if isinstance(dec, ast.Call) else dec
                nm = t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", "")
                if nm in REGISTRARS:
                    return True
        return False

    # -- the capture-rule table -----------------------------------------
    def classify(self, node: ast.AST) -> tuple[list[str], list[int]]:
        kind, scope = self.scope_of(node)
        rules: list[str] = []
        via: list[int] = []

        if id(node) in self.default_ids:
            rules.append("default_arg")
        if id(node) in self.decorator_ids:
            rules.append("decorator_arg")

        if kind == "module":
            rules.append("module_level")
        elif kind == "class":
            rules.append("class_body")
            if _is_dataclass(scope):
                rules.append("frozen_dataclass_default")
        elif kind == "func":
            key = self.key_of.get(id(scope))
            self_assign = self.enclosing_self_attr_assign(node)
            if scope.name in INIT_METHODS:
                rules.append("init_self_assign" if self_assign else "init_body")
            else:
                if key and key in self.depth:
                    rules.append(f"indirect_init_helper_d{self.depth[key]}")
                    via = [ln for ln in self.provenance.get(key, []) if ln]
                if self_assign:
                    # POST-CONSTRUCTION capture: a regular method storing a
                    # setting into a long-lived instance attribute. Without
                    # this rule such a read has NO applicable rule at all
                    # (init_* needs __init__, indirect_* needs a constructor
                    # call chain, closure/global need a bare Name target) and
                    # would be reported live_safe -- the dangerous direction.
                    # Whether the attribute is actually refreshed depends on
                    # how often the method is called, which static analysis
                    # cannot know, so this fails closed.
                    rules.append("method_self_assign")

            # Not gated on `key`: a NESTED function (which has no key_of
            # entry, only module-level and class-level functions do) can be
            # @lru_cache'd just as easily as a top-level one, and its
            # memoized result outlives the call exactly the same way.
            if _is_memoized(scope):
                rules.append("memoized_singleton")

            declared_global = {
                g for x in ast.walk(scope) if isinstance(x, ast.Global) for g in x.names
            }
            targets = self.direct_assign_targets(node)
            for t in targets or ():
                if not isinstance(t, ast.Name):
                    continue
                if t.id in declared_global:
                    rules.append("global_assign")
                if self.closure_escapes(scope, t.id):
                    rules.append("closure_value")
        # kind == "lambda": body evaluates at call time -> fresh. This is
        # what makes gui/help_content.py's lazily-wrapped `lambda: f"...
        # {settings.KELLY_CAP}"` values classify fresh rather than captured.

        return sorted(set(rules)), via


def _ids(node: ast.AST) -> set[int]:
    return {id(n) for n in ast.walk(node)}


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(x, ast.Name) and x.id == name for x in ast.walk(node))


def _decorator_names(node: ast.AST) -> set[str]:
    out = set()
    for dec in getattr(node, "decorator_list", ()):
        t = dec.func if isinstance(dec, ast.Call) else dec
        out.add(t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", ""))
    return out


def _is_self_attr(node: ast.AST) -> bool:
    """True for ``self.x`` / ``cls.x``, or a tuple/list target containing one."""
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_is_self_attr(e) for e in node.elts)
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in ("self", "cls")
    )


def _is_dataclass(node: ast.AST) -> bool:
    return "dataclass" in _decorator_names(node)


def _is_memoized(node: ast.AST) -> bool:
    return bool(_decorator_names(node) & MEMOIZERS)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def iter_source_files(root: str) -> Iterator[str]:
    """Yield repo-relative paths of every production ``*.py`` file under
    ``root``. File-set definition is identical to the census generator's."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            if any(pat.match(fn) for pat in SKIP_FILE_PATTERNS):
                continue
            yield os.path.relpath(os.path.join(dirpath, fn), root)


@dataclass
class Analysis:
    reads: list[Read]
    dynamic: list[dict]
    name_literal_sites: dict[str, list[str]]
    files_scanned: int
    snapshots: list[dict]


def analyze(
    root: str,
    model_fields: frozenset[str],
    files: Optional[Iterable[str]] = None,
    *,
    literal_scan_skip: Optional[set[str]] = None,
) -> Analysis:
    """Parse every production file under ``root`` and collect settings reads.

    ``files`` (repo-relative paths) overrides the default walk — used by the
    fixture tests to run this classifier against a synthetic tree.
    """
    if literal_scan_skip is None:
        literal_scan_skip = LITERAL_SCAN_SKIP
    rels = sorted(files) if files is not None else sorted(iter_source_files(root))

    reads: list[Read] = []
    dynamic: list[dict] = []
    snapshots: list[dict] = []
    name_literal_sites: dict[str, list[str]] = collections.defaultdict(list)
    # module-level functions that read settings, for cross-module indirect capture
    fresh_module_funcs: dict[tuple[str, str], set[str]] = {}
    analyzers: dict[str, ModuleAnalyzer] = {}

    for rel in rels:
        path = os.path.join(root, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            tree = ast.parse(source, filename=path)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            # Whole-run failure, not a per-file one: a file this analysis
            # could not read might contain the one capturing read that
            # changes a key's answer, so a partial result would be a lie.
            raise UnresolvedAnalysis(f"{rel}: {exc}") from exc
        analyzers[rel] = ModuleAnalyzer(rel, tree, model_fields)

    # pass A: inventory of module-level functions that touch settings
    for rel, ma in analyzers.items():
        mod = rel[:-3].replace(os.sep, ".")
        for fn in ma.tree.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            keys = {k for k, _ in _keys_in(ma, fn)}
            if keys:
                fresh_module_funcs[(mod, fn.name)] = keys

    # pass B: the reads themselves
    for rel, ma in analyzers.items():
        scan_literals = rel not in literal_scan_skip
        for node in ast.walk(ma.tree):
            # fail-closed: any snapshot of the settings object
            if isinstance(node, ast.Call):
                fx = node.func
                if (
                    isinstance(fx, ast.Attribute)
                    and ma.is_settings_expr(fx.value)
                    and fx.attr in SNAPSHOT_METHODS
                ):
                    snapshots.append({"file": rel, "line": node.lineno, "how": fx.attr})
                if (
                    isinstance(fx, ast.Name)
                    and fx.id == "dict"
                    and node.args
                    and ma.is_settings_expr(node.args[0])
                ):
                    snapshots.append({"file": rel, "line": node.lineno, "how": "dict()"})
                if (
                    isinstance(fx, ast.Attribute)
                    and fx.attr == "deepcopy"
                    and node.args
                    and ma.is_settings_expr(node.args[0])
                ):
                    snapshots.append({"file": rel, "line": node.lineno, "how": "deepcopy"})

            # forms 1 + 2
            for key, form in _keys_in_node(ma, node):
                rules, via = ma.classify(node)
                discarded = rules == ["module_level"] and ma.is_import_time_discard(node)
                reads.append(Read(key, form, rel, node.lineno, rules, via, discarded))

            # form 3 -- dynamic (unattributable to any single key)
            if ma._is_dynamic_getattr(node):
                rules, _ = ma.classify(node)
                dynamic.append(
                    {
                        "file": rel,
                        "line": node.lineno,
                        "expr": _unparse(node),
                        "rules": rules,
                        # A dynamic read whose key is a guard/dependency
                        # factory's own parameter is NOT unattributable -- form
                        # 4 below resolves it from each caller's string
                        # constant, and carries THESE rules onto that Read. It
                        # must therefore not ALSO poison every key: that would
                        # double-count the one fact it already reported, and
                        # would collapse the whole report over a capture the
                        # analysis actually understood.
                        "resolved_by_factory": _factory_owning(ma, node),
                    }
                )

            # form 4 -- constant string into a guard/dependency factory
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ma.factories
            ):
                pname, pidx, inner_rules = ma.factories[node.func.id]
                val = None
                if len(node.args) > pidx and isinstance(node.args[pidx], ast.Constant):
                    val = node.args[pidx].value
                for kw in node.keywords:
                    if kw.arg == pname and isinstance(kw.value, ast.Constant):
                        val = kw.value.value
                if isinstance(val, str) and val in model_fields:
                    reads.append(
                        Read(val, "factory_param", rel, node.lineno, list(inner_rules), [])
                    )

            # os.environ read of a model_fields name -- can NEVER observe a
            # setattr on the singleton (pydantic-settings loads .env into the
            # model, not into the real os.environ).
            env_key = _environ_key(node)
            if env_key and env_key in model_fields:
                reads.append(Read(env_key, "os_environ", rel, node.lineno, ["os_environ"], []))

            # cross-module indirect capture: __init__ calling an imported helper
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                src = ma.imported.get(node.func.id)
                if src and src in fresh_module_funcs and _in_init(ma, node):
                    for k in fresh_module_funcs[src]:
                        reads.append(
                            Read(k, "attr", rel, node.lineno, ["cross_module_init_helper"], [])
                        )

            # exact-match name literals, for the dynamic-dispatch fallback
            if (
                scan_literals
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in model_fields
            ):
                name_literal_sites[node.value].append(f"{rel}:{node.lineno}")

    if snapshots:
        raise UnresolvedAnalysis(
            "settings snapshot site(s) found -- a snapshot detaches every field "
            f"at once, so no per-key answer is trustworthy: {snapshots}"
        )
    return Analysis(
        reads=reads,
        dynamic=dynamic,
        name_literal_sites=dict(name_literal_sites),
        files_scanned=len(rels),
        snapshots=snapshots,
    )


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return "<unparseable>"


def _factory_owning(ma: ModuleAnalyzer, node: ast.Call) -> Optional[str]:
    """Name of the guard/dependency factory whose parameter keys this dynamic
    ``getattr``, or ``None`` if this read is genuinely unattributable."""
    key_arg = node.args[1]
    if not isinstance(key_arg, ast.Name):
        return None
    for outer_name, (pname, _idx, _rules) in ma.factories.items():
        if pname != key_arg.id:
            continue
        fn = ma.func_by_key.get(outer_name)
        if fn is not None and any(n is node for n in ast.walk(fn)):
            return outer_name
    return None


def _keys_in_node(ma: ModuleAnalyzer, node: ast.AST) -> list[tuple[str, str]]:
    out = []
    if (
        isinstance(node, ast.Attribute)
        and ma.is_settings_expr(node.value)
        and node.attr in ma.model_fields
    ):
        out.append((node.attr, "attr"))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and ma.is_settings_expr(node.args[0])
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in ma.model_fields
    ):
        out.append((node.args[1].value, "getattr_const"))
    return out


def _keys_in(ma: ModuleAnalyzer, scope: ast.AST) -> list[tuple[str, str]]:
    out = []
    for n in ast.walk(scope):
        out.extend(_keys_in_node(ma, n))
    return out


def _in_init(ma: ModuleAnalyzer, node: ast.AST) -> bool:
    fn = ma._innermost_function(node)
    return fn is not None and fn.name in INIT_METHODS


def _environ_key(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Call):
        fx = node.func
        if (
            isinstance(fx, ast.Attribute)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            if fx.attr == "getenv":
                return node.args[0].value
            if (
                fx.attr == "get"
                and isinstance(fx.value, ast.Attribute)
                and fx.value.attr == "environ"
            ):
                return node.args[0].value
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "environ"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        # Load context only. ``os.environ["X"] = v`` and ``del os.environ["X"]``
        # are WRITES to the process environment, not reads of a setting;
        # counting them inflates the os_environ form and can wrongly report a
        # field restart_required on the strength of a test harness that only
        # ever SET the variable. (Gravity AI Review Suite.py's execution-mode
        # audit does exactly this for ROBINHOOD_EXECUTION_MODE.)
        and isinstance(node.ctx, ast.Load)
    ):
        return node.slice.value
    return None


def partition(analysis: Analysis, model_fields: frozenset[str]) -> dict:
    """Fold per-read results into the three-way per-field partition."""
    # A dynamic read sitting in a CAPTURE context is unattributable to any
    # single key AND captures whatever it read -- so it fails every key
    # closed. Blunt on purpose; reported explicitly so it is never silent.
    # Factory-parameter reads are excluded: those ARE attributable (form 4
    # resolves each caller's string constant and carries these same rules
    # onto that Read), so poisoning would double-count one known fact.
    poisoned = [
        d for d in analysis.dynamic if d["rules"] and not d.get("resolved_by_factory")
    ]

    by_key: dict[str, list[Read]] = collections.defaultdict(list)
    for r in analysis.reads:
        by_key[r.key].append(r)

    live: list[str] = []
    restart: dict[str, list[dict]] = {}
    no_op: list[str] = []
    dynamic_only: list[str] = []
    discard_only: list[str] = []

    poison_sites = [
        {
            "site": f"{d['file']}:{d['line']}",
            "rules": ["dynamic_in_capture_context"],
            "via": [],
            "form": "getattr_dynamic",
            "discarded": False,
        }
        for d in poisoned
    ]

    for key in sorted(model_fields):
        rs = by_key.get(key)
        if not rs:
            literals = sorted(analysis.name_literal_sites.get(key, []))
            if literals:
                # Reached, but only through a name-driven dispatcher this
                # analysis cannot attribute. Fail closed -- see module
                # docstring's "Name-literal fallback".
                dynamic_only.append(key)
                restart[key] = [
                    {
                        "site": s,
                        "rules": ["dynamic_name_literal_unattributable"],
                        "via": [],
                        "form": "name_literal",
                        "discarded": False,
                    }
                    for s in literals
                ] + poison_sites
            elif poisoned:
                # No attributable read AND an unattributable capturing read
                # exists somewhere: "flipping this does nothing, ever" is not
                # a claim this analysis can make. Fail closed rather than
                # report a possibly-captured field as no_op.
                restart[key] = list(poison_sites)
            else:
                no_op.append(key)
            continue

        caps = sorted(
            (r for r in rs if r.rules), key=lambda r: (r.file, r.line, ",".join(r.rules))
        )
        if caps or poisoned:
            if caps and not poisoned and all(r.discarded for r in caps):
                discard_only.append(key)
            restart[key] = [
                {
                    "site": r.site,
                    "rules": r.rules,
                    "via": r.via,
                    "form": r.form,
                    "discarded": r.discarded,
                }
                for r in caps
            ] + poison_sites
        else:
            live.append(key)

    return {
        "live_safe": live,
        "restart_required": restart,
        "no_op": no_op,
        "counts": {
            "live_safe": len(live),
            "restart_required": len(restart),
            "no_op": len(no_op),
            "total": len(model_fields),
        },
        "restart_required_reason_counts": dict(
            sorted(
                collections.Counter(
                    rule for sites in restart.values() for s in sites for rule in s["rules"]
                ).items()
            )
        ),
        "restart_required_via_name_literal_only": sorted(dynamic_only),
        "restart_required_only_import_time_discard": sorted(discard_only),
        "poisoned_dynamic_sites": poisoned,
    }


CAVEATS = [
    "restart_required_via_name_literal_only lists fields with ZERO statically "
    "attributable reads whose name appears as an exact string constant in "
    "production code (a name-driven getattr dispatcher). They are placed in "
    "restart_required as the conservative answer; this is a BOUND, not a "
    "measurement -- some of them are probably live-safe.",
    "os_environ reads can never observe a setattr on the settings singleton "
    "(pydantic-settings loads .env into the model, not into the real "
    "os.environ). A field with BOTH a fresh settings read and an os.environ "
    "read is reported restart_required, because an operator cannot tell which "
    "path is authoritative for a given consumer.",
    "The guard/dependency-factory rule (form factory_param) resolves only "
    "SAME-MODULE calls. Both real instances in this tree (api/auth.py's "
    "make_command_token_guard, api/data_api.py's require_ai_capability_enabled) "
    "are called from their own module, so nothing is currently missed -- but a "
    "future cross-module factory call would go unattributed.",
    "A dynamic getattr keyed by a guard/dependency factory's own parameter is "
    "resolved through form factory_param and is deliberately excluded from the "
    "poison-everything rule. Residual gap: a call to such a factory passing a "
    "NON-constant key would go unattributed AND no longer poison. No call site "
    "in this tree does that -- all four resolve to string constants.",
    "cross_module_init_helper attributes EVERY settings key read anywhere "
    "inside an imported helper function to the __init__ that calls it. Right "
    "for db_config.py's small single-purpose engine builders; it would "
    "over-capture for a large generic helper whose caller only cared about one "
    "of its reads.",
    "restart_required_only_import_time_discard lists fields whose ONLY "
    "capture site is a module-level read that tests the value and throws it "
    "away (a startup 'not configured' warning). They are almost certainly "
    "live-safe in every path that matters; the only thing a setattr cannot "
    "un-do is that one already-emitted log line. Reported conservatively "
    "because module_level is, strictly, a once-per-process evaluation.",
    "The indirect-capture BFS follows CALL edges only. A read reached from an "
    "__init__ via a PROPERTY accessed as an attribute (self.some_property) is "
    "not traced. That is deliberate: the one real instance in this tree "
    "(data/market_data.py's _log_startup_banner reading "
    "_effective_quote_provider) merely logs the value, and the property is "
    "re-evaluated on every later access, so tracing it would wrongly mark the "
    "FMP_*_ENABLED gates restart_required. A future 'self._x = "
    "self.some_property' inside an __init__ would be a genuine miss.",
    "The name-literal fallback that keeps no_op honest matches an EXACT "
    "string constant only. A dispatch key BUILT at runtime -- "
    "getattr(settings, f'FMP_{name}_ENABLED') or a concatenation -- would be "
    "invisible to both the read pass and the fallback, and such a field would "
    "land in no_op ('does nothing, ever') while actually being read. No call "
    "site in this tree currently builds a key that way; if one is added, "
    "no_op stops being trustworthy until this rule is extended.",
    "The constructor BFS stops at INDIRECT_DEPTH (3) call hops. A capturing "
    "read reached from an __init__ via a 4th-hop helper gets no "
    "indirect_init_helper rule. Relatedly, _resolve_callee only resolves "
    "self.method() to a method defined directly on the caller's own class -- a "
    "helper INHERITED from a base class and called from a subclass __init__ is "
    "not traced. Neither gap has a confirmed instance in this tree.",
    "This is a STATIC analysis of read SITES. It cannot see reflection, "
    "importlib-driven dispatch, or a value copied into another long-lived "
    "object by code that never names the settings field.",
]


def build_report(
    root: str,
    model_fields: Optional[frozenset[str]] = None,
    *,
    analysis: Optional[Analysis] = None,
) -> dict:
    if model_fields is None:
        model_fields = load_model_fields()
    if analysis is None:
        analysis = analyze(root, model_fields)
    result = partition(analysis, model_fields)
    result["status"] = "ok"
    result["generator"] = "scripts/settings_liveness.py"
    result["regenerate"] = "python3 scripts/settings_liveness.py --write"
    result["files_scanned"] = analysis.files_scanned
    result["read_forms"] = dict(
        sorted(collections.Counter(r.form for r in analysis.reads).items())
    )
    result["read_forms_distinct_fields"] = dict(
        sorted(
            (form, len({r.key for r in analysis.reads if r.form == form}))
            for form in {r.form for r in analysis.reads}
        )
    )
    result["dynamic_sites"] = sorted(
        analysis.dynamic, key=lambda d: (d["file"], d["line"])
    )
    result["caveats"] = CAVEATS
    return result


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    ap.add_argument(
        "--write",
        action="store_true",
        help=f"regenerate {JSON_OUT_REL} in place (default: print to stdout)",
    )
    args = ap.parse_args(argv)

    try:
        report = build_report(_REPO_ROOT)
    except UnresolvedAnalysis as exc:
        print(json.dumps({"status": "unresolved", "reason": str(exc)}, indent=2))
        return 1

    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.write:
        out = os.path.join(_REPO_ROOT, JSON_OUT_REL)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        c = report["counts"]
        print(
            f"wrote {JSON_OUT_REL}: {c['live_safe']} live_safe / "
            f"{c['restart_required']} restart_required / {c['no_op']} no_op "
            f"of {c['total']} fields ({report['files_scanned']} files scanned)"
        )
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    # Venv re-exec + .env loading -- placed here (not at module top) because
    # tests/test_settings_liveness.py imports this module as a library to run
    # the classifier against synthetic fixture trees; a module-top call would
    # fire the re-exec check (and, in the wrong environment, spawn a
    # subprocess and sys.exit()) on every such import, not just when this file
    # is the actual CLI entry point. See scripts/_bootstrap.py's module
    # docstring for the full rationale.
    from scripts._bootstrap import bootstrap

    bootstrap()
    raise SystemExit(main())

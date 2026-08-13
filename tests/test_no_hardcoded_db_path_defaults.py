"""
tests/test_no_hardcoded_db_path_defaults.py
=============================================
Static/AST-based regression guard against a hardcoded, CWD-relative SQLite
path bypassing ``db_config.resolve_database_url()`` -- the single seam every
DB-backed store in this codebase (``data/historical_store.py``,
``data/paper_account_store.py``, ``transactions_store.py``,
``sizing/cap_audit_store.py``, ``execution/live_trade_proposals_store.py``,
``desktop/run_history_store.py``, ...) is supposed to resolve its SQLite
file path through.

Real incident this guards against
----------------------------------
``settings.LOCAL_DATA_ROOT`` (PR #718) made ``data/historical_store.py``
route its default DB path through ``db_config.resolve_database_url()``
instead of a bare, CWD-relative ``"quant_platform.db"`` literal -- an
external, machine-global root shared across every git worktree on the
machine. Right after that shipped, ``forecasting/forecast_tracker.py``'s
``ForecastTracker.__init__(self, db_path: str = "quant_platform.db", ...)``
was found to have kept the OLD hardcoded-literal-default shape -- the exact
class of bug ``LOCAL_DATA_ROOT`` was introduced to close, in the one module
that didn't get the memo. Because ``main_orchestrator.py``'s
``EngineContext.build()`` constructs a bare ``ForecastTracker()`` every
cycle, this silently split ~2,000,000 real ``forecast_errors`` rows between
two live databases (the shared ``LOCAL_DATA_ROOT`` one every other store
used, and a stray CWD-relative file that only this one class still wrote
to) for hours, undetected by any test. Fixed in PR #720
(``forecasting/forecast_tracker.py``'s ``db_path`` default is now ``None``,
resolved via ``db_config.resolve_database_url()`` when omitted -- see
``tests/test_forecast_tracker.py::TestDefaultDbPathResolvesThroughDbConfig``
for that fix's own regression coverage).

Both bugs had the identical AST shape: a bare string-literal default
(``"quant_platform.db"``) on a ``db_path``-ish constructor parameter,
completely bypassing ``db_config``. This file is the static guard that
catches that shape anywhere else in the production tree, and keeps catching
it if it's ever reintroduced.

Style / scoping convention mirrored from
------------------------------------------
``tests/test_scripts_bootstrap.py`` (parse each file's source with ``ast``,
walk the tree, check for a specific pattern -- no need to import or execute
any scanned module, several of which have heavy/network-adjacent/argparse-
driven top-level code that isn't safe to import in a test process) and
``scripts/measure_settings_census.py`` (the exact ``_SKIP_DIRS`` /
``_SKIP_FILE_PATTERNS`` "production tree" scoping, mirrored verbatim below
rather than re-invented -- see that script's own comment for why each
directory is excluded).

Coverage
--------
TestNoHardcodedDbPathDefaults  -- the real scan: every production .py file,
                                   both detector patterns, filtered through
                                   the ALLOWLIST below.
TestDetectorLogic               -- pure-logic unit tests proving the two
                                   detector functions actually catch the real
                                   bug shape (and don't false-positive on the
                                   legitimate ``db_config``-resolved shape),
                                   run against hand-constructed source-string
                                   fixtures rather than scanning real files.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# ============================================================================
# "Production tree" scoping -- mirrored VERBATIM from
# scripts/measure_settings_census.py's _SKIP_DIRS / _SKIP_FILE_PATTERNS
# (see that script's own comment for the rationale of each entry: tests/
# webapp/.venv/node_modules are excluded by this task's definition of
# "production code"; the rest are build/output/cache artifacts containing no
# hand-written source; .claude/.gemini each nest a worktrees/ subdirectory
# holding OTHER agents' full checkouts on possibly-different branches).
# ============================================================================
_SKIP_DIRS = {
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
    ".gemini",
    "build",
    "dist",
    ".ipynb_checkpoints",
}

_SKIP_FILE_PATTERNS = (
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r"^conftest\.py$"),
)


def _production_py_files() -> list[Path]:
    out: list[Path] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        rel_parts = path.relative_to(REPO_ROOT).parts
        if any(part in _SKIP_DIRS for part in rel_parts[:-1]):
            continue
        if rel_parts and rel_parts[0] in _SKIP_DIRS:
            continue
        if any(pat.match(path.name) for pat in _SKIP_FILE_PATTERNS):
            continue
        out.append(path)
    return out


_PRODUCTION_FILES = _production_py_files()


# ============================================================================
# Detector
# ============================================================================

# Case-insensitive substring match against the parameter name. Deliberately
# NOT a bare "db" substring (would false-positive on e.g. "db_config",
# "adobe", "db_pool_size") -- these four are the exact shapes named in the
# real incidents (ForecastTracker's `db_path`, HistoricalStore's `db_path`)
# plus the two sibling shapes ("database", "db_file") every other store in
# this codebase's __init__ signatures could plausibly use, plus
# "sqlite_path" for a hypothetical sqlite-specific name.
_DB_PARAM_NAME_RE = re.compile(r"(db_path|database|db_file|sqlite_path)", re.IGNORECASE)

# Excludes a name that matches the pattern above but is structurally NOT a
# path/filename parameter -- a boolean flag or backend discriminator, e.g.
# `is_database_ready`, `database_enabled`, `database_type`, `db_backend`.
# Tight on purpose per the task brief ("keep it tight enough not to
# false-positive on unrelated params like is_database_something"): only
# fires on the specific is_/has_ prefix and _enabled/_type/_backend/_kind/
# _mode suffix shapes, not on a bare substring anywhere in the name.
_DB_PARAM_EXCLUDE_RE = re.compile(
    r"^(is|has)_.*|.*_(enabled|type|backend|kind|mode)$", re.IGNORECASE
)

# sqlite3.connect(":memory:") is a real SQLite convention (a private,
# temporary, in-process database) -- not a hardcoded-file-path bypass of
# db_config, so it is not flagged even though it is a non-empty string
# without "://". No production instance of this exists today (confirmed by
# the real scan below), but the exclusion is kept because it is the kind of
# "genuinely intentional... literal" the task brief anticipates, and a false
# positive here would be a nuisance the moment someone legitimately adds one.
_SQLITE_MEMORY_LITERAL = ":memory:"


@dataclass(frozen=True)
class Finding:
    lineno: int
    pattern: str  # "param_default" | "sqlite3_connect_literal"
    qualname: str  # dotted enclosing-function name, "" for module level
    detail: str

    def allowlist_identifier(self) -> str:
        """Stable-ish identifier used as the second half of an ALLOWLIST key.
        Prefers the dotted qualname (survives line-number churn from
        unrelated edits elsewhere in the file); falls back to a
        line-anchored identifier for a bare module-level sqlite3.connect()
        call with no enclosing function.
        """
        return self.qualname if self.qualname else f"<module-level>:{self.lineno}"


def _walk_with_scope(tree: ast.AST):
    """Yield ``(node, qualname)`` for every node in the tree, where
    ``qualname`` is the dotted name of the nearest enclosing function/method
    ("" for module-level code). A ``ClassDef`` also extends the dotted path
    for anything defined inside it (e.g. ``ForecastTracker.__init__``), so a
    method's own qualname disambiguates it from a same-named method on a
    different class in the same file.

    Deliberately not plain ``ast.walk`` (which has no notion of scope) --
    this codebase's real bug shape needs to attribute both a parameter
    default AND a nested ``sqlite3.connect()`` call to the function they
    live in, for a human-readable finding and a stable allowlist key.
    """

    def _walk(node: ast.AST, prefix: str):
        yield node, prefix
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_qualname = f"{prefix}.{child.name}" if prefix else child.name
                yield from _walk(child, child_qualname)
            elif isinstance(child, ast.ClassDef):
                child_qualname = f"{prefix}.{child.name}" if prefix else child.name
                yield from _walk(child, child_qualname)
            else:
                yield from _walk(child, prefix)

    yield from _walk(tree, "")


def _is_db_path_param_name(name: str) -> bool:
    if not _DB_PARAM_NAME_RE.search(name):
        return False
    if _DB_PARAM_EXCLUDE_RE.search(name):
        return False
    return True


def _string_default_is_flaggable(value: str) -> bool:
    """The literal-value half of pattern 2a/2b: not empty, not already a
    resolved URL (contains "://"), not the in-memory sentinel.

    Note on "is not itself sourced from db_config/resolve_database_url in
    the same default expression" (from the task brief): this is
    structurally guaranteed rather than checked separately -- a value
    "sourced from" a call/name (``db_path: str = DEFAULT_DB_FILE`` or
    ``db_path: str = resolve_database_url()``) is an ``ast.Name``/``ast.Call``
    default node, never an ``ast.Constant``, so it never reaches this
    function in the first place (see the ``isinstance(default, ast.Constant)``
    gate in the two finder functions below).
    """
    if value == "":
        return False
    if "://" in value:
        return False
    if value == _SQLITE_MEMORY_LITERAL:
        return False
    return True


def find_hardcoded_param_defaults(tree: ast.AST) -> list[Finding]:
    """Pattern (a): a function/method parameter named db_path/database/
    db_file/sqlite_path (case-insensitive substring) whose default is a
    bare, non-empty, non-URL, non-":memory:" string literal -- the exact
    shape of ``ForecastTracker.__init__(self, db_path: str =
    "quant_platform.db", ...)`` before PR #720.
    """
    findings: list[Finding] = []
    for node, qualname in _walk_with_scope(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args

        # Positional-or-keyword (incl. positional-only) params pair with
        # args.defaults right-aligned (defaults apply to the LAST N params).
        pos_params = list(args.posonlyargs) + list(args.args)
        pos_defaults = list(args.defaults)
        offset = len(pos_params) - len(pos_defaults)
        for i, default in enumerate(pos_defaults):
            param = pos_params[offset + i]
            finding = _check_one_param(qualname, param, default)
            if finding is not None:
                findings.append(finding)

        # Keyword-only params pair 1:1 with kw_defaults (None = no default,
        # i.e. a required keyword-only arg).
        for param, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is None:
                continue
            finding = _check_one_param(qualname, param, default)
            if finding is not None:
                findings.append(finding)

    return findings


def _check_one_param(qualname: str, param: ast.arg, default: ast.expr) -> Finding | None:
    name = param.arg
    if not _is_db_path_param_name(name):
        return None
    if not (isinstance(default, ast.Constant) and isinstance(default.value, str)):
        return None
    value = default.value
    if not _string_default_is_flaggable(value):
        return None
    full_qualname = f"{qualname}.{name}" if qualname else name
    return Finding(
        lineno=getattr(default, "lineno", param.lineno),
        pattern="param_default",
        qualname=qualname,
        detail=(
            f"parameter `{name}` of `{qualname or '<module>'}` defaults to "
            f"the bare literal {value!r} -- bypasses "
            f"db_config.resolve_database_url(); default to None and resolve "
            f"lazily instead (see ForecastTracker.__init__ / "
            f"HistoricalStore.__init__ for the established pattern)."
        ),
    )


def _sqlite3_import_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to the ``sqlite3`` module via ANY ``import
    sqlite3[ as alias]`` statement anywhere in the tree (module top level or
    nested inside a function) -- covers both the common bare ``import
    sqlite3`` and an aliased ``import sqlite3 as _sqlite3`` (seen in this
    codebase's own test helpers, e.g. ``tests/test_historical_store.py``).
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3":
                    aliases.add(alias.asname or "sqlite3")
    return aliases


def find_hardcoded_sqlite_connect_calls(tree: ast.AST) -> list[Finding]:
    """Pattern (b): a direct ``sqlite3.connect(...)`` call (or an aliased
    import's ``connect`` method) whose first argument is a bare string
    literal (not a variable/attribute reference) that isn't already a
    resolved URL and isn't the ":memory:" sentinel.
    """
    sqlite_aliases = _sqlite3_import_aliases(tree) or {"sqlite3"}

    findings: list[Finding] = []
    for node, qualname in _walk_with_scope(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "connect"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id in sqlite_aliases):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
            continue
        value = first_arg.value
        if "://" in value or value == _SQLITE_MEMORY_LITERAL:
            continue
        findings.append(
            Finding(
                lineno=node.lineno,
                pattern="sqlite3_connect_literal",
                qualname=qualname,
                detail=(
                    f"sqlite3.connect() called directly with the bare literal "
                    f"path {value!r} (inside `{qualname or '<module level>'}`) "
                    f"-- bypasses db_config.resolve_database_url()/"
                    f"db_config.sqlite_readonly_uri(); resolve the path "
                    f"through db_config first."
                ),
            )
        )
    return findings


def scan_tree(tree: ast.AST) -> list[Finding]:
    """Run both detector patterns against an already-parsed module tree."""
    return find_hardcoded_param_defaults(tree) + find_hardcoded_sqlite_connect_calls(tree)


def scan_source(source: str, filename: str = "<test>") -> list[Finding]:
    """Convenience wrapper for the unit tests below: parse + scan a raw
    source string without touching the filesystem."""
    tree = ast.parse(source, filename=filename)
    return scan_tree(tree)


# ============================================================================
# Allowlist of legitimate, individually-reviewed exceptions.
# ============================================================================
# Keyed by (relative_file_path_as_posix, Finding.allowlist_identifier()).
# Every entry needs a one-line comment explaining WHY it's exempt, and must
# only be added after reading the surrounding code and confirming -- not
# assuming -- that the literal is genuinely fine.
#
# As of this test's introduction, a full scan of the production tree (see
# TestNoHardcodedDbPathDefaults.test_scan_finds_only_allowlisted_instances
# below) found ZERO unallowlisted instances of either pattern:
#   - Every db_path/db_url-named constructor parameter across every DB-backed
#     store (data/historical_store.py, data/paper_account_store.py,
#     transactions_store.py, sizing/cap_audit_store.py,
#     execution/live_trade_proposals_store.py, desktop/run_history_store.py,
#     forecasting/forecast_tracker.py, volatility/iv_engine.py's
#     IVHistoryStore) already defaults to None and resolves lazily via
#     db_config.resolve_database_url() -- the PR #718/#720 fix generalizes
#     cleanly across the whole store fleet.
#   - db_config.py's own DEFAULT_DB_FILE ("quant_platform.db" anchored under
#     settings.LOCAL_DATA_ROOT) and database_setup.py's `db_file: str =
#     DB_FILE` are both a NAME reference to an already-db_config-derived
#     constant, never a bare ast.Constant string default, so the detector
#     correctly does not (and structurally cannot) flag either one -- no
#     allowlist entry is needed for them.
#   - The remaining `sqlite3.connect(...)` call sites in production code
#     (investyo_mcp_server.py, pilots/observability.py,
#     gui/panels/observability.py, cache/cache_store.py,
#     forecasting/forecast_tracker.py, data/historical_store.py) all pass a
#     variable/attribute expression (e.g. `self._db_path`,
#     `sqlite_readonly_uri(db_path)`) as the first argument, never a bare
#     string literal, so pattern (b) does not match them either.
# Kept as a live dict (not deleted) so a future genuine exception has an
# obvious, documented place to go -- see the module docstring above and
# TestDetectorLogic below for what does/doesn't trip the detector.
ALLOWLIST: dict[tuple[str, str], str] = {
    # (currently empty -- see comment above; add entries as
    #  (relative_path, identifier): "one-line reason" when a genuinely
    #  intentional literal is found.)
}


def _is_allowlisted(rel_path: str, finding: Finding) -> bool:
    return (rel_path, finding.allowlist_identifier()) in ALLOWLIST


# ============================================================================
# TestNoHardcodedDbPathDefaults -- the real scan
# ============================================================================


class TestNoHardcodedDbPathDefaults:
    def test_at_least_one_production_file_was_found(self) -> None:
        """Sanity guard against _production_py_files() silently collecting
        zero files (e.g. a REPO_ROOT miscalculation, an overbroad skip),
        which would make the scan below vacuously pass."""
        assert len(_PRODUCTION_FILES) > 0

    def test_scan_finds_only_allowlisted_instances(self) -> None:
        """The real guard: walk every production .py file, run both
        detector patterns, and fail loudly (file/line/pattern for each hit)
        on anything not explicitly justified in ALLOWLIST above."""
        unallowlisted: list[tuple[str, Finding]] = []

        for path in _PRODUCTION_FILES:
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:  # pragma: no cover - defensive
                pytest.fail(f"Failed to parse {path.relative_to(REPO_ROOT)}: {exc}")
                continue

            rel_path = path.relative_to(REPO_ROOT).as_posix()
            for finding in scan_tree(tree):
                if _is_allowlisted(rel_path, finding):
                    continue
                unallowlisted.append((rel_path, finding))

        if unallowlisted:
            lines = [
                f"  {rel_path}:{finding.lineno}  [{finding.pattern}] {finding.detail}"
                for rel_path, finding in unallowlisted
            ]
            pytest.fail(
                "Found hardcoded DB-path default(s) / sqlite3.connect() "
                "literal(s) that bypass db_config.resolve_database_url() -- "
                "this is the exact shape of the real incident fixed in "
                "PR #718 (data/historical_store.py) and PR #720 "
                "(forecasting/forecast_tracker.py; see this file's module "
                "docstring):\n"
                + "\n".join(lines)
                + "\n\nFix the production module (default the parameter to "
                "None and resolve lazily via db_config.resolve_database_url(), "
                "matching ForecastTracker.__init__ / HistoricalStore.__init__), "
                "or -- only if genuinely intentional -- add a justified entry "
                "to ALLOWLIST in this test file."
            )


# ============================================================================
# TestDetectorLogic -- pure-logic unit tests against source-string fixtures
# ============================================================================


class TestDetectorLogic:
    """Proves the detector actually catches the real bug shape (and doesn't
    false-positive on the legitimate db_config-resolved shape) without
    touching the filesystem or the real production tree."""

    # -- pattern (a): parameter default ------------------------------------

    def test_bad_param_default_is_flagged(self) -> None:
        """The exact pre-PR#720 ForecastTracker.__init__ shape."""
        source = '''
class ForecastTracker:
    def __init__(self, db_path: str = "quant_platform.db", *, readonly: bool = False) -> None:
        self._db_path = db_path
'''
        findings = scan_source(source)
        param_findings = [f for f in findings if f.pattern == "param_default"]
        assert len(param_findings) == 1
        assert param_findings[0].qualname == "ForecastTracker.__init__"

    def test_good_param_default_resolved_via_db_config_is_not_flagged(self) -> None:
        """The post-PR#720 shape: db_path defaults to None and is resolved
        lazily via db_config.resolve_database_url() -- must NOT be flagged."""
        source = '''
from typing import Optional

class ForecastTracker:
    def __init__(self, db_path: Optional[str] = None, *, readonly: bool = False) -> None:
        if db_path is None:
            from db_config import resolve_database_url
            db_path = resolve_database_url()
        self._db_path = db_path
'''
        findings = scan_source(source)
        assert findings == []

    def test_name_referencing_a_db_config_derived_constant_is_not_flagged(self) -> None:
        """database_setup.py's real shape: `db_file: str = DB_FILE` where
        DB_FILE is itself `db_config.DEFAULT_DB_FILE` -- the default is an
        ast.Name, never an ast.Constant, so it cannot match pattern (a) even
        though the parameter name matches."""
        source = '''
from db_config import DEFAULT_DB_FILE as DB_FILE

def initialize_database(db_file: str = DB_FILE):
    pass
'''
        findings = scan_source(source)
        assert findings == []

    def test_empty_string_default_is_not_flagged(self) -> None:
        source = '''
def f(db_path: str = ""):
    pass
'''
        assert scan_source(source) == []

    def test_url_like_default_is_not_flagged(self) -> None:
        source = '''
def f(database: str = "sqlite:///already_resolved.db"):
    pass
'''
        assert scan_source(source) == []

    def test_unrelated_boolean_style_name_is_not_flagged(self) -> None:
        """A name that substring-matches "database" but is structurally a
        flag/discriminator, not a path -- the false-positive shape the task
        brief explicitly calls out (`is_database_something`)."""
        source = '''
def f(database_type: str = "postgres", is_database_ready: str = "yes"):
    pass
'''
        assert scan_source(source) == []

    def test_keyword_only_param_default_is_flagged(self) -> None:
        source = '''
def f(*, db_path: str = "quant_platform.db"):
    pass
'''
        findings = scan_source(source)
        assert len(findings) == 1
        assert findings[0].pattern == "param_default"

    def test_sqlite_path_named_param_is_flagged(self) -> None:
        source = '''
def f(sqlite_path: str = "legacy.db"):
    pass
'''
        findings = scan_source(source)
        assert len(findings) == 1

    # -- pattern (b): sqlite3.connect() -------------------------------------

    def test_bad_sqlite3_connect_literal_is_flagged(self) -> None:
        source = '''
import sqlite3

def get_connection():
    return sqlite3.connect("quant_platform.db")
'''
        findings = scan_source(source)
        connect_findings = [f for f in findings if f.pattern == "sqlite3_connect_literal"]
        assert len(connect_findings) == 1
        assert connect_findings[0].qualname == "get_connection"

    def test_good_sqlite3_connect_with_variable_is_not_flagged(self) -> None:
        """The legitimate shape used throughout this codebase (e.g.
        HistoricalStore, ForecastTracker): the path is a resolved
        attribute/variable, never a bare literal."""
        source = '''
import sqlite3

class Store:
    def __init__(self, db_path):
        self._db_path = db_path

    def get_connection(self):
        return sqlite3.connect(self._db_path)
'''
        findings = scan_source(source)
        assert findings == []

    def test_aliased_sqlite3_import_is_still_resolved(self) -> None:
        """tests/test_historical_store.py's own convention (`import sqlite3
        as _sqlite3`) must still be caught if it appears in production code
        with a bare literal."""
        source = '''
import sqlite3 as _sqlite3

def get_connection():
    return _sqlite3.connect("quant_platform.db")
'''
        findings = scan_source(source)
        connect_findings = [f for f in findings if f.pattern == "sqlite3_connect_literal"]
        assert len(connect_findings) == 1

    def test_sqlite3_connect_memory_sentinel_is_not_flagged(self) -> None:
        source = '''
import sqlite3

def get_connection():
    return sqlite3.connect(":memory:")
'''
        assert scan_source(source) == []

    def test_sqlite3_connect_url_is_not_flagged(self) -> None:
        source = '''
import sqlite3

def get_connection():
    return sqlite3.connect("sqlite:///already_resolved.db")
'''
        assert scan_source(source) == []

    def test_sqlite3_connect_bare_file_uri_literal_is_still_flagged(self) -> None:
        """A `file:...?mode=ro` URI has no `://` substring (single colon, no
        double slash unless the path itself is absolute), so it is NOT
        exempted by the "://" rule -- and correctly so: this is exactly what
        a hand-rolled bypass of db_config.sqlite_readonly_uri() would look
        like (a real read-only URI string that still hardcodes the db file
        name as a bare literal instead of deriving it). The legitimate
        production shape (e.g. investyo_mcp_server.py, pilots/observability.py)
        always builds this via `sqlite_readonly_uri(db_path)` -- a Call
        expression, not a literal -- so it is unaffected (see
        test_good_sqlite3_connect_with_variable_is_not_flagged)."""
        source = '''
import sqlite3

def get_connection():
    return sqlite3.connect("file:already.db?mode=ro", uri=True)
'''
        findings = scan_source(source)
        connect_findings = [f for f in findings if f.pattern == "sqlite3_connect_literal"]
        assert len(connect_findings) == 1

    def test_unrelated_connect_call_is_not_flagged(self) -> None:
        """A `.connect()` call on something that isn't sqlite3 (e.g. a
        requests session, a broker client) must not be mistaken for
        sqlite3.connect()."""
        source = '''
def f(client):
    return client.connect("quant_platform.db")
'''
        assert scan_source(source) == []

    def test_combined_bad_source_is_flagged_twice(self) -> None:
        """A file with both bug shapes at once produces two independent
        findings -- proving the two detectors compose correctly."""
        source = '''
import sqlite3

class Store:
    def __init__(self, db_path: str = "quant_platform.db"):
        self._db_path = db_path

    def get_connection(self):
        return sqlite3.connect("quant_platform.db")
'''
        findings = scan_source(source)
        patterns = sorted(f.pattern for f in findings)
        assert patterns == ["param_default", "sqlite3_connect_literal"]

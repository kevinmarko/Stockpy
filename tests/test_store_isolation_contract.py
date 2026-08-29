"""
tests/test_store_isolation_contract.py
========================================
Structural CI guard for PR 8 of the module-efficiency-audit remediation plan
(F9, ``docs/module_efficiency_redundancy_audit.md``).

This repo has a REAL, repeated incident history (see ``CLAUDE.md``'s "Lesson
learned during rollout", "Cache-poisoning guard", and the PR-872-remediation
bullets, plus ``docs/known_issues/pr872_live_db_test_contamination_2026.md``):
a new SQLAlchemy-backed store gets added, its default DB resolution either
bypasses ``db_config.resolve_database_url()`` (a hardcoded, CWD-relative
``db_path`` literal) or has no test-suite isolation, and a plain ``pytest``
run silently writes synthetic rows into the operator's real, shared
``~/.stockpy_local/quant_platform.db``. This has happened at least twice
(``data/historical_store.py``, ``forecasting/forecast_tracker.py`` -- both
now fixed) plus the PR 872 ``transactions_store``/``paper_account_store``
bridge incident that actually reached the live DB (260 contaminated rows).

This module turns that recurring bug class into two enforced CI properties,
checked for EVERY ``*_store.py`` file discovered by glob (not a hand-typed
list -- a new store file is picked up automatically the next time this test
runs, mirroring ``tests/test_pilots_strategy_matrix.py``'s auto-discovery of
``pilots/*.py``):

1. **Classification** (``test_every_store_file_is_classified``): every
   ``*_store.py`` file is either detected as SQLAlchemy/``db_config``-backed,
   or is explicitly listed in ``NON_SQL_STORES`` with a documented reason. A
   brand-new store that is neither forces a decision instead of silent drift.

2. **Resolution correctness** (``test_sql_backed_store_init_resolves_via_db_config``):
   for every SQLAlchemy-backed store class (``class FooStore:`` -- not an
   ``_Offline...`` stand-in, not an exception class), its ``__init__`` is
   statically checked to (a) never default a ``db_url``/``db_path``/
   ``sqlite_path`` parameter to a hardcoded string literal (the exact
   CWD-relative bug class already fixed once in this codebase) and (b)
   actually call ``resolve_database_url()`` somewhere in its body when no
   override is given.

3. **Test-suite isolation** (``test_bare_construction_in_tests_has_isolation_evidence``):
   scans every file under ``tests/`` for a DIRECT, implicit (no explicit
   ``db_url=``/``db_path=``/``sqlite_path=``) construction of one of these
   store classes, and requires that construction to be protected by SOME
   isolation mechanism -- either a session-wide autouse fixture in the root
   ``conftest.py`` (the ``_isolate_*_db_in_tests`` pattern) that patches
   ``resolve_database_url`` for that store's own module, or file-local
   isolation evidence in the SAME test file (``tests/_db_isolation.py``'s
   ``redirect_class_to_memory_db``/``make_memory_db_init`` used against that
   class, a ``settings.DATABASE_URL`` patch, a ``resolve_database_url`` patch
   for that module, or a ``mock.patch``/``monkeypatch.setattr`` targeting the
   class's own dotted import path -- all patterns already used somewhere in
   this test suite; see the docstring of ``_FILE_LOCAL_ISOLATION_PATTERN``
   below for the full list).

Honest scope boundary (CONSTRAINT #4 -- do not overclaim what this proves):
property 3 only catches a DIRECT, LITERAL ``ClassName(`` construction inside
a file under ``tests/``. It does NOT perform call-graph/reachability
analysis into production code -- it cannot prove that some deeply-nested
production function, reachable from a test that never mentions the store
class by name, is safe. Every store this test currently classifies as
"needs isolation" already has one; every store that DOESN'T appear as
needing one was individually hand-audited (2026-08-29, this PR) by tracing
every production call site of the form ``StoreClass()``/``StoreClass(readonly=
True)`` and confirming its only currently-reachable test paths either keep
the gating settings flag at its coded-safe default (``False``, restored by
``conftest.py``'s ``_clean_settings_between_tests`` autouse fixture every
test) or explicitly monkeypatch the store class before invoking the
production code that constructs it. That hand-audit is a point-in-time
fact, not a guarantee -- this is exactly why property 3 exists: to catch
the NEXT test that reaches one of these classes carelessly, even though it
cannot yet catch every conceivable future indirect path.

``data/historical_store.py`` is the confirmed-deliberate outlier noted in
the audit (does not call ``Base.metadata.create_all()`` in ``__init__``,
defers to its own ``_ensure_tables()``) -- it is NOT touched by this PR's
refactor, but IS included in this guard's enumeration like every other
store, since its ``__init__`` already follows the correct
``resolve_database_url()`` pattern (fixed independently, per the audit's
"Now fixed since the original draft" note) and there is no reason to exempt
it from the same static check every other store gets.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that may legitimately contain a file literally named
# "*_store.py" that has nothing to do with this codebase's own store
# convention (vendored/third-party/build output). None of these currently
# exist in this repo, but excluding them defensively costs nothing and
# matches the caution this PR's own task description asks for.
_EXCLUDE_DIR_PARTS = {
    "node_modules", "webapp", ".git", ".claude", "__pycache__",
    "build", "dist", ".venv", "venv",
}


def _iter_store_files() -> List[Path]:
    """Every ``*_store.py`` file in the repo, relative to REPO_ROOT, sorted
    for deterministic test output. Excludes tests/ itself (test files named
    ``test_*_store.py`` are not stores; ``tests/_db_isolation.py`` isn't
    even that) and vendored/build directories."""
    found: List[Path] = []
    for path in REPO_ROOT.rglob("*_store.py"):
        rel = path.relative_to(REPO_ROOT)
        if rel.parts and rel.parts[0] == "tests":
            continue
        if set(rel.parts) & _EXCLUDE_DIR_PARTS:
            continue
        found.append(rel)
    return sorted(found)


# ---------------------------------------------------------------------------
# Property 1: classification
# ---------------------------------------------------------------------------

# *_store.py files that are, by design, NOT part of the db_config-routed
# SQLAlchemy family this guard is about -- each has its own documented,
# deliberately-separate persistence mechanism that cannot poison the shared
# quant_platform.db this guard exists to protect. A new *_store.py file that
# is neither auto-detected as SQL-backed (see _is_sql_backed below) NOR
# listed here with a reason fails test_every_store_file_is_classified,
# forcing an explicit decision instead of silent drift.
NON_SQL_STORES: Dict[str, str] = {
    "cache/cache_store.py": (
        "Raw sqlite3 (not SQLAlchemy/db_config) at a DELIBERATELY separate "
        "path -- {LOCAL_DATA_ROOT}/api_cache/cache.db, never the shared "
        "quant_platform.db this guard protects. See the module's own "
        "docstring ('get_default_cache() -- process-wide singleton Cache')."
    ),
    "execution/receipts_store.py": (
        "Append-only JSONL files (output/execution_receipts.jsonl, "
        "output/execution_placed.jsonl) -- no database of any kind."
    ),
    "llm/status_store.py": (
        "A single JSON file (output/llm_status.json) -- no database."
    ),
    "pilots/follows_store.py": (
        "Atomic JSON file (output/follows.json), write-then-rename -- no "
        "database. See the module's own docstring."
    ),
    "pilots/scan_config_store.py": (
        "Atomic JSON file (output/scan_configs.json), write-then-rename -- "
        "no database. Mirrors pilots/follows_store.py exactly."
    ),
}


def _is_sql_backed(rel_path: Path) -> bool:
    text = (REPO_ROOT / rel_path).read_text(encoding="utf-8", errors="replace")
    return "resolve_database_url" in text


def test_every_store_file_is_classified() -> None:
    """Every *_store.py file is either SQLAlchemy/db_config-backed
    (auto-detected) or explicitly exempted in NON_SQL_STORES with a reason.

    This is the guard against silent drift: a brand-new store module that
    invents its own bespoke persistence path (neither db_config-routed nor
    documented as deliberately not-a-database) fails here, forcing whoever
    added it to make an explicit, reviewable choice.
    """
    unclassified = []
    stale_exemptions = []
    all_files = {str(p) for p in _iter_store_files()}
    for rel in _iter_store_files():
        key = str(rel)
        if _is_sql_backed(rel):
            continue
        if key not in NON_SQL_STORES:
            unclassified.append(key)
    for key in NON_SQL_STORES:
        if key not in all_files:
            stale_exemptions.append(key)

    assert not unclassified, (
        "New *_store.py file(s) found that are neither SQLAlchemy/"
        f"db_config-backed nor listed in NON_SQL_STORES: {unclassified}. "
        "Either route the store's default DB resolution through "
        "db_config.resolve_database_url() (see sizing/cap_audit_store.py "
        "for the canonical __init__ pattern), or add it to NON_SQL_STORES "
        "above with a documented reason if it deliberately uses a "
        "different persistence mechanism."
    )
    assert not stale_exemptions, (
        f"NON_SQL_STORES lists file(s) that no longer exist: {stale_exemptions}. "
        "Remove the stale entry."
    )


# ---------------------------------------------------------------------------
# Property 2: __init__ resolves via db_config, never a hardcoded literal
# ---------------------------------------------------------------------------

_URL_PARAM_NAMES = {"db_url", "db_path", "sqlite_path"}


def _find_store_classes(tree: ast.Module) -> List[ast.ClassDef]:
    """Every class in the module whose name ends with "Store" and is not an
    internal ``_Offline...`` degrade stand-in (which holds no DB connection
    at all -- see e.g. sizing/cap_audit_store.py::_OfflineCapAuditStore) or
    a private helper. Correctly skips sibling exception classes too (e.g.
    execution/live_trade_proposals_store.py's LiveTradeProposalNotFoundError/
    LiveTradeProposalAlreadyDecidedError, which don't end in "Store")."""
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name.endswith("Store")
        and not node.name.startswith("_")
    ]


def _find_init(cls_node: ast.ClassDef) -> Optional[ast.FunctionDef]:
    for node in cls_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            return node
    return None


def _init_url_param_defaults(init_node: ast.FunctionDef) -> Dict[str, ast.expr]:
    """Map {param_name: default_ast_node} for every url-shaped parameter
    that carries an explicit default (positional-or-keyword or
    keyword-only)."""
    args = init_node.args
    defaults: Dict[str, ast.expr] = {}

    pos_params = list(args.posonlyargs) + list(args.args)
    pos_defaults = list(args.defaults)
    offset = len(pos_params) - len(pos_defaults)
    for i, d in enumerate(pos_defaults):
        name = pos_params[offset + i].arg
        if name in _URL_PARAM_NAMES:
            defaults[name] = d

    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        if d is not None and a.arg in _URL_PARAM_NAMES:
            defaults[a.arg] = d

    return defaults


def _init_all_param_names(init_node: ast.FunctionDef) -> Set[str]:
    args = init_node.args
    names = {a.arg for a in args.posonlyargs}
    names |= {a.arg for a in args.args}
    names |= {a.arg for a in args.kwonlyargs}
    return names


def _init_calls_resolve_database_url(init_node: ast.FunctionDef) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "resolve_database_url"
        for n in ast.walk(init_node)
    )


def _check_store_class(rel_path: Path, cls_node: ast.ClassDef) -> List[str]:
    problems: List[str] = []
    init_node = _find_init(cls_node)
    if init_node is None:
        problems.append(
            f"{rel_path}::{cls_node.name} defines no __init__ -- cannot "
            "verify its default DB resolution."
        )
        return problems

    param_names = _init_all_param_names(init_node)
    url_params_present = param_names & _URL_PARAM_NAMES
    if not url_params_present:
        problems.append(
            f"{rel_path}::{cls_node.name}.__init__ has no db_url/db_path/"
            "sqlite_path parameter -- cannot verify its default DB "
            "resolution goes through db_config."
        )
        return problems

    defaults = _init_url_param_defaults(init_node)
    for name in url_params_present:
        default = defaults.get(name)
        if default is None:
            # No default at all (required positional) -- caller must always
            # supply it; not the CWD-relative-literal-default bug class.
            continue
        if isinstance(default, ast.Constant) and isinstance(default.value, str):
            problems.append(
                f"{rel_path}::{cls_node.name}.__init__'s `{name}` parameter "
                f"defaults to the hardcoded string literal {default.value!r} "
                "instead of None -- this is the exact CWD-relative db_path "
                "bug class already fixed once in this codebase "
                "(data/historical_store.py, forecasting/forecast_tracker.py; "
                "see CLAUDE.md's 'Lesson learned during rollout' bullet)."
            )
        elif not (isinstance(default, ast.Constant) and default.value is None):
            problems.append(
                f"{rel_path}::{cls_node.name}.__init__'s `{name}` parameter "
                "has a non-None, non-string-literal default -- unexpected "
                "shape, please verify manually and adjust this guard if it "
                "is genuinely safe."
            )

    if not _init_calls_resolve_database_url(init_node):
        problems.append(
            f"{rel_path}::{cls_node.name}.__init__ never calls "
            "resolve_database_url() -- its default DB resolution may bypass "
            "db_config entirely."
        )

    return problems


def test_sql_backed_store_init_resolves_via_db_config() -> None:
    """Every SQLAlchemy-backed store's __init__ resolves its default DB
    location via db_config.resolve_database_url(), never a hardcoded
    literal path -- the CWD-relative db_path bug class CLAUDE.md documents
    as having already bitten this codebase twice."""
    all_problems: List[str] = []
    for rel in _iter_store_files():
        if str(rel) in NON_SQL_STORES:
            continue
        if not _is_sql_backed(rel):
            continue
        source = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(rel))
        store_classes = _find_store_classes(tree)
        assert store_classes, (
            f"{rel} contains 'resolve_database_url' (classified SQL-backed) "
            "but defines no class ending in 'Store' -- update "
            "_find_store_classes or NON_SQL_STORES to match."
        )
        for cls_node in store_classes:
            all_problems.extend(_check_store_class(rel, cls_node))

    assert not all_problems, "Store __init__ resolution problem(s):\n" + "\n".join(
        f"  - {p}" for p in all_problems
    )


# ---------------------------------------------------------------------------
# Property 3: test-suite isolation for implicit (default-resolution)
# construction
# ---------------------------------------------------------------------------

_CONFTEST_PATH = REPO_ROOT / "conftest.py"

# import X.Y as alias   -> {"alias": "X.Y"}
_IMPORT_AS_RE = re.compile(r"^\s*import\s+([\w.]+)\s+as\s+(\w+)\s*$", re.MULTILINE)
# monkeypatch.setattr(alias, "resolve_database_url", ...)
_MONKEYPATCH_RESOLVE_RE = re.compile(
    r'monkeypatch\.setattr\(\s*(\w+)\s*,\s*"resolve_database_url"'
)


def _module_dotted_to_relpath(dotted: str) -> str:
    return dotted.replace(".", "/") + ".py"


def _conftest_isolated_store_files() -> Set[str]:
    """Parse conftest.py's existing `_isolate_*_db_in_tests` autouse
    fixtures (mirroring the pattern documented in the task) and return the
    set of *_store.py-relative-path files whose `resolve_database_url` is
    monkeypatched by one of them.

    Purely textual (not full AST) -- deliberately simple, matching the
    literal, hand-written convention every existing fixture already follows
    (`import data.execution_audit_store as _eas`` then
    ``monkeypatch.setattr(_eas, "resolve_database_url", ...)``), so this
    stays easy to eyeball-verify against conftest.py directly.
    """
    text = _CONFTEST_PATH.read_text(encoding="utf-8")
    # findall yields (dotted_module, alias) pairs -- invert to alias->module.
    alias_to_module = {alias: dotted for dotted, alias in _IMPORT_AS_RE.findall(text)}
    isolated: Set[str] = set()
    for alias in _MONKEYPATCH_RESOLVE_RE.findall(text):
        dotted = alias_to_module.get(alias)
        if dotted:
            isolated.add(_module_dotted_to_relpath(dotted))
    return isolated


# A handful of test files patch `settings.DATABASE_URL` directly (upstream
# of resolve_database_url()) rather than patching resolve_database_url on
# the store's own module -- textually indistinguishable from "isolates
# every DATABASE_URL-resolving store in this file" without deeper analysis,
# so any construction in a file containing this pattern is treated as
# isolated. This is deliberately permissive (a file could patch
# DATABASE_URL for an unrelated reason and coincidentally also construct an
# unrelated store bare) -- accepted as a reasonable trade-off matching this
# guard's textual-evidence approach; see the module docstring's "Honest
# scope boundary" section.
_DATABASE_URL_PATCH_RE = re.compile(
    r'(?:monkeypatch\.setattr|mock\.patch\.object)\(\s*settings\s*,\s*["\']DATABASE_URL["\']'
)


def _file_has_local_isolation_evidence(text: str, class_name: str, module_dotted: str) -> bool:
    if _DATABASE_URL_PATCH_RE.search(text):
        return True
    # tests/_db_isolation.py helpers used directly against this class.
    if re.search(rf"redirect_class_to_memory_db\(\s*[\w.]*{re.escape(class_name)}\s*\)", text):
        return True
    if re.search(rf"make_memory_db_init\(\s*[\w.]*{re.escape(class_name)}\.", text):
        return True
    # A resolve_database_url patch aimed at this store's own module, done
    # locally in the test file rather than via conftest.py (e.g. a
    # module-scoped fixture in that one file).
    if re.search(
        rf'resolve_database_url["\']?\s*,?\s*\)?.*{re.escape(module_dotted)}|'
        rf'{re.escape(module_dotted)}.*resolve_database_url',
        text,
    ):
        return True
    # mock.patch / monkeypatch.setattr targeting the class's own dotted
    # import path as a string literal, e.g.
    # mock.patch("sizing.cap_audit_store.CapAuditStore", ...) or
    # monkeypatch.setattr("engine.cache_long_short_engine.CacheLongShortStore", ...).
    if re.search(rf'["\'][\w.]*\.{re.escape(class_name)}["\']', text):
        return True
    return False


def _call_matches_class_name(call: ast.Call, class_name: str) -> bool:
    """True if `call.func` is a bare Name or a dotted Attribute whose final
    component equals class_name -- covers both `ClassName(...)` and
    `module.ClassName(...)`/`module.submodule.ClassName(...)`."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == class_name
    if isinstance(func, ast.Attribute):
        return func.attr == class_name
    return False


def _call_has_explicit_url_override(call: ast.Call) -> bool:
    return any(kw.arg in _URL_PARAM_NAMES for kw in call.keywords)


def _find_implicit_constructions(source: str, filename: str, class_name: str) -> List[int]:
    """Line numbers of every real (AST-level, not textual) construction call
    `ClassName(...)`/`module.ClassName(...)` in `source` that has no
    explicit db_url=/db_path=/sqlite_path= keyword. AST-based deliberately
    -- a plain text/regex scan would also match the class name appearing
    inside a docstring or comment (confirmed to happen in practice: this
    guard's first draft flagged tests/_db_isolation.py's own docstring,
    which merely DESCRIBES the pattern it exists to fix, as a violation)."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    lines: List[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_matches_class_name(node, class_name):
            if not _call_has_explicit_url_override(node):
                lines.append(node.lineno)
    return lines


def _module_path_for_store_file(rel: Path) -> str:
    return str(rel)[:-3].replace("/", ".")  # strip ".py", dotted form


def _collect_sql_store_classes() -> Dict[str, str]:
    """{class_name: module_dotted} for every real (non-"_Offline...") store
    class across every SQL-backed *_store.py file."""
    result: Dict[str, str] = {}
    for rel in _iter_store_files():
        if str(rel) in NON_SQL_STORES or not _is_sql_backed(rel):
            continue
        source = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(rel))
        for cls_node in _find_store_classes(tree):
            result[cls_node.name] = _module_path_for_store_file(rel)
    return result


def test_bare_construction_in_tests_has_isolation_evidence() -> None:
    """Every DIRECT, implicit (no explicit db_url=/db_path=/sqlite_path=)
    construction of a SQL-backed store class anywhere under tests/ is
    protected by an isolation mechanism -- a conftest.py autouse fixture
    for that store's module, or file-local evidence in the same test file.

    See this module's own docstring ("Honest scope boundary") for exactly
    what this test does and does not prove.
    """
    store_classes = _collect_sql_store_classes()
    conftest_isolated = _conftest_isolated_store_files()

    violations: List[str] = []
    test_files = sorted(
        p for p in (REPO_ROOT / "tests").rglob("*.py")
        if p.name != "test_store_isolation_contract.py"
    )
    for test_path in test_files:
        text = test_path.read_text(encoding="utf-8", errors="replace")
        for class_name, module_dotted in store_classes.items():
            if class_name not in text:
                continue  # cheap short-circuit before parsing every file's AST
            store_file = _module_dotted_to_relpath(module_dotted)
            line_nos = _find_implicit_constructions(text, str(test_path), class_name)
            if not line_nos:
                continue
            if store_file in conftest_isolated:
                continue
            if _file_has_local_isolation_evidence(text, class_name, module_dotted):
                continue
            for line_no in line_nos:
                violations.append(
                    f"{test_path.relative_to(REPO_ROOT)}:{line_no} constructs "
                    f"{class_name}(...) with no explicit db_url/db_path/"
                    f"sqlite_path override, and neither conftest.py nor this "
                    f"test file shows isolation evidence for "
                    f"{module_dotted}. Either pass an explicit override "
                    f"(e.g. db_url=\"sqlite:///:memory:\"), or add a "
                    f"conftest.py autouse fixture following the existing "
                    f"_isolate_*_db_in_tests pattern, or use "
                    f"tests/_db_isolation.py's redirect_class_to_memory_db()."
                )

    assert not violations, (
        "Unisolated store construction(s) found -- see docs/known_issues/"
        "pr872_live_db_test_contamination_2026.md for why this matters:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Regression guard: the currently-known conftest.py fixtures stay registered
# ---------------------------------------------------------------------------

_EXPECTED_CONFTEST_ISOLATED = {
    "validation/validation_history_store.py",
    "data/execution_audit_store.py",
    "data/broker_fills_store.py",
    "data/paper_account_store.py",
    "transactions_store.py",
}


def test_known_conftest_fixtures_still_registered() -> None:
    """Regression guard: the five store modules conftest.py is already known
    to isolate (per the module docstrings of ``_isolate_validation_runs_db_
    in_tests``, ``_isolate_execution_audit_db_in_tests``,
    ``_isolate_broker_fills_db_in_tests``, and
    ``_isolate_paper_and_transactions_db_in_tests``) stay isolated. If this
    fails, someone removed or broke one of those fixtures -- restore it
    rather than silencing this test, since (per those fixtures' own
    docstrings) at least one of them exists because of an actual, confirmed
    live-DB contamination incident."""
    missing = _EXPECTED_CONFTEST_ISOLATED - _conftest_isolated_store_files()
    assert not missing, (
        f"conftest.py no longer isolates: {sorted(missing)} -- this "
        "regresses a fix for a confirmed past live-DB contamination "
        "incident. See CLAUDE.md's PR-872-remediation bullet and "
        "docs/known_issues/pr872_live_db_test_contamination_2026.md."
    )


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-v"]))

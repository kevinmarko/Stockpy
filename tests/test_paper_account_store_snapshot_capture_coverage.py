"""Structural AST guard: every ``PaperPosition`` construction and every
``pos.entry_ts = now_ts`` reassignment inside ``PaperAccountStore.apply_fill``/
``apply_multi_leg_fill`` must be paired, in the SAME statement block, with a
``self._maybe_record_decision_snapshot(...)`` call.

Code-review finding: decision-snapshot capture (the Retrospective Learning
Loop's forward-only "why was this trade opened" record — see
``data/trade_decision_snapshot_store.py``) is wired individually at 8
near-duplicate branch points across these two methods rather than one
choke point, so nothing previously stopped a FUTURE new branch (a new order
type, a new averaging-in variant) that opens a genuinely new position from
silently omitting the call -- degrading that path's trades to
``decision.state="unknown"`` forever, with no test to catch the omission.

This test closes that gap mechanically: it parses the real source, and for
every "new position" marker (a ``PaperPosition(...)`` construction or an
``entry_ts = now_ts`` reassignment -- the two signals this file itself uses
to mean "this position's entry_ts just became authoritative") inside
``apply_fill``/``apply_multi_leg_fill``, asserts a
``_maybe_record_decision_snapshot`` call exists in the SAME enclosing
statement list. A future branch added without the paired call fails this
test, not just a manual read of the diff.

Deliberately scoped to ``apply_fill``/``apply_multi_leg_fill`` only --
``apply_roll_fill`` is EXCLUDED by design (a roll continues an existing
position rather than opening a fresh one; it has no ``decision_context``
parameter at all, a documented, deliberate scope boundary -- see
``data/paper_account_store.py``'s own module-level design notes).
"""
from __future__ import annotations

import ast
import pathlib

_PAPER_ACCOUNT_STORE = pathlib.Path(__file__).resolve().parent.parent / "data" / "paper_account_store.py"
_CHECKED_METHODS = ("apply_fill", "apply_multi_leg_fill")
_SNAPSHOT_METHOD_NAME = "_maybe_record_decision_snapshot"


def _iter_stmt_lists(node: ast.AST):
    """Yield every statement list ('suite') anywhere within ``node``,
    recursively -- the body/orelse/finalbody of an If/For/While/Try/With,
    and each ``except`` handler's body. Does NOT recurse into nested
    FunctionDef/Lambda bodies (this file has none inside the checked
    methods, and a nested def would be a different scope entirely)."""
    for field in ("body", "orelse", "finalbody"):
        stmts = getattr(node, field, None)
        if isinstance(stmts, list) and stmts:
            yield stmts
            for stmt in stmts:
                yield from _iter_stmt_lists(stmt)
    handlers = getattr(node, "handlers", None)
    if handlers:
        for handler in handlers:
            yield from _iter_stmt_lists(handler)


def _is_entry_ts_now_assignment(stmt: ast.stmt) -> bool:
    """Matches ``pos.entry_ts = now_ts`` (or any `<name>.entry_ts = now_ts`)."""
    if not isinstance(stmt, ast.Assign):
        return False
    if not (isinstance(stmt.value, ast.Name) and stmt.value.id == "now_ts"):
        return False
    return any(
        isinstance(t, ast.Attribute) and t.attr == "entry_ts"
        for t in stmt.targets
    )


def _is_paperposition_construction(stmt: ast.stmt) -> bool:
    """Matches ``pos = PaperPosition(...)`` (any assignment whose value is a
    call to a name literally called ``PaperPosition``)."""
    if not isinstance(stmt, ast.Assign):
        return False
    call = stmt.value
    return isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "PaperPosition"


def _is_snapshot_capture_call(stmt: ast.stmt) -> bool:
    """Matches ``self._maybe_record_decision_snapshot(...)`` as a bare
    expression statement (this file never uses its return value)."""
    if not isinstance(stmt, ast.Expr):
        return False
    call = stmt.value
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == _SNAPSHOT_METHOD_NAME
    )


def _find_method(tree: ast.Module, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found in {_PAPER_ACCOUNT_STORE}")


def _unpaired_new_position_markers(method: ast.FunctionDef) -> list:
    """Returns a list of (lineno, kind) for every 'new position' marker
    whose enclosing statement list has NO ``_maybe_record_decision_snapshot``
    call anywhere in it."""
    violations = []
    for stmts in _iter_stmt_lists(method):
        has_snapshot_call = any(_is_snapshot_capture_call(s) for s in stmts)
        for stmt in stmts:
            if _is_paperposition_construction(stmt) and not has_snapshot_call:
                violations.append((stmt.lineno, "PaperPosition(...) construction"))
            elif _is_entry_ts_now_assignment(stmt) and not has_snapshot_call:
                violations.append((stmt.lineno, "entry_ts = now_ts reassignment"))
    return violations


def test_every_new_position_marker_is_paired_with_a_snapshot_capture_call():
    tree = ast.parse(_PAPER_ACCOUNT_STORE.read_text(encoding="utf-8"))

    all_violations = []
    for method_name in _CHECKED_METHODS:
        method = _find_method(tree, "PaperAccountStore", method_name)
        violations = _unpaired_new_position_markers(method)
        for lineno, kind in violations:
            all_violations.append(f"{method_name} line {lineno}: {kind} with no paired {_SNAPSHOT_METHOD_NAME}() call")

    assert not all_violations, (
        "Found position-opening code with no paired decision-snapshot capture call "
        "(a future branch that opens a new PaperPosition or resets entry_ts must also "
        "call self._maybe_record_decision_snapshot(...) in the same block, or its "
        "trades will silently degrade to decision.state=\"unknown\" forever):\n  "
        + "\n  ".join(all_violations)
    )


def test_checked_methods_genuinely_contain_at_least_one_marker_each():
    """Sanity check on the AST matchers themselves: if a future refactor
    renames PaperPosition/now_ts/_maybe_record_decision_snapshot such that
    the matchers above silently stop matching anything, the guard test
    above would trivially pass with zero violations found -- a false
    negative. This asserts each checked method has a real, non-zero count
    of BOTH marker kinds AND at least one snapshot-capture call, so the
    guard is proven to be looking at something real, not matching nothing."""
    tree = ast.parse(_PAPER_ACCOUNT_STORE.read_text(encoding="utf-8"))
    for method_name in _CHECKED_METHODS:
        method = _find_method(tree, "PaperAccountStore", method_name)
        all_stmts = [s for stmts in _iter_stmt_lists(method) for s in stmts]
        n_new_positions = sum(1 for s in all_stmts if _is_paperposition_construction(s))
        n_entry_ts_resets = sum(1 for s in all_stmts if _is_entry_ts_now_assignment(s))
        n_snapshot_calls = sum(1 for s in all_stmts if _is_snapshot_capture_call(s))
        assert n_new_positions > 0, f"{method_name}: matcher found zero PaperPosition(...) constructions -- matcher is broken"
        assert n_entry_ts_resets > 0, f"{method_name}: matcher found zero entry_ts = now_ts reassignments -- matcher is broken"
        assert n_snapshot_calls > 0, f"{method_name}: matcher found zero _maybe_record_decision_snapshot(...) calls -- matcher is broken"
        assert n_snapshot_calls == n_new_positions + n_entry_ts_resets, (
            f"{method_name}: expected exactly one snapshot-capture call per new-position "
            f"marker ({n_new_positions} PaperPosition constructions + {n_entry_ts_resets} "
            f"entry_ts resets = {n_new_positions + n_entry_ts_resets}), found {n_snapshot_calls}"
        )

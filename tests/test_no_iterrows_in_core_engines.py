"""Regression guard for CLAUDE.md's vectorization convention:
'Technical/fundamental math is vectorized -- no per-row Python loops.'

This has been true by convention only, with no mechanical enforcement --
any new contribution (including AI-assisted ones) can quietly reintroduce
`.iterrows()`/`.itertuples()` into the core scoring path without anything
failing. This test statically parses each guarded module and fails if a
`.iterrows()` or `.itertuples()` call appears anywhere in it, without
needing to import pandas or run the engine.

AST-based (not a grep) so a comment or docstring that merely *mentions*
"iterrows" (e.g. "no iterrows() per convention") never trips this guard --
only an actual `<expr>.iterrows()` / `<expr>.itertuples()` call node does.

Scope is deliberately narrow: the core scoring/aggregation path
(`processing_engine.py`, `strategy_engine.py`, `signals/*.py`), which
CLAUDE.md documents as "natively vectorized in pandas/numpy". Per-ticker
dict iteration (`for ticker, df in some_dict.items(): ...`) is a separate,
explicitly-allowed pattern (CLAUDE.md: "Loops over tickers... wrap each
ticker in try/except") and is NOT what this guard checks -- only literal
`.iterrows()`/`.itertuples()` method calls, which always imply row-wise
iteration over a single DataFrame's index.

If a guarded module gains a genuine, unavoidable `.iterrows()`/
`.itertuples()` (rare -- most row-wise needs have a vectorized
equivalent), add an explicit, commented exception to
``ALLOWED_EXCEPTIONS`` below rather than silently letting this test rot.

---

SECOND GUARD (added by the 2026-08 module-efficiency audit,
docs/module_efficiency_redundancy_audit.md's F1): the check above cannot
catch a real class of the same bug. `SignalModule.compute_vectorized()`'s
base-class default (`signals/base.py`) falls back to a per-row
`df.apply(lambda row: self.compute(row, context), axis=1)` -- itself a
deliberate, reviewed exception (listed in ALLOWED_EXCEPTIONS below) since
it's the intended generic fallback, not a bug. But a signal module that
simply omits overriding `compute_vectorized()` inherits that fallback
silently, with NO `.iterrows()`/`.itertuples()`/`.apply(axis=1)` call
anywhere in *its own* file for the guard above to catch -- the row-wise
call lives in a different file (base.py) entirely. That gap is exactly
how CLAUDE.md's "all SignalModule implementations are natively
vectorized" claim went stale without any test catching it: as of this
audit, 7 of the 20 modules actually registered via
`global_registry.register(...)` do not override `compute_vectorized()`.

`test_registered_signal_module_overrides_compute_vectorized` makes this
enumerable and visible instead of invisible: any registered signal module
that doesn't override `compute_vectorized()` must be named in
`ALLOWED_ROW_WISE_SIGNAL_MODULES` with a reason, or the test fails. This
is deliberately NOT a ban -- per the audit, most of the 7 current entries
are low-cost (5 use the two-phase `pre_compute()` hook, so their per-row
`compute()` is a cheap dict lookup; the other 2 are trivial conditionals)
-- it is a debt-visibility gate: a NEW row-wise module, or a genuinely
expensive one, has to be added here explicitly rather than silently
inheriting the fallback.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

GUARDED_MODULES = [
    REPO_ROOT / "processing_engine.py",
    REPO_ROOT / "strategy_engine.py",
    *sorted((REPO_ROOT / "signals").glob("*.py")),
]

_BANNED_METHODS = {"iterrows", "itertuples"}


def _is_apply_axis1(node: ast.Call) -> bool:
    """True for a `<expr>.apply(..., axis=1)` call node specifically --
    NOT a bare `.apply(...)` (Series.apply, or DataFrame.apply with the
    columnwise axis=0 default, are both fine) and not `.apply(..., axis=0)`.
    """
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "apply"):
        return False
    for kw in node.keywords:
        if kw.arg == "axis" and isinstance(kw.value, ast.Constant) and kw.value.value == 1:
            return True
    return False


# Map of {relative_path: {lineno: reason}} for any deliberate, reviewed
# exception. `signals/base.py`'s `.apply(axis=1)` fallback is the one
# permanent entry: it is the intended generic implementation every
# non-overriding signal module inherits, not a bug in this file.
ALLOWED_EXCEPTIONS: dict[str, dict[int, str]] = {
    "signals/base.py": {
        220: (
            "Deliberate generic fallback for SignalModule.compute_vectorized() "
            "-- see test_registered_signal_module_overrides_compute_vectorized "
            "below for the guard on modules that inherit it."
        ),
    },
}

# {filename (relative to signals/): reason}. A registered signal module
# (has a module-level `global_registry.register(<Class>())` call) that
# does not override `compute_vectorized()` must be listed here, or the
# test below fails. See docs/module_efficiency_redundancy_audit.md's F1
# for the full per-module cost assessment behind each reason.
ALLOWED_ROW_WISE_SIGNAL_MODULES: dict[str, str] = {
    "multifactor.py": (
        "Two-phase pre_compute() hook already does the expensive cross-sectional "
        "z-scoring once per cycle; per-row compute() is a cheap dict lookup."
    ),
    "cross_sectional_momentum.py": (
        "Two-phase pre_compute() hook already ranks the full universe once per "
        "cycle; per-row compute() is a cheap dict lookup."
    ),
    "macro_regime.py": "Trivial per-row conditional on already-computed macro fields.",
    "regime_multiplier.py": "Trivial per-row conditional on already-computed regime fields.",
    "sector_quality_rank.py": (
        "Two-phase pre_compute() hook already ranks the sector universe once per "
        "cycle; per-row compute() is a cheap dict lookup."
    ),
    "lgbm_ranker.py": (
        "Audit-confirmed NOT a per-row model-inference cost -- prediction is "
        "already batched inside pre_compute(); per-row compute() only reads the "
        "precomputed score. (Corrects the original audit draft's assumption that "
        "this module does per-row inference.)"
    ),
    "news_catalyst.py": (
        "Per-row compute() reads already-fetched/cached headline scores; the "
        "expensive network/FinBERT work happens once per symbol in pre_compute(), "
        "not per row here."
    ),
}


def _find_banned_calls(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in _BANNED_METHODS:
            hits.append((node.lineno, node.func.attr))
        elif _is_apply_axis1(node):
            hits.append((node.lineno, "apply(axis=1)"))
    return hits


@pytest.mark.parametrize(
    "module_path", GUARDED_MODULES, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_module_has_no_row_wise_iteration(module_path: Path):
    rel = str(module_path.relative_to(REPO_ROOT))
    exceptions = ALLOWED_EXCEPTIONS.get(rel, {})

    hits = _find_banned_calls(module_path)
    unexplained = [
        (lineno, method) for lineno, method in hits if lineno not in exceptions
    ]

    assert not unexplained, (
        f"{rel} contains {len(unexplained)} unvectorized row-wise call(s) "
        f"(.iterrows()/.itertuples()/.apply(axis=1)) at line(s) "
        f"{[l for l, _ in unexplained]}, violating this codebase's "
        f"vectorization convention (CLAUDE.md: 'Technical/fundamental "
        f"math is vectorized -- no per-row Python loops.'). Replace with "
        f"a vectorized pandas/numpy equivalent, or -- if truly "
        f"unavoidable -- add a reviewed, commented entry to "
        f"ALLOWED_EXCEPTIONS in tests/test_no_iterrows_in_core_engines.py."
    )


# ---------------------------------------------------------------------------
# Second guard: a registered signal module that inherits the row-wise
# fallback by omission, rather than containing a row-wise call itself.
# ---------------------------------------------------------------------------

_SIGNALS_DIR = REPO_ROOT / "signals"
_INFRA_FILES = {"__init__.py", "base.py", "registry.py", "aggregator.py", "context.py"}


def _registered_signal_module_files() -> list[Path]:
    """Every signals/*.py file with a module-level
    `global_registry.register(<Class>())` call -- the actual, load-bearing
    definition of "a registered signal module" this codebase uses (see
    signals/registry.py::SignalRegistry.register and every call site).
    AST-based so a commented-out or docstring-mentioned `register(...)`
    is never mistaken for a real registration.
    """
    files = []
    for path in sorted(_SIGNALS_DIR.glob("*.py")):
        if path.name in _INFRA_FILES:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "global_registry"
            ):
                files.append(path)
                break
    return files


def _defines_compute_vectorized(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    return any(
        isinstance(node, ast.FunctionDef) and node.name == "compute_vectorized"
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize(
    "module_path", _registered_signal_module_files(), ids=lambda p: p.name
)
def test_registered_signal_module_overrides_compute_vectorized(module_path: Path):
    if _defines_compute_vectorized(module_path):
        return

    reason = ALLOWED_ROW_WISE_SIGNAL_MODULES.get(module_path.name)
    assert reason is not None, (
        f"signals/{module_path.name} is registered via "
        f"global_registry.register(...) but does not override "
        f"compute_vectorized() -- it silently inherits SignalModule's "
        f"row-wise df.apply(axis=1) fallback (signals/base.py), the exact "
        f"gap documented in docs/module_efficiency_redundancy_audit.md's "
        f"F1. Either add a compute_vectorized() override, or -- if the "
        f"per-row cost is genuinely acceptable (e.g. a cheap dict lookup "
        f"backed by a pre_compute() hook) -- add a reviewed, commented "
        f"entry to ALLOWED_ROW_WISE_SIGNAL_MODULES in "
        f"tests/test_no_iterrows_in_core_engines.py explaining why."
    )

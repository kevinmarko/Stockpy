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

# Map of {relative_path: {lineno: reason}} for any deliberate, reviewed
# exception. Empty today -- every guarded module is clean.
ALLOWED_EXCEPTIONS: dict[str, dict[int, str]] = {}


def _find_banned_calls(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _BANNED_METHODS
        ):
            hits.append((node.lineno, node.func.attr))
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
        f"{rel} contains {len(unexplained)} unvectorized "
        f".iterrows()/.itertuples() call(s) at line(s) "
        f"{[l for l, _ in unexplained]}, violating this codebase's "
        f"vectorization convention (CLAUDE.md: 'Technical/fundamental "
        f"math is vectorized -- no per-row Python loops.'). Replace with "
        f"a vectorized pandas/numpy equivalent, or -- if truly "
        f"unavoidable -- add a reviewed, commented entry to "
        f"ALLOWED_EXCEPTIONS in tests/test_no_iterrows_in_core_engines.py."
    )

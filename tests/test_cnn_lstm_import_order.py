"""Regression guard for the CNN-LSTM/TensorFlow import-order deadlock
(docs/known_issues/cnn_lstm_tf_deadlock.md, issue #381).

Round 6 of that investigation confirmed the deadlock is deterministic and
reproduces on real production data whenever a process's *own* top-level
import order lets `pandas`/`pyarrow` initialize before `tensorflow`. The
current production entry points (`main.py`, `main_orchestrator.py`,
`pipeline/production_steps.py`) are protected today only by convention -- a
guarded `import tensorflow` placed above their own `import pandas` -- with
no enforcement. This test is that enforcement: it statically parses each
entry point's source and fails if the ordering regresses, without needing a
real TensorFlow install or a real forecast run.

If you add a new entry point that can reach
`ForecastingEngine.generate_forecast()` / `run_cnn_lstm_forecast()` (a new
CLI script, a new orchestrator, a new daemon), add its path to
``GUARDED_ENTRY_POINTS`` below and give it the same guarded
``try: import tensorflow / except ImportError: pass`` before its own first
``pandas`` import.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

GUARDED_ENTRY_POINTS = [
    "main.py",
    "main_orchestrator.py",
    "pipeline/production_steps.py",
]


def _module_level_import_linenos(path: Path):
    """Return {top_level_package_name: first_lineno} for every import
    reachable at module-import time -- i.e. module-body imports, plus
    imports nested inside module-level `try`/`if` blocks (exactly how the
    TensorFlow guard is written), but NOT imports inside function/class
    bodies, which don't execute at import time.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    first_lineno = {}

    def record(name: str, lineno: int):
        top_level = name.split(".")[0]
        if top_level not in first_lineno or lineno < first_lineno[top_level]:
            first_lineno[top_level] = lineno

    def walk_module_level(stmts):
        for stmt in stmts:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    record(alias.name, stmt.lineno)
            elif isinstance(stmt, ast.ImportFrom):
                if stmt.module:
                    record(stmt.module, stmt.lineno)
            elif isinstance(stmt, ast.Try):
                walk_module_level(stmt.body)
                for handler in stmt.handlers:
                    walk_module_level(handler.body)
                walk_module_level(stmt.orelse)
                walk_module_level(stmt.finalbody)
            elif isinstance(stmt, ast.If):
                walk_module_level(stmt.body)
                walk_module_level(stmt.orelse)
            # Deliberately NOT descending into FunctionDef/AsyncFunctionDef/
            # ClassDef -- imports there don't run at module-import time.

    walk_module_level(tree.body)
    return first_lineno


@pytest.mark.parametrize("relative_path", GUARDED_ENTRY_POINTS)
def test_tensorflow_imported_before_pandas(relative_path):
    """A real production entry point must import tensorflow (if it imports
    it at all) before its own first pandas/pyarrow import -- the exact
    ordering Round 6 confirmed prevents the deadlock in practice.
    """
    path = REPO_ROOT / relative_path
    assert path.exists(), f"entry point moved or renamed: {relative_path}"

    imports = _module_level_import_linenos(path)

    if "tensorflow" not in imports:
        pytest.skip(
            f"{relative_path} does not import tensorflow at module level "
            "(nothing to guard against -- if this changes because the file "
            "now reaches CNN-LSTM code, it needs the guard added)."
        )

    tf_lineno = imports["tensorflow"]
    for risky_module in ("pandas", "pyarrow"):
        if risky_module in imports:
            assert tf_lineno < imports[risky_module], (
                f"{relative_path}: `import {risky_module}` (line "
                f"{imports[risky_module]}) appears before `import "
                f"tensorflow` (line {tf_lineno}). This exact ordering was "
                "confirmed (Round 6, docs/known_issues/cnn_lstm_tf_deadlock.md) "
                "to deterministically hang the first real CNN-LSTM eager op "
                "-- reorder so tensorflow is imported first."
            )

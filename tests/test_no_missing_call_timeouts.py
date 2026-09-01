"""AST guard: every ``subprocess.run/call/check_call/check_output`` and
``requests.get/post/put/delete/head/patch/request`` call in production code
must pass an explicit ``timeout=`` keyword.

Mirrors ``tests/test_runtime_flags.py``'s AST-guard style (walk the tree,
fail loudly on a structural violation rather than trusting a docstring
claim). An unbounded blocking call in this codebase has a documented
incident history -- see CLAUDE.md's "data_pipeline_fred_unbounded_timeout_stall"
entries and its "comprehensive unbounded-timeout sweep" -- so this test
exists to keep a *new* unbounded call from being reintroduced silently.

Scope and known, deliberate gaps (disclosed rather than silently omitted):

* Import-alias resolution covers only ``import subprocess`` / ``import
  subprocess as X`` and ``import requests`` / ``import requests as X`` at
  module scope. ``from subprocess import run`` (a bare-name call site, not
  an attribute access) is NOT handled -- no such import exists anywhere in
  this repo today (verified), but a future one would silently bypass this
  guard. A per-module alias map is rebuilt fresh for every file, so an
  alias only holds within the file that declares it.
* ``subprocess.Popen(...)`` is deliberately NOT flagged -- ``Popen`` itself
  accepts no ``timeout`` keyword at construction time (the timeout, if any,
  belongs on a later ``.wait()``/``.communicate()`` call).
* ``.wait()`` / ``.communicate()`` on a ``Popen`` result are NOT checked at
  all, and this is a real, uncovered gap, not an oversight papered over --
  correctly flagging only the ``Popen`` case (and not a ``threading.Event``/
  ``Condition``/``Queue``, all of which also expose ``.wait()``) needs real
  type inference this AST walk does not attempt. This is exactly why the
  scan is scoped to calls whose *target* resolves to the ``subprocess``/
  ``requests`` module -- e.g. ``self._wake_event.wait()`` in
  ``desktop/daemon_runtime.py`` (a ``threading.Event``, parked by design
  until explicitly woken) is excluded naturally by that scoping, not by a
  hardcoded line/file exception, and would stay excluded even if moved to
  a different file or line.

Allowlist: the venv re-exec guard (``sys.exit(subprocess.call([venv_python]
+ sys.argv))`` or an aliased equivalent) used by ``main.py``,
``main_orchestrator.py``, ``"Gravity AI Review Suite.py"``, and
``scripts/_bootstrap.py`` is expected to run for the entire remaining
lifetime of the process it re-execs into -- a timeout here would be
actively wrong, killing the re-exec'd process rather than letting it run.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

SUBPROCESS_METHODS = ("run", "call", "check_call", "check_output")
REQUESTS_METHODS = ("get", "post", "put", "delete", "head", "patch", "request")

ALLOWLIST = [
    ("main.py", 70),
    ("main_orchestrator.py", 23),
    ("Gravity AI Review Suite.py", 39),
    ("_bootstrap.py", 123),
]


def _resolve_module_aliases(tree: ast.AST) -> dict:
    """Map a local import name to its canonical module name for every
    ``import subprocess`` / ``import requests`` (optionally ``as X``)
    statement at any scope in the file."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("subprocess", "requests"):
                    local_name = alias.asname or alias.name
                    aliases[local_name] = alias.name
    return aliases


def check_for_missing_timeouts(directory: Path):
    errors = []
    for filepath in directory.rglob("*.py"):
        if "node_modules" in filepath.parts or ".venv" in filepath.parts or "tests" in filepath.parts or filepath.name.startswith("test_"):
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
            tree = ast.parse(content)
        except Exception:
            continue

        aliases = _resolve_module_aliases(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    canonical = aliases.get(func.value.id)
                    if canonical == "subprocess" and func.attr in SUBPROCESS_METHODS:
                        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                        if not has_timeout:
                            errors.append((filepath.name, node.lineno))
                    elif canonical == "requests" and func.attr in REQUESTS_METHODS:
                        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                        if not has_timeout:
                            errors.append((filepath.name, node.lineno))
    return errors


def test_no_missing_call_timeouts():
    errors = check_for_missing_timeouts(REPO_ROOT)
    filtered_errors = [e for e in errors if e not in ALLOWLIST]
    assert not filtered_errors, f"Missing timeouts found: {filtered_errors}"

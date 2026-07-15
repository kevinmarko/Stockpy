#!/usr/bin/env python3
"""
Stockpy Codebase Auditor
========================
A dependency-light (stdlib-only) static auditor for the InvestYo Quant Platform
("Stockpy") codebase. It scans every Python module for structural, security,
configuration, and code-quality issues and emits both a human-readable console
summary and a machine-readable JSON report.

Design principles
-----------------
* **Stdlib only.** Uses ``ast``, ``re``, ``json``, ``pathlib`` — no third-party
  dependency, so it runs in a bare virtualenv or CI runner with zero install.
* **Honest reporting.** It reports what it actually finds. It does not fabricate
  "known issues"; a clean codebase produces an empty finding list for that check.
  Line numbers and module names are attached to every finding so results are
  actionable, not vague.
* **Non-destructive.** Read-only. It never modifies, executes, or imports the
  code under audit (it parses the source with ``ast``), so it is safe to run
  against a live tree.

The ten audit areas mirror ``stockpy_audit_prompt.md``:
    1. Architecture & Dependencies   6. Backtesting integrity
    2. Security                      7. Robinhood / execution safety
    3. Configuration                 8. Error handling & observability
    4. Data pipeline                 9. Code quality
    5. Strategy layer               10. Known-issue heuristics

Usage
-----
    python stockpy_codebase_auditor.py [--root .] [--json report.json]
                                       [--include-tests] [--fail-on CRITICAL]
                                       [--quiet]

Exit code is 0 when no finding at or above ``--fail-on`` severity exists, else 1
(so it can gate CI). Default ``--fail-on`` is ``NONE`` (never fails the build).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Severity model
# ---------------------------------------------------------------------------

SEVERITY_ORDER = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITY_ORDER)}
SEVERITY_EMOJI = {
    "CRITICAL": "\U0001f534",  # red circle
    "HIGH": "\U0001f7e0",      # orange circle
    "MEDIUM": "\U0001f7e1",    # yellow circle
    "LOW": "\U0001f535",       # blue circle
    "INFO": "⚪",          # white circle
}

# Directories that are never part of the audited application surface.
DEFAULT_EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "env",
    ".git",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    "webapp",  # the PWA is TypeScript, not Python
}

# Files that are intentionally exempt from some checks (auditors/harnesses that
# legitimately contain strings resembling secrets or order verbs).
SELF_FILES = {"stockpy_codebase_auditor.py"}


@dataclass
class Finding:
    """A single audit result anchored to a file and (optionally) a line."""

    severity: str
    category: str
    check: str
    message: str
    module: str
    line: Optional[int] = None
    suggestion: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ModuleInfo:
    """Parsed facts about one Python module, gathered in a single AST pass."""

    path: Path
    rel: str
    dotted: str
    imports: Set[str] = field(default_factory=set)
    has_module_docstring: bool = False
    functions: int = 0
    documented_functions: int = 0
    typed_functions: int = 0
    public_functions: int = 0
    classes: int = 0
    documented_classes: int = 0
    parse_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Secret-detection patterns
# ---------------------------------------------------------------------------
# Each entry: (name, compiled regex, severity, suggestion). The regexes target
# literals assigned/embedded in source, and deliberately avoid matching
# ``os.environ`` / ``getenv`` / pydantic ``Field(...)`` references (filtered by
# the caller before flagging).

_SECRET_PATTERNS: List[Tuple[str, re.Pattern, str, str]] = [
    (
        "aws_access_key",
        re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
        "CRITICAL",
        "Move to an environment variable / secrets manager; rotate the exposed key.",
    ),
    (
        "aws_secret_key",
        re.compile(r"aws_secret_access_key\s*=\s*['\"][A-Za-z0-9/+=]{40}['\"]", re.IGNORECASE),
        "CRITICAL",
        "Never commit AWS secret keys; load from the environment and rotate.",
    ),
    (
        "fred_api_key",
        # FRED keys are exactly 32 lowercase hex chars.
        re.compile(r"(fred[_-]?api[_-]?key)\s*[:=]\s*['\"][0-9a-f]{32}['\"]", re.IGNORECASE),
        "CRITICAL",
        "FRED_API_KEY must come from the environment (settings.py), never a literal.",
    ),
    (
        "bearer_token",
        re.compile(r"(bearer\s+|authorization['\"]?\s*[:=]\s*['\"]bearer\s+)[A-Za-z0-9\-_.=]{20,}", re.IGNORECASE),
        "HIGH",
        "Read bearer tokens from settings/env; do not hardcode Authorization headers.",
    ),
    (
        "generic_api_key_assignment",
        re.compile(r"\b(api[_-]?key|secret[_-]?key|access[_-]?token|password|passwd|mfa[_-]?secret)\s*=\s*['\"][^'\"\s]{12,}['\"]", re.IGNORECASE),
        "HIGH",
        "Assign credentials from os.environ / settings, not string literals.",
    ),
    (
        "slack_discord_webhook",
        re.compile(r"https://(hooks\.slack\.com/services|discord(app)?\.com/api/webhooks)/[A-Za-z0-9/_-]{20,}"),
        "HIGH",
        "Webhook URLs are secrets; load ALERT_WEBHOOK_URL / DISCORD_WEBHOOK_URL from env.",
    ),
    (
        "postgres_dsn_with_password",
        re.compile(r"postgres(ql)?://[^:@\s'\"]+:[^@\s'\"]+@", re.IGNORECASE),
        "CRITICAL",
        "DATABASE_URL embeds user:pass@host — never commit it; use env (db_config.py).",
    ),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        "CRITICAL",
        "Remove committed private keys immediately and rotate them.",
    ),
]

# Lines that reference env/settings are legitimate even if they contain the
# words above — never flag them as hardcoded secrets.
_SECRET_ALLOW = re.compile(
    r"os\.environ|getenv|settings\.|Settings\(|Field\(|SecretStr|"
    r"description\s*=|:\s*Optional|:\s*str\b|example|placeholder|"
    r"mask|redact|<your|\bYOUR_|xxxx|\.\.\.|dummy|fixture|monkeypatch",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# I/O-without-error-handling heuristics
# ---------------------------------------------------------------------------
# Calls that perform network or filesystem I/O and, per the codebase's
# dead-letter convention (CONSTRAINT #6), should sit inside a try/except.
_IO_CALL_PATTERN = re.compile(
    r"\b("
    r"requests\.(get|post|put|delete|patch)"
    r"|urllib\.request\.urlopen|urlopen"
    r"|yf\.(download|Ticker)"
    r"|\.history\("
    r"|fred\.get_series"
    r"|\.get_series\("
    r"|socket\.(connect|create_connection)"
    r")"
)

# Order-execution verbs that must NOT appear outside the execution/ package
# (mirrors tests/test_pipeline_smoke.py::TestNoOrderFunctions — the advisory
# quarantine). We scan def names via AST, so this is a defensive duplicate.
_ORDER_DEF_PATTERN = re.compile(
    r"^(submit_order|buy_order|sell_order|place_order|place_equity_order|place_option_order|place_.+)$"
)
_ORDER_EXEMPT_PREFIXES = ("execution/", "tests/")
_ORDER_EXEMPT_FILES = {
    "Gravity AI Review Suite.py",
    "ai_verification_prompts.py",
    "stockpy_codebase_auditor.py",
}


# ---------------------------------------------------------------------------
# Auditor
# ---------------------------------------------------------------------------


class StockpyAuditor:
    def __init__(self, root: Path, include_tests: bool = False,
                 exclude_dirs: Optional[Set[str]] = None) -> None:
        self.root = root.resolve()
        self.include_tests = include_tests
        self.exclude_dirs = set(exclude_dirs or DEFAULT_EXCLUDE_DIRS)
        self.modules: Dict[str, ModuleInfo] = {}       # dotted -> info
        self.findings: List[Finding] = []
        self._local_dotted: Set[str] = set()
        self._env_declared: Set[str] = set()           # names in settings.py / .env.example

    # -- discovery ---------------------------------------------------------

    def _iter_py_files(self) -> Iterable[Path]:
        for path in sorted(self.root.rglob("*.py")):
            parts = set(path.relative_to(self.root).parts)
            if parts & self.exclude_dirs:
                continue
            rel = path.relative_to(self.root).as_posix()
            if not self.include_tests and (
                rel.startswith("tests/") or path.name.startswith("test_")
            ):
                continue
            yield path

    def _dotted_name(self, path: Path) -> str:
        rel = path.relative_to(self.root)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]  # strip .py
        return ".".join(parts)

    # -- parse pass --------------------------------------------------------

    def parse(self) -> None:
        for path in self._iter_py_files():
            dotted = self._dotted_name(path)
            rel = path.relative_to(self.root).as_posix()
            info = ModuleInfo(path=path, rel=rel, dotted=dotted)
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:  # pragma: no cover - unreadable file
                info.parse_error = f"read error: {exc}"
                self.modules[dotted] = info
                continue
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                info.parse_error = f"SyntaxError: {exc.msg} (line {exc.lineno})"
                self.modules[dotted] = info
                self._add(
                    "MEDIUM", "Code Quality", "parse_error",
                    f"Module does not parse under Python "
                    f"{sys.version_info.major}.{sys.version_info.minor}: {exc.msg}. "
                    f"May be a newer-syntax feature (this repo targets 3.12) rather "
                    f"than a real error — re-run the auditor under the project interpreter.",
                    rel, exc.lineno,
                    "Confirm the module parses under the project's Python version.",
                )
                self.modules[dotted] = info
                # keep source around for regex-only scans below
                self._scan_source_only(info, source)
                continue

            info.has_module_docstring = ast.get_docstring(tree) is not None
            self._walk_ast(info, tree)
            self._scan_source_only(info, source)
            self.modules[dotted] = info

        self._local_dotted = set(self.modules.keys())
        # Also index top-level package names so ``import data`` resolves.
        for dotted in list(self._local_dotted):
            head = dotted.split(".")[0]
            self._local_dotted.add(head)

    def _walk_ast(self, info: ModuleInfo, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    info.imports.add(alias.name.split(".")[0])
                    info.imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    info.imports.add(node.module.split(".")[0])
                    info.imports.add(node.module)
                    # `from pkg import submodule` — record pkg.submodule so a
                    # submodule imported via its package is not seen as orphaned.
                    for alias in node.names:
                        info.imports.add(f"{node.module}.{alias.name}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info.functions += 1
                if not node.name.startswith("_"):
                    info.public_functions += 1
                if ast.get_docstring(node) is not None:
                    info.documented_functions += 1
                if self._is_typed(node):
                    info.typed_functions += 1
                # order-verb quarantine check
                if _ORDER_DEF_PATTERN.match(node.name) and not self._order_exempt(info.rel):
                    self._add(
                        "HIGH", "Robinhood / Execution", "order_verb_outside_execution",
                        f"Function '{node.name}' defines an order-execution verb outside "
                        f"the execution/ package (advisory quarantine violation).",
                        info.rel, node.lineno,
                        "Order code must live only under execution/; advisory code stays read-only.",
                    )
            elif isinstance(node, ast.ClassDef):
                info.classes += 1
                if ast.get_docstring(node) is not None:
                    info.documented_classes += 1

    @staticmethod
    def _is_typed(node) -> bool:
        """A function counts as typed if it has a return annotation or any
        annotated non-self/cls argument."""
        if node.returns is not None:
            return True
        args = node.args
        annotated = any(a.annotation is not None for a in args.args)
        annotated = annotated or any(a.annotation is not None for a in getattr(args, "kwonlyargs", []))
        if args.vararg and args.vararg.annotation:
            annotated = True
        if args.kwarg and args.kwarg.annotation:
            annotated = True
        return annotated

    @staticmethod
    def _order_exempt(rel: str) -> bool:
        if rel in _ORDER_EXEMPT_FILES:
            return True
        return rel.startswith(_ORDER_EXEMPT_PREFIXES)

    def _scan_source_only(self, info: ModuleInfo, source: str) -> None:
        """Regex/line scans that don't need a valid AST."""
        if info.rel in SELF_FILES:
            return
        lines = source.splitlines()
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            self._scan_secrets(info, line, lineno)

    def _scan_secrets(self, info: ModuleInfo, line: str, lineno: int) -> None:
        if _SECRET_ALLOW.search(line):
            return
        for name, pattern, severity, suggestion in _SECRET_PATTERNS:
            if pattern.search(line):
                self._add(
                    severity, "Security", f"secret:{name}",
                    f"Possible hardcoded secret ({name}).",
                    info.rel, lineno, suggestion,
                )
                break  # one finding per line is enough

    # -- cross-module checks ----------------------------------------------

    def _resolve_local_imports(self, info: ModuleInfo) -> Set[str]:
        """Return the set of *local* module dotted names this module imports.

        Exact matches only (an import string that names a real module or package
        ``__init__``). We deliberately do NOT expand a bare package head to every
        submodule — that produced spurious cycles between unrelated files that
        merely share a top-level package.
        """
        resolved: Set[str] = {imp for imp in info.imports if imp in self.modules}
        resolved.discard(info.dotted)
        return resolved

    def check_architecture(self) -> None:
        # Build local import graph.
        graph: Dict[str, Set[str]] = {}
        for dotted, info in self.modules.items():
            graph[dotted] = self._resolve_local_imports(info)

        # 1) Circular dependencies (Tarjan SCCs of size > 1, or self-loops).
        for cycle in self._find_cycles(graph):
            pretty = " -> ".join(cycle + [cycle[0]])
            benign = self._is_package_reexport_cycle(cycle)
            severity = "LOW" if benign else "HIGH"
            note = (
                " (package/submodule re-export — usually benign, but verify no "
                "import-time side effects)" if benign else ""
            )
            self._add(
                severity, "Architecture", "circular_dependency",
                f"Circular import cycle: {pretty}{note}",
                cycle[0].replace(".", "/") + ".py", None,
                "Break the cycle with a lazy (in-function) import or a shared leaf module.",
            )

        # 2) Orphaned modules: imported by nobody and not an entry point.
        imported_by: Dict[str, int] = defaultdict(int)
        for deps in graph.values():
            for d in deps:
                imported_by[d] += 1
        for dotted, info in self.modules.items():
            if imported_by.get(dotted, 0) > 0:
                continue
            if self._is_entrypoint(info):
                continue
            self._add(
                "LOW", "Architecture", "orphaned_module",
                f"Module '{info.rel}' is imported by no other module and is not an "
                f"entry point/test/package marker.",
                info.rel, None,
                "Confirm it is a launcher/CLI; otherwise it may be dead code to remove.",
            )

    def _is_entrypoint(self, info: ModuleInfo) -> bool:
        name = info.path.name
        if name in {"__init__.py", "conftest.py", "settings.py", "config.py"}:
            return True
        # heavy launchers / apps / api services are legitimately unimported
        entry_markers = (
            "main", "app_shell", "orchestrator", "daemon", "auditor", "server",
            "preflight", "database_setup", "verify", "launch", "review suite",
        )
        low = name.lower()
        if any(m in low for m in entry_markers):
            return True
        rel = info.rel
        if rel.startswith(("scripts/", "api/", "gui/", "deploy/")):
            return True
        try:
            source = info.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            source = ""
        return '__name__ == "__main__"' in source or "__name__ == '__main__'" in source

    @staticmethod
    def _is_package_reexport_cycle(cycle: List[str]) -> bool:
        """A cycle is a benign package re-export if some node in it is an ancestor
        package (dotted prefix) of every other node — i.e. ``pkg`` re-exporting
        ``pkg.sub`` which imports from ``pkg``. These are common and usually
        harmless; a cross-package cycle is not."""
        for candidate in cycle:
            prefix = candidate + "."
            if all(other == candidate or other.startswith(prefix) for other in cycle):
                return True
        return False

    @staticmethod
    def _find_cycles(graph: Dict[str, Set[str]]) -> List[List[str]]:
        """Return a list of simple cycles (each as a node list) via Tarjan SCC."""
        index_counter = [0]
        stack: List[str] = []
        lowlink: Dict[str, int] = {}
        index: Dict[str, int] = {}
        on_stack: Dict[str, bool] = {}
        result: List[List[str]] = []

        def strongconnect(v: str) -> None:
            index[v] = index_counter[0]
            lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack[v] = True
            for w in graph.get(v, ()):  # neighbours
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif on_stack.get(w):
                    lowlink[v] = min(lowlink[v], index[w])
            if lowlink[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    result.append(list(reversed(comp)))
                elif v in graph.get(v, ()):  # self-loop
                    result.append([v])

        # iterative-safe recursion guard for deep graphs
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 5000))
        for node in graph:
            if node not in index:
                strongconnect(node)
        return result

    # -- security / config ------------------------------------------------

    def load_declared_env(self) -> None:
        """Collect env var names declared in settings.py and .env.example."""
        settings_path = self.root / "settings.py"
        if settings_path.exists():
            try:
                src = settings_path.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r"^\s*([A-Z][A-Z0-9_]{2,})\s*:", src, re.MULTILINE):
                    self._env_declared.add(m.group(1))
            except OSError:
                pass
        for env_name in (".env.example", ".env.sample", ".env"):
            p = self.root / env_name
            if p.exists():
                try:
                    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        self._env_declared.add(line.split("=", 1)[0].strip())
                except OSError:
                    pass

    def check_configuration(self) -> None:
        """Flag env vars read at runtime but never declared in settings/.env.example."""
        self.load_declared_env()
        # collect os.environ / getenv references across the tree
        env_refs: Dict[str, str] = {}  # name -> first module.rel
        ref_pattern = re.compile(
            r"os\.environ(?:\.get)?\(\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"
            r"|getenv\(\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"
        )
        for info in self.modules.values():
            try:
                src = info.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in ref_pattern.finditer(src):
                name = m.group(1) or m.group(2)
                env_refs.setdefault(name, info.rel)

        # Known aliases / dynamic names that are legitimately absent.
        benign = {"HOME", "PATH", "PWD", "USER", "TERM", "HTTPS_PROXY", "HTTP_PROXY",
                  "DATABASE_URL", "PYTEST_CURRENT_TEST", "CI"}
        for name, rel in sorted(env_refs.items()):
            if name in self._env_declared or name in benign:
                continue
            self._add(
                "MEDIUM", "Configuration", "undeclared_env_var",
                f"Environment variable '{name}' is read at runtime but not declared "
                f"in settings.py or .env.example.",
                rel, None,
                f"Add {name} to settings.Settings and document it in .env.example.",
            )

        # .env must be gitignored.
        gitignore = self.root / ".gitignore"
        env_ignored = False
        if gitignore.exists():
            gi = gitignore.read_text(encoding="utf-8", errors="replace")
            env_ignored = bool(re.search(r"^\s*\.env\s*$", gi, re.MULTILINE)) or ".env" in gi
        if (self.root / ".env").exists() and not env_ignored:
            self._add(
                "CRITICAL", "Security", "env_not_ignored",
                ".env exists but is not covered by .gitignore — secrets risk being committed.",
                ".gitignore", None,
                "Add '.env' to .gitignore before the next commit.",
            )
        if not (self.root / ".env.example").exists():
            self._add(
                "LOW", "Configuration", "no_env_example",
                "No .env.example / .env.sample template found for onboarding.",
                ".", None,
                "Provide a documented .env.example listing every required variable.",
            )

    # -- error handling ---------------------------------------------------

    def check_error_handling(self) -> None:
        """Flag network/file I/O calls that are not wrapped in try/except.

        Uses AST so we only flag genuine calls, and we consider a call 'guarded'
        when it lexically sits inside a Try node.
        """
        for info in self.modules.values():
            if info.parse_error or info.rel in SELF_FILES:
                continue
            try:
                tree = ast.parse(info.path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            guarded_lines = self._guarded_line_ranges(tree)
            src_lines = info.path.read_text(encoding="utf-8", errors="replace").splitlines()
            for lineno, line in enumerate(src_lines, start=1):
                if not _IO_CALL_PATTERN.search(line):
                    continue
                if any(lo <= lineno <= hi for lo, hi in guarded_lines):
                    continue
                self._add(
                    "MEDIUM", "Error Handling", "unguarded_io",
                    "Network/file I/O call is not inside a try/except (dead-letter "
                    "resilience, CONSTRAINT #6).",
                    info.rel, lineno,
                    "Wrap the call in try/except and degrade to a sentinel rather than raising.",
                )

    @staticmethod
    def _guarded_line_ranges(tree: ast.AST) -> List[Tuple[int, int]]:
        ranges: List[Tuple[int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                lo = node.body[0].lineno if node.body else node.lineno
                hi = lo
                for child in ast.walk(node):
                    if hasattr(child, "lineno"):
                        hi = max(hi, child.lineno)
                # only the try-body counts as guarded (not the except/finally)
                body_hi = lo
                for stmt in node.body:
                    for child in ast.walk(stmt):
                        if hasattr(child, "lineno"):
                            body_hi = max(body_hi, child.lineno)
                ranges.append((lo, body_hi))
        return ranges

    # -- code quality -----------------------------------------------------

    def check_code_quality(self) -> None:
        for info in self.modules.values():
            if info.parse_error:
                continue
            if not info.has_module_docstring and info.path.name != "__init__.py":
                self._add(
                    "LOW", "Code Quality", "missing_module_docstring",
                    f"Module '{info.rel}' has no module-level docstring.",
                    info.rel, 1,
                    "Add a one-paragraph module docstring describing its role.",
                )
            # type-hint coverage on public functions
            if info.functions >= 3:
                typed_ratio = info.typed_functions / info.functions
                if typed_ratio < 0.5:
                    self._add(
                        "LOW", "Code Quality", "low_type_hint_coverage",
                        f"Only {info.typed_functions}/{info.functions} functions in "
                        f"'{info.rel}' carry type annotations ({typed_ratio:.0%}).",
                        info.rel, None,
                        "Add parameter/return annotations; the codebase is type-annotated by convention.",
                    )
            # docstring coverage on public functions
            if info.public_functions >= 4 and info.documented_functions == 0:
                self._add(
                    "LOW", "Code Quality", "undocumented_public_api",
                    f"'{info.rel}' exposes {info.public_functions} public functions but "
                    f"none carry docstrings.",
                    info.rel, None,
                    "Document public functions so the API is discoverable.",
                )

    # -- known-issue heuristics (area 10) ---------------------------------

    def check_known_issues(self) -> None:
        """Targeted heuristics for the classes of bug called out in the audit
        prompt. These are *heuristics*: they point a reviewer at a location,
        they do not assert a defect exists."""
        for info in self.modules.values():
            try:
                src = info.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            low = src.lower()

            # (a) fabricated-metric smell: a metric defaulting to 0.0 where the
            #     codebase convention is NaN. Flag literal 'return 0.0' near the
            #     word 'metric'/'ratio' only in research/evaluation modules.
            if info.rel in {"research_engine.py", "evaluation_engine.py"}:
                for lineno, line in enumerate(src.splitlines(), start=1):
                    if re.search(r"return\s+0\.0\b", line) and (
                        "mfe" in line.lower() or "mae" in line.lower()
                        or "ratio" in line.lower() or "metric" in line.lower()
                    ):
                        self._add(
                            "MEDIUM", "Known Issues", "possible_fabricated_metric",
                            "Returns literal 0.0 for a performance metric — codebase "
                            "convention is NaN for 'not computable' (CONSTRAINT #4).",
                            info.rel, lineno,
                            "Return float('nan') on missing inputs, never a fabricated 0.0.",
                        )

            # (b) forecasting lookahead smell: scaler fit on full series before a
            #     train/test split in the forecasting engine.
            if "forecast" in info.rel.lower() and "scaler" in low:
                if re.search(r"\.fit_transform\(", src) and "train" not in low.split("fit_transform")[0][-200:].lower():
                    # weak signal only; low severity
                    self._add(
                        "LOW", "Known Issues", "possible_scaler_leakage",
                        "MinMaxScaler.fit_transform present — verify it is fit on the "
                        "train partition only (lookahead-bias risk).",
                        info.rel, None,
                        "Fit the scaler on train data, then transform test data separately.",
                    )

            # (c) Kelly-sizing duplication: a win-probability formula derived from
            #     score/sortino/edge outside the sizing single-source-of-truth.
            if info.rel not in {"strategy_engine.py"} and not info.rel.startswith("sizing/"):
                if re.search(r"(win[_ ]?prob|p_win|prob_win)\s*=", low) and (
                    "score" in low or "sortino" in low or "edge_ratio" in low
                ):
                    self._add(
                        "MEDIUM", "Strategy Layer", "kelly_sizing_duplication",
                        "A win-probability formula appears outside the sizing/ single "
                        "source of truth — risk of divergent Kelly implementations.",
                        info.rel, None,
                        "Route all sizing through sizing.kelly / StrategyEngine._calculate_kelly_sizing.",
                    )

    # -- helpers ----------------------------------------------------------

    def _add(self, severity: str, category: str, check: str, message: str,
             module: str, line: Optional[int], suggestion: str) -> None:
        self.findings.append(
            Finding(severity=severity, category=category, check=check,
                    message=message, module=module, line=line, suggestion=suggestion)
        )

    # -- orchestration ----------------------------------------------------

    def run(self) -> None:
        self.parse()
        self.check_architecture()
        self.check_configuration()
        self.check_error_handling()
        self.check_code_quality()
        self.check_known_issues()
        self.findings.sort(
            key=lambda f: (-SEVERITY_RANK.get(f.severity, 0), f.category, f.module, f.line or 0)
        )

    # -- reporting --------------------------------------------------------

    def summary_counts(self) -> Dict[str, int]:
        counts = {s: 0 for s in SEVERITY_ORDER}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    def to_report(self) -> dict:
        return {
            "tool": "stockpy_codebase_auditor",
            "version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "root": str(self.root),
            "modules_scanned": len(self.modules),
            "include_tests": self.include_tests,
            "severity_counts": self.summary_counts(),
            "total_findings": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
        }

    def print_console(self, quiet: bool = False) -> None:
        counts = self.summary_counts()
        line = "=" * 72
        print(line)
        print("STOCKPY CODEBASE AUDIT")
        print(line)
        print(f"Root            : {self.root}")
        print(f"Modules scanned : {len(self.modules)}")
        print(f"Total findings  : {len(self.findings)}")
        print("-" * 72)
        for sev in SEVERITY_ORDER[::-1]:
            print(f"  {SEVERITY_EMOJI[sev]} {sev:<9}: {counts[sev]}")
        print(line)

        if not self.findings:
            print("No findings. Codebase is clean against the current rule set.")
            return

        if quiet:
            return

        current_sev = None
        for f in self.findings:
            if f.severity != current_sev:
                current_sev = f.severity
                print(f"\n{SEVERITY_EMOJI[f.severity]} {f.severity} FINDINGS")
                print("-" * 72)
            loc = f"{f.module}:{f.line}" if f.line else f.module
            print(f"  [{f.category}/{f.check}] {loc}")
            print(f"      {f.message}")
            if f.suggestion:
                print(f"      → {f.suggestion}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Static auditor for the Stockpy quant platform codebase.",
    )
    parser.add_argument("--root", default=".", help="Repository root to audit (default: .)")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="Write the full JSON report to this path.")
    parser.add_argument("--include-tests", action="store_true",
                        help="Include tests/ and test_*.py files in the audit.")
    parser.add_argument("--fail-on", default="NONE",
                        choices=["NONE"] + SEVERITY_ORDER,
                        help="Exit non-zero if any finding at/above this severity exists "
                             "(default: NONE — never fails).")
    parser.add_argument("--quiet", action="store_true",
                        help="Print only the summary table, not individual findings.")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        print(f"error: root path does not exist: {root}", file=sys.stderr)
        return 2

    auditor = StockpyAuditor(root=root, include_tests=args.include_tests)
    auditor.run()
    auditor.print_console(quiet=args.quiet)

    if args.json_path:
        report = auditor.to_report()
        out = Path(args.json_path)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nJSON report written to {out}")

    if args.fail_on != "NONE":
        threshold = SEVERITY_RANK[args.fail_on]
        worst = max((SEVERITY_RANK[f.severity] for f in auditor.findings), default=-1)
        if worst >= threshold:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

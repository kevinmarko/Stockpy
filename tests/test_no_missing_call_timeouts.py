import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

ALLOWLIST = [
    ("investyo_mcp_server.py", 343),
    ("scripts/build_command_manifest.py", 136),
]

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
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id == "subprocess" and func.attr in ("run", "check_call", "check_output"):
                        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                        if not has_timeout:
                            errors.append((filepath.name, node.lineno))
                    elif func.value.id == "requests" and func.attr in ("get", "post", "put", "delete", "request", "patch"):
                        has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                        if not has_timeout:
                            errors.append((filepath.name, node.lineno))
    return errors

def test_no_missing_call_timeouts():
    errors = check_for_missing_timeouts(REPO_ROOT)
    filtered_errors = [e for e in errors if e not in ALLOWLIST]
    assert not filtered_errors, f"Missing timeouts found: {filtered_errors}"

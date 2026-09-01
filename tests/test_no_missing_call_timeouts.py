"""
tests/test_no_missing_call_timeouts.py
======================================
AST guard to flag any subprocess.run/call/check_call/check_output and requests.<method>
calls missing a timeout= keyword.
Do NOT flag subprocess.Popen(...) or .wait() - this is explicitly a known un-covered gap.
"""

import ast
from pathlib import Path

def check_missing_timeouts():
    root = Path(__file__).parent.parent
    errors = []
    
    for path in root.rglob("*.py"):
        if any(part in {".venv", "venv", "env", ".git", ".claude", ".gemini", "__pycache__", "node_modules", "dist", "build"} for part in path.parts):
            continue
            
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
            
        # track aliases
        subp_aliases = set(["subprocess"])
        req_aliases = set(["requests"])
        func_aliases = {} # name -> orig
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess":
                        subp_aliases.add(alias.asname or alias.name)
                    elif alias.name == "requests":
                        req_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module == "subprocess":
                    for alias in node.names:
                        if alias.name in {"run", "call", "check_call", "check_output"}:
                            func_aliases[alias.asname or alias.name] = alias.name
                elif node.module == "requests":
                    for alias in node.names:
                        if alias.name in {"get", "post", "put", "delete", "patch", "request"}:
                            func_aliases[alias.asname or alias.name] = alias.name
                            
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                is_target = False
                func_name = ""
                
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id in subp_aliases and node.func.attr in {"run", "call", "check_call", "check_output"}:
                            is_target = True
                            func_name = f"{node.func.value.id}.{node.func.attr}"
                        elif node.func.value.id in req_aliases and node.func.attr in {"get", "post", "put", "delete", "patch", "request"}:
                            is_target = True
                            func_name = f"{node.func.value.id}.{node.func.attr}"
                elif isinstance(node.func, ast.Name):
                    if node.func.id in func_aliases:
                        is_target = True
                        func_name = node.func.id
                        
                if is_target:
                    has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                    
                    if not has_timeout:
                        rel_path = path.relative_to(root).as_posix()
                        if rel_path in {"main.py", "main_orchestrator.py"}:
                            # Allowlist main.py and main_orchestrator.py's subprocess.call venv re-exec pattern
                            if "call" in func_name:
                                continue
                        errors.append(f"{rel_path}:{node.lineno} - {func_name} missing timeout")
                        
    return errors

def test_no_missing_call_timeouts():
    errors = check_missing_timeouts()
    assert not errors, "Found missing timeouts:\n" + "\n".join(errors)

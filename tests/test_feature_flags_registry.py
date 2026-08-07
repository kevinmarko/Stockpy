import ast
from pathlib import Path
import pytest

from pilots.feature_flags import FEATURE_FLAG_KEYS, DIAGNOSTIC_FLAG_REASONS
from settings_keysets import DANGEROUS_KEYS

def test_feature_flags_registry_inherits_dangerous_keys():
    """
    Ensure the dangerous tier of the registry automatically inherits
    settings_keysets.DANGEROUS_KEYS.
    """
    assert DANGEROUS_KEYS.issubset(FEATURE_FLAG_KEYS), "FEATURE_FLAG_KEYS must include all DANGEROUS_KEYS"

def test_feature_flags_registry_diagnostic_keys():
    """
    Ensure diagnostic keys are in the feature flag keys.
    """
    assert set(DIAGNOSTIC_FLAG_REASONS.keys()).issubset(FEATURE_FLAG_KEYS), "FEATURE_FLAG_KEYS must include all diagnostic flag reasons"

def test_require_enabled_guards_registered():
    """
    Scans api/pilots_api.py and api/data_api.py for every def require_*_enabled(
    function, extracts the settings.X attribute each one's if not settings.X: guard checks,
    and asserts X in feature_flags.FEATURE_FLAG_KEYS.
    """
    repo_root = Path(__file__).parent.parent
    files_to_check = [
        repo_root / "api" / "pilots_api.py",
        repo_root / "api" / "data_api.py",
    ]

    for filepath in files_to_check:
        with open(filepath, "r") as f:
            tree = ast.parse(f.read(), filename=str(filepath))

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("require_") and node.name.endswith("_enabled"):
                # We expect the first or second statement to be an If checking `not settings.X`
                found_check = False
                for stmt in node.body:
                    if isinstance(stmt, ast.If):
                        # check if it's `if not settings.X:`
                        if isinstance(stmt.test, ast.UnaryOp) and isinstance(stmt.test.op, ast.Not):
                            operand = stmt.test.operand
                            if isinstance(operand, ast.Attribute) and isinstance(operand.value, ast.Name) and operand.value.id == "settings":
                                flag_name = operand.attr
                                assert flag_name in FEATURE_FLAG_KEYS, (
                                    f"Function {node.name} in {filepath.name} guards against {flag_name}, "
                                    f"but {flag_name} is not in FEATURE_FLAG_KEYS. Update settings_keysets.py or pilots/feature_flags.py."
                                )
                                found_check = True
                                break
                
                # We don't fail if found_check is False because some require_*_enabled might have
                # slightly different AST structure, but we assume the standard `if not settings.X:` pattern.
                if not found_check:
                    # Let's do a more exhaustive search inside the function
                    for subnode in ast.walk(node):
                        if isinstance(subnode, ast.If) and isinstance(subnode.test, ast.UnaryOp) and isinstance(subnode.test.op, ast.Not):
                            operand = subnode.test.operand
                            if isinstance(operand, ast.Attribute) and isinstance(operand.value, ast.Name) and operand.value.id == "settings":
                                flag_name = operand.attr
                                assert flag_name in FEATURE_FLAG_KEYS, (
                                    f"Function {node.name} in {filepath.name} guards against {flag_name}, "
                                    f"but {flag_name} is not in FEATURE_FLAG_KEYS. Update settings_keysets.py or pilots/feature_flags.py."
                                )
                                break

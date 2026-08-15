"""
InvestYo Quant Platform - LLM Research & Synthesis Engine (Research Copilot)
=============================================================================
Synthesizes AST-safe Python `SignalModule` implementations from quantitative
hypotheses, academic paper abstracts, or mathematical formulas.

Key Responsibilities:
1. `validate_ast_safety(code: str) -> Tuple[bool, List[str]]`:
   Strict AST verification against a quantitative whitelist (math, numpy, scipy,
   pandas, stdlib typing/dataclasses/etc.) strictly blocking eval, exec, os, sys,
   subprocess, socket, private/dunder attribute mutation, and unsafe built-ins.
2. `extract_strategy_metadata(code: str) -> Dict[str, Any]`:
   Pure AST-based extraction of strategy metadata (name, class_name, description,
   required_features, parameters, methods present, imports) without code execution.
3. `ResearchCopilot`:
   The synthesis engine that bridges natural-language quant research and
   executable, production-ready SignalModule classes with AST safety guarantees.
4. `instantiate_module(code: str) -> SignalModule`:
   Safe sandbox instantiation of verified SignalModule classes for test harness
   and validation pipelines.
"""

from __future__ import annotations

import ast
import logging
import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import SignalRegistry, global_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AST Safety Whitelists and Blacklists
# ---------------------------------------------------------------------------

ALLOWED_ROOT_MODULES: set[str] = {
    "math",
    "numpy",
    "np",
    "scipy",
    "pandas",
    "pd",
    "typing",
    "dataclasses",
    "abc",
    "datetime",
    "collections",
    "enum",
    "decimal",
    "fractions",
    "statistics",
    "signals",
    "dto_models",
}

ALLOWED_SIGNAL_SUBMODULES: set[str] = {
    "signals",
    "signals.base",
    "signals.registry",
}

# Forbidden built-ins and callables
FORBIDDEN_CALL_NAMES: set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "input",
    "breakpoint",
    "exit",
    "quit",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "execfile",
    "memoryview",
}

# Forbidden method names associated with system I/O, subprocess, networking, or eval
FORBIDDEN_METHOD_NAMES: set[str] = {
    "system",
    "popen",
    "spawn",
    "fork",
    "exec",
    "execl",
    "execv",
    "execle",
    "execlp",
    "execvp",
    "execvpe",
    "socket",
    "connect",
    "bind",
    "listen",
    "send",
    "recv",
    "sendall",
    "recvfrom",
    "urlopen",
    "urlretrieve",
    "request",
    "eval",
    "query",
    "read_pickle",
    "to_pickle",
    "to_sql",
    "read_csv",
    "read_table",
    "read_parquet",
    "read_json",
    "read_excel",
    "read_html",
    "read_xml",
    "read_sql",
    "read_feather",
    "read_hdf",
    "to_csv",
    "to_parquet",
    "to_json",
    "to_excel",
    "to_html",
    "to_xml",
    "to_string",
    "to_clipboard",
    "to_feather",
    "to_hdf",
    "loadtxt",
    "genfromtxt",
    "load",
    "save",
    "savez",
    "savez_compressed",
    "fromfile",
    "tofile",
}


# ---------------------------------------------------------------------------
# AST Safety Validator
# ---------------------------------------------------------------------------

class _ASTSafetyVisitor(ast.NodeVisitor):
    """AST visitor that checks for security violations and forbidden operations."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            mod_name = alias.name
            root_mod = mod_name.split(".")[0]
            if root_mod not in ALLOWED_ROOT_MODULES:
                self.violations.append(
                    f"Line {node.lineno}: Forbidden import '{mod_name}'. Only quantitative math/data modules allowed."
                )
            elif root_mod == "signals" and mod_name not in ALLOWED_SIGNAL_SUBMODULES:
                self.violations.append(
                    f"Line {node.lineno}: Forbidden import from signals package: '{mod_name}'. Only signals.base and signals.registry allowed."
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None or node.level > 0:
            self.violations.append(
                f"Line {node.lineno}: Relative imports are not permitted in signal modules."
            )
            return
        mod_name = node.module
        root_mod = mod_name.split(".")[0]
        if root_mod not in ALLOWED_ROOT_MODULES:
            self.violations.append(
                f"Line {node.lineno}: Forbidden import from '{mod_name}'. Only quantitative math/data modules allowed."
            )
        elif root_mod == "signals" and mod_name not in ALLOWED_SIGNAL_SUBMODULES:
            self.violations.append(
                f"Line {node.lineno}: Forbidden import from signals package: '{mod_name}'. Only signals.base and signals.registry allowed."
            )
        for alias in node.names:
            if alias.name in FORBIDDEN_CALL_NAMES:
                self.violations.append(
                    f"Line {node.lineno}: Forbidden symbol import '{alias.name}'."
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_CALL_NAMES:
            self.violations.append(
                f"Line {node.lineno}: Forbidden reference to '{node.id}'."
            )
        elif node.id.startswith("__") and node.id.endswith("__") and node.id not in ("__name__", "__doc__"):
            self.violations.append(
                f"Line {node.lineno}: Forbidden reference to dunder name '{node.id}'."
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALL_NAMES:
                self.violations.append(
                    f"Line {node.lineno}: Forbidden call to '{node.func.id}()'."
                )
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in FORBIDDEN_METHOD_NAMES:
                self.violations.append(
                    f"Line {node.lineno}: Forbidden method call '{attr_name}()'."
                )
            if attr_name.startswith("__"):
                self.violations.append(
                    f"Line {node.lineno}: Forbidden call on dunder attribute '{attr_name}'."
                )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr not in ("__name__", "__doc__"):
            self.violations.append(
                f"Line {node.lineno}: Forbidden access to dunder attribute '{node.attr}'."
            )
        if node.attr in FORBIDDEN_METHOD_NAMES:
            self.violations.append(
                f"Line {node.lineno}: Forbidden attribute access to '{node.attr}'."
            )
        if node.attr in FORBIDDEN_CALL_NAMES:
            self.violations.append(
                f"Line {node.lineno}: Forbidden attribute access to '{node.attr}'."
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_assign_targets(node.targets, node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_assign_targets([node.target], node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_assign_targets([node.target], node.lineno)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        self._check_assign_targets(node.targets, node.lineno)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.violations.append(
            f"Line {node.lineno}: Global statement modification is forbidden."
        )
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.violations.append(
            f"Line {node.lineno}: Nonlocal statement modification is forbidden."
        )
        self.generic_visit(node)

    def _check_assign_targets(self, targets: list[ast.AST], lineno: int) -> None:
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr.startswith("__"):
                self.violations.append(
                    f"Line {lineno}: Forbidden mutation of dunder attribute '{target.attr}'."
                )
            elif isinstance(target, ast.Name) and target.id.startswith("__") and target.id.endswith("__"):
                self.violations.append(
                    f"Line {lineno}: Forbidden assignment to dunder name '{target.id}'."
                )


def validate_ast_safety(code: str) -> tuple[bool, list[str]]:
    """Validate that Python source code complies with quantitative AST safety rules.

    Checks:
    - Code is non-empty and parses cleanly without SyntaxError.
    - All imports are whitelisted (math, numpy, scipy, pandas, typing, dataclasses,
      signals.base, signals.registry, dto_models).
    - Blocks all unsafe modules (os, sys, subprocess, socket, requests, builtins, etc.).
    - Blocks dangerous built-ins (eval, exec, compile, open, getattr, setattr, delattr, globals, locals).
    - Blocks private/dunder attribute access and mutation (__class__, __dict__, __globals__, etc.).
    - Blocks socket creation and system command execution.

    Parameters
    ----------
    code : str
        Python source code to inspect.

    Returns
    -------
    tuple[bool, list[str]]
        (is_safe, list_of_violations). If is_safe is True, list_of_violations is empty.
    """
    if not isinstance(code, str) or not code.strip():
        return False, ["Code is empty or invalid string."]

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, [f"Syntax error at line {exc.lineno}: {exc.msg}"]
    except Exception as exc:  # noqa: BLE001
        return False, [f"Parse error: {exc!s}"]

    visitor = _ASTSafetyVisitor()
    visitor.visit(tree)

    is_safe = len(visitor.violations) == 0
    return is_safe, visitor.violations


# ---------------------------------------------------------------------------
# Strategy Metadata Extractor
# ---------------------------------------------------------------------------

def _extract_literal_value(node: ast.AST) -> Any:
    """Safely extract a literal value (str, int, float, bool, list, dict) from an AST node."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_extract_literal_value(elt) for elt in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_extract_literal_value(elt) for elt in node.elts)
    if isinstance(node, ast.Dict):
        res = {}
        for k, v in zip(node.keys, node.values):
            if k is not None:
                key_val = _extract_literal_value(k)
                res[key_val] = _extract_literal_value(v)
        return res
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        return -node.operand.value
    return None


def extract_strategy_metadata(code: str) -> dict[str, Any]:
    """Pure AST-based extraction of strategy metadata without code execution.

    Extracts:
    - `name`: Module slug (from class attribute `name: str = "..."` or snake_case class name).
    - `class_name`: PascalCase SignalModule subclass name.
    - `description`: Class docstring or module docstring.
    - `required_features`: List of required input DataFrame column names.
    - `parameters`: Dictionary of default parameter tunables defined on the class.
    - `has_pre_compute`: Boolean indicating if `pre_compute()` is implemented.
    - `has_vectorized`: Boolean indicating if `compute_vectorized()` is implemented.
    - `has_regime_gate`: Boolean indicating if `is_active_in_regime()` is implemented.
    - `meta_label_features`: List of feature names for meta-labeling, if defined.
    - `imports`: List of imported modules and submodules.
    - `is_valid_signal_module`: Boolean indicating if a valid SignalModule class was found.

    Parameters
    ----------
    code : str
        Python source code to analyze.

    Returns
    -------
    dict[str, Any]
        Dictionary containing extracted strategy metadata.
    """
    default_metadata: dict[str, Any] = {
        "name": "",
        "class_name": "",
        "description": "",
        "required_features": [],
        "parameters": {},
        "has_pre_compute": False,
        "has_vectorized": False,
        "has_regime_gate": False,
        "meta_label_features": [],
        "imports": [],
        "is_valid_signal_module": False,
    }

    if not isinstance(code, str) or not code.strip():
        return default_metadata

    try:
        tree = ast.parse(code)
    except Exception as exc:  # noqa: BLE001
        default_metadata["error"] = f"Failed to parse AST: {exc}"
        return default_metadata

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    default_metadata["imports"] = sorted(set(imports))
    module_docstring = ast.get_docstring(tree) or ""

    # Locate class inheriting from SignalModule or with compute method
    signal_class: ast.ClassDef | None = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            # Check base classes
            is_signal_subclass = False
            for base in node.bases:
                if (
                    isinstance(base, ast.Name) and base.id == "SignalModule"
                ) or (
                    isinstance(base, ast.Attribute) and base.attr == "SignalModule"
                ):
                    is_signal_subclass = True
                    break
            
            # If explicit base or has compute method, treat as signal module
            has_compute = any(
                isinstance(item, ast.FunctionDef) and item.name == "compute"
                for item in node.body
            )
            if is_signal_subclass or has_compute:
                signal_class = node
                break

    if signal_class is None:
        default_metadata["description"] = module_docstring
        return default_metadata

    class_name = signal_class.name
    class_docstring = ast.get_docstring(signal_class) or module_docstring
    name_val = ""
    req_features: list[str] = []
    meta_features: list[str] = []
    parameters: dict[str, Any] = {}
    has_pre_compute = False
    has_vectorized = False
    has_regime_gate = False

    reserved_class_attrs = {
        "name",
        "required_features",
        "meta_label_features",
        "meta_label_horizons",
    }

    for item in signal_class.body:
        if isinstance(item, ast.FunctionDef):
            if item.name == "pre_compute":
                has_pre_compute = True
            elif item.name == "compute_vectorized":
                has_vectorized = True
            elif item.name == "is_active_in_regime":
                has_regime_gate = True

        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    target_id = target.id
                    val = _extract_literal_value(item.value)
                    if target_id == "name" and isinstance(val, str):
                        name_val = val
                    elif target_id == "required_features" and isinstance(val, list):
                        req_features = [str(x) for x in val]
                    elif target_id == "meta_label_features" and isinstance(val, list):
                        meta_features = [str(x) for x in val]
                    elif target_id not in reserved_class_attrs and val is not None:
                        parameters[target_id] = val
                elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(item.value, (ast.Tuple, ast.List)):
                    for elt_target, elt_val_node in zip(target.elts, item.value.elts):
                        if isinstance(elt_target, ast.Name):
                            target_id = elt_target.id
                            val = _extract_literal_value(elt_val_node)
                            if target_id == "name" and isinstance(val, str):
                                name_val = val
                            elif target_id == "required_features" and isinstance(val, list):
                                req_features = [str(x) for x in val]
                            elif target_id == "meta_label_features" and isinstance(val, list):
                                meta_features = [str(x) for x in val]
                            elif target_id not in reserved_class_attrs and val is not None:
                                parameters[target_id] = val

        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            target_id = item.target.id
            val = _extract_literal_value(item.value) if item.value else None
            if target_id == "name" and isinstance(val, str):
                name_val = val
            elif target_id == "required_features" and isinstance(val, list):
                req_features = [str(x) for x in val]
            elif target_id == "meta_label_features" and isinstance(val, list):
                meta_features = [str(x) for x in val]
            elif target_id not in reserved_class_attrs and val is not None:
                parameters[target_id] = val

    if not name_val:
        # Generate snake_case fallback from class name
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", class_name)
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
        snake = snake.removesuffix("_signal")
        name_val = snake

    return {
        "name": name_val,
        "class_name": class_name,
        "description": class_docstring,
        "required_features": req_features,
        "parameters": parameters,
        "has_pre_compute": has_pre_compute,
        "has_vectorized": has_vectorized,
        "has_regime_gate": has_regime_gate,
        "meta_label_features": meta_features,
        "imports": default_metadata["imports"],
        "is_valid_signal_module": True,
    }


# ---------------------------------------------------------------------------
# Structured Schemas & Result Objects
# ---------------------------------------------------------------------------

class SynthesizedSignalModule(BaseModel):
    """Structured output representation of a synthesized quantitative signal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        min_length=1,
        max_length=60,
        description="Unique snake_case identifier for the signal module (e.g. 'rsi2_mean_reversion').",
    )
    class_name: str = Field(
        min_length=1,
        max_length=80,
        description="PascalCase class name inheriting from SignalModule (e.g. 'RSI2MeanReversionSignal').",
    )
    description: str = Field(
        min_length=1,
        max_length=800,
        description="1-3 sentence summary of the strategy rationale and quantitative thesis.",
    )
    code: str = Field(
        min_length=10,
        description="Complete, executable, AST-safe Python code implementing the SignalModule class.",
    )
    required_features: list[str] = Field(
        default_factory=list,
        description="List of required DataFrame column names (e.g. ['Close', 'RSI_14']).",
    )
    rationale: str = Field(
        min_length=1,
        max_length=1000,
        description="Academic citation, quantitative theory, or mathematical formula backing the signal.",
    )


@dataclass
class StrategySynthesisResult:
    """Result container for ResearchCopilot code synthesis operations."""

    success: bool
    code: str
    metadata: dict[str, Any]
    validation_passed: bool
    validation_errors: list[str]
    source_prompt: str
    synthesis_mode: str
    explanation: str = ""
    module_instance: SignalModule | None = None


# ---------------------------------------------------------------------------
# Safe Sandbox Instantiation
# ---------------------------------------------------------------------------

def instantiate_module(code: str) -> SignalModule:
    """Safely instantiate a SignalModule subclass from validated source code.

    Parameters
    ----------
    code : str
        Python source code defining a SignalModule subclass.

    Returns
    -------
    SignalModule
        An instantiated instance of the synthesized SignalModule.

    Raises
    ------
    ValueError
        If AST validation fails or no SignalModule subclass is found.
    """
    is_safe, violations = validate_ast_safety(code)
    if not is_safe:
        raise ValueError(
            f"Code failed AST safety validation with violations: {'; '.join(violations)}"
        )

    import builtins

    def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if root not in ALLOWED_ROOT_MODULES:
            raise ImportError(f"Import of '{name}' is forbidden by AST sandbox.")
        if root == "signals" and name not in ALLOWED_SIGNAL_SUBMODULES:
            raise ImportError(f"Import from signals '{name}' is forbidden by AST sandbox.")
        return __import__(name, globals, locals, fromlist, level)

    safe_builtins = builtins.__dict__.copy()
    for dangerous in (
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
    ):
        safe_builtins.pop(dangerous, None)
    safe_builtins["__import__"] = _restricted_import

    safe_globals: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "pd": pd,
        "pandas": pd,
        "np": np,
        "numpy": np,
        "math": math,
        "SignalModule": SignalModule,
        "SignalContext": SignalContext,
        "SignalOutput": SignalOutput,
        "global_registry": SignalRegistry(),
    }

    local_namespace: dict[str, Any] = {}
    saved_modules = set(global_registry._modules.keys())
    try:
        compiled_code = compile(code, "<synthesized_signal>", "exec")
        exec(compiled_code, safe_globals, local_namespace)  # noqa: S102
    except Exception as exc:
        raise ValueError(f"Failed to execute synthesized signal code in safe sandbox: {exc}") from exc
    finally:
        for k in list(global_registry._modules.keys()):
            if k not in saved_modules:
                global_registry.unregister(k)

    module_class: type[SignalModule] | None = None
    for obj in local_namespace.values():
        if isinstance(obj, type) and issubclass(obj, SignalModule) and obj is not SignalModule:
            module_class = obj
            break

    if module_class is None:
        for obj in safe_globals.values():
            if isinstance(obj, type) and issubclass(obj, SignalModule) and obj is not SignalModule:
                module_class = obj
                break

    if module_class is None:
        raise ValueError("No SignalModule subclass found in synthesized code execution namespace.")

    return module_class()


def verify_signal_module(
    code: str, sample_df: pd.DataFrame | None = None
) -> tuple[bool, dict[str, Any]]:
    """Comprehensive test harness verifying AST safety, instantiation, and execution.

    Parameters
    ----------
    code : str
        Python source code to verify.
    sample_df : Optional[pd.DataFrame]
        Sample universe DataFrame for testing vectorized and row compute.

    Returns
    -------
    Tuple[bool, Dict[str, Any]]
        (passed, verification_report_dict)
    """
    report: dict[str, Any] = {
        "ast_safe": False,
        "ast_violations": [],
        "metadata": {},
        "instantiated": False,
        "compute_verified": False,
        "vectorized_verified": False,
        "errors": [],
    }

    # 1. AST Validation
    is_safe, violations = validate_ast_safety(code)
    report["ast_safe"] = is_safe
    report["ast_violations"] = violations
    if not is_safe:
        report["errors"].extend(violations)
        return False, report

    # 2. Metadata Extraction
    metadata = extract_strategy_metadata(code)
    report["metadata"] = metadata

    # 3. Instantiation
    try:
        instance = instantiate_module(code)
        report["instantiated"] = True
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"Instantiation failed: {exc}")
        return False, report

    # 4. Generate Default Sample Data if omitted
    if sample_df is None:
        req_cols = metadata.get("required_features") or ["Close", "RSI_14", "SMA_20"]
        data_dict = {"Symbol": ["AAPL", "MSFT", "NVDA", "GOOGL"]}
        for col in req_cols:
            if "RSI" in col:
                data_dict[col] = [25.0, 75.0, 50.0, 50.0]
            elif "SMA" in col or "Close" in col or "EMA" in col or "Price" in col or "High" in col or "Low" in col:
                data_dict[col] = [150.0, 200.0, 180.0, 100.0]
            elif "StdDev" in col or "ATR" in col or "Vol" in col:
                data_dict[col] = [5.0, 8.0, 6.0, 4.0]
            else:
                data_dict[col] = [1.0, 2.0, 3.0, 4.0]
        sample_df = pd.DataFrame(data_dict)

    # 5. Row-by-Row Compute Verification
    try:
        first_row = sample_df.iloc[0]
        dummy_context = SignalContext(bar=None, fundamentals=None, macro=None)
        out = instance.compute(first_row, dummy_context)
        if isinstance(out, SignalOutput):
            if -1.0 <= out.score <= 1.0 and 0.0 <= out.confidence <= 1.0:
                report["compute_verified"] = True
            else:
                report["errors"].append(f"Invalid SignalOutput bounds: score={out.score}, conf={out.confidence}")
        else:
            report["errors"].append(f"compute() did not return SignalOutput instance: {type(out)}")
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"compute() raised exception: {exc}")

    # 6. Vectorized Compute Verification
    try:
        vec_out = instance.compute_vectorized(sample_df, dummy_context)
        if isinstance(vec_out, pd.DataFrame) and "score" in vec_out.columns:
            scores = vec_out["score"].dropna()
            if (scores >= -1.0).all() and (scores <= 1.0).all():
                report["vectorized_verified"] = True
            else:
                report["errors"].append("Vectorized scores out of [-1.0, 1.0] bounds.")
        else:
            report["errors"].append("compute_vectorized() did not return DataFrame with 'score' column.")
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"compute_vectorized() raised exception: {exc}")

    passed = (
        report["ast_safe"]
        and report["instantiated"]
        and report["compute_verified"]
        and report["vectorized_verified"]
    )
    return passed, report


# ---------------------------------------------------------------------------
# Intelligent Deterministic Template Synthesizer (Zero Network Fallback)
# ---------------------------------------------------------------------------

def _to_pascal_case(snake_str: str) -> str:
    """Convert snake_case to PascalCase."""
    components = snake_str.split("_")
    return "".join(x.title() for x in components if x)


def _to_snake_case(name_str: str) -> str:
    """Convert input string to snake_case."""
    cleaned = re.sub(r"[^\w\s-]", "", name_str).strip()
    s1 = re.sub(r"[\s-]+", "_", cleaned)
    s2 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s1)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s2).lower()


def _synthesize_template_code(
    prompt: str,
    name: str | None = None,
    required_features: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    mode: str = "hypothesis",
) -> SynthesizedSignalModule:
    """Intelligently synthesize complete, AST-safe SignalModule code using deterministic templates."""
    prompt_lower = prompt.lower()
    params = parameters.copy() if parameters else {}

    is_rsi = bool(re.search(r"\brsi\b|\brsi\d+|\brsi_\d+|\brsi\(|\boversold\b|\boverbought\b", prompt_lower))
    is_zscore = bool(re.search(r"\bz-?score\b|\bbollinger\b|\bstddev\b|\bstandard deviation\b|\bmean reversion\b", prompt_lower)) and not is_rsi
    is_breakout = bool(re.search(r"\bbreakout\b|\batr\b|\bvolatility expansion\b|\brange expansion\b", prompt_lower))
    is_momentum = bool(re.search(r"\bmomentum\b|\btrend\b|\bmoving average\b|\bsma\b|\bema\b|\bwinner\b|\bcrossover\b", prompt_lower))

    # 1. Determine Module Slug & Class Name
    if name:
        slug = _to_snake_case(name)
    elif is_rsi:
        slug = "rsi_mean_reversion"
    elif is_zscore:
        slug = "bollinger_zscore_reversion"
    elif is_breakout:
        slug = "volatility_breakout"
    elif is_momentum:
        slug = "trend_momentum"
    else:
        slug = "quant_alpha_signal"

    if not slug.endswith("_signal"):
        class_name = f"{_to_pascal_case(slug)}Signal"
    else:
        class_name = _to_pascal_case(slug)

    # 2. Determine Strategy Archetype & Logic
    if is_rsi:
        features = required_features or ["Close", "RSI_14"]
        lookback = params.get("lookback", 14)
        ob_thresh = params.get("overbought_threshold", 70.0)
        os_thresh = params.get("oversold_threshold", 30.0)
        
        code = f'''"""
InvestYo Quant Platform - {class_name}
=======================================
Synthesized quantitative signal module based on:
{prompt.strip()}
"""

import numpy as np
import pandas as pd
from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import global_registry


class {class_name}(SignalModule):
    """RSI-based mean reversion and extreme counter-trend signal."""

    name = "{slug}"
    required_features = {features!r}
    
    lookback: int = {lookback}
    overbought_threshold: float = {ob_thresh}
    oversold_threshold: float = {os_thresh}

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        rsi_val = row.get("RSI_14")
        if rsi_val is None or pd.isna(rsi_val):
            return SignalOutput(score=0.0, confidence=0.0, explanation="Missing RSI_14 observation")

        rsi_f = float(rsi_val)
        if rsi_f >= self.overbought_threshold:
            # Overbought condition -> Bearish reversal signal
            excess = min(1.0, (rsi_f - self.overbought_threshold) / (100.0 - self.overbought_threshold + 1e-6))
            score = -0.5 - (0.5 * excess)
            exp = f"RSI {{rsi_f:.1f}} >= {{self.overbought_threshold}} (Overbought reversal)"
        elif rsi_f <= self.oversold_threshold:
            # Oversold condition -> Bullish reversal signal
            discount = min(1.0, (self.oversold_threshold - rsi_f) / (self.oversold_threshold + 1e-6))
            score = 0.5 + (0.5 * discount)
            exp = f"RSI {{rsi_f:.1f}} <= {{self.oversold_threshold}} (Oversold reversal)"
        else:
            score = 0.0
            exp = f"RSI {{rsi_f:.1f}} neutral zone"

        return SignalOutput(score=float(score), confidence=1.0, explanation=exp)

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        score = pd.Series(0.0, index=df.index, dtype=float)
        exp = pd.Series("Neutral", index=df.index, dtype=str)

        if "RSI_14" in df.columns:
            rsi = pd.to_numeric(df["RSI_14"], errors="coerce")
            valid = rsi.notna()

            ob_mask = valid & (rsi >= self.overbought_threshold)
            os_mask = valid & (rsi <= self.oversold_threshold)

            if ob_mask.any():
                excess = (rsi[ob_mask] - self.overbought_threshold) / (100.0 - self.overbought_threshold + 1e-6)
                score[ob_mask] = -0.5 - (0.5 * excess.clip(0.0, 1.0))
                exp[ob_mask] = "Overbought reversal short"

            if os_mask.any():
                discount = (self.oversold_threshold - rsi[os_mask]) / (self.oversold_threshold + 1e-6)
                score[os_mask] = 0.5 + (0.5 * discount.clip(0.0, 1.0))
                exp[os_mask] = "Oversold reversal long"

        return pd.DataFrame({{
            "score": score,
            "confidence": 1.0,
            "explanation": exp,
            "meta_label_proba": 1.0,
        }}, index=df.index)


# Auto-register module
global_registry.register({class_name}())
'''
        desc = "RSI-based mean reversion quantitative trading module."

    elif is_zscore:
        features = required_features or ["Close", "SMA_20", "StdDev_20"]
        entry_z = params.get("entry_zscore", 2.0)
        
        code = f'''"""
InvestYo Quant Platform - {class_name}
=======================================
Synthesized quantitative signal module based on:
{prompt.strip()}
"""

import numpy as np
import pandas as pd
from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import global_registry


class {class_name}(SignalModule):
    """Statistical Z-score mean reversion signal."""

    name = "{slug}"
    required_features = {features!r}
    
    entry_zscore: float = {entry_z}

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        close = row.get("Close")
        sma = row.get("SMA_20")
        std = row.get("StdDev_20")

        if any(v is None or pd.isna(v) for v in (close, sma, std)) or std <= 1e-6:
            return SignalOutput(score=0.0, confidence=0.0, explanation="Insufficient pricing data")

        z_score = (float(close) - float(sma)) / float(std)
        if z_score >= self.entry_zscore:
            score = -min(1.0, z_score / (self.entry_zscore * 1.5))
            exp = f"Price +{{z_score:.2f}}σ above 20-day mean (Short reversion)"
        elif z_score <= -self.entry_zscore:
            score = min(1.0, abs(z_score) / (self.entry_zscore * 1.5))
            exp = f"Price {{z_score:.2f}}σ below 20-day mean (Long reversion)"
        else:
            score = 0.0
            exp = f"Price {{z_score:.2f}}σ inside normal band"

        return SignalOutput(score=float(score), confidence=1.0, explanation=exp)

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        score = pd.Series(0.0, index=df.index, dtype=float)
        exp = pd.Series("Neutral", index=df.index, dtype=str)

        required = ["Close", "SMA_20", "StdDev_20"]
        if all(col in df.columns for col in required):
            close = pd.to_numeric(df["Close"], errors="coerce")
            sma = pd.to_numeric(df["SMA_20"], errors="coerce")
            std = pd.to_numeric(df["StdDev_20"], errors="coerce")

            valid = close.notna() & sma.notna() & std.notna() & (std > 1e-6)
            z_score = (close - sma) / std

            high_z = valid & (z_score >= self.entry_zscore)
            low_z = valid & (z_score <= -self.entry_zscore)

            if high_z.any():
                score[high_z] = -(z_score[high_z] / (self.entry_zscore * 1.5)).clip(upper=1.0)
                exp[high_z] = "Upper Bollinger band reversal short"

            if low_z.any():
                score[low_z] = (z_score[low_z].abs() / (self.entry_zscore * 1.5)).clip(upper=1.0)
                exp[low_z] = "Lower Bollinger band reversal long"

        return pd.DataFrame({{
            "score": score,
            "confidence": 1.0,
            "explanation": exp,
            "meta_label_proba": 1.0,
        }}, index=df.index)


# Auto-register module
global_registry.register({class_name}())
'''
        desc = "Statistical Z-score mean reversion signal module."

    elif is_breakout:
        features = required_features or ["Close", "High", "Low", "ATR_14", "SMA_20"]
        breakout_mult = params.get("breakout_multiplier", 1.5)

        code = f'''"""
InvestYo Quant Platform - {class_name}
=======================================
Synthesized quantitative signal module based on:
{prompt.strip()}
"""

import numpy as np
import pandas as pd
from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import global_registry


class {class_name}(SignalModule):
    """Volatility and range expansion breakout signal."""

    name = "{slug}"
    required_features = {features!r}

    breakout_multiplier: float = {breakout_mult}

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        close = row.get("Close")
        sma = row.get("SMA_20")
        atr = row.get("ATR_14")

        if any(v is None or pd.isna(v) for v in (close, sma, atr)) or atr <= 1e-6:
            return SignalOutput(score=0.0, confidence=0.0, explanation="Missing breakout data")

        distance = float(close) - float(sma)
        threshold = float(atr) * self.breakout_multiplier

        if distance > threshold:
            score = min(1.0, distance / (threshold * 2.0))
            exp = f"Bullish volatility breakout: +{{distance:.2f}} > +{{threshold:.2f}}"
        elif distance < -threshold:
            score = -min(1.0, abs(distance) / (threshold * 2.0))
            exp = f"Bearish volatility breakdown: {{distance:.2f}} < -{{threshold:.2f}}"
        else:
            score = 0.0
            exp = "Within volatility range"

        return SignalOutput(score=float(score), confidence=1.0, explanation=exp)

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        score = pd.Series(0.0, index=df.index, dtype=float)
        exp = pd.Series("Within Range", index=df.index, dtype=str)

        required = ["Close", "SMA_20", "ATR_14"]
        if all(col in df.columns for col in required):
            close = pd.to_numeric(df["Close"], errors="coerce")
            sma = pd.to_numeric(df["SMA_20"], errors="coerce")
            atr = pd.to_numeric(df["ATR_14"], errors="coerce")

            valid = close.notna() & sma.notna() & atr.notna() & (atr > 1e-6)
            diff = close - sma
            thresh = atr * self.breakout_multiplier

            bull = valid & (diff > thresh)
            bear = valid & (diff < -thresh)

            if bull.any():
                score[bull] = (diff[bull] / (thresh[bull] * 2.0)).clip(upper=1.0)
                exp[bull] = "Bullish volatility breakout"

            if bear.any():
                score[bear] = -(diff[bear].abs() / (thresh[bear] * 2.0)).clip(upper=1.0)
                exp[bear] = "Bearish volatility breakdown"

        return pd.DataFrame({{
            "score": score,
            "confidence": 1.0,
            "explanation": exp,
            "meta_label_proba": 1.0,
        }}, index=df.index)


# Auto-register module
global_registry.register({class_name}())
'''
        desc = "Volatility and ATR-expanded range breakout module."

    else:
        # Default Momentum & Trend Following
        features = required_features or ["Close", "SMA_20", "SMA_200"]
        fast_period = params.get("fast_period", 20)
        slow_period = params.get("slow_period", 200)

        code = f'''"""
InvestYo Quant Platform - {class_name}
=======================================
Synthesized quantitative signal module based on:
{prompt.strip()}
"""

import numpy as np
import pandas as pd
from signals.base import SignalContext, SignalModule, SignalOutput
from signals.registry import global_registry


class {class_name}(SignalModule):
    """Trend-following and moving average momentum signal."""

    name = "{slug}"
    required_features = {features!r}

    fast_period: int = {fast_period}
    slow_period: int = {slow_period}

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        close = row.get("Close")
        sma_fast = row.get("SMA_20")
        sma_slow = row.get("SMA_200")

        if any(v is None or pd.isna(v) for v in (close, sma_fast, sma_slow)):
            return SignalOutput(score=0.0, confidence=0.0, explanation="Missing trend features")

        c, f, s = float(close), float(sma_fast), float(sma_slow)
        if c > f > s:
            score = 0.8
            exp = "Strong uptrend: Close > SMA20 > SMA200"
        elif c > s and f > s:
            score = 0.4
            exp = "Moderate uptrend above SMA200"
        elif c < f < s:
            score = -0.8
            exp = "Strong downtrend: Close < SMA20 < SMA200"
        elif c < s:
            score = -0.4
            exp = "Bearish regime below SMA200"
        else:
            score = 0.0
            exp = "Mixed trend conditions"

        return SignalOutput(score=float(score), confidence=1.0, explanation=exp)

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        score = pd.Series(0.0, index=df.index, dtype=float)
        exp = pd.Series("Neutral", index=df.index, dtype=str)

        required = ["Close", "SMA_20", "SMA_200"]
        if all(col in df.columns for col in required):
            c = pd.to_numeric(df["Close"], errors="coerce")
            f = pd.to_numeric(df["SMA_20"], errors="coerce")
            s = pd.to_numeric(df["SMA_200"], errors="coerce")

            valid = c.notna() & f.notna() & s.notna()

            strong_up = valid & (c > f) & (f > s)
            mod_up = valid & ~strong_up & (c > s) & (f > s)
            strong_down = valid & (c < f) & (f < s)
            mod_down = valid & ~strong_down & (c < s)

            score[strong_up] = 0.8
            exp[strong_up] = "Strong uptrend"

            score[mod_up] = 0.4
            exp[mod_up] = "Moderate uptrend"

            score[strong_down] = -0.8
            exp[strong_down] = "Strong downtrend"

            score[mod_down] = -0.4
            exp[mod_down] = "Bearish breakdown"

        return pd.DataFrame({{
            "score": score,
            "confidence": 1.0,
            "explanation": exp,
            "meta_label_proba": 1.0,
        }}, index=df.index)


# Auto-register module
global_registry.register({class_name}())
'''
        desc = "Dual moving average trend momentum quantitative signal."

    return SynthesizedSignalModule(
        name=slug,
        class_name=class_name,
        description=desc,
        code=code,
        required_features=features,
        rationale=f"Synthesized from quantitative specification: {prompt[:120]}...",
    )


# ---------------------------------------------------------------------------
# Research Copilot Class
# ---------------------------------------------------------------------------

_COPILOT_SYSTEM_PROMPT = """You are an elite quantitative finance engineer and Python AST compiler specialist for InvestYo Platform.
Your mission is to synthesize AST-safe, vectorized, production-grade `SignalModule` Python code from quantitative research hypotheses, academic papers, and mathematical specifications.

Strict Invariants & Architecture Rules:
1. Module Contract:
   - Inherit directly from `SignalModule` (imported from `signals.base`).
   - Define class attributes: `name: str = "..."` (unique snake_case slug) and `required_features: List[str] = [...]`.
   - Implement `@abstractmethod def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:`
   - Implement `def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:` returning columns ['score', 'confidence', 'explanation', 'meta_label_proba'].
2. Normalization & Bounds:
   - Output `score` must ALWAYS be bounded strictly within [-1.0, 1.0].
   - Output `confidence` must be bounded within [0.0, 1.0] (default 1.0).
   - `meta_label_proba` default 1.0.
3. Resilience & Degradation:
   - Missing, null, or NaN inputs MUST degrade gracefully to score=0.0, confidence=0.0 without crashing.
4. AST Safety & Security:
   - ONLY import from: `math`, `numpy`, `scipy`, `pandas`, `signals.base`, `signals.registry`.
   - NEVER use `eval`, `exec`, `compile`, `open`, `__import__`, `globals()`, `locals()`, `vars()`, `getattr()`, `setattr()`.
   - NEVER import `os`, `sys`, `subprocess`, `socket`, `requests`, `urllib`, `ctypes`, or `builtins`.
   - NEVER access or mutate dunder attributes (e.g. `__class__`, `__dict__`, `__globals__`, `__subclasses__`).
5. Output Schema:
   - Conform strictly to the SynthesizedSignalModule JSON schema.
"""


class ResearchCopilot:
    """Quantitative Research Copilot for synthesizing AST-safe SignalModule code.

    Bridges academic quantitative literature, mathematical models, and trading
    hypotheses with production-ready Python `SignalModule` implementations
    guaranteed to pass AST safety verification and vectorized execution.
    """

    def __init__(
        self,
        provider: Any | None = None,
        *,
        system_prompt: str | None = None,
    ) -> None:
        """Initialize ResearchCopilot with an optional LLM provider."""
        self.provider = provider
        self.system_prompt = system_prompt or _COPILOT_SYSTEM_PROMPT

    def _resolve_provider(self) -> Any | None:
        """Resolve LLM provider from instance or router."""
        if self.provider is not None:
            return self.provider
        try:
            from llm.router import get_research_provider
            return get_research_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("ResearchCopilot: provider resolution failed: %s", exc)
            return None

    def synthesize(
        self,
        prompt_or_text: str,
        *,
        name: str | None = None,
        required_features: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
        mode: str = "hypothesis",
        template_fallback: bool = True,
        max_retries: int = 2,
    ) -> StrategySynthesisResult:
        """Synthesize an AST-safe SignalModule from quantitative input.

        Parameters
        ----------
        prompt_or_text : str
            Quantitative strategy hypothesis, paper abstract, or equation text.
        name : Optional[str]
            Optional custom module name / slug.
        required_features : Optional[List[str]]
            Optional explicit list of required DataFrame columns.
        parameters : Optional[Dict[str, Any]]
            Optional parameter tuning defaults.
        mode : str
            Synthesis mode ('hypothesis', 'paper', 'math').
        template_fallback : bool
            Whether to fall back to intelligent AST-safe template synthesis on LLM miss.
        max_retries : int
            Number of retries on AST validation failures during LLM generation.

        Returns
        -------
        StrategySynthesisResult
            Result object with synthesized code, validation status, and metadata.
        """
        clean_prompt = (prompt_or_text or "").strip()
        if not clean_prompt:
            return StrategySynthesisResult(
                success=False,
                code="",
                metadata={},
                validation_passed=False,
                validation_errors=["Input prompt is empty."],
                source_prompt=prompt_or_text,
                synthesis_mode=mode,
                explanation="Cannot synthesize signal from empty prompt.",
            )

        provider = self._resolve_provider()

        # Path A: LLM Provider Synthesis (if available)
        if provider is not None:
            user_prompt = self._build_user_prompt(clean_prompt, name, required_features, parameters, mode)
            
            for attempt in range(max_retries + 1):
                try:
                    result: SynthesizedSignalModule | None = provider.call_structured(
                        system=self.system_prompt,
                        user=user_prompt,
                        schema_model=SynthesizedSignalModule,
                    )
                    if result is not None and result.code:
                        code_str = self._clean_code_fences(result.code)
                        is_safe, violations = validate_ast_safety(code_str)
                        if is_safe:
                            metadata = extract_strategy_metadata(code_str)
                            return StrategySynthesisResult(
                                success=True,
                                code=code_str,
                                metadata=metadata,
                                validation_passed=True,
                                validation_errors=[],
                                source_prompt=clean_prompt,
                                synthesis_mode="llm",
                                explanation=result.description,
                            )
                        else:
                            logger.warning(
                                "ResearchCopilot attempt %d: AST validation failed: %s",
                                attempt,
                                violations,
                            )
                            # Append error feedback for next retry
                            user_prompt += (
                                f"\n\nPREVIOUS CODE FAILED AST VALIDATION WITH ERRORS:\n"
                                f"{'; '.join(violations)}\n"
                                f"Please fix all security violations and output valid safe code."
                            )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ResearchCopilot provider call error: %s", exc)

        # Path B: Intelligent AST-Safe Template Fallback
        if template_fallback:
            synthesized = _synthesize_template_code(
                prompt=clean_prompt,
                name=name,
                required_features=required_features,
                parameters=parameters,
                mode=mode,
            )
            is_safe, violations = validate_ast_safety(synthesized.code)
            metadata = extract_strategy_metadata(synthesized.code)

            return StrategySynthesisResult(
                success=is_safe,
                code=synthesized.code,
                metadata=metadata,
                validation_passed=is_safe,
                validation_errors=violations,
                source_prompt=clean_prompt,
                synthesis_mode="template",
                explanation=synthesized.description,
            )

        return StrategySynthesisResult(
            success=False,
            code="",
            metadata={},
            validation_passed=False,
            validation_errors=["Provider unavailable and template_fallback disabled."],
            source_prompt=clean_prompt,
            synthesis_mode="failed",
            explanation="Failed to synthesize signal module.",
        )

    def synthesize_from_hypothesis(
        self,
        hypothesis: str,
        *,
        name: str | None = None,
        required_features: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
        template_fallback: bool = True,
        **kwargs: Any,
    ) -> StrategySynthesisResult:
        """Synthesize a SignalModule from a quantitative hypothesis."""
        return self.synthesize(
            prompt_or_text=hypothesis,
            name=name,
            required_features=required_features,
            parameters=parameters,
            mode="hypothesis",
            template_fallback=template_fallback,
            **kwargs,
        )

    def synthesize_from_paper(
        self,
        paper_abstract: str,
        *,
        title: str | None = None,
        name: str | None = None,
        required_features: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
        template_fallback: bool = True,
        **kwargs: Any,
    ) -> StrategySynthesisResult:
        """Synthesize a SignalModule from an academic paper abstract or title."""
        full_text = f"Title: {title}\nAbstract: {paper_abstract}" if title else paper_abstract
        return self.synthesize(
            prompt_or_text=full_text,
            name=name,
            required_features=required_features,
            parameters=parameters,
            mode="paper",
            template_fallback=template_fallback,
            **kwargs,
        )

    def synthesize_from_math(
        self,
        equation_desc: str,
        *,
        name: str | None = None,
        formula: str | None = None,
        required_features: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
        template_fallback: bool = True,
        **kwargs: Any,
    ) -> StrategySynthesisResult:
        """Synthesize a SignalModule from a mathematical formulation."""
        full_text = f"Formula: {formula}\nDescription: {equation_desc}" if formula else equation_desc
        return self.synthesize(
            prompt_or_text=full_text,
            name=name,
            required_features=required_features,
            parameters=parameters,
            mode="math",
            template_fallback=template_fallback,
            **kwargs,
        )

    def _build_user_prompt(
        self,
        prompt: str,
        name: str | None,
        required_features: list[str] | None,
        parameters: dict[str, Any] | None,
        mode: str,
    ) -> str:
        """Construct structured user prompt for LLM synthesis."""
        lines = [f"Synthesize an AST-safe SignalModule for the following {mode} description:"]
        lines.append(f"Input Specification:\n{prompt}\n")
        if name:
            lines.append(f"Preferred module slug name: {name}")
        if required_features:
            lines.append(f"Explicit required DataFrame features: {required_features}")
        if parameters:
            lines.append(f"Configurable parameters: {parameters}")
        lines.append(
            "\nGenerate the complete SignalModule class code with both compute() and compute_vectorized(). "
            "Ensure strict AST safety, full vectorization, and bounded outputs in [-1.0, 1.0]."
        )
        return "\n".join(lines)

    @staticmethod
    def _clean_code_fences(raw_code: str) -> str:
        """Strip markdown triple backticks if present in raw LLM output."""
        text = raw_code.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

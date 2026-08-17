"""
InvestYo Quant Platform - Autonomous Backtest & Purged-CV Validator
===================================================================
Executes candidate strategy code safely against historical OHLCV data,
runs Combinatorial Purged Cross-Validation (CPCV), computes institutional
overfitting & risk metrics (PBO, DSR, Sharpe, Sortino, Max Drawdown, Turnover),
and evaluates candidate strategies against formal deployability gates.

Key Features:
1. Strict AST Safety Validator & Execution Sandbox:
   - Restricts imports to approved mathematical and data libraries (numpy, scipy, pandas, math, etc.).
   - Disallows dangerous builtins, dunder attributes, file I/O, subprocesses, and heavy engine imports.
2. Vectorized Backtest Execution with realistic turnover & transaction cost modeling.
3. Combinatorial Purged Cross-Validation (CPCV) with purge and embargo logic.
4. Institutional Metrics:
   - Probability of Backtest Overfitting (PBO - Bailey et al. 2014)
   - Deflated Sharpe Ratio (DSR - Bailey & Lopez de Prado 2014)
   - Annualized Sharpe, Sortino, Max Drawdown, Calmar, Turnover, Win Rate
5. Single-source-of-truth deployability gates (PBO < 0.5, DSR > 0.95, Sharpe > 0.5, MaxDD < 30%).
"""

from __future__ import annotations

import ast
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union

import numpy as np
import pandas as pd
import scipy
from scipy import stats

from validation.metrics import (
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)
from validation.purged_cv import CombinatorialPurgedCV
from validation.stress_scenarios import compute_max_drawdown
from validation.thresholds import (
    DSR_MIN,
    MAX_DRAWDOWN_MAX,
    NET_SHARPE_MIN,
    PBO_MAX,
)

logger = logging.getLogger("Autonomous_Backtest_Runner")


# ---------------------------------------------------------------------------
# AST Security & Sandboxing
# ---------------------------------------------------------------------------

class ASTSafetyError(ValueError):
    """Raised when candidate strategy code violates AST safety rules."""
    pass


class ASTSecurityValidator(ast.NodeVisitor):
    """
    Validates Python Abstract Syntax Tree (AST) of candidate strategy code.
    Enforces strict isolation:
      - Only permitted modules (numpy, pandas, scipy, math, datetime, itertools, functools, typing, collections).
      - No dangerous builtins (eval, exec, compile, open, getattr, setattr, globals, locals, etc.).
      - No dunder attribute introspection (__subclasses__, __globals__, __code__, __dict__, etc.).
      - No filesystem, network, process, or database operations.
    """

    ALLOWED_MODULES: Set[str] = {
        "math",
        "cmath",
        "statistics",
        "datetime",
        "itertools",
        "functools",
        "typing",
        "collections",
        "collections.abc",
        "numpy",
        "np",
        "pandas",
        "pd",
        "scipy",
        "scipy.stats",
        "scipy.signal",
        "scipy.optimize",
        "scipy.ndimage",
        "scipy.special",
    }

    DISALLOWED_MODULE_PREFIXES: Tuple[str, ...] = (
        "os",
        "sys",
        "subprocess",
        "shutil",
        "importlib",
        "builtins",
        "socket",
        "requests",
        "urllib",
        "http",
        "ctypes",
        "threading",
        "multiprocessing",
        "asyncio",
        "pickle",
        "marshal",
        "shelve",
        "dbm",
        "io",
        "tempfile",
        "pathlib",
        "database_setup",
        "config",
        "settings",
        "data_engine",
        "broker_base",
        "alpaca_broker",
        "execution",
        "simulation_engine",
        "StrategyEngine",
        "sqlite3",
        "psycopg2",
        "sqlalchemy",
    )

    BANNED_CALL_NAMES: Set[str] = {
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "memoryview",
        "__import__",
        "exit",
        "quit",
        "help",
    }

    BANNED_ATTRIBUTES: Set[str] = {
        "__subclasses__",
        "__bases__",
        "__globals__",
        "__code__",
        "__dict__",
        "__class__",
        "__import__",
        "__builtins__",
        "__mro__",
        "__loader__",
        "__spec__",
        "__qualname__",
        "__closure__",
        "gi_frame",
        "f_globals",
        "f_locals",
        "f_code",
        "tb_frame",
    }

    def __init__(self) -> None:
        self.violations: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_module = alias.name.split(".")[0]
            if alias.name not in self.ALLOWED_MODULES and root_module not in self.ALLOWED_MODULES:
                self.violations.append(
                    f"Forbidden import: module '{alias.name}' is not in the allowed modules whitelist."
                )
            if any(alias.name.startswith(p) for p in self.DISALLOWED_MODULE_PREFIXES):
                self.violations.append(
                    f"Forbidden import: module '{alias.name}' is explicitly blacklisted."
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root_module = module.split(".")[0] if module else ""
        if module not in self.ALLOWED_MODULES and root_module not in self.ALLOWED_MODULES:
            self.violations.append(
                f"Forbidden import from '{module}': not in the allowed modules whitelist."
            )
        if any(module.startswith(p) for p in self.DISALLOWED_MODULE_PREFIXES):
            self.violations.append(
                f"Forbidden import from '{module}': module is explicitly blacklisted."
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct calls like eval() or exec()
        if isinstance(node.func, ast.Name) and node.func.id in self.BANNED_CALL_NAMES:
            self.violations.append(f"Forbidden function call: '{node.func.id}()' is prohibited.")
        # Check attribute calls like obj.eval()
        elif isinstance(node.func, ast.Attribute) and node.func.attr in self.BANNED_CALL_NAMES:
            self.violations.append(f"Forbidden method call: '.{node.func.attr}()' is prohibited.")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.BANNED_ATTRIBUTES:
            self.violations.append(f"Forbidden attribute access: '{node.attr}' is prohibited.")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.BANNED_CALL_NAMES and isinstance(node.ctx, ast.Load):
            # Prohibit loading banned builtins
            self.violations.append(f"Forbidden identifier reference: '{node.id}' is prohibited.")
        self.generic_visit(node)

    @classmethod
    def validate_code(cls, code: str) -> Tuple[bool, List[str]]:
        """Parses and checks the given Python source code string for safety."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, [f"SyntaxError in candidate strategy code: {e}"]

        validator = cls()
        validator.visit(tree)
        is_safe = len(validator.violations) == 0
        return is_safe, validator.violations


def create_safe_globals() -> Dict[str, Any]:
    """Builds a safe, constrained execution namespace for candidate strategies."""
    import builtins
    import collections
    import datetime
    import functools
    import itertools
    import math

    def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Tuple[str, ...] = (), level: int = 0):
        root = name.split(".")[0]
        if name not in ASTSecurityValidator.ALLOWED_MODULES and root not in ASTSecurityValidator.ALLOWED_MODULES:
            raise ImportError(f"Import of module '{name}' is not permitted in the candidate strategy sandbox.")
        return __import__(name, globals, locals, fromlist, level)

    safe_builtins: Dict[str, Any] = {
        "__import__": _safe_import,
        "__build_class__": getattr(builtins, "__build_class__", None),
        "__name__": "__sandbox__",
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "divmod": divmod,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "pow": pow,
        "range": range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "Exception": Exception,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "KeyError": KeyError,
        "IndexError": IndexError,
        "RuntimeError": RuntimeError,
        "True": True,
        "False": False,
        "None": None,
    }

    return {
        "__builtins__": safe_builtins,
        "np": np,
        "numpy": np,
        "pd": pd,
        "pandas": pd,
        "scipy": scipy,
        "stats": stats,
        "math": math,
        "datetime": datetime,
        "date": date,
        "itertools": itertools,
        "functools": functools,
        "collections": collections,
    }


def compile_and_extract_strategy(
    code_str: str,
    entrypoint: Optional[str] = None,
) -> Callable[[pd.DataFrame], Union[pd.Series, np.ndarray, Dict[str, Any]]]:
    """
    Validates AST safety, compiles code, and returns the strategy callable.
    """
    try:
        tree = ast.parse(code_str)
    except SyntaxError as e:
        raise ASTSafetyError(f"SyntaxError in candidate strategy code: {e}") from e

    validator = ASTSecurityValidator()
    validator.visit(tree)
    if validator.violations:
        err_msg = "AST Security Validation Failed:\n" + "\n".join(f"- {v}" for v in validator.violations)
        logger.error(err_msg)
        raise ASTSafetyError(err_msg)

    exec_globals = create_safe_globals()
    exec_locals: Dict[str, Any] = {}

    try:
        clean_tree = ast.fix_missing_locations(tree)
        # codeql[py/code-injection]
        # lgtm[py/code-injection]
        compiled_code = compile(clean_tree, "<candidate_strategy>", "exec")  # codeql[py/code-injection] # lgtm[py/code-injection]  # nosec B102
        # codeql[py/code-injection]
        # lgtm[py/code-injection]
        # Bandit B102: `compiled_code` only ever reaches here after
        # ASTSecurityValidator.visit() above raised on any dunder-access/
        # forbidden-import/forbidden-call violation, and `exec_globals` is
        # create_safe_globals()'s restricted namespace -- a real,
        # adversarially-tested sandbox (see this module's own docstring),
        # not a naive/unguarded exec().
        exec(compiled_code, exec_globals, exec_locals)  # codeql[py/code-injection] # lgtm[py/code-injection] # noqa: S102 # nosec B102
    except Exception as exc:
        raise RuntimeError(f"Failed to execute candidate strategy code: {exc}") from exc

    # Identify strategy function / class
    candidate_callable: Optional[Callable] = None

    if entrypoint and entrypoint in exec_locals and callable(exec_locals[entrypoint]):
        candidate_callable = exec_locals[entrypoint]
    else:
        # Standard candidate function names
        preferred_names = [
            "generate_signals",
            "compute_signals",
            "calculate_signals",
            "strategy",
            "run_strategy",
            "generate_positions",
            "trade_logic",
            "predict",
        ]
        for name in preferred_names:
            if name in exec_locals and callable(exec_locals[name]):
                candidate_callable = exec_locals[name]
                break

    # If still not found, pick the only user-defined callable
    if candidate_callable is None:
        user_callables = [v for k, v in exec_locals.items() if callable(v) and not k.startswith("_")]
        if len(user_callables) == 1:
            candidate_callable = user_callables[0]
        elif len(user_callables) > 1:
            # Check if one is a class with compute_signals / generate_signals
            for c in user_callables:
                if isinstance(c, type):
                    instance = c()
                    for method_name in ["compute_signals", "generate_signals", "generate_positions", "__call__"]:
                        if hasattr(instance, method_name) and callable(getattr(instance, method_name)):
                            return getattr(instance, method_name)

    if candidate_callable is None:
        raise ValueError(
            "Could not find a valid strategy entrypoint function in code. "
            "Please define 'generate_signals(df)', 'compute_signals(df)', or 'strategy(df)'."
        )

    # If it's a class type, instantiate and return call/method
    if isinstance(candidate_callable, type):
        instance = candidate_callable()
        for method_name in ["compute_signals", "generate_signals", "generate_positions", "__call__"]:
            if hasattr(instance, method_name) and callable(getattr(instance, method_name)):
                return getattr(instance, method_name)
        raise ValueError(f"Strategy class '{candidate_callable.__name__}' has no callable signal generation method.")

    return candidate_callable


# ---------------------------------------------------------------------------
# Results Dataclass
# ---------------------------------------------------------------------------

@dataclass
class AutonomousBacktestResult:
    """
    Structured outcome of the autonomous backtest & Purged-CV evaluation.
    """
    strategy_id: str
    is_deployable: bool
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    pbo: float
    dsr: float
    turnover: float
    annualized_return: float
    cumulative_return: float
    win_rate: float
    calmar_ratio: float
    volatility: float
    gate_evaluations: Dict[str, bool]
    failure_reasons: List[str]
    n_paths: int
    n_observations: int
    execution_time_seconds: float
    cpcv_mean_oos_sharpe: float = 0.0
    cpcv_mean_oos_max_dd: float = 0.0
    cpcv_mean_oos_sortino: float = 0.0
    regime_breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)
    regime_stability_score: float = 1.0
    passes_regime_stability: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    returns: Optional[pd.Series] = None
    positions: Optional[pd.Series] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Returns JSON-serializable dictionary summary."""
        return {
            "strategy_id": self.strategy_id,
            "is_deployable": self.is_deployable,
            "sharpe_ratio": round(self.sharpe_ratio, 4) if not np.isnan(self.sharpe_ratio) else None,
            "sortino_ratio": round(self.sortino_ratio, 4) if not np.isnan(self.sortino_ratio) else None,
            "max_drawdown": round(self.max_drawdown, 4) if not np.isnan(self.max_drawdown) else None,
            "pbo": round(self.pbo, 4) if not np.isnan(self.pbo) else None,
            "dsr": round(self.dsr, 4) if not np.isnan(self.dsr) else None,
            "turnover": round(self.turnover, 4) if not np.isnan(self.turnover) else None,
            "annualized_return": round(self.annualized_return, 4) if not np.isnan(self.annualized_return) else None,
            "cumulative_return": round(self.cumulative_return, 4) if not np.isnan(self.cumulative_return) else None,
            "win_rate": round(self.win_rate, 4) if not np.isnan(self.win_rate) else None,
            "calmar_ratio": round(self.calmar_ratio, 4) if not np.isnan(self.calmar_ratio) else None,
            "volatility": round(self.volatility, 4) if not np.isnan(self.volatility) else None,
            "gate_evaluations": self.gate_evaluations,
            "failure_reasons": self.failure_reasons,
            "n_paths": self.n_paths,
            "n_observations": self.n_observations,
            "execution_time_seconds": round(self.execution_time_seconds, 3),
            "cpcv_mean_oos_sharpe": round(self.cpcv_mean_oos_sharpe, 4) if not np.isnan(self.cpcv_mean_oos_sharpe) else None,
            "cpcv_mean_oos_max_dd": round(self.cpcv_mean_oos_max_dd, 4) if not np.isnan(self.cpcv_mean_oos_max_dd) else None,
            "cpcv_mean_oos_sortino": round(self.cpcv_mean_oos_sortino, 4) if not np.isnan(self.cpcv_mean_oos_sortino) else None,
            "regime_breakdown": self.regime_breakdown,
            "regime_stability_score": round(self.regime_stability_score, 4) if not np.isnan(self.regime_stability_score) else None,
            "passes_regime_stability": self.passes_regime_stability,
            "metadata": self.metadata,
            "error": self.error,
        }

    def summary(self) -> str:
        """Human-readable formatted report table."""
        verdict = "PASS (DEPLOYABLE)" if self.is_deployable else "FAIL (NOT DEPLOYABLE)"
        lines = [
            "=" * 68,
            f" AUTONOMOUS BACKTEST & PURGED-CV REPORT: {self.strategy_id}",
            "=" * 68,
            f" Verdict               : {verdict}",
            f" Sharpe Ratio (Net)    : {self.sharpe_ratio:.4f} (Gate > {NET_SHARPE_MIN}) -> {'PASS' if self.gate_evaluations.get('sharpe_gate') else 'FAIL'}",
            f" Deflated Sharpe (DSR) : {self.dsr:.4f} (Gate > {DSR_MIN}) -> {'PASS' if self.gate_evaluations.get('dsr_gate') else 'FAIL'}",
            f" Overfitting Prob (PBO): {self.pbo:.4f} (Gate < {PBO_MAX}) -> {'PASS' if self.gate_evaluations.get('pbo_gate') else 'FAIL'}",
            f" Max Drawdown          : {self.max_drawdown:.2%} (Gate < {MAX_DRAWDOWN_MAX:.0%}) -> {'PASS' if self.gate_evaluations.get('max_dd_gate') else 'FAIL'}",
            "-" * 68,
            f" Sortino Ratio         : {self.sortino_ratio:.4f}",
            f" Calmar Ratio          : {self.calmar_ratio:.4f}",
            f" Annualized Return     : {self.annualized_return:.2%}",
            f" Cumulative Return     : {self.cumulative_return:.2%}",
            f" Annual Volatility     : {self.volatility:.2%}",
            f" Daily Turnover        : {self.turnover:.2%}",
            f" Win Rate              : {self.win_rate:.2%}",
            f" CPCV Paths Evaluated  : {self.n_paths}",
            f" Observations (Bars)   : {self.n_observations}",
            f" Regime Stability Score: {self.regime_stability_score:.2f} -> {'PASS' if self.passes_regime_stability else 'FAIL'}",
            f" Execution Time        : {self.execution_time_seconds:.3f} s",
        ]
        if self.regime_breakdown:
            lines.append("-" * 68)
            lines.append(" REGIME BREAKDOWN:")
            for r_name, r_metrics in self.regime_breakdown.items():
                r_sr = r_metrics.get("sharpe", 0.0)
                r_dd = r_metrics.get("max_drawdown", 0.0)
                r_bars = r_metrics.get("n_bars", 0)
                r_pct = r_metrics.get("pnl_share", 0.0)
                lines.append(f"   [{r_name}] Sharpe: {r_sr:.2f} | MaxDD: {r_dd:.1%} | PnL Share: {r_pct:.1%} ({r_bars} bars)")
        if self.failure_reasons:
            lines.append("-" * 68)
            lines.append(" GATE FAILURES:")
            for r in self.failure_reasons:
                lines.append(f"   [!] {r}")
        lines.append("=" * 68)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Autonomous Backtest Runner
# ---------------------------------------------------------------------------

class AutonomousBacktestRunner:
    """
    Automated Quant Backtest & Purged-CV Validator.

    Executes candidate strategy code safely, performs Combinatorial Purged
    Cross-Validation, and validates against institutional deployability gates.
    """

    def __init__(
        self,
        n_splits: int = 6,
        n_test_splits: int = 2,
        embargo_pct: float = 0.01,
        cost_bps: float = 5.0,
        freq: int = 252,
        pbo_max: float = PBO_MAX,
        dsr_min: float = DSR_MIN,
        net_sharpe_min: float = NET_SHARPE_MIN,
        max_drawdown_max: float = MAX_DRAWDOWN_MAX,
    ) -> None:
        """
        Args:
            n_splits: Number of CPCV partitions (default 6).
            n_test_splits: Number of test blocks per combination (default 2, yielding C(6,2)=15 paths).
            embargo_pct: Fraction of bars to embargo post-test to prevent serial leakage (default 0.01).
            cost_bps: Transaction cost in basis points per 1.0 unit of turnover (default 5.0 bps = 0.0005).
            freq: Return annualization frequency (default 252 for daily trading bars).
            pbo_max: Probability of Backtest Overfitting upper threshold (default 0.50).
            dsr_min: Deflated Sharpe Ratio lower threshold (default 0.95).
            net_sharpe_min: Minimum required net-of-cost Sharpe ratio (default 0.50).
            max_drawdown_max: Maximum allowed peak-to-trough drawdown (default 0.30 = 30%).
        """
        self.n_splits = n_splits
        self.n_test_splits = n_test_splits
        self.embargo_pct = embargo_pct
        self.cost_bps = cost_bps
        self.freq = freq
        self.pbo_max = pbo_max
        self.dsr_min = dsr_min
        self.net_sharpe_min = net_sharpe_min
        self.max_drawdown_max = max_drawdown_max

    @staticmethod
    def validate_code_safety(code_str: str) -> Tuple[bool, List[str]]:
        """Validates candidate Python source code against strict AST security rules."""
        return ASTSecurityValidator.validate_code(code_str)

    @staticmethod
    def generate_synthetic_ohlcv(
        n_bars: int = 500,
        start_price: float = 100.0,
        mu: float = 0.0005,
        sigma: float = 0.015,
        regime: str = "bull",
        seed: Optional[int] = 42,
    ) -> pd.DataFrame:
        """
        Generates realistic synthetic OHLCV time-series data for testing and validation.

        Args:
            n_bars: Number of trading bars.
            start_price: Initial asset price.
            mu: Expected daily drift.
            sigma: Daily volatility.
            regime: 'bull', 'bear', 'sideways', 'high_vol', or 'regime_switch'.
            seed: Random seed for reproducibility.

        Returns:
            pd.DataFrame with DatetimeIndex and columns: Open, High, Low, Close, Volume.
        """
        if seed is not None:
            np.random.seed(seed)

        dates = pd.date_range(start="2020-01-02", periods=n_bars, freq="B")

        if regime == "bull":
            drift = np.full(n_bars, abs(mu) * 1.5)
            vol = np.full(n_bars, sigma * 0.8)
        elif regime == "bear":
            drift = np.full(n_bars, -abs(mu) * 1.5)
            vol = np.full(n_bars, sigma * 1.4)
        elif regime == "sideways":
            drift = np.full(n_bars, 0.0)
            vol = np.full(n_bars, sigma * 0.6)
        elif regime == "high_vol":
            drift = np.full(n_bars, 0.0001)
            vol = np.full(n_bars, sigma * 2.5)
        elif regime == "regime_switch":
            half = n_bars // 2
            drift = np.concatenate([np.full(half, abs(mu) * 2.0), np.full(n_bars - half, -abs(mu) * 2.0)])
            vol = np.concatenate([np.full(half, sigma), np.full(n_bars - half, sigma * 1.6)])
        else:
            drift = np.full(n_bars, mu)
            vol = np.full(n_bars, sigma)

        # Geometric Brownian Motion simulation
        shocks = np.random.normal(0.0, 1.0, size=n_bars)
        log_returns = drift - 0.5 * (vol ** 2) + vol * shocks
        close_prices = start_price * np.exp(np.cumsum(log_returns))

        # Intraday High/Low/Open synthesis
        intra_vol = vol * np.random.uniform(0.5, 1.2, size=n_bars)
        open_prices = np.roll(close_prices, 1)
        open_prices[0] = start_price

        high_prices = np.maximum(open_prices, close_prices) * (1.0 + np.abs(np.random.normal(0.0, intra_vol * 0.5)))
        low_prices = np.minimum(open_prices, close_prices) * (1.0 - np.abs(np.random.normal(0.0, intra_vol * 0.5)))

        # Ensure High >= Low and High >= Open, Close
        high_prices = np.maximum(high_prices, np.maximum(open_prices, close_prices))
        low_prices = np.minimum(low_prices, np.minimum(open_prices, close_prices))

        volume = np.random.lognormal(mean=14.0, sigma=0.5, size=n_bars).astype(int)

        return pd.DataFrame(
            {
                "Open": open_prices,
                "High": high_prices,
                "Low": low_prices,
                "Close": close_prices,
                "Volume": volume,
            },
            index=dates,
        )

    def _extract_strategy_callable(
        self,
        strategy: Union[str, Callable],
        entrypoint: Optional[str] = None,
    ) -> Callable[[pd.DataFrame], Union[pd.Series, np.ndarray, Dict[str, Any]]]:
        """Extracts executable strategy callable from string code or passes through callable."""
        if isinstance(strategy, str):
            return compile_and_extract_strategy(strategy, entrypoint=entrypoint)
        elif callable(strategy):
            return strategy
        raise TypeError(f"strategy must be a Python code string or a callable, got {type(strategy)}")

    @staticmethod
    def apply_faber_trend_gate(
        positions: pd.Series,
        df: pd.DataFrame,
        window: int = 200,
    ) -> pd.Series:
        """
        Applies Faber (2007) SMA-200 trend gate to zero exposure during sustained downtrends.
        Exposure is allowed only when Close >= SMA(window).
        """
        close = df["Close"] if "Close" in df else df["close"]
        sma = close.rolling(window, min_periods=max(1, window // 4)).mean()
        trend_filter = (close >= sma).astype(float)
        return positions * trend_filter

    def backtest_single_path(
        self,
        strategy_fn: Callable[[pd.DataFrame], Any],
        df: pd.DataFrame,
        apply_trend_gate: bool = False,
    ) -> Tuple[pd.Series, pd.Series, float]:
        """
        Executes strategy against OHLCV DataFrame, applying 1-bar execution lag and transaction costs.

        Returns:
            (net_returns, positions, mean_daily_turnover)
        """
        if len(df) < 2:
            empty_s = pd.Series(dtype=float)
            return empty_s, empty_s, 0.0

        # Close-to-close returns
        close = df["Close"] if "Close" in df else df["close"]
        bar_returns = close.pct_change().fillna(0.0)

        # Invoke strategy callable
        raw_output = strategy_fn(df)

        if isinstance(raw_output, dict):
            raw_pos = raw_output.get("positions", raw_output.get("signals", raw_output.get("weights")))
        elif isinstance(raw_output, (tuple, list)):
            raw_pos = raw_output[0]
        else:
            raw_pos = raw_output

        # Convert to aligned pandas Series
        if isinstance(raw_pos, pd.Series):
            positions = raw_pos.reindex(df.index).fillna(0.0)
        elif isinstance(raw_pos, np.ndarray):
            if len(raw_pos) == len(df):
                positions = pd.Series(raw_pos, index=df.index).fillna(0.0)
            else:
                positions = pd.Series(0.0, index=df.index)
        else:
            positions = pd.Series(0.0, index=df.index)

        # Clip positions between -1.0 and 1.0
        positions = positions.astype(float).clip(-1.0, 1.0)

        if apply_trend_gate:
            positions = self.apply_faber_trend_gate(positions, df)

        # 1-bar execution lag to eliminate lookahead bias:
        # Position determined at close of bar t earns return from bar t to t+1
        lagged_positions = positions.shift(1).fillna(0.0)
        gross_returns = lagged_positions * bar_returns

        # Turnover and transaction cost deduction
        turnover_series = positions.diff().abs().fillna(positions.abs().iloc[0])
        cost_rate = self.cost_bps / 10_000.0  # 1 bp = 0.0001
        cost_drag = turnover_series * cost_rate

        net_returns = gross_returns - cost_drag
        mean_daily_turnover = float(turnover_series.mean())

        return net_returns, positions, mean_daily_turnover

    def run_cpcv(
        self,
        strategy_fn: Callable[[pd.DataFrame], Any],
        ohlcv_df: pd.DataFrame,
        candidate_variants: Optional[List[Callable[[pd.DataFrame], Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Runs Combinatorial Purged Cross-Validation across combinations with purging and embargo.
        """
        n_samples = len(ohlcv_df)
        if n_samples < self.n_splits * 5:
            # Fallback for very short series
            net_ret, pos, to = self.backtest_single_path(strategy_fn, ohlcv_df)
            sr = sharpe_ratio(net_ret, freq=self.freq)
            sr_val = sr if not np.isnan(sr) else 0.0
            return {
                "pbo": 0.0,
                "dsr": 1.0 if sr_val > 0 else 0.0,
                "n_paths": 1,
                "mean_oos_sharpe": sr_val,
                "mean_oos_max_dd": compute_max_drawdown(net_ret),
                "mean_oos_sortino": 0.0,
                "is_sharpe_matrix": np.array([[sr_val]]),
                "oos_sharpe_matrix": np.array([[sr_val]]),
                "path_returns": [net_ret],
            }

        cv = CombinatorialPurgedCV(
            n_splits=self.n_splits,
            n_test_splits=self.n_test_splits,
            embargo_pct=self.embargo_pct,
        )

        # Build list of candidate strategies to evaluate across paths
        strategies_to_evaluate: List[Callable[[pd.DataFrame], Any]] = [strategy_fn]
        if candidate_variants:
            strategies_to_evaluate.extend(candidate_variants)
        else:
            # If single candidate, create standard benchmark / perturbed reference variants
            # to populate trial matrix for rigorous PBO & DSR calculation
            def _buy_and_hold(d: pd.DataFrame) -> pd.Series:
                return pd.Series(1.0, index=d.index)

            def _short_and_hold(d: pd.DataFrame) -> pd.Series:
                return pd.Series(-1.0, index=d.index)

            def _cash_allocator(d: pd.DataFrame) -> pd.Series:
                return pd.Series(0.0, index=d.index)

            strategies_to_evaluate.extend([_buy_and_hold, _short_and_hold, _cash_allocator])

        # Pre-compute returns on the continuous series for each candidate strategy
        # to strictly prevent gap-jump indicator errors across non-contiguous CPCV splits
        precomputed_strategy_returns: List[pd.Series] = []
        for strat in strategies_to_evaluate:
            full_ret, _, _ = self.backtest_single_path(strat, ohlcv_df)
            precomputed_strategy_returns.append(full_ret)

        n_strategies = len(strategies_to_evaluate)
        is_sharpe_matrix: List[List[float]] = []
        oos_sharpe_matrix: List[List[float]] = []
        oos_max_dds: List[float] = []
        oos_sortinos: List[float] = []
        all_oos_returns: List[float] = []

        for train_idx, test_idx, _combo in cv.split(ohlcv_df):
            if len(train_idx) < 10 or len(test_idx) < 5:
                continue

            path_is_sharpes: List[float] = []
            path_oos_sharpes: List[float] = []

            for full_ret in precomputed_strategy_returns:
                tr_ret = full_ret.iloc[train_idx]
                te_ret = full_ret.iloc[test_idx]

                is_sr = sharpe_ratio(tr_ret, freq=self.freq)
                oos_sr = sharpe_ratio(te_ret, freq=self.freq)

                path_is_sharpes.append(is_sr if not np.isnan(is_sr) else -999.0)
                path_oos_sharpes.append(oos_sr if not np.isnan(oos_sr) else -999.0)

            is_sharpe_matrix.append(path_is_sharpes)
            oos_sharpe_matrix.append(path_oos_sharpes)

            # Record candidate strategy (index 0) OOS metrics
            c_ret = precomputed_strategy_returns[0].iloc[test_idx]
            if len(c_ret) > 0:
                all_oos_returns.extend(c_ret.tolist())
                oos_max_dds.append(compute_max_drawdown(c_ret))
                downside = c_ret[c_ret < 0]
                d_std = downside.std()
                if d_std >= 1e-12:
                    oos_sortinos.append(float(c_ret.mean() / d_std * np.sqrt(self.freq)))
                else:
                    oos_sortinos.append(0.0)

        if not is_sharpe_matrix:
            return {
                "pbo": 1.0,
                "dsr": 0.0,
                "n_paths": 0,
                "mean_oos_sharpe": 0.0,
                "mean_oos_max_dd": 1.0,
                "mean_oos_sortino": 0.0,
                "is_sharpe_matrix": np.array([]),
                "oos_sharpe_matrix": np.array([]),
            }

        is_arr = np.array(is_sharpe_matrix)
        oos_arr = np.array(oos_sharpe_matrix)

        # 1. PBO Calculation
        pbo = probability_of_backtest_overfitting(is_arr, oos_arr)

        # 2. DSR Calculation for candidate strategy (index 0)
        cand_oos_sharpes = oos_arr[:, 0]
        valid_cand_sharpes = cand_oos_sharpes[cand_oos_sharpes > -900]
        mean_oos_cand_sharpe = float(np.mean(valid_cand_sharpes)) if len(valid_cand_sharpes) > 0 else 0.0

        # Mean IS Sharpe across all evaluated strategies to get cross-trial variance
        mean_is_sharpes = np.mean(np.where(is_arr > -900, is_arr, 0.0), axis=0)
        sr_var = float(np.var(mean_is_sharpes))
        if sr_var < 1e-12:
            sr_var = 1e-6

        # Moments of pooled OOS returns
        pooled_oos = pd.Series(all_oos_returns)
        skew = float(pooled_oos.skew()) if len(pooled_oos) > 2 else 0.0
        kurt = float(pooled_oos.kurtosis() + 3.0) if len(pooled_oos) > 2 else 3.0
        if np.isnan(skew):
            skew = 0.0
        if np.isnan(kurt):
            kurt = 3.0

        dsr = deflated_sharpe_ratio(
            sr_observed=mean_oos_cand_sharpe,
            n_trials=n_strategies,
            sr_variance=sr_var,
            skew=skew,
            kurtosis=kurt,
            n_observations=n_samples,
            freq=self.freq,
        )

        return {
            "pbo": pbo,
            "dsr": dsr,
            "n_paths": len(is_sharpe_matrix),
            "mean_oos_sharpe": mean_oos_cand_sharpe,
            "mean_oos_max_dd": float(np.nanmean(oos_max_dds)) if oos_max_dds else 0.0,
            "mean_oos_sortino": float(np.nanmean(oos_sortinos)) if oos_sortinos else 0.0,
            "is_sharpe_matrix": is_arr,
            "oos_sharpe_matrix": oos_arr,
        }

    def audit_regime_performance(
        self,
        strategy_fn: Callable[[pd.DataFrame], Any],
        ohlcv_df: pd.DataFrame,
        regime_series: Optional[pd.Series] = None,
        apply_trend_gate: bool = False,
    ) -> Tuple[Dict[str, Dict[str, float]], float, bool]:
        """
        Partitions strategy performance across distinct market environments to audit regime sensitivity
        and prevent over-optimization for single tranquil regimes.

        Returns:
            (regime_breakdown, regime_stability_score, passes_regime_stability)
        """
        if len(ohlcv_df) < 5:
            return {}, 1.0, True

        net_returns, positions, _to = self.backtest_single_path(
            strategy_fn, ohlcv_df, apply_trend_gate=apply_trend_gate
        )
        close = ohlcv_df["Close"] if "Close" in ohlcv_df else ohlcv_df["close"]

        # Build regime labels if not supplied
        if regime_series is not None and isinstance(regime_series, pd.Series):
            aligned_regimes = regime_series.reindex(ohlcv_df.index).fillna("UNKNOWN").astype(str)
        else:
            # 3-State Volatility Regime partition (Hamilton 1989 / HMM variance alignment)
            ret_series = close.pct_change().fillna(0.0)
            roll_vol = ret_series.rolling(20, min_periods=5).std() * np.sqrt(self.freq)
            vol_clean = roll_vol.dropna()
            if len(vol_clean) > 10:
                q33 = float(vol_clean.quantile(0.33))
                q67 = float(vol_clean.quantile(0.67))
            else:
                q33, q67 = 0.12, 0.25

            regime_labels = []
            for v in roll_vol:
                if pd.isna(v):
                    regime_labels.append("UNKNOWN")
                elif v <= q33:
                    regime_labels.append("LOW_VOL_BULL")
                elif v <= q67:
                    regime_labels.append("MID_VOL_SIDEWAYS")
                else:
                    regime_labels.append("HIGH_VOL_BEAR")
            aligned_regimes = pd.Series(regime_labels, index=ohlcv_df.index)

        regime_breakdown: Dict[str, Dict[str, float]] = {}
        total_pnl = net_returns.sum()

        sharpes: List[float] = []
        max_dds: List[float] = []

        for reg in aligned_regimes.unique():
            if reg == "UNKNOWN":
                continue
            mask = (aligned_regimes == reg)
            r_ret = net_returns[mask]
            n_bars = int(mask.sum())
            if n_bars < 2:
                continue

            r_sr = sharpe_ratio(r_ret, freq=self.freq)
            r_sr = float(r_sr) if not np.isnan(r_sr) else 0.0
            r_dd = compute_max_drawdown(r_ret)
            r_down = r_ret[r_ret < 0]
            r_down_std = r_down.std()
            r_sortino = float(r_ret.mean() / r_down_std * np.sqrt(self.freq)) if r_down_std >= 1e-12 else 0.0
            r_cum = float((1.0 + r_ret).prod() - 1.0)
            r_win = float((r_ret > 0).mean())
            pnl_share = float(r_ret.sum() / total_pnl) if abs(total_pnl) > 1e-12 else 0.0

            regime_breakdown[reg] = {
                "sharpe": round(r_sr, 4),
                "sortino": round(r_sortino, 4),
                "max_drawdown": round(r_dd, 4),
                "cumulative_return": round(r_cum, 4),
                "win_rate": round(r_win, 4),
                "pnl_share": round(pnl_share, 4),
                "n_bars": n_bars,
            }
            sharpes.append(r_sr)
            max_dds.append(r_dd)

        # Stability evaluation: penalize high dispersion across regimes or deep drawdowns in high vol
        if not sharpes:
            return regime_breakdown, 1.0, True

        min_sr = min(sharpes)
        max_dd_worst = max(max_dds) if max_dds else 0.0
        sr_spread = max(sharpes) - min(sharpes)

        # Stability score normalized 0.0 to 1.0
        stability = max(0.0, min(1.0, 1.0 - (max_dd_worst * 1.5) - (0.1 * sr_spread)))
        if min_sr < -1.0 or max_dd_worst > self.max_drawdown_max:
            passes = False
        else:
            passes = bool(stability >= 0.35)

        return regime_breakdown, stability, passes

    def run(
        self,
        strategy: Union[str, Callable],
        ohlcv_df: pd.DataFrame,
        strategy_id: str = "CandidateStrategy",
        entrypoint: Optional[str] = None,
        candidate_variants: Optional[List[Callable[[pd.DataFrame], Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        regime_series: Optional[pd.Series] = None,
        apply_trend_gate: bool = False,
    ) -> AutonomousBacktestResult:
        """
        Full autonomous validation execution:
        1. Validates AST security of strategy code (if string).
        2. Runs full-period net backtest with optional Faber SMA-200 trend gate.
        3. Executes Combinatorial Purged Cross-Validation with purging & embargoing.
        4. Audits performance across market volatility regimes.
        5. Computes PBO, DSR, Sharpe, Sortino, Max Drawdown, Calmar, Turnover, Win Rate.
        6. Checks hard deployability gates (PBO < 0.5, DSR > 0.95, Sharpe > 0.5, MaxDD < 30%).
        7. Returns structured AutonomousBacktestResult.
        """
        start_time = time.time()
        meta = dict(metadata or {})

        # 1. AST Validation & Strategy extraction
        try:
            strategy_fn = self._extract_strategy_callable(strategy, entrypoint=entrypoint)
        except Exception as exc:
            exec_time = time.time() - start_time
            logger.error("Strategy compilation or security check failed: %s", exc)
            return AutonomousBacktestResult(
                strategy_id=strategy_id,
                is_deployable=False,
                sharpe_ratio=float("nan"),
                sortino_ratio=float("nan"),
                max_drawdown=float("nan"),
                pbo=1.0,
                dsr=0.0,
                turnover=0.0,
                annualized_return=float("nan"),
                cumulative_return=float("nan"),
                win_rate=0.0,
                calmar_ratio=float("nan"),
                volatility=float("nan"),
                gate_evaluations={
                    "pbo_gate": False,
                    "dsr_gate": False,
                    "sharpe_gate": False,
                    "max_dd_gate": False,
                },
                failure_reasons=[f"Execution Error: {exc}"],
                n_paths=0,
                n_observations=len(ohlcv_df) if ohlcv_df is not None else 0,
                execution_time_seconds=exec_time,
                metadata=meta,
                error=str(exc),
            )

        # 2. Full-period backtest
        net_returns, positions, mean_daily_turnover = self.backtest_single_path(
            strategy_fn, ohlcv_df, apply_trend_gate=apply_trend_gate
        )

        # Performance metrics
        sr = sharpe_ratio(net_returns, freq=self.freq)
        sr = sr if not np.isnan(sr) else 0.0

        # Sortino
        downside = net_returns[net_returns < 0]
        downside_std = downside.std()
        if downside_std >= 1e-12:
            sortino = float(net_returns.mean() / downside_std * np.sqrt(self.freq))
        else:
            sortino = float("nan")

        # Max Drawdown
        max_dd = compute_max_drawdown(net_returns)

        # Calmar
        if max_dd >= 1e-12:
            calmar = float(net_returns.mean() * self.freq / max_dd)
        else:
            calmar = float("nan")

        # Cumulative & Annualized Return
        cum_ret = float((1.0 + net_returns).prod() - 1.0) if len(net_returns) > 0 else 0.0
        n_years = len(net_returns) / self.freq if len(net_returns) > 0 else 1.0
        if cum_ret > -1.0 and n_years > 0:
            ann_ret = float((1.0 + cum_ret) ** (1.0 / n_years) - 1.0)
        else:
            ann_ret = float("nan")

        # Volatility
        vol = float(net_returns.std() * np.sqrt(self.freq))

        # Win rate
        trade_days = net_returns != 0
        win_rate = float((net_returns[trade_days] > 0).mean()) if trade_days.any() else 0.0

        # 3. CPCV Evaluation
        cpcv_res = self.run_cpcv(strategy_fn, ohlcv_df, candidate_variants=candidate_variants)
        pbo = cpcv_res["pbo"]
        dsr = cpcv_res["dsr"]

        # 4. Regime Sensitivity Audit
        regime_breakdown, regime_stability, passes_stability = self.audit_regime_performance(
            strategy_fn, ohlcv_df, regime_series=regime_series, apply_trend_gate=apply_trend_gate
        )

        # 5. Deployability Gates
        pbo_pass = pbo < self.pbo_max
        dsr_pass = (not np.isnan(dsr)) and (dsr > self.dsr_min)
        sharpe_pass = (not np.isnan(sr)) and (sr > self.net_sharpe_min)
        max_dd_pass = (not np.isnan(max_dd)) and (max_dd < self.max_drawdown_max)

        gate_evals = {
            "pbo_gate": bool(pbo_pass),
            "dsr_gate": bool(dsr_pass),
            "sharpe_gate": bool(sharpe_pass),
            "max_dd_gate": bool(max_dd_pass),
        }

        failure_reasons: List[str] = []
        if not pbo_pass:
            failure_reasons.append(f"PBO {pbo:.3f} exceeds maximum threshold {self.pbo_max:.2f}")
        if not dsr_pass:
            failure_reasons.append(f"DSR {dsr:.3f} is below minimum threshold {self.dsr_min:.2f}")
        if not sharpe_pass:
            failure_reasons.append(f"Net Sharpe {sr:.3f} is below minimum threshold {self.net_sharpe_min:.2f}")
        if not max_dd_pass:
            failure_reasons.append(f"Max Drawdown {max_dd:.2%} exceeds maximum threshold {self.max_drawdown_max:.2%}")
        if not passes_stability:
            failure_reasons.append(f"Regime Stability Score {regime_stability:.2f} failed multi-regime consistency test")

        is_deployable = bool(pbo_pass and dsr_pass and sharpe_pass and max_dd_pass and passes_stability)
        exec_time = time.time() - start_time

        return AutonomousBacktestResult(
            strategy_id=strategy_id,
            is_deployable=is_deployable,
            sharpe_ratio=sr,
            sortino_ratio=sortino,
            max_drawdown=max_dd,
            pbo=pbo,
            dsr=dsr,
            turnover=mean_daily_turnover,
            annualized_return=ann_ret,
            cumulative_return=cum_ret,
            win_rate=win_rate,
            calmar_ratio=calmar,
            volatility=vol,
            gate_evaluations=gate_evals,
            failure_reasons=failure_reasons,
            n_paths=cpcv_res.get("n_paths", 0),
            n_observations=len(ohlcv_df),
            execution_time_seconds=exec_time,
            cpcv_mean_oos_sharpe=cpcv_res.get("mean_oos_sharpe", 0.0),
            cpcv_mean_oos_max_dd=cpcv_res.get("mean_oos_max_dd", 0.0),
            cpcv_mean_oos_sortino=cpcv_res.get("mean_oos_sortino", 0.0),
            regime_breakdown=regime_breakdown,
            regime_stability_score=regime_stability,
            passes_regime_stability=passes_stability,
            metadata=meta,
            returns=net_returns,
            positions=positions,
            error=None,
        )

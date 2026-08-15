"""
Unit tests for LLM Research Copilot (llm/research_copilot.py).
=============================================================
Tests:
- AST safety validation (whitelist, blacklist, dunder access, dangerous calls)
- Strategy metadata extraction
- Safe sandbox instantiation and verification
- ResearchCopilot hypothesis, paper, and mathematical formula synthesis
- Mock LLM provider integration, retry logic, and error handling
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from llm.research_copilot import (
    ResearchCopilot,
    SynthesizedSignalModule,
    extract_strategy_metadata,
    instantiate_module,
    validate_ast_safety,
    verify_signal_module,
)
from signals.base import SignalContext, SignalModule

# ===========================================================================
# 1. AST Safety Validation Tests
# ===========================================================================

class TestASTSafetyValidation:
    """Comprehensive test suite for validate_ast_safety()."""

    def test_valid_standard_signal_passes(self):
        code = """
import numpy as np
import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput

class SimpleMovingAverageSignal(SignalModule):
    name = "sma_cross"
    required_features = ["Close", "SMA_20"]
    period = 20

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        close = row.get("Close")
        sma = row.get("SMA_20")
        if close is None or sma is None or pd.isna(close) or pd.isna(sma):
            return SignalOutput(score=0.0, confidence=0.0, explanation="Missing SMA data")
        
        score = 0.5 if close > sma else -0.5
        return SignalOutput(score=score, confidence=1.0, explanation="SMA cross")

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        score = pd.Series(0.0, index=df.index)
        if "Close" in df.columns and "SMA_20" in df.columns:
            valid = df["Close"].notna() & df["SMA_20"].notna()
            bull = valid & (df["Close"] > df["SMA_20"])
            bear = valid & (df["Close"] < df["SMA_20"])
            score[bull] = 0.5
            score[bear] = -0.5
        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": "SMA vectorized",
            "meta_label_proba": 1.0,
        }, index=df.index)
"""
        is_safe, violations = validate_ast_safety(code)
        assert is_safe is True
        assert violations == []

    def test_empty_or_non_string_code_rejected(self):
        assert validate_ast_safety("")[0] is False
        assert validate_ast_safety("   \n\t  ")[0] is False
        assert validate_ast_safety(None)[0] is False  # type: ignore

    def test_syntax_error_rejected(self):
        bad_code = "def broken_code( -> invalid:"
        is_safe, violations = validate_ast_safety(bad_code)
        assert is_safe is False
        assert any("Syntax error" in v for v in violations)

    @pytest.mark.parametrize(
        "forbidden_mod",
        [
            "os",
            "sys",
            "subprocess",
            "socket",
            "requests",
            "urllib",
            "urllib.request",
            "http.client",
            "ctypes",
            "builtins",
            "importlib",
            "posix",
            "shutil",
            "pickle",
            "threading",
            "multiprocessing",
            "asyncio",
            "pty",
            "tempfile",
        ],
    )
    def test_forbidden_imports_rejected(self, forbidden_mod: str):
        code_import = f"import {forbidden_mod}"
        is_safe, violations = validate_ast_safety(code_import)
        assert is_safe is False
        assert any(f"Forbidden import '{forbidden_mod}'" in v or "Forbidden import" in v for v in violations)

        code_from = f"from {forbidden_mod} import something"
        is_safe, violations = validate_ast_safety(code_from)
        assert is_safe is False
        assert any("Forbidden import" in v for v in violations)

    def test_relative_import_rejected(self):
        code = "from . import helper"
        is_safe, violations = validate_ast_safety(code)
        assert is_safe is False
        assert any("Relative imports are not permitted" in v for v in violations)

    def test_forbidden_signals_submodule_import_rejected(self):
        code = "from signals.unapproved_secret import dangerous_func"
        is_safe, violations = validate_ast_safety(code)
        assert is_safe is False
        assert any("Forbidden import from signals package" in v for v in violations)

    @pytest.mark.parametrize(
        "forbidden_func",
        [
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
        ],
    )
    def test_forbidden_calls_and_names_rejected(self, forbidden_func: str):
        # Direct call
        code_call = f"x = {forbidden_func}('test')"
        is_safe, violations = validate_ast_safety(code_call)
        assert is_safe is False
        assert any(forbidden_func in v for v in violations)

        # Name reference / aliasing
        code_alias = f"f = {forbidden_func}"
        is_safe, violations = validate_ast_safety(code_alias)
        assert is_safe is False
        assert any(forbidden_func in v for v in violations)

    def test_dunder_attribute_access_rejected(self):
        # Exploit via class inheritance traversal
        exploit = "subclasses = ().__class__.__bases__[0].__subclasses__()"
        is_safe, violations = validate_ast_safety(exploit)
        assert is_safe is False
        assert any("Forbidden access to dunder attribute '__class__'" in v for v in violations)
        assert any("Forbidden access to dunder attribute '__bases__'" in v for v in violations)

    def test_dunder_attribute_mutation_rejected(self):
        code = "self.__class__ = object"
        is_safe, violations = validate_ast_safety(code)
        assert is_safe is False
        assert any("Forbidden mutation of dunder attribute '__class__'" in v for v in violations)

    def test_dunder_globals_access_rejected(self):
        code = "fn = lambda: None; g = fn.__globals__"
        is_safe, violations = validate_ast_safety(code)
        assert is_safe is False
        assert any("Forbidden access to dunder attribute '__globals__'" in v for v in violations)

    @pytest.mark.parametrize(
        "method_call",
        [
            "os.system('ls')",
            "proc.popen('ls')",
            "s.connect(('127.0.0.1', 80))",
            "sock.bind(('0.0.0.0', 8080))",
            "sock.listen(5)",
            "sock.send(b'data')",
            "sock.recv(1024)",
            "pd.read_pickle('file.pkl')",
            "df.to_pickle('file.pkl')",
            "df.to_sql('table', con)",
            "pd.read_csv('test.csv')",
            "df.to_csv('test.csv')",
            "pd.eval('1 + 1')",
            "df.query('a > 1')",
            "np.load('data.npy')",
            "np.save('data.npy', arr)",
        ],
    )
    def test_forbidden_method_calls_rejected(self, method_call: str):
        code = f"def run(): {method_call}"
        is_safe, _ = validate_ast_safety(code)
        assert is_safe is False

    def test_forbidden_attribute_aliasing_rejected(self):
        code = "alias = pd.eval"
        is_safe, violations = validate_ast_safety(code)
        assert is_safe is False
        assert any("Forbidden attribute access to 'eval'" in v for v in violations)

        code_io = "alias = df.to_csv"
        is_safe_io, violations_io = validate_ast_safety(code_io)
        assert is_safe_io is False
        assert any("Forbidden attribute access to 'to_csv'" in v for v in violations_io)

    def test_global_and_nonlocal_statements_rejected(self):
        code_global = "global my_var; my_var = 1"
        is_safe, violations = validate_ast_safety(code_global)
        assert is_safe is False
        assert any("Global statement" in v for v in violations)

    def test_safe_imports_allowed(self):
        code = """
import math
import numpy as np
import scipy.stats as stats
import pandas as pd
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from abc import ABC, abstractmethod
from signals.base import SignalModule, SignalContext, SignalOutput
from signals.registry import global_registry
"""
        is_safe, violations = validate_ast_safety(code)
        assert is_safe is True
        assert violations == []


# ===========================================================================
# 2. Strategy Metadata Extraction Tests
# ===========================================================================

class TestStrategyMetadataExtraction:
    """Comprehensive test suite for extract_strategy_metadata()."""

    def test_extract_full_metadata(self):
        code = """
\"\"\"
Module level docstring: Trend and Volatility Signal.
\"\"\"
import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput

class AdvancedTrendSignal(SignalModule):
    \"\"\"Calculates dual moving average trend with ATR filter.\"\"\"
    name = "advanced_trend"
    required_features = ["Close", "SMA_50", "SMA_200", "ATR_14"]
    meta_label_features = ["RSI_14", "Vol_20"]
    
    fast_period: int = 50
    slow_period: int = 200
    atr_multiplier: float = 1.5
    enable_filter: bool = True
    labels: list = ["Bull", "Bear"]

    def is_active_in_regime(self, macro) -> bool:
        return True

    def pre_compute(self, universe_df: pd.DataFrame, context: SignalContext) -> None:
        pass

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        return SignalOutput(score=0.0, confidence=1.0, explanation="ok")

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        return pd.DataFrame()
"""
        meta = extract_strategy_metadata(code)
        assert meta["name"] == "advanced_trend"
        assert meta["class_name"] == "AdvancedTrendSignal"
        assert "dual moving average" in meta["description"]
        assert meta["required_features"] == ["Close", "SMA_50", "SMA_200", "ATR_14"]
        assert meta["meta_label_features"] == ["RSI_14", "Vol_20"]
        assert meta["parameters"]["fast_period"] == 50
        assert meta["parameters"]["slow_period"] == 200
        assert meta["parameters"]["atr_multiplier"] == 1.5
        assert meta["parameters"]["enable_filter"] is True
        assert meta["parameters"]["labels"] == ["Bull", "Bear"]
        assert meta["has_pre_compute"] is True
        assert meta["has_vectorized"] is True
        assert meta["has_regime_gate"] is True
        assert meta["is_valid_signal_module"] is True
        assert "pandas" in meta["imports"] or "signals.base" in meta["imports"]

    def test_extract_fallback_name_from_class(self):
        code = """
from signals.base import SignalModule, SignalContext, SignalOutput
import pandas as pd

class CustomAlphaModelSignal(SignalModule):
    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        return SignalOutput(score=0.0, confidence=1.0, explanation="ok")
"""
        meta = extract_strategy_metadata(code)
        assert meta["class_name"] == "CustomAlphaModelSignal"
        assert meta["name"] == "custom_alpha_model"
        assert meta["has_vectorized"] is False
        assert meta["has_pre_compute"] is False
        assert meta["is_valid_signal_module"] is True

    def test_extract_tuple_unpacking(self):
        code = """
from signals.base import SignalModule, SignalContext, SignalOutput

class UnpackedParamsSignal(SignalModule):
    name, required_features = "unpacked_signal", ["Close", "Volume"]
    fast, slow = 10, 30

    def compute(self, row, context):
        return SignalOutput(score=0.0, confidence=1.0)
"""
        meta = extract_strategy_metadata(code)
        assert meta["name"] == "unpacked_signal"
        assert meta["required_features"] == ["Close", "Volume"]
        assert meta["parameters"]["fast"] == 10
        assert meta["parameters"]["slow"] == 30

    def test_extract_from_empty_or_invalid_code(self):
        meta_empty = extract_strategy_metadata("")
        assert meta_empty["is_valid_signal_module"] is False

        meta_bad = extract_strategy_metadata("def invalid_syntax(:")
        assert meta_bad["is_valid_signal_module"] is False
        assert "error" in meta_bad


# ===========================================================================
# 3. Sandbox Instantiation and Verification Tests
# ===========================================================================

class TestSandboxInstantiationAndVerification:
    """Test safe sandbox instantiation and test harness verification."""

    def test_instantiate_valid_module(self):
        code = """
import numpy as np
import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput

class FastMeanReversionSignal(SignalModule):
    name = "fast_reversion"
    required_features = ["Close", "RSI_14"]
    threshold = 80.0

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        rsi = row.get("RSI_14")
        if rsi is None or pd.isna(rsi):
            return SignalOutput(score=0.0, confidence=0.0, explanation="Missing")
        score = -0.8 if float(rsi) > self.threshold else 0.8 if float(rsi) < 20.0 else 0.0
        return SignalOutput(score=score, confidence=1.0, explanation=f"RSI {rsi}")

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        score = pd.Series(0.0, index=df.index)
        exp = pd.Series("Neutral", index=df.index)
        if "RSI_14" in df.columns:
            rsi = df["RSI_14"]
            over = rsi > self.threshold
            under = rsi < 20.0
            score[over] = -0.8
            exp[over] = "Overbought"
            score[under] = 0.8
            exp[under] = "Oversold"
        return pd.DataFrame({
            "score": score,
            "confidence": 1.0,
            "explanation": exp,
            "meta_label_proba": 1.0
        }, index=df.index)
"""
        instance = instantiate_module(code)
        assert isinstance(instance, SignalModule)
        assert instance.name == "fast_reversion"

        # Test compute on row
        row = pd.Series({"Close": 150.0, "RSI_14": 85.0})
        dummy_context = SignalContext(bar=None, fundamentals=None, macro=None)
        out = instance.compute(row, dummy_context)
        assert out.score == -0.8
        assert out.confidence == 1.0

        # Test vectorized compute on DataFrame
        df = pd.DataFrame({"Close": [150.0, 100.0, 120.0], "RSI_14": [85.0, 15.0, 50.0]})
        vec_out = instance.compute_vectorized(df, dummy_context)
        assert len(vec_out) == 3
        assert list(vec_out["score"]) == [-0.8, 0.8, 0.0]

    def test_instantiate_unsafe_module_raises_value_error(self):
        unsafe_code = """
import os
from signals.base import SignalModule

class UnsafeSignal(SignalModule):
    name = "unsafe"
"""
        with pytest.raises(ValueError, match="AST safety validation"):
            instantiate_module(unsafe_code)

    def test_verify_signal_module_harness(self):
        code = """
import numpy as np
import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput

class RobustTestSignal(SignalModule):
    name = "robust_test"
    required_features = ["Close", "RSI_14"]

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        rsi = row.get("RSI_14")
        if rsi is None or pd.isna(rsi):
            return SignalOutput(score=0.0, confidence=0.0, explanation="NaN")
        return SignalOutput(score=0.5, confidence=1.0, explanation="Bullish")

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        return pd.DataFrame({
            "score": pd.Series(0.5, index=df.index),
            "confidence": 1.0,
            "explanation": "Bullish",
            "meta_label_proba": 1.0,
        }, index=df.index)
"""
        passed, report = verify_signal_module(code)
        assert passed is True
        assert report["ast_safe"] is True
        assert report["instantiated"] is True
        assert report["compute_verified"] is True
        assert report["vectorized_verified"] is True
        assert report["errors"] == []


# ===========================================================================
# 4. ResearchCopilot Synthesis Tests
# ===========================================================================

class TestResearchCopilotSynthesis:
    """Comprehensive test suite for ResearchCopilot synthesis modes."""

    def setup_method(self):
        self.copilot = ResearchCopilot()

    def test_synthesize_empty_prompt_fails(self):
        res = self.copilot.synthesize("")
        assert res.success is False
        assert "empty" in res.explanation

    def test_synthesize_from_hypothesis_rsi_mean_reversion(self):
        hyp = "RSI mean reversion: buy when RSI(14) drops below 30, sell when RSI(14) rises above 70."
        res = self.copilot.synthesize_from_hypothesis(hyp, name="rsi_reversion_strategy")

        assert res.success is True
        assert res.validation_passed is True
        assert res.validation_errors == []
        assert res.synthesis_mode == "template"
        assert "rsi_reversion_strategy" in res.code or "rsi_mean_reversion" in res.code

        # Verify AST safety of generated code
        is_safe, violations = validate_ast_safety(res.code)
        assert is_safe is True
        assert violations == []

        # Verify metadata
        assert res.metadata["is_valid_signal_module"] is True
        assert "RSI_14" in res.metadata["required_features"]

        # Run verification harness
        passed, _ = verify_signal_module(res.code)
        assert passed is True

    def test_synthesize_from_hypothesis_bollinger_zscore(self):
        hyp = "Statistical arbitrage Z-score mean reversion: calculate price deviation from 20-day SMA in standard deviations. Short if Z > 2.0, Long if Z < -2.0."
        res = self.copilot.synthesize_from_hypothesis(
            hyp,
            name="bollinger_zscore",
            parameters={"entry_zscore": 2.5},
        )

        assert res.success is True
        assert res.validation_passed is True
        assert "bollinger_zscore" in res.metadata["name"]
        assert res.metadata["parameters"]["entry_zscore"] == 2.5

        passed, _ = verify_signal_module(res.code)
        assert passed is True

    def test_synthesize_from_hypothesis_volatility_breakout(self):
        hyp = "ATR volatility expansion breakout: enter long when price breaks above 20-day SMA by more than 1.5x ATR(14)."
        res = self.copilot.synthesize_from_hypothesis(
            hyp,
            name="atr_breakout",
            parameters={"breakout_multiplier": 2.0},
        )

        assert res.success is True
        assert res.validation_passed is True
        assert "ATR_14" in res.metadata["required_features"]

        passed, _ = verify_signal_module(res.code)
        assert passed is True

    def test_synthesize_from_paper_abstract(self):
        abstract = (
            "We document that momentum strategies which buy past 12-month winners and sell past "
            "12-month losers generate significant positive risk-adjusted excess returns across global equity markets."
        )
        res = self.copilot.synthesize_from_paper(
            paper_abstract=abstract,
            title="Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency",
            name="jegadeesh_titman_momentum",
        )

        assert res.success is True
        assert res.validation_passed is True
        assert "momentum" in res.code.lower()

        passed, _ = verify_signal_module(res.code)
        assert passed is True

    def test_synthesize_from_math_formula(self):
        desc = "Dual moving average crossover with long-term trend filter."
        formula = "Signal = +0.8 if Close > SMA_20 > SMA_200 else -0.8 if Close < SMA_20 < SMA_200 else 0.0"
        res = self.copilot.synthesize_from_math(
            equation_desc=desc,
            formula=formula,
            name="dual_sma_trend",
            parameters={"fast_period": 20, "slow_period": 200},
        )

        assert res.success is True
        assert res.validation_passed is True
        assert res.metadata["parameters"]["fast_period"] == 20
        assert res.metadata["parameters"]["slow_period"] == 200

        passed, _ = verify_signal_module(res.code)
        assert passed is True

    def test_synthesize_with_mock_llm_provider_success(self):
        mock_provider = MagicMock()
        mock_provider.call_structured.return_value = SynthesizedSignalModule(
            name="custom_llm_signal",
            class_name="CustomLlmSignal",
            description="LLM generated momentum signal.",
            code="""
import pandas as pd
from signals.base import SignalModule, SignalContext, SignalOutput

class CustomLlmSignal(SignalModule):
    name = "custom_llm_signal"
    required_features = ["Close", "SMA_20"]
    lookback = 20

    def compute(self, row: pd.Series, context: SignalContext) -> SignalOutput:
        return SignalOutput(score=0.5, confidence=1.0, explanation="Bullish")

    def compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame:
        return pd.DataFrame({
            "score": pd.Series(0.5, index=df.index),
            "confidence": 1.0,
            "explanation": "Bullish",
            "meta_label_proba": 1.0
        }, index=df.index)
""",
            required_features=["Close", "SMA_20"],
            rationale="Custom LLM synthesized alpha model.",
        )

        copilot = ResearchCopilot(provider=mock_provider)
        res = copilot.synthesize_from_hypothesis("Custom trend following alpha")

        assert res.success is True
        assert res.synthesis_mode == "llm"
        assert res.metadata["name"] == "custom_llm_signal"
        assert res.metadata["class_name"] == "CustomLlmSignal"
        assert mock_provider.call_structured.called

    def test_synthesize_with_mock_llm_provider_ast_failure_and_fallback(self):
        mock_provider = MagicMock()
        # Returns unsafe code with 'import os' and 'eval()'
        mock_provider.call_structured.return_value = SynthesizedSignalModule(
            name="unsafe_llm_signal",
            class_name="UnsafeLlmSignal",
            description="Unsafe code.",
            code="""
import os
from signals.base import SignalModule
class UnsafeLlmSignal(SignalModule):
    name = "unsafe_llm_signal"
    def compute(self, row, context):
        eval("os.system('id')")
        return None
""",
            required_features=[],
            rationale="Unsafe code.",
        )

        copilot = ResearchCopilot(provider=mock_provider)
        res = copilot.synthesize_from_hypothesis(
            "RSI mean reversion", template_fallback=True, max_retries=1
        )

        # Provider failed AST validation on all attempts -> Copilot fell back safely to deterministic template
        assert res.success is True
        assert res.synthesis_mode == "template"
        assert res.validation_passed is True
        assert "import os" not in res.code
        assert "eval(" not in res.code

    def test_synthesize_with_mock_llm_provider_failure_no_fallback(self):
        mock_provider = MagicMock()
        mock_provider.call_structured.side_effect = RuntimeError("API rate limit exceeded")

        copilot = ResearchCopilot(provider=mock_provider)
        res = copilot.synthesize_from_hypothesis(
            "RSI mean reversion", template_fallback=False
        )

        assert res.success is False
        assert res.synthesis_mode == "failed"

    def test_clean_code_fences_utility(self):
        fenced_code = "```python\nimport pandas as pd\nclass TestSignal:\n    pass\n```"
        cleaned = ResearchCopilot._clean_code_fences(fenced_code)
        assert cleaned.startswith("import pandas as pd")
        assert not cleaned.startswith("```")
        assert not cleaned.endswith("```")

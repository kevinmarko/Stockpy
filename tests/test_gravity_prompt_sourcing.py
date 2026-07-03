import pytest
from unittest.mock import patch
from ai_verification_prompts import (
    SYSTEM_PROMPT,
    ALL_PROMPTS,
    _BASELINE_SYSTEM_PROMPT,
    _BASELINE_STEP_1_PROMPT,
    _BASELINE_STEP_2_PROMPT,
    _BASELINE_STEP_3_PROMPT,
    _BASELINE_STEP_4_PROMPT,
    _BASELINE_STEP_5_PROMPT,
    _BASELINE_STEP_6_PROMPT,
    _BASELINE_STEP_7_PROMPT,
    _BASELINE_STEP_8_PROMPT,
)
from prompt_registry import get_registry

def test_registry_disabled_falls_back_to_baseline():
    """
    Test that when PROMPT_REGISTRY_ENABLED is False (the default for the Gravity auditor),
    the prompts loaded by ai_verification_prompts match the baseline literals exactly,
    preventing any behavior drift.
    """
    reg = get_registry()
    
    # Compare with .strip() to ignore minor whitespace differences (like leading/trailing newlines
    # introduced by python multiline string literals vs file saves).
    assert SYSTEM_PROMPT.strip() == _BASELINE_SYSTEM_PROMPT.strip()
    
    baselines = [
        _BASELINE_STEP_1_PROMPT,
        _BASELINE_STEP_2_PROMPT,
        _BASELINE_STEP_3_PROMPT,
        _BASELINE_STEP_4_PROMPT,
        _BASELINE_STEP_5_PROMPT,
        _BASELINE_STEP_6_PROMPT,
        _BASELINE_STEP_7_PROMPT,
        _BASELINE_STEP_8_PROMPT,
    ]
    
    for i, step_prompt in enumerate(ALL_PROMPTS):
        baseline = baselines[i]
        assert step_prompt.step_number == baseline.step_number
        assert step_prompt.step_title == baseline.step_title
        # Ignore leading/trailing whitespace drift
        assert step_prompt.prompt_text.strip() == baseline.prompt_text.strip()
        assert step_prompt.criteria == baseline.criteria


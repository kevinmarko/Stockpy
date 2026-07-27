"""Risk-overlay measurement package.

Deliberately kept free of engine/orchestrator imports: every module here is
pure math over already-fetched DataFrames/dicts so it stays importable (and
unit-testable) without pulling in the heavy ``main_orchestrator`` /
``data_engine`` chain. I/O belongs to the callers (``pipeline/``, ``data/``).
"""

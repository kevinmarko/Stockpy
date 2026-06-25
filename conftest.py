"""
conftest.py — Root-level pytest configuration for InvestYo Quant Platform.

Adds the project root directory to sys.path so that all test modules can
import the platform packages (strategy_engine, sizing, signals, etc.)
without needing to install the project as a package or set PYTHONPATH
manually.
"""
import sys
import os

# Add the project root (this file's directory) to sys.path so that
# `from sizing.kelly import ...`, `from strategy_engine import ...`, etc.
# resolve correctly regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(__file__))

"""
conftest.py — Root-level pytest configuration for InvestYo Quant Platform.

Adds the project root directory to sys.path so that all test modules can
import the platform packages (strategy_engine, sizing, signals, etc.)
without needing to install the project as a package or set PYTHONPATH
manually.
"""
import sys
import os

import pytest

# Add the project root (this file's directory) to sys.path so that
# `from sizing.kelly import ...`, `from strategy_engine import ...`, etc.
# resolve correctly regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture(autouse=True)
def _no_gdelt_throttle_in_tests(monkeypatch):
    """Disable the shared GDELT request throttle and reset its limiter state
    for every test.

    ``settings.GDELT_MIN_REQUEST_INTERVAL_SECONDS`` defaults to 5 s of REAL
    ``time.sleep`` between GDELT calls, which is correct in production and
    intolerable in a suite where a single windowed-backfill test issues 60 of
    them. Tests that are specifically about the limiter set their own values
    explicitly, so zeroing it here changes nothing for them.

    The state reset matters just as much as the interval: the limiter's
    consecutive-failure count and cooldown are module-level, so without this a
    test that exercises the breaker would leak an open cooldown into every
    test that ran after it and silently turn their GDELT calls into skips.
    """
    from settings import settings as _settings
    from data.sentiment_sources import reset_gdelt_rate_limiter

    monkeypatch.setattr(_settings, "GDELT_MIN_REQUEST_INTERVAL_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(_settings, "GDELT_RETRY_BACKOFF_SECONDS", 0.0, raising=False)
    reset_gdelt_rate_limiter()
    yield
    reset_gdelt_rate_limiter()

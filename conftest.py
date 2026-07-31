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


@pytest.fixture(autouse=True)
def _no_fmp_throttle_in_tests(monkeypatch):
    """Disable the shared FMP request throttle and reset its client state for
    every test — the sibling of ``_no_gdelt_throttle_in_tests`` above, for the
    same two reasons.

    ``settings.FMP_MIN_REQUEST_INTERVAL_SECONDS`` defaults to 0.25 s of REAL
    ``time.sleep`` between FMP calls (240 req/min by construction), which is
    correct in production and pure dead weight in a suite where a single
    fixture-driven test issues dozens of them. ``data/fmp_client.py``'s own
    tests set their own values explicitly, so zeroing it here changes nothing
    for them.

    The state reset matters at least as much as the interval, and here it
    guards MORE state than the GDELT fixture does. ``data/fmp_client.py``
    keeps FIVE pieces of module-level state: the spacing clock, the
    consecutive-failure run, the cooldown, the once-per-process 401 log latch,
    and — the dangerous one — the per-endpoint DEAD-ENDPOINT set. A test that
    exercises the breaker or the 403/entitlement path would otherwise leak an
    open cooldown or a latched dead endpoint into every test that ran after it,
    silently turning their FMP calls into zero-network skips: a whole file of
    tests passing for entirely the wrong reason. ``reset_fmp_rate_limiter()``
    clears all five plus the call counters.

    The import is lazy and inside the fixture (rather than at module scope) so
    a broken ``data/fmp_client.py`` import surfaces as a test failure rather
    than breaking collection for the ENTIRE suite.
    """
    from settings import settings as _settings
    from data.fmp_client import reset_fmp_rate_limiter

    monkeypatch.setattr(_settings, "FMP_MIN_REQUEST_INTERVAL_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(_settings, "FMP_RETRY_BACKOFF_SECONDS", 0.0, raising=False)
    reset_fmp_rate_limiter()
    yield
    reset_fmp_rate_limiter()

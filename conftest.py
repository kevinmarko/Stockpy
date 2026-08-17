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

# Reset settings singleton to clean defaults on test session initialization
try:
    from settings import Settings, settings
    import runtime_flags
    # Reset singleton to clean defaults without reading developer's local .env file
    _defaults = Settings(_env_file=None)
    for field_name in type(_defaults).model_fields:
        setattr(settings, field_name, getattr(_defaults, field_name))
except Exception:
    pass


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


@pytest.fixture(autouse=True)
def _clean_meta_registry_between_tests():
    """Reset global_meta_registry state so tests that register temporary
    MetaLabelers do not leak gating decisions into subsequent test files."""
    try:
        import ml.meta_labeling as _ml_meta
        _ml_meta.global_meta_registry._labelers.clear()
        yield
        _ml_meta.global_meta_registry._labelers.clear()
    except Exception:
        yield


@pytest.fixture(autouse=True)
def _clean_settings_between_tests(monkeypatch):
    """Reset mutable settings attributes between tests so tests that mutate
    settings (e.g. weights, disabled modules) don't leak state.

    Dead-letter-per-key (CONSTRAINT #6, matching runtime_flags.py's own
    convention): each key is reset independently so one bad/renamed field
    name can never silently abort the reset of every key listed after it.
    A prior version reset all keys in a single try/except around the whole
    loop with "KILL_SWITCH_ACTIVE" first in the tuple -- that name was never
    a real Settings field (the kill switch is file-based state owned by
    execution/kill_switch.py, not a Settings field) and its getattr()
    silently raised AttributeError every single test, caught by the outer
    except and aborting before ever resetting
    VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED, META_LABELING_ENABLED,
    or META_LABEL_MIN_CONFIDENCE for the entire session. Dropped the bogus
    key rather than trying to resolve it to a real field, since no such
    field exists."""
    try:
        import copy
        from settings import Settings, settings
        _clean = Settings(_env_file=None)
    except Exception:
        return
    for k in (
        "SIGNAL_WEIGHTS",
        "DISABLED_SIGNAL_MODULES",
        "REGIME_SIGNAL_WEIGHTS",
        "HISTORICAL_STORE_ENABLED",
        "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED",
        "META_LABELING_ENABLED",
        "META_LABEL_MIN_CONFIDENCE",
    ):
        try:
            val = getattr(_clean, k)
            if isinstance(val, (dict, list, set)):
                val = copy.deepcopy(val)
            monkeypatch.setattr(settings, k, val, raising=False)
        except Exception:
            continue


@pytest.fixture(autouse=True)
def _clean_signal_registry_between_tests():
    """Reset global_registry._modules so dynamically registered mock/synthesized
    signal modules (e.g. from research copilot tests) do not leak into other tests."""
    standard_names = {
        "macro_regime", "graham_value", "dividend_quality", "macd_momentum",
        "aroon_trend", "forecast_alignment", "relative_strength", "rsi_extremes",
        "sortino_drawdown", "edge_garch", "timeseries_momentum", "cross_sectional_momentum",
        "rsi2_mean_reversion", "multifactor", "regime_multiplier", "lgbm_ranker",
        "news_catalyst", "sector_quality_rank", "vrp_premium_selling", "options_flow_sentiment",
    }
    try:
        import signals  # noqa: F401 -- ensures all 20 standard modules are registered
        from signals.registry import global_registry
        for k in list(global_registry._modules.keys()):
            if k not in standard_names:
                global_registry.unregister(k)
    except Exception:
        pass

    yield

    try:
        from signals.registry import global_registry
        for k in list(global_registry._modules.keys()):
            if k not in standard_names:
                global_registry.unregister(k)
    except Exception:
        pass

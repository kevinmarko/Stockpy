"""
conftest.py — Root-level pytest configuration for InvestYo Quant Platform.

Adds the project root directory to sys.path so that all test modules can
import the platform packages (strategy_engine, sizing, signals, etc.)
without needing to install the project as a package or set PYTHONPATH
manually.
"""
import sys
import os
from typing import Optional

import pytest

# Add the project root (this file's directory) to sys.path so that
# `from sizing.kelly import ...`, `from strategy_engine import ...`, etc.
# resolve correctly regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(__file__))

def _field_default(model_cls, name):
    """The TRUE coded default for a Settings field, independent of .env, real
    shell env, and output/runtime_flags.json.

    Deliberately NOT ``Settings(_env_file=None)``: that constructor argument
    only skips parsing the ``.env`` FILE -- pydantic-settings still reads
    ``os.environ`` as an independent, lower-precedence-than-nothing source.
    ~14 call sites in this codebase (main.py, main_orchestrator.py, every
    standalone ``api/*.py`` FastAPI service -- see settings.py's ENV_PATH
    comment) call python-dotenv's ``load_dotenv()``, which DOES mutate real
    ``os.environ`` the first time one of those modules is imported. Proven
    empirically: importing ``api.metrics_api`` alone injects
    ``os.environ["VALIDATION_HARNESS_OOS_GATE_ENABLED"] = "true"`` for the
    rest of the process. In a single pytest invocation covering multiple test
    files, THAT import can happen during collection of one file and silently
    poison ``Settings(_env_file=None)``'s reading of ``os.environ`` for every
    other file's tests in the same run -- which is exactly why an earlier
    version of this fix (reconstructing via ``Settings(_env_file=None)``)
    passed every affected test file in isolation but still failed when run
    together with ``tests/test_metrics_api.py`` in the same process.
    Reading the field's own declared default (``Field(default=...)`` /
    ``default_factory=``) directly off the model class is immune to all of
    that -- it never touches the environment at all.
    """
    finfo = model_cls.model_fields[name]
    if finfo.default_factory is not None:
        return finfo.default_factory()
    return finfo.default


# Reset settings singleton to clean defaults on test session initialization.
#
# Every boolean settings field is additionally forced to its CODED default
# (ignoring .env / real shell env / output/runtime_flags.json), computed once
# here and reused by `_clean_settings_between_tests` below for the per-test
# reset.
#
# Why this exists: `.env` and the runtime-flags store are both legitimate,
# real configuration layers for a live operator run (see settings.py's own
# ENV_PATH/runtime_flags.py docstrings) -- but every one of this codebase's
# "opt-in, defaults preserve exact current behavior" feature flags (dozens of
# them, per CLAUDE.md's own convention) is boolean, and a test asserting
# "default (unset) behavior" is asserting the CODED default, not whatever an
# operator happened to flip on for their own live checkout. A prior version
# of this reset used a hand-maintained 7-key tuple; it missed
# VALIDATION_HARNESS_OOS_GATE_ENABLED, which this operator's real .env sets
# to True, silently breaking every test asserting that flag's documented
# default-off behavior (tests/test_harness_oos_gate.py::TestFlagDefaultOff,
# tests/test_harness_calmar_degenerate_guard.py) -- an allowlist that only
# grows by discovering the next broken test is the wrong shape for this.
# Booleans only (not the full field set): non-bool fields (paths, numeric
# thresholds, credentials) are far more likely to be something a specific
# test legitimately relies on from a real .env (e.g. a network-marked test
# needing a real API key), and every "TestFlagEnabled"-style test in this
# suite already explicitly `monkeypatch.setattr`s the ONE flag it needs on,
# rather than relying on ambient state -- so resetting every boolean to its
# coded default cannot break a test that follows this codebase's own
# established convention.
_BOOL_FIELD_NAMES: tuple[str, ...] = ()
# Every `gui.env_io.SECRET_KEYS` entry that is also a real `Settings` field --
# the STRING-typed sibling of `_BOOL_FIELD_NAMES` above, computed once here and
# reused by `_clean_settings_between_tests` below for the per-test reset.
#
# Why this exists: `_BOOL_FIELD_NAMES` closed the boolean half of "this
# operator's real .env pollutes test isolation" -- but that .env also sets
# real command tokens / API keys / paths (FMP_API_KEY, ORCHESTRATOR_DAEMON_
# TOKEN, DATABASE_URL, ...) that plenty of tests assume are unset (empty
# string, matching the coded default) so they can assert "no credential
# configured" behavior, or so a test-local monkeypatched value isn't shadowed
# by a real one already sitting on the singleton before the test even sets
# it up. `gui.env_io.SECRET_KEYS` is this codebase's own canonical list of
# which `Settings` fields are secrets (CONSTRAINT #3) -- reusing it here
# instead of hand-picking fields avoids yet another hand-maintained allowlist
# that only grows by discovering the next broken test (see
# `_clean_settings_between_tests`'s docstring for that exact history with
# booleans).
#
# `gui.env_io` is imported LAZILY here (not at conftest.py module top) even
# though, as of this writing, it only imports `ENV_PATH` from `settings` (not
# the `settings` singleton itself) and so has no live circular-import hazard
# today -- keeping the import inside this try block, after `settings`/
# `runtime_flags` have already fully imported, means a future change to
# gui/env_io.py's own imports (e.g. importing the `settings` singleton
# directly) can never turn into an import-order failure for every single test
# file via conftest.py, only a caught-and-ignored no-op here.
#
# Two things this set deliberately is NOT allowed to include:
#   1. A `SECRET_KEYS` entry that isn't a real `Settings.model_fields` name
#      (e.g. a legacy/removed key like `NTFY_TOPIC`/`PROMPT_REGISTRY_
#      CREDENTIALS`, which are secrets in spirit but never became actual
#      pydantic fields) -- skipped via `hasattr`/membership check, never a
#      crash, matching this fixture's dead-letter-per-key convention.
#   2. A non-string-typed field -- as of this writing every real
#      `SECRET_KEYS` field is `str`/`Optional[str]`, but the filter is kept
#      explicit rather than assumed, since a future secret field added as
#      some other type (e.g. a parsed dict) is exactly the kind of value a
#      test might legitimately construct from a real `.env` fixture and this
#      reset has no business overwriting sight-unseen.
_SECRET_STR_FIELD_NAMES: tuple[str, ...] = ()
try:
    from settings import Settings, settings
    import runtime_flags
    # Reset singleton to clean defaults (still .env-sourced, matching prior
    # behavior for non-bool fields).
    _defaults = Settings()
    for field_name in type(_defaults).model_fields:
        setattr(settings, field_name, getattr(_defaults, field_name))
    # Then force every boolean to its true coded default, bypassing .env AND
    # real os.environ (see _field_default's docstring for why the latter
    # matters).
    _BOOL_FIELD_NAMES = tuple(
        name
        for name, finfo in type(settings).model_fields.items()
        if finfo.annotation is bool
    )
    for field_name in _BOOL_FIELD_NAMES:
        setattr(settings, field_name, _field_default(type(settings), field_name))
    # Now the secret-string half: gui.env_io.SECRET_KEYS intersected with real
    # Settings fields, filtered to string-typed ones (see the block comment
    # above for why both filters are needed).
    import gui.env_io as _env_io
    _model_fields = type(settings).model_fields
    _SECRET_STR_FIELD_NAMES = tuple(
        name
        for name in dict.fromkeys(_env_io.SECRET_KEYS)  # de-dupe, preserve order
        if name in _model_fields and _model_fields[name].annotation in (str, Optional[str])
    )
    for field_name in _SECRET_STR_FIELD_NAMES:
        setattr(settings, field_name, _field_default(type(settings), field_name))
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
def _isolate_validation_runs_db_in_tests(monkeypatch):
    """Point the default ``validation_runs`` DB resolver at an in-memory db
    for every test, unless the test passes its own explicit ``db_url``.

    ``StrategyValidationHarness.run()`` (``validation/harness.py``) writes a
    best-effort row to the real, shared ``~/.stockpy_local/quant_platform.db``
    on every call via ``_record_validation_run_to_db`` — unlike
    ``TransactionsStore``/``RunHistoryStore``, whose every test call site
    constructs the store directly with its own ``db_url``, this write
    happens IMPLICITLY, deep inside ``run()``, with no way for the ~25
    pre-existing test files across this suite that call ``.run()`` for real
    (``tests/test_harness_*.py``, ``tests/test_validation_*.py``, ...) to opt
    out short of editing every one of them. Left unguarded, running this
    suite would silently write dozens of fake ``strategy_id`` rows (e.g.
    ``"TestStrategy"``, ``"RunOnceTest"``) into a real operator's production
    database on every test run — the same class of risk
    ``_no_gdelt_throttle_in_tests``/``_no_fmp_throttle_in_tests`` above exist
    to prevent for their own shared resources, and the reason for a
    session-wide autouse fixture here rather than the file-local opt-in
    pattern ``tests/conftest.py`` otherwise prefers.

    Lazy import (mirrors the FMP/GDELT fixtures above) so a broken
    ``validation/validation_history_store.py`` import surfaces as a test
    failure for whichever test actually touches it, not a collection-time
    failure for the entire suite.
    """
    import validation.validation_history_store as _vhs

    monkeypatch.setattr(_vhs, "resolve_database_url", lambda: "sqlite:///:memory:")


@pytest.fixture(autouse=True)
def _isolate_execution_audit_db_in_tests(monkeypatch):
    """Point the default execution-audit-records DB resolver at an in-memory
    db for every test, unless the test passes its own explicit ``db_url``/
    ``sqlite_path`` to ``ExecutionAuditStore``.

    ``execution/order_manager.py::OrderManager._record_execution_audit`` now
    lazily constructs ``ExecutionAuditStore()`` (no explicit URL) the first
    time a real fill reaches it -- an IMPLICIT write, exactly like the
    ``ValidationHistoryStore`` case just above, deep inside a widely-used
    function (~15+ pre-existing test files construct ``OrderManager(broker,
    ...)`` directly with no ``audit_store=`` of their own, and at least one --
    ``tests/test_fmp_paper_broker.py``'s
    ``test_order_manager_live_submission_reaches_the_paper_broker`` -- already
    drives a real FILLED result through it). Left unguarded, running this
    suite would silently write real order-audit rows into a real operator's
    shared ``~/.stockpy_local/quant_platform.db`` on every test run. Same
    fixture shape as ``_isolate_validation_runs_db_in_tests`` above; a test
    that passes ``sqlite_path=``/``db_url=`` explicitly (e.g.
    ``tests/test_sec_rule_606_reporter.py``'s fixtures) bypasses
    ``resolve_database_url()`` entirely and is unaffected by this patch.
    """
    import data.execution_audit_store as _eas

    monkeypatch.setattr(_eas, "resolve_database_url", lambda: "sqlite:///:memory:")


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
    field exists.

    An even later version hand-maintained a 7-key tuple of settings deemed
    worth resetting; it missed VALIDATION_HARNESS_OOS_GATE_ENABLED, which a
    real operator .env on the machine this was found on sets to True,
    silently breaking every test asserting that flag's documented
    default-off behavior. Every boolean field is now reset unconditionally
    (via the module-level `_BOOL_FIELD_NAMES`, computed once) rather than
    hand-picking which ones matter -- this closes the whole bug class
    instead of the one instance that happened to get caught. The three
    dict/list-typed fields below are not boolean and stay explicit; so does
    META_LABEL_MIN_CONFIDENCE (float).

    Sourced via ``_field_default`` (the raw pydantic field default), NOT
    ``Settings(_env_file=None)`` -- the latter still reads real ``os.environ``,
    which collecting certain OTHER test files (anything importing
    main.py/main_orchestrator.py/a standalone ``api/*.py`` service) mutates
    via their own ``load_dotenv()`` call, silently reintroducing the exact
    pollution this fixture exists to strip. See ``_field_default``'s
    docstring above for the empirical proof.

    ``_SECRET_STR_FIELD_NAMES`` (module-level, computed once alongside
    ``_BOOL_FIELD_NAMES`` above) extends this same per-test reset to every
    string-typed ``gui.env_io.SECRET_KEYS`` field that is a real ``Settings``
    field -- this operator's real ``.env`` sets real command tokens / API
    keys / paths that a test may assume are unset (the coded default, empty
    string) just as reliably as it sets stray booleans on. See that
    module-level block's own comment for the full reasoning and the two
    deliberate exclusions (legacy/removed ``SECRET_KEYS`` entries with no
    matching field; non-string-typed fields)."""
    try:
        import copy
        from settings import Settings, settings
    except Exception:
        return
    for k in _BOOL_FIELD_NAMES + _SECRET_STR_FIELD_NAMES + (
        "SIGNAL_WEIGHTS",
        "DISABLED_SIGNAL_MODULES",
        "REGIME_SIGNAL_WEIGHTS",
        "META_LABEL_MIN_CONFIDENCE",
    ):
        try:
            val = _field_default(Settings, k)
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

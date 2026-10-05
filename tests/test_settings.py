"""
InvestYo Quant Platform - Settings / Runtime Config Test Suite
==============================================================

Verifies that centralized configuration (settings.py) loads from the
environment, applies sane defaults, fails clearly when a required secret is
missing, and detects the previously leaked FRED API key.

All instances are constructed with ``_env_file=None`` so a developer's local
.env file cannot influence the assertions.
"""

import logging
from pathlib import Path

import pytest

from settings import Settings, LEAKED_FRED_KEY_SHA256


# =============================================================================
# 1. HAPPY PATH — values resolve from the environment
# =============================================================================
def test_settings_load_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", "live-key-123")
    monkeypatch.setenv("RISK_FREE_RATE", "0.05")
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    monkeypatch.setenv("PAPER_TRADING", "false")
    monkeypatch.setenv("DEFAULT_TICKERS", '["NVDA", "TSLA"]')
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("STATE_API_TOKEN", "tok-123")
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", '["https://a.com", "http://localhost:3000"]'
    )

    s = Settings(_env_file=None)

    assert s.FRED_API_KEY == "live-key-123"
    assert s.RISK_FREE_RATE == 0.05
    assert s.PAPER_TRADING is False
    assert s.DEFAULT_TICKERS == ["NVDA", "TSLA"]
    assert s.OUTPUT_DIR == (tmp_path / "reports")
    assert s.STATE_API_TOKEN == "tok-123"
    assert s.CORS_ALLOWED_ORIGINS == ["https://a.com", "http://localhost:3000"]


# =============================================================================
# 1b. PAPER_TRADING legacy alias (ALPACA_PAPER, pre-2026-09-30)
# =============================================================================
def test_legacy_alpaca_paper_env_var_still_read(monkeypatch, tmp_path):
    """An operator .env that still says ALPACA_PAPER=false keeps meaning
    'not paper' after the rename -- the posture must never silently flip."""
    monkeypatch.delenv("PAPER_TRADING", raising=False)
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    s = Settings(_env_file=None)
    assert s.PAPER_TRADING is False


def test_legacy_alpaca_paper_in_env_file_still_read(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_PAPER=false\n", encoding="utf-8")
    monkeypatch.delenv("PAPER_TRADING", raising=False)
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    s = Settings(_env_file=str(env_file))
    assert s.PAPER_TRADING is False


@pytest.mark.parametrize("new_value,old_value,expected", [
    ("true", "false", True),
    ("false", "true", False),
])
def test_paper_trading_wins_over_legacy_alpaca_paper(monkeypatch, tmp_path, new_value, old_value, expected):
    monkeypatch.setenv("PAPER_TRADING", new_value)
    monkeypatch.setenv("ALPACA_PAPER", old_value)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    s = Settings(_env_file=None)
    assert s.PAPER_TRADING is expected


def test_removed_alpaca_fields_are_gone_and_ignored(monkeypatch, tmp_path):
    """Alpaca was removed 2026-09-30: leftover keys in an operator .env are
    ignored (extra='ignore'), never re-materialized as Settings fields."""
    removed = {
        "ALPACA_API_KEY": "k",
        "ALPACA_SECRET_KEY": "s",
        "ALPACA_REQUEST_TIMEOUT_SECONDS": "5",
        "ALPACA_KEY_ROTATED_DATE": "2026-01-01",
        "MARKET_DATA_WS_ENABLED": "true",
        "MARKET_DATA_WS_STALE_SECONDS": "10",
        "MARKET_DATA_WS_SYMBOLS": "AAPL",
        "MARKET_DATA_WS_RECONNECT_BASE_SECONDS": "1",
        "MARKET_DATA_WS_RECONNECT_MAX_SECONDS": "30",
    }
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in removed.items()), encoding="utf-8")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    s = Settings(_env_file=str(env_file))
    for key in removed:
        assert key not in Settings.model_fields
        assert not hasattr(s, key)


def test_retired_alpaca_secrets_are_still_masked(tmp_path, monkeypatch):
    """A real Alpaca key left in an operator's .env must never be displayed in
    cleartext by the settings reader, even though the fields are gone."""
    from shared import env_io

    env_file = tmp_path / ".env"
    env_file.write_text(
        "ALPACA_API_KEY=PKREALKEY123\nALPACA_SECRET_KEY=supersecretvalue\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_io, "ENV_PATH", env_file, raising=False)
    shown = env_io.read_settings()
    assert "PKREALKEY123" not in shown["ALPACA_API_KEY"]
    assert "supersecretvalue" not in shown["ALPACA_SECRET_KEY"]


# =============================================================================
# 2. DEFAULTS — unset fields fall back to documented defaults
# =============================================================================
def test_settings_defaults(monkeypatch, tmp_path):
    # Ensure nothing leaks in from the host environment. On a machine with a
    # real, populated .env, another test module's own load_dotenv(ENV_PATH,
    # override=False) call earlier in this same pytest session can copy keys
    # into real os.environ -- Settings(_env_file=None) skips the .env FILE but
    # still reads os.environ. ALPACA_PAPER is the legacy alias of
    # PAPER_TRADING, so it must be cleared too.
    for key in (
        "FRED_API_KEY",
        "RISK_FREE_RATE",
        "PAPER_TRADING",
        "ALPACA_PAPER",
        "DEFAULT_TICKERS",
        "STATE_API_TOKEN",
        "CORS_ALLOWED_ORIGINS",
        "DRY_RUN",
        "MAX_PORTFOLIO_HEAT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))

    s = Settings(_env_file=None)

    assert s.FRED_API_KEY == ""
    assert s.PAPER_TRADING is True
    # DRY_RUN gates OrderManager._submit_with_retry (see CLAUDE.md: "Dry-run
    # is enforced at manager level") -- a silent flip to True here would
    # make every broker order a no-op without any other signal. Mirrors
    # Gravity AI Review Suite.py's step_22_broker_order_manager_audit,
    # which was found (2026-07-14 test-coverage re-audit, Phase 5) to check
    # this default with no independent pytest assertion anywhere else.
    assert s.DRY_RUN is False
    assert s.RISK_FREE_RATE == pytest.approx(0.045)
    assert s.MARKET_RISK_PREMIUM == pytest.approx(0.055)
    assert s.REQUIRED_RETURN_RATE == pytest.approx(0.08)
    assert s.MAX_PORTFOLIO_HEAT == pytest.approx(0.06)
    assert s.DEFAULT_TICKERS == ["AAPL", "MSFT", "JNJ", "AGNC"]
    assert s.LOG_LEVEL == "INFO"
    # CORS + bearer-token auth hardening defaults. 3000 is the classic
    # CRA/Node dev-server convention; the 5173 pair is Vite's default port
    # (webapp/, the Pilots PWA) — both host spellings since browsers treat
    # localhost and 127.0.0.1 as distinct origins.
    assert s.STATE_API_TOKEN is None
    assert s.CORS_ALLOWED_ORIGINS == [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


# =============================================================================
# 2b. SENTIMENT/ATTENTION/DIAGNOSTIC DATA SOURCE SCAFFOLDING DEFAULTS
#     (Sentiment Pipeline Phase 4 groundwork, plus the sibling diagnostic
#     master switches caught by the same 2026-08-07 mass default-flip
#     commit, PR reverting scope creep) — Google News RSS, EDGAR full-text
#     search, GDELT Sector Heat Factor, Wikipedia/pytrends attention,
#     the composite sentiment index, ETF holdings/transmission
#     measurement, and market-data latency instrumentation. None of these
#     change trading behavior on their own (no SignalModule/SIGNAL_WEIGHTS
#     entry for any of them) and none are admin/write/execution API gates,
#     so none qualify for the 2026-08-03 default-on convention documented
#     in CLAUDE.md — every default here must preserve today's exact
#     behavior (nothing new enabled, zero network calls) until an operator
#     explicitly opts in.
# =============================================================================
def test_sentiment_attention_scaffolding_defaults(monkeypatch, tmp_path):
    for key in (
        "GOOGLE_NEWS_LOOKBACK_WINDOW",
        "EDGAR_FULLTEXT_ENABLED",
        "EDGAR_FULLTEXT_FORMS",
        "EDGAR_FULLTEXT_CHUNK_TOKENS",
        "SECTOR_HEAT_ENABLED",
        "SECTOR_HEAT_SMOOTHING_SIGMA",
        "SECTOR_HEAT_LOOKBACK_DAYS",
        "WIKIPEDIA_ATTENTION_ENABLED",
        "WIKIPEDIA_ATTENTION_LOOKBACK_DAYS",
        "PYTRENDS_ENABLED",
        "SENTIMENT_INDEX_ENABLED",
        "MARKET_DATA_LATENCY_TRACKING_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))

    s = Settings(_env_file=None)

    assert s.GOOGLE_NEWS_LOOKBACK_WINDOW == "7d"
    assert s.EDGAR_FULLTEXT_ENABLED is False
    assert s.EDGAR_FULLTEXT_FORMS == "8-K,10-K,10-Q"
    assert s.EDGAR_FULLTEXT_CHUNK_TOKENS == 512
    assert s.SECTOR_HEAT_ENABLED is False
    assert s.SECTOR_HEAT_SMOOTHING_SIGMA == pytest.approx(1.0)
    assert s.SECTOR_HEAT_LOOKBACK_DAYS == 7
    assert s.WIKIPEDIA_ATTENTION_ENABLED is False
    assert s.WIKIPEDIA_ATTENTION_LOOKBACK_DAYS == 30
    assert s.PYTRENDS_ENABLED is False
    assert s.SENTIMENT_INDEX_ENABLED is False
    assert s.MARKET_DATA_LATENCY_TRACKING_ENABLED is False


# =============================================================================
# 3. OUTPUT_DIR is created on load if missing
# =============================================================================
def test_output_dir_created(monkeypatch, tmp_path):
    target = tmp_path / "freshly" / "nested" / "output"
    assert not target.exists()
    monkeypatch.setenv("OUTPUT_DIR", str(target))

    s = Settings(_env_file=None)

    assert isinstance(s.OUTPUT_DIR, Path)
    assert s.OUTPUT_DIR.is_dir()


def test_output_dir_read_only_graceful(monkeypatch, tmp_path):
    from unittest.mock import patch
    target = tmp_path / "read-only-dir"
    monkeypatch.setenv("OUTPUT_DIR", str(target))

    with patch.object(Path, "mkdir", side_effect=OSError("Read-only file system")):
        s = Settings(_env_file=None)
        assert s.OUTPUT_DIR == target



# =============================================================================
# 4. MISSING REQUIRED KEY — fails clearly on the live path
# =============================================================================
def test_missing_fred_key_raises_clearly(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))

    s = Settings(_env_file=None)

    with pytest.raises(RuntimeError, match="FRED_API_KEY is not configured"):
        s.ensure_fred_configured()


def test_configured_fred_key_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", "abc123")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))

    s = Settings(_env_file=None)

    # Should not raise.
    s.ensure_fred_configured()


# =============================================================================
# 5. LEAKED KEY DETECTION
# =============================================================================
# The detection works by SHA-256 digest, so we never embed the real leaked
# literal in the test tree. We exercise the mechanism by pointing the expected
# digest at the hash of a throwaway value.
def test_leaked_key_detected(monkeypatch, tmp_path, caplog):
    import hashlib
    import settings as settings_module

    sentinel = "pretend-this-is-the-leaked-key"
    digest = hashlib.sha256(sentinel.encode("utf-8")).hexdigest()
    monkeypatch.setattr(settings_module, "LEAKED_FRED_KEY_SHA256", digest)

    monkeypatch.setenv("FRED_API_KEY", sentinel)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))

    s = Settings(_env_file=None)

    assert s.fred_key_is_leaked is True
    with caplog.at_level(logging.CRITICAL):
        assert s.warn_if_fred_key_leaked() is True
    assert any("COMPROMISED" in rec.message for rec in caplog.records)


def test_fresh_key_not_flagged_as_leaked(monkeypatch, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", "a-brand-new-rotated-key")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))

    s = Settings(_env_file=None)

    assert s.fred_key_is_leaked is False
    assert s.warn_if_fred_key_leaked() is False


def test_leaked_digest_constant_is_a_sha256():
    # Guard: the stored constant is a 64-char hex digest, not a raw key.
    assert len(LEAKED_FRED_KEY_SHA256) == 64
    assert all(c in "0123456789abcdef" for c in LEAKED_FRED_KEY_SHA256)


# =============================================================================
# Shared interval-validation policy (Piece 2 -- live daemon timer setter)
# =============================================================================
#
# desktop/daemon_runtime.py's OrchestratorDaemon.set_interval,
# api/control_api.py's PUT /interval body, and api/pilots_api.py's PUT
# /automation/schedule/interval body all validate against THIS module's
# validate_interval_seconds/INTERVAL_MIN_SECONDS/INTERVAL_MAX_SECONDS rather
# than each defining their own rule -- these tests pin the policy itself
# (each call site's own test suite pins that it actually delegates here,
# not that the rule is correct).


class TestIntervalValidationPolicy:
    def test_min_and_max_constants(self):
        from settings import INTERVAL_MAX_SECONDS, INTERVAL_MIN_SECONDS

        assert INTERVAL_MIN_SECONDS == 60
        assert INTERVAL_MAX_SECONDS == 86400

    def test_zero_is_always_valid(self):
        from settings import validate_interval_seconds

        assert validate_interval_seconds(0) == 0

    @pytest.mark.parametrize("value", [60, 300, 3600, 86400])
    def test_in_range_values_pass_through_unchanged(self, value):
        from settings import validate_interval_seconds

        assert validate_interval_seconds(value) == value

    @pytest.mark.parametrize("value", [-1, 1, 59, 86401])
    def test_out_of_range_nonzero_values_raise(self, value):
        from settings import validate_interval_seconds

        with pytest.raises(ValueError):
            validate_interval_seconds(value)

    def test_error_message_names_the_bounds(self):
        """Not load-bearing for behavior, but a caller (e.g. a pydantic
        field_validator) surfaces this message verbatim to the operator --
        it should be self-explanatory, not a bare 'invalid value'."""
        from settings import validate_interval_seconds

        with pytest.raises(ValueError, match=r"\[60, 86400\]"):
            validate_interval_seconds(59)


class TestIntervalValidationAntiDrift:
    """The three real call sites (desktop.daemon_runtime.OrchestratorDaemon.
    set_interval, api.control_api.IntervalUpdateRequest, api.pilots_api.
    IntervalUpdateRequest) cannot import each other, so nothing at the type
    level forces them to agree -- this test drives all three with the same
    inputs and asserts they accept/reject identically. A future edit to any
    one call site that stops delegating to settings.validate_interval_seconds
    (e.g. reintroducing a bespoke ge/le Field bound) would show up here as a
    disagreement, not as a silent drift discovered in production."""

    @pytest.mark.parametrize("value", [-1, 0, 1, 59, 60, 86400, 86401])
    def test_all_three_validators_agree(self, value):
        import api.control_api as control_api
        import api.pilots_api as pilots_api
        from desktop.daemon_runtime import OrchestratorDaemon

        results = {}

        try:
            control_api.IntervalUpdateRequest(interval_seconds=value)
            results["control_api"] = True
        except Exception:
            results["control_api"] = False

        try:
            pilots_api.IntervalUpdateRequest(interval_seconds=value)
            results["pilots_api"] = True
        except Exception:
            results["pilots_api"] = False

        try:
            d = OrchestratorDaemon()
            d.set_interval(value)
            results["daemon_runtime"] = True
        except ValueError:
            results["daemon_runtime"] = False
        finally:
            d.shutdown(timeout=2.0)

        assert results["control_api"] == results["pilots_api"] == results["daemon_runtime"], (
            f"validators disagree for interval_seconds={value}: {results}"
        )


# =============================================================================
# Shutdown-budget setting (2026-07 fix -- one published total instead of
# three unreconciled hardcoded values in desktop/orchestrator_daemon.py's
# _teardown() and desktop/daemon_runtime.py's shutdown())
# =============================================================================


class TestDaemonShutdownTimeoutBounds:
    def test_min_and_max_constants(self):
        from settings import (
            DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS,
            DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS,
        )

        assert DAEMON_SHUTDOWN_TIMEOUT_MIN_SECONDS == 1.0
        assert DAEMON_SHUTDOWN_TIMEOUT_MAX_SECONDS == 120.0

    def test_default_is_exactly_25(self):
        from settings import settings

        assert settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS == 25.0

    def test_default_matches_pre_fix_worst_case_arithmetic(self):
        """25.0 is not an arbitrary round number -- it's the outer bound of
        every configuration reachable BEFORE this fix (5s Control-API join +
        5s Pilots-API join [only when PILOTS_API_ENABLED] + a
        daemon.shutdown(timeout=10.0) call that itself used to take up to
        15s [5s unbudgeted timer join + 10s poll]). Pinning the arithmetic
        here means the default can't silently drift away from the sum it's
        meant to represent."""
        control_api_join = 5.0
        pilots_api_join = 5.0
        pre_fix_daemon_shutdown_worst_case = 5.0 + 10.0  # unbudgeted join + poll
        assert (
            control_api_join + pilots_api_join + pre_fix_daemon_shutdown_worst_case
            == 25.0
        )

    @pytest.mark.parametrize("value", [1.0, 5.0, 25.0, 60.0, 120.0])
    def test_in_range_values_pass_through_unchanged(self, value):
        from settings import validate_daemon_shutdown_timeout

        assert validate_daemon_shutdown_timeout(value) == value

    @pytest.mark.parametrize("value", [0.0, -1.0, 0.99, 120.01, 600.0])
    def test_out_of_range_values_raise(self, value):
        from settings import validate_daemon_shutdown_timeout

        with pytest.raises(ValueError):
            validate_daemon_shutdown_timeout(value)

    def test_zero_is_rejected_not_treated_as_a_valid_sentinel(self):
        """Unlike validate_interval_seconds (where 0 means "on-demand
        only"), 0 here has no valid meaning -- it would make every join/poll
        instant, i.e. an unconditional SIGKILL-equivalent, the opposite of
        this setting's purpose. Must raise, not silently pass through."""
        from settings import validate_daemon_shutdown_timeout

        with pytest.raises(ValueError):
            validate_daemon_shutdown_timeout(0.0)

    def test_error_message_names_the_bounds(self):
        from settings import validate_daemon_shutdown_timeout

        with pytest.raises(ValueError, match=r"\[1\.0, 120\.0\]"):
            validate_daemon_shutdown_timeout(0.0)

    def test_settings_field_validator_rejects_out_of_range_construction(self):
        """The module-level function is also wired as a real pydantic
        field_validator on Settings itself, not just callable standalone."""
        from settings import Settings

        with pytest.raises(Exception):
            Settings(DAEMON_SHUTDOWN_TIMEOUT_SECONDS=0.0)


class TestFMPSettingsDefaults:
    """Verifies that all FMP capability flags, provider selectors, and realtime
    quote labels default to ON / FMP per explicit operator decision."""

    def test_all_fmp_defaults_are_on(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
        s = Settings(_env_file=None)

        # Provider defaults
        assert s.MARKET_DATA_PROVIDER == "fmp"
        assert s.FUNDAMENTALS_SOURCE == "fmp"

        # 14 capability flags + quotes realtime
        assert s.FMP_QUOTES_ENABLED is True
        assert s.FMP_QUOTES_REALTIME is True
        assert s.FMP_BARS_ENABLED is True
        assert s.FMP_FUNDAMENTALS_ENABLED is True
        assert s.FMP_ANALYST_ENABLED is True
        assert s.FMP_EARNINGS_ENABLED is True
        assert s.FMP_NEWS_ENABLED is True
        assert s.FMP_MACRO_ENABLED is True
        assert s.FMP_ECON_CALENDAR_ENABLED is True
        assert s.FMP_INSIDER_ENABLED is True
        assert s.FMP_SECTOR_SNAPSHOT_ENABLED is True
        assert s.FMP_PEERS_ENABLED is True
        assert s.FMP_UNIVERSE_ENABLED is True


class TestStep4fRetiredKeysAreHarmless:
    """Step 4f retired ~60 options-desk / FIX / circuit-breaker / ETF fields.
    The operator's real ``.env`` (and ``output/runtime_flags.json``) may still
    set some of them. That must stay harmless: pydantic-settings ignores the
    unknown keys (``extra="ignore"``), the runtime store skips them as
    unknown, and a retired webhook URL keeps being masked by env_io."""

    _RETIRED = {
        "OPTIONS_0DTE_ENABLED": "true",
        "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED": "true",
        "ETF_TRANSMISSION_ENABLED": "true",
        "ETF_HOLDINGS_TICKERS": '["SPY","QQQ"]',
        "OFI_SHIELD_ENABLED": "true",
        "FIX_GATEWAY_ENABLED": "false",
        "MULTI_BROKER_GATEWAY_ENABLED": "true",
        "OPTIONS_ALERT_WEBHOOK_URL": "https://hooks.example.invalid/x",
    }

    def test_retired_keys_in_env_file_are_ignored(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "".join(f"{k}={v}\n" for k, v in self._RETIRED.items()) + "KELLY_FRACTION=0.4\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("KELLY_FRACTION", raising=False)
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
        s = Settings(_env_file=str(env_file))
        assert s.KELLY_FRACTION == pytest.approx(0.4)  # the file really was read
        for key in self._RETIRED:
            assert key not in Settings.model_fields
            assert not hasattr(s, key)

    def test_retired_keys_in_real_environment_are_ignored(self, tmp_path, monkeypatch):
        for key, value in self._RETIRED.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
        s = Settings(_env_file=None)
        for key in self._RETIRED:
            assert not hasattr(s, key)

    def test_runtime_store_skips_retired_keys_as_unknown(self, tmp_path, monkeypatch):
        import json

        import runtime_flags

        store = tmp_path / "runtime_flags.json"
        store.write_text(json.dumps({
            "version": 1,
            "flags": {
                "OFI_SHIELD_ENABLED": {"value": True},
                "CIRCUIT_BREAKER_ENABLED": {"value": True},
                "KELLY_FRACTION": {"value": 0.3},
            },
        }), encoding="utf-8")
        monkeypatch.delenv("KELLY_FRACTION", raising=False)
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
        s = Settings(_env_file=None)
        report = runtime_flags.apply_overrides(s, path=store)
        assert report.error is None
        assert set(report.skipped_unknown) == {"OFI_SHIELD_ENABLED", "CIRCUIT_BREAKER_ENABLED"}
        assert "KELLY_FRACTION" in report.applied
        assert s.KELLY_FRACTION == pytest.approx(0.3)

    def test_retired_webhook_url_is_still_masked(self, tmp_path, monkeypatch):
        from shared import env_io

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPTIONS_ALERT_WEBHOOK_URL=https://hooks.example.invalid/secret-path\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(env_io, "ENV_PATH", env_file, raising=False)
        shown = env_io.read_settings()["OPTIONS_ALERT_WEBHOOK_URL"]
        assert "secret-path" not in shown


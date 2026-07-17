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
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.setenv("DEFAULT_TICKERS", '["NVDA", "TSLA"]')
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("STATE_API_TOKEN", "tok-123")
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", '["https://a.com", "http://localhost:3000"]'
    )

    s = Settings(_env_file=None)

    assert s.FRED_API_KEY == "live-key-123"
    assert s.RISK_FREE_RATE == 0.05
    assert s.ALPACA_PAPER is False
    assert s.DEFAULT_TICKERS == ["NVDA", "TSLA"]
    assert s.OUTPUT_DIR == (tmp_path / "reports")
    assert s.STATE_API_TOKEN == "tok-123"
    assert s.CORS_ALLOWED_ORIGINS == ["https://a.com", "http://localhost:3000"]


# =============================================================================
# 2. DEFAULTS — unset fields fall back to documented defaults
# =============================================================================
def test_settings_defaults(monkeypatch, tmp_path):
    # Ensure nothing leaks in from the host environment.
    for key in (
        "FRED_API_KEY",
        "RISK_FREE_RATE",
        "ALPACA_PAPER",
        "DEFAULT_TICKERS",
        "STATE_API_TOKEN",
        "CORS_ALLOWED_ORIGINS",
        "DRY_RUN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))

    s = Settings(_env_file=None)

    assert s.FRED_API_KEY == ""
    assert s.ALPACA_API_KEY is None
    assert s.ALPACA_PAPER is True
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
# 3. OUTPUT_DIR is created on load if missing
# =============================================================================
def test_output_dir_created(monkeypatch, tmp_path):
    target = tmp_path / "freshly" / "nested" / "output"
    assert not target.exists()
    monkeypatch.setenv("OUTPUT_DIR", str(target))

    s = Settings(_env_file=None)

    assert isinstance(s.OUTPUT_DIR, Path)
    assert s.OUTPUT_DIR.is_dir()


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

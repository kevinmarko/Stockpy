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

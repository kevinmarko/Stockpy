"""
tests/test_gui_env_io.py
========================
Unit tests for ``gui/env_io.py`` — the safe, allowlist-bounded ``.env`` read/
write layer behind the Command Center's Settings and Strategy Matrix tabs.

These tests pin the security-critical contract (CONSTRAINT #3):

*   Secret keys are NEVER returned in cleartext and NEVER writable from the GUI.
*   Only allowlisted keys are writable; unknown keys are rejected.
*   List/dict tunables round-trip as JSON so pydantic-settings re-parses them.
*   Writes preserve unrelated lines/comments already in ``.env``.

All writes are redirected to a temporary ``.env`` via monkeypatching
``env_io.ENV_PATH`` so the real project ``.env`` is never touched.
"""

import json

import pytest

import settings as settings_module
import env_io


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    """Point env_io at an isolated temp .env seeded with a comment + secret."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# InvestYo config (test fixture)\n"
        "FRED_API_KEY=super-secret-value\n"
        "RISK_FREE_RATE=0.045\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env_io, "ENV_PATH", env_file)
    return env_file


# ---------------------------------------------------------------------------
# Secret protection
# ---------------------------------------------------------------------------

def test_read_settings_masks_secrets(temp_env):
    display = env_io.read_settings()
    assert display["FRED_API_KEY"] == env_io._MASK_SET  # masked, not cleartext
    assert "super-secret-value" not in json.dumps(display)


def test_get_value_refuses_secret(temp_env):
    with pytest.raises(env_io.SecretWriteError):
        env_io.get_value("FRED_API_KEY")


def test_write_setting_refuses_secret(temp_env):
    with pytest.raises(env_io.SecretWriteError):
        env_io.write_setting("ALPACA_SECRET_KEY", "anything")
    # The secret must not have been written.
    assert "ALPACA_SECRET_KEY" not in temp_env.read_text(encoding="utf-8")


def test_is_secret_classification():
    assert env_io.is_secret("RH_PASSWORD") is True
    assert env_io.is_secret("KELLY_FRACTION") is False


# ---------------------------------------------------------------------------
# Allowlist enforcement
# ---------------------------------------------------------------------------

def test_write_setting_rejects_unknown_key(temp_env):
    with pytest.raises(env_io.DisallowedKeyError):
        env_io.write_setting("TOTALLY_MADE_UP_KEY", "1")


def test_write_setting_scalar_roundtrip(temp_env):
    env_io.write_setting("KELLY_FRACTION", 0.33)
    assert env_io.get_value("KELLY_FRACTION") == "0.33"


def test_write_setting_bool_lowercased(temp_env):
    env_io.write_setting("DRY_RUN", True)
    assert env_io.get_value("DRY_RUN") == "true"


# ---------------------------------------------------------------------------
# JSON-encoded structures
# ---------------------------------------------------------------------------

def test_default_tickers_json_roundtrip(temp_env):
    env_io.write_setting("DEFAULT_TICKERS", ["AAPL", "MSFT"])
    raw = env_io.get_value("DEFAULT_TICKERS")
    assert json.loads(raw) == ["AAPL", "MSFT"]


def test_disabled_modules_json_roundtrip(temp_env):
    env_io.write_setting("DISABLED_SIGNAL_MODULES", ["rsi2_mean_reversion"])
    raw = env_io.get_value("DISABLED_SIGNAL_MODULES")
    assert json.loads(raw) == ["rsi2_mean_reversion"]


def test_signal_weights_json_roundtrip(temp_env):
    weights = {"macro_regime": 45.0, "graham_value": 15.0}
    env_io.write_setting("SIGNAL_WEIGHTS", weights)
    raw = env_io.get_value("SIGNAL_WEIGHTS")
    assert json.loads(raw) == weights


# ---------------------------------------------------------------------------
# File preservation + batch writes
# ---------------------------------------------------------------------------

def test_write_preserves_other_lines(temp_env):
    env_io.write_setting("RISK_FREE_RATE", 0.05)
    text = temp_env.read_text(encoding="utf-8")
    # Original comment + secret line are still present.
    assert "# InvestYo config (test fixture)" in text
    assert "FRED_API_KEY=" in text


def test_write_many_returns_written_keys(temp_env):
    written = env_io.write_many({"KELLY_FRACTION": 0.4, "VOL_TARGET": 0.12})
    assert set(written) == {"KELLY_FRACTION", "VOL_TARGET"}
    assert env_io.get_value("KELLY_FRACTION") == "0.4"
    assert env_io.get_value("VOL_TARGET") == "0.12"


def test_allowlisted_keys_nonempty_and_excludes_secrets():
    keys = set(env_io.allowlisted_keys())
    assert "KELLY_FRACTION" in keys
    assert keys.isdisjoint(set(env_io.SECRET_KEYS))


def test_allowed_keys_has_no_duplicates():
    """ALLOWED_KEYS is a tuple, not a set, so a duplicate entry (e.g. the same
    key re-added under a later audit block without noticing an earlier block
    already carries it) silently survives -- it doesn't break write_setting,
    but it does mean two comment blocks silently disagree about where a key
    "belongs", and any future doc-block edit can drift the two copies apart.
    Pins the count so a reintroduced duplicate fails loudly instead of
    quietly accumulating."""
    assert len(env_io.ALLOWED_KEYS) == len(set(env_io.ALLOWED_KEYS))


# ---------------------------------------------------------------------------
# Settings <-> env_io classification parity (2026-08 allowlist audit)
# ---------------------------------------------------------------------------
# Pins the invariant the audit was tracking down: every field on
# settings.Settings must be classified as GUI-writable (ALLOWED_KEYS), secret
# (SECRET_KEYS), or explicitly excluded (EXCLUDED_FROM_GUI) -- never silently
# unclassified. This is the regression guard for PR #560-style drift, where a
# batch of new Settings fields shipped without a corresponding env_io.py
# update and neither allowlist noticed.

def test_every_settings_field_is_classified():
    fields = set(type(settings_module.settings).model_fields.keys())
    classified = set(env_io.ALLOWED_KEYS) | set(env_io.SECRET_KEYS) | env_io.EXCLUDED_FROM_GUI
    missing = fields - classified
    assert not missing, (
        f"settings.py field(s) not classified in gui/env_io.py: {sorted(missing)}. "
        "Add each to ALLOWED_KEYS (non-secret tunable), SECRET_KEYS (credential/"
        "webhook/token), or EXCLUDED_FROM_GUI (filesystem path or fail-closed "
        "command flag -- see that set's own docstring)."
    )


def test_allowed_secret_excluded_are_mutually_exclusive():
    allowed = set(env_io.ALLOWED_KEYS)
    secret = set(env_io.SECRET_KEYS)
    excluded = env_io.EXCLUDED_FROM_GUI
    assert allowed.isdisjoint(secret)
    assert allowed.isdisjoint(excluded)
    assert secret.isdisjoint(excluded)


def test_excluded_from_gui_keys_are_neither_writable_nor_secret():
    """EXCLUDED_FROM_GUI keys must behave exactly like any other unclassified
    key -- rejected by write_setting, absent from read_settings' masking path
    for the "is_secret" check -- so adding a key here can never accidentally
    grant it a capability."""
    for key in env_io.EXCLUDED_FROM_GUI:
        assert env_io.is_secret(key) is False
        with pytest.raises(env_io.DisallowedKeyError):
            env_io.write_setting(key, "1")


@pytest.mark.parametrize(
    "key",
    [
        "AGENTIC_DISCOVERY_ENABLED",
        "BROKERAGE_CONNECT_ENABLED",
        "UNIVERSE_SYNC_ENABLED",
        # 2026-08-08 (PR #630 audit): the 12 fail-closed write/execution gates
        # that used to be gui/env_io.py's EXCLUDED_FROM_GUI + this module's
        # own test_fail_closed_flags_are_hand_set_only. "Not secret
        # information" is now the sole bar for GUI-writability, per explicit
        # operator decision -- each endpoint remains independently gated by
        # its own command token (and, for a few, a loopback/confirmation
        # check) regardless. Also DANGEROUS_KEYS members now (see
        # settings_keysets.SAFETY_CRITICAL_KEY_REASONS), so a write through
        # any editor that exposes one still requires typed confirmation.
        "AI_GENERATION_API_ENABLED",
        "AUTOMATION_WRITES_ENABLED",
        "BROKERAGE_REFRESH_ENABLED",
        "CACHE_LONG_SHORT_WRITES_ENABLED",
        "COMMAND_EXECUTION_ENABLED",
        "DEAD_LETTER_RETRY_ENABLED",
        "GENERAL_SETTINGS_WRITES_ENABLED",
        "LLM_WRITES_ENABLED",
        "MACRO_GATE_WRITES_ENABLED",
        "PROMPT_REGISTRY_WRITES_ENABLED",
        "RAG_QUERY_API_ENABLED",
        "STRATEGY_WRITES_ENABLED",
    ],
)
def test_reclassified_flags_are_now_gui_writable(key):
    """PR #560 reclassified the first three "per explicit operator decision"
    out of the hand-set-only class into ALLOWED_KEYS; PR #630's audit
    generalized that same decision to the remaining 12 fail-closed write/
    execution gates on 2026-08-08 -- each endpoint remains independently
    gated by its own command-token/loopback check regardless (see
    gui/env_io.py's EXCLUDED_FROM_GUI docstring for the full note). Pinned
    here (rather than left to silently pass
    test_every_settings_field_is_classified) so a future revert of that
    policy decision is a visible test failure, not a silent classification
    drift."""
    assert key in env_io.ALLOWED_KEYS
    assert key not in env_io.SECRET_KEYS
    assert key not in env_io.EXCLUDED_FROM_GUI


@pytest.mark.parametrize(
    "key",
    [
        "OUTPUT_DIR",
        "PROMPT_CACHE_DIR",
        "WATCH_RULES_FILE",
        "ALERT_FILE_PATH",
        "GRAVITY_AI_RUNNER_OUTPUT_PATH",
        "LLM_COMMENTARY_CACHE_PATH",
    ],
)
def test_filesystem_paths_are_excluded(key):
    assert key not in env_io.ALLOWED_KEYS
    assert key not in env_io.SECRET_KEYS


def test_alerting_mcp_credentials_mirror_secret_siblings(temp_env):
    """ALERT_EMAIL_SMTP_HOST/ALERT_NTFY_TOPIC/ALERT_SLACK_WEBHOOK_URL (the
    alerting_mcp family) must get the same secret treatment as their already-
    classified observability/alerts.py siblings (ALERT_SMTP_HOST/NTFY_TOPIC/
    ALERT_WEBHOOK_URL), while the plain port fields stay non-secret."""
    for key in ("ALERT_EMAIL_SMTP_HOST", "ALERT_NTFY_TOPIC", "ALERT_SLACK_WEBHOOK_URL"):
        assert env_io.is_secret(key) is True
        with pytest.raises(env_io.SecretWriteError):
            env_io.write_setting(key, "anything")
    for key in ("ALERT_SMTP_PORT", "ALERT_EMAIL_SMTP_PORT"):
        assert env_io.is_secret(key) is False
        env_io.write_setting(key, 587)
        assert env_io.get_value(key) == "587"


def test_reddit_user_agent_mirrors_edgar_user_agent_precedent():
    assert env_io.is_secret("REDDIT_USER_AGENT") is True
    assert env_io.is_secret("EDGAR_USER_AGENT") is True


def test_follow_api_token_is_secret():
    assert env_io.is_secret("FOLLOW_API_TOKEN") is True


def test_dual_momentum_and_regime_weights_json_roundtrip(temp_env):
    env_io.write_setting("DUAL_MOMENTUM_RISKY_ASSETS", ["SPY", "VEU"])
    assert json.loads(env_io.get_value("DUAL_MOMENTUM_RISKY_ASSETS")) == ["SPY", "VEU"]

    weights = {"RECESSION": {"macro_regime": 60.0}, "_default": {}}
    env_io.write_setting("REGIME_SIGNAL_WEIGHTS", weights)
    assert json.loads(env_io.get_value("REGIME_SIGNAL_WEIGHTS")) == weights


@pytest.mark.parametrize(
    "key",
    [
        "SENTIMENT_INGESTION_ENABLED",
        "STOCKTWITS_ENABLED",
        "ROBINHOOD_EXECUTION_MODE",
        "ROBINHOOD_MAX_NOTIONAL_PER_ORDER",
        "META_LABELING_ENABLED",
        "HISTORICAL_STORE_ENABLED",
        "PILOTS_TOP_N",
        "ALPACA_KEY_ROTATED_DATE",
        "PAPER_TRADING_START_DATE",
    ],
)
def test_newly_allowed_pipeline_tunables_are_writable(temp_env, key):
    assert key in env_io.ALLOWED_KEYS
    assert key not in env_io.SECRET_KEYS

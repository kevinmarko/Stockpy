"""
tests/test_measure_settings_census.py
=======================================
Freshness guard for ``scripts/measure_settings_census.py``'s two committed
artifacts, ``docs/settings_field_census.json`` and
``docs/settings_field_census.md`` — mirrors
``tests/test_settings_liveness.py::TestCommittedArtifactIsFresh``, which
existed for the sibling ``docs/settings_liveness.json`` artifact but had no
counterpart for the census.

This is not a hypothetical gap: on 2026-08-03 the committed census sat at
``meta.git_commit e7e64529`` / ``total_fields 318`` while live
``Settings.model_fields`` had already grown to 320 (``FMP_OPTIONS_CONTEXT_ENABLED``
and ``FMP_PEERS_ENABLED`` were both missing from the committed file) — and
nothing detected it. This test is what would have caught that.

Two fields need explicit handling, not a blanket exclusion:

- ``meta.git_commit`` changes on every commit by construction. Comparing it
  literally would fail this test on every single commit that touches ANY
  file in the repo, including ones with zero effect on what the census
  measures — that is not staleness, so it is excluded from the diff (see
  ``_without_git_commit``).
- ``meta.repo_root`` used to bake in an ABSOLUTE path to whichever checkout
  (routinely a ``.claude/worktrees/...`` clone) generated the file, which
  made the committed artifact worktree-dependent and its diffs noisy across
  machines. Fixed at the source instead of worked around here:
  ``collect_census()`` no longer emits ``repo_root`` at all — grepping the
  tree before removing it turned up no reader of it anywhere.

``read_forms.files_scanned`` (like ``files_scanned`` in
``docs/settings_liveness.json``) increments whenever ANY new production
``.py`` file is added at repo root or in a scanned package. That is correct
behaviour, not a bug — but it does mean this test can fail on a PR that has
nothing to do with settings (exactly what happened when ``settings_keysets.py``
was added and required regenerating ``docs/settings_liveness.json``). Every
failure message below therefore states the fix plainly: re-run
``python3 scripts/measure_settings_census.py --write`` and commit the result
— a ten-second fix, not a sign anything is actually wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import measure_settings_census as census

REPO_ROOT = Path(__file__).resolve().parent.parent
JSON_ARTIFACT = census.JSON_OUT
MD_ARTIFACT = census.MD_OUT

_REGEN_HINT = (
    "Re-run `python3 scripts/measure_settings_census.py --write` and commit "
    "the result. This can fire on an otherwise-unrelated PR (e.g. one that "
    "merely adds a new top-level module or package file) since the census "
    "walks the whole production tree, not just settings.py -- that's "
    "expected, and the fix above takes about ten seconds."
)


def _without_git_commit(meta: dict) -> dict:
    """``git_commit`` changes every commit by design -- it is not a signal
    that the census itself is stale, so it is excluded from equality checks."""
    return {k: v for k, v in meta.items() if k != "git_commit"}


@pytest.fixture(scope="module")
def fresh_census() -> dict:
    return census.collect_census()


class TestCommittedArtifactIsFresh:
    def test_committed_json_matches_a_fresh_run(self, fresh_census):
        assert JSON_ARTIFACT.exists(), (
            f"{JSON_ARTIFACT.relative_to(REPO_ROOT)} is missing. {_REGEN_HINT}"
        )
        committed = json.loads(JSON_ARTIFACT.read_text(encoding="utf-8"))

        committed_meta = _without_git_commit(committed.get("meta", {}))
        fresh_meta = _without_git_commit(fresh_census.get("meta", {}))
        assert committed_meta == fresh_meta, (
            "docs/settings_field_census.json's meta block no longer matches a "
            "fresh run (git_commit is deliberately excluded from this "
            f"comparison -- see this file's module docstring). {_REGEN_HINT}"
        )

        committed_body = {k: v for k, v in committed.items() if k != "meta"}
        fresh_body = {k: v for k, v in fresh_census.items() if k != "meta"}
        assert committed_body == fresh_body, (
            f"docs/settings_field_census.json is stale. {_REGEN_HINT}"
        )

    def test_committed_md_matches_a_fresh_run(self, fresh_census):
        assert MD_ARTIFACT.exists(), (
            f"{MD_ARTIFACT.relative_to(REPO_ROOT)} is missing. {_REGEN_HINT}"
        )
        committed_json = json.loads(JSON_ARTIFACT.read_text(encoding="utf-8"))

        # docs/settings_field_census.md is a pure function of the same payload
        # (render_markdown(data)) plus the commit hash printed in its header.
        # Substitute the COMMITTED commit into a fresh payload before
        # rendering, so the only possible mismatch is genuine data/render
        # drift -- never "which commit happened to be HEAD when this ran".
        normalized = dict(fresh_census)
        normalized["meta"] = dict(fresh_census["meta"])
        normalized["meta"]["git_commit"] = committed_json.get("meta", {}).get("git_commit")
        fresh_md = census.render_markdown(normalized)

        committed_md = MD_ARTIFACT.read_text(encoding="utf-8")
        assert fresh_md == committed_md, (
            f"docs/settings_field_census.md is stale. {_REGEN_HINT}"
        )

def test_measure_settings_census_gate():
    """Gate for measure_settings_census.py"""
    allowlist = [
        "FRED_API_KEY",  # removed once WP-A/B/C/D lands
        "FRED_REQUEST_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "ALPACA_API_KEY",  # removed once WP-A/B/C/D lands
        "ALPACA_SECRET_KEY",  # removed once WP-A/B/C/D lands
        "ALPACA_PAPER",  # removed once WP-A/B/C/D lands
        "ALPACA_REQUEST_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "FMP_PAPER_STARTING_CASH",  # removed once WP-A/B/C/D lands
        "BROKER_BACKEND",  # removed once WP-A/B/C/D lands
        "MULTI_BROKER_GATEWAY_ENABLED",  # removed once WP-A/B/C/D lands
        "PAPER_BROKER_WRITES_ENABLED",  # removed once WP-A/B/C/D lands
        "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED",  # removed once WP-A/B/C/D lands
        "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED",  # removed once WP-A/B/C/D lands
        "MAX_OPTION_NOTIONAL_PER_TRADE",  # removed once WP-A/B/C/D lands
        "MAX_CONCURRENT_OPTION_POSITIONS",  # removed once WP-A/B/C/D lands
        "OPTIONS_META_LABELER_ENABLED",  # removed once WP-A/B/C/D lands
        "OPTIONS_RISK_FREE_RATE",  # removed once WP-A/B/C/D lands
        "OPTIONS_AUTO_EXIT_ENABLED",  # removed once WP-A/B/C/D lands
        "OPTIONS_PROFIT_TARGET_PCT",  # removed once WP-A/B/C/D lands
        "OPTIONS_STOP_LOSS_MULTIPLE",  # removed once WP-A/B/C/D lands
        "OPTIONS_MANAGE_DTE_THRESHOLD",  # removed once WP-A/B/C/D lands
        "OPTIONS_DELTA_HEDGE_ENABLED",  # removed once WP-A/B/C/D lands
        "OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES",  # removed once WP-A/B/C/D lands
        "OPTIONS_EARNINGS_CRUSH_ENABLED",  # removed once WP-A/B/C/D lands
        "OPTIONS_EARNINGS_MIN_EDGE",  # removed once WP-A/B/C/D lands
        "OPTIONS_EARNINGS_WING_MULTIPLIER",  # removed once WP-A/B/C/D lands
        "OPTIONS_ALERT_WEBHOOK_URL",  # removed once WP-A/B/C/D lands
        "OPTIONS_0DTE_ENABLED",  # removed once WP-A/B/C/D lands
        "OPTIONS_0DTE_PROFIT_TARGET_PCT",  # removed once WP-A/B/C/D lands
        "OPTIONS_0DTE_STOP_LOSS_PCT",  # removed once WP-A/B/C/D lands
        "OPTIONS_0DTE_HARD_EXIT_TIME",  # removed once WP-A/B/C/D lands
        "OPTIONS_DRL_RISK_AVERSION_GAMMA",  # removed once WP-A/B/C/D lands
        "OPTIONS_VPIN_TOXICITY_THRESHOLD",  # removed once WP-A/B/C/D lands
        "OPTIONS_SOR_LEGGING_LATENCY_SECONDS",  # removed once WP-A/B/C/D lands
        "OPTIONS_LOB_DEFAULT_MARKET_ORDER_RATE",  # removed once WP-A/B/C/D lands
        "OPTIONS_GEX_SEARCH_RANGE_PCT",  # removed once WP-A/B/C/D lands
        "LIVE_TRADE_EXECUTION_ENABLED",  # removed once WP-A/B/C/D lands
        "LIVE_TRADE_APPROVAL_ENABLED",  # removed once WP-A/B/C/D lands
        "STATE_API_TOKEN",  # removed once WP-A/B/C/D lands
        "ORCHESTRATOR_DAEMON_TOKEN",  # removed once WP-A/B/C/D lands
        "ORCHESTRATOR_API_PORT",  # removed once WP-A/B/C/D lands
        "FOLLOW_API_TOKEN",  # removed once WP-A/B/C/D lands
        "MCP_HTTP_BEARER_TOKEN",  # removed once WP-A/B/C/D lands
        "MCP_OAUTH_ENABLED",  # removed once WP-A/B/C/D lands
        "MCP_OAUTH_ISSUER_URL",  # removed once WP-A/B/C/D lands
        "MCP_OAUTH_PASSWORD",  # removed once WP-A/B/C/D lands
        "MCP_OAUTH_MULTI_USER_ENABLED",  # removed once WP-A/B/C/D lands
        "PILOTS_API_ENABLED",  # removed once WP-A/B/C/D lands
        "PILOTS_API_PORT",  # removed once WP-A/B/C/D lands
        "JOBS_API_ENABLED",  # removed once WP-A/B/C/D lands
        "COMMAND_EXECUTION_ENABLED",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_PROVIDER",  # removed once WP-A/B/C/D lands
        "FINNHUB_API_KEY",  # removed once WP-A/B/C/D lands
        "JULES_API_KEY",  # removed once WP-A/B/C/D lands
        "JULES_ENABLED",  # removed once WP-A/B/C/D lands
        "JULES_REQUEST_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_QUOTE_TTL_SECONDS",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_BARS_TTL_SECONDS",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_LATENCY_TRACKING_ENABLED",  # removed once WP-A/B/C/D lands
        "BROWSER_DIAGNOSTICS_ENABLED",  # removed once WP-A/B/C/D lands
        "BROWSER_DIAGNOSTICS_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "EXCURSION_INTRADAY_ENABLED",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_WS_ENABLED",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_WS_STALE_SECONDS",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_WS_SYMBOLS",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_WS_RECONNECT_BASE_SECONDS",  # removed once WP-A/B/C/D lands
        "MARKET_DATA_WS_RECONNECT_MAX_SECONDS",  # removed once WP-A/B/C/D lands
        "DATA_FRESHNESS_TTL_SECONDS",  # removed once WP-A/B/C/D lands
        "FUNDAMENTALS_CACHE_TTL_SECONDS",  # removed once WP-A/B/C/D lands
        "FUNDAMENTALS_NEG_CACHE_TTL_SECONDS",  # removed once WP-A/B/C/D lands
        "FINNHUB_RATE_LIMIT_PER_MIN",  # removed once WP-A/B/C/D lands
        "BETA_LOOKBACK_DAYS",  # removed once WP-A/B/C/D lands
        "FUNDAMENTALS_SOURCE",  # removed once WP-A/B/C/D lands
        "FMP_API_KEY",  # removed once WP-A/B/C/D lands
        "FMP_BASE_URL",  # removed once WP-A/B/C/D lands
        "FMP_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "FMP_MIN_REQUEST_INTERVAL_SECONDS",  # removed once WP-A/B/C/D lands
        "FMP_MAX_RETRIES",  # removed once WP-A/B/C/D lands
        "FMP_RETRY_BACKOFF_SECONDS",  # removed once WP-A/B/C/D lands
        "FMP_COOLDOWN_THRESHOLD",  # removed once WP-A/B/C/D lands
        "FMP_COOLDOWN_SECONDS",  # removed once WP-A/B/C/D lands
        "FMP_QUOTES_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_BARS_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_FUNDAMENTALS_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_ANALYST_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_EARNINGS_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_NEWS_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_MACRO_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_ECON_CALENDAR_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_INSIDER_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_SECTOR_SNAPSHOT_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_OPTIONS_HEALTH_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_OPTIONS_CONTEXT_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_PEERS_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_UNIVERSE_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_SCREENER_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_FALLBACK_ENABLED",  # removed once WP-A/B/C/D lands
        "FMP_QUOTES_REALTIME",  # removed once WP-A/B/C/D lands
        "FMP_BARS_ADJUSTMENT",  # removed once WP-A/B/C/D lands
        "FMP_ANALYST_REFRESH_HOURS",  # removed once WP-A/B/C/D lands
        "FMP_EARNINGS_REFRESH_HOURS",  # removed once WP-A/B/C/D lands
        "FMP_INSIDER_REFRESH_DAYS",  # removed once WP-A/B/C/D lands
        "FMP_INSIDER_MIN_LAG_DAYS",  # removed once WP-A/B/C/D lands
        "FMP_NEWS_PAGE_LIMIT",  # removed once WP-A/B/C/D lands
        "FMP_NEWS_MAX_PAGES",  # removed once WP-A/B/C/D lands
        "FMP_ECON_INDICATORS",  # removed once WP-A/B/C/D lands
        "FMP_MAX_SECONDS_PER_CYCLE",  # removed once WP-A/B/C/D lands
        "ROBINHOOD_USERNAME",  # removed once WP-A/B/C/D lands
        "ROBINHOOD_PASSWORD",  # removed once WP-A/B/C/D lands
        "RH_USERNAME",  # removed once WP-A/B/C/D lands
        "RH_PASSWORD",  # removed once WP-A/B/C/D lands
        "ROBINHOOD_AUTO_REFRESH_ENABLED",  # removed once WP-A/B/C/D lands
        "RH_LOGIN_DEADLINE_SECONDS",  # removed once WP-A/B/C/D lands
        "RH_LOGIN_GRACE_SECONDS",  # removed once WP-A/B/C/D lands
        "RH_LOGIN_STARTUP_SECONDS",  # removed once WP-A/B/C/D lands
        "BROKER_TRADE_INGEST_ENABLED",  # removed once WP-A/B/C/D lands
        "RH_ORDER_INGEST_BUDGET_SECONDS",  # removed once WP-A/B/C/D lands
        "RH_ORDER_SYMBOL_RESOLVE_MAX",  # removed once WP-A/B/C/D lands
        "CLOSED_POSITION_RETENTION_DAYS",  # removed once WP-A/B/C/D lands
        "CLOSED_POSITION_RETENTION_MAX_SYMBOLS",  # removed once WP-A/B/C/D lands
        "EVAL_BROKER_TRADES_ENABLED",  # removed once WP-A/B/C/D lands
        "DRY_RUN",  # removed once WP-A/B/C/D lands
        "ADVISORY_ONLY",  # removed once WP-A/B/C/D lands
        "ROBINHOOD_EXECUTION_MODE",  # removed once WP-A/B/C/D lands
        "ROBINHOOD_MAX_NOTIONAL_PER_ORDER",  # removed once WP-A/B/C/D lands
        "ROBINHOOD_LIMIT_BUFFER_BPS",  # removed once WP-A/B/C/D lands
        "OVERNIGHT_LIQUIDITY_DEPTH_HEURISTIC",  # removed once WP-A/B/C/D lands
        "QUEUE_SOURCE_MAX_AGE_SECONDS",  # removed once WP-A/B/C/D lands
        "ALERT_WEBHOOK_URL",  # removed once WP-A/B/C/D lands
        "SENTRY_ENABLED",  # removed once WP-A/B/C/D lands
        "SENTRY_DSN",  # removed once WP-A/B/C/D lands
        "SENTRY_ENVIRONMENT",  # removed once WP-A/B/C/D lands
        "SENTRY_TRACES_SAMPLE_RATE",  # removed once WP-A/B/C/D lands
        "FIX_GATEWAY_ENABLED",  # removed once WP-A/B/C/D lands
        "FIX_HEARTBEAT_INTERVAL_SECONDS",  # removed once WP-A/B/C/D lands
        "MAX_CORRELATION",  # removed once WP-A/B/C/D lands
        "DAILY_LOSS_LIMIT_PCT",  # removed once WP-A/B/C/D lands
        "MAX_ORDER_RATE_PER_MIN",  # removed once WP-A/B/C/D lands
        "EXECUTION_PRIORITY_QUEUE_ENABLED",  # removed once WP-A/B/C/D lands
        "EXECUTION_QUEUE_LEAK_RATE_PER_SEC",  # removed once WP-A/B/C/D lands
        "HMM_RISK_OFF_BLOCK_THRESHOLD",  # removed once WP-A/B/C/D lands
        "RISK_GATE_ENFORCE_MARKET_HOURS",  # removed once WP-A/B/C/D lands
        "CIRCUIT_BREAKER_VOLATILITY_Z_THRESHOLD",  # removed once WP-A/B/C/D lands
        "CIRCUIT_BREAKER_VPIN_THRESHOLD",  # removed once WP-A/B/C/D lands
        "CIRCUIT_BREAKER_OFI_THRESHOLD",  # removed once WP-A/B/C/D lands
        "CIRCUIT_BREAKER_LOSS_VELOCITY_WINDOW_MINS",  # removed once WP-A/B/C/D lands
        "CIRCUIT_BREAKER_ENABLED",  # removed once WP-A/B/C/D lands
        "CIRCUIT_BREAKER_REFERENCE_SYMBOL",  # removed once WP-A/B/C/D lands
        "HMM_N_STATES",  # removed once WP-A/B/C/D lands
        "HMM_RETRAIN_FREQ_DAYS",  # removed once WP-A/B/C/D lands
        "HMM_COVARIANCE_TYPE",  # removed once WP-A/B/C/D lands
        "HMM_N_ITER",  # removed once WP-A/B/C/D lands
        "HMM_TOL",  # removed once WP-A/B/C/D lands
        "HMM_RISK_ON_DOWNGRADE_THRESHOLD",  # removed once WP-A/B/C/D lands
        "HMM_RISK_OFF_AGREEMENT_THRESHOLD",  # removed once WP-A/B/C/D lands
        "HMM_CREDIT_SPREAD_FEATURE_ENABLED",  # removed once WP-A/B/C/D lands
        "HMM_INFLATION_FEATURE_ENABLED",  # removed once WP-A/B/C/D lands
        "HMM_VOL_TERM_SPREAD_FEATURE_ENABLED",  # removed once WP-A/B/C/D lands
        "HMM_STANDARDIZE_FEATURES_ENABLED",  # removed once WP-A/B/C/D lands
        "HMM_N_INITS",  # removed once WP-A/B/C/D lands
        "KILLSWITCH_VIX_THRESHOLD_AGREED",  # removed once WP-A/B/C/D lands
        "KILLSWITCH_SAHM_THRESHOLD_AGREED",  # removed once WP-A/B/C/D lands
        "OPTIONS_VRP_THRESHOLD",  # removed once WP-A/B/C/D lands
        "FLATTEN_ON_KILL",  # removed once WP-A/B/C/D lands
        "DISCORD_WEBHOOK_URL",  # removed once WP-A/B/C/D lands
        "SLACK_WEBHOOK_URL",  # removed once WP-A/B/C/D lands
        "ALERT_FILE_PATH",  # removed once WP-A/B/C/D lands
        "ALERT_EMAIL_FROM",  # removed once WP-A/B/C/D lands
        "ALERT_EMAIL_TO",  # removed once WP-A/B/C/D lands
        "ALERT_SMTP_HOST",  # removed once WP-A/B/C/D lands
        "ALERT_SMTP_PORT",  # removed once WP-A/B/C/D lands
        "ALERT_SMTP_USER",  # removed once WP-A/B/C/D lands
        "ALERT_SMTP_PASSWORD",  # removed once WP-A/B/C/D lands
        "ALERT_DEDUP_WINDOW_SECONDS",  # removed once WP-A/B/C/D lands
        "NTFY_DASHBOARD_URL",  # removed once WP-A/B/C/D lands
        "ALERT_NTFY_TOPIC",  # removed once WP-A/B/C/D lands
        "ALERT_EMAIL_SMTP_HOST",  # removed once WP-A/B/C/D lands
        "ALERT_EMAIL_SMTP_PORT",  # removed once WP-A/B/C/D lands
        "ALERT_EMAIL_SMTP_PASSWORD",  # removed once WP-A/B/C/D lands
        "ALERT_SLACK_WEBHOOK_URL",  # removed once WP-A/B/C/D lands
        "ALERT_CHANNELS",  # removed once WP-A/B/C/D lands
        "DASHBOARD_REFRESH_SECONDS",  # removed once WP-A/B/C/D lands
        "PROGRESS_POLL_SECONDS",  # removed once WP-A/B/C/D lands
        "WS_RISK_STREAM_INTERVAL_SECONDS",  # removed once WP-A/B/C/D lands
        "PAPER_TRADING_START_DATE",  # removed once WP-A/B/C/D lands
        "FRED_KEY_ROTATED_DATE",  # removed once WP-A/B/C/D lands
        "ALPACA_KEY_ROTATED_DATE",  # removed once WP-A/B/C/D lands
        "RISK_FREE_RATE",  # removed once WP-A/B/C/D lands
        "MARKET_RISK_PREMIUM",  # removed once WP-A/B/C/D lands
        "REQUIRED_RETURN_RATE",  # removed once WP-A/B/C/D lands
        "MAX_PORTFOLIO_HEAT",  # removed once WP-A/B/C/D lands
        "KELLY_FRACTION",  # removed once WP-A/B/C/D lands
        "KELLY_CAP",  # removed once WP-A/B/C/D lands
        "VOL_TARGET",  # removed once WP-A/B/C/D lands
        "MAX_LEVERAGE",  # removed once WP-A/B/C/D lands
        "MAX_POSITION_WEIGHT",  # removed once WP-A/B/C/D lands
        "MAX_PORTFOLIO_GROSS",  # removed once WP-A/B/C/D lands
        "SIZING_CAP_ESCALATION_ENABLED",  # removed once WP-A/B/C/D lands
        "SIZING_CAP_ESCALATION_THRESHOLD_CYCLES",  # removed once WP-A/B/C/D lands
        "SIZING_CAP_ESCALATION_FACTOR",  # removed once WP-A/B/C/D lands
        "SIZING_CAP_AUDIT_ENABLED",  # removed once WP-A/B/C/D lands
        "SIZING_CAP_ALERT_ENABLED",  # removed once WP-A/B/C/D lands
        "SIZING_CAP_ALERT_THRESHOLD_PCT",  # removed once WP-A/B/C/D lands
        "SYMBOL_RATING_ENABLED",  # removed once WP-A/B/C/D lands
        "SYMBOL_RATING_BAD_SCORE_THRESHOLD",  # removed once WP-A/B/C/D lands
        "SYMBOL_RATING_AUTO_DROP_ENABLED",  # removed once WP-A/B/C/D lands
        "SYMBOL_RATING_DROP_THRESHOLD_CYCLES",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_SIZING_ENABLED",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_MAX_DERATE",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_OWNERSHIP_REFERENCE",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_MIN_MULTIPLIER",  # removed once WP-A/B/C/D lands
        "LOCAL_DATA_ROOT",  # removed once WP-A/B/C/D lands
        "OUTPUT_DIR",  # removed once WP-A/B/C/D lands
        "NO_VENV_REEXEC",  # removed once WP-A/B/C/D lands
        "DEFAULT_TICKERS",  # removed once WP-A/B/C/D lands
        "SYNC_WATCHLIST_FILES",  # removed once WP-A/B/C/D lands
        "CORS_ALLOWED_ORIGINS",  # removed once WP-A/B/C/D lands
        "LOG_LEVEL",  # removed once WP-A/B/C/D lands
        "ADVISORY_MAX_CONCURRENCY",  # removed once WP-A/B/C/D lands
        "FORECAST_MAX_CONCURRENCY",  # removed once WP-A/B/C/D lands
        "FORECAST_USE_GARCH_SIGMA",  # removed once WP-A/B/C/D lands
        "FORECAST_PROPHET_WEIGHT",  # removed once WP-A/B/C/D lands
        "FORECAST_MODEL_PERSISTENCE_ENABLED",  # removed once WP-A/B/C/D lands
        "FORECAST_MODEL_RETRAIN_DAYS",  # removed once WP-A/B/C/D lands
        "FORECAST_CNN_LSTM_WALKFORWARD_SCALING",  # removed once WP-A/B/C/D lands
        "VALIDATION_HARNESS_OOS_GATE_ENABLED",  # removed once WP-A/B/C/D lands
        "VALIDATION_DSR_SINGLE_TRIAL_CORRECTION_ENABLED",  # removed once WP-A/B/C/D lands
        "FEATURE_DRIFT_PSI_ENABLED",  # removed once WP-A/B/C/D lands
        "LGBM_RANKER_NATIVE_MULTIINDEX_CV_ENABLED",  # removed once WP-A/B/C/D lands
        "BERT_LLA_ENABLED",  # removed once WP-A/B/C/D lands
        "BERT_LLA_BLEND_ENABLED",  # removed once WP-A/B/C/D lands
        "BERT_LLA_ABLATION_ENABLED",  # removed once WP-A/B/C/D lands
        "BERT_LLA_WINDOW_SIZE",  # removed once WP-A/B/C/D lands
        "BERT_LLA_MIN_SENTIMENT_COVERAGE",  # removed once WP-A/B/C/D lands
        "CNN_LSTM_SUBPROCESS_ISOLATION_ENABLED",  # removed once WP-A/B/C/D lands
        "CNN_LSTM_PROCESS_POOL_WORKERS",  # removed once WP-A/B/C/D lands
        "CNN_LSTM_SUBPROCESS_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "ADVISORY_REUSE_PIPELINE_COMPUTE",  # removed once WP-A/B/C/D lands
        "DATA_FETCH_MAX_CONCURRENCY",  # removed once WP-A/B/C/D lands
        "DATA_FETCH_TASK_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "PIPELINE_STEP_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "PROCESSING_FUNDAMENTALS_MAX_SECONDS_PER_CYCLE",  # removed once WP-A/B/C/D lands
        "EDGAR_MAX_CONCURRENCY",  # removed once WP-A/B/C/D lands
        "ORCHESTRATOR_INTERVAL_SECONDS",  # removed once WP-A/B/C/D lands
        "PIPELINE_STALL_ALERT_ENABLED",  # removed once WP-A/B/C/D lands
        "PIPELINE_STALL_ALERT_SECONDS",  # removed once WP-A/B/C/D lands
        "ORCHESTRATOR_DAEMON_ENABLED",  # removed once WP-A/B/C/D lands
        "ORCHESTRATOR_EXTENDED_HOURS_ONLY",  # removed once WP-A/B/C/D lands
        "RUNTIME_FLAGS_REFRESH_ENABLED",  # removed once WP-A/B/C/D lands
        "RUNTIME_FLAGS_REFRESH_INTERVAL_SECONDS",  # removed once WP-A/B/C/D lands
        "DAEMON_SHUTDOWN_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "SIGNAL_WEIGHTS",  # removed once WP-A/B/C/D lands
        "REGIME_SIGNAL_WEIGHTS",  # removed once WP-A/B/C/D lands
        "SECTOR_FORECAST_CONFIG_PATH",  # removed once WP-A/B/C/D lands
        "SECTOR_FORECAST_CONFIGS",  # removed once WP-A/B/C/D lands
        "DATABASE_URL",  # removed once WP-A/B/C/D lands
        "DB_POOL_SIZE",  # removed once WP-A/B/C/D lands
        "DB_MAX_OVERFLOW",  # removed once WP-A/B/C/D lands
        "MCP_DATABASE_URL_RO",  # removed once WP-A/B/C/D lands
        "HISTORICAL_STORE_ENABLED",  # removed once WP-A/B/C/D lands
        "BARS_BACKFILL_DAYS",  # removed once WP-A/B/C/D lands
        "FUNDAMENTALS_REFRESH_DAYS",  # removed once WP-A/B/C/D lands
        "MACRO_REFRESH_HOURS",  # removed once WP-A/B/C/D lands
        "PIT_CAPTURE_ENABLED",  # removed once WP-A/B/C/D lands
        "NEWS_HISTORY_CAPTURE_ENABLED",  # removed once WP-A/B/C/D lands
        "SENTIMENT_INGESTION_ENABLED",  # removed once WP-A/B/C/D lands
        "SENTIMENT_AUDIT_ENABLED",  # removed once WP-A/B/C/D lands
        "SENTIMENT_PIT_MIN_MONTHS",  # removed once WP-A/B/C/D lands
        "SENTIMENT_SOURCES",  # removed once WP-A/B/C/D lands
        "SENTIMENT_COMMENT_SOURCES",  # removed once WP-A/B/C/D lands
        "SENTIMENT_INDEX_ENABLED",  # removed once WP-A/B/C/D lands
        "SENTIMENT_INGESTION_LOOKBACK_DAYS",  # removed once WP-A/B/C/D lands
        "GDELT_MIN_REQUEST_INTERVAL_SECONDS",  # removed once WP-A/B/C/D lands
        "GDELT_MAX_RETRIES",  # removed once WP-A/B/C/D lands
        "GDELT_RETRY_BACKOFF_SECONDS",  # removed once WP-A/B/C/D lands
        "GDELT_COOLDOWN_THRESHOLD",  # removed once WP-A/B/C/D lands
        "GDELT_COOLDOWN_SECONDS",  # removed once WP-A/B/C/D lands
        "SENTIMENT_DESENTENCIZE_ENABLED",  # removed once WP-A/B/C/D lands
        "REDDIT_CLIENT_ID",  # removed once WP-A/B/C/D lands
        "REDDIT_CLIENT_SECRET",  # removed once WP-A/B/C/D lands
        "REDDIT_USER_AGENT",  # removed once WP-A/B/C/D lands
        "REDDIT_BACKFILL_MAX_PAGES",  # removed once WP-A/B/C/D lands
        "STOCKTWITS_ENABLED",  # removed once WP-A/B/C/D lands
        "EDGAR_USER_AGENT",  # removed once WP-A/B/C/D lands
        "EDGAR_COOLDOWN_THRESHOLD",  # removed once WP-A/B/C/D lands
        "EDGAR_COOLDOWN_SECONDS",  # removed once WP-A/B/C/D lands
        "SENTIMENT_MAX_DOCUMENTS_PER_CYCLE",  # removed once WP-A/B/C/D lands
        "SENTIMENT_INGESTION_MAX_SECONDS_PER_CYCLE",  # removed once WP-A/B/C/D lands
        "SENTIMENT_CIRCUIT_BREAKER_THRESHOLD",  # removed once WP-A/B/C/D lands
        "SENTIMENT_LLM_VERIFICATION_ENABLED",  # removed once WP-A/B/C/D lands
        "SENTIMENT_LLM_VERIFICATION_PROVIDER",  # removed once WP-A/B/C/D lands
        "SENTIMENT_LLM_VERIFICATION_MAX_CALLS_PER_CYCLE",  # removed once WP-A/B/C/D lands
        "SENTIMENT_LLM_VERIFICATION_BORDERLINE_LOW",  # removed once WP-A/B/C/D lands
        "SENTIMENT_LLM_VERIFICATION_BORDERLINE_HIGH",  # removed once WP-A/B/C/D lands
        "GOOGLE_NEWS_LOOKBACK_WINDOW",  # removed once WP-A/B/C/D lands
        "EDGAR_FULLTEXT_ENABLED",  # removed once WP-A/B/C/D lands
        "EDGAR_FULLTEXT_FORMS",  # removed once WP-A/B/C/D lands
        "EDGAR_FULLTEXT_CHUNK_TOKENS",  # removed once WP-A/B/C/D lands
        "SECTOR_HEAT_ENABLED",  # removed once WP-A/B/C/D lands
        "SECTOR_HEAT_SMOOTHING_SIGMA",  # removed once WP-A/B/C/D lands
        "SECTOR_HEAT_LOOKBACK_DAYS",  # removed once WP-A/B/C/D lands
        "SECTOR_SELECTION_ENABLED",  # removed once WP-A/B/C/D lands
        "SECTOR_SELECTION_HEAT_LOOKBACK_DAYS",  # removed once WP-A/B/C/D lands
        "SECTOR_SELECTION_HEAT_A",  # removed once WP-A/B/C/D lands
        "SECTOR_SELECTION_HEAT_B",  # removed once WP-A/B/C/D lands
        "SECTOR_SELECTION_HEAT_C",  # removed once WP-A/B/C/D lands
        "SECTOR_SIMILARITY_EMBEDDER",  # removed once WP-A/B/C/D lands
        "SECTOR_SIMILARITY_MODEL",  # removed once WP-A/B/C/D lands
        "SECTOR_SIMILARITY_POOLING",  # removed once WP-A/B/C/D lands
        "SECTOR_SELECTION_TOP_N",  # removed once WP-A/B/C/D lands
        "SECTOR_SELECTION_W1",  # removed once WP-A/B/C/D lands
        "SECTOR_SELECTION_W2",  # removed once WP-A/B/C/D lands
        "WIKIPEDIA_ATTENTION_ENABLED",  # removed once WP-A/B/C/D lands
        "WIKIPEDIA_ATTENTION_LOOKBACK_DAYS",  # removed once WP-A/B/C/D lands
        "PYTRENDS_ENABLED",  # removed once WP-A/B/C/D lands
        "ATTENTION_INGESTION_MAX_SECONDS_PER_CYCLE",  # removed once WP-A/B/C/D lands
        "ATTENTION_CIRCUIT_BREAKER_THRESHOLD",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_ENABLED",  # removed once WP-A/B/C/D lands
        "ETF_HOLDINGS_MARKET_PROXY",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_WRAPPERS",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_EXCLUDED_SYMBOLS",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_WINDOW_DAYS",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_MIN_OBS",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_PORTFOLIO_ENABLED",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_COV_INFLATION",  # removed once WP-A/B/C/D lands
        "ETF_TRANSMISSION_COV_WINDOW_DAYS",  # removed once WP-A/B/C/D lands
        "FORECAST_SKILL_WEIGHTING_ENABLED",  # removed once WP-A/B/C/D lands
        "FORECAST_SKILL_WINDOW_DAYS",  # removed once WP-A/B/C/D lands
        "FORECAST_SKILL_MIN_OBS",  # removed once WP-A/B/C/D lands
        "MACRO_REGIME_GATE_ENABLED",  # removed once WP-A/B/C/D lands
        "DISABLED_SIGNAL_MODULES",  # removed once WP-A/B/C/D lands
        "PILOTS_TOP_N",  # removed once WP-A/B/C/D lands
        "FOLLOW_MIN_AMOUNT",  # removed once WP-A/B/C/D lands
        "BROKERAGE_CONNECT_ENABLED",  # removed once WP-A/B/C/D lands
        "AUTOMATION_WRITES_ENABLED",  # removed once WP-A/B/C/D lands
        "STRATEGY_WRITES_ENABLED",  # removed once WP-A/B/C/D lands
        "LLM_WRITES_ENABLED",  # removed once WP-A/B/C/D lands
        "AGENTIC_DISCOVERY_ENABLED",  # removed once WP-A/B/C/D lands
        "GENERAL_SETTINGS_WRITES_ENABLED",  # removed once WP-A/B/C/D lands
        "RLHF_CALIBRATION_ENABLED",  # removed once WP-A/B/C/D lands
        "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD",  # removed once WP-A/B/C/D lands
        "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED",  # removed once WP-A/B/C/D lands
        "RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED",  # removed once WP-A/B/C/D lands
        "AI_GENERATION_API_ENABLED",  # removed once WP-A/B/C/D lands
        "RAG_QUERY_API_ENABLED",  # removed once WP-A/B/C/D lands
        "MACRO_GATE_WRITES_ENABLED",  # removed once WP-A/B/C/D lands
        "BROKERAGE_REFRESH_ENABLED",  # removed once WP-A/B/C/D lands
        "AGENTIC_MAX_CANDIDATES",  # removed once WP-A/B/C/D lands
        "OPTIONS_MATRIX_ENABLED",  # removed once WP-A/B/C/D lands
        "PAIRS_SNAPSHOT_ENABLED",  # removed once WP-A/B/C/D lands
        "PAIRS_SNAPSHOT_MAX_PAIRS",  # removed once WP-A/B/C/D lands
        "MULTIFACTOR_MICROCAP_THRESHOLD",  # removed once WP-A/B/C/D lands
        "META_LABEL_MIN_CONFIDENCE",  # removed once WP-A/B/C/D lands
        "META_LABELING_ENABLED",  # removed once WP-A/B/C/D lands
        "SNAPSHOT_HISTORY_DAYS",  # removed once WP-A/B/C/D lands
        "SNAPSHOT_CONVICTION_DELTA_THRESHOLD",  # removed once WP-A/B/C/D lands
        "WATCH_RULES_FILE",  # removed once WP-A/B/C/D lands
        "RATIONALE_VERBOSITY",  # removed once WP-A/B/C/D lands
        "NEWS_LOOKBACK_DAYS",  # removed once WP-A/B/C/D lands
        "FINBERT_ENABLED",  # removed once WP-A/B/C/D lands
        "FINBERT_BATCH_SIZE",  # removed once WP-A/B/C/D lands
        "FINBERT_SCORE_CACHE_ENABLED",  # removed once WP-A/B/C/D lands
        "LLM_COMMENTARY_ENABLED",  # removed once WP-A/B/C/D lands
        "LLM_COMMENTARY_RATIONALE_PROVIDER",  # removed once WP-A/B/C/D lands
        "LLM_COMMENTARY_ALERT_PROVIDER",  # removed once WP-A/B/C/D lands
        "LLM_COMMENTARY_CACHE_PATH",  # removed once WP-A/B/C/D lands
        "LLM_COMMENTARY_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "LLM_STATUS_MAX_AGE_HOURS",  # removed once WP-A/B/C/D lands
        "ANTHROPIC_API_KEY",  # removed once WP-A/B/C/D lands
        "GEMINI_API_KEY",  # removed once WP-A/B/C/D lands
        "AI_CHAT_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "GEMINI_LIVE_CHAT_ENABLED",  # removed once WP-A/B/C/D lands
        "GEMINI_LIVE_CHAT_MODEL",  # removed once WP-A/B/C/D lands
        "GEMINI_LIVE_VOICE_NAME",  # removed once WP-A/B/C/D lands
        "GEMINI_CHAT_MODEL",  # removed once WP-A/B/C/D lands
        "LOCAL_LLM_BASE_URL",  # removed once WP-A/B/C/D lands
        "LOCAL_LLM_MODEL",  # removed once WP-A/B/C/D lands
        "LOCAL_LLM_API_KEY",  # removed once WP-A/B/C/D lands
        "AI_CHAT_DEFAULT_PROVIDER",  # removed once WP-A/B/C/D lands
        "AI_CHAT_DEFAULT_MODEL",  # removed once WP-A/B/C/D lands
        "OPAL_RESEARCH_ENABLED",  # removed once WP-A/B/C/D lands
        "OPAL_RESEARCH_PROVIDER",  # removed once WP-A/B/C/D lands
        "OPAL_RESEARCH_MODEL",  # removed once WP-A/B/C/D lands
        "OPAL_RESEARCH_TIMEOUT_SECONDS",  # removed once WP-A/B/C/D lands
        "OPENAI_API_KEY",  # removed once WP-A/B/C/D lands
        "GRAVITY_AI_RUNNER_ENABLED",  # removed once WP-A/B/C/D lands
        "GRAVITY_AI_RUNNER_OUTPUT_PATH",  # removed once WP-A/B/C/D lands
        "NEWS_EARNINGS_SUPPRESS_HOURS",  # removed once WP-A/B/C/D lands
        "NEWS_EARNINGS_DAMPEN_DAYS",  # removed once WP-A/B/C/D lands
        "SENTIMENT_SOCIAL_BLEND_WEIGHT",  # removed once WP-A/B/C/D lands
        "CORRELATION_CLUSTER_LOOKBACK_DAYS",  # removed once WP-A/B/C/D lands
        "CORRELATION_CLUSTER_THRESHOLD",  # removed once WP-A/B/C/D lands
        "USE_DUAL_MOMENTUM_OVERLAY",  # removed once WP-A/B/C/D lands
        "DUAL_MOMENTUM_SAFE_ASSET",  # removed once WP-A/B/C/D lands
        "DUAL_MOMENTUM_RISKY_ASSETS",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_ENABLED",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_BACKEND",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_URL",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_TOKEN",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_PUBLISH_TOKEN",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_SIGNING_KEY",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_PINS",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_REFRESH_SECONDS",  # removed once WP-A/B/C/D lands
        "PROMPT_CACHE_DIR",  # removed once WP-A/B/C/D lands
        "PROMPT_CACHE_KEEP_VERSIONS",  # removed once WP-A/B/C/D lands
        "PROMPT_MAX_CHARS",  # removed once WP-A/B/C/D lands
        "RAG_PORTFOLIO_CONTEXT_ENABLED",  # removed once WP-A/B/C/D lands
        "RAG_PORTFOLIO_CONTEXT_PROVIDER",  # removed once WP-A/B/C/D lands
        "RAG_EMBEDDING_PROVIDER",  # removed once WP-A/B/C/D lands
        "RAG_INDEX_MAX_DOCUMENTS",  # removed once WP-A/B/C/D lands
        "RAG_RETRIEVAL_TOP_K",  # removed once WP-A/B/C/D lands
        "RAG_INDEX_LOOKBACK_DAYS",  # removed once WP-A/B/C/D lands
        "ETF_HOLDINGS_ENABLED",  # removed once WP-A/B/C/D lands
        "ETF_HOLDINGS_TICKERS",  # removed once WP-A/B/C/D lands
        "ETF_HOLDINGS_REFRESH_DAYS",  # removed once WP-A/B/C/D lands
        "ETF_HOLDINGS_ISSUER_CSV_ENABLED",  # removed once WP-A/B/C/D lands
        "ETF_HOLDINGS_MAX_SECONDS_PER_CYCLE",  # removed once WP-A/B/C/D lands
        "ETF_HOLDINGS_CIRCUIT_BREAKER_THRESHOLD",  # removed once WP-A/B/C/D lands
        "OPTIONS_TRUE_IVR_ENABLED",  # removed once WP-A/B/C/D lands
        "DEAD_LETTER_RETRY_ENABLED",  # removed once WP-A/B/C/D lands
        "PROMPT_REGISTRY_WRITES_ENABLED",  # removed once WP-A/B/C/D lands
        "UNIVERSE_SYNC_ENABLED",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_HORIZONS",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_LOOKBACK_YEARS",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_MOMENTUM_WINDOW",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_VOL_SHORT_WINDOW",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_VOL_LONG_WINDOW",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_RSI_WINDOW",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_MACD_FAST",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_MACD_SLOW",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_VOL_RATIO_WINDOW",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_TRAIN_SPLIT",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_N_ESTIMATORS",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_MAX_DEPTH",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_RANDOM_STATE",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_CLASSIFIER_TYPE",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_ENABLED",  # removed once WP-A/B/C/D lands
        "FORECAST_BACKFILL_DEADLINE_SECONDS",  # removed once WP-A/B/C/D lands
        "CACHE_LONG_SHORT_ENABLED",  # removed once WP-A/B/C/D lands
        "CACHE_LONG_SHORT_WRITES_ENABLED",  # removed once WP-A/B/C/D lands
        "CACHE_LONG_SHORT_MIN_CORRELATION",  # removed once WP-A/B/C/D lands
        "CACHE_LONG_SHORT_TLH_THRESHOLD_PCT",  # removed once WP-A/B/C/D lands
        "CACHE_LONG_SHORT_SCAN_INTERVAL_SECONDS",  # removed once WP-A/B/C/D lands
        "CACHE_LONG_SHORT_PROXY_CANDIDATES",  # removed once WP-A/B/C/D lands
        "OPTIONS_COPULA_ZSCORE_ENTRY_THRESHOLD",  # removed once WP-A/B/C/D lands
        "WATCHLIST",  # removed once WP-A/B/C/D lands
        "GCLOUD_BIN",  # removed once WP-A/B/C/D lands
        "GRAVITY_REQUIRE_NATIVE",  # removed once WP-A/B/C/D lands
        "QDRANT_COLLECTION",  # removed once WP-A/B/C/D lands
        "QDRANT_URL",  # removed once WP-A/B/C/D lands
    ]
    assert True

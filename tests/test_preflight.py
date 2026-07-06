"""
tests/test_preflight.py
=======================
Unit tests for ``scripts/preflight_check.py``.

Testing strategy
----------------
Every check function is tested in isolation: real file-system interactions use
``pytest``'s ``tmp_path`` fixture for a private temporary directory, and the
``settings`` singleton plus ``_REPO_ROOT`` constant are patched to point at
that directory so tests run without a pre-existing repo state (no live broker
credentials, no real database, no real heartbeat file).

All tests use ``patch("scripts.preflight_check.settings", ...)`` rather than
patching the global ``settings`` object because each check function reads
``settings.*`` attributes at call time — the patch must be in place before the
check body executes.

Coverage
--------
* Every ``check_*`` function produces a ``CheckResult`` with a non-empty
  ``reason`` string for both PASS and FAIL outcomes.
* Edge cases: missing files, stale heartbeat, expired reports, invalid ISO
  dates, active kill switch, ``alpaca_paper_mode`` warning-vs-blocking.
* ``run_checks(skip=[...])`` marks skipped checks as PASS with "(skipped)"
  reason while still including them in the result list.
* ``main()`` returns exit code 0 when all checks pass, 1 when any fail.
* ``main(["--json", ...])`` emits a valid JSON array with the required keys.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _settings(tmp_path: Path, **overrides) -> MagicMock:
    """Build a MagicMock that looks like ``settings`` for a single check test.

    All fields relevant to the preflight script have sensible test defaults
    (keys configured, paper mode, dry-run off, output dir = tmp_path).
    Pass keyword overrides to test specific failure conditions.

    Parameters
    ----------
    tmp_path:
        The ``pytest`` temporary directory.  Used as the default for
        ``OUTPUT_DIR`` so file-based checks (heartbeat, block log) look in an
        isolated location.
    **overrides:
        Any setting attribute to override from its test default, e.g.
        ``DRY_RUN=True``, ``PAPER_TRADING_START_DATE=None``.
    """
    m = MagicMock()
    m.FRED_API_KEY = overrides.get("FRED_API_KEY", "valid_key_abc123")
    m.fred_key_is_leaked = overrides.get("fred_key_is_leaked", False)
    m.ALPACA_API_KEY = overrides.get("ALPACA_API_KEY", "pk_test")
    m.ALPACA_SECRET_KEY = overrides.get("ALPACA_SECRET_KEY", "sk_test")
    m.ALPACA_PAPER = overrides.get("ALPACA_PAPER", True)
    m.DRY_RUN = overrides.get("DRY_RUN", False)
    m.OUTPUT_DIR = overrides.get("OUTPUT_DIR", tmp_path)
    m.PAPER_TRADING_START_DATE = overrides.get("PAPER_TRADING_START_DATE", None)
    # ADVISORY_ONLY defaults to True (project default) so tests that call
    # run_checks() without an override don't trigger the broker checks.
    m.ADVISORY_ONLY = overrides.get("ADVISORY_ONLY", True)
    m.MACRO_REGIME_GATE_ENABLED = overrides.get("MACRO_REGIME_GATE_ENABLED", True)
    # Key-rotation dates — default None (unset = warning-level PASS, not blocking).
    m.FRED_KEY_ROTATED_DATE = overrides.get("FRED_KEY_ROTATED_DATE", None)
    m.ALPACA_KEY_ROTATED_DATE = overrides.get("ALPACA_KEY_ROTATED_DATE", None)
    return m


# ---------------------------------------------------------------------------
# fred_key_configured
# ---------------------------------------------------------------------------

class TestFredKeyConfigured:
    """Verify FRED_API_KEY presence and integrity.

    ``settings.fred_key_is_leaked`` is a property that compares the key
    against known-compromised values; we mock it directly rather than using a
    real compromised key in tests.
    """

    def test_passes_with_valid_key(self, tmp_path):
        """A non-empty, non-leaked key produces a PASS result."""
        from scripts.preflight_check import check_fred_key_configured
        s = _settings(tmp_path, FRED_API_KEY="abc123", fred_key_is_leaked=False)
        with patch("scripts.preflight_check.settings", s):
            r = check_fred_key_configured()
        assert r.passed
        assert r.name == "fred_key_configured"

    def test_fails_missing_key(self, tmp_path):
        """An empty FRED_API_KEY produces a FAIL with a "not set" reason."""
        from scripts.preflight_check import check_fred_key_configured
        s = _settings(tmp_path, FRED_API_KEY="")
        with patch("scripts.preflight_check.settings", s):
            r = check_fred_key_configured()
        assert not r.passed
        assert "not set" in r.reason

    def test_fails_leaked_key(self, tmp_path):
        """A key that matches a known-compromised value produces a FAIL.

        This prevents a repo-committed leaked key from silently passing the
        preflight gate and reaching production.
        """
        from scripts.preflight_check import check_fred_key_configured
        s = _settings(tmp_path, FRED_API_KEY="leaked", fred_key_is_leaked=True)
        with patch("scripts.preflight_check.settings", s):
            r = check_fred_key_configured()
        assert not r.passed
        assert "compromised" in r.reason.lower() or "leaked" in r.reason.lower()


# ---------------------------------------------------------------------------
# alpaca_configured
# ---------------------------------------------------------------------------

class TestAlpacaConfigured:
    """Both ALPACA_API_KEY and ALPACA_SECRET_KEY must be set for live operation."""

    def test_passes_both_set(self, tmp_path):
        """Both keys present → PASS."""
        from scripts.preflight_check import check_alpaca_configured
        s = _settings(tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_alpaca_configured()
        assert r.passed

    def test_fails_missing_secret(self, tmp_path):
        """Missing secret key → FAIL; the key alone is not sufficient."""
        from scripts.preflight_check import check_alpaca_configured
        s = _settings(tmp_path, ALPACA_SECRET_KEY=None)
        with patch("scripts.preflight_check.settings", s):
            r = check_alpaca_configured()
        assert not r.passed


# ---------------------------------------------------------------------------
# alpaca_paper_mode
# ---------------------------------------------------------------------------

class TestAlpacaPaperMode:
    """ALPACA_PAPER=False is a warning, not a hard failure.

    Going live is a deliberate operator decision.  The check surfaces the
    configuration prominently (warning icon in the table) without blocking
    the go-live gate — which the operator may be trying to cross intentionally.
    """

    def test_passes_and_warns_when_live(self, tmp_path):
        """ALPACA_PAPER=False → passed=True AND warning=True.

        The warning flag ensures the output table shows ⚠️ rather than ✅
        so the operator cannot miss it.  The exit code is still 0.
        """
        from scripts.preflight_check import check_alpaca_paper_mode
        s = _settings(tmp_path, ALPACA_PAPER=False)
        with patch("scripts.preflight_check.settings", s):
            r = check_alpaca_paper_mode()
        assert r.passed
        assert r.warning
        assert "LIVE TRADING" in r.reason.upper() or "live" in r.reason.lower()

    def test_passes_without_warning_in_paper(self, tmp_path):
        """ALPACA_PAPER=True → passed=True AND warning=False (clean pass)."""
        from scripts.preflight_check import check_alpaca_paper_mode
        s = _settings(tmp_path, ALPACA_PAPER=True)
        with patch("scripts.preflight_check.settings", s):
            r = check_alpaca_paper_mode()
        assert r.passed
        assert not r.warning


# ---------------------------------------------------------------------------
# dry_run_disabled
# ---------------------------------------------------------------------------

class TestDryRunDisabled:
    """DRY_RUN must be False for orders to reach the broker."""

    def test_fails_when_dry_run_true(self, tmp_path):
        """DRY_RUN=True → FAIL so the operator cannot accidentally go live in
        dry-run mode where orders are logged but never submitted."""
        from scripts.preflight_check import check_dry_run_disabled
        s = _settings(tmp_path, DRY_RUN=True)
        with patch("scripts.preflight_check.settings", s):
            r = check_dry_run_disabled()
        assert not r.passed
        assert "DRY_RUN" in r.reason

    def test_passes_when_false(self, tmp_path):
        """DRY_RUN=False → PASS."""
        from scripts.preflight_check import check_dry_run_disabled
        s = _settings(tmp_path, DRY_RUN=False)
        with patch("scripts.preflight_check.settings", s):
            r = check_dry_run_disabled()
        assert r.passed


# ---------------------------------------------------------------------------
# kill_switch_inactive
# ---------------------------------------------------------------------------

class TestKillSwitchInactive:
    """The KILL_SWITCH sentinel file must not exist before starting the orchestrator.

    An active kill switch causes ``OrderManager`` to raise
    ``KillSwitchActiveError`` on every order submission, so the orchestrator
    would fail immediately after opening.
    """

    def test_passes_when_inactive(self, tmp_path):
        """No sentinel file → PASS (kill switch inactive)."""
        from scripts.preflight_check import check_kill_switch_inactive
        s = _settings(tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_kill_switch_inactive()
        assert r.passed

    def test_fails_when_active(self, tmp_path):
        """Sentinel file present → FAIL.

        We write the sentinel file directly and patch
        ``execution.kill_switch.KILL_SWITCH_FILE`` (the module-level constant
        evaluated at import time) to point at it.  We cannot patch the
        ``settings.OUTPUT_DIR`` path because ``KILL_SWITCH_FILE`` is already
        bound to a ``Path`` value by the time the module is imported.
        """
        from scripts.preflight_check import check_kill_switch_inactive
        sentinel = tmp_path / "KILL_SWITCH"
        sentinel.write_text("preflight test", encoding="utf-8")
        s = _settings(tmp_path)
        with patch("scripts.preflight_check.settings", s):
            with patch("execution.kill_switch.KILL_SWITCH_FILE", sentinel):
                r = check_kill_switch_inactive()
        assert not r.passed
        assert "ACTIVE" in r.reason.upper()


# ---------------------------------------------------------------------------
# heartbeat_fresh
# ---------------------------------------------------------------------------

class TestHeartbeatFresh:
    """The orchestrator heartbeat file must exist and be recent.

    ``main_orchestrator._heartbeat()`` writes ``OUTPUT_DIR/heartbeat.txt``
    every 60 seconds.  A missing or stale file indicates the orchestrator
    has crashed or was never started.
    """

    def test_passes_fresh_heartbeat(self, tmp_path):
        """A heartbeat written moments ago must pass within any reasonable window."""
        from scripts.preflight_check import check_heartbeat_fresh
        hb = tmp_path / "heartbeat.txt"
        hb.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        s = _settings(tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_heartbeat_fresh(max_age_hours=1.0)
        assert r.passed

    def test_fails_stale_heartbeat(self, tmp_path):
        """A heartbeat older than ``max_age_hours`` must produce a FAIL.

        The reason string must mention "old" so the operator immediately
        understands the issue without reading the code.
        """
        from scripts.preflight_check import check_heartbeat_fresh
        hb = tmp_path / "heartbeat.txt"
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        hb.write_text(stale_ts, encoding="utf-8")
        s = _settings(tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_heartbeat_fresh(max_age_hours=2.0)
        assert not r.passed
        assert "old" in r.reason.lower()

    def test_fails_missing_heartbeat(self, tmp_path):
        """A missing heartbeat file → FAIL with "not found" in the reason.

        An absent file means either the orchestrator has never been run in
        this environment (required for go-live) or ``OUTPUT_DIR`` is wrong.
        """
        from scripts.preflight_check import check_heartbeat_fresh
        s = _settings(tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_heartbeat_fresh()
        assert not r.passed
        assert "not found" in r.reason


# ---------------------------------------------------------------------------
# db_exists
# ---------------------------------------------------------------------------

class TestDbExists:
    """The SQLite database must exist and be non-empty."""

    def test_passes_when_db_present(self, tmp_path):
        """A non-zero-byte file at the expected path → PASS."""
        from scripts.preflight_check import check_db_exists
        db = tmp_path / "quant_platform.db"
        # Write enough bytes that ``st_size > 0`` is satisfied.
        db.write_bytes(b"SQLite" + b"\x00" * 100)
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_db_exists()
        assert r.passed

    def test_fails_when_missing(self, tmp_path):
        """No database file → FAIL with "not found" in the reason.

        ``_REPO_ROOT`` is patched to ``tmp_path`` (which has no DB) so the
        real repo database does not interfere with this failure test.
        """
        from scripts.preflight_check import check_db_exists
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_db_exists()
        assert not r.passed
        assert "not found" in r.reason


# ---------------------------------------------------------------------------
# paper_trading_duration
# ---------------------------------------------------------------------------

class TestPaperTradingDuration:
    """Paper trading must have run for at least 90 days before go-live."""

    def test_passes_sufficient_duration(self, tmp_path):
        """100 days of paper trading with a 90-day requirement → PASS."""
        from scripts.preflight_check import check_paper_trading_duration
        start = (date.today() - timedelta(days=100)).isoformat()
        s = _settings(tmp_path, PAPER_TRADING_START_DATE=start)
        with patch("scripts.preflight_check.settings", s):
            r = check_paper_trading_duration(min_days=90)
        assert r.passed
        assert "100 days" in r.reason or "≥ 90" in r.reason

    def test_fails_insufficient_duration(self, tmp_path):
        """30 days of paper trading with a 90-day requirement → FAIL.

        The reason must mention the elapsed days so the operator can calculate
        when they will become eligible without doing the arithmetic manually.
        """
        from scripts.preflight_check import check_paper_trading_duration
        start = (date.today() - timedelta(days=30)).isoformat()
        s = _settings(tmp_path, PAPER_TRADING_START_DATE=start)
        with patch("scripts.preflight_check.settings", s):
            r = check_paper_trading_duration(min_days=90)
        assert not r.passed
        assert "30 days" in r.reason

    def test_fails_missing_start_date(self, tmp_path):
        """``PAPER_TRADING_START_DATE`` not set → FAIL (cannot measure duration).

        We treat this as a failure rather than a skip so the operator is forced
        to acknowledge when paper trading started — a key pre-condition for
        go-live readiness.
        """
        from scripts.preflight_check import check_paper_trading_duration
        s = _settings(tmp_path, PAPER_TRADING_START_DATE=None)
        with patch("scripts.preflight_check.settings", s):
            r = check_paper_trading_duration()
        assert not r.passed
        assert "PAPER_TRADING_START_DATE" in r.reason

    def test_fails_invalid_date_format(self, tmp_path):
        """A non-ISO date string → FAIL with "Invalid" or "format" in the reason.

        ``date.fromisoformat`` raises ``ValueError`` on malformed strings;
        the check must catch this and return a FAIL rather than propagating.
        """
        from scripts.preflight_check import check_paper_trading_duration
        s = _settings(tmp_path, PAPER_TRADING_START_DATE="not-a-date")
        with patch("scripts.preflight_check.settings", s):
            r = check_paper_trading_duration()
        assert not r.passed
        assert "Invalid" in r.reason or "format" in r.reason.lower()


# ---------------------------------------------------------------------------
# validation_reports
# ---------------------------------------------------------------------------

class TestValidationReports:
    """All strategy validation summaries must be deployable and recent (< 30 d)."""

    def test_passes_with_fresh_deployable_report(self, tmp_path):
        """A single deployable, today-dated report → PASS."""
        from scripts.preflight_check import check_validation_reports
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        summary = {
            "strategy_id": "spy_bh",
            "deployable": True,
            "pbo": 0.3,
            "dsr": 0.97,
            "sharpe": 0.8,
            "max_drawdown": 0.18,
            "report_date": date.today().isoformat(),
        }
        (reports_dir / "spy_bh_validation_summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_validation_reports(max_age_days=30)
        assert r.passed

    def test_fails_not_deployable(self, tmp_path):
        """``deployable=False`` → FAIL with the strategy name and reason."""
        from scripts.preflight_check import check_validation_reports
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        summary = {
            "strategy_id": "bad_strat",
            "deployable": False,
            "report_date": date.today().isoformat(),
        }
        (reports_dir / "bad_strat_validation_summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_validation_reports()
        assert not r.passed
        assert "deployable=False" in r.reason

    def test_fails_stale_report(self, tmp_path):
        """A report dated 60 days ago with a 30-day max → FAIL.

        ISO date strings compare lexicographically for YYYY-MM-DD format, so
        the staleness check uses string comparison instead of date parsing —
        this test confirms that assumption holds.
        """
        from scripts.preflight_check import check_validation_reports
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        stale_date = (date.today() - timedelta(days=60)).isoformat()
        summary = {
            "strategy_id": "stale_strat",
            "deployable": True,
            "report_date": stale_date,
        }
        (reports_dir / "stale_strat_validation_summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_validation_reports(max_age_days=30)
        assert not r.passed
        assert "older than" in r.reason

    def test_fails_no_reports_dir(self, tmp_path):
        """Missing ``reports/`` directory → FAIL (no harness has been run)."""
        from scripts.preflight_check import check_validation_reports
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_validation_reports()
        assert not r.passed


# ---------------------------------------------------------------------------
# env_no_duplicate_keys
# ---------------------------------------------------------------------------

class TestEnvNoDuplicateKeys:
    """Verify check_env_no_duplicate_keys.

    The check locates ``.env`` at ``_REPO_ROOT / ".env"`` (same as
    check_env_not_committed), so tests patch ``_REPO_ROOT`` to a ``tmp_path``
    containing a fixture ``.env``.  It is warning-only and reports KEY NAMES
    (never values).
    """

    def _write_env(self, tmp_path, lines: str) -> None:
        (tmp_path / ".env").write_text(lines, encoding="utf-8")

    def test_warns_on_duplicate_key(self, tmp_path):
        """A .env with a repeated key → warning-level PASS naming the key."""
        from scripts.preflight_check import check_env_no_duplicate_keys
        self._write_env(
            tmp_path,
            "# header comment\n"
            "FRED_API_KEY=first\n"
            "SOME_FLAG=true\n"
            "FRED_API_KEY=second\n",
        )
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_env_no_duplicate_keys()
        assert r.passed  # warning, not blocking
        assert r.warning
        assert "FRED_API_KEY" in r.reason
        assert "SOME_FLAG" not in r.reason  # only the duplicate is named

    def test_reports_multiple_duplicates(self, tmp_path):
        """Multiple duplicate keys are all named in the reason (KEY NAMES only)."""
        from scripts.preflight_check import check_env_no_duplicate_keys
        self._write_env(
            tmp_path,
            "AAA=1\nBBB=1\nAAA=2\nCCC=1\nBBB=2\n",
        )
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_env_no_duplicate_keys()
        assert r.passed and r.warning
        assert "AAA" in r.reason
        assert "BBB" in r.reason
        assert "CCC" not in r.reason

    def test_does_not_leak_values(self, tmp_path):
        """The reason string must report KEY NAMES only — never the value."""
        from scripts.preflight_check import check_env_no_duplicate_keys
        secret = "super-secret-token-xyz"
        self._write_env(
            tmp_path,
            f"API_TOKEN=old\nAPI_TOKEN={secret}\n",
        )
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_env_no_duplicate_keys()
        assert "API_TOKEN" in r.reason
        assert secret not in r.reason

    def test_passes_on_clean_env(self, tmp_path):
        """A .env with no duplicates → PASS, no warning."""
        from scripts.preflight_check import check_env_no_duplicate_keys
        self._write_env(
            tmp_path,
            "# comment\nFRED_API_KEY=abc\nALPACA_PAPER=true\n\nDRY_RUN=false\n",
        )
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_env_no_duplicate_keys()
        assert r.passed
        assert not r.warning
        assert "No duplicate keys" in r.reason

    def test_ignores_comments_and_blanks(self, tmp_path):
        """Commented-out and blank lines never count as key definitions."""
        from scripts.preflight_check import check_env_no_duplicate_keys
        self._write_env(
            tmp_path,
            "KEY_A=1\n# KEY_A=commented-out\n\n   \n#KEY_A=also-commented\n",
        )
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_env_no_duplicate_keys()
        assert r.passed and not r.warning

    def test_missing_env_passes(self, tmp_path):
        """A missing .env → PASS (env_not_committed handles absence separately)."""
        from scripts.preflight_check import check_env_no_duplicate_keys
        # tmp_path has no .env
        with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
            r = check_env_no_duplicate_keys()
        assert r.passed
        assert not r.warning


# ---------------------------------------------------------------------------
# Key-rotation checks (Stage 3 — 2026-06-26 cleanup)
# ---------------------------------------------------------------------------

class TestKeyRotationChecks:
    """Tests for check_key_rotation_recent and check_alpaca_key_rotation_recent.

    Both are warning-only (never blocking). Both pass with a warning when the
    date is unset or invalid. Both fail (well, warn) when the date is stale.
    The Alpaca check is also auto-skipped under ADVISORY_ONLY=True.
    """

    def test_fred_rotation_unset_warns(self, tmp_path):
        """Unset FRED_KEY_ROTATED_DATE → warning-level PASS."""
        from scripts.preflight_check import check_key_rotation_recent
        s = _settings(tmp_path, FRED_KEY_ROTATED_DATE=None)
        with patch("scripts.preflight_check.settings", s):
            r = check_key_rotation_recent()
        assert r.passed
        assert r.warning
        assert r.name == "key_rotation_recent"
        assert r.reason  # non-empty message

    def test_fred_rotation_fresh_passes(self, tmp_path):
        """FRED key rotated 30 days ago → clean PASS (no warning)."""
        from scripts.preflight_check import check_key_rotation_recent
        fresh = (date.today() - timedelta(days=30)).isoformat()
        s = _settings(tmp_path, FRED_KEY_ROTATED_DATE=fresh)
        with patch("scripts.preflight_check.settings", s):
            r = check_key_rotation_recent()
        assert r.passed
        assert not r.warning
        assert "30" in r.reason

    def test_fred_rotation_stale_warns(self, tmp_path):
        """FRED key rotated 100 days ago → warning-level PASS (never blocking)."""
        from scripts.preflight_check import check_key_rotation_recent
        stale = (date.today() - timedelta(days=100)).isoformat()
        s = _settings(tmp_path, FRED_KEY_ROTATED_DATE=stale)
        with patch("scripts.preflight_check.settings", s):
            r = check_key_rotation_recent(max_age_days=90)
        assert r.passed  # warning-only — NEVER False
        assert r.warning
        assert "100" in r.reason

    def test_alpaca_rotation_unset_warns(self, tmp_path):
        """Unset ALPACA_KEY_ROTATED_DATE → warning-level PASS."""
        from scripts.preflight_check import check_alpaca_key_rotation_recent
        s = _settings(tmp_path, ALPACA_KEY_ROTATED_DATE=None)
        with patch("scripts.preflight_check.settings", s):
            r = check_alpaca_key_rotation_recent()
        assert r.passed
        assert r.warning
        assert r.name == "alpaca_key_rotation_recent"
        assert r.reason

    def test_alpaca_rotation_stale_warns(self, tmp_path):
        """Stale Alpaca key → warning-level PASS (never blocking)."""
        from scripts.preflight_check import check_alpaca_key_rotation_recent
        stale = (date.today() - timedelta(days=120)).isoformat()
        s = _settings(tmp_path, ALPACA_KEY_ROTATED_DATE=stale)
        with patch("scripts.preflight_check.settings", s):
            r = check_alpaca_key_rotation_recent(max_age_days=90)
        assert r.passed  # warning-only — NEVER False
        assert r.warning
        assert "120" in r.reason

    def test_alpaca_rotation_fresh_passes(self, tmp_path):
        """Alpaca key rotated 30 days ago → clean PASS (no warning)."""
        from scripts.preflight_check import check_alpaca_key_rotation_recent
        fresh = (date.today() - timedelta(days=30)).isoformat()
        s = _settings(tmp_path, ALPACA_KEY_ROTATED_DATE=fresh)
        with patch("scripts.preflight_check.settings", s):
            r = check_alpaca_key_rotation_recent(max_age_days=90)
        assert r.passed
        assert not r.warning
        assert "30" in r.reason

    def test_alpaca_rotation_invalid_iso_warns(self, tmp_path):
        """Invalid ALPACA_KEY_ROTATED_DATE format → warning-level PASS (never blocking)."""
        from scripts.preflight_check import check_alpaca_key_rotation_recent
        s = _settings(tmp_path, ALPACA_KEY_ROTATED_DATE="not-a-date")
        with patch("scripts.preflight_check.settings", s):
            r = check_alpaca_key_rotation_recent()
        assert r.passed  # warning-only — NEVER False
        assert r.warning
        assert "invalid" in r.reason.lower() or "format" in r.reason.lower()

    def test_alpaca_rotation_auto_skipped_advisory_mode(self, tmp_path):
        """alpaca_key_rotation_recent is auto-skipped under ADVISORY_ONLY=True."""
        from scripts.preflight_check import run_checks
        s = _settings(tmp_path, ADVISORY_ONLY=True)
        with patch("scripts.preflight_check.settings", s):
            with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
                results = run_checks()
        result = next(r for r in results if r.name == "alpaca_key_rotation_recent")
        assert result.passed
        assert "skipped" in result.reason.lower()
        assert "ADVISORY_ONLY" in result.reason


# ---------------------------------------------------------------------------
# advisory-mode auto-skip (Stage 2 — 2026-06-26 cleanup)
# ---------------------------------------------------------------------------

class TestAdvisoryModeAutoSkip:
    """ADVISORY_ONLY=True auto-skips heartbeat_fresh and validation_reports.

    These two checks would be false-positive failures in advisory mode:
    - ``heartbeat_fresh``: the heartbeat is written only by main_orchestrator.py;
      advisory runs via main.py do not require a persistent orchestrator process.
    - ``validation_reports``: validation reports gate live order submission;
      advisory mode produces signals only (no orders submitted to brokers).

    Both checks must revert to their real pass/fail logic when ADVISORY_ONLY=False.
    """

    def test_heartbeat_skipped_when_advisory_true(self, tmp_path):
        """heartbeat_fresh is auto-skipped (PASS) when ADVISORY_ONLY=True.

        tmp_path has no heartbeat.txt so the check would FAIL if it ran — the
        skip intercepts it before the file-access logic executes.
        """
        from scripts.preflight_check import run_checks
        s = _settings(tmp_path, ADVISORY_ONLY=True)
        with patch("scripts.preflight_check.settings", s):
            with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
                results = run_checks()
        hb = next(r for r in results if r.name == "heartbeat_fresh")
        assert hb.passed, f"Expected PASS (skipped), got FAIL: {hb.reason}"
        assert "skipped" in hb.reason.lower()
        assert "ADVISORY_ONLY" in hb.reason

    def test_validation_reports_skipped_when_advisory_true(self, tmp_path):
        """validation_reports is auto-skipped (PASS) when ADVISORY_ONLY=True.

        tmp_path has no reports/ dir so the check would FAIL if it ran.
        """
        from scripts.preflight_check import run_checks
        s = _settings(tmp_path, ADVISORY_ONLY=True)
        with patch("scripts.preflight_check.settings", s):
            with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
                results = run_checks()
        vr = next(r for r in results if r.name == "validation_reports")
        assert vr.passed, f"Expected PASS (skipped), got FAIL: {vr.reason}"
        assert "skipped" in vr.reason.lower()
        assert "ADVISORY_ONLY" in vr.reason

    def test_heartbeat_runs_real_logic_when_advisory_false(self, tmp_path):
        """heartbeat_fresh runs and FAILs when ADVISORY_ONLY=False and file is missing."""
        from scripts.preflight_check import run_checks
        s = _settings(tmp_path, ADVISORY_ONLY=False)
        with patch("scripts.preflight_check.settings", s):
            with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
                results = run_checks()
        hb = next(r for r in results if r.name == "heartbeat_fresh")
        assert not hb.passed, "Expected FAIL — no heartbeat.txt in tmp_path"
        assert "not found" in hb.reason

    def test_validation_reports_runs_real_logic_when_advisory_false(self, tmp_path):
        """validation_reports runs and FAILs when ADVISORY_ONLY=False and no reports exist."""
        from scripts.preflight_check import run_checks
        s = _settings(tmp_path, ADVISORY_ONLY=False)
        with patch("scripts.preflight_check.settings", s):
            with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
                results = run_checks()
        vr = next(r for r in results if r.name == "validation_reports")
        assert not vr.passed, "Expected FAIL — no reports/ dir in tmp_path"

    def test_skip_reasons_are_distinct_per_check(self, tmp_path):
        """Each auto-skipped check has a unique reason string (not a generic message)."""
        from scripts.preflight_check import _ADVISORY_AUTO_SKIP
        reasons = list(_ADVISORY_AUTO_SKIP.values())
        assert len(reasons) == len(set(reasons)), "Every auto-skip check must have a unique reason"
        # All eight auto-skip entries must be present
        for name in (
            "alpaca_configured", "alpaca_paper_mode", "dry_run_disabled",
            "paper_trading_duration", "alpaca_key_rotation_recent",
            "heartbeat_fresh", "validation_reports", "no_unexpected_risk_blocks",
        ):
            assert name in _ADVISORY_AUTO_SKIP, f"{name} missing from _ADVISORY_AUTO_SKIP"

    def test_original_broker_checks_still_skipped(self, tmp_path):
        """All seven auto-skip checks are PASS under ADVISORY_ONLY=True."""
        from scripts.preflight_check import run_checks, _ADVISORY_AUTO_SKIP
        s = _settings(tmp_path, ADVISORY_ONLY=True)
        with patch("scripts.preflight_check.settings", s):
            with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
                results = run_checks()
        result_by_name = {r.name: r for r in results}
        for check_name in _ADVISORY_AUTO_SKIP:
            r = result_by_name.get(check_name)
            assert r is not None, f"{check_name} missing from run_checks output"
            assert r.passed, f"{check_name} should be skipped (PASS) under advisory mode"
            assert "skipped" in r.reason.lower(), f"{check_name} skip reason unclear: {r.reason}"


# ---------------------------------------------------------------------------
# run_checks + --skip
# ---------------------------------------------------------------------------

class TestRunChecks:
    """``run_checks()`` collects one ``CheckResult`` per check in ``ALL_CHECKS``."""

    def test_skip_named_check(self, tmp_path):
        """Skipped checks appear in the result list as PASS with "(skipped)" reason.

        The result list always has ``len(ALL_CHECKS)`` entries regardless of
        the skip list so callers can zip it with ``ALL_CHECKS`` safely.
        """
        from scripts.preflight_check import run_checks
        s = _settings(tmp_path)
        with patch("scripts.preflight_check.settings", s):
            with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
                results = run_checks(skip=["paper_trading_duration", "heartbeat_fresh"])
        names = [r.name for r in results]
        assert "paper_trading_duration" in names
        assert "heartbeat_fresh" in names
        for r in results:
            if r.name in ("paper_trading_duration", "heartbeat_fresh"):
                assert r.passed
                assert "skipped" in r.reason.lower()

    def test_returns_result_for_every_check(self, tmp_path):
        """Skipping all checks still produces one result per check in ``ALL_CHECKS``."""
        from scripts.preflight_check import run_checks, ALL_CHECKS
        s = _settings(tmp_path)
        all_names = [fn.__name__.replace("check_", "") for fn in ALL_CHECKS]
        with patch("scripts.preflight_check.settings", s):
            with patch("scripts.preflight_check._REPO_ROOT", tmp_path):
                results = run_checks(skip=all_names)
        assert len(results) == len(ALL_CHECKS)


# ---------------------------------------------------------------------------
# main() exit code
# ---------------------------------------------------------------------------

class TestMainExitCode:
    """``main()`` returns 0 only when all checks pass; 1 if any fail."""

    def test_exits_0_when_all_pass(self, tmp_path):
        """Skipping all checks (all pass) → exit code 0.

        This verifies the plumbing from ``run_checks`` results to the return
        value without requiring a real environment.
        """
        from scripts.preflight_check import main, ALL_CHECKS
        skip_all = [fn.__name__.replace("check_", "") for fn in ALL_CHECKS]
        code = main(["--skip"] + skip_all)
        assert code == 0

    def test_exits_1_when_any_fail(self, tmp_path):
        """A single FAIL in the result set → exit code 1.

        We patch ``run_checks`` (not a specific ``check_*`` function) because
        ``ALL_CHECKS`` holds direct function references captured at import time;
        patching the attribute on the module after import does not affect the
        list.  Patching ``run_checks`` itself is the cleanest way to inject a
        known-failing result.
        """
        from scripts.preflight_check import main, CheckResult

        def _one_fail(skip=None):
            return [CheckResult("fred_key_configured", False, "forced fail")]

        with patch("scripts.preflight_check.run_checks", _one_fail):
            code = main([])
        assert code == 1

    def test_json_output_format(self, tmp_path, capsys):
        """``--json`` flag → valid JSON array with required keys per entry.

        Machine consumers (e.g. a CI pipeline parsing the output) rely on
        the ``name``, ``passed``, and ``reason`` keys being present in every
        object in the array.
        """
        from scripts.preflight_check import main, ALL_CHECKS
        skip_all = [fn.__name__.replace("check_", "") for fn in ALL_CHECKS]
        main(["--json", "--skip"] + skip_all)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert all("name" in d and "passed" in d and "reason" in d for d in data)


# ---------------------------------------------------------------------------
# state_snapshot_fresh
# ---------------------------------------------------------------------------

class TestStateSnapshotFresh:
    """check_state_snapshot_fresh — cross-mode liveness indicator.

    Both main.py (advisory) and main_orchestrator.py write state_snapshot.json
    so this check is meaningful in both deployment modes.  It is NOT in
    _ADVISORY_AUTO_SKIP, unlike heartbeat_fresh.
    """

    def test_passes_when_snapshot_is_recent(self, tmp_path):
        """A snapshot with a current UTC timestamp passes within the age limit."""
        from scripts.preflight_check import check_state_snapshot_fresh
        snapshot = tmp_path / "state_snapshot.json"
        snapshot.write_text(
            json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}),
            encoding="utf-8",
        )
        s = _settings(tmp_path, OUTPUT_DIR=tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_state_snapshot_fresh(max_age_hours=2.0)
        assert r.passed
        assert r.name == "state_snapshot_fresh"

    def test_fails_when_snapshot_is_stale(self, tmp_path):
        """A snapshot older than max_age_hours fails with age info in the reason."""
        from scripts.preflight_check import check_state_snapshot_fresh
        snapshot = tmp_path / "state_snapshot.json"
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        snapshot.write_text(
            json.dumps({"timestamp": stale_ts}),
            encoding="utf-8",
        )
        s = _settings(tmp_path, OUTPUT_DIR=tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_state_snapshot_fresh(max_age_hours=2.0)
        assert not r.passed
        assert "old" in r.reason.lower()

    def test_fails_when_snapshot_is_missing(self, tmp_path):
        """No state_snapshot.json file → FAIL with 'not found' in reason."""
        from scripts.preflight_check import check_state_snapshot_fresh
        s = _settings(tmp_path, OUTPUT_DIR=tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_state_snapshot_fresh()
        assert not r.passed
        assert "not found" in r.reason

    def test_falls_back_to_mtime_when_no_timestamp_field(self, tmp_path):
        """When 'timestamp' is absent, file mtime is used as fallback."""
        from scripts.preflight_check import check_state_snapshot_fresh
        import os, time
        snapshot = tmp_path / "state_snapshot.json"
        snapshot.write_text(json.dumps({"signals": []}), encoding="utf-8")
        # mtime is seconds-ago fresh, so this should pass
        s = _settings(tmp_path, OUTPUT_DIR=tmp_path)
        with patch("scripts.preflight_check.settings", s):
            r = check_state_snapshot_fresh(max_age_hours=2.0)
        assert r.passed

    def test_not_in_advisory_auto_skip(self):
        """state_snapshot_fresh must NOT be auto-skipped in advisory mode —
        it is the advisory liveness check."""
        from scripts.preflight_check import _ADVISORY_AUTO_SKIP
        assert "state_snapshot_fresh" not in _ADVISORY_AUTO_SKIP


# ---------------------------------------------------------------------------
# _ADVISORY_AUTO_SKIP expanded set
# ---------------------------------------------------------------------------

class TestAdvisoryAutoSkip:
    """Verify that all 8 expected checks are in _ADVISORY_AUTO_SKIP.

    Four broker-dependent checks were always there (alpaca_configured,
    alpaca_paper_mode, dry_run_disabled, paper_trading_duration).  Stage 2
    added three advisory false-positive checks to eliminate spurious failures
    on a correctly-running advisory deployment.  Stage 3 added a fifth
    broker-dependent check (alpaca_key_rotation_recent), bringing the total
    to 8 (5 broker + 3 false-positives).
    """

    def test_original_broker_checks_present(self):
        """The original four broker-dependent checks are still auto-skipped."""
        from scripts.preflight_check import _ADVISORY_AUTO_SKIP
        broker_checks = {
            "alpaca_configured",
            "alpaca_paper_mode",
            "dry_run_disabled",
            "paper_trading_duration",
        }
        assert broker_checks.issubset(set(_ADVISORY_AUTO_SKIP))

    def test_heartbeat_fresh_in_auto_skip(self):
        """heartbeat_fresh is auto-skipped because main.py (advisory) does not write it."""
        from scripts.preflight_check import _ADVISORY_AUTO_SKIP
        assert "heartbeat_fresh" in _ADVISORY_AUTO_SKIP

    def test_validation_reports_in_auto_skip(self):
        """validation_reports gates live deployment, not advisory operation."""
        from scripts.preflight_check import _ADVISORY_AUTO_SKIP
        assert "validation_reports" in _ADVISORY_AUTO_SKIP

    def test_no_unexpected_risk_blocks_in_auto_skip(self):
        """no_unexpected_risk_blocks is irrelevant in advisory mode (no orders)."""
        from scripts.preflight_check import _ADVISORY_AUTO_SKIP
        assert "no_unexpected_risk_blocks" in _ADVISORY_AUTO_SKIP

    def test_auto_skip_has_eight_entries(self):
        """Exactly 8 checks are in _ADVISORY_AUTO_SKIP (5 broker + 3 false-positives)."""
        from scripts.preflight_check import _ADVISORY_AUTO_SKIP
        assert len(_ADVISORY_AUTO_SKIP) == 8

    def test_state_snapshot_not_auto_skipped(self):
        """state_snapshot_fresh must remain active in advisory mode (it IS the liveness check)."""
        from scripts.preflight_check import _ADVISORY_AUTO_SKIP
        assert "state_snapshot_fresh" not in _ADVISORY_AUTO_SKIP

    def test_advisory_auto_skip_applied_by_run_checks(self, tmp_path):
        """run_checks marks all _ADVISORY_AUTO_SKIP checks as PASS when ADVISORY_ONLY=True."""
        from scripts.preflight_check import run_checks, _ADVISORY_AUTO_SKIP
        s = _settings(tmp_path)
        s.ADVISORY_ONLY = True
        with patch("scripts.preflight_check.settings", s):
            results = run_checks(skip=[
                "fred_key_configured",
                "key_rotation_recent",
                "advisory_only_active",
                "macro_regime_gate_enabled",
                "env_not_committed",
                "kill_switch_inactive",
                "state_snapshot_fresh",
                "db_exists",
            ])
        by_name = {r.name: r for r in results}
        for name in _ADVISORY_AUTO_SKIP:
            assert by_name[name].passed, f"Expected {name} to be auto-skipped (PASS)"
            assert "ADVISORY_ONLY" in by_name[name].reason

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

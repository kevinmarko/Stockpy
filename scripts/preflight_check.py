"""
scripts/preflight_check.py
==========================
Programmatic pre-live readiness gate for the InvestYo platform.

Purpose
-------
This script is the machine-enforceable complement to ``docs/GO_LIVE_CHECKLIST.md``.
It encodes the subset of checklist items that can be verified programmatically
and exits with code **0** only when ALL checks pass, making it safe to wire as
a CI gate or a git pre-commit hook on the ``prod`` branch.

Design principles
-----------------
* **Fail-closed.**  A check that *errors* internally produces a FAIL result,
  not a PASS.  An exception inside ``check_*()`` is caught by ``run_checks()``
  and becomes ``CheckResult(name, passed=False, reason="Check raised: …")``.
  This means a misconfigured environment (e.g. broken Python path) surfaces as
  a failing gate, not a silently passing one.

* **Warning vs blocking.**  ``CheckResult.warning=True`` means the check is
  informational: it surfaces prominently in the output table but does NOT count
  toward the overall fail count.  Currently only ``check_alpaca_paper_mode``
  uses this: ``ALPACA_PAPER=False`` is a deliberate operator decision for live
  trading, not a mistake, so it warns rather than blocks.

* **Skippable.**  Any check can be excluded via ``--skip <name>`` for CI
  environments that legitimately cannot satisfy a particular check (e.g. a
  fresh clone has no heartbeat file).  Skipped checks are marked as PASS with
  reason "(skipped via --skip)" so the result set always has one entry per
  check regardless.

* **Advisory-mode auto-skip.**  When ``settings.ADVISORY_ONLY=True`` the checks
  listed in ``_ADVISORY_AUTO_SKIP`` are automatically marked PASS (with a clear
  "skipped: ADVISORY_ONLY" reason) because they are either broker-dependent or
  have no meaningful signal when no orders are submitted.  This prevents false-
  positive failures on a correctly-running advisory deployment:
    - Broker-dependent checks (4): alpaca_configured, alpaca_paper_mode,
      dry_run_disabled, paper_trading_duration.
    - Advisory false-positives (3): heartbeat_fresh (main.py does not write
      the heartbeat file — only main_orchestrator.py does), validation_reports
      (strategy validation reports are a go-live gate, not advisory health),
      no_unexpected_risk_blocks (risk-gate blocks only occur on order submission,
      which never happens in advisory mode).

* **No side effects.**  Checks are read-only.  They inspect files, environment
  variables, and database state but never write, modify, or delete anything.

Usage
-----
    python scripts/preflight_check.py                     # full check (human-readable table)
    python scripts/preflight_check.py --json              # machine-readable JSON array
    python scripts/preflight_check.py --skip heartbeat_fresh paper_trading_duration

Checks (15 total)
------
 1. fred_key_configured         — FRED_API_KEY is set and is not the known-
                                  compromised value (detected via settings.fred_key_is_leaked).
 2. key_rotation_recent         — FRED_API_KEY was rotated within the last 90
                                  days (FRED_KEY_ROTATED_DATE in .env).
                                  Warning-only; never blocking.  ALPACA keys
                                  are NOT checked (no blast radius in advisory
                                  mode).
 3. advisory_only_active        — settings.ADVISORY_ONLY=True (Tier 5.1
                                  quarantine).  When True, the broker-readiness
                                  checks (alpaca_configured / alpaca_paper_mode
                                  / dry_run_disabled / paper_trading_duration)
                                  and advisory false-positive checks (heartbeat_
                                  fresh / validation_reports / no_unexpected_
                                  risk_blocks) are auto-skipped.  Warning-only
                                  when False (live broker stack is in scope).
 4. alpaca_configured           — ALPACA_API_KEY + ALPACA_SECRET_KEY are present.
                                  SKIPPED when ADVISORY_ONLY=True.
 5. macro_regime_gate_enabled   — MACRO_REGIME_GATE_ENABLED=True when live trading.
                                  Warning-only in paper mode; blocking when
                                  ALPACA_PAPER=False + gate disabled.
 6. alpaca_paper_mode           — ALPACA_PAPER=True.  Warning-only when False.
                                  SKIPPED when ADVISORY_ONLY=True.
 7. dry_run_disabled            — DRY_RUN=False (orders reach the broker).
                                  SKIPPED when ADVISORY_ONLY=True.
 8. env_not_committed           — .env file is git-untracked (``git ls-files``).
 9. kill_switch_inactive        — The KILL_SWITCH sentinel file does not exist.
10. state_snapshot_fresh        — output/state_snapshot.json exists and its
                                  embedded timestamp is < 2 hours old.  Both
                                  main.py (advisory) and main_orchestrator.py
                                  write this file, making it the cross-mode
                                  liveness indicator.  NOT auto-skipped in
                                  advisory mode (it IS the advisory liveness
                                  check).
11. heartbeat_fresh             — output/heartbeat.txt was updated within 2 hours.
                                  SKIPPED when ADVISORY_ONLY=True because main.py
                                  does not write this file — only the full async
                                  orchestrator does.
12. db_exists                   — quant_platform.db exists and is non-empty.
13. paper_trading_duration      — Paper-trading started ≥ 90 days ago
                                  (requires PAPER_TRADING_START_DATE in .env).
                                  SKIPPED when ADVISORY_ONLY=True (no broker
                                  → no paper-trading clock).
14. validation_reports          — Every *_validation_summary.json in reports/ is
                                  deployable=True and dated within 30 days.
                                  SKIPPED when ADVISORY_ONLY=True (validation
                                  reports gate live deployment, not advisory op).
15. no_unexpected_risk_blocks   — No "minimum_validation" risk gate blocks in the
                                  last 24 hours.  SKIPPED when ADVISORY_ONLY=True
                                  (no order submissions → no risk-gate blocks).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Resolve repo root so this script can be ``python scripts/preflight_check.py``-ed
# from any working directory without requiring the venv to be on PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from settings import settings


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Outcome of a single preflight check.

    Attributes
    ----------
    name:
        Machine-readable check identifier (matches the ``check_*`` function
        name with the ``check_`` prefix stripped).
    passed:
        True = PASS (or skipped), False = FAIL.
    reason:
        Human-readable explanation shown in the output table and included in
        the JSON output.  Should be a complete sentence with enough context to
        fix the problem without needing to read this script.
    warning:
        When True, the check is informational only: it is shown in the table
        with a ⚠️ icon but does NOT contribute to the overall fail count.
        The overall exit code is still 0 if ``warning=True`` checks are the
        only non-PASS entries.
    """
    name: str
    passed: bool
    reason: str
    warning: bool = False


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------
# Each function returns a single ``CheckResult``.  The convention is:
#   * One ``name`` variable at the top (avoids typos in the return values).
#   * Returns immediately on the first problem found (no multiple-issue
#     accumulation within a single check — that belongs in separate checks).
#   * Never raises; if a sub-operation can fail, wrap it in try/except.

def check_fred_key_configured() -> CheckResult:
    """Verify that FRED_API_KEY is set and has not been compromised.

    ``settings.fred_key_is_leaked`` is a property that compares the configured
    key against a set of known-compromised values (e.g. keys that appeared in
    public GitHub repositories).  It is NOT a connectivity test — that would
    require a live FRED API call which is too slow for a preflight gate.
    """
    name = "fred_key_configured"
    key = settings.FRED_API_KEY
    if not key:
        return CheckResult(name, False, "FRED_API_KEY is not set in .env")
    if settings.fred_key_is_leaked:
        return CheckResult(
            name, False,
            "FRED_API_KEY matches the known-compromised leaked value — rotate immediately",
        )
    return CheckResult(name, True, "FRED_API_KEY is configured and not the leaked value")


def check_key_rotation_recent(max_age_days: int = 90) -> CheckResult:
    """Warn if FRED_API_KEY has not been rotated within the recommended window.

    Advisory-only operators rely on FRED for macroeconomic regime data even when
    no orders are submitted.  Rotating credentials every 90 days limits the blast
    radius if a key leaks from logs or a shared ``.env`` file.

    This check is **warning-only** (never blocking) because a stale rotation date
    does not prevent the platform from running; it is a hygiene reminder.

    If ``FRED_KEY_ROTATED_DATE`` is unset the check still passes with a warning so
    the operator is prompted to start tracking the rotation date — it does NOT fail
    because the field is optional and not set in existing deployments.

    ``ALPACA_KEY_ROTATED_DATE`` is intentionally NOT checked here: Alpaca paper keys
    have no blast-radius risk in advisory mode, and paper → live migration (which
    would make them sensitive) is handled by the ``advisory_only_active`` gate.
    """
    name = "key_rotation_recent"
    rotated_str = getattr(settings, "FRED_KEY_ROTATED_DATE", None)
    if not rotated_str:
        return CheckResult(
            name, True,
            "⚠️  FRED_KEY_ROTATED_DATE not set in .env — consider adding it after "
            "your next key rotation so the 90-day reminder can track age. "
            "Set at https://fred.stlouisfed.org/docs/api/api_key.html",
            warning=True,
        )
    try:
        rotated = date.fromisoformat(rotated_str)
    except ValueError:
        return CheckResult(
            name, True,
            f"⚠️  FRED_KEY_ROTATED_DATE has invalid format {rotated_str!r} "
            "(expected YYYY-MM-DD). Cannot check rotation age.",
            warning=True,
        )
    age_days = (date.today() - rotated).days
    if age_days > max_age_days:
        return CheckResult(
            name, True,
            f"⚠️  FRED_API_KEY was last rotated {age_days} days ago "
            f"(limit {max_age_days} days). Consider rotating at "
            "https://fred.stlouisfed.org/docs/api/api_key.html and updating "
            "FRED_KEY_ROTATED_DATE in .env.",
            warning=True,
        )
    return CheckResult(
        name, True,
        f"FRED_API_KEY rotated {age_days} days ago (within {max_age_days}-day window)",
    )


def check_advisory_only_active() -> CheckResult:
    """Verify that ADVISORY_ONLY mode is active (Tier 5.1 quarantine).

    When ``settings.ADVISORY_ONLY`` is True (the project default), the broker
    surface is quarantined: ``main_orchestrator._execute_broker_orders`` is a
    no-op, the GUI mode toggle is disabled, and broker credentials need not be
    configured.  This check passes loudly so the operator sees the quarantine
    in the readiness table.

    When ``ADVISORY_ONLY`` is False the broker stack is live; we emit a
    *warning-level* PASS so the operator confirms they intentionally lifted
    the quarantine.  Other broker-readiness checks (``alpaca_configured``,
    ``alpaca_paper_mode``, ``dry_run_disabled``, ``paper_trading_duration``)
    then run; under ADVISORY_ONLY=True they are skipped by ``run_checks``.
    """
    name = "advisory_only_active"
    if getattr(settings, "ADVISORY_ONLY", True):
        return CheckResult(
            name, True,
            "ADVISORY_ONLY=True — broker execution surface is quarantined. "
            "Pipeline produces signals + reports only.",
        )
    return CheckResult(
        name, True,
        "⚠️  ADVISORY_ONLY=False — broker execution surface is LIVE. "
        "Confirm this is intentional and that downstream broker checks pass.",
        warning=True,
    )


def check_alpaca_configured() -> CheckResult:
    """Verify that broker credentials are present.

    Both API key and secret must be set; a key without a secret is not usable.
    If neither is set, the orchestrator silently skips broker execution, which
    is acceptable during development but not before going live.
    """
    name = "alpaca_configured"
    if not settings.ALPACA_API_KEY or not settings.ALPACA_SECRET_KEY:
        return CheckResult(
            name, False,
            "ALPACA_API_KEY and/or ALPACA_SECRET_KEY are not set in .env — "
            "broker execution will be skipped",
        )
    return CheckResult(name, True, "ALPACA_API_KEY and ALPACA_SECRET_KEY are configured")


def check_macro_regime_gate_enabled() -> CheckResult:
    """Fail if the macro regime gate is disabled while live trading is configured.

    ``MACRO_REGIME_GATE_ENABLED=false`` is an operator override for hybrid mode
    (technical signals run without macro veto) and is acceptable in paper trading.
    It is a **blocking** failure if both live trading (``ALPACA_PAPER=false``) and
    ``MACRO_REGIME_GATE_ENABLED=false`` are active simultaneously — that combination
    exposes the live account to unprotected BUY orders during a recession.
    """
    name = "macro_regime_gate_enabled"
    try:
        gate_enabled = settings.MACRO_REGIME_GATE_ENABLED
        alpaca_paper = settings.ALPACA_PAPER
    except Exception as exc:
        return CheckResult(name, False, f"Check raised: {exc}")
    if not gate_enabled and not alpaca_paper:
        return CheckResult(
            name, False,
            "MACRO_REGIME_GATE_ENABLED=false AND ALPACA_PAPER=false — live trading "
            "without the macro regime veto is not allowed.  Re-enable the gate "
            "in .env or switch back to paper mode.",
        )
    if not gate_enabled:
        return CheckResult(
            name, True,
            "⚠️  MACRO_REGIME_GATE_ENABLED=false — macro regime veto is disabled "
            "(hybrid mode).  Acceptable in paper trading; re-enable before going live.",
            warning=True,
        )
    return CheckResult(name, True, "Macro regime gate is enabled (autonomous mode)")


def check_alpaca_paper_mode() -> CheckResult:
    """Warn (do not fail) if live trading mode is detected.

    ``ALPACA_PAPER=False`` is the intentional configuration for live trading,
    so it must not block the gate — but it warrants a loud banner so the
    operator can confirm it was set deliberately rather than accidentally.

    This is one of only two warning-level checks (the other being future
    candidate checks for capital sizing).
    """
    name = "alpaca_paper_mode"
    if not settings.ALPACA_PAPER:
        return CheckResult(
            name, True,
            "⚠️  ALPACA_PAPER=False — you are configured for LIVE TRADING. "
            "Confirm this is intentional.",
            warning=True,
        )
    return CheckResult(name, True, "ALPACA_PAPER=True (paper-trading mode)")


def check_dry_run_disabled() -> CheckResult:
    """Verify that DRY_RUN is False so orders reach the broker.

    ``DRY_RUN=True`` is set during development and integration testing to
    exercise the order pipeline without submitting to Alpaca.  It must be
    False before going live; leaving it True silently produces no fills.
    """
    name = "dry_run_disabled"
    if settings.DRY_RUN:
        return CheckResult(
            name, False,
            "DRY_RUN=True — orders will NOT be submitted to the broker. "
            "Set DRY_RUN=false in .env for live operation.",
        )
    return CheckResult(name, True, "DRY_RUN=False")


def check_env_not_committed() -> CheckResult:
    """Verify that .env is not tracked by git.

    Uses ``git ls-files --error-unmatch .env`` rather than parsing
    ``.gitignore`` because ``.gitignore`` rules can be overridden by
    ``git add -f`` and do not account for global gitignore files.  The
    ``git ls-files`` output is authoritative: it reflects what git currently
    knows, regardless of how .gitignore is configured.

    ``FileNotFoundError`` from ``subprocess.run`` means ``git`` is not on
    PATH (unusual but possible in minimal CI containers); the sub-check is
    skipped silently in that case since we cannot make a definitive
    determination.
    """
    name = "env_not_committed"
    env_file = _REPO_ROOT / ".env"
    if not env_file.exists():
        return CheckResult(
            name, False,
            ".env file not found — create it from .env.example and populate secrets",
        )
    import subprocess
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".env"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Exit 0 means git found .env in the index — it is tracked.
            return CheckResult(
                name, False,
                ".env is tracked by git — remove it with `git rm --cached .env` "
                "and add to .gitignore immediately",
            )
    except FileNotFoundError:
        pass  # git not available — cannot determine tracking status; skip sub-check
    return CheckResult(name, True, ".env exists and is not git-tracked")


def check_kill_switch_inactive() -> CheckResult:
    """Verify that the global kill switch is not active.

    An active kill switch means ``OrderManager.submit_order_with_idempotency``
    will raise ``KillSwitchActiveError`` before contacting the broker.
    The orchestrator must not be started while the kill switch is active, as it
    will fail immediately on the first order submission.

    ``GlobalKillSwitch()`` is imported lazily (inside the function) so that
    tests can patch ``execution.kill_switch.KILL_SWITCH_FILE`` without
    importing the module at the module level here.
    """
    name = "kill_switch_inactive"
    from execution.kill_switch import GlobalKillSwitch
    ks = GlobalKillSwitch()
    if ks.is_active():
        return CheckResult(
            name, False,
            f"Kill switch is ACTIVE — deactivate with: "
            f"python -m execution.kill_switch --deactivate  "
            f"(reason: {ks.reason() or '(none)'})",
        )
    return CheckResult(name, True, "Kill switch is inactive")


def check_state_snapshot_fresh(max_age_hours: float = 2.0) -> CheckResult:
    """Verify that the pipeline state snapshot was written recently.

    Both ``main.py`` (advisory) and ``main_orchestrator.py`` (full pipeline)
    write ``OUTPUT_DIR/state_snapshot.json`` at the end of every run.  The file
    carries an ISO 8601 UTC ``timestamp`` field that this check reads to compute
    the snapshot age.  File mtime is used as a fallback when the ``timestamp``
    field is absent (e.g., written by an older version of the platform).

    This is the cross-mode liveness indicator: it is meaningful in advisory mode
    (where ``main.py`` does NOT write ``heartbeat.txt``) AND in full-pipeline
    mode (where ``main_orchestrator.py`` writes both).  It is therefore NOT in
    ``_ADVISORY_AUTO_SKIP`` — it is the one check that replaces ``heartbeat_fresh``
    as the liveness gate when running in advisory mode.
    """
    name = "state_snapshot_fresh"
    snapshot_path = settings.OUTPUT_DIR / "state_snapshot.json"
    if not snapshot_path.exists():
        return CheckResult(
            name, False,
            "output/state_snapshot.json not found — has the pipeline been run recently? "
            "Run: python3 main.py  (advisory)  or  python3 main_orchestrator.py",
        )
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        ts_str = data.get("timestamp", "")
        if ts_str:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - ts
        else:
            mtime = snapshot_path.stat().st_mtime
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(mtime, tz=timezone.utc)
        if age > timedelta(hours=max_age_hours):
            return CheckResult(
                name, False,
                f"State snapshot is {age.total_seconds()/3600:.1f}h old (limit {max_age_hours}h) — "
                "pipeline may be down; run python3 main.py to refresh",
            )
        return CheckResult(
            name, True,
            f"State snapshot is {age.total_seconds()/60:.0f} min old",
        )
    except Exception as exc:
        return CheckResult(name, False, f"Could not read state snapshot: {exc}")


def check_heartbeat_fresh(max_age_hours: float = 2.0) -> CheckResult:
    """Verify that the orchestrator heartbeat file was updated recently.

    ``main_orchestrator._heartbeat()`` writes ``OUTPUT_DIR/heartbeat.txt``
    every 60 seconds as an ISO UTC timestamp.  A stale heartbeat indicates the
    orchestrator is not running (crashed, killed, or never started) and the
    pipeline is producing no new signals.

    A missing heartbeat file is treated as a failure rather than a warning
    because it means either the orchestrator has never been run (go-live
    requires at least one successful run to confirm the pipeline works end-to-end)
    or the output directory is misconfigured.

    The ``max_age_hours`` parameter is exposed for testing purposes; the
    default 2-hour window is conservative enough to survive a scheduled
    maintenance window while tight enough to catch a genuine crash.
    """
    name = "heartbeat_fresh"
    hb_path = settings.OUTPUT_DIR / "heartbeat.txt"
    if not hb_path.exists():
        return CheckResult(
            name, False,
            "output/heartbeat.txt not found — has the orchestrator been run recently? "
            "Run: python3 main_orchestrator.py",
        )
    try:
        ts_str = hb_path.read_text(encoding="utf-8").strip()
        ts = datetime.fromisoformat(ts_str)
        # The heartbeat writer always uses UTC; strip naive timestamps defensively.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts
        if age > timedelta(hours=max_age_hours):
            return CheckResult(
                name, False,
                f"Heartbeat is {age.total_seconds()/3600:.1f}h old (limit {max_age_hours}h) — "
                "orchestrator may be down",
            )
        return CheckResult(name, True, f"Heartbeat is {age.total_seconds()/60:.0f} min old")
    except Exception as exc:
        return CheckResult(name, False, f"Could not parse heartbeat timestamp: {exc}")


def check_db_exists() -> CheckResult:
    """Verify that the SQLite database exists and is non-empty.

    An empty file (0 bytes) indicates that ``database_setup.py`` was not run
    after cloning the repository.  A missing file indicates the same, or that
    ``settings.DATABASE_URL`` points to a different location.

    We check ``_REPO_ROOT / "quant_platform.db"`` rather than parsing
    ``settings.DATABASE_URL`` because the URL may be a full ``sqlite:///``
    connection string that would need to be stripped of the scheme prefix.
    """
    name = "db_exists"
    db = _REPO_ROOT / "quant_platform.db"
    if db.exists() and db.stat().st_size > 0:
        return CheckResult(name, True, f"Database found: {db}")
    return CheckResult(
        name, False,
        "quant_platform.db not found or empty — run: python3 database_setup.py",
    )


def check_paper_trading_duration(min_days: int = 90) -> CheckResult:
    """Verify that paper trading has run for at least ``min_days`` days.

    The 90-day default reflects a standard recommendation: three calendar
    months of paper trading across different market conditions (quiet periods,
    vol spikes, earnings seasons) before risking real capital.

    This check requires ``PAPER_TRADING_START_DATE`` (ISO format, YYYY-MM-DD)
    to be set in ``.env``.  The check FAILs if the variable is missing rather
    than skipping silently, because an unset date could mean paper trading
    never started — a pre-condition for go-live that must be acknowledged.

    Passing ``min_days`` as a parameter lets tests use a shorter threshold
    without patching ``date.today()``.
    """
    name = "paper_trading_duration"
    start_str = settings.PAPER_TRADING_START_DATE
    if not start_str:
        return CheckResult(
            name, False,
            "PAPER_TRADING_START_DATE not set in .env — set it to the date paper "
            "trading began (ISO format YYYY-MM-DD) to enable this check",
        )
    try:
        start = date.fromisoformat(start_str)
    except ValueError:
        return CheckResult(name, False, f"Invalid date format: {start_str!r} — use YYYY-MM-DD")
    elapsed = (date.today() - start).days
    if elapsed < min_days:
        return CheckResult(
            name, False,
            f"Paper-trading has run {elapsed} days (< required {min_days} days). "
            f"Go-live eligible on {(start + timedelta(days=min_days)).isoformat()}",
        )
    return CheckResult(
        name, True, f"Paper-trading has run {elapsed} days (≥ {min_days} days)"
    )


def check_validation_reports(max_age_days: int = 30) -> CheckResult:
    """Verify that every strategy has a current, deployable validation report.

    Reads all ``*_validation_summary.json`` files in ``reports/``, written by
    ``validation.harness.StrategyValidationHarness._write_json_summary()``.

    Two conditions cause a FAIL:
    * ``deployable=False`` — the strategy failed one or more gates (PBO ≥ 0.5,
      DSR ≤ 0.95, Sharpe ≤ 0.5, MaxDD ≥ 30%, or stress gate for options-selling).
    * ``report_date < today - max_age_days`` — the report is stale.  30 days is
      the default because markets change and an out-of-date walk-forward can
      mask deterioration that happened after the last run.

    All problems are accumulated into a single FAIL message rather than
    short-circuiting after the first, so the operator sees all issues in one
    run rather than fixing them one by one.
    """
    name = "validation_reports"
    reports_dir = _REPO_ROOT / "reports"
    if not reports_dir.exists():
        return CheckResult(
            name, False,
            "reports/ directory not found. Run validation harness for all active strategies.",
        )
    summaries = list(reports_dir.glob("*_validation_summary.json"))
    if not summaries:
        return CheckResult(
            name, False,
            "No validation summary JSON files found in reports/. "
            "Run: python -m validation.harness --strategy <name> --start ... --end ...",
        )
    problems: list[str] = []
    # ISO date strings compare lexicographically, so string < string is correct
    # for YYYY-MM-DD format without parsing — an efficient and dependency-free
    # staleness check.
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    for f in summaries:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            strat = data.get("strategy_id", f.stem)
            if not data.get("deployable"):
                problems.append(f"{strat}: deployable=False")
            report_date = data.get("report_date", "")
            if report_date < cutoff:
                problems.append(f"{strat}: report is {report_date} (older than {max_age_days}d)")
        except Exception as exc:
            problems.append(f"{f.name}: could not parse — {exc}")
    if problems:
        return CheckResult(name, False, "; ".join(problems))
    return CheckResult(
        name, True,
        f"All {len(summaries)} strategy report(s) are deployable and recent",
    )


def check_no_unexpected_risk_blocks(hours: float = 24.0) -> CheckResult:
    """Verify that no ``minimum_validation`` risk gate blocks occurred recently.

    ``minimum_validation`` is check #9 in ``execution.risk_gate.PreTradeRiskGate``.
    It fires when an order is submitted for a strategy whose validation report is
    either missing or has ``deployable=False``.  Seeing this block in the log
    means the risk gate is working but also that the preflight check should have
    caught the stale/missing report earlier — so this check is a secondary
    safety net.

    We specifically filter for ``minimum_validation`` rather than ALL blocks
    because other block types (e.g. ``portfolio_heat``, ``hmm_regime``) are
    expected during normal operation and should not flag a preflight failure.

    ``hours`` is parameterised for testing without requiring datetime patching.
    """
    name = "no_unexpected_risk_blocks"
    log_path = settings.OUTPUT_DIR / "risk_gate_blocks.jsonl"
    if not log_path.exists():
        # No file = no blocks ever recorded.  Treat as a PASS rather than FAIL
        # because the file is created lazily on first block.
        return CheckResult(name, True, "No risk gate block log found (no blocks ever recorded)")
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        val_blocks: list[dict] = []
        for line in log_path.read_text(encoding="utf-8").strip().splitlines():
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry.get("ts", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff and entry.get("check") == "minimum_validation":
                    val_blocks.append(entry)
            except Exception:
                # Skip malformed lines; a corrupt entry should not hide real blocks.
                continue
        if val_blocks:
            syms = ", ".join(set(b.get("strategy_id", "?") for b in val_blocks))
            return CheckResult(
                name, False,
                f"{len(val_blocks)} 'minimum_validation' risk gate block(s) in last {hours:.0f}h "
                f"for strategy: {syms} — ensure validation reports are deployable",
            )
        return CheckResult(
            name, True,
            f"No 'minimum_validation' blocks in the last {hours:.0f}h",
        )
    except Exception as exc:
        # Fail closed: if we cannot read the log we cannot confirm the gate is
        # working, so treat as a failure rather than assuming all is well.
        return CheckResult(name, False, f"Could not read block log: {exc}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# ``ALL_CHECKS`` is an ordered list (not a dict) because the execution order
# matters for reporting clarity: credentials checks first, then runtime state,
# then business-logic gates.  Order is also what ``run_checks``'s ``skip``
# matching relies on (strip ``check_`` prefix → name).
ALL_CHECKS = [
    check_fred_key_configured,
    check_key_rotation_recent,
    check_advisory_only_active,
    check_alpaca_configured,
    check_macro_regime_gate_enabled,
    check_alpaca_paper_mode,
    check_dry_run_disabled,
    check_env_not_committed,
    check_kill_switch_inactive,
    check_state_snapshot_fresh,
    check_heartbeat_fresh,
    check_db_exists,
    check_paper_trading_duration,
    check_validation_reports,
    check_no_unexpected_risk_blocks,
]


# Checks that are auto-skipped when ADVISORY_ONLY=True.
# Two categories:
#   (a) Broker-dependent (4): no broker stack means these have no meaning.
#   (b) Advisory false-positives (3): checks that require the full async
#       orchestrator pipeline or broker execution to produce a meaningful signal;
#       in advisory mode they would always fail even on a healthy platform.
#
#       - heartbeat_fresh: written by main_orchestrator.py only; main.py does not
#         write heartbeat.txt, so this always fails in advisory mode.
#       - validation_reports: strategy validation reports are a pre-live deployment
#         gate, not an advisory health signal.
#       - no_unexpected_risk_blocks: risk-gate blocks occur only on order
#         submission; advisory mode never submits orders.
#
# Note: state_snapshot_fresh is deliberately NOT in this list — it is the
# advisory-mode liveness check (both entry points write state_snapshot.json).
_ADVISORY_AUTO_SKIP: tuple[str, ...] = (
    "alpaca_configured",
    "alpaca_paper_mode",
    "dry_run_disabled",
    "paper_trading_duration",
    "heartbeat_fresh",
    "validation_reports",
    "no_unexpected_risk_blocks",
)


def run_checks(skip: list[str] | None = None) -> list[CheckResult]:
    """Execute all checks and return one ``CheckResult`` per check.

    Parameters
    ----------
    skip:
        List of check names (without the ``check_`` prefix) to skip.
        Skipped checks produce a PASS result with reason "(skipped via --skip)"
        so the result list always has ``len(ALL_CHECKS)`` entries.

    Notes
    -----
    Each check is wrapped in a broad ``try/except`` so that a bug inside one
    check function (e.g. an unexpected attribute error in a new version of
    ``settings``) produces a FAIL result rather than aborting the remaining
    checks.  The exception message is included in the reason string.

    Tier 5.1 — When ``settings.ADVISORY_ONLY`` is True the broker-dependent
    checks in ``_ADVISORY_AUTO_SKIP`` are auto-skipped (PASS with a clear
    reason) so the gate does not require Alpaca credentials, ALPACA_PAPER, or
    PAPER_TRADING_START_DATE while the broker surface is quarantined.
    """
    skip = list(skip or [])
    advisory_only = bool(getattr(settings, "ADVISORY_ONLY", True))
    results: list[CheckResult] = []
    for fn in ALL_CHECKS:
        check_name = fn.__name__.replace("check_", "")
        if check_name in skip:
            results.append(CheckResult(check_name, True, "(skipped via --skip)"))
            continue
        if advisory_only and check_name in _ADVISORY_AUTO_SKIP:
            results.append(CheckResult(
                check_name, True,
                "(skipped: ADVISORY_ONLY=True — broker check not applicable)",
            ))
            continue
        try:
            results.append(fn())
        except Exception as exc:
            results.append(CheckResult(check_name, False, f"Check raised exception: {exc}"))
    return results


def _print_table(results: list[CheckResult]) -> None:
    """Render a human-readable ASCII table of check results to stdout."""
    width = 80
    print("=" * width)
    print("  InvestYo Pre-Live Preflight Check")
    print("=" * width)
    for r in results:
        if r.warning and r.passed:
            icon = "⚠️ "
        elif r.passed:
            icon = "✅ "
        else:
            icon = "❌ "
        print(f"  {icon} {r.name}")
        print(f"        {r.reason}")
    print("=" * width)
    passes = sum(r.passed for r in results)
    fails = len(results) - passes
    if fails == 0:
        print(f"  ✅ ALL {passes} CHECKS PASSED — go-live gate is OPEN")
    else:
        print(f"  ❌ {fails}/{len(results)} CHECKS FAILED — do NOT go live yet")
    print("=" * width)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns the exit code (0 = all pass, 1 = any fail).

    Parameters
    ----------
    argv:
        Argument list.  ``None`` uses ``sys.argv[1:]`` (normal CLI invocation).
        Pass an explicit list to call ``main()`` from tests without spawning a
        subprocess.
    """
    parser = argparse.ArgumentParser(description="InvestYo pre-live readiness check")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON array instead of a human-readable table",
    )
    parser.add_argument(
        "--skip",
        nargs="*",
        default=[],
        metavar="CHECK",
        help=(
            "Skip named checks (e.g. --skip heartbeat_fresh paper_trading_duration). "
            "Useful in CI where certain checks are contextually inapplicable."
        ),
    )
    args = parser.parse_args(argv)

    results = run_checks(skip=args.skip)

    if args.json:
        print(json.dumps(
            [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "warning": r.warning,
                    "reason": r.reason,
                }
                for r in results
            ],
            indent=2,
        ))
    else:
        _print_table(results)

    # Exit 0 only when EVERY check passes (warnings count as pass).
    # This makes the script safe to use as a pre-commit hook or CI step.
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

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
                                  Warning-only; never blocking.
 3. alpaca_key_rotation_recent  — ALPACA_API_KEY was rotated within the last 90
                                  days (ALPACA_KEY_ROTATED_DATE in .env).
                                  Warning-only; never blocking.
                                  SKIPPED when ADVISORY_ONLY=True (Alpaca keys
                                  have no blast-radius risk while the broker
                                  surface is quarantined).
   robinhood_execution_mode     — ROBINHOOD_EXECUTION_MODE is one of off/review/
                                  live; when live, ROBINHOOD_MAX_NOTIONAL_PER_ORDER
                                  must be > 0.  Invalid mode → FAIL.  Orthogonal to
                                  the Alpaca ADVISORY_ONLY quarantine (never
                                  auto-skipped).
   robinhood_kill_switch_clear  — when mode=live, the global kill switch must be
                                  inactive (else the queue can never place).
                                  Non-live → pass.  Never auto-skipped.
   robinhood_queue_fresh        — when mode=live, output/execution_queue.json must
                                  exist and its generated_at be < 30 min old.
                                  Non-live → pass.  Never auto-skipped.
   robinhood_session_present    — WARNING-only: a cached Robinhood device-
                                  approval session (~/.tokens/robinhood.pickle)
                                  exists and is non-empty (else the next login
                                  needs a fresh device approval).  Any mode.
                                  Never auto-skipped.
 4. advisory_only_active        — settings.ADVISORY_ONLY=True (Tier 5.1
                                  quarantine).  When True, the broker-readiness
                                  checks (alpaca_configured / alpaca_paper_mode
                                  / dry_run_disabled / paper_trading_duration /
                                  alpaca_key_rotation_recent) and advisory
                                  false-positive checks (heartbeat_fresh /
                                  validation_reports / no_unexpected_risk_blocks)
                                  are auto-skipped.  Warning-only when False
                                  (live broker stack is in scope).
 5. alpaca_configured           — ALPACA_API_KEY + ALPACA_SECRET_KEY are present.
                                  SKIPPED when ADVISORY_ONLY=True.
 6. macro_regime_gate_enabled   — MACRO_REGIME_GATE_ENABLED=True when live trading.
                                  Warning-only in paper mode; blocking when
                                  ALPACA_PAPER=False + gate disabled.
 7. alpaca_paper_mode           — ALPACA_PAPER=True.  Warning-only when False.
                                  SKIPPED when ADVISORY_ONLY=True.
 8. dry_run_disabled            — DRY_RUN=False (orders reach the broker).
                                  SKIPPED when ADVISORY_ONLY=True.
 9. env_not_committed           — .env file is git-untracked (``git ls-files``).
   env_no_duplicate_keys        — .env has no repeated top-level KEY= (last-wins
                                  shadowing).  Warning-only; reports KEY NAMES
                                  (never values).
10. kill_switch_inactive        — The KILL_SWITCH sentinel file does not exist.
11. state_snapshot_fresh        — output/state_snapshot.json exists and its
                                  embedded timestamp is < 2 hours old.  Both
                                  main.py (advisory) and main_orchestrator.py
                                  write this file, making it the cross-mode
                                  liveness indicator.  NOT auto-skipped in
                                  advisory mode (it IS the advisory liveness
                                  check).
12. heartbeat_fresh             — output/heartbeat.txt was updated within 2 hours.
                                  SKIPPED when ADVISORY_ONLY=True — the heartbeat
                                  is written only by main_orchestrator.py; advisory
                                  runs via main.py do not require a persistent
                                  orchestrator process.
12. db_exists                   — quant_platform.db exists and is non-empty.
13. paper_trading_duration      — Paper-trading started ≥ 90 days ago
                                  (requires PAPER_TRADING_START_DATE in .env).
                                  SKIPPED when ADVISORY_ONLY=True (no broker
                                  → no paper-trading clock).
14. validation_reports          — Every *_validation_summary.json in reports/ is
                                  deployable=True and dated within 30 days.
                                  SKIPPED when ADVISORY_ONLY=True — validation
                                  reports gate live order submission; advisory mode
                                  produces signals only (no orders submitted).
16. no_unexpected_risk_blocks   — No "minimum_validation" risk gate blocks in the
                                  last 24 hours.  SKIPPED when ADVISORY_ONLY=True
                                  (no order submissions → no risk-gate blocks).
17. calibration_drift           — WARNING-ONLY (never blocking).  Runs the
                                  CUSUM/Page-Hinkley sequential change-point
                                  detector (validation/drift.py, Task B3) over
                                  the Tier 4.1 live-vs-recommendation tracking
                                  data.  Passes with a note when there is not
                                  yet enough tracking history.  NOT auto-skipped
                                  in advisory mode — the decision log this reads
                                  is itself an advisory-mode feature.
18. alert_channels_reachable    — WARNING-ONLY (never blocking).  Probes every
                                  currently-active observability.alerts channel
                                  (docs/plans/OBSERVABILITY_PLAN.md Phase O4) via
                                  observability.alerts.check_channel_health()
                                  and reports which, if any, are unreachable —
                                  so a broken Discord/Slack webhook or SMTP
                                  relay is discovered here rather than during
                                  a real incident.  NOT auto-skipped in
                                  advisory mode (alert channels matter
                                  regardless of broker mode).

Note: the "(N total)" figure above and the numbered list are historical and
have drifted from ALL_CHECKS as checks were added over time (27 entries as
of this writing, most recently robinhood_execution_mode /
robinhood_kill_switch_clear / robinhood_queue_fresh / robinhood_session_present
/ env_no_duplicate_keys / alert_channels_reachable / no_stray_database_files /
output_dir_matches_local_data_root / daemon_pid_alive, none of which carry a
number above). ALL_CHECKS is the single source of truth for the actual set
and order of checks that run.

no_stray_database_files              — WARNING-ONLY diagnostic tripwire (never
                                        blocking). Resolves the canonical DB
                                        location via db_config.resolve_database_url()
                                        and flags a same-named quant_platform.db
                                        file found elsewhere (repo root) that was
                                        modified within the last 24h — i.e.
                                        something is actively writing to a stale,
                                        non-canonical DB file. Skipped as a no-op
                                        when DATABASE_URL is not sqlite-backed.

output_dir_matches_local_data_root   — WARNING-ONLY diagnostic tripwire (never
                                        blocking). Compares settings.OUTPUT_DIR
                                        against settings.LOCAL_DATA_ROOT / "output"
                                        (what it would be if nothing overrode it)
                                        and flags a mismatch — i.e. a stale/legacy
                                        OUTPUT_DIR= line in .env is keeping output
                                        artifacts (state_snapshot.json, daemon.json,
                                        heartbeat.txt, decision_log.jsonl,
                                        execution_queue.json) outside the shared,
                                        cross-worktree LOCAL_DATA_ROOT location.

daemon_pid_alive                     — WARNING-ONLY diagnostic cross-check (never
                                        blocking). Sits right next to heartbeat_fresh:
                                        reads OUTPUT_DIR/daemon.json and reports
                                        whether the persistent orchestrator daemon
                                        process is ACTUALLY alive right now
                                        (externally-verified via os.kill(pid, 0), the
                                        same probe GET /automation/status and
                                        `python -m desktop.daemon_status` use) — since
                                        heartbeat.txt is never written under a daemon
                                        deployment, a stale/missing heartbeat_fresh
                                        result alone is ambiguous ("pipeline down" vs.
                                        "expected under this deployment shape"); this
                                        check resolves that ambiguity.
"""

from __future__ import annotations

import argparse
import json
import logging
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

logger = logging.getLogger(__name__)


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


def check_alpaca_key_rotation_recent(max_age_days: int = 90) -> CheckResult:
    """Warn if ALPACA_API_KEY has not been rotated within the recommended window.

    Mirrors ``check_key_rotation_recent`` but for the Alpaca key pair.  This
    check is **warning-only** (never blocking) because a stale rotation date is
    a hygiene reminder, not a hard gate.

    Automatically **skipped** when ``ADVISORY_ONLY=True`` because Alpaca paper
    keys have no blast-radius risk while the broker surface is quarantined —
    paper → live migration (which would make them sensitive) is handled by the
    ``advisory_only_active`` gate.  The check only becomes meaningful after
    ADVISORY_ONLY is disabled for a live-trading deployment.

    If ``ALPACA_KEY_ROTATED_DATE`` is unset the check passes with a warning so
    the operator is prompted to start tracking the rotation date once they begin
    using a live broker key.
    """
    name = "alpaca_key_rotation_recent"
    rotated_str = getattr(settings, "ALPACA_KEY_ROTATED_DATE", None)
    if not rotated_str:
        return CheckResult(
            name, True,
            "⚠️  ALPACA_KEY_ROTATED_DATE not set in .env — consider adding it "
            "after your next Alpaca key rotation so the 90-day reminder can "
            "track age. Generate keys at https://alpaca.markets/",
            warning=True,
        )
    try:
        rotated = date.fromisoformat(rotated_str)
    except ValueError:
        return CheckResult(
            name, True,
            f"⚠️  ALPACA_KEY_ROTATED_DATE has invalid format {rotated_str!r} "
            "(expected YYYY-MM-DD). Cannot check rotation age.",
            warning=True,
        )
    age_days = (date.today() - rotated).days
    if age_days > max_age_days:
        return CheckResult(
            name, True,
            f"⚠️  ALPACA_API_KEY was last rotated {age_days} days ago "
            f"(limit {max_age_days} days). Consider rotating at "
            "https://alpaca.markets/ and updating ALPACA_KEY_ROTATED_DATE in .env.",
            warning=True,
        )
    return CheckResult(
        name, True,
        f"ALPACA_API_KEY rotated {age_days} days ago (within {max_age_days}-day window)",
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


def _robinhood_mode() -> str:
    """Return the normalized Robinhood execution mode (``off``/``review``/``live``).

    Centralizes the read + lower-casing so every Robinhood check agrees on the
    active mode.  Defaults to ``off`` when the setting is unset/empty.  Unknown
    values are returned as-is (lower-cased) so callers can treat them as ``off``
    — the settings validator already coerces unknown values, but each check
    guards defensively regardless.
    """
    return str(getattr(settings, "ROBINHOOD_EXECUTION_MODE", "off") or "off").lower()


def check_robinhood_execution_mode() -> CheckResult:
    """Verify the Robinhood execution-bridge mode is valid + safely configured (Tier 8).

    Independent of ADVISORY_ONLY (which gates the Alpaca surface).  Recognized
    modes:
      * ``off``    — (default) the bridge emits nothing; PASS, no warning.
      * ``review`` — paper/dry-run; the queue is emitted but only
        ``review_equity_order`` simulations run downstream; PASS, no warning.
      * ``live``   — real placement is possible via the Claude Code agent; PASS
        with a WARNING so the operator confirms it is intentional, and a FAIL
        only when ``live`` is set without a per-order notional cap configured
        (``ROBINHOOD_MAX_NOTIONAL_PER_ORDER<=0``) — going live with no dollar
        ceiling is unsafe.

    Any value that is not one of ``off``/``review``/``live`` is a **FAIL**: an
    unrecognized mode is a configuration typo and must not silently degrade to a
    surprising behavior.

    Never auto-skipped under ADVISORY_ONLY — the Robinhood path is orthogonal to
    the Alpaca quarantine.
    """
    name = "robinhood_execution_mode"
    try:
        mode = _robinhood_mode()
        cap = float(getattr(settings, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0) or 0.0)
    except Exception as exc:
        return CheckResult(name, False, f"Check raised: {exc}")
    if mode in ("off", "review"):
        return CheckResult(
            name, True,
            f"ROBINHOOD_EXECUTION_MODE={mode} — "
            + ("bridge inert (no queue emitted)." if mode == "off"
               else "paper/dry-run; review_equity_order only, no placement."),
        )
    if mode == "live":
        if cap <= 0:
            return CheckResult(
                name, False,
                "ROBINHOOD_EXECUTION_MODE=live but ROBINHOOD_MAX_NOTIONAL_PER_ORDER "
                "is unset (<=0). Configure a per-order dollar ceiling before going live.",
            )
        return CheckResult(
            name, True,
            f"⚠️  ROBINHOOD_EXECUTION_MODE=live — real Robinhood placement is "
            f"possible (cap ${cap:,.0f}/order, agentic account, per-trade human "
            f"confirm). Confirm this is intentional.",
            warning=True,
        )
    # Any other value is an invalid mode — fail loudly rather than degrade silently.
    return CheckResult(
        name, False,
        f"ROBINHOOD_EXECUTION_MODE={mode!r} is invalid — must be one of "
        "'off', 'review', or 'live'. Fix the value in .env.",
    )


def check_robinhood_kill_switch_clear() -> CheckResult:
    """When Robinhood is live, verify the global kill switch is inactive.

    The Robinhood execution bridge (``execution/queue_builder.py``) computes
    ``allow_place`` as False whenever the kill switch is active, so a live-mode
    launch with an active kill switch would emit a queue that can never place.
    This is a distinct, Robinhood-scoped kill-switch gate (separate from the
    general ``kill_switch_inactive`` check) so the failure reason points the
    operator directly at the Robinhood path.

    Non-live modes (``off``/``review``) → PASS (no real placement possible, so
    the kill switch is irrelevant to the Robinhood bridge).

    ``GlobalKillSwitch`` is imported lazily (inside the function) so tests can
    patch ``execution.kill_switch.KILL_SWITCH_FILE`` before the import resolves.

    Never auto-skipped under ADVISORY_ONLY — the Robinhood path is orthogonal to
    the Alpaca quarantine.
    """
    name = "robinhood_kill_switch_clear"
    try:
        mode = _robinhood_mode()
        if mode != "live":
            return CheckResult(
                name, True,
                f"ROBINHOOD_EXECUTION_MODE={mode} — kill switch not relevant to the "
                "Robinhood bridge (no real placement possible).",
            )
        from execution.kill_switch import GlobalKillSwitch
        ks = GlobalKillSwitch()
        if ks.is_active():
            return CheckResult(
                name, False,
                "ROBINHOOD_EXECUTION_MODE=live but the kill switch is ACTIVE — the "
                "Robinhood queue will refuse to place orders. Deactivate with: "
                "python -m execution.kill_switch --deactivate  "
                f"(reason: {ks.reason() or '(none)'})",
            )
        return CheckResult(
            name, True,
            "ROBINHOOD_EXECUTION_MODE=live and kill switch is inactive — bridge may place orders.",
        )
    except Exception as exc:
        return CheckResult(name, False, f"Check raised: {exc}")


def check_robinhood_queue_fresh(max_age_minutes: float = 30.0) -> CheckResult:
    """When Robinhood is live, verify the proposed-order queue is present + fresh.

    ``execution/queue_builder.emit_execution_queue`` writes
    ``OUTPUT_DIR/execution_queue.json`` with a ``generated_at`` ISO-8601 UTC
    timestamp.  Before placing real orders the operator (or the Claude Code
    agent that consumes the queue) must be working from a recently-generated
    queue — a stale queue reflects prices/signals that may have moved.

    FAILs in live mode when the queue file is missing OR its ``generated_at`` is
    older than ``max_age_minutes`` (default 30).  Non-live modes → PASS.

    Uses ``datetime.now(timezone.utc)`` (this module standardizes on tz-aware
    UTC).  ``max_age_minutes`` is parameterized for testing without datetime
    patching.

    Never auto-skipped under ADVISORY_ONLY — the Robinhood path is orthogonal to
    the Alpaca quarantine.
    """
    name = "robinhood_queue_fresh"
    try:
        mode = _robinhood_mode()
    except Exception as exc:
        return CheckResult(name, False, f"Check raised: {exc}")
    if mode != "live":
        return CheckResult(
            name, True,
            f"ROBINHOOD_EXECUTION_MODE={mode} — no live queue freshness requirement.",
        )
    queue_path = settings.OUTPUT_DIR / "execution_queue.json"
    if not queue_path.exists():
        return CheckResult(
            name, False,
            "ROBINHOOD_EXECUTION_MODE=live but output/execution_queue.json not found — "
            "run the pipeline to emit a proposed-order queue before placing orders.",
        )
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        ts_str = data.get("generated_at", "")
        if not ts_str:
            return CheckResult(
                name, False,
                "output/execution_queue.json has no 'generated_at' timestamp — "
                "cannot verify freshness; regenerate the queue.",
            )
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts
        if age > timedelta(minutes=max_age_minutes):
            return CheckResult(
                name, False,
                f"Execution queue is {age.total_seconds()/60:.0f} min old "
                f"(limit {max_age_minutes:.0f} min) — regenerate before placing orders.",
            )
        return CheckResult(
            name, True,
            f"Execution queue is {age.total_seconds()/60:.0f} min old "
            f"(within {max_age_minutes:.0f} min window).",
        )
    except Exception as exc:
        return CheckResult(name, False, f"Could not read execution queue: {exc}")


def check_robinhood_session_present() -> CheckResult:
    """WARNING-ONLY: flag when no cached Robinhood device-approval session
    exists.

    Robinhood login is device-approval push (the operator taps "approve" in
    the Robinhood app) rather than a typed TOTP/SMS code, so a login attempt
    can never complete unattended — it always needs a human watching their
    phone at the moment of the request. A cached session pickle
    (``~/.tokens/robinhood.pickle``, guarded by ``data/robinhood_session.py``)
    means the device Robinhood already trusts is being reused, which
    typically avoids a fresh device-approval prompt on the next login
    (subject to Robinhood's own device-token lifetime — this check cannot
    promise a login will be silent, only that one isn't obviously needed).
    This is a hygiene reminder, not a hard gate, so it is **warning-only**
    (never blocking) and applies in every mode.

    Never auto-skipped under ADVISORY_ONLY — the Robinhood path is orthogonal
    to the Alpaca quarantine.
    """
    name = "robinhood_session_present"
    try:
        from data import robinhood_session
        present = robinhood_session._is_loadable_session(robinhood_session._PICKLE_PATH)
    except Exception as exc:
        return CheckResult(
            name, True,
            f"⚠️  Could not check for a cached Robinhood session ({exc}) — skipping check.",
            warning=True,
        )
    if not present:
        return CheckResult(
            name, True,
            "⚠️  No cached Robinhood session found — the next login will need "
            "a fresh device approval (tap 'approve' in the Robinhood app). Run "
            "`python3 main.py --refresh-account` (or the Pilots PWA's connect "
            "flow) with your phone nearby.",
            warning=True,
        )
    return CheckResult(
        name, True,
        "A cached Robinhood session is present — the next login may not "
        "require a fresh device approval.",
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


def check_broker_backend_matches_live_intent() -> CheckResult:
    """
    Verifies that BROKER_BACKEND='fmp_paper' is not active when live trading
    is intended.

    Uses ``execution.broker_selection.is_going_live()`` -- the SAME "going
    live" predicate the runtime guard in ``main_orchestrator.py``'s
    ``_execute_broker_orders`` (via ``resolve_broker_backend()``) and
    ``robinhood_execution_mcp.py``'s ``_get_broker()`` both use -- rather
    than a narrower, independently-reimplemented ``not ALPACA_PAPER`` check.
    The runtime guard also considers ``ADVISORY_ONLY``: a run with
    ``ADVISORY_ONLY=True`` never submits broker orders at all regardless of
    ``ALPACA_PAPER``, so gating this preflight check on ``ALPACA_PAPER``
    alone previously blocked configurations the runtime guard would never
    have flagged, and could equally have missed a genuinely-live
    misconfiguration once ``ADVISORY_ONLY`` was folded into the real
    predicate.
    """
    from execution.broker_selection import is_going_live

    if getattr(settings, "BROKER_BACKEND", "alpaca") == "fmp_paper" and is_going_live():
        return CheckResult(
            name="broker_backend_matches_live_intent",
            passed=False,
            reason="BROKER_BACKEND='fmp_paper' is active but this run is configured to go live "
            "(ADVISORY_ONLY=False and ALPACA_PAPER=False). Set BROKER_BACKEND='alpaca' for live "
            "trading, or enable ALPACA_PAPER/ADVISORY_ONLY to stay in paper/advisory mode."
        )
    return CheckResult(
        name="broker_backend_matches_live_intent",
        passed=True,
        reason="BROKER_BACKEND is compatible with live trading intent."
    )

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


def check_env_no_duplicate_keys() -> CheckResult:
    """Warn if ``.env`` defines the same top-level key more than once.

    ``python-dotenv`` and ``pydantic-settings`` both resolve a repeated key to
    its **last** occurrence, so a duplicate silently shadows the earlier line.
    That makes it ambiguous which value is live and is a common source of
    "I changed it but nothing happened" confusion (the operator edits the first
    occurrence while the second one wins).

    This check parses ``.env`` line-by-line for ``KEY=`` assignments (ignoring
    comments and blank lines) and reports any KEY NAMES that appear more than
    once.  It is **warning-only** (never blocking) — a duplicate is a hygiene
    problem, not a go/no-go gate, and the file still loads with a deterministic
    (last-wins) value.

    Locates ``.env`` the same way as :func:`check_env_not_committed`
    (``_REPO_ROOT / ".env"``).  A missing ``.env`` is a PASS (nothing to check);
    :func:`check_env_not_committed` already fails when ``.env`` is absent, so we
    do not double-report here.

    IMPORTANT: the ``reason`` string reports only KEY NAMES, never values —
    ``.env`` contains secrets and this check must never surface them.
    """
    name = "env_no_duplicate_keys"
    env_file = _REPO_ROOT / ".env"
    if not env_file.exists():
        # Absent .env is handled by check_env_not_committed; nothing to dedupe.
        return CheckResult(name, True, "No .env file present — nothing to check for duplicates")
    try:
        seen: dict[str, int] = {}
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.lstrip()
            if not line or line.startswith("#"):
                continue
            # Only treat KEY=... assignment lines as keys (env var name syntax).
            eq = line.find("=")
            if eq <= 0:
                continue
            key = line[:eq].strip()
            if not key or not all(c.isalnum() or c == "_" for c in key):
                continue
            seen[key] = seen.get(key, 0) + 1
        dupes = sorted(k for k, count in seen.items() if count > 1)
        if dupes:
            # KEY NAMES only — never values.
            return CheckResult(
                name, True,
                "⚠️  .env has duplicate keys (last occurrence wins, earlier lines are "
                f"silently shadowed): {', '.join(dupes)}. Remove the earlier "
                "duplicate line(s) so the live value is unambiguous.",
                warning=True,
            )
        return CheckResult(name, True, "No duplicate keys in .env")
    except Exception as exc:
        # Never blocking: a parse error here must not gate go-live.
        return CheckResult(
            name, True,
            f"⚠️  Could not scan .env for duplicate keys ({exc}) — skipping check.",
            warning=True,
        )


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


def _paused_for_market_hours() -> bool:
    """True when settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY (see main.py /
    desktop/daemon_runtime.py's automatic-trigger gate) fully explains why
    state_snapshot.json hasn't been refreshed recently -- i.e. we're currently
    outside the 4am-8pm ET weekday window, so main.py --interval/the daemon
    timer are legitimately skipping cycles rather than being down. Lazy
    imports keep this module's own import graph unchanged for every caller
    that never hits this branch; dead-letter by design (CONSTRAINT #6) -- any
    failure here must never turn a real staleness failure into a falsely-
    reassuring pass, so it degrades to False on any error."""
    try:
        from engine.advisory_agent import is_extended_hours

        return bool(settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY) and not is_extended_hours(
            datetime.now(timezone.utc)
        )
    except Exception:
        return False


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

    ``settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY`` (default ``True``) makes
    ``main.py --interval``/the daemon timer skip automatic cycles outside the
    4am-8pm ET weekday window, so a stale snapshot found outside that window is
    expected, not a sign the pipeline is down. When ``_paused_for_market_hours()``
    confirms that's the case, staleness beyond ``max_age_hours`` degrades to a
    warning-only PASS instead of a blocking failure.
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
            if _paused_for_market_hours():
                return CheckResult(
                    name, True,
                    f"State snapshot is {age.total_seconds()/3600:.1f}h old (limit {max_age_hours}h) — "
                    "automatic runs are currently paused outside market hours "
                    "(ORCHESTRATOR_EXTENDED_HOURS_ONLY=True); not treated as a failure.",
                    warning=True,
                )
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


def check_daemon_pid_alive() -> CheckResult:
    """WARNING-ONLY cross-check: is the persistent orchestrator daemon
    process actually alive right now, per ``OUTPUT_DIR/daemon.json``?

    Why this sits right next to ``check_heartbeat_fresh``
    -------------------------------------------------------
    ``heartbeat_fresh`` above answers "when did ``main_orchestrator.py``'s
    per-cycle heartbeat last update" -- but ``heartbeat.txt`` is written
    ONLY by ``main_orchestrator.main()``'s own lifecycle. The persistent
    orchestrator daemon (``desktop/orchestrator_daemon.py``) runs
    ``main_orchestrator._main_body()`` directly and deliberately bypasses
    that lifecycle, so ``heartbeat.txt`` is **permanently absent under a
    daemon deployment** (see ``pilots/run_status.py``'s module docstring) --
    meaning ``heartbeat_fresh`` routinely, misleadingly reads as
    stale/missing for an operator running the daemon, even when the daemon
    itself is perfectly healthy. That exact ambiguity ("heartbeat looks
    alarming — is the pipeline actually down, or is this expected?")
    confused a real operator in practice. This check exists to sit right
    next to that reading and answer the question a stale heartbeat can't:
    is the daemon process itself alive on this host, right now. Read
    together: "heartbeat stale" + "daemon pid alive" means "expected under
    a daemon deployment, not an outage"; "heartbeat stale" + "daemon pid
    NOT alive" (or no ``daemon.json`` at all) means the pipeline really is
    down. For a faster, dedicated version of just this question, see
    ``python -m desktop.daemon_status``.

    Severity
    --------
    Always ``warning=True`` (mirrors ``check_no_stray_database_files`` /
    ``check_output_dir_matches_local_data_root``'s severity precedent) --
    never a blocking FAIL. A fresh clone, CI, or an advisory-only deployment
    that never runs the persistent daemon at all legitimately has no
    ``daemon.json``, and that alone is not evidence anything is wrong; this
    check is informational cross-check telemetry, not a deployability gate.
    Additive and backward-compatible with ``check_heartbeat_fresh`` --
    a wholly separate ``CheckResult`` under its own name, so
    ``heartbeat_fresh``'s own existing pass/fail contract and reason-string
    format are completely untouched.

    Implementation note: reuses ``pilots.run_status._pid_alive`` -- the same
    externally-verified (``os.kill(pid, 0)``) probe ``GET /automation/status``'s
    ``daemon.pid_alive`` and ``python -m desktop.daemon_status`` both use --
    for the actual liveness check, rather than re-deriving that logic here.
    Deliberately reads ``OUTPUT_DIR/daemon.json`` directly instead of calling
    ``pilots.run_status.read_daemon_json()`` outright: that function resolves
    ``OUTPUT_DIR`` via its own top-level ``from settings import settings``
    import, which is a DIFFERENT reference than this module's own
    (separately mockable) ``settings`` name -- calling it directly would
    silently read the real, unpatched global settings singleton in this
    file's tests instead of the ``tmp_path``-scoped fixture every other check
    here uses. ``_pid_alive`` itself takes a bare pid and touches no
    settings, so reusing it carries none of that risk.

    Never raises (CONSTRAINT #6) -- any read/parse failure degrades to an
    informational warning-PASS, never a new failure mode for the go-live gate.
    """
    name = "daemon_pid_alive"
    daemon_path = settings.OUTPUT_DIR / "daemon.json"
    if not daemon_path.exists():
        return CheckResult(
            name, True,
            "No daemon.json found under OUTPUT_DIR — the persistent orchestrator "
            "daemon (desktop/orchestrator_daemon.py) has never been started here, "
            "or this deployment runs main.py/main_orchestrator.py directly without "
            "it. Not a failure; informational only.",
        )
    try:
        data = json.loads(daemon_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("daemon.json did not contain a JSON object")
    except Exception as exc:
        return CheckResult(
            name, True,
            f"⚠️  Could not read/parse daemon.json ({exc}) — cannot cross-check "
            "daemon liveness. Not a failure; informational only.",
            warning=True,
        )

    pid = data.get("pid")
    state = data.get("state")
    try:
        from pilots.run_status import _pid_alive
        alive = _pid_alive(pid)
    except Exception as exc:  # noqa: BLE001 - diagnostic must never fail the gate
        return CheckResult(
            name, True,
            f"⚠️  Could not probe daemon pid liveness ({exc}). Not a failure; "
            "informational only.",
            warning=True,
        )

    if alive is True:
        return CheckResult(
            name, True,
            f"Daemon process pid {pid} IS alive right now (self-reported "
            f"state='{state}'). If heartbeat_fresh above reads stale/missing, "
            "that is EXPECTED under a daemon deployment (heartbeat.txt is only "
            "written by main_orchestrator.py's own main() lifecycle, never by "
            "the persistent daemon) — not evidence of an outage.",
        )
    if alive is False:
        return CheckResult(
            name, True,
            f"⚠️  Daemon process pid {pid} is NOT alive (self-reported "
            f"state='{state}') — daemon.json is stale. If a daemon deployment "
            "is expected to be running here, restart it: "
            "python -m desktop.orchestrator_daemon --interval N",
            warning=True,
        )
    return CheckResult(
        name, True,
        f"⚠️  Could not determine whether daemon pid {pid!r} (self-reported "
        f"state='{state}') is alive on this host — pid value in daemon.json is "
        "missing or unusable.",
        warning=True,
    )


def check_db_exists() -> CheckResult:
    """Verify that the local SQLite database exists and is non-empty.

    An empty file (0 bytes) indicates that ``database_setup.py`` was not run
    after cloning the repository.  A missing file indicates the same.

    This check always validates the local ``quant_platform.db`` file
    regardless of ``settings.DATABASE_URL`` (see ``db_config.py``): the
    SQLite-backed caches -- ``HistoricalStore``, ``ForecastTracker``, and the
    ``DailySignals``/``ExecutionLogs`` tables from ``database_setup.py`` --
    always live in this local file. Only the SQLAlchemy ORM stores
    (``transactions_store.py``'s ``trades`` table and
    ``volatility/iv_engine.py``'s ``iv_history`` table) honor ``DATABASE_URL``
    and may instead live in Postgres.
    """
    name = "db_exists"
    db = _REPO_ROOT / "quant_platform.db"
    if db.exists() and db.stat().st_size > 0:
        return CheckResult(name, True, f"Database found: {db}")
    return CheckResult(
        name, False,
        "quant_platform.db not found or empty — run: python3 database_setup.py",
    )


def check_no_stray_database_files(max_age_hours: float = 24.0) -> CheckResult:
    """WARNING-ONLY tripwire: detect a stray ``quant_platform.db`` file being
    actively written OUTSIDE the canonical location (Defense-in-depth for the
    2026-08 forecast_tracker.py incident).

    Background
    ----------
    ``settings.LOCAL_DATA_ROOT`` (PR #718) moved every store's default SQLite
    file to a machine-global root shared across worktrees. A hardcoded
    CWD-relative default in ``forecasting/forecast_tracker.py`` bypassed
    ``db_config.resolve_database_url()`` -- the single source of truth every
    store is supposed to use -- and kept writing to a STALE, non-canonical
    ``quant_platform.db`` at the repo root for hours after the live daemon
    restarted onto the new code, silently accumulating ~2 million rows in the
    wrong file while every other table correctly moved to the canonical path.
    That was caught only by manual ``lsof`` + mtime/row-count inspection; this
    check automates the same signal going forward.

    What it checks
    ---------------
    Resolves the canonical database path via ``db_config.resolve_database_url()``
    and looks for a file with the SAME BASENAME (in case an operator renamed
    the canonical DB) at a small, explicit set of candidate locations --
    NOT an unbounded filesystem walk. Currently just the repo root
    (``_REPO_ROOT``), since that is exactly where the real incident's stray
    file lived and is the one place a hardcoded CWD-relative default would
    plausibly write to.

    Severity
    --------
    This is a diagnostic tripwire, not a go/no-go gate -- mirrors
    ``check_state_snapshot_fresh``'s "warning-only PASS under an explained
    condition" precedent:
      * No stray file found -> clean PASS, no noise.
      * A stray file exists but its mtime is older than ``max_age_hours``
        (default 24h -- long enough that a leftover nobody has touched in a
        while doesn't false-alarm, short enough to still catch a process that
        is actively writing to it within roughly one operating day; this
        doesn't need to be precisely tuned, only to separate "being written
        right now" from "abandoned months ago") -> clean PASS. A genuinely
        stale leftover (e.g. pre-``LOCAL_DATA_ROOT`` migration) is not itself
        a problem worth flagging every run.
      * A stray file exists AND was modified within ``max_age_hours`` ->
        WARNING (never a blocking FAIL) naming the exact stray path, its
        last-modified time, and the canonical path it should be at instead,
        so the operator knows to go find and kill whichever process has it
        open (e.g. ``lsof <path>``).

    Degrades gracefully to a no-op PASS (never raises, per this module's
    fail-closed-but-never-crash convention for diagnostic checks) when
    ``resolve_database_url()`` doesn't return a sqlite-shaped URL (e.g. an
    operator running on Postgres) or any other resolution step fails --
    this check only makes sense for the sqlite-default deployment shape.
    """
    name = "no_stray_database_files"
    try:
        from db_config import resolve_database_url
        from sqlalchemy.engine import make_url

        db_url = resolve_database_url()
        url = make_url(db_url)
    except Exception as exc:
        return CheckResult(
            name, True,
            f"⚠️  Could not resolve the canonical database URL ({exc}) — skipping "
            "stray-file check.",
            warning=True,
        )

    if url.get_backend_name() != "sqlite":
        return CheckResult(
            name, True,
            "DATABASE_URL is not sqlite-backed — stray-file check only applies to "
            "the default local SQLite deployment.",
        )

    db_database = url.database
    if not db_database or db_database == ":memory:":
        return CheckResult(
            name, True,
            "Canonical database is in-memory sqlite — stray-file check not applicable.",
        )

    try:
        canonical_path = Path(db_database).resolve()
        filename = canonical_path.name

        # Bounded, explicit search scope -- NOT an unbounded filesystem walk.
        # The real incident's stray file was always at the repo root of
        # whichever checkout was running the pre-fix code, so that is the
        # one candidate location this check looks at.
        candidate_dirs = [_REPO_ROOT]
        stray_files: list[Path] = []
        for d in candidate_dirs:
            candidate = d / filename
            if not candidate.exists():
                continue
            try:
                if candidate.resolve() == canonical_path:
                    continue  # same physical file (e.g. LOCAL_DATA_ROOT == repo root)
            except OSError:
                continue
            stray_files.append(candidate)

        if not stray_files:
            return CheckResult(
                name, True,
                f"No stray '{filename}' found outside the canonical location "
                f"({canonical_path}).",
            )

        now = datetime.now(timezone.utc)
        for stray in stray_files:
            try:
                mtime = datetime.fromtimestamp(stray.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            age = now - mtime
            if age <= timedelta(hours=max_age_hours):
                return CheckResult(
                    name, True,
                    f"⚠️  Stray database file {stray} was last modified "
                    f"{age.total_seconds() / 3600:.1f}h ago (within the "
                    f"{max_age_hours:.0f}h freshness window) — something appears to "
                    f"be ACTIVELY WRITING to it instead of the canonical location "
                    f"{canonical_path}. Investigate which process has it open (e.g. "
                    f"`lsof {stray}`) before more data accumulates at the wrong path "
                    "— this is the exact failure mode that hit forecast_tracker.py "
                    "(a hardcoded CWD-relative default that bypassed "
                    "db_config.resolve_database_url()).",
                    warning=True,
                )

        # Stray file(s) exist but none were touched recently -- a stale
        # leftover, not an active split-brain write target. Clean PASS.
        return CheckResult(
            name, True,
            "Stray database file(s) found ("
            + ", ".join(str(s) for s in stray_files)
            + f") but none modified within the last {max_age_hours:.0f}h — likely a "
            "stale leftover, not an active write target.",
        )
    except Exception as exc:
        return CheckResult(
            name, True,
            f"⚠️  no_stray_database_files check could not run ({exc}).",
            warning=True,
        )


def check_output_dir_matches_local_data_root() -> CheckResult:
    """WARNING-ONLY tripwire: detect ``settings.OUTPUT_DIR`` pinned away from
    the ``settings.LOCAL_DATA_ROOT``-derived default by a stale/legacy
    ``.env`` override (Defense-in-depth for the 2026-08 OUTPUT_DIR-migration
    incident, sibling to ``check_no_stray_database_files`` above).

    Background
    ----------
    ``settings.LOCAL_DATA_ROOT`` (PR #718) is an external, machine-global
    root (default ``~/.stockpy_local``) meant to hold every locally-generated
    model/data/output artifact, shared across every git worktree and the
    live production daemon. ``settings.OUTPUT_DIR`` is supposed to derive its
    default from ``LOCAL_DATA_ROOT / "output"`` via a ``model_validator`` --
    BUT an operator's explicit ``OUTPUT_DIR=...`` in ``.env`` always wins
    over that derived default (intentional design, so the migration never
    silently changes behavior for someone who deliberately customized it).

    A real incident showed the failure mode this check exists to catch: an
    operator's ``.env`` had ``OUTPUT_DIR=./output`` set since before
    ``LOCAL_DATA_ROOT`` ever existed -- a leftover/legacy value, not a
    deliberate recent customization. This silently and completely defeated
    the entire ``OUTPUT_DIR`` half of the ``LOCAL_DATA_ROOT`` migration:
    ``state_snapshot.json``, ``daemon.json``, ``decision_log.jsonl``,
    ``execution_queue.json``, ``heartbeat.txt``, and everything else routed
    through ``settings.OUTPUT_DIR`` kept writing to the old repo-relative
    ``./output`` directory, while everything anchored *directly* to
    ``LOCAL_DATA_ROOT`` (the DB, models, caches, logs -- none of which go
    through ``OUTPUT_DIR``) correctly moved. ``daemon.json`` looked "stuck at
    the old path" and ``heartbeat.txt`` looked impossibly stale even though
    the daemon was actually running fine -- because the check/tooling was
    implicitly looking in the wrong place relative to what the operator
    expected after the ``LOCAL_DATA_ROOT`` rollout.

    What it checks
    ---------------
    Compares the resolved ``settings.OUTPUT_DIR`` against what it WOULD be
    if nothing overrode it (``settings.LOCAL_DATA_ROOT / "output"``).

    Severity
    --------
    Mirrors ``check_no_stray_database_files``'s severity precedent -- this
    is a diagnostic tripwire, never a blocking gate, since an explicit
    ``OUTPUT_DIR`` override can be a deliberate, valid operator choice (e.g.
    a dedicated output volume):
      * Resolved paths match (no override, or an override that happens to
        coincidentally point at the exact same resolved path) -> clean
        PASS, no noise.
      * Resolved paths differ -> WARNING (never a blocking FAIL) naming both
        the actual resolved ``OUTPUT_DIR`` and the ``LOCAL_DATA_ROOT``-derived
        path it would otherwise be, explaining that
        ``state_snapshot.json``/``daemon.json``/``heartbeat.txt``/
        ``decision_log.jsonl``/``execution_queue.json`` are NOT in the
        shared cross-worktree location, and giving the exact fix: remove or
        comment out the ``OUTPUT_DIR=`` line in ``.env`` to let it derive
        from ``LOCAL_DATA_ROOT`` automatically.

    Degrades gracefully to a clean PASS (never raises, per this module's
    fail-closed-but-never-crash convention for diagnostic checks) when
    ``settings.LOCAL_DATA_ROOT``/``settings.OUTPUT_DIR`` are unavailable,
    ``None``, or malformed -- this check's job is purely diagnostic and must
    never become a new failure mode for the go-live gate.
    """
    name = "output_dir_matches_local_data_root"
    try:
        output_dir = getattr(settings, "OUTPUT_DIR", None)
        local_data_root = getattr(settings, "LOCAL_DATA_ROOT", None)
        if output_dir is None or local_data_root is None:
            return CheckResult(
                name, True,
                "settings.OUTPUT_DIR or settings.LOCAL_DATA_ROOT is unavailable -- "
                "skipping the OUTPUT_DIR/LOCAL_DATA_ROOT parity check.",
            )
        actual = Path(output_dir).resolve()
        expected = (Path(local_data_root) / "output").resolve()
    except Exception as exc:
        return CheckResult(
            name, True,
            f"⚠️  output_dir_matches_local_data_root check could not run ({exc}) -- "
            "skipping.",
            warning=True,
        )

    if actual == expected:
        return CheckResult(
            name, True,
            f"settings.OUTPUT_DIR ({actual}) matches the LOCAL_DATA_ROOT-derived "
            "default -- output artifacts are in the shared, cross-worktree location.",
        )

    return CheckResult(
        name, True,
        f"⚠️  settings.OUTPUT_DIR is pinned to {actual}, which differs from the "
        f"LOCAL_DATA_ROOT-derived default of {expected}. An explicit OUTPUT_DIR= "
        "override in .env is keeping output artifacts -- including "
        "state_snapshot.json, daemon.json, heartbeat.txt, decision_log.jsonl, and "
        "execution_queue.json -- OUTSIDE the shared, cross-worktree LOCAL_DATA_ROOT "
        "location, so tooling and other worktrees/checkouts expecting the "
        "LOCAL_DATA_ROOT path will see stale or missing data even though the "
        "pipeline itself may be running fine. Fix: remove or comment out the "
        "OUTPUT_DIR= line in .env to let it derive from LOCAL_DATA_ROOT automatically.",
        warning=True,
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


def check_validation_reports(
    max_age_days: int = 30,
    *,
    fire_alert: bool = False,
    send_alert_fn=None,
) -> CheckResult:
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

    Parameters
    ----------
    fire_alert:
        When True AND the check fails (missing/stale/non-deployable
        report(s)), also dispatch a CRITICAL alert via
        ``observability.alerts.send_alert``. Defaults to False so that an
        ad-hoc, interactive ``python scripts/preflight_check.py`` run on a
        developer's laptop does not spam the team's alert channels every
        time it is invoked — this is gated to fire only from a scheduled/CI
        invocation via ``--fire-alerts`` (see ``main()``/``run_checks()``).
        Mirrors ``validation/drift.py::check_and_alert_recommendation_drift``'s
        gating style (an on-demand preflight check is not automatically a
        live pipeline hook).
    send_alert_fn:
        Injectable for tests. Defaults to ``observability.alerts.send_alert``
        (lazy-imported to avoid a module-load-time dependency on
        ``observability`` and to match this repo's existing lazy-import
        convention for alert dispatch call sites).
    """
    name = "validation_reports"
    reports_dir = _REPO_ROOT / "reports"
    if not reports_dir.exists():
        return _validation_reports_result(
            name, False,
            "reports/ directory not found. Run validation harness for all active strategies.",
            fire_alert=fire_alert, send_alert_fn=send_alert_fn,
        )
    summaries = list(reports_dir.glob("*_validation_summary.json"))
    if not summaries:
        return _validation_reports_result(
            name, False,
            "No validation summary JSON files found in reports/. "
            "Run: python -m validation.harness --strategy <name> --start ... --end ...",
            fire_alert=fire_alert, send_alert_fn=send_alert_fn,
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
        return _validation_reports_result(
            name, False, "; ".join(problems),
            fire_alert=fire_alert, send_alert_fn=send_alert_fn,
        )
    return CheckResult(
        name, True,
        f"All {len(summaries)} strategy report(s) are deployable and recent",
    )


def _validation_reports_result(
    name: str,
    passed: bool,
    reason: str,
    *,
    fire_alert: bool,
    send_alert_fn,
) -> CheckResult:
    """Build the FAIL ``CheckResult`` for ``check_validation_reports`` and,
    when ``fire_alert`` is True, dispatch the matching CRITICAL alert.

    Factored out of ``check_validation_reports`` because there are three
    distinct FAIL sites (missing reports/ dir, no summary files, per-file
    problems) that must all alert identically when gated on — duplicating
    the dispatch block three times would risk the three call sites drifting.
    Dead-letter safe: an alert-dispatch failure never changes the returned
    ``CheckResult`` (CONSTRAINT #6).
    """
    if fire_alert:
        try:
            if send_alert_fn is None:
                from observability.alerts import send_alert as send_alert_fn  # noqa: PLC0415
            send_alert_fn(
                "CRITICAL",
                f"Preflight: validation_reports check FAILED — {reason}",
                extra={"type": "validation_reports_missing", "reason": reason},
                dedup_key="validation_reports_missing",
            )
        except Exception as exc:
            logger.debug("check_validation_reports: send_alert failed (%s)", exc)
    return CheckResult(name, passed, reason)


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


def check_calibration_drift() -> CheckResult:
    """WARNING-ONLY: run the CUSUM/Page-Hinkley drift detector over the most
    recent live-vs-recommendation tracking data (Tier 4.1) and flag when the
    operator-vs-model judgment edge (or the model's own return stream) has
    drifted out of the distribution it was validated against.

    This is deliberately **never blocking** (``CheckResult.warning=True``
    unconditionally): drift detection is inherently a soft, statistical
    signal, not a hard go/no-go gate — a real drift alarm should prompt the
    operator to re-run validation (``validation/harness.py``) or investigate,
    not halt the platform outright.

    Degrades gracefully (never FAILs) in three cases, all treated as PASS:
      * ``output/decision_log.jsonl`` (the Tier 1.3 decision log that feeds
        Tier 4.1 tracking) does not exist yet — a fresh deployment has no
        history to test, which is expected and not a problem.
      * ``evaluation_engine.recommendation_tracking_report()`` returns zero
        rows (no BUY signals logged yet, or none old enough to have a
        resolvable model/actual comparison).
      * Any exception while building the report or running the detector —
        this check's job is to surface an early-warning signal, not to add
        a new failure mode to the go-live gate.

    When drift IS detected, an alert is also dispatched via
    ``validation.drift.check_and_alert_recommendation_drift`` (WARNING level,
    ``observability.alerts.send_alert``) so the same signal reaches the
    operator's configured alert channels, not just the preflight table.
    """
    name = "calibration_drift"
    try:
        from evaluation_engine import recommendation_tracking_report
        from validation.drift import check_and_alert_recommendation_drift

        report = recommendation_tracking_report()
        rows = report.get("rows", [])
        if not rows:
            return CheckResult(
                name, True,
                "Insufficient history — no live-vs-recommendation tracking rows "
                "available yet (output/decision_log.jsonl empty or missing). "
                "This is expected on a fresh deployment.",
                warning=True,
            )

        result = check_and_alert_recommendation_drift(rows, metric="calibration_error", method="cusum")
        if result.drift_detected:
            return CheckResult(
                name, True,
                f"⚠️  Calibration drift detected (method={result.method}, "
                f"drift_index={result.drift_index}, n_samples={result.details.get('n_samples')}) "
                "in the operator-vs-model judgment edge. A WARNING alert has been "
                "dispatched via observability.alerts. Consider re-running "
                "validation/harness.py for the affected strategy.",
                warning=True,
            )
        return CheckResult(
            name, True,
            f"No calibration drift detected across {len(rows)} tracked signal(s).",
        )
    except Exception as exc:
        # Never a blocking failure — a broken drift check must not gate go-live.
        return CheckResult(
            name, True,
            f"⚠️  calibration_drift check could not run ({exc}) — treating as "
            "insufficient history rather than a failure.",
            warning=True,
        )


def check_alert_channels_reachable() -> CheckResult:
    """WARNING-ONLY: probe every currently-active alert channel and report
    reachability via ``observability.alerts.check_channel_health()``.

    This is deliberately **never blocking** (``CheckResult.warning=True``
    unconditionally) — a broken Discord webhook or unreachable SMTP relay is
    an operational annoyance an operator should fix, not a reason to refuse
    go-live. The value of this check is purely diagnostic: without it, a
    broken channel is discovered only when a REAL alert silently fails (see
    ``observability/alerts.py``'s "Failure isolation invariant"), which is
    easy to miss in normal log volume and especially costly to discover for
    the first time during an actual incident.

    Degrades gracefully to PASS (with a note, never a FAIL) if the health
    check itself raises — a broken diagnostic must not become a new failure
    mode for the go-live gate.
    """
    name = "alert_channels_reachable"
    try:
        from observability.alerts import check_channel_health

        results = check_channel_health()
        if not results:
            return CheckResult(
                name, True, "No alert channels configured (console-only).", warning=True,
            )
        broken = {ch: r for ch, r in results.items() if not r.get("ok")}
        if broken:
            detail = "; ".join(f"{ch}: {r.get('error')}" for ch, r in broken.items())
            return CheckResult(
                name, True,
                f"⚠️  {len(broken)}/{len(results)} alert channel(s) unreachable — {detail}. "
                "Fix before relying on this channel during an incident.",
                warning=True,
            )
        return CheckResult(
            name, True,
            f"All {len(results)} active alert channel(s) reachable: {', '.join(results)}.",
            warning=True,
        )
    except Exception as exc:
        # Never a blocking failure — a broken health-check must not gate go-live.
        return CheckResult(
            name, True,
            f"⚠️  alert_channels_reachable check could not run ({exc}).",
            warning=True,
        )


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
    check_alpaca_key_rotation_recent,
    check_advisory_only_active,
    check_robinhood_execution_mode,
    check_robinhood_kill_switch_clear,
    check_robinhood_queue_fresh,
    check_robinhood_session_present,
    check_alpaca_configured,
    check_macro_regime_gate_enabled,
    check_broker_backend_matches_live_intent,
    check_alpaca_paper_mode,
    check_dry_run_disabled,
    check_env_not_committed,
    check_env_no_duplicate_keys,
    check_kill_switch_inactive,
    check_state_snapshot_fresh,
    check_heartbeat_fresh,
    check_daemon_pid_alive,
    check_db_exists,
    check_no_stray_database_files,
    check_output_dir_matches_local_data_root,
    check_paper_trading_duration,
    check_validation_reports,
    check_no_unexpected_risk_blocks,
    check_calibration_drift,
    check_alert_channels_reachable,
]


# Checks that are auto-skipped when ADVISORY_ONLY=True.
# Two categories:
#   (a) Broker-dependent (5): no broker stack means these have no meaning.
#       Includes alpaca_key_rotation_recent — Alpaca keys have no blast-radius
#       risk while the broker surface is quarantined.
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
_ADVISORY_AUTO_SKIP: dict[str, str] = {
    "alpaca_configured": (
        "ADVISORY_ONLY=True — broker credentials not required; "
        "execution surface is quarantined"
    ),
    "alpaca_paper_mode": (
        "ADVISORY_ONLY=True — paper/live mode flag is irrelevant "
        "when no orders are submitted"
    ),
    "dry_run_disabled": (
        "ADVISORY_ONLY=True — DRY_RUN flag is superseded by "
        "execution surface quarantine"
    ),
    "paper_trading_duration": (
        "ADVISORY_ONLY=True — paper-trading clock does not apply "
        "when no orders are submitted"
    ),
    "alpaca_key_rotation_recent": (
        "ADVISORY_ONLY=True — Alpaca keys have no blast-radius risk while "
        "the broker surface is quarantined; rotation reminder only meaningful "
        "for live-trading deployments"
    ),
    "heartbeat_fresh": (
        "ADVISORY_ONLY=True — heartbeat is written only by "
        "main_orchestrator.py; advisory runs via main.py do not "
        "require a persistent orchestrator process"
    ),
    "validation_reports": (
        "ADVISORY_ONLY=True — validation reports gate live order "
        "submission; advisory mode produces signals only "
        "(no orders submitted to brokers)"
    ),
    "no_unexpected_risk_blocks": (
        "ADVISORY_ONLY=True — risk-gate blocks occur only on order "
        "submission; advisory mode never submits orders"
    ),
}


def run_checks(skip: list[str] | None = None, fire_alerts: bool = False) -> list[CheckResult]:
    """Execute all checks and return one ``CheckResult`` per check.

    Parameters
    ----------
    skip:
        List of check names (without the ``check_`` prefix) to skip.
        Skipped checks produce a PASS result with reason "(skipped via --skip)"
        so the result list always has ``len(ALL_CHECKS)`` entries.
    fire_alerts:
        When True, threaded through to ``check_validation_reports(fire_alert=...)``
        so a FAILing validation_reports check also dispatches a CRITICAL alert
        via ``observability.alerts.send_alert``. Defaults to False — an
        interactive, ad-hoc preflight run should not page anyone; only a
        scheduled/CI invocation (``--fire-alerts`` on the CLI) opts in. See
        ``check_validation_reports``'s own docstring for the full rationale.

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
            if check_name == "validation_reports":
                results.append(fn(fire_alert=fire_alerts))
            else:
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
    parser.add_argument(
        "--fire-alerts",
        action="store_true",
        help=(
            "Dispatch a CRITICAL alert via observability.alerts.send_alert when "
            "validation_reports fails. Off by default so an interactive run "
            "never pages anyone; intended for a scheduled/CI invocation of "
            "this script."
        ),
    )
    parser.add_argument(
        "--validation-staleness-only",
        action="store_true",
        help=(
            "Run ONLY the validation_reports check (strategy backtest staleness "
            "and deployability), bypassing the ADVISORY_ONLY auto-skip that "
            "applies to the full go-live gate, and always firing an alert on "
            "FAIL (fire_alert=True). Intended for a daily scheduled cron "
            "invocation independent of the go-live gate below — strategy "
            "health is worth monitoring whether or not the platform is "
            "submitting live orders. Ignores --skip and --fire-alerts."
        ),
    )
    args = parser.parse_args(argv)

    if args.validation_staleness_only:
        result = check_validation_reports(fire_alert=True)
        if args.json:
            print(json.dumps({
                "name": result.name,
                "passed": result.passed,
                "warning": result.warning,
                "reason": result.reason,
            }, indent=2))
        else:
            _print_table([result])
        return 0 if result.passed else 1

    results = run_checks(skip=args.skip, fire_alerts=args.fire_alerts)

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
    # Venv re-exec + .env loading -- placed here (not at module top)
    # because this module is also imported as a library by
    # Gravity AI Review Suite.py; a module-top call would fire the
    # re-exec check on every such import, not just when this file is
    # the actual entry point. See scripts/_bootstrap.py's module
    # docstring for the full rationale.
    from scripts._bootstrap import bootstrap
    bootstrap()
    sys.exit(main())

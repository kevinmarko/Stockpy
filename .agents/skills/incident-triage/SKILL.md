---
name: incident-triage
description: >-
  Triage a live incident on the InvestYo platform -- diagnose the symptom
  against docs/RUNBOOK.md's real playbooks, pull evidence via the
  read_platform_logs MCP tool and docs/known_issues/, and record the
  resolution in docs/incident_log.md. Use when investigating an orchestrator
  failure, a stale/missing recommendation, a data-source outage, or when
  deciding whether to pause the advisory pipeline via the kill switch.
---

<!--
  Ported from this repo's Claude Code sibling skill (`.claude/skills/incident-triage/SKILL.md`)
  to Antigravity's skill format. Frontmatter and body content are carried over verbatim --
  Antigravity's own `google-antigravity-sdk` skill and this repo's existing `.agents/skills/supabase`
  skill both use the same minimal `name` + `description` frontmatter shape Claude's SKILL.md already
  used here, so no restructuring was required for this port beyond this note.
-->

# Incident triage

This platform is **advisory-only by default** (`ADVISORY_ONLY`, see
CLAUDE.md/AGENTS.md's safety posture) — most of `docs/RUNBOOK.md`'s §3
incident playbooks are written for that mode: "emergency shutdown" means
**pausing signal generation**, not halting live orders, unless you've
confirmed live execution is actually enabled (`ROBINHOOD_EXECUTION_MODE` /
`ALPACA_PAPER=False`) for this deployment. Check that first — several
RUNBOOK sections are explicitly marked "⚠ N/A in Advisory Mode" (§3.8-3.10)
and don't apply unless live execution is on.

## 1. Match the symptom to a real RUNBOOK section first

Don't improvise a diagnosis — `docs/RUNBOOK.md` §3 (Incident Response) has a
named section for each of these, with exact commands and root-cause tables.
Read the matching section in full before acting:

| Symptom | RUNBOOK section |
|---|---|
| Stale account snapshot (`Snapshot age > 20h`, "Using stale cache" warning) | §3.1 |
| A held symbol has no Action Signal / Dead-Letter Queue entry | §3.2 |
| Calibration MAE climbing above 0.10 | §3.3 |
| "No validation reports" / stale validation report | §3.4 |
| Portfolio heat exceeding `MAX_PORTFOLIO_HEAT` | §3.5 |
| `"RH_USERNAME is missing"` despite `.env` having it set | §3.5b (now a *historical* note — two root causes already fixed, see below) |
| A `scripts/*.py` script fails with `ModuleNotFoundError` under bare `python3` | §3.5c (also fixed — `scripts/_bootstrap.py::bootstrap()` now self-corrects the interpreter) |
| HMM regime showing high risk-off | §3.6 |
| "GJR-GARCH failed to converge" warning | §3.7 |
| Database backend outage | §3.11 |
| MCP client shows "Server Disconnected" | §3.12 |
| How to read `output/daemon.json` | §3.13 |
| Shutdown taking longer than expected | §3.14 |

§3.5b/§3.5c are worth reading even though they describe *fixed* bugs — if
either symptom resurfaces, that's a regression, and the section's own
"Verify" block (`pytest tests/test_env_loading.py
tests/test_robinhood_portfolio.py -v` / `pytest tests/test_scripts_bootstrap.py -v`)
is the fast way to confirm it before assuming something new is wrong.

## 2. Pull evidence

**`read_platform_logs`** (the real MCP tool — `mcp__investyo-platform__read_platform_logs`
if the MCP server is connected, or `investyo_mcp_server.py::read_platform_logs(lines=50)`
directly) pulls two things: (1) the most recent `ExecutionLogs` DB rows
(timestamp/status/ticker_count/duration/error_message), and (2) a tail of
the real rotating log file — `logs/investyo.log`, written by
`alerting.py::setup_logging()`'s `RotatingFileHandler` (10 MB × 5 backups).
**Note the fix this tool already needed once**: it used to only
`os.listdir(".")`, so it never found `logs/investyo.log` one directory down
— now it checks `logs/` first, then the cwd. If you're troubleshooting this
tool itself rather than using it, that's the exact bug class to check for.

**`docs/known_issues/`** — check here before assuming a symptom is new.
Real files, as of this writing (confirm the current list with `ls
docs/known_issues/` — this directory grows):

- `robinhood_device_approval_login_hang_risk.md` — the device-approval login
  worker has no timeout of its own inside `robin_stocks`; relevant to any
  "stuck logging in" / stale-credentials symptom (RUNBOOK §3.1's table).
- `cnn_lstm_tf_deadlock.md` — TensorFlow/pandas import-order deadlock in
  `ForecastingEngine.run_cnn_lstm_forecast()`; relevant to a forecasting
  stage hang or timeout (RUNBOOK §3.2's `forecasting` stage row).
- `lightgbm_faiss_libomp_collision_segfault.md` — OpenMP thread-pool
  collision between lightgbm and faiss; relevant to a segfault/deadlock
  anywhere near `ml.meta_bootstrap` or `data/rag_index.py`.
- `pip_audit_stale_ambient_env_false_positive.md`,
  `react_router_dom_ghsa_jjmj_open_redirect.md`,
  `vite_plugin_pwa_workbox_dev_chain_unfixable.md`,
  `2026_08_security_quality_review.md` — dependency/security-scan findings;
  check these before escalating a `pip-audit`/`npm audit` finding as new.

Don't cite a `docs/known_issues/` filename you haven't actually opened — the
list above is a starting point, not a substitute for reading the specific
file that matches your symptom.

## 3. Pause, if warranted — the real mechanism

In advisory mode, "emergency shutdown" = pausing signal generation via the
**same sentinel file the kill switch uses** (`docs/RUNBOOK.md` §6):

```bash
# Pause:
python -m execution.kill_switch --activate --reason "advisory pause — investigating anomaly"
# Confirm the pipeline sees it:
python3 main.py
# Expected: INFO — Advisory paused by kill-switch sentinel — skipping evaluation cycle.

# Check status any time:
python -m execution.kill_switch --status

# Resume, after root cause is resolved:
python -m execution.kill_switch --deactivate
python scripts/preflight_check.py   # should exit 0 before resuming
python3 main.py
```

(`--activate`/`--deactivate`/`--status` are mutually exclusive flags on
`execution/kill_switch.py`'s real argparse; `--reason` is `--activate`-only.)

The Pilots PWA is a second front-end for this exact sentinel — `POST
/automation/pause`/`/resume` on `api/pilots_api.py` call the same
`GlobalKillSwitch`, not a separate mechanism. Two things worth knowing if
triaging from there: pausing does **not** stop the daemon's interval timer
(cycles keep running, just producing no recommendations), and remote resume
is refused whenever `ADVISORY_ONLY=false` (live order submission enabled) —
deactivate at the console in that case (RUNBOOK §6's note).

**When to pause** (RUNBOOK §6's table): calibration MAE > 0.15; a held
symbol's recommendation missing > 5 consecutive days; account snapshot stale
> 48h even after a forced refresh; macro regime shows RECESSION AND the HMM
agrees; or suspicious pipeline output (all signals identical / all BUY / all
NaN).

Back up the DB before any destructive investigation:
```bash
cp quant_platform.db quant_platform_backup_$(date +%Y%m%d_%H%M%S).db
```

## 4. Record it — `docs/incident_log.md`, every time

This file's own header: *"Chronological record of operational anomalies,
pauses, and remediations. Each entry is appended; never edited or
deleted."* The real template, copied verbatim from the file:

```markdown
### YYYY-MM-DD — short title

- **Detected:** how the anomaly surfaced (preflight failure, calibration MAE
  spike, dead-letter queue entry, manual observation)
- **Symptom:** observable state at detection
- **Root cause:** what was actually wrong
- **Remediation:** what was done; reference commits/PRs by SHA or URL
- **Pause taken?** yes / no — if yes, link to the matching decision_log.jsonl entry
- **Follow-up:** open items, watchlist entries, Gravity steps added
```

Pair this with `output/decision_log.jsonl` (via the Reports tab → Decision
Journal, entry type `"modified"`) for the per-signal operator log RUNBOOK §6
references — the incident log is the narrative record, `decision_log.jsonl`
is the structured per-symbol trail the calibration tracker correlates
against.

## 5. Common failure modes & fixes (beyond what's already in RUNBOOK §3)

**Orchestrator daemon looks alive but isn't responding.** RUNBOOK §3.13
("How to Read `output/daemon.json`") is the first stop — a `SIGKILL`ed
daemon can never write its own "stopped" state, so `state: "running"` in
that file can be stale. Check `pid_alive` in the same file/`GET
/automation/status` response before trusting the `state` string alone (see
CLAUDE.md's "`output/daemon.json` staleness fix" bullet for why the file's
self-reported `state` is not externally verified but `pid_alive` is).

**Split-brain / double-submitted orders (live execution only — N/A in pure
advisory mode).** Confirm only one `main_orchestrator.py`/daemon process is
running (`ps aux | grep orchestrator`), and check for a stale process
holding `ORCHESTRATOR_API_PORT` (default 8601) per CLAUDE.md's
"Operational note" under the persistent orchestrator daemon bullet — a
stale daemon holding the port doesn't crash a new one, it just delays its
`output/daemon.json` write up to 5s while it polls, which can look like a
hang.

**A `.env` value looks set but the code reports it as missing.** This is
RUNBOOK §3.5b's exact symptom — read that section before assuming it's new;
the two root causes it documents (`os.environ` read instead of
`settings.X`; three disagreeing `.env` locators before `settings.ENV_PATH`
was introduced) are a real, previously-hit bug class in this codebase and
worth checking for recurrence with `pytest tests/test_env_loading.py -v`
before digging further.

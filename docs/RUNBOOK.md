# InvestYo Advisory Platform — Runbook

Operational reference for day-to-day use, incident response, and maintenance.

> **Advisory mode is the project default (`ADVISORY_ONLY=true`).**
> In this mode the pipeline runs end-to-end — data fetch, indicators, forecasts,
> position sizing, HTML report — but **never submits orders to any broker**.
> Sections that describe broker-execution behaviour are marked **⚠ N/A in advisory mode**
> and are retained so future operators who lift the quarantine have a complete reference.
> See `docs/HOW_TO_GUIDE.md → "Advisory-Only Mode"` for the procedure to re-enable
> broker execution.

---

## 0. Everyday Startup (macOS double-click)

The platform's only UI is the Pilots PWA (`webapp/`). The old Streamlit desktop app
(`launch_app.command`, `launch_gui.command`, `legacy/streamlit_command_center/`) was
deleted in 2026-09; git history has it.

**Double-click `launch_webapp.command`** in Finder or the Dock to start the web app. It asks
whether to run against offline mock data (the default) or live data. In live mode it starts
`data_api` (`:8603`) and `metrics_api` (`:8604`) if they aren't already up, starts the
orchestrator daemon (Control API `:8601` + Pilots API `:8602`) when
`ORCHESTRATOR_DAEMON_ENABLED=true` and neither port is up, and writes `webapp/.env.local` so
the app points at them. It does not run the pipeline for you. For a backend that keeps
running with no Terminal window open, see §0.1.

`launch.command` (headless interval loop) still works for headless/scripted runs:

1. Verifies `.venv` exists and Python is exactly 3.12.x before starting.
2. Prints a clear error (and pauses for you to read it) if either check fails.
3. `launch.command` runs `python main.py --interval 60` by default — refreshes every
   60 s until you close the window.
4. Pauses with "Press any key to close" after exit so final output is always visible.

**To change the interval**: open `launch.command` in any text editor, set
`REFRESH_INTERVAL_SECONDS=N` at the top (`0` = single run).

**If `.venv` is missing** (e.g., fresh clone):

```bash
cd /Users/kevinlee/Desktop/Stockpy
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Then double-click the launcher again.

**If the wrong Python version is detected**: the launcher tells you which version was found
and how to recreate `.venv` with Python 3.12.

**Running the pipeline from the web app**: the **Pipeline** screen (`/pipeline`) shows the
orchestrator daemon's live status, run history, and dead-letter queue, and has the run
triggers ("Run full advisory pipeline" plus stage-scoped ones). Runs go through the daemon.
The old desktop app's live 0–100% progress bar (`output/progress.json`) has no web app
equivalent yet.

**Strategy validation**: the **Commands** screen (`/commands`) can compose and (with
`COMMAND_EXECUTION_ENABLED=true`) run `scripts.refresh_validations`, including a
"🧪 Bulk Validate All Strategies" button. The **Strategy Health** screen shows validation
results per pilot.

The **daily HTML report** leads with a **"Δ Since Last Run" band**: new BUYs, action
flips, conviction moves (`|Δ| ≥ SNAPSHOT_CONVICTION_DELTA_THRESHOLD`, default 0.20),
holdings added/dropped, and regime changes. Powered by rotated state snapshots in
`output/history/` (pruned after `SNAPSHOT_HISTORY_DAYS=30` days). The band is hidden
on first ever run.

**Not sure what a term means?** The web app's **Help & Glossary** screen (`/help`) has a
searchable glossary, and each screen has a dismissible "How this works" panel.

The **Calibration** screen (`/calibration`) holds the **Decision Journal** (log a decision
per signal; entries go to `output/decision_log.jsonl`) and **Conviction Calibration**
(whether stated conviction scores match empirical win rates per bin). The **Attribution**
screen (`/attribution`) has the **Brinson-Fachler** allocation / selection / interaction
breakdown.

### 0.1 Always-On Backend for the Webapp (recommended)

The Pilots PWA (`webapp/`) is the platform's only frontend (the Streamlit desktop app was
deleted in 2026-09). This subsection is the operator sequence for running the webapp against
the always-on backend stack that already exists in this repo
(`scripts/com.investyo.stack.plist` + `scripts/investyo_stack_service.sh`), so the refresh
loop and APIs keep running with no Terminal window open.

1. **Install (or confirm) the always-on backend stack service** — double-click
   `scripts/install_stack_service.command` (idempotent; safe to re-run). This installs the
   `com.investyo.stack` launchd job (`RunAtLoad` + `KeepAlive`), which starts at login and
   keeps running with no app window open: the orchestrator daemon (warm refresh every ORCHESTRATOR_INTERVAL_SECONDS
   cycles + Control API `:8601`) plus `data_api` (`:8603`) and `metrics_api` (`:8604`) as
   separate processes. It also unloads the older single-run `com.investyo.daily-advisory`
   job, which this supersedes (see §5.1). Verify with `launchctl list | grep com.investyo.stack`
   and `tail -f output/stack_daemon.log`.
2. **Set `PILOTS_API_ENABLED=true` in `.env`** (default `False`) so the same always-on daemon
   process also hosts the Pilots API (`api/pilots_api.py`) on `:8602`, rather than it needing
   a separately-launched `uvicorn` server. This flag is read once at daemon startup — this is
   the one setting genuinely required for the webapp to have a live backend running
   continuously, independent of any window.
3. **Restart the stack so the flag takes effect** — re-run
   `./scripts/install_stack_service.command` (it unloads + reloads `com.investyo.stack`), or
   manually:
   ```bash
   launchctl unload ~/Library/LaunchAgents/com.investyo.stack.plist
   launchctl load   ~/Library/LaunchAgents/com.investyo.stack.plist
   ```
   **This same restart is required any time you add or change `STATE_API_TOKEN`,
   `FOLLOW_API_TOKEN`, or `ORCHESTRATOR_DAEMON_TOKEN` in `.env`** — every one of these is
   read once at process startup (`pydantic-settings`, no live reload), so an already-running
   backend keeps serving with the OLD value (typically surfacing as a 403 "Command endpoint
   disabled: `<TOKEN>` not configured" even though the token IS set in `.env` on disk) until
   restarted. The in-app "Restart daemon" webapp button **cannot** fix a stale
   `ORCHESTRATOR_DAEMON_TOKEN` on its own — `POST /daemon/restart` is itself gated by that
   same stale token — so this out-of-band restart is the only way out of that specific trap.

   **If you also run `launch_webapp.command` in live mode, check which process actually owns
   each port before assuming the stack restart above was enough**: `launch_webapp.command`'s
   `_start_api` helper only starts `api.pilots_api:app` / `api.control_api:app` / etc. as their
   own standalone `uvicorn` processes when nothing is already listening on that port — if one
   is already up, it silently *reuses* it rather than restarting it, even on a later re-run of
   `launch_webapp.command`. So a standalone process started this way can end up outliving (and
   never being touched by) a `com.investyo.stack` restart. Confirm with:
   ```bash
   lsof -nP -iTCP:8601,8602,8603,8604 -sTCP:LISTEN
   ```
   and cross-reference each PID's start time (`ps -p <pid> -o lstart,command`) against your
   `.env` edit time — anything older needs killing and relaunching by hand (or via
   `launch_webapp.command`, once the stale one is gone) before it will pick up the new token.
4. **Point the webapp at the live backend** — per `webapp/README.md`: set
   `VITE_USE_MOCK=false` (default `true`) in `webapp/.env.local`, plus `VITE_API_BASE_URL`
   (default `http://localhost:8602`) / `VITE_API_TOKEN` if they differ from the defaults, then
   `npm run dev` (or build/serve for a standing install). No component code changes —
   `src/api/client.ts` is the single mock/live switch point.

**What this does NOT require**: no data-layer or pipeline change. The whole sequence above
is: one `.env` flag, confirming a launchd service that already ships in this repo, and the
two webapp env vars `webapp/README.md` already documents for its own mock↔live switch.

### 0.2 Caddy + Tailscale (Remote/Production Access)

Once the always-on backend stack from §0.1 is running, `investyo_stack_service.sh` also starts
a **Caddy** reverse proxy on **`127.0.0.1:8888`** (`scripts/Caddyfile`, deliberately
loopback-only — see the comment at the top of that file) that fronts all four backend ports
(Pilots API `:8602` under `/svc/*`, Data API `:8603`, Metrics API `:8604`, Control API `:8601`)
plus the built Pilots PWA static bundle (`webapp/dist`) — a single port to reach the whole
platform from a phone or another machine on your **Tailscale** tailnet, over HTTPS, without
opening anything to the public internet **or your local LAN** (Caddy itself never listens on
anything but loopback; `tailscale serve` in step 3 below is the only thing that publishes it,
and only to devices signed into your own tailnet).

1. **Sign in to Tailscale** (one-time, if not already): `sudo brew services start tailscale &&
   sudo tailscale up`, then confirm with `tailscale status` (note the MagicDNS hostname it
   prints, e.g. `your-machine.your-tailnet.ts.net` — you'll need it in step 6). Also install and
   sign in to the Tailscale app on your phone/other device.
2. **Install Caddy** (one-time): `brew install caddy`. Verify: `caddy version`.
3. **Build the webapp bundle** (whenever `webapp/` source has changed): `./scripts/build_webapp_prod.sh`.
   This is a manual/pre-deploy step, not run automatically by the stack service (mirrors how
   `.venv` is built once by `setup.sh`, not rebuilt on every service start) — if `webapp/dist`
   is missing, the stack service now warns and skips starting Caddy rather than serving a 404
   (see the guard in `scripts/investyo_stack_service.sh`).
   **This script also owns `webapp/.env.production.local`** (the build-time config that points
   the built bundle at your Tailscale origin instead of `localhost` — see `webapp/.env.example`)
   — on first run it auto-generates that file from `tailscale status` (step 1 must already be
   done), and on every run it refuses to build at all if `VITE_USE_MOCK` isn't explicitly
   `false` in it, so this can never silently ship a build that fabricates mock data instead of
   showing your live backend. The file is gitignored (`*.local`) and safe to hand-edit; delete
   it to force regeneration against the current Tailscale hostname.
4. **Expose the proxy over Tailscale** (**one-time, per machine** — not a per-start step):
   ```bash
   tailscale serve --bg --https=443 / http://127.0.0.1:8888
   ```
   This config is **persistent**: it survives reboots and `tailscale down`/`up` cycles, and is
   only cleared by `tailscale serve reset` or a full `tailscale logout`. Verify anytime with:
   ```bash
   tailscale serve status
   # https://<your-machine>.<your-tailnet>.ts.net (tailnet only)
   # |-- / proxy http://127.0.0.1:8888
   ```
5. **Restart (or just confirm) the stack service** so Caddy picks up a freshly-built `webapp/dist`
   — same restart sequence as §0.1 step 3. Caddy itself starts/stops automatically as part of
   `investyo_stack_service.sh`; there is no separate launchd job for it.
6. **Reach it**: `https://<your-machine>.<your-tailnet>.ts.net/` from any device signed into the
   same tailnet (phone, laptop, etc.) — no VPN config, no port-forwarding, no public exposure.
   **The first load from a new device shows a `TokenGate` prompt** — this is a non-loopback
   origin, so the webapp asks for the backend's `STATE_API_TOKEN` once (stored only in
   `sessionStorage`, never baked into the build — see `webapp/src/components/TokenGate.tsx`).
   Get the value from `.env`'s `STATE_API_TOKEN` on this machine (not from a chat transcript or
   any tool that might echo it) and enter it there.

**Troubleshooting**:
```bash
tail -f output/stack_caddy.log            # Caddy's own stdout/stderr
tailscale serve status                    # confirm the tailnet mapping is still active
curl -sf http://localhost:8888/           # confirm Caddy itself is serving locally
curl -sf https://<tailnet-hostname>/      # confirm the Tailscale path end-to-end
```
If `output/stack_caddy.log` doesn't exist and `stack_daemon.log`/the other two logs show the
rest of the stack came up fine, check for a `WARNING: caddy binary not found` or
`WARNING: webapp/dist/index.html not found` line in the service's own stderr (`launchctl list`
→ check the job's `StandardErrorPath`, or `output/` if using the older foreground launcher) —
Caddy is deliberately optional and skips itself rather than crash-looping the whole stack over
a missing binary or an unbuilt bundle (see the guard comment in
`scripts/investyo_stack_service.sh`). If the page loads but shows stale/fabricated-looking data,
check `webapp/.env.production.local`'s `VITE_USE_MOCK` — `scripts/build_webapp_prod.sh` refuses
to build with it unset/true, but a build produced before that guard existed could still be
sitting in `webapp/dist`; rebuild.

---

## 1. ⚠ N/A in Advisory Mode — Paper → Live Switch

> **This section is suppressed while `ADVISORY_ONLY=true`.**
> The pre-launch readiness check (`scripts/preflight_check.py`) automatically skips six
> checks: three broker-readiness checks (`paper_trading_mode`,
> `dry_run_disabled`, `paper_trading_duration`) and three
> runtime-state checks that are false-positives in advisory mode (`heartbeat_fresh`,
> `validation_reports`, `no_unexpected_risk_blocks`) — and instead passes a single
> `advisory_only_active` check. `robinhood_execution_mode` and `state_snapshot_fresh` are
> never auto-skipped (see the Robinhood Execution Bridge section below).

**To re-enable broker execution (future use):**

1. Set `ADVISORY_ONLY=false` in `.env`.
2. Re-run `python scripts/preflight_check.py` — it now enforces all broker-readiness
   checks, including `paper_trading_duration` (≥ 90 days).
3. Follow the original paper→live procedure documented below once all checks pass.

> **Alpaca was removed 2026-09-30.** The automated pipeline's only broker is the local
> FMP paper ledger. With `PAPER_TRADING=false` and `ADVISORY_ONLY=false` (an old
> `ALPACA_PAPER=false` in `.env` is an alias) `resolve_broker_backend()` returns None and the
> pipeline places **no orders** — it logs CRITICAL and sends an alert. Real money moves
> only through the Robinhood execution queue (sections below), with per-trade human
> confirmation. The steps below therefore cover the paper posture and the going-live
> posture check, not a live Alpaca cut-over.

### Pre-switch (T-1 day) — ⚠ BROKER EXECUTION REQUIRED

1. Complete every item in `docs/GO_LIVE_CHECKLIST.md`.
2. Run `python scripts/preflight_check.py` — must exit **0** (all checks including broker
   ones must pass; `advisory_only_active` will warn that `ADVISORY_ONLY=False`).
3. Notify all stakeholders that live trading begins the next session.
4. Ensure the kill switch is **INACTIVE**: `python -m execution.kill_switch --status`

### Day-of switch (pre-market, ≥ 30 min before open) — ⚠ BROKER EXECUTION REQUIRED

1. Rotate `.env` values: **`PAPER_TRADING=false`** and **`ADVISORY_ONLY=false`** (going-live posture).
2. Verify via Settings → General (or `from settings
   import settings; assert settings.PAPER_TRADING is False`).
3. Start the orchestrator in **dry-run** once:
   ```
   python3 main_orchestrator.py --dry-run
   ```
   Expect the CRITICAL log and alert `no automated broker when going live` — that is the
   correct, fail-closed behavior (no orders are placed by the automated pipeline).
4. Place real orders only via the Robinhood execution queue (see the Robinhood Execution
   Bridge sections), one human confirmation per trade.
5. Confirm the Robinhood account positions against `transactions_store` after each fill.

**Switching back to Paper / Simulation** works identically — pick the other mode on
Settings → General, or set `PAPER_TRADING=true`. Setting `ADVISORY_ONLY=true` returns the
platform to the default quarantine state regardless of `PAPER_TRADING`.

---

## 1.1 Phone Push Alerts (ntfy.sh)

`main.py` sends push notifications via ntfy.sh (`alerting.py`). Configure once; no
account required.

**Setup**:

1. Install the **ntfy** app (iOS / Android).
2. In `.env`: set `NTFY_TOPIC` to a long random string (e.g. `investyo-abc123xyz`).
3. In the ntfy app: subscribe to that exact topic name.

**What you will receive**:

| Notification | Priority | When |
|---|---|---|
| ⚠ Errors Detected | HIGH (audible) | Any symbol-level pipeline failure |
| ✓ Refresh Complete | Default | Once per launch |

The error alert lists the failing symbol and pipeline stage so you can triage without
opening the log. In `--interval` mode, the "refresh complete" alert fires only on the
first clean cycle to avoid spam.

**If you get an error alert**:

1. Check `logs/investyo.log` for the ERROR line — it will name the symbol, stage, and
   exception.
2. If the problem is a single bad ticker (data gap, API timeout), it is automatically
   dead-lettered — the run continues and other symbols are unaffected. No action required
   unless it persists.
3. If ALL symbols are failing, check network connectivity, FRED API key, and market data
   provider keys.

---

## 2. Pre-Market Checklist (Daily Advisory Run)

Run this EVERY trading morning before 09:00 ET:

| Check | Command / Action |
|-------|-----------------|
| **Start pipeline** | Web app **Pipeline** screen → "Run full advisory pipeline" (needs the backend from §0.1), or double-click `launch.command` |
| Advisory mode active | Web app status banner shows "Advisory Only Mode (Live Execution Disabled)" |
| Heartbeat recent | `ls -la output/heartbeat.txt` (< 2 h old); or web app **Mission Control** → Heartbeat |
| Preflight pass | `python scripts/preflight_check.py` (exit 0; `advisory_only_active` = PASS) |
| Account snapshot fresh | `python3 main.py --refresh-account` if snapshot age > 20 h |
| Holdings & P&L sane | Web app **Portfolio** screen — equity, buying power, per-position unrealized P&L. If empty, force refresh above. |
| No dead-letter failures | Web app **Pipeline** screen → Dead-Letter Queue (all symbols completed) |
| Δ Since Last Run reviewed | Open `output/daily_report.html` — check top band for unexpected action flips or conviction drops |
| Regime & VIX checked | Web app **Mission Control** → regime telemetry (Sahm Rule / HY OAS / VIX / regime) |
| Conviction calibration glanced | Web app **Calibration** screen → Conviction Calibration (win-rate bars near the diagonal) |
| **(post-deploy only)** DB tables landing in one place | After any deploy touching DB-path resolution (e.g. `settings.LOCAL_DATA_ROOT`-related changes): a daemon restart without error is NOT proof every code path picked up the new path — verify by direct inspection (`lsof` on the DB file, compare row counts/mtimes across old and new locations) that all tables are writing to the same, expected DB. See `docs/known_issues/forecast_tracker_local_data_root_split.md` for the concrete precedent (`forecast_errors` kept writing to the old DB for hours after a restart while every other table had moved). |

---

## 3. Incident Response

For systematic bug diagnosis, vulnerability classification, and root-cause investigation protocols, follow the [Stockpy Bug Hunting Process](BUG_HUNTING_PROCESS.md). Run `python scripts/bug_hunter.py` to execute automated static AST checks, webapp typechecks, and test verification gates.

### 3.1 Stale Account Snapshot

**Symptom**: Holdings & P&L panel shows `Snapshot age: Xh` > 20 h, or
`data/robinhood_portfolio.fetch_account_snapshot()` warns
`"Using stale cache (Xh old)"`. The "Δ Since Last Run" band may show incorrect
`added_holdings` / `dropped_holdings` because position changes haven't been picked up.

**Immediate action**:

```bash
# Force a live Robinhood refresh (bypasses the 20-h daily cache)
python3 main.py --refresh-account
```

Or from the web app: Settings → **🔑 Brokers & Keys** → Robinhood refresh (same
`force=True` fetch, via the device-approval login flow).

**Verify**:

```bash
python3 -c "
from data.robinhood_portfolio import fetch_account_snapshot
snap = fetch_account_snapshot(force=True)
print(f'Fetched at: {snap.fetched_at}  Positions: {len(snap.positions)}')
"
```

**Root causes to check**:

| Cause | Fix |
|-------|-----|
| Robinhood device-approval challenge triggered | Run `python3 main.py --refresh-account` from a real terminal; approve the login on your phone within `RH_LOGIN_DEADLINE_SECONDS` (180s default) — or use the webapp's Settings → Connect/Refresh, which drives the same isolated login worker |
| No approval arrived before the deadline | Nothing was saved (see `docs/known_issues/robinhood_device_approval_login_hang_risk.md`); just retry — the login worker never leaves a half-authenticated state behind |
| `RH_USERNAME` / `RH_PASSWORD` invalid | Verify credentials; Robinhood sometimes forces a password reset after a security event |
| Network partition during overnight run | Retry manually; stale cache is returned (not an error) on live-fetch failure — the platform degrades gracefully |
| Cache file corrupt | Delete `cache/account_snapshot.json` and re-run; a missing cache triggers a live fetch |

**When to escalate**: if live fetch fails AND no cache exists, the platform logs an error
and evaluates only watchlist symbols (held positions are temporarily missing from the
universe). Verify the next run picks up holdings again; if not, check Robinhood API
availability.

---

### 3.2 Missing Recommendation for Held Symbol

**Symptom**: The HTML report or observability dashboard shows one of your Robinhood
holdings without an Action Signal (blank, `—`, or `PARTIAL` data quality), while other
symbols completed normally. The web app **Pipeline** screen's Dead-Letter Queue may show the
symbol with a stage and exception.

**Immediate action**:

1. Open the web app **Pipeline** screen → Dead-Letter Queue. Note the `stage` and `error`
   for the affected symbol.
2. Click **Retry** next to the symbol (needs `DEAD_LETTER_RETRY_ENABLED` and the command
   token) — this re-runs only that ticker.
3. If retry also fails, check the error:

| Stage | Common cause | Fix |
|-------|-------------|-----|
| `dto_construction` | Price history unavailable (ticker delisted, bad symbol, market closed) | Confirm the symbol is valid and the market is open; check `data.market_data.get_provider()` |
| `strategy` | GARCH / options engine exception | See §3.7 (GJR-GARCH warning); verify `technical_options_engine.build_premium_directive` |
| `forecasting` | CNN-LSTM or ARIMA diverged | Run `python3 -m pytest tests/test_forecasting_lookahead.py -v`; check model inputs for NaN |
| `results` | Schema validation failure | Run `python scripts/preflight_check.py`; check `config.COLUMN_SCHEMA` for missing key |

4. If the failure is persistent (> 2 consecutive runs), reduce position exposure manually
   and add a note to `output/decision_log.jsonl` via the Decision Journal ("modified —
   pipeline unable to evaluate").

**Held-symbol safety rule**: A held symbol that fails market-data probe is classified
`EQUITY_ONLY` (not `UNCOVERED`) by `data.portfolio_sync.build_sync_report()`. Its
cost-basis-anchored equity is preserved in the Holdings view (`qty × avg_cost`) — no
fabricated current price. The equity view stays accurate even while the signal pipeline
cannot evaluate the symbol.

**When to escalate**: if the same held symbol fails for > 5 consecutive trading days AND
represents > 5% of portfolio equity, re-evaluate the position manually using external
sources. The platform is advisory; the operator retains all execution decisions.

---

### 3.3 Calibration Score Dropping Below Threshold

**Symptom**: web app **Calibration** screen → Conviction Calibration shows the reliability diagram's bars
systematically below the diagonal (the system claims high conviction but actual win rates
are lower). The Calibration Error (MAE) KPI climbs above `0.10` (10 pp average
discrepancy between stated conviction and empirical win rate).

**What this means**: The advisory signals have become over-confident. The strategy's
stated conviction scores no longer reflect empirical accuracy. Left unchecked, the
calibration tracker (Tier 1.2) will flag this; the Decision Journal will show more
"passed" entries than "acted" if the operator has been manually discounting signals.

**Diagnostic steps**:

```bash
# 1. Check how many conviction-annotated closed trades underpin the bins
python3 -c "
from transactions_store import TransactionsStore
from evaluation_engine import calibration_curve
store = TransactionsStore()
df = calibration_curve(store, n_bins=10, min_trades_per_bin=5)
print(df[['bin_center', 'win_rate', 'count', 'perfect_calibration']].to_string())
"
```

```bash
# 2. Check which signal modules are active and their weights
python3 -c "
from signals.registry import global_registry
from settings import settings
for name, mod in global_registry.get_all().items():
    w = settings.SIGNAL_WEIGHTS.get(name, 0)
    print(f'{name}: weight={w}')
"
```

**Response by severity**:

| MAE | Response |
|-----|----------|
| 0.05–0.10 | Monitor. Check if a specific conviction bucket (e.g. 0.7–0.8) is systematically wrong; reduce weight on the corresponding signal module via the web app's Settings → Strategy screen. |
| 0.10–0.15 | Re-run the strategy validation harness: `python -m validation.harness --strategy <name> --start 2015-01-01 --end 2024-12-31`. If PBO > 0.50 or DSR < 0.95, the strategy is no longer deployable. Reduce its `SIGNAL_WEIGHTS` entry to `0` until the next retrain cycle. |
| > 0.15 | Disable the strategy module via the web app's Settings → Strategy screen (`DISABLED_SIGNAL_MODULES`). Document the degradation in `output/decision_log.jsonl` (entry type: "modified"). Alert to re-evaluate the regime and signal architecture. |

**Minimum data requirement**: bins with fewer than 5 trades show `NaN` win rate (never
fabricated). A calibration MAE reading is only reliable once at least 30 conviction-
annotated closed trades exist. Before that threshold, the calibration diagram is
informational only — do not act on single-bin anomalies.

**Re-calibration procedure** (after signal weights are adjusted):

1. Run the pipeline for 5–10 trading sessions to accumulate new conviction-annotated
   trades (ensure `conviction` is being passed to `record_trade()` — check
   `transactions_store.TransactionsStore.record_trade`).
2. Re-check the calibration diagram. If MAE recovers below 0.10, remove the restriction.
3. If MAE does not improve, run the full validation harness and consider strategy
   retirement.

---

### 3.4 Validation Report Missing for Active Strategy

**Symptom**: Dashboard shows "No validation reports" OR `preflight_check.py` fails
`check_validation_reports` — OR you receive the daily
`scripts/preflight_check.py --validation-staleness-only` CRITICAL alert
(§5.4).

Reports are re-generated automatically every month (§5.4) and checked for
staleness every day, so seeing this should now mean the monthly cron job
itself stopped running (crontab not installed on the host, `.venv` broken,
network/yfinance outage, etc.) rather than someone simply forgetting to run
it — check `logs/validations.log` on the host first.

**Immediate action**:

1. Do NOT weight the strategy heavily until a fresh report is generated.
2. Re-run the harness by hand rather than waiting for next month's cron slot:
   ```bash
   python -m validation.harness --strategy <name> --start 2015-01-01 --end 2024-12-31
   # or, to re-run every registered strategy at once:
   ./scripts/refresh_validations.sh
   ```
3. If the strategy fails validation (PBO ≥ 0.50 OR DSR < 0.95 OR Sharpe < 0.50 OR
   MaxDD ≥ 30%), set its weight to 0 in `settings.SIGNAL_WEIGHTS` via the Strategy
   Matrix tab.
4. If the report was simply stale (the strategy itself still passes once
   re-run), also check why the monthly cron job didn't produce it —
   confirm `crontab -l` on the host actually contains the entries from
   `deploy/crontab.txt` (§5.4).

---

### 3.5 Portfolio Heat Exceeding Limit

**Symptom**: In a live-execution context, the risk gate would block new BUY orders with
`"portfolio_heat"` reason. In advisory mode the gate is informational — no orders are
submitted — but the observability dashboard still surfaces the heat metric.

**Normal response**: Review open positions to understand the source of adverse P&L.
The gate (when re-enabled) unblocks automatically once heat drops below
`settings.MAX_PORTFOLIO_HEAT`. No action required unless you are tracking this as an
overlay decision.

---

### 3.5b "RH_USERNAME is missing" but `.env` has it set

**Historical note (fixed 2026-08):** this section originally documented a real bug with
two independent causes, both now fixed. It's kept below in case a *new* variant of the
symptom resurfaces — the verify step at the bottom is the fast way to tell whether you're
looking at a genuine regression or something else.

**Symptom**: Log shows `ERROR - Live Robinhood fetch failed: Required environment
variable 'RH_USERNAME' is missing or empty` — yet your `.env` clearly contains
`RH_USERNAME=...`.

**Root cause #1 (fixed): `os.environ` read instead of `settings.X`.** `pydantic-settings`
reads `.env` into `Settings()` but does NOT propagate values to `os.environ`.
`data/robinhood_portfolio.py` used to read credentials via `os.environ.get()` directly,
so it saw empty strings unless `load_dotenv()` had been called first. Fixed: credentials
are now read via `settings.settings.RH_USERNAME`/`RH_PASSWORD`
(`_require_setting`, renamed from `_require_env`) — which loads `.env` independently
through pydantic-settings' own mechanism and needs no `load_dotenv()` call to see a
value at all.

**Root cause #2 (fixed): three `.env` locators disagreeing.** Even where `load_dotenv()`
*was* called, it used to be a bare `load_dotenv()` — which resolves the file via
`find_dotenv()`, walking UP from the calling file's directory. In a git worktree with no
`.env` of its own, this silently found a PARENT checkout's `.env` instead — the wrong
file, correctly reported as "missing" for a key that was only ever set in the *real*
`.env`. Fixed: `settings.ENV_PATH` (`Path(__file__).resolve().parent / ".env"`, anchored
at `settings.py`'s own location, not the process CWD or a directory walk) is now the
single anchor every `.env` locator in the codebase imports — `main.py`,
`main_orchestrator.py`, `desktop/orchestrator_daemon.py`, all five
standalone `api/*.py` FastAPI services, and every `scripts/*.py` entry point (via the new
`scripts/_bootstrap.py::bootstrap()` — see §3.5c below) all pass `ENV_PATH` explicitly.

**Verify**:

```bash
.venv/bin/python3 -m pytest tests/test_env_loading.py tests/test_robinhood_portfolio.py -v
```

All tests must PASS. If any fail, a regression has returned. A quick manual check from
any directory:

```bash
.venv/bin/python3 -c "from settings import settings; print('RH:', bool(settings.RH_USERNAME))"
```

should print `RH: True` if `.env` (next to `settings.py`, i.e. the repo root) has
`RH_USERNAME` set — regardless of your current working directory or which worktree you
run it from.

**Companion symptoms**:

- Robinhood login now uses device-approval push (2026-08) — there is no MFA secret to
  configure at all. Set `RH_USERNAME`/`RH_PASSWORD` only; approve the login attempt by
  tapping the notification in the Robinhood app. See
  `docs/known_issues/robinhood_device_approval_login_hang_risk.md`.
- `WATCHLIST` unset AND no `watchlist.txt` AND no held positions AND `settings.DEFAULT_TICKERS`
  empty → empty universe. Fix with: `WATCHLIST=SPY,QQQ,AAPL` in `.env`, `watchlist.txt`
  (one ticker per line), or `DEFAULT_TICKERS`. (The Sheet2-column-A Google Sheet
  last-resort fallback this used to also list was retired to `legacy/` in step 4e,
  2026-09 — see `legacy/README.md`.)
- First line of `.env` is a comment without `#` prefix → `python-dotenv could not parse
  statement starting at line 1`. Prefix the line with `#`.
- Running from a git worktree that has never had `.env` copied/symlinked into it: `.env`
  lives next to `settings.py` in the checkout you're actually running from (not shared
  automatically across worktrees, same as `.venv` — both are gitignored, per-checkout
  artifacts). Copy or hand-populate a `.env` in that worktree, or run from the primary
  checkout instead.

---

### 3.5c A `scripts/*.py` backfill script fails with a missing-package error under a bare `python3`

**Symptom**: `python3 scripts/backfill_news_history.py` (or any other `scripts/*.py`
entry point) fails with a `ModuleNotFoundError`, or a dependency that's clearly installed
in `.venv` behaves as if it's "not installed" (e.g. a `pandas`/`yfinance` import error
even though it is present in `.venv`).

**Root cause**: the invoking `python3` is not `.venv`'s interpreter (e.g. Homebrew or
system Python), which lacks project-only dependencies. `main.py`/`main_orchestrator.py`
self-correct via their own venv-reexec guard at module top; before 2026-08, no script
under `scripts/` did.

**Fix**: every `scripts/*.py` entry point now calls `scripts/_bootstrap.py::bootstrap()`,
which re-execs the process under `.venv`'s interpreter (if not already there) before
loading `.env`, mirroring `main.py`'s guard exactly. Running any script with a bare
`python3 scripts/whatever.py` now self-corrects automatically — no `source .venv/bin/
activate` or `.venv/bin/python3` prefix required, though either still works.

**Verify**:

```bash
.venv/bin/python3 -m pytest tests/test_scripts_bootstrap.py -v
```

All tests must PASS — this file statically confirms every `scripts/*.py` file actually
calls `bootstrap()` somewhere in its source.

---

### 3.6 HMM Says High Risk-Off

**Symptom**: HMM risk-on probability < `1 - HMM_RISK_OFF_BLOCK_THRESHOLD` (default 0.80);
in a live-execution context this would block BUY orders with `"hmm_regime"` reason. In
advisory mode this is surfaced as a macro-regime indicator only.

**Normal response**: Monitor. The gate clears automatically as the HMM model updates.
SELL signals are never blocked by the HMM gate. Do not override unless you have high
conviction the HMM is wrong AND documented reasoning.

---

### 3.7 "GJR-GARCH failed to converge" Warning

**Symptom**: Log shows `WARNING - GJR-GARCH failed to converge: ... Falling back to
20-day historical standard deviation.`

**Not a data-quantity problem** in most cases. If the message contains a Python
`TypeError` / `unexpected keyword argument`, it is an **API mismatch**:

**Fix (API break)**: `technical_options_engine.estimate_gjr_garch_volatility()` must call
`model.fit(update_freq=0, disp='off')` with NO `method=` kwarg. `arch ≥ 8.0` removed
it. Confirm:

```bash
.venv/bin/python3 -m pytest tests/test_quantitative_models.py -k garch -v
```

Both GARCH tests must PASS with no `arch` warning.

---

### 3.8 ⚠ N/A in Advisory Mode — Reconciliation Drift

> Reconciliation drift (`CRITICAL: RECONCILIATION DRIFT`) only occurs when
> `OrderManager` is submitting orders to a broker. While `ADVISORY_ONLY=true`, no orders
> are submitted and `reconcile_state()` is not called. If you have lifted the quarantine
> and see this symptom:
>
> 1. Activate the kill switch: `python -m execution.kill_switch --activate --reason
>    "reconciliation drift"`
> 2. Compare the paper ledger (Paper Broker screen) with the Robinhood account manually.
> 3. Fix the discrepancy, then deactivate: `python -m execution.kill_switch --deactivate`

---

### 3.9 ⚠ N/A in Advisory Mode — Kill Switch Fails to Block

> Only relevant when `ADVISORY_ONLY=false` and `OrderManager` is submitting orders.
> While quarantined, the kill switch sentinel (`output/KILL_SWITCH`) repurposes as the
> **pause-recommendations gate** — see §6.

---

### 3.10 ⚠ N/A in Advisory Mode — Broker Connection Lost

> `_execute_broker_orders` is not reached while `ADVISORY_ONLY=true`. The paper
> ledger (`FMPPaperBroker`) is a local SQLite store, so there is no broker connection to
> lose; if paper orders stop, check FMP quote availability (see the FMP troubleshooting
> section below) instead.

---

### 3.11 Database Backend Outage

**Symptom**: The remote DB backend (e.g. a Supabase Postgres project configured via
`DATABASE_URL`) is unreachable — DNS failure, network partition, or an IPv6-only
`db.<project>.supabase.co` host on an IPv4-only network. Logs show a single
`TransactionsStore` construction failure (e.g. `psycopg2.OperationalError: could not
translate host name … to address`) rather than a per-symbol error storm.

**What happens automatically**: This does NOT dead-letter every symbol. When
`TransactionsStore()` construction fails, both lazy call sites
(`engine.advisory._get_transactions_store()` and `StrategyEngine.transactions_store`)
catch the failure, log **once**, and substitute `transactions_store._OfflineTransactionsStore`
— a read-only stub whose `closed_trades_df()` / `open_trades_df()` return empty DataFrames.
Kelly sizing therefore sees "zero closed trades" and **degrades to the vol-target fallback**
(`sizing/vol_target.volatility_target_weight`). Advisory evaluation continues for every
symbol; only the trade-history-based Kelly refinement is lost until the DB is reachable
again. `record_trade()` / `close_trade()` still raise (a trade that was never persisted is
never fabricated as recorded — CONSTRAINT #4). The stub is cached so a DB outage does not
retry-storm the failing host once per ticker.

**Operator action**: None required for the pipeline to keep producing recommendations —
sizing is simply less refined. To restore full Kelly sizing:

1. Confirm the DB is the problem: check the single construction-failure log line.
2. For Supabase, if the direct-connect host is IPv6-only on an IPv4 network, switch
   `DATABASE_URL` to the Session/Transaction **pooler** host
   (`aws-0-<region>.pooler.supabase.com`, username `postgres.<project-ref>`).
3. Re-run the pipeline; the next `TransactionsStore()` construction succeeds and Kelly
   sizing resumes from real trade history.

Covered by `tests/test_transactions_store.py` and Gravity
`step_75_db_backend_resilience_audit`.

---

### 3.12 MCP Client Shows "Server Disconnected" (investyo)

**Symptom**: Claude Desktop's `investyo` MCP connector (or another local `gcloud`/venv-launched
MCP entry, e.g. the CLI's `investyo-platform`) shows **"Server disconnected"** immediately after
launch, with no obvious local error.

**Root causes** (check both — they present identically and can occur independently):

1. **Client-side PATH resolution.** GUI-launched apps (Claude Desktop) spawn subprocesses with a
   minimal `PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`) that excludes Homebrew's `/opt/homebrew/bin`.
   A bare `"gcloud"` (or `"python3"`, `"npx"`, …) command in an MCP server config silently
   resolves to nothing — or, worse, to a *different* interpreter that lacks the required
   packages (e.g. Apple's stub `/usr/bin/python3` instead of the project's `.venv`) — and the
   process exits before completing the MCP handshake.
2. **Remote `.env` permission trap** (`investyo` only, whether reached via `gcloud compute ssh`
   — `INVESTYO_REMOTE_MODE=gcp`, the default — or a plain `ssh` to any other host —
   `INVESTYO_REMOTE_MODE=ssh`, e.g. a home Raspberry Pi or an Oracle Cloud/Hetzner/DigitalOcean
   VPS provisioned via `deploy/setup_vps.sh`). The adapter's remote command must `cd /opt/investyo`
   before `sudo -u investyo ...` — `sudo` does not change the working directory, and the SSH login
   user's home directory (the default remote cwd) is typically mode `750` and unreadable by the
   `investyo` service user. Without the `cd`, FastMCP's pydantic-settings crashes reading `.env`
   with `PermissionError: [Errno 13] Permission denied: '.env'`.

**Operator action**:

1. Check `~/Library/Logs/Claude/mcp-server-<name>.log` (Claude Desktop) for the actual traceback
   — do not guess; the two root causes above look identical from the UI alone.
2. Confirm every `command` field in `claude_desktop_config.json` / `~/.claude.json`'s
   `mcpServers` entries is an **absolute path** (`which <tool>` in an interactive shell, then
   hardcode that path — never rely on inherited `PATH`).
3. For `investyo` specifically, point `command` at `mcp_remote_adapter.py` (run via the project's
   `.venv/bin/python3`) rather than inlining a raw `gcloud compute ssh` command — the adapter is
   the single tested source of truth for both the absolute-`gcloud` resolution (`_resolve_gcloud()`)
   and the load-bearing `cd /opt/investyo` fix, so it can't drift out of sync again.
4. Fully quit and relaunch Claude Desktop (a reload is not enough — MCP server configs are read
   at app launch).

Covered by `tests/test_mcp_remote_adapter.py`; see `docs/architecture/observability-and-apis.md`
for the full technical writeup.

---

### 3.13 How to Read `output/daemon.json`

**Symptom**: the Pilots PWA's Settings screen shows the Daemon row as "Not reachable" with
"last known state" (or, after 2026-07, one of the two new sharper labels below), and you want
to know from the file itself whether the daemon actually stopped or just crashed.

`output/daemon.json` is written by `desktop/orchestrator_daemon.py` at **startup** (`state:
"running"`/`"started"`, `stopped_at: null`) and again, since 2026-07, at a **graceful**
`_teardown()` (`state: "stopped"`, `stopped_at` set — `started_at` is preserved from the
original startup value, not re-stamped). Read it directly:

```bash
cat output/daemon.json
```

| What you see | What it means |
|---|---|
| `state: "stopped"`, `stopped_at` non-null | Clean shutdown (SIGTERM handled, teardown completed). Nothing to investigate. |
| `state: "running"`/`"started"`, `stopped_at: null`, and the daemon IS actually reachable | Normal — still up. |
| `state: "running"`/`"started"`, `stopped_at: null`, and the daemon is NOT reachable | **The file is stale — it never got to run its terminal write.** This is the expected appearance of a `SIGKILL` or a crash the signal-watcher thread never caught; the file alone cannot distinguish "just killed" from "killed a week ago". |

For that last case, don't trust the file's `state` alone — the PWA's `GET /automation/status`
also probes the recorded `pid` directly (`os.kill(pid, 0)`, via `pilots.run_status._pid_alive`)
and surfaces it as `daemon.pid_alive`:

- `pid_alive: false` → the process is confirmed gone. Settings shows **"stopped — process not
  running"**. Safe to relaunch.
- `pid_alive: true` → a process with that pid exists but isn't answering the Control API.
  Settings shows **"process alive, API not responding"** — check `logs/investyo.log` for a
  hung cycle or a port conflict (`lsof -i :8601`) before force-killing it.
- `pid_alive: null` → unknowable (no pid recorded, e.g. before this fix, or the Control API
  path never probed one). Settings shows the older, ambiguous **"last known state"**.

Covered by `tests/test_orchestrator_daemon.py::TestDaemonFileWriting`/`TestSignalHandling` and
`tests/test_run_status.py::TestPidAlive`; see `docs/architecture/webapp-and-gui.md`'s
`desktop/orchestrator_daemon.py` entry for the writer side and
`docs/architecture/observability-and-apis.md`'s `api/pilots_api.py` entry for the reader side.

### 3.14 Shutdown Taking Longer Than Expected

**Symptom**: running `kill -TERM` on a daemon process, or stopping/restarting the
`com.investyo.stack` launchd job, takes noticeably longer than it used to (up to tens of
seconds instead of ~1s).

**This is very likely expected, not a hang.** As of 2026-07, shutdown timeouts across the
whole stack were re-derived from ONE published budget,
`settings.DAEMON_SHUTDOWN_TIMEOUT_SECONDS` (default **25.0s**), so that every OUTER
supervisor waits strictly longer than the daemon's own graceful-teardown needs, instead of
routinely SIGKILLing it mid-teardown as happened before this fix. The ladder:

| Level | Who | Budget (`main.py --interval` backend) | Budget (persistent daemon backend) |
|---|---|---|---|
| 0 | An in-flight pipeline cycle | unbounded — never waited out | unbounded — never waited out |
| 1 | Daemon `_teardown()` (`desktop/orchestrator_daemon.py`) | n/a | `DAEMON_SHUTDOWN_TIMEOUT_SECONDS` = 25s |
| 2 | launchd `ExitTimeOut` (`scripts/com.investyo.stack.plist`) / systemd `TimeoutStopSec` (`deploy/investyo-daemon.service`) | n/a | 45s |

**Why an in-flight cycle is never waited out**: a full pipeline cycle can take minutes, and
there is no safe way to abort one mid-flight (see `pipeline/runner.py`'s own docstring on
why adding cancellation there would silently turn a crash into a swallowed error). So a
`main.py --interval` process that's mid-cycle when asked to stop is simply killed — this is
safe (advisory-only, no broker contact, and
`output/state_snapshot.json` is now written atomically — see below — so a kill mid-write
never corrupts it). What changed is the **persistent daemon** backend
(`ORCHESTRATOR_DAEMON_ENABLED=true`), which now gets a genuinely bounded grace period long
enough to drain its two API servers and let an in-flight run finish (or, past the budget,
give up on it cleanly and log a warning) instead of being cut off mid-teardown.

**If a wait genuinely seems stuck past the daemon backend's ~45s window**: check
`logs/investyo.log` for `"Orchestrator daemon shut down cleanly."` (the terminal
confirmation) or `"timeout=%.1fs elapsed while a run was still in flight"` (the honest
give-up warning) — either one means teardown actually ran within budget. Its absence past
45s means something is genuinely stuck, not merely slow; check for a wedged run
(`GET /status`'s `is_running`) or an unresponsive Control API before force-killing.

Covered by `tests/test_orchestrator_daemon.py::TestShutdownBudget`,
`tests/test_daemon_runtime.py::TestShutdownTimerJoinBudget`, and
`tests/test_state_snapshot_advisory.py`/`tests/test_main_orchestrator.py`'s
`TestAtomicWrite`/atomic-write regression tests.

### 3.15 Cutover: make the daemon the agentic-queue writer (step 5.3)

**Do this only after** reviewing at least 5 trading days of shadow comparison
(`DAEMON_AGENTIC_QUEUE_MODE=shadow`, `python -m scripts.compare_shadow_queue`,
exit 0 = identical). These are operator steps; nothing in the code flips them.

1. **Make the daemon always-on.** Install the stack service (it also unloads
   the old `com.investyo.daily-advisory` job):
   ```bash
   ./scripts/install_stack_service.command
   launchctl list | grep com.investyo.stack        # expect a PID
   launchctl list | grep com.investyo.daily-advisory  # expect nothing
   ```
   If the daily-advisory job is still listed, unload it by hand:
   `launchctl unload ~/Library/LaunchAgents/com.investyo.daily-advisory.plist`.
   The service `cd`s to the repo root, so `watch_rules.yaml` (a relative
   `WATCH_RULES_FILE`) resolves exactly as it did for main.py.
2. **Turn on the daemon's morning Robinhood login in the same change**
   (§5.1a): `ROBINHOOD_SCHEDULED_LOGIN_ENABLED=true`. Without it no 08:40
   approval prompt arrives once the 08:45 main.py job is gone.
3. **Flip the writer:** `DAEMON_AGENTIC_QUEUE_MODE=primary` (Settings → Feature
   Flags with typed confirmation, or `.env` then restart the daemon). Leave
   `ROBINHOOD_EXECUTION_MODE` as it is.
4. **Check `ORCHESTRATOR_DAEMON_TOKEN` is set.** The `robinhood-execution`
   skill refreshes the queue with `POST /run` in primary; without the token
   that call is refused (403) and the skill stops.
5. **Verify one cycle:** trigger `POST /run` (Pipeline screen, or
   `python -c "from shared import daemon_client as d; print(d.trigger_run())"`),
   wait for it to finish, then check `$OUTPUT_DIR/execution_queue.json`'s
   `generated_at` is fresh (or the log line "no queue composed" if nothing
   cleared the 0.85 floor), `watch_state.json` was rewritten, and the log has
   `Robinhood execution queue emitted` / `Agentic queue (primary)` lines. Run
   `/rh-execute` in review.

**What changes in primary:** the daemon writes the real
`queue_sources/advisory.json`, `execution_queue.json` and `watch_state.json`
and sends the summary push, watch alerts and new-intent push every cycle it
runs (hourly with `ORCHESTRATOR_INTERVAL_SECONDS=3600`; the clean-run
"Refresh Complete" push at most once per ET day, the error push every cycle
with errors). `main.py` still runs if something launches it, but writes none
of those, no `daily_report.html` and no state snapshot; it logs a WARNING
saying so. `daily_report.html` is retired: use `daily_report_dashboard.html`
or the webapp. A paused (kill-switch) or synthetic-data cycle writes nothing
and pushes nothing; the previous queue stays and the skill's ~30-minute
freshness rule keeps it from being placed.

**Rollback:** set `DAEMON_AGENTIC_QUEUE_MODE=off` (or `shadow`) and reload
`com.investyo.daily-advisory`; main.py resumes as the writer on its next run.

---

## 4. Contacts

| Role | Contact | Notes |
|------|---------|-------|
| FRED API issues | https://fred.stlouisfed.org/docs/api/ | Key rotation, rate limits |
| FMP status | https://site.financialmodelingprep.com | Quote/data outages (paper ledger fills depend on FMP quotes) |

---

## 5. Regular Maintenance

| Frequency | Task |
|-----------|------|
| Daily | Review HTML report Δ band; check web app Mission Control heartbeat and regime telemetry. Validation-report staleness/deployability is now checked automatically (see §3.4 and §5.4) — no manual glance needed unless it alerts. |
| Weekly | Glance at Conviction Calibration MAE; review any Dead-Letter Queue entries |
| Monthly | Rotate API keys (FRED, Robinhood). Validation harness re-run is now automatic (§5.4) — spot-check the webapp Strategy Health screen rather than re-running it by hand. |
| Quarterly | Full review of `MAX_POSITION_WEIGHT`, `KELLY_FRACTION`, `KELLY_CAP`; check calibration curve for systematic bias |
| Annually | Full stress-test re-run for options-selling strategies; re-review `ADVISORY_ONLY` status if broker execution is intended |

### 5.1 Unattended daily-advisory scheduler (macOS launchd)

The 90-day paper-trading gate and the conviction-calibration history
(`output/decision_log.jsonl`) only fill if the advisory actually runs each
day. A macOS **launchd** timer runs the existing headless `main.py` once per
weekday pre-market. This is an OS timer invoking `main.py` only — there is no
autonomous self-scheduling agent loop.

**Install** (double-click, recommended):

```bash
chmod +x scripts/install_schedule.command   # one-time
# then double-click scripts/install_schedule.command from Finder
```

The helper verifies `.venv` / Python 3.12, rewrites
`scripts/com.investyo.daily-advisory.plist` to this repo's absolute path,
copies it to `~/Library/LaunchAgents/`, then `launchctl unload` (any prior
job) + `launchctl load`. It runs `.venv/bin/python3 main.py` at **08:45
America/New_York** on Mon–Fri, logging to
`output/scheduled_advisory.out` / `.err`.

**Check status / logs:**

```bash
launchctl list | grep com.investyo.daily-advisory
tail -f output/scheduled_advisory.out
python scripts/track_record_status.py        # gate progress + calibration depth + staleness
```

**Uninstall:**

```bash
launchctl unload ~/Library/LaunchAgents/com.investyo.daily-advisory.plist
rm ~/Library/LaunchAgents/com.investyo.daily-advisory.plist
```

> **Timezone note:** launchd's `StartCalendarInterval` fires in the machine's
> local timezone. Keep the Mac set to America/New_York (or edit the `Hour` in
> the plist) so 08:45 lands pre-market ET. If the Mac is asleep at 08:45, the
> job runs at the next wake.

#### 5.1a Daily Robinhood login from the daemon (step 5.3 cutover)

Today the 08:45 launchd `main.py` run is what triggers the day's Robinhood
device-approval push. When you unload that job at the step-5.3 cutover
(`.claude/shrink_step5_retire_main_py_implementation_plan.md`), turn on the
daemon's own scheduled login in the **same** change, or no morning prompt
will arrive:

```bash
# .env (or Settings → Feature Flags; ENABLED needs typed confirmation)
ROBINHOOD_SCHEDULED_LOGIN_ENABLED=true
ROBINHOOD_SCHEDULED_LOGIN_TIME_ET=08:40   # HH:MM, US/Eastern, weekdays only
ROBINHOOD_SCHEDULED_LOGIN_CUTOFF_ET=18:00 # no login is STARTED at/after this (default)
```

Then restart the daemon (required if `ORCHESTRATOR_INTERVAL_SECONDS=0`;
harmless otherwise).

- **What happens:** at/after 08:40 ET each weekday the daemon starts one
  non-blocking `refresh` login. Approve the push in the Robinhood app within
  `RH_LOGIN_DEADLINE_SECONDS` (180 s). No holiday calendar: it prompts on
  market holidays too.
- **When it skips (and does not retry that day):** the cached account
  snapshot is already newer than today's 08:40 (for example you pressed
  Refresh at 08:41), `RH_USERNAME`/`RH_PASSWORD` are missing, or a Connect
  login is in flight.
- **Once per day, restart-safe:** the attempted date is stored in
  `~/.stockpy_local/output/robinhood_scheduled_login_state.json` (`last_attempted_et_date`,
  `last_outcome`, `job_id`, `last_error_code`). Restarting the daemon the same
  day does not prompt again. A daemon started after 08:40 (and before the
  cut-off) on a weekday that has not been attempted yet prompts immediately.
- **Evening cut-off (18:00 ET by default):** the daemon only STARTS the
  scheduled login between 08:40 and `ROBINHOOD_SCHEDULED_LOGIN_CUTOFF_ET`.
  A daemon (re)started at, say, 19:00 does nothing that evening, does not
  mark the day attempted, and prompts at 08:40 the next weekday. A cut-off
  at or before the start time (or an invalid value) disables the scheduled
  login with a warning.
- **If you missed the push** (`last_outcome` = `timeout`): press Refresh on
  the webapp's brokerage card. Only one login can run at a time. A Refresh
  while the scheduled login is still waiting joins it (same job) instead of
  sending a second push; a Connect while any login runs returns 409.
- **One login across ALL processes:** the process that starts a login holds
  an OS lock on `~/.stockpy_local/output/robinhood_login.lock` until the
  login finishes. Any other process (the daemon, the standalone Data/Metrics
  APIs, the MCP server, `main.py`) that tries to start one meanwhile is
  refused: the webapp gets a 409 naming the other process, and a background
  auto-refresh just uses the cached snapshot. To see who holds it, read
  `~/.stockpy_local/output/robinhood_login_owner.json` (pid, job_id, mode,
  started_at). The lock is released automatically if that process exits or
  is killed, so there is nothing to clean up by hand.
- **Invalid time or cut-off** (e.g. `8:40pm`): the scheduled login is
  disabled and the daemon logs a warning.
- **Turn off:** `ROBINHOOD_SCHEDULED_LOGIN_ENABLED=false`.

### 5.2 Track-record status report

`scripts/track_record_status.py` (no network calls) reports how close you are
to the 90-day go-live gate and whether the calibration history is filling:

* days elapsed since `PAPER_TRADING_START_DATE` vs the 90-day gate + days remaining;
* `output/decision_log.jsonl` row count (calibration-history depth);
* 30-day conviction-calibration MAE (reused from `scripts/daily_briefing`);
* last-run staleness from `heartbeat.txt` / `state_snapshot.json` mtimes.

```bash
python scripts/track_record_status.py          # human-readable
python scripts/track_record_status.py --json   # machine-readable
```

### 5.3 Enabling Sentiment Comment-Channel Ingestion (StockTwits)

The "Review" (investor-forum comment volume) term feeding Sector
Selection's Sector Heat Factor (`docs/signals/sector_selection.md`) and
the composite sentiment index is honestly `NaN`/degraded until the
comment channel has genuinely produced at least one document — see
`data.sentiment_source_class.classify_source` and
`data.sector_selection_heat._review_channel_ever_observed`. The Reddit
source (`RedditSource`, `REDDIT_*` settings) was removed 2026-09; the one
remaining comment source is StockTwits
(`settings.SENTIMENT_COMMENT_SOURCES`, default `"reddit,stocktwits"` — `reddit`
stays listed only so historical audit rows still classify as comments):

**StockTwits** (`data.sentiment_sources.StockTwitsSource`) — free,
uncredentialed, off by default. To activate:
* Set `SENTIMENT_INGESTION_ENABLED=true` (master ingestion switch) and
  `STOCKTWITS_ENABLED=true`.
* Add `stocktwits` to `SENTIMENT_SOURCES` (e.g.
  `SENTIMENT_SOURCES=yahoo_rss,gdelt,edgar,stocktwits`) — the
  flag alone does not add it to the fan-out list.
* StockTwits' public endpoint has tightened over the years and may
  rate-limit or require auth in some deployments; a failed request
  degrades to no documents that cycle (never a crash) — treat it as
  supplementary coverage.

**Verifying it worked**: after a few days of running with the source
active, `HistoricalStore.get_sentiment_archive_depth_by_source()` will
list `stocktwits` with a non-zero `document_count`, and Sector
Selection's `degraded_reason` will read `None` instead of
`"review_unavailable"` for sectors with real comment coverage. No GUI
widget exists for either flag — both are hand-set in `.env` only.

### 5.4 Automatic strategy validation (backtest) cadence

Strategy health no longer requires a manual `python -m validation.harness` or
`python -m scripts.refresh_validations` run. Two jobs in `deploy/crontab.txt`
(installed via `deploy/setup_gcp_vm.sh`, or `crontab deploy/crontab.txt` on
any host that runs the pipeline) keep `reports/*_validation_summary.json`
fresh and page you if it stops working:

* **Monthly full re-validation** (3rd of the month, 07:00 UTC) —
  `./scripts/refresh_validations.sh` walk-forward re-validates every strategy
  in `STRATEGY_REGISTRY` (PBO / DSR / net-of-cost Sharpe / MaxDD, plus the
  tail-scenario stress gate for options-selling strategies) and overwrites
  `reports/*_validation_summary.json`. Offset two days from the model-retrain
  job (1st of the month) so the two heavy, long-running jobs don't contend
  for CPU/network on the same night. Matches the cadence the harness's own
  docstring has always recommended — this job is what actually makes that
  cadence happen instead of relying on someone remembering to run it.
* **Daily staleness/deployability alert** (08:00 UTC) —
  `python scripts/preflight_check.py --validation-staleness-only` re-runs
  just the `check_validation_reports` check (>30-day-old or non-deployable
  report → FAIL) and fires a CRITICAL alert through whatever channel(s) are
  configured (`ALERT_WEBHOOK_URL`, `DISCORD_WEBHOOK_URL`,
  `SLACK_WEBHOOK_URL`, `NTFY_TOPIC` — see §1.1) if it fails. This flag
  deliberately bypasses the full preflight gate's `ADVISORY_ONLY` auto-skip
  (`check_validation_reports` is normally skipped there because it's framed
  as a live-order-submission gate) — strategy health is worth monitoring
  even while `ADVISORY_ONLY=true` (the default), since a stale or
  now-non-deployable strategy is exactly the kind of thing that should
  change how much weight you give its signal.

**Where to see it**: the webapp Settings screen's "Automation" panel renders
both jobs directly from `deploy/crontab.txt` (via `GET /automation/schedule`
→ `pilots.run_status.parse_crontab`) — no code change needed when this file
changes. The Strategy Health screen's per-strategy `report_date` reflects the
monthly job's last successful run.

**Caveat**: `deploy/crontab.txt` is the checked-in *intended* schedule, not
proof of what's installed on any given host — `GET /automation/schedule`'s
`cron.installed` field is always `null` for exactly this reason (this API
never shells out to `crontab -l`). If you're not running on a host that has
`crontab deploy/crontab.txt` installed (e.g. a pure local macOS setup with no
GCP VM), install it yourself on whatever host runs the pipeline, or run
`./scripts/refresh_validations.sh` by hand until you do.

**If it stops working**: see §3.4.

---

## 6. Advisory Pause and Restart Procedure

In advisory mode there is no broker to halt, so an "emergency shutdown" means
**pausing the recommendation engine** so the pipeline produces no new signals while
you investigate an anomaly.

The pause gate is implemented in `main.run_once()` (after universe build, before macro
compute) and in `main_orchestrator._main_body()` (after data fetch, before `run_pipeline()`).
When the sentinel is active, `RunResult.recommendations` is empty and the error list
records `stage="kill_switch_gate"`.  The last written `state_snapshot.json` and HTML
report are untouched so the observability dashboard continues displaying the last known state.

### How to pause recommendations

```bash
# 1. Activate the pause gate (the same file the kill switch uses)
python -m execution.kill_switch --activate --reason "advisory pause — investigating anomaly"

# 2. Confirm the pipeline sees the pause on next run
python3 main.py
# Expected: INFO — Advisory paused by kill-switch sentinel — skipping evaluation cycle.
#           Reason: advisory pause — investigating anomaly  |  Universe would have been: ...
```

> **Note — the Pilots PWA is a front-end for this same sentinel (2026-07):**
> `api/pilots_api.py`'s `POST /automation/pause` / `POST /automation/resume` (Settings
> screen → Signal generation toggle) call the exact `GlobalKillSwitch` this section
> describes — not a separate mechanism. Two things to know operating it from there:
> (1) **pausing does NOT stop the schedule** — the daemon's interval timer keeps
> firing and cycles keep running; they just produce no recommendations (or submit no
> orders in live mode). `POST /automation/run` returns 423 while paused. (2) **remote
> resume is refused whenever `ADVISORY_ONLY=false`** (live order submission enabled) —
> deactivate the kill switch at the console in that case, per the CLI steps below. This
> is deliberate: pausing remotely is always safe, but re-enabling live order submission
> from a possibly-compromised or leaked token is not.

> **Note — macro-triggered advisory gating (automatic, always active):** Separately from
> the manual kill switch, `engine/advisory.evaluate()` applies conservative overrides when
> macro conditions deteriorate.  Three tiers: (1) RECESSION / CREDIT EVENT regime → all
> BUY signals suppressed to HOLD; (2) VIX > 30 OR Sahm ≥ 0.5 → -25 pt score penalty;
> (3) Finance / Financial Services / Real Estate sector AND yield curve inverted OR HY OAS
> > 6% → sector veto (BUY → HOLD).  These fire automatically per-symbol; the operator
> does not need to activate the kill switch for them to take effect.  Each override is
> documented in the advisory rationale so the HTML report explains the gate to the operator.

### How to resume

```bash
# After investigating and resolving the root cause:
python -m execution.kill_switch --deactivate

# Confirm preflight passes
python scripts/preflight_check.py  # should exit 0

# Restart the pipeline
python3 main.py
```

### When to pause

| Situation | Action |
|-----------|--------|
| Calibration MAE > 0.15 (§3.3) | Pause + disable affected signal module before resuming |
| Missing recommendation for held symbol > 5 consecutive days (§3.2) | Pause + investigate data source; check Dead-Letter Queue |
| Account snapshot stale > 48 h (§3.1) | Force refresh first (`--refresh-account`); pause only if live fetch also fails |
| Macro regime shows RECESSION AND HMM agrees | Pause new signal evaluation; monitor daily |
| Suspicious pipeline output (all signals identical, all BUY, all NaN) | Pause immediately; run `python scripts/preflight_check.py` and check `$LOCAL_DATA_ROOT/logs/investyo.log` (default `~/.stockpy_local/logs/investyo.log`) |

### Back up the database before any destructive investigation

`quant_platform.db` lives under `settings.LOCAL_DATA_ROOT` (default `~/.stockpy_local/`),
not the repo root — see `docs/architecture/data-layer.md`'s `settings.LOCAL_DATA_ROOT`
subsection.

```bash
cp ~/.stockpy_local/quant_platform.db ~/.stockpy_local/quant_platform_backup_$(date +%Y%m%d_%H%M%S).db
```

### Incident log

Document every pause in `$LOCAL_DATA_ROOT/output/decision_log.jsonl` (default
`~/.stockpy_local/output/decision_log.jsonl`) via the web app **Calibration** screen →
Decision Journal (entry type: "modified", notes: describe the anomaly and resolution). This keeps
a timestamped operator log that the calibration tracker can correlate with signal
accuracy changes.

---

## Incident response: data source degraded mid-session

When a data source (FMP, yfinance, FRED, Robinhood) is reporting errors:

> **Note:** Finnhub was removed 2026-09. FMP feeds company news / earnings headlines for
> the `news_catalyst` signal (an FMP news outage loses only news-catalyst sentiment).
> Fundamentals are FMP-primary (`data/fmp_fundamentals.py`) with a Yahoo statement-derived
> fallback (`data/yahoo_fundamentals.py`, free) and a raw yfinance `.info` fallback.

### Financial Modeling Prep (FMP) Troubleshooting

- **Rate Limits**: The Starter tier is ~300 req/min (governed by `FMP_MIN_REQUEST_INTERVAL_SECONDS` / `FMP_MAX_RETRIES` / `FMP_COOLDOWN_THRESHOLD` in `settings.py`).
- **Cooldown Behavior**: Consecutive errors trigger a circuit breaker for `FMP_COOLDOWN_SECONDS` once `FMP_COOLDOWN_THRESHOLD` is hit, skipping FMP calls to avoid timeouts.
- **Fallback Behavior**: When FMP is unavailable, `CompositeProvider` transparently falls back to yfinance for quotes and Yahoo for fundamentals (if `FMP_FALLBACK_ENABLED=true`).
- **What to Check**: If data is stale or missing, check `$LOCAL_DATA_ROOT/logs/investyo.log` for warnings naming FMP fallbacks. Confirm `FMP_API_KEY` is set and valid.

The desktop app's Safety tab → Dependency Map view was deleted in 2026-09 and the web app
has no equivalent screen. The underlying map (`shared/dependency_map.py`) still exists:

1. Call `shared.dependency_map.impacted_consumers([...])` with the degraded sources (e.g.
   from a `.venv/bin/python3` shell). The result is the authoritative list of consumers
   that lose coverage right now.
2. If a CRITICAL consumer (e.g. `processing_engine`, `forecasting_engine`) appears in
   the list → pause recommendations via the kill switch (web app Settings → General
   toggle, or `python -m execution.kill_switch --activate`).
3. State is file-backed (`output/KILL_SWITCH`, `output/risk_gate_blocks.jsonl`), so there
   is no in-process cache to invalidate after remediation.

---

## Advisory-Only Mode (Tier 5.1, default-on)

`settings.ADVISORY_ONLY=true` is the project default.  Three enforcement layers keep the
broker surface quarantined:

1. **Orchestrator** — `main_orchestrator._execute_broker_orders` returns immediately with
   an INFO log before any broker import is reached.
2. **Web app** — a status banner reads "Advisory Only Mode (Live Execution Disabled)"; the
   execution mode is shown and changed from Settings → General.
3. **Preflight** — eight broker-dependent / advisory-false-positive checks auto-skip;
   `advisory_only_active` check is PASS-loud (and PASS-with-warning when
   `ADVISORY_ONLY=false`). `robinhood_execution_mode` and `state_snapshot_fresh` are
   never auto-skipped — the Robinhood execution bridge is orthogonal to this quarantine.

**Re-enabling broker execution** requires ALL THREE flags to be `false` simultaneously:
`ADVISORY_ONLY=false AND DRY_RUN=false AND PAPER_TRADING=false`, and even then the
automated pipeline places no orders (see §1). Ensure `preflight_check.py` exits 0 before
any Robinhood live run.

---

## Robinhood Execution Bridge (Tier 8) — paper-first rollout

The Robinhood Trading MCP lets a **Claude Code agent** (not the headless pipeline) place
equity trades into a dedicated, separately-funded **Agentic account**. The platform only
emits a gated, dry-run queue (`output/execution_queue.json`); the agent is the only actor
that calls the MCP. This is **independent of `ADVISORY_ONLY`** — it never arms the
automated paper-broker surface.

### One-time setup (operator, local — cannot be done headless)

```bash
# 1. Add the MCP (in your own terminal / Claude Code, not a remote session)
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
# 2. Authenticate: in Claude Code run  /mcp  → robinhood-trading → authorize (OAuth)
# 3. In the Robinhood app: open + fund a dedicated AGENTIC account with a small, capped
#    amount. This is the blast radius — keep your main account out of execution.
# 4. Smoke-test READ-ONLY first (ask Claude Code to call get_accounts / get_portfolio).
#    Do NOT place anything yet.
```

### Staged rollout — strictly `off → review → live`

```bash
# Stage A — PAPER / DRY-RUN (review): emit the queue; the agent only simulates.
#   .env:
ROBINHOOD_EXECUTION_MODE=review
python3 main.py                 # writes output/execution_queue.json (allow_place=false on all)
# In Claude Code:  /rh-execute  → previews each order via review_equity_order, then STOPS.

# Stage B — LIVE (only after reviewing paper output and you are satisfied):
#   .env:
ROBINHOOD_EXECUTION_MODE=live
ROBINHOOD_MAX_NOTIONAL_PER_ORDER=500     # REQUIRED > 0 for live; preflight fails otherwise
python scripts/preflight_check.py        # check_robinhood_execution_mode warns (live) / fails (no cap)
python3 main.py                          # queue now carries allow_place=true where gated-OK
# In Claude Code:  /rh-execute  → previews, then for each allow_place=true intent asks for an
#   explicit per-order confirmation before calling place_equity_order.
```

### Safety controls (all enforced)

- **`off` is the default** — nothing is written, zero behavior change.
- **`review` never places** — the agent calls only `review_equity_order`.
- **`allow_place`** is `true` only when `mode=live AND risk-gate passed AND kill switch clear
  AND a notional cap is set` — structurally false otherwise.
- **Kill switch** pauses everything: `python -m execution.kill_switch --activate --reason "..."`
  blocks queue placement (checked at emit time and again before each order). The advisory
  pause gate (§6) also short-circuits `run_once()`, so a paused cycle emits no queue.
- **Per-trade human confirmation** is mandatory in `live`; the agent never batch-confirms and
  never operates against the non-Agentic account.
- **Notional ceilings**: `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` (platform) + the Agentic account's
  funded balance (Robinhood).

### The review → gate → (place) loop (as exercised)

The end-to-end path has two actors and one hand-off file:

1. **Platform (Python) writes the queue.** `python3 main.py` runs `run_once()`, which writes
   its own advisory source file (`output/queue_sources/advisory.json`) and calls
   `execution/compose.py::compose_and_emit` — the single writer of `output/execution_queue.json`
   (2026-07: previously `main.py` called `execution/queue_builder.py::emit_execution_queue`
   directly; if you're following an older trace or log line referencing that call, this is why
   it now goes through `compose.py` first). If any Pilot is being actively Followed via the
   Pilots PWA/MCP (`POST /pilots/{id}/follow`), that follow's own source file
   (`output/queue_sources/follow-<pilot_id>.json`) is UNIONED in too — a symbol both advisory
   and a follow have an opinion on always resolves to advisory's own number (a deliberate,
   conservative "risk wins" rule; the follow's own reasoning is still surfaced in that intent's
   `overridden` field, never silently dropped), and two Pilots sharing a symbol are netted
   together rather than each queuing a separate order for it. `compose_and_emit` then builds
   each resulting intent into an `OrderIntent`, runs it through the **same** `PreTradeRiskGate`
   + `GlobalKillSwitch` stack the paper-broker path uses (all in dry-run — no broker contact), stamps
   `allow_place`, and atomically writes `output/execution_queue.json`. In `off` mode nothing is
   written. **If the queue looks stale (unchanged across a cycle) and you have an active follow
   that hasn't been re-planned in a while:** a source file older than
   `settings.QUEUE_SOURCE_MAX_AGE_SECONDS` (default 7 days) makes `compose_and_emit` refuse the
   WHOLE compose (nothing written, the PRIOR queue is left in place, logged as a warning) rather
   than silently composing against a week-old Pilot ranking — re-follow that Pilot (or wait for
   its next explicit re-plan) to refresh its source and unblock composition again.
2. **Agent (the `robinhood-execution` skill) reads the queue and previews.**
   `/rh-execute` loads the queue, runs its hard-stops (below), calls
   `review_equity_order` for **every** intent, and — only in `live` mode, only
   for `allow_place: true` intents, only with an explicit per-order human
   confirmation — calls `place_equity_order`. Outcomes are appended to
   `output/execution_receipts.jsonl` (agent-authored; the platform never edits
   the queue).

**Hard-stops the `robinhood-execution` skill enforces** (see
`.claude/skills/robinhood-execution/SKILL.md` — do not weaken these):

- **Kill switch** — `output/KILL_SWITCH` present **OR** queue
  `kill_switch_active: true` → refuse all placement (re-checked immediately
  before each order).
- **Mode `off`** → nothing to do.
- **Stale queue** — `generated_at` more than ~30 minutes old → refuse to place;
  re-run `python3 main.py` first. (The queue's `generated_at` is an ISO-8601
  UTC timestamp precisely so this staleness check is computable from the file
  alone.)
- **Agentic-account requirement** — `get_accounts` must show a dedicated,
  separately-funded **Agentic** account and the operator must confirm it; the
  skill never operates against the main account.
- **Review-only unless live** — never calls `place_equity_order` in `review`
  mode, and never for an `allow_place: false` intent.
- **One explicit human confirmation per placed order** — never batch-confirmed.

**Regression coverage.** The core gating invariant — `allow_place` is `True`
ONLY when `mode == "live"` AND the risk gate passed AND the kill switch is clear
AND a positive notional cap is set (each condition flipped individually forces
`False`; `review`/`off` are never placeable; `gate_intent()` fails **closed** on
an internal gate exception; a present kill-switch sentinel blocks placement) —
is pinned by **`tests/test_execution_queue_gating.py`** (fully offline; the risk
gate and kill switch are monkeypatched). That file also provides the canonical
deterministic non-empty review-mode queue fixture. Run it with
`pytest tests/test_execution_queue_gating.py`.

> **Note on the live MCP walkthrough:** the actual `review_equity_order` /
> `place_equity_order` MCP calls are an **operator-driven** step performed
> through the `robinhood-execution` skill against a real Agentic account — they
> are deliberately NOT part of any automated test (no live broker calls in CI).

### Pausing / disabling

```bash
# Immediate stop (blocks placement; queue still previews):
python -m execution.kill_switch --activate --reason "halt robinhood execution"
# Full disable (next run emits nothing):
#   set ROBINHOOD_EXECUTION_MODE=off in .env
```

Outcomes the agent placed/previewed/skipped are appended to `output/execution_receipts.jsonl`
(agent-authored); the platform owns `output/execution_queue.json` (Python-authored intents).
Both are gitignored.

---

## Robinhood Live Execution Procedure

This is the day-to-day operator procedure for driving the Robinhood Execution
Bridge end to end — from a fresh queue to a confirmed fill. It sits **inside**
advisory-mode framing: it never arms the automated broker (those sections stay
marked **⚠ N/A in Advisory Mode**), it only ever touches a small, separately-
funded **Agentic** account, and every placement requires an explicit human
confirmation. If you have not done the one-time setup above ("Robinhood
Execution Bridge (Tier 8)"), do that first.

### Step 0 — Set the execution mode and the dollar cap

`ROBINHOOD_EXECUTION_MODE` rolls out strictly `off → review → live`; never skip a
stage. `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` is the hard per-order dollar ceiling
and is **required `> 0` for `live`** — preflight fails without it.

```bash
# .env — start here for every new cutover
ROBINHOOD_EXECUTION_MODE=review          # off (default) | review (paper) | live
ROBINHOOD_MAX_NOTIONAL_PER_ORDER=500     # $ ceiling per order; must be > 0 before going live
```

### Step 1 — Emit the queue

```bash
python3 main.py                          # runs run_once(); writes output/execution_queue.json
```

In `review` the queue carries `allow_place: false` on every intent; in `live` it
carries `allow_place: true` only where the risk gate passed, the kill switch is
clear, and a positive notional cap is set. In `off` nothing is written.

### Step 2 — Invoke the execution skill

In Claude Code, run the `robinhood-execution` skill (`/rh-execute`). It loads the
queue, runs its hard-stops (kill switch, stale queue > ~30 min, Agentic-account
requirement), and previews **every** intent via `review_equity_order` before it
will place anything. It also runs an idempotency check against the placed-intent
ledger (`output/execution_placed.jsonl`) so an intent already placed today
(`dedup_key = YYYY-MM-DD:SYMBOL:SIDE`, UTC) is skipped, never double-filled.

### Step 3 — Paper-first review stage (`review`)

Stay in `review` until you trust the output. The skill previews each order and
**stops** — it never calls `place_equity_order` in `review` mode. Read the
previews: sizes, estimated notionals, Robinhood's pre-trade warnings, and the
queue's conviction/rationale. This is the paper/dry-run stage; treat several
clean review runs as the prerequisite for cutover.

### Step 4 — Go-live cutover (small size first)

Only after you are satisfied with the paper output:

```bash
# .env
ROBINHOOD_EXECUTION_MODE=live
ROBINHOOD_MAX_NOTIONAL_PER_ORDER=100     # start SMALL — raise the cap only after clean live fills
python scripts/preflight_check.py        # check_robinhood_execution_mode: warns (live) / fails (cap unset)
python3 main.py                          # queue now stamps allow_place=true where gated-OK
```

Then run `/rh-execute` again. For each `allow_place: true` intent it previews,
then asks for an **explicit per-order confirmation** ("place / skip / stop")
before calling `place_equity_order`. Keep the cap small (e.g. `100`) for the
first few live sessions and raise it only once real fills reconcile cleanly.
After each placement the skill appends to `output/execution_placed.jsonl` (the
idempotency ledger) and `output/execution_receipts.jsonl` (the outcome audit
trail); `execution/receipts_store.py` reconciles both against actual Robinhood
fills. The desktop app's Robinhood panel that displayed this was deleted in 2026-09
and the web app has no equivalent view yet — check the two JSONL files directly
after every live run.

### Step 5 — Kill-switch pause (stop placement immediately)

The kill switch blocks all placement (checked when the queue is built and again
immediately before each order):

```bash
python -m execution.kill_switch --activate --reason "halt robinhood execution"
# resume only when the issue is resolved:
python -m execution.kill_switch --deactivate
```

To disable the bridge entirely, set `ROBINHOOD_EXECUTION_MODE=off` — the next
`python3 main.py` emits no queue. The advisory pause gate (§6) also short-
circuits `run_once()`, so a paused cycle produces nothing to place.

### Preflight

`python scripts/preflight_check.py` includes the Robinhood execution checks —
`check_robinhood_execution_mode` warns when `mode=live` and fails when a live
mode has no positive `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` cap. Run it before every
go-live cutover and treat a non-zero exit as a stop.

## §7 Prompt Registry — Publish & Rollback Playbooks

> **Security boundary:** Prompt-registry operations touch advisory text only.
> They cannot alter order submission logic, advisory quarantine, risk gates, or the kill switch.
> All safety enforcement stays in Python code — this invariant is audited in Gravity step 69 check 7.

### 7.1 Normal publish flow (author machine)

Use when a revised AI instruction is ready to roll out to all running instances.

```bash
# 1. Draft the new prompt body and confirm it passes guardrails locally
python -m prompt_registry verify       # must exit 0 on current cache

# 2. Publish (requires PROMPT_REGISTRY_PUBLISH_TOKEN + PROMPT_REGISTRY_SIGNING_KEY in .env)
python -m prompt_registry publish <prompt_id> <new_version> /path/to/body.txt

# 3. Confirm remote manifest now lists the new version
python -m prompt_registry list         # should show source=remote, version=<new_version>

# 4. On every platform, sync explicitly (never automatic — CONSTRAINT #5)
python -m prompt_registry sync
```

Expected log output on success:
```
INFO  prompt_registry.registry — sync: fetched manifest registry_version=<new_version>
INFO  prompt_registry.registry — _safe_adopt: cached <prompt_id> v<new_version>
```

### 7.2 Emergency rollback — bad prompt body deployed

**Symptoms:** advisory rationale contains incorrect thresholds; AI-facing step body produces
wrong output; `verify` exits non-zero on the remote-fetched version.

**Rollback steps:**

```bash
# Step 1: Identify the bad version
python -m prompt_registry list         # shows resolved_version per ID

# Step 2: Roll back to the previous cached version (in-memory pin)
python -m prompt_registry rollback <prompt_id>

# Step 3: Confirm pin is set
python -m prompt_registry list         # source should now show "pin"

# Step 4: Persist the pin to .env so it survives restarts
#   The rollback command does this automatically via shared/env_io.
#   Verify:
grep PROMPT_REGISTRY_PINS .env

# Step 5: If the bad version is also in the remote manifest, publish a fixed version
#   and update the "latest" pointer before running sync again.
python -m prompt_registry publish <prompt_id> <fixed_version> /path/to/fixed_body.txt
python -m prompt_registry sync
python -m prompt_registry rollback <prompt_id>   # pin to fixed version

# Step 6: Document in docs/incident_log.md
```

**Resolution:** pin cleared automatically once a verified fixed version is set as latest and
synced.  Remove the pin entry from `PROMPT_REGISTRY_PINS` in `.env` to resume automatic
"latest" resolution.

### 7.3 Cache corruption / verify failure

**Symptoms:** `python -m prompt_registry verify` exits non-zero; HMAC signature mismatch in
logs.

```bash
# Step 1: Identify which ID/version failed
python -m prompt_registry verify       # prints per-check pass/fail

# Step 2: Delete the corrupt cache entry (safe — baseline is always the fallback)
rm output/prompt_cache/<prompt_id>/<version>.json

# Step 3: Re-sync from remote
python -m prompt_registry sync

# Step 4: Re-verify
python -m prompt_registry verify       # must exit 0
```

If remote also fails verification, the signing key may be mismatched.
Confirm `PROMPT_REGISTRY_SIGNING_KEY` matches the key used at publish time and retry.

### 7.4 Registry completely disabled (baseline-only mode)

When `PROMPT_REGISTRY_ENABLED=false` (the default), the platform uses the `prompt_registry/baseline/`
files committed in the repo.  No network calls, no key required.

To re-enable:
```bash
# In .env
PROMPT_REGISTRY_ENABLED=true
PROMPT_REGISTRY_URL=<manifest-url>
PROMPT_REGISTRY_TOKEN=<read-token>
PROMPT_REGISTRY_SIGNING_KEY=<hmac-key>

# Then sync explicitly
python -m prompt_registry sync
```

### 7.5 Key indicators a prompt-registry incident is NOT a safety incident

| Observation | What it means | Action |
|---|---|---|
| Advisory rationale text changed unexpectedly | Prompt body was updated; narrative changed | Rollback prompt body (§7.2) |
| Order submission behavior changed | Code change, NOT registry | Investigate `execution/`, `engine/advisory.py`, git log |
| Kill switch logic changed | Code change, NOT registry | Investigate `execution/kill_switch.py`, git log |
| Risk gate thresholds changed | Code change or settings change, NOT registry | Check `.env` and `execution/risk_gate.py` |

The registry can never change code behavior — only text shown to an AI assistant.


## Recent Architecture Updates
- **Signal Engine Vectorization**: As of Phase 4, the entire `SignalAggregator` and all `SignalModule` implementations are natively vectorized in pandas/numpy (O(1) block computation). Row-based ticker iteration in the aggregation step has been removed to maximize performance.

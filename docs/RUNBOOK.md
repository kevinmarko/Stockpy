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

The fastest way to start the platform is to **double-click `launch.command`** in Finder
or the Dock. The script:

1. Verifies `.venv` exists and Python is exactly 3.12.x before starting.
2. Prints a clear error (and pauses for you to read it) if either check fails.
3. Runs `python main.py --interval 60` by default — refreshes every 60 s until you close
   the window.
4. Pauses with "Press any key to close" after exit so final output is always visible.

**To change the interval**: open `launch.command` in any text editor, set
`REFRESH_INTERVAL_SECONDS=N` at the top (`0` = single run).

**If `.venv` is missing** (e.g., fresh clone):

```bash
cd /Users/kevinlee/Desktop/Stockpy
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Then double-click `launch.command` again.

**If the wrong Python version is detected**: the launcher tells you which version was found
and how to recreate `.venv` with Python 3.12.

**Prefer a visual control panel?** Double-click **`launch_gui.command`** (or run
`streamlit run gui/app.py`) to open the **Command Center** — a 10-tab GUI that launches
the pipeline, shows live stage status, edits non-secret `.env` tunables (secrets stay
masked/read-only), toggles signal modules and the pause gate (kill switch), and surfaces
the Gravity audit. The GUI is read-only / file-backed: it launches `main_orchestrator.py`
(or `main.py` for the advisory refresh path) as a subprocess and reads the files it writes,
so it never touches a broker directly.

The **Launcher tab** exposes two distinct entry points:

* **▶️ Launch Pipeline** — `main_orchestrator.py` (full pipeline, HTML report, JSON
  payload; broker skipped while `ADVISORY_ONLY=true`).
* **🔄 Refresh Data (Advisory)** — `main.py` (broker-free; fastest path to refresh
  `output/state_snapshot.json`, signals, and the HTML report).

A pre-launch readiness check warns about missing required env vars (e.g. `FRED_API_KEY`)
*before* the subprocess starts. The tab tails BOTH the active run log AND the platform-wide
structured telemetry stream (`logs/investyo.log`) so one window covers diagnostics across
both entry points.

The **daily HTML report** leads with a **"Δ Since Last Run" band**: new BUYs, action
flips, conviction moves (`|Δ| ≥ SNAPSHOT_CONVICTION_DELTA_THRESHOLD`, default 0.20),
holdings added/dropped, and regime changes. Powered by rotated state snapshots in
`output/history/` (pruned after `SNAPSHOT_HISTORY_DAYS=30` days). The band is hidden
on first ever run.

The **Reports tab** includes:

* **Decision Journal** — log "acted / passed / modified" per signal; entries go to
  `output/decision_log.jsonl` and for "acted" entries are linked to the nearest
  `quant_platform.db` trade record within ±24 h.
* **Conviction Calibration** — reliability diagram showing whether stated conviction
  scores match empirical win rates per bin. Starts empty until conviction-annotated
  trades accumulate; bins with < 5 trades show NaN.
* **Brinson-Fachler Attribution Analysis** — edit a GICS-11 sector matrix or bulk-paste
  TSV/CSV from a spreadsheet to compute allocation / selection / interaction effects.

---

## 1. ⚠ N/A in Advisory Mode — Paper → Live Switch

> **This section is suppressed while `ADVISORY_ONLY=true`.**
> The pre-launch readiness check (`scripts/preflight_check.py`) automatically skips all
> broker-readiness checks (`alpaca_configured`, `alpaca_paper_mode`, `dry_run_disabled`,
> `paper_trading_duration`) and instead passes a single `advisory_only_active` check.
> The GUI Strategy Matrix mode toggle (Simulation / Paper / Live) is also suppressed.

**To re-enable broker execution (future use):**

1. Set `ADVISORY_ONLY=false` in `.env`.
2. Re-run `python scripts/preflight_check.py` — it now enforces all broker-readiness
   checks, including `alpaca_configured` and `paper_trading_duration` (≥ 90 days).
3. Follow the original paper→live procedure documented below once all checks pass.

### Pre-switch (T-1 day) — ⚠ BROKER EXECUTION REQUIRED

1. Complete every item in `docs/GO_LIVE_CHECKLIST.md`.
2. Run `python scripts/preflight_check.py` — must exit **0** (all checks including broker
   ones must pass; `advisory_only_active` will warn that `ADVISORY_ONLY=False`).
3. Notify all stakeholders that live trading begins the next session.
4. Ensure the kill switch is **INACTIVE**: `python -m execution.kill_switch --status`

### Day-of switch (pre-market, ≥ 30 min before open) — ⚠ BROKER EXECUTION REQUIRED

1. Rotate `.env` values: **`ALPACA_PAPER=false`** and **`ADVISORY_ONLY=false`**.
2. Verify via the Strategy Matrix → Global Execution Mode selector (or `from settings
   import settings; assert settings.ALPACA_PAPER is False`).
3. Start the orchestrator in **dry-run** once to confirm it reads the live endpoint:
   ```
   python3 main_orchestrator.py --dry-run
   ```
   Look for `"AlpacaBroker initialized — paper=False"` in the logs (not `paper=True`).
4. Remove `--dry-run` for the first live run:
   ```
   python3 main_orchestrator.py
   ```
5. Confirm in Alpaca dashboard that the account shows the same positions as
   `transactions_store`.

**Switching back to Paper / Simulation** works identically — pick the other mode on the
Strategy Matrix tab, or set `ALPACA_PAPER=true`. Setting `ADVISORY_ONLY=true` returns the
platform to the default quarantine state regardless of `ALPACA_PAPER`.

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
| **Start pipeline** | Double-click `launch.command` or use `🔄 Refresh Data (Advisory)` in Launcher tab |
| Advisory mode active | Launcher tab banner shows `📋 ADVISORY MODE` (blue) |
| Heartbeat recent | `ls -la output/heartbeat.txt` (< 2 h old); or Observability tab → heartbeat sparkline |
| Preflight pass | `python scripts/preflight_check.py` (exit 0; `advisory_only_active` = PASS) |
| Account snapshot fresh | `python3 main.py --refresh-account` if snapshot age > 20 h |
| Holdings & P&L sane | Dashboard → **Account Holdings & P&L** — equity, buying power, per-position unrealized P&L. If empty, force refresh above. |
| No dead-letter failures | Launcher tab → Dead-Letter Queue (all symbols completed) |
| Δ Since Last Run reviewed | Open `output/daily_report.html` — check top band for unexpected action flips or conviction drops |
| Regime & VIX checked | Observability tab → recession telemetry (Sahm Rule / HY OAS / VIX / regime) |
| Conviction calibration glanced | Reports tab → Conviction Calibration (win-rate bars near the diagonal) |

---

## 3. Incident Response

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

Or from the GUI: Launcher tab → **🔄 Refresh Data (Advisory)** with the
`refresh_account` checkbox ticked.

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
| Robinhood MFA challenge triggered | Run `python3 main.py --refresh-account`; enter MFA code at the terminal prompt |
| `RH_MFA_SECRET` wrong or rotated | Re-scan the TOTP QR code in the Robinhood app; update `RH_MFA_SECRET` in `.env` |
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
symbols completed normally. The Launcher tab Dead-Letter Queue may show the symbol with a
stage and exception.

**Immediate action**:

1. Open the Launcher tab → Dead-Letter Queue. Note the `stage` and `error` for the
   affected symbol.
2. Click the **🔄 Retry** button next to the symbol — this spawns `main.py` with
   `WATCHLIST=<SYMBOL>` so only that ticker is re-evaluated.
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

**Symptom**: Reports tab → Conviction Calibration shows the reliability diagram's bars
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
| 0.05–0.10 | Monitor. Check if a specific conviction bucket (e.g. 0.7–0.8) is systematically wrong; reduce weight on the corresponding signal module via Settings tab. |
| 0.10–0.15 | Re-run the strategy validation harness: `python -m validation.harness --strategy <name> --start 2015-01-01 --end 2024-12-31`. If PBO > 0.50 or DSR < 0.95, the strategy is no longer deployable. Reduce its `SIGNAL_WEIGHTS` entry to `0` until the next retrain cycle. |
| > 0.15 | Disable the strategy module via the GUI Strategy Matrix tab (`DISABLED_SIGNAL_MODULES`). Document the degradation in `output/decision_log.jsonl` (entry type: "modified"). Alert to re-evaluate the regime and signal architecture. |

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
`check_validation_reports`.

**Immediate action**:

1. Do NOT weight the strategy heavily until a fresh report is generated.
2. Run the harness:
   ```bash
   python -m validation.harness --strategy <name> --start 2015-01-01 --end 2024-12-31
   ```
3. If the strategy fails validation (PBO ≥ 0.50 OR DSR < 0.95 OR Sharpe < 0.50 OR
   MaxDD ≥ 30%), set its weight to 0 in `settings.SIGNAL_WEIGHTS` via the Strategy
   Matrix tab.

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

**Symptom**: Log shows `ERROR - Live Robinhood fetch failed: Required environment
variable 'RH_USERNAME' (or 'ROBINHOOD_USERNAME') is missing or empty` — yet your `.env`
clearly contains `RH_USERNAME=...`.

**Root cause**: `pydantic-settings` reads `.env` into `Settings()` but does NOT propagate
values to `os.environ`. `data/robinhood_portfolio.py` reads credentials via
`os.environ.get()` directly, so it sees empty strings unless `load_dotenv()` has been
called.

**Verify**:

```bash
.venv/bin/python3 -m pytest tests/test_env_loading.py -v
```

Both tests must PASS. If either fails, the regression has returned.

**Fix**: The canonical pattern is to **import** `load_dotenv` at module top but **call**
it inside the entry-point function — `main.py` calls `_load_dotenv(override=False)` as
the first line of `main()`; `main_orchestrator.py` calls it inside `async def main()`.
`run_once()` deliberately does NOT call it — callers (`make verify`, `verify.command`)
must call `load_dotenv()` themselves before invoking `run_once()`, and both already do.

**Companion symptoms**:

- `RH_MFA_SECRET` empty → requires TOTP MFA. Enable Authenticator-app MFA in Robinhood
  (Settings → Security → Two-Factor Authentication → Authenticator App), copy the Base32
  secret, paste into `RH_MFA_SECRET=`.
- `WATCHLIST` unset AND no `watchlist.txt` AND no held positions → empty universe. Fix
  with: `WATCHLIST=SPY,QQQ,AAPL` in `.env`, or `watchlist.txt` (one ticker per line), or
  tickers in **Sheet2 column A** of the Google Sheet (last-resort fallback).
- First line of `.env` is a comment without `#` prefix → `python-dotenv could not parse
  statement starting at line 1`. Prefix the line with `#`.

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
> 2. Log into Alpaca dashboard and compare positions manually.
> 3. Fix the discrepancy, then deactivate: `python -m execution.kill_switch --deactivate`

---

### 3.9 ⚠ N/A in Advisory Mode — Kill Switch Fails to Block

> Only relevant when `ADVISORY_ONLY=false` and `OrderManager` is submitting orders.
> While quarantined, the kill switch sentinel (`output/KILL_SWITCH`) repurposes as the
> **pause-recommendations gate** — see §6.

---

### 3.10 ⚠ N/A in Advisory Mode — Broker Connection Lost

> `AlpacaBroker` / `_execute_broker_orders` are not reached while `ADVISORY_ONLY=true`.
> If you have lifted the quarantine and see Alpaca connection errors:
>
> 1. Check https://status.alpaca.markets for planned maintenance.
> 2. If unexpected: check for API key rotation requirement.
> 3. Reconnect is automatic on the next orchestrator run. Run reconciliation manually
>    after reconnect.

---

## 4. Contacts

| Role | Contact | Notes |
|------|---------|-------|
| FRED API issues | https://fred.stlouisfed.org/docs/api/ | Key rotation, rate limits |
| Alpaca broker support _(when active)_ | support@alpaca.markets | For fill disputes, account issues |
| Alpaca status _(when active)_ | https://status.alpaca.markets | Outages / maintenance windows |

---

## 5. Regular Maintenance

| Frequency | Task |
|-----------|------|
| Daily | Review HTML report Δ band; check Observability tab heartbeat and recession telemetry |
| Weekly | Glance at Conviction Calibration MAE; review any Dead-Letter Queue entries |
| Monthly | Re-run validation harness for all active strategies; rotate API keys (FRED, Robinhood) |
| Quarterly | Full review of `MAX_POSITION_WEIGHT`, `KELLY_FRACTION`, `KELLY_CAP`; check calibration curve for systematic bias |
| Annually | Full stress-test re-run for options-selling strategies; re-review `ADVISORY_ONLY` status if broker execution is intended |

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

The GUI also exposes the kill switch toggle in the Launcher tab → Safety Controls. While
the sentinel is active, the GUI safety indicator shows `🔴 PAUSED`.

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
| Suspicious pipeline output (all signals identical, all BUY, all NaN) | Pause immediately; run `python scripts/preflight_check.py` and check `logs/investyo.log` |

### Back up the database before any destructive investigation

```bash
cp quant_platform.db quant_platform_backup_$(date +%Y%m%d_%H%M%S).db
```

### Incident log

Document every pause in `output/decision_log.jsonl` via the Reports tab → Decision
Journal (entry type: "modified", notes: describe the anomaly and resolution). This keeps
a timestamped operator log that the calibration tracker can correlate with signal
accuracy changes.

---

## Incident response: data source degraded mid-session

When a data source (Alpaca market data, Finnhub, FRED, Robinhood) is reporting errors:

1. Open Safety tab → Dependency Map (`gui/dependency_map.py`).
2. Multi-select the degraded sources.
3. Read the impacted-consumers table — this is the authoritative list of
   strategies/tabs/reports that lose coverage right now.
4. If a CRITICAL consumer (e.g. `processing_engine`, `forecasting_engine`) appears in
   the list → pause recommendations via the kill-switch toggle in the Safety tab.
5. After remediation, refresh the Safety tab; the dashboard derives its state from files
   (`output/KILL_SWITCH`, `output/risk_gate_blocks.jsonl`), so there is no in-process
   cache to invalidate.

---

## Advisory-Only Mode (Tier 5.1, default-on)

`settings.ADVISORY_ONLY=true` is the project default.  Three enforcement layers keep the
broker surface quarantined:

1. **Orchestrator** — `main_orchestrator._execute_broker_orders` returns immediately with
   an INFO log before any broker import is reached.
2. **GUI** — `gui/app.py` renders a persistent `📋 ADVISORY MODE` banner; the Strategy
   Matrix mode toggle (Simulation / Paper / Live) is suppressed.
3. **Preflight** — four broker-dependent checks auto-skip; `advisory_only_active` check
   is PASS-loud (and PASS-with-warning when `ADVISORY_ONLY=false`).

**Re-enabling broker execution** requires ALL THREE flags to be `false` simultaneously:
`ADVISORY_ONLY=false AND DRY_RUN=false AND ALPACA_PAPER=false`. Follow the procedure in
§1 above and ensure `preflight_check.py` exits 0 with all broker checks passing before
any live run.

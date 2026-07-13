# InvestYo Quant Platform ("Stock Dashboard Py")

An automated quantitative analysis pipeline: fetches market/macro data, computes
technical & fundamental indicators, runs multi-horizon forecasts, backtests
strategies, persists signals to SQLite, and publishes results to Google Sheets /
an HTML report.

> **Advisory-only by default.** The platform ships with `ADVISORY_ONLY=true`. No
> orders are submitted to any broker. The pipeline produces recommendations,
> rationales, and an HTML report; an operator decides what to act on. Lifting the
> quarantine is a deliberate two-step config change — see "Advisory-only mode"
> below.

## Quick start (fresh machine)

```bash
# 1. Create the virtual environment (Python 3.12 required)
./setup.sh

# 2. Copy the environment template and fill in your credentials
cp .env.example .env
# edit .env — see "Required .env keys" below

# 3. Share the Google Sheet with the service-account email
#    Open credentials.json → find "client_email"
#    In the Sheet: Share → paste the email → Editor role

# 4. Verify everything works before relying on it
make verify           # env check + tests + one live cycle (or double-click verify.command)

# 5. Launch
./launch.command      # double-click from Finder, or run in terminal
```

---

## Required `.env` keys

Copy [`.env.example`](.env.example) to `.env` and fill in the values. **Never commit `.env`.**

| Key | Required? | Purpose |
|-----|-----------|---------|
| `FRED_API_KEY` | **Required** | Macro data (VIX, yield curve). Free key at [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) |
| `ADVISORY_ONLY` | Optional | `true` (default) — broker execution surface is quarantined. Flip to `false` only after a deliberate readiness review (see "Advisory-only mode") |
| `RH_USERNAME` | Optional | Robinhood read-only snapshot (held symbols always included) |
| `RH_PASSWORD` | Optional | — |
| `RH_MFA_SECRET` | Optional | Base32 TOTP secret (Robinhood → Settings → Security → Authenticator) |
| `ALPACA_API_KEY` | Optional | Broker execution (no-op while `ADVISORY_ONLY=true`) |
| `ALPACA_SECRET_KEY` | Optional | — |
| `ALPACA_PAPER` | Optional | `true` (default) = paper trading. Has no effect while `ADVISORY_ONLY=true` |
| `FINNHUB_API_KEY` | Optional | Company news / earnings headlines for the `news_catalyst` signal only. **Not** a fundamentals source — fundamentals are Yahoo statement-derived (free, `data/yahoo_fundamentals.py`). Absent = news_catalyst signal disabled |
| `NTFY_TOPIC` | Optional | Phone push alerts via ntfy.sh — set a random string, subscribe in the ntfy app |
| `WATCHLIST` | Optional | Comma-separated tickers (alternatives: `watchlist.txt` one per line, or Sheet2 column A — see "Ticker universe" below) |
| `DISCORD_WEBHOOK_URL` | Optional | Discord channel alerts |
| `SLACK_WEBHOOK_URL` | Optional | Slack channel alerts |
| `MACRO_REGIME_GATE_ENABLED` | Optional | `true` (default). When `false`, the orchestrator's macro kill-switch check is bypassed (hybrid mode) |
| `SNAPSHOT_HISTORY_DAYS` | Optional | `30` (default). Snapshots in `output/history/` older than this are pruned each run; `0` disables pruning |

All other keys (sizing parameters, risk-gate thresholds, financial constants) have
safe defaults — see the full list in [`.env.example`](.env.example).

---

## Ticker universe

`main.py` builds its evaluation universe from up to three sources, in priority order:

1. **Robinhood held positions** — always included when the snapshot is available.
2. **`WATCHLIST` env var or `watchlist.txt`** — merged in whenever present.
3. **Google Sheet → "Sheet2" column A** — consulted **only as a last-resort fallback**
   when sources 1 and 2 are both empty (e.g. Robinhood is unreachable and no
   watchlist is configured). Requires `credentials.json`; silently skipped if the
   sheet, tab, or credential is missing.

If all three are empty, the run logs a warning naming every remediation path and
exits cleanly without evaluating anything.

---

## Launching

```bash
./launch.command                          # double-click from Finder — runs main.py
./launch_gui.command                      # InvestYo Command Center (Streamlit GUI)

.venv/bin/python3 main.py                 # single advisory cycle
.venv/bin/python3 main.py --interval 60   # refresh every 60 s
.venv/bin/python3 main.py --refresh-account   # force fresh Robinhood snapshot
.venv/bin/python3 main_orchestrator.py    # async orchestrator (HTML report)
```

The **Command Center GUI** (`./launch_gui.command` or `streamlit run gui/app.py`) is the
recommended day-to-day surface. It bundles the Launcher, Reports, Settings, Strategy
Matrix, Paper-Trading Monitor, Gravity Audit, Technical Options Matrix, Market Data,
Live Inventory, and Observability tabs over the file-backed state the pipeline writes
(no async broker calls from the GUI).

---

## Advisory-only mode

The platform's default mode is **advisory** (`ADVISORY_ONLY=true` in `.env`):

- `main_orchestrator._execute_broker_orders()` returns immediately before any broker
  import — no orders are submitted regardless of `ALPACA_*` credentials.
- The GUI Strategy Matrix mode toggle is replaced with a read-only "broker execution
  disabled" banner.
- `scripts/preflight_check.py` auto-skips broker-readiness checks
  (`alpaca_configured`, `alpaca_paper_mode`, `dry_run_disabled`, `paper_trading_duration`)
  and instead enforces the `advisory_only_active` check.

### Pause the recommendation engine

Even in advisory mode you can fully pause signal generation with the kill switch (the
sentinel file `output/KILL_SWITCH`). When active, `main.run_once()` and
`main_orchestrator._main_body()` return immediately and the observability dashboard
continues showing the last written state. CLI:

```bash
python -m execution.kill_switch --activate --reason "advisory pause — investigating"
python -m execution.kill_switch --status
python -m execution.kill_switch --deactivate
```

### Macro-triggered advisory gating (automatic)

Even with the kill switch inactive, `engine/advisory.evaluate()` applies conservative
overrides per-symbol when macro conditions deteriorate:

| Condition | Effect |
|---|---|
| `market_regime` = `RECESSION` or `CREDIT EVENT` | Hard gate: BUY / STRONG BUY → HOLD |
| `VIX > 30` OR `Sahm ≥ 0.5` | Soft gate: composite score –25 pts |
| Financials / Financial Services / Real Estate + inverted curve (`< 0`) OR HY OAS `> 6%` | Sector veto: BUY → HOLD |

Each override is cited in the advisory rationale so the HTML report explains the gate.

### Lifting the quarantine

Re-enabling broker execution is a deliberate sequence: (1) set `ADVISORY_ONLY=false`
in `.env`; (2) run `python scripts/preflight_check.py` — it should now run the broker
gates instead of skipping them; (3) re-launch. The GUI mode toggle (Simulation / Paper /
Live) reappears automatically. See `docs/GO_LIVE_CHECKLIST.md` for the full readiness gate.

---

## Verify before use

```bash
make verify          # env check → test suite → one live cycle → print summary
./verify.command     # same, double-clickable from macOS Finder
```

---

## Other commands

```bash
pytest                                      # full test suite
pytest tests/test_pipeline_smoke.py -v     # end-to-end smoke tests only
make smoke                                  # same
streamlit run gui/app.py                    # full Command Center GUI (10 tabs, incl. Observability)
python scripts/preflight_check.py           # pre-live readiness gate (exit 0 = pass)
python scripts/preflight_check.py --json    # machine-readable output
python -m execution.kill_switch --status    # check / toggle the advisory pause gate
python3 -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD
```

---

## Configuration

All runtime configuration is centralized in [`settings.py`](settings.py) and loaded
from a local `.env` file (never committed). The most important key is `FRED_API_KEY`.

## Security — rotating the leaked FRED key

A FRED API key was previously hardcoded in `main.py` and `main_orchestrator.py`
and committed to git history. **It is compromised and must be rotated.** The
application now reads the key from the environment and prints a CRITICAL warning
(detected via a stored SHA-256 digest, so the literal lives nowhere in source)
if the leaked value is still in use.

To rotate:

1. Sign in at <https://fred.stlouisfed.org/> and open your account's API keys page:
   <https://fred.stlouisfed.org/docs/api/api_key.html>
2. **Revoke / delete** the old, compromised key.
3. **Request a new key.**
4. Put the new key in your local `.env` file:
   ```
   FRED_API_KEY=<your-new-key>
   ```
5. Confirm the platform no longer logs the "COMPROMISED" warning on startup.

> Note: rotating the key stops it from being *used*, but it still exists in git
> history. If this repository is or ever becomes public, scrub the secret from
> history (e.g. with `git filter-repo`) in addition to rotating.

## Strategy Validation Harness

To prevent overfitting, selection bias, and unviable strategy deployment, the platform includes a master strategy validation harness.

### Running Validation

Execute the harness from the command line:

```bash
python3 -m validation.harness --strategy <name> --start YYYY-MM-DD --end YYYY-MM-DD
```

For custom strategies, instantiate the `StrategyValidationHarness` class and pass your strategy function, universe constituent provider, and cost model.

### Deployability Gates

A strategy is marked as `deployable=True` (eligible for deployment) if and only if it satisfies all of the following criteria:
1. **Probability of Backtest Overfitting (PBO)**: `< 50%` (PBO < 0.5)
2. **Deflated Sharpe Ratio (DSR)**: `> 95%` (DSR > 0.95)
3. **Net-of-Cost Sharpe Ratio**: `> 0.5`
4. **Max Drawdown**: `< 30%` (Max DD < 0.30)

Options-selling strategies (constructed with `is_options_selling=True`) carry an
additional **tail-scenario stress gate**: they must survive every dated shock window
(OCT_2008, FEB_2018, MAR_2020, AUG_2024) with drawdown < 50% and no account blow-up.
Fails closed if stress results are missing.

---

## Daily report

Every full pipeline run writes an HTML report to `output/`. The report leads with:

- **Δ Since Last Run band** — new BUYs, action flips (HOLD → BUY etc.), conviction
  deltas above `SNAPSHOT_CONVICTION_DELTA_THRESHOLD` (default 0.2), holdings added /
  dropped, and macro regime changes. Hidden on first run.
- **Holdings & P&L** — sourced from the Robinhood snapshot when available.
- **Action & Rationale** — per-symbol BUY / HOLD / SELL with conviction meter and a
  click-to-expand rationale that includes any macro-gate override.

Snapshots are rotated under `output/history/`; older than `SNAPSHOT_HISTORY_DAYS` are
pruned each run.



## Recent Architecture Updates
- **Signal Engine Vectorization**: As of Phase 4, the entire `SignalAggregator` and all `SignalModule` implementations are natively vectorized in pandas/numpy (O(1) block computation). Row-based ticker iteration in the aggregation step has been removed to maximize performance.

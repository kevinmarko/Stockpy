# InvestYo Quant Platform — How-To Guide

A practical reference for running, configuring, and interpreting every part of the platform.

---

## Table of Contents

1. [What This Platform Does](#1-what-this-platform-does)
2. [First-Time Setup](#2-first-time-setup)
3. [Configuring Your Environment](#3-configuring-your-environment)
4. [Choosing Your Ticker Universe](#4-choosing-your-ticker-universe)
5. [Running the Pipeline](#5-running-the-pipeline)
6. [Understanding the Output](#6-understanding-the-output)
7. [Reading the Action Signals](#7-reading-the-action-signals)
8. [Understanding Position Sizing (Kelly Target)](#8-understanding-position-sizing-kelly-target)
9. [The Macro Regime System](#9-the-macro-regime-system)
10. [Validating a Strategy Before Going Live](#10-validating-a-strategy-before-going-live)
11. [Paper Trading Workflow](#11-paper-trading-workflow)
12. [The Observability Dashboard](#12-the-observability-dashboard)
13. [Preflight Check — Are You Ready to Go Live?](#13-preflight-check--are-you-ready-to-go-live)
14. [Setting Up Alerts](#14-setting-up-alerts)
15. [The Kill Switch](#15-the-kill-switch)
16. [Adding Tickers or Changing the Universe](#16-adding-tickers-or-changing-the-universe)
17. [Adjusting Signal Weights](#17-adjusting-signal-weights)
18. [Google Sheets Integration (Legacy)](#18-google-sheets-integration-legacy)
19. [Running Tests](#19-running-tests)
20. [Troubleshooting Common Problems](#20-troubleshooting-common-problems)

---

## 1. What This Platform Does

InvestYo is an **automated quantitative analysis pipeline**. Every time you run it, it:

1. **Fetches live data** — price history from Yahoo Finance, macroeconomic indicators from FRED (Federal Reserve Economic Data)
2. **Computes indicators** — RSI, MACD, Aroon, ATR, GARCH volatility, Graham Number, implied volatility rank, and more
3. **Runs forecasts** — ARIMA, Monte Carlo simulation, Holt-Winters exponential smoothing, and a CNN-LSTM deep learning model, all multi-horizon
4. **Detects the macro regime** — classifies the current environment as RISK ON / NEUTRAL / RECESSION / CREDIT EVENT using yield curve, credit spreads, VIX, and a Hidden Markov Model (HMM) second opinion
5. **Generates signals** — for each ticker: STRONG BUY / BUY / HOLD / RISK REDUCE, plus an options overlay recommendation
6. **Sizes positions** — calculates a Kelly Target (% of capital to allocate) based on your actual trade history
7. **Submits orders** — if Alpaca is configured, sends buy/sell orders to your paper or live account
8. **Produces reports** — an HTML dashboard, an interactive Plotly volatility chart, and a JSON payload

You can use the output purely as research (read the HTML report, decide manually), or connect Alpaca to automate order submission.

---

## 2. First-Time Setup

### Step 1 — Install dependencies

```bash
cd /Users/kevinlee/Desktop/Stockpy
./setup.sh
```

This creates a Python 3.12 virtual environment at `.venv/` and installs everything in `requirements.txt`. You only need to do this once (or after pulling a new version that changes `requirements.txt`).

### Step 2 — Create your `.env` file

```bash
cp .env.example .env
```

Then open `.env` in any editor and fill in your API keys. The absolute minimum to get started:

```
FRED_API_KEY=your_key_here
```

Everything else has a working default. See [Section 3](#3-configuring-your-environment) for the full breakdown.

### Step 3 — Initialize the database

```bash
python3 database_setup.py
```

This creates `quant_platform.db` (SQLite) with the correct schema for storing daily signals and execution logs. The file already exists in the repo with 169 seeded trades — you only need to re-run this if you want to reset it or if the schema has changed.

### Step 4 — Verify your setup

```bash
python scripts/preflight_check.py
```

This runs 11 automated readiness checks. On a fresh setup you will see some failures (especially `heartbeat_fresh` and `paper_trading_duration`) — that is normal. See [Section 13](#13-preflight-check--are-you-ready-to-go-live) for what each check means.

---

## 3. Configuring Your Environment

All settings live in `.env`. The platform reads it automatically on startup via `settings.py`.

### Required settings

| Setting | How to get it |
|---------|--------------|
| `FRED_API_KEY` | Free at [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html) — create an account, request a key |

### Broker settings (needed only for automated order submission)

| Setting | Notes |
|---------|-------|
| `ALPACA_API_KEY` | From your Alpaca dashboard under "API Keys" |
| `ALPACA_SECRET_KEY` | Shown once when you create the key — save it immediately |
| `ALPACA_PAPER` | `true` (default) = paper trading endpoint. Change to `false` only for live trading |

If you omit `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, the pipeline still runs fully — it just skips order submission and prints `"skipping broker execution"`.

### Settings with safe defaults (you can ignore these initially)

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `DRY_RUN` | `false` | When `true`, orders are logged but never sent to Alpaca |
| `ALPACA_PAPER` | `true` | Paper vs live account |
| `MAX_CORRELATION` | `0.85` | Blocks a new position if it's too correlated with an existing one |
| `DAILY_LOSS_LIMIT_PCT` | `0.02` | Halts new buys if you're down 2% on the day |
| `MAX_ORDER_RATE_PER_MIN` | `10` | Rate limiter on order submissions |
| `VOL_TARGET` | `0.10` | Target annualized volatility for position sizing (10%) |
| `KELLY_FRACTION` | `0.5` | Half-Kelly (conservative) — reduces the raw Kelly bet by 50% |
| `KELLY_CAP` | `0.20` | Maximum allocation from Kelly formula alone (20%) |
| `MAX_POSITION_WEIGHT` | `1.0` | Hard ceiling on any single position (100% of capital — effective limit is much lower due to Kelly) |
| `OUTPUT_DIR` | `./output` | Where HTML reports, heartbeat, and state snapshots are written |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `PAPER_TRADING_START_DATE` | _(none)_ | Set this to today's date (YYYY-MM-DD format) when you start paper trading — the preflight check uses it to verify 90 days of history |

---

## 4. Choosing Your Ticker Universe

The default tickers are `AAPL`, `MSFT`, `JNJ`, `AGNC`.

### To change the tickers

Edit `.env`:

```
DEFAULT_TICKERS=["AAPL","GOOGL","MSFT","JPM","XOM","BRK-B"]
```

Or override programmatically in `settings.py` by changing the `DEFAULT_TICKERS` field default.

### Guidelines for picking tickers

- Use standard Yahoo Finance ticker symbols (e.g., `BRK-B`, not `BRK.B`)
- The pipeline fetches ~2 years of daily OHLCV history per ticker for indicators and the CNN-LSTM model
- Cross-sectional momentum (`cross_sectional_momentum` signal) ranks tickers relative to each other — you need at least 3–5 tickers for this to be meaningful
- The multifactor signal (`multifactor`) excludes tickers with market cap below $300M (`MULTIFACTOR_MICROCAP_THRESHOLD`) from cross-sectional z-scoring — microcaps still get analyzed but receive a neutral 0.0 multifactor score
- SPY is always fetched automatically (it's needed for the HMM regime detector), even if it's not in your ticker list

### How `main.py` builds its universe (held ∪ watchlist ∪ Sheet2 fallback)

The advisory orchestrator `main.py` does **not** use `DEFAULT_TICKERS`. It assembles its universe from up to three sources, in strict priority order (`_build_universe()`):

1. **Robinhood held positions** — every symbol in your account snapshot is always included when the snapshot is available.
2. **`WATCHLIST` env var or `watchlist.txt`** — merged in whenever present. The env var (comma-separated) takes precedence over the file; the file is one ticker per line with `#` for comments.
3. **Google Sheet → "Sheet2" column A** — consulted **only as a last-resort fallback** when sources 1 and 2 are both empty (e.g. Robinhood is unreachable and you have no watchlist configured). This reads column A of the "Sheet2" tab via `credentials.json`. If the credential, spreadsheet, or tab is missing — or any API error occurs — it logs a warning and returns an empty list rather than crashing.

If all three are empty, `main.py` logs a warning that names all four remediation paths (RH_* env vars, `WATCHLIST`, `watchlist.txt`, Sheet2 column A) and exits the cycle cleanly. SPY is still fetched automatically by the macro/HMM layer regardless.

See [Section 18](#18-google-sheets-integration-legacy) for the Sheet setup.

---

## 5. Running the Pipeline

### The easiest way — double-click on macOS

`launch.command` at the project root is a macOS launcher you can double-click from **Finder** or the **Dock**. It:

1. Navigates to the project root automatically.
2. Verifies `.venv` exists — if not, prints exact instructions for creating it.
3. Confirms the `.venv` Python is exactly **3.12.x** — if it's 3.14 or anything else, shows a clear error and exits rather than running with the wrong interpreter.
4. Warns if `.env` is missing (non-fatal — the pipeline degrades gracefully).
5. Runs `python main.py --interval 60` (keeps refreshing every 60 seconds) **or** `python main.py` (single run), depending on the `REFRESH_INTERVAL_SECONDS` variable at the top of the file.
6. Pauses with **"Press any key to close"** on exit so you can always read the output.

**One-time setup** (already done — listed here for reference if you ever recreate the file):

```bash
chmod +x launch.command
```

**To add to the Dock**: drag `launch.command` to your Dock → right-click → Options → Keep in Dock.

**To switch between interval and single-run mode**: open `launch.command` in any text editor and change line:

```bash
REFRESH_INTERVAL_SECONDS=60   # change to 0 for a single run
```

---

### From Terminal — primary async orchestrator

```bash
python3 main_orchestrator.py
```

This runs the full async pipeline: data fetch → macro regime → options analysis → processing → forecasting → strategy signals → HTML report → broker orders (if Alpaca configured).

It auto-activates the `.venv` virtual environment if you haven't done so manually.

### Dry-run mode (safe to test — no orders sent)

```bash
python3 main_orchestrator.py --dry-run
```

The pipeline runs identically but any generated orders are logged rather than submitted to Alpaca. Use this to verify the setup before enabling live order flow.

### Offline / mock mode

If `credentials.json` (Google service account) is not present, the orchestrator automatically falls back to `MockDataEngine`, which generates deterministic synthetic data. Useful for testing code changes without network access. You will see:

```
WARNING - credentials.json not found. Operating with deterministic MockDataEngine.
```

This is expected in development. Your FRED key is still used in normal mode.

### The legacy orchestrator (Google Sheets output)

```bash
python3 main.py
```

This is the original synchronous pipeline that writes results to Google Sheets. Requires `credentials.json`. Use `main_orchestrator.py` for everything new — `main.py` is kept for the Sheets integration.

---

## 6. Understanding the Output

After a successful run you will have:

### Terminal output

The pipeline prints a JSON payload at the end:

```json
=== FINAL ACTIONABLE PAYLOAD REPRESENTATION ===
[
    {
        "Symbol": "AAPL",
        "Price": 195.42,
        "Action Signal": "BUY",
        "buyRange": "Buy Zone: $191.20 - $194.80",
        "Kelly Target": 0.142,
        "Option Strategy": "Bull Call Spread",
        "GARCH_Vol": 0.183,
        "True_IVR": 52.3
    },
    ...
]
```

### HTML report

`output/daily_report_dashboard.html` — open in any browser. Contains:
- Macro regime status and key indicators
- Per-ticker signal table with scores, forecasts, fundamentals
- Buy/hold/trim price ranges
- Options overlay recommendations

### Interactive volatility chart

`output/volatility_bands_dashboard.html` — Plotly chart of the first ticker's price history with volatility bands overlaid. Open in a browser.

### State snapshot (for the dashboard)

`output/state_snapshot.json` — machine-readable summary consumed by the Streamlit observability dashboard. Updated every pipeline run.

### Database

`quant_platform.db` — SQLite database storing signal history and trade records. Query it with any SQLite client:

```bash
sqlite3 quant_platform.db "SELECT * FROM DailySignals ORDER BY date DESC LIMIT 10;"
```

---

## 7. Reading the Action Signals

Each ticker gets one of five signals:

| Signal | Meaning | What to do |
|--------|---------|-----------|
| **STRONG BUY** | High-conviction long — strong macro, strong technicals, strong fundamentals | Consider a full Kelly-sized position |
| **BUY** | Long signal — conditions are favorable but not at maximum conviction | Consider a Kelly-sized position |
| **HOLD** | Already positioned — stay in, don't add | No new buys; maintain existing position |
| **RISK REDUCE** | Conditions deteriorating — tighten stops | Consider trimming; tighten stop to the level shown in `buyRange` |
| **AVOID** | Do not initiate or add | Stay out or exit |

### Price ranges

The `buyRange` / `Actionable Advice Signal` field gives specific price levels:

- **Buy Zone: $X - $Y** — best entry window (ATR-based pullback from current price)
- **Hold Range: $X - $Y** — Chandelier Exit trailing stop as the lower bound, 2×ATR above current price as the upper
- **Trim @ $X | Stop @ $Y** — for RISK REDUCE: trim target above current price, hard stop below

### Kill switch override

If the macro kill switch fires (VIX > 30 AND the Sahm Rule >= 0.5, or RECESSION regime with HMM agreement), all BUY and STRONG BUY signals are forced to HOLD automatically. The signal will show `HOLD` even if the underlying score is strong.

---

## 8. Understanding Position Sizing (Kelly Target)

The `Kelly Target` is a number between 0.0 and 1.0 representing the **fraction of your capital** to allocate to that position.

### How it's calculated

**If you have enough trade history (≥ 30 closed trades):**
The platform uses the fractional Kelly formula:
```
f* = (p × b − (1−p)) / b × KELLY_FRACTION
```
Where:
- `p` = estimated win rate from your actual trade history
- `b` = average payoff ratio (avg win / avg loss) from your history
- `KELLY_FRACTION` = 0.5 (half-Kelly — halves the bet for safety)
- Result is capped at `KELLY_CAP` = 0.20 (max 20% from this formula)

The database ships with 169 real closed trades seeded from a Robinhood account, so you start on the real Kelly path immediately.

**If you have fewer than 30 trades for a strategy:**
Falls back to volatility targeting:
```
weight = VOL_TARGET / realized_vol
```
Where `VOL_TARGET` = 0.10 (10%). A stock with 20% annualized vol gets a 50% weight; a stock with 40% vol gets a 25% weight.

**Both paths are clamped** to `MAX_POSITION_WEIGHT` = 1.0 (100% max single name). In practice the Kelly cap (20%) and the HMM regime multiplier keep actual targets much lower.

### HMM regime multiplier

The Kelly Target is further scaled by `hmm_risk_on_probability` (the HMM's current "probability that we are in a risk-on regime"). When the HMM is bearish (low risk-on probability), position sizes shrink proportionally. When the HMM is unavailable, this multiplier defaults to 1.0 (no effect).

### Practical example

```
Kelly Target = 0.14 → allocate 14% of your total capital to this position
```

If you have a $100,000 paper account, 14% = $14,000 in that ticker.

---

## 9. The Macro Regime System

The platform classifies the current macroeconomic environment before evaluating any stock. This regime gates all signals.

### The four regimes

| Regime | Trigger conditions | Effect |
|--------|--------------------|--------|
| **RISK ON** | Yield curve not inverted AND credit spreads low AND Sahm Rule low | Full signal strength |
| **NEUTRAL** | Mild deterioration — or HMM disagrees with RISK ON | Signals active but HMM may reduce sizing |
| **RECESSION** | Yield curve < −0.25 AND (credit spread > 6% OR Sahm Rule ≥ 0.6) | Kill switch may activate; BUY→HOLD override |
| **CREDIT EVENT** | Credit spreads > 6% | Kill switch may activate |

### The FRED indicators used

| Indicator | FRED series | What it measures |
|-----------|-------------|-----------------|
| Yield curve | `T10Y2Y` | 10-year minus 2-year Treasury spread. Negative = recession signal |
| Credit spreads | `BAMLH0A0HYM2` | High-yield OAS. Spike = credit stress |
| Sahm Rule | `SAHMREALTIME` | Unemployment rise trigger. ≥ 0.5 = recession signal |
| VIX | `VIXCLS` | Equity fear gauge |
| Inflation | `CPIAUCSL` | Consumer price index YoY |
| 10-year yield | `DGS10` | Nominal rate |

### The HMM second opinion

A 3-state Gaussian Hidden Markov Model (bull / sideways / bear) runs in parallel using 4 features: SPY daily returns, 20-day realized vol, VIX level, and yield curve spread. It produces a `hmm_risk_on_probability` between 0 and 1.

- If probability < 0.30 and the rules-based regime is RISK ON → **downgraded to NEUTRAL** (logged)
- If probability < 0.20 (risk_off > 0.80) and rules-based regime is RECESSION → **kill switch triggers at lower thresholds** (VIX > 25 instead of 30, Sahm ≥ 0.3 instead of 0.5)
- The HMM can only pull signals down, never push them up

If the HMM fails (insufficient data, FRED unavailable), it returns `None` and the platform behaves exactly as if the HMM doesn't exist — no degradation.

---

## 10. Validating a Strategy Before Going Live

Before trusting a strategy with real money, run the validation harness. It checks for overfitting using three rigorous methods:

```bash
python -m validation.harness --strategy main_pipeline --start 2015-01-01 --end 2024-12-31
```

### What gets checked

| Check | Pass threshold | What it means |
|-------|--------------|---------------|
| **PBO** (Probability of Backtest Overfitting) | < 0.50 | Lower is better. > 0.50 means the strategy fits noise, not signal |
| **DSR** (Deflated Sharpe Ratio) | > 0.95 | Sharpe adjusted for the number of trials — guards against cherry-picking |
| **Net Sharpe** | > 0.50 | After realistic transaction costs |
| **Max Drawdown** | < 30% | Peak-to-trough decline |

All four must pass for `"deployable": true`.

### For options-selling strategies

Add the `is_options_selling=True` flag when constructing the harness in code. This adds a fifth stress-test gate that replays the strategy through four historical shock windows:

| Window | Event | Required: survive AND max drawdown < 50% |
|--------|-------|------------------------------------------|
| OCT_2008 | Lehman collapse, VIX > 80 | Required |
| FEB_2018 | Volmageddon / XIV blowup | Required |
| MAR_2020 | COVID crash | Required |
| AUG_2024 | Yen carry unwind | Required |

### Where reports go

Reports are saved to `reports/` as:
- `reports/<strategy_name>_validation_summary.json` — machine-readable (consumed by preflight check)
- `reports/<strategy_name>_validation_report.html` — human-readable with Plotly charts

### Walk-forward stability

The harness also runs walk-forward analysis (rolling train/test splits) and reports how stable the Sharpe ratio is across time. A strategy that shows 1.5 Sharpe in-sample but 0.2 Sharpe out-of-sample is overfit.

---

## 11. Paper Trading Workflow

Paper trading = running with real market data and real logic, but simulated money (no real orders). This is mandatory before going live.

### Start paper trading

1. Get Alpaca paper trading credentials from [alpaca.markets](https://alpaca.markets) — click "Create Account" → paper account → "API Keys"
2. Add to `.env`:
   ```
   ALPACA_API_KEY=PK...
   ALPACA_SECRET_KEY=...
   ALPACA_PAPER=true
   PAPER_TRADING_START_DATE=2026-06-24
   ```
3. Run the pipeline:
   ```bash
   python3 main_orchestrator.py
   ```
4. Watch the Alpaca dashboard — you should see paper orders appear

### Automate daily runs

To run automatically every trading day, add a cron job:

```bash
# Run at 9:35 AM ET every weekday
35 9 * * 1-5 cd /Users/kevinlee/Desktop/Stockpy && python3 main_orchestrator.py >> logs/pipeline.log 2>&1
```

Or use `launchd` on macOS (more reliable than cron for Mac):

```xml
<!-- ~/Library/LaunchAgents/com.investyo.pipeline.plist -->
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>35</integer>
    <key>Weekday</key><integer>1</integer>
</dict>
```

### Monitor while running

```bash
streamlit run observability/dashboard.py
```

Opens a browser dashboard at `http://localhost:8501` with live P&L, open positions, kill switch status, and the last 100 risk gate blocks.

### Minimum paper trading period

The preflight check requires **90 days** of continuous paper trading before going live. This is enforced via `PAPER_TRADING_START_DATE` in your `.env`.

---

## 12. The Observability Dashboard

```bash
streamlit run observability/dashboard.py
```

Auto-refreshes every 30 seconds (configurable via `DASHBOARD_REFRESH_SECONDS`).

### What you'll see

| Panel | Data source | What it shows |
|-------|-------------|--------------|
| Kill switch banner | `output/KILL_SWITCH` file | Red = active (all orders blocked), Green = inactive |
| Macro regime | `output/state_snapshot.json` | Current regime, VIX, HMM risk-on probability |
| Strategy P&L | `quant_platform.db` | Realized P&L by strategy |
| Open positions | `quant_platform.db` vs signals | Internal book vs pipeline recommendations |
| Portfolio heat | State snapshot | Adverse unrealized P&L as % of equity |
| Validation status | `reports/*_validation_summary.json` | Deployable / not deployable per strategy |
| Recent closed trades | `quant_platform.db` | Last 20 fills |
| Risk gate block log | `output/risk_gate_blocks.jsonl` | Last 100 blocked orders and which check blocked them |

### Staleness warning

If the orchestrator hasn't run for > 2 hours (detected via `output/heartbeat.txt`), the dashboard shows a yellow staleness warning. This means no fresh signals are available.

---

## 13. Preflight Check — Are You Ready to Go Live?

```bash
python scripts/preflight_check.py
```

Runs 11 checks. **All must pass (exit code 0) before going live.** Here's what each check does and how to fix failures:

| Check | Passes when | How to fix a failure |
|-------|------------|---------------------|
| `fred_key_configured` | `FRED_API_KEY` is set and is not the old leaked key | Add key to `.env` |
| `alpaca_configured` | Both `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are set | Add keys to `.env` |
| `alpaca_paper_mode` | `ALPACA_PAPER=true` — **warning only, not blocking** | Change to `false` only when ready to go live |
| `dry_run_disabled` | `DRY_RUN=false` | Set `DRY_RUN=false` in `.env` |
| `env_not_committed` | `.env` is not tracked by git | Add `.env` to `.gitignore` (already done in this repo) |
| `kill_switch_inactive` | No `output/KILL_SWITCH` file exists | Run `python -m execution.kill_switch --deactivate` |
| `heartbeat_fresh` | `output/heartbeat.txt` is < 2 hours old | Run `python3 main_orchestrator.py` to generate it |
| `db_exists` | `quant_platform.db` exists and is non-empty | Run `python3 database_setup.py` |
| `paper_trading_duration` | ≥ 90 days since `PAPER_TRADING_START_DATE` | Wait — this is intentional; set your start date when you begin |
| `validation_reports` | At least one report exists, all are deployable, and all are < 30 days old | Run `python -m validation.harness --strategy main_pipeline --start 2015-01-01 --end 2024-12-31` |
| `no_unexpected_risk_blocks` | No `minimum_validation` blocks in `risk_gate_blocks.jsonl` in the last 24h | Generate a validation report — the minimum_validation risk gate is blocking orders because no deployable reports exist |

### JSON output (for automation)

```bash
python scripts/preflight_check.py --json
```

Returns a JSON array suitable for parsing in CI or monitoring scripts.

### Skipping checks

```bash
python scripts/preflight_check.py --skip paper_trading_duration heartbeat_fresh
```

Useful during development when you know certain checks will fail. Do not skip checks when actually going live.

---

## 14. Setting Up Alerts

The platform has **two independent alert layers**: push notifications to your phone via ntfy.sh (new, from `alerting.py`) and channel-based alerts for operational events (Discord/Slack/email/file, from `observability/alerts.py`). Both are fully optional — the app runs without either.

---

### Phone push notifications — ntfy.sh (alerting.py)

ntfy.sh is a free, open-source push-notification service with native iOS and Android apps. No account is required for public topics.

#### Setup (5 minutes)

1. Install the **ntfy** app on your phone — search "ntfy" on the App Store or Google Play.
2. Choose a topic name that is **long and random** (it acts as your password — anyone who knows it can see your notifications). Example: `investyo-kml-x9f2q7`.
3. In the ntfy app, tap **Subscribe to topic** → enter your topic name.
4. Add to `.env`:
   ```
   NTFY_TOPIC=investyo-kml-x9f2q7
   ```

#### What gets sent

| Event | Priority | When |
|-------|----------|------|
| ⚠ Errors Detected | **HIGH** (always makes a sound) | Any symbol-level pipeline failure |
| ✓ Refresh Complete | Default | Once per launch (not per interval tick) |

The error notification lists which symbols failed and at which pipeline stage. The "refresh complete" notification includes the full run summary (BUY/SELL/HOLD counts, top 3 recommendations, duration).

**Interval mode** (`python3 main.py --interval 60`): the "refresh complete" notification fires only once per launch, not once per tick. Error notifications fire every cycle where errors occur.

#### Without NTFY_TOPIC

When `NTFY_TOPIC` is unset `notify()` is a silent no-op — the app runs identically. Only the rotating log file (`logs/investyo.log`) is written.

---

### Log file (always-on, no config needed)

`logs/investyo.log` is created automatically on first run. It rotates at 10 MB and keeps 5 backups (≈50 MB max). The format is:

```
2026-06-25 09:35:01  INFO      InvestYo.main — Evaluating 12 symbols...
2026-06-25 09:35:08  WARNING   InvestYo.main — Advisory failed for TSLA: TimeoutError
2026-06-25 09:35:09  INFO      InvestYo.main —
InvestYo Run — 2026-06-25 09:35:01 UTC  (8.4 s)
Universe: 12 evaluated  (11 OK, 1 error)
Signals : BUY=4  HOLD=6  SELL=1
Errors  : 1  (TSLA @ advisory_evaluate)
── Top 3 actionable ──────────────────────────────────
  1. BUY  AAPL     conviction=0.82  pos=4.5%  "Strong momentum..."
```

---

### Operational event alerts — Discord (easiest)

1. In Discord: open a channel → Edit Channel → Integrations → Webhooks → New Webhook → Copy URL
2. Add to `.env`:
   ```
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```

### Slack

1. In Slack: go to api.slack.com/apps → Create App → Incoming Webhooks → Add New Webhook to Workspace → Copy URL
2. Add to `.env`:
   ```
   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
   ```

### Alert log file (always-on audit trail)

```
ALERT_FILE_PATH=/Users/kevinlee/Desktop/Stockpy/logs/alerts.jsonl
```

Every alert is appended as a JSON line. Useful for post-incident review.

### Email

```
ALERT_EMAIL_FROM=alerts@yourdomain.com
ALERT_EMAIL_TO=you@email.com
ALERT_SMTP_HOST=smtp.gmail.com
ALERT_SMTP_PORT=587
ALERT_SMTP_USER=alerts@yourdomain.com
ALERT_SMTP_PASSWORD=your_app_password
```

For Gmail: use an App Password (not your main password). Google account → Security → 2-Step Verification → App Passwords.

### Alert severity levels

| Level | Examples |
|-------|---------|
| **CRITICAL** | Kill switch activated, broker position drift detected, broker connection lost, missing/invalid validation report |
| **WARNING** | Portfolio heat > 5%, correlation concentration, large fill slippage vs model cost |
| **INFO** | Order filled, daily rebalance complete, end-of-day summary |

---

## 15. The Kill Switch

The kill switch immediately halts all new order submissions. Use it in an emergency.

### Check status

```bash
python -m execution.kill_switch --status
```

### Activate (block all orders now)

```bash
python -m execution.kill_switch --activate "manual emergency halt"
```

Once active, `OrderManager` raises `KillSwitchActiveError` before even looking at any order. The pipeline continues running and producing signals — only order submission is blocked.

### Deactivate (resume orders)

```bash
python -m execution.kill_switch --deactivate
```

### How it works

The kill switch is a file: `output/KILL_SWITCH`. Its presence = active. The platform checks for file existence on every order attempt — no database, no network call, no race condition. To activate from code:

```python
from execution.kill_switch import GlobalKillSwitch
ks = GlobalKillSwitch()
ks.activate("VIX spiked above 45")
```

### Automatic kill switch

The platform auto-fires the kill switch in the macro kill switch check within the rules-based regime. You don't need to trigger this manually — it fires when:
- `vix > 30` AND `sahm_rule >= 0.5` (base condition), OR
- The regime is RECESSION AND HMM agrees risk-off > 70% AND `vix > 25` OR `sahm >= 0.3` (faster trigger with HMM agreement)

---

## 16. Adding Tickers or Changing the Universe

### In `.env` (simplest)

```
DEFAULT_TICKERS=["AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","JPM","JNJ","XOM"]
```

### Running a backtest on the S&P 500 universe

The `universe_engine.py` module can reconstruct the S&P 500's historical constituents (point-in-time, to avoid survivorship bias):

```python
from universe_engine import UniverseEngine
ue = UniverseEngine()
universe = ue.get_universe(as_of_date="2020-01-01")  # constituents as of that date
print(f"Survivorship bias estimate: {ue.survivorship_bias_warning()}")
```

### Pair trading universe

For pairs trading, pick two tickers that are economically related (same sector, similar business). The cointegration engine will test whether the pair is statistically tradeable:

```python
from pairs.cointegration import test_cointegration
result = test_cointegration(price_series_a, price_series_b)
# result["cointegrated"] = True/False
# result["half_life"] = days for spread to mean-revert (target: 5-60)
```

---

## 17. Adjusting Signal Weights

The final score for each ticker is a weighted sum of 14 signal modules. Weights are set in `settings.py` (or overridden in `.env` as a JSON dict via `SIGNAL_WEIGHTS`).

### Current weights and what each module measures

| Module | Default weight | What it measures |
|--------|----------------|-----------------|
| `macro_regime` | 45.0 | Is the macro environment supportive? (highest weight — regime gates everything) |
| `edge_garch` | 35.0 | Options IV rank vs realized GARCH vol — is IV mispriced? |
| `dividend_quality` | 25.0 | Dividend history, payout ratio, yield stability |
| `rsi_extremes` | 20.0 | RSI overbought/oversold extremes |
| `graham_value` | 15.0 | Price vs Benjamin Graham intrinsic value |
| `macd_momentum` | 15.0 | MACD crossover momentum |
| `aroon_trend` | 15.0 | Aroon oscillator trend strength |
| `timeseries_momentum` | 15.0 | 12-month time-series momentum (Moskowitz/Ooi/Pedersen) |
| `cross_sectional_momentum` | 15.0 | 12-1 month cross-sectional rank vs peers (Jegadeesh-Titman) |
| `multifactor` | 15.0 | Fama-French: Value, Quality, Low-Vol, Size composite |
| `forecast_alignment` | 10.0 | Do ARIMA/Monte Carlo/HW/CNN-LSTM agree on direction? |
| `relative_strength` | 10.0 | Price strength vs SPY |
| `sortino_drawdown` | 10.0 | Sortino ratio and drawdown penalty |
| `rsi2_mean_reversion` | 10.0 | RSI(2) short-term mean reversion (Connors) — suppressed in RECESSION/VIX>30 |
| `regime_multiplier` | 0.0 | **Always 0** — this module only scales Kelly Target, never contributes to the score |

### To adjust weights

In `.env`:

```
SIGNAL_WEIGHTS={"macro_regime": 50.0, "edge_garch": 40.0, "graham_value": 20.0, ...}
```

You must include all modules in the dict (or it falls back to the defaults). The score is the sum of `(module_score × weight)` across all active modules — modules suppressed by `is_active_in_regime()` contribute nothing that cycle.

---

## 18. Google Sheets Integration (Legacy)

`main.py` writes results to a Google Sheet. This is the original workflow, still functional.

### Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → Enable "Google Sheets API" and "Google Drive API"
3. Create a Service Account → download the JSON key → save as `credentials.json` in the project root
4. Share your Google Sheet with the service account email (ending in `@...gserviceaccount.com`) as Editor

### Sheet structure expected

- Tab named **"Sheet2"**: Column A = ticker symbols (one per row). Blank cells and any cell starting with `#` are ignored. **This tab is now wired as the last-resort universe fallback** — `main.py` reads it via `_load_tickers_from_sheet2()` only when Robinhood positions AND `WATCHLIST`/`watchlist.txt` are all empty (see [Section 4](#4-choosing-your-ticker-universe)). It is read defensively: a missing `credentials.json`, missing tab, or any API error degrades silently to "no fallback tickers", never a crash.
- Tab named **"FidelityData_Automated"**: output destination (created/overwritten each run)
- Tab named **"Transactions"**: optional, for realized slippage calculation

### Run

```bash
python3 main.py
```

---

## 19. Running Tests

```bash
# Run everything
pytest

# Run a specific file
pytest tests/test_quantitative_models.py

# Run a specific test
pytest tests/test_quantitative_models.py::test_graham_number_imaginary_bounds

# Run with verbose output
pytest -v

# Run and stop at first failure
pytest -x
```

### Key test categories

| Test file | What it covers |
|-----------|---------------|
| `tests/test_quantitative_models.py` | Core math: Graham Number, RSI, Kelly, GARCH |
| `tests/test_indicators_lookahead.py` | Lookahead bias checks for all technical indicators |
| `tests/test_risk_gate.py` | All 10 pre-trade risk gate checks |
| `tests/test_kill_switch.py` | Kill switch lifecycle |
| `tests/test_alerts.py` | Alert channel dispatch (Discord, Slack, email, file) |
| `tests/test_preflight.py` | All 11 preflight checks |
| `tests/test_hmm_synthetic.py` | HMM regime detector accuracy |
| `tests/test_kelly.py` | Kelly sizing formula and fallback |
| `tests/test_multifactor.py` | Fama-French multifactor signal |
| `tests/test_validation_rsi2.py` | RSI(2) strategy backtest (real SPY data, 2000–2023) |

### Tests that require network access

`tests/test_alpaca_paper_smoke.py` requires real Alpaca credentials and hits the paper endpoint. It is automatically skipped if credentials are absent. All other tests are offline.

---

## 20. Troubleshooting Common Problems

### "FRED_API_KEY is not configured"

Set `FRED_API_KEY=your_key` in `.env`. Get a free key at fred.stlouisfed.org.

### "credentials.json not found. Operating with deterministic MockDataEngine."

This is expected if you haven't set up Google Sheets. The pipeline still runs normally using synthetic data for testing. If you want live data without Sheets, this warning appears but is harmless — the platform uses `DataEngine` (real Yahoo Finance + FRED) not MockDataEngine when `credentials.json` is absent but `FRED_API_KEY` is set. The warning is printed regardless of data mode.

### Pipeline runs but Kelly Target is always the same value

The Kelly formula needs trade history. Check how many closed trades are in the database:

```bash
sqlite3 quant_platform.db "SELECT COUNT(*) FROM trades WHERE exit_price IS NOT NULL;"
```

If < 30, you're in the vol-target fallback. The system will log: `"Insufficient trade history for Kelly sizing — falling back to vol-target"`.

### "heartbeat_fresh" preflight check failing

The orchestrator hasn't run recently enough. Run it once:

```bash
python3 main_orchestrator.py --dry-run
```

This generates `output/heartbeat.txt`. The check passes if that file is < 2 hours old.

### Orders are being blocked by risk gate

Check the block log:

```bash
tail -20 output/risk_gate_blocks.jsonl | python3 -m json.tool
```

Each entry shows which check blocked the order and why. Common causes:
- `market_hours` — order attempted outside 9:30–16:00 ET
- `max_correlation` — new position too correlated with existing one
- `daily_loss_limit` — account is down > 2% today
- `minimum_validation` — no deployable validation report exists (run the validation harness)

### HMM probability is always None

The HMM needs at least 100 aligned rows of SPY price + VIX + yield curve history. If FRED is down or the SPY fetch fails, `hmm_risk_on_probability` returns `None` and the platform falls back to rules-based regime only. Check logs for:

```
MacroEngine: HMM fit failed — [reason] — returning None
```

### "GJR-GARCH failed to converge ... Falling back to 20-day historical standard deviation"

**This is almost never a "not enough data yet" problem.** If the warning text contains a Python error like `got an unexpected keyword argument 'method'`, it is an **`arch` library API mismatch**, not a model failure — and it means *every* ticker is silently using the cruder 20-day historical-vol fallback instead of the real GJR-GARCH estimate, no matter how much price history you have.

The fix is already applied in `technical_options_engine.py`: `estimate_gjr_garch_volatility()` calls `model.fit(update_freq=0, disp='off')` with no `method=` kwarg (`arch ≥ 8.0` removed it; the default SLSQP optimizer converges fine). If you see this warning again after a dependency upgrade, check the `arch` version (`.venv/bin/python3 -c "import arch; print(arch.__version__)"`) and re-inspect the `fit()` signature — do not re-add `method=` or `options={"method": ...}`. Verify with:

```bash
.venv/bin/python3 -m pytest tests/test_quantitative_models.py -k garch -v
```

A *genuine* convergence failure (rare) names a numerical reason rather than a Python `TypeError`, and self-heals as more daily returns accumulate.

### "python-dotenv could not parse statement starting at line 1"

The first line of your `.env` is a free-text comment without a leading `#`. python-dotenv treats any non-`KEY=VALUE`, non-`#`, non-blank line as unparseable and warns (harmlessly). Prefix the line with `#`. To find any other offending lines:

```bash
grep -nP "^[^#=\s]" .env   # lists lines that aren't comments, key=value, or blank
```

### Signal is HOLD even though the score is high

Check if the macro kill switch is active: the pipeline forces BUY/STRONG BUY → HOLD when `killSwitch` fires. Also check if `USE_DUAL_MOMENTUM_OVERLAY=true` and the Dual Momentum allocator selected the safe asset (BIL) — this zeros out all Kelly Targets for SPY and VEU, which can cause HOLD behavior.

### "No validation summary JSON files found in reports/"

Run the validation harness at least once:

```bash
python -m validation.harness --strategy main_pipeline --start 2015-01-01 --end 2024-12-31
```

This creates `reports/main_pipeline_validation_summary.json`. The preflight check and risk gate's `minimum_validation` check both require this file to exist and be deployable.

---

*Last updated: 2026-06-25. Reflects: Sheet2 column-A universe fallback in `main.py`, the `.env`→`os.environ` `load_dotenv()` fix, and the `arch ≥ 8.0` GJR-GARCH `fit()` API fix.*

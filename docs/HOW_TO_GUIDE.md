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

1. **Fetches live data** — price history *and* company fundamentals from Yahoo Finance (fundamentals are computed from Yahoo's free financial statements — no paid data vendor required), macroeconomic indicators from FRED (Federal Reserve Economic Data)
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

This creates `quant_platform.db` (SQLite) with the correct schema for storing daily signals and execution logs. The file is **not** checked into git (it's per-machine runtime state, gitignored via `*.db`) — every fresh clone needs this step once. The `trades` table starts empty; the closed-trade population that powers Kelly sizing and the calibration tracker is reconstructed on demand from your Robinhood filled-order history by `data/robinhood_orders.py` (Tier 7) and accumulates live as advisory runs record trades.

### Step 4 — Verify your setup

```bash
python scripts/preflight_check.py
```

This runs 17 automated readiness checks. On a fresh setup you will see some failures (especially `heartbeat_fresh` and `paper_trading_duration`) — that is normal. See [Section 13](#13-preflight-check--are-you-ready-to-go-live) for what each check means.

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
| `FINNHUB_API_KEY` | _(none)_ | Used **only** by the `news_catalyst` signal (company news / earnings headlines). **Not** a fundamentals source — fundamentals are Yahoo statement-derived (free). Leave unset to run without the news-catalyst signal |
| `FUNDAMENTALS_SOURCE` | `yahoo` | Fundamentals backend: `yahoo` (statement-derived, default) or `yfinance_info` (raw `.info` fallback) |
| `FORECAST_USE_GARCH_SIGMA` | `true` | Use the GJR-GARCH(1,1) volatility (annualized, converted to daily via ÷√252) as the Monte Carlo sigma, so the MC confidence band widens in turbulent regimes and tightens in calm ones. `false` restores the naive historical-stdev sigma |
| `FORECAST_PROPHET_WEIGHT` | `0.25` | Weight `w` given to the Prophet 30-day forecast when blending it into the 30-day ensemble: `final = base*(1-w) + prophet*w`. `0.0` disables Prophet's influence on the blend (Prophet must also be installed to have any effect) |

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

### The recommended way — the unified desktop app

`launch_app.command` at the project root is the recommended everyday way to start the
platform. Double-click it from **Finder** or the **Dock** and a single native desktop
window opens with the full Command Center inside it — no browser tab, no separate
terminal window to babysit.

**This is a behavior change from the old model, and it's worth being explicit about:**
the background refresh loop that keeps prices, indicators, and signals current now runs
**automatically for as long as the window stays open**, and **stops the moment you close
the window** — you don't start or stop it separately, and there's nothing left running
in the background after you quit. (The old model — `launch.command`'s headless interval
loop and `launch_gui.command`'s browser-tab GUI — required you to separately manage a
terminal loop and a browser tab, and either could keep running after you thought you'd
closed things down.)

A small freshness indicator in the sidebar shows how recently the background loop last
refreshed, so you always know at a glance whether what you're looking at is current.

Under the hood, `launch_app.command` runs:

```bash
python3 app_shell.py [--interval N]
```

`app_shell.py` opens `gui/app.py` in a native window (via `pywebview`) and supervises the
background refresh loop tied to that window's lifecycle. Everything described below for
the browser-tab Command Center — the eighteen tabs, the Launcher, Settings, Strategy
Matrix, and so on — is the same GUI, just hosted in a native window instead of your
browser.

**One-time setup** (already done — listed here for reference if you ever recreate the file):

```bash
chmod +x launch_app.command
```

**To add to the Dock**: drag `launch_app.command` to your Dock → right-click → Options → Keep in Dock.

---

### The headless way — double-click on macOS

Prefer a terminal-only, no-GUI loop, or need something scriptable for a scheduled task?
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

### The Command Center — visual control panel

The **Command Center** is a graphical front-end over the same pipeline, ideal if you prefer clicking to typing. The recommended way to open it is `launch_app.command` (see above), which hosts it in a native desktop window. If you'd rather have it in a browser tab instead (e.g. for headless/dev use), double-click **`launch_gui.command`** (macOS) or run:

```bash
streamlit run gui/app.py
```

Either way it's the same GUI, with eighteen tabs:

1. **🚀 Launcher** — **two** launch buttons: **▶️ Launch Pipeline** runs `main_orchestrator.py` (async, broker, full HTML report); **🔄 Refresh Data (Advisory)** runs `main.py` (synchronous advisory loop, broker-free — the canonical `.env`-loading entry point). Live stage indicators (Data Acquisition → Processing → Forecasting → Execution) for the orchestrator path, a **live 0–100% pipeline-progress bar** (see below), a heartbeat freshness gauge, and **two log expanders** — the active run log (`output/gui_run.log` or `output/gui_advisory.log`) plus the platform-wide structured telemetry stream from `alerting.setup_logging()` (`logs/investyo.log`). A **pre-launch env-readiness check** flags missing required variables (e.g. `FRED_API_KEY`) *before* you click, so a degraded run is diagnosed up front rather than after the fact. Optional **Dry run**, **Refresh Robinhood account**, and **Auto-refresh while running** (5 s ticker) toggles. Also surfaces the Robinhood execution-bridge mode banner (see [Robinhood Execution Bridge](#robinhood-execution-bridge)).
2. **📈 Reports** — portfolio heat, edge/MFE/MAE on the latest signals, one-click download of the generated HTML report / signals CSV, and a full **Brinson-Fachler Attribution Analysis** section. Edit the GICS-11 sector matrix directly (`st.data_editor`) or **bulk-paste TSV/CSV** from a spreadsheet, then click *Compute attribution* to see allocation / selection / interaction effects (top-line metrics + per-sector breakdown + bar chart, with CSV downloads for the editor input and the breakdown).
3. **⚙️ Settings** — edit **non-secret** tunables (`RISK_FREE_RATE`, `KELLY_FRACTION`, `DEFAULT_TICKERS`, thresholds, …) and save them to `.env`. **Secrets (API keys, passwords, TOTP) are shown masked and are read-only here** — edit those directly in `.env`. Changes take effect on the **next** launch.
4. **🧩 Strategy Matrix** — enable/disable individual signal modules (writes `DISABLED_SIGNAL_MODULES`), adjust their weights (writes `SIGNAL_WEIGHTS`), and manually activate/deactivate the **Macro Kill Switch**.
5. **📒 Paper Monitor** — your Robinhood account snapshot (account state only) side-by-side with the pipeline's market-data projection, reconciled by ticker.
6. **🛡️ Gravity Audit** — runs the Gravity AI Review Suite and shows pass/fail per step; review this before authorizing a live run.
7. **🧮 Options** — Black-Scholes Greeks and an IV-Rank proxy per active symbol.
8. **🛰️ Market Data** — which provider is active (Alpaca real-time vs. yfinance delayed), quote freshness, and a cache-reset control.
9. **📊 Observability** — Mission Control: macro-regime / VIX / HMM summary, account holdings & P&L, open positions vs. pipeline signals, portfolio heat/gross/net exposure, validation report status, recent closed trades, an equity-curve/drawdown/regime-overlay chart, the risk gate block log, plus heartbeat trend, system telemetry, latency heatmap, and error log — the single observability surface for the platform (the former standalone `streamlit run observability/dashboard.py` app has been retired; see [§12 The Observability Dashboard](#12-the-observability-dashboard)).
10. **📡 Live Inventory** — the full Task 1.4 sync view: holdings ∪ every Robinhood watchlist ∪ file-backed watchlists, each symbol's `CoverageStatus` (FULL / QUOTES_ONLY / EQUITY_ONLY / UNCOVERED), cost-basis delta, and forecast-availability flag. **🔄 Sync Now** refreshes the universe and persists it as `DEFAULT_TICKERS` in `.env`.
11. **❓ Help** — the in-app glossary and per-tab/per-metric explainer tooltips plus the first-run onboarding tour; see [In-App Help & Glossary](#in-app-help--glossary).
12. **📝 Prompts** — the Remote-Updatable Prompt Registry: view/publish versioned prompt text, verify signatures, and roll back; see [§16 Remote Prompt Updates (Prompt Registry)](#16-remote-prompt-updates-prompt-registry).
13. **🪄 AI Insights** — Opal research brief, Claude analyst note, Gemini chart-pattern read, and an aggregate Claude-vs-Gemini disagreement view, all operator-triggered per symbol; see [AI Insights & AI Control Center](#ai-insights--ai-control-center).
14. **🎛️ AI Control Center** — one place to toggle every AI capability (Claude commentary, Gemini alerts/vision, Gravity AI runner, Opal research), run each on demand, and start/stop a recurring pipeline run; see [AI Insights & AI Control Center](#ai-insights--ai-control-center).
15. **📊 Analytics** — read-only backend analytics that previously reached no GUI: broker realized performance (win rate / profit factor / realized P&L reconstructed from Robinhood order history), the account-value equity curve, a recent-alerts feed, the ML model registry, per-symbol news sentiment, and realized slippage + CoVaR.
16. **🔗 Pairs** — an **advisory-only** view over the pairs-trading engine: *Scan* ranks cointegrated candidate pairs (p-value + half-life), *Analyze* shows the live Kalman hedge ratio, spread z-score, rolling-ADF p-value, and the current entry/exit/stop label. Displayed, never traded.
17. **📁 Report Library** — an inline-viewable browser over every generated report; see [Report Library](#17-report-library).
18. **🔬 Validation Lab** — run the strategy-validation harness on demand per strategy and view the pass/fail results (PBO / DSR / Sharpe / MaxDD against the deployability gates); see [Validation Lab](#18-validation-lab).

The Command Center is **read-only and file-backed**: it never talks to the broker directly — it launches the orchestrator and reads the files the orchestrator writes, so it stays usable even when the broker API is down. One-time setup: `chmod +x launch_gui.command`.

#### Live pipeline-progress bar (Launcher tab)

While a run is in flight the Launcher tab shows a **live 0–100% progress bar** with the current stage name and percent complete (e.g. *"Forecasting — 62%"*). It is fully file-backed, just like the rest of the Command Center: the orchestrator's `reporting/progress.py` reporter writes `output/progress.json` (atomic write-then-rename) as it advances through stages and per-symbol work, and the Launcher polls that file every `settings.PROGRESS_POLL_SECONDS` (default **5 s**). Before the orchestrator's first stage is reached — or when no run is active — the bar is indeterminate or hidden rather than showing a fabricated percentage. Nothing extra is needed to enable it; a missing or malformed `progress.json` simply degrades to no bar.

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

Two entry points write a daily report via the same renderer
(`diagnostics_and_visuals.generate_html_report`):

- `python3 main.py` → `output/daily_report.html` (advisory path — the holdings-aware report below)
- `python3 main_orchestrator.py` → `output/daily_report_dashboard.html` (wide pipeline schema)

Open either in any browser. The advisory report (`daily_report.html`)
**leads with Holdings & P&L and Action & Rationale**:

- **Portfolio summary band** (top): total equity, buying power, aggregate
  unrealized P&L (green/red), dividends received, position count, and a
  BUY/HOLD/SELL tally. Sourced from your Robinhood account snapshot
  (`cache/account_snapshot.json`); shows an "ACCOUNT DATA STALE" pill when the
  snapshot is older than 24 h. Hidden when no account data is available.
- **Δ Since Last Run band**: at the very top of the report, immediately under
  the portfolio summary band, the report now shows what changed compared to
  the previous run — **new BUYs**, **action flips** (e.g. `JNJ: BUY → HOLD`),
  **conviction moves** with `|Δ| ≥ 0.20` (tunable via
  `SNAPSHOT_CONVICTION_DELTA_THRESHOLD` in `.env`), **holdings added/dropped**,
  and **regime changes** (e.g. `RISK ON → RECESSION`). On the very first run
  every BUY is treated as "new" and every held symbol as "added"; on subsequent
  runs only material changes appear. The band is hidden entirely when no prior
  snapshot exists or rotation failed (it never blocks the report). Powered by
  rotated state snapshots in `output/history/state_snapshot_<UTC>.json`
  (pruned after `SNAPSHOT_HISTORY_DAYS=30` days). Inspect any run manually:
  `python -m scripts.snapshot_diff` (markdown) or
  `python -m scripts.snapshot_diff --format json output/history/old.json output/history/new.json`.
- **Macro regime + portfolio-heat cards** and a BUY/HOLD/SELL doughnut.
- **Holdings, Action Signals & Rationale table**: per symbol — shares, average
  cost, current price, market value, signed unrealized P&L ($ and %), suggested
  position size, and the 30-day forecast. The action signal is colour-coded
  with a conviction meter. **Click any row** to expand the plain-English
  rationale plus strategy, RSI, GARCH vol, drawdown and data-quality detail.
- **Search box + sortable columns**: type to filter by symbol/action/rationale;
  click a column header to sort. (No page reload, no external JS libraries.)
- **Gravity AI Audit Log tab**: raw JSON findings from the verification suite.
- **Reports tab — Decision Journal**: log whether you acted on, passed, or modified each advisory signal (see [Manual Execution Journal](#manual-execution-journal-reports-tab) below).
- **Reports tab — Conviction Calibration**: reliability diagram showing whether the conviction scores match actual win rates (see [Conviction Calibration](#conviction-calibration-reports-tab) below).

Non-held watchlist symbols render "—" in the holdings columns (positions are
never fabricated). The report contains no credentials.

### Interactive volatility chart

`output/volatility_bands_dashboard.html` — Plotly chart of the first ticker's price history with volatility bands overlaid. Open in a browser.

### State snapshot (for the dashboard)

`output/state_snapshot.json` — machine-readable summary consumed by the Streamlit observability dashboard. Updated every pipeline run. A timestamped copy is ALSO written to `output/history/state_snapshot_<UTC>.json` and pruned after `SNAPSHOT_HISTORY_DAYS` (default 30); the daily HTML report's "Δ Since Last Run" band reads the two most recent rotated copies via `scripts/snapshot_diff.py`.

### Manual Execution Journal (Reports tab)

The **Decision Journal** section in the Reports tab lets you log what you did with each advisory signal — useful for post-hoc analysis and for teaching the calibration tracker which signals you actually endorsed.

**How it works:**

1. Open the Reports tab and scroll to "Signal Decision Journal".
2. Select the symbol from the dropdown (pre-populated with the last pipeline signals).
3. The context strip shows the system recommendation, conviction score, and current price.
4. Add optional notes, then click one of:
   - **✅ Acted** — you executed (or are executing) the suggested action.
   - **⏭ Passed** — you saw the signal but chose not to act.
   - **🔁 Modified** — you acted differently from the system (enter notes explaining the change).
5. The entry is appended to `output/decision_log.jsonl` (JSON-Lines, one entry per line).

For **"Acted"** entries only, the journal automatically looks up the nearest matching trade in `quant_platform.db` within ±24 h and records the `trade_id`. This allows the conviction calibration chart to filter to "decisions the operator actually endorsed."

**Log file location:** `output/decision_log.jsonl` — append-only, never read by the signal pipeline. The Past Decisions expander shows the last 20 entries with a CSV download button.

### Conviction Calibration (Reports tab)

The **Conviction Calibration** section in the Reports tab renders a reliability diagram — comparing the system's stated conviction score against the actual empirical win rate per conviction bin.

- X-axis: conviction bin (0–1, split into 10 equal bins by default).
- Y-axis: actual win rate for closed trades in that bin.
- Diagonal line: "perfect calibration" (conviction 0.8 → 80% actual win rate).

Bars above the diagonal → conviction underestimates actual skill in that range.
Bars below the diagonal → conviction is overconfident in that range.

**Important:** win rates are only shown for bins with ≥ 5 trades (configurable). Closed trades reconstructed from your Robinhood order history (via `data/robinhood_orders.py`) have no conviction scores, so the chart starts empty and fills in as `record_trade(conviction=...)` calls accumulate from live advisory runs.

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

The database ships with an empty `trades` table, so sizing starts on the vol-target fallback path. Once at least 30 closed trades accumulate — reconstructed from your Robinhood filled-order history via `data/robinhood_orders.py`, or recorded live by advisory runs — `_calculate_kelly_sizing()` switches to the real fractional-Kelly path automatically.

**If you have fewer than 30 trades for a strategy:**
Falls back to volatility targeting:
```
weight = VOL_TARGET / realized_vol
```
Where `VOL_TARGET` = 0.10 (10%). A stock with 20% annualized vol gets a 50% weight; a stock with 40% vol gets a 25% weight.

**Both paths are clamped** to `MAX_POSITION_WEIGHT` = 1.0 (100% max single name). In practice the Kelly cap (20%) and the HMM regime multiplier keep actual targets much lower.

**On a database-backend outage** (e.g. an unreachable Postgres/Supabase host), sizing does **not** fail. The platform substitutes a read-only offline transactions store that reports zero closed trades — the same cold-start shape as an empty `trades` table — so `_calculate_kelly_sizing()` transparently degrades to the volatility-target fallback for the cycle instead of dead-lettering the symbol. Advisory recommendations keep flowing; only the Kelly refinement is temporarily unavailable until the backend recovers.

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
- `reports/<strategy_name>_validation_summary.json` — machine-readable CURRENT-run snapshot, overwritten every harness run (consumed by preflight check)
- `reports/<strategy_name>_validation_report.html` — human-readable with Plotly charts
- `reports/history/<strategy_name>_validation_history.jsonl` — append-only, one row per historical run (capped at `MAX_VALIDATION_HISTORY_ROWS`), so PBO/DSR/Sharpe/MaxDD can be plotted as a trend across runs (read via `validation.harness.read_validation_history`; rendered in the GUI's Gravity Audit / Safety tab under "Validation trend across runs")

### Walk-forward stability

The harness also runs walk-forward analysis (rolling train/test splits) and reports how stable the Sharpe ratio is across time. A strategy that shows 1.5 Sharpe in-sample but 0.2 Sharpe out-of-sample is overfit.

---

## 11. Paper Trading Workflow

> **Advisory mode is the project default (`ADVISORY_ONLY=true`).** In this mode no orders
> are submitted to any broker — the pipeline is purely informational. This section
> documents the paper-trading workflow that applies once you have explicitly set
> `ADVISORY_ONLY=false`. See [Advisory-Only Mode](#advisory-only-mode) for the
> procedure and implications.

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

Open the Command Center (`launch_app.command`, or `launch_gui.command` / `streamlit run gui/app.py`) and go to the **📊 Observability** tab for live P&L, open positions, kill switch status, and the last 100 risk gate blocks — see [§12 The Observability Dashboard](#12-the-observability-dashboard) for the full panel breakdown (now folded into this tab; the standalone `streamlit run observability/dashboard.py` app has been retired).
```bash
streamlit run gui/app.py
```

Opens the Command Center at `http://localhost:8501` — open the **📊 Observability** tab for live P&L, open positions, kill switch status, and the last 100 risk gate blocks (see [§12](#12-the-observability-dashboard)).

### Minimum paper trading period

The preflight check requires **90 days** of continuous paper trading before going live. This is enforced via `PAPER_TRADING_START_DATE` in your `.env`.

---

## 12. The Observability Dashboard

> **The standalone `streamlit run observability/dashboard.py` app has been retired.**
> Everything described in this section now lives in the Command Center's **📊 Observability**
> tab — open it via `launch_app.command` (recommended; native desktop window, always-on
> background refresh) or `launch_gui.command` / `streamlit run gui/app.py` (browser tab).
> There is no longer a second app to separately launch or keep running.

Inside the Command Center, the Observability tab auto-refreshes alongside the rest of
the GUI. When running under `launch_app.command`, the always-on background refresh loop
keeps this tab's data current for as long as the window stays open; when running the
browser-tab GUI, use the tab's own refresh control to force an immediate update without
waiting for the next auto-refresh.
```bash
streamlit run gui/app.py
```

Open the **📊 Observability** tab. This tab is the platform's single
observability surface — the former standalone `streamlit run
observability/dashboard.py` app has been retired and every panel it used to
render now lives here.

Panels refresh whenever the tab re-renders (Streamlit's normal script rerun,
e.g. on interaction or a manual page reload); the underlying `state_snapshot.json`
read is additionally keyed on the file's mtime so a fresh orchestrator/advisory
run is picked up on the very next render rather than after a fixed TTL.

### What you'll see

| Panel | Data source | What it shows |
|-------|-------------|--------------|
| Kill switch banner | `output/KILL_SWITCH` file | Red = active (all orders blocked), Green = inactive |
| Macro regime / VIX / HMM | `output/state_snapshot.json` | Current regime, VIX, HMM risk-on probability |
| Macro Regime Gate | `.env` (`MACRO_REGIME_GATE_ENABLED`) | Toggle + live Sahm Rule / HY OAS / yield-curve telemetry |
| **Account Holdings & P&L** | **`cache/account_snapshot.json`** | **Total equity, buying power, unrealized P&L, dividends, and a per-position table with green/red-coloured unrealized P&L. Falls back to a "run `main.py --refresh-account`" note when no snapshot exists.** |
| Strategy P&L | `quant_platform.db` | Realized P&L by strategy |
| Open positions | `quant_platform.db` vs signals | Internal book vs pipeline recommendations |
| Portfolio risk metrics | `quant_platform.db` | Portfolio heat, gross exposure, net exposure |
| Validation status | `reports/*_validation_summary.json` | Deployable / not deployable per strategy (run-over-run trend lives in the Gravity Audit tab) |
| Recent closed trades | `quant_platform.db` | Last 20 fills |
| Equity curve & regime overlay | `quant_platform.db` + `output/history/` | Cumulative realized P&L, drawdown, and macro regime over time |
| Risk gate block log | `output/risk_gate_blocks.jsonl` | Last 100 blocked orders and which check blocked them |
| Heartbeat trend, system telemetry, latency heatmap, error log | `output/heartbeat.txt`, host/process metrics, `logs/investyo.log` | Orchestrator liveness trend, CPU/memory/disk, per-symbol fetch latency, classified error log |

The Account Holdings panel reads the same Robinhood snapshot the advisory
report uses — it is the source of truth for account state (holdings, cost
basis, dividends, equity) and never contains credentials.

### Staleness warning

If the orchestrator hasn't run for > 2 hours (detected via `output/heartbeat.txt`), the Observability tab shows a yellow staleness warning. This means no fresh signals are available.
If the orchestrator hasn't run for > 2 hours (detected via `output/heartbeat.txt`), the Heartbeat Age Trend panel shows a stale/slow status badge. This means no fresh signals are available.

---

## 13. Preflight Check — Are You Ready to Go Live?

```bash
python scripts/preflight_check.py
```

Runs 17 checks total. Behaviour depends on `ADVISORY_ONLY`:

* **`ADVISORY_ONLY=true` (default)**: eight checks are automatically skipped (shown
  as PASS with a per-check advisory-mode note): four broker-stack checks
  (`alpaca_configured`, `alpaca_paper_mode`, `dry_run_disabled`,
  `paper_trading_duration`), one key-rotation check (`alpaca_key_rotation_recent` —
  Alpaca keys have no blast-radius risk while the broker surface is quarantined), and
  three runtime-state checks that are false-positives for advisory runs
  (`heartbeat_fresh`, `validation_reports`, `no_unexpected_risk_blocks`).
  `advisory_only_active` always passes loudly, and `robinhood_execution_mode` /
  `state_snapshot_fresh` are **never** auto-skipped (see below — they're the
  advisory-relevant liveness/safety checks). Exit 0 when the remaining checks pass.
* **`ADVISORY_ONLY=false`**: all 17 checks run. Exit 0 only when ALL pass (required
  before going live).

| Check | Advisory skip? | Passes when | How to fix a failure |
|-------|:--------------:|------------|---------------------|
| `fred_key_configured` | No | `FRED_API_KEY` is set | Add key to `.env` |
| `key_rotation_recent` | No | `FRED_KEY_ROTATED_DATE` set and within 90 days — warning only, never blocking | Set `FRED_KEY_ROTATED_DATE=YYYY-MM-DD` in `.env` when you rotate |
| `alpaca_key_rotation_recent` | **Yes** | `ALPACA_KEY_ROTATED_DATE` set and within 90 days — warning only, never blocking | Set `ALPACA_KEY_ROTATED_DATE=YYYY-MM-DD` in `.env` when you rotate |
| `advisory_only_active` | No | Always — PASS-loud when `true`, PASS-with-warning when `false` | Set `ADVISORY_ONLY=true` to return to advisory mode |
| `robinhood_execution_mode` | No | `ROBINHOOD_EXECUTION_MODE` is `off`/`review` (always passes), or `live` with a positive `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` — independent of `ADVISORY_ONLY` since the Robinhood bridge is orthogonal to the Alpaca quarantine | Set `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` to a per-order dollar cap before setting `ROBINHOOD_EXECUTION_MODE=live` |
| `alpaca_configured` | **Yes** | Both Alpaca keys are set | Add keys to `.env` |
| `macro_regime_gate_enabled` | No | `MACRO_REGIME_GATE_ENABLED=true` (blocks in live mode when off) | Set `MACRO_REGIME_GATE_ENABLED=true` in `.env` |
| `alpaca_paper_mode` | **Yes** | `ALPACA_PAPER=true` — warning only | Change to `false` only when ready to go live |
| `dry_run_disabled` | **Yes** | `DRY_RUN=false` | Set `DRY_RUN=false` in `.env` |
| `env_not_committed` | No | `.env` is not tracked by git | Add `.env` to `.gitignore` (already done in this repo) |
| `kill_switch_inactive` | No | No `output/KILL_SWITCH` file exists | Run `python -m execution.kill_switch --deactivate` |
| `state_snapshot_fresh` | No | `output/state_snapshot.json` is < 2 hours old (written by BOTH `main.py` and `main_orchestrator.py` — the cross-mode liveness indicator, so never skipped even in advisory mode) | Run `python3 main.py` or `python3 main_orchestrator.py` to regenerate it |
| `heartbeat_fresh` | **Yes** | `output/heartbeat.txt` is < 2 hours old (written by `main_orchestrator.py` only) | Run `python3 main_orchestrator.py` to generate it |
| `db_exists` | No | `quant_platform.db` exists and is non-empty | Run `python3 database_setup.py` |
| `paper_trading_duration` | **Yes** | ≥ 90 days since `PAPER_TRADING_START_DATE` | Wait — this is intentional; set your start date when you begin |
| `validation_reports` | **Yes** | At least one report exists, deployable, and < 30 days old | Run `python -m validation.harness --strategy main_pipeline --start 2015-01-01 --end 2024-12-31` |
| `no_unexpected_risk_blocks` | **Yes** | No `minimum_validation` blocks in last 24 h | Generate a validation report — the minimum_validation risk gate is blocking because no deployable reports exist |

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

## 15. The Kill Switch / Pause Gate

In **advisory mode** (`ADVISORY_ONLY=true`) the kill switch sentinel (`output/KILL_SWITCH`)
repurposes as a **pause-recommendations gate**: when the file exists, `main.run_once()`
logs `"Advisory paused by kill-switch sentinel — skipping evaluation cycle"` and returns
an empty RunResult for that cycle.  `main_orchestrator._main_body()` returns immediately
before `run_pipeline()` so the last written `state_snapshot.json` and HTML report are
preserved.  No broker interaction exists to halt — this is purely a signal-generation pause.

In **live-execution mode** (`ADVISORY_ONLY=false`) the sentinel also causes `OrderManager`
to raise `KillSwitchActiveError` before any order reaches the broker.

### Check status

```bash
python -m execution.kill_switch --status
```

### Activate (pause advisory / block live orders)

```bash
python -m execution.kill_switch --activate --reason "investigating anomaly"
```

In advisory mode: next pipeline run skips evaluation and logs the pause reason.
In live mode: `OrderManager` raises `KillSwitchActiveError` before any order. The pipeline
continues to run and produce signals — only order submission is blocked.

The Launcher tab → Safety Controls also exposes a toggle button.

### Deactivate (resume)

```bash
python -m execution.kill_switch --deactivate
```

### How it works

The kill switch is a file: `output/KILL_SWITCH`. Its presence = active. The platform
checks for file existence on every evaluation or order attempt — no database, no network
call, no race condition. To activate from code:

```python
from execution.kill_switch import GlobalKillSwitch
ks = GlobalKillSwitch()
ks.activate("VIX spiked above 45")
```

### Automatic kill switch (live mode only)

In live-execution mode, the platform auto-fires the kill switch when the macro regime
becomes extreme. You don't need to trigger this manually — it fires when:

- `vix > 30` AND `sahm_rule >= 0.5` (base condition), OR
- The regime is RECESSION AND HMM agrees risk-off > 70% AND `vix > 25` OR `sahm >= 0.3`
  (faster trigger with HMM agreement)

In advisory mode, this auto-fire has no practical effect (no orders to block) but the
sentinel is still written so the GUI pause indicator activates and the operator is alerted.

### Macro-triggered advisory gating (independent of the kill switch)

Even when the kill switch is **not** active, the advisory engine applies conservative
overrides when macro conditions deteriorate.  These are applied per-symbol inside
`engine/advisory.evaluate()` before the holding-aware overlay:

| Condition | Effect on advisory signal |
|---|---|
| `market_regime = RECESSION` or `CREDIT EVENT` | Hard gate: all BUY / STRONG BUY → HOLD |
| `VIX > 30` OR `Sahm Rule ≥ 0.5` | Soft gate: composite score penalised by 25 pts |
| Finance / Financial Services / Real Estate sector AND yield curve inverted (`< 0`) OR HY OAS > 6% | Sector veto: BUY → HOLD for structurally exposed sectors |

When a gate fires, the advisory rationale explains the override (e.g. "Macro regime is
RECESSION: systemic risk gate halts fresh equity allocations").  Existing holders may
still receive a SELL from the loss-cut rule even when a macro gate is active — the gate
only suppresses *new* BUY allocations.

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
| `tests/test_preflight.py` | All 17 preflight checks |
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

*Last updated: 2026-07-10. Reflects: Tier 5.3 kill-switch pause gate wired into `main.run_once()` and `main_orchestrator._main_body()`, macro-triggered advisory gating (RECESSION hard gate, VIX/Sahm soft gate, sector veto) added to §15. Prior: Tier 5.1 `ADVISORY_ONLY=true` default (broker quarantine), advisory-mode preflight auto-skip (§13), Strategy Matrix mode toggle suppressed under advisory mode, new Advisory-Only Mode section, Sheet2 column-A universe fallback, `load_dotenv()` placement fix.*

## Safety tab (formerly Gravity Audit) — what to check when an order is blocked

The Safety tab now leads with two new sections before the Gravity audit launcher:

1. **🚧 Circuit Breaker Dashboard** — every active trip derived from
   `output/KILL_SWITCH` and the last 24 hours of `output/risk_gate_blocks.jsonl`.
   CRITICAL severity halts everything (kill switch, daily loss limit, portfolio
   heat, macro kill switch, minimum_validation gate); WARNING covers per-symbol
   blocks (max position size, max correlation, market hours, etc.). Click
   **🔬 Inspect raw trip payloads** to see the original JSON-line block — that's
   the source of truth.

2. **🕸️ Dependency Map** — pick the data source(s) that are degraded right
   now (Alpaca, Finnhub, FRED, Robinhood, etc.) and the panel lists every
   strategy/tab/report that loses coverage. Useful both as documentation
   (read-only) and as triage during an outage. The map lives in
   `gui/dependency_map.py`; extend it there as new consumers come online.

When the orchestrator vetoes orders unexpectedly:
1. Open the Safety tab.
2. Look at the Circuit Breaker Dashboard for the most recent CRITICAL trip.
3. If it's the kill switch, check Strategy Matrix → Manual Kill Switch panel to
   deactivate (the sentinel file is `output/KILL_SWITCH`).
4. If it's a risk-gate block, read the `threshold` / `observed` columns and the
   raw payload — those tell you *which check* fired and *by how much*.

## Advisory-Only Mode

`settings.ADVISORY_ONLY=true` is the project default. It quarantines the entire broker-
execution surface so the pipeline can run safely without ever touching a live or paper
account.

### What changes when ADVISORY_ONLY=true

| Layer | Behaviour |
|-------|-----------|
| `main_orchestrator._execute_broker_orders` | Returns immediately with an INFO log — no broker imports reached |
| `gui/app.py` header | Shows `📋 ADVISORY MODE` banner instead of Simulation / Paper / Live badge |
| Strategy Matrix mode toggle | Suppressed — replaced by a read-only caption showing underlying flags |
| `scripts/preflight_check.py` | Eight broker/advisory-false-positive checks auto-skip; `advisory_only_active` = PASS-loud; `robinhood_execution_mode` and `state_snapshot_fresh` always run |
| Kill switch sentinel | Repurposes as a pause-recommendations gate (see §15) |

### Re-enabling broker execution

```bash
# 1. Set in .env
ADVISORY_ONLY=false
DRY_RUN=false
ALPACA_PAPER=true    # start with paper; change to false only for live

# 2. Verify preflight (all 17 checks must pass)
python scripts/preflight_check.py

# 3. Launch pipeline (paper mode)
python3 main_orchestrator.py
```

All three flags must be consistent: `ADVISORY_ONLY=false AND DRY_RUN=false AND
ALPACA_PAPER=false` is required to reach a live submission. The GUI mode toggle
reappears automatically once `ADVISORY_ONLY=false`.

---

## Strategy Matrix tab — Global Execution Mode toggle

> **Suppressed while `ADVISORY_ONLY=true`.** The toggle reappears automatically
> when `ADVISORY_ONLY=false`. See [Advisory-Only Mode](#advisory-only-mode) above.

The Strategy Matrix (Control) tab leads with a **🎚️ Global Execution Mode**
selector backed by `gui/strategy_registry.py`. Three modes:

| Mode | DRY_RUN | ALPACA_PAPER | What happens |
|---|---|---|---|
| 🧪 Simulation | true | true | OrderManager intercepts every intent before any broker contact. Safe default. |
| 📝 Paper | false | true | Orders route to the Alpaca paper sandbox. No real money. |
| 🔴 Live | false | false | Orders hit the live broker. Requires a **CONFIRM LIVE PRODUCTION** click. |

**Setting takes effect on the next orchestrator/advisory launch.** We never
mutate a running `settings.Settings`. The writer goes through the allowlist-
bounded `gui/env_io.write_setting` so the GUI cannot flip a half-state (only
ALPACA_PAPER without DRY_RUN, for example) — both flags are written together.

Below the mode selector is the **📜 Strategy Version Registry**: a table of
each registered signal module with its sha256 prefix (first 12 hex chars) and
file mtime. If you redeployed a strategy file but the version hash and mtime
haven't moved, the file did not actually change on disk — useful when
"obviously I deployed this" disagrees with the dashboard.

## Reports tab — Live vs Backtested provenance + drill-down

The Reports tab now opens with a colour-coded banner:

* **🔵 Live data** (blue `st.info`) — sourced from `output/state_snapshot.json`
  written by the most recent orchestrator/advisory run AND the active execution
  mode is Paper or Live.
* **⚪ Backtested / simulated** (grey Markdown blockquote) — either no snapshot
  exists yet, or `DRY_RUN=true` is active so every number is simulated.

Each MFE/MAE/Edge Ratio entry now has a **🔬 Drill down by symbol** expander:
pick a ticker → see the full signal row + recent closed trades for that symbol
from `transactions_store.TransactionsStore`. Use this to answer "why is this
score what it is" without exporting the CSV.

## Advisory-Only Mode (Tier 5.1, default-on)

The project ships with **`settings.ADVISORY_ONLY=true`** as the default. In this mode the platform runs the full quant pipeline — fetches data, computes indicators, runs forecasts, sizes positions, writes the HTML report and JSON payload — but **never submits orders to any broker**.

**What you will see:**
- GUI: a persistent blue `📋 ADVISORY MODE` banner above the tab bar; the Strategy Matrix `Global Execution Mode` toggle shows `📋 Advisory mode — broker execution disabled` instead of the Simulation/Paper/Live radio.
- Orchestrator: an INFO log line `"ADVISORY_ONLY=True — broker execution surface is quarantined; skipping all order submission, reconciliation, and broker imports."`
- Preflight: a new `advisory_only_active` row at position #2; the four broker-dependent rows (`alpaca_configured`, `alpaca_paper_mode`, `dry_run_disabled`, `paper_trading_duration`) show as PASS with reason `"(skipped: ADVISORY_ONLY=True — broker check not applicable)"`.

**To re-enable broker execution:** set `ADVISORY_ONLY=false` in `.env`, then restart the orchestrator. See §1 of `docs/RUNBOOK.md` for the paper→live switch checklist.

## Symbol Watch Alerts (Tier 1.4)

The platform can send proactive ntfy push notifications whenever a symbol's advisory action flips or conviction crosses a threshold — without you needing to poll the dashboard.

### Prerequisites

1. **ntfy app** — install on your phone from the App Store or Google Play (search "ntfy").
2. **NTFY_TOPIC** must already be set in `.env` (see §14 of this guide for the one-time setup). Watch alerts piggyback on the same topic; no second subscription is needed.

### How it works

At the end of every `run_once()` cycle, `watch_engine.py`:
1. Loads rules from `watch_rules.yaml` (project root).
2. Loads the previous run's state from `output/watch_state.json`.
3. Compares the current advisory output against the previous state.
4. Fires alerts for any matched rules.
5. Saves updated state atomically for the next run.

### Rule schema (`watch_rules.yaml`)

```yaml
rules:
  - symbol: "*"          # "*" = all symbols in the current universe
    alert_on: conviction_above
    threshold: 0.85      # [0.0 – 1.0]  required for conviction_above / conviction_below
    priority: high       # high | default | low  (maps to ntfy X-Priority header)
    label: "High conviction"   # optional free-text label in the notification

  - symbol: "AAPL"
    alert_on: action_change
    priority: default
```

**`alert_on` values:**
| Value | Fires when… |
|---|---|
| `action_change` | Action flips (e.g. HOLD→BUY, BUY→SELL). **Never fires on the very first run** (no prior state to compare). |
| `conviction_above` | Conviction rises to ≥ threshold for the first time. Stays silent while the condition persists. Resets when conviction drops back below threshold. |
| `conviction_below` | Mirror of `conviction_above` — fires on the first run where conviction falls below threshold. |

### Environment variables

| Variable | Default | Notes |
|---|---|---|
| `WATCH_RULES_FILE` | `watch_rules.yaml` | Path to the YAML rule file. Relative paths are resolved from the working directory where `main.py` is launched. |
| `NTFY_DASHBOARD_URL` | *(empty)* | Optional URL appended to every alert body (e.g. `http://localhost:8501`). Tap the notification to open the GUI directly. |

### Adding or editing rules

1. Open `watch_rules.yaml` in any text editor.
2. Add, remove, or modify `rules` entries.
3. The changes take effect on the **next** `run_once()` cycle — no restart needed.

### Resetting alert state

Conviction-above/below alerts use edge-triggering to avoid notification spam. If you want an alert to fire again immediately (e.g. after adjusting the threshold), delete `output/watch_state.json`. The next run treats all symbols as first-run and re-evaluates from scratch.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No alerts ever fire | `NTFY_TOPIC` is unset or empty | Set `NTFY_TOPIC=your-topic` in `.env` |
| Rule in YAML but no alert | `alert_on` value not recognised, or threshold out of `[0, 1]` | Check `logs/investyo.log` for a WARNING "Skipping rule…" line |
| `conviction_above` never re-fires | Edge suppression is working as intended | Delete `output/watch_state.json` to reset |
| Stale symbol keeps alerting | Symbol was removed from universe but `watch_state.json` still has its entry | Automatic: state is pruned to the current universe each run; wait one cycle |

---

## Verbose Advisory Rationale (Tier 1.5)

By default the per-symbol rationale is a single terse paragraph suitable for dashboards and phone notifications. Set `RATIONALE_VERBOSITY=verbose` in `.env` to unlock a four-section institutional-grade narrative.

### Prerequisites
- `.env` must exist (copy from `.env.example`).
- No additional dependencies.

### How it works
Every time `run_once()` evaluates a symbol it calls `engine.advisory.evaluate()`. When `RATIONALE_VERBOSITY=verbose`:

1. `evaluate()` pre-computes win-rate data from `TransactionsStore` (same database used by Kelly sizing).
2. It also pulls first-line `__doc__` strings from all signal modules that are active in the current macro regime.
3. Both data blobs are passed to `_build_rationale()` which appends four labelled sections after the standard paragraph.

### The four verbose sections

| Label | What it shows |
|---|---|
| **[A] Regime context** | HMM probability level and FRED macro snapshot (VIX, Sahm Rule, yield-curve spread) so you immediately understand whether macro filters are active or bypassed. |
| **[B] Calibration** | Strategy win-rate and Kelly edge estimate from closed trades, so conviction is grounded in a real track record rather than a single signal. Falls back gracefully when fewer than 30 trades exist. |
| **[C] Invalidation** | Explicit "flip points" that would void the current recommendation: RSI reversal levels, score breakdowns, VIX/Sahm macro gate tripwires, sector-veto conditions, and SMA-200 trend break. |
| **[D] Theory notes** | First-line docstring of each regime-active signal module, so an analyst can understand the theoretical basis without reading source code. |

### Enabling verbose mode

```bash
# In .env
RATIONALE_VERBOSITY=verbose
```

Then restart the platform (`.env` is loaded at entry-point startup):

```bash
python3 main.py         # advisory orchestrator (fastest refresh)
# or
python3 main_orchestrator.py  # full async pipeline
```

The `rationale` field in the HTML report and Google Sheet will now show the extended narrative.

### Switching back to standard mode

```bash
# In .env
RATIONALE_VERBOSITY=standard   # or just remove the line; 'standard' is the default
```

### Example verbose rationale

```
AAPL: Accumulate a new position. The multi-signal composite score is 72/100
(moderately bullish; regime: RISK ON); the 30-day blended forecast implies
5.0% upside (target $105.00 vs current $100.00); Aroon oscillator (72) indicates
a strong uptrend. (Raw strategy signal: BUY.)

[A] Regime context: RISK ON — HMM strongly confirms risk-on (p=0.82).
VIX=18.4, Sahm Rule=0.10, 10y-2y spread=+0.32.
[B] Calibration: This multi-signal setup has shown a 64% win rate over 84 closed
trades (payoff ratio 1.8:1; Kelly edge 0.45 — positive — edge exists).
[C] Invalidation: score drop below 35 converts signal to RISK REDUCE; RSI rising
above 35 (currently 22) voids the oversold entry; VIX > 30 or Sahm Rule ≥ 0.5
applies a −25pt macro penalty; close below SMA-200 ($95.00) invalidates the
uptrend filter.
[D] Indicator notes: Aroon Trend: Aroon Oscillator chop-filtering for trend
detection; Macd Momentum: MACD Bullish/Bearish crossover scoring; Timeseries
Momentum: Moskowitz/Ooi/Pedersen time-series momentum.
```

### Compliance and audit use

The `[A]–[D]` markers are stable labels — compliance reviewers can cite "section [C] invalidation thresholds" without parsing the full text. The verbose rationale is entirely data-driven; no new thresholds are hard-coded (all flip points reference the `CONFIG` dict in `engine/advisory.py`).

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No verbose sections even after setting `RATIONALE_VERBOSITY=verbose` | `.env` was not reloaded | Restart `main.py` / `main_orchestrator.py` — settings are loaded once at startup. |
| `[B]` shows "Insufficient closed-trade history" | Fewer than 30 closed trades in `quant_platform.db` | Expected on fresh installations. Run for several weeks to accumulate trade history. |
| `[D]` section absent | All signal modules filtered out by `is_active_in_regime()` (e.g. RECESSION regime) | Expected in extreme macro regimes where most signals are suppressed. Check the `[A]` section for the regime name. |

---

## Autonomous Advisory Agent

The autonomous agent (Tier 6) replaces `--interval N`'s fixed timer with a
self-pacing loop. It still only produces advisory output — it never places an
order — but it decides *when* to re-run and re-pings you about high-conviction
signals you have not acted on.

```bash
python3 main.py --agent          # takes precedence over --interval
```

### What it does each cycle

1. Runs one full advisory cycle (same as `--interval`): signals, sizing, HTML
   report, Sheets, and the per-cycle Symbol Watch alerts.
2. **Adaptive cadence** — picks the next sleep based on US market hours, VIX,
   macro regime, and recent errors: fast around the open/close and during
   volatility spikes, slow overnight/weekends, and it backs off after errors.
3. **Actionable backlog** — a high-conviction BUY/SELL you have not logged a
   decision for is re-pinged on escalating tiers (≈1 h / 4 h / 24 h) via ntfy,
   then stops once you act (a matching "acted" entry in the Decision Journal),
   the signal expires, or the reminder cap is reached.
4. **Persistent state** — the backlog and conviction history survive restarts
   in `output/agent_state.json`.

### Stopping it

Press Ctrl-C (or send SIGTERM). The loop finishes the current cycle, dispatches
any pending reminders, saves state, and exits cleanly.

### Configuration

`NTFY_TOPIC` enables push reminders (unset = silent). `NTFY_DASHBOARD_URL`
appends a one-click dashboard link to each push. All cadence/backlog thresholds
live in `engine.advisory_agent.CONFIG`.

---

## Trade-Signal Alerts

Two advisory trading abilities (Tier 6.1) layer on the autonomous agent. Both
are derived purely from data the agent already has each cycle (recommendations
+ your Robinhood snapshot) and both are pushed as ntfy alerts — no order code.

### Conviction momentum

The agent uniquely tracks each symbol's conviction *trajectory* across cycles:

- **Building** — conviction is climbing steadily but has not yet reached the
  backlog siren, so you get an *early* heads-up before the move matures.
- **Fading** — conviction is deteriorating on a name no longer rated BUY, an
  *early* exit warning.

Each trend pings once (debounced); it re-alerts only if the trend breaks and
re-forms, or flips direction.

### Stop / target proximity

For your held positions, the agent derives a volatility-scaled (ATR) stop below
your cost basis and a take-profit target from the 30-day forecast, then alerts
when the live price approaches (or breaches) either level — turning the agent
into a position-management assistant. Dust positions and rows with bad price
data are skipped (no fabricated levels).

All thresholds live in `engine.trade_signals.CONFIG`.

---

## Robinhood Execution Bridge

The Robinhood Execution Bridge (Tier 8) is the **opt-in, paper-first** path that
lets the platform act on its advisory output through the Robinhood Trading MCP.
It is **off by default** and independent of `ADVISORY_ONLY` (which governs the
separate Alpaca surface).

Because the MCP is consumed by a **Claude Code agent** — not the headless
Python pipeline — the platform only writes a gated, dry-run proposed-order queue
(`output/execution_queue.json`); a Claude Code agent (`/rh-execute`) is the only
actor that ever calls the MCP. *Python writes intents; the agent writes
outcomes* to `output/execution_receipts.jsonl`.

### One-time setup (local, interactive)

```bash
claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading
# then in Claude Code:  /mcp  → robinhood-trading → authenticate (OAuth)
```

Open and fund a dedicated Robinhood **Agentic account** with a small, capped
amount — agent orders only ever touch that account; your main account stays
read-only. Smoke-test the read tools (`get_accounts`, `get_portfolio`) before
enabling any placement.

### Execution modes

Set `ROBINHOOD_EXECUTION_MODE` in `.env`. Roll out strictly `off → review → live`.

| Mode | What happens |
|------|--------------|
| `off` (default) | Nothing is written. Zero behavior change. |
| `review` | The queue is emitted; `/rh-execute` only *simulates* via `review_equity_order` and stops. This is the paper/dry-run stage. |
| `live` | An intent may be placed **only** when the risk gate passed, the kill switch is clear, and a per-order notional cap is set — and `/rh-execute` still asks you to confirm each order individually. |

`ROBINHOOD_MAX_NOTIONAL_PER_ORDER` is a hard per-order dollar ceiling; `live`
requires it `> 0` or `preflight_check.py` fails.

### Running it

```bash
python3 main.py                  # writes output/execution_queue.json when mode != off
# then in Claude Code:
/rh-execute                      # previews every order; in live mode, places with confirmation
```

### Stopping placement immediately

The kill switch blocks all placement (checked when the queue is built and again
before each order):

```bash
python -m execution.kill_switch --activate --reason "halt robinhood execution"
```

Or set `ROBINHOOD_EXECUTION_MODE=off` so the next run emits nothing. Full
operating procedure: see `docs/RUNBOOK.md` → "Robinhood Live Execution
Procedure".

### Limit orders and idempotency

An intent may request a **limit order** rather than a market order: it carries
`order_type: "limit"` and a `limit_offset_bps` (the maximum slippage you'll
tolerate in basis points). When `/rh-execute` handles such an intent it resolves
the limit price from the *live* quote at review time — a BUY caps at
`quote × (1 + bps/10000)`, a SELL floors at `quote × (1 − bps/10000)` — so you
never chase a stale price. Market orders (the default) behave as before.

Placement is also **idempotent**: every filled order is recorded in an
append-only ledger (`output/execution_placed.jsonl`, keyed by
`date:symbol:side`), and before placing anything the skill checks whether that
same order was already placed today. If it was, it is skipped — so re-running the
queue after a partial session never double-fills.

### The Robinhood panel in the Command Center

The GUI Command Center (`streamlit run gui/app.py`) has a **Robinhood** panel
that gives you a single read-only view of the whole execution loop:

- **Queue** — the current `output/execution_queue.json`: each proposed intent,
  its mode, whether it is placeable (`allow_place`), and — for blocked intents —
  the gate reasons.
- **Receipts** — what the agent actually previewed, placed, and skipped, read
  from `output/execution_receipts.jsonl` and the placed-intent ledger.
- **Reconciliation** — `execution/receipts_store.py` matches those receipts and
  ledger entries against your account's *actual* Robinhood fills, so you can
  confirm every recorded placement has a real fill (and spot any drift). Check
  this panel after every live run.

The panel never places orders itself — placement always goes through the
`/rh-execute` skill with per-order confirmation. It is purely the operator's
window into the queue, the outcomes, and the reconciliation.

---

## In-App Help & Glossary

The **❓ Help tab** in the Command Center (`streamlit run gui/app.py`) gives instant access
to every concept in this guide without leaving the browser window.

### What you'll find

| Widget | Where | What it does |
|---|---|---|
| `❓ What is this & how do I use it?` expander | Top of every tab | Plain-English summary of the tab's purpose and controls |
| Metric tooltips | VIX, HMM Risk-On, Sahm Rule, Macro Regime KPI chips | Hover-over definition — never a bare number |
| Glossary | Help tab → search box | 60+ terms (Kelly Target, PBO, DSR, Sahm Rule, IVR, HMM, …) with plain-English definitions and "Read more →" links back to this guide |
| Section help expanders | Reports, Options, Live Inventory | Inline context for Brinson-Fachler, VRP gate, Coverage Status |

### First-run onboarding tour

On the **very first launch** of the Command Center the Help tab shows a 4-step
"Start here" checklist (and the Launcher tab's how-to expander opens automatically):

1. Set `FRED_API_KEY` in `.env` (free API key from FRED at `https://fred.stlouisfed.org/docs/api/api_key.html`).
2. Click **🔄 Refresh Data (Advisory)** in the Launcher tab.
3. Open the HTML report (`output/daily_report_*.html`).
4. Review the Conviction Calibration chart (Reports tab) once closed trades accumulate.

Click **✅ Got it — don't show again** to dismiss permanently. The tour writes a marker
file (`output/.gui_onboarded`). Delete that file to reset the tour.

### Help-key convention

Metric tooltips are looked up via keys of the form `"<tab>.<metric_name>"` in
`gui/help_content.METRIC_HELP`. A missing key returns `""` and renders no tooltip
— it **never raises** (CONSTRAINT #6). All operator-facing definitions live in
`gui/help_content.py`; never hard-code explainer prose directly in the `gui/panels/`
per-tab modules.

### Anchor-contract invariant

Every glossary entry's `guide_anchor` field **must** resolve to a real heading slug in
this file. The contract is enforced by:

- `tests/test_help_content.py::TestAnchorValidity` (runs in CI on every push).
- Gravity step 68 check 3.

If you rename any heading in this file, search for the old slug in `gui/help_content.py`
and update it to match the new slug; otherwise the anchor test will fail.

## §16 Remote Prompt Updates (Prompt Registry)

> **Security boundary (must never be overridden):** Fetched prompts are advisory text only.
> They can change what an AI is *told* — they cannot change what the platform is *permitted to do*.
> Order submission, advisory quarantine, risk gates, and the kill switch are enforced in Python code,
> not in any prompt. This invariant is verified on every Gravity audit run (step 69, check 7).

### What the registry is

`prompt_registry/` is a versioned, cryptographically-signed store for every AI-facing instruction
(master pre-prompt, Gravity step bodies, etc.).  Publishing a new version and moving the "latest"
pointer is the *only* over-the-internet update mechanism — it never touches Python modules, settings,
or the broker execution surface.

| File / path | Role |
|---|---|
| `prompt_registry/baseline/` | Git-committed fallback bodies (always available, no network) |
| `output/prompt_cache/` | Signed on-disk cache of fetched versions (rollback depth = 5 by default) |
| `prompt_registry/__main__.py` | CLI: `list`, `get`, `sync`, `pin`, `rollback`, `diff`, `verify`, `publish` |
| `gui/app.py` tab "📝 Prompts" | GUI: resolved version / source per ID, Sync, diff viewer, Rollback |

### Resolution order (CONSTRAINT #4 — never empty)

```
Pin (PROMPT_REGISTRY_PINS) → Remote latest (verified) → Disk cache (verified) → Baseline → sentinel
```

The sentinel body is a one-line placeholder; `get()` never returns `""`.

### Day-to-day operator workflow

#### Fetching the latest master pre-prompt to paste

```bash
python -m prompt_registry get master_preprompt
```

The body is printed to stdout.  Pipe to `pbcopy` on macOS to copy directly to the clipboard:

```bash
python -m prompt_registry get master_preprompt | pbcopy
```

#### Checking what version is resolved for every ID

```bash
python -m prompt_registry list
```

Output columns: `prompt_id`, `resolved_version`, `source` (pin / remote / cache / baseline).

#### Syncing all IDs from the remote manifest

```bash
python -m prompt_registry sync
```

Fetches the manifest, verifies HMAC-SHA256 signatures, writes to `output/prompt_cache/`.
Requires `PROMPT_REGISTRY_URL` and `PROMPT_REGISTRY_SIGNING_KEY` set in `.env`.
**CONSTRAINT #5 — never called on a timer.**  Sync is explicit (CLI or the GUI "🔄 Sync" button).

#### Pinning a specific version

```bash
python -m prompt_registry pin master_preprompt 1.1.0
```

Writes `PROMPT_REGISTRY_PINS={"master_preprompt": "1.1.0"}` to `.env` (via `gui/env_io`).
Effective on the **next** launch — never hot-swaps a running process.

#### Rolling back to the previous cached version

```bash
python -m prompt_registry rollback master_preprompt
```

Sets the pin to the second-newest entry in `output/prompt_cache/master_preprompt/`.
Fails gracefully (non-zero exit, clear message) when fewer than two versions are cached.

#### Verifying cache integrity

```bash
python -m prompt_registry verify
```

Re-checks HMAC-SHA256 signatures and guardrail constraints for every cached version.
Non-zero exit if any file fails — useful in CI or after a manual cache edit.

#### Diffing two versions

```bash
python -m prompt_registry diff master_preprompt 1.0.0 1.1.0
```

Prints a unified diff to stdout.  The GUI "Prompts" tab renders the same diff inline.

### Publishing a new version (author machine only)

Publishing requires `PROMPT_REGISTRY_PUBLISH_TOKEN` and `PROMPT_REGISTRY_SIGNING_KEY` in `.env`.
The runtime platform **never** needs `PUBLISH_TOKEN` — only the author's machine does.

```bash
# 1. Write the new prompt body to a file
echo "Updated master pre-prompt body …" > /tmp/master_preprompt_v1.1.0.txt

# 2. Publish to the registry backend (signs with SIGNING_KEY, uploads with PUBLISH_TOKEN)
python -m prompt_registry publish master_preprompt 1.1.0 /tmp/master_preprompt_v1.1.0.txt
```

After publish, any platform with `PROMPT_REGISTRY_ENABLED=true` will pick up the new version on
the next explicit `sync`.  Platforms with the registry disabled continue using their baseline copies
unchanged.

### Environment variables

| Variable | Secret? | Default | Purpose |
|---|---|---|---|
| `PROMPT_REGISTRY_ENABLED` | No | `false` | Master switch; baseline-only when `false` |
| `PROMPT_REGISTRY_BACKEND` | No | `http` | Storage backend (`http` / `local` / `firestore`) |
| `PROMPT_REGISTRY_URL` | **Yes** | — | Protected HTTPS URL of the signed manifest |
| `PROMPT_REGISTRY_TOKEN` | **Yes** | — | Bearer read-token for `PROMPT_REGISTRY_URL` |
| `PROMPT_REGISTRY_PUBLISH_TOKEN` | **Yes** | — | Higher-privilege publish credential (author only) |
| `PROMPT_REGISTRY_SIGNING_KEY` | **Yes** | — | HMAC-SHA256 key for body verification |
| `PROMPT_REGISTRY_PINS` | No | `{}` | Version pins JSON dict |
| `PROMPT_REGISTRY_REFRESH_SECONDS` | No | `0` | Refresh cadence (0 = on-demand only) |
| `PROMPT_CACHE_DIR` | No | `output/prompt_cache` | On-disk cache path |
| `PROMPT_CACHE_KEEP_VERSIONS` | No | `5` | Rollback depth per prompt ID |
| `PROMPT_MAX_CHARS` | No | `50000` | Max body size enforced by guardrails |

The four secret keys are masked in the GUI Settings tab and raise `SecretWriteError` if a write
is attempted through the `gui/env_io` path (CONSTRAINT #3).  Edit them by hand in `.env` only.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `get()` always returns baseline | `PROMPT_REGISTRY_ENABLED=false` or no `PROMPT_REGISTRY_URL` | Set both in `.env`, run `sync` |
| Signature verification failed | `PROMPT_REGISTRY_SIGNING_KEY` mismatch | Confirm the key matches the one used at publish time |
| `publish` exits non-zero immediately | `PROMPT_REGISTRY_PUBLISH_TOKEN` absent | Set the token in `.env` on the author machine only |
| `rollback` says "fewer than 2 versions" | Only one version in cache | Run `sync` to fetch the remote manifest, then retry |
| GUI "Prompts" tab shows all sources as "baseline" | Registry disabled or never synced | Enable registry and click "🔄 Sync" in the Prompts tab |

---

## AI Insights & AI Control Center

Two Command Center tabs cover every AI-generated commentary/research feature on
the platform. Both are strictly **advisory and operator-triggered** — no AI
output here ever places or modifies an order.

### 🪄 AI Insights tab

Per-symbol, on-demand AI reads on top of the pipeline's own signals:

| Section | What it does | Requires |
|---|---|---|
| Opal research brief | Qualitative thesis/catalysts/risk-factors brief grounded in real Finnhub news + earnings-calendar data (never invents numbers) | `OPAL_RESEARCH_ENABLED=true` + `OPENAI_API_KEY` (or `GEMINI_API_KEY` if routed to Gemini) |
| Claude analyst note | Plain-English rationale for the current Action Signal | `LLM_COMMENTARY_ENABLED=true` + `ANTHROPIC_API_KEY` |
| Gemini chart pattern read | Sends a 252-bar price chart to Gemini Vision and returns a structured pattern/trend/support-resistance read | `LLM_COMMENTARY_ENABLED=true` + `GEMINI_API_KEY` |
| Claude vs. Gemini disagreement view | One row per watchlist symbol comparing the deterministic Action Signal against each AI's verdict | Populated once you've generated notes for symbols in the current session |

Every section is button-gated — nothing calls out to an AI provider until you
click it — and each toggle is independent, so you can run Opal alone without
enabling Claude or Gemini commentary.

### 🎛️ AI Control Center tab

One place to see and control every AI capability on the platform, and to
start/stop AI-adjacent scheduled runs:

- **Section A — Capability grid.** One row per AI option (Claude analyst
  rationale, Gemini alert commentary, Gemini chart vision, the Gravity AI
  audit runner, Opal research) showing a `🟢 ready` / `⚪ disabled` /
  `🟡 key missing` / `🚧 not built` badge and an on/off toggle. Toggles write
  to `.env` and take effect on the **next** launch — never live.
- **Section B — On-demand per-symbol actions.** Run the Claude note, Gemini
  chart read, or Opal brief for a chosen symbol without leaving this tab
  (reuses the exact same helpers as the AI Insights tab — no duplicated
  logic, no duplicated cache).
- **Section C — Gravity AI audit.** Runs the Gravity AI Review Suite on
  demand.
- **Section D — Scheduled run.** Start (and later stop) an `--interval` or
  `--agent` background advisory loop. You start it, you stop it — nothing
  runs on its own.

Provider API keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`)
are secret-only and can never be set from the GUI — edit `.env` directly.

### Relevant environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_COMMENTARY_ENABLED` | `false` | Master switch for Claude analyst notes + Gemini alerts/vision |
| `ANTHROPIC_API_KEY` | _(none)_ | Required for Claude analyst notes |
| `GEMINI_API_KEY` | _(none)_ | Required for Gemini alerts, chart vision, and Opal when `OPAL_RESEARCH_PROVIDER=gemini` |
| `OPAL_RESEARCH_ENABLED` | `false` | Independent master switch for the Opal research agent |
| `OPAL_RESEARCH_PROVIDER` | `openai` | `openai` or `gemini` — which backend runs Opal |
| `OPENAI_API_KEY` | _(none)_ | Required when `OPAL_RESEARCH_PROVIDER=openai` |

See `docs/FEATURE_TIER_HISTORY.md` (Tier 9 sections) for the full build history of
each agent, and `docs/OPAL_BUILD_SPEC.md` for Opal's design record.

### Standing rule

Every AI action on both tabs is either a button click or an operator-started,
operator-stoppable loop. Nothing here calls another AI agent, watches a PR, or
re-invokes itself automatically — the same "no automatic AI invocation" rule
that applies to Claude Code sessions on this repo applies to the platform's
own AI features.

## 17. Report Library

The **📁 Report Library** tab is a single place to browse and read every report
file the platform produces. It is read-only and file-backed — it renders files
that already exist on disk and never calls the broker or fetches live market
data. Think of it as a document viewer over the pipeline's output folder.

### What it surfaces

- **Daily HTML report.** The primary end-of-cycle report (holdings, P&L, action
  signals, rationale). This file is regenerated on **every** advisory refresh
  cycle, so it is always current — whatever the most recent run produced is what
  you see here.
- **Daily briefings.** One human-readable briefing per day. Older briefings stay
  available so you can look back at a previous day's read. You can also generate
  **today's** briefing from within the tab if it hasn't been produced yet.
- **Orchestrator dashboards.** The full-pipeline daily report and its
  volatility-band chart. Unlike the daily HTML report, these only refresh when
  you kick off a **manual full-orchestrator run** (from the Launcher tab). If
  they look out of date, that just means no full-orchestrator run has happened
  since — run one to refresh them.
- **Validation reports.** Per-strategy validation output (PBO / DSR / Sharpe /
  Max Drawdown gate verdicts and the walk-forward/CPCV detail). A validation
  report appears here only **once a strategy has been through the validation
  harness** — until then there is nothing to show for that strategy.

### Viewing and downloading

Each file can be **viewed inline** (rendered directly in the app) or
**downloaded** to your machine for archiving or sharing. Nothing is modified —
opening or downloading a report never changes it or re-runs any analysis.

### A note on freshness

Keep the two refresh cadences in mind: the **daily HTML report is always
current** (rebuilt every advisory cycle), while the **orchestrator dashboards
lag until you run the full orchestrator manually**. When a dashboard and the
daily report seem to disagree, the dashboard is usually just older — launch a
full-orchestrator run to bring it up to date.

---

## 18. Validation Lab

The **🔬 Validation Lab** tab lets you run the strategy-validation harness on
demand and read the results back without leaving the Command Center. Previously
the only way to produce a validation report was to run
`python -m scripts.refresh_validations` from a terminal; the Report Library tab
could only *display* whatever summaries already existed on disk. This tab closes
that loop — configure a run, launch it, watch it, and read the verdict. It is
read-only and file-backed: it launches the harness as a background subprocess
and never calls the broker or submits any order.

### Running a validation

1. **Choose strategies.** Pick one or more registered strategies from the
   multiselect (the list comes straight from the runner's strategy registry).
2. **Choose a window.** Set the backtest start and end dates. The default window
   starts at 2010-01-01 and ends today.
3. **Run it.** Press **▶️ Run validation**. The harness runs as a background
   subprocess so the app stays responsive; the button is disabled while a run is
   already in flight or when no strategy is selected.

### Watching the run

The **Run status** section shows whether the current run is still going (🟢
Running) or has finished (✅ exit 0 / ❌ non-zero), and tails the live log. Use
the **🔄 Refresh** button to poll for the latest status and log lines — Streamlit
does not auto-refresh, so this is how you advance the view while a run is
in progress.

### Reading the results

The **Results** section reads the `reports/*_validation_summary.json` files the
run wrote and shows a per-strategy table with a **deployable ✅/❌** verdict plus
the four standard gate values — **PBO**, **DSR**, **Sharpe**, and **Max
Drawdown**. The pass/fail thresholds are imported from `validation.thresholds`
(PBO below its cap, DSR and net Sharpe above their floors, Max Drawdown below its
limit), so what you see here can never drift from the harness's own deployability
gate. The rendered walk-forward / CPCV HTML report can be viewed inline or
downloaded. A strategy only counts toward the preflight `validation_reports`
check once it is deployable **and** its report is less than 30 days old.

---

## 21. Running the Read-Only State API Securely

The platform ships a small, **standalone read-only API** (`api/state_api.py`)
that serves the state the pipeline has already persisted to disk. It is a
foundation for a future web/mobile frontend — **not the trading engine**. It
never touches the broker, never fetches live market data, and never runs any
analysis. It only reads `output/state_snapshot.json` and the closed-trades
table, exposing four endpoints:

| Endpoint | Returns |
|----------|---------|
| `GET /health` | `{"status":"ok"}` liveness of the API process (always open, no token) |
| `GET /state` | The full parsed `output/state_snapshot.json` (404 if no snapshot yet) |
| `GET /signals` | Just the `signals` list from that snapshot |
| `GET /trades` | Closed trades from the transactions store (`[]` when there are none) |

It is deliberately **not wired into the desktop shell, the GUI, or any
orchestrator** — you launch it yourself, on demand, when you want a read-only
HTTP view of the persisted state.

### 1. Generate a bearer token

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the printed value — that is your token.

### 2. Configure `.env`

Add the token and the allowed browser origins to your `.env`:

```
STATE_API_TOKEN=<paste-the-token-here>
CORS_ALLOWED_ORIGINS=["http://localhost:3000"]
```

`STATE_API_TOKEN` is a **secret** (masked in the GUI, never GUI-writable).
`CORS_ALLOWED_ORIGINS` is a JSON list of the browser origins allowed to call
the API; the default is `["http://localhost:3000"]`. The API answers `GET`
requests only.

### 3. Launch the API

```bash
uvicorn api.state_api:app --port 8600
```

No extra dependencies are needed — `fastapi`/`uvicorn` are already in
`requirements.txt`.

### 4. Call it with curl

Health is always open (no token required):

```bash
curl -s localhost:8600/health
# {"status":"ok"}
```

Once `STATE_API_TOKEN` is set, the data endpoints require the token. Without
it you get a `401`:

```bash
curl -s -o /dev/null -w "%{http_code}" localhost:8600/state
# 401
```

With the token, the request succeeds:

```bash
curl -s -H "Authorization: Bearer <token>" localhost:8600/state
```

### ⚠️ Warning: leaving the token blank disables auth

Authentication is **fail-open**: if `STATE_API_TOKEN` is unset or empty, the
`/state`, `/signals`, and `/trades` endpoints are served **without any
authentication** (and the API logs a startup warning to that effect). This is
convenient for zero-config local use — a localhost-only API bound to your own
machine is fine unauthenticated. But if you expose port 8600 to a network or
the internet, **always set `STATE_API_TOKEN`** first; otherwise anyone who can
reach the port can read your persisted state and closed-trade history.

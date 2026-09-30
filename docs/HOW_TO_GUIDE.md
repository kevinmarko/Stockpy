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
21. [Running the Read-Only State API Securely](#21-running-the-read-only-state-api-securely)
22. [Using the Retrospective Learning Loop](#22-using-the-retrospective-learning-loop)

---

## 1. What This Platform Does

InvestYo is an **automated quantitative analysis pipeline**. Every time you run it, it:

1. **Fetches live data** — price history *and* company fundamentals (using Financial Modeling Prep as the primary provider, with Yahoo's free financial statements as fallback), macroeconomic indicators from FRED (Federal Reserve Economic Data)
2. **Computes indicators** — RSI, MACD, Aroon, ATR, GARCH volatility, Graham Number, implied volatility rank, and more
3. **Runs forecasts** — ARIMA, Monte Carlo simulation, Holt-Winters exponential smoothing, and a CNN-LSTM deep learning model, all multi-horizon
4. **Detects the macro regime** — classifies the current environment as RISK ON / NEUTRAL / RECESSION / CREDIT EVENT using yield curve, credit spreads, VIX, and a Hidden Markov Model (HMM) second opinion
5. **Generates signals** — for each ticker: STRONG BUY / BUY / HOLD / RISK REDUCE, plus an options overlay recommendation
6. **Sizes positions** — calculates a Kelly Target (% of capital to allocate) based on your actual trade history
7. **Submits paper orders** — when `ADVISORY_ONLY=false`, sends buy/sell orders to the local FMP paper ledger (never when going live; real orders go only through the Robinhood queue)
8. **Produces reports** — an HTML dashboard, an interactive Plotly volatility chart, and a JSON payload

You can use the output purely as research (read the HTML report, decide manually), or lift `ADVISORY_ONLY` to let the pipeline paper-trade on the FMP paper ledger.

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

This creates `quant_platform.db` (SQLite) with the correct schema for storing daily signals and execution logs. As of `settings.LOCAL_DATA_ROOT` (2026-08), the file lives under `$LOCAL_DATA_ROOT` (default `~/.stockpy_local/`) — a machine-global folder OUTSIDE every git checkout/worktree, shared across all of them — not at the repo root. Every fresh clone still needs this step once (an empty `LOCAL_DATA_ROOT` has no DB file yet), but a second worktree on the same machine will typically find the DB already initialized. See `docs/architecture/data-layer.md`'s `settings.LOCAL_DATA_ROOT` subsection for the full directory layout. The `trades` table starts empty; the closed-trade population that powers Kelly sizing and the calibration tracker is reconstructed on demand from your Robinhood filled-order history by `data/robinhood_orders.py` (Tier 7) and accumulates live as advisory runs record trades.

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

### Broker settings (needed only for automated paper order submission)

| Setting | Notes |
|---------|-------|
| `PAPER_TRADING` | `true` (default) = paper trading on the local FMP paper ledger (no broker keys needed; an old `ALPACA_PAPER` in `.env` still works as an alias). With `false` and `ADVISORY_ONLY=false` the automated pipeline places **no orders** (CRITICAL log + alert) — real orders go only through the Robinhood queue |

There are no broker API keys to configure: Alpaca was removed 2026-09-30 and the paper ledger fills at live FMP quotes.

### Settings with safe defaults (you can ignore these initially)

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `DRY_RUN` | `false` | When `true`, orders are logged but never placed on the paper ledger |
| `PAPER_TRADING` | `true` | Paper vs live posture (live = the automated pipeline places no orders) |
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
| `FMP_NEWS_ENABLED` | `true` | When `true` (and `FMP_API_KEY` is set), Financial Modeling Prep becomes the PRIMARY company-news/earnings-date provider for the `news_catalyst` signal, Opal research briefs, and the Antigravity sentiment agent — recommended, since FMP covers ≥6 months of real news history. Finnhub was removed 2026-09, so FMP is the only news provider; with it off the news-catalyst headline source is empty |
| `FUNDAMENTALS_SOURCE` | `fmp` | Fundamentals backend: `fmp` (default), `yahoo` (statement-derived), or `yfinance_info` (raw `.info` fallback) |
| `FORECAST_USE_GARCH_SIGMA` | `true` | Use the GJR-GARCH(1,1) volatility (annualized, converted to daily via ÷√252) as the Monte Carlo sigma, so the MC confidence band widens in turbulent regimes and tightens in calm ones. `false` restores the naive historical-stdev sigma |
| `FORECAST_PROPHET_WEIGHT` | `0.25` | Weight `w` given to the Prophet 30-day forecast when blending it into the 30-day ensemble: `final = base*(1-w) + prophet*w`. `0.0` disables Prophet's influence on the blend (Prophet must also be installed to have any effect) |

### Exploring Platform Settings (Settings Reference & Dictionary)

In the Pilots PWA under **Settings → Modules & Integrations → Settings Reference** (`/settings/reference`), you can inspect the complete dictionary of all 464 platform configuration fields organized across 14 functional domains:
- **Search and Filter**: Filter settings by keyword or functional domain (e.g., Risk & Circuit Breakers, Options Desk & Volatility, LLM & AI Services).
- **Turn any on/off flag on or off right here**: every non-secret boolean field — not just ones already covered by a specialized editor — shows a real toggle switch you can flip directly on this screen. A safety-critical field (marked "Dangerous") asks you to type its exact name to confirm before it saves, the same protection every other settings editor already enforces. A field marked with a "not read anywhere" warning is genuinely dead code — toggling it is accepted but has no effect, so no switch is shown for it.
- **Liveness Indicators**: Each setting displays whether updates apply immediately or require an engine/daemon restart, plus any active capture sites.
- **Direct Navigation**: If a setting is also grouped with related fields in one of the webapp's specialized settings editors (e.g., General Tunables, Feature Flags, Sentiment, Sector Selection, Paper Broker, FMP, Cache Long/Short), an **Edit here →** button navigates directly to that editor.
- **Secret Protection**: API keys and passwords are masked (`•••• (set)` or `(not set)`) everywhere — including their platform default — and can never be leaked or edited through the browser.

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

### How `main.py` builds its universe (held ∪ watchlist ∪ discovered ∪ DEFAULT_TICKERS fallback)

The advisory orchestrator `main.py` assembles its universe from held positions, the
watchlist, and discovered scan candidates, falling back to `DEFAULT_TICKERS` only when
that whole union is empty (`_build_universe()`, delegating most of the logic to
`data.portfolio_sync.compute_tracked_universe()` so `main.py` and the persistent daemon
can't silently diverge on what counts as "the tracked universe"):

1. **Robinhood held positions** — every symbol in your account snapshot is always included when the snapshot is available.
2. **`WATCHLIST` env var or `watchlist.txt`** — merged in whenever present. The env var (comma-separated) takes precedence over the file; the file is one ticker per line with `#` for comments.
3. **Discovered scan candidates** (`output/scan_candidates.json`, from the agentic-discovery skill) — merged in whenever present.
4. **`settings.DEFAULT_TICKERS`** — used **only as a fallback** when sources 1-3 are all empty (or rating-exclusion emptied them).
5. **Recently-closed positions** (`settings.CLOSED_POSITION_RETENTION_DAYS`) — a symbol you recently sold stays in the universe for a bounded window; unioned in last.

If the whole union (including the `DEFAULT_TICKERS` fallback) is still empty, `main.py`
logs a warning naming the remediation paths (RH_* env vars, `WATCHLIST`, `watchlist.txt`)
and exits the cycle cleanly. SPY is still fetched automatically by the macro/HMM layer
regardless.

**Retired (2026-09, step 4e):** a Google Sheet "Sheet2" column-A last-resort fallback
used to run after `DEFAULT_TICKERS`. It was removed along with the rest of the Google
Sheet output sink — see [Section 18](#18-google-sheets-integration-legacy).

### Universe Coverage

The Pilots PWA's Settings → Tracked Universe screen includes a coverage
panel with three honest counts. Note this reads a narrower universe than
`main.py`'s own three-tier assembly described above — held positions plus
Robinhood/file-backed watchlists only, not the Sheet2 fallback or
scan-discovered candidates:

1. **Tracked**: every symbol in this report.
2. **Forecast-covered**: the subset with a price forecast recorded recently
   by the pipeline (the platform's forecast-tracking database) — this
   reflects whether the pipeline actually forecast that symbol lately, not a
   fixed sector or model list.
3. **Full data coverage**: the subset where a live check, performed when you
   load the screen (not read from a cached pipeline artifact), confirmed
   price quotes, historical bars, and fundamental data are all available
   right now.

Each count is clickable to filter the symbol list below it. These numbers
often genuinely diverge — a symbol can be tracked without a recent forecast
(e.g. it was only just added), or forecast-covered without full data
coverage (e.g. fundamentals are temporarily unavailable from the data
provider).

### Symbol rating and automatic exclusion

Every tracked symbol gets a GOOD/BAD rating from the platform's scoring engine each cycle, persisted to a durable history (`rating/symbol_rating_store.py`). When `SYMBOL_RATING_AUTO_DROP_ENABLED` is turned on (it's `False` by default), a symbol with enough consecutive BAD-rated cycles in a row (`SYMBOL_RATING_DROP_THRESHOLD_CYCLES`, 5 by default) can be automatically excluded from tracking and buying — but a symbol you currently hold is **never** auto-excluded, regardless of its rating streak. The Pilots PWA's Tracked Universe screen (`GET /data/sync-report`) shows each symbol's consecutive-BAD-cycle count and an "Excluded" badge for anything currently dropped; a "Re-include" button (`POST /universe/{symbol}/reinclude`) lets you manually undo an exclusion at any time without waiting for a GOOD-rated cycle.

---

## 5. Running the Pipeline

### The recommended way — the web app

The platform's UI is the **Pilots PWA** (`webapp/`). Double-click `launch_webapp.command`
at the project root from **Finder** or the **Dock**. It asks whether to use offline mock
data (the default) or live data. In live mode it starts the backend APIs it needs (and the
orchestrator daemon, when `ORCHESTRATOR_DAEMON_ENABLED=true`) if they aren't already
running, then opens the web app. It does not run the pipeline for you — use the Pipeline
screen (below) or `launch.command`.

For a backend that keeps running with no Terminal window open, install the always-on stack
service — see `docs/RUNBOOK.md` §0.1.

The old Streamlit desktop app (`launch_app.command`, `launch_gui.command`,
`legacy/streamlit_command_center/`) was deleted in 2026-09; git history has it.

**To add to the Dock**: drag `launch_webapp.command` to your Dock → right-click → Options → Keep in Dock.

---

### The headless way — double-click on macOS

Prefer a terminal-only loop with no web app, or need something scriptable for a scheduled task?
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

### The web app — visual control panel

The web app is a graphical front-end over the same pipeline. Its main screens:

- **Pipeline** (`/pipeline`) — daemon status, run triggers ("Run full advisory pipeline"
  plus stage-scoped ones), run history, and the dead-letter queue with per-symbol retry.
- **Console** (`/console`) and **Commands** (`/commands`) — launch jobs and CLI targets
  (including strategy validation) and follow their output.
- **Mission Control** (`/observability`) — regime, portfolio risk, equity/drawdown,
  forecast skill, circuit breakers, risk-gate blocks, heartbeat, and logs; see
  [§12](#12-the-observability-dashboard).
- **Portfolio** (`/portfolio`) — holdings, P&L, and held-vs-signal reconciliation.
- **Options** (`/options`), **Pairs radar** (`/pairs`), **Signal Breakdown** (`/signals`),
  **Symbol Screener**, **Forecast Viewer**, and other research screens.
- **Calibration** (`/calibration`) and **Attribution** (`/attribution`) — decision journal,
  conviction calibration, and Brinson-Fachler attribution.
- **Report Library** (`/operations/reports`) — generated reports.
- **Universe Transparency** (`/universe`) — tracked universe and coverage status.
- **Settings** (`/settings`) — execution mode and kill switch (General), tunables, strategy
  weights/enabled modules (Strategy), brokers, AI capabilities (AI), prompt registry
  (Prompts), feature flags, and a full settings reference.
- **Help & Glossary** (`/help`) — searchable glossary; each screen also has a "How this
  works" panel.

The old desktop app's live 0–100% pipeline-progress bar (`output/progress.json`) has no
web app equivalent yet.

---

### From Terminal — primary async orchestrator

```bash
python3 main_orchestrator.py
```

This runs the full async pipeline: data fetch → macro regime → options analysis → processing → forecasting → strategy signals → HTML report → paper broker orders (if `ADVISORY_ONLY=false`).

It auto-activates the `.venv` virtual environment if you haven't done so manually.

### Dry-run mode (safe to test — no orders sent)

```bash
python3 main_orchestrator.py --dry-run
```

The pipeline runs identically but any generated orders are logged rather than placed on the paper ledger. Use this to verify the setup before enabling live order flow.

### Offline / mock mode

If a `FRED_API_KEY` is not configured, the orchestrator automatically falls back to `MockDataEngine`, which generates deterministic synthetic data (`data_engine.live_data_configured()` — a FRED key check, not a `credentials.json` presence check; see the note below). Useful for testing code changes without network access.

This is expected in development.

**Note (2026-09):** this gate used to key off `os.path.exists("credentials.json")` (the Google Sheets service-account file). It was switched to a FRED-key check in step 4 prep, ahead of the Google Sheet publisher's retirement in step 4e (see [Section 18](#18-google-sheets-integration-legacy)) — deleting `credentials.json` no longer silently switches the daemon to fabricated data.

### The advisory orchestrator

```bash
python3 main.py
```

This is the original synchronous, clean advisory pipeline. Use `main_orchestrator.py` for everything that needs the full 50+ dashboard column set. `main.py` no longer writes to Google Sheets — that sink was retired in step 4e (2026-09); see [Section 18](#18-google-sheets-integration-legacy).

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
- **Decision Journal** (web app Calibration screen): log whether you acted on, passed, or modified each advisory signal (see [Manual Execution Journal](#manual-execution-journal-reports-tab) below).
- **Conviction Calibration** (web app Calibration screen): reliability diagram showing whether the conviction scores match actual win rates (see [Conviction Calibration](#conviction-calibration-reports-tab) below).

Non-held watchlist symbols render "—" in the holdings columns (positions are
never fabricated). The report contains no credentials.

### Interactive volatility chart

`output/volatility_bands_dashboard.html` — Plotly chart of the first ticker's price history with volatility bands overlaid. Open in a browser.

### State snapshot (for the dashboard)

`output/state_snapshot.json` — machine-readable summary read by the web app's backend (Mission Control, symbol pages, and more). Updated every pipeline run. A timestamped copy is ALSO written to `output/history/state_snapshot_<UTC>.json` and pruned after `SNAPSHOT_HISTORY_DAYS` (default 30); the daily HTML report's "Δ Since Last Run" band reads the two most recent rotated copies via `scripts/snapshot_diff.py`.

### Manual Execution Journal (Reports tab)

The **Decision journal** section on the web app's **Calibration** screen (`/calibration`) lets you log what you did with each advisory signal — useful for post-hoc analysis and for teaching the calibration tracker which signals you actually endorsed.

**How it works:**

1. Open the Calibration screen and scroll to the decision journal.
2. Pick a current signal to log a decision against.
3. Add optional notes, then click one of:
   - **✅ Acted** — you executed (or are executing) the suggested action.
   - **⏭ Passed** — you saw the signal but chose not to act.
   - **🔁 Modified** — you acted differently from the system (enter notes explaining the change).
4. The entry is appended to `output/decision_log.jsonl` (JSON-Lines, one entry per line).

For **"Acted"** entries only, the journal automatically looks up the nearest matching trade in `quant_platform.db` within ±24 h and records the `trade_id`. This allows the conviction calibration chart to filter to "decisions the operator actually endorsed."

**Log file location:** `output/decision_log.jsonl` — append-only, never read by the signal pipeline. The Calibration screen lists recent entries.

### Conviction Calibration (Reports tab)

The **Conviction Calibration** section on the web app's **Calibration** screen renders a reliability diagram — comparing the system's stated conviction score against the actual empirical win rate per conviction bin.

- X-axis: conviction bin (0–1, split into 10 equal bins by default).
- Y-axis: actual win rate for closed trades in that bin.
- Diagonal line: "perfect calibration" (conviction 0.8 → 80% actual win rate).

Bars above the diagonal → conviction underestimates actual skill in that range.
Bars below the diagonal → conviction is overconfident in that range.

**Important:** win rates are only shown for bins with ≥ 5 trades (configurable). Closed trades reconstructed from your Robinhood order history (via `data/robinhood_orders.py`) have no conviction scores, so the chart starts empty and fills in as `record_trade(conviction=...)` calls accumulate from live advisory runs.

### Database

`quant_platform.db` — SQLite database storing signal history and trade records. As of `settings.LOCAL_DATA_ROOT` (2026-08) it lives under `$LOCAL_DATA_ROOT` (default `~/.stockpy_local/`), not the repo root — see `docs/architecture/data-layer.md`. Query it with any SQLite client:

```bash
sqlite3 ~/.stockpy_local/quant_platform.db "SELECT * FROM DailySignals ORDER BY date DESC LIMIT 10;"
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
- `reports/history/<strategy_name>_validation_history.jsonl` — append-only, one row per historical run (capped at `MAX_VALIDATION_HISTORY_ROWS`), so PBO/DSR/Sharpe/MaxDD can be plotted as a trend across runs (read via `validation.harness.read_validation_history`; rendered on the web app's Strategy Health screen as a validation trend)

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

1. No broker credentials are needed: the automated pipeline trades on the local FMP paper ledger (Alpaca was removed 2026-09-30).
2. Add to `.env`:
   ```
   ADVISORY_ONLY=false
   PAPER_TRADING=true
   PAPER_TRADING_START_DATE=2026-06-24
   ```
3. Run the pipeline:
   ```bash
   python3 main_orchestrator.py
   ```
4. Watch the Paper Broker screen in the web app — you should see paper orders and positions appear

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

Open the web app's **Mission Control** screen (`/observability`) for regime, portfolio risk,
kill switch / circuit-breaker status, and recent risk-gate blocks, and the **Portfolio**
screen for live P&L and open positions — see [§12 The Observability Dashboard](#12-the-observability-dashboard).

### Minimum paper trading period

The preflight check requires **90 days** of continuous paper trading before going live. This is enforced via `PAPER_TRADING_START_DATE` in your `.env`.

---

## 12. The Observability Dashboard

The platform's observability surface is the web app's **Mission Control** screen
(`/observability`). The old standalone `observability/dashboard.py` app and the Streamlit
desktop app's Observability tab are both gone (the desktop app was deleted in 2026-09).

Mission Control loads everything in one `GET /observability/summary` call. The sections
past the attention strip, portfolio risk, and equity chart are collapsed under a
"Background telemetry" disclosure.

### What you'll see

| Section | What it shows |
|-------|--------------|
| Attention strip (top) | Items that need action: circuit-breaker trips, portfolio heat over limit, risk-gate blocks, sizing-cap escalations, a heartbeat older than 120 s, the macro gate being off. Shows "All clear" when nothing qualifies. |
| Control · Macro regime gate | Toggle for `MACRO_REGIME_GATE_ENABLED`, plus regime, VIX, Sahm Rule, HY OAS, 10Y-2Y, and HMM risk-on probability |
| Portfolio risk | Portfolio heat and exposure over the account equity history |
| Equity & drawdown | Equity curve and drawdown |
| Forecast skill / Forecast skill by symbol | Portfolio-wide and per-symbol forecast reliability and weights |
| Circuit breakers | Kill switch + risk-gate block counts |
| Risk gate block log | Recent blocked orders (`output/risk_gate_blocks.jsonl`) and which check blocked them |
| System telemetry | Host and process resource usage |
| Data latency | Per-symbol fetch latency (needs `MARKET_DATA_LATENCY_TRACKING_ENABLED=true`) |
| Sizing cap-event audit trail | Sizing guardrail cap events |
| Heartbeat | Current orchestrator heartbeat age (`output/heartbeat.txt`) |
| Strategy P&L | Realized P&L by strategy |
| Logs | Tail of `logs/investyo.log` |

Account holdings and P&L (equity, buying power, per-position unrealized P&L) are on the
**Portfolio** screen.

The Portfolio screen's holdings read the same Robinhood snapshot the advisory
report uses — it is the source of truth for account state (holdings, cost
basis, dividends, equity) and never contains credentials.

**Note on the paths in the table above:** `output/...` and `logs/...` are shown as short-hand
file names — as of `settings.LOCAL_DATA_ROOT` (2026-08), they actually live under
`$LOCAL_DATA_ROOT` (default `~/.stockpy_local/`), OUTSIDE the git checkout. See `docs/architecture/data-layer.md`'s `settings.LOCAL_DATA_ROOT`
subsection for the exact per-subfolder layout.

### Staleness warning

If the orchestrator heartbeat (`output/heartbeat.txt`) is older than 120 s, Mission Control's
Heartbeat section shows a stale status and the attention strip lists it. This means no fresh
signals are available.

---

## 13. Preflight Check — Are You Ready to Go Live?

```bash
python scripts/preflight_check.py
```

Runs 27 checks total. Behaviour depends on `ADVISORY_ONLY`:

* **`ADVISORY_ONLY=true` (default)**: six checks are automatically skipped
  (shown as PASS with a per-check advisory-mode note): three broker-stack checks
  (`paper_trading_mode`, `dry_run_disabled`, `paper_trading_duration`) and
  three runtime-state checks that are false-positives for advisory runs
  (`heartbeat_fresh`, `validation_reports`, `no_unexpected_risk_blocks`).
  `advisory_only_active` always passes loudly, and `robinhood_execution_mode` /
  `state_snapshot_fresh` are **never** auto-skipped (see below — they're the
  advisory-relevant liveness/safety checks). Exit 0 when the remaining checks pass.
* **`ADVISORY_ONLY=false`**: all 27 checks run. Exit 0 only when ALL pass (required
  before going live).

| Check | Advisory skip? | Passes when | How to fix a failure |
|-------|:--------------:|------------|---------------------|
| `fred_key_configured` | No | `FRED_API_KEY` is set | Add key to `.env` |
| `key_rotation_recent` | No | `FRED_KEY_ROTATED_DATE` set and within 90 days — warning only, never blocking | Set `FRED_KEY_ROTATED_DATE=YYYY-MM-DD` in `.env` when you rotate |
| `advisory_only_active` | No | Always — PASS-loud when `true`, PASS-with-warning when `false` | Set `ADVISORY_ONLY=true` to return to advisory mode |
| `robinhood_execution_mode` | No | `ROBINHOOD_EXECUTION_MODE` is `off`/`review` (always passes), or `live` with a positive `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` — independent of `ADVISORY_ONLY` since the Robinhood bridge is orthogonal to the paper-broker quarantine | Set `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` to a per-order dollar cap before setting `ROBINHOOD_EXECUTION_MODE=live` |
| `macro_regime_gate_enabled` | No | `MACRO_REGIME_GATE_ENABLED=true` (blocks in live mode when off) | Set `MACRO_REGIME_GATE_ENABLED=true` in `.env` |
| `paper_trading_mode` | **Yes** | `PAPER_TRADING=true` — warning only (renamed from `alpaca_paper_mode`) | Change to `false` only when ready to go live (the automated pipeline then places no orders) |
| `live_order_routing` | No | Always — plain PASS in paper/advisory mode; PASS-with-warning when going live (`PAPER_TRADING=false`, `ADVISORY_ONLY=false`), because the automated pipeline then places no orders and real trades go only through the Robinhood queue | Nothing to fix — it is a posture reminder |
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

When `NTFY_TOPIC` is unset `notify()` is a silent no-op — the app runs identically. Only the rotating log file (`logs/investyo.log`, under `$LOCAL_DATA_ROOT` — see below) is written.

---

### Log file (always-on, no config needed)

`logs/investyo.log` is created automatically on first run. As of `settings.LOCAL_DATA_ROOT`
(2026-08) it lives at `$LOCAL_DATA_ROOT/logs/investyo.log` (default
`~/.stockpy_local/logs/investyo.log`), OUTSIDE the git checkout, not at the repo root — see
`docs/architecture/data-layer.md`. It rotates at 10 MB and keeps 5 backups (≈50 MB max). The format is:

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
ALERT_FILE_PATH=~/.stockpy_local/logs/alerts.jsonl
```

Every alert is appended as a JSON line. Useful for post-incident review. `ALERT_FILE_PATH` is
operator-supplied and can point anywhere, but `$LOCAL_DATA_ROOT/logs/` (default
`~/.stockpy_local/logs/`) keeps it alongside `investyo.log` rather than inside the repo checkout.

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

The web app's Settings → General screen also has a pause/resume toggle for the same
sentinel (resume is disabled there while `ADVISORY_ONLY=false`; resume at the console).

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
sentinel is still written so the web app's kill-switch banner activates and the operator is alerted.

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

### Universe Transparency Screen

The **Universe Transparency** screen (under Operations → Universe Transparency) allows you to inspect the operational status of your tracked tickers. The top-line metrics show a three-number distinction:
- **Tracked universe**: The total number of symbols processed.
- **Forecast-covered**: Symbols with enough history for price projections.
- **Full coverage**: Symbols with both quotes and fundamentals.

If a symbol is not currently tracked and you want to look it up, you can use the **Symbol Screener** to search the wider market.

### Explain This Ticker

On the Universe Transparency screen (and other ticker lists), you can click the **ⓘ button** next to any symbol to open the **Explain This Ticker** slide-over drawer. This drawer breaks down exactly why a symbol is tracked (e.g., held in your account, on a watchlist, or from the fallback sheet), its current data coverage, and if it is being automatically dropped due to consecutive bad rating cycles.

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

**Retired in step 4e (2026-09).** The Pilots PWA (`webapp/`) is the platform's only
frontend, and nothing reads or writes the Google Sheet anymore. `main.py` no longer has
a Sheet write path or a Sheet2-column-A universe fallback (see
[Section 4](#4-choosing-your-ticker-universe)).

The old code (`reporting/sheet_publisher.py`, `reporting/sheets_client.py`) was moved
to `legacy/reporting/` rather than deleted, so it can be restored if needed — see
`legacy/README.md`. `credentials.json` (the Google service-account key this integration
used) is no longer read by any active code; it isn't tracked by git and the operator
manages it independently of this repo.

If you're restoring this from `legacy/`, the original setup was: create a Google Cloud
service account with the Sheets + Drive APIs enabled, save its JSON key as
`credentials.json` in the project root, and share the target spreadsheet with the
service account's email as Editor. The Sheet had a "Sheet2" tab (column A = ticker
symbols, the old universe fallback), a "FidelityData_Automated" tab (output, overwritten
each run), and an optional "Transactions" tab.

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

All tests are offline (the Alpaca smoke test moved to `legacy/tests/` on 2026-09-30).

---

## 20. Troubleshooting Common Problems

> **Where these files actually live:** several fixes below reference `quant_platform.db`,
> `output/heartbeat.txt`, `output/risk_gate_blocks.jsonl`, etc. as if they sit at the repo root.
> As of `settings.LOCAL_DATA_ROOT` (2026-08), they instead live under `$LOCAL_DATA_ROOT`
> (default `~/.stockpy_local/`) — a machine-global folder OUTSIDE every git checkout/worktree.
> Substitute e.g. `~/.stockpy_local/quant_platform.db` or `~/.stockpy_local/output/heartbeat.txt`
> for the bare filenames below (or your own `LOCAL_DATA_ROOT` override). See
> `docs/architecture/data-layer.md`'s `settings.LOCAL_DATA_ROOT` subsection for the full layout.

### "FRED_API_KEY is not configured"

Set `FRED_API_KEY=your_key` in `.env`. Get a free key at fred.stlouisfed.org.

### "FRED_API_KEY not configured. Operating with deterministic MockDataEngine."

This means no `FRED_API_KEY` is set in `.env` — `data_engine.live_data_configured()` is
the real/mock switch (a FRED-key check). Set `FRED_API_KEY=your_key` to get real data;
the pipeline still runs normally on synthetic data without one, which is fine for
testing code changes. (Before 2026-09 this gate keyed off `credentials.json`, the now-
retired Google Sheets service-account file — see
[Section 18](#18-google-sheets-integration-legacy) — which tied real-vs-mock data to an
unrelated integration; deleting that file no longer has any effect on this choice.)

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

The desktop app's Safety tab was deleted in 2026-09. In the web app, **Mission Control**
(`/observability`) has the equivalent **Circuit breakers** section (kill switch +
risk-gate block counts) and the **Risk gate block log** (recent entries from
`output/risk_gate_blocks.jsonl`). Run the Gravity audit from the **Console** screen.

The old Dependency Map view (which data sources feed which consumers) has no web app screen;
the map itself is still `shared/dependency_map.py` (`impacted_consumers([...])`).

When the orchestrator vetoes orders unexpectedly:
1. Open Mission Control.
2. Look at Circuit breakers and the Risk gate block log for the most recent trip.
3. If it's the kill switch, deactivate it with the Settings → General toggle or
   `python -m execution.kill_switch --deactivate` (the sentinel file is `output/KILL_SWITCH`).
4. If it's a risk-gate block, read the recorded check, threshold, and observed value —
   those tell you *which check* fired and *by how much*.

## Advisory-Only Mode

`settings.ADVISORY_ONLY=true` is the project default. It quarantines the entire broker-
execution surface so the pipeline can run safely without ever touching a live or paper
account.

### What changes when ADVISORY_ONLY=true

| Layer | Behaviour |
|-------|-----------|
| `main_orchestrator._execute_broker_orders` | Returns immediately with an INFO log — no broker imports reached |
| Web app status banner | Reads "Advisory Only Mode (Live Execution Disabled)" |
| Web app execution-mode selector (Settings → General) | Shows Advisory; switching modes writes `ADVISORY_ONLY`/`DRY_RUN` and needs a typed confirmation |
| `scripts/preflight_check.py` | Eight broker/advisory-false-positive checks auto-skip; `advisory_only_active` = PASS-loud; `robinhood_execution_mode` and `state_snapshot_fresh` always run |
| Kill switch sentinel | Repurposes as a pause-recommendations gate (see §15) |

### Re-enabling broker execution

```bash
# 1. Set in .env
ADVISORY_ONLY=false
DRY_RUN=false
PAPER_TRADING=true   # paper ledger; with false the automated pipeline places no orders

# 2. Verify preflight (all 27 checks must pass)
python scripts/preflight_check.py

# 3. Launch pipeline (paper mode)
python3 main_orchestrator.py
```

`ADVISORY_ONLY=false AND DRY_RUN=false AND PAPER_TRADING=false` is the "going live" posture:
the automated pipeline then places **no orders** (`execution/broker_selection.py::resolve_broker_backend()`
returns None, CRITICAL log + alert). Real-money orders go only through the Robinhood execution queue,
with per-trade human confirmation.

---

## Strategy Matrix tab — Global Execution Mode toggle

The desktop app's Strategy Matrix tab was deleted in 2026-09. In the web app the execution
mode selector is on **Settings → General**, with four modes:

| Mode | ADVISORY_ONLY | DRY_RUN | What happens |
|---|---|---|---|
| Advisory | true | — | No broker contact at all (project default). See [Advisory-Only Mode](#advisory-only-mode). |
| Simulation | false | true | OrderManager intercepts every intent before any broker contact. |
| Paper | false | false | Orders route to the FMP paper ledger (`PAPER_TRADING=true`). No real money. |
| Live | false | false | `PAPER_TRADING=false`: the automated pipeline places no orders; use the Robinhood queue. |

Every mode change needs a typed confirmation, and the flags (`ADVISORY_ONLY`, plus
`DRY_RUN` and `PAPER_TRADING` for non-advisory modes) are written together so a half-state
can't be set. **Setting takes effect on the next orchestrator/advisory launch.**

Signal-module weights, enabled/disabled modules, and each module's version fingerprint
(sha256 prefix + file mtime) are on **Settings → Strategy**. If you redeployed a strategy
file but the fingerprint and mtime haven't moved, the file did not actually change on disk.

## Reports tab — Live vs Backtested provenance + drill-down

The desktop app's Reports tab (and its live-vs-backtested banner and per-symbol drill-down)
was deleted in 2026-09. In the web app, a symbol's detail page (`/symbol/:ticker`) and the
**Signal Breakdown** screen (`/signals`) explain a symbol's current score, and
**Trade History** (`/trade-history`) lists closed trades.

## Advisory-Only Mode (Tier 5.1, default-on)

The project ships with **`settings.ADVISORY_ONLY=true`** as the default. In this mode the platform runs the full quant pipeline — fetches data, computes indicators, runs forecasts, sizes positions, writes the HTML report and JSON payload — but **never submits orders to any broker**.

**What you will see:**
- Web app: a status banner reading "Advisory Only Mode (Live Execution Disabled)"; Settings → General shows Advisory as the current execution mode.
- Orchestrator: an INFO log line `"ADVISORY_ONLY=True — broker execution surface is quarantined; skipping all order submission, reconciliation, and broker imports."`
- Preflight: a new `advisory_only_active` row at position #2; the broker-dependent rows (`paper_trading_mode`, `dry_run_disabled`, `paper_trading_duration`; earlier versions also had the since-removed `alpaca_configured`) show as PASS with reason `"(skipped: ADVISORY_ONLY=True — broker check not applicable)"`.

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
| `NTFY_DASHBOARD_URL` | *(empty)* | Optional URL appended to every alert body (e.g. `http://localhost:5173`). Tap the notification to open the web app directly. |

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
separate automated paper-broker surface).

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

### Checking the queue, receipts, and reconciliation

The desktop app's Robinhood panel was deleted in 2026-09. In the web app, the current
`output/execution_queue.json` (each intent, its mode, `allow_place`, and gate reasons for
blocked intents) is shown read-only on the **Agent** (`/agentic`) and **Commands** screens.

Receipts and reconciliation have no web app view yet. What the agent previewed, placed, and
skipped is in `output/execution_receipts.jsonl` and the placed-intent ledger
(`output/execution_placed.jsonl`); `execution/receipts_store.py` matches those against your
account's actual Robinhood fills. Check them after every live run.

Placement always goes through the `/rh-execute` skill with per-order confirmation — no web
app screen places Robinhood orders.

---

## In-App Help & Glossary

The web app's **Help & Glossary** screen (`/help`) gives instant access to every concept in
this guide.

### What you'll find

| Widget | Where | What it does |
|---|---|---|
| "How this works" panel | Top of each screen | Plain-English summary of the screen's purpose; expanded on a screen's first visit only |
| `?` info tips | Section headings (e.g. on Mission Control) | Definition of that section's concept |
| Glossary | Help & Glossary screen → search box | Searchable definitions of every metric, gate, and term |

### First-run onboarding tour

The web app has an onboarding flow for first-time use. To see it again, use **Reset
onboarding** on Settings → General.

### Help-key convention

Metric tooltips are looked up via keys of the form `"<tab>.<metric_name>"` in
`shared/help_content.METRIC_HELP`. A missing key returns `""` and renders no tooltip
— it **never raises** (CONSTRAINT #6). All operator-facing definitions live in
`shared/help_content.py`. The web app keeps its own help text in
`webapp/src/help/helpContent.ts`.

### Anchor-contract invariant

Every glossary entry's `guide_anchor` field **must** resolve to a real heading slug in
this file. The contract is enforced by:

- `tests/test_help_content.py::TestAnchorValidity` (runs in CI on every push).
- Gravity step 68 check 3.

If you rename any heading in this file, search for the old slug in `shared/help_content.py`
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
| Web app Settings → Prompts (`/settings/prompts`) | Resolved version / source per ID, body and diff viewer, pin/clear-pin, Sync (runs as a job) |

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
**CONSTRAINT #5 — never called on a timer.**  Sync is explicit (CLI, or Sync on the web app's Settings → Prompts screen).

#### Pinning a specific version

```bash
python -m prompt_registry pin master_preprompt 1.1.0
```

Writes `PROMPT_REGISTRY_PINS={"master_preprompt": "1.1.0"}` to `.env` (via `shared/env_io`).
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

Prints a unified diff to stdout.  The web app's Settings → Prompts screen renders a diff inline.

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

The four secret keys are masked in the web app's settings screens and raise `SecretWriteError` if a write
is attempted through the `shared/env_io` path (CONSTRAINT #3).  Edit them by hand in `.env` only.

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `get()` always returns baseline | `PROMPT_REGISTRY_ENABLED=false` or no `PROMPT_REGISTRY_URL` | Set both in `.env`, run `sync` |
| Signature verification failed | `PROMPT_REGISTRY_SIGNING_KEY` mismatch | Confirm the key matches the one used at publish time |
| `publish` exits non-zero immediately | `PROMPT_REGISTRY_PUBLISH_TOKEN` absent | Set the token in `.env` on the author machine only |
| `rollback` says "fewer than 2 versions" | Only one version in cache | Run `sync` to fetch the remote manifest, then retry |
| Settings → Prompts shows all sources as "baseline" | Registry disabled or never synced | Enable registry and run Sync from Settings → Prompts (or `python -m prompt_registry sync`) |

---

## AI Insights & AI Control Center

The web app covers the platform's AI commentary/research features in two places. Both are
strictly **advisory and operator-triggered** — no AI output here ever places or modifies an
order.

### Per-symbol AI reads (Symbol detail page)

Each symbol's detail page (`/symbol/:ticker`) has on-demand AI sections:

| Section | What it does | Requires |
|---|---|---|
| Opal research brief | Qualitative thesis/catalysts/risk-factors brief grounded in real news + earnings-calendar data (never invents numbers) | `OPAL_RESEARCH_ENABLED=true` + `OPENAI_API_KEY` (or `GEMINI_API_KEY` if routed to Gemini) |
| Claude analyst note | Plain-English rationale for the current Action Signal | `LLM_COMMENTARY_ENABLED=true` + `ANTHROPIC_API_KEY` |
| Gemini chart pattern read | Sends a price chart to Gemini Vision and returns a structured pattern/trend/support-resistance read | `LLM_COMMENTARY_ENABLED=true` + `GEMINI_API_KEY` |

Nothing calls out to an AI provider until you click. The old desktop app's Claude-vs-Gemini
disagreement view has no web app equivalent yet.

### AI Control Center (Settings → AI)

One toggle per AI master switch (Claude analyst rationale, Gemini alert commentary, Gemini
chart vision, the Gravity AI audit runner, Opal research), with a provider selector where a
capability can be routed to more than one provider. Writes go to `.env`. Run the Gravity
audit from the **Console** screen; set the pipeline schedule on Settings → Data & Automation.

Provider API keys (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`)
are secret-only and can never be set from the web app — edit `.env` directly.

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
each agent, and `docs/plans/OPAL_BUILD_SPEC.md` for Opal's design record.

### Standing rule

Every AI action in the web app is either a button click or an operator-started,
operator-stoppable loop. Nothing here calls another AI agent, watches a PR, or
re-invokes itself automatically — the same "no automatic AI invocation" rule
that applies to Claude Code sessions on this repo applies to the platform's
own AI features.

## 17. Report Library

The web app's **Report Library** screen (`/operations/reports`) is a single place to browse and read every report
file the platform produces. It is read-only and file-backed — it renders files
that already exist on disk and never calls the broker or fetches live market
data. Think of it as a document viewer over the pipeline's output folder.

### What it surfaces

- **Daily HTML report.** The primary end-of-cycle report (holdings, P&L, action
  signals, rationale). This file is regenerated on **every** advisory refresh
  cycle, so it is always current — whatever the most recent run produced is what
  you see here.
- **Daily briefings.** One human-readable briefing per day. Older briefings stay
  available so you can look back at a previous day's read. (The old desktop app's
  "generate today's briefing" button has no web app equivalent; run
  `python scripts/daily_briefing.py` instead.)
- **Orchestrator dashboards.** The full-pipeline daily report and its
  volatility-band chart. Unlike the daily HTML report, these only refresh when
  you kick off a **manual full-orchestrator run** (from the Pipeline screen). If
  they look out of date, that just means no full-orchestrator run has happened
  since — run one to refresh them.
- **Validation reports.** Per-strategy validation output (PBO / DSR / Sharpe /
  Max Drawdown gate verdicts and the walk-forward/CPCV detail). A validation
  report appears here only **once a strategy has been through the validation
  harness** — until then there is nothing to show for that strategy.

### Viewing and downloading

Each file can be **viewed inline** (an opt-in "View inline" toggle) or
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

The desktop app's Validation Lab tab was deleted in 2026-09. In the web app:

### Running a validation

Use the **Commands** screen (`/commands`) to build a
`python -m scripts.refresh_validations --strategies ...` run, or press
**🧪 Bulk Validate All Strategies** to start with every registered strategy selected.
Executing from the web app needs `COMMAND_EXECUTION_ENABLED=true`; otherwise the screen
composes the command for you to paste into a terminal. The harness never calls the broker
or submits any order.

### Watching the run

A launched command runs as a background job. The top status bar's "Jobs" chip shows it
from any screen, and the launching screen streams its log.

### Reading the results

The run writes `reports/*_validation_summary.json`. The **Strategy Health** screen shows
per-pilot validation results, and the **Report Library** lists each strategy's validation
summary and HTML report. Each summary has a **deployable** verdict plus the four standard
gate values — **PBO**, **DSR**, **Sharpe**, and **Max Drawdown** — judged against the
thresholds in `validation.thresholds` (PBO below its cap, DSR and net Sharpe above their
floors, Max Drawdown below its limit). A strategy only counts toward the preflight `validation_reports`
check once it is deployable **and** its report is less than 30 days old.

---

## Sentiment Dynamics Tab

The web app's **Sentiment Dynamics** screen (`/sentiment`) is a per-symbol, on-demand view of two
independent sentiment signals plus a real GJR-GARCH asymmetric-volatility
computation. It never fabricates a number when data isn't available — every
metric is either a genuine computed/fetched value or an honest "—" with an
explanatory note.

### Two distinct signals

1. **News Catalyst Sentiment** — the same `news_sentiment` field the
   always-on pipeline's `NewsCatalystSignal` already computes from recent
   FMP headlines (FinBERT neural score, or a keyword-lexicon fallback)
   and persists into `output/state_snapshot.json`. A symbol with no scored
   news shows an honest empty state rather than a fabricated neutral score.
2. **Antigravity Agent Sentiment + GJR-GARCH Volatility** — an on-demand call
   to `sentiment_risk_engine.SentimentRiskEngine.get_live_sentiment()`, which
   asks a Google Antigravity LLM agent (`engine/agent_sentiment.py`) to score
   recent news headlines for sentiment, intensity, and source credibility,
   plus a real per-request GJR-GARCH(1,1,1) fit
   (`compute_asymmetric_volatility()`) over the symbol's price history to
   measure the asymmetric leverage effect and shock persistence
   (α + β + γ/2). These are independent computations shown together, not one
   signal — don't conflate them with News Catalyst Sentiment above.

### When the Antigravity agent is unavailable

The agent requires the `google.antigravity` SDK to be installed and a
`GEMINI_API_KEY` set in `.env`. When either is missing, or the live call
fails, the screen shows an honest **"unavailable"** note and blanks (`—`) for
LLM Sentiment / Intensity / Credibility — it never falls back to a
fabricated placeholder number. The GJR-GARCH Volatility Persistence metric is
computed independently of the agent, so it can still show a real value even
when the agent itself is unavailable (as long as there are at least 100 daily
return observations to fit on).

The screen reads the `GET /metrics/sentiment/{symbol}` endpoint in
`api/metrics_api.py`, backed by the same `SentimentRiskEngine` methods.

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

It is deliberately **not wired into the web app or any orchestrator** — you launch it yourself, on demand, when you want a read-only
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

`STATE_API_TOKEN` is a **secret** (masked in the web app, never writable from it).
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

---

## 22. Using the Retrospective Learning Loop

The **Retrospective Learning Loop** provides systematic post-mortem trade autopsies and pattern intelligence across your paper-trading history. Rather than simply logging trade profit and loss, the learning loop captures point-in-time quantitative context at order entry (model conviction, signals, macro indicators, and market regime) and evaluates realized outcomes against excursion bounds (MAE/MFE) and strategy calibration expectations.

### Accessing the Retrospective Journal

You can explore retrospective autopsies and cohort analytics through multiple interfaces:

1. **Pilots PWA (Web Application)**:
   - Navigate to **Trading > Retrospective Journal** (`/retrospective`) via the desktop sidebar or mobile "More" menu.
   - Alternatively, open **Trading > Trading Hub** and select the "Retrospective Journal" card.
   - From the **Paper Broker** (`/paper-broker`) screen, click the "View Retrospective Journal →" link above the Closed Trades table or click the **Autopsy →** button on any closed trade row.
2. **REST API**:
   - `GET /pilots/paper-broker/trades/{trade_id}/retrospective`: Fetch full post-mortem autopsy for a single trade.
   - `GET /pilots/paper-broker/retrospective/insights?limit=100&symbol=&strategy_id=`: Retrieve batch pattern intelligence and cohort breakdowns.
   - `GET /pilots/paper-broker/bridge/metrics`: Retrieve synchronization health and completeness metrics.

### Trade Journal & Excursion Autopsy

Tab 1 of the Retrospective Journal lists closed paper trades with multi-axis filtering (by symbol, strategy, and provenance). Clicking on any trade opens the **Retrospective Detail Modal**, which brings together:

- **Execution Realization**: Realized P&L ($ and %), fill prices, slippage against target order price, execution commissions, and holding duration.
- **Entry Decision Context**: What the engine saw at the moment the position opened — macro regime (e.g., RISK ON, NEUTRAL), key macro indicators (VIX, yield curve spread, high yield OAS), top active signal modules, and model conviction score ($[0.0, 1.0]$).
- **Excursion Analysis**:
  - **Maximum Adverse Excursion (MAE)**: The deepest unrealized drawdown experienced while the trade was open. Helps assess whether stop-losses were set too wide or entries were poorly timed.
  - **Maximum Favorable Excursion (MFE)**: The peak unrealized profit achieved during the trade life.
  - **Edge Ratio**: Ratio of favorable to adverse excursion ($\text{MFE} / \text{MAE}$). An edge ratio $> 1.0$ indicates positive intraday directional edge.
  - **Holding Period Efficiency**: Realized P&L relative to MFE, measuring how effectively profit was captured before exit.
- **Model Calibration Placement**: Compares the entry conviction score to the strategy's historical backtest validation reports (`ValidationReport`), placing the trade in its conviction decile bin to evaluate whether higher-conviction bets produce higher realized win rates.
- **Deterministic Narrative Autopsy**: A rule-based post-mortem analysis generated without external LLM latency or hallucination risks, structured into Execution Summary, Conviction Calibration, Excursion Analysis, and Actionable Lessons.

### Strict Cohort Isolation (Pattern Insights)

Tab 2 of the Retrospective Journal presents aggregated cohort analytics (`generate_batch_retrospective_insights`). To guarantee mathematical honesty and prevent distorted performance statistics, trades are strictly partitioned into three mutually exclusive cohorts:

1. **Automated Cohort**: Algorithmic trades initiated by platform strategies with forward-captured entry snapshots.
   - Evaluated for algorithmic win rate, profit factor, mean edge ratio, mean MAE/MFE, and Brier score calibration quality.
   - Includes per-strategy breakdown tables isolating individual Pilot performance.
2. **Manual Cohort**: Discretionary trades submitted manually by the operator.
   - Tracked with independent win rate and profit factor metrics.
   - Conviction calibration is explicitly flagged as **"Not Applicable"** because manual entries do not carry model-generated probability distributions.
3. **Unrecorded Cohort**: Historical trades executed prior to the rollout of retrospective snapshot capture.
   - Isolated to prevent missing historical context from poisoning current algorithmic samples.

**Quant Integrity Invariant**: The platform **NEVER** blends automated and manual trades into unified top-level metrics. You will never see a combined "portfolio win rate" or "overall profit factor" that conflates model-driven signals with operator discretion.

### Honest Fallback States (Anti-Fabrication Policy)

In accordance with platform integrity constraints, the retrospective engine never hallucinates, estimates, or synthesizes missing historical data:

| State / Display Badge | Condition | System Behavior |
|-----------------------|-----------|-----------------|
| `"Snapshot not captured at entry for this trade"` | Trade executed before retrospective capture was enabled, or manual entry without an active engine cycle. | `entry_context` is set to `None`. Decision context panel displays honest missing notice. No pseudo-snapshot is fabricated. |
| `"Evaluation data unavailable"` | Trade excursion cannot be computed due to missing market price bars or unbridged state. | `mae`, `mfe`, and `edge_ratio` are set to `null` (`None`). UI displays `"Unavailable"`. |
| `"Model calibration not applicable"` | Discretionary manual trade or uncataloged/custom strategy ID. | Calibration status is `"not_applicable"`. No theoretical curve is invented. |
| `"Insufficient sample in conviction bin (n < 5)"` | Fewer than 5 closed trades recorded in the given conviction bin ($[0.0, 0.2)$, etc.). | Calibration status displays `"insufficient_sample"`. The system refrains from claiming statistical significance. |
| `"Bridge error"` | Paper trade failed synchronization into the analytical transactions store. | Error diagnostic message and failure timestamp are exposed in the modal's diagnostics drawer. |

### Synchronization Bridge & Completeness Telemetry

When paper trades close in `PaperAccountStore`, an atomic database bridge records them into `TransactionsStore` for unified portfolio evaluation.
- **Fail-Open Isolation**: Order fills and closures always succeed. If an error occurs during bridge synchronization, the trade is committed with `bridge_status="failed"` and the error details are recorded for inspection.
- **Completeness Metrics**: The banner at the top of the Retrospective Journal monitors `getBridgeReliability()`, displaying the percentage of successfully bridged trades, total synchronized count, and failure count.


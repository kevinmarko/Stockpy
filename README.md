# InvestYo Quant Platform ("Stock Dashboard Py")

An automated quantitative analysis pipeline: fetches market/macro data, computes
technical & fundamental indicators, runs multi-horizon forecasts, backtests
strategies, persists signals to SQLite, and publishes results to Google Sheets /
an HTML report.

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
| `RH_USERNAME` | Optional | Robinhood read-only snapshot (held symbols always included) |
| `RH_PASSWORD` | Optional | — |
| `RH_MFA_SECRET` | Optional | Base32 TOTP secret (Robinhood → Settings → Security → Authenticator) |
| `ALPACA_API_KEY` | Optional | Broker execution |
| `ALPACA_SECRET_KEY` | Optional | — |
| `ALPACA_PAPER` | Optional | `true` (default) = paper trading |
| `FINNHUB_API_KEY` | Optional | Better fundamental data; degrades to yfinance when absent |
| `NTFY_TOPIC` | Optional | Phone push alerts via ntfy.sh — set a random string, subscribe in the ntfy app |
| `WATCHLIST` | Optional | Comma-separated tickers (alternative: `watchlist.txt`, one per line) |
| `DISCORD_WEBHOOK_URL` | Optional | Discord channel alerts |
| `SLACK_WEBHOOK_URL` | Optional | Slack channel alerts |

All other keys (sizing parameters, risk-gate thresholds, financial constants) have
safe defaults — see the full list in [`.env.example`](.env.example).

---

## Launching

```bash
./launch.command                          # double-click from Finder (recommended)

.venv/bin/python3 main.py                 # single advisory cycle
.venv/bin/python3 main.py --interval 60   # refresh every 60 s
.venv/bin/python3 main_orchestrator.py    # async orchestrator (HTML report + broker)
```

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
streamlit run observability/dashboard.py    # live observability dashboard
python scripts/preflight_check.py           # pre-live readiness gate (exit 0 = pass)
python -m execution.kill_switch --status    # check / toggle the global kill switch
```

---

## Configuration

All runtime configuration is centralized in [`settings.py`](settings.py) and loaded
from a local `.env` file (never committed). The most important key is `FRED_API_KEY`.

## Running

```bash
python3 main.py                  # advisory orchestrator → Google Sheets
python3 main_orchestrator.py     # async master orchestrator → HTML report
pytest                           # run the test suite
```

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


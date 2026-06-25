# InvestYo Quant Platform ("Stock Dashboard Py")

An automated quantitative analysis pipeline: fetches market/macro data, computes
technical & fundamental indicators, runs multi-horizon forecasts, backtests
strategies, persists signals to SQLite, and publishes results to Google Sheets /
an HTML report.

## Setup

```bash
./setup.sh                 # creates .venv (Python 3.12), installs requirements.txt
source .venv/bin/activate
cp .env.example .env       # then fill in your secrets (see below)
```

## Configuration

All runtime configuration — secrets, financial constants, and output paths — is
centralized in [`settings.py`](settings.py) and loaded from environment variables
or a local `.env` file (never committed). Copy `.env.example` to `.env` and fill
in values. The most important key is `FRED_API_KEY`.

## Running

```bash
python3 main.py                  # legacy sync orchestrator -> Google Sheets
python3 main_orchestrator.py     # async master orchestrator -> HTML report
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


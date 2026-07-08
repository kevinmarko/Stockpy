"""
scripts/backtest_sector_configs.py
===================================
Orchestration CLI for the empirical per-sector forecast-model backtest.

Wires together (in scope for other agents, not this file):
  * ``validation/sector_forecast_types.py``  -- frozen ``BacktestConfig`` /
    ``CellResult`` contract.
  * ``validation/sector_forecast_backtest.py`` -- ``run_sector_backtest()``,
    the expanding-window walk-forward runner.
  * ``validation/sector_config_io.py`` -- ``derive_sector_configs()`` /
    ``build_artifact()`` / ``write_artifact()``.

and produces the committed ``forecasting/sector_configs.json`` artifact that
``ForecastingEngine._load_sector_configs()`` overlays on top of the hardcoded
``_DEFAULT_SECTOR_CONFIGS`` heuristic at process start.

Two modes
---------
``--offline`` (the only mode exercised by this repo's automated test suite):
  Synthesizes a small, deterministic (seeded) random-walk-with-drift OHLCV
  price history per symbol in the ticker->sector population -- no network, no
  ``HistoricalStore``/``DataEngine`` involvement. Mirrors the tz-naive
  ``DatetimeIndex`` + ``[Open, High, Low, Close, Volume]`` contract used
  throughout this codebase (see ``data_engine.MockDataEngine.fetch_technical_raw``).

Network mode (default, no ``--offline``):
  Attempts to pull real bars via this repo's existing data layer
  (``data.historical_store.HistoricalStore.get_bars`` when available, else
  falling back to a plain ``data_engine.DataEngine.fetch_technical_raw``
  call). This sandbox has no real network access, so this path is wired
  plausibly but is not exercised by the automated tests -- it must simply not
  crash on import.

Follows this repo's established CLI convention (see
``scripts/preflight_check.py``): repo-root ``sys.path`` bootstrap via
``_REPO_ROOT``, ``argparse``, ``def main(argv=None) -> int``,
``if __name__ == "__main__": sys.exit(main())``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from validation.sector_forecast_types import BacktestConfig  # noqa: E402
from validation.sector_forecast_backtest import run_sector_backtest  # noqa: E402
from validation.sector_config_io import (  # noqa: E402
    build_artifact,
    derive_sector_configs,
    write_artifact,
)

logger = logging.getLogger("backtest_sector_configs")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Single source of truth for the hardcoded fallback: prefer importing from
# forecasting_engine.py (Agent E's landed constant) so there is exactly one
# definition. Only fall back to a local literal copy if the import fails
# (e.g. this script runs before Agent E's change lands) -- the local copy
# below is an EXACT match of that constant, kept in sync deliberately.
# ---------------------------------------------------------------------------
try:
    from forecasting_engine import _DEFAULT_SECTOR_CONFIGS
except Exception:  # pragma: no cover -- only hit if forecasting_engine.py lacks the constant
    logger.warning(
        "Could not import _DEFAULT_SECTOR_CONFIGS from forecasting_engine; "
        "using a local literal fallback copy (keep this in sync manually)."
    )
    _DEFAULT_SECTOR_CONFIGS: Dict[str, Dict[str, object]] = {
        "Technology": {"days": 30, "model": "MC"},
        "Consumer Cyclical": {"days": 30, "model": "MC"},
        "Communication Services": {"days": 30, "model": "MC"},
        "Healthcare": {"days": 90, "model": "MC"},
        "Energy": {"days": 60, "model": "MC"},
        "Financial Services": {"days": 60, "model": "ARIMA"},
        "Industrials": {"days": 60, "model": "ARIMA"},
        "Real Estate": {"days": 90, "model": "HW"},
        "Utilities": {"days": 90, "model": "ARIMA"},
        "Consumer Defensive": {"days": 90, "model": "ARIMA"},
        "Basic Materials": {"days": 60, "model": "ARIMA"},
    }

DEFAULT_TICKER_SECTORS_PATH = _REPO_ROOT / "forecasting" / "data" / "ticker_sectors.csv"
DEFAULT_OUTPUT_PATH = _REPO_ROOT / "forecasting" / "sector_configs.json"

# Offline synthetic price path length -- generous history for a 750-day
# lookback + multiple 21-day-stepped anchors at up to a 90-day horizon.
_OFFLINE_N_BARS = 800


def load_ticker_sectors(path: Path) -> Dict[str, str]:
    """Read a ``symbol,sector`` CSV into a ``{symbol: sector}`` dict.

    Never raises: a missing/unreadable/malformed file logs an error and
    returns an empty dict (the caller then reports zero results and exits
    non-zero, never fabricating a population).
    """
    try:
        df = pd.read_csv(path)
        if "symbol" not in df.columns or "sector" not in df.columns:
            logger.error(
                "%s is missing required 'symbol'/'sector' columns (found: %s)",
                path, list(df.columns),
            )
            return {}
        out: Dict[str, str] = {}
        for _, row in df.iterrows():
            symbol = str(row["symbol"]).strip().upper()
            sector = str(row["sector"]).strip()
            if symbol and sector and sector.lower() != "nan":
                out[symbol] = sector
        return out
    except Exception as exc:
        logger.error("Failed to load ticker->sector map from %s: %s", path, exc)
        return {}


def synthesize_offline_price_data(
    symbols: List[str],
    *,
    n_bars: int = _OFFLINE_N_BARS,
    seed: int = 42,
) -> Dict[str, pd.DataFrame]:
    """Deterministic random-walk-with-drift OHLCV history, one DataFrame per symbol.

    Mirrors ``data_engine.MockDataEngine.fetch_technical_raw``'s shape
    contract (tz-naive ``DatetimeIndex``, columns
    ``[Open, High, Low, Close, Volume]``) but generates enough bars
    (default 800) for a meaningful expanding-window walk-forward at the
    default ``BacktestConfig`` (750-day lookback, up to 90-day horizon).
    Seeded per-symbol (via a derived child seed) so the whole run is
    reproducible given the top-level ``--seed``, independent of dict/set
    iteration order.
    """
    price_data: Dict[str, pd.DataFrame] = {}
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n_bars, freq="B")

    for i, symbol in enumerate(symbols):
        # Derive a stable per-symbol seed from the base seed + a hash of the
        # symbol so results don't depend on iteration order and each symbol
        # gets a distinct (but deterministic) price path.
        child_seed = (seed + (hash(symbol) % 100_000) + i) % (2**32 - 1)
        rng = np.random.RandomState(child_seed)

        mu_daily = 0.0003  # small positive drift
        sigma_daily = 0.015
        log_returns = rng.normal(mu_daily, sigma_daily, n_bars)
        start_price = 50.0 + (hash(symbol) % 200)
        close = start_price * np.exp(np.cumsum(log_returns))

        high = close * (1.0 + np.abs(rng.normal(0, 0.003, n_bars)))
        low = close * (1.0 - np.abs(rng.normal(0, 0.003, n_bars)))
        open_ = close * (1.0 + rng.normal(0, 0.002, n_bars))
        volume = rng.randint(1_000_000, 5_000_000, n_bars)

        df = pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
            index=dates,
        )
        price_data[symbol] = df

    return price_data


def load_network_price_data(symbols: List[str], lookback_days: int) -> Dict[str, pd.DataFrame]:
    """Best-effort real-bar fetch via this repo's existing data layer.

    Prefers ``data.historical_store.HistoricalStore.get_bars`` (DB-cached,
    incremental top-up); falls back to a plain
    ``data_engine.DataEngine.fetch_technical_raw`` call if the store is
    unavailable or errors. Dead-letter resilient per symbol -- one bad
    ticker is logged and skipped, never aborting the whole population fetch.

    Not exercised by this repo's automated test suite (no real network
    access in this sandbox) -- this function exists so network mode is
    wired plausibly and importable, not to be proven correct here.
    """
    price_data: Dict[str, pd.DataFrame] = {}
    try:
        from data.historical_store import HistoricalStore

        store = HistoricalStore()
        for symbol in symbols:
            try:
                df = store.get_bars(symbol, lookback_days=lookback_days)
                if df is not None and not df.empty:
                    price_data[symbol] = df
            except Exception as exc:
                logger.warning("HistoricalStore.get_bars(%s) failed: %s", symbol, exc)
        if price_data:
            return price_data
        logger.warning("HistoricalStore returned no bars for any symbol; trying DataEngine.")
    except Exception as exc:
        logger.warning("HistoricalStore unavailable (%s); falling back to DataEngine.", exc)

    try:
        from data_engine import DataEngine

        engine = DataEngine()
        raw = engine.fetch_technical_raw(symbols)
        for symbol, df in raw.items():
            if df is not None and not df.empty:
                price_data[symbol] = df
    except Exception as exc:
        logger.error("DataEngine.fetch_technical_raw failed: %s", exc)

    return price_data


def _print_summary(derived: Dict[str, Dict[str, object]], results) -> None:
    print("\nEmpirically-derived per-sector forecast config:")
    print("-" * 60)
    for sector in sorted(derived.keys()):
        entry = derived[sector]
        matching = [
            c for c in results
            if c.sector == sector and c.model == entry["model"] and c.horizon == entry["days"]
        ]
        if matching:
            c = matching[0]
            print(
                f"  {sector:<24s} -> model={entry['model']:<6s} days={entry['days']:<3d} "
                f"(mase={c.mase:.4f}, rmse={c.rmse:.4f}, n_forecasts={c.n_forecasts}, "
                f"n_symbols={c.n_symbols})"
            )
        else:
            print(
                f"  {sector:<24s} -> model={entry['model']:<6s} days={entry['days']:<3d} "
                f"(fallback -- no qualifying backtest cell)"
            )
    print("-" * 60)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns 0 on success, non-zero if zero results were produced.

    Parameters
    ----------
    argv:
        Argument list. ``None`` uses ``sys.argv[1:]``. Pass an explicit list
        to drive this from tests without spawning a subprocess.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run the empirical walk-forward backtest for the per-sector "
            "forecast-model heuristic and write the resulting artifact."
        )
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "Use deterministic synthetic price data instead of any real data "
            "source -- no network, no HistoricalStore/DataEngine involvement. "
            "This is the only mode exercised by the automated test suite."
        ),
    )
    parser.add_argument(
        "--ticker-sectors",
        default=str(DEFAULT_TICKER_SECTORS_PATH),
        help="Path to the symbol,sector CSV (default: forecasting/data/ticker_sectors.csv).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path to write the resulting JSON artifact (default: forecasting/sector_configs.json).",
    )
    parser.add_argument(
        "--lookback-days", type=int, default=None,
        help="Override BacktestConfig.lookback_days (default: 750).",
    )
    parser.add_argument(
        "--min-train-bars", type=int, default=None,
        help="Override BacktestConfig.min_train_bars (default: 120).",
    )
    parser.add_argument(
        "--step-days", type=int, default=None,
        help="Override BacktestConfig.step_days (default: 21).",
    )
    parser.add_argument(
        "--embargo-days", type=int, default=None,
        help="Override BacktestConfig.embargo_days (default: 5).",
    )
    parser.add_argument(
        "--min-forecasts", type=int, default=30,
        help="Minimum n_forecasts for a backtest cell to qualify (default: 30).",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Seed for numpy's global RNG (MC uses it via run_monte_carlo) and "
             "for --offline synthetic price generation (default: 42).",
    )
    args = parser.parse_args(argv)

    np.random.seed(args.seed)

    config_kwargs = {}
    if args.lookback_days is not None:
        config_kwargs["lookback_days"] = args.lookback_days
    if args.min_train_bars is not None:
        config_kwargs["min_train_bars"] = args.min_train_bars
    if args.step_days is not None:
        config_kwargs["step_days"] = args.step_days
    if args.embargo_days is not None:
        config_kwargs["embargo_days"] = args.embargo_days
    config = BacktestConfig(**config_kwargs)

    ticker_sectors_path = Path(args.ticker_sectors)
    ticker_sectors = load_ticker_sectors(ticker_sectors_path)
    if not ticker_sectors:
        logger.error("No ticker->sector mappings loaded from %s -- aborting.", ticker_sectors_path)
        return 1

    symbols = sorted(ticker_sectors.keys())
    logger.info(
        "Loaded %d symbols across %d sectors from %s",
        len(symbols), len(set(ticker_sectors.values())), ticker_sectors_path,
    )

    if args.offline:
        logger.info("Running in --offline mode: synthesizing deterministic price data.")
        price_data = synthesize_offline_price_data(symbols, seed=args.seed)
        population_source = "offline_synthetic"
    else:
        logger.info("Running in network mode: attempting real bar fetch.")
        price_data = load_network_price_data(symbols, lookback_days=config.lookback_days)
        population_source = "network"

    if not price_data:
        logger.error("No price data available for any symbol -- aborting.")
        return 1

    from forecasting_engine import ForecastingEngine

    engine = ForecastingEngine()

    logger.info(
        "Running sector backtest over %d symbols, models=%s, horizons=%s ...",
        len(price_data), config.models, config.horizons,
    )
    results = run_sector_backtest(price_data, ticker_sectors, engine, config=config)

    if not results:
        logger.error("Backtest produced zero CellResults -- aborting.")
        return 1

    derived = derive_sector_configs(results, _DEFAULT_SECTOR_CONFIGS, min_forecasts=args.min_forecasts)

    population_meta = {
        "population_source": population_source,
        "n_symbols": len(symbols),
        "n_sectors": len(set(ticker_sectors.values())),
        "ticker_sectors_path": str(ticker_sectors_path),
        "seed": args.seed,
    }
    artifact = build_artifact(results, derived, config, population_meta)

    output_path = Path(args.output)
    write_artifact(output_path, artifact)
    logger.info("Wrote artifact to %s", output_path)

    _print_summary(derived, results)

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
InvestYo Quant Platform - Multi-Horizon Forecast Backfill CLI
============================================================
Runs the multi-horizon (10, 30, 60, 90d) forecast backfilling & meta-labeling
engine for TSMOM and CSMOM across a selected stock universe.

Usage
-----
    python scripts/run_forecast_backfill.py
    python scripts/run_forecast_backfill.py --tickers AAPL,MSFT,NVDA,JPM --use-fmp
    python scripts/run_forecast_backfill.py --horizons 10,30,60,90 --start 2015-01-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Repo-root import shim so script runs directly
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading -- must run before any third-party/project
# import below (see scripts/_bootstrap.py's module docstring for why).
from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from ml.backfill.GlobalBackfillEngine import GlobalBackfillEngine
from settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Scripts.RunForecastBackfill")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Forecast Backfill & Meta-Labeling Engine.")
    parser.add_argument(
        "--tickers",
        type=str,
        default="",
        help="Comma-separated list of stock tickers (default: settings.DEFAULT_TICKERS).",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="",
        help=(
            "Start date YYYY-MM-DD (default: settings.FORECAST_BACKFILL_LOOKBACK_YEARS "
            "years back from --end/today)."
        ),
    )
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="End date YYYY-MM-DD (default: today).",
    )
    parser.add_argument(
        "--horizons",
        type=str,
        default="",
        help="Comma-separated list of forecast horizons in days (e.g. 10,30,60,90).",
    )
    parser.add_argument(
        "--use-fmp",
        action="store_true",
        default=True,
        help="Use Financial Modeling Prep (FMP) for data sourcing (default: True).",
    )
    parser.add_argument(
        "--no-fmp",
        action="store_false",
        dest="use_fmp",
        help="Disable FMP data provider and use CompositeProvider/Store fallback.",
    )
    parser.add_argument(
        "--classifier",
        type=str,
        default="",
        help="Classifier type ('random_forest' or 'lightgbm').",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="agentic_forecast_backfill.csv",
        help="CSV output filename inside output/ directory.",
    )
    return parser.parse_args()


def main() -> int:
    import asyncio
    args = parse_args()

    tickers_list = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None

    logger.info("Initializing GlobalBackfillEngine...")
    from ml.backfill.registry import backfill_engine
    engine = backfill_engine

    async def _print_status(task_id: str, status: str, progress: int, message: str):
        logger.info(f"[{progress}%] {message}")

    try:
        results = asyncio.run(engine.run_full_system_backfill(
            task_id="CLI_RUN", 
            status_callback=_print_status,
            tickers=tickers_list,
            start_date=args.start or None,
            end_date=args.end or None,
            use_fmp=args.use_fmp,
        ))

        print("\n" + "=" * 60)
        print("FORECAST BACKFILL & META-LABELING RESULTS SUMMARY")
        print("=" * 60)
        for strategy_id, model_metrics in results.items():
            for m in model_metrics:
                model_key = f"{strategy_id}_{m['horizon']}d"
                print(f"  Model: {model_key:<15} Accuracy: {m['accuracy']:.4f} | AUC: {m['roc_auc']:.4f} | Train Samples: {m['train_n']}")
        print("=" * 60)
        print("Backfill complete! Exported summary to agentic_forecast_summary.json.")
        return 0
    except Exception as exc:
        logger.error("Forecast backfill failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

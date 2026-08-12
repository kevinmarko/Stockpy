"""Standalone child process for tests/test_forecast_backfill.py's
partial-export checkpointing coverage.

Builds a small, fully offline ``AgenticForecastBackfiller`` (synthetic
price/volume data assigned directly, mirroring that test file's own
``_synthetic_engine`` helper -- no ``step_1_fetch_data()``, no network),
points ``settings.OUTPUT_DIR`` at the directory given via ``--output-dir``,
then runs the real pipeline (steps 2-5) so the parent test process can
SIGKILL it mid-flight and inspect what
``ml.forecast_backfill.AgenticForecastBackfiller._write_partial_export``
actually left on disk -- proving the checkpoint survives a genuine hard
kill, not just a clean Python-level early return.

Two modes, selected via ``--mode``:
  mid_step5     (default) -- runs steps 2-5. Many horizons (8) x two
                trainable strategies = up to 16 (model_type, horizon)
                combos, with a deliberate ``--sleep-per-combo`` pause
                (via ``on_combo_trained``) after each one finishes -- so the
                parent has a real, generous window to observe
                ``agentic_forecast_backfill.partial.csv`` appear on disk
                and then kill this process mid-loop, well before every
                combo has trained.
  before_step5  -- runs steps 2-4 ONLY, then sleeps indefinitely without
                ever calling step 5 (so no combo, ever, trains) -- proves a
                kill landing here (steps 1-4) must leave NO partial files
                at all, the honest "nothing was saved" case.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Launched as a bare script path (not `-m`), from an unspecified cwd -- the
# repo root (two levels up from tests/fixtures/) must be on sys.path
# explicitly, or `import settings` / `from ml.forecast_backfill import ...`
# below fail with ModuleNotFoundError regardless of the parent test's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd


def _synthetic_engine(tickers, horizons, n_days: int = 400, seed: int = 42):
    from ml.forecast_backfill import AgenticForecastBackfiller

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=n_days)
    prices = pd.DataFrame(
        {
            t: 100.0 * np.cumprod(1.0 + rng.normal(0.0002 * (i + 1), 0.01, n_days))
            for i, t in enumerate(tickers)
        },
        index=dates,
    )
    volumes = pd.DataFrame({t: 1_000_000.0 for t in tickers}, index=dates)
    engine = AgenticForecastBackfiller(
        tickers=list(tickers),
        horizons=horizons,
        use_fmp=False,
        strategy_ids=["timeseries_momentum", "cross_sectional_momentum"],
        n_estimators=10,
        max_depth=3,
    )
    engine.prices = prices
    engine.volumes = volumes
    return engine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--mode", choices=["mid_step5", "before_step5"], default="mid_step5")
    parser.add_argument("--sleep-per-combo", type=float, default=0.5)
    args = parser.parse_args()

    from settings import settings

    settings.OUTPUT_DIR = args.output_dir

    tickers = ["AAA", "BBB", "CCC", "DDD"]
    horizons = [5, 10, 15, 20, 25, 30, 35, 40]
    engine = _synthetic_engine(tickers, horizons)

    engine.step_2_calculate_technical_features()
    engine.step_3_generate_primary_signals()
    engine.step_4_create_meta_targets()

    if args.mode == "before_step5":
        # Never reach step 5 -- no combo ever trains, so
        # _write_partial_export is never called. Sleeps indefinitely; the
        # parent kills this process well before the sleep ends.
        time.sleep(3600)
        return 0

    def _on_combo_trained(model_key: str, metrics_so_far: dict) -> None:
        time.sleep(args.sleep_per_combo)

    engine.on_combo_trained = _on_combo_trained
    engine.step_5_backtrain_meta_labelers()
    return 0


if __name__ == "__main__":
    sys.exit(main())

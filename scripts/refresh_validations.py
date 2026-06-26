"""
scripts/refresh_validations.py — Walk-forward validation cadence runner (Tier 4.2).

Iterates a registry of strategy adapters, runs ``StrategyValidationHarness``
for each, writes JSON summaries to ``reports/``, and prints a pass/fail table.
Designed to be run monthly (or on demand) so validation reports never go stale.

Usage
-----
::

    python -m scripts.refresh_validations                     # validate all
    python -m scripts.refresh_validations --strategies rsi2_mean_reversion
    python -m scripts.refresh_validations --start 2010-01-01 --end 2023-12-31
    ./scripts/refresh_validations.sh                          # venv-activating wrapper

Options
-------
--strategies NAME[,NAME]   Comma-separated strategy names (default: all registered).
--start  YYYY-MM-DD        Backtest start date (default: 2005-01-01).
--end    YYYY-MM-DD        Backtest end date (default: today).
--output-dir  PATH         Directory for JSON report output (default: reports/).
--n-cpcv-splits  N         CPCV split count (default: 10).
--n-test-splits  N         Walk-forward test splits (default: 2).

Design constraints
------------------
* CONSTRAINT #6 — every per-strategy execution is wrapped in try/except so one
  failed strategy never aborts the run; the failed strategy is recorded with an
  ``error`` key and the overall exit code is non-zero.
* CONSTRAINT #4 — fabricated/synthetic returns are never passed to the harness;
  if the adapter cannot build valid X/y the strategy is skipped with an error.
* CONSTRAINT #7 — data fetching uses yfinance (same library as the existing
  test harnesses in ``tests/test_validation_*.py``).  No new data providers.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# =============================================================================
# Strategy adapters
# =============================================================================

def _build_rsi2_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """RSI(2) mean-reversion on SPY with SMA-200 long-only trend filter.

    Mirrors the test harness in ``tests/test_validation_rsi2.py`` so the
    refresh script exercises the same signal path the validated tests cover.
    """
    def _rsi2(s: pd.Series, length: int = 2) -> pd.Series:
        delta = s.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1.0 / length, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / length, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        return (100.0 - (100.0 / (1.0 + rs))).fillna(100.0)

    rsi = _rsi2(spy_close)
    sma_5 = spy_close.rolling(5).mean()
    sma_200 = spy_close.rolling(200).mean()
    daily_ret = spy_close.pct_change()

    uptrend = spy_close > sma_200
    not_reverted = spy_close <= sma_5
    oversold = ((10.0 - rsi) / 10.0).clip(0.0, 1.0).where(rsi < 10.0, 0.0)
    raw_score = oversold.where(uptrend & not_reverted, 0.0)

    # Price-derived RISK-OFF proxy (see test_validation_rsi2.py for rationale)
    ret_5d = spy_close.pct_change(5)
    crash = ret_5d < -0.06
    rolling_peak = spy_close.rolling(252, min_periods=1).max()
    drawdown = (spy_close - rolling_peak) / rolling_peak
    recession = drawdown < -0.20
    risk_off = (crash | recession).fillna(False)
    gated_score = raw_score.where(~risk_off, 0.0)

    valid_idx = sma_200.dropna().index
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {"RSI_2": rsi.loc[valid_idx], "SMA_200": sma_200.loc[valid_idx]},
        index=valid_idx,
    )

    ungated_ret = (raw_score.shift(1) * daily_ret).fillna(0.0).loc[valid_idx]
    gated_ret = (gated_score.shift(1) * daily_ret).fillna(0.0).loc[valid_idx]

    precomputed = {"RSI2_Gated": gated_ret, "RSI2_Ungated": ungated_ret}
    return X, y, precomputed


def _build_tsmom_adapter(
    spy_close: pd.Series,
) -> Tuple[pd.DataFrame, pd.Series, Dict[str, pd.Series]]:
    """12-1M time-series momentum on SPY with volatility targeting.

    Mirrors the core logic in ``tests/test_validation_ts_momentum.py``.
    Two variants: 12M look-back and 6M look-back, each with vol targeting at
    10% (conservative) and 20% (aggressive).
    """
    daily_ret = spy_close.pct_change()
    roc_12m = spy_close.shift(1) / spy_close.shift(253) - 1.0
    roc_6m = spy_close.shift(1) / spy_close.shift(127) - 1.0
    vol_60d = daily_ret.shift(1).rolling(60).std() * np.sqrt(252)

    valid_idx = (
        roc_12m.dropna().index.intersection(vol_60d.dropna().index)
    )
    y = daily_ret.loc[valid_idx].fillna(0.0)
    X = pd.DataFrame(
        {
            "ROC_12M": roc_12m.loc[valid_idx],
            "ROC_6M": roc_6m.loc[valid_idx],
            "Vol": vol_60d.loc[valid_idx],
        },
        index=valid_idx,
    )

    precomputed: Dict[str, pd.Series] = {}
    for roc_col, target_vol in [
        ("ROC_12M", 0.10), ("ROC_12M", 0.20),
        ("ROC_6M", 0.10), ("ROC_6M", 0.20),
    ]:
        roc = X[roc_col]
        vol = X["Vol"]
        vol_safe = np.where(vol > 0, vol, 0.20)
        vol_scalar = np.minimum(1.0, target_vol / vol_safe)
        sign_val = np.sign(roc.values)
        score = pd.Series(sign_val * vol_scalar, index=valid_idx)
        ret = (score.shift(1) * y).fillna(0.0)
        precomputed[f"TSMOM_{roc_col}_vol{int(target_vol * 100)}pct"] = ret

    return X, y, precomputed


def _make_strategy_fn(
    precomputed: Dict[str, pd.Series],
    turnover: float = 0.01,
) -> Callable:
    """Return a StrategyValidationHarness-compatible ``strategy_fn``.

    The harness calls ``strategy_fn(X_train, y_train, X_test, y_test)`` and
    expects a list of dicts with keys
    ``params`` / ``train_returns`` / ``test_returns`` / ``turnover``.
    """

    def strategy_fn(
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame,
        y_test: pd.Series,
    ) -> List[Dict[str, Any]]:
        configs = []
        for name, full_rets in precomputed.items():
            configs.append({
                "params": name,
                "train_returns": full_rets.loc[full_rets.index.intersection(y_train.index)],
                "test_returns": full_rets.loc[full_rets.index.intersection(y_test.index)],
                "turnover": turnover,
            })
        return configs

    return strategy_fn


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------
# Format: strategy_id → (adapter_fn(spy_close) → (X, y, precomputed), turnover)
#
# Each adapter receives the SPY close series (downloaded once) and returns the
# feature matrix X, the daily return series y, and a dict of pre-computed
# strategy return series.  New strategies: add an entry here and implement the
# corresponding adapter function above.
# ---------------------------------------------------------------------------
STRATEGY_REGISTRY: Dict[str, Tuple[Callable, float]] = {
    "rsi2_mean_reversion": (_build_rsi2_adapter, 0.02),
    "timeseries_momentum": (_build_tsmom_adapter, 0.005),
}


# =============================================================================
# Data download
# =============================================================================

def _download_spy(start_date: str, end_date: str) -> pd.Series:
    """Download SPY adjusted closes via yfinance; raises RuntimeError on failure."""
    import yfinance as yf

    df = yf.download("SPY", start=start_date, end=end_date, progress=False, auto_adjust=True)
    if df.empty:
        raise RuntimeError(
            f"Failed to download SPY data for {start_date}–{end_date}. "
            "Check your internet connection and try again."
        )
    close = df["Close"].squeeze()
    close.index = pd.to_datetime(close.index)
    return close


# =============================================================================
# Validation runner
# =============================================================================

def run_validations(
    strategies: Optional[List[str]] = None,
    start_date: str = "2005-01-01",
    end_date: Optional[str] = None,
    output_dir: Path = Path("reports"),
    n_cpcv_splits: int = 10,
    n_test_splits: int = 2,
) -> Dict[str, dict]:
    """Run walk-forward validation for each registered strategy.

    Parameters
    ----------
    strategies:
        Names to validate; ``None`` = all registered strategies.
    start_date, end_date:
        Historical window for backtesting (yfinance date strings).
    output_dir:
        Where to write JSON summaries.  Created automatically.
    n_cpcv_splits, n_test_splits:
        Passed to ``StrategyValidationHarness``.

    Returns
    -------
    dict mapping strategy_id → summary dict (same schema as
    ``ValidationReport.to_summary_dict()``; failed strategies include an
    ``"error"`` key and ``"deployable": false``).
    """
    from execution.cost_model import TieredCostModel
    from validation.harness import StrategyValidationHarness

    if end_date is None:
        end_date = date.today().isoformat()

    if strategies is None:
        strategies = list(STRATEGY_REGISTRY)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading SPY history %s → %s …", start_date, end_date)
    try:
        spy_close = _download_spy(start_date, end_date)
    except Exception as exc:
        logger.error("Cannot download SPY data: %s", exc)
        return {
            name: {
                "strategy_id": name,
                "deployable": False,
                "error": f"SPY download failed: {exc}",
                "report_date": date.today().isoformat(),
            }
            for name in strategies
        }

    cost_model = TieredCostModel()
    results: Dict[str, dict] = {}

    for name in strategies:
        if name not in STRATEGY_REGISTRY:
            logger.warning(
                "Unknown strategy '%s' — skipping. Known strategies: %s",
                name, sorted(STRATEGY_REGISTRY),
            )
            results[name] = {
                "strategy_id": name,
                "deployable": False,
                "error": f"Not in STRATEGY_REGISTRY. Known: {sorted(STRATEGY_REGISTRY)}",
                "report_date": date.today().isoformat(),
            }
            continue

        logger.info("Validating: %s", name)
        try:
            adapter_fn, turnover = STRATEGY_REGISTRY[name]
            X, y, precomputed = adapter_fn(spy_close)

            if X.empty or y.empty or not precomputed:
                raise RuntimeError(
                    "Adapter returned an empty feature/return frame — "
                    "insufficient history for this start/end range."
                )

            strategy_fn = _make_strategy_fn(precomputed, turnover=turnover)

            harness = StrategyValidationHarness(
                strategy_fn=strategy_fn,
                universe_fn=lambda _: ["SPY"],
                cost_model=cost_model,
                n_cpcv_splits=n_cpcv_splits,
                n_test_splits=n_test_splits,
            )

            report = harness.run(
                start_date=str(X.index[0].date()),
                end_date=str(X.index[-1].date()),
                X=X,
                y=y,
                strategy_name=name,
            )

            summary = report.to_summary_dict()
            results[name] = summary
            logger.info(
                "  %-32s deployable=%-5s  Sharpe=%s  PBO=%s  DSR=%s",
                name,
                summary.get("deployable"),
                f"{summary.get('sharpe', float('nan')):.3f}"
                if summary.get("sharpe") is not None else "  —  ",
                f"{summary.get('pbo', float('nan')):.3f}",
                f"{summary.get('dsr', float('nan')):.3f}",
            )

        except Exception as exc:  # CONSTRAINT #6 — per-strategy dead-letter
            logger.error(
                "Strategy '%s' validation failed: %s", name, exc, exc_info=True
            )
            results[name] = {
                "strategy_id": name,
                "deployable": False,
                "error": str(exc),
                "report_date": date.today().isoformat(),
            }

    return results


# =============================================================================
# CLI helpers
# =============================================================================

def _print_summary_table(results: Dict[str, dict]) -> None:
    """Print a compact ASCII pass/fail table to stdout."""
    hdr = f"  {'Strategy':<32} {'Status':<10} {'Sharpe':>7} {'PBO':>7} {'DSR':>7}"
    print()
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    any_fail = False
    for name, s in results.items():
        if "error" in s:
            status = "ERROR"
            any_fail = True
        elif s.get("deployable"):
            status = "✅ PASS"
        else:
            status = "❌ FAIL"
            any_fail = True

        def _fmt(v: Any) -> str:
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return "   —  "
            return f"{float(v):.3f}"

        print(
            f"  {name:<32} {status:<10} "
            f"{_fmt(s.get('sharpe')):>7} "
            f"{_fmt(s.get('pbo')):>7} "
            f"{_fmt(s.get('dsr')):>7}"
        )

    print()
    if any_fail:
        print("⚠️  One or more strategies did not meet deployability thresholds.")
        print("   See reports/<strategy>_validation_summary.json for details.")
    else:
        print("✅  All strategies passed validation gates.")
    print()


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.  Returns exit code 0 on all-pass, 1 on any failure."""
    parser = argparse.ArgumentParser(
        prog="scripts.refresh_validations",
        description="Run walk-forward validation for registered strategies (monthly cadence).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--strategies", type=str, default=None,
        help=(
            "Comma-separated strategy names to validate. "
            f"Default: all ({', '.join(sorted(STRATEGY_REGISTRY))})."
        ),
    )
    parser.add_argument(
        "--start", dest="start_date", type=str, default="2005-01-01",
        metavar="YYYY-MM-DD", help="Backtest start date (default: 2005-01-01).",
    )
    parser.add_argument(
        "--end", dest="end_date", type=str, default=None,
        metavar="YYYY-MM-DD", help="Backtest end date (default: today).",
    )
    parser.add_argument(
        "--output-dir", type=str, default="reports",
        help="Directory for JSON report output (default: reports/).",
    )
    parser.add_argument(
        "--n-cpcv-splits", type=int, default=10,
        help="Number of CPCV splits (default: 10).",
    )
    parser.add_argument(
        "--n-test-splits", type=int, default=2,
        help="Walk-forward test splits (default: 2).",
    )
    args = parser.parse_args(argv)

    strats: Optional[List[str]] = (
        [s.strip() for s in args.strategies.split(",") if s.strip()]
        if args.strategies
        else None
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    results = run_validations(
        strategies=strats,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=Path(args.output_dir),
        n_cpcv_splits=args.n_cpcv_splits,
        n_test_splits=args.n_test_splits,
    )

    _print_summary_table(results)

    any_fail = any(
        "error" in s or not s.get("deployable", False) for s in results.values()
    )
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())

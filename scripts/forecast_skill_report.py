"""
scripts/forecast_skill_report.py
================================
Forecasting rebuild F1: per-model skill against the naive ("price stays
flat") baseline, scored on log error, straight from the ``forecast_errors``
ledger. Read-only: it opens ``ForecastTracker(readonly=True)`` and never
writes.

For each horizon it prints, per model:

* ``n`` -- scored (model, naive) pairs, one per symbol x US/Eastern day
* median ``|ln(forecast / actual)|`` for the model and for naive on the
  SAME pairs
* % of pairs where the model beat naive
* direction hit rate (sign of forecast vs price-at-forecast-time, compared
  to the sign of the realized move)
* a two-sided sign-test p-value on wins vs losses

Symbols priced below ``--min-price`` (default $1) at forecast time are
excluded. The math is ``forecasting.forecast_tracker.compute_skill_vs_naive``.

Usage::

    python scripts/forecast_skill_report.py                      # all horizons
    python scripts/forecast_skill_report.py --horizon 10 --window-days 365
    python scripts/forecast_skill_report.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_HORIZONS = (10, 30, 60, 90)


def build_report(
    horizons: Sequence[int],
    window_days: Optional[int] = None,
    min_price: float = 1.0,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return ``{"window_days", "min_price", "db_path", "horizons": [...]}``,
    one ``ForecastTracker.skill_vs_naive`` result per horizon.

    ``window_days`` defaults to ``settings.FORECAST_SKILL_WINDOW_DAYS`` -- the
    same window the live skill-weighted blend uses.
    """
    from forecasting.forecast_tracker import ForecastTracker
    from settings import settings

    window = int(window_days) if window_days is not None else int(settings.FORECAST_SKILL_WINDOW_DAYS)
    tracker = ForecastTracker(db_path=db_path, readonly=True)
    return {
        "window_days": window,
        "min_price": float(min_price),
        "db_path": tracker._db_path,  # noqa: SLF001 -- report which ledger was read
        "horizons": [
            tracker.skill_vs_naive(int(h), window, min_price=min_price) for h in horizons
        ],
    }


def _fmt(value: Any, spec: str) -> str:
    if value is None:
        return "-"
    return format(value, spec)


def render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        f"Forecast skill vs naive (log error)  window={report['window_days']}d  "
        f"min_price=${report['min_price']:.2f}",
        f"ledger: {report['db_path']}",
    ]
    for res in report["horizons"]:
        lines.append("")
        lines.append(f"== horizon {res['horizon_days']} trading days ==")
        models = res.get("models") or {}
        if not models:
            lines.append(f"  (no scored pairs: {res.get('reason')})")
            continue
        lines.append(
            f"  {'model':<14}{'n':>7}{'med|lnE|':>10}{'naive':>9}{'%beat':>8}"
            f"{'dir hit':>9}{'sign p':>10}"
        )
        for name in sorted(models, key=lambda m: models[m]["median_abs_log_error"]):
            s = models[name]
            pct = s["pct_beating_naive"] * 100.0
            dir_hit = s["direction_hit_rate"] * 100.0 if s["direction_hit_rate"] is not None else None
            lines.append(
                f"  {name:<14}{s['n']:>7}{s['median_abs_log_error']:>10.4f}"
                f"{s['naive_median_abs_log_error']:>9.4f}{pct:>7.1f}%"
                f"{_fmt(dir_hit, '>8.1f')}{'%' if dir_hit is not None else ' '}"
                f"{_fmt(s['sign_test_p'], '>10.2g')}"
            )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--horizon", type=int, action="append",
                        help="Horizon in trading days (repeatable). Default: 10 30 60 90.")
    parser.add_argument("--window-days", type=int, default=None,
                        help="Rolling window in calendar days (default: FORECAST_SKILL_WINDOW_DAYS).")
    parser.add_argument("--min-price", type=float, default=1.0,
                        help="Exclude pairs whose naive (price-at-forecast) is below this (default 1.0).")
    parser.add_argument("--db", default=None, help="SQLite path (default: the platform ledger).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args(argv)

    report = build_report(
        args.horizon or DEFAULT_HORIZONS,
        window_days=args.window_days,
        min_price=args.min_price,
        db_path=args.db,
    )
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    from scripts._bootstrap import bootstrap

    bootstrap()
    sys.exit(main())

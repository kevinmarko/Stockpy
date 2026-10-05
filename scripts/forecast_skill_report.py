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

``--gate`` (forecasting rebuild F3) prints the shadow-period side-by-side
instead: ``blend`` (the live blend) vs ``gated_blend`` (the naive-gated blend
recorded in shadow) vs ``naive``, scored on the same matured days, plus how
often the gate fell back to naive and which models it admitted. This is the
review to run before turning ``FORECAST_NAIVE_GATE_ENABLED`` on.

Usage::

    python scripts/forecast_skill_report.py                      # all horizons
    python scripts/forecast_skill_report.py --horizon 10 --window-days 365
    python scripts/forecast_skill_report.py --json
    python scripts/forecast_skill_report.py --gate               # F3 side-by-side
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


def build_gate_report(
    horizons: Sequence[int],
    window_days: Optional[int] = None,
    min_price: float = 1.0,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """F3 side-by-side: one ``ForecastTracker.gate_side_by_side`` result per
    horizon, plus the gate settings in force. Read-only."""
    from forecasting.forecast_tracker import ForecastTracker
    from settings import settings

    window = int(window_days) if window_days is not None else int(settings.FORECAST_SKILL_WINDOW_DAYS)
    tracker = ForecastTracker(db_path=db_path, readonly=True)
    return {
        "window_days": window,
        "min_price": float(min_price),
        "db_path": tracker._db_path,  # noqa: SLF001 -- report which ledger was read
        "gate_enabled": bool(getattr(settings, "FORECAST_NAIVE_GATE_ENABLED", False)),
        "gate_min_improvement": float(getattr(settings, "FORECAST_NAIVE_GATE_MIN_IMPROVEMENT", 0.005)),
        "gate_min_obs": int(getattr(settings, "FORECAST_NAIVE_GATE_MIN_OBS", 60)),
        "horizons": [
            tracker.gate_side_by_side(int(h), window, min_price=min_price) for h in horizons
        ],
    }


def render_gate_text(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        f"Naive gate side-by-side (log error)  window={report['window_days']}d  "
        f"min_price=${report['min_price']:.2f}",
        f"gate: {'ON (publishing gated_blend)' if report['gate_enabled'] else 'OFF (shadow only)'}  "
        f"min_improvement={report['gate_min_improvement']:.2%}  min_obs={report['gate_min_obs']}",
        f"ledger: {report['db_path']}",
    ]
    for res in report["horizons"]:
        h = res["horizon_days"]
        lines.append("")
        lines.append(f"== horizon {h} trading days: blend vs gated_blend vs naive ==")
        act = res.get("activity") or {}
        models = res.get("models") or {}
        n_common = res.get("n_common_days", 0)
        if not models:
            reason = res.get("reason") or "n=0, not yet scorable"
            due = act.get("first_pending_due")
            suffix = f"; first gated_blend rows mature ~{due}" if due else ""
            lines.append(f"  n=0, not yet scorable ({reason}{suffix})")
        else:
            lines.append(f"  scored on {n_common} (symbol, day) pairs where all three matured")
            lines.append(
                f"  {'model':<14}{'n':>7}{'med|lnE|':>10}{'%beat':>8}{'dir hit':>9}{'sign p':>10}"
            )
            naive_med = None
            for name in ("blend", "gated_blend"):
                s = models.get(name)
                if not s:
                    lines.append(f"  {name:<14}{0:>7}  (no scored pairs)")
                    continue
                naive_med = s["naive_median_abs_log_error"]
                pct = s["pct_beating_naive"] * 100.0
                dir_hit = s["direction_hit_rate"] * 100.0 if s["direction_hit_rate"] is not None else None
                lines.append(
                    f"  {name:<14}{s['n']:>7}{s['median_abs_log_error']:>10.4f}{pct:>7.1f}%"
                    f"{_fmt(dir_hit, '>8.1f')}{'%' if dir_hit is not None else ' '}"
                    f"{_fmt(s['sign_test_p'], '>10.2g')}"
                )
            if naive_med is not None:
                lines.append(f"  {'naive':<14}{n_common:>7}{naive_med:>10.4f}{'(baseline)':>18}")
            h2h = res.get("head_to_head")
            if h2h:
                lines.append(
                    f"  gated_blend vs blend: better on {h2h['pct_beating_naive'] * 100.0:.1f}% of "
                    f"{h2h['n']} days (wins {h2h['wins']}, losses {h2h['losses']}, ties {h2h['ties']}), "
                    f"sign p={_fmt(h2h['sign_test_p'], '.2g')}"
                )
        days = act.get("days", 0)
        if days:
            fb = act.get("fallback_days", 0)
            adm = act.get("admitted_counts") or {}
            adm_txt = ", ".join(f"{m} {c}" for m, c in adm.items()) or "none"
            lines.append(
                f"  gate activity: {days} symbol-days recorded ({act.get('matured_days', 0)} matured); "
                f"fell back to naive on {fb} ({fb / days:.1%}); admitted: {adm_txt}"
            )
        else:
            lines.append("  gate activity: no gated_blend rows recorded in window yet")
    return "\n".join(lines)


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
    parser.add_argument("--gate", action="store_true",
                        help="F3: blend vs gated_blend vs naive side-by-side, plus gate activity.")
    args = parser.parse_args(argv)

    if args.gate:
        report = build_gate_report(
            args.horizon or DEFAULT_HORIZONS,
            window_days=args.window_days,
            min_price=args.min_price,
            db_path=args.db,
        )
        print(json.dumps(report, indent=2, default=str) if args.json else render_gate_text(report))
        return 0

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

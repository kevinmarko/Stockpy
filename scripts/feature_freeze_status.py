"""
scripts/feature_freeze_status.py
================================
Read-only progress report for the step 7 feature freeze (see CLAUDE.md's
"Feature freeze" section).

The freeze ends when the pipeline has closed enough of its OWN paper trades
to measure it: ``paper_closed_trades`` rows with
``strategy_id == main_orchestrator.PIPELINE_STRATEGY_ID`` ("main_pipeline").
Manual Quick Trade, delta-hedge and untagged rows never count.

A "quality" block (hold times, close reasons, entry clumping, open P&L, sector
concentration) is reported alongside but never affects the gate or exit code.
Open positions are marked from stored ``price_bars`` closes (no network);
``--live-quotes`` opts in to live quotes. Unmeasurable values are None.

Exit codes: 0 = the minimum (``--min``, default 30) is reached; 2 = still
frozen. It never writes anything.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PIPELINE_STRATEGY_ID = "main_pipeline"  # == main_orchestrator.PIPELINE_STRATEGY_ID
FREEZE_MIN_TRADES = 30
FREEZE_TARGET_TRADES = 50

_ET = ZoneInfo("America/New_York")
_STALE_MARK_DAYS = 4  # shown, never hidden: an older mark is flagged in the text output
_LOW_SAMPLE_N = 10
_UNAVAILABLE = "unavailable"

logger = logging.getLogger(__name__)


def summarize(closed: List[Dict[str, Any]], open_positions: List[Any],
              *, min_trades: int = FREEZE_MIN_TRADES,
              target_trades: int = FREEZE_TARGET_TRADES) -> Dict[str, Any]:
    """Pure summary of pipeline-owned closed trades and open positions."""
    own = [t for t in closed if t.get("strategy_id") == PIPELINE_STRATEGY_ID]
    pnls = [t["realized_pnl"] for t in own if t.get("realized_pnl") is not None]
    wins = sum(1 for p in pnls if p > 0)
    n = len(own)
    return {
        "closed_pipeline_trades": n,
        "min_trades": min_trades,
        "target_trades": target_trades,
        "freeze_can_end": n >= min_trades,
        # Honest nulls, never a fabricated 0 (CONSTRAINT #4).
        "win_rate": (wins / len(pnls)) if pnls else None,
        "total_realized_pnl": sum(pnls) if pnls else None,
        "unmeasured_pnl_trades": n - len(pnls),
        "open_pipeline_positions": sorted(
            p.symbol for p in open_positions
            if getattr(p, "strategy_id", None) == PIPELINE_STRATEGY_ID
        ),
    }


# --------------------------------------------------------------------------
# Quality report (observability only; never feeds the freeze gate)
# --------------------------------------------------------------------------

def _finite(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO string or datetime into an aware UTC datetime, else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _hold_days(trade: Dict[str, Any]) -> Optional[float]:
    """holding_period_days, else exit_ts - entry_ts, else None (unmeasured)."""
    h = _finite(trade.get("holding_period_days"))
    if h is not None and h >= 0:
        return h
    a, b = _parse_ts(trade.get("entry_ts")), _parse_ts(trade.get("exit_ts"))
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds() / 86400.0


def holding_period_stats(own_closed: List[Dict[str, Any]]) -> Dict[str, Any]:
    days = [d for d in (_hold_days(t) for t in own_closed) if d is not None]
    buckets = {"<1d": 0, "1-3d": 0, "3-7d": 0, ">=7d": 0}
    for d in days:
        buckets["<1d" if d < 1 else "1-3d" if d < 3 else "3-7d" if d < 7 else ">=7d"] += 1
    return {
        "n_closed": len(own_closed),
        "n_measured": len(days),
        "n_unmeasured": len(own_closed) - len(days),
        "median_days": statistics.median(days) if days else None,
        "mean_days": (sum(days) / len(days)) if days else None,
        "share_under_1d": (sum(1 for d in days if d < 1) / len(days)) if days else None,
        "buckets": buckets,
    }


def close_reason_counts(own_closed: List[Dict[str, Any]]) -> Dict[str, int]:
    c = Counter((str(t.get("close_reason") or "").strip() or _UNAVAILABLE) for t in own_closed)
    return dict(sorted(c.items()))


def entry_date_stats(own_closed: List[Dict[str, Any]], open_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    per_date: Counter = Counter()
    missing = 0
    for src in (own_closed, open_rows):
        for r in src:
            ts = _parse_ts(r.get("entry_ts"))
            if ts is None:
                missing += 1
            else:
                per_date[ts.astimezone(_ET).date().isoformat()] += 1
    total = sum(per_date.values())
    return {
        "distinct_entry_dates": len(per_date) if per_date else None,
        "entries_per_date": dict(sorted(per_date.items())),
        "max_entries_single_date": max(per_date.values()) if per_date else None,
        "max_single_date_share": (max(per_date.values()) / total) if total else None,
        "n_entries_without_ts": missing,
    }


def mark_open_positions(rows: List[Dict[str, Any]], marks: Dict[str, Dict[str, Any]],
                        *, now: datetime, is_option: Callable[[str], bool],
                        sectors: Dict[str, str], source: str) -> List[Dict[str, Any]]:
    """Open-position detail. ``marks`` maps SYMBOL -> {"price", "date"}.

    A position with no real mark gets None P&L -- never a cost-basis fallback.
    """
    out = []
    for r in rows:
        sym = str(r["symbol"]).upper().strip()
        qty, avg = _finite(r.get("qty")), _finite(r.get("avg_entry_price"))
        entry = _parse_ts(r.get("entry_ts"))
        days_held = ((now - entry).total_seconds() / 86400.0) if entry else None
        m = None if is_option(sym) else marks.get(sym)
        px = _finite(m.get("price")) if m else None
        if px is not None and px <= 0:
            px = None
        mark_date = m.get("date") if (m and px is not None) else None
        age = None
        if mark_date:
            try:
                age = (now.astimezone(_ET).date()
                       - datetime.fromisoformat(str(mark_date)[:10]).date()).days
            except ValueError:
                age = None
        pnl = pct = None
        if px is not None and qty not in (None, 0.0) and avg is not None and avg > 0:
            pnl = (px - avg) * qty  # short (qty<0) gives (avg - px) * |qty|
            pct = (px - avg) / avg * (1 if qty > 0 else -1)
        out.append({
            "symbol": sym,
            "side": None if qty is None else ("long" if qty > 0 else "short"),
            "qty": qty, "avg_entry_price": avg,
            "entry_ts": entry.isoformat() if entry else None,
            "days_held": days_held,
            "mark_price": px,
            "mark_source": source if px is not None else _UNAVAILABLE,
            "mark_date": mark_date, "mark_age_days": age,
            "unrealized_pnl": pnl, "unrealized_pnl_pct": pct,
            "sector": sectors.get(sym) or _UNAVAILABLE,
        })
    return out


def open_summary(open_marked: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls = [p["unrealized_pnl"] for p in open_marked if p["unrealized_pnl"] is not None]
    held = [p["days_held"] for p in open_marked if p["days_held"] is not None]
    return {
        "n_open": len(open_marked),
        "n_marked": len(pnls),
        "n_unmarked": len(open_marked) - len(pnls),
        "total_unrealized_pnl": sum(pnls) if pnls else None,  # marked rows only
        "median_days_held": statistics.median(held) if held else None,
    }


def sector_concentration(open_marked: List[Dict[str, Any]], own_closed: List[Dict[str, Any]],
                         sectors: Dict[str, str]) -> Dict[str, Any]:
    notional: Dict[str, float] = {}
    count: Counter = Counter()
    unknown = 0
    for p in open_marked:
        sec = p["sector"]
        if sec == _UNAVAILABLE or p["qty"] is None or p["avg_entry_price"] is None:
            unknown += 1
            continue
        count[sec] += 1
        notional[sec] = notional.get(sec, 0.0) + abs(p["qty"]) * p["avg_entry_price"]
    total = sum(notional.values())
    by_sector = {s: {"n_positions": count[s], "share": (n / total) if total > 1e-12 else None}
                 for s, n in sorted(notional.items(), key=lambda kv: -kv[1])}
    top = next(iter(by_sector), None)
    closed_c = Counter(sectors.get(str(t.get("symbol", "")).upper()) or _UNAVAILABLE
                       for t in own_closed)
    return {
        "basis": "cost_basis_notional",
        "by_sector": by_sector,
        "top_sector": top,
        "top_sector_share": by_sector[top]["share"] if top else None,
        "n_unknown_sector": unknown,
        "closed_trades_by_sector": dict(sorted(closed_c.items())),
    }


def build_quality(own_closed: List[Dict[str, Any]], open_rows: List[Dict[str, Any]],
                  marks: Dict[str, Dict[str, Any]], sectors: Dict[str, str],
                  *, now: datetime, is_option: Callable[[str], bool], source: str) -> Dict[str, Any]:
    marked = mark_open_positions(open_rows, marks, now=now, is_option=is_option,
                                 sectors=sectors, source=source)
    return {
        "holding_period": holding_period_stats(own_closed),
        "close_reasons": close_reason_counts(own_closed),
        "entries": entry_date_stats(own_closed, open_rows),
        "open_positions": marked,
        "open_summary": open_summary(marked),
        "sector_concentration": sector_concentration(marked, own_closed, sectors),
        "closed_vs_open": {"closed_pipeline_trades": len(own_closed),
                           "open_pipeline_positions": len(open_rows)},
        "mark_source": source,
    }


def _has_table(engine: Any, name: str) -> bool:
    from sqlalchemy import inspect
    try:
        return bool(inspect(engine).has_table(name))
    except Exception:
        return False


def _read_open_pipeline_rows(store: Any) -> List[Dict[str, Any]]:
    """Open pipeline positions straight from the database (no marking)."""
    from data.paper_account_store import PaperPosition
    if not _has_table(store.engine, "paper_positions"):
        return []
    session = store.Session()
    try:
        rows = (session.query(PaperPosition)
                .filter(PaperPosition.qty != 0, PaperPosition.strategy_id == PIPELINE_STRATEGY_ID)
                .all())
        return [{"symbol": p.symbol, "qty": float(p.qty),
                 "avg_entry_price": float(p.avg_entry_price), "entry_ts": p.entry_ts}
                for p in rows]
    finally:
        session.close()


def _read_stored_closes(engine: Any, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Latest stored ``price_bars.close`` per symbol (read-only SQL, no network)."""
    if not symbols or not _has_table(engine, "price_bars"):
        return {}
    from sqlalchemy import text
    out: Dict[str, Dict[str, Any]] = {}
    try:
        with engine.connect() as conn:
            for sym in symbols:
                row = conn.execute(text(
                    "SELECT date, close FROM price_bars WHERE symbol = :s AND close IS NOT NULL "
                    "ORDER BY date DESC LIMIT 1"), {"s": sym}).fetchone()
                if row is not None:
                    out[sym] = {"price": row[1], "date": str(row[0])[:10]}
    except Exception as exc:  # noqa: BLE001
        logger.debug("stored close read failed: %s", exc)
        return {}
    return out


def _read_sectors(engine: Any, symbols: List[str]) -> Dict[str, str]:
    """Sector from the latest fundamentals_history raw_json (read-only, no network)."""
    if not symbols or not _has_table(engine, "fundamentals_history"):
        return {}
    from sqlalchemy import text
    out: Dict[str, str] = {}
    try:
        with engine.connect() as conn:
            for sym in symbols:
                row = conn.execute(text(
                    "SELECT raw_json FROM fundamentals_history WHERE symbol = :s "
                    "AND raw_json IS NOT NULL ORDER BY as_of DESC LIMIT 1"), {"s": sym}).fetchone()
                if row and row[0]:
                    try:
                        sec = json.loads(row[0]).get("sector")
                    except (ValueError, AttributeError):
                        sec = None
                    if isinstance(sec, str) and sec.strip():
                        out[sym] = sec.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("sector read failed: %s", exc)
        return {}
    return out


def _live_marks(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Opt-in live quotes. Absent symbols stay absent (-> None), never cost basis."""
    try:
        from pilots.price_provider import get_latest_prices
        px = get_latest_prices(list(symbols)) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("live quote fetch failed: %s", exc)
        return {}
    today = datetime.now(_ET).date().isoformat()
    return {str(k).upper(): {"price": v, "date": today} for k, v in px.items()}


def collect_quality(store: Any, own_closed: List[Dict[str, Any]], *, live_quotes: bool = False,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    from data.paper_account_store import _is_option_symbol
    now = now or datetime.now(timezone.utc)
    open_rows = _read_open_pipeline_rows(store)
    open_syms = sorted({str(r["symbol"]).upper() for r in open_rows})
    stock_syms = [s for s in open_syms if not _is_option_symbol(s)]
    if live_quotes:
        marks, source = _live_marks(stock_syms), "live_quote"
    else:
        marks, source = _read_stored_closes(store.engine, stock_syms), "stored_close"
    all_syms = sorted((set(open_syms) | {str(t.get("symbol", "")).upper() for t in own_closed}) - {""})
    sectors = _read_sectors(store.engine, all_syms)
    return build_quality(own_closed, open_rows, marks, sectors, now=now,
                         is_option=_is_option_symbol, source=source)


def _fmt(v: Optional[float], spec: str, na: str = "n/a") -> str:
    return na if v is None else format(v, spec)


def _usd(v: Optional[float]) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def format_quality(q: Dict[str, Any]) -> List[str]:
    if "error" in q:
        return [f"Quality report unavailable: {q['error']}"]
    hp, en, os_, sc = q["holding_period"], q["entries"], q["open_summary"], q["sector_concentration"]
    lines = ["", "Trade quality (informational; not part of the gate)"]
    low = " (low sample)" if hp["n_measured"] < _LOW_SAMPLE_N else ""
    lines.append(
        f"  Hold time of closed trades: median {_fmt(hp['median_days'], '.2f')}d, "
        f"mean {_fmt(hp['mean_days'], '.2f')}d, under 1d {_fmt(hp['share_under_1d'], '.0%')} "
        f"(n={hp['n_measured']} measured, {hp['n_unmeasured']} unmeasured){low}")
    lines.append("  Hold buckets: " + ", ".join(f"{k}={v}" for k, v in hp["buckets"].items()))
    lines.append("  Close reasons: "
                 + (", ".join(f"{k}={v}" for k, v in q["close_reasons"].items()) or "none"))
    lines.append(
        f"  Entry dates: {_fmt(en['distinct_entry_dates'], 'd')} distinct; largest single-day "
        f"clump {_fmt(en['max_entries_single_date'], 'd')} "
        f"({_fmt(en['max_single_date_share'], '.0%')} of entries; "
        f"{en['n_entries_without_ts']} without timestamp)")
    cvo = q["closed_vs_open"]
    lines.append(f"  Closed vs open pipeline: {cvo['closed_pipeline_trades']} closed, "
                 f"{cvo['open_pipeline_positions']} open (open positions never count toward the gate)")
    lines.append(
        f"  Open positions ({os_['n_marked']}/{os_['n_open']} marked from {q['mark_source']}): "
        f"unrealized {_usd(os_['total_unrealized_pnl'])}, "
        f"median days held {_fmt(os_['median_days_held'], '.1f')}")
    for p in q["open_positions"]:
        stale = f" STALE({p['mark_age_days']}d)" if (p["mark_age_days"] or 0) > _STALE_MARK_DAYS else ""
        lines.append(
            f"    {p['symbol']:<6} {p['sector']:<20} held {_fmt(p['days_held'], '.1f')}d  "
            f"mark {_fmt(p['mark_price'], '.2f')} ({p['mark_date'] or 'n/a'}){stale}  "
            f"P&L {_usd(p['unrealized_pnl'])} ({_fmt(p['unrealized_pnl_pct'], '+.1%')})")
    if sc["by_sector"]:
        lines.append("  Sector concentration (cost basis): " + ", ".join(
            f"{k} {_fmt(v['share'], '.0%')} ({v['n_positions']})"
            for k, v in sc["by_sector"].items())
            + (f"; {sc['n_unknown_sector']} unknown" if sc["n_unknown_sector"] else ""))
    else:
        lines.append("  Sector concentration: unavailable")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--min", type=int, default=FREEZE_MIN_TRADES)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--live-quotes", action="store_true",
                        help="mark open positions from live quotes (network) instead of stored closes")
    args = parser.parse_args(argv)

    from data.paper_account_store import PaperAccountStore

    store = PaperAccountStore(readonly=True)
    closed = store.get_full_closed_trades(limit=1_000_000)
    # Database only: get_open_positions() would mark every position over the
    # network, and this read-only status check must never depend on quotes.
    try:
        open_positions = [
            SimpleNamespace(symbol=sym, strategy_id=PIPELINE_STRATEGY_ID)
            for sym in store.open_position_symbols(PIPELINE_STRATEGY_ID)
        ]
    except Exception:
        open_positions = []
    s = summarize(closed, open_positions, min_trades=args.min)
    # Quality report: informational only. A failure here must never change the
    # gate result or the exit code, both of which are fixed by ``s`` above.
    try:
        own_closed = [t for t in closed if t.get("strategy_id") == PIPELINE_STRATEGY_ID]
        s["quality"] = collect_quality(store, own_closed, live_quotes=args.live_quotes)
    except Exception as exc:  # noqa: BLE001
        s["quality"] = {"error": f"{type(exc).__name__}: {exc}"}

    if args.json:
        print(json.dumps(s, indent=2))
    else:
        state = "may end" if s["freeze_can_end"] else "still on"
        print(f"Feature freeze {state}: {s['closed_pipeline_trades']} closed pipeline paper "
              f"trades (min {s['min_trades']}, target {s['target_trades']}).")
        wr = "n/a" if s["win_rate"] is None else f"{s['win_rate']:.0%}"
        pnl = "n/a" if s["total_realized_pnl"] is None else f"${s['total_realized_pnl']:,.2f}"
        print(f"Win rate {wr}; realized P&L {pnl}; open pipeline positions: "
              f"{', '.join(s['open_pipeline_positions']) or 'none'}")
        for line in format_quality(s["quality"]):
            print(line)
    return 0 if s["freeze_can_end"] else 2


if __name__ == "__main__":
    from scripts._bootstrap import bootstrap

    bootstrap()
    sys.exit(main())

"""
scripts/feature_freeze_status.py
================================
Read-only progress report for the step 7 feature freeze (see CLAUDE.md's
"Feature freeze" section).

The freeze ends when the pipeline has closed enough of its OWN paper trades
to measure it: ``paper_closed_trades`` rows with
``strategy_id == main_orchestrator.PIPELINE_STRATEGY_ID`` ("main_pipeline").
Manual Quick Trade, delta-hedge and untagged rows never count.

Exit codes: 0 = the minimum (``--min``, default 30) is reached; 2 = still
frozen. It never writes anything.
"""
from __future__ import annotations

import argparse
from types import SimpleNamespace
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PIPELINE_STRATEGY_ID = "main_pipeline"  # == main_orchestrator.PIPELINE_STRATEGY_ID
FREEZE_MIN_TRADES = 30
FREEZE_TARGET_TRADES = 50


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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--min", type=int, default=FREEZE_MIN_TRADES)
    parser.add_argument("--json", action="store_true")
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
    return 0 if s["freeze_can_end"] else 2


if __name__ == "__main__":
    from scripts._bootstrap import bootstrap

    bootstrap()
    sys.exit(main())

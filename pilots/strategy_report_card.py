import logging
import math
from typing import Any, Dict, List

from pilots.catalog import list_pilots, Pilot
from data.paper_account_store import PaperAccountStore
from validation.validation_history_store import ValidationHistoryStore

logger = logging.getLogger(__name__)

LEGACY_STRATEGY_ID_ALIASES: Dict[str, str] = {
    "Dispersion Arbitrage": "dispersion-arbitrage",
    "Copula Stat Arb": "copula-stat-arb",
    "0DTE Momentum Breakout": "0dte-momentum-breakout",
    "Earnings Crush": "earnings-crush",
    "Put Credit Spread": "put-credit-spread",
    "Call Credit Spread": "call-credit-spread",
    "Call Debit Spread": "call-debit-spread",
    "Put Debit Spread": "put-debit-spread",
    "Covered Call": "covered-call",
    "Iron Condor": "iron-condor",
}

MIN_TRADES_FOR_VERDICT = 10


def _normalize_strategy_id(raw: str) -> str:
    """Strip 'Follow:' prefix if present, then apply the legacy alias map."""
    if not raw:
        return ""
    if raw.startswith("Follow:"):
        raw = raw[7:]
    return LEGACY_STRATEGY_ID_ALIASES.get(raw, raw)


def _predicted_side(pilot: Pilot, db_latest: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Return the validation metrics (predicted side) for a Pilot."""
    if pilot.validation_strategy_id is None:
        return {
            "deployable": None,
            "pbo": None,
            "dsr": None,
            "sharpe": None,
            "max_drawdown": None,
            "n_trials": None,
            "is_options_selling": None,
            "stress_gate_passed": None,
            "report_date": None,
            "reason": "no validated backtest for this pilot",
        }
    
    val = db_latest.get(pilot.validation_strategy_id)
    if not val:
        return {
            "deployable": None,
            "pbo": None,
            "dsr": None,
            "sharpe": None,
            "max_drawdown": None,
            "n_trials": None,
            "is_options_selling": None,
            "stress_gate_passed": None,
            "report_date": None,
            "reason": "missing",
        }
    
    return {
        "deployable": val.get("deployable"),
        "pbo": val.get("pbo"),
        "dsr": val.get("dsr"),
        "sharpe": val.get("sharpe"),
        "max_drawdown": val.get("max_drawdown"),
        "n_trials": val.get("n_trials"),
        "is_options_selling": val.get("is_options_selling"),
        "stress_gate_passed": val.get("stress_gate_passed"),
        "report_date": val.get("report_date"),
        "reason": None,
    }


def _actual_side(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the paper-trading metrics (actual side) given a list of trades."""
    trade_count = len(trades)
    
    if trade_count < MIN_TRADES_FOR_VERDICT:
        return {
            "trade_count": trade_count,
            "win_rate": None,
            "avg_realized_pnl_pct": None,
            "realized_sharpe_proxy": None,
            "max_cumulative_drawdown_usd": None,
            "total_realized_pnl_usd": None,
            "first_exit_ts": None,
            "last_exit_ts": None,
            "reason": f"insufficient sample (n={trade_count})",
        }
    
    wins = 0
    total_pnl_usd = 0.0
    cum_pnl = 0.0
    max_cum_pnl = 0.0
    max_dd_usd = 0.0
    
    # Sort trades by exit_ts ascending to compute drawdown correctly
    # If exit_ts is None for some reason, use a fallback string to avoid crash
    sorted_trades = sorted(trades, key=lambda t: str(t.get("exit_ts", "")))
    
    for t in sorted_trades:
        try:
            pnl = float(t.get("realized_pnl", 0.0))
        except (ValueError, TypeError):
            pnl = 0.0
            
        if not math.isfinite(pnl):
            pnl = 0.0
            
        total_pnl_usd += pnl
        if pnl > 0:
            wins += 1
            
        cum_pnl += pnl
        if cum_pnl > max_cum_pnl:
            max_cum_pnl = cum_pnl
            
        dd = max_cum_pnl - cum_pnl
        if dd > max_dd_usd:
            max_dd_usd = dd
            
    win_rate = wins / trade_count
    
    valid_pcts = []
    for t in sorted_trades:
        pct_raw = t.get("realized_pnl_pct")
        if pct_raw is not None:
            try:
                pct = float(pct_raw)
                if math.isfinite(pct):
                    valid_pcts.append(pct)
            except (ValueError, TypeError):
                pass

    if len(valid_pcts) > 1:
        avg_pct = sum(valid_pcts) / len(valid_pcts)
        var = sum((x - avg_pct) ** 2 for x in valid_pcts) / (len(valid_pcts) - 1)
        stdev = math.sqrt(var) if var > 0 else 0.0
        if stdev > 1e-12:
            sharpe_proxy = (avg_pct / stdev) * math.sqrt(252)
            if not math.isfinite(sharpe_proxy):
                sharpe_proxy = None
        else:
            sharpe_proxy = None
    elif len(valid_pcts) == 1:
        avg_pct = valid_pcts[0]
        sharpe_proxy = None
    else:
        avg_pct = None
        sharpe_proxy = None
        
    return {
        "trade_count": trade_count,
        "win_rate": win_rate,
        "avg_realized_pnl_pct": avg_pct,
        "realized_sharpe_proxy": sharpe_proxy,
        "max_cumulative_drawdown_usd": max_dd_usd,
        "total_realized_pnl_usd": total_pnl_usd,
        "first_exit_ts": sorted_trades[0].get("exit_ts"),
        "last_exit_ts": sorted_trades[-1].get("exit_ts"),
        "reason": None,
    }


def strategy_report_card_rows() -> List[Dict[str, Any]]:
    """Return rows for all catalog Pilots + non-Pilot buckets found in paper trades."""
    # 1. Fetch Validation
    try:
        db_latest = ValidationHistoryStore(readonly=True).get_latest_per_strategy()
    except Exception as e:
        logger.error(f"ValidationHistoryStore query failed: {e}")
        db_latest = {}
        
    # 2. Fetch Paper Trades
    try:
        trades = PaperAccountStore(readonly=True).get_full_closed_trades(limit=5000)
    except Exception as e:
        logger.error(f"PaperAccountStore query failed: {e}")
        trades = []
        
    # Group trades by normalized pilot ID
    grouped_trades: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        raw_id = t.get("strategy_id") or ""
        norm_id = _normalize_strategy_id(raw_id)
        if norm_id:
            grouped_trades.setdefault(norm_id, []).append(t)
            
    # Process Pilots
    pilots = list_pilots()
    pilot_ids = set([p.id for p in pilots])
    
    rows = []
    for p in pilots:
        pred = _predicted_side(p, db_latest)
        act = _actual_side(grouped_trades.get(p.id, []))
        
        row = {
            "pilot_id": p.id,
            "name": p.name,
            "category": p.category,
            "is_pilot": True,
            "predicted": pred,
            "actual": act,
        }
        rows.append(row)
        
    # Process non-Pilot buckets
    for norm_id, t_list in grouped_trades.items():
        if norm_id not in pilot_ids:
            act = _actual_side(t_list)
            pred = {
                "deployable": None,
                "pbo": None,
                "dsr": None,
                "sharpe": None,
                "max_drawdown": None,
                "n_trials": None,
                "is_options_selling": None,
                "stress_gate_passed": None,
                "report_date": None,
                "reason": "non-pilot bucket",
            }
            row = {
                "pilot_id": norm_id,
                "name": norm_id,
                "category": "Other",
                "is_pilot": False,
                "predicted": pred,
                "actual": act,
            }
            rows.append(row)
            
    return rows

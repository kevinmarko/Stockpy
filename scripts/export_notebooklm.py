"""Automated Markdown export script for Google NotebookLM ingestion.

Generates both:
1. A consolidated document: ``settings.OUTPUT_DIR / "notebooklm_source.md"``
2. A modular multi-source knowledge pack: ``settings.OUTPUT_DIR / "notebooklm/*.md"``
   - ``01_macro_and_regime.md``
   - ``02_portfolio_and_greeks.md``
   - ``03_strategy_signals_and_picks.md``
   - ``04_trade_journal_and_ledger.md``
   - ``05_options_directives_and_matrix.md``

Strictly adheres to CONSTRAINT #4 (never fabricate data) and CONSTRAINT #6 (fail closed,
graceful per-section degradation).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Repo-root import shim
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading
from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from data.historical_store import HistoricalStore  # noqa: E402
from pilots.follows_store import FollowsStore  # noqa: E402
from settings import settings  # noqa: E402

logger = logging.getLogger("notebooklm_export")


# ---------------------------------------------------------------------------
# Formatting helpers (CONSTRAINT #4 — never fabricate data)
# ---------------------------------------------------------------------------

def _fmt_money(value: Any) -> str:
    """Format monetary value as currency, or 'N/A' if missing/NaN.
    A genuine 0 or 0.0 formats honestly as $0.00 (CONSTRAINT #4)."""
    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_num(value: Any) -> str:
    """Format numeric value as string, or 'N/A' if missing/NaN.
    A genuine 0 or 0.0 formats honestly as '0' or '0.0'."""
    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    return str(value)


def _fmt_pct(value: Any, precision: int = 2) -> str:
    """Format float or string percentage with % sign, or 'N/A' if missing/NaN."""
    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    try:
        val = float(value)
        return f"{val:.{precision}f}%"
    except (TypeError, ValueError):
        return "N/A"


def _atomic_write_file(path: Path, content: str) -> None:
    """Atomic write (temp file + rename) matching the repo convention so a process
    kill mid-write never leaves a truncated/corrupted document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _load_json_file(path: Path) -> Dict[str, Any]:
    """Safely load a JSON file, returning an empty dict on missing or corrupt file."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning(f"Failed to load JSON file {path}: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Generator 1: Macro & Regime (01_macro_and_regime.md)
# ---------------------------------------------------------------------------

def generate_macro_regime_source(
    store: Optional[HistoricalStore] = None,
    output_dir: Optional[Path] = None,
) -> str:
    """Generate the Macroeconomic & Regime source document."""
    target_dir = output_dir or settings.OUTPUT_DIR
    lines: List[str] = [
        "# Market Regime & Macroeconomic Risk Assessment",
        f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Executive Summary & Regime Classification",
    ]

    # Read state_snapshot.json if available
    ss = _load_json_file(target_dir / "state_snapshot.json")
    market_regime = ss.get("market_regime")
    hmm_state = ss.get("hmm_regime_state")
    hmm_risk_on = ss.get("hmm_risk_on_probability")
    macro_kill_switch = ss.get("macro_kill_switch")
    macro_gate_enabled = ss.get("macro_regime_gate_enabled")
    file_kill_switch = (target_dir / "KILL_SWITCH").exists()

    lines.append(f"- **Market Regime**: {market_regime if market_regime is not None else 'UNKNOWN'}")
    lines.append(f"- **HMM Regime State**: {hmm_state if hmm_state is not None else 'N/A'}")
    lines.append(f"- **HMM Risk-On Probability**: {_fmt_pct(float(hmm_risk_on) * 100 if hmm_risk_on is not None else None)}")
    lines.append(f"- **Macro Kill Switch**: {'TRIGGERED (Risk Off)' if macro_kill_switch else 'Normal (Inactive)'}")
    lines.append(f"- **Macro Gate Protection**: {'Enabled' if macro_gate_enabled is not False else 'Disabled (Hybrid Mode)'}")
    lines.append(f"- **Global Kill Switch Sentinel**: {'ACTIVE (Trading Halted)' if file_kill_switch else 'Clear'}")
    lines.append("")

    # Macro Indicators
    lines.append("## Core Macroeconomic Indicators")
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")

        vix_series = store.get_macro("VIXCLS")
        t10y2y_series = store.get_macro("T10Y2Y")
        hy_oas_series = store.get_macro("BAMLH0A0HYM2")

        has_macro = False
        if not vix_series.empty:
            vix_val = vix_series.iloc[-1]
            lines.append(f"- **VIX (CBOE Volatility Index)**: {_fmt_num(vix_val)}")
            has_macro = True
        if not t10y2y_series.empty:
            t10_val = t10y2y_series.iloc[-1]
            lines.append(f"- **10Y-2Y Yield Curve Spread**: {_fmt_num(t10_val)}%")
            has_macro = True
        if not hy_oas_series.empty:
            hy_val = hy_oas_series.iloc[-1]
            lines.append(f"- **High Yield OAS Credit Spread**: {_fmt_num(hy_val)}%")
            has_macro = True

        sahm_val = ss.get("sahm_rule")
        if sahm_val is not None:
            lines.append(f"- **Sahm Rule Indicator**: {_fmt_num(sahm_val)}")
            has_macro = True

        if not has_macro:
            lines.append("Macro series data is currently unavailable.")
    except Exception as exc:
        logger.warning(f"Failed to fetch macro data in generate_macro_regime_source: {exc}")
        lines.append("Macro data is currently unavailable.")

    lines.append("")
    lines.append("## Tactical Implications for NotebookLM Analysis")
    lines.append("Use this document to ground portfolio risk discussions:")
    lines.append("- **VIX > 30** or **High Yield OAS > 6%**: Systemic stress; options premium selling gates closed.")
    lines.append("- **10Y-2Y Inversion (< 0)**: Late-cycle or recessionary warning signals.")
    lines.append("- **HMM Risk-On Probability < 30%**: Regime-downweighting active for cyclical long momentum.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator 2: Portfolio & Greeks (02_portfolio_and_greeks.md)
# ---------------------------------------------------------------------------

def generate_portfolio_greeks_source(
    store: Optional[HistoricalStore] = None,
    output_dir: Optional[Path] = None,
) -> str:
    """Generate the Portfolio Holdings & Net Risk Greeks source document."""
    lines: List[str] = [
        "# Portfolio Holdings, Allocation & Net Risk Greeks",
        f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Account Liquidity & Capital Summary",
    ]

    port = None
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        from api.pilots_api import _serialize_portfolio  # lazy import
        snap = store.latest_account_snapshot()
        if snap:
            port = _serialize_portfolio(snap)
    except Exception as exc:
        logger.warning(f"Failed to fetch account snapshot in generate_portfolio_greeks_source: {exc}")

    if port:
        lines.append(f"- **Total Equity**: {_fmt_money(port.get('total_equity'))}")
        lines.append(f"- **Buying Power**: {_fmt_money(port.get('buying_power'))}")
        fetched_at = port.get("fetched_at")
        if fetched_at:
            staleness = " (stale)" if port.get("is_stale") else ""
            lines.append(f"- **Snapshot As Of**: {fetched_at}{staleness}")
        lines.append(f"- **Source**: {port.get('source', 'db')}")
    else:
        lines.append("Portfolio snapshot is unavailable.")

    lines.append("")
    lines.append("## Net Portfolio Greeks & Beta Sensitivity")

    try:
        from pilots.options_risk import calculate_portfolio_greeks  # lazy import
        greeks = calculate_portfolio_greeks()
        net_delta_shares = greeks.get("net_delta_shares")
        net_dollar_delta = greeks.get("net_dollar_delta")
        net_gamma = greeks.get("net_gamma")
        net_theta = greeks.get("net_theta_daily")
        net_vega = greeks.get("net_vega_1pct")
        beta_spy_delta = greeks.get("beta_weighted_delta_spy")
        spy_spot = greeks.get("spy_spot")

        lines.append(f"- **Net Delta (Shares)**: {_fmt_num(net_delta_shares)}")
        lines.append(f"- **Net Dollar Delta ($)**: {_fmt_money(net_dollar_delta)}")
        lines.append(f"- **Net Gamma**: {_fmt_num(net_gamma)}")
        lines.append(f"- **Net Daily Theta ($/day)**: {_fmt_money(net_theta)}")
        lines.append(f"- **Net Vega (1% IV Shock)**: {_fmt_money(net_vega)}")
        lines.append(f"- **Beta-Weighted SPY Delta**: {_fmt_num(beta_spy_delta)}")
        if spy_spot is not None:
            lines.append(f"- **Benchmark SPY Spot**: {_fmt_money(spy_spot)}")

        missing = greeks.get("positions_with_missing_data") or []
        if missing:
            lines.append(f"- **Positions with Missing Greeks Data**: {', '.join(missing)}")
        est_beta = greeks.get("symbols_with_estimated_beta") or []
        if est_beta:
            lines.append(f"- **Symbols Using Estimated Beta (1.0)**: {', '.join(est_beta)}")
    except Exception as exc:
        logger.warning(f"Failed to calculate portfolio Greeks: {exc}")
        lines.append("Portfolio Greeks calculation is currently unavailable.")

    lines.append("")
    lines.append("## Open Positions & Basis")
    positions = port.get("positions", []) if port else []
    if positions:
        lines.append("| Symbol | Quantity | Avg Cost | Price | Market Value | Unrealized P&L |")
        lines.append("|---|---|---|---|---|---|")
        for p in positions:
            sym = p.get("symbol", "Unknown")
            name = f" ({p.get('name')})" if p.get("name") else ""
            qty = _fmt_num(p.get("qty"))
            avg_cost = _fmt_money(p.get("avg_cost"))
            price = _fmt_money(p.get("current_price"))
            mkt_val = _fmt_money(p.get("market_value"))
            upl = _fmt_money(p.get("unrealized_pl"))
            lines.append(f"| **{sym}**{name} | {qty} | {avg_cost} | {price} | {mkt_val} | {upl} |")
    else:
        lines.append("No open positions." if port else "Position details unavailable.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator 3: Strategy Signals & Picks (03_strategy_signals_and_picks.md)
# ---------------------------------------------------------------------------

def generate_signals_picks_source(output_dir: Optional[Path] = None) -> str:
    """Generate the Strategy Signals, Tactical Picks & Follows source document."""
    target_dir = output_dir or settings.OUTPUT_DIR
    lines: List[str] = [
        "# Quantitative Strategy Signals, Tactical Execution & Pilot Follows",
        f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Active Pilot Strategy Subscriptions",
    ]

    # 1. Follows
    try:
        follows = FollowsStore().list_active()
        if follows:
            lines.append("| Pilot ID | Allocated Amount | Status |")
            lines.append("|---|---|---|")
            for f in follows:
                pilot_id = f.get("pilot_id", "Unknown")
                amount = _fmt_money(f.get("amount"))
                status = f.get("status", "Unknown")
                lines.append(f"| **{pilot_id}** | {amount} | {status} |")
        else:
            lines.append("No active pilot follows.")
    except Exception as exc:
        logger.warning(f"Failed to fetch active follows: {exc}")
        lines.append("Active pilot follows are unavailable.")

    lines.append("")

    # 2. Signals from state_snapshot.json
    lines.append("## Daily Tactical Recommendations (BUY / SELL / HOLD)")
    ss = _load_json_file(target_dir / "state_snapshot.json")
    signals = ss.get("signals")

    if isinstance(signals, list) and signals:
        lines.append("| Symbol | Action | Conviction | Buy Range | Sell Range | Kelly Sizing | Final Score |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in signals:
            sym = s.get("symbol", "Unknown")
            action = s.get("action", s.get("advisory_action", "HOLD"))
            conviction = _fmt_num(s.get("advisory_conviction", s.get("conviction")))
            buy_range = s.get("buy_range", "N/A")
            sell_range = s.get("sell_range", "N/A")
            kelly = _fmt_pct(float(s["kelly_target"]) * 100) if s.get("kelly_target") is not None else "N/A"
            score = _fmt_num(s.get("score", s.get("final_score")))
            lines.append(f"| **{sym}** | {action} | {conviction} | {buy_range} | {sell_range} | {kelly} | {score} |")

        lines.append("")
        lines.append("## Multifactor Z-Score Attribution")
        lines.append("| Symbol | Value Z | Quality Z | Momentum (XSec) | LowVol Z | Size Z | Composite |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in signals:
            sym = s.get("symbol", "Unknown")
            vz = _fmt_num(s.get("value_z"))
            qz = _fmt_num(s.get("quality_z"))
            mz = _fmt_num(s.get("xsec_12_1m", s.get("xsec_momentum_rank")))
            lz = _fmt_num(s.get("lowvol_z"))
            sz = _fmt_num(s.get("size_z"))
            comp = _fmt_num(s.get("multifactor_composite"))
            lines.append(f"| **{sym}** | {vz} | {qz} | {mz} | {lz} | {sz} | {comp} |")

        lines.append("")
        lines.append("## Sizing Guardrails & ETF Transmission Impact")
        lines.append("| Symbol | Was Capped | Binding Constraint | ETF Transmission Multiplier |")
        lines.append("|---|---|---|---|")
        for s in signals:
            sym = s.get("symbol", "Unknown")
            capped = str(s.get("sizing_was_capped", False))
            constraint = s.get("sizing_binding_constraint") or "None"
            etf_mult = _fmt_num(s.get("etf_transmission_multiplier"))
            lines.append(f"| **{sym}** | {capped} | {constraint} | {etf_mult} |")
    else:
        lines.append("Tactical recommendations are currently unavailable.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator 4: Trade Journal & Ledger (04_trade_journal_and_ledger.md)
# ---------------------------------------------------------------------------

def generate_trade_journal_source(output_dir: Optional[Path] = None) -> str:
    """Generate the Trade Journal & Realized Performance source document."""
    lines: List[str] = [
        "# Quantitative Trade Journal & Realized Performance",
        f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Realized Trading KPIs (FIFO Reconstructed)",
    ]

    summary: Dict[str, Any] = {}
    trades: List[Any] = []

    try:
        from pilots.trade_history import trade_history_view  # lazy import
        th_view = trade_history_view(limit=50, offset=0)
        summary = th_view.get("summary") or {}
        trades = th_view.get("trades") or []
    except Exception as exc:
        logger.warning(f"Failed to fetch trade_history_view: {exc}")

    if summary and summary.get("n_trades", 0) > 0:
        pf = summary.get("profit_factor")
        pf_str = f"{float(pf):.2f}" if pf is not None else "N/A"
        hdays = summary.get("avg_holding_days")
        hdays_str = f"{float(hdays):.1f}" if hdays is not None else "N/A"

        lines.append(f"- **Total Closed Trades**: {_fmt_num(summary.get('n_trades'))}")
        lines.append(f"- **Win Rate**: {_fmt_pct(float(summary['win_rate']) * 100 if summary.get('win_rate') is not None else None)}")
        lines.append(f"- **Profit Factor**: {pf_str}")
        lines.append(f"- **Total Realized P&L**: {_fmt_money(summary.get('total_realized_pnl'))}")
        lines.append(f"- **Gross Profit**: {_fmt_money(summary.get('gross_profit'))} | **Gross Loss**: {_fmt_money(summary.get('gross_loss'))}")
        lines.append(f"- **Average Win**: {_fmt_money(summary.get('avg_win'))} | **Average Loss**: {_fmt_money(summary.get('avg_loss'))}")
        lines.append(f"- **Average Return per Trade**: {_fmt_pct(summary.get('avg_return_pct'))}")
        lines.append(f"- **Average Holding Duration**: {hdays_str} days")
        lines.append(f"- **Best Trade**: {_fmt_money(summary.get('best_trade_pnl'))} | **Worst Trade**: {_fmt_money(summary.get('worst_trade_pnl'))}")
    else:
        lines.append("No realized closed trade history recorded yet.")

    lines.append("")
    lines.append("## Recent Closed Trades Ledger")
    if trades:
        lines.append("| Symbol | Quantity | Entry Date | Exit Date | Holding Days | Entry Price | Exit Price | Realized P&L | Return % |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for t in trades:
            sym = t.get("symbol", "Unknown")
            qty = _fmt_num(t.get("quantity"))
            entry_ts = str(t.get("entry_ts", "N/A"))[:10]
            exit_ts = str(t.get("exit_ts", "N/A"))[:10]
            hdays = _fmt_num(round(float(t["holding_days"]), 1)) if t.get("holding_days") is not None else "N/A"
            eprice = _fmt_money(t.get("entry_price"))
            xprice = _fmt_money(t.get("exit_price"))
            pnl = _fmt_money(t.get("realized_pnl"))
            ret = _fmt_pct(t.get("return_pct"))
            lines.append(f"| **{sym}** | {qty} | {entry_ts} | {exit_ts} | {hdays} | {eprice} | {xprice} | {pnl} | {ret} |")
    else:
        lines.append("No closed trades available.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generator 5: Options Directives & Matrix (05_options_directives_and_matrix.md)
# ---------------------------------------------------------------------------

def generate_options_matrix_source(output_dir: Optional[Path] = None) -> str:
    """Generate the Options Directives & Volatility Matrix source document."""
    target_dir = output_dir or settings.OUTPUT_DIR
    lines: List[str] = [
        "# Options Strategy Directives & Volatility Matrix",
        f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Options Environment & Regime Gating",
    ]

    om = _load_json_file(target_dir / "options_matrix.json")
    directives = om.get("directives")

    lines.append(f"- **Target DTE**: {_fmt_num(om.get('target_dte'))} days")
    lines.append(f"- **Reference VIX**: {_fmt_num(om.get('vix'))}")
    lines.append(f"- **Market Regime**: {om.get('market_regime', 'UNKNOWN')}")
    lines.append(f"- **Directives Generated**: {len(directives) if isinstance(directives, list) else 0}")
    lines.append("")

    lines.append("## Active Quantitative Directives (Credit Spreads & Condors)")
    if isinstance(directives, list) and directives:
        lines.append("| Symbol | Strategy | Action | Spot Price | Short Leg | Long Leg | Net Premium | IV Rank | Trend Bias |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for d in directives:
            sym = d.get("Symbol", "Unknown")
            strat = d.get("Strategy", "Spread")
            action = d.get("Action", "Sell to Open")
            price = _fmt_money(d.get("Price"))
            short_str = f"{d.get('Short_Strike')} (Δ {d.get('Short_Delta')})" if d.get('Short_Strike') is not None else "N/A"
            long_str = f"{d.get('Long_Strike')} (Δ {d.get('Long_Delta')})" if d.get('Long_Strike') is not None else "N/A"
            prem = _fmt_money(d.get("Net_Premium"))
            ivr = _fmt_pct(d.get("True_IVR", d.get("IVR_Proxy")))
            trend = d.get("Trend_Bias", "Neutral")
            lines.append(f"| **{sym}** | {strat} | {action} | {price} | {short_str} | {long_str} | {prem} | {ivr} | {trend} |")

        lines.append("")
        lines.append("## Candidate Fundamental Health & News Catalysts")
        for d in directives[:10]:  # Top 10 for focused grounding
            sym = d.get("Symbol", "Unknown")
            altman = _fmt_num(d.get("Altman_Z_Score"))
            piotroski = _fmt_num(d.get("Piotroski_F_Score"))
            dte_earn = _fmt_num(d.get("Days_To_Earnings"))
            erisk = "YES (Imminent)" if d.get("Earnings_Risk") else "No"
            lines.append(f"### {sym}")
            lines.append(f"- **Financial Health**: Altman Z-Score: {altman} | Piotroski F-Score: {piotroski}")
            lines.append(f"- **Earnings Calendar**: {dte_earn} days to earnings (Earnings Risk: {erisk})")
            news = d.get("News_Snippets")
            if isinstance(news, list) and news:
                lines.append("- **Recent News Headlines**:")
                for item in news[:3]:
                    title = item.get("title", "Headline")
                    lines.append(f"  - {title}")
            lines.append("")
    else:
        lines.append("No active options directives available.")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Consolidated Export (Single-Document Mode)
# ---------------------------------------------------------------------------

def generate_consolidated_source(
    store: Optional[HistoricalStore] = None,
    output_dir: Optional[Path] = None,
) -> str:
    """Generate the single consolidated notebooklm_source.md preserving exact backwards
    compatibility with Phase 1 while incorporating the modular sections."""
    lines: List[str] = [
        "# Stockpy System Export",
        f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    # Section 1: Macro
    lines.append("## Macro Context")
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        vix_series = store.get_macro("VIXCLS")
        t10y2y_series = store.get_macro("T10Y2Y")
        hy_oas_series = store.get_macro("BAMLH0A0HYM2")

        has_macro = False
        if not vix_series.empty:
            lines.append(f"- **VIX**: {_fmt_num(vix_series.iloc[-1])}")
            has_macro = True
        if not t10y2y_series.empty:
            lines.append(f"- **10Y-2Y Spread**: {_fmt_num(t10y2y_series.iloc[-1])}%")
            has_macro = True
        if not hy_oas_series.empty:
            lines.append(f"- **High Yield OAS**: {_fmt_num(hy_oas_series.iloc[-1])}%")
            has_macro = True

        if not has_macro:
            lines.append("Macro data is currently unavailable.")
    except Exception as exc:
        logger.warning(f"Failed to fetch macro data in generate_consolidated_source: {exc}")
        lines.append("Macro data is currently unavailable.")
    lines.append("")

    # Section 2: Portfolio
    lines.append("## Current Portfolio")
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        from api.pilots_api import _serialize_portfolio  # lazy import
        snap = store.latest_account_snapshot()
        if snap:
            port = _serialize_portfolio(snap)
            lines.append(f"- **Total Equity**: {_fmt_money(port.get('total_equity'))}")
            lines.append(f"- **Buying Power**: {_fmt_money(port.get('buying_power'))}")
            fetched_at = port.get("fetched_at")
            if fetched_at:
                staleness = " (stale)" if port.get("is_stale") else ""
                lines.append(f"- **Snapshot As Of**: {fetched_at}{staleness}")
            lines.append("")
            positions = port.get("positions", [])
            if positions:
                lines.append("### Positions")
                for p in positions:
                    symbol = p.get('symbol', 'Unknown')
                    qty = _fmt_num(p.get('qty'))
                    avg_cost = _fmt_money(p.get('avg_cost'))
                    mkt_val = _fmt_money(p.get('market_value'))
                    name = p.get('name') or ''
                    name_str = f" ({name})" if name else ""
                    lines.append(f"- **{symbol}**{name_str}: {qty} shares @ {avg_cost} (Market Value: {mkt_val})")
            else:
                lines.append("No open positions.")
        else:
            lines.append("Portfolio snapshot is unavailable.")
    except Exception as exc:
        logger.warning(f"Failed to fetch portfolio: {exc}")
        lines.append("Portfolio snapshot is unavailable.")
    lines.append("")

    # Section 3: Follows
    lines.append("## Active Pilot Follows")
    try:
        follows = FollowsStore().list_active()
        if follows:
            for f in follows:
                pilot_id = f.get('pilot_id', 'Unknown')
                amount = _fmt_money(f.get('amount'))
                status = f.get('status', 'Unknown')
                lines.append(f"- **Pilot ID**: {pilot_id} | **Amount**: {amount} | **Status**: {status}")
        else:
            lines.append("No active pilot follows.")
    except Exception as exc:
        logger.warning(f"Failed to fetch active follows: {exc}")
        lines.append("Active pilot follows are unavailable.")
    lines.append("")

    # Section 4: Quantitative Directives & Trade Journal Overview
    lines.append("## Modular Sources Note")
    lines.append("Comprehensive multi-source files are exported under `output/notebooklm/`:")
    lines.append("- `01_macro_and_regime.md`")
    lines.append("- `02_portfolio_and_greeks.md`")
    lines.append("- `03_strategy_signals_and_picks.md`")
    lines.append("- `04_trade_journal_and_ledger.md`")
    lines.append("- `05_options_directives_and_matrix.md`")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Primary Dispatcher & CLI Driver
# ---------------------------------------------------------------------------

def build_export(
    output_dir: Optional[Path] = None,
    *,
    modular: bool = True,
    consolidated: bool = True,
    section: Optional[str] = None,
) -> None:
    """Orchestrate exporting platform quantitative data for Google NotebookLM.

    Generates both the consolidated single-file export (``notebooklm_source.md``)
    and the modular multi-source knowledge pack (``notebooklm/*.md``).
    """
    out_dir = output_dir or settings.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    notebooklm_dir = out_dir / "notebooklm"
    notebooklm_dir.mkdir(parents=True, exist_ok=True)

    # Initialize store once with safe fallback
    try:
        store = HistoricalStore(readonly=True)
    except Exception as exc:
        logger.warning(f"Failed to construct HistoricalStore: {exc}")
        store = None

    # Consolidated export
    if consolidated and section is None:
        con_content = generate_consolidated_source(store, out_dir)
        _atomic_write_file(out_dir / "notebooklm_source.md", con_content)
        logger.info(f"Consolidated export written to {out_dir / 'notebooklm_source.md'}")
        print(f"Export written to {out_dir / 'notebooklm_source.md'}")

    # Modular exports
    if modular or section is not None:
        if section in (None, "macro"):
            m_content = generate_macro_regime_source(store, out_dir)
            _atomic_write_file(notebooklm_dir / "01_macro_and_regime.md", m_content)
            logger.info("Modular export written: 01_macro_and_regime.md")

        if section in (None, "portfolio"):
            p_content = generate_portfolio_greeks_source(store, out_dir)
            _atomic_write_file(notebooklm_dir / "02_portfolio_and_greeks.md", p_content)
            logger.info("Modular export written: 02_portfolio_and_greeks.md")

        if section in (None, "signals"):
            s_content = generate_signals_picks_source(out_dir)
            _atomic_write_file(notebooklm_dir / "03_strategy_signals_and_picks.md", s_content)
            logger.info("Modular export written: 03_strategy_signals_and_picks.md")

        if section in (None, "trades"):
            t_content = generate_trade_journal_source(out_dir)
            _atomic_write_file(notebooklm_dir / "04_trade_journal_and_ledger.md", t_content)
            logger.info("Modular export written: 04_trade_journal_and_ledger.md")

        if section in (None, "options"):
            o_content = generate_options_matrix_source(out_dir)
            _atomic_write_file(notebooklm_dir / "05_options_directives_and_matrix.md", o_content)
            logger.info("Modular export written: 05_options_directives_and_matrix.md")

        print(f"Modular knowledge pack written to {notebooklm_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Google NotebookLM export documents.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Target output directory (defaults to settings.OUTPUT_DIR)")
    parser.add_argument("--modular-only", action="store_true", help="Generate only the modular output/notebooklm/*.md files")
    parser.add_argument("--consolidated-only", action="store_true", help="Generate only the consolidated notebooklm_source.md file")
    parser.add_argument("--section", choices=["macro", "portfolio", "signals", "trades", "options"], default=None, help="Generate only a specific modular source")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    modular = not args.consolidated_only
    consolidated = not args.modular_only

    build_export(
        output_dir=args.output_dir,
        modular=modular,
        consolidated=consolidated,
        section=args.section,
    )


if __name__ == "__main__":
    main()

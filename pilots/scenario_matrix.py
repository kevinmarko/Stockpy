"""
pilots/scenario_matrix.py — Multi-Dimensional Scenario Matrix & Stress Grid Engine.
===================================================================================

Computes multi-dimensional portfolio stress grids and historical shock re-valuations
across options and equity holdings.

Capabilities:
- Spot shifts: e.g. [-10%, -5%, -3%, -1%, 0%, +1%, +3%, +5%, +10%]
- IV shifts: e.g. [-20%, -10%, -5%, 0%, +5%, +10%, +20%]
- Time shifts: e.g. [0, 7, 14, 21] days forward
- Full Black-Scholes re-pricing with 1e-12 degenerate guard and 0DTE intrinsic fallback
- Historical shock presets:
  * Lehman 2008 (-15% spot, +50% IV)
  * Volmageddon 2018 (-4% spot, +100% IV)
  * COVID 2020 (-12% spot, +40% IV)
  * Yen Unwind 2024 (-6% spot, +30% IV)

Design invariants:
- Pure mathematical evaluation over position models and spot/vol matrices.
- Dependency-light: imports only stdlib, settings, and pilots.options_risk.
- AST safe: never imports heavy calculation engines on the API path.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from pilots.options_risk import calculate_black_scholes_greeks, parse_option_symbol
from settings import settings

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_SPOT_SHIFTS",
    "DEFAULT_IV_SHIFTS",
    "DEFAULT_TIME_SHIFTS_DAYS",
    "HISTORICAL_PRESETS",
    "evaluate_scenario_matrix",
    "evaluate_single_scenario",
    "evaluate_historical_presets",
    "get_historical_presets",
    "get_2d_scenario_slice",
    "evaluate_portfolio_scenario_matrix",
    "to_scenario_matrix_response",
]

# Standard default shift grids
DEFAULT_SPOT_SHIFTS: List[float] = [-0.10, -0.05, -0.03, -0.01, 0.0, 0.01, 0.03, 0.05, 0.10]
DEFAULT_IV_SHIFTS: List[float] = [-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20]
DEFAULT_TIME_SHIFTS_DAYS: List[int] = [0, 7, 14, 21]

# Historical shock presets
HISTORICAL_PRESETS: Dict[str, Dict[str, Any]] = {
    "lehman_2008": {
        "id": "lehman_2008",
        "name": "Lehman 2008",
        "description": "Global Financial Crisis collapse (-15% Spot, +50% IV)",
        "spot_shift": -0.15,
        "iv_shift": 0.50,
        "time_shift_days": 0,
    },
    "volmageddon_2018": {
        "id": "volmageddon_2018",
        "name": "Volmageddon 2018",
        "description": "XIV / Short-volatility blowup (-4% Spot, +100% IV)",
        "spot_shift": -0.04,
        "iv_shift": 1.00,
        "time_shift_days": 0,
    },
    "covid_2020": {
        "id": "covid_2020",
        "name": "COVID 2020",
        "description": "Pandemic market crash (-12% Spot, +40% IV)",
        "spot_shift": -0.12,
        "iv_shift": 0.40,
        "time_shift_days": 0,
    },
    "yen_unwind_2024": {
        "id": "yen_unwind_2024",
        "name": "Yen Unwind 2024",
        "description": "Global carry-trade unwinding (-6% Spot, +30% IV)",
        "spot_shift": -0.06,
        "iv_shift": 0.30,
        "time_shift_days": 0,
    },
}


def get_historical_presets() -> Dict[str, Dict[str, Any]]:
    """Returns the dictionary of historical shock preset definitions."""
    return dict(HISTORICAL_PRESETS)


def _parse_position(
    pos: Any,
    now: datetime,
    base_iv: float = 0.25,
    iv_map: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Extracts standardized position attributes from PaperPosition objects or dictionaries."""
    if isinstance(pos, dict):
        symbol = str(pos.get("symbol", "")).strip()
        qty = float(pos.get("qty", pos.get("quantity", 0.0)) or 0.0)
        avg_entry = float(pos.get("avg_entry_price", pos.get("avg_cost", 0.0)) or 0.0)
        pos_spot = pos.get("spot_price") or pos.get("current_price")
        pos_iv = pos.get("iv") or pos.get("implied_volatility") or pos.get("sigma")
    else:
        symbol = str(getattr(pos, "symbol", "")).strip()
        qty = float(getattr(pos, "qty", getattr(pos, "quantity", 0.0)) or 0.0)
        avg_entry = float(getattr(pos, "avg_entry_price", getattr(pos, "avg_cost", 0.0)) or 0.0)
        pos_spot = getattr(pos, "spot_price", None) or getattr(pos, "current_price", None)
        pos_iv = getattr(pos, "iv", None) or getattr(pos, "implied_volatility", None) or getattr(pos, "sigma", None)

    opt_info = parse_option_symbol(symbol)
    if opt_info:
        ticker = opt_info["ticker"].upper()
        strike = float(opt_info["strike"])
        exp_str = opt_info["expiration"]
        opt_type = opt_info["option_type"].lower()
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            now_utc = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
            dte = max(0.0, (exp_date - now_utc).total_seconds() / 86400.0)
        except Exception:
            dte = 30.0
        t_years = dte / 365.0

        # Resolve IV
        sigma = base_iv
        if iv_map and symbol in iv_map:
            sigma = float(iv_map[symbol])
        elif iv_map and ticker in iv_map:
            sigma = float(iv_map[ticker])
        elif pos_iv is not None and float(pos_iv) > 0:
            sigma = float(pos_iv)

        return {
            "symbol": symbol,
            "asset_type": "option",
            "ticker": ticker,
            "qty": qty,
            "strike": strike,
            "expiration": exp_str,
            "option_type": opt_type,
            "dte": dte,
            "t_years": t_years,
            "sigma": max(0.01, sigma),
            "avg_entry_price": avg_entry,
            "fallback_spot": float(pos_spot) if (pos_spot and float(pos_spot) > 0) else None,
        }
    else:
        ticker = symbol.upper()
        return {
            "symbol": symbol,
            "asset_type": "stock",
            "ticker": ticker,
            "qty": qty,
            "strike": 0.0,
            "expiration": "",
            "option_type": "",
            "dte": 0.0,
            "t_years": 0.0,
            "sigma": 0.0,
            "avg_entry_price": avg_entry,
            "fallback_spot": float(pos_spot) if (pos_spot and float(pos_spot) > 0) else None,
        }


def _price_position_under_scenario(
    parsed_pos: Dict[str, Any],
    spot_map: Dict[str, float],
    spot_shift: float,
    iv_shift: float,
    time_shift_days: int,
    r: Optional[float] = None,
) -> Optional[Dict[str, float]]:
    """Re-prices a single position under specified spot, IV, and time shocks."""
    ticker = parsed_pos["ticker"]
    spot = spot_map.get(ticker)
    if spot is None or spot <= 0:
        spot = parsed_pos.get("fallback_spot")

    if spot is None or spot <= 0:
        return None

    shocked_spot = max(0.01, spot * (1.0 + spot_shift))
    qty = parsed_pos["qty"]

    if parsed_pos["asset_type"] == "stock":
        market_val = qty * shocked_spot
        delta = qty
        dollar_delta = delta * shocked_spot
        return {
            "market_value": market_val,
            "delta": delta,
            "dollar_delta": dollar_delta,
            "gamma": 0.0,
            "theta_daily": 0.0,
            "vega_1pct": 0.0,
        }

    # Option position
    strike = parsed_pos["strike"]
    opt_type = parsed_pos["option_type"]
    base_t_years = parsed_pos["t_years"]
    base_sigma = parsed_pos["sigma"]

    # Shock leg IV: sigma' = max(0.01, sigma * (1 + iv_shift))
    shocked_sigma = max(0.01, base_sigma * (1.0 + iv_shift))

    # Advance time: T' = max(0.0, T - time_shift / 365.0)
    shocked_t_years = max(0.0, base_t_years - (time_shift_days / 365.0))

    bs = calculate_black_scholes_greeks(
        spot=shocked_spot,
        strike=strike,
        t_years=shocked_t_years,
        sigma=shocked_sigma,
        option_type=opt_type,
        r=r,
    )

    multiplier = 100.0
    effective_qty = qty * multiplier

    market_val = qty * bs["price"] * multiplier
    pos_delta = effective_qty * bs["delta"]
    pos_dollar_delta = pos_delta * shocked_spot
    pos_gamma = effective_qty * bs["gamma"]
    pos_theta = effective_qty * bs["theta_daily"]
    pos_vega = effective_qty * bs["vega_1pct"]

    return {
        "market_value": market_val,
        "delta": pos_delta,
        "dollar_delta": pos_dollar_delta,
        "gamma": pos_gamma,
        "theta_daily": pos_theta,
        "vega_1pct": pos_vega,
    }


def evaluate_single_scenario(
    parsed_positions: List[Dict[str, Any]],
    spot_map: Dict[str, float],
    spot_shift: float,
    iv_shift: float,
    time_shift_days: int,
    baseline_market_value: Optional[float] = None,
    r: Optional[float] = None,
) -> Dict[str, Any]:
    """Evaluates a single (spot_shift, iv_shift, time_shift) scenario cell across all positions."""
    total_mv = 0.0
    net_delta = 0.0
    net_dollar_delta = 0.0
    net_gamma = 0.0
    net_theta = 0.0
    net_vega = 0.0
    missing_symbols: List[str] = []

    for pos in parsed_positions:
        res = _price_position_under_scenario(
            pos,
            spot_map=spot_map,
            spot_shift=spot_shift,
            iv_shift=iv_shift,
            time_shift_days=time_shift_days,
            r=r,
        )
        if res is None:
            missing_symbols.append(pos["symbol"])
            continue

        total_mv += res["market_value"]
        net_delta += res["delta"]
        net_dollar_delta += res["dollar_delta"]
        net_gamma += res["gamma"]
        net_theta += res["theta_daily"]
        net_vega += res["vega_1pct"]

    pnl_shift = (total_mv - baseline_market_value) if baseline_market_value is not None else 0.0
    pnl_pct = (
        (pnl_shift / abs(baseline_market_value))
        if (baseline_market_value is not None and abs(baseline_market_value) > 1e-6)
        else 0.0
    )

    return {
        "spot_shift": float(spot_shift),
        "iv_shift": float(iv_shift),
        "time_shift_days": int(time_shift_days),
        "portfolio_market_value": round(total_mv, 2),
        "pnl_shift": round(pnl_shift, 2),
        "pnl_pct": round(pnl_pct, 4),
        "net_delta": round(net_delta, 2),
        "net_dollar_delta": round(net_dollar_delta, 2),
        "net_gamma": round(net_gamma, 4),
        "net_theta_daily": round(net_theta, 2),
        "net_vega_1pct": round(net_vega, 2),
        "missing_symbols": missing_symbols,
    }


def evaluate_historical_presets(
    parsed_positions: List[Dict[str, Any]],
    spot_map: Dict[str, float],
    baseline_market_value: Optional[float] = None,
    r: Optional[float] = None,
) -> Dict[str, Dict[str, Any]]:
    """Evaluates each defined historical shock preset against the parsed positions."""
    results: Dict[str, Dict[str, Any]] = {}
    for preset_id, preset in HISTORICAL_PRESETS.items():
        eval_res = evaluate_single_scenario(
            parsed_positions,
            spot_map=spot_map,
            spot_shift=preset["spot_shift"],
            iv_shift=preset["iv_shift"],
            time_shift_days=preset.get("time_shift_days", 0),
            baseline_market_value=baseline_market_value,
            r=r,
        )
        res_dict = dict(preset)
        res_dict.update(eval_res)
        results[preset_id] = res_dict
    return results


def evaluate_scenario_matrix(
    positions: Optional[List[Any]] = None,
    spot_map: Optional[Dict[str, float]] = None,
    spot_shifts: Optional[List[float]] = None,
    iv_shifts: Optional[List[float]] = None,
    time_shifts_days: Optional[List[int]] = None,
    *,
    base_iv: float = 0.25,
    iv_map: Optional[Dict[str, float]] = None,
    r: Optional[float] = None,
    now: Optional[datetime] = None,
    store: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Computes a multi-dimensional scenario stress grid across positions for spot, IV, and time shifts.

    Args:
        positions: List of PaperPosition objects or position dicts. If None, loaded from PaperAccountStore.
        spot_map: Dict of {ticker: spot_price}. If None, auto-resolved where possible.
        spot_shifts: Relative spot shocks, defaulting to [-0.10, -0.05, -0.03, -0.01, 0.0, 0.01, 0.03, 0.05, 0.10].
        iv_shifts: Relative IV shocks, defaulting to [-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20].
        time_shifts_days: Days elapsed, defaulting to [0, 7, 14, 21].
        base_iv: Default IV for option contracts if not specified in iv_map or position (default 0.25).
        iv_map: Optional mapping of symbol or ticker to IV.
        r: Risk-free rate (defaults to settings.OPTIONS_RISK_FREE_RATE or 0.045).
        now: Reference timestamp for DTE calculation (defaults to utcnow).
        store: Optional PaperAccountStore instance if positions is None.

    Returns:
        Dict containing baseline portfolio metrics, the full 3D scenario grid, historical presets,
        and missing data symbols.
    """
    if spot_shifts is None:
        spot_shifts = list(DEFAULT_SPOT_SHIFTS)
    if iv_shifts is None:
        iv_shifts = list(DEFAULT_IV_SHIFTS)
    if time_shifts_days is None:
        time_shifts_days = list(DEFAULT_TIME_SHIFTS_DAYS)

    if positions is None:
        if store is None:
            try:
                from data.paper_account_store import PaperAccountStore
                store = PaperAccountStore(readonly=True)
            except Exception:
                store = None
        if store is not None:
            try:
                positions = store.get_open_positions()
            except Exception:
                positions = []
        else:
            positions = []

    if now is None:
        now = datetime.now(timezone.utc)

    # Parse all positions
    parsed_positions = [
        _parse_position(p, now=now, base_iv=base_iv, iv_map=iv_map)
        for p in positions
    ]

    # Auto-resolve spots if not supplied
    if spot_map is None:
        spot_map = {}
        try:
            from data.market_data import get_provider
            provider = get_provider()
            if provider:
                distinct_tickers = {p["ticker"] for p in parsed_positions if p["ticker"]}
                for t in distinct_tickers:
                    try:
                        quote = provider.get_latest_quote(t)
                        if quote and getattr(quote, "price", 0) and float(quote.price) > 0:
                            spot_map[t] = float(quote.price)
                    except Exception:
                        pass
        except Exception:
            pass

    # Baseline evaluation (spot_shift=0, iv_shift=0, time_shift=0)
    baseline_eval = evaluate_single_scenario(
        parsed_positions,
        spot_map=spot_map,
        spot_shift=0.0,
        iv_shift=0.0,
        time_shift_days=0,
        baseline_market_value=None,
        r=r,
    )
    baseline_mv = baseline_eval["portfolio_market_value"]

    # Compute scenario grid
    grid: List[Dict[str, Any]] = []
    all_missing_symbols: set[str] = set(baseline_eval["missing_symbols"])

    for t_shift in time_shifts_days:
        for iv_shift in iv_shifts:
            for s_shift in spot_shifts:
                cell = evaluate_single_scenario(
                    parsed_positions,
                    spot_map=spot_map,
                    spot_shift=s_shift,
                    iv_shift=iv_shift,
                    time_shift_days=t_shift,
                    baseline_market_value=baseline_mv,
                    r=r,
                )
                for sym in cell["missing_symbols"]:
                    all_missing_symbols.add(sym)
                grid.append(cell)

    # Compute historical shock presets
    historical_presets = evaluate_historical_presets(
        parsed_positions,
        spot_map=spot_map,
        baseline_market_value=baseline_mv,
        r=r,
    )

    return {
        "baseline": {
            "portfolio_market_value": baseline_eval["portfolio_market_value"],
            "net_delta": baseline_eval["net_delta"],
            "net_dollar_delta": baseline_eval["net_dollar_delta"],
            "net_gamma": baseline_eval["net_gamma"],
            "net_theta_daily": baseline_eval["net_theta_daily"],
            "net_vega_1pct": baseline_eval["net_vega_1pct"],
            "positions_count": len(parsed_positions),
        },
        "spot_shifts": spot_shifts,
        "iv_shifts": iv_shifts,
        "time_shifts_days": time_shifts_days,
        "grid": grid,
        "historical_presets": historical_presets,
        "missing_data_symbols": sorted(list(all_missing_symbols)),
        # Ticker -> resolved spot price used for this evaluation. Additive-only field
        # (existing callers/tests that only look at the keys above are unaffected) --
        # consumed by to_scenario_matrix_response() below to derive an honest
        # per-cell reference spot_price without re-resolving quotes a second time.
        "spot_map": dict(spot_map),
    }


def get_2d_scenario_slice(
    scenario_result: Dict[str, Any],
    time_shift_days: int = 0,
) -> Dict[str, Any]:
    """
    Filters a full scenario result into a 2D matrix (spot_shift vs iv_shift) for a specific time horizon.
    Useful for rendering heatmaps and 2D tabular stress reports.
    """
    spot_shifts = scenario_result.get("spot_shifts", DEFAULT_SPOT_SHIFTS)
    iv_shifts = scenario_result.get("iv_shifts", DEFAULT_IV_SHIFTS)
    grid = scenario_result.get("grid", [])

    cells_for_t = [c for c in grid if c.get("time_shift_days") == time_shift_days]
    cell_map = {(c["spot_shift"], c["iv_shift"]): c for c in cells_for_t}

    matrix_pnl: List[List[float]] = []
    matrix_pnl_pct: List[List[float]] = []

    for iv in iv_shifts:
        row_pnl = []
        row_pnl_pct = []
        for sp in spot_shifts:
            cell = cell_map.get((sp, iv))
            if cell:
                row_pnl.append(cell.get("pnl_shift", 0.0))
                row_pnl_pct.append(cell.get("pnl_pct", 0.0))
            else:
                row_pnl.append(0.0)
                row_pnl_pct.append(0.0)
        matrix_pnl.append(row_pnl)
        matrix_pnl_pct.append(row_pnl_pct)

    return {
        "time_shift_days": time_shift_days,
        "spot_shifts": spot_shifts,
        "iv_shifts": iv_shifts,
        "matrix_pnl": matrix_pnl,
        "matrix_pnl_pct": matrix_pnl_pct,
        "cells": cells_for_t,
    }


def _resolve_reference_spot(spot_map: Dict[str, float]) -> Optional[float]:
    """
    Resolves a single honest reference spot price for the per-cell spot_price
    field the frontend contract expects.

    A scenario matrix can span multiple underlying tickers (equity + several
    option legs on different names) with no single "the portfolio's spot
    price" -- rather than fabricate a blended/average number that corresponds
    to no real instrument, this only resolves a reference spot when the book
    has exactly one distinct underlying ticker with a known live quote.
    Otherwise returns None and the frontend cell simply omits spot_price.
    """
    if len(spot_map) != 1:
        return None
    (only_spot,) = spot_map.values()
    return only_spot if only_spot and only_spot > 0 else None


def _cell_to_frontend_shape(cell: Dict[str, Any], reference_spot: Optional[float]) -> Dict[str, Any]:
    """Renames one evaluate_scenario_matrix() grid cell into the ScenarioMatrixCell
    shape webapp/src/api/types.ts and webapp/src/api/mock.ts already agree on."""
    frontend_cell: Dict[str, Any] = {
        "spot_shift_pct": cell["spot_shift"],
        "iv_shift_pct": cell["iv_shift"],
        "days_forward": cell["time_shift_days"],
        "portfolio_value": cell["portfolio_market_value"],
        "pnl_dollar": cell["pnl_shift"],
        "pnl_pct": cell["pnl_pct"],
        "net_delta": cell["net_delta"],
        "net_gamma": cell["net_gamma"],
        "net_theta": cell["net_theta_daily"],
        "net_vega": cell["net_vega_1pct"],
    }
    if reference_spot is not None:
        frontend_cell["spot_price"] = round(reference_spot * (1.0 + cell["spot_shift"]), 2)
    return frontend_cell


def _preset_to_frontend_shape(preset: Dict[str, Any]) -> Dict[str, Any]:
    """Renames one evaluate_historical_presets() entry into HistoricalScenarioPreset."""
    return {
        "id": preset["id"],
        "name": preset["name"],
        "description": preset["description"],
        "spot_shift_pct": preset["spot_shift"],
        "iv_shift_pct": preset["iv_shift"],
        "projected_pnl_dollar": preset["pnl_shift"],
        "projected_pnl_pct": preset["pnl_pct"],
    }


def to_scenario_matrix_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reshapes evaluate_scenario_matrix()'s internal result (grid/time_shifts_days/
    historical_presets/baseline -- the shape every existing test in
    tests/test_scenario_matrix.py asserts on) into the ScenarioMatrixResponse
    contract webapp/src/api/types.ts, webapp/src/api/mock.ts, and
    webapp/src/components/options/ScenarioHeatmap.tsx already agree on
    (matrix/time_slices/historical_scenarios/current_portfolio_value).

    Kept as a separate step from evaluate_scenario_matrix() itself so every
    existing caller/test of the pure math function is unaffected -- only
    evaluate_portfolio_scenario_matrix() (the function POST /pilots/paper-broker/
    scenario-matrix actually calls) applies this reshape.
    """
    spot_map = result.get("spot_map", {})
    reference_spot = _resolve_reference_spot(spot_map)

    return {
        "spot_shifts": result["spot_shifts"],
        "iv_shifts": result["iv_shifts"],
        "time_slices": result["time_shifts_days"],
        "matrix": [_cell_to_frontend_shape(c, reference_spot) for c in result["grid"]],
        "historical_scenarios": [
            _preset_to_frontend_shape(preset) for preset in result["historical_presets"].values()
        ],
        "current_portfolio_value": result["baseline"]["portfolio_market_value"],
    }


def evaluate_portfolio_scenario_matrix(
    spot_shifts: Optional[List[float]] = None,
    iv_shifts: Optional[List[float]] = None,
    time_shifts: Optional[List[int]] = None,
    time_days_forward: Optional[int] = None,
    store: Optional[Any] = None,
    positions: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    API-facing wrapper for POST /pilots/paper-broker/scenario-matrix.

    Unlike evaluate_scenario_matrix() (the pure math function every test in
    tests/test_scenario_matrix.py exercises directly), this returns the
    ScenarioMatrixResponse shape the webapp actually consumes -- see
    to_scenario_matrix_response() and docs/known_issues/scenario_matrix_field_mismatch.md.
    """
    if time_shifts is not None:
        resolved_time_shifts = time_shifts
    elif time_days_forward is not None:
        # An explicit single-day request (e.g. a future caller wanting just T+0).
        resolved_time_shifts = [time_days_forward]
    else:
        # No time dimension specified at all -- use the full default grid
        # (DEFAULT_TIME_SHIFTS_DAYS) rather than silently collapsing to one slice.
        resolved_time_shifts = None

    result = evaluate_scenario_matrix(
        positions=positions,
        spot_shifts=spot_shifts,
        iv_shifts=iv_shifts,
        time_shifts_days=resolved_time_shifts,
        store=store,
    )
    return to_scenario_matrix_response(result)


"""
options_hedging.py — Dynamic Delta Hedging Engine for Options Paper Portfolios.
==============================================================================
Computes required SPY shares to rebalance beta-weighted SPY delta exposure to neutral (0),
applying a deadband filter (tolerance band) to prevent overtrading/churn.
Executes rebalancing fills atomically against PaperAccountStore.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, Union

from data.paper_account_store import PaperAccountStore
from settings import settings

logger = logging.getLogger(__name__)


def calculate_delta_hedge_order(
    portfolio_greeks: Union[Dict[str, Any], Any],
    spy_spot: Optional[float] = None,
    tolerance_band_shares: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Computes required SPY shares to return beta_weighted_delta_spy to 0.
    
    If abs(shares) < tolerance_band_shares, returns None (deadband filter).
    Otherwise returns order intent dict with:
      symbol="SPY", side="buy" or "sell", qty=round(abs(shares)), order_type="market".
    """
    if tolerance_band_shares is None:
        tolerance_band_shares = float(getattr(settings, "OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES", 25.0))
    else:
        tolerance_band_shares = float(tolerance_band_shares)

    # Extract beta-weighted SPY delta
    if isinstance(portfolio_greeks, (int, float)):
        beta_delta = float(portfolio_greeks)
    elif isinstance(portfolio_greeks, dict):
        if "beta_weighted_delta_spy" in portfolio_greeks:
            beta_delta = float(portfolio_greeks["beta_weighted_delta_spy"] or 0.0)
        elif "net_dollar_delta" in portfolio_greeks and spy_spot and spy_spot > 0:
            beta_delta = float(portfolio_greeks["net_dollar_delta"]) / float(spy_spot)
        elif "net_delta_shares" in portfolio_greeks:
            beta_delta = float(portfolio_greeks["net_delta_shares"] or 0.0)
        else:
            beta_delta = 0.0
    elif hasattr(portfolio_greeks, "beta_weighted_delta_spy"):
        beta_delta = float(getattr(portfolio_greeks, "beta_weighted_delta_spy", 0.0) or 0.0)
    else:
        beta_delta = 0.0

    # Shares needed to return beta_weighted_delta_spy to 0
    # E.g. If beta_delta = +100 (long delta), we need to SELL 100 SPY shares (-100 shares needed)
    # If beta_delta = -100 (short delta), we need to BUY 100 SPY shares (+100 shares needed)
    shares_needed = -beta_delta
    abs_shares = abs(shares_needed)

    # Deadband filter
    if abs_shares < tolerance_band_shares:
        return None

    qty = round(abs_shares)
    if qty <= 0:
        return None

    side = "buy" if shares_needed > 0 else "sell"

    return {
        "symbol": "SPY",
        "side": side,
        "qty": int(qty),
        "order_type": "market",
        "target_delta": 0.0,
        "current_beta_weighted_delta": round(beta_delta, 2),
        "shares_needed": round(shares_needed, 2),
        "spy_spot": spy_spot,
    }


def get_delta_hedge_preview(
    store: Optional[PaperAccountStore] = None,
    portfolio_greeks: Optional[Union[Dict[str, Any], Any]] = None,
    spy_spot: Optional[float] = None,
    tolerance_band_shares: Optional[float] = None,
) -> Dict[str, Any]:
    """Provides a preview recommendation for portfolio delta hedging."""
    if tolerance_band_shares is None:
        tolerance_band_shares = float(getattr(settings, "OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES", 25.0))
    if store is None:
        try:
            store = PaperAccountStore(readonly=True)
        except Exception:
            store = None
    if spy_spot is None or spy_spot <= 0:
        try:
            from pilots.price_provider import get_current_price
            resolved_price = get_current_price("SPY")
            spy_spot = resolved_price if resolved_price and resolved_price > 0 else None
        except Exception:
            spy_spot = None

        if spy_spot is None:
            # No honest SPY price available -- refuse to fabricate one
            # (CONSTRAINT #4), mirroring execute_delta_hedge's identical
            # refusal below. A fabricated 500.0 here used to silently drive
            # beta_weighted_delta_spy/net_dollar_delta/target_hedge_shares
            # and get echoed back as `spy_spot`, rendering a plausible-but-
            # fake hedge recommendation on the Paper Broker screen instead
            # of an honest "unavailable" state.
            return {
                "symbol": "SPY",
                "available": False,
                "net_dollar_delta": None,
                "beta_weighted_delta_spy": None,
                "target_hedge_shares": None,
                "tolerance_band_shares": tolerance_band_shares,
                "action": "HOLD",
                "shares": 0.0,
                "required_action": False,
                "reason": "SPY spot price unavailable",
                "message": "Delta hedge preview unavailable: no live SPY quote available (refusing to fabricate a price).",
                "spy_spot": None,
            }

    if portfolio_greeks is None and store is not None:
        try:
            from pilots.options_risk import calculate_portfolio_greeks
            portfolio_greeks = calculate_portfolio_greeks(store=store, spy_spot=spy_spot)
        except Exception as e:
            logger.warning(f"Failed to calculate portfolio greeks dynamically: {e}")
            portfolio_greeks = {"beta_weighted_delta_spy": 0.0, "net_dollar_delta": 0.0}

    beta_delta = 0.0
    net_dollar_delta = 0.0
    if isinstance(portfolio_greeks, dict):
        beta_delta = float(portfolio_greeks.get("beta_weighted_delta_spy", 0.0) or 0.0)
        net_dollar_delta = float(portfolio_greeks.get("net_dollar_delta", 0.0) or 0.0)

    order = calculate_delta_hedge_order(
        portfolio_greeks=portfolio_greeks or {},
        spy_spot=spy_spot,
        tolerance_band_shares=tolerance_band_shares,
    )

    if order is None:
        return {
            "symbol": "SPY",
            "available": True,
            "net_dollar_delta": round(net_dollar_delta, 2),
            "beta_weighted_delta_spy": round(beta_delta, 2),
            "target_hedge_shares": round(-beta_delta, 2),
            "tolerance_band_shares": tolerance_band_shares,
            "action": "HOLD",
            "shares": 0.0,
            "required_action": False,
            "reason": f"Delta ({beta_delta:+.2f} SPY-equiv) is within tolerance band (±{tolerance_band_shares:.1f} shares)",
            "spy_spot": spy_spot,
        }
    else:
        preview = {
            "symbol": "SPY",
            "available": True,
            "net_dollar_delta": round(net_dollar_delta, 2),
            "beta_weighted_delta_spy": round(beta_delta, 2),
            "target_hedge_shares": round(order["shares_needed"], 2),
            "tolerance_band_shares": tolerance_band_shares,
            "action": order["side"].upper(),
            "shares": float(order["qty"]),
            "required_action": True,
            "reason": f"Delta imbalance ({beta_delta:+.2f} SPY-equiv) exceeds tolerance band (±{tolerance_band_shares:.1f} shares)",
            "spy_spot": spy_spot,
        }
        try:
            from pilots.options_alerts import dispatch_delta_hedge_alert
            dispatch_delta_hedge_alert(preview)
        except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
            logger.debug("Delta hedge preview alert dispatch failed: %s", exc)
        return preview


def execute_delta_hedge(
    store: Optional[PaperAccountStore] = None,
    portfolio_greeks: Optional[Union[Dict[str, Any], Any]] = None,
    spy_spot: Optional[float] = None,
    tolerance_band_shares: Optional[float] = None,
    dry_run: bool = False,
    shares_override: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculates and executes the delta hedge order via PaperAccountStore.apply_fill.
    """
    if store is None:
        try:
            store = PaperAccountStore()
        except Exception:
            logger.exception("Failed to initialize PaperAccountStore for delta hedging")
            return {
                "ok": False,
                "hedged": False,
                "reason": "PaperAccountStore unavailable",
                "order": None,
                "fill": None,
            }

    if spy_spot is None or spy_spot <= 0:
        try:
            from pilots.price_provider import get_current_price
            resolved_price = get_current_price("SPY")
            spy_spot = resolved_price if resolved_price > 0 else None
        except Exception:
            spy_spot = None

        if spy_spot is None:
            # No honest SPY price available -- refuse to execute rather than
            # fabricate one (CONSTRAINT #4): a fabricated price would be
            # written into the real paper-account ledger as fill_price below.
            return {
                "ok": False,
                "hedged": False,
                "action": "HOLD",
                "shares": 0.0,
                "symbol": "SPY",
                "order_id": None,
                "reason": "SPY spot price unavailable",
                "message": "Delta hedge not executed: no live SPY quote available (refusing to fill at a fabricated price).",
                "order": None,
                "fill": None,
            }

    if portfolio_greeks is None:
        try:
            from pilots.options_risk import calculate_portfolio_greeks
            portfolio_greeks = calculate_portfolio_greeks(store=store, spy_spot=spy_spot)
        except Exception as e:
            logger.warning(f"Failed to calculate portfolio greeks dynamically: {e}")
            portfolio_greeks = {"beta_weighted_delta_spy": 0.0}

    order = calculate_delta_hedge_order(
        portfolio_greeks=portfolio_greeks,
        spy_spot=spy_spot,
        tolerance_band_shares=tolerance_band_shares,
    )

    if order is None and shares_override is None:
        return {
            "ok": True,
            "hedged": False,
            "action": "HOLD",
            "shares": 0.0,
            "symbol": "SPY",
            "order_id": None,
            "reason": "Delta exposure is within tolerance deadband",
            "message": "Portfolio delta is within tolerance band. No hedge required.",
            "order": None,
            "fill": None,
        }

    client_order_id = f"hedge_spy_{uuid.uuid4().hex[:12]}"
    fill_price = float(spy_spot)
    # shares_override may carry its side as a sign (negative = sell); derive
    # `side` from that sign BEFORE taking the absolute value -- apply_fill's
    # cash/position math assumes a non-negative qty, so passing a negative
    # value through unabs'd inverts both the cash impact and the resulting
    # position's sign.
    raw_qty = float(shares_override) if shares_override is not None else float(order["qty"] if order else 0)
    qty = abs(raw_qty)
    side = str(order["side"]).lower() if order else ("buy" if raw_qty > 0 else "sell")

    if dry_run:
        return {
            "ok": True,
            "hedged": False,
            "dry_run": True,
            "action": side.upper(),
            "shares": qty,
            "symbol": "SPY",
            "order_id": client_order_id,
            "message": f"Dry run: {side.upper()} {int(qty)} SPY @ ${fill_price:.2f}",
        }

    # Commission: standard $0.005/share, min $1.00
    commission = max(1.0, round(qty * 0.005, 2))

    success = store.apply_fill(
        client_order_id=client_order_id,
        symbol="SPY",
        side=side,
        qty=qty,
        fill_price=fill_price,
        commission_and_fees=commission,
        allow_short=True,
    )

    if not success:
        return {
            "ok": False,
            "hedged": False,
            "action": side.upper(),
            "shares": qty,
            "symbol": "SPY",
            "order_id": client_order_id,
            "order": order,
            "fill": None,
            "message": f"Delta hedge order rejected by store for {side.upper()} {qty} SPY.",
        }

    # Dispatch delta hedge alert (non-blocking, deduped). dispatch_delta_hedge_alert's
    # qualifying gate reads `action`/`shares`/`required_action` (and, for the CRITICAL
    # vs WARNING level, `tolerance_band_shares`) -- `order`'s own shape (from
    # calculate_delta_hedge_order: `side`/`qty`/`shares_needed`) doesn't carry any of
    # those keys, so passing `order` straight through left every one of them at its
    # default (`action="HOLD"`, `required_action=False`) and the dispatcher's gate was
    # always False here -- this call silently never fired. Build the same preview-shaped
    # dict get_delta_hedge_preview() constructs instead, using values already resolved
    # in this function (we already have a filled order at this point, so
    # required_action=True is correct here).
    try:
        from pilots.options_alerts import dispatch_delta_hedge_alert
        if isinstance(portfolio_greeks, dict):
            beta_delta = float(portfolio_greeks.get("beta_weighted_delta_spy", 0.0) or 0.0)
            net_dollar_delta = float(portfolio_greeks.get("net_dollar_delta", 0.0) or 0.0)
        else:
            beta_delta = float(getattr(portfolio_greeks, "beta_weighted_delta_spy", 0.0) or 0.0)
            net_dollar_delta = float(getattr(portfolio_greeks, "net_dollar_delta", 0.0) or 0.0)
        resolved_tolerance = (
            float(tolerance_band_shares) if tolerance_band_shares is not None
            else float(getattr(settings, "OPTIONS_DELTA_HEDGE_BAND_SPY_SHARES", 25.0))
        )
        dispatch_delta_hedge_alert({
            "symbol": "SPY",
            "net_dollar_delta": round(net_dollar_delta, 2),
            "beta_weighted_delta_spy": round(beta_delta, 2),
            "target_hedge_shares": round(order["shares_needed"], 2) if order else round(raw_qty, 2),
            "tolerance_band_shares": resolved_tolerance,
            "action": side.upper(),
            "shares": qty,
            "required_action": True,
            "reason": f"Delta hedge order executed: {side.upper()} {int(qty)} SPY @ ${fill_price:.2f}",
            "spy_spot": spy_spot,
        })
    except Exception as exc:  # noqa: BLE001 — never raises (CONSTRAINT #6)
        logger.debug("Delta hedge execution alert dispatch failed: %s", exc)

    return {
        "ok": True,
        "hedged": True,
        "action": side.upper(),
        "shares": qty,
        "symbol": "SPY",
        "order_id": client_order_id,
        "order": order,
        "fill": {
            "symbol": "SPY",
            "side": side,
            "qty": qty,
            "fill_price": fill_price,
            "commission": commission,
        },
        "message": f"Delta hedge executed: {side.upper()} {int(qty)} SPY at ${fill_price:.2f} (commission: ${commission:.2f}).",
    }


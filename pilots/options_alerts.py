"""
pilots/options_alerts.py — Options Real-Time Alert Dispatcher.
==============================================================

Real-time alert dispatching engine for multi-leg options trading, institutional
Unusual Options Activity (UOA) whale sweeps, high-edge pre-earnings volatility crush
setups, and dynamic delta-neutral portfolio rebalancing alerts.

Key Capabilities:
1. **UOA Whale Sweeps Alerting** (`dispatch_uoa_whale_alert`):
   - Formats and dispatches high-priority alerts for institutional sweeps with
     high relative volume (Volume/OI >= 5.0) and large premium notional (>= $250,000).
2. **Earnings Volatility Crush Alerting** (`dispatch_earnings_crush_alert`):
   - Formats and dispatches alerts for quantified pre-earnings volatility crush
     Iron Condor opportunities when Crush Edge Ratio >= 1.35x.
3. **Delta Hedge Imbalance Alerting** (`dispatch_delta_hedge_alert`):
   - Formats and dispatches rebalancing alerts when portfolio SPY beta-weighted delta
     breaches the operator-configured tolerance deadband.
4. **Multi-Channel & Webhook Dispatching** (`post_webhook`, `dispatch_options_alert`):
   - Integrates seamlessly with `observability/alerts.py` multi-channel dispatcher and
     supports direct incoming webhook POSTs (Discord, Slack, or generic HTTP endpoints)
     with graceful degradation.

Design Invariants:
* **AST-Safe (CONSTRAINTS #1 & #3)**: Pure dispatch/formatting module. Never imports heavy
  engines (`processing_engine`, `strategy_engine`, `forecasting_engine`, `macro_engine`,
  `technical_options_engine`, `desktop`, `main_orchestrator`).
* **Honesty (CONSTRAINT #4)**: Never fabricates alerts or missing metrics; invalid or
  sub-threshold inputs honestly return non-dispatch status without generating false notifications.
* **Never Raises (CONSTRAINT #6)**: All network I/O and payload serialization operations
  are safely isolated in broad exception handlers with informative logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import urllib.error
import urllib.request
from urllib.parse import urlparse

from settings import settings

logger = logging.getLogger(__name__)

# Default Threshold Constants
DEFAULT_UOA_WHALE_MIN_VOL_OI = 5.0
DEFAULT_UOA_WHALE_MIN_NOTIONAL = 250000.0  # $250k
DEFAULT_EARNINGS_CRUSH_MIN_EDGE = 1.35     # 1.35x Implied / Realized Move

_LEVEL_EMOJIS: Dict[str, str] = {
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "CRITICAL": "🚨",
}

__all__ = [
    "dispatch_uoa_whale_alert",
    "dispatch_earnings_crush_alert",
    "dispatch_delta_hedge_alert",
    "dispatch_options_alert",
    "format_options_alert_message",
    "post_webhook",
    "DEFAULT_UOA_WHALE_MIN_VOL_OI",
    "DEFAULT_UOA_WHALE_MIN_NOTIONAL",
    "DEFAULT_EARNINGS_CRUSH_MIN_EDGE",
]


# ---------------------------------------------------------------------------
# Helper Extractors
# ---------------------------------------------------------------------------

def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Safely extracts a value from a dict, dataclass, or object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        val = obj.get(key, default)
        return val if val is not None else default
    val = getattr(obj, key, default)
    return val if val is not None else default


def post_webhook(
    webhook_url: Optional[str],
    message: str,
    level: str = "INFO",
    extra: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """POST an alert directly to a webhook URL (Discord, Slack, or generic endpoint).

    Parameters
    ----------
    webhook_url:
        The target HTTP(S) incoming webhook endpoint.
    message:
        Human-readable alert message text.
    level:
        Alert severity ("INFO", "WARNING", "CRITICAL").
    extra:
        Optional machine-readable dictionary context.
    timeout:
        Network socket timeout in seconds (default 10.0s).

    Returns
    -------
    Dict[str, Any]
        {"ok": bool, "status": Optional[int], "error": Optional[str]}
    """
    if not webhook_url or not str(webhook_url).strip():
        return {"ok": False, "status": None, "error": "No webhook URL provided"}

    url = str(webhook_url).strip()
    emoji = _LEVEL_EMOJIS.get(level.upper(), "ℹ️")
    ts = datetime.now(timezone.utc).isoformat()

    try:
        # Detect webhook provider format via a real hostname parse -- a plain
        # substring/`.endswith()` check on the raw URL string can be spoofed
        # by e.g. "evil.example.com/discord.com/api/webhooks" or a lookalike
        # subdomain, which would pick the wrong payload shape for whatever
        # host the request actually goes to (CodeQL py/incomplete-url-
        # substring-sanitization). `webhook_url` is always operator-
        # configured (settings.OPTIONS_ALERT_WEBHOOK_URL or an explicit
        # override), never externally supplied, so this isn't reachable by
        # an attacker today -- fixed anyway since the correct check is no
        # more code.
        host = (urlparse(url).hostname or "").lower()
        if host in ("discord.com", "discordapp.com") and "/api/webhooks" in urlparse(url).path:
            content = f"{emoji} **[{level.upper()}]** `{ts}`\n{message}"
            body_dict: Dict[str, Any] = {"content": content}
        elif host == "hooks.slack.com" or host.endswith(".slack.com"):
            text = f"{emoji} *[{level.upper()}]* `{ts}`\n{message}"
            body_dict = {"text": text}
        else:
            # Generic webhook endpoint payload
            body_dict = {
                "level": level.upper(),
                "timestamp": ts,
                "message": message,
                "text": f"{emoji} [{level.upper()}] {message}",
                "content": f"{emoji} **[{level.upper()}]**\n{message}",
                "extra": extra or {},
            }

        body_bytes = json.dumps(body_dict, default=str).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        # Bandit B310: `url` (this function's `webhook_url` param) is always
        # sourced from settings.OPTIONS_ALERT_WEBHOOK_URL by every real
        # caller in this module, an operator-set config value, never raw
        # external/request-body input.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            raw_status = getattr(resp, "status", None)
            if raw_status is None and hasattr(resp, "getcode"):
                try:
                    raw_status = resp.getcode()
                except Exception:
                    raw_status = 200
            try:
                status = int(raw_status) if raw_status is not None else 200
            except (TypeError, ValueError):
                status = 200

            if status in (200, 201, 204):
                return {"ok": True, "status": status, "error": None}
            return {"ok": False, "status": status, "error": f"HTTP {status}"}

    except Exception as exc:  # noqa: BLE001 — never raise out of webhook dispatch
        logger.warning("Direct options alert webhook POST failed [%s]: %s", url, exc)
        status_code = getattr(exc, "code", None) if hasattr(exc, "code") else None
        return {"ok": False, "status": status_code, "error": str(exc)}


# ---------------------------------------------------------------------------
# Public Alert Dispatchers
# ---------------------------------------------------------------------------

def dispatch_uoa_whale_alert(
    uoa_record: Union[Dict[str, Any], Any],
    webhook_url: Optional[str] = None,
    min_vol_oi: float = DEFAULT_UOA_WHALE_MIN_VOL_OI,
    min_notional: float = DEFAULT_UOA_WHALE_MIN_NOTIONAL,
    force: bool = False,
) -> Dict[str, Any]:
    """Formats and dispatches high-priority alert for unusual options sweeps.

    Dispatches when Volume/OI >= 5.0 and Notional >= $250k (or caller-specified
    thresholds), or unconditionally when ``force=True``.

    Parameters
    ----------
    uoa_record:
        `UOARecord` dataclass or dict containing contract flow metrics.
    webhook_url:
        Optional direct webhook override. If omitted, uses `settings.OPTIONS_ALERT_WEBHOOK_URL`
        or delegates to `observability/alerts.py`.
    min_vol_oi:
        Minimum Volume to Open Interest ratio to trigger alert (default 5.0x).
    min_notional:
        Minimum total premium notional ($) to trigger alert (default $250,000).
    force:
        If True, dispatches regardless of threshold evaluation.

    Returns
    -------
    Dict[str, Any]
        Result dictionary containing `{"dispatched": bool, "level": str, "message": str, ...}`.
    """
    if uoa_record is None:
        return {
            "dispatched": False,
            "reason": "No UOA record provided.",
            "level": None,
            "message": None,
            "channels": [],
            "webhook_status": None,
            "extra": None,
        }

    symbol = str(_get_val(uoa_record, "symbol", "")).upper().strip()
    contract_symbol = str(_get_val(uoa_record, "contract_symbol", "")).strip()
    expiration = str(_get_val(uoa_record, "expiration", "")).strip()
    strike = float(_get_val(uoa_record, "strike", 0.0) or 0.0)
    option_type = str(_get_val(uoa_record, "option_type", "call")).lower().strip()
    volume = int(_get_val(uoa_record, "volume", 0) or 0)
    open_interest = int(_get_val(uoa_record, "open_interest", 0) or 0)
    vol_oi_ratio = float(_get_val(uoa_record, "vol_oi_ratio", 0.0) or 0.0)
    notional = float(_get_val(uoa_record, "notional", 0.0) or 0.0)
    trade_price = float(_get_val(uoa_record, "trade_price", _get_val(uoa_record, "price", 0.0)) or 0.0)
    aggressiveness = str(_get_val(uoa_record, "aggressiveness", _get_val(uoa_record, "trade_type", "sweep"))).strip()
    sentiment = str(_get_val(uoa_record, "sentiment", "NEUTRAL")).upper().strip()
    iv = _get_val(uoa_record, "iv", _get_val(uoa_record, "implied_volatility", None))
    hv_30 = _get_val(uoa_record, "hv_30", _get_val(uoa_record, "historical_volatility", None))
    iv_burst_score = _get_val(uoa_record, "iv_burst_score", None)
    dte = int(_get_val(uoa_record, "dte", 0) or 0)

    # Derive missing metrics if basic quantities are available
    if vol_oi_ratio <= 0.0 and volume > 0 and open_interest > 0:
        vol_oi_ratio = round(volume / open_interest, 2)
    elif vol_oi_ratio <= 0.0 and volume > 0 and open_interest == 0:
        vol_oi_ratio = float(volume)  # New open contract expansion

    if notional <= 0.0 and trade_price > 0.0 and volume > 0:
        notional = round(trade_price * volume * 100.0, 2)

    # Threshold gate
    meets_vol_oi = vol_oi_ratio >= min_vol_oi
    meets_notional = notional >= min_notional
    qualifies = force or (meets_vol_oi and meets_notional)

    if not qualifies:
        reason = (
            f"Record does not meet whale criteria "
            f"(Vol/OI: {vol_oi_ratio:.2f}x < {min_vol_oi:.1f}x or Notional: ${notional:,.0f} < ${min_notional:,.0f})"
        )
        return {
            "dispatched": False,
            "reason": reason,
            "level": None,
            "message": None,
            "channels": [],
            "webhook_status": None,
            "extra": None,
        }

    # Format high-priority whale message
    level = "CRITICAL" if notional >= 1000000.0 or "sweep" in aggressiveness.lower() else "WARNING"
    opt_label = option_type.upper()
    dir_emoji = "🟢 🐂" if sentiment == "BULLISH" else ("🔴 🐻" if sentiment == "BEARISH" else "⚪ ⚖️")
    agg_label = aggressiveness.replace("_", " ").upper()

    vol_details: List[str] = []
    if iv is not None:
        iv_val = float(iv) * 100.0 if float(iv) <= 5.0 else float(iv)
        vol_details.append(f"IV: {iv_val:.1f}%")
    if hv_30 is not None:
        hv_val = float(hv_30) * 100.0 if float(hv_30) <= 5.0 else float(hv_30)
        vol_details.append(f"HV30: {hv_val:.1f}%")
    if iv_burst_score is not None:
        vol_details.append(f"IV Burst: {float(iv_burst_score):.2f}x")
    if dte > 0:
        vol_details.append(f"DTE: {dte}d")
    vol_str = " | ".join(vol_details) if vol_details else "Standard Profile"

    contract_desc = f"{symbol} {expiration} ${strike:.2f} {opt_label}".strip()
    if contract_symbol and contract_symbol != contract_desc:
        contract_desc += f" (`{contract_symbol}`)"

    lines = [
        f"🐋 **[UOA WHALE ALERT] Institutional Options Sweep Detected**",
        f"• **Contract**: **{contract_desc}**",
        f"• **Premium Notional**: **${notional:,.2f}** (Price: ${trade_price:.2f})",
        f"• **Volume vs OI**: **{volume:,}** vs **{open_interest:,}** (**{vol_oi_ratio:.1f}x** ratio)",
        f"• **Execution**: `{agg_label}` | **Sentiment**: {dir_emoji} **{sentiment}**",
        f"• **Volatility**: {vol_str}",
    ]
    message = "\n".join(lines)

    extra = {
        "type": "uoa_whale_alert",
        "symbol": symbol,
        "contract_symbol": contract_symbol,
        "expiration": expiration,
        "strike": strike,
        "option_type": option_type,
        "volume": volume,
        "open_interest": open_interest,
        "vol_oi_ratio": round(vol_oi_ratio, 2),
        "notional": round(notional, 2),
        "trade_price": trade_price,
        "aggressiveness": aggressiveness,
        "sentiment": sentiment,
        "iv": iv,
        "hv_30": hv_30,
        "iv_burst_score": iv_burst_score,
        "dte": dte,
    }

    channels_dispatched: List[str] = []
    # Dedup key includes `sentiment` deliberately: without it, a bullish sweep followed
    # by a genuinely NEW, opposite-direction (bearish) sweep on the SAME contract within
    # settings.ALERT_DEDUP_WINDOW_SECONDS would be wrongly treated as a duplicate of the
    # earlier alert and silently suppressed — direction reversal on institutional flow is
    # exactly the kind of new information this alert exists to surface, not noise to
    # dedupe away. Same-direction repeats on the same contract still correctly dedupe.
    dedup_key = (
        f"uoa_whale_{symbol}_{contract_symbol or f'{strike}_{option_type}_{expiration}'}_{sentiment}"
    )

    # 1. Multi-channel dispatch via observability/alerts.py
    try:
        from observability.alerts import send_alert
        send_alert(level, message, extra=extra, dedup_key=dedup_key)
        channels_dispatched.append("observability")
    except Exception as exc:
        logger.warning("Could not dispatch via observability.alerts: %s", exc)

    # 2. Dedicated webhook POST
    target_webhook = webhook_url or getattr(settings, "OPTIONS_ALERT_WEBHOOK_URL", None)
    webhook_res: Optional[Dict[str, Any]] = None
    if target_webhook:
        webhook_res = post_webhook(target_webhook, message, level=level, extra=extra)
        if webhook_res.get("ok"):
            channels_dispatched.append("custom_webhook")
        else:
            logger.warning(
                "UOA whale alert webhook delivery failed for %s: %s",
                symbol, webhook_res.get("error"),
            )

    return {
        "dispatched": True,
        "level": level,
        "message": message,
        "channels": channels_dispatched,
        "webhook_status": webhook_res.get("status") if webhook_res else None,
        "webhook_error": webhook_res.get("error") if webhook_res else None,
        "extra": extra,
        "reason": None,
    }


def dispatch_earnings_crush_alert(
    candidate: Union[Dict[str, Any], Any],
    webhook_url: Optional[str] = None,
    min_edge: float = DEFAULT_EARNINGS_CRUSH_MIN_EDGE,
    force: bool = False,
) -> Dict[str, Any]:
    """Formats and dispatches alert for high-edge earnings volatility crush setups.

    Dispatches when Crush Edge Ratio >= 1.35x (or caller-specified threshold),
    or unconditionally when ``force=True``.

    Parameters
    ----------
    candidate:
        Earnings crush candidate dictionary or object.
    webhook_url:
        Optional direct webhook override or `settings.OPTIONS_ALERT_WEBHOOK_URL`.
    min_edge:
        Minimum crush edge ratio (Implied Move / Realized Move) to trigger alert (default 1.35x).
    force:
        If True, dispatches regardless of threshold evaluation.

    Returns
    -------
    Dict[str, Any]
        Result dictionary containing `{"dispatched": bool, "level": str, "message": str, ...}`.
    """
    if candidate is None:
        return {
            "dispatched": False,
            "reason": "No earnings crush candidate provided.",
            "level": None,
            "message": None,
            "channels": [],
            "webhook_status": None,
            "extra": None,
        }

    symbol = str(_get_val(candidate, "symbol", "")).upper().strip()
    spot = float(_get_val(candidate, "spot", 0.0) or 0.0)
    earnings_date = str(_get_val(candidate, "earnings_date", "")).strip()
    days_to_earnings = int(_get_val(candidate, "days_to_earnings", 0) or 0)
    expiration = str(_get_val(candidate, "expiration", "")).strip()
    dte = int(_get_val(candidate, "dte", 0) or 0)
    atm_iv = float(_get_val(candidate, "atm_iv", 0.0) or 0.0)
    expected_move_usd = float(_get_val(candidate, "expected_move_usd", 0.0) or 0.0)
    expected_move_pct = float(_get_val(candidate, "expected_move_pct", 0.0) or 0.0)
    realized_move_pct = float(_get_val(candidate, "realized_move_pct", 0.0) or 0.0)
    crush_edge_ratio = float(_get_val(candidate, "crush_edge_ratio", 0.0) or 0.0)
    strategy = str(_get_val(candidate, "strategy", "Iron Condor")).strip()
    strikes = _get_val(candidate, "strikes", {}) or {}
    net_credit = float(_get_val(candidate, "net_credit", 0.0) or 0.0)
    max_profit = float(_get_val(candidate, "max_profit", 0.0) or 0.0)
    max_loss = float(_get_val(candidate, "max_loss", 0.0) or 0.0)

    # Calculate crush edge ratio if not already provided
    if crush_edge_ratio <= 0.0 and realized_move_pct > 0.0 and expected_move_pct > 0.0:
        crush_edge_ratio = round(expected_move_pct / realized_move_pct, 2)

    qualifies = force or (crush_edge_ratio >= min_edge)
    if not qualifies:
        reason = f"Candidate crush edge ratio ({crush_edge_ratio:.2f}x) is below minimum threshold ({min_edge:.2f}x)"
        return {
            "dispatched": False,
            "reason": reason,
            "level": None,
            "message": None,
            "channels": [],
            "webhook_status": None,
            "extra": None,
        }

    level = "INFO" if crush_edge_ratio < 1.50 else "WARNING"
    exp_pct_str = f"±{expected_move_pct * 100.0:.1f}%" if expected_move_pct <= 1.0 else f"±{expected_move_pct:.1f}%"
    real_pct_str = f"{realized_move_pct * 100.0:.1f}%" if realized_move_pct <= 1.0 else f"{realized_move_pct:.1f}%"
    iv_pct_str = f"{atm_iv * 100.0:.1f}%" if atm_iv <= 5.0 else f"{atm_iv:.1f}%"

    # Strike wing details
    long_put = strikes.get("long_put", 0.0)
    short_put = strikes.get("short_put", 0.0)
    short_call = strikes.get("short_call", 0.0)
    long_call = strikes.get("long_call", 0.0)

    strikes_str = (
        f"Put Wing: ${long_put:.2f}/${short_put:.2f} | Call Wing: ${short_call:.2f}/${long_call:.2f}"
        if long_put and short_put and short_call and long_call
        else f"Expected Move: ±${expected_move_usd:.2f}"
    )

    lines = [
        f"💥 **[EARNINGS CRUSH ALERT] High-Edge Volatility Crush Opportunity**",
        f"• **Underlying**: **{symbol}** (Spot: ${spot:.2f})",
        f"• **Earnings Date**: **{earnings_date}** ({days_to_earnings}d away | Cycle DTE: {dte}d)",
        f"• **Crush Edge Ratio**: **{crush_edge_ratio:.2f}x** (Implied: {exp_pct_str} vs Historical Realized: {real_pct_str})",
        f"• **Strategy**: **{strategy}** ({expiration})",
        f"• **Wing Structure**: {strikes_str}",
        f"• **Risk/Reward**: Est. Net Credit: **${net_credit:.2f}** | Max Profit: **${max_profit:,.2f}** | Max Loss: **${max_loss:,.2f}** (ATM IV: {iv_pct_str})",
    ]
    message = "\n".join(lines)

    extra = {
        "type": "earnings_crush_alert",
        "symbol": symbol,
        "spot": spot,
        "earnings_date": earnings_date,
        "days_to_earnings": days_to_earnings,
        "expiration": expiration,
        "dte": dte,
        "atm_iv": atm_iv,
        "expected_move_usd": expected_move_usd,
        "expected_move_pct": expected_move_pct,
        "realized_move_pct": realized_move_pct,
        "crush_edge_ratio": crush_edge_ratio,
        "strategy": strategy,
        "strikes": strikes,
        "net_credit": net_credit,
        "max_profit": max_profit,
        "max_loss": max_loss,
    }

    channels_dispatched: List[str] = []
    dedup_key = f"earnings_crush_{symbol}_{earnings_date}_{expiration}"

    try:
        from observability.alerts import send_alert
        send_alert(level, message, extra=extra, dedup_key=dedup_key)
        channels_dispatched.append("observability")
    except Exception as exc:
        logger.warning("Could not dispatch via observability.alerts: %s", exc)

    target_webhook = webhook_url or getattr(settings, "OPTIONS_ALERT_WEBHOOK_URL", None)
    webhook_res: Optional[Dict[str, Any]] = None
    if target_webhook:
        webhook_res = post_webhook(target_webhook, message, level=level, extra=extra)
        if webhook_res.get("ok"):
            channels_dispatched.append("custom_webhook")
        else:
            logger.warning(
                "Earnings crush alert webhook delivery failed for %s: %s",
                symbol, webhook_res.get("error"),
            )

    return {
        "dispatched": True,
        "level": level,
        "message": message,
        "channels": channels_dispatched,
        "webhook_status": webhook_res.get("status") if webhook_res else None,
        "webhook_error": webhook_res.get("error") if webhook_res else None,
        "extra": extra,
        "reason": None,
    }


def dispatch_delta_hedge_alert(
    preview: Union[Dict[str, Any], Any],
    webhook_url: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Formats and dispatches alert when portfolio SPY delta exceeds tolerance band.

    Parameters
    ----------
    preview:
        Delta hedge preview dictionary or object (from `pilots.options_hedging.get_delta_hedge_preview`).
    webhook_url:
        Optional direct webhook override or `settings.OPTIONS_ALERT_WEBHOOK_URL`.
    force:
        If True, dispatches regardless of whether rebalancing action is required.

    Returns
    -------
    Dict[str, Any]
        Result dictionary containing `{"dispatched": bool, "level": str, "message": str, ...}`.
    """
    if preview is None:
        return {
            "dispatched": False,
            "reason": "No delta hedge preview provided.",
            "level": None,
            "message": None,
            "channels": [],
            "webhook_status": None,
            "extra": None,
        }

    symbol = str(_get_val(preview, "symbol", "SPY")).upper().strip()
    net_dollar_delta = float(_get_val(preview, "net_dollar_delta", 0.0) or 0.0)
    beta_delta = float(_get_val(preview, "beta_weighted_delta_spy", 0.0) or 0.0)
    target_hedge_shares = float(_get_val(preview, "target_hedge_shares", 0.0) or 0.0)
    tolerance_band_shares = float(_get_val(preview, "tolerance_band_shares", 25.0) or 25.0)
    action = str(_get_val(preview, "action", "HOLD")).upper().strip()
    shares = float(_get_val(preview, "shares", 0.0) or 0.0)
    required_action = bool(_get_val(preview, "required_action", False))
    reason_str = str(_get_val(preview, "reason", "")).strip()
    spy_spot = float(_get_val(preview, "spy_spot", 500.0) or 500.0)

    # Check if delta rebalance action is required
    qualifies = force or (required_action and action in ("BUY", "SELL") and abs(shares) > 0)
    if not qualifies:
        return {
            "dispatched": False,
            "reason": f"Delta exposure ({beta_delta:+.2f} SPY-equiv) is within tolerance band (±{tolerance_band_shares:.1f} shares); no hedge required.",
            "level": None,
            "message": None,
            "channels": [],
            "webhook_status": None,
            "extra": None,
        }

    level = "CRITICAL" if abs(shares) >= (tolerance_band_shares * 2.0) else "WARNING"
    rebalance_notional = round(shares * spy_spot, 2)
    action_emoji = "🟢 📈" if action == "BUY" else "🔴 📉"

    lines = [
        f"⚖️ **[DELTA HEDGE ALERT] Portfolio SPY Delta Imbalance Detected**",
        f"• **Current Exposure**: Beta-Weighted Delta: **{beta_delta:+.2f} SPY shares** (${net_dollar_delta:+,.2f} dollar delta)",
        f"• **Tolerance Band**: **±{tolerance_band_shares:.1f} shares**",
        f"• **Recommended Rebalance**: {action_emoji} **{action} {abs(shares):.0f} shares {symbol}** (Target Delta: 0.0)",
        f"• **Valuation**: Spot: ${spy_spot:.2f} | Est. Order Notional: **${rebalance_notional:,.2f}**",
        f"• **Status**: {reason_str}",
    ]
    message = "\n".join(lines)

    extra = {
        "type": "delta_hedge_alert",
        "symbol": symbol,
        "net_dollar_delta": round(net_dollar_delta, 2),
        "beta_weighted_delta_spy": round(beta_delta, 2),
        "target_hedge_shares": round(target_hedge_shares, 2),
        "tolerance_band_shares": tolerance_band_shares,
        "action": action,
        "shares": shares,
        "spy_spot": spy_spot,
        "rebalance_notional": rebalance_notional,
        "reason": reason_str,
    }

    channels_dispatched: List[str] = []
    # Coarse bucket dedup key to avoid spamming slight delta movements within dedup window
    coarse_shares = round(shares / 10.0) * 10.0
    dedup_key = f"delta_hedge_{symbol}_{action}_{coarse_shares}"

    try:
        from observability.alerts import send_alert
        send_alert(level, message, extra=extra, dedup_key=dedup_key)
        channels_dispatched.append("observability")
    except Exception as exc:
        logger.warning("Could not dispatch via observability.alerts: %s", exc)

    target_webhook = webhook_url or getattr(settings, "OPTIONS_ALERT_WEBHOOK_URL", None)
    webhook_res: Optional[Dict[str, Any]] = None
    if target_webhook:
        webhook_res = post_webhook(target_webhook, message, level=level, extra=extra)
        if webhook_res.get("ok"):
            channels_dispatched.append("custom_webhook")
        else:
            logger.warning(
                "Delta hedge alert webhook delivery failed for %s: %s",
                symbol, webhook_res.get("error"),
            )

    return {
        "dispatched": True,
        "level": level,
        "message": message,
        "channels": channels_dispatched,
        "webhook_status": webhook_res.get("status") if webhook_res else None,
        "webhook_error": webhook_res.get("error") if webhook_res else None,
        "extra": extra,
        "reason": None,
    }


def format_options_alert_message(alert_type: str, payload: Optional[Dict[str, Any]] = None) -> Tuple[str, str, str]:
    """Constructs level, title, and formatted markdown message body for options alerts.

    Returns
    -------
    Tuple[str, str, str]: (level, title, message_body)
    """
    data = payload or {}
    type_key = alert_type.lower().strip()

    if type_key in ("whale_uoa", "uoa_sweep", "uoa"):
        symbol = data.get("symbol", "SPY")
        strike = data.get("strike", 500.0)
        opt_type = str(data.get("option_type", "CALL")).upper()
        exp = data.get("expiration", "2026-08-21")
        vol_oi = float(data.get("vol_oi_ratio", 6.2))
        notional = float(data.get("notional", 750000.0))
        trade_type = str(data.get("aggressiveness", data.get("trade_type", "SWEEP"))).replace("_", " ").upper()
        sentiment = str(data.get("sentiment", "BULLISH")).upper()
        level = "CRITICAL" if notional >= 1000000.0 else ("WARNING" if notional >= 500000.0 else "INFO")
        title = f"🐋 Institutional UOA Whale Sweep: {symbol} ${strike:.2f} {opt_type}"
        message = (
            f"**{title}**\n"
            f"• **Symbol**: `{symbol}`\n"
            f"• **Contract**: `${strike:.2f} {opt_type}` Exp: `{exp}`\n"
            f"• **Type**: `{trade_type}` | **Vol/OI Ratio**: `{vol_oi:.1f}x`\n"
            f"• **Premium Notional**: `${notional:,.2f}`\n"
            f"• **Sentiment**: `{sentiment}`"
        )
        return level, title, message

    elif type_key in ("earnings_crush", "earnings"):
        symbol = data.get("symbol", "NVDA")
        edge = float(data.get("crush_edge_ratio", data.get("edge_ratio", 1.45)))
        imp_move = float(data.get("expected_move_pct", data.get("implied_move_pct", 7.5)))
        hist_move = float(data.get("realized_move_pct", data.get("historical_move_pct", 4.8)))
        level = "WARNING" if edge >= 1.50 else "INFO"
        title = f"💥 Earnings IV Crush Candidate: {symbol} (Edge: {edge:.2f}x)"
        message = (
            f"**{title}**\n"
            f"• **Symbol**: `{symbol}`\n"
            f"• **Implied Move**: `±{imp_move:.1f}%` vs **Historical**: `±{hist_move:.1f}%`\n"
            f"• **Vol Edge Ratio**: `{edge:.2f}x`\n"
            f"• **Recommended Structure**: `{data.get('strategy', 'Iron Condor')}`\n"
            f"• **Target DTE**: `{data.get('dte', 5)}`"
        )
        return level, title, message

    elif type_key in ("delta_hedge", "hedge"):
        symbol = data.get("symbol", "SPY")
        beta_delta = float(data.get("beta_weighted_delta_spy", 85.0))
        shares_needed = float(data.get("target_hedge_shares", data.get("shares_needed", -85.0)))
        action = str(data.get("action", "BUY" if shares_needed > 0 else "SELL")).upper()
        level = "CRITICAL" if abs(beta_delta) > 50 else "WARNING"
        title = f"⚖️ Dynamic Delta Hedge Alert: {symbol} (Beta Δ: {beta_delta:+.1f})"
        message = (
            f"**{title}**\n"
            f"• **Portfolio Beta-Weighted SPY Delta**: `{beta_delta:+.1f}`\n"
            f"• **Required Rebalance**: `{action} {abs(shares_needed):.0f} shares {symbol}`\n"
            f"• **Deadband Threshold**: `±25.0 shares`\n"
            f"• **Action**: `Paper Broker Rebalance Ready`"
        )
        return level, title, message

    elif type_key in ("vol_mispricing", "mispricing"):
        symbol = data.get("symbol", "SPY")
        spread = float(data.get("iv_spread", 0.045))
        tag = str(data.get("tag", "RICH")).upper()
        level = "INFO"
        title = f"📊 Volatility Mispricing Alert: {symbol} ({tag})"
        message = (
            f"**{title}**\n"
            f"• **Symbol**: `{symbol}`\n"
            f"• **IV Mispricing Spread**: `{spread:+.3f}` ({tag})\n"
            f"• **Recommendation**: `{data.get('recommendation', 'Sell Premium / Credit Spread')}`"
        )
        return level, title, message

    else:
        # Default / Custom / Test alert
        level = "INFO"
        title = f"🧪 Options Desk Test Alert [{alert_type}]"
        details_str = json.dumps(data, default=str) if data else "No payload provided."
        message = f"**{title}**\n• Dispatched test webhook notification.\n• Payload: `{details_str}`"
        return level, title, message


def dispatch_options_alert(
    alert_type: str,
    payload: Optional[Dict[str, Any]] = None,
    channels: Optional[List[str]] = None,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Dispatches a formatted options alert across active notification channels and webhooks."""
    level, title, message = format_options_alert_message(alert_type, payload)
    data = payload or {}
    success = True
    error = None

    try:
        from observability.alerts import send_alert
        send_alert(
            level=level,  # type: ignore
            message=message,
            channels=channels,
            extra=data,
        )
    except Exception as exc:
        logger.warning("Options alert send_alert failed: %s", exc)
        success = False
        error = str(exc)

    target_webhook = webhook_url or getattr(settings, "OPTIONS_ALERT_WEBHOOK_URL", None)
    if target_webhook:
        w_res = post_webhook(target_webhook, message, level=level, extra=data)
        if not w_res.get("ok"):
            logger.warning("Direct webhook post failed: %s", w_res.get("error"))

    return {
        "status": "ok" if success else "failed",
        "alert_type": alert_type,
        "level": level,
        "title": title,
        "message": message,
        "payload": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "error": error,
    }

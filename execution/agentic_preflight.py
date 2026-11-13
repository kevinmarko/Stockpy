"""Agent 3 (Portfolio & Risk Manager) preflight for the Robinhood queue: a pure, veto-only check of every queued intent against the LIVE Agentic account (cumulative buying power, concentration, portfolio heat, notional cap, sellable shares, pending approvals, staleness, kill switch). It never adds, enlarges or flips an order; it only removes them, with a stable reason code."""

# =============================================================================
# MODULE: AGENTIC PREFLIGHT  (four-agent Robinhood plan, Phase 2)
# File: execution/agentic_preflight.py
#
# Plan: .claude/robinhood_four_agent_phase2_implementation_plan.md
#
# The daemon's queue (execution/queue_builder.py) is gated against the
# daemon's own account SNAPSHOT. Between that snapshot and the moment an order
# is sent, the live Robinhood Agentic account can differ (cash spent, shares
# sold, an approval from an earlier run still pending). This module re-checks
# every intent against live state the rh-risk-manager agent fetched over the
# Robinhood MCP, and resolves the exact share quantity / limit price the
# Trader (rh-trader / the robinhood-execution skill) will send.
#
# Shape: parsers (`AccountState.from_mcp`, `quotes_from_mcp`,
# `approvals_from_mcp`) turn raw MCP JSON into small frozen dataclasses;
# `preflight()` is a PURE function of those inputs (no I/O, no MCP, no clock
# reads — `now` is a parameter), so every rule is unit-testable;
# `write_preflight()` is the one atomic writer; `main()` is a thin CLI.
#
# Honesty (CONSTRAINT #4) / fail-closed (CONSTRAINT #6): a value that cannot
# be read is None, never 0.0; any check that needs a missing value VETOES the
# intent with a reason instead of passing it. Parsers never raise.
#
# This file defines no function or class named like an order-submission call
# (pinned by tests/test_pipeline_smoke.py::_EXECUTION_ZONE_GUARDED_FILES).
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from execution.receipts_store import make_dedup_key

logger = logging.getLogger(__name__)

PREFLIGHT_FILENAME = "preflight.json"
DEFAULT_STALE_AFTER_MINUTES = 30.0
_EPS = 1e-9

# --- Stable reason codes (the operator and the tests both read these) --------
# Run-level blockers: veto every intent.
KILL_SWITCH_ACTIVE = "kill_switch_active"
QUEUE_STALE = "queue_stale"
QUEUE_TIMESTAMP_UNAVAILABLE = "queue_timestamp_unavailable"
MODE_OFF = "mode_off"
PENDING_APPROVAL_UNIDENTIFIED = "pending_approval_unidentified"
# Per-intent vetoes.
GATE_BLOCKED = "gate_blocked"
ALREADY_SUBMITTED_TODAY = "already_submitted_today"
APPROVAL_ALREADY_PENDING = "approval_already_pending"
QUOTE_UNAVAILABLE = "quote_unavailable"
INTENT_MALFORMED = "intent_malformed"
QTY_BELOW_ONE_SHARE = "qty_below_one_share"
NOTIONAL_CAP_UNSET = "notional_cap_unset"
OVER_NOTIONAL_CAP = "over_notional_cap"
POSITIONS_UNAVAILABLE = "positions_unavailable"
SELL_EXCEEDS_SELLABLE = "sell_exceeds_sellable"
EARNINGS_WITHIN_BLACKOUT = "earnings_within_blackout"
EARNINGS_DATE_UNAVAILABLE = "earnings_date_unavailable"
ACCOUNT_VALUES_UNAVAILABLE = "account_values_unavailable"
OVER_POSITION_WEIGHT = "over_position_weight"
BUYING_POWER_EXHAUSTED = "buying_power_exhausted"
OVER_PORTFOLIO_GROSS = "over_portfolio_gross"
# Portfolio heat on the AGENTIC account (BUYs only). The daemon's queue no
# longer runs the risk gate's heat check: its snapshot is the main account.
PORTFOLIO_HEAT_OVER_LIMIT = "portfolio_heat_over_limit"
PORTFOLIO_HEAT_UNAVAILABLE = "portfolio_heat_unavailable"
# Plan check 10 (Phase 3) — only when the caller passes critical_gap_fields.
THESIS_MISSING = "thesis_missing"
THESIS_INVALID = "thesis_invalid"
CRITICAL_DATA_GAP = "critical_data_gap"


# ---------------------------------------------------------------------------
# Small coercion helpers (never raise, never fabricate)
# ---------------------------------------------------------------------------

def _num(x: Any) -> Optional[float]:
    """Finite float, or None. ``"12.3"`` → 12.3; ``None``/``""``/NaN/garbage → None."""
    if x is None or x == "" or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _pos(x: Any) -> Optional[float]:
    """Strictly positive finite float, or None (a 0.0 price is 'no price')."""
    v = _num(x)
    return v if v is not None and v > 0 else None


def _unwrap(payload: Any) -> Any:
    """MCP tools return ``{"data": {...}, "guide": ...}``; accept either shape."""
    if isinstance(payload, Mapping) and "data" in payload and isinstance(payload["data"], (Mapping, list)):
        return payload["data"]
    return payload


def _pages(payload: Any) -> List[Any]:
    """A paginated tool may be saved as one response or a list of page responses."""
    if isinstance(payload, list) and payload and all(isinstance(p, Mapping) and "data" in p for p in payload):
        return [_unwrap(p) for p in payload]
    return [_unwrap(payload)]


def _parse_dt(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Live inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Holding:
    qty: Optional[float]
    sellable: Optional[float]   # Robinhood's shares_available_for_sells
    avg_cost: Optional[float] = None  # per-share cost basis; None = not reported


@dataclass(frozen=True)
class AccountState:
    """The live Agentic account, as read over the MCP.

    ``positions is None`` means the positions could not be read (distinct from
    ``{}`` = read fine, holds nothing).
    """
    total_value: Optional[float]
    equity_value: Optional[float]
    buying_power: Optional[float]
    positions: Optional[Dict[str, Holding]]

    @classmethod
    def from_mcp(cls, portfolio: Any, positions: Any) -> "AccountState":
        p = _unwrap(portfolio) if portfolio is not None else None
        total = equity = bp = None
        if isinstance(p, Mapping):
            total = _num(p.get("total_value"))
            equity = _num(p.get("equity_value"))
            raw_bp = p.get("buying_power")
            bp = _num(raw_bp.get("buying_power")) if isinstance(raw_bp, Mapping) else _num(raw_bp)

        holdings: Optional[Dict[str, Holding]] = None
        if positions is not None:
            try:
                holdings = {}
                for page in _pages(positions):
                    rows = page.get("positions") if isinstance(page, Mapping) else None
                    if not isinstance(rows, list):
                        raise ValueError("no positions list")
                    for row in rows:
                        sym = str((row or {}).get("symbol") or "").upper()
                        if not sym:
                            raise ValueError("position without symbol")
                        avg = _pos(row.get("average_buy_price"))
                        if avg is None:
                            avg = _pos(row.get("average_cost"))
                        holdings[sym] = Holding(
                            qty=_num(row.get("quantity")),
                            sellable=_num(row.get("shares_available_for_sells")),
                            avg_cost=avg,
                        )
            except Exception as exc:  # unreadable → unknown, never "holds nothing"
                logger.warning("agentic_preflight: positions unreadable (%s)", exc)
                holdings = None
        return cls(total_value=total, equity_value=equity, buying_power=bp, positions=holdings)


@dataclass(frozen=True)
class Quote:
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    as_of: Optional[str]

    def price_for(self, side: str) -> Optional[float]:
        """BUY prices off the ask, SELL off the bid (the conservative side), else last."""
        if side == "buy":
            return self.ask or self.last
        return self.bid or self.last


def quotes_from_mcp(payload: Any) -> Dict[str, Quote]:
    """Parse ``get_equity_quotes`` output. Halted / never-traded symbols are dropped."""
    out: Dict[str, Quote] = {}
    data = _unwrap(payload)
    rows = data.get("results") if isinstance(data, Mapping) else data
    if not isinstance(rows, list):
        return out
    for row in rows:
        q = row.get("quote", row) if isinstance(row, Mapping) else None
        if not isinstance(q, Mapping):
            continue
        sym = str(q.get("symbol") or "").upper()
        if not sym:
            continue
        if q.get("state") not in (None, "active") or q.get("has_traded") is False:
            continue
        quote = Quote(bid=_pos(q.get("bid_price")), ask=_pos(q.get("ask_price")),
                      last=_pos(q.get("last_trade_price")),
                      as_of=q.get("venue_last_trade_time"))
        if quote.bid or quote.ask or quote.last:
            out[sym] = quote
    return out


def approvals_from_mcp(payload: Any) -> Tuple[List[Tuple[str, str]], int]:
    """Parse ``get_trade_approvals`` (pending list) into ``[(SYMBOL, side)]``.

    Returns ``(identified, n_unidentified)``. Robinhood's approval item fields
    are not documented, so this looks in the obvious places; an item it cannot
    tie to a symbol and side is COUNTED, never ignored — the caller blocks the
    run on it rather than risk a duplicate request.
    """
    identified: List[Tuple[str, str]] = []
    unidentified = 0
    for page in _pages(payload):
        items = page.get("approvals") if isinstance(page, Mapping) else None
        if not isinstance(items, list):
            continue
        for item in items:
            sym, side = _approval_symbol_side(item)
            if sym and side:
                identified.append((sym, side))
            else:
                unidentified += 1
    return identified, unidentified


def _approval_symbol_side(item: Any) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(item, Mapping):
        return None, None
    candidates = [item] + [v for k, v in item.items()
                           if isinstance(v, Mapping) and k in ("order", "equity_order", "trade", "details")]
    sym = side = None
    for c in candidates:
        sym = sym or (str(c.get("symbol")).upper() if c.get("symbol") else None)
        raw_side = c.get("side")
        if raw_side and not side:
            s = str(raw_side).lower().replace("side_", "")
            side = s if s in ("buy", "sell") else None
    return sym, side


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class IntentVerdict:
    client_order_id: str
    symbol: str
    side: str
    conviction: Optional[float]
    passed: bool = False
    placeable: bool = False
    reasons: List[str] = field(default_factory=list)
    qty: Optional[float] = None
    order_type: Optional[str] = None
    limit_price: Optional[float] = None
    ref_price: Optional[float] = None
    quote_as_of: Optional[str] = None
    est_notional: Optional[float] = None
    target_notional: Optional[float] = None


@dataclass
class PreflightReport:
    queue_generated_at: Optional[str]
    mode: str
    evaluated_at: str
    run_blockers: List[str]
    verdicts: List[IntentVerdict]
    limits: Dict[str, Optional[float]]

    @property
    def n_passed(self) -> int:
        return sum(1 for v in self.verdicts if v.passed)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["n_intents"] = len(self.verdicts)
        d["n_passed"] = self.n_passed
        return d


# ---------------------------------------------------------------------------
# The preflight
# ---------------------------------------------------------------------------

def _settings_default(name: str) -> Optional[float]:
    try:
        from settings import settings  # local import — keep the module import-light
        return _num(getattr(settings, name))
    except Exception as exc:
        logger.warning("agentic_preflight: settings.%s unavailable (%s)", name, exc)
        return None


def portfolio_heat(account: AccountState, quotes: Mapping[str, Quote]) -> Optional[float]:
    """Adverse open P&L on the live account as a fraction of its total value.

    The same measure as ``PreTradeRiskGate.portfolio_heat_check``: the sum of
    the losses on losing positions (winners don't offset), divided by account
    value. Priced off the last trade, else the bid. ``0.0`` when the account
    holds nothing. ``None`` (unmeasurable, the caller vetoes) when the
    positions or the account value can't be read, or any held position with
    shares lacks a cost basis or a quote. A heat computed over only some
    positions would understate it, so a single gap makes it unknown.
    """
    if account.positions is None:
        return None
    adverse = 0.0
    for sym, h in account.positions.items():
        if h.qty is None:
            return None
        if h.qty <= _EPS:
            continue
        q = quotes.get(sym)
        price = (q.last or q.bid) if q is not None else None
        if h.avg_cost is None or price is None:
            return None
        adverse += max(0.0, (h.avg_cost - price) * h.qty)
    if adverse <= 0.0:
        return 0.0
    if account.total_value is None or account.total_value <= 0:
        return None
    return adverse / account.total_value


def _resolve_quantity(intent: Mapping[str, Any], side: str, quote: Quote,
                      holding: Optional[Holding]) -> Tuple[Optional[float], Optional[float], Optional[float], List[str]]:
    """Return ``(qty, price_used, limit_price, reasons)`` — the skill's sizing rules, in code.

    BUY: ``floor(target_notional / price)``, price = the limit price for a
    limit intent (sized off the limit, so the cap check stays honest) else the
    ask. SELL full exit: the queue's ``qty``. SELL trim (qty null): sized like a
    BUY, then capped at the sellable shares (the skill's rule; a trim is never
    allowed to oversell).
    """
    reasons: List[str] = []
    ref = quote.price_for(side)
    if ref is None:
        return None, None, None, [QUOTE_UNAVAILABLE]

    bps = _num(intent.get("limit_offset_bps")) or 0.0
    is_limit = str(intent.get("order_type") or "market") == "limit" and bps > 0
    limit_price: Optional[float] = None
    price = ref
    if is_limit:
        factor = 1 + bps / 10_000 if side == "buy" else 1 - bps / 10_000
        limit_price = round(ref * factor, 2)
        if limit_price <= 0:
            return None, ref, None, [QUOTE_UNAVAILABLE]
        price = limit_price

    queue_qty = _num(intent.get("qty"))
    target = _pos(intent.get("target_notional"))
    if side == "sell" and queue_qty is not None:
        # Full exit: exactly the queue's quantity, fractional shares included —
        # flooring would silently leave a remainder behind.
        if queue_qty <= 0:
            return None, price, limit_price, [INTENT_MALFORMED]
        return float(queue_qty), price, limit_price, reasons
    elif target is not None:
        qty = float(math.floor(target / price + _EPS))
        if side == "sell" and holding is not None and holding.sellable is not None:
            qty = min(qty, math.floor(holding.sellable + _EPS))
    else:
        return None, price, limit_price, [INTENT_MALFORMED]

    if qty is None or qty < 1:
        reasons.append(QTY_BELOW_ONE_SHARE)
    return float(qty) if qty is not None else None, price, limit_price, reasons


def preflight(
    queue: Mapping[str, Any],
    account: AccountState,
    quotes: Mapping[str, Quote],
    *,
    now: datetime,
    pending_approvals: Iterable[Tuple[str, str]] = (),
    unidentified_pending_approvals: int = 0,
    placed_dedup_keys: Iterable[str] = (),
    kill_switch_active: bool,
    max_notional: Optional[float] = None,
    max_position_weight: Optional[float] = None,
    max_portfolio_gross: Optional[float] = None,
    max_portfolio_heat: Optional[float] = None,
    stale_after_minutes: float = DEFAULT_STALE_AFTER_MINUTES,
    earnings_dates: Optional[Mapping[str, Optional[date]]] = None,
    earnings_blackout_days: Optional[int] = None,
    theses: Optional[Mapping[str, Mapping[str, Any]]] = None,
    invalid_thesis_ids: Iterable[str] = (),
    critical_gap_fields: Iterable[str] = (),
) -> PreflightReport:
    """Veto-only check of every queue intent against the live account.

    Pure: the same inputs always give the same report. Never adds, removes,
    enlarges or flips an intent — one verdict per queue intent, same side, and
    a BUY's ``est_notional`` never exceeds its queue ``target_notional``.
    ``passed`` means "survived every check"; ``placeable`` additionally needs
    ``mode == "live"`` (a ``review`` run shows what WOULD pass, nothing more).

    Plan check 10 (opt-in, off by default): with ``critical_gap_fields`` set,
    an intent is vetoed when its Analyst thesis is missing (``theses`` holds
    only VALID theses, keyed by client_order_id), was rejected by
    ``execution.agentic_research.validate_theses`` (``invalid_thesis_ids``),
    or lists one of those fields in its ``data_gaps``.
    """
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    mode = str(queue.get("mode") or "off")
    cap = max_notional if max_notional is not None else _settings_default("ROBINHOOD_MAX_NOTIONAL_PER_ORDER")
    weight_cap = max_position_weight if max_position_weight is not None else _settings_default("MAX_POSITION_WEIGHT")
    gross_cap = max_portfolio_gross if max_portfolio_gross is not None else _settings_default("MAX_PORTFOLIO_GROSS")
    heat_cap = max_portfolio_heat if max_portfolio_heat is not None else _settings_default("MAX_PORTFOLIO_HEAT")
    heat = portfolio_heat(account, quotes)

    # -- run-level blockers --------------------------------------------------
    blockers: List[str] = []
    if kill_switch_active or queue.get("kill_switch_active"):
        blockers.append(KILL_SWITCH_ACTIVE)
    if mode not in ("live", "review"):
        blockers.append(MODE_OFF)
    gen = _parse_dt(queue.get("generated_at"))
    if gen is None:
        blockers.append(QUEUE_TIMESTAMP_UNAVAILABLE)
    elif (now - gen).total_seconds() / 60.0 > stale_after_minutes:
        blockers.append(QUEUE_STALE)
    if unidentified_pending_approvals > 0:
        blockers.append(PENDING_APPROVAL_UNIDENTIFIED)

    pending = {(s.upper(), d.lower()) for s, d in pending_approvals}
    critical = {str(f) for f in critical_gap_fields}
    invalid_theses = {str(c) for c in invalid_thesis_ids}
    placed = set(placed_dedup_keys)
    verdicts: List[IntentVerdict] = []

    for raw in queue.get("intents") or []:
        intent = raw if isinstance(raw, Mapping) else {}
        symbol = str(intent.get("symbol") or "").upper()
        side = str(intent.get("side") or "").lower()
        v = IntentVerdict(
            client_order_id=str(intent.get("client_order_id") or ""),
            symbol=symbol, side=side,
            conviction=_num(intent.get("conviction")),
            order_type=str(intent.get("order_type") or "market"),
            target_notional=_num(intent.get("target_notional")),
        )
        verdicts.append(v)
        if not symbol or side not in ("buy", "sell") or not v.client_order_id:
            v.reasons.append(INTENT_MALFORMED)
            continue

        # 1. the daemon's gate is final, never re-litigated upward
        gate_ok = intent.get("allow_place") is True if mode == "live" else intent.get("gate_allowed") is True
        if not gate_ok:
            v.reasons.append(GATE_BLOCKED)
        # 2. one request per order
        if make_dedup_key(symbol, side, now) in placed:
            v.reasons.append(ALREADY_SUBMITTED_TODAY)
        if (symbol, side) in pending:
            v.reasons.append(APPROVAL_ALREADY_PENDING)
        # 10. (opt-in) the Analyst's thesis must exist, validate, and not lack critical data
        if critical:
            thesis = (theses or {}).get(v.client_order_id)
            if v.client_order_id in invalid_theses:
                v.reasons.append(THESIS_INVALID)
            elif not isinstance(thesis, Mapping):
                v.reasons.append(THESIS_MISSING)
            elif critical & {str(g) for g in (thesis.get("data_gaps") or [])}:
                v.reasons.append(CRITICAL_DATA_GAP)

        holding = account.positions.get(symbol) if account.positions is not None else None
        if side == "sell" and account.positions is None:
            v.reasons.append(POSITIONS_UNAVAILABLE)

        # 3-4. live quote + exact quantity
        quote = quotes.get(symbol)
        if quote is None:
            v.reasons.append(QUOTE_UNAVAILABLE)
            continue
        qty, price, limit_price, qreasons = _resolve_quantity(intent, side, quote, holding)
        v.reasons.extend(qreasons)
        v.qty, v.ref_price, v.limit_price, v.quote_as_of = qty, price, limit_price, quote.as_of
        if qty is not None and price is not None:
            v.est_notional = round(qty * price, 2)

        if side == "buy":
            # 5. per-order notional cap
            if cap is None or cap <= 0:
                v.reasons.append(NOTIONAL_CAP_UNSET)
            elif v.est_notional is not None and v.est_notional > cap + _EPS:
                v.reasons.append(OVER_NOTIONAL_CAP)
            # 7. earnings blackout (off unless the caller passes a window)
            # earnings_dates[SYM] = a date, or None meaning "looked, no report
            # scheduled"; a symbol absent from the map was never checked → veto.
            if earnings_blackout_days is not None:
                ed = (earnings_dates or {}).get(symbol, "missing")
                if ed == "missing":
                    v.reasons.append(EARNINGS_DATE_UNAVAILABLE)
                elif isinstance(ed, date) and 0 <= (ed - now.date()).days <= earnings_blackout_days:
                    v.reasons.append(EARNINGS_WITHIN_BLACKOUT)
            # 8. post-trade single-name concentration on the REAL account
            if account.total_value is None or account.total_value <= 0 or account.positions is None:
                v.reasons.append(ACCOUNT_VALUES_UNAVAILABLE)
            elif weight_cap is not None and v.est_notional is not None:
                held_qty = holding.qty if holding is not None and holding.qty is not None else 0.0
                held_mv = held_qty * (quote.last or price or 0.0)
                if (held_mv + v.est_notional) / account.total_value > weight_cap + _EPS:
                    v.reasons.append(OVER_POSITION_WEIGHT)
            elif weight_cap is None:
                v.reasons.append(ACCOUNT_VALUES_UNAVAILABLE)
            # 9. portfolio heat on the REAL Agentic account (moved here from the
            # daemon's queue gate, which only sees the main account)
            if heat is None or heat_cap is None:
                v.reasons.append(PORTFOLIO_HEAT_UNAVAILABLE)
            elif heat > heat_cap + _EPS:
                v.reasons.append(PORTFOLIO_HEAT_OVER_LIMIT)
        else:
            # 6. never sell more than Robinhood says is sellable
            if holding is None or holding.sellable is None:
                if account.positions is not None:
                    v.reasons.append(SELL_EXCEEDS_SELLABLE)
            elif qty is not None and qty > holding.sellable + _EPS:
                v.reasons.append(SELL_EXCEEDS_SELLABLE)

    # -- cycle-wide: buys together must fit buying power and the gross cap ---
    survivors = sorted(
        (v for v in verdicts if v.side == "buy" and not v.reasons and v.est_notional is not None),
        key=lambda v: (-(v.conviction if v.conviction is not None else -math.inf), v.client_order_id),
    )
    bp = account.buying_power
    equity = account.equity_value
    cumulative = 0.0
    for v in survivors:
        if bp is None or equity is None or gross_cap is None:
            v.reasons.append(ACCOUNT_VALUES_UNAVAILABLE)
            continue
        if cumulative + v.est_notional > bp + _EPS:
            v.reasons.append(BUYING_POWER_EXHAUSTED)
            continue
        if (equity + cumulative + v.est_notional) / account.total_value > gross_cap + _EPS:
            v.reasons.append(OVER_PORTFOLIO_GROSS)
            continue
        cumulative += v.est_notional

    for v in verdicts:
        v.passed = not blockers and not v.reasons
        v.placeable = v.passed and mode == "live"

    return PreflightReport(
        queue_generated_at=queue.get("generated_at"),
        mode=mode,
        evaluated_at=now.isoformat(),
        run_blockers=blockers,
        verdicts=verdicts,
        limits={"critical_gap_fields": sorted(critical) or None,
                "max_notional_per_order": cap, "max_position_weight": weight_cap,
                "max_portfolio_gross": gross_cap, "buying_power": bp,
                "portfolio_heat": heat, "max_portfolio_heat": heat_cap,
                "total_value": account.total_value, "equity_value": equity},
    )


# ---------------------------------------------------------------------------
# Persistence + CLI
# ---------------------------------------------------------------------------

def _output_dir(output_dir: Optional[Any]) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    from settings import settings
    return Path(settings.OUTPUT_DIR)


def run_id_for(report: PreflightReport) -> str:
    """Deterministic run id from the queue timestamp, so one queue ↔ one folder."""
    gen = _parse_dt(report.queue_generated_at)
    return gen.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") if gen else "unknown-queue"


def write_preflight(report: PreflightReport, *, run_id: Optional[str] = None,
                    output_dir: Optional[Any] = None) -> Path:
    """Atomically write ``<OUTPUT_DIR>/agentic/<run_id>/preflight.json``."""
    folder = _output_dir(output_dir) / "agentic" / (run_id or run_id_for(report))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PREFLIGHT_FILENAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_preflight_for_queue(queue: Mapping[str, Any], output_dir: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """The preflight written for THIS queue, or None (missing, unreadable, or another queue's)."""
    gen = queue.get("generated_at")
    report_stub = PreflightReport(gen, "", "", [], [], {})
    path = _output_dir(output_dir) / "agentic" / run_id_for(report_stub) / PREFLIGHT_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if data.get("queue_generated_at") == gen else None


def _load_json(path: Optional[str]) -> Any:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _earnings_from_json(payload: Any) -> Dict[str, Optional[date]]:
    """``{"SYM": "YYYY-MM-DD" | null}``: null = checked, nothing scheduled.
    An unparseable date is DROPPED (→ treated as never checked → veto), never
    read as "no earnings"."""
    out: Dict[str, Optional[date]] = {}
    if isinstance(payload, Mapping):
        for sym, raw in payload.items():
            if raw is None:
                out[str(sym).upper()] = None
                continue
            try:
                out[str(sym).upper()] = date.fromisoformat(str(raw))
            except ValueError:
                logger.warning("agentic_preflight: unparseable earnings date for %s: %r", sym, raw)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Agent 3 preflight: veto queue intents against the live Robinhood account.")
    ap.add_argument("--portfolio", required=True, help="saved get_portfolio output (JSON)")
    ap.add_argument("--positions", required=True, help="saved get_equity_positions output (JSON; a list of pages is fine)")
    ap.add_argument("--quotes", required=True, help="saved get_equity_quotes output (JSON)")
    ap.add_argument("--approvals", required=True, help="saved get_trade_approvals output (JSON; a list of pages is fine)")
    ap.add_argument("--earnings", help='optional {"SYM": "YYYY-MM-DD"} map; needs --blackout-days')
    ap.add_argument("--blackout-days", type=int,
                    help="veto BUYs within N days of earnings (default: off). Without --earnings, "
                         "dates come from the Research Scout's research.json for this queue")
    ap.add_argument("--critical-gaps", default="",
                    help="comma-separated data_gaps fields that veto a BUY/SELL (plan check 10; "
                         "also vetoes intents with no valid thesis). Default: off")
    ap.add_argument("--queue", help="queue path (default: $OUTPUT_DIR/execution_queue.json)")
    ap.add_argument("--output-dir", help="override $OUTPUT_DIR")
    ap.add_argument("--no-write", action="store_true", help="print only; do not write preflight.json")
    args = ap.parse_args(argv)

    from execution.kill_switch import GlobalKillSwitch
    from execution.receipts_store import read_placed_ledger

    out_dir = _output_dir(args.output_dir)
    queue_path = Path(args.queue) if args.queue else out_dir / "execution_queue.json"
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps({"error": f"queue unreadable: {queue_path} ({exc})"}))
        return 2

    approvals, unidentified = approvals_from_mcp(_load_json(args.approvals))
    critical = [f.strip() for f in args.critical_gaps.split(",") if f.strip()]
    earnings = _earnings_from_json(_load_json(args.earnings)) if args.earnings else None
    theses: Dict[str, Any] = {}
    invalid: List[str] = []
    if critical or (args.blackout_days is not None and earnings is None):
        from execution.agentic_research import earnings_dates_from_research, load_run_artifacts
        art = load_run_artifacts(queue, out_dir)
        if earnings is None and art["research"] is not None and not art["research_errors"]:
            earnings = earnings_dates_from_research(art["research"])
        doc = (art["theses"] or {}).get("theses") or {}
        theses = {c: doc[c] for c in art["valid_thesis_ids"]}
        invalid = [c for c in art["thesis_errors"] if c != "_doc"]
    report = preflight(
        queue,
        AccountState.from_mcp(_load_json(args.portfolio), _load_json(args.positions)),
        quotes_from_mcp(_load_json(args.quotes)),
        now=datetime.now(timezone.utc),
        pending_approvals=approvals,
        unidentified_pending_approvals=unidentified,
        placed_dedup_keys=[r.get("dedup_key") for r in read_placed_ledger(out_dir) if r.get("dedup_key")],
        kill_switch_active=GlobalKillSwitch().is_active(),
        earnings_dates=earnings or {},
        earnings_blackout_days=args.blackout_days,
        theses=theses,
        invalid_thesis_ids=invalid,
        critical_gap_fields=critical,
    )
    summary = report.to_dict()
    if not args.no_write:
        summary["written_to"] = str(write_preflight(report, output_dir=out_dir))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

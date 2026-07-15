"""ADVISORY ONLY autonomous advisory-loop policy: decides WHEN to re-run the advisory pipeline (adaptive cadence from market hours / macro regime / VIX / errors) and WHICH high-conviction backlog items to re-ping, with persistent AgentState round-tripped to disk. Contains no order-submission or broker code; all compute_* functions are pure and lookahead-free."""

# =============================================================================
# MODULE: ADVISORY AGENT  (autonomous advisory loop policy — ADVISORY ONLY)
# File: engine/advisory_agent.py
#
# ADVISORY ONLY — this module decides WHEN to re-run the advisory pipeline and
# WHICH high-conviction signals warrant a follow-up push notification.  It
# contains NO order-submission, order-modification, or broker-contact code of
# any kind.  All work routes through `engine.advisory.evaluate()` and the
# existing `alerting.notify()` ntfy channel.
#
# The module composes on top of:
#   * `main.run_once()`              — produces a fresh RunResult per cycle
#   * `watch_engine.evaluate_*`      — already fires per-cycle edge alerts
#   * `gui.decision_log.read_*`      — tracks what the operator actually did
#   * `alerting.notify()`            — ntfy push channel
#
# What it ADDS that does not already exist:
#   1. ADAPTIVE CADENCE — `compute_next_run_delay()` returns seconds-to-sleep
#      based on (a) US market hours, (b) macro regime, (c) VIX, (d) error count.
#      Replaces `--interval N`'s fixed timer with a policy.
#   2. ACTIONABLE BACKLOG — `compute_backlog_reminders()` re-pings high-
#      conviction BUY/SELL recommendations the operator has NOT yet logged a
#      decision for, on escalating tiers (1h / 4h / 24h).
#   3. PERSISTENT STATE — `AgentState` round-trips to `output/agent_state.json`
#      so the backlog survives restarts.
#
# No-lookahead invariant
# ----------------------
# All `compute_*` functions are pure: they consume `now_utc`, the latest
# `RunResult`, the decision log entries already on disk, and the previous
# `AgentState`.  They never call into market-data providers, forecasting
# engines, or any source of future-dated data.  Verified by the Gravity audit.
# =============================================================================

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timezone constants (mirror execution/risk_gate.py)
# ---------------------------------------------------------------------------
_ET = ZoneInfo("America/New_York")
_RTH_OPEN_HM = (9, 30)
_RTH_CLOSE_HM = (16, 0)

# ---------------------------------------------------------------------------
# Policy CONFIG — single source of truth for every threshold / constant.
# Operator may override via settings.* counterparts (wired by main.py).
# No magic numbers belong in the logic functions below.
# ---------------------------------------------------------------------------
CONFIG: Dict[str, Any] = {
    # ── Cadence (seconds) ──────────────────────────────────────────────
    # Active RTH refresh (default 5 min) — fast enough to catch intraday
    # regime/conviction changes without flooding ntfy or yfinance rate limits.
    "rth_normal_delay_s": 300,
    # During high-volatility regime (VIX > vol_spike_threshold) we tighten
    # the cadence inside RTH to catch fast moves.
    "rth_high_vol_delay_s": 120,
    # During market-open / market-close 30-minute windows we tighten further
    # — these are the densest information periods of the day.
    "rth_open_close_delay_s": 60,
    "rth_open_close_window_minutes": 30,
    # Outside RTH but inside the extended window (4 AM – 8 PM ET on weekdays)
    # we still refresh, just much less often (default 1 h).
    "extended_hours_delay_s": 3600,
    # Outside the extended window and on weekends we drop to a long heartbeat
    # — once every 4 h — so the dashboard stays warm without burning quota.
    "off_hours_delay_s": 14400,
    # When the last cycle had errors we back off to give upstream APIs time
    # to recover (linear back-off bounded by error_backoff_max_s).
    "error_backoff_base_s": 60,
    "error_backoff_max_s": 900,
    # Regime that triggers the high-vol cadence even when VIX is moderate.
    # Matches `engine/advisory.CONFIG["macro_vix_gate_threshold"]` semantics.
    "vol_spike_vix_threshold": 25.0,
    "high_vol_regimes": ("RISK OFF", "RECESSION", "CREDIT EVENT"),
    # Cadence floor — even the most aggressive policy never pings faster than
    # this so we cannot accidentally hot-loop the yfinance API.
    "min_delay_s": 60,

    # ── Backlog reminders ──────────────────────────────────────────────
    # A signal enters the backlog when conviction ≥ this threshold AND the
    # action is BUY or SELL (HOLD is informational, not actionable).
    # Mirrors `watch_rules.yaml`'s default 0.85 universe-wide siren.
    "backlog_conviction_threshold": 0.85,
    # Escalation cadence in hours.  Each tier corresponds to a notify priority.
    "backlog_tier_hours": (1.0, 4.0, 24.0),
    "backlog_tier_priorities": ("default", "high", "high"),
    # Maximum number of reminders per (symbol, action) before we stop pinging
    # — operator has clearly decided to ignore this signal.
    "backlog_max_reminders": 3,
    # Backlog entries older than this expire silently (signal has gone stale).
    "backlog_expiry_hours": 72.0,

    # ── Decision-log join ──────────────────────────────────────────────
    # When checking whether the operator already actioned a signal, accept
    # any decision logged within this window AFTER the signal timestamp.
    # Matches the 1.3 decision-log default join window.
    "decision_log_match_window_hours": 24.0,
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BacklogEntry:
    """One actionable signal awaiting operator action.

    Attributes
    ----------
    symbol :
        Uppercase ticker.
    action :
        "BUY" or "SELL" — HOLD never enters the backlog.
    conviction :
        Conviction score at the time the signal was first observed, [0, 1].
    first_seen_iso :
        UTC ISO timestamp of the first cycle on which this (symbol, action)
        crossed the conviction threshold and the operator had not yet logged
        a decision.
    last_pinged_iso :
        UTC ISO timestamp of the most recent reminder dispatch, or empty
        string if no reminder has fired yet.
    reminders_sent :
        Count of tier reminders already dispatched; capped at
        ``CONFIG["backlog_max_reminders"]``.
    """
    symbol: str
    action: str
    conviction: float
    first_seen_iso: str
    last_pinged_iso: str
    reminders_sent: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "BacklogEntry":
        return cls(
            symbol=str(payload.get("symbol", "")).upper(),
            action=str(payload.get("action", "")).upper(),
            conviction=float(payload.get("conviction", 0.0)),
            first_seen_iso=str(payload.get("first_seen_iso", "")),
            last_pinged_iso=str(payload.get("last_pinged_iso", "")),
            reminders_sent=int(payload.get("reminders_sent", 0)),
        )


@dataclass(frozen=True)
class BacklogReminder:
    """One reminder alert ready for `alerting.notify()`.

    Pure data object; `dispatch_backlog_reminders()` consumes a list of these
    and translates them into ntfy calls.
    """
    symbol: str
    action: str
    conviction: float
    tier: int           # 1, 2, 3 … (1-indexed)
    age_hours: float    # hours since first_seen
    priority: str       # "default" | "high"
    title: str
    message: str


@dataclass
class AgentState:
    """Persistent state across agent cycles.

    Mutable by design — `process_run_result()` and `compute_backlog_reminders()`
    rewrite the backlog dict in place; the loop driver saves the resulting state
    via `save_agent_state()` at the end of each cycle.
    """
    cycle_count: int = 0
    last_cycle_iso: str = ""
    last_error_count: int = 0
    consecutive_error_cycles: int = 0
    backlog: Dict[str, BacklogEntry] = field(default_factory=dict)
    # Free-form mtime tracker so the next run can compute time-since-last-event
    # without re-parsing the on-disk file.
    last_summary_iso: str = ""
    # ── Trade-signal abilities (engine/trade_signals.py) state ──────────
    # Rolling per-symbol conviction window (oldest first) feeding the
    # conviction-momentum detector.
    conviction_history: Dict[str, List[float]] = field(default_factory=dict)
    # Per-symbol debounce flags so each ability pings once per trend, not per
    # cycle.  momentum_alerted: symbol -> "building"|"fading"; price_trigger_
    # alerted: symbol -> "stop"|"target".
    momentum_alerted: Dict[str, str] = field(default_factory=dict)
    price_trigger_alerted: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_count": self.cycle_count,
            "last_cycle_iso": self.last_cycle_iso,
            "last_error_count": self.last_error_count,
            "consecutive_error_cycles": self.consecutive_error_cycles,
            "backlog": {k: v.to_dict() for k, v in self.backlog.items()},
            "last_summary_iso": self.last_summary_iso,
            "conviction_history": {k: list(v) for k, v in self.conviction_history.items()},
            "momentum_alerted": dict(self.momentum_alerted),
            "price_trigger_alerted": dict(self.price_trigger_alerted),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AgentState":
        backlog_raw = payload.get("backlog", {}) or {}
        backlog: Dict[str, BacklogEntry] = {}
        for k, v in backlog_raw.items():
            try:
                backlog[str(k).upper()] = BacklogEntry.from_dict(v)
            except Exception as exc:
                logger.debug("agent_state: dropped corrupt backlog entry %s (%s)", k, exc)

        # Tolerant rehydration of the trade-signal state (CONSTRAINT #6).
        hist_raw = payload.get("conviction_history", {}) or {}
        conviction_history: Dict[str, List[float]] = {}
        for k, v in hist_raw.items():
            try:
                conviction_history[str(k).upper()] = [float(x) for x in (v or [])]
            except Exception as exc:
                logger.debug("agent_state: dropped corrupt conviction history %s (%s)", k, exc)

        def _str_map(raw: Any) -> Dict[str, str]:
            out: Dict[str, str] = {}
            for k, v in (raw or {}).items():
                try:
                    out[str(k).upper()] = str(v)
                except Exception:
                    continue
            return out

        return cls(
            cycle_count=int(payload.get("cycle_count", 0)),
            last_cycle_iso=str(payload.get("last_cycle_iso", "")),
            last_error_count=int(payload.get("last_error_count", 0)),
            consecutive_error_cycles=int(payload.get("consecutive_error_cycles", 0)),
            backlog=backlog,
            last_summary_iso=str(payload.get("last_summary_iso", "")),
            conviction_history=conviction_history,
            momentum_alerted=_str_map(payload.get("momentum_alerted")),
            price_trigger_alerted=_str_map(payload.get("price_trigger_alerted")),
        )


# ---------------------------------------------------------------------------
# Persistence (atomic write-then-rename — same pattern as watch_engine)
# ---------------------------------------------------------------------------

def load_agent_state(path: Path) -> AgentState:
    """Read `AgentState` from disk; return a fresh empty state on any failure.

    Tolerant by design (CONSTRAINT #6) — corrupt JSON, missing file, or
    invalid schema all degrade to a fresh `AgentState()` with a DEBUG log.
    """
    try:
        if not path.exists():
            return AgentState()
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return AgentState()
        return AgentState.from_dict(json.loads(text))
    except Exception as exc:
        logger.debug("agent_state read from '%s' failed (%s); using fresh state", path, exc)
        return AgentState()


def save_agent_state(state: AgentState, path: Path) -> None:
    """Persist `AgentState` atomically (write-to-temp-then-rename).

    Failures are logged at WARNING and swallowed (CONSTRAINT #6) — a missed
    state write must never crash the agent loop.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        tmp.rename(path)
        logger.debug("agent_state.json written to '%s' (backlog=%d)", path, len(state.backlog))
    except Exception as exc:
        logger.warning("Failed to save agent_state.json to '%s' (%s); state not persisted.", path, exc)


# ---------------------------------------------------------------------------
# Market-hours detection
# ---------------------------------------------------------------------------

def is_us_market_open(now_utc: datetime) -> bool:
    """Return True iff `now_utc` falls within NYSE regular trading hours.

    RTH = 09:30 – 16:00 America/New_York, Monday-Friday.  Holiday calendar
    is NOT applied (would require pandas_market_calendars or an external
    feed); the operator should set `enforce_market_hours=False` if running
    on a half-day or holiday.

    Parameters
    ----------
    now_utc :
        Timezone-aware UTC datetime.  Naive datetimes are promoted to UTC.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_et = now_utc.astimezone(_ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_t = now_et.replace(
        hour=_RTH_OPEN_HM[0], minute=_RTH_OPEN_HM[1], second=0, microsecond=0,
    )
    close_t = now_et.replace(
        hour=_RTH_CLOSE_HM[0], minute=_RTH_CLOSE_HM[1], second=0, microsecond=0,
    )
    return open_t <= now_et <= close_t


def is_extended_hours(now_utc: datetime) -> bool:
    """Return True iff `now_utc` is inside the 4 AM – 8 PM ET weekday window.

    Used to distinguish "premarket / aftermarket" cadence from
    "overnight / weekend" cadence.  RTH is a strict subset of extended hours.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_et = now_utc.astimezone(_ET)
    if now_et.weekday() >= 5:
        return False
    return 4 <= now_et.hour < 20


def _minutes_into_or_until_rth(now_utc: datetime) -> Tuple[int, int]:
    """Return `(minutes_since_open, minutes_until_close)` clipped to [0, ∞).

    Helper for the open/close cadence boost.  When `now_utc` is outside RTH
    both values are returned as `int(1e9)` so the caller treats it as "far
    from any boundary".
    """
    if not is_us_market_open(now_utc):
        return (1_000_000_000, 1_000_000_000)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_et = now_utc.astimezone(_ET)
    open_t = now_et.replace(
        hour=_RTH_OPEN_HM[0], minute=_RTH_OPEN_HM[1], second=0, microsecond=0,
    )
    close_t = now_et.replace(
        hour=_RTH_CLOSE_HM[0], minute=_RTH_CLOSE_HM[1], second=0, microsecond=0,
    )
    since_open = int((now_et - open_t).total_seconds() // 60)
    until_close = int((close_t - now_et).total_seconds() // 60)
    return (max(0, since_open), max(0, until_close))


# ---------------------------------------------------------------------------
# Adaptive cadence
# ---------------------------------------------------------------------------

def compute_next_run_delay(
    now_utc: datetime,
    *,
    state: AgentState,
    vix: Optional[float] = None,
    market_regime: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> int:
    """Return the number of seconds to sleep before the next advisory cycle.

    Decision tree (first match wins):
      1. Error back-off — `state.consecutive_error_cycles > 0` →
         `min(error_backoff_base_s * N, error_backoff_max_s)`.
      2. Open / close 30-minute boost — if inside RTH AND within
         `rth_open_close_window_minutes` of either boundary →
         `rth_open_close_delay_s` (default 60 s).
      3. High-volatility RTH — inside RTH AND (`vix ≥ vol_spike_vix_threshold`
         OR `market_regime in high_vol_regimes`) → `rth_high_vol_delay_s`.
      4. Normal RTH — `rth_normal_delay_s`.
      5. Extended hours — `extended_hours_delay_s`.
      6. Off-hours / weekend — `off_hours_delay_s`.

    The return is always ≥ `min_delay_s` to prevent hot-looping.

    Pure function: only reads its arguments; no I/O, no `datetime.now()`.
    """
    cfg = {**CONFIG, **(config or {})}

    # Rule 1 — error back-off short-circuits everything else.
    if state.consecutive_error_cycles > 0:
        backoff = cfg["error_backoff_base_s"] * state.consecutive_error_cycles
        return max(cfg["min_delay_s"], min(backoff, cfg["error_backoff_max_s"]))

    in_rth = is_us_market_open(now_utc)

    if in_rth:
        # Rule 2 — boost cadence around the open/close.
        since_open, until_close = _minutes_into_or_until_rth(now_utc)
        boundary = cfg["rth_open_close_window_minutes"]
        if since_open <= boundary or until_close <= boundary:
            return max(cfg["min_delay_s"], cfg["rth_open_close_delay_s"])

        # Rule 3 — high-vol cadence.
        regime_hot = (market_regime or "").upper() in {
            r.upper() for r in cfg["high_vol_regimes"]
        }
        vix_hot = vix is not None and vix >= cfg["vol_spike_vix_threshold"]
        if vix_hot or regime_hot:
            return max(cfg["min_delay_s"], cfg["rth_high_vol_delay_s"])

        # Rule 4 — normal RTH cadence.
        return max(cfg["min_delay_s"], cfg["rth_normal_delay_s"])

    # Rule 5 — extended-hours cadence (4 AM – 8 PM ET weekday, RTH excluded).
    if is_extended_hours(now_utc):
        return max(cfg["min_delay_s"], cfg["extended_hours_delay_s"])

    # Rule 6 — off-hours / weekend.
    return max(cfg["min_delay_s"], cfg["off_hours_delay_s"])


# ---------------------------------------------------------------------------
# Actionable backlog management
# ---------------------------------------------------------------------------

def _parse_iso(text: str) -> Optional[datetime]:
    """Parse an ISO-8601 string; return ``None`` on any failure."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def update_backlog(
    state: AgentState,
    recommendations: List[Any],
    decision_log_entries: List[Any],
    now_utc: datetime,
    *,
    config: Optional[Dict[str, Any]] = None,
) -> AgentState:
    """Update the backlog dict in `state` against the current cycle's output.

    Three operations are performed:
      1. INSERT — a recommendation with action in {BUY, SELL} and conviction
         ≥ ``backlog_conviction_threshold`` is added to the backlog if not
         already present.
      2. ACTIONED — any symbol with a decision-log entry whose `timestamp`
         is AFTER the backlog's `first_seen_iso` (within the match window)
         is removed from the backlog.
      3. EXPIRED — entries older than ``backlog_expiry_hours`` are dropped.

    The function MUTATES `state.backlog` and returns the same `state` for
    fluency.  Pure with respect to wall-clock: `now_utc` is the only time
    source.

    Parameters
    ----------
    state :
        Current agent state.  Its `backlog` dict is updated in place.
    recommendations :
        Iterable of `engine.advisory.Recommendation`-shaped objects.  Each
        must expose `.symbol`, `.action`, `.conviction` (duck-typed to keep
        this module decoupled from the heavy `engine.advisory` import).
    decision_log_entries :
        Iterable of `gui.decision_log.DecisionEntry`-shaped objects.  Each
        must expose `.symbol` (case-insensitive) and `.timestamp` (ISO string).
        Only entries whose `action_taken == "acted"` are considered.
    now_utc :
        Reference timestamp for "first_seen" insertion and expiry check.
    """
    cfg = {**CONFIG, **(config or {})}
    conv_threshold = float(cfg["backlog_conviction_threshold"])
    expiry_hours = float(cfg["backlog_expiry_hours"])
    match_window_h = float(cfg["decision_log_match_window_hours"])

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_iso = now_utc.isoformat()

    # ── (1) INSERT new high-conviction actionable signals ────────────────
    for rec in recommendations or []:
        try:
            action = str(getattr(rec, "action", "")).upper()
            symbol = str(getattr(rec, "symbol", "")).upper()
            conv = float(getattr(rec, "conviction", 0.0) or 0.0)
        except Exception:
            continue
        if not symbol or action not in {"BUY", "SELL"}:
            continue
        if conv < conv_threshold:
            continue
        key = f"{symbol}:{action}"
        existing = state.backlog.get(key)
        if existing is None:
            state.backlog[key] = BacklogEntry(
                symbol=symbol,
                action=action,
                conviction=conv,
                first_seen_iso=now_iso,
                last_pinged_iso="",
                reminders_sent=0,
            )
        else:
            # Already in backlog: keep the original first_seen_iso so the tier
            # escalation reflects time since the operator was FIRST told.  Only
            # refresh the recorded conviction so dashboard text stays current.
            state.backlog[key] = replace(existing, conviction=conv)

    # ── (2) ACTIONED — drop entries where the operator logged "acted" ────
    # Pre-compute the youngest "acted" timestamp per symbol so the inner
    # backlog loop is O(N + M) rather than O(N*M).
    youngest_acted_ts: Dict[str, datetime] = {}
    for entry in decision_log_entries or []:
        try:
            if str(getattr(entry, "action_taken", "")).lower() != "acted":
                continue
            sym = str(getattr(entry, "symbol", "")).upper()
            ts = _parse_iso(str(getattr(entry, "timestamp", "")))
        except Exception:
            continue
        if not sym or ts is None:
            continue
        prev = youngest_acted_ts.get(sym)
        if prev is None or ts > prev:
            youngest_acted_ts[sym] = ts

    to_drop: List[str] = []
    for key, b in state.backlog.items():
        first_seen = _parse_iso(b.first_seen_iso)
        if first_seen is None:
            continue
        # (2a) ACTIONED clear — any "acted" log entry at or after first_seen
        #      and within `match_window_h` of it resolves the backlog item.
        #      The lower bound (acted_ts >= first_seen) already prevents a
        #      stale prior action from clearing a freshly re-surfaced signal;
        #      the upper bound guards against an implausibly late match.
        acted_ts = youngest_acted_ts.get(b.symbol)
        if (
            acted_ts is not None
            and first_seen <= acted_ts <= first_seen + timedelta(hours=match_window_h)
        ):
            to_drop.append(key)
            continue
        # (2b) EXPIRED — silent drop after expiry_hours.
        if (now_utc - first_seen).total_seconds() / 3600.0 > expiry_hours:
            to_drop.append(key)
    for key in to_drop:
        state.backlog.pop(key, None)

    return state


def compute_backlog_reminders(
    state: AgentState,
    now_utc: datetime,
    *,
    config: Optional[Dict[str, Any]] = None,
) -> List[BacklogReminder]:
    """Return the reminder alerts that should fire on this cycle.

    For each backlog entry, walks the `backlog_tier_hours` ladder and emits
    AT MOST ONE reminder per call — the highest tier the entry has reached
    since its last reminder.  Reminders are capped at
    ``backlog_max_reminders`` per entry (default 3).

    The function does NOT mutate state.  The caller (`apply_reminder_dispatch`)
    advances `last_pinged_iso` and `reminders_sent` for each entry whose
    reminder was actually dispatched.
    """
    cfg = {**CONFIG, **(config or {})}
    tiers: Tuple[float, ...] = tuple(cfg["backlog_tier_hours"])
    priorities: Tuple[str, ...] = tuple(cfg["backlog_tier_priorities"])
    max_reminders = int(cfg["backlog_max_reminders"])

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    out: List[BacklogReminder] = []
    for b in state.backlog.values():
        first_seen = _parse_iso(b.first_seen_iso)
        if first_seen is None:
            continue
        if b.reminders_sent >= max_reminders:
            continue

        age_h = (now_utc - first_seen).total_seconds() / 3600.0
        last_pinged = _parse_iso(b.last_pinged_iso)

        # Walk the tier ladder; pick the highest tier we've reached AND
        # whose `last_pinged_iso` is either absent or older than the tier
        # boundary that just qualified the entry.
        tier_to_fire: Optional[int] = None
        for i, t_hours in enumerate(tiers, start=1):
            if age_h < t_hours:
                break
            # Has this tier already been dispatched?
            # We've already sent `b.reminders_sent` reminders — they were
            # tiers 1..reminders_sent.  Fire the NEXT one if its threshold
            # has now been crossed.
            if i > b.reminders_sent:
                tier_to_fire = i
                break

        if tier_to_fire is None:
            continue

        priority = (
            priorities[tier_to_fire - 1]
            if 0 <= tier_to_fire - 1 < len(priorities)
            else "default"
        )

        # Cosmetic guard for the last_pinged_iso check: refuse to ping faster
        # than `min_delay_s` even if the policy says we could.
        if last_pinged is not None:
            since_last = (now_utc - last_pinged).total_seconds()
            if since_last < float(cfg["min_delay_s"]):
                continue

        out.append(
            BacklogReminder(
                symbol=b.symbol,
                action=b.action,
                conviction=b.conviction,
                tier=tier_to_fire,
                age_hours=round(age_h, 2),
                priority=priority,
                title=f"⏰ {b.action} {b.symbol} unactioned ({age_h:.1f}h)",
                message=(
                    f"{b.action} {b.symbol} @ conviction {b.conviction:.2f} "
                    f"was first surfaced {age_h:.1f}h ago and no operator "
                    f"decision has been logged yet (tier {tier_to_fire})."
                ),
            )
        )
    return out


def apply_reminder_dispatch(
    state: AgentState,
    reminders: List[BacklogReminder],
    now_utc: datetime,
) -> AgentState:
    """Advance `last_pinged_iso` and `reminders_sent` for each dispatched reminder.

    Call this AFTER successfully dispatching `reminders` via `notify()` (the
    loop driver does this).  Returns the same `state` for fluency.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_iso = now_utc.isoformat()
    max_reminders = int(CONFIG["backlog_max_reminders"])
    for r in reminders:
        key = f"{r.symbol}:{r.action}"
        old = state.backlog.get(key)
        if old is None:
            continue
        state.backlog[key] = replace(
            old,
            last_pinged_iso=now_iso,
            reminders_sent=min(old.reminders_sent + 1, max_reminders),
        )
    return state


# ---------------------------------------------------------------------------
# Run-result integration
# ---------------------------------------------------------------------------

def process_run_result(
    state: AgentState,
    run_result: Any,
    now_utc: datetime,
) -> AgentState:
    """Update `state` with the outcome of one `run_once()` cycle.

    Tracks cycle count, error streaks, and the timestamp of the most recent
    cycle.  Pure with respect to wall-clock: `now_utc` is the only time
    source.

    The backlog is NOT updated here — `update_backlog()` is the dedicated
    function for that and the loop driver calls both in sequence.
    """
    state.cycle_count += 1
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    state.last_cycle_iso = now_utc.isoformat()

    try:
        n_errors = len(getattr(run_result, "errors", []) or [])
    except Exception:
        n_errors = 0
    state.last_error_count = n_errors
    if n_errors > 0:
        state.consecutive_error_cycles += 1
    else:
        state.consecutive_error_cycles = 0
    return state


# ---------------------------------------------------------------------------
# Dispatch helper (kept thin — the loop driver imports `alerting.notify`)
# ---------------------------------------------------------------------------

def dispatch_backlog_reminders(
    reminders: List[BacklogReminder],
    *,
    dashboard_url: Optional[str] = None,
) -> None:
    """Push every reminder via `alerting.notify()`.

    Mirrors the contract of `watch_engine.dispatch_watch_alerts`: when
    `NTFY_TOPIC` is unset `notify()` is already a no-op, so this is silently
    inert with no ntfy configuration.  Per-reminder dispatch is wrapped in
    try/except — a network failure on one reminder never blocks the rest
    (CONSTRAINT #6).
    """
    if not reminders:
        return
    from alerting import notify  # noqa: PLC0415 — match watch_engine pattern
    for r in reminders:
        try:
            msg = r.message
            if dashboard_url:
                msg = f"{msg}\n\n📊 Dashboard: {dashboard_url}"
            notify(title=r.title, message=msg, priority=r.priority)
            logger.info(
                "Backlog reminder dispatched — symbol=%s action=%s tier=%d age=%.1fh",
                r.symbol, r.action, r.tier, r.age_hours,
            )
        except Exception as exc:
            logger.warning(
                "Failed to dispatch backlog reminder for %s/%s (%s)",
                r.symbol, r.action, exc,
            )

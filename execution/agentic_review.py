"""Phase 5 of the Robinhood agentic plan: Agent 5 ("Reviewer") and the burn-in scorecard.

The Reviewer agent (``.claude/agents/rh-reviewer.md``) is read-only. It saves raw
Robinhood MCP output (``get_equity_orders``, ``get_equity_quotes``,
``get_trade_approval_setting``) through :func:`save_input` and runs
:func:`run_review`. Every judgement is made here, in code, from files:

1. **Reconciliation** — the placed ledger against the Agentic account's real
   fills, via ``receipts_store.reconcile(fills=...)``. The fills come from the
   saved MCP response through :func:`fills_from_mcp`, which reuses the pure
   ``data.robinhood_orders.parse_orders`` (never a second parser, and never the
   shared main-account order cache).
2. **Outcomes** — for each approved order: entry fill price, the latest quote,
   and the move since entry. Before the thesis horizon has passed the order is
   labelled ``too_early``. A missing fill or quote stays ``None``, never 0.
3. **Cycle health** — cycles completed/failed/skipped (``cycle.json`` per run),
   veto reasons (``preflight.json``), and approval outcomes.
4. **Safety invariants** — any failure is a hard FAIL:
   ``order_without_phone_approval`` (Robinhood says an agent submitted an order
   itself, ``placed_agent == "agentic"``), ``placed_in_review_cycle``,
   ``placed_without_ref_id``, ``placed_vetoed_or_oversized`` (not passed by
   the preflight, or more shares than it allowed) and ``duplicate_placement``
   (two placements for one day's ``dedup_key``).
5. **Burn-in readiness** — READY only when every invariant passes, there are
   at least ``REQUIRED_REVIEW_DAYS`` days with a completed ``review`` cycle, no
   failed cycle on the last ``RECENT_DAYS`` cycle days, the reconciliation is
   ``ok`` and a positive per-order cap is set. Advice only: switching to
   ``live`` stays the operator's own settings change.

Output: ``OUTPUT_DIR/agentic/review/<YYYY-MM-DD>/review.json`` (atomic), with the
raw inputs under ``inputs/``. CLI exit codes: 0 READY, 1 NOT READY, 2 inputs
unusable (fail closed, CONSTRAINT #6). This module never calls a Robinhood
tool and defines no order symbol (``_EXECUTION_ZONE_GUARDED_FILES``).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from execution.agentic_preflight import PREFLIGHT_FILENAME, _output_dir, _unwrap, quotes_from_mcp

logger = logging.getLogger(__name__)

REVIEW_DIRNAME = "review"
REVIEW_FILENAME = "review.json"
INPUT_NAMES = ("orders", "quotes", "approval_setting")

REQUIRED_REVIEW_DAYS = 10
RECENT_DAYS = 3
DEFAULT_HORIZON_DAYS = 30

# Hard ceiling on one unattended review run (a code constant, not a setting).
LAUNCH_TIMEOUT_SECONDS = 1200
REVIEWER_AGENT = "rh-reviewer"
COORDINATOR_TOOLS = ("Agent", "Task", "Read", "Bash", "Glob", "Grep")

EXIT_READY = 0
EXIT_NOT_READY = 1
EXIT_UNUSABLE = 2

INVARIANTS = (
    "order_without_phone_approval",
    "placed_in_review_cycle",
    "placed_without_ref_id",
    "placed_vetoed_or_oversized",
    "duplicate_placement",
)
_EPS = 1e-9


# ---------------------------------------------------------------------------
# Paths and inputs
# ---------------------------------------------------------------------------

def review_dir(day: date, output_dir: Optional[Any] = None) -> Path:
    return _output_dir(output_dir) / "agentic" / REVIEW_DIRNAME / day.isoformat()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def save_input(name: str, payload: Any, *, day: date, output_dir: Optional[Any] = None) -> Path:
    """Save one raw MCP response verbatim under ``review/<day>/inputs/<name>.json``."""
    if name not in INPUT_NAMES:
        raise ValueError(f"unknown input {name!r}; expected one of {INPUT_NAMES}")
    path = review_dir(day, output_dir) / "inputs" / f"{name}.json"
    _write_json_atomic(path, payload)
    return path


def _pages(payload: Any, key: str) -> List[Any]:
    """Rows under ``data.<key>`` (or ``<key>``), across a single response or a list of pages."""
    pages = payload if isinstance(payload, list) else [payload]
    rows: List[Any] = []
    for page in pages:
        data = _unwrap(page)
        if isinstance(data, Mapping) and isinstance(data.get(key), list):
            rows.extend(data[key])
        elif isinstance(data, list):
            rows.extend(data)
    return rows


# ---------------------------------------------------------------------------
# Robinhood orders → fills (reusing the existing pure parser)
# ---------------------------------------------------------------------------

def orders_from_mcp(payload: Any) -> List[Dict[str, Any]]:
    return [o for o in _pages(payload, "orders") if isinstance(o, Mapping)]


def fills_from_mcp(orders: Sequence[Mapping[str, Any]]) -> Tuple[List[Any], int]:
    """``(fills, n_unresolved)`` from MCP ``get_equity_orders`` rows.

    The MCP rows carry the ticker in ``symbol``; ``parse_orders`` resolves a
    symbol from ``instrument``, so each row is handed over with its own
    symbol as the "instrument" and an identity resolver. A filled row with no
    symbol can't be attributed and is counted, never guessed.
    """
    from data.robinhood_orders import parse_orders

    rows: List[Dict[str, Any]] = []
    unresolved = 0
    for o in orders:
        sym = str(o.get("symbol") or "").strip().upper()
        if not sym:
            if str(o.get("state") or "").lower() == "filled":
                unresolved += 1
            continue
        rows.append({**o, "instrument": sym})
    return parse_orders(rows, lambda inst: inst or None), unresolved


def approvals_required_from_mcp(payload: Any) -> Optional[bool]:
    """``human_must_approve_trades`` from a ``get_trade_approval_setting`` response.

    Robinhood nests it under ``data.setting`` (seen live 2026-11-10:
    ``{"data": {"setting": {"account_number", "human_must_approve_trades",
    "updated_at"}}}``); a flat ``data`` is accepted too. Only a real boolean
    counts: anything else is ``None`` (unconfirmed), never a guessed ``True``.
    """
    data = _unwrap(payload)
    if not isinstance(data, Mapping):
        return None
    for candidate in (data.get("setting"), data):
        if isinstance(candidate, Mapping) and isinstance(candidate.get("human_must_approve_trades"), bool):
            return candidate["human_must_approve_trades"]
    return None


def _is_agent_initiated(o: Mapping[str, Any]) -> bool:
    """Agent-initiated per Robinhood. Rows without the field count as agent orders (fail closed)."""
    return o.get("agent_initiated") is not False


# ---------------------------------------------------------------------------
# Cycle health
# ---------------------------------------------------------------------------

def _run_dirs(output_dir: Optional[Any]) -> List[Path]:
    root = _output_dir(output_dir) / "agentic"
    try:
        return sorted(p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z", p.name))
    except Exception:
        return []


def _run_day(run_id: str) -> Optional[date]:
    try:
        return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").date()
    except ValueError:
        return None


def load_cycles(output_dir: Optional[Any] = None) -> List[Dict[str, Any]]:
    out = []
    for d in _run_dirs(output_dir):
        c = _read_json(d / "cycle.json")
        if isinstance(c, dict):
            c = dict(c)
            c.setdefault("run_id", d.name)
            out.append(c)
    return out


def load_preflights(output_dir: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    out = {}
    for d in _run_dirs(output_dir):
        p = _read_json(d / PREFLIGHT_FILENAME)
        if isinstance(p, dict):
            out[d.name] = p
    return out


def cycle_health(cycles: Sequence[Mapping[str, Any]],
                 preflights: Mapping[str, Mapping[str, Any]],
                 approval_states: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    by_status = Counter(str(c.get("status") or "unknown") for c in cycles)
    failed_by_stage = Counter(str(c.get("failed_stage") or "unknown")
                              for c in cycles if c.get("status") == "failed")
    review_days = sorted({str(_run_day(str(c.get("run_id"))))
                          for c in cycles
                          if c.get("status") == "completed" and c.get("mode") == "review"
                          and _run_day(str(c.get("run_id"))) is not None})
    live_days = sorted({str(_run_day(str(c.get("run_id"))))
                        for c in cycles
                        if c.get("status") == "completed" and c.get("mode") == "live"
                        and _run_day(str(c.get("run_id"))) is not None})
    vetoes: Counter = Counter()
    n_passed = 0
    for pf in preflights.values():
        for v in pf.get("verdicts") or []:
            if not isinstance(v, Mapping):
                continue
            if v.get("passed"):
                n_passed += 1
            for r in v.get("reasons") or []:
                vetoes[str(r)] += 1
        for r in pf.get("run_blockers") or []:
            vetoes[f"run:{r}"] += 1
    approvals = Counter(str(rec.get("state") or "unknown") for rec in approval_states.values())
    return {
        "cycles": dict(by_status),
        "n_cycles": len(cycles),
        "failed_by_stage": dict(failed_by_stage),
        "review_days": review_days,
        "live_days": live_days,
        "n_would_pass": n_passed,
        "vetoes": dict(vetoes),
        "approvals": dict(approvals),
    }


# ---------------------------------------------------------------------------
# Safety invariants
# ---------------------------------------------------------------------------

def check_invariants(
    *,
    orders: Sequence[Mapping[str, Any]],
    placed: Sequence[Mapping[str, Any]],
    cycles: Sequence[Mapping[str, Any]],
    preflights: Mapping[str, Mapping[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """``{invariant: [violations]}`` for every invariant (empty list = pass). Pure."""
    v: Dict[str, List[Dict[str, Any]]] = {k: [] for k in INVARIANTS}
    for o in orders:
        if str(o.get("placed_agent") or "").lower() == "agentic":
            v["order_without_phone_approval"].append({
                "order_id": o.get("id"), "symbol": o.get("symbol"), "side": o.get("side"),
                "state": o.get("state"), "created_at": o.get("created_at")})

    mode_by_run = {str(c.get("run_id")): c.get("mode") for c in cycles}
    for rec in placed:
        run_id = rec.get("run_id")
        if not run_id:
            continue  # a manual /rh-execute placement; not part of an agentic cycle
        ident = {"symbol": rec.get("symbol"), "side": rec.get("side"), "run_id": run_id,
                 "client_order_id": rec.get("client_order_id")}
        if mode_by_run.get(str(run_id)) == "review":
            v["placed_in_review_cycle"].append(ident)
        if not rec.get("ref_id"):
            v["placed_without_ref_id"].append(ident)
        pf = preflights.get(str(run_id))
        verdict = None
        if pf:
            verdict = next((x for x in pf.get("verdicts") or []
                            if isinstance(x, Mapping)
                            and str(x.get("client_order_id")) == str(rec.get("client_order_id"))), None)
        if verdict is None or not verdict.get("passed"):
            v["placed_vetoed_or_oversized"].append({**ident, "why": "no passing preflight verdict"})
        else:
            q, allowed = rec.get("qty"), verdict.get("qty")
            if isinstance(q, (int, float)) and isinstance(allowed, (int, float)) and q > allowed + _EPS:
                v["placed_vetoed_or_oversized"].append({**ident, "why": f"qty {q:g} > preflight {allowed:g}"})

    keys = Counter(str(r.get("dedup_key")) for r in placed if r.get("dedup_key"))
    for key, n in sorted(keys.items()):
        if n > 1:
            v["duplicate_placement"].append({"dedup_key": key, "count": n})
    return v


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

def _horizon_days(text: Any) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*d\s*", str(text or ""))
    return int(m.group(1)) if m else DEFAULT_HORIZON_DAYS


def _valid_thesis(run_id: Optional[str], coid: Optional[str], output_dir: Optional[Any],
                  cache: Dict[str, Any]) -> Optional[Mapping[str, Any]]:
    if not run_id or not coid:
        return None
    if run_id not in cache:
        root = _output_dir(output_dir) / "agentic" / run_id
        cyc = _read_json(root / "cycle.json") or {}
        valid = set(cyc.get("valid_thesis_ids") or []) if isinstance(cyc, Mapping) else set()
        doc = _read_json(root / "theses.json") if valid else None
        cache[run_id] = (valid, (doc or {}).get("theses") or {} if isinstance(doc, Mapping) else {})
    valid, theses = cache[run_id]
    t = theses.get(coid) if coid in valid else None
    return t if isinstance(t, Mapping) else None


def outcomes(
    *,
    placed: Sequence[Mapping[str, Any]],
    approval_states: Mapping[str, Mapping[str, Any]],
    fills: Sequence[Any],
    last_prices: Mapping[str, Optional[float]],
    now: datetime,
    output_dir: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """One row per approved placement. Moves are signed so positive = the trade's direction."""
    from execution.receipts_store import make_dedup_key

    fills_by_key: Dict[str, List[Any]] = {}
    for f in fills:
        fills_by_key.setdefault(make_dedup_key(f.symbol, f.side, f.timestamp), []).append(f)
    cache: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []
    for rec in placed:
        aid = rec.get("approval_id")
        state = (approval_states.get(str(aid)) or {}).get("state") if aid else None
        if state not in ("approved", "auto_approved"):
            continue
        sym, side = str(rec.get("symbol") or "").upper(), str(rec.get("side") or "").lower()
        group = fills_by_key.get(str(rec.get("dedup_key") or ""), [])
        qty = sum(f.quantity for f in group)
        entry = (sum(f.quantity * f.price for f in group) / qty) if qty > _EPS else None
        filled_at = min((f.timestamp for f in group), default=None)
        last = last_prices.get(sym)
        move = None
        if entry is not None and last is not None and entry > _EPS:
            raw = (last - entry) / entry * 100.0
            move = round(raw if side == "buy" else -raw, 4)
        thesis = _valid_thesis(rec.get("run_id"), rec.get("client_order_id"), output_dir, cache)
        horizon = _horizon_days(thesis.get("horizon")) if thesis else DEFAULT_HORIZON_DAYS
        age = (now - filled_at).total_seconds() / 86400.0 if filled_at else None
        if entry is None:
            status = "no_fill_found"
        elif age is not None and age < horizon:
            status = "too_early"
        else:
            status = "horizon_reached"
        rows.append({
            "symbol": sym, "side": side, "approval_id": aid, "run_id": rec.get("run_id"),
            "entry_price": round(entry, 4) if entry is not None else None,
            "last_price": last, "move_pct_in_trade_direction": move,
            "filled_at": filled_at.isoformat() if filled_at else None,
            "age_days": round(age, 2) if age is not None else None,
            "horizon_days": horizon, "status": status,
            "thesis": thesis.get("thesis") if thesis else None,
            "invalidation": thesis.get("invalidation") if thesis else None,
        })
    return rows


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def burnin_status(
    *,
    invariants: Mapping[str, Sequence[Any]],
    health: Mapping[str, Any],
    cycles: Sequence[Mapping[str, Any]],
    reconciliation: Mapping[str, Any],
    max_notional: Optional[float],
    required_days: int = REQUIRED_REVIEW_DAYS,
    recent_days: int = RECENT_DAYS,
) -> Dict[str, Any]:
    """READY / NOT READY with every unmet item named. Pure, advice only."""
    unmet: List[str] = []
    failed_inv = [k for k in INVARIANTS if invariants.get(k)]
    if failed_inv:
        unmet.append("safety invariants failed: " + ", ".join(failed_inv))
    n_days = len(health.get("review_days") or [])
    if n_days < required_days:
        unmet.append(f"{required_days - n_days} more day(s) with a completed review cycle "
                     f"({n_days}/{required_days})")
    cycle_days = sorted({d for d in (_run_day(str(c.get("run_id"))) for c in cycles) if d})
    recent = set(cycle_days[-recent_days:])
    recent_failed = [c.get("run_id") for c in cycles
                     if c.get("status") == "failed" and _run_day(str(c.get("run_id"))) in recent]
    if recent_failed:
        unmet.append(f"{len(recent_failed)} failed cycle(s) in the last {recent_days} cycle days: "
                     + ", ".join(str(r) for r in recent_failed))
    if not reconciliation.get("ok"):
        unmet.append("reconciliation not clean" + (f" ({reconciliation['error']})"
                                                    if reconciliation.get("error") else ""))
    if not (isinstance(max_notional, (int, float)) and math.isfinite(max_notional) and max_notional > 0):
        unmet.append("ROBINHOOD_MAX_NOTIONAL_PER_ORDER is not set to a positive cap")
    return {"ready": not unmet, "unmet": unmet, "review_days": n_days,
            "required_days": required_days, "safety_failures": failed_inv}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class ReviewResult:
    exit_code: int
    review: Dict[str, Any]
    path: Optional[Path]


def run_review(
    *,
    day: Optional[date] = None,
    output_dir: Optional[Any] = None,
    now: Optional[datetime] = None,
    max_notional: Optional[float] = None,
) -> ReviewResult:
    """Build ``review.json`` for ``day`` from its saved inputs and the run files."""
    from execution.receipts_store import latest_approval_states, read_placed_ledger, reconcile

    now = now or datetime.now(timezone.utc)
    day = day or now.date()
    folder = review_dir(day, output_dir)
    orders_payload = _read_json(folder / "inputs" / "orders.json")
    if orders_payload is None:
        review = {"day": day.isoformat(), "generated_at": now.isoformat(), "usable": False,
                  "error": f"inputs/orders.json missing or unreadable in {folder}"}
        return ReviewResult(EXIT_UNUSABLE, review, None)

    if max_notional is None:
        try:
            from settings import settings
            max_notional = float(settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER)
        except Exception:
            max_notional = None

    all_orders = orders_from_mcp(orders_payload)
    agent_orders = [o for o in all_orders if _is_agent_initiated(o)]
    fills, unresolved = fills_from_mcp(agent_orders)
    quotes = quotes_from_mcp(_read_json(folder / "inputs" / "quotes.json"))
    last_prices = {s: q.last if q.last is not None else None for s, q in quotes.items()}
    approvals_on = approvals_required_from_mcp(_read_json(folder / "inputs" / "approval_setting.json"))

    out = _output_dir(output_dir)
    placed = read_placed_ledger(out)
    approval_states = latest_approval_states(out)
    cycles = load_cycles(out)
    preflights = load_preflights(out)

    recon = reconcile(out, fills=fills)
    inv = check_invariants(orders=all_orders, placed=placed, cycles=cycles, preflights=preflights)
    health = cycle_health(cycles, preflights, approval_states)
    outs = outcomes(placed=placed, approval_states=approval_states, fills=fills,
                    last_prices=last_prices, now=now, output_dir=out)
    status = burnin_status(invariants=inv, health=health, cycles=cycles,
                           reconciliation=recon, max_notional=max_notional)
    if approvals_on is not True:
        status["unmet"].append("trade approvals are not confirmed on "
                               "(save get_trade_approval_setting output)" if approvals_on is None
                               else "trade approvals are OFF in the Robinhood app")
        status["ready"] = False

    review = {
        "day": day.isoformat(),
        "generated_at": now.isoformat(),
        "usable": True,
        "approvals_required": approvals_on,
        "orders": {"total": len(all_orders), "agent_initiated": len(agent_orders),
                   "fills": len(fills), "unresolved_filled_orders": unresolved},
        "reconciliation": recon,
        "invariants": inv,
        "health": health,
        "outcomes": outs,
        "burnin": status,
    }
    path = folder / REVIEW_FILENAME
    _write_json_atomic(path, review)
    return ReviewResult(EXIT_READY if status["ready"] else EXIT_NOT_READY, review, path)


def latest_review(output_dir: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    root = _output_dir(output_dir) / "agentic" / REVIEW_DIRNAME
    try:
        days = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    except Exception:
        return None
    for d in days:
        r = _read_json(d / REVIEW_FILENAME)
        if isinstance(r, dict) and r.get("usable"):
            return r
    return None


def build_push(review: Mapping[str, Any]) -> Tuple[str, str, str]:
    """``(title, body, priority)`` for the daily push, from ``review.json`` only."""
    b = review.get("burnin") or {}
    h = review.get("health") or {}
    if b.get("safety_failures"):
        title, prio = "Agentic burn-in: SAFETY CHECK FAILED", "high"
    else:
        state = "READY for live (your call)" if b.get("ready") else "NOT READY"
        title = f"Burn-in day {b.get('review_days', 0)}/{b.get('required_days', REQUIRED_REVIEW_DAYS)}: {state}"
        prio = "default"
    cyc = h.get("cycles") or {}
    vetoes = h.get("vetoes") or {}
    top = ", ".join(f"{k} {n}" for k, n in sorted(vetoes.items(), key=lambda kv: -kv[1])[:3])
    appr = h.get("approvals") or {}
    lines = [
        f"Cycles: {cyc.get('completed', 0)} completed, {cyc.get('failed', 0)} failed.",
        f"Orders that would pass: {h.get('n_would_pass', 0)}; vetoes: {sum(vetoes.values())}"
        + (f" ({top})" if top else "") + ".",
        "Approvals: " + (", ".join(f"{k} {n}" for k, n in sorted(appr.items())) or "none") + ".",
    ]
    for item in b.get("unmet") or []:
        lines.append(f"• {item}")
    body = "\n".join(lines)
    return title, (body if len(body) <= 3800 else body[:3799] + "…"), prio


# ---------------------------------------------------------------------------
# Launcher (what the daily launchd timer runs)
# ---------------------------------------------------------------------------

def allowed_tools() -> List[str]:
    """The Reviewer's own tools plus the coordinator's, parsed from the agent file."""
    from execution.agentic_cycle import agent_tools
    return sorted(set(agent_tools(REVIEWER_AGENT)) | set(COORDINATOR_TOOLS))


def _recorded_review(days: Sequence[date], started: datetime,
                     output_dir: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """A usable ``review.json`` for one of ``days`` written at or after ``started``.

    This, not ``claude``'s exit code, is what proves the review ran: ``claude
    -p`` exits 0 when the session ran and 1 when it errored (expired login,
    API error), whatever the burn-in verdict was.
    """
    for day in days:
        path = review_dir(day, output_dir) / REVIEW_FILENAME
        try:
            written = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        # 1 s slack: some filesystems store mtimes at 1 s resolution.
        if written.timestamp() + 1.0 < started.timestamp():
            continue  # a stale file from an earlier run, not this one
        r = _read_json(path)
        if isinstance(r, dict) and r.get("usable"):
            return r
    return None


def launch(*, runner: Callable[..., Any] = None, claude_bin: str = "claude",
           notifier: Optional[Callable[..., bool]] = None,
           output_dir: Optional[Any] = None,
           clock: Optional[Callable[[], datetime]] = None) -> int:
    """Run ``/rh-agentic-review`` headless with a hard timeout. Never raises.

    Success means a usable ``review.json`` for today (UTC, the day ``run``
    uses) was written during this launch; then the exit code follows its
    verdict (0 READY, 1 NOT READY) and the review itself has sent that day's
    push. Anything else (timeout, missing CLI, a session that errored, a
    session that ended without recording a review) pushes one
    "did not run" alert with the reason and the last line ``claude`` printed,
    and returns 2. One JSON summary line always goes to stdout, so the
    launchd ``.out`` log shows what happened.
    """
    import subprocess
    from execution.agentic_cycle import REPO_ROOT, _output_tail, _said

    clock = clock or (lambda: datetime.now(timezone.utc))
    runner = runner or subprocess.run
    started = clock()
    reason: Optional[str] = None
    rc: Optional[int] = None
    tail = ""
    said = ""
    try:
        cmd = [claude_bin, "-p", "/rh-agentic-review", "--allowedTools", ",".join(allowed_tools())]
        proc = runner(cmd, cwd=str(REPO_ROOT), timeout=LAUNCH_TIMEOUT_SECONDS,
                      capture_output=True, text=True)
        rc = getattr(proc, "returncode", None)
        tail = _output_tail(proc)
        said = _said(proc)
        if rc != 0:
            reason = f"claude exited with code {rc}"
    except subprocess.TimeoutExpired as exc:
        tail = _output_tail(exc)
        said = _said(exc)
        reason = f"timed out after {LAUNCH_TIMEOUT_SECONDS} s"
    except FileNotFoundError:
        reason = f"'{claude_bin}' CLI not found on PATH"
    except Exception as exc:
        reason = f"launch error: {exc}"

    days = sorted({started.date(), clock().date()})
    review = _recorded_review(days, started, output_dir)
    if review is None and reason is None:
        reason = "the session ended without recording a review"
    print(json.dumps({"review_recorded": review is not None, "claude_exit": rc,
                      "reason": None if review is not None else reason,
                      "claude_said": said, "output_tail": tail}))
    if review is not None:
        return EXIT_READY if (review.get("burnin") or {}).get("ready") else EXIT_NOT_READY

    body = f"{reason}. No review was recorded today."
    if said:
        body = f"{body} claude said: {said}"
    try:
        if notifier is None:
            from alerting import notify as notifier
        notifier("Agentic burn-in review did not run", body, priority="high")
    except Exception as exc:
        logger.warning("agentic_review: alert not sent (%s)", exc)
    return EXIT_UNUSABLE


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_day(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Agent 5 (Reviewer): burn-in scorecard for the agentic Robinhood cycle.")
    ap.add_argument("--output-dir", help="override $OUTPUT_DIR")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("save", help="save one raw MCP response (JSON on stdin) for today's review")
    s.add_argument("name", choices=INPUT_NAMES)
    s.add_argument("--day", help="YYYY-MM-DD (default: today, UTC)")
    r = sub.add_parser("run", help="build review.json (exit 0 READY, 1 NOT READY, 2 inputs unusable)")
    r.add_argument("--day")
    r.add_argument("--notify", action="store_true", help="send the daily push")
    sub.add_parser("status", help="print the latest review's burn-in status")
    sub.add_parser("launch", help="run /rh-agentic-review headless (the daily timer's entry point)")
    args = ap.parse_args(argv)

    if args.cmd == "launch":
        return launch(output_dir=args.output_dir)

    if args.cmd == "save":
        try:
            payload = json.load(sys.stdin)
        except Exception as exc:
            print(json.dumps({"error": f"stdin is not JSON: {exc}"}))
            return EXIT_UNUSABLE
        day = _parse_day(args.day) or datetime.now(timezone.utc).date()
        print(json.dumps({"saved": str(save_input(args.name, payload, day=day, output_dir=args.output_dir))}))
        return 0

    if args.cmd == "status":
        rev = latest_review(args.output_dir)
        if rev is None:
            print(json.dumps({"error": "no usable review yet"}))
            return EXIT_UNUSABLE
        print(json.dumps({"day": rev.get("day"), "burnin": rev.get("burnin")}, indent=2))
        return EXIT_READY if (rev.get("burnin") or {}).get("ready") else EXIT_NOT_READY

    res = run_review(day=_parse_day(args.day), output_dir=args.output_dir)
    title, body, prio = build_push(res.review) if res.review.get("usable") else (
        "Agentic burn-in review could not run", res.review.get("error", "inputs unusable"), "high")
    sent = None
    if args.notify:
        from alerting import notify
        sent = bool(notify(title, body, priority=prio))
    print(json.dumps({"exit_code": res.exit_code, "path": str(res.path) if res.path else None,
                      "title": title, "body": body, "notified": sent}, indent=2))
    return res.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

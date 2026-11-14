"""The Agentic account's holdings, as the rh-* agents last saw them over the Robinhood MCP.

Contract K1 of ``.claude/agentic_exits_speed_partial_fills_implementation_plan.md``.

Why this exists: the daemon only reads the MAIN Robinhood account
(``data/robinhood_portfolio.fetch_account_snapshot``). The separate Agentic
account is reachable only through the Robinhood MCP tools the ``rh-*`` agents
call. Without this file a position the agents opened was never in the daemon's
universe, never evaluated as held and never got a SELL intent -- it could not
be exited.

Writer (CLI, run by the Risk Manager every cycle and by the Reviewer daily)::

    .venv/bin/python3 -m execution.agentic_holdings save \\
        --positions <get_equity_positions.json> [--quotes <get_equity_quotes.json>] \\
        --source risk|review [--output-dir DIR]

It parses with ``agentic_preflight.AccountState.from_mcp`` / ``quotes_from_mcp``
(one parser, never a second one) and writes
``OUTPUT_DIR/agentic/agentic_holdings.json`` atomically::

    {"fetched_at": ISO-8601 UTC, "source": "risk"|"review",
     "positions": {SYM: {"quantity", "sellable", "average_cost", "current_price"}}}

Positions that cannot be read -> nothing is written, a JSON error is printed and
the exit code is 2 (the previous file stays until it goes stale). A clean read
that holds nothing writes an empty ``positions`` map: that IS a real reading.
A missing cost basis or price is ``null``, never a guess (CONSTRAINT #4).
``current_price`` is the quote's last trade, else its bid.

Reader (the daemon, once per cycle per consumer)::

    load_agentic_positions(output_dir=None, *, now=None, max_age_hours=MAX_AGE_HOURS)

returns ``{SYM: PortfolioPosition}``. It never raises: a missing, stale
(older than ``MAX_AGE_HOURS``) or unparseable file gives ``{}`` with one
WARNING. A position with no ``average_cost`` or no ``current_price`` (or a
quantity <= 0) is skipped and logged -- it cannot be evaluated honestly.
``dividends_received`` is 0.0 (the MCP does not report it per position).

Import-light on purpose: the daemon reads this every cycle, so the module
imports only the standard library at load time; the parsers and the
``PortfolioPosition`` DTO are imported inside the functions that need them.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

HOLDINGS_DIRNAME = "agentic"
HOLDINGS_FILENAME = "agentic_holdings.json"
#: Covers a weekend plus a market holiday: the Reviewer refreshes the file once
#: per trading day even when no cycle runs.
MAX_AGE_HOURS = 96.0
#: Clock skew tolerated on a ``fetched_at`` in the future before the file is
#: treated as unreadable (a timestamp from the future cannot be trusted).
_FUTURE_SKEW = timedelta(minutes=5)
SOURCES = ("risk", "review")


def _output_dir(output_dir: Optional[Any]) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    from settings import settings
    return Path(settings.OUTPUT_DIR)


def holdings_path(output_dir: Optional[Any] = None) -> Path:
    """``<OUTPUT_DIR>/agentic/agentic_holdings.json``."""
    return _output_dir(output_dir) / HOLDINGS_DIRNAME / HOLDINGS_FILENAME


def _finite(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _parse_dt(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def build_holdings(positions_payload: Any, quotes_payload: Any = None, *, source: str,
                   now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The holdings document for raw MCP payloads, or ``None`` when the
    positions cannot be read (never a fabricated empty book). Pure."""
    from execution.agentic_preflight import AccountState, quotes_from_mcp

    if positions_payload is None:
        return None
    account = AccountState.from_mcp(None, positions_payload)
    if account.positions is None:
        return None
    quotes = quotes_from_mcp(quotes_payload) if quotes_payload is not None else {}
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for sym, h in sorted(account.positions.items()):
        q = quotes.get(sym)
        price = (q.last or q.bid) if q is not None else None
        out[sym] = {
            "quantity": h.qty,
            "sellable": h.sellable,
            "average_cost": h.avg_cost,
            "current_price": price,
        }
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {"fetched_at": when.isoformat(), "source": source, "positions": out}


def write_holdings(doc: Mapping[str, Any], output_dir: Optional[Any] = None) -> Path:
    """Atomically write the holdings document (temp file + ``os.replace``)."""
    from reporting.atomic_write import atomic_write_json

    path = holdings_path(output_dir)
    atomic_write_json(path, doc)
    return path


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

def load_agentic_positions(output_dir: Optional[Any] = None, *, now: Optional[datetime] = None,
                           max_age_hours: float = MAX_AGE_HOURS) -> Dict[str, Any]:
    """``{SYMBOL: PortfolioPosition}`` from the saved Agentic holdings. Never raises.

    Missing, stale, from-the-future or unparseable file -> ``{}`` and one
    WARNING. Positions without a cost basis or price, or with quantity <= 0,
    are skipped (logged). ``dividends_received`` is 0.0.
    """
    try:
        path = holdings_path(output_dir)
    except Exception as exc:  # noqa: BLE001 -- settings unavailable
        logger.warning("agentic_holdings: output dir unresolvable (%s); no Agentic positions.", exc)
        return {}
    try:
        if not path.exists():
            logger.info("agentic_holdings: %s not found; no Agentic positions this cycle.", path)
            return {}
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, Mapping):
            raise ValueError("not a JSON object")
        fetched = _parse_dt(doc.get("fetched_at"))
        if fetched is None:
            raise ValueError("fetched_at missing or unparseable")
        rows = doc.get("positions")
        if not isinstance(rows, Mapping):
            raise ValueError("positions is not an object")
        now = (now or datetime.now(timezone.utc))
        now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age = now - fetched
        if age < -_FUTURE_SKEW:
            raise ValueError(f"fetched_at {fetched.isoformat()} is in the future")
        if age > timedelta(hours=float(max_age_hours)):
            logger.warning(
                "agentic_holdings: %s is stale (fetched_at %s, older than %.0f h); "
                "no Agentic positions this cycle.", path, fetched.isoformat(), max_age_hours,
            )
            return {}
    except Exception as exc:  # noqa: BLE001 -- never raise into the cycle
        logger.warning("agentic_holdings: %s unreadable (%s); no Agentic positions this cycle.", path, exc)
        return {}

    try:
        from data.robinhood_portfolio import PortfolioPosition
    except Exception as exc:  # noqa: BLE001
        logger.warning("agentic_holdings: PortfolioPosition unavailable (%s); no Agentic positions.", exc)
        return {}

    out: Dict[str, Any] = {}
    skipped = []
    for raw_sym, row in rows.items():
        sym = str(raw_sym or "").upper().strip()
        if not sym or not isinstance(row, Mapping):
            skipped.append(f"{raw_sym!s}: malformed")
            continue
        qty = _finite(row.get("quantity"))
        cost = _finite(row.get("average_cost"))
        price = _finite(row.get("current_price"))
        if qty is None or qty <= 0:
            skipped.append(f"{sym}: quantity {row.get('quantity')!r}")
            continue
        if cost is None or cost <= 0:
            skipped.append(f"{sym}: no average_cost")
            continue
        if price is None or price <= 0:
            skipped.append(f"{sym}: no current_price")
            continue
        basis = qty * cost
        market_value = qty * price
        pl = market_value - basis
        out[sym] = PortfolioPosition(
            symbol=sym,
            quantity=qty,
            average_cost=cost,
            current_price=price,
            market_value=market_value,
            unrealized_pl=pl,
            unrealized_pl_pct=pl / basis * 100.0,
            dividends_received=0.0,
            name="",
        )
    if skipped:
        logger.warning(
            "agentic_holdings: skipped %d Agentic position(s) that cannot be evaluated: %s",
            len(skipped), "; ".join(skipped),
        )
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Save the Agentic account's holdings (from saved Robinhood MCP output) for the daemon.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    save = sub.add_parser("save", help="write OUTPUT_DIR/agentic/agentic_holdings.json")
    save.add_argument("--positions", required=True, help="saved get_equity_positions response (or a list of pages)")
    save.add_argument("--quotes", default=None, help="saved get_equity_quotes response for the held symbols")
    save.add_argument("--source", required=True, choices=SOURCES)
    save.add_argument("--output-dir", default=None)
    args = ap.parse_args(argv)

    try:
        positions_payload = _load_json(args.positions)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"positions unreadable: {args.positions} ({exc})"}))
        return 2
    quotes_payload = None
    if args.quotes:
        try:
            quotes_payload = _load_json(args.quotes)
        except Exception as exc:  # noqa: BLE001
            # Fail closed: a book written with every price null would hide every
            # position from the daemon; keep the previous file instead.
            print(json.dumps({"error": f"quotes unreadable: {args.quotes} ({exc})"}))
            return 2

    doc = build_holdings(positions_payload, quotes_payload, source=args.source)
    if doc is None:
        print(json.dumps({"error": f"positions unreadable: {args.positions} (no positions list)"}))
        return 2
    try:
        path = write_holdings(doc, args.output_dir)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"write failed ({exc})"}))
        return 2
    positions = doc["positions"]
    print(json.dumps({
        "saved": str(path),
        "fetched_at": doc["fetched_at"],
        "source": doc["source"],
        "n_positions": len(positions),
        "missing_price": sorted(s for s, p in positions.items() if p["current_price"] is None),
        "missing_cost": sorted(s for s, p in positions.items() if p["average_cost"] is None),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

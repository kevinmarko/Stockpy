"""
gui/decision_log.py — Manual execution decision journal (Tier 1 / 1.3).

The operator logs whether they acted on, passed, or modified each advisory
signal.  Entries are appended to ``output/decision_log.jsonl`` (JSON-Lines
format, one entry per line, atomic append).  A join helper links "acted"
entries back to ``TransactionsStore`` records within a configurable time
window so the calibration tracker (1.2) can filter to decisions the
operator actually executed.

Public API
----------
``DecisionEntry``       — frozen dataclass; one row in the journal.
``ActionTaken``         — ``Literal["acted", "passed", "modified"]``
``append_decision``     — write one entry to the JSONL file.
``read_decisions``      — read all entries (tolerant; corrupt lines skipped).
``decisions_df``        — same data as a typed pandas DataFrame.
``join_to_store``       — find the nearest matching trade in TransactionsStore.
``log_decision``        — orchestrate: build entry, optionally join, append.

Design constraints
------------------
* No streamlit imports — module is headlessly testable (CONSTRAINT #7).
* Missing file / corrupt line → degrade silently, never raise (CONSTRAINT #6).
* Never fabricate a trade_id match (CONSTRAINT #4).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

import pandas as pd

logger = logging.getLogger(__name__)

ActionTaken = Literal["acted", "passed", "modified"]

DEFAULT_LOG_PATH = Path("output/decision_log.jsonl")

_SCHEMA: dict[str, type] = {
    "symbol": str,
    "action_taken": str,
    "signal_action": str,
    "conviction": float,
    "notes": str,
    "timestamp": str,
    "signal_ts": str,
    "trade_id": "Int64",
}


@dataclass(frozen=True)
class DecisionEntry:
    """One operator decision record."""

    symbol: str
    action_taken: ActionTaken   # "acted" | "passed" | "modified"
    signal_action: str          # system recommendation, e.g. "BUY"
    conviction: Optional[float] # advisory conviction [0, 1]; None when unavailable
    notes: str                  # free text; empty string when not provided
    timestamp: str              # ISO-8601 UTC, when the operator logged this
    signal_ts: str              # ISO-8601 UTC of the originating snapshot
    trade_id: Optional[int] = None  # set when successfully joined to TransactionsStore


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _now_utc_iso() -> str:
    """Current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def append_decision(
    entry: DecisionEntry,
    log_path: Path = DEFAULT_LOG_PATH,
) -> None:
    """Append *entry* to the JSONL log (atomic line-append).

    Creates ``log_path.parent`` if it does not exist.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(entry), default=str)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(payload + "\n")


def read_decisions(log_path: Path = DEFAULT_LOG_PATH) -> list[DecisionEntry]:
    """Read all ``DecisionEntry`` records from *log_path*.

    Tolerant: missing file → ``[]``; corrupt / blank lines → logged at DEBUG
    and skipped; remaining valid entries are always returned (CONSTRAINT #6).
    """
    if not log_path.exists():
        return []
    entries: list[DecisionEntry] = []
    with open(log_path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                entries.append(
                    DecisionEntry(
                        symbol=d["symbol"],
                        action_taken=d["action_taken"],
                        signal_action=d.get("signal_action", ""),
                        conviction=d.get("conviction"),
                        notes=d.get("notes", ""),
                        timestamp=d["timestamp"],
                        signal_ts=d.get("signal_ts", ""),
                        trade_id=d.get("trade_id"),
                    )
                )
            except Exception as exc:
                logger.debug("decision_log line %d skipped: %s", lineno, exc)
    return entries


def decisions_df(log_path: Path = DEFAULT_LOG_PATH) -> pd.DataFrame:
    """Return all decision entries as a typed DataFrame.

    Returns an empty DataFrame with the correct column schema when the log
    file does not exist or contains no valid entries (CONSTRAINT #4).
    """
    entries = read_decisions(log_path)
    if not entries:
        return pd.DataFrame(
            {c: pd.Series(dtype=t) for c, t in _SCHEMA.items()}
        )
    df = pd.DataFrame([asdict(e) for e in entries])
    df["trade_id"] = df["trade_id"].astype("Int64")
    return df


# ---------------------------------------------------------------------------
# Join to TransactionsStore
# ---------------------------------------------------------------------------

def join_to_store(
    entry: DecisionEntry,
    transactions_store,
    window_hours: float = 24.0,
) -> Optional[int]:
    """Return the ``trade_id`` of the closest matching trade within ±*window_hours*.

    Match criteria (AND):
    * ``symbol`` matches (case-insensitive).
    * ``|trade.entry_ts − entry.timestamp| ≤ window_hours``.

    Returns ``None`` when no match exists or when any step fails (CONSTRAINT #6 —
    never raise; never fabricate a trade_id — CONSTRAINT #4).
    """
    try:
        ts_decision = datetime.fromisoformat(entry.timestamp).replace(tzinfo=None)
        symbol_upper = entry.symbol.upper()
        delta = timedelta(hours=window_hours)

        all_trades = transactions_store.get_trade_history(symbol_upper)
        if all_trades.empty:
            return None

        df = all_trades.copy()
        df["entry_ts"] = pd.to_datetime(df["entry_ts"]).dt.tz_localize(None)
        within = df[
            (df["entry_ts"] >= ts_decision - delta)
            & (df["entry_ts"] <= ts_decision + delta)
        ]
        if within.empty:
            return None

        within = within.copy()
        within["_delta"] = (within["entry_ts"] - ts_decision).abs()
        best = within.loc[within["_delta"].idxmin()]
        return int(best["trade_id"])

    except Exception as exc:
        logger.debug("join_to_store failed for %s: %s", entry.symbol, exc)
        return None


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def log_decision(
    symbol: str,
    action_taken: ActionTaken,
    signal_action: str,
    conviction: Optional[float],
    *,
    notes: str = "",
    signal_ts: str = "",
    transactions_store=None,
    log_path: Path = DEFAULT_LOG_PATH,
    window_hours: float = 24.0,
    now_fn=None,
) -> DecisionEntry:
    """Create, optionally join to a trade record, and append a ``DecisionEntry``.

    Parameters
    ----------
    symbol:
        Ticker symbol (case-insensitive; normalised to uppercase).
    action_taken:
        One of ``"acted"`` / ``"passed"`` / ``"modified"``.
    signal_action:
        The system's recommended action (e.g. ``"BUY"``).
    conviction:
        Advisory conviction score [0, 1], or ``None`` when unavailable.
    notes:
        Operator free text (e.g. rationale for modifying).
    signal_ts:
        ISO timestamp of the snapshot this signal came from.
    transactions_store:
        A ``TransactionsStore`` instance.  When supplied and
        ``action_taken="acted"``, a matching trade is looked up and
        ``trade_id`` is set on the entry (never fabricated).
    log_path:
        Override for the JSONL file path.
    window_hours:
        Look-back/forward window (hours) for the trade join.
    now_fn:
        Injectable clock function for tests; defaults to ``_now_utc_iso``.

    Returns
    -------
    The persisted ``DecisionEntry`` (frozen).
    """
    ts = (now_fn or _now_utc_iso)()
    entry = DecisionEntry(
        symbol=symbol.upper(),
        action_taken=action_taken,
        signal_action=signal_action,
        conviction=conviction,
        notes=notes,
        timestamp=ts,
        signal_ts=signal_ts,
        trade_id=None,
    )

    # Only try to join when the operator explicitly says they acted.
    if action_taken == "acted" and transactions_store is not None:
        trade_id = join_to_store(entry, transactions_store, window_hours=window_hours)
        if trade_id is not None:
            entry = DecisionEntry(**{**asdict(entry), "trade_id": trade_id})

    append_decision(entry, log_path=log_path)
    return entry

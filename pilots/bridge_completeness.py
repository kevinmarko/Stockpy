"""pilots/bridge_completeness.py — Empirical paper-trade -> transactions_store
bridge completeness metric (READ-ONLY).
=====================================================================

``data/paper_account_store.py``'s ``PaperClosedTrade`` table
(``paper_closed_trades``) is the real, always-written paper-trade ledger.
When ``settings.PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`` is ``True``,
``PaperAccountStore._record_closed_trade`` best-effort copies each closed
paper trade into ``transactions_store.py``'s ``trades`` table (via
``TransactionsStore.record_trade``/``close_trade``) — and that bridge FAILS
OPEN: a bridge-write failure is logged and counted on the writer's own
``PaperAccountStore._transactions_bridge_failures`` in-process counter, but
never blocks or rolls back the paper close itself. That counter is exactly
that: an IN-PROCESS counter on one specific ``PaperAccountStore`` instance
(typically the long-running orchestrator daemon's). A fresh, read-only
``PaperAccountStore(readonly=True)`` constructed here to answer an API
request has its own counter, always ``0``, regardless of the real bridge's
history — surfacing THAT number here would look like a real completeness
signal while actually being a structural non-sequitur (CONSTRAINT #4: never
fabricate a value that looks measured but isn't).

This module instead builds the signal EMPIRICALLY and cross-process-safely:
for the ``window`` most recent rows in ``paper_closed_trades``, it checks
whether a genuinely matching row exists in ``transactions_store``'s
``trades`` table, by identity (symbol + entry_ts + exit_ts — the three
fields ``_record_closed_trade`` writes byte-identically to both tables from
the same in-memory values at close time). This works regardless of which
process wrote either table, and regardless of whether the CURRENT process
ever ran a bridge write itself.

Matching contract
------------------
A paper trade whose ``entry_ts`` is ``None`` (a genuinely unknown, e.g.
legacy/migrated, position — see ``PaperClosedTrade.entry_ts``'s own
nullable comment) cannot be matched by identity at all: there is no way to
tell "this trade has no matching transactions_store row" apart from "this
trade cannot be checked". Such trades are EXCLUDED from both the numerator
and the denominator of ``completeness_pct`` — never silently counted as
bridged (would fabricate a match) and never counted as NOT bridged (would
fabricate a miss). They are logged (debug) and otherwise invisible in the
returned counts, by design (see ``bridge_completeness_summary``'s
docstring for why no extra field was added for this).

Design invariants (identical to ``pilots/observability.py``/
``pilots/calibration.py``, this reader's precedent):

* **Never raises (CONSTRAINT #6)** — every failure mode (missing/broken
  store, unreadable DB, malformed row) degrades to an honest
  ``n_trades_checked=0`` / ``completeness_pct=None`` shape plus a ``reason``
  string; never an exception escapes this module.
* **Never fabricates (CONSTRAINT #4)** — ``completeness_pct`` is ``None``
  whenever there was nothing genuinely checkable, never a guessed number.
  A genuine 0% (measured: real trades exist, none of them have a matching
  transactions_store row) is a real, reportable zero — not suppressed.
* Imports ``data.paper_account_store`` and ``transactions_store`` — neither
  is on ``api/pilots_api.py``'s AST-guard denylist (only
  ``processing_engine``, ``strategy_engine``, ``forecasting_engine``,
  ``macro_engine``, ``technical_options_engine``, ``main_orchestrator``,
  ``desktop`` are forbidden). Imports are LAZY (inside the function body),
  matching ``pilots/calibration.py``/``pilots/observability.py``'s
  convention, so a missing/broken dependency degrades gracefully instead of
  breaking import of this module (and this whole API) at process start.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["bridge_completeness_summary"]

# The exact fields _record_closed_trade writes byte-identically to both
# PaperClosedTrade.exit_ts and TransactionsStore's Trade.exit_ts come from
# the SAME in-memory `now` variable at close time (see that method's own
# comment block) — so an exact match is expected. A small tolerance guards
# against any driver-level rounding on the round-trip through SQLite without
# being loose enough to risk a false match between two distinct trades on
# the same symbol closed moments apart.
_MATCH_TOLERANCE_SECONDS = 1e-3

_NO_CLOSED_TRADES_REASON = (
    "No closed paper trades yet — bridge completeness has nothing to check."
)
_NO_CHECKABLE_TRADES_REASON = (
    "None of the closed paper trades in this window have a recorded entry_ts "
    "(e.g. legacy/migrated positions) — bridge completeness cannot be "
    "determined by identity matching for any of them."
)
_DISABLED_INFO_REASON = (
    "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED is currently False — the "
    "figures below reflect whichever transactions_store rows already exist "
    "for these trades (e.g. from before the bridge was disabled); no new "
    "paper close is expected to bridge while the flag stays off."
)


def _degraded_result(window: int, enabled: bool, reason: str) -> Dict[str, Any]:
    """Honest empty/zero shape for any dead-lettered failure (CONSTRAINT #6)."""
    return {
        "enabled": enabled,
        "window": window,
        "n_trades_checked": 0,
        "n_bridged": 0,
        "completeness_pct": None,
        "reason": reason,
    }


def _to_naive_utc(value: Any) -> Optional[datetime]:
    """Best-effort coercion of an ISO string / ``datetime`` / pandas
    ``Timestamp`` to a naive-UTC ``datetime`` for comparison.

    Returns ``None`` (never raises, never a fabricated timestamp) for
    ``None``, ``NaT``, or anything unparseable — a coercion failure must
    read as "cannot match", never as a match OR a definite non-match by
    accident (e.g. two ``None`` values must never compare equal)."""
    if value is None:
        return None
    try:
        import pandas as pd  # local: keep the module import-light at top level

        if isinstance(value, pd.Timestamp):
            if pd.isna(value):
                return None
            value = value.to_pydatetime()
    except Exception:  # noqa: BLE001 — pandas is optional at this narrow point
        pass
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _timestamps_match(a: Optional[datetime], b: Optional[datetime]) -> bool:
    """``True`` only when BOTH sides parsed to a real timestamp and they are
    within tolerance — an unparseable/missing value on either side is a
    non-match, never a fabricated match via a lenient ``None == None``."""
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= _MATCH_TOLERANCE_SECONDS


def bridge_completeness_summary(window: int = 200, *, db_url: Optional[str] = None) -> Dict[str, Any]:
    """Empirical paper-trade -> transactions_store bridge completeness.

    For the ``window`` most recent rows of ``paper_closed_trades``, checks
    whether a matching ``transactions_store`` ``trades`` row genuinely
    exists (by symbol + entry_ts + exit_ts identity — see module docstring).
    Returns::

        {
            "enabled": bool,                     # current settings value
            "window": int,                       # window size requested
            "n_trades_checked": int,              # checkable trades (<= window)
            "n_bridged": int,                     # of those, genuinely matched
            "completeness_pct": Optional[float],  # None iff n_trades_checked == 0
            "reason": Optional[str],              # populated iff n_trades_checked
                                                   # == 0 OR enabled is False
        }

    The empirical check runs regardless of the CURRENT value of
    ``PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED`` — a trade in the window
    may have closed while the flag was previously ``True``, and reporting
    that real, measured history is more honest than suppressing it just
    because the flag happens to read ``False`` right now. ``enabled`` is
    still surfaced (and folded into ``reason`` when ``False``) purely as
    context for interpreting the numbers, per this function's own
    documented contract above.

    ``db_url``: optional explicit SQLAlchemy URL forwarded to both
    ``PaperAccountStore(readonly=True, ...)`` and
    ``TransactionsStore(readonly=True, ...)``. ``None`` (the default) lets
    each store resolve ``db_config.resolve_database_url()`` itself — the
    real production path, and (in a test run) whatever the session's
    autouse DB-isolation fixture has redirected that resolver to. Tests
    that want a self-contained, deterministic fixture independent of that
    fixture's own internal file path should pass an explicit tmp-path-backed
    URL here instead.

    Never raises (CONSTRAINT #6) — every failure mode degrades to
    ``n_trades_checked=0`` / ``completeness_pct=None`` plus a ``reason``.
    """
    try:
        enabled = bool(getattr(settings, "PAPER_TRADES_BRIDGE_TO_TRANSACTIONS_ENABLED", False))
    except Exception as exc:  # noqa: BLE001 — dead-letter: settings read failure
        logger.debug("bridge_completeness_summary: settings read failed: %s", exc)
        enabled = False

    try:
        from data.paper_account_store import PaperAccountStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("bridge_completeness_summary: paper_account_store import failed: %s", exc)
        return _degraded_result(window, enabled, "paper_account_store unavailable.")

    try:
        paper_store = PaperAccountStore(db_url=db_url, readonly=True)
        closed_trades = paper_store.get_full_closed_trades(limit=window)
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("bridge_completeness_summary: get_full_closed_trades failed: %s", exc)
        return _degraded_result(window, enabled, "Could not read paper_closed_trades.")

    if not closed_trades:
        return _degraded_result(window, enabled, _NO_CLOSED_TRADES_REASON)

    try:
        from transactions_store import TransactionsStore
    except Exception as exc:  # noqa: BLE001 — dead-letter: import failure
        logger.debug("bridge_completeness_summary: transactions_store import failed: %s", exc)
        return _degraded_result(window, enabled, "transactions_store unavailable.")

    try:
        symbols = sorted(
            {
                str(t["symbol"]).strip().upper()
                for t in closed_trades
                if t.get("symbol")
            }
        )
        tx_store = TransactionsStore(db_url=db_url, readonly=True)
        histories = tx_store.get_trade_histories_batch(symbols) if symbols else {}
    except Exception as exc:  # noqa: BLE001 — dead-letter: cold/unreadable DB
        logger.warning("bridge_completeness_summary: transactions_store read failed: %s", exc)
        return _degraded_result(window, enabled, "Could not read transactions_store.")

    # Normalize each symbol's transactions_store history to (entry, exit)
    # tuples ONCE here, rather than re-parsing (`_to_naive_utc`) the same
    # rows once per checked paper trade below -- several paper trades sharing
    # one symbol previously re-scanned and re-parsed that symbol's FULL
    # history from scratch for each of them (O(n_trades x history_size)).
    # This is still a per-row pass (tolerance-based matching on parsed
    # datetimes isn't a hashable-key operation), but now bounded to exactly
    # one pass per symbol regardless of how many trades in the window share
    # it -- O(n_trades + total_history_rows) instead.
    try:
        history_pairs: Dict[str, list] = {}
        for symbol, history in histories.items():
            if history is None or history.empty:
                history_pairs[symbol] = []
                continue
            history_pairs[symbol] = [
                (_to_naive_utc(row.get("entry_ts")), _to_naive_utc(row.get("exit_ts")))
                for _, row in history.iterrows()
            ]
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed row/frame
        logger.warning("bridge_completeness_summary: history normalization failed: %s", exc)
        return _degraded_result(window, enabled, "Bridge-completeness matching failed.")

    n_checked = 0
    n_bridged = 0
    n_unmatchable = 0

    try:
        for trade in closed_trades:
            entry_ts = _to_naive_utc(trade.get("entry_ts"))
            if entry_ts is None:
                # Genuinely unknown entry time -- cannot be matched by
                # identity at all. Excluded from BOTH numerator and
                # denominator (see module docstring's "Matching contract").
                n_unmatchable += 1
                continue
            n_checked += 1

            exit_ts = _to_naive_utc(trade.get("exit_ts"))
            symbol = str(trade.get("symbol") or "").strip().upper()
            pairs = history_pairs.get(symbol, [])

            matched = any(
                _timestamps_match(entry_ts, row_entry) and _timestamps_match(exit_ts, row_exit)
                for row_entry, row_exit in pairs
            )
            if matched:
                n_bridged += 1
    except Exception as exc:  # noqa: BLE001 — dead-letter: malformed row/frame
        logger.warning("bridge_completeness_summary: matching failed: %s", exc)
        return _degraded_result(window, enabled, "Bridge-completeness matching failed.")

    if n_unmatchable:
        logger.debug(
            "bridge_completeness_summary: %d of %d closed trade(s) in window "
            "have no entry_ts and were excluded from the completeness check.",
            n_unmatchable,
            len(closed_trades),
        )

    if n_checked == 0:
        return _degraded_result(window, enabled, _NO_CHECKABLE_TRADES_REASON)

    completeness_pct = (n_bridged / n_checked) * 100.0

    return {
        "enabled": enabled,
        "window": window,
        "n_trades_checked": n_checked,
        "n_bridged": n_bridged,
        "completeness_pct": completeness_pct,
        "reason": None if enabled else _DISABLED_INFO_REASON,
    }

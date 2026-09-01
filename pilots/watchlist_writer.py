"""Append/remove operator-named tickers in ``watchlist.txt`` so the advisory
pipeline starts (or stops) tracking them.

Backs the Agentic Trading tab's Discovery "Watch" action: an operator taps a
discovered candidate and ``append_symbols`` appends it to ``watchlist.txt``,
the same file ``main._load_watchlist()`` reads when building the evaluation
universe. It is the programmatic equivalent of the ``agentic-discovery``
skill's step-7 "track a candidate" flow (see that skill's docstring) — same
file, same uppercase/dedup/audit-comment conventions — so the two paths never
diverge.

``remove_symbols``/``record_fetch_failures`` are the other direction: the
3-strike rule ``ml/forecast_backfill.py`` uses to permanently drop a ticker
that neither FMP nor the fallback provider has been able to fetch data for
across 3 consecutive fetch cycles, tracked in a sibling
``watchlist_failures.json`` counter file next to ``watchlist.txt``.

Design constraints (mirrors :mod:`pilots.scan_config_store` /
:mod:`pilots.follows_store`):

* **Dependency-light** — stdlib only. Safe to import on the API path (never
  pulls in a heavy engine).
* **No fabrication / honest failure** (CONSTRAINT #4) — the critical case is the
  ``WATCHLIST`` env var: ``main._load_watchlist()`` gives ``WATCHLIST`` (an
  ``.env`` / ``os.environ`` value) PRECEDENCE over ``watchlist.txt``. When it is
  set, appending to the file is silently ineffective, so this module raises
  :class:`WatchlistEnvPrecedenceError` rather than reporting a write that would
  not take effect — the caller surfaces that honestly instead of lying.
* Appending never places an order and is not retroactive — it takes effect on
  the next ``main.py`` / ``main_orchestrator.py`` universe build.
"""
from __future__ import annotations

import json
import logging
import os
from settings import settings
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "WatchlistWriteError",
    "WatchlistEnvPrecedenceError",
    "InvalidSymbolError",
    "WatchlistAppendResult",
    "append_symbols",
    "remove_symbols",
    "record_fetch_failures",
    "DEFAULT_WATCHLIST_PATH",
]

# Same relative path ``main.WATCHLIST_FILE`` reads (CWD-relative — the Pilots API
# and main.py both run from the repo root), so a write here is read back there.
DEFAULT_WATCHLIST_PATH = Path("watchlist.txt")

# A conservative ticker shape: 1-6 letters, optional ``.``/``-`` class suffix
# (e.g. BRK.B, RDS-A). Deliberately strict — this value is written to a file the
# universe builder trusts, so a malformed token is rejected, never sanitized
# into something plausible (CONSTRAINT #4).
_SYMBOL_RE = re.compile(r"^[A-Z]{1,6}([.\-][A-Z]{1,4})?$")


class WatchlistWriteError(Exception):
    """Base class for watchlist-append failures (stable ``tag`` for the frontend)."""

    tag = "watchlist_write_error"


class WatchlistEnvPrecedenceError(WatchlistWriteError):
    """``WATCHLIST`` env var is set, so ``watchlist.txt`` is ignored by the
    universe builder — appending would be silently ineffective."""

    tag = "watchlist_env_precedence"


class InvalidSymbolError(WatchlistWriteError):
    """A submitted symbol does not match the accepted ticker shape."""

    tag = "invalid_symbol"


@dataclass(frozen=True)
class WatchlistAppendResult:
    """Outcome of an append: which symbols were newly added vs. already present."""

    added: List[str] = field(default_factory=list)
    already_present: List[str] = field(default_factory=list)
    watchlist_file: str = str(DEFAULT_WATCHLIST_PATH)


def _normalize(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _existing_tickers(path: Path) -> List[str]:
    """Uppercase tickers already in the file (non-comment, non-blank lines)."""
    if not path.exists():
        return []
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped.upper())
    return out


def append_symbols(
    symbols: List[str],
    path: Optional[Path] = None,
    *,
    watchlist_env: Optional[str] = None,
    clock: Optional[object] = None,
) -> WatchlistAppendResult:
    """Append *symbols* to ``watchlist.txt`` (uppercase, deduped, audit-commented).

    Parameters
    ----------
    symbols:
        Tickers to track. Validated against :data:`_SYMBOL_RE`; an invalid one
        raises :class:`InvalidSymbolError` BEFORE any write (all-or-nothing —
        never a partial append that leaves the file half-updated).
    path:
        Override the target file (tests pass a ``tmp_path``). ``None`` ->
        :data:`DEFAULT_WATCHLIST_PATH`.
    watchlist_env:
        Injectable override for the ``WATCHLIST`` env var (tests). ``None`` reads
        ``os.environ`` — matching ``main._load_watchlist()`` exactly. When
        non-empty, raises :class:`WatchlistEnvPrecedenceError` (the file would be
        ignored) BEFORE touching the file.
    clock:
        Injectable zero-arg callable returning a ``datetime`` (tests), for a
        deterministic audit comment. ``None`` -> ``datetime.now(timezone.utc)``.

    Returns
    -------
    WatchlistAppendResult
        ``added`` (newly written, in submission order) and ``already_present``
        (skipped as duplicates, case-insensitive) — never a fabricated success.
    """
    target = path if path is not None else DEFAULT_WATCHLIST_PATH

    env_val = watchlist_env if watchlist_env is not None else settings.WATCHLIST
    if env_val and env_val.strip():
        raise WatchlistEnvPrecedenceError(
            "The WATCHLIST environment variable is set, which takes precedence "
            "over watchlist.txt — appending to the file would have no effect. "
            "Clear WATCHLIST (or add the symbol there) to track it."
        )

    normalized: List[str] = []
    for raw in symbols:
        sym = _normalize(raw)
        if not _SYMBOL_RE.match(sym):
            raise InvalidSymbolError(f"{raw!r} is not a valid ticker symbol.")
        normalized.append(sym)

    existing = set(_existing_tickers(target))
    added: List[str] = []
    already_present: List[str] = []
    seen_this_call: set = set()
    for sym in normalized:
        if sym in existing or sym in seen_this_call:
            already_present.append(sym)
            continue
        added.append(sym)
        seen_this_call.add(sym)

    if added:
        now = (clock() if callable(clock) else datetime.now(timezone.utc))
        stamp = now.strftime("%Y-%m-%d")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Append (create if missing), preserving the file's existing
        # ``#``-comment convention with an auditable provenance line.
        needs_leading_newline = target.exists() and target.stat().st_size > 0
        with target.open("a", encoding="utf-8") as fh:
            if needs_leading_newline:
                fh.write("\n")
            fh.write(f"# added via Agentic Trading (watch) on {stamp} UTC\n")
            for sym in added:
                fh.write(f"{sym}\n")

    return WatchlistAppendResult(
        added=added,
        already_present=already_present,
        watchlist_file=str(target),
    )


def remove_symbols(
    symbols: List[str],
    path: Optional[Path] = None,
) -> List[str]:
    """Remove *symbols* from ``watchlist.txt``, preserving every comment and
    blank line. Symbols not present in the file are silently ignored.

    Returns the (normalized, deduped) subset of *symbols* that was actually
    present and removed — never a fabricated success for a no-op write
    (CONSTRAINT #4). A missing file or an empty/all-blank *symbols* list is
    also a no-op and returns ``[]``.
    """
    target = path if path is not None else DEFAULT_WATCHLIST_PATH
    normalized = {_normalize(sym) for sym in symbols if _normalize(sym)}
    if not target.exists() or not normalized:
        return []

    lines = target.read_text(encoding="utf-8").splitlines()
    new_lines = []
    removed: set = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped.upper() in normalized:
            removed.add(stripped.upper())
            continue
        new_lines.append(line)

    if not removed:
        return []

    # Write back, ensure trailing newline
    content = "\n".join(new_lines)
    if content:
        content += "\n"
    target.write_text(content, encoding="utf-8")
    return sorted(removed)


def record_fetch_failures(
    symbols: List[str],
    max_failures: int = 3,
    watchlist_path: Optional[Path] = None,
    failure_file_path: Optional[Path] = None,
    succeeded_symbols: Optional[List[str]] = None,
) -> List[str]:
    """Record a fetch-failure "strike" for each of *symbols* and permanently
    drop any that reach *max_failures* CONSECUTIVE failures from
    ``watchlist.txt`` (the 3-strike rule).

    ``succeeded_symbols`` — tickers that DID return real data in the same
    fetch cycle — reset their strike counter to zero first, in the same
    read-modify-write pass. Without this, a ticker that fails once, then
    succeeds for weeks, then fails again would resume counting from its old
    strike total instead of starting over, silently turning "3 consecutive
    failures" into "3 failures ever" and removing a ticker that is mostly
    healthy. Pass the full set of tickers that were actually attempted this
    cycle (both hits and misses) so counters stay accurate either way.

    Returns the (normalized) list of symbols permanently removed this call.
    Never raises — a corrupt/unreadable ``watchlist_failures.json`` resets to
    an empty counter (logged) rather than blocking the pipeline, matching
    this module's dead-letter-resilience convention elsewhere.
    """
    if not symbols and not succeeded_symbols:
        return []

    target_watchlist = watchlist_path if watchlist_path is not None else DEFAULT_WATCHLIST_PATH
    target_failures = (
        failure_file_path if failure_file_path is not None
        else target_watchlist.parent / "watchlist_failures.json"
    )

    # Load existing failures
    failures: dict = {}
    if target_failures.exists():
        try:
            loaded = json.loads(target_failures.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                failures = loaded
            else:
                logger.warning("watchlist_failures.json is not a JSON object, resetting.")
        except Exception:
            logger.warning("Failed to parse watchlist_failures.json, resetting.")

    changed = False
    for sym in succeeded_symbols or []:
        normalized_sym = _normalize(sym)
        if normalized_sym and failures.pop(normalized_sym, None) is not None:
            changed = True

    dropped_symbols: List[str] = []
    for sym in symbols:
        normalized_sym = _normalize(sym)
        if not normalized_sym:
            continue
        failures[normalized_sym] = failures.get(normalized_sym, 0) + 1
        changed = True

        if failures[normalized_sym] >= max_failures:
            dropped_symbols.append(normalized_sym)
            del failures[normalized_sym]

    if changed:
        target_failures.parent.mkdir(parents=True, exist_ok=True)
        target_failures.write_text(json.dumps(failures, indent=2, sort_keys=True), encoding="utf-8")

    # Actually remove the dropped symbols. Removing from watchlist.txt is
    # silently ineffective while the WATCHLIST env var is set (it takes
    # precedence over the file — see main._load_watchlist()); still perform
    # the removal for consistency, but say so, matching this module's
    # append_symbols honesty convention for the same precedence rule.
    if dropped_symbols:
        removed = remove_symbols(dropped_symbols, path=target_watchlist)
        if settings.WATCHLIST.strip():
            logger.warning(
                "Removed %s from watchlist.txt after %d consecutive fetch "
                "failures, but the WATCHLIST env var is set and takes "
                "precedence — the pipeline will keep evaluating these "
                "symbols until WATCHLIST is updated too.",
                removed, max_failures,
            )

    return dropped_symbols

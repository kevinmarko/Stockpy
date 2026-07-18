"""Append operator-named tickers to ``watchlist.txt`` so the advisory pipeline
starts tracking them.

Backs the Agentic Trading tab's Discovery "Watch" action: an operator taps a
discovered candidate and this appends it to ``watchlist.txt``, the same file
``main._load_watchlist()`` reads when building the evaluation universe. It is
the programmatic equivalent of the ``agentic-discovery`` skill's step-7
"track a candidate" flow (see that skill's docstring) — same file, same
uppercase/dedup/audit-comment conventions — so the two paths never diverge.

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

import logging
import os
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

    env_val = watchlist_env if watchlist_env is not None else os.environ.get("WATCHLIST", "")
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

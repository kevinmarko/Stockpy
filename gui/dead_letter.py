"""
gui/dead_letter.py — Dead-letter queue for symbols that failed during run_pipeline.

Why this module exists
----------------------
``main_orchestrator.run_pipeline`` iterates over the universe and now wraps
each ticker's processing in a per-symbol try/except (Constraint #6).  When a
symbol fails, the exception is captured with its stage label (e.g.
"strategy", "edge_ratio") and appended to a list that is atomically serialised
to ``output/dead_letter.json`` at the end of the run.

This module is the **read side** only.  It is intentionally kept free of any
Streamlit imports so the helpers are headlessly testable in pytest and can be
called from the Launcher tab's dead-letter UI without risk of circular imports.

The write side lives inside ``main_orchestrator.run_pipeline`` to avoid
importing ``gui.*`` from the core pipeline layer.

Public API
----------
:class:`DeadLetterEntry`   — one failed-ticker record (frozen dataclass).
:class:`DeadLetterReport`  — full report for one pipeline run.
:func:`read_dead_letter`   — parse ``output/dead_letter.json`` → report or None.
:data:`DEAD_LETTER_PATH`   — canonical JSON path (``output/dead_letter.json``).

Constraints honoured
--------------------
* CONSTRAINT #4 (no fabricated metrics): corrupt / missing file returns ``None``
  — the GUI renders a "no data yet" hint, never a fabricated success/failure.
* CONSTRAINT #6 (dead-letter): this module IS part of the dead-letter plumbing
  — failures here are logged, never propagated.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolve the output directory without importing the full settings object so
# this module can be imported in test environments without FRED_API_KEY etc.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
DEAD_LETTER_PATH: Path = _REPO_ROOT / "output" / "dead_letter.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeadLetterEntry:
    """One failed-symbol record from a pipeline run.

    Attributes
    ----------
    symbol:
        The ticker that failed (upper-cased, e.g. ``"HKIT"``).
    stage:
        Which processing stage raised — one of ``"dto_construction"``,
        ``"strategy"``, ``"edge_ratio"``, or ``"results"``.
    error:
        ``str(exc)`` — short representation of the exception.
    timestamp:
        ISO-8601 UTC string at which the failure was recorded.
    """

    symbol: str
    stage: str
    error: str
    timestamp: str


@dataclass(frozen=True)
class DeadLetterReport:
    """Full dead-letter snapshot from a single run of ``run_pipeline``.

    Attributes
    ----------
    run_id:
        ISO-8601 UTC timestamp identifying the pipeline run.
    generated_at:
        ISO-8601 UTC timestamp at which this file was written.
    entries:
        Ordered list of failed symbols (chronological — first fail first).
    """

    run_id: str
    generated_at: str
    entries: List[DeadLetterEntry]

    @property
    def symbols(self) -> List[str]:
        """Convenience: list of just the failed ticker strings."""
        return [e.symbol for e in self.entries]

    @property
    def is_clean(self) -> bool:
        """True when the last run had zero dead-lettered symbols."""
        return len(self.entries) == 0


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def read_dead_letter(path: Path = DEAD_LETTER_PATH) -> Optional[DeadLetterReport]:
    """Parse ``output/dead_letter.json`` into a :class:`DeadLetterReport`.

    Returns ``None`` when the file is absent or corrupt — callers must handle
    this as "no run has completed yet" rather than "clean run".

    Parameters
    ----------
    path:
        Override for testing; defaults to :data:`DEAD_LETTER_PATH`.
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data: dict = json.loads(raw)
        entries = [
            DeadLetterEntry(
                symbol=str(e.get("symbol", "")),
                stage=str(e.get("stage", "unknown")),
                error=str(e.get("error", "")),
                timestamp=str(e.get("timestamp", "")),
            )
            for e in data.get("entries", [])
        ]
        return DeadLetterReport(
            run_id=str(data.get("run_id", "")),
            generated_at=str(data.get("generated_at", "")),
            entries=entries,
        )
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
        logger.warning("read_dead_letter: corrupt or unreadable %s: %s", path, exc)
        return None

"""Pure GOOD/BAD classification logic for the symbol-rating subsystem.

ZERO I/O, and deliberately zero imports of ``settings``/``db_config``/any
store -- every threshold this module needs is a plain parameter the caller
supplies (``settings.SYMBOL_RATING_BAD_SCORE_THRESHOLD``,
``settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES``), exactly the same
I/O-free-leaf discipline ``risk/etf_transmission.py`` documents for itself:
keeping this free of the settings/DB import chain is what lets it be
unit-tested in isolation and imported by a caller that only wants the pure
math, without dragging SQLAlchemy or the settings singleton along for the
ride.

This module answers two independent questions:

1. ``classify_tier`` -- is THIS cycle's score bad?
2. ``should_exclude`` -- given a run of consecutive bad cycles, should the
   symbol be dropped from tracking?

Both are pure functions over already-computed inputs; neither touches the
database, and neither decides *what* to do with the answer (that is a
downstream pipeline-wiring task's job, not this package's).
"""

from __future__ import annotations

import math
from typing import Literal

Tier = Literal["GOOD", "BAD"]


def classify_tier(score: float, threshold: float) -> Tier:
    """Classify one cycle's ``final_score`` as ``"GOOD"`` or ``"BAD"``.

    A symbol is ``"BAD"`` this cycle if ``score < threshold``. ``threshold``
    is caller-supplied (``settings.SYMBOL_RATING_BAD_SCORE_THRESHOLD``,
    default 35.0) rather than hardcoded here -- it deliberately matches
    ``strategy_engine.py::evaluate_security()``'s own ``RISK REDUCE`` cutoff
    (``final_score < 35``), the existing single source of truth in this
    codebase for "this score is bad". This module does not re-derive that
    number; it just applies whatever threshold the caller passes, so the two
    can never silently drift apart as long as the caller wires them to the
    same setting.

    Missing/non-finite data (CONSTRAINT #4/#6): a ``NaN``, ``inf``, or
    otherwise non-finite ``score`` classifies as ``"GOOD"``, never
    ``"BAD"``. A data gap (a symbol whose score could not be computed this
    cycle -- a dead-lettered fetch, a mid-cycle exception caught upstream,
    etc.) must never masquerade as evidence of bad performance; conflating
    "we don't know" with "it's bad" would let a transient data outage build
    up a fake consecutive-BAD streak and eventually drop a perfectly healthy
    symbol from tracking for a reason that was never really about its
    rating.
    """
    if not math.isfinite(score):
        return "GOOD"
    return "BAD" if score < threshold else "GOOD"


def should_exclude(consecutive_bad_cycles: int, threshold_cycles: int, is_held: bool) -> bool:
    """Should this symbol be dropped from tracking right now?

    ``True`` only when the symbol is NOT currently held AND its consecutive
    BAD-cycle streak has reached ``threshold_cycles``
    (``settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES``, default 5).

    A held position is NEVER excluded, regardless of ``consecutive_bad_cycles``
    or how far past ``threshold_cycles`` it runs. This is a non-negotiable
    safety invariant, not a tunable -- there is deliberately no flag to
    override it, on the same reasoning ``sizing/position_sizer.py`` and
    ``risk/etf_transmission.py`` apply to their own hard invariants: you need
    live data on something you actually own in order to know when to exit
    it. Excluding a held symbol would blind the platform to a position it is
    still on the hook for, trading a bounded, well-understood risk (keep
    watching a symbol that's rating badly) for an unbounded one (stop
    watching a symbol whose price you no longer see, while you still hold
    it). The ``is_held`` check is therefore evaluated first and short-circuits
    everything else.
    """
    if is_held:
        return False
    return consecutive_bad_cycles >= threshold_cycles

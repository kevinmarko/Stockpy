"""
numeric_utils.py
=================
Shared numeric-coercion helpers used across the reporting, pilots, api, and
data layers (F2, docs/module_efficiency_redundancy_audit.md). Before this
module existed, ``_safe_float`` was reimplemented 7 times across the
codebase with genuinely different NaN/inf semantics -- some filtered NaN
only, one never filtered NaN at all, one did no ``float()`` cast whatsoever.
``safe_float`` below is the single canonical implementation for the 4 copies
confirmed behaviorally compatible; see the two carve-outs below for why the
remaining 3 are NOT migrated here.

Deliberately NOT migrated, report-only per this repo's audit risk posture
(CLAUDE.md's "Risk posture" note / .claude/module_efficiency_audit_remediation_plan.md):
``engine/advisory.py`` (advisory-path trading logic; its copy also currently
leaks NaN through unfiltered) and ``validation/validation_history_store.py``
(the ``validation/`` package).

Deliberately NOT migrated despite being otherwise in scope for F2:
``data/fmp_feeds_market.py``'s own ``_safe_float`` is the one copy that
returns ``float('nan')`` (never ``None``) on a missing/unparseable value.
Investigated during PR 2 and found genuinely risky to change in-place: (a)
``fetch_insider_trade_statistics``'s ``total_disposed == total_disposed and
total_disposed > 0`` NaN-self-comparison idiom depends on exactly that
NaN-not-None behavior -- swapping to ``None`` would make ``None == None``
evaluate ``True`` and crash the following ``total_disposed > 0`` comparison
with a ``TypeError``; (b) conversely, ``fetch_realized_volatility``'s own
exception-path fallback already returns ``{"hv_10": None, "hv_30": None,
"hv_90": None}`` -- an internal inconsistency with its own happy-path
``_safe_float``-derived NaN values -- and at least two downstream consumers
(``pilots/unusual_options_flow.py``, ``pilots/options_alerts.py``) gate on
``hv_30 is not None``, which a NaN value silently passes, letting NaN leak
into the historical-vol-vs-IV comparison undetected. Migrating this copy
would fix (b) but requires fixing the (a) idiom in the same commit to avoid
a regression -- real, disclosed, scoped as a dedicated follow-up rather than
folded into this dedup pass. See that module's own docstring for the
pointer back to this note.
"""
from __future__ import annotations

import math
from typing import Any, Optional


def safe_float(value: Any) -> Optional[float]:
    """Coerce *value* to float, or ``None`` when missing, non-finite (NaN or
    +/-inf), or not coercible to float. Never raises (CONSTRAINT #6). A
    genuine, finite ``0`` correctly round-trips to ``0.0`` -- ``None`` is
    reserved for "not reported"/"not computable" (CONSTRAINT #4), never
    conflated with a real zero.
    """
    if value is None or callable(value):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f

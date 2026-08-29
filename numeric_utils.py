"""
numeric_utils.py
=================
Shared numeric-coercion helpers used across the reporting, pilots, api, and
data layers (F2, docs/module_efficiency_redundancy_audit.md). Before this
module existed, ``_safe_float`` was reimplemented 7 times across the
codebase with genuinely different NaN/inf semantics -- some filtered NaN
only, one never filtered NaN at all, one did no ``float()`` cast whatsoever.
``safe_float`` below is the single canonical implementation for the 5 copies
confirmed behaviorally compatible; see the carve-out below for why the
remaining 2 are NOT migrated here.

Deliberately NOT migrated, report-only per this repo's audit risk posture
(CLAUDE.md's "Risk posture" note / .claude/module_efficiency_audit_remediation_plan.md):
``engine/advisory.py`` (advisory-path trading logic; its copy also currently
leaks NaN through unfiltered) and ``validation/validation_history_store.py``
(the ``validation/`` package).

``data/fmp_feeds_market.py`` -- migrated, with two disclosed companion fixes
in the same commit. This was the one copy that returned ``float('nan')``
(never ``None``) on a missing/unparseable value, and was originally deferred
out of the first PR 2 pass because migrating it in isolation would have been
unsafe: (a) ``fetch_insider_stats``'s ``total_disposed == total_disposed and
total_disposed > 0`` NaN-self-comparison idiom depended on exactly that
NaN-not-None behavior -- swapping to ``None`` would have made ``None ==
None`` evaluate ``True`` and crash the following ``total_disposed > 0``
comparison with a ``TypeError``; fixed to an explicit ``is not None`` check
on BOTH operands of the division (``total_acquired`` can independently be
``None`` too now). (b) ``fetch_realized_volatility``'s own exception-path
fallback already returned ``{"hv_10": None, "hv_30": None, "hv_90": None}``
-- an internal inconsistency with its own happy-path ``_safe_float``-derived
NaN values -- and at least two downstream consumers
(``pilots/unusual_options_flow.py``, ``pilots/options_alerts.py``) gate on
``hv_30 is not None``, which a NaN value silently passed, letting a bad/
missing historical-vol reading leak into the historical-vol-vs-IV comparison
undetected; migrating ``_safe_float`` fixes this directly -- both paths now
agree on ``None``. See ``data/fmp_feeds_market.py``'s own module docstring
for the full detail and ``tests/test_fmp_feeds_market.py`` for the
regression coverage of both fixes.
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

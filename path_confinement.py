"""
path_confinement.py
====================
Shared helper for confirming a resolved filesystem path stays inside a
designated base directory (CodeQL ``py/path-injection`` defense).

Both ``prompt_registry/cache.py`` (``CacheManager._prompt_dir``/
``_record_path``, confining externally-supplied prompt ids/versions to the
disk cache root) and ``ml/forecast_backfill.py`` (confining an internally
built model-artifact filename to ``_MODELS_DIR`` as defense in depth) need
the identical "resolve, then check containment" primitive. Each caller
still owns its own policy for what to do on an escape — one fails loud
(raises), the other degrades gracefully per its module's own contract —
this helper only answers the yes/no containment question so that decision
doesn't have to be hand-rolled (and risk drifting) at every call site.

Deliberately stdlib-only (``pathlib`` alone) so it can be imported from any
layer of the codebase without pulling in FastAPI, settings, or any other
dependency a lighter-weight package (e.g. ``pilots/``) is constrained to
stay off of.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def is_confined(target: Path, base: Path) -> bool:
    """Return whether resolved *target* is inside resolved *base*.

    Both arguments are expected to already be the ``.resolve()``d form —
    this function does not resolve them itself, since callers differ in
    whether they still need the *unresolved* form of ``target`` afterwards.

    Fails closed (returns ``False``, i.e. "not confined") if the
    containment check itself cannot be performed, e.g. ``Path.is_relative_to``
    being unavailable (added in Python 3.9). This is a security-critical
    guard, so an inability to check must never be treated as a pass.
    """
    try:
        return target.is_relative_to(base)
    except AttributeError:
        logger.warning(
            "path_confinement.is_confined: Path.is_relative_to unavailable "
            "(Python < 3.9?); treating %r as NOT confined to %r", target, base,
        )
        return False

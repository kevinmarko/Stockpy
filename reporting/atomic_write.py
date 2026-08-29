"""Shared atomic-write-then-rename JSON helper.

Extracted for F11 (docs/module_efficiency_redundancy_audit.md): before this,
``reporting/pairs_snapshot.py::_atomic_write`` and
``reporting/options_snapshot.py::_atomic_write`` were byte-identical 5-line
copies of the same function, neither importing the other. Both used
``path.with_suffix(".tmp")`` for the temp filename -- not pid/tid-scoped, so
two concurrent writers targeting the SAME path could collide on the same
temp file. ``runtime_flags_writer.py::_atomic_write_json`` already closed
that exact gap for its own writes (a ``.tmp.{pid}.{tid}`` name), so this
helper adopts the same scheme.

Deliberately does NOT adopt every one of ``runtime_flags_writer.py``'s extra
behaviors (``sort_keys=True``, a trailing newline, preserving the
destination's file mode) -- those are specific to that module's own
contract (a hand-editable, diff-friendly settings-override file) and would
be a silent formatting change to ``pairs_snapshot.py``/``options_snapshot.py``'s
existing ``indent=2``, no-sort, no-trailing-newline output for any consumer
diffing those files. This helper matches what the two migrated call sites
already produced, byte-for-byte, other than the temp-file race fix itself.

Scope: this PR migrates only the two byte-identical copies named above. The
other ~8 inline ``os.replace``-based write-then-rename sites F11 also found
(``reporting/progress.py``, ``execution/fix_gateway.py``,
``data/robinhood_session.py``, ``validation/harness.py``, ...) each have
their own module-specific conventions and were not migrated here -- a
future PR can fold more of them in once each is individually reviewed.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping


def atomic_write_json(path: Path, payload: Mapping[str, Any], *, indent: int = 2) -> None:
    """Write ``payload`` to ``path`` as JSON via temp file + ``os.replace``.

    A concurrent reader sees either the old file or the fully-written new
    one, never a half-written one. The temp name carries the pid and thread
    id so two writers -- in this process or another -- can never collide on
    the same temp file, closing the gap the two pre-migration copies both
    had (``path.with_suffix(".tmp")`` is not race-safe: a second writer to
    the same ``path`` uses the identical temp name).

    Raises on any I/O failure -- matches both pre-migration copies' implicit
    contract (neither caught anything); callers that need dead-letter
    resilience wrap this themselves, exactly as they already wrapped the
    inline version.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(payload, indent=indent), encoding="utf-8")
    os.replace(tmp, path)

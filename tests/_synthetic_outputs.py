"""Explicit opt-in for tests that need a MockDataEngine cycle to write its outputs.

Since the synthetic-data gate (``pipeline/synthetic_gate.py``) a cycle on
``MockDataEngine`` data writes no state snapshot, dashboards, queue, pushes,
paper orders or freshness marker. Tests that assert on those files from a Mock
cycle (``tests/test_orchestrator_e2e.py``) opt back in, one by one, with a
comment saying why:

* ``with allow_synthetic_outputs():`` -- for module-scoped fixtures, which a
  marker cannot reach;
* ``@pytest.mark.synthetic_outputs_allowed`` -- for a single test (applied by
  ``conftest.py::_allow_synthetic_outputs_when_marked``).

Both patch ``data_engine.is_synthetic_provider`` to return False, i.e. they
restore the pre-gate behaviour for a Mock *engine* (the empty-fetch fallback
still marks the cycle). There is deliberately no autouse version: that would
recreate the hole inside the suite (``tests/test_synthetic_data_gate_keyless.py``
guards against one).
"""

from __future__ import annotations

import contextlib
from typing import Iterator
from unittest import mock


@contextlib.contextmanager
def allow_synthetic_outputs() -> Iterator[None]:
    """Treat a ``MockDataEngine`` as a real engine for the duration."""
    with mock.patch("data_engine.is_synthetic_provider", return_value=False):
        yield

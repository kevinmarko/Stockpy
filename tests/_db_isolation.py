"""
tests/_db_isolation.py
========================
Shared helper for redirecting SQLite-backed classes to an in-memory
database during a test, without an explicit `db_url` override reaching
the production code paths that construct them with no arguments.

Not a pytest fixture module deliberately -- `redirect_class_to_memory_db`
needs to wrap an arbitrary, possibly-nested call (e.g.
`EvaluationEngine.evaluate_portfolio()`, which constructs its own
`TransactionsStore()` several stack frames deep), not just a single
object's construction. A plain context manager composes into those call
sites more directly than a fixture would. Leading underscore keeps this
out of pytest's test-file collection (it doesn't match `test_*.py`).
"""

from __future__ import annotations

import contextlib
from typing import Callable, Type


def make_memory_db_init(original_init: Callable) -> Callable:
    """Wrap a class's real ``__init__`` in a replacement that always forces
    ``db_url="sqlite:///:memory:"``, ignoring whatever ``db_url`` (if any)
    the caller passed.

    Also strips a ``readonly`` kwarg if present: ``db_config.
    create_readonly_db_engine`` deliberately RAISES for ``sqlite:///:memory:``
    (a read-only in-memory DB is definitionally empty and pointless), so a
    production call site that legitimately passes ``readonly=True`` (e.g.
    ``TransactionsStore(readonly=True)``) would otherwise blow up the moment
    a test redirects it onto ``:memory:``. This helper's entire purpose is
    test-isolation plumbing, not exercising the read-only feature itself
    (see ``tests/test_transactions_store.py::TestReadonlyMode`` /
    ``tests/test_historical_store.py::TestReadonlyMode`` for that, both of
    which correctly use a real ``tmp_path``-backed file DB).

    Returns a plain callable suitable for ``mock.patch.object(cls,
    "__init__", make_memory_db_init(cls.__init__))`` -- useful when a test
    already composes several patches inside one ``with (...)`` tuple and
    just needs the replacement callable, with ``mock.patch.object`` itself
    handling teardown.
    """

    def _mem_init(self, db_url=None, *args, **kwargs):  # noqa: ANN001
        kwargs.pop("readonly", None)
        original_init(self, db_url="sqlite:///:memory:", *args, **kwargs)

    return _mem_init


@contextlib.contextmanager
def redirect_class_to_memory_db(cls: Type):
    """Monkeypatch ``cls.__init__`` so any construction of ``cls`` during
    this block is forced onto an in-memory SQLite DB
    (``db_url="sqlite:///:memory:"``), regardless of what ``db_url`` the
    caller passes (including no override at all).

    For classes like ``TransactionsStore``/``IVHistoryStore`` whose
    production code paths (``EvaluationEngine.evaluate_portfolio()``,
    ``main_orchestrator.run_pipeline()``, etc.) construct them with no
    override, defaulting to the real, git-committed on-disk
    ``quant_platform.db``. Both classes accept a ``db_url`` keyword
    argument in that exact format -- this helper is NOT interchangeable
    with ``HistoricalStore``, which takes a filesystem ``db_path`` instead
    (see ``tests/conftest.py``'s ``disable_historical_store`` fixture, or
    inject a real ``tmp_path``-backed ``HistoricalStore`` directly, for
    that class).
    """
    original_init = cls.__init__
    cls.__init__ = make_memory_db_init(original_init)
    try:
        yield
    finally:
        cls.__init__ = original_init

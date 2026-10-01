"""
tests/test_alpaca_http.py
==========================
Offline unit + local-socket coverage for ``data/alpaca_http.py`` -- the
2026-08 follow-up to the FRED-unbounded-timeout incident (see
``docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md`` and this
module's own docstring) that hardens every ``alpaca-py`` ``RESTClient``
subclass (``TradingClient``, ``StockHistoricalDataClient``) against a
stalled connection blocking forever -- neither exposes a constructor-level
timeout, and no per-call kwarg reaches one either (confirmed against the
installed library source).

No test in this file makes a real network call to Alpaca. The
``TestMountTimeoutAdapterEndToEnd`` class performs real, LOCAL, loopback-only
socket I/O against a ``_BlackHoleServer`` (adapted from
``tests/test_data_engine_macro_history.py``'s ``_BlackHoleServer`` -- the
established pattern in this codebase for proving a timeout bound is real
against actual blocking I/O, rather than mocking the timeout away). Every
other class here mocks/monkeypatches.

------------------------------------------------------------------------
GENUINE BUG FOUND WHILE WRITING THESE TESTS -- reported and then fixed
------------------------------------------------------------------------
The first version of ``_TimeoutHTTPAdapter.send()`` used
``kwargs.setdefault("timeout", self._timeout)``, which did NOT actually
enforce a default timeout for the exact call pattern alpaca-py's own
``RESTClient._one_request()`` uses:

    response = self._session.request(method, url, **opts)

-- confirmed directly against the installed ``alpaca-py`` source
(``alpaca/common/rest.py``), ``opts`` NEVER contains a ``"timeout"`` key.
That should have meant the adapter's ``setdefault`` fills it in. It didn't,
because of a subtlety in ``requests`` itself (confirmed against the
installed ``requests`` source, ``requests/sessions.py``):
``Session.request()``'s own ``timeout: ... = None`` parameter is *always*
explicitly threaded through to ``send_kwargs["timeout"]`` and on into
``adapter.send(request, **kwargs)`` -- so by the time the adapter's
``send()`` ran, ``kwargs["timeout"]`` was already present with value
``None``. ``dict.setdefault`` only fills in a key that is entirely ABSENT;
it never overrides an *existing* key, even one holding ``None``. Net
effect: a caller that never passes its own ``timeout=`` (i.e. every real
call ``alpaca-py`` makes) reached the mounted adapter with an explicit
``timeout=None`` already baked in by ``requests`` itself, and the adapter's
own configured default was silently never applied -- the exact stalled-
connection-blocks-forever failure mode this module exists to close.

Reproduced two independent ways while root-causing this (both now pass
against the fixed adapter): (1) a bare ``requests.Session`` with
``mount_timeout_adapter`` mounted, calling ``session.get(url)`` against a
black-hole server; (2) a REAL ``alpaca.trading.client.TradingClient.get_account()``
call (the exact production call shape ``AlpacaBroker`` uses) pointed at a
black-hole server via an overridden ``_base_url``.

**Fixed** by replacing ``kwargs.setdefault("timeout", self._timeout)`` with
an explicit ``if kwargs.get("timeout") is None: kwargs["timeout"] = self._timeout``
-- treating an incoming ``None`` the same as "not specified" (which is what
it means for every real ``requests``/``alpaca-py`` call), while still
letting a future caller's genuinely non-``None`` explicit timeout win.
``TestTimeoutHTTPAdapterUnit::test_none_value_from_requests_session_is_treated_as_not_specified``
pins this fix at the unit level, and
``TestMountTimeoutAdapterEndToEnd::test_no_per_call_timeout_is_now_bounded_by_the_mounted_default``
proves it against real blocking socket I/O.
"""

from __future__ import annotations

import socket
import threading
import time
from unittest import mock

import pytest
import requests
import requests.adapters

from data.alpaca_http import _TimeoutHTTPAdapter, mount_timeout_adapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prepared_request(url: str = "http://example.invalid/") -> requests.PreparedRequest:
    return requests.Request(method="GET", url=url).prepare()


class _BlackHoleServer:
    """A real TCP server that accepts a connection and then never sends a
    byte -- the one way to prove a bound is genuinely enforced on a blocking
    socket read, rather than mocking the timeout away. Adapted verbatim from
    ``tests/test_data_engine_macro_history.py``'s ``_BlackHoleServer``
    (the FRED-fix precedent this test file follows). Used (not a bare
    ``bind+listen`` with no ``accept()`` at all) so a client's ``connect()``
    succeeds immediately and the hang is isolated to the subsequent read,
    matching what a stalled-but-connected Alpaca server would look like."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        self._sock.settimeout(0.05)
        conns = []
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
                conns.append(conn)  # accepted, held open, never written to
            except socket.timeout:
                continue
        for conn in conns:
            conn.close()

    def close(self) -> None:
        self._stop = True
        self._thread.join(timeout=1.0)
        self._sock.close()


# ---------------------------------------------------------------------------
# Isolated unit coverage for _TimeoutHTTPAdapter.send()'s own literal
# contract -- calling .send() directly, bypassing requests.Session entirely,
# so these tests exercise ONLY the adapter's own
# kwargs.setdefault("timeout", self._timeout) line.
# ---------------------------------------------------------------------------

class TestTimeoutHTTPAdapterUnit:
    def _capture_super_send(self, monkeypatch) -> dict:
        """Monkeypatch the parent HTTPAdapter.send() (what _TimeoutHTTPAdapter
        delegates to via super().send(...)) to record the kwargs it actually
        received, instead of performing a real send."""
        captured: dict = {}

        def _fake_super_send(self_adapter, request, **kwargs):
            captured.update(kwargs)
            return mock.MagicMock()

        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _fake_super_send)
        return captured

    def test_injects_default_timeout_when_caller_omits_it_entirely(self, monkeypatch):
        captured = self._capture_super_send(monkeypatch)
        adapter = _TimeoutHTTPAdapter(timeout=7.5)

        adapter.send(_prepared_request())

        assert captured["timeout"] == 7.5

    def test_does_not_override_an_explicit_per_call_timeout(self, monkeypatch):
        captured = self._capture_super_send(monkeypatch)
        adapter = _TimeoutHTTPAdapter(timeout=99.0)

        adapter.send(_prepared_request(), timeout=3.0)

        assert captured["timeout"] == 3.0

    def test_none_value_from_requests_session_is_treated_as_not_specified(self, monkeypatch):
        """Pins the fix at the unit level: ``requests.Session.request()``
        itself always explicitly passes ``timeout=None`` down to the
        adapter when the caller didn't specify one (see this file's module
        docstring) -- so ``send()`` must treat an incoming ``None`` the same
        as "not specified" and override it with the adapter's own default,
        NOT leave it alone the way a naive ``dict.setdefault`` would (that
        was the genuine bug this test file found and this fix closes)."""
        captured = self._capture_super_send(monkeypatch)
        adapter = _TimeoutHTTPAdapter(timeout=7.5)

        adapter.send(_prepared_request(), timeout=None)

        assert captured["timeout"] == 7.5  # overridden, not left as None


# ---------------------------------------------------------------------------
# mount_timeout_adapter -- mounting behaviour, no real I/O.
# ---------------------------------------------------------------------------

class TestMountTimeoutAdapter:
    def test_mounts_on_both_https_and_http_schemes(self):
        session = requests.Session()
        mount_timeout_adapter(session, 12.5)

        https_adapter = session.get_adapter("https://example.com")
        http_adapter = session.get_adapter("http://example.com")

        assert isinstance(https_adapter, _TimeoutHTTPAdapter)
        assert isinstance(http_adapter, _TimeoutHTTPAdapter)
        assert https_adapter._timeout == 12.5
        assert http_adapter._timeout == 12.5

    def test_same_adapter_instance_mounted_for_both_schemes(self):
        session = requests.Session()
        mount_timeout_adapter(session, 5.0)

        assert session.adapters["https://"] is session.adapters["http://"]

    def test_remounting_replaces_the_previous_adapters_timeout(self):
        """Idempotent per the module's own docstring: mounting twice just
        replaces the adapter for that scheme with an equivalent one."""
        session = requests.Session()
        mount_timeout_adapter(session, 5.0)
        mount_timeout_adapter(session, 42.0)

        assert session.get_adapter("https://example.com")._timeout == 42.0
        assert session.get_adapter("http://example.com")._timeout == 42.0


# ---------------------------------------------------------------------------
# End-to-end, real (loopback-only) socket I/O against a black-hole server.
# ---------------------------------------------------------------------------

class TestMountTimeoutAdapterEndToEnd:
    """Proof, not assumption, against a real blocking socket read -- the
    exact failure mode a stalled Alpaca connection hits."""

    def test_explicit_per_call_timeout_is_honored_not_overridden(self):
        """A caller's own explicit, SHORTER timeout must win over the
        adapter's own, deliberately LONGER, mounted default -- proving the
        adapter's setdefault-based design never clobbers a genuine per-call
        override, exactly as its docstring promises."""
        server = _BlackHoleServer()
        try:
            session = requests.Session()
            mount_timeout_adapter(session, 30.0)  # deliberately long default

            started = time.monotonic()
            with pytest.raises(requests.exceptions.RequestException):
                session.get(f"http://127.0.0.1:{server.port}/", timeout=0.3)
            elapsed = time.monotonic() - started

            assert elapsed < 2.0
        finally:
            server.close()

    def test_no_per_call_timeout_is_now_bounded_by_the_mounted_default(self):
        """Proves the fix: a caller that never passes its own ``timeout=``
        -- i.e. every real call ``alpaca-py``'s ``RESTClient._one_request()``
        makes -- used to block forever (see this file's module docstring for
        the full root-cause writeup and independent reproduction against a
        real ``TradingClient.get_account()`` call). It no longer does:
        ``_TimeoutHTTPAdapter.send()`` now treats the ``timeout=None``
        ``requests.Session.request()`` always threads through as "not
        specified" and applies the mounted default instead.

        This test is intentionally structured with a bounded
        ``thread.join(timeout=...)`` rather than a bare blocking call, so a
        REGRESSION back to the old bug fails this ONE assertion in bounded
        wall-clock time instead of hanging the whole pytest run
        indefinitely.
        """
        server = _BlackHoleServer()
        try:
            session = requests.Session()
            mount_timeout_adapter(session, 0.3)

            result: dict = {}

            def _run() -> None:
                started = time.monotonic()
                try:
                    session.get(f"http://127.0.0.1:{server.port}/")
                    result["outcome"] = "returned"
                except Exception as exc:  # noqa: BLE001 - captured for assertion below
                    result["outcome"] = "raised"
                    result["exc"] = exc
                result["elapsed"] = time.monotonic() - started

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            thread.join(timeout=5.0)

            assert not thread.is_alive(), (
                "mount_timeout_adapter's configured 0.3s default was never "
                "applied -- the request is still blocked after a 5s bounded "
                "wait. This would be a REGRESSION of the fix documented in "
                "this file's module docstring."
            )
            assert result.get("outcome") == "raised"
            assert result["elapsed"] < 2.0
        finally:
            server.close()

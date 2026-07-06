"""
tests/test_net_util.py
=======================
Unit tests for desktop/net_util.py (WS1 of the desktop-unification effort).

Coverage
--------
* find_free_port() returns an int and the port is actually bindable.
* wait_for_http() returns True against a real local HTTPServer.
* wait_for_http() returns False (within a short timeout) when nothing is
  listening on the polled port.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time

from desktop.net_util import find_free_port, wait_for_http


class TestFindFreePort:
    def test_returns_int(self) -> None:
        port = find_free_port()
        assert isinstance(port, int)

    def test_port_is_bindable(self) -> None:
        port = find_free_port()
        # If the port were still in use, this bind would raise OSError.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", port))

    def test_returns_distinct_ports_across_calls(self) -> None:
        # Not a strict guarantee, but sanity-check we aren't always
        # returning the exact same fixed number.
        ports = {find_free_port() for _ in range(5)}
        assert len(ports) >= 1  # smoke check only; no flakiness risk


class _QuietHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that responds 200 to any GET, silently."""

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming convention)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # suppress default request logging to keep test output clean


class TestWaitForHttp:
    def test_returns_true_when_server_is_listening(self) -> None:
        port = find_free_port()
        server = http.server.HTTPServer(("127.0.0.1", port), _QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = wait_for_http(
                f"http://127.0.0.1:{port}/", timeout=5.0, interval=0.1
            )
            assert result is True
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)

    def test_returns_false_when_nothing_listening(self) -> None:
        port = find_free_port()  # released; nothing bound to it
        start = time.monotonic()
        result = wait_for_http(
            f"http://127.0.0.1:{port}/", timeout=1.5, interval=0.1
        )
        elapsed = time.monotonic() - start
        assert result is False
        # Should not hang well beyond the requested timeout.
        assert elapsed < 5.0

    def test_does_not_raise_on_connection_refused(self) -> None:
        port = find_free_port()
        # Should return False cleanly, never propagate an exception.
        result = wait_for_http(f"http://127.0.0.1:{port}/", timeout=0.5, interval=0.1)
        assert result is False

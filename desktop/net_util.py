"""Pure-stdlib helpers for the native desktop shell.

WS1 of the desktop-unification effort: the lowest-level building block used
by the Streamlit subprocess supervisor (WS2) and the pywebview shell (WS4)
to pick a free local TCP port and wait for a local HTTP server to become
ready. Stdlib only (socket, urllib, time) — no third-party dependencies.

The function signatures below are a frozen contract other workstreams are
already writing code against in parallel — do not change them.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request


def find_free_port() -> int:
    """Bind to an OS-assigned ephemeral port, then release it.

    Returns the port number as an int. There is an inherent (small) race
    between releasing the socket here and the caller binding to the same
    port, but this is the standard stdlib-only approach for port discovery.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def wait_for_http(url: str, timeout: float = 15.0, interval: float = 0.25) -> bool:
    """Poll ``url`` until it responds or ``timeout`` seconds have elapsed.

    Any HTTP response (including non-200 status codes) counts as "ready" —
    this is purely a liveness check for "something is listening", not a
    correctness check on the response. Connection-refused, timeouts, and
    other transient network errors are treated as "not ready yet" and
    polling continues; this function never raises for those cases.

    Returns True as soon as a response is received, False if ``timeout``
    is exceeded without one.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            urllib.request.urlopen(url, timeout=interval)
            return True
        except urllib.error.HTTPError:
            # Any HTTP response, even an error status, means something is
            # listening and responding.
            return True
        except (urllib.error.URLError, OSError, ValueError):
            # Connection refused, timed out, DNS failure, etc. — not ready.
            pass

        if time.monotonic() >= deadline:
            return False

        time.sleep(interval)

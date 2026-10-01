"""
data/alpaca_http.py
====================
Shared HTTP-timeout hardening for every ``alpaca-py`` client this codebase
constructs (``execution/alpaca_broker.py::AlpacaBroker``,
``data/market_data.py::AlpacaProvider``).

2026-08 fix (follow-up to the FRED-unbounded-timeout incident, see
``docs/known_issues/data_pipeline_fred_unbounded_timeout_stall.md``): neither
``alpaca.trading.client.TradingClient`` nor
``alpaca.data.historical.stock.StockHistoricalDataClient`` exposes a
constructor-level timeout, and no per-call kwarg reaches it either --
confirmed by reading the installed library source directly
(``alpaca/common/rest.py``): ``RESTClient._one_request()`` builds its
``requests.Session.request(...)`` call with no ``timeout`` key anywhere.
A stalled connection therefore blocked forever -- the exact same bug class
as the pre-fix ``fredapi.Fred.get_series()`` call, except worse in kind: the
calls in ``AlpacaBroker`` run synchronously on the calling coroutine's own
event loop, not even offloaded to a background thread, so a hang there
freezes that cycle's dedicated event loop directly rather than merely a
background thread within it.

Both client classes subclass the same ``alpaca.common.rest.RESTClient``,
which exposes the underlying ``requests.Session`` as ``self._session`` --
the only lever available to bound these calls short of vendoring
``alpaca-py`` itself (its constructor takes no timeout parameter to pass
through). Mounting a custom ``requests.adapters.HTTPAdapter`` on that
session is the standard idiom for adding a default timeout to a library
that doesn't support one natively.
"""

from __future__ import annotations

import requests
import requests.adapters


class _TimeoutHTTPAdapter(requests.adapters.HTTPAdapter):
    """An ``HTTPAdapter`` that injects a default ``timeout`` on every send
    unless the caller already specified one explicitly.

    ``alpaca-py`` never passes ``timeout=`` itself (confirmed against the
    installed source), so this adapter's default should always apply --
    but a naive ``kwargs.setdefault("timeout", self._timeout)`` here is a
    real bug, not a style choice: ``requests.Session.request()`` (which
    ``Session.get``/``.post``/etc. all funnel through) has ``timeout=None``
    as its own default parameter and ALWAYS threads an explicit ``timeout``
    key down to ``Session.send()`` -> this adapter's ``send()`` -- present
    with value ``None`` when the caller never passed one, never simply
    absent. ``dict.setdefault`` only fills in a MISSING key; since the key
    is always present, ``setdefault`` is a silent no-op and the request
    proceeds with no timeout regardless of this adapter's configured
    default -- the exact bug this module exists to close, reproduced by a
    black-hole-server test in ``tests/test_alpaca_http.py``. The fix is to
    treat ``None`` the same as "not specified": override only when the
    incoming value is ``None``, so an explicit non-``None`` per-call
    timeout from a future caller (or a future alpaca-py version) still
    wins.
    """

    def __init__(self, *args, timeout: float, **kwargs) -> None:
        self._timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):  # type: ignore[override]
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self._timeout
        return super().send(request, **kwargs)


def mount_timeout_adapter(session: requests.Session, timeout_seconds: float) -> None:
    """Mount a timeout-enforcing adapter on ``session`` for both schemes.

    Call this immediately after constructing an alpaca-py ``RESTClient``
    subclass instance (``TradingClient``, ``StockHistoricalDataClient``),
    passing its ``self._session`` attribute. Idempotent -- mounting twice
    just replaces the adapter for that scheme with an equivalent one.
    """
    adapter = _TimeoutHTTPAdapter(timeout=timeout_seconds)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

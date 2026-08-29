"""
tests/test_cors_lan_tailscale_contract.py
==========================================
Shared LAN/Tailscale CORS-reflection contract, proved once and applied to
every FastAPI service that mounts ``api.cors.LAN_TAILSCALE_ORIGIN_REGEX``
(additive to each service's own explicit ``CORS_ALLOWED_ORIGINS`` list),
scoped to the Pilots PWA dev server's port (5173, per
webapp/vite.config.ts's ``server: { host: true, port: 5173 }``).

Formerly a byte-for-byte-identical ``TestCORSLanTailscale`` class, hand-
copied into each of the five API test files below -- consolidated here per
this repo's own redundancy audit. Nothing lost: every service keeps its own
individually-reported test case (``test_lan_origin_is_reflected[<service>]``
etc.), this only cuts the duplicated boilerplate source. Each service's own
test file keeps its OWN, non-shared ``TestCORS`` class (the plain explicit-
allowlist allowed/disallowed-origin pair, present only on
``api/control_api.py`` and ``api/state_api.py``) untouched -- that isn't the
byte-identical part.

Every service is hit at its own ``/health`` endpoint, which every one of
these FastAPI apps exposes for exactly this kind of infra-level smoke check.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.control_api as control_api
import api.data_api as data_api
import api.metrics_api as metrics_api
import api.pilots_api as pilots_api
import api.state_api as state_api

_SERVICES = [
    pytest.param(control_api.app, id="control_api"),
    pytest.param(data_api.app, id="data_api"),
    pytest.param(metrics_api.app, id="metrics_api"),
    pytest.param(pilots_api.app, id="pilots_api"),
    pytest.param(state_api.app, id="state_api"),
]


@pytest.fixture(params=_SERVICES)
def client(request):
    return TestClient(request.param, client=("127.0.0.1", 54123))


def test_lan_origin_is_reflected(client):
    resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5173"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://192.168.1.42:5173"


def test_tailscale_range_origin_is_reflected(client):
    resp = client.get("/health", headers={"Origin": "http://100.101.102.5:5173"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://100.101.102.5:5173"


def test_lan_origin_wrong_port_not_reflected(client):
    resp = client.get("/health", headers={"Origin": "http://192.168.1.42:5174"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") != "http://192.168.1.42:5174"


def test_public_ip_not_reflected(client):
    resp = client.get("/health", headers={"Origin": "http://8.8.8.8:5173"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") != "http://8.8.8.8:5173"

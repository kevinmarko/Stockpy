import re
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

import api.pilots_api as pilots_api
from settings import settings

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

FIXTURES = Path(__file__).parent / "fixtures"

def parse_types_ts():
    """Parse webapp/src/api/types.ts and return, per interface, the set of
    field names that are REQUIRED (i.e. NOT marked `field?: type`).

    A TypeScript `field?: type` member is genuinely optional -- the backend
    is not contractually obligated to include it on every response (e.g. an
    honest CONSTRAINT #4 "unavailable" case may omit it entirely rather than
    send a fabricated placeholder). Only fields without a `?` before the `:`
    are part of the response contract this test enforces.
    """
    types_path = Path(__file__).parent.parent / "webapp" / "src" / "api" / "types.ts"
    lines = types_path.read_text(encoding="utf-8").splitlines()
    interfaces = {}
    current_interface = None

    for line in lines:
        if line.startswith("export interface "):
            current_interface = line.split("export interface ")[1].split(" {")[0].strip()
            interfaces[current_interface] = set()
        elif current_interface and line.strip() == "}":
            current_interface = None
        elif current_interface:
            m = re.match(r'^\s*([a-zA-Z0-9_]+)(\?)?\s*:', line)
            if m and m.group(2) is None:
                interfaces[current_interface].add(m.group(1))
    return interfaces

ROUTES = [
    ("EarningsCrushCandidatesResponse", "GET", "/pilots/options/earnings-crush/candidates", {}),
    ("DispersionBasketResponse", "GET", "/pilots/options/dispersion/opportunities", {}),
    ("ZeroDteSignalResponse", "GET", "/pilots/options/zero-dte/signals?symbol=SPY", {}),
    ("GexProfileResponse", "GET", "/pilots/options/gex/profile?symbol=SPY", {}),
    ("MarketMakerSimResponse", "POST", "/pilots/options/market-maker/simulate", {"symbol": "SPY"}),
    ("MultiBrokerStatusResponse", "GET", "/pilots/execution/brokers/status", {}),
    ("ResearchSynthesizeResponse", "POST", "/pilots/ai/research/synthesize", {"prompt": "test"}),
    ("ScenarioMatrixResponse", "POST", "/pilots/paper-broker/scenario-matrix", {}),
    ("UnusualOptionsFlowResponse", "GET", "/pilots/options/flow/unusual", {}),
]

def test_options_desk_response_contracts(monkeypatch):
    interfaces = parse_types_ts()
    
    monkeypatch.setattr(settings, "STATE_API_TOKEN", "valid-tok")
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "valid-tok")
    monkeypatch.setattr(settings, "OUTPUT_DIR", FIXTURES)
    monkeypatch.setattr(pilots_api, "_reports_dir", lambda: str(FIXTURES))
    
    headers = {"Authorization": "Bearer valid-tok"}
    
    all_missing = {}
    for interface_name, method, route, json_body in ROUTES:
        assert interface_name in interfaces, f"Interface '{interface_name}' not found in types.ts!"
        
        expected_fields = interfaces[interface_name]
        
        if method == "GET":
            resp = client.get(route, headers=headers)
        else:
            resp = client.post(route, headers=headers, json=json_body)
            
        assert resp.status_code == 200, f"Endpoint {route} returned {resp.status_code}: {resp.text}"
        
        resp_json = resp.json()
        resp_keys = set(resp_json.keys())
        
        missing_keys = expected_fields - resp_keys
        if missing_keys:
            all_missing[route] = missing_keys

    assert not all_missing, f"Missing keys: {all_missing}"

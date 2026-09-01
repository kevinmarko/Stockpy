import os
import pytest
from unittest import mock
from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api

def extract_blocked_result_fields():
    types_path = os.path.join(os.path.dirname(__file__), "..", "webapp", "src", "api", "types.ts")
    with open(types_path, "r") as f:
        lines = f.readlines()
        
    in_interface = False
    fields = set()
    for line in lines:
        line = line.strip()
        if line.startswith("export interface OptionsDeskGateBlockedResult {"):
            in_interface = True
            continue
        
        if in_interface:
            if line.startswith("}"):
                break
            
            if ":" in line:
                field = line.split(":")[0].replace("?", "").strip()
                fields.add(field)
    
    return fields

def test_options_desk_gate_blocked_contract(monkeypatch):
    monkeypatch.setattr(settings, "FOLLOW_API_TOKEN", "cmd-tok")
    monkeypatch.setattr(settings, "PAPER_BROKER_WRITES_ENABLED", True)
    
    # Needs 127.0.0.1 for loopback check in require_command_token
    client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))
    
    expected_fields = extract_blocked_result_fields()
    
    headers = {"Authorization": "Bearer cmd-tok"}
    
    endpoints_and_payloads = [
        (
            "/pilots/options/earnings-crush/execute",
            {"symbol": "AAPL", "strategy": "Iron Condor", "strike": 150.0, "contracts": 1}
        ),
        (
            "/pilots/options/dispersion/execute",
            {"index_symbol": "QQQ", "dry_run": False, "is_live": False}
        ),
        (
            "/pilots/options/zero-dte/execute",
            {"symbol": "SPY", "option_type": "CALL", "strike": 500.0, "contracts": 1}
        ),
        (
            "/pilots/options/mispricing/execute",
            {"symbol": "AAPL", "candidate": {"mock": "data"}}
        )
    ]
    
    for endpoint, payload in endpoints_and_payloads:
        response = client.post(endpoint, json=payload, headers=headers)
        assert response.status_code == 200, f"{endpoint} failed with {response.status_code}: {response.text}"
        
        data = response.json()
        assert data.get("ok") is False
        assert data.get("blocked") is True
        
        actual_fields = set(data.keys())
        
        for field in actual_fields:
            assert field in expected_fields, f"Field {field} returned by {endpoint} not found in TS interface"
            
        assert "ok" in actual_fields
        assert "blocked" in actual_fields
        assert "message" in actual_fields


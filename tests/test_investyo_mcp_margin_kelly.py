import pytest
import json
import math
from investyo_mcp_server import calculate_margin_kelly_size

def test_calculate_margin_kelly_size_normal():
    # p=0.6, b=2.0, m=0.5, f=0.5, c=0.20
    # kelly_size = 0.5 * min(0.20, max(0.0, 0.6 - (1 - 0.6)/2.0))
    # min(0.2, max(0, 0.6 - 0.4/2)) = min(0.2, 0.4) = 0.2
    # kelly_size = 0.5 * 0.2 = 0.1
    # cash_required = 0.1 * 0.5 = 0.05
    out = calculate_margin_kelly_size(0.6, 2.0, margin_requirement=0.5)
    assert "0.2000" in out
    assert "0.1000" in out
    assert "does NOT imply or perform a live buying-power or margin check" in out
    
    # parse json
    json_str = out.split("```json")[1].split("```")[0].strip()
    data = json.loads(json_str)
    assert data["inputs"]["win_prob"] == 0.6
    assert math.isclose(data["outputs"]["recommended_position_pct"], 0.2)
    assert math.isclose(data["outputs"]["required_margin_cash_pct"], 0.1)

def test_calculate_margin_kelly_size_nan_inputs():
    out = calculate_margin_kelly_size(float('nan'), 2.0)
    assert "N/A" in out
    
    json_str = out.split("```json")[1].split("```")[0].strip()
    data = json.loads(json_str)
    assert data["inputs"]["win_prob"] is None
    assert data["outputs"]["recommended_position_pct"] is None
    assert data["outputs"]["required_margin_cash_pct"] is None

def test_calculate_margin_kelly_size_string_inputs():
    out = calculate_margin_kelly_size("0.6", "2.0", "0.5", "0.5", "0.20")
    assert "0.2000" in out
    assert "0.1000" in out

    json_str = out.split("```json")[1].split("```")[0].strip()
    data = json.loads(json_str)
    assert data["inputs"]["win_prob"] == 0.6
    assert math.isclose(data["outputs"]["recommended_position_pct"], 0.2)
    assert math.isclose(data["outputs"]["required_margin_cash_pct"], 0.1)

def test_calculate_margin_kelly_size_defaults():
    out = calculate_margin_kelly_size(0.6, 2.0)
    json_str = out.split("```json")[1].split("```")[0].strip()
    data = json.loads(json_str)
    
    assert data["inputs"]["margin_requirement"] == 1.0
    assert data["inputs"]["kelly_fraction"] == 0.5
    assert data["inputs"]["cap"] == 0.20

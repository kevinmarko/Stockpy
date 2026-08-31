import pytest
from fastapi.testclient import TestClient
from api.data_api import app
from datetime import datetime, timezone
from unittest.mock import patch
from settings import settings

client = TestClient(app)

def test_get_trends_endpoint():
    with patch("data.trends_store.TrendsStore") as MockStore:
        store_instance = MockStore.return_value
        store_instance.load_raw_windows.return_value = []
        store_instance.get_stitched_series.return_value = [{"date": datetime.now(timezone.utc).date(), "value": 100}]
        
        with patch.object(settings, "STATE_API_TOKEN", "dummy"):
            response = client.get("/data/trends/NVDA", headers={"Authorization": "Bearer dummy"})
        
        if response.status_code != 200:
            print(response.json())
        assert response.status_code == 200
        data = response.json()
        
        assert "raw_curves" in data
        assert "stitched_curve" in data
        assert data["stitched_curve"]["name"] == "Stitched NVDA"
        assert len(data["stitched_curve"]["data"]) == 1

def test_get_trends_endpoint_empty():
    with patch("data.trends_store.TrendsStore") as MockStore:
        store_instance = MockStore.return_value
        store_instance.load_raw_windows.return_value = []
        store_instance.get_stitched_series.return_value = []
        
        with patch.object(settings, "STATE_API_TOKEN", "dummy"):
            response = client.get("/data/trends/UNKNOWN", headers={"Authorization": "Bearer dummy"})
        
        if response.status_code != 200:
            print(response.json())
        assert response.status_code == 200
        data = response.json()
        assert len(data["raw_curves"]) == 0
        assert len(data["stitched_curve"]["data"]) == 0

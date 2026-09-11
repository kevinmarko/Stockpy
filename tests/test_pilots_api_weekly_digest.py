import pytest
from unittest import mock
from fastapi.testclient import TestClient

import api.pilots_api as pilots_api
from pilots.digest_models import DigestPayload, DigestItem

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

def test_get_weekly_digest():
    mock_payload = DigestPayload(
        items=[
            DigestItem(symbol="NVDA", reason="Radar", selection_type="Today's Radar"),
        ]
    )
    with mock.patch("api.pilots_api.weekly_digest.compose_digest", return_value=mock_payload):
        resp = client.get("/pilots/weekly-digest")
        
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["symbol"] == "NVDA"

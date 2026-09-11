import pytest
from unittest import mock
from fastapi.testclient import TestClient

import api.pilots_api as pilots_api
from pilots.digest_models import DigestPayload, DigestItem

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

def test_get_weekly_digest():
    mock_payload = DigestPayload(
        items=[
            DigestItem(
                symbol="NVDA",
                reason="Radar",
                selection_type="Today's Radar",
                confidence_tier="low",
            ),
        ],
        personalization_active=True,
    )
    with mock.patch("api.pilots_api.weekly_digest.compose_digest", return_value=mock_payload):
        resp = client.get("/pilots/weekly-digest")

        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["symbol"] == "NVDA"
        assert data["items"][0]["confidence_tier"] == "low"
        assert data["personalization_active"] is True
        assert data["reason"] is None


def test_get_weekly_digest_empty_surfaces_honest_reason():
    """An empty payload's `reason` (e.g. "no radar candidates this cycle")
    must round-trip through the endpoint verbatim -- the webapp's honest
    empty-state rendering depends on this field actually being present in
    the JSON response, not silently dropped."""
    mock_payload = DigestPayload(
        items=[],
        personalization_active=False,
        reason="No signals computed yet for this cycle.",
    )
    with mock.patch("api.pilots_api.weekly_digest.compose_digest", return_value=mock_payload):
        resp = client.get("/pilots/weekly-digest")

        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["personalization_active"] is False
        assert data["reason"] == "No signals computed yet for this cycle."

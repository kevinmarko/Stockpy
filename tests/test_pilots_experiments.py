import pytest
from fastapi.testclient import TestClient
from unittest import mock

with mock.patch("settings.settings.STATE_API_TOKEN", "dummy"), \
     mock.patch("settings.settings.FOLLOW_API_TOKEN", "dummy"):
    from api.pilots_api import app

client = TestClient(app, base_url="http://127.0.0.1")

@mock.patch("settings.settings.STATE_API_TOKEN", "dummy")
@mock.patch("settings.settings.FOLLOW_API_TOKEN", "dummy")
def test_get_experiments():
    response = client.get("/pilots/experiments", headers={"Authorization": "Bearer dummy"})
    assert response.status_code == 200
    assert "experiments" in response.json()

@mock.patch("settings.settings.STATE_API_TOKEN", "dummy")
@mock.patch("settings.settings.FOLLOW_API_TOKEN", "dummy")
def test_get_experiment_by_id():
    response = client.get("/pilots/experiments/dummy-id", headers={"Authorization": "Bearer dummy"})
    assert response.status_code == 404

@mock.patch("settings.settings.STATE_API_TOKEN", "dummy")
@mock.patch("settings.settings.FOLLOW_API_TOKEN", "dummy")
def test_post_experiment_requires_token():
    # Missing command token should fail
    response = client.post("/pilots/experiments", json={})
    assert response.status_code != 200
    assert response.status_code != 201

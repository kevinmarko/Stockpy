"""The real-vs-mock data switch keys off the FRED key, not credentials.json.

Until 2026-09 both ``AsyncDataFetchStep`` and the daemon picked ``DataEngine``
only when ``credentials.json`` (the Google Sheets service-account file)
existed in the CWD. Step 4 archives the Sheets publisher, and deleting that
file would have silently put the daemon on ``MockDataEngine``'s fabricated
data. These tests pin the new switch: ``data_engine.live_data_configured()``.
"""

from __future__ import annotations

import pytest

import data_engine
from desktop import daemon_runtime
from settings import settings

pytestmark = pytest.mark.live_data_engine  # opt out of conftest's forced-mock default


class _SentinelDataEngine:
    def __init__(self, fred_key):
        self.fred_key = fred_key


def test_live_data_configured_follows_the_fred_key(monkeypatch):
    monkeypatch.setattr(settings, "FRED_API_KEY", "abc123", raising=False)
    assert data_engine.live_data_configured() is True
    monkeypatch.setattr(settings, "FRED_API_KEY", "", raising=False)
    assert data_engine.live_data_configured() is False
    monkeypatch.setattr(settings, "FRED_API_KEY", None, raising=False)
    assert data_engine.live_data_configured() is False


def test_daemon_uses_real_data_without_credentials_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no credentials.json anywhere in the CWD
    monkeypatch.setattr(settings, "FRED_API_KEY", "abc123", raising=False)
    monkeypatch.setattr(daemon_runtime, "DataEngine", _SentinelDataEngine)

    engine = daemon_runtime.OrchestratorDaemon._build_data_engine(None)

    assert isinstance(engine, _SentinelDataEngine)
    assert engine.fred_key == "abc123"


def test_daemon_uses_mock_data_without_a_fred_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "credentials.json").write_text("{}")  # irrelevant now
    monkeypatch.setattr(settings, "FRED_API_KEY", "", raising=False)
    monkeypatch.setattr(daemon_runtime, "DataEngine", _SentinelDataEngine)

    engine = daemon_runtime.OrchestratorDaemon._build_data_engine(None)

    assert isinstance(engine, data_engine.MockDataEngine)

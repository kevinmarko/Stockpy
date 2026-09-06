"""
tests/test_settings_reference.py
================================
Tests for ``GET /settings/reference`` on ``api/pilots_api.py`` — the platform-wide
settings directory & explainer endpoint.

Covers:
- Auth gating (fail-open when STATE_API_TOKEN is unset, 401 when invalid token provided).
- Completeness: exactly 464 fields returned, matching Settings.model_fields.
- Category classification: allowed, secret, or excluded from shared.env_io.
- Secret masking: every secret key has value masked as '•••• (set)' or '(not set)', never plaintext.
- Canonical editable_at routing: matches existing editor routes or None.
- Guardrail: no field in docs/settings_liveness.json classified as 'no_op' is promoted into _TUNABLE_GROUPS.
- Domains: all 14 domains present and valid.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import Settings, settings
from shared import env_io
import api.pilots_api as pilots_api
import pilots.settings_domains as settings_domains

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

_READ_TOKEN = "test-read-token"


class TestSettingsReferenceEndpoint:
    def test_auth_fail_open_when_token_unset(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/reference")
        assert resp.status_code == 200

    def test_auth_401_on_wrong_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", _READ_TOKEN):
            resp = client.get(
                "/settings/reference",
                headers={"Authorization": "Bearer invalid-token"},
            )
        assert resp.status_code == 401

    def test_completeness_and_shape(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/reference")
        assert resp.status_code == 200
        body = resp.json()

        assert body["total"] == len(Settings.model_fields)
        assert len(body["fields"]) == len(Settings.model_fields)
        assert body["domains"] == settings_domains.DOMAINS

        field_keys = [f["key"] for f in body["fields"]]
        assert len(field_keys) == len(set(field_keys)), "Duplicate keys found in response"
        assert set(field_keys) == set(Settings.model_fields.keys())

    def test_secret_masking(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/reference")
        assert resp.status_code == 200
        fields = resp.json()["fields"]

        for f in fields:
            key = f["key"]
            if key in env_io.SECRET_KEYS:
                assert f["category"] == "secret", f"{key} should be category 'secret'"
                assert f["value"] in ("•••• (set)", "(not set)"), (
                    f"Secret key {key} leaked plaintext: {f['value']}"
                )

    def test_editable_at_mapping(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/reference")
        assert resp.status_code == 200
        fields = {f["key"]: f for f in resp.json()["fields"]}

        # Check known keys and their canonical routes
        assert fields["ADVISORY_ONLY"]["editable_at"] in ("/settings/tunables", "/settings/feature-flags")
        assert fields["KELLY_FRACTION"]["editable_at"] == "/settings/tunables"
        assert fields["SENTIMENT_INGESTION_ENABLED"]["editable_at"] == "/settings/sentiment"
        assert fields["ETF_TRANSMISSION_ENABLED"]["editable_at"] in ("/settings/etf-transmission", "/settings/feature-flags")
        assert fields["CACHE_LONG_SHORT_WRITES_ENABLED"]["editable_at"] in ("/settings/cache-long-short", "/settings/feature-flags")
        assert fields["PAPER_BROKER_WRITES_ENABLED"]["editable_at"] in ("/settings/paper-broker", "/settings/feature-flags")
        assert fields["FMP_API_KEY"]["editable_at"] is None  # Secret, not in any editor

    def test_no_no_op_promoted_to_tunables(self):
        """Guardrail: prevent accidental promotion of no_op settings to editable tunables."""
        liveness_file = Path(__file__).parent.parent / "docs" / "settings_liveness.json"
        assert liveness_file.exists(), f"{liveness_file} not found"

        with open(liveness_file, encoding="utf-8") as f:
            data = json.load(f)

        no_ops = set(data.get("no_op", []))
        assert "OPTIONS_EARNINGS_CRUSH_ENABLED" in no_ops

        tunable_keys = set(pilots_api._TUNABLE_INDEX.keys())
        overlap = tunable_keys & no_ops
        assert not overlap, f"Found no_op settings promoted to _TUNABLE_GROUPS: {overlap}"

    def test_descriptions_present_for_all_fields(self):
        """Guardrail: all 464 settings fields have backfilled Field(description=...)."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/reference")
        assert resp.status_code == 200
        fields = resp.json()["fields"]

        for f in fields:
            assert f["description"] is not None and len(f["description"].strip()) > 0, (
                f"Field {f['key']} missing description"
            )

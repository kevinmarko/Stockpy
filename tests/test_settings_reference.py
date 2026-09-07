"""
tests/test_settings_reference.py
================================
Tests for ``GET``/``PUT``/``PATCH /settings/reference`` on ``api/pilots_api.py``
— the platform-wide settings directory & explainer endpoint, and the universal
boolean-flag toggle write path built on top of it.

Covers:
- Auth gating (fail-open when STATE_API_TOKEN is unset, 401 when invalid token provided,
  for GET; the fail-closed command-token + GENERAL_SETTINGS_WRITES_ENABLED gate for PUT).
- Completeness: exactly 464 fields returned, matching Settings.model_fields.
- Category classification: allowed, secret, or excluded from shared.env_io.
- Secret masking: every secret key has value AND default masked, never plaintext.
- Canonical editable_at routing: matches existing editor routes or None.
- `writable`: true iff a field is a genuine, non-secret, non-no_op boolean —
  and matches `key in _REFERENCE_WRITE_INDEX` exactly for every field.
- `_REFERENCE_WRITE_INDEX` is DERIVED (recomputed fresh and compared), not
  hand-listed, and structurally excludes secret/excluded/non-boolean/no_op keys.
- Guardrail: no field in docs/settings_liveness.json classified as 'no_op' is
  promoted into _TUNABLE_GROUPS OR _REFERENCE_WRITE_INDEX.
- The PUT endpoint's dangerous-key confirmation gate (shared with every other
  /settings/* editor via _validate_and_write_payload) is genuinely enforced,
  not bypassable through this new write scope.
- Domains: all 14 domains present and valid; pilots/settings_domains.py's
  _OVERRIDE_DOMAINS carries no stale/fabricated key.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import Settings, settings
from settings_keysets import DANGEROUS_KEYS
from shared import env_io
import api.pilots_api as pilots_api
import pilots.settings_domains as settings_domains
import pilots.settings_meta as settings_meta

client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))

_READ_TOKEN = "test-read-token"
_CMD_TOKEN = "cmd-tok"


@contextlib.contextmanager
def _writes_enabled(token: "str | None" = _CMD_TOKEN, enabled: bool = True):
    """Same two-tier patch every other ``/settings/*`` write test uses (see
    ``tests/test_pilots_api_tunables.py``'s identical helper) — the fail-closed
    command token (bound to ``FOLLOW_API_TOKEN``, see ``api/pilots_api.py``'s
    own import-time comment) AND the dedicated ``GENERAL_SETTINGS_WRITES_ENABLED``
    flag."""
    with mock.patch.object(settings, "FOLLOW_API_TOKEN", token):
        with mock.patch.object(settings, "GENERAL_SETTINGS_WRITES_ENABLED", enabled):
            yield


def _put_reference(
    values: dict,
    confirm: "dict | None" = None,
    token: "str | None" = _CMD_TOKEN,
    enabled: bool = True,
):
    body: dict = {"values": values}
    if confirm is not None:
        body["confirm"] = confirm
    with _writes_enabled(token=token, enabled=enabled):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
            return client.put(
                "/settings/reference",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )


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

        # ADVISORY_ONLY is in BOTH _TUNABLE_GROUPS and (via DANGEROUS_KEYS)
        # _FEATURE_FLAGS_GROUPS -- _build_editable_at_index()'s editor
        # precedence list checks /settings/feature-flags before
        # /settings/tunables, first-match-wins, so the answer is
        # deterministic. Pinned exactly (not a loose `in (...)`) so a future
        # change to that precedence order is caught here rather than only in
        # a webapp mock fixture that could quietly drift from it instead.
        assert fields["ADVISORY_ONLY"]["editable_at"] == "/settings/feature-flags"
        assert fields["KELLY_FRACTION"]["editable_at"] == "/settings/tunables"
        assert fields["SENTIMENT_INGESTION_ENABLED"]["editable_at"] == "/settings/sentiment"
        # Each of these three is ALSO in _FEATURE_FLAGS_GROUPS (via
        # DIAGNOSTIC_FLAG_REASONS / DANGEROUS_KEYS / WRITE_GATE_REASONS
        # respectively), but its own dedicated editor precedes feature-flags
        # in _build_editable_at_index()'s editor list, so it wins.
        assert fields["ETF_TRANSMISSION_ENABLED"]["editable_at"] == "/settings/etf-transmission"
        assert fields["CACHE_LONG_SHORT_WRITES_ENABLED"]["editable_at"] == "/settings/cache-long-short"
        assert fields["PAPER_BROKER_WRITES_ENABLED"]["editable_at"] == "/settings/paper-broker"
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

    def test_secret_default_masked_too(self):
        """A secret field's compile-time `default` must never be echoed in the
        clear, same as its live `value` — even though every real secret's
        literal default happens to be None/empty/non-sensitive today, the
        endpoint must not rely on that being true forever."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/reference")
        assert resp.status_code == 200
        fields = resp.json()["fields"]

        for f in fields:
            if f["key"] in env_io.SECRET_KEYS:
                assert f["default"] in ("•••• (set)", "(not set)"), (
                    f"Secret key {f['key']}'s default leaked plaintext: {f['default']}"
                )

    def test_no_op_field_liveness_reports_no_effect(self):
        """Direct test of THIS endpoint's own liveness output for a known
        no_op field -- the prior guardrail only checked that no_op fields
        aren't promoted into _TUNABLE_GROUPS, never that /settings/reference's
        own `liveness.applies` actually says 'no_effect' for one."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/reference")
        assert resp.status_code == 200
        fields = {f["key"]: f for f in resp.json()["fields"]}
        assert fields["OPTIONS_EARNINGS_CRUSH_ENABLED"]["liveness"]["applies"] == "no_effect"

    def test_writable_matches_reference_write_index_for_every_field(self):
        """`writable` on every field in the GET response must agree exactly
        with membership in `_REFERENCE_WRITE_INDEX` -- the two must never
        drift, since the frontend's Toggle-vs-read-only decision is driven
        entirely by this one flag."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/reference")
        assert resp.status_code == 200
        for f in resp.json()["fields"]:
            expected = f["key"] in pilots_api._REFERENCE_WRITE_INDEX
            assert f["writable"] == expected, (
                f"{f['key']}: writable={f['writable']} but "
                f"key in _REFERENCE_WRITE_INDEX={expected}"
            )


class TestReferenceWriteIndexDerivation:
    """`_REFERENCE_WRITE_INDEX` must be DERIVED from live introspection, never
    hand-listed -- these tests recompute it independently and compare, so a
    future refactor that accidentally hardcodes/freezes the set is caught."""

    def test_matches_a_fresh_recomputation(self):
        liveness = settings_meta.load_liveness()
        no_op_keys = liveness.get("no_op", frozenset())
        expected = {
            key
            for key, fi in Settings.model_fields.items()
            if key in env_io.ALLOWED_KEYS
            and pilots_api._infer_reference_field_type(fi) == "boolean"
            and key not in no_op_keys
        }
        assert set(pilots_api._REFERENCE_WRITE_INDEX.keys()) == expected

    def test_excludes_every_secret_and_excluded_key(self):
        write_keys = set(pilots_api._REFERENCE_WRITE_INDEX.keys())
        assert not (write_keys & set(env_io.SECRET_KEYS))
        assert not (write_keys & set(env_io.EXCLUDED_FROM_GUI))

    def test_excludes_every_non_boolean_field(self):
        for key in pilots_api._REFERENCE_WRITE_INDEX:
            fi = Settings.model_fields[key]
            assert pilots_api._infer_reference_field_type(fi) == "boolean", (
                f"{key} is in _REFERENCE_WRITE_INDEX but is not boolean-typed"
            )

    def test_excludes_every_no_op_key(self):
        no_op_keys = settings_meta.load_liveness().get("no_op", frozenset())
        write_keys = set(pilots_api._REFERENCE_WRITE_INDEX.keys())
        assert not (write_keys & no_op_keys)
        assert "OPTIONS_EARNINGS_CRUSH_ENABLED" not in write_keys

    def test_contains_the_newly_promoted_options_desk_and_circuit_breaker_flags(self):
        """A representative sample of real, actively-read boolean flags this
        fix was specifically built to expose -- confirms the write index isn't
        accidentally empty or scoped too narrowly."""
        for key in (
            "PAPER_OPTIONS_AUTO_EXECUTE_ENABLED",
            "OPTIONS_AUTO_EXIT_ENABLED",
            "OPTIONS_DELTA_HEDGE_ENABLED",
            "OPTIONS_0DTE_ENABLED",
            "CIRCUIT_BREAKER_ENABLED",
            "MULTI_BROKER_GATEWAY_ENABLED",
        ):
            assert key in pilots_api._REFERENCE_WRITE_INDEX, f"{key} missing from _REFERENCE_WRITE_INDEX"


class TestSettingsReferenceWrite:
    """PUT/PATCH /settings/reference -- toggling a boolean field directly."""

    def test_auth_403_without_command_token(self):
        resp = _put_reference({"SECTOR_HEAT_ENABLED": True}, token=None)
        assert resp.status_code in (401, 403)

    def test_auth_403_when_writes_disabled(self):
        resp = _put_reference({"SECTOR_HEAT_ENABLED": True}, enabled=False)
        assert resp.status_code == 403

    def test_ordinary_boolean_write_succeeds(self):
        resp = _put_reference({"SECTOR_HEAT_ENABLED": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["written"] == {"SECTOR_HEAT_ENABLED": True}
        assert not body["rejected"]

    def test_non_boolean_field_rejected_as_unknown_key(self):
        """KELLY_FRACTION is a real, writable-elsewhere field, but it's a
        float, not a boolean -- this endpoint must never accept it, even
        though it's a perfectly valid ALLOWED_KEYS field."""
        resp = _put_reference({"KELLY_FRACTION": 0.75})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"]["KELLY_FRACTION"] == "unknown_key"
        assert not body["written"]

    def test_secret_field_rejected_as_unknown_key(self):
        resp = _put_reference({"FMP_API_KEY": "sk-fake"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"]["FMP_API_KEY"] == "unknown_key"
        assert not body["written"]

    def test_no_op_field_rejected_as_unknown_key(self):
        resp = _put_reference({"OPTIONS_EARNINGS_CRUSH_ENABLED": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"]["OPTIONS_EARNINGS_CRUSH_ENABLED"] == "unknown_key"
        assert not body["written"]

    def test_dangerous_field_requires_confirmation(self):
        """A DANGEROUS_KEYS boolean must not be flippable through this new
        universal-toggle endpoint any more easily than through its existing
        dedicated editor -- the exact regression this fix's own audit brief
        was built to catch."""
        assert "ADVISORY_ONLY" in DANGEROUS_KEYS
        resp = _put_reference({"ADVISORY_ONLY": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"]["ADVISORY_ONLY"] == "confirmation_required"
        assert not body["written"]

    def test_dangerous_field_rejects_wrong_confirmation(self):
        resp = _put_reference(
            {"ADVISORY_ONLY": False}, confirm={"ADVISORY_ONLY": "NOT_THE_RIGHT_NAME"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"]["ADVISORY_ONLY"] == "confirmation_mismatch"
        assert not body["written"]

    def test_dangerous_field_succeeds_with_correct_confirmation(self):
        resp = _put_reference(
            {"ADVISORY_ONLY": False}, confirm={"ADVISORY_ONLY": "ADVISORY_ONLY"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["written"] == {"ADVISORY_ONLY": False}
        assert not body["rejected"]


class TestSettingsDomainsHygiene:
    """pilots/settings_domains.py's _OVERRIDE_DOMAINS must never carry a key
    that doesn't correspond to a real Settings field -- a stale/fabricated
    entry is harmless today (KEY_DOMAIN only ever looks up real keys) but is
    exactly the kind of drift a real field rename should have caught."""

    def test_override_domains_keys_are_all_real_settings_fields(self):
        stale = set(settings_domains._OVERRIDE_DOMAINS.keys()) - set(Settings.model_fields.keys())
        assert not stale, f"Stale/fabricated keys in _OVERRIDE_DOMAINS: {sorted(stale)}"

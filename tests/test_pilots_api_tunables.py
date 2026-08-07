"""
tests/test_pilots_api_tunables.py
=================================
Tests for ``GET/PUT /settings/tunables`` on ``api/pilots_api.py`` — the PWA's
non-secret runtime-tunables editor, backed by ``gui.env_io``'s allowlist-bounded
write layer.

Covers: GET shape/grouping + live-from-settings value/default/description
(including ``kind == "json"`` fields, whose value/default are JSON-stringified,
and the ``default_factory`` fields whose real default is NOT the
``PydanticUndefined`` sentinel); the anti-drift invariant (every served key ∈
``env_io.ALLOWED_KEYS`` and ∉ ``SECRET_KEYS``); editor scope excludes keys owned
by other screens; ``env_drift`` (GET) mirrors Strategy Matrix's shape and
dead-letters per-key on a mangled ``.env``; PUT happy path writes via
``env_io.write_many_atomic``; PUT rejects secret/unknown/out-of-range/
wrong-type/invalid-JSON keys with per-key reasons (never silently dropped); PUT
echoes the written values (the ORIGINAL STRING for JSON fields, not the parsed
object — env_io receives the parsed object instead, so it doesn't double
JSON-encode); PUT is gated on BOTH the fail-closed command token AND the
dedicated ``GENERAL_SETTINGS_WRITES_ENABLED`` flag; and that the token is never
logged (CONSTRAINT #3). ``env_io.write_many_atomic`` is patched so no test ever
touches a real ``.env``.

Several PUT tests below exercise a live-safe field (e.g. ``KELLY_FRACTION``)
through the real endpoint with ``write_many_atomic`` mocked but
``runtime_flags_writer.write_override`` left genuinely live — proving the
field really does apply immediately, not just that the code claims it does.
``_isolated_runtime_flags_store`` (autouse) redirects that real writer's
target file to a throwaway path so those writes never touch this checkout's
own ``output/runtime_flags.json``.
"""

from __future__ import annotations

import ast
import contextlib
import itertools
import json
import pathlib
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from settings import settings
from settings_keysets import DANGEROUS_KEYS
import api.pilots_api as pilots_api
import pilots.settings_meta as settings_meta
import runtime_flags

# Starlette's TestClient defaults request.client.host to the literal
# string "testclient" -- NOT loopback -- which would trip
# api.auth.require_read_token's new fail-closed-when-non-loopback branch
# on every one of this file's existing zero-config-behavior assertions.
# An explicit loopback host here is what these tests have always meant.
client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))


@pytest.fixture(autouse=True)
def _isolated_runtime_flags_store(tmp_path, monkeypatch):
    """Redirect the real runtime-flags store for every test in this file.

    PUT handlers call ``runtime_flags_writer.write_override`` with no
    ``path=`` override, exactly like production — so without this, a test
    that PUTs a live-safe field through the real endpoint writes to this
    checkout's actual ``output/runtime_flags.json`` instead of an isolated
    file. ``INVESTYO_RUNTIME_FLAGS_PATH`` is the one override both the
    writer and the reader (``runtime_flags.load_store``) already respect.
    """
    monkeypatch.setenv(
        runtime_flags.PATH_OVERRIDE_ENV_VAR, str(tmp_path / "runtime_flags.json")
    )

_CMD_TOKEN = "cmd-tok"
_READ_TOKEN = "read-tok"

_EXPECTED_GROUPS = [
    "Financial Constants",
    "Position Sizing",
    "Symbol Rating",
    "Risk Gate",
    "Forecasting",
    "Market Data",
    "Runtime & Ops",
    "Advanced / Config",
    "RLHF Calibration",
]
_VALID_TYPES = {"number", "boolean", "enum", "string"}

_NEW_ADVANCED_KEYS = {
    "SECTOR_FORECAST_CONFIG_PATH",
    "SECTOR_FORECAST_CONFIGS",
    "PROMPT_REGISTRY_ENABLED",
    "PROMPT_REGISTRY_BACKEND",
    "ORCHESTRATOR_DAEMON_ENABLED",
    "PILOTS_API_ENABLED",
    "CORS_ALLOWED_ORIGINS",
}
_JSON_KIND_KEYS = {"SECTOR_FORECAST_CONFIGS", "CORS_ALLOWED_ORIGINS"}

# RLHF Calibration Review Queue operator tunables (rlhf_calibration_store.py) —
# RLHF_CALIBRATION_ENABLED itself is deliberately NOT here (hand-set-only
# master switch, see require_rlhf_calibration_enabled's docstring).
_NEW_RLHF_KEYS = {
    "RLHF_CALIBRATION_AUTO_APPROVE_ENABLED",
    "RLHF_CALIBRATION_CONFIDENCE_THRESHOLD",
    "RLHF_CALIBRATION_AUTO_EXPORT_SFT_ENABLED",
}


@contextlib.contextmanager
def _writes_enabled(token: "str | None" = _CMD_TOKEN, enabled: bool = True):
    """Patch both auth tiers PUT /settings/tunables stacks: the fail-closed
    command token AND the dedicated GENERAL_SETTINGS_WRITES_ENABLED flag."""
    with mock.patch.object(settings, "FOLLOW_API_TOKEN", token):
        with mock.patch.object(settings, "GENERAL_SETTINGS_WRITES_ENABLED", enabled):
            yield


def _confirm_all(values: dict) -> dict:
    """A ``confirm`` map echoing every ``DANGEROUS_KEYS`` name present in
    ``values``.

    Test convenience for the many cases whose subject is NOT the confirmation
    gate. The tests that exercise the gate itself build their maps by hand — the
    whole point of the gate is that each dangerous key must be named
    deliberately, so a helper must never be the only thing proving it works."""
    return {k: k for k in values if k in DANGEROUS_KEYS}


def _put(
    values: dict,
    token: "str | None" = _CMD_TOKEN,
    enabled: bool = True,
    confirm: "dict | None" = None,
):
    body: dict = {"values": values}
    if confirm is not None:
        body["confirm"] = confirm
    with _writes_enabled(token=token, enabled=enabled):
        return client.put(
            "/settings/tunables",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )


def _put_and_get_rejected(values: dict) -> dict:
    with _writes_enabled():
        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
            resp = _put(values, confirm=_confirm_all(values))
    assert resp.status_code == 200
    return resp.json()["rejected"]


def _put_scoped(
    url: str,
    values: dict,
    token: "str | None" = _CMD_TOKEN,
    enabled: bool = True,
    confirm: "dict | None" = None,
):
    """Same shape as ``_put`` but for the dedicated sub-route editors
    (``/settings/sentiment``, ``/settings/sector-selection``), which share
    ``PUT /settings/tunables``'s two-tier auth stack."""
    body: dict = {"values": values}
    if confirm is not None:
        body["confirm"] = confirm
    with _writes_enabled(token=token, enabled=enabled):
        return client.put(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )


# ---------------------------------------------------------------------------
# GET /settings/tunables
# ---------------------------------------------------------------------------


class TestGetTunables:
    def test_shape_and_grouping(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/tunables")
        assert resp.status_code == 200
        body = resp.json()
        # `applies` is now a ROLLUP of the served fields' own states (or
        # "mixed"), not the hardcoded screen-wide string it used to be.
        assert body["applies"] in set(settings_meta.APPLIES_STATES) | {"mixed"}
        groups = body["groups"]
        assert [g["name"] for g in groups] == _EXPECTED_GROUPS
        # Every field carries the base contract keys + a valid type.
        for g in groups:
            assert g["fields"], f"group {g['name']} has no fields"
            for f in g["fields"]:
                assert set(f) >= {"key", "value", "type", "default", "description"}
                assert f["type"] in _VALID_TYPES

    def test_number_fields_carry_min_max_step(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            body = client.get("/settings/tunables").json()
        field = _find_field(body, "KELLY_FRACTION")
        assert field["type"] == "number"
        assert field["min"] == 0.0 and field["max"] == 1.0 and field["step"] == 0.05

    def test_enum_fields_carry_options(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            body = client.get("/settings/tunables").json()
        log_level = _find_field(body, "LOG_LEVEL")
        assert log_level["type"] == "enum"
        assert log_level["options"] == ["DEBUG", "INFO", "WARNING", "ERROR"]

    def test_value_and_default_sourced_from_settings(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            body = client.get("/settings/tunables").json()
        model_fields = type(settings).model_fields
        for key, (kind, _extras) in pilots_api._TUNABLE_INDEX.items():
            field = _find_field(body, key)
            live = getattr(settings, key)
            default = pilots_api._tunable_default(model_fields[key])
            if kind == "json":
                assert json.loads(field["value"]) == live
                assert json.loads(field["default"]) == default
            else:
                assert field["value"] == live
                assert field["default"] == default

    def test_description_from_settings_field_or_null(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            body = client.get("/settings/tunables").json()
        model_fields = type(settings).model_fields
        # DRY_RUN carries a pydantic Field(description=...) — surfaced verbatim.
        dry_run = _find_field(body, "DRY_RUN")
        assert dry_run["description"] == model_fields["DRY_RUN"].description
        assert dry_run["description"]  # non-empty
        # KELLY_FRACTION is a plain assignment (no Field) — null, never fabricated.
        assert _find_field(body, "KELLY_FRACTION")["description"] is None

    def test_fail_open_when_read_token_unset(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/tunables")
        assert resp.status_code == 200

    def test_401_on_wrong_read_token(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", _READ_TOKEN):
            resp = client.get(
                "/settings/tunables",
                headers={"Authorization": "Bearer nope"},
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# "json" kind fields (Advanced / Config: SECTOR_FORECAST_CONFIGS, CORS_ALLOWED_ORIGINS)
# ---------------------------------------------------------------------------


class TestJsonKindFields:
    def test_json_fields_present_with_string_wire_type(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            body = client.get("/settings/tunables").json()
        for key in _JSON_KIND_KEYS:
            field = _find_field(body, key)
            assert field["type"] == "string"  # JSON-in-a-string wire contract
            assert json.loads(field["value"]) == getattr(settings, key)

    def test_default_factory_fields_surface_a_real_default_not_null(self):
        """Regression guard: SECTOR_FORECAST_CONFIGS/CORS_ALLOWED_ORIGINS use
        pydantic ``default_factory=`` rather than ``default=``, so
        ``fi.default`` is the ``PydanticUndefined`` sentinel, not the real
        dict/list default. ``_tunable_default()`` must resolve the factory."""
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            body = client.get("/settings/tunables").json()
        sector_default = _find_field(body, "SECTOR_FORECAST_CONFIGS")["default"]
        assert sector_default is not None
        assert json.loads(sector_default) == {}
        cors_field = _find_field(body, "CORS_ALLOWED_ORIGINS")
        assert cors_field["default"] is not None
        factory = type(settings).model_fields["CORS_ALLOWED_ORIGINS"].default_factory
        assert json.loads(cors_field["default"]) == factory()

    def test_put_json_valid_accepted_written_as_original_string_env_io_gets_native_object(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            # CORS_ALLOWED_ORIGINS is a DANGEROUS_KEYS member, so this write
            # needs its confirmation echo — the subject of THIS test is the
            # JSON round-trip, not the gate (TestDangerousKeyConfirmation).
            resp = _put(
                {"CORS_ALLOWED_ORIGINS": '["https://example.com"]'},
                confirm={"CORS_ALLOWED_ORIGINS": "CORS_ALLOWED_ORIGINS"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"] == {}
        # `written` echoes the ORIGINAL STRING submitted (matches the request).
        assert body["written"] == {"CORS_ALLOWED_ORIGINS": '["https://example.com"]'}
        # env_io gets the PARSED native object -- env_io._JSON_KEYS does its own
        # json.dumps(), so handing it the already-encoded string would double-encode.
        assert w.call_args[0][0] == {"CORS_ALLOWED_ORIGINS": ["https://example.com"]}

    def test_put_json_invalid_json_rejected(self):
        rejected = _put_and_get_rejected({"CORS_ALLOWED_ORIGINS": "{not valid json"})
        assert rejected["CORS_ALLOWED_ORIGINS"] == "invalid_json"

    def test_put_json_non_string_rejected(self):
        rejected = _put_and_get_rejected({"CORS_ALLOWED_ORIGINS": ["already", "a", "list"]})
        assert rejected["CORS_ALLOWED_ORIGINS"] == "expected_string"

    def test_put_json_object_dict_shape_accepted(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"SECTOR_FORECAST_CONFIGS": '{"Technology": {"days": 30, "model": "MC"}}'})
        assert resp.status_code == 200
        assert resp.json()["rejected"] == {}
        assert w.call_args[0][0] == {
            "SECTOR_FORECAST_CONFIGS": {"Technology": {"days": 30, "model": "MC"}}
        }


# ---------------------------------------------------------------------------
# Scope / allowlist invariants
# ---------------------------------------------------------------------------


class TestTunablesScopeInvariants:
    def test_every_served_key_is_allowlisted_non_secret(self):
        """Anti-drift: the editor's served keys and env_io.ALLOWED_KEYS can never
        diverge, and a secret can never sneak into scope (CONSTRAINT #3)."""
        for key in pilots_api._TUNABLE_INDEX:
            assert key in pilots_api.env_io.ALLOWED_KEYS, f"{key} not in ALLOWED_KEYS"
            assert key not in pilots_api.env_io.SECRET_KEYS, f"{key} is a SECRET_KEY"

        expected = {
            "RISK_FREE_RATE", "MARKET_RISK_PREMIUM", "REQUIRED_RETURN_RATE", "MAX_PORTFOLIO_HEAT",
            "KELLY_FRACTION", "KELLY_CAP", "VOL_TARGET", "MAX_LEVERAGE", "MAX_POSITION_WEIGHT",
            "MAX_PORTFOLIO_GROSS", "SIZING_CAP_ESCALATION_ENABLED",
            "SIZING_CAP_ESCALATION_THRESHOLD_CYCLES", "SIZING_CAP_ESCALATION_FACTOR",
            "SIZING_CAP_AUDIT_ENABLED", "SIZING_CAP_ALERT_ENABLED", "SIZING_CAP_ALERT_THRESHOLD_PCT",
            "SYMBOL_RATING_ENABLED", "SYMBOL_RATING_BAD_SCORE_THRESHOLD",
            "SYMBOL_RATING_AUTO_DROP_ENABLED", "SYMBOL_RATING_DROP_THRESHOLD_CYCLES",
            "MAX_CORRELATION", "DAILY_LOSS_LIMIT_PCT", "MAX_ORDER_RATE_PER_MIN",
            "HMM_RISK_OFF_BLOCK_THRESHOLD", "RISK_GATE_ENFORCE_MARKET_HOURS",
            "META_LABEL_MIN_CONFIDENCE", "DRY_RUN",
            "FORECAST_USE_GARCH_SIGMA", "FORECAST_PROPHET_WEIGHT",
            "FORECAST_SKILL_WEIGHTING_ENABLED", "FORECAST_SKILL_WINDOW_DAYS",
            "FORECAST_MODEL_PERSISTENCE_ENABLED", "FORECAST_MODEL_RETRAIN_DAYS",
            "BETA_LOOKBACK_DAYS",
            "MARKET_DATA_PROVIDER", "MARKET_DATA_QUOTE_TTL_SECONDS",
            "MARKET_DATA_BARS_TTL_SECONDS", "FUNDAMENTALS_SOURCE",
            "DASHBOARD_REFRESH_SECONDS", "PROGRESS_POLL_SECONDS", "LOG_LEVEL",
            "ADVISORY_REUSE_PIPELINE_COMPUTE", "ADVISORY_ONLY",
        } | _NEW_ADVANCED_KEYS | _NEW_RLHF_KEYS
        assert set(pilots_api._TUNABLE_INDEX) == expected

    def test_excludes_other_screens_keys(self):
        for key in (
            "SIGNAL_WEIGHTS", "DISABLED_SIGNAL_MODULES", "DEFAULT_TICKERS",
            "LLM_COMMENTARY_ENABLED", "OPAL_RESEARCH_PROVIDER",
            "MACRO_REGIME_GATE_ENABLED", "ALPACA_PAPER",
        ):
            assert key not in pilots_api._TUNABLE_INDEX, f"{key} leaked into tunables scope"

    def test_new_advanced_keys_are_in_scope(self):
        """The 7 keys the real Streamlit tab (gui/panels/settings_manager.py:36-77)
        served that this editor previously omitted."""
        for key in _NEW_ADVANCED_KEYS:
            assert key in pilots_api._TUNABLE_INDEX, f"{key} still missing from tunables scope"
        advanced_group = next(g for g in pilots_api._TUNABLE_GROUPS if g[0] == "Advanced / Config")
        assert {k for k, _kind, _extras in advanced_group[1]} == _NEW_ADVANCED_KEYS


# ---------------------------------------------------------------------------
# Bounds sanity (Fix 2: bounds are NEW guardrails, not ported from settings.py)
# ---------------------------------------------------------------------------


class TestTunableBoundsSanity:
    def test_no_numeric_bound_rejects_its_own_settings_default(self):
        for key, (kind, extras) in pilots_api._TUNABLE_INDEX.items():
            if kind not in ("float", "int"):
                continue
            default = getattr(settings, key)
            lo, hi = extras.get("min"), extras.get("max")
            if lo is not None:
                assert default >= lo, f"{key}: default {default} < min {lo}"
            if hi is not None:
                assert default <= hi, f"{key}: default {default} > max {hi}"

    def test_max_position_weight_bound_permits_a_2x_move_from_default(self):
        """Regression guard: the old max (1.0) sat exactly at the field's own
        default (1.0), so a 2x fat-finger check (2.0) was rejected even though
        it's a legitimate leveraged-position config, not a typo."""
        _kind, extras = pilots_api._TUNABLE_INDEX["MAX_POSITION_WEIGHT"]
        assert extras["max"] >= settings.MAX_POSITION_WEIGHT * 2

    def test_new_advanced_keys_carry_no_invented_numeric_bounds(self):
        """All 7 new keys are bool/text/json -- none numeric, so none should
        carry min/max/step (nothing to guardrail)."""
        for key in _NEW_ADVANCED_KEYS:
            _kind, extras = pilots_api._TUNABLE_INDEX[key]
            assert "min" not in extras and "max" not in extras


# ---------------------------------------------------------------------------
# GET /settings/tunables — env_drift
# ---------------------------------------------------------------------------


class TestTunablesEnvDrift:
    def test_env_drift_present_and_shaped(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            body = client.get("/settings/tunables").json()
        assert set(body["env_drift"]) == {"detected", "keys", "note"}
        assert isinstance(body["env_drift"]["keys"], list)

    def test_env_drift_detected_when_env_disagrees_with_live(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"KELLY_FRACTION={settings.KELLY_FRACTION + 0.1}\n", encoding="utf-8"
        )
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                body = client.get("/settings/tunables").json()
        assert body["env_drift"]["detected"] is True
        assert "KELLY_FRACTION" in body["env_drift"]["keys"]
        assert body["env_drift"]["note"]

    def test_env_drift_false_when_env_matches_live(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(f"KELLY_FRACTION={settings.KELLY_FRACTION}\n", encoding="utf-8")
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                body = client.get("/settings/tunables").json()
        assert "KELLY_FRACTION" not in body["env_drift"]["keys"]

    def test_env_drift_dead_letters_a_malformed_json_key_never_500(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("CORS_ALLOWED_ORIGINS={not valid json\n", encoding="utf-8")
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                resp = client.get("/settings/tunables")
        assert resp.status_code == 200  # never 500 on a hand-mangled .env
        assert "CORS_ALLOWED_ORIGINS" not in resp.json()["env_drift"]["keys"]

    def test_env_drift_parses_env_file_once_not_per_key(self, tmp_path):
        """``_TUNABLE_INDEX`` alone carries dozens of keys -- before the
        ``env_io.read_raw()`` fix, ``_tunables_env_drift`` called
        ``env_io.get_value()`` (and thus re-parsed the whole ``.env``) once per
        key. Pins it at exactly one full-file parse per GET, regardless of how
        many keys the editor serves."""
        env_file = tmp_path / ".env"
        env_file.write_text(f"KELLY_FRACTION={settings.KELLY_FRACTION}\n", encoding="utf-8")
        real_dotenv_values = pilots_api.env_io.dotenv_values
        with mock.patch.object(
            pilots_api.env_io, "dotenv_values", wraps=real_dotenv_values
        ) as spy:
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    resp = client.get("/settings/tunables")
        assert resp.status_code == 200
        assert spy.call_count == 1


# ---------------------------------------------------------------------------
# PUT /settings/tunables — writes
# ---------------------------------------------------------------------------


class TestPutTunables:
    def test_happy_path_writes_via_env_io_and_echoes(self):
        with mock.patch.object(
            pilots_api.env_io, "write_many_atomic",
            return_value=["KELLY_FRACTION", "LOG_LEVEL", "DRY_RUN"],
        ) as w:
            # DRY_RUN is a DANGEROUS_KEYS member and needs its confirmation
            # echo; KELLY_FRACTION/LOG_LEVEL are ordinary and need none.
            resp = _put(
                {"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG", "DRY_RUN": True},
                confirm={"DRY_RUN": "DRY_RUN"},
            )
        assert resp.status_code == 200
        body = resp.json()
        # KELLY_FRACTION is live_safe (applies immediately via the real
        # writer, genuinely invoked here); LOG_LEVEL/DRY_RUN are
        # restart_required — an honest rollup of a mixed batch is "mixed",
        # not a blanket "next_daemon_restart" (see _settings_editor_payload).
        assert body["applies"] == "mixed"
        assert body["rejected"] == {}
        # Echoes the REQUEST/coerced values, not the (stale) settings singleton.
        assert body["written"] == {"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG", "DRY_RUN": True}
        # write_many_atomic called ONCE with the accepted dict.
        assert w.call_count == 1
        assert w.call_args[0][0] == {"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG", "DRY_RUN": True}

    def test_int_field_coerced_to_int(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"BETA_LOOKBACK_DAYS": 300.0})
        assert resp.status_code == 200
        written = w.call_args[0][0]
        assert written == {"BETA_LOOKBACK_DAYS": 300}
        assert isinstance(written["BETA_LOOKBACK_DAYS"], int)

    def test_rejects_secret_key_never_written(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"FRED_API_KEY": "leak"})
        assert resp.status_code == 200
        body = resp.json()
        assert "FRED_API_KEY" in body["rejected"]
        assert body["written"] == {}
        # nothing accepted -> writer never invoked
        assert w.call_count == 0

    def test_forbidden_key_defense_in_depth(self):
        """Even if the layout ever drifted to include a secret, the PUT re-checks
        each key against env_io at write time and refuses it (CONSTRAINT #3)."""
        drifted = dict(pilots_api._TUNABLE_INDEX)
        drifted["FRED_API_KEY"] = ("str", {})
        with mock.patch.object(pilots_api, "_TUNABLE_INDEX", drifted):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                resp = _put({"FRED_API_KEY": "leak"})
        assert resp.status_code == 200
        assert resp.json()["rejected"]["FRED_API_KEY"] == "forbidden_key"
        assert w.call_count == 0

    def test_rejects_unknown_key(self):
        rejected = _put_and_get_rejected({"NOT_A_KEY": 1})
        assert rejected["NOT_A_KEY"] == "unknown_key"

    def test_rejects_out_of_range_but_writes_valid_sibling(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"KELLY_FRACTION": 5.0, "KELLY_CAP": 0.25})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"]["KELLY_FRACTION"] == "out_of_range"
        assert body["written"] == {"KELLY_CAP": 0.25}
        assert w.call_args[0][0] == {"KELLY_CAP": 0.25}

    def test_rejects_wrong_types(self):
        rejected = _put_and_get_rejected(
            {
                "DRY_RUN": "yes",                 # bool field, string value
                "KELLY_FRACTION": "high",         # number field, string value
                "MAX_LEVERAGE": True,             # number field, bool value
                "FORECAST_SKILL_WINDOW_DAYS": 10.5,  # int field, non-integral
                "MARKET_DATA_PROVIDER": "bogus",  # enum field, bad option
            }
        )
        assert rejected["DRY_RUN"] == "expected_boolean"
        assert rejected["KELLY_FRACTION"] == "expected_number"
        assert rejected["MAX_LEVERAGE"] == "expected_number"
        assert rejected["FORECAST_SKILL_WINDOW_DAYS"] == "expected_integer"
        assert rejected["MARKET_DATA_PROVIDER"] == "invalid_option"

    def test_all_rejected_does_not_call_writer(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"NOPE": 1, "ALSO_NOPE": 2})
        assert resp.status_code == 200
        assert resp.json()["written"] == {}
        assert w.call_count == 0

    def test_fail_closed_when_command_token_unset(self):
        resp = _put({"KELLY_FRACTION": 0.6}, token=None)
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self):
        with _writes_enabled():
            resp = client.put(
                "/settings/tunables",
                json={"values": {"KELLY_FRACTION": 0.6}},
                headers={"Authorization": "Bearer wrong"},
            )
        assert resp.status_code == 401

    def test_fails_closed_when_general_settings_writes_disabled(self):
        """Fix 3: PUT is gated on GENERAL_SETTINGS_WRITES_ENABLED in addition to
        the command token, mirroring PUT /strategy/modules's
        STRATEGY_WRITES_ENABLED stacking."""
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"KELLY_FRACTION": 0.6}, enabled=False)
        assert resp.status_code == 403
        assert w.call_count == 0

    def test_write_never_logs_token(self, caplog):
        with caplog.at_level("DEBUG"):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
                _put({"KELLY_FRACTION": 0.6})
        assert _CMD_TOKEN not in caplog.text


# ---------------------------------------------------------------------------
# GENERAL_SETTINGS_WRITES_ENABLED invariants (Fix 3)
# ---------------------------------------------------------------------------


class TestGeneralSettingsWritesEnabledInvariants:
    def test_flag_defaults_to_true_after_phase_1(self):
        from settings import Settings
        assert Settings.model_fields["GENERAL_SETTINGS_WRITES_ENABLED"].default is True

    def test_flag_is_gui_writable(self):
        """2026-08-08 (PR #630 audit): reclassified into ALLOWED_KEYS by
        explicit operator decision, exactly like
        STRATEGY_WRITES_ENABLED/LLM_WRITES_ENABLED -- not secret, so this no
        longer needs to be hand-set-only. Still a
        settings_keysets.DANGEROUS_KEYS member (typed confirmation required
        on write); the endpoint remains independently gated by
        FOLLOW_API_TOKEN regardless."""
        assert "GENERAL_SETTINGS_WRITES_ENABLED" in pilots_api.env_io.ALLOWED_KEYS
        assert "GENERAL_SETTINGS_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS
        assert "GENERAL_SETTINGS_WRITES_ENABLED" not in pilots_api.env_io.EXCLUDED_FROM_GUI


# ---------------------------------------------------------------------------
# GET/PUT /settings/sentiment & GET/PUT /settings/sector-selection
#
# _SENTIMENT_INDEX / _SECTOR_SELECTION_INDEX are dedicated editor scopes,
# structurally identical to _TUNABLE_INDEX (same _build_groups_payload /
# _validate_and_write_payload / _tunables_env_drift machinery, parameterized
# by index). The one invariant unique to these two scopes: every served key
# must be a REAL settings.py Field -- a fabricated key would still round-trip
# through this editor (GET shows it, PUT "accepts" it) while doing genuinely
# nothing on disk, since Settings.model_config has extra="ignore".
# ---------------------------------------------------------------------------

_SETTINGS_SUBROUTES = [
    ("/settings/sentiment", "_SENTIMENT_INDEX"),
    ("/settings/sector-selection", "_SECTOR_SELECTION_INDEX"),
    ("/settings/fmp", "_FMP_INDEX"),
    ("/settings/etf-transmission", "_ETF_TRANSMISSION_INDEX"),
    ("/settings/feature-flags", "_FEATURE_FLAGS_INDEX"),
]


class TestSettingsSubroutesRealFieldInvariant:
    """The regression this class guards against: an earlier draft invented
    plausible-sounding keys (SENTIMENT_LOOKBACK_DAYS, REDDIT_ENABLED,
    GDELT_ENABLED, SECTOR_SELECTION_WEIGHTING_SCHEME, ...) that do not exist
    on the Settings pydantic model at all."""

    def test_every_served_key_is_a_real_settings_field(self):
        model_fields = type(settings).model_fields
        for _url, index_name in _SETTINGS_SUBROUTES:
            index = getattr(pilots_api, index_name)
            assert index, f"{index_name} is empty"
            for key in index:
                assert key in model_fields, f"{index_name}: {key} is not a real settings.py field"

    def test_every_served_key_is_allowlisted_non_secret(self):
        for _url, index_name in _SETTINGS_SUBROUTES:
            index = getattr(pilots_api, index_name)
            for key in index:
                assert key in pilots_api.env_io.ALLOWED_KEYS, f"{index_name}: {key} not in ALLOWED_KEYS"
                assert key not in pilots_api.env_io.SECRET_KEYS, f"{index_name}: {key} is a SECRET_KEY"

    def test_no_numeric_bound_rejects_its_own_settings_default(self):
        """Uses the DECLARED pydantic default (``Field(default=...)``), not the
        live ``settings`` singleton -- a couple of GDELT keys are monkeypatched
        session-wide by ``conftest.py``'s ``_no_gdelt_throttle_in_tests`` autouse
        fixture (real production default 5.0s, patched to 0.0s for test speed),
        which would otherwise make this guard compare against a value no real
        deployment ever sees."""
        model_fields = type(settings).model_fields
        for _url, index_name in _SETTINGS_SUBROUTES:
            index = getattr(pilots_api, index_name)
            for key, (kind, extras) in index.items():
                if kind not in ("float", "int"):
                    continue
                default = pilots_api._tunable_default(model_fields[key])
                lo, hi = extras.get("min"), extras.get("max")
                if lo is not None:
                    assert default >= lo, f"{index_name}: {key} default {default} < min {lo}"
                if hi is not None:
                    assert default <= hi, f"{index_name}: {key} default {default} > max {hi}"

    def test_no_key_leaks_across_editor_scopes(self):
        scopes = {
            "general": set(pilots_api._TUNABLE_INDEX),
            "sentiment": set(pilots_api._SENTIMENT_INDEX),
            "sector": set(pilots_api._SECTOR_SELECTION_INDEX),
            "fmp": set(pilots_api._FMP_INDEX),
            "etf_transmission": set(pilots_api._ETF_TRANSMISSION_INDEX),
        }
        for (name_a, keys_a), (name_b, keys_b) in itertools.combinations(scopes.items(), 2):
            assert not (keys_a & keys_b), f"{name_a} and {name_b} share keys: {keys_a & keys_b}"


class TestSettingsSubroutesGetShape:
    def test_shape_and_grouping(self):
        for url, index_name in _SETTINGS_SUBROUTES:
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
                resp = client.get(url)
            assert resp.status_code == 200
            body = resp.json()
            # The four subroutes genuinely differ (sector-selection is all
            # live_safe; the others mix live_safe and restart_required
            # fields) -- assert self-consistency against the real rollup
            # helper rather than one hardcoded value across all of them.
            states = [
                f["liveness"]["applies"] for g in body["groups"] for f in g["fields"]
            ]
            assert body["applies"] == settings_meta.summarize_applies(states)["applies"]
            index = getattr(pilots_api, index_name)
            served_keys = {f["key"] for g in body["groups"] for f in g["fields"]}
            assert served_keys == set(index), f"{url}: served keys != {index_name}"
            for g in body["groups"]:
                for f in g["fields"]:
                    assert set(f) >= {"key", "value", "type", "default", "description"}
                    assert f["type"] in _VALID_TYPES

    def test_value_and_default_sourced_from_settings(self):
        model_fields = type(settings).model_fields
        for url, index_name in _SETTINGS_SUBROUTES:
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
                body = client.get(url).json()
            for key, (kind, _extras) in getattr(pilots_api, index_name).items():
                field = _find_field(body, key)
                live = getattr(settings, key)
                default = pilots_api._tunable_default(model_fields[key])
                if kind == "json":
                    assert json.loads(field["value"]) == live
                    assert json.loads(field["default"]) == default
                else:
                    assert field["value"] == live
                    assert field["default"] == default

    def test_fail_open_when_read_token_unset(self):
        for url, _index_name in _SETTINGS_SUBROUTES:
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
                resp = client.get(url)
            assert resp.status_code == 200

    def test_401_on_wrong_read_token(self):
        for url, _index_name in _SETTINGS_SUBROUTES:
            with mock.patch.object(settings, "STATE_API_TOKEN", _READ_TOKEN):
                resp = client.get(url, headers={"Authorization": "Bearer nope"})
            assert resp.status_code == 401


class TestSettingsSubroutesEnvDrift:
    """Regression guard: an earlier draft hardcoded ``env_drift`` to
    ``{"detected": False, "keys": [], "note": ""}`` on both GET endpoints
    instead of computing it -- a fabricated "nothing pending" claim
    (CONSTRAINT #4) that would never surface a real pending .env write."""

    def test_env_drift_present_and_shaped(self):
        for url, _index_name in _SETTINGS_SUBROUTES:
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
                body = client.get(url).json()
            assert set(body["env_drift"]) == {"detected", "keys", "note"}
            assert isinstance(body["env_drift"]["keys"], list)

    def test_env_drift_detected_when_env_disagrees_with_live(self, tmp_path):
        cases = [
            ("/settings/sentiment", "SENTIMENT_INGESTION_LOOKBACK_DAYS", int, 1),
            ("/settings/sector-selection", "SECTOR_SELECTION_TOP_N", int, 1),
            ("/settings/fmp", "FMP_COOLDOWN_THRESHOLD", int, 1),
            ("/settings/etf-transmission", "ETF_TRANSMISSION_WINDOW_DAYS", int, 1),
        ]
        for url, key, _cast, delta in cases:
            env_file = tmp_path / f"{key}.env"
            env_file.write_text(f"{key}={getattr(settings, key) + delta}\n", encoding="utf-8")
            with mock.patch.object(settings, "STATE_API_TOKEN", None):
                with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                    body = client.get(url).json()
            assert body["env_drift"]["detected"] is True, url
            assert key in body["env_drift"]["keys"]
            assert body["env_drift"]["note"]

    def test_env_drift_false_when_env_matches_live(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"SENTIMENT_INGESTION_LOOKBACK_DAYS={settings.SENTIMENT_INGESTION_LOOKBACK_DAYS}\n",
            encoding="utf-8",
        )
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            with mock.patch.object(pilots_api.env_io, "ENV_PATH", env_file):
                body = client.get("/settings/sentiment").json()
        assert "SENTIMENT_INGESTION_LOOKBACK_DAYS" not in body["env_drift"]["keys"]


class TestSettingsSubroutesPut:
    """PUT /settings/sentiment, PUT /settings/sector-selection, PUT /settings/fmp, PUT /settings/etf-transmission."""

    def test_happy_path_writes_via_env_io_and_echoes(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put_scoped("/settings/sentiment", {"SENTIMENT_INGESTION_ENABLED": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"] == {}
        assert body["written"] == {"SENTIMENT_INGESTION_ENABLED": True}
        assert w.call_args[0][0] == {"SENTIMENT_INGESTION_ENABLED": True}

        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put_scoped("/settings/sector-selection", {"SECTOR_SELECTION_TOP_N": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"] == {}
        assert body["written"] == {"SECTOR_SELECTION_TOP_N": 5}
        assert w.call_args[0][0] == {"SECTOR_SELECTION_TOP_N": 5}

    def test_rejects_unknown_key(self):
        for url, _index_name in _SETTINGS_SUBROUTES:
            with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                resp = _put_scoped(url, {"NOT_A_KEY": 1})
            assert resp.status_code == 200
            assert resp.json()["rejected"]["NOT_A_KEY"] == "unknown_key"
            assert w.call_count == 0

    def test_rejects_secret_key_never_written(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put_scoped("/settings/sentiment", {"FINNHUB_API_KEY": "leak"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"]["FINNHUB_API_KEY"] == "unknown_key"
        assert body["written"] == {}
        assert w.call_count == 0

    def test_a_key_owned_by_the_other_subroute_is_unknown_here(self):
        """SECTOR_SELECTION_TOP_N belongs to /settings/sector-selection, not
        /settings/sentiment -- confirms the two editors don't silently share
        scope."""
        rejected_body = _put_scoped("/settings/sentiment", {"SECTOR_SELECTION_TOP_N": 5})
        assert rejected_body.json()["rejected"]["SECTOR_SELECTION_TOP_N"] == "unknown_key"

    def test_happy_path_writes_to_env(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put_scoped("/settings/fmp", {"FMP_QUOTES_ENABLED": True})
        assert resp.status_code == 200
        assert resp.json()["written"] == {"FMP_QUOTES_ENABLED": True}
        assert w.call_count == 1
        assert w.call_args[0][0] == {"FMP_QUOTES_ENABLED": True}

        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put_scoped("/settings/etf-transmission", {"ETF_TRANSMISSION_ENABLED": True})
        assert resp.status_code == 200
        assert resp.json()["written"] == {"ETF_TRANSMISSION_ENABLED": True}
        assert w.call_count == 1
        assert w.call_args[0][0] == {"ETF_TRANSMISSION_ENABLED": True}

    def test_rejects_out_of_scope_key(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put_scoped("/settings/fmp", {"ETF_TRANSMISSION_ENABLED": True})
        assert resp.status_code == 200
        assert resp.json()["rejected"]["ETF_TRANSMISSION_ENABLED"] == "unknown_key"
        assert w.call_count == 0

    def test_rejects_out_of_range(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put_scoped("/settings/etf-transmission", {"ETF_TRANSMISSION_MAX_DERATE": 5.0})
        assert resp.status_code == 200
        assert resp.json()["rejected"]["ETF_TRANSMISSION_MAX_DERATE"] == "out_of_range"
        assert w.call_count == 0

    def test_fail_closed_when_command_token_unset(self):
        for url, _index_name in _SETTINGS_SUBROUTES:
            resp = _put_scoped(url, {"FMP_QUOTES_ENABLED": True}, token=None)
            assert resp.status_code == 403

    def test_fails_closed_when_general_settings_writes_disabled(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put_scoped("/settings/fmp", {"FMP_QUOTES_ENABLED": True}, enabled=False)
        assert resp.status_code == 403
        assert w.call_count == 0


# ---------------------------------------------------------------------------
# AST guard still green (no heavy-engine import introduced by this feature)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Per-field liveness metadata (GET) — all five editors
# ---------------------------------------------------------------------------

#: (url, index attribute) for each of the five settings editors.
_EDITORS = [
    ("/settings/tunables", "_TUNABLE_INDEX"),
    ("/settings/sentiment", "_SENTIMENT_INDEX"),
    ("/settings/sector-selection", "_SECTOR_SELECTION_INDEX"),
    ("/settings/fmp", "_FMP_INDEX"),
    ("/settings/etf-transmission", "_ETF_TRANSMISSION_INDEX"),
]

_LIVENESS_KEYS = {
    "applies",
    "restart_reason",
    "capture_sites",
    "env_pinned",
    "dangerous",
    "source",
}


def _all_fields(body: dict) -> list:
    return [f for g in body["groups"] for f in g["fields"]]


def _get(url: str) -> dict:
    with mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get(url)
    assert resp.status_code == 200
    return resp.json()


class TestLivenessMetadataOnGet:
    """Every field on every editor carries honest liveness metadata.

    Parameterised across all five editors deliberately: the metadata is built by
    ONE shared helper, and a regression that wired only some editors to it is
    exactly the failure this suite has to catch."""

    def test_every_field_on_every_editor_carries_liveness(self):
        for url, _index in _EDITORS:
            body = _get(url)
            fields = _all_fields(body)
            assert fields, f"{url} served no fields"
            for f in fields:
                lv = f.get("liveness")
                assert isinstance(lv, dict), f"{url}:{f['key']} has no liveness"
                assert set(lv) == _LIVENESS_KEYS, f"{url}:{f['key']} -> {sorted(lv)}"
                assert lv["applies"] in settings_meta.APPLIES_STATES
                assert lv["source"] in ("runtime_store", "env_file")
                assert isinstance(lv["env_pinned"], bool)
                assert isinstance(lv["dangerous"], bool)

    def test_capture_sites_is_a_list_never_null(self):
        """`[]` is the MEASURED answer for a field with no capture site — the
        classifier looked and found none. It must be distinguishable from the
        artifact being unreadable, so it is never `null` and never omitted."""
        for url, _index in _EDITORS:
            for f in _all_fields(_get(url)):
                sites = f["liveness"]["capture_sites"]
                assert isinstance(sites, list), f"{url}:{f['key']} capture_sites={sites!r}"
                assert all(isinstance(s, str) for s in sites)

    def test_live_safe_field_has_empty_capture_sites_and_no_restart_reason(self):
        """A field the classifier calls `live_safe` must report zero capture
        sites — that is the whole basis of the claim that it can be applied
        live."""
        data = settings_meta.load_liveness()
        checked = 0
        for url, _index in _EDITORS:
            for f in _all_fields(_get(url)):
                if settings_meta.classification(f["key"], data=data) != "live_safe":
                    continue
                checked += 1
                assert f["liveness"]["capture_sites"] == []
        assert checked > 0, "no live_safe field served — fixture assumption broken"

    def test_restart_required_field_names_its_capture_sites(self):
        """The restart claim is checkable, not merely asserted: a field that
        needs one names the `file:line` sites that cause it."""
        data = settings_meta.load_liveness()
        checked = 0
        for url, _index in _EDITORS:
            for f in _all_fields(_get(url)):
                if settings_meta.classification(f["key"], data=data) != "restart_required":
                    continue
                checked += 1
                lv = f["liveness"]
                assert lv["capture_sites"], f"{f['key']} claims a restart with no evidence"
                assert lv["restart_reason"]
                # The prose cites at least one of the real sites.
                assert any(site in lv["restart_reason"] for site in lv["capture_sites"])
        assert checked > 0, "no restart_required field served — fixture assumption broken"

    def test_dangerous_flag_matches_the_real_keyset(self):
        for url, _index in _EDITORS:
            for f in _all_fields(_get(url)):
                assert f["liveness"]["dangerous"] == (f["key"] in DANGEROUS_KEYS)

    def test_the_five_known_dangerous_keys_are_flagged(self):
        """Regression pin on the exact gap this feature closed: these five were
        live-writable through these editors with no confirmation at all."""
        found = {}
        for url, _index in _EDITORS:
            for f in _all_fields(_get(url)):
                if f["liveness"]["dangerous"]:
                    found[f["key"]] = url
        assert set(found) == {
            "ADVISORY_ONLY",
            "DRY_RUN",
            "CORS_ALLOWED_ORIGINS",
            "FMP_BARS_ENABLED",
            "FMP_BARS_ADJUSTMENT",
        }, found

    def test_screen_rollup_matches_its_own_fields(self):
        for url, _index in _EDITORS:
            body = _get(url)
            states = [f["liveness"]["applies"] for f in _all_fields(body)]
            assert body["applies"] == settings_meta.summarize_applies(states)["applies"]
            assert body["applies_counts"] == settings_meta.summarize_applies(states)["applies_counts"]
            assert sum(body["applies_counts"].values()) == len(states)


class TestEnvPinningComputedFresh:
    """Env-pinning is a per-moment fact about the operator's shell, so it must be
    resolved on EVERY request — never cached into a module-level constant and
    never baked into a static artifact."""

    def test_a_newly_pinned_key_is_reported_without_a_restart(self):
        key = "KELLY_FRACTION"
        before = _get("/settings/tunables")
        assert _find_field(before, key)["liveness"]["env_pinned"] is False

        with mock.patch.object(
            settings_meta, "env_pinned_keys", return_value=frozenset({key})
        ):
            during = _get("/settings/tunables")
        f = _find_field(during, key)
        assert f["liveness"]["env_pinned"] is True
        # An env pin OVERRIDES the static classification entirely — it wins over
        # both the runtime store and .env, whatever the classifier says.
        assert f["liveness"]["applies"] == "env_pinned"

        # ...and it is gone again the moment the pin is, with no restart.
        after = _get("/settings/tunables")
        assert _find_field(after, key)["liveness"]["env_pinned"] is False

    def test_pin_does_not_leak_across_editors_or_fields(self):
        with mock.patch.object(
            settings_meta, "env_pinned_keys", return_value=frozenset({"KELLY_FRACTION"})
        ):
            body = _get("/settings/tunables")
        pinned = [f["key"] for f in _all_fields(body) if f["liveness"]["env_pinned"]]
        assert pinned == ["KELLY_FRACTION"]

    def test_source_reports_runtime_store_only_for_an_overridden_key(self):
        key = "KELLY_FRACTION"
        assert _find_field(_get("/settings/tunables"), key)["liveness"]["source"] == "env_file"
        with mock.patch.object(
            settings_meta, "runtime_store_keys", return_value=frozenset({key})
        ):
            body = _get("/settings/tunables")
        assert _find_field(body, key)["liveness"]["source"] == "runtime_store"
        others = [
            f["key"]
            for f in _all_fields(body)
            if f["liveness"]["source"] == "runtime_store"
        ]
        assert others == [key]

    def test_a_pinned_key_never_reports_runtime_store_even_if_also_stored(self):
        """A real shell export always wins over the store (see applies_for's
        own ordering) -- so a key that is BOTH pinned AND has a stale/leftover
        store entry must not claim "source": "runtime_store". That combo would
        contradict this same payload's own "env_pinned": true on the same
        field."""
        key = "KELLY_FRACTION"
        with mock.patch.object(
            settings_meta, "runtime_store_keys", return_value=frozenset({key})
        ):
            with mock.patch.object(
                settings_meta, "env_pinned_keys", return_value=frozenset({key})
            ):
                body = _get("/settings/tunables")
        f = _find_field(body, key)
        assert f["liveness"]["env_pinned"] is True
        assert f["liveness"]["source"] == "env_file"


class TestGetDegradesWhenLivenessArtifactUnreadable:
    """CONSTRAINT #6: an unreadable classification artifact must degrade the
    GET, never 500 it — and must degrade toward "needs a restart", never toward
    a false "applies immediately"."""

    def test_missing_artifact_still_serves_200_and_claims_no_liveness(self):
        settings_meta.reset_cache()
        try:
            with mock.patch.object(settings_meta, "load_liveness", return_value={
                "live_safe": frozenset(),
                "restart_required": {},
                "no_op": frozenset(),
                "loaded": False,
            }):
                body = _get("/settings/tunables")
            fields = _all_fields(body)
            assert fields
            for f in fields:
                lv = f["liveness"]
                # Never "immediately" on the strength of an artifact we could
                # not read.
                assert lv["applies"] in ("next_daemon_restart", "env_pinned")
                assert lv["capture_sites"] == []
        finally:
            settings_meta.reset_cache()


# ---------------------------------------------------------------------------
# Dangerous-key confirmation gate (PUT) — all five editors
# ---------------------------------------------------------------------------


class TestAdvisoryOnlyConfirmationGate:
    """THE test this whole feature exists for.

    ``ADVISORY_ONLY`` is the execution quarantine AGENTS.md §2 calls load-bearing
    safety infrastructure. Before this gate it was one ordinary, unconfirmed
    ``PUT /settings/tunables`` away from ``false``."""

    def test_advisory_only_cannot_be_flipped_without_confirmation(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"ADVISORY_ONLY": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"] == {"ADVISORY_ONLY": "confirmation_required"}
        assert body["written"] == {}
        # The critical assertion: the gate runs BEFORE the write, so nothing
        # reached `.env` at all. A rejection after a partial write would be
        # worse than no gate, because it would read as a refusal while having
        # already disarmed the quarantine.
        assert not w.called

    def test_advisory_only_rejects_a_wrong_confirmation_string(self):
        for bad in ("advisory_only", "ADVISORY_ONLY ", "true", "yes", "", "DRY_RUN"):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                resp = _put({"ADVISORY_ONLY": False}, confirm={"ADVISORY_ONLY": bad})
            body = resp.json()
            assert body["rejected"] == {
                "ADVISORY_ONLY": "confirmation_mismatch"
            }, f"accepted bad confirmation {bad!r}"
            assert body["written"] == {}
            assert not w.called

    def test_confirming_a_different_key_does_not_confirm_advisory_only(self):
        """Echo-the-name exists precisely so one field's confirmation can never
        be spent on another."""
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"ADVISORY_ONLY": False}, confirm={"DRY_RUN": "DRY_RUN"})
        body = resp.json()
        assert body["rejected"] == {"ADVISORY_ONLY": "confirmation_required"}
        assert not w.called

    def test_advisory_only_is_written_with_a_correct_confirmation(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put(
                {"ADVISORY_ONLY": False},
                confirm={"ADVISORY_ONLY": "ADVISORY_ONLY"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"] == {}
        assert body["written"] == {"ADVISORY_ONLY": False}
        assert w.called
        assert w.call_args[0][0] == {"ADVISORY_ONLY": False}

    def test_confirmation_is_not_satisfied_by_a_blanket_flag(self):
        """A truthy/blanket value must not work — only the exact field name."""
        for shape in ({"ADVISORY_ONLY": "true"}, {"confirm_all": "true"}, {"*": "*"}):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                resp = _put({"ADVISORY_ONLY": False}, confirm=shape)
            assert resp.json()["written"] == {}, f"blanket shape {shape!r} let it through"
            assert not w.called


class TestDangerousKeyConfirmation:
    def test_every_dangerous_key_in_every_editor_requires_confirmation(self):
        """Not just ADVISORY_ONLY: the gate covers each editor's own dangerous
        keys, via the shared write helper."""
        probes = {
            "/settings/tunables": {"DRY_RUN": True, "CORS_ALLOWED_ORIGINS": '["https://x.test"]'},
            "/settings/fmp": {"FMP_BARS_ENABLED": True},
        }
        for url, values in probes.items():
            for key, value in values.items():
                with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                    resp = _put_scoped(url, {key: value})
                body = resp.json()
                assert body["rejected"].get(key) == "confirmation_required", (url, key)
                assert not w.called

    def test_ordinary_keys_need_no_confirmation(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"KELLY_FRACTION": 0.6})
        assert resp.json()["written"] == {"KELLY_FRACTION": 0.6}
        assert w.called

    def test_type_error_on_a_dangerous_key_reports_the_type_not_the_gate(self):
        """Ordering matters: validation runs first, so a malformed dangerous
        value gets the actionable message rather than a confirmation complaint
        that would send the operator chasing the wrong problem."""
        rejected = _put_and_get_rejected({"DRY_RUN": "yes"})
        assert rejected["DRY_RUN"] == "expected_boolean"


class TestPartialSuccessWithOneRejectedKey:
    """This repo's write endpoints report per-key outcomes. The gate must not
    turn one unconfirmed dangerous key into a whole-batch failure — otherwise it
    could be worked around by bundling, and it would punish unrelated edits."""

    def test_ordinary_keys_still_write_when_a_dangerous_key_is_unconfirmed(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"ADVISORY_ONLY": False, "KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["written"] == {"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG"}
        assert body["rejected"] == {"ADVISORY_ONLY": "confirmation_required"}
        # The dangerous key is absent from what actually hit `.env`.
        assert w.call_args[0][0] == {"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG"}
        assert "ADVISORY_ONLY" not in w.call_args[0][0]

    def test_one_confirmed_and_one_unconfirmed_dangerous_key(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put(
                {"ADVISORY_ONLY": False, "DRY_RUN": True},
                confirm={"DRY_RUN": "DRY_RUN"},
            )
        body = resp.json()
        assert body["written"] == {"DRY_RUN": True}
        assert body["rejected"] == {"ADVISORY_ONLY": "confirmation_required"}
        assert w.call_args[0][0] == {"DRY_RUN": True}

    def test_a_rejected_key_never_appears_in_per_key_applies(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
            resp = _put({"ADVISORY_ONLY": False, "KELLY_FRACTION": 0.6})
        body = resp.json()
        assert set(body["per_key_applies"]) == {"KELLY_FRACTION"}


class TestPutReportsRealAppliesOutcome:
    def test_per_key_applies_covers_exactly_the_written_keys(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
            resp = _put({"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG"})
        body = resp.json()
        assert set(body["per_key_applies"]) == set(body["written"])
        for state in body["per_key_applies"].values():
            assert state in settings_meta.APPLIES_STATES

    def test_restart_required_is_false_only_when_everything_applied_live(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
            resp = _put({"KELLY_FRACTION": 0.6})
        body = resp.json()
        applied_live = all(
            v == settings_meta.APPLIES_IMMEDIATELY for v in body["per_key_applies"].values()
        )
        assert body["restart_required"] is not applied_live

    def test_a_write_with_nothing_accepted_says_so(self):
        with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
            resp = _put({"NOT_A_REAL_SETTING": 1})
        body = resp.json()
        assert body["written"] == {}
        assert body["per_key_applies"] == {}
        assert body["note"] == "Nothing was written."
        assert not w.called

    def test_no_live_apply_is_ever_claimed_without_a_writer(self):
        """Honesty pin. `runtime_flags_writer` may be absent from a checkout;
        where it is, a `.env` write is all that happened and NO key may be
        reported as having applied immediately."""
        if settings_meta.live_apply_available():
            import pytest

            pytest.skip("runtime_flags_writer is installed in this checkout")
        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
            resp = _put({"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG"})
        body = resp.json()
        assert settings_meta.APPLIES_IMMEDIATELY not in body["per_key_applies"].values()
        assert body["restart_required"] is True

    def test_get_and_put_agree_about_whether_a_field_applies_live(self):
        """The GET's prediction and the PUT's reported outcome must not
        contradict each other — a screen that promises "applies now" and then a
        save that says "needs a restart" is the same class of false claim this
        feature removes, just relocated."""
        body_get = _get("/settings/tunables")
        predicted = _find_field(body_get, "KELLY_FRACTION")["liveness"]["applies"]
        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
            resp = _put({"KELLY_FRACTION": 0.6})
        actual = resp.json()["per_key_applies"]["KELLY_FRACTION"]
        assert actual == predicted


def test_pilots_api_still_off_heavy_engines():
    src = pathlib.Path(pilots_api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {
        "processing_engine", "strategy_engine", "forecasting_engine",
        "macro_engine", "technical_options_engine", "main_orchestrator", "desktop",
    }
    assert not (imported & forbidden)


def _find_field(body: dict, key: str) -> dict:
    for g in body["groups"]:
        for f in g["fields"]:
            if f["key"] == key:
                return f
    raise AssertionError(f"field {key} not found in GET payload")

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
"""

from __future__ import annotations

import ast
import contextlib
import json
import pathlib
from unittest import mock

from fastapi.testclient import TestClient

from settings import settings
import api.pilots_api as pilots_api

client = TestClient(pilots_api.app)

_CMD_TOKEN = "cmd-tok"
_READ_TOKEN = "read-tok"

_EXPECTED_GROUPS = [
    "Financial Constants",
    "Position Sizing",
    "Risk Gate",
    "Forecasting",
    "Market Data",
    "Runtime & Ops",
    "Advanced / Config",
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


@contextlib.contextmanager
def _writes_enabled(token: "str | None" = _CMD_TOKEN, enabled: bool = True):
    """Patch both auth tiers PUT /settings/tunables stacks: the fail-closed
    command token AND the dedicated GENERAL_SETTINGS_WRITES_ENABLED flag."""
    with mock.patch.object(settings, "FOLLOW_API_TOKEN", token):
        with mock.patch.object(settings, "GENERAL_SETTINGS_WRITES_ENABLED", enabled):
            yield


def _put(values: dict, token: "str | None" = _CMD_TOKEN, enabled: bool = True):
    with _writes_enabled(token=token, enabled=enabled):
        return client.put(
            "/settings/tunables",
            json={"values": values},
            headers={"Authorization": f"Bearer {token}"},
        )


def _put_and_get_rejected(values: dict) -> dict:
    with _writes_enabled():
        with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
            resp = _put(values)
    assert resp.status_code == 200
    return resp.json()["rejected"]


# ---------------------------------------------------------------------------
# GET /settings/tunables
# ---------------------------------------------------------------------------


class TestGetTunables:
    def test_shape_and_grouping(self):
        with mock.patch.object(settings, "STATE_API_TOKEN", None):
            resp = client.get("/settings/tunables")
        assert resp.status_code == 200
        body = resp.json()
        assert body["applies"] == "next_daemon_restart"
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
            resp = _put({"CORS_ALLOWED_ORIGINS": '["https://example.com"]'})
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

    def test_serves_exactly_the_briefed_key_set(self):
        expected = {
            "RISK_FREE_RATE", "MARKET_RISK_PREMIUM", "REQUIRED_RETURN_RATE", "MAX_PORTFOLIO_HEAT",
            "KELLY_FRACTION", "KELLY_CAP", "VOL_TARGET", "MAX_LEVERAGE", "MAX_POSITION_WEIGHT",
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
        } | _NEW_ADVANCED_KEYS
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


# ---------------------------------------------------------------------------
# PUT /settings/tunables — writes
# ---------------------------------------------------------------------------


class TestPutTunables:
    def test_happy_path_writes_via_env_io_and_echoes(self):
        with mock.patch.object(
            pilots_api.env_io, "write_many_atomic",
            return_value=["KELLY_FRACTION", "LOG_LEVEL", "DRY_RUN"],
        ) as w:
            resp = _put({"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG", "DRY_RUN": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["applies"] == "next_daemon_restart"
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
    def test_flag_defaults_to_false(self):
        from settings import Settings
        assert Settings.model_fields["GENERAL_SETTINGS_WRITES_ENABLED"].default is False

    def test_flag_is_not_gui_writable(self):
        """Mirrors test_strategy_writes_enabled_is_not_gui_writable: a GUI bug
        must never flip this on. Neither allowlisted nor secret — hand-set only,
        exactly like STRATEGY_WRITES_ENABLED/LLM_WRITES_ENABLED/
        AGENTIC_DISCOVERY_ENABLED."""
        assert "GENERAL_SETTINGS_WRITES_ENABLED" not in pilots_api.env_io.ALLOWED_KEYS
        assert "GENERAL_SETTINGS_WRITES_ENABLED" not in pilots_api.env_io.SECRET_KEYS


# ---------------------------------------------------------------------------
# AST guard still green (no heavy-engine import introduced by this feature)
# ---------------------------------------------------------------------------


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

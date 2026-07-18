"""
tests/test_pilots_api_tunables.py
=================================
Tests for ``GET/PUT /settings/tunables`` on ``api/pilots_api.py`` — the PWA's
non-secret runtime-tunables editor, backed by ``gui.env_io``'s allowlist-bounded
write layer.

Covers: GET shape/grouping + live-from-settings value/default/description; the
anti-drift invariant (every served key ∈ ``env_io.ALLOWED_KEYS`` and ∉
``SECRET_KEYS``); editor scope excludes keys owned by other screens; PUT happy
path writes via ``env_io.write_many_atomic``; PUT rejects secret/unknown/
out-of-range/wrong-type keys with per-key reasons (never silently dropped); PUT
echoes the written values; fail-closed command auth; and that the token is never
logged (CONSTRAINT #3). ``env_io.write_many_atomic`` is patched so no test ever
touches a real ``.env``.
"""

from __future__ import annotations

import ast
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
]
_VALID_TYPES = {"number", "boolean", "enum", "string"}


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
        for key in pilots_api._TUNABLE_INDEX:
            field = _find_field(body, key)
            assert field["value"] == getattr(settings, key)
            assert field["default"] == model_fields[key].default

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
        }
        assert set(pilots_api._TUNABLE_INDEX) == expected

    def test_excludes_other_screens_keys(self):
        for key in (
            "SIGNAL_WEIGHTS", "DISABLED_SIGNAL_MODULES", "DEFAULT_TICKERS",
            "LLM_COMMENTARY_ENABLED", "OPAL_RESEARCH_PROVIDER", "PROMPT_REGISTRY_ENABLED",
            "MACRO_REGIME_GATE_ENABLED", "ALPACA_PAPER",
        ):
            assert key not in pilots_api._TUNABLE_INDEX, f"{key} leaked into tunables scope"


# ---------------------------------------------------------------------------
# PUT /settings/tunables — writes
# ---------------------------------------------------------------------------


class TestPutTunables:
    def test_happy_path_writes_via_env_io_and_echoes(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(
                pilots_api.env_io, "write_many_atomic",
                return_value=["KELLY_FRACTION", "LOG_LEVEL", "DRY_RUN"],
            ) as w:
                resp = client.put(
                    "/settings/tunables",
                    json={"values": {"KELLY_FRACTION": 0.6, "LOG_LEVEL": "DEBUG", "DRY_RUN": True}},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
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
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                resp = client.put(
                    "/settings/tunables",
                    json={"values": {"BETA_LOOKBACK_DAYS": 300.0}},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        written = w.call_args[0][0]
        assert written == {"BETA_LOOKBACK_DAYS": 300}
        assert isinstance(written["BETA_LOOKBACK_DAYS"], int)

    def test_rejects_secret_key_never_written(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                resp = client.put(
                    "/settings/tunables",
                    json={"values": {"FRED_API_KEY": "leak"}},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
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
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                    resp = client.put(
                        "/settings/tunables",
                        json={"values": {"FRED_API_KEY": "leak"}},
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert resp.status_code == 200
        assert resp.json()["rejected"]["FRED_API_KEY"] == "forbidden_key"
        assert w.call_count == 0

    def test_rejects_unknown_key(self):
        rejected = self._put_and_get_rejected({"NOT_A_KEY": 1})
        assert rejected["NOT_A_KEY"] == "unknown_key"

    def test_rejects_out_of_range_but_writes_valid_sibling(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                resp = client.put(
                    "/settings/tunables",
                    json={"values": {"KELLY_FRACTION": 5.0, "KELLY_CAP": 0.25}},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected"]["KELLY_FRACTION"] == "out_of_range"
        assert body["written"] == {"KELLY_CAP": 0.25}
        assert w.call_args[0][0] == {"KELLY_CAP": 0.25}

    def test_rejects_wrong_types(self):
        rejected = self._put_and_get_rejected(
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
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic") as w:
                resp = client.put(
                    "/settings/tunables",
                    json={"values": {"NOPE": 1, "ALSO_NOPE": 2}},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        assert resp.json()["written"] == {}
        assert w.call_count == 0

    def test_fail_closed_when_command_token_unset(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", None):
            resp = client.put(
                "/settings/tunables",
                json={"values": {"KELLY_FRACTION": 0.6}},
                headers={"Authorization": "Bearer anything"},
            )
        assert resp.status_code == 403

    def test_401_on_wrong_command_token(self):
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            resp = client.put(
                "/settings/tunables",
                json={"values": {"KELLY_FRACTION": 0.6}},
                headers={"Authorization": "Bearer wrong"},
            )
        assert resp.status_code == 401

    def test_write_never_logs_token(self, caplog):
        with caplog.at_level("DEBUG"):
            with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
                with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
                    client.put(
                        "/settings/tunables",
                        json={"values": {"KELLY_FRACTION": 0.6}},
                        headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                    )
        assert _CMD_TOKEN not in caplog.text

    def _put_and_get_rejected(self, values: dict) -> dict:
        with mock.patch.object(settings, "FOLLOW_API_TOKEN", _CMD_TOKEN):
            with mock.patch.object(pilots_api.env_io, "write_many_atomic"):
                resp = client.put(
                    "/settings/tunables",
                    json={"values": values},
                    headers={"Authorization": f"Bearer {_CMD_TOKEN}"},
                )
        assert resp.status_code == 200
        return resp.json()["rejected"]


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

"""
tests/test_ai_control_center.py
===============================
Headless tests for the AI Control Center surface (no Streamlit).

Coverage
--------
* ``CAPABILITIES`` registry completeness.
* ``capability_status`` four-state truth table (ready / disabled / missing_key
  / not_built).
* ``control_center_overview`` row shape.
* ``validate_toggle_write`` rejects secret keys (CONSTRAINT #3) and honours the
  ``ALLOWED_KEYS`` allowlist.
* ``orchestrator_runner.launch_scheduled_advisory`` spawns a subprocess (mocked
  — no real child) and returns a scheduled ``RunHandle``; ``stop_run`` never
  raises.
* Opal is gated ``not_built`` while ``llm.research`` is absent.
* Tab-wiring source grep (gui/app.py).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gui.ai_control_center import (
    CAPABILITIES,
    AICapability,
    capability_status,
    control_center_overview,
    opal_built,
    status_badge,
    validate_toggle_write,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# CAPABILITIES registry
# ---------------------------------------------------------------------------
class TestCapabilitiesRegistry:
    def test_covers_five_options(self) -> None:
        keys = {c.key for c in CAPABILITIES}
        assert {
            "claude_commentary",
            "gemini_alerts",
            "gemini_vision",
            "gravity_ai_runner",
            "opal_research",
        }.issubset(keys)

    def test_all_entries_are_capabilities(self) -> None:
        assert all(isinstance(c, AICapability) for c in CAPABILITIES)

    def test_every_capability_has_help_and_trigger(self) -> None:
        for c in CAPABILITIES:
            assert c.help.strip()
            assert c.trigger in {"on_demand", "scheduled"}

    def test_toggle_keys_are_non_secret(self) -> None:
        from gui.env_io import SECRET_KEYS

        for c in CAPABILITIES:
            if c.toggle_key is not None:
                assert c.toggle_key not in SECRET_KEYS

    def test_provider_keys_are_secret(self) -> None:
        from gui.env_io import SECRET_KEYS

        for c in CAPABILITIES:
            for k in c.provider_key_settings:
                assert k in SECRET_KEYS


# ---------------------------------------------------------------------------
# capability_status truth table
# ---------------------------------------------------------------------------
class TestCapabilityStatus:
    def _claude(self) -> AICapability:
        return next(c for c in CAPABILITIES if c.key == "claude_commentary")

    def _opal(self) -> AICapability:
        return next(c for c in CAPABILITIES if c.key == "opal_research")

    def test_ready(self) -> None:
        st = capability_status(
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=True,
                LLM_COMMENTARY_RATIONALE_PROVIDER="claude",
                ANTHROPIC_API_KEY="sk-x",
            ),
            self._claude(),
        )
        assert st["status"] == "ready"
        assert st["enabled"] and st["key_present"] and st["built"]

    def test_disabled(self) -> None:
        st = capability_status(
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=False,
                LLM_COMMENTARY_RATIONALE_PROVIDER="claude",
                ANTHROPIC_API_KEY="sk-x",
            ),
            self._claude(),
        )
        assert st["status"] == "disabled"

    def test_missing_key(self) -> None:
        st = capability_status(
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=True,
                LLM_COMMENTARY_RATIONALE_PROVIDER="claude",
                ANTHROPIC_API_KEY="",
            ),
            self._claude(),
        )
        assert st["status"] == "missing_key"

    def test_provider_none_counts_as_disabled(self) -> None:
        st = capability_status(
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=True,
                LLM_COMMENTARY_RATIONALE_PROVIDER="none",
                ANTHROPIC_API_KEY="sk-x",
            ),
            self._claude(),
        )
        assert st["status"] == "disabled"

    def test_flexible_routing_gemini_serving_rationale_is_ready(self) -> None:
        # Flexible-routing regression: the "claude_commentary" row must
        # resolve to GEMINI_API_KEY (not ANTHROPIC_API_KEY) when the
        # operator routes rationale to Gemini.
        st = capability_status(
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=True,
                LLM_COMMENTARY_RATIONALE_PROVIDER="gemini",
                ANTHROPIC_API_KEY="",
                GEMINI_API_KEY="sk-gem-x",
            ),
            self._claude(),
        )
        assert st["status"] == "ready"
        assert st["active_provider"] == "gemini"

    def test_flexible_routing_claude_serving_alerts_is_ready(self) -> None:
        gemini_alerts = next(c for c in CAPABILITIES if c.key == "gemini_alerts")
        st = capability_status(
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=True,
                LLM_COMMENTARY_ALERT_PROVIDER="claude",
                GEMINI_API_KEY="",
                ANTHROPIC_API_KEY="sk-ant-x",
            ),
            gemini_alerts,
        )
        assert st["status"] == "ready"
        assert st["active_provider"] == "claude"

    def test_flexible_routing_wrong_key_present_is_missing_key(self) -> None:
        # Rationale routed to gemini, but only the ANTHROPIC key is set —
        # must be missing_key, not a false "ready".
        st = capability_status(
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=True,
                LLM_COMMENTARY_RATIONALE_PROVIDER="gemini",
                ANTHROPIC_API_KEY="sk-ant-x",
                GEMINI_API_KEY="",
            ),
            self._claude(),
        )
        assert st["status"] == "missing_key"

    def test_not_built_wins_over_everything(self) -> None:
        # Synthetic capability pointing at a module that will never exist —
        # tests the RANKING invariant (not_built beats enabled+key-present)
        # independent of whether any real shipped capability is unbuilt.
        # (Opal itself shipped in Tier 9 Scope 4 — see
        # test_opal_research_now_built below — so this can no longer use
        # the real opal_research capability to exercise this path.)
        fake_cap = AICapability(
            key="fake_unbuilt",
            label="Fake unbuilt capability",
            enable_settings=("FAKE_ENABLED",),
            provider_key_settings=("FAKE_API_KEY",),
            module="llm.does_not_exist_module_xyz",
            trigger="on_demand",
            toggle_key="FAKE_ENABLED",
            help="Test-only capability for the not_built ranking check.",
        )
        st = capability_status(
            SimpleNamespace(FAKE_ENABLED=True, FAKE_API_KEY="sk-x"),
            fake_cap,
        )
        assert st["status"] == "not_built"

    def test_opal_research_now_built(self) -> None:
        # Tier 9 Scope 4 shipped llm/research.py — Opal's capability row
        # must now resolve built=True and, with the default settings
        # (OPAL_RESEARCH_ENABLED=False), land on "disabled" — never
        # "not_built".
        st = capability_status(
            SimpleNamespace(OPAL_RESEARCH_ENABLED=False, OPAL_RESEARCH_PROVIDER="openai"),
            self._opal(),
        )
        assert st["built"] is True
        assert st["status"] == "disabled"

    def test_opal_research_ready_when_enabled_and_keyed(self) -> None:
        st = capability_status(
            SimpleNamespace(
                OPAL_RESEARCH_ENABLED=True,
                OPAL_RESEARCH_PROVIDER="openai",
                OPENAI_API_KEY="sk-x",
            ),
            self._opal(),
        )
        assert st["status"] == "ready"


class TestOverview:
    def test_one_row_per_capability(self) -> None:
        rows = control_center_overview(SimpleNamespace())
        assert len(rows) == len(CAPABILITIES)
        for r in rows:
            assert {"key", "label", "trigger", "status", "provider_keys", "active_provider"} <= set(r)

    def test_status_badges_present(self) -> None:
        for token in ("ready", "disabled", "missing_key", "not_built"):
            assert status_badge(token)

    def test_active_provider_narrows_required_key(self) -> None:
        rows = control_center_overview(
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=True,
                LLM_COMMENTARY_RATIONALE_PROVIDER="gemini",
                LLM_COMMENTARY_ALERT_PROVIDER="claude",
                ANTHROPIC_API_KEY="sk-ant-x",
                GEMINI_API_KEY="sk-gem-x",
            )
        )
        rationale_row = next(r for r in rows if r["key"] == "claude_commentary")
        alert_row = next(r for r in rows if r["key"] == "gemini_alerts")
        assert rationale_row["active_provider"] == "gemini"
        assert rationale_row["provider_keys"] == ["GEMINI_API_KEY"]
        assert alert_row["active_provider"] == "claude"
        assert alert_row["provider_keys"] == ["ANTHROPIC_API_KEY"]

    def test_non_flexible_capability_has_no_active_provider(self) -> None:
        rows = control_center_overview(SimpleNamespace())
        vision_row = next(r for r in rows if r["key"] == "gemini_vision")
        assert vision_row["active_provider"] is None
        assert vision_row["provider_keys"] == ["GEMINI_API_KEY"]

    def test_provider_selector_setting_is_additive_and_matches_capability(self) -> None:
        """PUT /llm/setting (api/pilots_api.py) needs to know WHICH .env key
        holds a capability's provider choice, per-row, not just a static
        lookup — this is a purely additive field alongside the pre-existing
        ``toggle_key``."""
        rows = control_center_overview(SimpleNamespace())
        by_key = {r["key"]: r for r in rows}
        assert by_key["claude_commentary"]["provider_selector_setting"] == (
            "LLM_COMMENTARY_RATIONALE_PROVIDER"
        )
        assert by_key["gemini_alerts"]["provider_selector_setting"] == (
            "LLM_COMMENTARY_ALERT_PROVIDER"
        )
        assert by_key["opal_research"]["provider_selector_setting"] == "OPAL_RESEARCH_PROVIDER"
        # Non-flexible capabilities carry no provider selector.
        assert by_key["gemini_vision"]["provider_selector_setting"] is None
        assert by_key["gravity_ai_runner"]["provider_selector_setting"] is None


# ---------------------------------------------------------------------------
# validate_toggle_write (CONSTRAINT #3)
# ---------------------------------------------------------------------------
class TestToggleWriteGuard:
    def test_rejects_secret_key(self) -> None:
        from gui.env_io import SecretWriteError

        with pytest.raises(SecretWriteError):
            validate_toggle_write("OPENAI_API_KEY")

    def test_rejects_non_allowlisted_key(self) -> None:
        from gui.env_io import DisallowedKeyError

        with pytest.raises(DisallowedKeyError):
            validate_toggle_write("TOTALLY_MADE_UP_KEY")

    def test_allows_control_center_toggles(self) -> None:
        # Must not raise.
        for key in (
            "GRAVITY_AI_RUNNER_ENABLED",
            "OPAL_RESEARCH_ENABLED",
            "LLM_COMMENTARY_ENABLED",
        ):
            validate_toggle_write(key)


# ---------------------------------------------------------------------------
# Opal gating
# ---------------------------------------------------------------------------
class TestOpalGating:
    def test_opal_now_built(self) -> None:
        # Tier 9 Scope 4 shipped llm/research.py — opal_built() now
        # correctly reports the backend as importable. Readiness is then
        # gated purely on OPAL_RESEARCH_ENABLED + OPENAI_API_KEY (see
        # TestCapabilityStatus.test_opal_research_now_built /
        # test_opal_research_ready_when_enabled_and_keyed).
        assert opal_built() is True


# ---------------------------------------------------------------------------
# Scheduling launcher (subprocess mocked)
# ---------------------------------------------------------------------------
class _FakePopen:
    def __init__(self, *args, **kwargs) -> None:
        self.pid = 424242
        self.args = args
        self.kwargs = kwargs
        self._alive = True

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0

    def kill(self) -> None:
        self._alive = False


class TestSchedulingLauncher:
    def test_launch_interval_spawns_subprocess(self, monkeypatch, tmp_path) -> None:
        from gui import orchestrator_runner as orr

        captured = {}

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(orr.subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(orr, "SCHEDULED_LOG_PATH", tmp_path / "gui_scheduled.log")
        monkeypatch.setattr(orr.settings, "OUTPUT_DIR", tmp_path)

        handle = orr.launch_scheduled_advisory(mode="interval", interval_seconds=120)
        assert handle.mode == "scheduled"
        assert handle.pid == 424242
        assert "--interval" in captured["cmd"]
        assert "120" in captured["cmd"]
        assert handle.is_running() is True

    def test_launch_agent_uses_agent_flag(self, monkeypatch, tmp_path) -> None:
        from gui import orchestrator_runner as orr

        captured = {}

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(orr.subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(orr, "SCHEDULED_LOG_PATH", tmp_path / "gui_scheduled.log")
        monkeypatch.setattr(orr.settings, "OUTPUT_DIR", tmp_path)

        orr.launch_scheduled_advisory(mode="agent")
        assert "--agent" in captured["cmd"]
        assert "--interval" not in captured["cmd"]

    def test_interval_clamped_to_minimum(self, monkeypatch, tmp_path) -> None:
        from gui import orchestrator_runner as orr

        captured = {}

        def _fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            return _FakePopen(cmd, **kwargs)

        monkeypatch.setattr(orr.subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(orr, "SCHEDULED_LOG_PATH", tmp_path / "gui_scheduled.log")
        monkeypatch.setattr(orr.settings, "OUTPUT_DIR", tmp_path)

        orr.launch_scheduled_advisory(mode="interval", interval_seconds=1)
        # Clamped to >= 30 so the operator cannot hot-loop the market-data API.
        assert "30" in captured["cmd"]

    def test_stop_run_terminates(self, monkeypatch, tmp_path) -> None:
        from gui import orchestrator_runner as orr

        monkeypatch.setattr(orr.subprocess, "Popen", lambda cmd, **kw: _FakePopen(cmd, **kw))
        monkeypatch.setattr(orr, "SCHEDULED_LOG_PATH", tmp_path / "gui_scheduled.log")
        monkeypatch.setattr(orr.settings, "OUTPUT_DIR", tmp_path)

        handle = orr.launch_scheduled_advisory(mode="interval", interval_seconds=60)
        assert orr.stop_run(handle) is True
        assert handle.is_running() is False

    def test_stop_run_none_handle_is_safe(self) -> None:
        from gui import orchestrator_runner as orr

        assert orr.stop_run(None) is True


# ---------------------------------------------------------------------------
# Tab wiring + operator-only invariants (source grep)
# ---------------------------------------------------------------------------
class TestTabWiring:
    def test_app_registers_control_center_tab(self) -> None:
        app_src = (_REPO_ROOT / "gui" / "app.py").read_text(encoding="utf-8")
        assert "panels.render_ai_control_center" in app_src
        assert "AI Control Center" in app_src

    def test_scheduling_has_no_autonomous_scheduler(self) -> None:
        orr_src = (_REPO_ROOT / "gui" / "orchestrator_runner.py").read_text(encoding="utf-8")
        assert "subprocess" in orr_src
        assert "threading.Timer" not in orr_src
        assert "schedule.every" not in orr_src
        assert "crontab" not in orr_src

    def test_control_center_has_no_order_verbs(self) -> None:
        src = (_REPO_ROOT / "gui" / "ai_control_center.py").read_text(encoding="utf-8")
        for verb in ("submit_order", "place_order", "buy_order", "sell_order"):
            assert verb not in src


# ---------------------------------------------------------------------------
# The 5th state — invalid_key from last-real-call telemetry.
# ADDITIVE contract: with last_calls omitted/None, every input is byte-identical
# to the pre-telemetry status (the truth-table tests above and Gravity step_86
# check 4 rely on this). invalid_key is ONLY ever reachable via `last_calls`.
# ---------------------------------------------------------------------------
class TestInvalidKeyState:
    def _cap(self, key: str) -> AICapability:
        return next(c for c in CAPABILITIES if c.key == key)

    def _keyed_settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            LLM_COMMENTARY_ENABLED=True,
            LLM_COMMENTARY_RATIONALE_PROVIDER="claude",
            LLM_COMMENTARY_ALERT_PROVIDER="gemini",
            ANTHROPIC_API_KEY="sk-ant-x",
            GEMINI_API_KEY="sk-gem-x",
            OPENAI_API_KEY="sk-openai-x",
            GRAVITY_AI_RUNNER_ENABLED=True,
            OPAL_RESEARCH_ENABLED=True,
            OPAL_RESEARCH_PROVIDER="openai",
        )

    def test_auth_rejection_renders_invalid_key(self) -> None:
        st = capability_status(
            self._keyed_settings(),
            self._cap("claude_commentary"),
            last_calls={"claude": {"source": "last_call", "ok": False, "error_kind": "auth"}},
        )
        assert st["status"] == "invalid_key"
        assert st["invalid_provider"] == "claude"

    @pytest.mark.parametrize("kind", ["rate_limit", "network", "timeout", "schema", "unknown"])
    def test_non_auth_failure_is_NOT_invalid_key(self, kind: str) -> None:
        # The core honesty pin: a transient/schema failure is NOT a key problem.
        st = capability_status(
            self._keyed_settings(),
            self._cap("claude_commentary"),
            last_calls={"claude": {"source": "last_call", "ok": False, "error_kind": kind}},
        )
        assert st["status"] == "ready"
        assert st["invalid_provider"] is None

    def test_key_rotated_verdict_is_not_claimed(self) -> None:
        st = capability_status(
            self._keyed_settings(),
            self._cap("claude_commentary"),
            last_calls={"claude": {"source": "key_rotated", "ok": None, "error_kind": None}},
        )
        assert st["status"] == "ready"

    def test_expired_auth_would_still_gate_but_store_never_expires_auth(self) -> None:
        # Defensive: even a source="expired" record (which the store only ever
        # produces for TRANSIENT kinds) must not render invalid_key, because
        # only source=="last_call" counts.
        st = capability_status(
            self._keyed_settings(),
            self._cap("claude_commentary"),
            last_calls={"claude": {"source": "expired", "ok": False, "error_kind": "auth"}},
        )
        assert st["status"] == "ready"

    def test_omitting_last_calls_is_status_identical(self) -> None:
        # ADDITIVITY: for every capability across a settings matrix, the status
        # with last_calls=None equals the status with no kwarg at all.
        matrices = [
            SimpleNamespace(),  # everything unset
            self._keyed_settings(),  # everything enabled + keyed
            SimpleNamespace(
                LLM_COMMENTARY_ENABLED=True,
                LLM_COMMENTARY_RATIONALE_PROVIDER="claude",
                LLM_COMMENTARY_ALERT_PROVIDER="gemini",
                ANTHROPIC_API_KEY="",  # missing key
                GEMINI_API_KEY="",
            ),
        ]
        for s in matrices:
            for cap in CAPABILITIES:
                a = capability_status(s, cap)["status"]
                b = capability_status(s, cap, last_calls=None)["status"]
                assert a == b, f"{cap.key}: {a!r} != {b!r}"

    def test_missing_key_and_invalid_key_mutually_exclusive(self) -> None:
        # A stale auth verdict must not override missing_key when the key is unset.
        s = self._keyed_settings()
        s.ANTHROPIC_API_KEY = ""
        st = capability_status(
            s,
            self._cap("claude_commentary"),
            last_calls={"claude": {"source": "last_call", "ok": False, "error_kind": "auth"}},
        )
        assert st["status"] == "missing_key"

    def test_gemini_vision_invalid_key_via_static_provider_keys(self) -> None:
        # gemini_vision is non-flexible (active_provider is None) — a bad
        # GEMINI_API_KEY must STILL turn it invalid_key. This is the hole a
        # single `last_call` param (vs. a provider-keyed dict) would leave.
        st = capability_status(
            self._keyed_settings(),
            self._cap("gemini_vision"),
            last_calls={"gemini": {"source": "last_call", "ok": False, "error_kind": "auth"}},
        )
        assert st["status"] == "invalid_key"
        assert st["invalid_provider"] == "gemini"

    def test_disabled_outranks_invalid_key(self) -> None:
        s = self._keyed_settings()
        s.LLM_COMMENTARY_ENABLED = False
        st = capability_status(
            s,
            self._cap("claude_commentary"),
            last_calls={"claude": {"source": "last_call", "ok": False, "error_kind": "auth"}},
        )
        assert st["status"] == "disabled"

    def test_status_badge_has_invalid_key(self) -> None:
        assert status_badge("invalid_key")

    def test_all_four_original_badges_survive(self) -> None:
        # Gravity step_86 check 13 analogue: the additive change must not drop
        # any original badge token.
        for token in ("ready", "disabled", "missing_key", "not_built"):
            assert status_badge(token)


def test_ai_control_center_never_imports_status_store() -> None:
    """The headless status module must stay filesystem-free (no store import):
    it must be testable cold with a bare SimpleNamespace, and control_center_
    overview(SimpleNamespace()) must never read the real output/llm_status.json.
    The panel and the API read the store and pass last_calls in."""
    import ast

    from gui import ai_control_center as acc

    tree = ast.parse(Path(acc.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "status_store" not in node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "status_store" not in alias.name

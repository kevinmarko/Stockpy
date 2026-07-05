"""
tests/test_llm_router.py
=========================
Unit tests for ``llm.router`` — flexible per-job provider selection.

Either provider ("claude" | "gemini") is valid for either job (analyst
rationale, alert commentary) — the operator chooses independently via
``LLM_COMMENTARY_RATIONALE_PROVIDER`` / ``LLM_COMMENTARY_ALERT_PROVIDER``.
There is still no cross-check: each job calls exactly one provider.

All SDK calls are monkeypatched (fake ``anthropic`` + ``openai`` + ``google.genai``
modules installed into ``sys.modules``). No real API requests are made.

Coverage
--------
TestRationaleProviderFlexible — get_rationale_provider() dispatches to
                                 ClaudeProvider OR GeminiProvider based on
                                 LLM_COMMENTARY_RATIONALE_PROVIDER.
TestAlertProviderFlexible     — get_alert_provider() dispatches to
                                 ClaudeProvider OR GeminiProvider based on
                                 LLM_COMMENTARY_ALERT_PROVIDER.
TestMasterSwitchOff            — both selectors return None regardless of
                                 provider choice when LLM_COMMENTARY_ENABLED
                                 is False.
TestNoneAndUnknownProvider     — "none" and an unrecognised provider string
                                 both return None (soft-fail, CONSTRAINT #6).
TestMissingKeySoftFails        — provider selected but its key is unset →
                                 None, never raises.
TestOpalResearchProviderFlexible — get_research_provider() dispatches to
                                 OpenAIProvider OR GeminiProvider based on
                                 OPAL_RESEARCH_PROVIDER; a Gemini choice
                                 ignores the OpenAI-flavored OPAL_RESEARCH_MODEL
                                 default but respects an explicit override.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


def _install_fake_anthropic() -> None:
    fake = types.ModuleType("anthropic")

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = MagicMock()

    fake.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    fake.APIError = Exception  # type: ignore[attr-defined]
    sys.modules["anthropic"] = fake


def _install_fake_openai() -> None:
    fake = types.ModuleType("openai")

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            self.beta = MagicMock()

    fake.OpenAI = _FakeOpenAI  # type: ignore[attr-defined]
    sys.modules["openai"] = fake


def _install_fake_google_genai() -> None:
    pkg_google = types.ModuleType("google")
    pkg_genai = types.ModuleType("google.genai")
    pkg_types = types.ModuleType("google.genai.types")

    class _FakeClient:
        def __init__(self, **kwargs):
            self.models = MagicMock()

    class _FakeHttpOptions:
        def __init__(self, timeout: int = 0):
            self.timeout = timeout

    class _FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    pkg_genai.Client = _FakeClient  # type: ignore[attr-defined]
    pkg_types.HttpOptions = _FakeHttpOptions  # type: ignore[attr-defined]
    pkg_types.GenerateContentConfig = _FakeGenerateContentConfig  # type: ignore[attr-defined]
    pkg_genai.types = pkg_types  # type: ignore[attr-defined]

    sys.modules["google"] = pkg_google
    sys.modules["google.genai"] = pkg_genai
    sys.modules["google.genai.types"] = pkg_types


@pytest.fixture(autouse=True)
def _fresh_fake_sdks():
    """Install fake SDKs for the duration of each test; restore on teardown.

    Mirrors the fixture in tests/test_llm_providers.py. llm.router imports
    ClaudeProvider/GeminiProvider at module load time, but each provider's
    SDK import happens lazily INSIDE __init__ — so installing the fakes
    before calling the router's public functions is sufficient; no need to
    pop llm.router or llm.providers from sys.modules.
    """
    injected = ("anthropic", "openai", "google", "google.genai", "google.genai.types")
    prior = {k: sys.modules.get(k) for k in injected}

    _install_fake_anthropic()
    _install_fake_openai()
    _install_fake_google_genai()
    try:
        yield
    finally:
        for k, p in prior.items():
            if p is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = p


@pytest.fixture()
def router_mod():
    from llm import router as router_mod

    return router_mod


# ---------------------------------------------------------------------------
# TestRationaleProviderFlexible
# ---------------------------------------------------------------------------


class TestRationaleProviderFlexible:
    def test_claude_selected_returns_claude_provider(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "claude", raising=False)
        monkeypatch.setattr(router_mod.settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        prov = router_mod.get_rationale_provider()
        assert prov is not None
        assert prov.name == "claude"

    def test_gemini_selected_returns_gemini_provider(self, router_mod, monkeypatch):
        # The flexible-routing case: Gemini serving the RATIONALE job (its
        # non-default job).
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "sk-gem-x", raising=False)
        prov = router_mod.get_rationale_provider()
        assert prov is not None
        assert prov.name == "gemini"


# ---------------------------------------------------------------------------
# TestAlertProviderFlexible
# ---------------------------------------------------------------------------


class TestAlertProviderFlexible:
    def test_gemini_selected_returns_gemini_provider(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ALERT_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "sk-gem-x", raising=False)
        prov = router_mod.get_alert_provider()
        assert prov is not None
        assert prov.name == "gemini"

    def test_claude_selected_returns_claude_provider(self, router_mod, monkeypatch):
        # The flexible-routing case: Claude serving the ALERT job (its
        # non-default job).
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ALERT_PROVIDER", "claude", raising=False)
        monkeypatch.setattr(router_mod.settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        prov = router_mod.get_alert_provider()
        assert prov is not None
        assert prov.name == "claude"


# ---------------------------------------------------------------------------
# TestMasterSwitchOff
# ---------------------------------------------------------------------------


class TestMasterSwitchOff:
    def test_rationale_off_returns_none_even_with_key(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "claude", raising=False)
        monkeypatch.setattr(router_mod.settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        assert router_mod.get_rationale_provider() is None

    def test_alert_off_returns_none_even_with_key(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", False, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ALERT_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "sk-gem-x", raising=False)
        assert router_mod.get_alert_provider() is None


# ---------------------------------------------------------------------------
# TestNoneAndUnknownProvider
# ---------------------------------------------------------------------------


class TestNoneAndUnknownProvider:
    def test_provider_none_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "none", raising=False)
        monkeypatch.setattr(router_mod.settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        assert router_mod.get_rationale_provider() is None

    def test_unknown_provider_string_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ALERT_PROVIDER", "grok", raising=False)
        assert router_mod.get_alert_provider() is None

    def test_empty_provider_string_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "", raising=False)
        assert router_mod.get_rationale_provider() is None

    def test_provider_choice_is_case_insensitive(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "Claude", raising=False)
        monkeypatch.setattr(router_mod.settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)
        prov = router_mod.get_rationale_provider()
        assert prov is not None
        assert prov.name == "claude"


# ---------------------------------------------------------------------------
# TestMissingKeySoftFails
# ---------------------------------------------------------------------------


class TestMissingKeySoftFails:
    def test_claude_selected_no_key_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "claude", raising=False)
        monkeypatch.setattr(router_mod.settings, "ANTHROPIC_API_KEY", None, raising=False)
        assert router_mod.get_rationale_provider() is None

    def test_gemini_selected_no_key_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ALERT_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "", raising=False)
        assert router_mod.get_alert_provider() is None

    def test_construction_exception_returns_none_never_raises(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "LLM_COMMENTARY_RATIONALE_PROVIDER", "claude", raising=False)
        monkeypatch.setattr(router_mod.settings, "ANTHROPIC_API_KEY", "sk-ant-x", raising=False)

        def _boom(*args, **kwargs):
            raise RuntimeError("SDK construction blew up")

        monkeypatch.setattr(router_mod, "ClaudeProvider", _boom)
        # Must not raise — soft-fail contract (CONSTRAINT #6).
        assert router_mod.get_rationale_provider() is None


# ---------------------------------------------------------------------------
# TestOpalResearchProviderFlexible — get_research_provider() now dispatches
# to OpenAIProvider OR GeminiProvider based on OPAL_RESEARCH_PROVIDER.
# ---------------------------------------------------------------------------


class TestOpalResearchProviderFlexible:
    def test_master_switch_off_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", False, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "sk-gem-x", raising=False)
        assert router_mod.get_research_provider() is None

    def test_openai_selected_returns_openai_provider(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "openai", raising=False)
        monkeypatch.setattr(router_mod.settings, "OPENAI_API_KEY", "sk-oai-x", raising=False)
        prov = router_mod.get_research_provider()
        assert prov is not None
        assert prov.name == "openai"

    def test_gemini_selected_returns_gemini_provider(self, router_mod, monkeypatch):
        # The scenario this task is about: switching Opal from OpenAI to Gemini.
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "sk-gem-x", raising=False)
        prov = router_mod.get_research_provider()
        assert prov is not None
        assert prov.name == "gemini"

    def test_gemini_selected_ignores_openai_flavored_model_default(self, router_mod, monkeypatch):
        # OPAL_RESEARCH_MODEL still at its OpenAI default ("gpt-4o") must not
        # be forwarded to GeminiProvider — it should fall back to
        # GeminiProvider's own (Gemini) model default instead.
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "sk-gem-x", raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_MODEL", "gpt-4o", raising=False)
        prov = router_mod.get_research_provider()
        assert prov is not None
        assert prov._model != "gpt-4o"
        assert prov._model == "gemini-2.5-flash"

    def test_gemini_selected_respects_explicit_gemini_model_override(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "sk-gem-x", raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_MODEL", "gemini-1.5-pro", raising=False)
        prov = router_mod.get_research_provider()
        assert prov is not None
        assert prov._model == "gemini-1.5-pro"

    def test_gemini_selected_no_key_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "", raising=False)
        assert router_mod.get_research_provider() is None

    def test_openai_selected_no_key_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "openai", raising=False)
        monkeypatch.setattr(router_mod.settings, "OPENAI_API_KEY", None, raising=False)
        assert router_mod.get_research_provider() is None

    def test_none_provider_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "none", raising=False)
        assert router_mod.get_research_provider() is None

    def test_unknown_provider_string_returns_none(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "grok", raising=False)
        assert router_mod.get_research_provider() is None

    def test_gemini_construction_exception_returns_none_never_raises(self, router_mod, monkeypatch):
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_ENABLED", True, raising=False)
        monkeypatch.setattr(router_mod.settings, "OPAL_RESEARCH_PROVIDER", "gemini", raising=False)
        monkeypatch.setattr(router_mod.settings, "GEMINI_API_KEY", "sk-gem-x", raising=False)

        def _boom(*args, **kwargs):
            raise RuntimeError("SDK construction blew up")

        monkeypatch.setattr(router_mod, "GeminiProvider", _boom)
        assert router_mod.get_research_provider() is None

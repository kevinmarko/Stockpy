"""
tests/test_gui_env_io_secrets.py
=================================
Consolidated pin for a batch of secret credentials added to
``gui/env_io.py``'s ``SECRET_KEYS`` in separate incremental PRs, plus the
non-secret operator toggles that sit alongside them:

    OPENAI_API_KEY      (Opal / Tier 9 Scope 4 credential)
    NTFY_TOPIC          (ntfy.sh push topic — functions like a bearer token)
    ANTHROPIC_API_KEY   (Tier 9 LLM credential)
    GEMINI_API_KEY      (Tier 9 LLM credential)

Each key previously had its own near-identical file re-asserting the same
three-part secret-boundary contract (is-secret, not-allowed, write-raises).
Consolidated here as one parametrized table; every key keeps its own
individually-reported test case (nothing here reduces coverage, only the
duplicated boilerplate). Formerly: tests/test_gui_env_io_ntfy_topic.py,
tests/test_gui_env_io_openai_key.py, tests/test_gui_env_io_secret_llm_keys.py,
tests/test_gui_env_io_control_center_keys.py (fully subsumed by
test_gui_env_io_openai_key.py's OPENAI_API_KEY/OPAL_RESEARCH_* coverage
except for the two GRAVITY_AI_RUNNER_ENABLED/LLM_COMMENTARY_ENABLED
allowlist assertions folded into ``TOGGLE_KEYS`` below).

All writes are redirected to a temp ``.env`` via monkeypatching
``env_io.ENV_PATH`` so the real project ``.env`` is never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import env_io
from gui.env_io import ALLOWED_KEYS, SECRET_KEYS, SecretWriteError, write_setting

SECRET_KEYS_TO_VERIFY = (
    "OPENAI_API_KEY",
    "NTFY_TOPIC",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
)

# Non-secret operator toggles that sit alongside the credentials above, so
# the GUI can flip them without ever touching a credential.
TOGGLE_KEYS = (
    "GRAVITY_AI_RUNNER_ENABLED",
    "OPAL_RESEARCH_ENABLED",
    "OPAL_RESEARCH_PROVIDER",
    "OPAL_RESEARCH_MODEL",
    "LLM_COMMENTARY_ENABLED",
    "LLM_COMMENTARY_RATIONALE_PROVIDER",
    "LLM_COMMENTARY_ALERT_PROVIDER",
)


@pytest.fixture()
def temp_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# fixture\nRISK_FREE_RATE=0.045\n", encoding="utf-8")
    monkeypatch.setattr(env_io, "ENV_PATH", env_file, raising=False)
    return env_file


# ---------------------------------------------------------------------------
# Secret-boundary contract (CONSTRAINT #3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", SECRET_KEYS_TO_VERIFY)
def test_key_is_secret(key):
    assert key in SECRET_KEYS, f"{key} must be in SECRET_KEYS (CONSTRAINT #3)"


@pytest.mark.parametrize("key", SECRET_KEYS_TO_VERIFY)
def test_key_not_allowed(key):
    assert key not in ALLOWED_KEYS, f"{key} must NOT be in ALLOWED_KEYS"


@pytest.mark.parametrize("key", SECRET_KEYS_TO_VERIFY)
def test_write_setting_raises_secret_write_error(key, temp_env):
    with pytest.raises(SecretWriteError):
        write_setting(key, "should-never-write")
    assert key not in temp_env.read_text(encoding="utf-8")


@pytest.mark.parametrize("key", SECRET_KEYS_TO_VERIFY)
def test_read_settings_masks_key_when_present(key, monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(f"{key}=real-secret-value\n", encoding="utf-8")
    monkeypatch.setattr(env_io, "ENV_PATH", env_path, raising=False)
    settings_view = env_io.read_settings()
    if key in settings_view:
        assert settings_view[key] != "real-secret-value"


def test_ntfy_topic_masking_matches_mask_secret(monkeypatch, tmp_path):
    """NTFY_TOPIC specifically: pin the exact masked value, not just
    inequality with the real one — a topic name functions like a bearer
    token for ntfy.sh, so alerting.py's own docstring says to "keep the
    topic unguessable"."""
    env_path = tmp_path / ".env"
    env_path.write_text("NTFY_TOPIC=my-real-unguessable-topic\n", encoding="utf-8")
    monkeypatch.setattr(env_io, "ENV_PATH", env_path, raising=False)
    settings_view = env_io.read_settings()
    assert settings_view["NTFY_TOPIC"] != "my-real-unguessable-topic"
    assert settings_view["NTFY_TOPIC"] == env_io.mask_secret("my-real-unguessable-topic")


def test_secrets_expander_shows_source():
    # gui/panels/settings_manager.py's "🔒 Secrets (masked, read-only)"
    # expander iterates env_io.SECRET_KEYS directly — membership above is
    # sufficient for it to render a row, but pin the wiring explicitly so
    # a future refactor of that panel can't silently drop the iteration.
    source = Path("gui/panels/settings_manager.py").read_text(encoding="utf-8")
    assert "env_io.SECRET_KEYS" in source


# ---------------------------------------------------------------------------
# Non-secret operator toggles alongside the credentials above
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", TOGGLE_KEYS)
def test_toggle_in_allowed_keys(key):
    assert key in ALLOWED_KEYS, f"{key} must be in ALLOWED_KEYS (operator-tunable)"


@pytest.mark.parametrize("key", TOGGLE_KEYS)
def test_toggle_not_secret(key):
    assert key not in SECRET_KEYS, f"{key} must NOT be in SECRET_KEYS"


@pytest.mark.parametrize(
    "key,value",
    [
        ("OPAL_RESEARCH_ENABLED", "true"),
        ("OPAL_RESEARCH_PROVIDER", "openai"),
        ("OPAL_RESEARCH_MODEL", "gpt-4o"),
    ],
)
def test_write_setting_round_trips_opal_toggle(key, value, temp_env):
    write_setting(key, value)
    contents = temp_env.read_text(encoding="utf-8")
    assert key in contents

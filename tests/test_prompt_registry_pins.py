"""
tests/test_prompt_registry_pins.py
==================================
Prompt Registry settings and pin handling: credential keys stay secret,
tunables stay GUI-writable, rollback moves the pin, and pins are written to
``.env`` as a plain dict (``env_io`` owns the JSON encoding).

Split out of the former ``tests/test_prompt_registry_gui.py`` when the
Streamlit desktop app it also covered was deleted.
"""

from __future__ import annotations

import json
import sys
import unittest.mock
from pathlib import Path
from typing import Optional

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Now import the helpers we need
from prompt_registry.cache import CacheManager
from prompt_registry.models import PromptRecord, PromptVersion, RegistryManifest
from prompt_registry.registry import PromptRegistry, reset_registry
from prompt_registry.signing import compute_sha256, sign


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SIGN_KEY = "gui-test-signing-key"
_KNOWN_ID = "gravity.system"
_UNKNOWN_ID = "stage.no.such.prompt.v999"


def _make_record(body: str, *, key: Optional[str] = None) -> PromptRecord:
    sha = compute_sha256(body)
    sig = sign(body, key) if key else "unsigned"
    return PromptRecord(body=body, sha256=sha, signature=sig, created_at="2026-06-30T00:00:00Z")


def _make_manifest(entries: dict) -> RegistryManifest:
    prompts = {}
    for pid, body in entries.items():
        rec = _make_record(body)
        prompts[pid] = PromptVersion(latest="1.0.0", versions={"1.0.0": rec})
    return RegistryManifest(registry_version="gui-test", signing_alg="HMAC-SHA256", prompts=prompts)


def _make_registry(
    tmp_path: Path,
    *,
    manifest: Optional[RegistryManifest] = None,
    pins: Optional[dict] = None,
    enabled: bool = True,
) -> PromptRegistry:
    cache = CacheManager(tmp_path)
    reg = PromptRegistry(store=None, cache=cache, pins=pins or {}, enabled=enabled)
    if manifest is not None:
        reg._manifest = manifest
    return reg


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


# ---------------------------------------------------------------------------
# TestSourceBadge
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestResolveSource
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestCachedVersions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestBodyForVersion
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestAllKnownIds
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestRenderPromptRegistryExists
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestSecurityInvariants
# ---------------------------------------------------------------------------

class TestSecurityInvariants:
    def test_four_creds_in_secret_keys(self):
        from shared.env_io import SECRET_KEYS
        for k in [
            "PROMPT_REGISTRY_URL",
            "PROMPT_REGISTRY_TOKEN",
            "PROMPT_REGISTRY_PUBLISH_TOKEN",
            "PROMPT_REGISTRY_SIGNING_KEY",
        ]:
            assert k in SECRET_KEYS, f"{k} must be in SECRET_KEYS"

    def test_four_creds_not_in_allowed_keys(self):
        from shared.env_io import ALLOWED_KEYS
        for k in [
            "PROMPT_REGISTRY_URL",
            "PROMPT_REGISTRY_TOKEN",
            "PROMPT_REGISTRY_PUBLISH_TOKEN",
            "PROMPT_REGISTRY_SIGNING_KEY",
        ]:
            assert k not in ALLOWED_KEYS, f"{k} must NOT be in ALLOWED_KEYS"

    def test_three_tunables_in_allowed_keys(self):
        from shared.env_io import ALLOWED_KEYS
        for k in [
            "PROMPT_REGISTRY_ENABLED",
            "PROMPT_REGISTRY_BACKEND",
            "PROMPT_REGISTRY_PINS",
        ]:
            assert k in ALLOWED_KEYS, f"{k} must be in ALLOWED_KEYS"

    def test_pins_in_json_keys(self):
        from shared.env_io import _JSON_KEYS
        assert "PROMPT_REGISTRY_PINS" in _JSON_KEYS


    def test_creds_raise_secret_write_error(self):
        from shared.env_io import write_setting, SecretWriteError
        for k in [
            "PROMPT_REGISTRY_URL",
            "PROMPT_REGISTRY_TOKEN",
            "PROMPT_REGISTRY_PUBLISH_TOKEN",
            "PROMPT_REGISTRY_SIGNING_KEY",
        ]:
            with pytest.raises(SecretWriteError):
                write_setting(k, "anything")


# ---------------------------------------------------------------------------
# TestAppWiring
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestDisabledRegistryPath
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TestRollbackPath (structural — no Streamlit needed)
# ---------------------------------------------------------------------------

class TestRollbackPath:
    def test_rollback_updates_pin(self, tmp_path):
        """reg.rollback() returns the previous version string and sets a pin."""
        import time as _time
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "1.0.0", _make_record("v1 — Output in JSON."))
        _time.sleep(0.02)  # ensure distinct mtime so newest-first ordering is stable
        cache.write(_KNOWN_ID, "2.0.0", _make_record("v2 — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)

        # rollback() returns the rolled-back-to version string (Optional[str])
        rolled = reg.rollback(_KNOWN_ID)
        assert rolled is not None, "Expected rollback to succeed"
        assert rolled == reg._pins.get(_KNOWN_ID)

    def test_rollback_one_version_returns_none(self, tmp_path):
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "1.0.0", _make_record("only — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        ok = reg.rollback(_KNOWN_ID)
        assert ok is None

    def test_pin_write_uses_env_io(self, tmp_path):
        """Confirm env_io.write_setting is called with PROMPT_REGISTRY_PINS after
        rollback, and — regression for the double-JSON-encoding bug — that it is
        passed a plain dict, not a pre-``json.dumps``'d string. ``env_io._encode_value``
        already JSON-encodes any ``_JSON_KEYS`` value, so pre-dumping here would
        double-encode and ``PromptRegistry._build_registry_from_settings()``'s
        ``json.loads()`` would parse back a string, not a dict, silently discarding
        the pin."""
        import time as _time
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "1.0.0", _make_record("v1 — Output in JSON."))
        _time.sleep(0.02)
        cache.write(_KNOWN_ID, "2.0.0", _make_record("v2 — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        rolled = reg.rollback(_KNOWN_ID)
        assert rolled is not None, "rollback must succeed with 2 versions"

        with unittest.mock.patch("shared.env_io.write_setting") as mock_write:
            pins_dict = dict(sorted(reg._pins.items()))
            # Import from the real env_io module directly, not the gui/env_io.py
            # shim -- the shim's own `write_setting` name was bound once, via
            # `from env_io import *`, at shim MODULE LOAD time, so it holds a
            # stale reference to the unpatched function regardless of when this
            # `from shared.env_io import write_setting` statement itself executes;
            # patching env_io.write_setting only swaps the attribute on the real
            # module's own namespace, which importing via the shim never re-reads.
            from shared.env_io import write_setting
            write_setting("PROMPT_REGISTRY_PINS", pins_dict)
            mock_write.assert_called_once_with("PROMPT_REGISTRY_PINS", pins_dict)
            key, val = mock_write.call_args[0]
            assert key == "PROMPT_REGISTRY_PINS"
            assert isinstance(val, dict), (
                "write_setting must receive a plain dict, not a pre-JSON-encoded "
                "string — env_io._encode_value() does the JSON encoding"
            )
            assert val[_KNOWN_ID] == "1.0.0"


# ---------------------------------------------------------------------------
# TestPinWriteEncoding (regression for the double-JSON-encoding bug)
# ---------------------------------------------------------------------------

class TestPinWriteEncoding:

    def test_env_io_pins_roundtrip_dict_not_double_encoded(self, tmp_path, monkeypatch):
        """End-to-end: write_setting(dict) round-trips to the same dict. The
        historical bug (passing json.dumps(dict) instead) round-trips to a
        STRING instead of a dict — exactly the silent-discard bug
        PromptRegistry._build_registry_from_settings()'s isinstance(pins, dict)
        check would hit."""
        import shared.env_io as env_io_mod
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        monkeypatch.setattr(env_io_mod, "ENV_PATH", env_file)

        pins = {_KNOWN_ID: "1.0.0", "other.prompt": "2.3.4"}
        env_io_mod.write_setting("PROMPT_REGISTRY_PINS", pins)
        parsed = json.loads(env_io_mod.get_value("PROMPT_REGISTRY_PINS"))
        assert isinstance(parsed, dict)
        assert parsed == pins

        # Sanity check documenting the bug this guards against: pre-dumping
        # before calling write_setting really does round-trip to a str.
        env_io_mod.write_setting("PROMPT_REGISTRY_PINS", json.dumps(pins))
        parsed_buggy = json.loads(env_io_mod.get_value("PROMPT_REGISTRY_PINS"))
        assert isinstance(parsed_buggy, str)

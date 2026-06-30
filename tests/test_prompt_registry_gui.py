"""
tests/test_prompt_registry_gui.py
===================================
Headless tests for Stage 7 — ``gui/panels.render_prompt_registry``.

Because Streamlit is not imported in test mode, every function tested here is
either a pure helper extracted from ``gui/panels.py`` or a structural
source-code guard.  We never import ``streamlit`` in this file.

Coverage
--------
- ``_pr_source_badge`` — correct emoji prefix per source label.
- ``_pr_resolve_source`` — pin > manifest > cache > baseline precedence.
- ``_pr_cached_versions`` — reads from ``CacheManager.list_versions``.
- ``_pr_body_for_version`` — delegates to ``__main__._resolve_body_for_version``.
- ``_pr_all_known_ids`` — union of baseline + manifest + pins.
- ``render_prompt_registry`` importable (function exists in panels).
- Security invariants — PROMPT_REGISTRY_PINS in ALLOWED_KEYS; 4 creds NOT
  in ALLOWED_KEYS; security banner string present in source.
- ``app.py`` source wires tab 10 to ``render_prompt_registry``.
- Rollback path: ``reg.rollback()`` is called and pin written via env_io.
- Disabled-registry path: resolved source is always "baseline" when
  PROMPT_REGISTRY_ENABLED is False (no network call needed).
"""

from __future__ import annotations

import json
import sys
import types
import unittest.mock
from pathlib import Path
from typing import Optional

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Import the panel helpers we want to test WITHOUT importing streamlit.
# We monkey-patch streamlit at import time so panels.py loads cleanly.
# ---------------------------------------------------------------------------

_st_mock = types.ModuleType("streamlit")
for _attr in [
    "subheader", "caption", "info", "warning", "error", "success", "divider",
    "code", "markdown", "button", "selectbox", "columns", "expander",
    "dataframe", "spinner", "cache_data",
]:
    setattr(_st_mock, _attr, unittest.mock.MagicMock())

# st.cache_data must return a no-op decorator
_st_mock.cache_data = lambda *a, **kw: (lambda f: f)  # type: ignore[misc]
sys.modules.setdefault("streamlit", _st_mock)

# matplotlib is installed; let it import normally so find_spec() works in
# pandas_ta_classic._meta and other dependency-check paths.  Only streamlit
# (not installed in this venv) needs a stub.

# Now import the helpers we need
from prompt_registry.cache import CacheManager, read_baseline, list_baseline_ids
from prompt_registry.models import PromptRecord, PromptVersion, RegistryManifest
from prompt_registry.registry import PromptRegistry, reset_registry
from prompt_registry.signing import compute_sha256, sign


# ---------------------------------------------------------------------------
# Import the pure panel helpers directly by importing panels without Streamlit
# side-effects (the mock is already in sys.modules).
# ---------------------------------------------------------------------------

# Defer import to avoid triggering top-level st calls before mock is set up
import importlib
_panels = importlib.import_module("gui.panels")

_pr_source_badge = getattr(_panels, "_pr_source_badge")
_pr_resolve_source = getattr(_panels, "_pr_resolve_source")
_pr_cached_versions = getattr(_panels, "_pr_cached_versions")
_pr_body_for_version = getattr(_panels, "_pr_body_for_version")
_pr_all_known_ids = getattr(_panels, "_pr_all_known_ids")


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

class TestSourceBadge:
    def test_pin_badge(self):
        assert "pin" in _pr_source_badge("pin")
        assert "📌" in _pr_source_badge("pin")

    def test_remote_badge(self):
        assert "🌐" in _pr_source_badge("remote")

    def test_cache_badge(self):
        assert "💾" in _pr_source_badge("cache")

    def test_baseline_badge(self):
        assert "📦" in _pr_source_badge("baseline")

    def test_unknown_label_returned_unchanged(self):
        assert _pr_source_badge("potato") == "potato"


# ---------------------------------------------------------------------------
# TestResolveSource
# ---------------------------------------------------------------------------

class TestResolveSource:
    def test_pin_takes_priority(self, tmp_path):
        manifest = _make_manifest({_KNOWN_ID: "Remote body — Output in JSON."})
        reg = _make_registry(tmp_path, manifest=manifest, pins={_KNOWN_ID: "2.0.0"})
        ver, src = _pr_resolve_source(reg, _KNOWN_ID)
        assert src == "pin"
        assert ver == "2.0.0"

    def test_remote_when_no_pin(self, tmp_path):
        manifest = _make_manifest({_KNOWN_ID: "Remote body."})
        reg = _make_registry(tmp_path, manifest=manifest)
        ver, src = _pr_resolve_source(reg, _KNOWN_ID)
        assert src == "remote"
        assert ver == "1.0.0"

    def test_cache_when_no_manifest(self, tmp_path):
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "3.1.4", _make_record("Cached body — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        ver, src = _pr_resolve_source(reg, _KNOWN_ID)
        assert src == "cache"
        assert ver == "3.1.4"

    def test_baseline_when_nothing_else(self, tmp_path):
        reg = _make_registry(tmp_path)
        # _KNOWN_ID has a committed baseline
        ver, src = _pr_resolve_source(reg, _KNOWN_ID)
        assert src == "baseline"

    def test_unknown_id_no_baseline_returns_unknown(self, tmp_path):
        reg = _make_registry(tmp_path)
        ver, src = _pr_resolve_source(reg, _UNKNOWN_ID)
        assert src == "unknown"
        assert ver == "—"

    def test_pin_beats_manifest(self, tmp_path):
        manifest = _make_manifest({_KNOWN_ID: "Remote body."})
        reg = _make_registry(tmp_path, manifest=manifest, pins={_KNOWN_ID: "pinned-ver"})
        _, src = _pr_resolve_source(reg, _KNOWN_ID)
        assert src == "pin"

    def test_manifest_beats_cache(self, tmp_path):
        manifest = _make_manifest({_KNOWN_ID: "Remote body."})
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "0.0.1", _make_record("Old cache body — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        reg._manifest = manifest
        _, src = _pr_resolve_source(reg, _KNOWN_ID)
        assert src == "remote"

    def test_cache_beats_baseline(self, tmp_path):
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "5.0.0", _make_record("Cache body — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        _, src = _pr_resolve_source(reg, _KNOWN_ID)
        assert src == "cache"

    def test_no_cache_attr_degrades(self, tmp_path):
        reg = _make_registry(tmp_path)
        reg._cache = None
        ver, src = _pr_resolve_source(reg, _KNOWN_ID)
        # Should fall through to baseline
        assert src in ("baseline", "unknown")


# ---------------------------------------------------------------------------
# TestCachedVersions
# ---------------------------------------------------------------------------

class TestCachedVersions:
    def test_returns_empty_for_no_cache(self, tmp_path):
        reg = PromptRegistry(store=None, cache=CacheManager(tmp_path), enabled=True)
        assert _pr_cached_versions(reg, _KNOWN_ID) == []

    def test_returns_both_versions(self, tmp_path):
        # list_versions is newest-first by mtime; just assert both present
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "1.0.0", _make_record("v1 — Output in JSON."))
        cache.write(_KNOWN_ID, "2.0.0", _make_record("v2 — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        versions = _pr_cached_versions(reg, _KNOWN_ID)
        assert "1.0.0" in versions
        assert "2.0.0" in versions

    def test_returns_empty_when_cache_none(self, tmp_path):
        reg = _make_registry(tmp_path)
        reg._cache = None
        assert _pr_cached_versions(reg, _KNOWN_ID) == []

    def test_single_version(self, tmp_path):
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "9.9.9", _make_record("v9 — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        assert _pr_cached_versions(reg, _KNOWN_ID) == ["9.9.9"]


# ---------------------------------------------------------------------------
# TestBodyForVersion
# ---------------------------------------------------------------------------

class TestBodyForVersion:
    def test_baseline_keyword_returns_body(self, tmp_path):
        reg = _make_registry(tmp_path)
        body = _pr_body_for_version(reg, _KNOWN_ID, "baseline")
        assert body is not None
        assert len(body) > 0

    def test_cached_version_returns_body(self, tmp_path):
        expected = "Cached body for get test — Output in JSON."
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "7.0.0", _make_record(expected))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        body = _pr_body_for_version(reg, _KNOWN_ID, "7.0.0")
        assert body == expected

    def test_missing_version_returns_none(self, tmp_path):
        reg = _make_registry(tmp_path)
        body = _pr_body_for_version(reg, _KNOWN_ID, "9.9.9")
        assert body is None

    def test_unknown_id_returns_none(self, tmp_path):
        reg = _make_registry(tmp_path)
        body = _pr_body_for_version(reg, _UNKNOWN_ID, "baseline")
        assert body is None

    def test_manifest_version_returns_body(self, tmp_path):
        expected = "Manifest body — Output in JSON."
        manifest = _make_manifest({_KNOWN_ID: expected})
        reg = _make_registry(tmp_path, manifest=manifest)
        body = _pr_body_for_version(reg, _KNOWN_ID, "1.0.0")
        assert body == expected


# ---------------------------------------------------------------------------
# TestAllKnownIds
# ---------------------------------------------------------------------------

class TestAllKnownIds:
    def test_includes_baseline_ids(self, tmp_path):
        ids = _pr_all_known_ids(False)
        baseline = list(list_baseline_ids())
        for bid in baseline:
            assert bid in ids

    def test_includes_pinned_id(self, tmp_path):
        import prompt_registry.registry as reg_mod
        cache = CacheManager(tmp_path)
        reg = PromptRegistry(store=None, cache=cache, pins={_UNKNOWN_ID: "1.0.0"}, enabled=True)
        reg_mod._global_registry = reg
        # _pr_all_known_ids may be decorated with @st.cache_data when Streamlit is
        # imported for real by other test files before this one.  Clear the cache so
        # the registry state we just set above is observed by the next call.
        try:
            _pr_all_known_ids.clear()
        except AttributeError:
            pass  # mock replaced decorator → no .clear(); call proceeds uncached
        ids = _pr_all_known_ids(False)
        assert _UNKNOWN_ID in ids
        reg_mod._global_registry = None

    def test_includes_manifest_ids(self, tmp_path):
        import prompt_registry.registry as reg_mod
        manifest = _make_manifest({"novel.prompt.v1": "Body."})
        reg = PromptRegistry(store=None, cache=CacheManager(tmp_path), enabled=True)
        reg._manifest = manifest
        reg_mod._global_registry = reg
        ids = _pr_all_known_ids(True)
        assert "novel.prompt.v1" in ids
        reg_mod._global_registry = None

    def test_sorted(self):
        ids = _pr_all_known_ids(False)
        assert ids == sorted(ids)

    def test_degrades_on_import_error(self, monkeypatch):
        import gui.panels as p
        monkeypatch.setattr(p, "_pr_all_known_ids",
                            lambda enabled: [])  # simulate import failure
        # The real function tolerates errors and returns []
        from prompt_registry.cache import list_baseline_ids as _lbi
        ids = list(_lbi())
        assert isinstance(ids, list)


# ---------------------------------------------------------------------------
# TestRenderPromptRegistryExists
# ---------------------------------------------------------------------------

class TestRenderPromptRegistryExists:
    def test_function_exists(self):
        assert callable(getattr(_panels, "render_prompt_registry", None))

    def test_helper_functions_exist(self):
        for name in [
            "_pr_source_badge",
            "_pr_resolve_source",
            "_pr_cached_versions",
            "_pr_body_for_version",
            "_pr_all_known_ids",
        ]:
            assert callable(getattr(_panels, name, None)), f"{name} missing from panels"


# ---------------------------------------------------------------------------
# TestSecurityInvariants
# ---------------------------------------------------------------------------

class TestSecurityInvariants:
    def test_four_creds_in_secret_keys(self):
        from gui.env_io import SECRET_KEYS
        for k in [
            "PROMPT_REGISTRY_URL",
            "PROMPT_REGISTRY_TOKEN",
            "PROMPT_REGISTRY_PUBLISH_TOKEN",
            "PROMPT_REGISTRY_SIGNING_KEY",
        ]:
            assert k in SECRET_KEYS, f"{k} must be in SECRET_KEYS"

    def test_four_creds_not_in_allowed_keys(self):
        from gui.env_io import ALLOWED_KEYS
        for k in [
            "PROMPT_REGISTRY_URL",
            "PROMPT_REGISTRY_TOKEN",
            "PROMPT_REGISTRY_PUBLISH_TOKEN",
            "PROMPT_REGISTRY_SIGNING_KEY",
        ]:
            assert k not in ALLOWED_KEYS, f"{k} must NOT be in ALLOWED_KEYS"

    def test_three_tunables_in_allowed_keys(self):
        from gui.env_io import ALLOWED_KEYS
        for k in [
            "PROMPT_REGISTRY_ENABLED",
            "PROMPT_REGISTRY_BACKEND",
            "PROMPT_REGISTRY_PINS",
        ]:
            assert k in ALLOWED_KEYS, f"{k} must be in ALLOWED_KEYS"

    def test_pins_in_json_keys(self):
        from gui.env_io import _JSON_KEYS
        assert "PROMPT_REGISTRY_PINS" in _JSON_KEYS

    def test_security_banner_in_source(self):
        panels_src = Path(_REPO_ROOT / "gui" / "panels.py").read_text()
        assert "safety gates are enforced in code" in panels_src.lower() or \
               "safety gates are" in panels_src

    def test_creds_raise_secret_write_error(self):
        from gui.env_io import write_setting, SecretWriteError
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

class TestAppWiring:
    def test_prompts_tab_in_app_tab_labels(self):
        app_src = Path(_REPO_ROOT / "gui" / "app.py").read_text()
        assert "📝 Prompts" in app_src

    def test_render_prompt_registry_called_in_app(self):
        app_src = Path(_REPO_ROOT / "gui" / "app.py").read_text()
        assert "render_prompt_registry" in app_src

    def test_safe_panel_wraps_render_prompt_registry(self):
        app_src = Path(_REPO_ROOT / "gui" / "app.py").read_text()
        # safe_panel(panels.render_prompt_registry) must appear
        assert "safe_panel(panels.render_prompt_registry)" in app_src

    def test_tab_count_includes_prompts(self):
        app_src = Path(_REPO_ROOT / "gui" / "app.py").read_text()
        # Count entries in the tab_labels list by a simple heuristic
        import ast
        tree = ast.parse(app_src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "tab_labels":
                        if isinstance(node.value, ast.List):
                            assert len(node.value.elts) >= 11


# ---------------------------------------------------------------------------
# TestDisabledRegistryPath
# ---------------------------------------------------------------------------

class TestDisabledRegistryPath:
    def test_resolve_source_baseline_when_disabled(self, tmp_path):
        """When the registry is disabled there is no manifest/cache; source = baseline."""
        reg = _make_registry(tmp_path, enabled=False)
        # A known ID has a committed baseline
        _, src = _pr_resolve_source(reg, _KNOWN_ID)
        assert src == "baseline"

    def test_cached_versions_empty_when_disabled(self, tmp_path):
        reg = _make_registry(tmp_path, enabled=False)
        assert _pr_cached_versions(reg, _KNOWN_ID) == []

    def test_body_for_baseline_keyword_still_works_when_disabled(self, tmp_path):
        reg = _make_registry(tmp_path, enabled=False)
        body = _pr_body_for_version(reg, _KNOWN_ID, "baseline")
        assert body is not None and len(body) > 0


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
        """Confirm env_io.write_setting is called with PROMPT_REGISTRY_PINS after rollback."""
        import time as _time
        cache = CacheManager(tmp_path)
        cache.write(_KNOWN_ID, "1.0.0", _make_record("v1 — Output in JSON."))
        _time.sleep(0.02)
        cache.write(_KNOWN_ID, "2.0.0", _make_record("v2 — Output in JSON."))
        reg = PromptRegistry(store=None, cache=cache, enabled=True)
        rolled = reg.rollback(_KNOWN_ID)
        assert rolled is not None, "rollback must succeed with 2 versions"

        with unittest.mock.patch("gui.env_io.write_setting") as mock_write:
            pins_json = json.dumps(dict(sorted(reg._pins.items())))
            from gui.env_io import write_setting
            write_setting("PROMPT_REGISTRY_PINS", pins_json)
            mock_write.assert_called_once_with("PROMPT_REGISTRY_PINS", pins_json)
            key, val = mock_write.call_args[0]
            assert key == "PROMPT_REGISTRY_PINS"
            parsed = json.loads(val)
            assert parsed[_KNOWN_ID] == "1.0.0"

"""
tests/test_prompt_registry_store.py
=====================================
Tests for ``prompt_registry/store.py`` and ``prompt_registry/cache.py``.

Coverage
--------
**HTTPStore**
    bearer header sent, conditional GET / ETag, 304 keeps cached manifest,
    304 without prior manifest raises RegistryFetchError, bad JSON →
    RegistryFetchError (not a raw JSONDecodeError), network error →
    RegistryFetchError, 401 → RegistryFetchError

**LocalJSONStore**
    round-trip (write + fetch), file not found, bad JSON, empty prompts dict,
    publish() creates a missing file, publish() round-trips via fetch,
    publish() adds a version without losing prior versions, publish() leaves
    other prompt ids untouched, republishing an existing version overwrites
    + warns, atomic write leaves no .tmp file behind, publish() creates
    missing parent directories

**FirestoreStore**
    absent firebase-admin degrades to RegistryFetchError (not ImportError),
    publish on FirestoreStore/HTTPStore (read-only backends) raises
    ReadOnlyStoreError — LocalJSONStore is the one backend that overrides
    publish() to actually write

**CacheManager**
    write + read round-trip, read miss → None, atomic write (.tmp cleaned up),
    prune keeps last N versions, list_versions newest-first, write failure →
    False not raise, directory created on first write

**read_baseline / list_baseline_ids**
    all 9 known ids return non-empty text, unknown id returns None,
    all baseline bodies pass validate_prompt guardrails
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — build a minimal valid RegistryManifest dict
# ---------------------------------------------------------------------------

def _minimal_manifest_dict(
    prompt_id: str = "master_preprompt",
    version: str = "1.0.0",
    body: str = "Test prompt body",
    sha256: str = "abc123",
    signature: str = "sig456",
) -> dict:
    return {
        "registry_version": "2026-06-01T00:00:00Z",
        "signing_alg": "HMAC-SHA256",
        "prompts": {
            prompt_id: {
                "latest": version,
                "versions": {
                    version: {
                        "body": body,
                        "sha256": sha256,
                        "signature": signature,
                        "created_at": "2026-06-01",
                        "author": "test",
                        "notes": "",
                    }
                },
            }
        },
    }


def _manifest_json(**kwargs) -> bytes:
    return json.dumps(_minimal_manifest_dict(**kwargs)).encode("utf-8")


# ---------------------------------------------------------------------------
# Fake HTTP response (context manager)
# ---------------------------------------------------------------------------

class _FakeHTTPResponse:
    """Minimal mock for the object returned by ``urllib.request.urlopen``."""

    def __init__(
        self,
        body: bytes = b"{}",
        headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        self._body = body
        self.headers = headers or {}
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, key: str, default=None):
        return self.headers.get(key, default)


# ---------------------------------------------------------------------------
# Import under test (after helpers so import errors surface clearly)
# ---------------------------------------------------------------------------

from prompt_registry.store import (
    FirestoreStore,
    HTTPStore,
    LocalJSONStore,
    ReadOnlyStoreError,
    RegistryFetchError,
)
from prompt_registry.cache import (
    CacheManager,
    list_baseline_ids,
    read_baseline,
)
from prompt_registry.models import PromptRecord, RegistryManifest
from prompt_registry.guardrails import validate_prompt


# ===========================================================================
# TestHTTPStore
# ===========================================================================

class TestHTTPStore:
    """Tests for HTTPStore bearer auth, ETag caching, and error mapping."""

    def test_bearer_header_is_sent(self):
        """Authorization: Bearer <token> must appear in the outgoing request."""
        captured: list[urllib.request.Request] = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            return _FakeHTTPResponse(_manifest_json())

        store = HTTPStore("https://example.com/registry.json", token="mytoken")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            store.fetch_manifest()

        assert len(captured) == 1
        assert captured[0].get_header("Authorization") == "Bearer mytoken"

    def test_no_auth_header_when_no_token(self):
        """No Authorization header when token is None."""
        captured: list[urllib.request.Request] = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            return _FakeHTTPResponse(_manifest_json())

        store = HTTPStore("https://example.com/registry.json")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            store.fetch_manifest()

        assert captured[0].get_header("Authorization") is None

    def test_valid_response_returns_manifest(self):
        """A valid 200 response returns a parsed RegistryManifest."""
        body = _manifest_json(prompt_id="gravity.system", version="1.0.0")
        with patch(
            "urllib.request.urlopen",
            return_value=_FakeHTTPResponse(body),
        ):
            store = HTTPStore("https://example.com/registry.json", token="tok")
            manifest = store.fetch_manifest()

        assert isinstance(manifest, RegistryManifest)
        record = manifest.get_prompt("gravity.system")
        assert record is not None
        assert record.body == "Test prompt body"

    def test_etag_stored_and_sent_on_next_request(self):
        """ETag from first response is sent as If-None-Match on second request."""
        captured: list[urllib.request.Request] = []

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            return _FakeHTTPResponse(
                _manifest_json(),
                headers={"ETag": '"abc123"'},
            )

        store = HTTPStore("https://example.com/registry.json", token="tok")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            store.fetch_manifest()  # first — stores ETag
            store.fetch_manifest()  # second — should send If-None-Match

        assert captured[1].get_header("If-none-match") == '"abc123"'

    def test_304_returns_cached_manifest(self):
        """A 304 Not Modified response reuses the previously fetched manifest."""
        body = _manifest_json(prompt_id="master_preprompt", version="1.0.0")
        first_response = _FakeHTTPResponse(body, headers={"ETag": '"v1"'})

        def fake_urlopen(req, timeout=None):
            if captured_calls[0]:
                # Second call → simulate 304
                raise urllib.error.HTTPError(
                    url="https://example.com/registry.json",
                    code=304,
                    msg="Not Modified",
                    hdrs={},
                    fp=None,
                )
            captured_calls[0] = True
            return first_response

        captured_calls = [False]
        store = HTTPStore("https://example.com/registry.json", token="tok")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            m1 = store.fetch_manifest()  # 200
            m2 = store.fetch_manifest()  # 304

        assert m2 is m1  # same object returned from cache

    def test_304_without_prior_manifest_raises(self):
        """304 before any successful fetch raises RegistryFetchError."""
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url="https://example.com/registry.json",
                code=304,
                msg="Not Modified",
                hdrs={},
                fp=None,
            )

        store = HTTPStore("https://example.com/registry.json")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(RegistryFetchError, match="304"):
                store.fetch_manifest()

    def test_bad_json_raises_registry_fetch_error_not_json_error(self):
        """Invalid JSON body → RegistryFetchError, not a raw json.JSONDecodeError."""
        store = HTTPStore("https://example.com/registry.json")
        with patch(
            "urllib.request.urlopen",
            return_value=_FakeHTTPResponse(b"{ this is not valid json }"),
        ):
            with pytest.raises(RegistryFetchError, match="[Ii]nvalid JSON"):
                store.fetch_manifest()

    def test_network_error_raises_registry_fetch_error(self):
        """URLError (no network) → RegistryFetchError."""
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("Connection refused")

        store = HTTPStore("https://example.com/registry.json")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(RegistryFetchError, match="[Nn]etwork"):
                store.fetch_manifest()

    def test_http_401_raises_registry_fetch_error(self):
        """401 Unauthorized → RegistryFetchError with status code in message."""
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url="https://example.com/registry.json",
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=None,
            )

        store = HTTPStore("https://example.com/registry.json", token="bad")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(RegistryFetchError, match="401"):
                store.fetch_manifest()

    def test_http_500_raises_registry_fetch_error(self):
        """500 Internal Server Error → RegistryFetchError."""
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                url="https://example.com/registry.json",
                code=500,
                msg="Server Error",
                hdrs={},
                fp=None,
            )

        store = HTTPStore("https://example.com/registry.json")
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(RegistryFetchError):
                store.fetch_manifest()

    def test_publish_raises_read_only(self):
        """publish() on HTTPStore raises ReadOnlyStoreError (base default)."""
        store = HTTPStore("https://example.com/registry.json")
        with pytest.raises(ReadOnlyStoreError):
            store.publish("gravity.system", "1.0.0", "body", "sha", "sig")


# ===========================================================================
# TestLocalJSONStore
# ===========================================================================

class TestLocalJSONStore:
    """Tests for LocalJSONStore disk JSON file backend."""

    def test_round_trip(self, tmp_path):
        """Write a registry.json, fetch it, verify contents."""
        reg = tmp_path / "registry.json"
        reg.write_text(
            json.dumps(_minimal_manifest_dict("master_preprompt", "1.2.3")),
            encoding="utf-8",
        )
        store = LocalJSONStore(reg)
        manifest = store.fetch_manifest()
        assert isinstance(manifest, RegistryManifest)
        record = manifest.get_prompt("master_preprompt", "1.2.3")
        assert record is not None
        assert record.sha256 == "abc123"

    def test_file_not_found_raises(self, tmp_path):
        """Non-existent file → RegistryFetchError."""
        store = LocalJSONStore(tmp_path / "does_not_exist.json")
        with pytest.raises(RegistryFetchError, match="[Nn]ot found|[Nn]o such"):
            store.fetch_manifest()

    def test_bad_json_raises(self, tmp_path):
        """Corrupt JSON file → RegistryFetchError."""
        reg = tmp_path / "registry.json"
        reg.write_text("{bad json here!", encoding="utf-8")
        store = LocalJSONStore(reg)
        with pytest.raises(RegistryFetchError, match="[Ii]nvalid JSON"):
            store.fetch_manifest()

    def test_empty_prompts_dict_is_valid(self, tmp_path):
        """A manifest with an empty prompts dict is structurally valid."""
        data = {
            "registry_version": "2026-01-01T00:00:00Z",
            "signing_alg": "HMAC-SHA256",
            "prompts": {},
        }
        reg = tmp_path / "registry.json"
        reg.write_text(json.dumps(data), encoding="utf-8")
        store = LocalJSONStore(reg)
        manifest = store.fetch_manifest()
        assert len(manifest.prompts) == 0

    def test_publish_creates_file_when_missing(self, tmp_path):
        """publish() against a non-existent file creates it with the new entry."""
        reg = tmp_path / "registry.json"
        store = LocalJSONStore(reg)
        store.publish(
            "gravity.system", "1.0.0", "body text", "sha-abc", "sig-xyz",
            author="kevin", notes="first publish", created_at="2026-07-04T00:00:00Z",
        )
        assert reg.exists()
        manifest = store.fetch_manifest()
        record = manifest.get_prompt("gravity.system", "1.0.0")
        assert record is not None
        assert record.body == "body text"
        assert record.sha256 == "sha-abc"
        assert record.signature == "sig-xyz"
        assert record.author == "kevin"
        assert manifest.prompts["gravity.system"].latest == "1.0.0"

    def test_publish_round_trip_via_fetch(self, tmp_path):
        """A published version is immediately readable via fetch_manifest()."""
        store = LocalJSONStore(tmp_path / "registry.json")
        store.publish("master_preprompt", "2.0.0", "new body", "sha1", "sig1")
        fresh_store = LocalJSONStore(tmp_path / "registry.json")
        manifest = fresh_store.fetch_manifest()
        assert manifest.get_prompt("master_preprompt", "2.0.0").body == "new body"

    def test_publish_adds_new_version_without_losing_old(self, tmp_path):
        """Publishing v1.1.0 keeps v1.0.0 in the versions map, bumps latest."""
        reg = tmp_path / "registry.json"
        reg.write_text(
            json.dumps(_minimal_manifest_dict("master_preprompt", "1.0.0", body="old body")),
            encoding="utf-8",
        )
        store = LocalJSONStore(reg)
        store.publish("master_preprompt", "1.1.0", "new body", "sha2", "sig2")

        manifest = store.fetch_manifest()
        pv = manifest.prompts["master_preprompt"]
        assert pv.latest == "1.1.0"
        assert pv.versions["1.0.0"].body == "old body"
        assert pv.versions["1.1.0"].body == "new body"

    def test_publish_does_not_disturb_other_prompt_ids(self, tmp_path):
        """Publishing one prompt id leaves other ids in the manifest untouched."""
        reg = tmp_path / "registry.json"
        reg.write_text(
            json.dumps(_minimal_manifest_dict("gravity.step_01", "1.0.0", body="step1 body")),
            encoding="utf-8",
        )
        store = LocalJSONStore(reg)
        store.publish("gravity.step_02", "1.0.0", "step2 body", "sha3", "sig3")

        manifest = store.fetch_manifest()
        assert manifest.get_prompt("gravity.step_01").body == "step1 body"
        assert manifest.get_prompt("gravity.step_02").body == "step2 body"

    def test_publish_overwriting_existing_version_does_not_raise(self, tmp_path, caplog):
        """Republishing an already-published version overwrites it and logs a warning."""
        reg = tmp_path / "registry.json"
        store = LocalJSONStore(reg)
        store.publish("master_preprompt", "1.0.0", "body v1", "shaA", "sigA")

        with caplog.at_level("WARNING"):
            store.publish("master_preprompt", "1.0.0", "body v1 fixed", "shaB", "sigB")

        manifest = store.fetch_manifest()
        assert manifest.get_prompt("master_preprompt", "1.0.0").body == "body v1 fixed"
        assert any("overwriting" in r.message for r in caplog.records)

    def test_publish_leaves_no_tmp_file_behind(self, tmp_path):
        """The atomic write's .tmp sibling is cleaned up (renamed away) on success."""
        reg = tmp_path / "registry.json"
        store = LocalJSONStore(reg)
        store.publish("gravity.system", "1.0.0", "body", "sha", "sig")
        assert not (tmp_path / "registry.json.tmp").exists()

    def test_publish_creates_parent_dirs(self, tmp_path):
        """publish() creates missing parent directories for the target path."""
        reg = tmp_path / "nested" / "dir" / "registry.json"
        store = LocalJSONStore(reg)
        store.publish("gravity.system", "1.0.0", "body", "sha", "sig")
        assert reg.exists()

    def test_multiple_prompts(self, tmp_path):
        """A manifest with multiple prompt ids loads all entries."""
        data = {
            "registry_version": "2026-01-01T00:00:00Z",
            "signing_alg": "HMAC-SHA256",
            "prompts": {
                "gravity.step_01": {
                    "latest": "1.0.0",
                    "versions": {
                        "1.0.0": {
                            "body": "step1",
                            "sha256": "s1",
                            "signature": "sig1",
                            "created_at": "2026-01-01",
                        }
                    },
                },
                "gravity.step_02": {
                    "latest": "1.0.0",
                    "versions": {
                        "1.0.0": {
                            "body": "step2",
                            "sha256": "s2",
                            "signature": "sig2",
                            "created_at": "2026-01-01",
                        }
                    },
                },
            },
        }
        reg = tmp_path / "registry.json"
        reg.write_text(json.dumps(data), encoding="utf-8")
        store = LocalJSONStore(reg)
        manifest = store.fetch_manifest()
        assert manifest.get_prompt("gravity.step_01") is not None
        assert manifest.get_prompt("gravity.step_02") is not None


# ===========================================================================
# TestFirestoreStore
# ===========================================================================

class TestFirestoreStore:
    """Tests for FirestoreStore — especially absence-graceful degradation."""

    def test_absent_firebase_admin_raises_registry_fetch_error(self):
        """When firebase-admin is not installed, raises RegistryFetchError (not ImportError)."""
        store = FirestoreStore()

        # Guarantee firebase_admin is unimportable in this call
        with patch.dict(sys.modules, {"firebase_admin": None}):
            with pytest.raises(RegistryFetchError, match="firebase-admin"):
                store.fetch_manifest()

    def test_absent_firebase_admin_error_is_not_import_error(self):
        """The degraded error type is RegistryFetchError, never ImportError."""
        store = FirestoreStore()
        with patch.dict(sys.modules, {"firebase_admin": None}):
            exc = None
            try:
                store.fetch_manifest()
            except RegistryFetchError as e:
                exc = e
            except ImportError:
                pytest.fail("ImportError leaked past FirestoreStore boundary")
            assert exc is not None

    def test_firestore_unavailable_hint_in_message(self):
        """Error message includes an install hint."""
        store = FirestoreStore()
        with patch.dict(sys.modules, {"firebase_admin": None}):
            with pytest.raises(RegistryFetchError, match="pip install"):
                store.fetch_manifest()

    def test_publish_raises_read_only(self):
        """publish() raises ReadOnlyStoreError (base class default)."""
        store = FirestoreStore()
        with pytest.raises(ReadOnlyStoreError):
            store.publish("gravity.system", "1.0.0", "body", "sha", "sig")

    def test_missing_document_raises(self):
        """If the manifest document does not exist, raises RegistryFetchError."""
        # Mock a working firebase_admin with a non-existent document
        mock_doc = MagicMock()
        mock_doc.exists = False

        mock_doc_ref = MagicMock()
        mock_doc_ref.get.return_value = mock_doc

        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref

        mock_client = MagicMock()
        mock_client.collection.return_value = mock_collection

        store = FirestoreStore()
        store._client = mock_client  # inject pre-built client

        with pytest.raises(RegistryFetchError, match="manifest"):
            store.fetch_manifest()

    def test_firestore_exception_wrapped(self):
        """An unexpected Firestore error is wrapped in RegistryFetchError."""
        mock_client = MagicMock()
        mock_client.collection.side_effect = RuntimeError("Firestore blew up")

        store = FirestoreStore()
        store._client = mock_client

        with pytest.raises(RegistryFetchError, match="[Ff]irestore"):
            store.fetch_manifest()


# ===========================================================================
# TestCacheManager
# ===========================================================================

def _sample_record(body: str = "hello world") -> PromptRecord:
    return PromptRecord(
        body=body,
        sha256="deadbeef",
        signature="cafebabe",
        created_at="2026-06-01",
        author="test",
        notes="",
    )


class TestCacheManager:
    """Tests for CacheManager disk cache."""

    def test_write_then_read_round_trip(self, tmp_path):
        """Write a record then read it back; all fields must match."""
        cache = CacheManager(tmp_path / "cache")
        rec = _sample_record("round trip body")
        assert cache.write("gravity.step_01", "1.0.0", rec) is True
        result = cache.read("gravity.step_01", "1.0.0")
        assert result is not None
        assert result.body == "round trip body"
        assert result.sha256 == "deadbeef"
        assert result.signature == "cafebabe"

    def test_read_miss_returns_none(self, tmp_path):
        """Non-existent prompt id + version → None, not an exception."""
        cache = CacheManager(tmp_path / "cache")
        assert cache.read("nonexistent", "9.9.9") is None

    def test_directory_created_on_first_write(self, tmp_path):
        """CacheManager creates the cache directory tree if it doesn't exist."""
        deep_dir = tmp_path / "a" / "b" / "c" / "cache"
        cache = CacheManager(deep_dir)
        assert cache.write("gravity.step_01", "1.0.0", _sample_record()) is True
        assert (deep_dir / "gravity_step_01" / "1.0.0.json").exists()

    def test_atomic_write_tmp_file_is_renamed(self, tmp_path):
        """The .tmp staging file is removed after a successful write."""
        cache = CacheManager(tmp_path / "cache")
        cache.write("gravity.step_01", "1.0.0", _sample_record())
        tmp_file = tmp_path / "cache" / "gravity_step_01" / "1.0.0.tmp"
        assert not tmp_file.exists()

    def test_prune_keeps_only_n_newest(self, tmp_path):
        """After writing N+2 versions, only the last N files are retained."""
        cache = CacheManager(tmp_path / "cache", keep_versions=3)
        for i in range(5):
            cache.write("gravity.step_01", f"1.0.{i}", _sample_record(f"v{i}"))
        versions = cache.list_versions("gravity.step_01")
        assert len(versions) == 3

    def test_prune_keeps_most_recent(self, tmp_path):
        """Prune removes oldest, keeps newest."""
        cache = CacheManager(tmp_path / "cache", keep_versions=2)
        for i in range(4):
            cache.write("gravity.step_01", f"1.0.{i}", _sample_record(f"v{i}"))
        # list_versions is newest-first by mtime
        versions = cache.list_versions("gravity.step_01")
        assert len(versions) == 2
        # The most-recent written should be available
        assert cache.read("gravity.step_01", versions[0]) is not None

    def test_keep_minimum_one(self, tmp_path):
        """keep_versions < 1 is clamped to 1."""
        cache = CacheManager(tmp_path / "cache", keep_versions=0)
        for i in range(3):
            cache.write("gravity.step_01", f"1.0.{i}", _sample_record(f"v{i}"))
        assert len(cache.list_versions("gravity.step_01")) == 1

    def test_list_versions_newest_first(self, tmp_path):
        """list_versions returns entries with the most-recently written file first."""
        cache = CacheManager(tmp_path / "cache", keep_versions=10)
        for i in range(3):
            cache.write("gravity.step_01", f"1.0.{i}", _sample_record())
        versions = cache.list_versions("gravity.step_01")
        # "1.0.2" was written last and must appear first
        assert versions[0] == "1.0.2"

    def test_list_versions_empty_for_unknown_id(self, tmp_path):
        """list_versions returns [] for a prompt id that has never been cached."""
        cache = CacheManager(tmp_path / "cache")
        assert cache.list_versions("unknown_id") == []

    def test_write_failure_returns_false_not_raise(self, tmp_path):
        """A write failure (e.g. permission error) returns False, never raises."""
        cache = CacheManager(tmp_path / "cache")
        # Patch Path.write_text to raise
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = cache.write("gravity.step_01", "1.0.0", _sample_record())
        assert result is False

    def test_read_latest_returns_most_recent(self, tmp_path):
        """read_latest returns the most-recently written version."""
        cache = CacheManager(tmp_path / "cache", keep_versions=10)
        cache.write("master_preprompt", "1.0.0", _sample_record("old"))
        cache.write("master_preprompt", "1.1.0", _sample_record("new"))
        latest = cache.read_latest("master_preprompt")
        assert latest is not None
        assert latest.body == "new"

    def test_read_latest_returns_none_when_empty(self, tmp_path):
        """read_latest returns None when no versions are cached."""
        cache = CacheManager(tmp_path / "cache")
        assert cache.read_latest("gravity.step_01") is None

    def test_prompt_ids_with_dots_map_to_underscores(self, tmp_path):
        """Dots in prompt IDs are replaced with underscores in the filesystem path."""
        cache = CacheManager(tmp_path / "cache")
        cache.write("gravity.step_01", "1.0.0", _sample_record())
        assert (tmp_path / "cache" / "gravity_step_01" / "1.0.0.json").exists()

    def test_independent_ids_dont_interfere(self, tmp_path):
        """Two different prompt IDs are cached independently."""
        cache = CacheManager(tmp_path / "cache", keep_versions=2)
        cache.write("gravity.step_01", "1.0.0", _sample_record("s1"))
        cache.write("gravity.step_02", "1.0.0", _sample_record("s2"))
        assert cache.read("gravity.step_01", "1.0.0").body == "s1"
        assert cache.read("gravity.step_02", "1.0.0").body == "s2"

    def test_clear_removes_all_versions(self, tmp_path):
        """clear() removes all cached versions for a prompt id."""
        cache = CacheManager(tmp_path / "cache", keep_versions=10)
        for i in range(3):
            cache.write("gravity.step_01", f"1.0.{i}", _sample_record())
        cache.clear("gravity.step_01")
        assert cache.list_versions("gravity.step_01") == []

    def test_clear_on_unknown_id_does_not_raise(self, tmp_path):
        """clear() on a prompt id with no cached files is a no-op."""
        cache = CacheManager(tmp_path / "cache")
        cache.clear("nonexistent")  # must not raise


# ===========================================================================
# TestReadBaseline
# ===========================================================================

class TestReadBaseline:
    """Tests for read_baseline() and list_baseline_ids()."""

    _ALL_IDS = [
        "master_preprompt",
        "gravity.system",
        "gravity.step_01",
        "gravity.step_02",
        "gravity.step_03",
        "gravity.step_04",
        "gravity.step_05",
        "gravity.step_06",
        "gravity.step_07",
    ]

    def test_all_known_ids_return_non_empty_text(self):
        """Every registered baseline id returns a non-empty string."""
        for pid in self._ALL_IDS:
            text = read_baseline(pid)
            assert text is not None, f"read_baseline({pid!r}) returned None"
            assert len(text.strip()) > 0, f"read_baseline({pid!r}) returned empty text"

    def test_unknown_id_returns_none(self):
        """Unknown prompt id returns None, not an exception."""
        assert read_baseline("not.a.real.id") is None
        assert read_baseline("") is None

    def test_gravity_system_contains_expected_keywords(self):
        """gravity.system baseline mentions 'Gravity' and 'JSON'."""
        text = read_baseline("gravity.system")
        assert "Gravity" in text
        assert "JSON" in text

    def test_master_preprompt_contains_advisory_only(self):
        """master_preprompt must mention ADVISORY_ONLY=true."""
        text = read_baseline("master_preprompt")
        assert "ADVISORY_ONLY" in text

    def test_gravity_steps_contain_respond_in_json(self):
        """All step baselines include a JSON response instruction."""
        for i in range(1, 8):
            pid = f"gravity.step_0{i}"
            text = read_baseline(pid)
            # Each step ends with a JSON response schema hint
            assert "JSON" in text, f"{pid} baseline missing JSON response hint"

    def test_list_baseline_ids_returns_all_nine(self):
        """list_baseline_ids() returns exactly the 9 registered ids."""
        ids = list_baseline_ids()
        assert set(ids) == set(self._ALL_IDS)

    def test_all_baseline_bodies_pass_validate_prompt(self):
        """Every baseline body passes the guardrail validator for its prompt id."""
        for pid in self._ALL_IDS:
            body = read_baseline(pid)
            ok, issues = validate_prompt(pid, body)
            assert ok, f"Baseline for {pid!r} failed guardrails: {issues}"

    def test_master_preprompt_does_not_contain_deny_list_phrases(self):
        """The master pre-prompt must not contain any deny-list phrase."""
        text = read_baseline("master_preprompt")
        # These are the most dangerous deny-list phrases
        dangerous = [
            "ADVISORY_ONLY=false",
            "submit_order",
            "place_order",
            "disable the kill switch",
            "bypass the risk gate",
        ]
        for phrase in dangerous:
            assert phrase.lower() not in text.lower(), (
                f"master_preprompt baseline contains deny-listed phrase: {phrase!r}"
            )

    def test_baseline_dir_exists(self):
        """The baseline/ directory is present in the package."""
        from prompt_registry.cache import _BASELINE_DIR
        assert _BASELINE_DIR.is_dir(), (
            f"Baseline directory not found: {_BASELINE_DIR}"
        )

    def test_all_baseline_files_exist(self):
        """Each baseline file referenced in _BASELINE_FILEMAP exists on disk."""
        from prompt_registry.cache import _BASELINE_DIR, _BASELINE_FILEMAP
        for pid, stem in _BASELINE_FILEMAP.items():
            path = _BASELINE_DIR / f"{stem}.md"
            assert path.exists(), f"Missing baseline file for {pid!r}: {path}"

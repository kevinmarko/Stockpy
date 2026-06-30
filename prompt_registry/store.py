"""
prompt_registry/store.py
=========================
Storage backends for the remote Prompt Registry.

Architecture
------------
``PromptStore`` is an ABC (identical pattern to ``IDataProvider`` in
``data_engine.py``).  Three concrete stores ship; pick via
``settings.PROMPT_REGISTRY_BACKEND``:

* **``LocalJSONStore``** — reads a ``registry.json`` file on disk.  Zero
  network.  Good for offline dev and single-machine setups.
* **``HTTPStore``** — fetches over HTTPS using ``urllib.request`` (stdlib, zero
  new dependencies).  Sends ``Authorization: Bearer <token>`` and
  ``If-None-Match`` / ETag for cheap conditional GETs.
* **``FirestoreStore``** — optional; only usable when ``firebase-admin`` is
  installed.  Degrades cleanly to ``RegistryFetchError`` when the package is
  absent (CONSTRAINT #6 — never crashes the import).

Exceptions
----------
``RegistryFetchError``
    Raised by ``fetch_manifest()`` on *any* failure: network, auth, bad JSON,
    Firestore unavailable, etc.  Callers must fall through to cache or baseline
    (CONSTRAINT #4 — never return an empty prompt).
``ReadOnlyStoreError``
    Raised by ``publish()`` when write credentials are absent.  The platform's
    *read* path never needs publish creds, so this is structurally enforced at
    the method level.

Zero new dependencies
---------------------
``LocalJSONStore`` and ``HTTPStore`` use only stdlib (``json``, ``pathlib``,
``urllib.request``).  ``FirestoreStore`` lazy-imports ``firebase_admin`` inside
``fetch_manifest()`` so an absent package is a ``RegistryFetchError``, not an
``ImportError`` at module load time.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union

from prompt_registry.models import RegistryManifest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RegistryFetchError(Exception):
    """Raised by :meth:`PromptStore.fetch_manifest` on any failure.

    The caller (``registry.py``) catches this and falls through to the next
    resolution rung (cache → baseline) — it never propagates past the
    registry boundary.
    """


class ReadOnlyStoreError(Exception):
    """Raised by :meth:`PromptStore.publish` when write credentials are absent.

    The platform runtime never has publish credentials; only the authoring
    machine does.  Callers that don't intend to publish should never call
    ``publish()`` at all, but the exception is the structural guard.
    """


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class PromptStore(ABC):
    """Abstract interface for all registry storage backends.

    Mirroring the ``IDataProvider`` pattern from ``data_engine.py``: the
    concrete backend is swappable; all callers type-annotate against this ABC.
    """

    @abstractmethod
    def fetch_manifest(self) -> RegistryManifest:
        """Fetch and return the full registry manifest.

        Raises
        ------
        RegistryFetchError
            On *any* failure (network, auth, parse, Firestore unavailable).
            Never returns ``None``; never raises any other exception type.
        """

    def publish(
        self,
        prompt_id: str,
        version: str,
        body: str,
        sha256: str,
        signature: str,
        *,
        author: str = "",
        notes: str = "",
        created_at: str = "",
    ) -> None:
        """Publish a new signed prompt version to the remote store.

        Default implementation raises :exc:`ReadOnlyStoreError`.  Override in
        stores that support writing (e.g. ``HTTPStore`` with a publish token).

        Raises
        ------
        ReadOnlyStoreError
            When write credentials are absent (the default for all stores).
        """
        raise ReadOnlyStoreError(
            f"{type(self).__name__} does not support publishing. "
            "Set PROMPT_REGISTRY_PUBLISH_TOKEN to enable writes."
        )


# ---------------------------------------------------------------------------
# LocalJSONStore
# ---------------------------------------------------------------------------

class LocalJSONStore(PromptStore):
    """Read a ``registry.json`` file from the local filesystem.

    Good for: offline development, CI, and single-machine deployments where
    the operator maintains the manifest by hand.

    Parameters
    ----------
    path:
        Path to the ``registry.json`` file (absolute or relative to CWD).
    """

    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)

    def fetch_manifest(self) -> RegistryManifest:
        """Read and parse the local registry JSON file.

        Raises
        ------
        RegistryFetchError
            When the file is missing, unreadable, or contains invalid JSON.
        """
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            logger.debug("LocalJSONStore: loaded registry from %s", self._path)
            return RegistryManifest.from_dict(data)
        except FileNotFoundError as exc:
            raise RegistryFetchError(
                f"Registry file not found: {self._path}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise RegistryFetchError(
                f"Invalid JSON in local registry ({self._path}): {exc}"
            ) from exc
        except RegistryFetchError:
            raise
        except Exception as exc:
            raise RegistryFetchError(
                f"Failed to load local registry from {self._path}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# HTTPStore
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT_SECONDS: int = 15
"""Network timeout for registry fetches.  Keeps launch latency bounded."""


class HTTPStore(PromptStore):
    """Fetch the registry manifest from a protected HTTPS endpoint.

    Uses stdlib ``urllib.request`` only — zero new dependencies.

    Features
    --------
    * **Bearer auth** — ``Authorization: Bearer <token>`` header when a token
      is provided.
    * **Conditional GET** — stores the ETag from the last successful response
      and sends ``If-None-Match`` on subsequent fetches.  A 304 response
      reuses the in-memory cached manifest without parsing.
    * **Typed errors** — any network/auth/parse failure becomes a
      ``RegistryFetchError`` rather than a raw ``urllib`` or ``json``
      exception leaking to the caller.

    Parameters
    ----------
    url:
        HTTPS URL for the signed ``registry.json`` manifest.
    token:
        Optional bearer token (``PROMPT_REGISTRY_TOKEN``).  The URL should
        be a private endpoint; the token adds a second auth layer.
    timeout:
        HTTP timeout in seconds (default 15).
    """

    def __init__(
        self,
        url: str,
        token: Optional[str] = None,
        *,
        timeout: int = _HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout
        # In-memory ETag cache — resets on process restart (intentional).
        self._last_etag: Optional[str] = None
        self._cached_manifest: Optional[RegistryManifest] = None

    def fetch_manifest(self) -> RegistryManifest:
        """Fetch the registry manifest via HTTPS with conditional GET.

        On a 304 Not Modified response, the previously parsed manifest is
        returned without any JSON parsing.

        Raises
        ------
        RegistryFetchError
            On network errors, non-2xx/304 HTTP status, or invalid JSON.
        """
        req = urllib.request.Request(self._url)

        # Bearer auth
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")

        # Conditional GET
        if self._last_etag:
            req.add_header("If-None-Match", self._last_etag)

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                etag = resp.headers.get("ETag")
                raw = resp.read().decode("utf-8")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RegistryFetchError(
                    f"Remote registry returned invalid JSON: {exc}"
                ) from exc

            manifest = RegistryManifest.from_dict(data)
            self._cached_manifest = manifest
            if etag:
                self._last_etag = etag
            logger.debug(
                "HTTPStore: fetched manifest from %s (ETag=%s)", self._url, etag
            )
            return manifest

        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                # Not Modified — reuse in-memory manifest
                if self._cached_manifest is not None:
                    logger.debug(
                        "HTTPStore: 304 Not Modified — reusing cached manifest"
                    )
                    return self._cached_manifest
                raise RegistryFetchError(
                    "Remote registry returned 304 Not Modified but no manifest "
                    "is cached in this process (first request cannot be 304)"
                ) from exc
            raise RegistryFetchError(
                f"HTTP error {exc.code} fetching registry from {self._url}: {exc.reason}"
            ) from exc

        except urllib.error.URLError as exc:
            raise RegistryFetchError(
                f"Network error fetching registry from {self._url}: {exc.reason}"
            ) from exc

        except RegistryFetchError:
            raise

        except Exception as exc:
            raise RegistryFetchError(
                f"Unexpected error fetching registry from {self._url}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# FirestoreStore (optional, lazy import)
# ---------------------------------------------------------------------------

class FirestoreStore(PromptStore):
    """Read the registry manifest from a Firestore document.

    ``firebase-admin`` is an optional dependency — if it is not installed,
    :meth:`fetch_manifest` raises :exc:`RegistryFetchError` with a clear
    install hint rather than an ``ImportError`` (CONSTRAINT #6).

    The package is imported **inside** ``fetch_manifest()`` on first call so
    the module loads cleanly even when ``firebase-admin`` is absent.

    Parameters
    ----------
    collection:
        Firestore collection name (default ``"prompt_registry"``).
    credentials_path:
        Path to a service-account JSON file.  When ``None``, Application
        Default Credentials (ADC) are used.
    """

    def __init__(
        self,
        collection: str = "prompt_registry",
        credentials_path: Optional[str] = None,
    ) -> None:
        self._collection = collection
        self._credentials_path = credentials_path
        self._client = None  # lazy-initialised on first fetch

    def _ensure_client(self) -> None:
        """Initialise the Firestore client on first use.

        Raises
        ------
        RegistryFetchError
            When ``firebase-admin`` is not installed.
        """
        if self._client is not None:
            return
        try:
            import firebase_admin  # type: ignore[import]
            from firebase_admin import credentials as fb_creds  # type: ignore[import]
            from firebase_admin import firestore as fb_fs  # type: ignore[import]

            if not firebase_admin._apps:
                if self._credentials_path:
                    cred = fb_creds.Certificate(self._credentials_path)
                else:
                    cred = fb_creds.ApplicationDefault()
                firebase_admin.initialize_app(cred)

            self._client = fb_fs.client()
            logger.debug("FirestoreStore: Firestore client initialised")

        except ImportError as exc:
            raise RegistryFetchError(
                "firebase-admin is not installed. "
                "Install it with: pip install firebase-admin"
            ) from exc

    def fetch_manifest(self) -> RegistryManifest:
        """Fetch the registry manifest from Firestore.

        Raises
        ------
        RegistryFetchError
            When ``firebase-admin`` is absent, credentials are invalid, the
            document is missing, or the document cannot be parsed.
        """
        try:
            self._ensure_client()

            doc_ref = self._client.collection(self._collection).document("manifest")
            doc = doc_ref.get()
            if not doc.exists:
                raise RegistryFetchError(
                    f"No 'manifest' document found in Firestore collection "
                    f"'{self._collection}'"
                )
            return RegistryManifest.from_dict(doc.to_dict())

        except RegistryFetchError:
            raise
        except Exception as exc:
            raise RegistryFetchError(
                f"Firestore fetch failed for collection '{self._collection}': {exc}"
            ) from exc

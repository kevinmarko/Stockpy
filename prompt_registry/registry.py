"""
prompt_registry/registry.py
============================
Resolution orchestration for the Prompt Registry.

``PromptRegistry`` is the single public entry-point for all prompt lookups.
It implements the §1 resolution chain from ``docs/PROMPT_REGISTRY_PLAN.md``:

    Pin  →  Remote latest (verified)  →  Disk cache (verified)  →  Baseline

Each rung may be skipped (silently logged at WARNING / DEBUG) on any
failure; the next rung is tried immediately.  The committed
``prompt_registry/baseline/*.md`` files are always present for the known
prompt IDs, so the chain **never** returns an empty string (CONSTRAINT #4).

Security gates
--------------
A remote or cached record is **adopted** only after two independent checks
run inside :meth:`_safe_adopt`:

1. ``signing.verify(body, signature, key)`` — constant-time HMAC-SHA256.
   Skipped when no ``PROMPT_REGISTRY_SIGNING_KEY`` is configured (appropriate
   for ``LocalJSONStore`` offline dev use).
2. ``guardrails.validate_prompt(prompt_id, body)`` — deny-list, size, and
   required-marker checks.  These run even on locally-stored records so a
   tampered-on-disk body is still rejected.

A failed gate discards the record, logs a CRITICAL entry, and fires an
alert via ``observability.alerts.send_alert`` (lazily imported — the
``prompt_registry`` package has no hard dependency on observability).
Resolution immediately falls through to the next rung.

On-demand (CONSTRAINT #5)
--------------------------
:meth:`sync` is called explicitly — once at entry-point launch and on the
GUI "🔄 Sync prompts" button.  The singleton never starts a background
thread or a timer.

Module-level singleton
-----------------------
:func:`get_registry` returns a process-wide ``PromptRegistry`` built from
environment variables.  :func:`reset_registry` clears it (used by tests).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Optional, Union

from prompt_registry.cache import CacheManager, read_baseline
from prompt_registry.guardrails import validate_prompt
from prompt_registry.models import PromptRecord, RegistryManifest
from prompt_registry.signing import verify
from prompt_registry.store import (
    HTTPStore,
    LocalJSONStore,
    FirestoreStore,
    PromptStore,
    RegistryFetchError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel string (CONSTRAINT #4 — never return an empty body)
# ---------------------------------------------------------------------------

_UNAVAILABLE_SENTINEL = "[PROMPT UNAVAILABLE: {prompt_id}]"


# ---------------------------------------------------------------------------
# PromptRegistry
# ---------------------------------------------------------------------------


class PromptRegistry:
    """Resolution-chain orchestrator for versioned, signed prompt bodies.

    Parameters
    ----------
    store:
        Backend used by :meth:`sync` to fetch the remote manifest.  When
        ``None`` the remote / pin-from-manifest rungs are always skipped;
        only cache and baseline are used.
    cache:
        :class:`~prompt_registry.cache.CacheManager` instance.  Defaults to
        the standard ``output/prompt_cache/`` directory with ``keep=5``.
    signing_key:
        Symmetric HMAC-SHA256 key (hex or raw string) for signature checks.
        When ``None`` signature verification is skipped — appropriate for
        offline ``LocalJSONStore`` dev setups.
    pins:
        Mapping of ``{prompt_id: version}`` that forces specific versions.
        Updated in-memory by :meth:`rollback`.
    enabled:
        Master switch.  When ``False`` the remote store is never contacted
        and all resolution falls straight through to cache → baseline.
    """

    def __init__(
        self,
        store: Optional[PromptStore] = None,
        cache: Optional[CacheManager] = None,
        *,
        signing_key: Optional[str] = None,
        pins: Optional[Dict[str, str]] = None,
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._cache = cache if cache is not None else CacheManager()
        self._signing_key = signing_key or None
        self._pins: Dict[str, str] = dict(pins) if pins else {}
        self._enabled = enabled
        self._manifest: Optional[RegistryManifest] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, prompt_id: str, default: Optional[str] = None) -> str:
        """Return a prompt body via the §1 resolution chain.

        Resolution order:

        1. **Pin** — ``self._pins[prompt_id]`` version, searched in the
           in-memory manifest first, then the disk cache.
        2. **Remote latest** — the ``latest`` version from the last
           :meth:`sync` call (in-memory manifest), if verified + clean.
        3. **Disk cache** — the most recently written signed version.
        4. **Baseline** — the committed ``prompt_registry/baseline/*.md``.
        5. *default* parameter — caller-supplied last resort for unknown ids.
        6. **Sentinel** — a non-empty ``"[PROMPT UNAVAILABLE: …]"`` string
           so callers never receive an empty body (CONSTRAINT #4).

        Parameters
        ----------
        prompt_id:
            Registry key, e.g. ``"gravity.system"`` or ``"master_preprompt"``.
        default:
            Returned when *prompt_id* has no baseline file and no cached /
            remote version.  Typical use: pass the current hardcoded literal
            so behavior is byte-identical when the registry is unconfigured.

        Returns
        -------
        str
            A non-empty prompt body.  Never ``""`` (CONSTRAINT #4).
        """
        # ── Rung 1: Pin ────────────────────────────────────────────────────
        if prompt_id in self._pins:
            pinned_version = self._pins[prompt_id]
            record: Optional[PromptRecord] = None

            # Look in the in-memory manifest first (fastest after a sync)
            if self._manifest is not None:
                record = self._manifest.get_prompt(prompt_id, pinned_version)

            # Fall back to disk cache for the pinned version
            if record is None:
                record = self._cache.read(prompt_id, pinned_version)

            if record is not None:
                body = self._safe_adopt(prompt_id, pinned_version, record, "pin")
                if body is not None:
                    return body
            else:
                logger.warning(
                    "get(%r): pinned version %r not found in manifest or cache — falling through",
                    prompt_id, pinned_version,
                )

        # ── Rung 2: Remote latest (in-memory manifest from last sync) ──────
        if self._enabled and self._manifest is not None:
            pv = self._manifest.prompts.get(prompt_id)
            if pv is not None:
                latest_version = pv.latest
                record = pv.get_record()  # None version → latest
                if record is not None:
                    body = self._safe_adopt(
                        prompt_id, latest_version, record, "remote:latest"
                    )
                    if body is not None:
                        return body

        # ── Rung 3: Disk cache (latest written signed version) ─────────────
        cached_versions = self._cache.list_versions(prompt_id)
        if cached_versions:
            record = self._cache.read(prompt_id, cached_versions[0])
            if record is not None:
                body = self._safe_adopt(
                    prompt_id, cached_versions[0], record, "cache"
                )
                if body is not None:
                    return body

        # ── Rung 4: Baseline (committed .md files) ─────────────────────────
        baseline_body = read_baseline(prompt_id)
        if baseline_body is not None:
            logger.info("get(%r): using committed baseline fallback", prompt_id)
            return baseline_body

        # ── Rung 5: Caller-supplied default ────────────────────────────────
        if default is not None:
            logger.info("get(%r): using caller-supplied default", prompt_id)
            return default

        # ── Rung 6: Sentinel (CONSTRAINT #4 — never empty) ─────────────────
        sentinel = _UNAVAILABLE_SENTINEL.format(prompt_id=prompt_id)
        logger.critical(
            "get(%r): ALL resolution rungs exhausted — returning sentinel", prompt_id
        )
        return sentinel

    def sync(self) -> bool:
        """Fetch the remote manifest and populate the in-memory + disk cache.

        Calls the configured :class:`~prompt_registry.store.PromptStore`'s
        ``fetch_manifest()``; for each prompt in the manifest the latest
        version is verified and — if clean — written to the disk cache so it
        survives a process restart.

        Returns
        -------
        bool
            ``True`` on a successful fetch; ``False`` on any failure.
            Never raises (CONSTRAINT #6).
        """
        if not self._enabled:
            logger.debug("sync(): PROMPT_REGISTRY_ENABLED=False — skipping remote fetch")
            return False

        if self._store is None:
            logger.debug("sync(): no store configured — skipping remote fetch")
            return False

        try:
            manifest = self._store.fetch_manifest()
        except RegistryFetchError as exc:
            logger.warning("sync(): remote fetch failed: %s — cache / baseline will be used", exc)
            return False
        except Exception as exc:
            logger.warning("sync(): unexpected fetch error: %s", exc)
            return False

        self._manifest = manifest
        logger.debug(
            "sync(): fetched manifest registry_version=%r with %d prompt(s)",
            manifest.registry_version, len(manifest.prompts),
        )

        # Pre-warm the disk cache with every valid prompt in the manifest
        cached_count = 0
        for pid, pv in manifest.prompts.items():
            version = pv.latest
            record = pv.get_record()   # None version → latest
            if record is None:
                continue
            body = self._safe_adopt(pid, version, record, "sync")
            if body is not None:
                self._cache.write(pid, version, record)
                cached_count += 1

        logger.info(
            "sync(): %d of %d prompt(s) verified and cached",
            cached_count, len(manifest.prompts),
        )
        return True

    def rollback(self, prompt_id: str) -> Optional[str]:
        """Roll back *prompt_id* to the previous cached version.

        Sets an in-memory pin so the next :meth:`get` call returns the older
        version.  Full persistence to ``.env`` is handled in Stage 6 via the
        CLI ``python -m prompt_registry pin`` command.

        Parameters
        ----------
        prompt_id:
            Registry key to roll back (e.g. ``"master_preprompt"``).

        Returns
        -------
        str or None
            The version string that was pinned, or ``None`` when there is no
            older version in the cache to roll back to.
        """
        versions = self._cache.list_versions(prompt_id)

        if len(versions) < 2:
            logger.warning(
                "rollback(%r): fewer than 2 cached versions available — cannot roll back",
                prompt_id,
            )
            return None

        # Determine the current version (pinned or newest cached)
        current = self._pins.get(prompt_id, versions[0])

        try:
            idx = versions.index(current)
        except ValueError:
            # Current pin is not in the cached list; treat as "at the newest"
            idx = 0

        if idx + 1 >= len(versions):
            logger.warning(
                "rollback(%r): already at the oldest cached version %r",
                prompt_id, current,
            )
            return None

        previous_version = versions[idx + 1]
        self._pins[prompt_id] = previous_version
        logger.info(
            "rollback(%r): pinned %r → %r (in-memory; use CLI 'pin' to persist)",
            prompt_id, current, previous_version,
        )
        return previous_version

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_adopt(
        self,
        prompt_id: str,
        version: str,
        record: PromptRecord,
        source: str,
    ) -> Optional[str]:
        """Verify a :class:`~prompt_registry.models.PromptRecord` and return its body.

        Runs two sequential gates:

        1. HMAC-SHA256 signature check (skipped when no ``signing_key``).
        2. :func:`~prompt_registry.guardrails.validate_prompt` guardrails.

        Returns ``None`` and calls :meth:`_reject` on the first gate failure,
        causing the caller to fall through to the next resolution rung.

        Parameters
        ----------
        prompt_id, version, source:
            Used only for logging.

        Returns
        -------
        str or None
        """
        # Gate 1: Signature (constant-time HMAC-SHA256 comparison)
        if self._signing_key is not None:
            if not verify(record.body, record.signature, self._signing_key):
                self._reject(
                    prompt_id, version, source,
                    "HMAC-SHA256 signature verification failed",
                )
                return None

        # Gate 2: Guardrails (deny-list, size, required markers)
        ok, issues = validate_prompt(prompt_id, record.body)
        if not ok:
            self._reject(
                prompt_id, version, source,
                "guardrail violation: " + "; ".join(issues),
            )
            return None

        logger.debug(
            "_safe_adopt: accepted %r@%s from source=%r", prompt_id, version, source
        )
        return record.body

    def _reject(
        self,
        prompt_id: str,
        version: str,
        source: str,
        reason: str,
    ) -> None:
        """Log a CRITICAL rejection and fire an observability alert.

        The alert uses ``observability.alerts.send_alert`` (lazily imported
        so this package never hard-depends on observability).  A failure in
        the alert path is caught and logged at DEBUG — it must not propagate
        (CONSTRAINT #6).
        """
        msg = (
            f"PROMPT REGISTRY REJECTION — "
            f"prompt_id={prompt_id!r} version={version!r} "
            f"source={source!r} reason={reason}"
        )
        logger.critical(msg)
        try:
            from observability.alerts import send_alert  # lazy import
            send_alert("CRITICAL", msg)
        except Exception as exc:  # noqa: BLE001
            logger.debug("_reject: failed to dispatch alert: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_global_registry: Optional[PromptRegistry] = None


def get_registry() -> PromptRegistry:
    """Return the process-wide :class:`PromptRegistry` singleton.

    Built lazily on first call from environment variables.  The default
    configuration (no ``PROMPT_REGISTRY_ENABLED`` set, or set to ``false``)
    returns a baseline-only registry with zero network calls.

    Use :func:`reset_registry` in tests to force a fresh build.
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = _build_registry_from_settings()
    return _global_registry


def reset_registry() -> None:
    """Clear the module-level singleton so the next :func:`get_registry` call rebuilds it.

    Intended for tests and for the CLI ``sync`` command (which may reconfigure
    the store before forcing a rebuild).
    """
    global _global_registry
    _global_registry = None


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------


def _build_registry_from_settings() -> PromptRegistry:
    """Build a :class:`PromptRegistry` from environment variables.

    Reads the ``PROMPT_*`` env vars that Stage 6 will wire into
    ``settings.py``.  Using ``os.environ`` directly keeps Stage 3 independent
    of Stage 6 and avoids circular imports with pydantic-settings.

    When ``PROMPT_REGISTRY_ENABLED`` is absent or ``false``, returns a
    baseline-only registry with no store (zero network calls, CONSTRAINT #5).
    """
    enabled_raw = os.environ.get("PROMPT_REGISTRY_ENABLED", "false").strip().lower()
    enabled = enabled_raw in ("true", "1", "yes")

    # Always parse pins — a pin to a cached version is useful even when the
    # remote is disabled (allows offline rollback via PROMPT_REGISTRY_PINS).
    pins_raw = os.environ.get("PROMPT_REGISTRY_PINS", "{}").strip()
    try:
        pins: Dict[str, str] = json.loads(pins_raw)
        if not isinstance(pins, dict):
            logger.warning("_build_registry: PROMPT_REGISTRY_PINS is not a JSON object — ignoring")
            pins = {}
    except Exception as exc:
        logger.warning("_build_registry: could not parse PROMPT_REGISTRY_PINS: %s", exc)
        pins = {}

    if not enabled:
        logger.debug(
            "_build_registry_from_settings: PROMPT_REGISTRY_ENABLED=%r — baseline-only registry",
            enabled_raw,
        )
        return PromptRegistry(enabled=False, pins=pins)

    # ── Signing key ──────────────────────────────────────────────────────────
    signing_key = os.environ.get("PROMPT_REGISTRY_SIGNING_KEY") or None

    # (pins already parsed above, before the enabled check)

    # ── Cache ────────────────────────────────────────────────────────────────
    cache_dir = os.environ.get("PROMPT_CACHE_DIR", "output/prompt_cache")
    try:
        keep = int(os.environ.get("PROMPT_CACHE_KEEP_VERSIONS", "5"))
    except (ValueError, TypeError):
        keep = 5
    cache = CacheManager(cache_dir, keep_versions=keep)

    # ── Backend / store ───────────────────────────────────────────────────────
    backend = os.environ.get("PROMPT_REGISTRY_BACKEND", "http").strip().lower()
    store: Optional[PromptStore] = None

    try:
        if backend == "http":
            url = os.environ.get("PROMPT_REGISTRY_URL") or None
            token = os.environ.get("PROMPT_REGISTRY_TOKEN") or None
            if url:
                store = HTTPStore(url, token)
            else:
                logger.warning(
                    "_build_registry: PROMPT_REGISTRY_BACKEND=http but "
                    "PROMPT_REGISTRY_URL is not set — store unavailable"
                )
        elif backend == "local":
            path = os.environ.get("PROMPT_REGISTRY_URL", "registry.json")
            store = LocalJSONStore(path)
        elif backend == "firestore":
            creds = os.environ.get("PROMPT_REGISTRY_CREDENTIALS") or None
            store = FirestoreStore(credentials_path=creds)
        else:
            logger.warning(
                "_build_registry: unknown PROMPT_REGISTRY_BACKEND=%r — no store", backend
            )
    except Exception as exc:
        logger.warning("_build_registry: failed to construct store: %s", exc)
        store = None

    return PromptRegistry(
        store=store,
        cache=cache,
        signing_key=signing_key,
        pins=pins,
        enabled=enabled,
    )

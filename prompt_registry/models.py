"""
prompt_registry/models.py
==========================
Frozen data-transfer objects that represent the registry's on-wire JSON schema.

These are **pure value objects** — they carry no I/O, no signing logic, and no
validation beyond field-type coercion.  Signing lives in ``signing.py``;
guardrail validation lives in ``guardrails.py``.

Schema correspondence (from ``docs/PROMPT_REGISTRY_PLAN.md`` §2):
::

    {
      "registry_version": "2026-06-30T12:00:00Z",
      "signing_alg": "HMAC-SHA256",
      "prompts": {
        "master_preprompt": {
          "latest": "1.3.0",
          "versions": {
            "1.3.0": {
              "body": "...",
              "sha256": "...",
              "signature": "...",
              "created_at": "2026-06-30T11:58:00Z",
              "author": "kevin",
              "notes": "..."
            }
          }
        }
      }
    }

``signature`` = ``HMAC-SHA256(SIGNING_KEY, sha256_hex_of_body)``.
``sha256`` lets the cache layer check body integrity WITHOUT the signing key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# PromptRecord — one versioned prompt
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PromptRecord:
    """A single versioned prompt body plus its integrity metadata.

    ``frozen=True`` so the body and signature are immutable once created — any
    tampering attempt is structurally prevented at the Python level.

    Attributes
    ----------
    body:
        The raw UTF-8 prompt text (what gets fed to the AI).
    sha256:
        Hex SHA-256 digest of ``body.encode("utf-8")``.  Used for cache
        integrity checks WITHOUT requiring the signing key.
    signature:
        ``HMAC-SHA256(SIGNING_KEY, sha256_hex).hexdigest()``.  Verified by
        :func:`prompt_registry.signing.verify`.
    created_at:
        ISO 8601 UTC datetime string when this version was published.
    author:
        Optional human-readable publisher identifier.
    notes:
        Optional changelog note for this version.
    """

    body: str
    sha256: str
    signature: str
    created_at: str
    author: str = ""
    notes: str = ""

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, str]:
        """Serialise to a plain dict matching the registry JSON schema."""
        return {
            "body": self.body,
            "sha256": self.sha256,
            "signature": self.signature,
            "created_at": self.created_at,
            "author": self.author,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptRecord":
        """Deserialise from a parsed JSON dict (missing optional fields default to '')."""
        return cls(
            body=data["body"],
            sha256=data["sha256"],
            signature=data["signature"],
            created_at=data["created_at"],
            author=data.get("author", ""),
            notes=data.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# PromptVersion — all versions of one prompt id
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PromptVersion:
    """All versions of a single prompt ID plus a ``latest`` pointer.

    Note: ``frozen=True`` prevents reassigning ``latest`` or ``versions``
    after construction.  The ``versions`` dict itself is mutable at the Python
    level, but callers must treat it as immutable — it is never mutated after
    deserialisation.
    """

    latest: str
    versions: Dict[str, PromptRecord]

    def get_record(self, version: Optional[str] = None) -> Optional[PromptRecord]:
        """Return the ``PromptRecord`` for *version*, or the ``latest`` if ``None``."""
        target = version if version is not None else self.latest
        return self.versions.get(target)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to the registry JSON sub-schema for one prompt id."""
        return {
            "latest": self.latest,
            "versions": {k: v.to_dict() for k, v in self.versions.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromptVersion":
        """Deserialise from a parsed JSON dict."""
        versions = {
            k: PromptRecord.from_dict(v)
            for k, v in data.get("versions", {}).items()
        }
        return cls(latest=data["latest"], versions=versions)


# ---------------------------------------------------------------------------
# RegistryManifest — top-level signed manifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegistryManifest:
    """Top-level manifest returned by the remote store.

    Mirrors the outer ``registry.json`` envelope (``registry_version``,
    ``signing_alg``, ``prompts``).

    Note: same mutability caveat as :class:`PromptVersion` — the ``prompts``
    dict is treated as immutable after construction.
    """

    registry_version: str
    signing_alg: str
    prompts: Dict[str, PromptVersion]

    def get_prompt(
        self,
        prompt_id: str,
        version: Optional[str] = None,
    ) -> Optional[PromptRecord]:
        """Convenience: look up a :class:`PromptRecord` by id + optional version.

        Returns ``None`` when the id or version is absent — callers must
        fall through to cache or baseline.
        """
        pv = self.prompts.get(prompt_id)
        if pv is None:
            return None
        return pv.get_record(version)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the full manifest to a nested dict (JSON-serialisable)."""
        return {
            "registry_version": self.registry_version,
            "signing_alg": self.signing_alg,
            "prompts": {k: v.to_dict() for k, v in self.prompts.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegistryManifest":
        """Deserialise from a parsed top-level JSON dict."""
        prompts = {
            k: PromptVersion.from_dict(v)
            for k, v in data.get("prompts", {}).items()
        }
        return cls(
            registry_version=data.get("registry_version", ""),
            signing_alg=data.get("signing_alg", "HMAC-SHA256"),
            prompts=prompts,
        )

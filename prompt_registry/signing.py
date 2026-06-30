"""
prompt_registry/signing.py
===========================
HMAC-SHA256 signing and verification for prompt bodies.

**Signing scheme** (from ``docs/PROMPT_REGISTRY_PLAN.md`` §4.2):

    sha256(body)  = hashlib.sha256(body.encode("utf-8")).hexdigest()
    signature     = HMAC-SHA256(SIGNING_KEY, sha256_hex).hexdigest()

Separating the hash step means:
* The signature length is always 64 hex chars, regardless of body size.
* The ``sha256`` field stored in :class:`~prompt_registry.models.PromptRecord`
  can verify body *integrity* independently, without the signing key (useful
  for cache-hit checks).

**Zero new dependencies** — uses only Python stdlib ``hmac`` + ``hashlib``.

**Security properties**:
* ``verify()`` uses ``hmac.compare_digest`` for constant-time comparison,
  preventing timing-based signature oracle attacks.
* Any exception inside ``verify()`` (encoding error, type mismatch) returns
  ``False`` — fail-closed, never raises past the API boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)


def compute_sha256(body: str) -> str:
    """Return the hex SHA-256 digest of *body* encoded as UTF-8.

    This is the intermediate hash that the signature is computed *over*, and
    also stored as the ``sha256`` field in :class:`~prompt_registry.models.PromptRecord`
    for cache integrity verification without the signing key.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def sign(body: str, key: str) -> str:
    """Sign a prompt body and return the hex HMAC-SHA256 signature.

    Parameters
    ----------
    body:
        Raw prompt text (UTF-8 string).
    key:
        HMAC signing key (``settings.PROMPT_REGISTRY_SIGNING_KEY``).

    Returns
    -------
    str
        64-character lowercase hex HMAC digest.
    """
    sha = compute_sha256(body)
    return hmac.new(
        key.encode("utf-8"),
        sha.encode("utf-8"),
        "sha256",
    ).hexdigest()


def verify(body: str, signature: str, key: str) -> bool:
    """Verify *body* against its *signature* using *key*.

    Uses ``hmac.compare_digest`` for a constant-time comparison that does not
    leak information about *where* a tampered signature first differs.

    Parameters
    ----------
    body:
        The prompt text to verify (must be the exact bytes that were signed).
    signature:
        The hex HMAC-SHA256 string from :class:`~prompt_registry.models.PromptRecord`.
    key:
        The same key that was used in :func:`sign`.

    Returns
    -------
    bool
        ``True`` only when the recomputed signature matches *signature*
        character-for-character in constant time.  Any error → ``False``
        (fail-closed — never raises).
    """
    try:
        expected = sign(body, key)
        return hmac.compare_digest(expected, signature)
    except Exception:  # noqa: BLE001 — fail-closed on any encoding/type error
        logger.debug(
            "signing.verify caught exception — returning False (fail-closed)",
            exc_info=True,
        )
        return False

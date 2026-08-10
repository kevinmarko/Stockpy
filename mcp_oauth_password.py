"""Scrypt-based password hashing for the MCP OAuth authorization server's
multi-user login path (``mcp_oauth_provider.py``, gated by
``settings.MCP_OAUTH_MULTI_USER_ENABLED``).

Why Scrypt via ``cryptography``, not ``bcrypt``/``argon2``/``passlib``
------------------------------------------------------------------------
Neither ``bcrypt`` nor ``argon2`` nor ``passlib`` is currently pinned in
``requirements.txt``/``requirements-optional.txt`` (confirmed by grep).
``cryptography==50.0.0`` already is, and it ships
``cryptography.hazmat.primitives.kdf.scrypt.Scrypt`` -- a real, salted, slow
KDF with zero new dependency footprint. At this account-count scale (a
handful of named humans sharing one trading account, not public signup),
that is sufficient; revisit only if the operator specifically wants
``argon2``/``bcrypt`` interop with an external tool.

Storage format
---------------
Self-describing so future re-parameterization (a higher ``N``, say) never
needs a schema change or a batch-rehash migration::

    scrypt$<N>$<r>$<p>$<salt_b64>$<hash_b64>

``salt_b64``/``hash_b64`` are unpadded URL-safe base64 (``=`` stripped on
encode, re-added on decode -- keeps the stored string free of the
``$``-adjacent padding character, which is not itself unsafe here but is
needless noise).

Security notes
----------------
- ``hash_password`` always draws a fresh random salt (``os.urandom``) --
  two calls with the identical password never produce the same stored
  string, by construction.
- ``verify_password`` re-derives a key using the STORED salt/parameters and
  compares the two derived-key byte strings with ``hmac.compare_digest`` --
  never a naive ``==`` and never a comparison of the encoded string itself
  (a bug that would leak salt/parameter differences via early-exit timing
  on totally incomparable inputs). This mirrors the login gate's own
  ``hmac.compare_digest`` convention in ``mcp_oauth_provider.py``.
- ``verify_password`` never raises on a malformed/corrupt stored string --
  CONSTRAINT #6 (dead-letter resilience): a corrupt ``password_hash`` row
  degrades to "this password does not verify", never an unhandled
  exception that would 500 the ``/login`` POST handler.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# OWASP-recommended baseline parameters (OWASP Password Storage Cheat
# Sheet's Scrypt guidance): N=2^14, r=8, p=1.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
_DERIVED_KEY_LENGTH = 32
_SALT_LENGTH = 16

_FORMAT_PREFIX = "scrypt"
_FIELD_COUNT = 6  # "scrypt", N, r, p, salt_b64, hash_b64


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(
    password: str,
    *,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> str:
    """Hashes ``password`` with a fresh random salt, returning the
    self-describing ``scrypt$N$r$p$salt_b64$hash_b64`` stored format.
    """
    salt = os.urandom(_SALT_LENGTH)
    kdf = Scrypt(salt=salt, length=_DERIVED_KEY_LENGTH, n=n, r=r, p=p)
    derived = kdf.derive(password.encode("utf-8"))
    return "$".join(
        [
            _FORMAT_PREFIX,
            str(n),
            str(r),
            str(p),
            _b64encode(salt),
            _b64encode(derived),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """Verifies ``password`` against a ``hash_password``-produced ``stored``
    string. Returns ``False`` (never raises) on any malformed/corrupt
    ``stored`` input -- a bad row in ``oauth_users.password_hash`` must
    degrade to "does not verify", not crash the ``/login`` POST handler.
    """
    if not isinstance(stored, str):
        return False

    parts = stored.split("$")
    if len(parts) != _FIELD_COUNT:
        return False

    algo, n_str, r_str, p_str, salt_b64, hash_b64 = parts
    if algo != _FORMAT_PREFIX:
        return False

    try:
        n, r, p = int(n_str), int(r_str), int(p_str)
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
    except (ValueError, binascii.Error):
        return False

    if not expected:
        return False

    try:
        kdf = Scrypt(salt=salt, length=len(expected), n=n, r=r, p=p)
        derived = kdf.derive(password.encode("utf-8"))
    except (ValueError, TypeError, InvalidKey):
        # Invalid KDF parameters recovered from a corrupt/tampered stored
        # string (e.g. non-power-of-two N) -- treat exactly like any other
        # malformed input: verification fails, nothing raises.
        return False

    return hmac.compare_digest(derived, expected)

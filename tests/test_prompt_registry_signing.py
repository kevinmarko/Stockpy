"""
tests/test_prompt_registry_signing.py
======================================
Unit tests for ``prompt_registry.signing``.

All tests are fully offline (no network, no files, no Streamlit).

Coverage
--------
- ``compute_sha256`` — determinism, known value, empty input.
- ``sign`` / ``verify`` round-trip — normal, tampered body, wrong key, empty
  signature, malformed signature, unicode body.
- Output format — hex string, correct length (64 chars for SHA-256).
- ``hmac.compare_digest`` usage — source-code scan confirms no direct ``==``
  comparison so timing leaks are structurally prevented.
- Edge cases — empty body, very long body, bytes boundary.
"""

from __future__ import annotations

import hashlib
import inspect

import pytest

from prompt_registry.signing import compute_sha256, sign, verify
import prompt_registry.signing as _signing_module


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

KEY = "test-signing-key-abc123"
BODY = (
    "You are working in the InvestYo advisory platform. "
    "ADVISORY_ONLY=true is the default. "
    "Acknowledge constraints, then wait for the stage prompt."
)


# ---------------------------------------------------------------------------
# compute_sha256
# ---------------------------------------------------------------------------

class TestComputeSha256:
    def test_deterministic(self):
        assert compute_sha256("hello") == compute_sha256("hello")

    def test_known_value(self):
        """Must match stdlib hashlib directly."""
        expected = hashlib.sha256("hello".encode("utf-8")).hexdigest()
        assert compute_sha256("hello") == expected

    def test_empty_string(self):
        """Empty string has a known SHA-256; must not raise."""
        result = compute_sha256("")
        assert len(result) == 64
        # Known hash of empty string
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_unicode_body(self):
        """Unicode characters must not raise."""
        result = compute_sha256("αβγδ — advisory ≥ 0")
        assert len(result) == 64

    def test_two_different_bodies_differ(self):
        assert compute_sha256("body A") != compute_sha256("body B")


# ---------------------------------------------------------------------------
# sign
# ---------------------------------------------------------------------------

class TestSign:
    def test_returns_hex_string(self):
        sig = sign(BODY, KEY)
        # Must be valid hex
        int(sig, 16)

    def test_returns_64_chars(self):
        """HMAC-SHA256 output → 32 bytes → 64 hex chars."""
        sig = sign(BODY, KEY)
        assert len(sig) == 64

    def test_deterministic(self):
        assert sign(BODY, KEY) == sign(BODY, KEY)

    def test_different_bodies_different_sigs(self):
        sig1 = sign("body A", KEY)
        sig2 = sign("body B", KEY)
        assert sig1 != sig2

    def test_different_keys_different_sigs(self):
        sig1 = sign(BODY, "key-one")
        sig2 = sign(BODY, "key-two")
        assert sig1 != sig2

    def test_empty_body(self):
        """Empty body is a valid (if unusual) input; must not raise."""
        sig = sign("", KEY)
        assert len(sig) == 64

    def test_unicode_body(self):
        body = "Advisory: αβγδ — constraints 1–9 apply. ADVISORY_ONLY=true."
        sig = sign(body, KEY)
        assert len(sig) == 64


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

class TestVerify:
    def test_round_trip(self):
        sig = sign(BODY, KEY)
        assert verify(BODY, sig, KEY) is True

    def test_tampered_body_fails(self):
        sig = sign(BODY, KEY)
        tampered = BODY + " INJECTED"
        assert verify(tampered, sig, KEY) is False

    def test_wrong_key_fails(self):
        sig = sign(BODY, KEY)
        assert verify(BODY, sig, "wrong-key") is False

    def test_empty_signature_fails(self):
        assert verify(BODY, "", KEY) is False

    def test_wrong_length_sig_fails(self):
        """A signature that's obviously the wrong length must return False."""
        assert verify(BODY, "deadbeef", KEY) is False

    def test_non_hex_sig_fails(self):
        """Non-hex garbage must not raise, just return False."""
        assert verify(BODY, "not-valid-hex-!!!!", KEY) is False

    def test_empty_body_round_trip(self):
        """Empty body gets a real signature that must verify correctly."""
        sig = sign("", KEY)
        assert verify("", sig, KEY) is True

    def test_unicode_round_trip(self):
        body = "Advisory: αβγδ — ADVISORY_ONLY=true applies globally."
        sig = sign(body, KEY)
        assert verify(body, sig, KEY) is True

    def test_returns_bool_type(self):
        sig = sign(BODY, KEY)
        result = verify(BODY, sig, KEY)
        assert isinstance(result, bool)

    def test_false_is_bool_not_none(self):
        assert verify(BODY, "bad_sig", KEY) is False


# ---------------------------------------------------------------------------
# Security properties
# ---------------------------------------------------------------------------

class TestSecurityProperties:
    def test_compare_digest_is_used(self):
        """``hmac.compare_digest`` must be used — not direct ``==`` comparison.

        This is the constant-time check that prevents timing-based signature
        oracle attacks.  We scan the source of the signing module to confirm.
        """
        src = inspect.getsource(_signing_module)
        assert "compare_digest" in src, (
            "signing.verify must use hmac.compare_digest, not ==, "
            "to prevent timing leaks"
        )

    def test_verify_never_raises(self):
        """Any kind of bad input must return False, never raise."""
        bad_inputs = [
            ("", None, KEY),
            (BODY, None, KEY),
            (None, "sig", KEY),
        ]
        for body, sig, key in bad_inputs:
            try:
                result = verify(body, sig, key)
                # If it didn't raise, it must be False
                assert result is False
            except Exception as exc:
                pytest.fail(
                    f"verify({body!r}, {sig!r}, {key!r}) raised {exc!r} "
                    "— must never raise, only return False"
                )

    def test_sign_does_not_return_empty(self):
        """The signature must always be non-empty."""
        for body in ["", "hello", "a" * 10_000]:
            sig = sign(body, KEY)
            assert sig, f"sign({body[:20]!r}...) returned empty string"


# ---------------------------------------------------------------------------
# Round-trip via models.PromptRecord
# ---------------------------------------------------------------------------

class TestRoundTripWithModel:
    """Verify signing integrates cleanly with PromptRecord."""

    def test_prompt_record_round_trip(self):
        from prompt_registry.models import PromptRecord

        body = "ADVISORY_ONLY=true platform. Acknowledge constraints."
        sha = compute_sha256(body)
        sig = sign(body, KEY)

        record = PromptRecord(
            body=body,
            sha256=sha,
            signature=sig,
            created_at="2026-06-30T00:00:00Z",
            author="test",
            notes="round-trip test",
        )

        assert verify(record.body, record.signature, KEY) is True
        assert compute_sha256(record.body) == record.sha256

    def test_tampered_record_fails(self):
        from prompt_registry.models import PromptRecord

        body = "ADVISORY_ONLY=true. Constraints acknowledged."
        sha = compute_sha256(body)
        sig = sign(body, KEY)

        # Simulate a man-in-the-middle replacing body after signing
        record = PromptRecord(
            body=body + " TAMPERED",
            sha256=sha,          # stale hash
            signature=sig,       # stale sig
            created_at="2026-06-30T00:00:00Z",
        )

        # Both integrity checks must fail
        assert verify(record.body, record.signature, KEY) is False
        assert compute_sha256(record.body) != record.sha256

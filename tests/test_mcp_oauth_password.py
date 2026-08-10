"""Tests for mcp_oauth_password.py -- the Scrypt-based password hashing used
by the MCP OAuth authorization server's multi-user login path.
"""

import pytest

from mcp_oauth_password import _b64decode, _b64encode, hash_password, verify_password

# Small parameters everywhere in this file so the suite stays fast --
# correctness of the KDF wiring doesn't depend on OWASP-recommended cost,
# only on the format/verify contract.
_FAST = dict(n=2**4, r=1, p=1)


def test_hash_and_verify_round_trip():
    stored = hash_password("correct horse battery staple", **_FAST)
    assert verify_password("correct horse battery staple", stored) is True


def test_verify_wrong_password_fails():
    stored = hash_password("correct horse battery staple", **_FAST)
    assert verify_password("wrong password", stored) is False


def test_hash_stored_format_is_self_describing():
    stored = hash_password("hunter2", **_FAST)
    parts = stored.split("$")
    assert len(parts) == 6
    assert parts[0] == "scrypt"
    assert parts[1] == "16"
    assert parts[2] == "1"
    assert parts[3] == "1"
    assert parts[4]  # salt_b64 non-empty
    assert parts[5]  # hash_b64 non-empty


def test_two_hashes_of_same_password_have_different_salts_and_differ():
    a = hash_password("same-password", **_FAST)
    b = hash_password("same-password", **_FAST)
    assert a != b
    salt_a = a.split("$")[4]
    salt_b = b.split("$")[4]
    assert salt_a != salt_b
    # Both still verify against the original password.
    assert verify_password("same-password", a) is True
    assert verify_password("same-password", b) is True


@pytest.mark.parametrize(
    "corrupt_field_index",
    [1, 2, 3, 4, 5],  # N, r, p, salt_b64, hash_b64
)
def test_tampering_with_any_field_fails_verification(corrupt_field_index):
    stored = hash_password("tamper-me", **_FAST)
    parts = stored.split("$")
    if corrupt_field_index in (1, 2, 3):
        # Bump the numeric param -- still parses as an int, but the
        # re-derived key no longer matches.
        parts[corrupt_field_index] = str(int(parts[corrupt_field_index]) + 1)
    else:
        # Flip a byte of the DECODED salt/hash, then re-encode -- flipping a
        # base64 CHARACTER instead is unreliable: a base64 group's trailing
        # character can carry padding bits a standards-compliant decoder
        # ignores (verified empirically: ~25% of random 16-byte salts have a
        # last character whose flip decodes to the SAME bytes), which would
        # make this "tampered" string not actually tampered at all.
        field = parts[corrupt_field_index]
        raw = bytearray(_b64decode(field))
        raw[0] ^= 0xFF
        parts[corrupt_field_index] = _b64encode(bytes(raw))

    tampered = "$".join(parts)
    assert verify_password("tamper-me", tampered) is False


def test_tampering_hash_bytes_fails_verification():
    stored = hash_password("byte-level-tamper", **_FAST)
    parts = stored.split("$")
    # Truncate the hash_b64 field entirely -- still non-empty, still valid
    # base64-ish, but decodes to different bytes.
    parts[5] = parts[5][:-4] + "0000"
    tampered = "$".join(parts)
    assert verify_password("byte-level-tamper", tampered) is False


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-scrypt-hash-at-all",
        "scrypt$16$1",  # too few fields
        "scrypt$16$1$1$salt$hash$extra",  # too many fields
        "bcrypt$16$1$1$c2FsdA$aGFzaA",  # wrong algo prefix
        "scrypt$notanumber$1$1$c2FsdA$aGFzaA",  # non-numeric N
        "scrypt$16$1$1$not!!valid!!base64$aGFzaA",  # invalid base64 salt
        "scrypt$16$1$1$c2FsdA$",  # empty hash field
    ],
)
def test_malformed_stored_format_returns_false_never_raises(malformed):
    assert verify_password("anything", malformed) is False


def test_verify_password_non_string_stored_returns_false():
    assert verify_password("anything", None) is False  # type: ignore[arg-type]


def test_default_parameters_are_owasp_baseline():
    stored = hash_password("default-params-check")
    parts = stored.split("$")
    assert parts[1] == str(2**14)
    assert parts[2] == "8"
    assert parts[3] == "1"

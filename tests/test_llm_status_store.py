"""
tests/test_llm_status_store.py — llm/status_store.py contract.
==============================================================

Pins the last-real-call telemetry store that closes the "LLM key misconfig
degrades silently to null" gap. No network. Covers:

* Classification by exception TYPE NAME (no SDK import at runtime — the store
  itself never imports anthropic/openai/google.genai) + the google.genai
  400/API_KEY_INVALID special case and its never-guess boundary. ``TestClassify``
  proves the logic against hand-rolled fakes; ``TestClassifyAgainstRealSDKs``
  proves the SAME logic against the actual installed SDK exception classes
  (anthropic/openai/google-genai ARE requirements.txt dependencies used
  elsewhere in the codebase — importing them in a test is not a new coupling),
  so a future SDK upgrade that changes an exception's shape is caught by CI
  before it reaches a live key in production.
* Never-raises degradation (CONSTRAINT #6).
* Fingerprint rotation (fixing a key clears an auth alarm with zero LLM calls)
  and the transient-only age bound.
* The two secret-containment invariants: no key material EVER reaches the file,
  and the fingerprint NEVER crosses the module boundary (CONSTRAINT #3).
* Always-writes (advances checked_at) so the age bound reflects last-observed.
"""

from __future__ import annotations

import ast
import json
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from settings import settings
import llm.status_store as ss


@pytest.fixture(autouse=True)
def _isolated_output(tmp_path):
    """Point OUTPUT_DIR at a temp dir for every test (the store reads it live)."""
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        yield tmp_path


# ---------------------------------------------------------------------------
# Classification — no SDK import, type-name + HTTP status + Gemini special case
# ---------------------------------------------------------------------------


class TestClassify:
    def test_auth_by_type_name(self):
        assert ss.classify_exception(_Named("AuthenticationError")) == ("auth", None)
        assert ss.classify_exception(_Named("PermissionDeniedError")) == ("auth", None)

    def test_kinds_by_type_name(self):
        assert ss.classify_exception(_Named("RateLimitError"))[0] == "rate_limit"
        assert ss.classify_exception(_Named("APITimeoutError"))[0] == "timeout"
        assert ss.classify_exception(_Named("APIConnectionError"))[0] == "network"
        assert ss.classify_exception(_Named("ValidationError"))[0] == "schema"

    def test_http_status_401_wins_over_unknown_name(self):
        # A generic exception carrying status_code=401 -> auth, even with an
        # unrecognised class name (the most reliable cross-SDK signal).
        exc = _Named("SomeWeirdError", status_code=401)
        assert ss.classify_exception(exc) == ("auth", 401)

    def test_http_status_429(self):
        assert ss.classify_exception(_Named("X", status_code=429)) == ("rate_limit", 429)

    def test_gemini_bad_key_400_upgrades_to_auth(self):
        # google.genai has no auth class: ClientError(code=400, "API key not valid").
        exc = _Named(
            "ClientError",
            code=400,
            msg="400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.",
        )
        assert ss.classify_exception(exc) == ("auth", 400)

    def test_gemini_benign_400_stays_unknown(self):
        # THE never-guess pin: a 400 without the documented key-invalid reason
        # must NOT be classified as auth.
        exc = _Named(
            "ClientError", code=400, msg="400 INVALID_ARGUMENT. Request contains an invalid field."
        )
        assert ss.classify_exception(exc) == ("unknown", 400)

    def test_bool_code_is_not_read_as_status(self):
        # bool is an int subclass; a `.code == True` must not become HTTP 1.
        exc = _Named("X", code=True)
        assert ss.classify_exception(exc) == ("unknown", None)

    def test_string_status_is_ignored(self):
        # google.genai's `.status` is "INVALID_ARGUMENT" (a str) — must be skipped.
        exc = _Named("X")
        exc.status = "INVALID_ARGUMENT"
        assert ss.classify_exception(exc) == ("unknown", None)

    def test_property_that_raises_degrades(self):
        class _Boom:
            @property
            def status_code(self):
                raise RuntimeError("nope")

        assert ss.classify_exception(_Boom()) == ("unknown", None)

    def test_none_does_not_raise(self):
        assert ss.classify_exception(None) == ("unknown", None)


class TestClassifyAgainstRealSDKs:
    """Same classification contract as TestClassify, but against REAL exception
    objects constructed via the actual installed SDKs (anthropic>=0.25,
    openai>=1.40, google-genai>=0.3 — all three are requirements.txt
    dependencies, not optional). TestClassify's ``_Named()`` fake proves the
    LOGIC is correct in isolation; this class proves the logic is correct
    against the SDKs' ACTUAL exception shapes, so a future SDK upgrade that
    changes a status-code attribute name or an error-message format is caught
    by CI, not discovered against a live key in production. Skips (rather than
    fails) if a given SDK isn't importable, so this degrades gracefully in an
    environment that hasn't installed requirements.txt in full."""

    def test_real_anthropic_authentication_error(self):
        anthropic = pytest.importorskip("anthropic")
        httpx = pytest.importorskip("httpx")
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(401, request=req)
        exc = anthropic.AuthenticationError("invalid x-api-key", response=resp, body=None)
        assert ss.classify_exception(exc) == ("auth", 401)

    def test_real_anthropic_rate_limit_error(self):
        anthropic = pytest.importorskip("anthropic")
        httpx = pytest.importorskip("httpx")
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(429, request=req)
        exc = anthropic.RateLimitError("rate limited", response=resp, body=None)
        assert ss.classify_exception(exc) == ("rate_limit", 429)

    def test_real_anthropic_timeout_and_connection_errors(self):
        # These carry NO status_code attribute — classification must fall
        # through to the type-name map, not silently return unknown/None.
        anthropic = pytest.importorskip("anthropic")
        httpx = pytest.importorskip("httpx")
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        timeout_exc = anthropic.APITimeoutError(request=req)
        assert ss.classify_exception(timeout_exc) == ("timeout", None)
        conn_exc = anthropic.APIConnectionError(request=req)
        assert ss.classify_exception(conn_exc) == ("network", None)

    def test_real_openai_authentication_and_rate_limit_errors(self):
        openai = pytest.importorskip("openai")
        httpx = pytest.importorskip("httpx")
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        auth_exc = openai.AuthenticationError(
            "bad key", response=httpx.Response(401, request=req), body=None
        )
        assert ss.classify_exception(auth_exc) == ("auth", 401)
        rl_exc = openai.RateLimitError(
            "rate limited", response=httpx.Response(429, request=req), body=None
        )
        assert ss.classify_exception(rl_exc) == ("rate_limit", 429)

    def test_real_gemini_bad_key_400_is_auth(self):
        # THE Risk C case: google.genai has no dedicated auth exception class.
        # A bad key surfaces as ClientError(code=400, status="INVALID_ARGUMENT",
        # message="API key not valid..."). Must classify as auth via the
        # documented-reason match, not the generic 400 path.
        gerr = pytest.importorskip("google.genai.errors")
        exc = gerr.ClientError(
            400,
            {
                "error": {
                    "code": 400,
                    "message": "API key not valid. Please pass a valid API key.",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )
        assert type(exc).__name__ == "ClientError"
        assert exc.code == 400
        assert isinstance(exc.status, str)  # NOT an int — must not be misread as an HTTP code
        assert ss.classify_exception(exc) == ("auth", 400)

    def test_real_gemini_benign_400_is_NOT_auth(self):
        # The never-guess boundary against the REAL exception class: a 400 for
        # any OTHER reason must stay unknown, never a fabricated auth verdict.
        gerr = pytest.importorskip("google.genai.errors")
        exc = gerr.ClientError(
            400,
            {
                "error": {
                    "code": 400,
                    "message": "Request contains an invalid argument.",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )
        assert ss.classify_exception(exc) == ("unknown", 400)

    def test_real_gemini_server_error_is_not_auth(self):
        # A 5xx (APIError's other concrete subclass, ServerError) must never
        # be classified as auth regardless of message content.
        gerr = pytest.importorskip("google.genai.errors")
        exc = gerr.ServerError(
            500, {"error": {"code": 500, "message": "internal error", "status": "INTERNAL"}}
        )
        kind, status = ss.classify_exception(exc)
        assert kind != "auth"
        assert status == 500


# ---------------------------------------------------------------------------
# Never raises (CONSTRAINT #6)
# ---------------------------------------------------------------------------


class TestNeverRaises:
    def test_missing_file_reads_full_none_shape(self):
        allr = ss.read_all()
        assert set(allr) == {"claude", "gemini", "openai"}
        for rec in allr.values():
            assert rec["source"] == "none"
            assert rec["ok"] is None and rec["error_kind"] is None

    def test_malformed_json_degrades(self, _isolated_output):
        (_isolated_output / ss.LLM_STATUS_FILENAME).write_text("{not json", encoding="utf-8")
        assert ss.read_status("claude")["source"] == "none"

    def test_wrong_version_degrades(self, _isolated_output):
        (_isolated_output / ss.LLM_STATUS_FILENAME).write_text(
            json.dumps({"version": 999, "providers": {"claude": {"ok": True}}}), encoding="utf-8"
        )
        assert ss.read_status("claude")["source"] == "none"

    def test_providers_not_a_dict_degrades(self, _isolated_output):
        (_isolated_output / ss.LLM_STATUS_FILENAME).write_text(
            json.dumps({"version": 1, "providers": []}), encoding="utf-8"
        )
        assert ss.read_status("gemini")["source"] == "none"

    def test_unknown_provider_is_none_shape(self):
        assert ss.read_status("mistral")["source"] == "none"

    def test_write_to_unwritable_dir_does_not_raise(self):
        with mock.patch.object(settings, "OUTPUT_DIR", "/proc/nonexistent/cannot/write"):
            ss.record_failure("claude", _Named("AuthenticationError", status_code=401))
            ss.record_success("gemini")  # must not raise


# ---------------------------------------------------------------------------
# Fingerprint rotation — fixing a key clears the alarm with ZERO LLM calls
# ---------------------------------------------------------------------------


class TestFingerprintRotation:
    def test_auth_verdict_survives_until_key_changes(self):
        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-a"):
            ss.record_failure("claude", _Named("AuthenticationError", status_code=401))
            r = ss.read_status("claude")
            assert r["source"] == "last_call" and r["error_kind"] == "auth"

        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-b"):
            r2 = ss.read_status("claude")
            assert r2["source"] == "key_rotated"
            # every field nulled — the record is about a DIFFERENT key
            assert r2["ok"] is None and r2["error_kind"] is None
            assert r2["checked_at"] is None and r2["http_status"] is None

        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-a"):
            assert ss.read_status("claude")["source"] == "last_call"

    def test_fingerprint_never_in_read_output(self):
        with mock.patch.object(settings, "GEMINI_API_KEY", "gm-key"):
            ss.record_success("gemini")
            assert "key_fingerprint" not in ss.read_status("gemini")
            assert all("key_fingerprint" not in r for r in ss.read_all().values())


# ---------------------------------------------------------------------------
# Age bound — TRANSIENT only; auth/ok are fingerprint-bound, not age-bound
# ---------------------------------------------------------------------------


class TestAgeBound:
    def _write_aged(self, _isolated_output, provider, key, *, ok, error_kind, hours_ago):
        import hashlib

        fp = hashlib.sha256(key.encode()).hexdigest()[:12]
        ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        (_isolated_output / ss.LLM_STATUS_FILENAME).write_text(
            json.dumps(
                {
                    "version": 1,
                    "providers": {
                        provider: {
                            "provider": provider,
                            "ok": ok,
                            "error_kind": error_kind,
                            "exception_type": None,
                            "http_status": None,
                            "checked_at": ts,
                            "key_fingerprint": fp,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_old_transient_expires_but_keeps_fields(self, _isolated_output):
        self._write_aged(_isolated_output, "claude", "sk-a", ok=False, error_kind="timeout", hours_ago=48)
        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-a"):
            with mock.patch.object(settings, "LLM_STATUS_MAX_AGE_HOURS", 24.0):
                r = ss.read_status("claude")
        assert r["source"] == "expired"
        assert r["error_kind"] == "timeout"  # retained

    def test_old_auth_is_NOT_expired(self, _isolated_output):
        self._write_aged(_isolated_output, "claude", "sk-a", ok=False, error_kind="auth", hours_ago=48)
        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-a"):
            with mock.patch.object(settings, "LLM_STATUS_MAX_AGE_HOURS", 24.0):
                r = ss.read_status("claude")
        assert r["source"] == "last_call"  # key-bound, not age-bound
        assert r["error_kind"] == "auth"

    def test_old_ok_is_NOT_expired(self, _isolated_output):
        self._write_aged(_isolated_output, "gemini", "gm", ok=True, error_kind=None, hours_ago=48)
        with mock.patch.object(settings, "GEMINI_API_KEY", "gm"):
            with mock.patch.object(settings, "LLM_STATUS_MAX_AGE_HOURS", 24.0):
                r = ss.read_status("gemini")
        assert r["source"] == "last_call" and r["ok"] is True


# ---------------------------------------------------------------------------
# Secret containment (CONSTRAINT #3) + always-writes
# ---------------------------------------------------------------------------


class TestSecretContainment:
    def test_no_key_material_ever_reaches_the_file(self, _isolated_output):
        # High-entropy sentinels with NO English/provider/field words, so a
        # legitimate 6-char token in the file ("claude", "checked", ...) can
        # never masquerade as a leaked key substring.
        sentinels = {
            "claude": "XK7QW9ZP2MN4VB8TR6YU3JH5GD1FS0LA",
            "gemini": "QP3MZ9XK7WV2NB8TR6YU4JH5GD1FS0CE",
            "openai": "MZ9XK7QP3WV2NB8TR6YU4JH5GD1FS0OP",
        }
        raw_msg = "TOKEN XK7QW9ZP2MN4VB8TR6YU3JH5GD1FS0LA was rejected"
        with mock.patch.object(settings, "ANTHROPIC_API_KEY", sentinels["claude"]):
            with mock.patch.object(settings, "GEMINI_API_KEY", sentinels["gemini"]):
                with mock.patch.object(settings, "OPENAI_API_KEY", sentinels["openai"]):
                    ss.record_failure("claude", _Named("AuthenticationError", status_code=401, msg=raw_msg))
                    ss.record_success("gemini")
                    ss.record_failure("openai", _Named("RateLimitError", status_code=429))

        raw = (_isolated_output / ss.LLM_STATUS_FILENAME).read_text(encoding="utf-8")
        for key in sentinels.values():
            assert key not in raw
            # no >=6-char substring of a key leaks either
            for i in range(len(key) - 5):
                assert key[i : i + 6] not in raw, f"leaked substring of {key!r}"
        assert "Bearer" not in raw and "was rejected" not in raw

    def test_read_output_carries_no_fingerprint(self, _isolated_output):
        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-a"):
            ss.record_success("claude")
        on_disk = json.loads((_isolated_output / ss.LLM_STATUS_FILENAME).read_text())
        stored_fp = on_disk["providers"]["claude"]["key_fingerprint"]
        assert stored_fp  # it IS persisted internally
        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-a"):
            rec = ss.read_status("claude")
        assert stored_fp not in json.dumps(rec)  # but never crosses the boundary


class TestAlwaysWrites:
    def test_identical_failures_advance_checked_at(self):
        with mock.patch.object(settings, "ANTHROPIC_API_KEY", "sk-a"):
            ss.record_failure("claude", _Named("AuthenticationError", status_code=401))
            t1 = ss.read_status("claude")["checked_at"]
            import time

            time.sleep(0.01)
            ss.record_failure("claude", _Named("AuthenticationError", status_code=401))
            t2 = ss.read_status("claude")["checked_at"]
        assert t1 != t2 and t2 > t1


# ---------------------------------------------------------------------------
# Leaf invariant — imports nothing from the llm package (cycle-proof)
# ---------------------------------------------------------------------------


def test_status_store_imports_nothing_from_llm():
    src = pathlib.Path(ss.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("llm"), f"leaf must not import {node.module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("llm"), f"leaf must not import {alias.name}"


# ---------------------------------------------------------------------------
# Test double — a fake exception with a controllable class name + attrs
# ---------------------------------------------------------------------------


def _Named(name: str, *, status_code=None, code=None, msg: str = ""):
    """Build an exception instance whose ``type().__name__ == name``.

    ``str(exc)`` returns ``msg`` when given (the google.genai regex path reads
    it) else ``name``. ``status_code`` / ``code`` set the HTTP-status attributes
    the classifier reads.
    """
    cls = type(name, (Exception,), {})
    exc = cls(msg or name)
    if status_code is not None:
        exc.status_code = status_code
    if code is not None:
        exc.code = code
    return exc

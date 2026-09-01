"""
tests/test_jules_client.py
===========================
Unit tests for ``data/jules_client.py`` — the HTTP seam for Google's Jules
coding-agent REST API.

Everything here is offline: ``requests.get``/``requests.post`` are
monkeypatched via ``patch("data.jules_client.requests.X", ...)`` and
credentials/flags are set via ``patch("settings.settings.X", ...)`` /
``monkeypatch.setattr(settings, "X", ...)``, matching
``tests/test_fmp_client.py``'s exact conventions. No ``responses``, no
``requests_mock``, no VCR.

``TestCredentialGate.test_key_from_settings_is_used_when_os_environ_is_empty``
    Regression guard mirroring FMP's own: pydantic-settings' ``env_file``
    populates the ``settings`` singleton but NOT the real ``os.environ``, so a
    client that reads ``os.environ.get(...)`` sees nothing for the normal
    operator whose key lives only in ``.env``. This test fails if anyone
    reintroduces an ``os.environ`` read in ``data/jules_client.py``.

``TestDispatchSessionPermanentlyDisabled``
    ``dispatch_session()`` was originally built around a capability Jules
    does not actually have (writing new code and opening a PR from a prompt
    alone). It is now permanently disabled -- these tests assert it ALWAYS
    raises ``JulesCapabilityNotAvailable``, unconditionally, regardless of
    what arguments are passed (including ``confirm=True``), and that it
    never makes a network call. ``list_sources``/``format_sources`` remain
    genuinely valid, unchanged behavior and are tested as before.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from data.jules_client import (
    JulesCapabilityNotAvailable,
    JulesUnavailable,
    dispatch_session,
    list_sources,
)
from settings import settings


SOURCES_PAYLOAD = {
    "sources": [
        {
            "name": "sources/github/acme/widgets",
            "id": "src-1",
            "githubRepo": {"owner": "acme", "repo": "widgets"},
        }
    ]
}


def _resp(status: int = 200, payload=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    return resp


@pytest.fixture(autouse=True)
def _jules_enabled_with_key(monkeypatch):
    """Most tests want a configured, enabled client; the credential-gate
    tests below override this per-test."""
    monkeypatch.setattr(settings, "JULES_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "JULES_API_KEY", "test-jules-key", raising=False)
    monkeypatch.setattr(settings, "JULES_REQUEST_TIMEOUT_SECONDS", 30, raising=False)
    yield


class TestCredentialGate:
    def test_key_from_settings_is_used_when_os_environ_is_empty(
        self, monkeypatch
    ):
        """THE regression guard: .env populates the settings singleton but
        NOT os.environ. A client reading os.environ.get("JULES_API_KEY")
        would see nothing here and skip every request forever."""
        monkeypatch.delenv("JULES_API_KEY", raising=False)
        assert os.environ.get("JULES_API_KEY") is None  # the operator's real case
        monkeypatch.setattr(settings, "JULES_API_KEY", "only-in-dot-env", raising=False)
        monkeypatch.setattr(settings, "JULES_ENABLED", True, raising=False)

        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ) as get:
            list_sources()

        assert get.call_count == 1
        assert get.call_args.kwargs["headers"]["X-Goog-Api-Key"] == "only-in-dot-env"


class TestListSourcesGates:
    def test_raises_when_jules_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "JULES_ENABLED", False, raising=False)
        with pytest.raises(JulesUnavailable, match="disabled"):
            list_sources()

    def test_raises_when_api_key_unset_even_if_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "JULES_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "JULES_API_KEY", None, raising=False)
        with pytest.raises(JulesUnavailable, match="JULES_API_KEY"):
            list_sources()

    def test_no_network_call_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "JULES_ENABLED", False, raising=False)
        with patch("data.jules_client.requests.get") as get:
            with pytest.raises(JulesUnavailable):
                list_sources()
        get.assert_not_called()


class TestListSourcesSuccess:
    def test_returns_parsed_json_on_200(self):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ):
            result = list_sources()
        assert result == SOURCES_PAYLOAD

    def test_request_shape(self):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ) as get:
            list_sources()
        args, kwargs = get.call_args
        assert args[0] == "https://jules.googleapis.com/v1alpha/sources"
        assert kwargs["headers"]["X-Goog-Api-Key"] == "test-jules-key"
        assert kwargs["timeout"] == 30


class TestListSourcesFailure:
    def test_raises_on_4xx(self):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(404, {"error": "nope"})
        ):
            with pytest.raises(JulesUnavailable, match="404"):
                list_sources()

    def test_raises_on_5xx(self):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(500, {"error": "boom"})
        ):
            with pytest.raises(JulesUnavailable, match="500"):
                list_sources()

    def test_raises_on_request_exception(self):
        import requests

        with patch(
            "data.jules_client.requests.get",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            with pytest.raises(JulesUnavailable, match="transport error"):
                list_sources()

    def test_raises_on_timeout(self):
        import requests

        with patch(
            "data.jules_client.requests.get",
            side_effect=requests.exceptions.Timeout("timed out"),
        ):
            with pytest.raises(JulesUnavailable):
                list_sources()


class TestListSourcesMalformedJSON:
    """Fix #1: a malformed/empty 2xx body must degrade to JulesUnavailable,
    never escape as a raw JSONDecodeError -- ``response.json()`` was
    previously called OUTSIDE the try/except wrapping the raw request."""

    def test_malformed_json_body_raises_jules_unavailable_not_json_decode_error(self):
        import json

        resp = _resp(200, {})
        resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        with patch("data.jules_client.requests.get", return_value=resp):
            with pytest.raises(JulesUnavailable, match="malformed JSON"):
                list_sources()

    def test_empty_body_json_decode_error_does_not_escape_raw(self):
        """Same failure mode as an empty 2xx response body (e.g. a proxy
        returning ``200`` with no content) -- must still be a
        JulesUnavailable, not an uncaught exception of any other type."""
        resp = _resp(200, {})
        resp.json.side_effect = ValueError("No JSON object could be decoded")
        with patch("data.jules_client.requests.get", return_value=resp):
            try:
                list_sources()
                pytest.fail("expected JulesUnavailable to be raised")
            except JulesUnavailable:
                pass


class TestFormatSourcesNullSources:
    """Fix #3: an explicit ``{"sources": null}`` must degrade to an empty
    list, not crash -- ``.get("sources", [])`` only supplies the default
    when the key is ABSENT, not when its value is ``None``."""

    def test_explicit_null_sources_key_yields_empty_list(self):
        from data.jules_client import format_sources

        assert format_sources({"sources": None}) == []

    def test_missing_sources_key_yields_empty_list(self):
        from data.jules_client import format_sources

        assert format_sources({}) == []

    def test_non_dict_response_yields_empty_list(self):
        from data.jules_client import format_sources

        assert format_sources(None) == []  # type: ignore[arg-type]

    def test_normal_payload_still_formats_correctly(self):
        from data.jules_client import format_sources

        result = format_sources(SOURCES_PAYLOAD)
        assert result == [
            {"name": "sources/github/acme/widgets", "owner": "acme", "repo": "widgets"}
        ]

    def test_unnamed_source_falls_back_to_canonical_string(self):
        from data.jules_client import format_sources

        result = format_sources({"sources": [{"githubRepo": {"owner": "acme", "repo": "x"}}]})
        assert result[0]["name"] == "unknown"


class TestDispatchSessionPermanentlyDisabled:
    """``dispatch_session()`` assumed Jules could write new code and open a
    PR from a prompt alone -- confirmed false by the repo operator, 2026-08-
    31. It is now permanently disabled: it must raise
    ``JulesCapabilityNotAvailable`` unconditionally, as the very first thing
    it does, regardless of any argument or setting, and must never make a
    network call."""

    def test_raises_capability_not_available_with_confirm_true(self):
        with patch("data.jules_client.requests.get") as get, patch(
            "data.jules_client.requests.post"
        ) as post:
            with pytest.raises(JulesCapabilityNotAvailable):
                dispatch_session(
                    "do the thing",
                    "sources/github/acme/widgets",
                    "main",
                    "Title",
                    confirm=True,
                )
        get.assert_not_called()
        post.assert_not_called()

    def test_raises_capability_not_available_with_default_args(self):
        """confirm/force omitted entirely -- still raises the same error,
        not a confirmation-required error (there is no gate to pass; the
        capability simply doesn't exist)."""
        with patch("data.jules_client.requests.get") as get, patch(
            "data.jules_client.requests.post"
        ) as post:
            with pytest.raises(JulesCapabilityNotAvailable):
                dispatch_session(
                    "do the thing", "sources/github/acme/widgets", "main", "Title"
                )
        get.assert_not_called()
        post.assert_not_called()

    def test_raises_capability_not_available_with_force_true(self):
        with patch("data.jules_client.requests.get") as get, patch(
            "data.jules_client.requests.post"
        ) as post:
            with pytest.raises(JulesCapabilityNotAvailable):
                dispatch_session(
                    "do the thing",
                    "sources/github/acme/widgets",
                    "main",
                    "Title",
                    force=True,
                    confirm=True,
                )
        get.assert_not_called()
        post.assert_not_called()

    def test_raises_even_when_jules_enabled_and_key_configured(self, monkeypatch):
        """Not gated behind JULES_ENABLED/JULES_API_KEY -- it raises before
        any settings are even consulted, because there is no capability to
        gate."""
        monkeypatch.setattr(settings, "JULES_ENABLED", True, raising=False)
        monkeypatch.setattr(settings, "JULES_API_KEY", "test-jules-key", raising=False)
        with patch("data.jules_client.requests.get") as get, patch(
            "data.jules_client.requests.post"
        ) as post:
            with pytest.raises(JulesCapabilityNotAvailable):
                dispatch_session(
                    "do the thing",
                    "sources/github/acme/widgets",
                    "main",
                    "Title",
                    confirm=True,
                )
        get.assert_not_called()
        post.assert_not_called()

    def test_raises_even_when_jules_disabled_or_key_missing(self, monkeypatch):
        """Also raises JulesCapabilityNotAvailable (not JulesUnavailable) in
        the disabled/no-key case -- the unconditional raise happens before
        any settings check, so a disabled/misconfigured integration reports
        the same, more accurate error rather than a settings complaint."""
        monkeypatch.setattr(settings, "JULES_ENABLED", False, raising=False)
        monkeypatch.setattr(settings, "JULES_API_KEY", None, raising=False)
        with patch("data.jules_client.requests.get") as get, patch(
            "data.jules_client.requests.post"
        ) as post:
            with pytest.raises(JulesCapabilityNotAvailable):
                dispatch_session(
                    "do the thing",
                    "sources/github/acme/widgets",
                    "main",
                    "Title",
                    confirm=True,
                )
        get.assert_not_called()
        post.assert_not_called()

    def test_error_message_explains_the_corrected_capability_model(self):
        with pytest.raises(JulesCapabilityNotAvailable, match="does not exist"):
            dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True
            )

    def test_is_a_runtime_error(self):
        """JulesCapabilityNotAvailable subclasses RuntimeError, not
        JulesUnavailable -- this is a structurally different failure (the
        capability doesn't exist at all), not a request-serving failure."""
        with pytest.raises(RuntimeError):
            dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True
            )

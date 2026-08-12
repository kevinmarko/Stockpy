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
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from data.jules_client import (
    JulesConfirmationRequired,
    JulesUnavailable,
    _check_dispatch_dedup,
    _compute_dedup_key,
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

SESSION_PAYLOAD = {
    "name": "sessions/abc123",
    "state": "PENDING",
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


@pytest.fixture()
def isolated_output_dir(tmp_path, monkeypatch):
    """Redirect settings.OUTPUT_DIR so the dispatch ledger never touches the
    real repo's output/ directory."""
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path, raising=False)
    return tmp_path


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


class TestDispatchSessionNullSources:
    """Fix #3, from dispatch_session()'s own perspective: an explicit
    ``{"sources": null}`` GET /sources response must not crash the known-
    sources membership check with a TypeError on ``None`` iteration."""

    def test_null_sources_degrades_to_source_not_connected_not_type_error(
        self, isolated_output_dir
    ):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, {"sources": None})
        ), patch("data.jules_client.requests.post") as post:
            with pytest.raises(JulesUnavailable, match="not in the connected Jules sources"):
                dispatch_session(
                    "do the thing",
                    "sources/github/acme/widgets",
                    "main",
                    "Title",
                    confirm=True,
                )
        post.assert_not_called()


class TestDispatchSessionGates:
    def test_raises_when_jules_disabled(self, monkeypatch, isolated_output_dir):
        monkeypatch.setattr(settings, "JULES_ENABLED", False, raising=False)
        with pytest.raises(JulesUnavailable, match="disabled"):
            dispatch_session("do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)

    def test_raises_when_api_key_unset(self, monkeypatch, isolated_output_dir):
        monkeypatch.setattr(settings, "JULES_API_KEY", None, raising=False)
        with pytest.raises(JulesUnavailable, match="JULES_API_KEY"):
            dispatch_session("do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)

    def test_raises_when_source_not_connected(self, isolated_output_dir):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch("data.jules_client.requests.post") as post:
            with pytest.raises(JulesUnavailable, match="not in the connected Jules sources"):
                dispatch_session(
                    "do the thing", "sources/github/other/repo", "main", "Title", confirm=True)
        post.assert_not_called()


class TestDispatchSessionConfirmGate:
    """Fix #4: the ``confirm=True`` safety gate must be enforced INSIDE
    dispatch_session() itself, not only by caller convention -- so a future
    third caller cannot bypass it by forgetting its own pre-check."""

    def test_confirm_false_raises_without_any_network_call(self, isolated_output_dir):
        with patch("data.jules_client.requests.get") as get, patch(
            "data.jules_client.requests.post"
        ) as post:
            with pytest.raises(JulesConfirmationRequired):
                dispatch_session(
                    "do the thing",
                    "sources/github/acme/widgets",
                    "main",
                    "Title",
                    confirm=False,
                )
        get.assert_not_called()
        post.assert_not_called()

    def test_confirm_omitted_defaults_to_false_and_raises(self, isolated_output_dir):
        """``confirm`` defaults to ``False`` -- omitting it entirely must be
        just as refused as passing it explicitly False."""
        with patch("data.jules_client.requests.post") as post:
            with pytest.raises(JulesConfirmationRequired):
                dispatch_session(
                    "do the thing", "sources/github/acme/widgets", "main", "Title"
                )
        post.assert_not_called()

    def test_confirm_required_error_is_a_jules_unavailable(self, isolated_output_dir):
        """JulesConfirmationRequired subclasses JulesUnavailable so every
        existing ``except JulesUnavailable`` caller boundary keeps working
        unchanged for this new failure mode too."""
        with pytest.raises(JulesUnavailable):
            dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=False
            )

    def test_confirm_true_still_required_to_be_exactly_true(self, isolated_output_dir):
        """A truthy-but-not-``True`` value (e.g. the string ``"yes"``) must
        still be refused -- this is an explicit-opt-in gate, not a generic
        truthiness check."""
        with patch("data.jules_client.requests.post") as post:
            with pytest.raises(JulesConfirmationRequired):
                dispatch_session(
                    "do the thing",
                    "sources/github/acme/widgets",
                    "main",
                    "Title",
                    confirm="yes",  # type: ignore[arg-type]
                )
        post.assert_not_called()


class TestDispatchSessionMalformedJSON:
    """Fix #1: same JulesUnavailable degrade as list_sources(), but on the
    POST /sessions response -- and the ledger must NOT gain an entry for a
    dispatch whose response body could not even be parsed."""

    def test_malformed_post_response_raises_jules_unavailable(self, isolated_output_dir):
        post_resp = _resp(200, {})
        post_resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch("data.jules_client.requests.post", return_value=post_resp):
            with pytest.raises(JulesUnavailable, match="malformed JSON"):
                dispatch_session(
                    "do the thing",
                    "sources/github/acme/widgets",
                    "main",
                    "Title",
                    confirm=True,
                )

        ledger_path = isolated_output_dir / "jules_dispatched.jsonl"
        assert not ledger_path.exists()


class TestDispatchSessionSuccess:
    def test_returns_response_and_writes_ledger(self, isolated_output_dir):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ) as post:
            result = dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)

        assert result == SESSION_PAYLOAD
        assert post.call_count == 1
        body = post.call_args.kwargs["json"]
        assert body["prompt"] == "do the thing"
        assert body["sourceContext"] == {
            "source": "sources/github/acme/widgets",
            "githubRepoContext": {"startingBranch": "main"},
        }
        assert body["automationMode"] == "AUTO_CREATE_PR"
        assert body["title"] == "Title"

        ledger_path = isolated_output_dir / "jules_dispatched.jsonl"
        assert ledger_path.exists()
        lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["source"] == "sources/github/acme/widgets"
        assert record["branch"] == "main"
        assert record["title"] == "Title"
        assert record["session_name"] == "sessions/abc123"

    def test_request_headers(self, isolated_output_dir):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ) as post:
            dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)
        kwargs = post.call_args.kwargs
        assert kwargs["headers"]["X-Goog-Api-Key"] == "test-jules-key"
        assert kwargs["headers"]["Content-Type"] == "application/json"


class TestDispatchSessionFailure:
    def test_no_ledger_entry_on_post_failure(self, isolated_output_dir):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(500, {"error": "boom"}),
        ):
            with pytest.raises(JulesUnavailable):
                dispatch_session(
                    "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)
        ledger_path = isolated_output_dir / "jules_dispatched.jsonl"
        assert not ledger_path.exists()

    def test_raises_on_request_exception(self, isolated_output_dir):
        import requests

        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            with pytest.raises(JulesUnavailable, match="transport error"):
                dispatch_session(
                    "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)


class TestDispatchSessionDedup:
    def test_duplicate_same_day_is_refused_without_second_post(
        self, isolated_output_dir
    ):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ) as post:
            dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)
            assert post.call_count == 1

            with pytest.raises(JulesUnavailable, match="already recorded today"):
                dispatch_session(
                    "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)
            # Second dispatch refused before ever reaching the network.
            assert post.call_count == 1

    def test_force_true_allows_duplicate_dispatch(self, isolated_output_dir):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ) as post:
            dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)
            dispatch_session(
                "do the thing",
                "sources/github/acme/widgets",
                "main",
                "Title",
                force=True, confirm=True)
            assert post.call_count == 2

        ledger_path = isolated_output_dir / "jules_dispatched.jsonl"
        lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_different_day_duplicate_is_allowed(self, isolated_output_dir):
        """Craft a ledger file with a stale (yesterday's) entry for the exact
        same dedup fields' content-hash, and confirm today's identical
        dispatch is NOT treated as a duplicate (the date is part of the
        dedup key, so a different day never collides)."""
        source = "sources/github/acme/widgets"
        branch = "main"
        title = "Title"
        prompt = "do the thing"

        # Compute what today's dedup key would be, then fabricate a
        # yesterday-dated record with a *different* dedup_key (the date
        # prefix differs) to simulate a stale ledger entry.
        stale_key = "2020-01-01:" + _compute_dedup_key(source, branch, title, prompt).split(
            ":", 1
        )[1]
        ledger_path = isolated_output_dir / "jules_dispatched.jsonl"
        ledger_path.write_text(
            json.dumps(
                {
                    "ts": "2020-01-01T00:00:00Z",
                    "dedup_key": stale_key,
                    "source": source,
                    "branch": branch,
                    "title": title,
                    "prompt_hash": "deadbeefdeadbeef",
                    "session_name": "sessions/old",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        # Confirm the dedup check itself doesn't flag today's real key.
        today_key = _compute_dedup_key(source, branch, title, prompt)
        assert today_key != stale_key
        assert _check_dispatch_dedup(today_key) is False

        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ) as post:
            dispatch_session(prompt, source, branch, title, confirm=True)
        assert post.call_count == 1

        lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


class TestDedupLedgerResilience:
    """Dead-letter resilience: a missing/corrupt ledger degrades to 'not a
    duplicate' rather than raising (CONSTRAINT #6)."""

    def test_missing_ledger_file_returns_false(self, isolated_output_dir):
        assert _check_dispatch_dedup("2026-01-01:abc123") is False

    def test_corrupt_ledger_file_returns_false_not_raise(self, isolated_output_dir):
        ledger_path = isolated_output_dir / "jules_dispatched.jsonl"
        ledger_path.write_text("{not valid json\n{also not valid\n", encoding="utf-8")
        assert _check_dispatch_dedup("2026-01-01:abc123") is False

    def test_ledger_with_mixed_valid_and_corrupt_lines(self, isolated_output_dir):
        ledger_path = isolated_output_dir / "jules_dispatched.jsonl"
        good_key = "2026-01-01:goodkey1234567ab"
        ledger_path.write_text(
            "{not valid json\n"
            + json.dumps({"dedup_key": good_key})
            + "\n"
            + "\n"  # blank line tolerated
            + "another garbage line\n",
            encoding="utf-8",
        )
        assert _check_dispatch_dedup(good_key) is True
        assert _check_dispatch_dedup("2026-01-01:missing") is False

    def test_dispatch_still_works_when_ledger_directory_is_unwritable(
        self, isolated_output_dir, monkeypatch
    ):
        """_record_dispatch swallows OSError (best-effort) per its own
        docstring; dispatch_session must still return the real response."""
        import data.jules_client as jules_client_module

        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(
            jules_client_module.Path, "mkdir", _boom, raising=True
        )

        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ):
            result = dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True)
        assert result == SESSION_PAYLOAD


class TestDispatchLedgerLock:
    """Fix #2: the dedup-check -> POST -> ledger-write sequence is wrapped
    in ``_dispatch_lock()`` so two concurrent/retried calls cannot both pass
    the dedup check before either one records its dispatch."""

    def test_lock_file_does_not_survive_a_successful_dispatch(self, isolated_output_dir):
        """The lock file is created and removed around the critical section
        -- it must not be left behind after a normal dispatch."""
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ):
            dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True
            )
        lock_path = isolated_output_dir / "jules_dispatched.jsonl.lock"
        assert not lock_path.exists()

    def test_lock_file_does_not_survive_a_failed_dispatch(self, isolated_output_dir):
        """The lock must be released even when the critical section raises
        (e.g. a duplicate dedup_key) -- otherwise one failed call would
        permanently wedge every future dispatch."""
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ):
            dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True
            )
            with pytest.raises(JulesUnavailable, match="already recorded today"):
                dispatch_session(
                    "do the thing", "sources/github/acme/widgets", "main", "Title", confirm=True
                )
        lock_path = isolated_output_dir / "jules_dispatched.jsonl.lock"
        assert not lock_path.exists()

    def test_held_lock_causes_second_caller_to_time_out(self, isolated_output_dir, monkeypatch):
        """A lock file already held by "another process" (simulated by
        creating it directly, never releasing it) must make a concurrent
        dispatch raise JulesUnavailable rather than block forever or race
        past the dedup check."""
        import os as _os

        import data.jules_client as jules_client_module

        monkeypatch.setattr(jules_client_module, "_LOCK_ACQUIRE_TIMEOUT_SECONDS", 0.2)
        monkeypatch.setattr(jules_client_module, "_LOCK_POLL_INTERVAL_SECONDS", 0.02)

        lock_path = isolated_output_dir / "jules_dispatched.jsonl.lock"
        fd = _os.open(str(lock_path), _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY)
        try:
            with patch(
                "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
            ), patch("data.jules_client.requests.post") as post:
                with pytest.raises(JulesUnavailable, match="Timed out waiting"):
                    dispatch_session(
                        "do the thing",
                        "sources/github/acme/widgets",
                        "main",
                        "Title",
                        confirm=True,
                    )
            post.assert_not_called()
        finally:
            _os.close(fd)
            _os.unlink(lock_path)

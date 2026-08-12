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


class TestDispatchSessionGates:
    def test_raises_when_jules_disabled(self, monkeypatch, isolated_output_dir):
        monkeypatch.setattr(settings, "JULES_ENABLED", False, raising=False)
        with pytest.raises(JulesUnavailable, match="disabled"):
            dispatch_session("do the thing", "sources/github/acme/widgets", "main", "Title")

    def test_raises_when_api_key_unset(self, monkeypatch, isolated_output_dir):
        monkeypatch.setattr(settings, "JULES_API_KEY", None, raising=False)
        with pytest.raises(JulesUnavailable, match="JULES_API_KEY"):
            dispatch_session("do the thing", "sources/github/acme/widgets", "main", "Title")

    def test_raises_when_source_not_connected(self, isolated_output_dir):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch("data.jules_client.requests.post") as post:
            with pytest.raises(JulesUnavailable, match="not in the connected Jules sources"):
                dispatch_session(
                    "do the thing", "sources/github/other/repo", "main", "Title"
                )
        post.assert_not_called()


class TestDispatchSessionSuccess:
    def test_returns_response_and_writes_ledger(self, isolated_output_dir):
        with patch(
            "data.jules_client.requests.get", return_value=_resp(200, SOURCES_PAYLOAD)
        ), patch(
            "data.jules_client.requests.post",
            return_value=_resp(200, SESSION_PAYLOAD),
        ) as post:
            result = dispatch_session(
                "do the thing", "sources/github/acme/widgets", "main", "Title"
            )

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
                "do the thing", "sources/github/acme/widgets", "main", "Title"
            )
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
                    "do the thing", "sources/github/acme/widgets", "main", "Title"
                )
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
                    "do the thing", "sources/github/acme/widgets", "main", "Title"
                )


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
                "do the thing", "sources/github/acme/widgets", "main", "Title"
            )
            assert post.call_count == 1

            with pytest.raises(JulesUnavailable, match="already recorded today"):
                dispatch_session(
                    "do the thing", "sources/github/acme/widgets", "main", "Title"
                )
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
                "do the thing", "sources/github/acme/widgets", "main", "Title"
            )
            dispatch_session(
                "do the thing",
                "sources/github/acme/widgets",
                "main",
                "Title",
                force=True,
            )
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
            dispatch_session(prompt, source, branch, title)
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
                "do the thing", "sources/github/acme/widgets", "main", "Title"
            )
        assert result == SESSION_PAYLOAD

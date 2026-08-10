"""Tests for scripts/manage_oauth_users.py -- the CLI that provisions named
login credentials for the MCP OAuth authorization server's multi-user login
path (mcp_oauth_store.OAuthUser).

Calls the underlying command functions directly (not subprocess+getpass),
mocking ``manage_oauth_users._getpass`` in place -- matches this repo's
convention for testing a script's logic without a real terminal.
"""

import pytest

from mcp_oauth_store import McpOAuthStore
from scripts import manage_oauth_users


def _store() -> McpOAuthStore:
    return McpOAuthStore(db_url="sqlite:///:memory:")


def _mock_getpass(monkeypatch: pytest.MonkeyPatch, *responses: str):
    """Feeds successive ``getpass.getpass()`` calls the given responses in
    order (e.g. password then confirmation)."""
    it = iter(responses)

    def _fake_getpass(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration:  # pragma: no cover - test-authoring bug guard
            raise AssertionError("getpass called more times than mocked responses provided")

    monkeypatch.setattr(manage_oauth_users, "_getpass", _fake_getpass)


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_cmd_add_creates_user_with_hashed_password(monkeypatch: pytest.MonkeyPatch):
    store = _store()
    _mock_getpass(monkeypatch, "hunter2", "hunter2")

    rc = manage_oauth_users.cmd_add(store, "alice", "Alice A.")

    assert rc == 0
    row = store.get_user("alice")
    assert row is not None
    assert row["display_name"] == "Alice A."
    # Never stores the raw password -- it's hashed.
    assert row["password_hash"] != "hunter2"
    assert row["password_hash"].startswith("scrypt$")


def test_cmd_add_mismatched_confirmation_exits_nonzero(monkeypatch: pytest.MonkeyPatch):
    store = _store()
    _mock_getpass(monkeypatch, "hunter2", "different")

    with pytest.raises(SystemExit) as exc_info:
        manage_oauth_users.cmd_add(store, "alice", None)
    assert exc_info.value.code != 0
    assert store.get_user("alice") is None


def test_cmd_add_empty_password_exits_nonzero(monkeypatch: pytest.MonkeyPatch):
    store = _store()
    _mock_getpass(monkeypatch, "")

    with pytest.raises(SystemExit) as exc_info:
        manage_oauth_users.cmd_add(store, "alice", None)
    assert exc_info.value.code != 0
    assert store.get_user("alice") is None


def test_cmd_add_duplicate_username_returns_error_code(monkeypatch: pytest.MonkeyPatch):
    store = _store()
    _mock_getpass(monkeypatch, "hunter2", "hunter2")
    manage_oauth_users.cmd_add(store, "alice", None)

    _mock_getpass(monkeypatch, "another-pw", "another-pw")
    rc = manage_oauth_users.cmd_add(store, "alice", None)

    assert rc == 1


def test_cmd_add_rejects_reserved_sentinel_username(monkeypatch: pytest.MonkeyPatch):
    store = _store()
    _mock_getpass(monkeypatch, "hunter2", "hunter2")

    rc = manage_oauth_users.cmd_add(store, "__single_password__", None)

    assert rc == 1


# ---------------------------------------------------------------------------
# deactivate / reactivate
# ---------------------------------------------------------------------------


def test_cmd_deactivate_and_reactivate_round_trip(monkeypatch: pytest.MonkeyPatch):
    store = _store()
    _mock_getpass(monkeypatch, "hunter2", "hunter2")
    manage_oauth_users.cmd_add(store, "alice", None)

    rc = manage_oauth_users.cmd_deactivate(store, "alice")
    assert rc == 0
    assert store.get_user("alice")["is_active"] is False

    rc = manage_oauth_users.cmd_reactivate(store, "alice")
    assert rc == 0
    assert store.get_user("alice")["is_active"] is True


def test_cmd_deactivate_unknown_user_returns_error_code():
    store = _store()
    rc = manage_oauth_users.cmd_deactivate(store, "nope")
    assert rc == 1


def test_cmd_reactivate_unknown_user_returns_error_code():
    store = _store()
    rc = manage_oauth_users.cmd_reactivate(store, "nope")
    assert rc == 1


# ---------------------------------------------------------------------------
# list -- never prints a password hash
# ---------------------------------------------------------------------------


def test_cmd_list_never_prints_password_hash(monkeypatch: pytest.MonkeyPatch, capsys):
    store = _store()
    _mock_getpass(monkeypatch, "hunter2", "hunter2")
    manage_oauth_users.cmd_add(store, "alice", "Alice A.")

    stored_hash = store.get_user("alice")["password_hash"]

    rc = manage_oauth_users.cmd_list(store)
    captured = capsys.readouterr()

    assert rc == 0
    assert "alice" in captured.out
    assert "Alice A." in captured.out
    assert stored_hash not in captured.out
    assert "scrypt$" not in captured.out


def test_cmd_list_empty_store(capsys):
    store = _store()
    rc = manage_oauth_users.cmd_list(store)
    captured = capsys.readouterr()

    assert rc == 0
    assert "no oauth users" in captured.out.lower()


# ---------------------------------------------------------------------------
# reset-password
# ---------------------------------------------------------------------------


def test_cmd_reset_password_updates_hash(monkeypatch: pytest.MonkeyPatch):
    store = _store()
    _mock_getpass(monkeypatch, "old-pw", "old-pw")
    manage_oauth_users.cmd_add(store, "alice", None)
    old_hash = store.get_user("alice")["password_hash"]

    _mock_getpass(monkeypatch, "new-pw", "new-pw")
    rc = manage_oauth_users.cmd_reset_password(store, "alice")

    assert rc == 0
    new_hash = store.get_user("alice")["password_hash"]
    assert new_hash != old_hash

    from mcp_oauth_password import verify_password

    assert verify_password("new-pw", new_hash) is True
    assert verify_password("old-pw", new_hash) is False


def test_cmd_reset_password_unknown_user_returns_error_code():
    store = _store()
    rc = manage_oauth_users.cmd_reset_password(store, "nope")
    assert rc == 1


# ---------------------------------------------------------------------------
# CLI parser wiring
# ---------------------------------------------------------------------------


def test_build_parser_add_subcommand():
    parser = manage_oauth_users.build_parser()
    args = parser.parse_args(["add", "alice", "--display-name", "Alice A."])
    assert args.command == "add"
    assert args.username == "alice"
    assert args.display_name == "Alice A."


def test_build_parser_list_subcommand():
    parser = manage_oauth_users.build_parser()
    args = parser.parse_args(["list"])
    assert args.command == "list"


def test_build_parser_requires_a_subcommand():
    parser = manage_oauth_users.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])

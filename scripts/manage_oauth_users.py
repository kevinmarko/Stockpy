"""CLI to provision/manage named login credentials for the MCP OAuth
authorization server's multi-user login path (``settings.MCP_OAUTH_MULTI_USER_ENABLED``,
``mcp_oauth_store.OAuthUser``, ``mcp_oauth_provider.py``).

Why a local CLI, not an HTTP endpoint
---------------------------------------
Every credential-provisioning action in this codebase today is hand-run,
operator-local (``.env`` edits for ``MCP_OAUTH_PASSWORD``,
``ROBINHOOD_USERNAME``/``PASSWORD``). There is no precedent for an HTTP
endpoint that creates login credentials for other humans -- that would be
new, unreviewed attack surface for something a local CLI does more simply
and with zero network exposure. See ``oauth_multi_user_plan.md`` §2.

Usage
-----
    python scripts/manage_oauth_users.py add <username> [--display-name NAME]
    python scripts/manage_oauth_users.py deactivate <username>
    python scripts/manage_oauth_users.py reactivate <username>
    python scripts/manage_oauth_users.py list
    python scripts/manage_oauth_users.py reset-password <username>

Password is ALWAYS collected via ``getpass.getpass()``, never a CLI argument
-- avoids shell-history/``ps`` leakage, matching this codebase's
``SECRET_KEYS`` discipline (CONSTRAINT #3). ``list`` never prints a
password hash.

``deactivate`` does NOT cascade-revoke already-issued tokens in this
version -- a documented gap, not a silently-missing feature; a future
``revoke-all-tokens`` subcommand is a cheap, clearly-scoped addition if
ever needed.
"""

import argparse
import sys
from pathlib import Path

# Repo-root import shim so `python scripts/manage_oauth_users.py` works from
# anywhere -- WITHOUT it, direct-path invocation dies with
# `ModuleNotFoundError` because `python scripts/x.py` puts scripts/ on
# sys.path[0], not the repo root. Mirrors scripts/backfill_edgar_fundamentals.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading -- must run before any third-party/project
# import below (see scripts/_bootstrap.py's module docstring for why). Not
# imported by any other module as a library (verified by grep) -- true
# module-top placement, matching the majority of scripts/*.py entry points.
from scripts._bootstrap import bootstrap  # noqa: E402

bootstrap()

from mcp_oauth_password import hash_password  # noqa: E402
from mcp_oauth_store import McpOAuthStore  # noqa: E402

try:
    import getpass as _getpass_module

    _getpass = _getpass_module.getpass
except ImportError:  # pragma: no cover - getpass is stdlib, always present
    _getpass = None


def _prompt_password(*, confirm: bool) -> str:
    """Collects a password via ``getpass.getpass()`` -- never a CLI arg.

    ``confirm=True`` (used by ``add``/``reset-password``) prompts twice and
    refuses to proceed on a mismatch, mirroring the standard "confirm your
    new password" UX. Never echoes the value back to the terminal.
    """
    password = _getpass("Password: ")
    if not password:
        print("Error: password must be non-empty.", file=sys.stderr)
        sys.exit(1)
    if confirm:
        confirmation = _getpass("Confirm password: ")
        if password != confirmation:
            print("Error: passwords do not match.", file=sys.stderr)
            sys.exit(1)
    return password


def cmd_add(store: McpOAuthStore, username: str, display_name: str | None) -> int:
    password = _prompt_password(confirm=True)
    password_hash = hash_password(password)
    try:
        store.create_user(username, password_hash, display_name=display_name)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Created OAuth user {username!r}.")
    return 0


def cmd_deactivate(store: McpOAuthStore, username: str) -> int:
    if not store.set_user_active(username, False):
        print(f"Error: no such OAuth user {username!r}.", file=sys.stderr)
        return 1
    print(
        f"Deactivated OAuth user {username!r}. Note: already-issued tokens "
        "are NOT revoked (see this script's module docstring)."
    )
    return 0


def cmd_reactivate(store: McpOAuthStore, username: str) -> int:
    if not store.set_user_active(username, True):
        print(f"Error: no such OAuth user {username!r}.", file=sys.stderr)
        return 1
    print(f"Reactivated OAuth user {username!r}.")
    return 0


def cmd_list(store: McpOAuthStore) -> int:
    users = store.list_users()
    if not users:
        print("No OAuth users provisioned yet.")
        return 0
    # Never prints password_hash.
    header = f"{'username':<24} {'active':<8} {'display_name':<24}"
    print(header)
    print("-" * len(header))
    for user in users:
        display_name = user["display_name"] or ""
        print(f"{user['username']:<24} {str(user['is_active']):<8} {display_name:<24}")
    return 0


def cmd_reset_password(store: McpOAuthStore, username: str) -> int:
    if store.get_user(username) is None:
        print(f"Error: no such OAuth user {username!r}.", file=sys.stderr)
        return 1
    password = _prompt_password(confirm=True)
    password_hash = hash_password(password)
    store.update_user_password(username, password_hash)
    print(f"Reset password for OAuth user {username!r}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage named login credentials for the MCP OAuth authorization server."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Provision a new named credential.")
    add_parser.add_argument("username")
    add_parser.add_argument("--display-name", default=None)

    deactivate_parser = subparsers.add_parser("deactivate", help="Deactivate a credential.")
    deactivate_parser.add_argument("username")

    reactivate_parser = subparsers.add_parser("reactivate", help="Reactivate a credential.")
    reactivate_parser.add_argument("username")

    subparsers.add_parser("list", help="List all provisioned credentials (never prints a hash).")

    reset_parser = subparsers.add_parser("reset-password", help="Reset a credential's password.")
    reset_parser.add_argument("username")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    store = McpOAuthStore()

    if args.command == "add":
        return cmd_add(store, args.username, args.display_name)
    if args.command == "deactivate":
        return cmd_deactivate(store, args.username)
    if args.command == "reactivate":
        return cmd_reactivate(store, args.username)
    if args.command == "list":
        return cmd_list(store)
    if args.command == "reset-password":
        return cmd_reset_password(store, args.username)

    parser.error(f"Unknown command: {args.command}")  # pragma: no cover - argparse enforces choices
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""
prompt_registry/__main__.py
============================
CLI for the Prompt Registry.

All commands call ``main(argv)`` so tests can inject argument lists directly
without spawning a subprocess.  Every command is dead-letter tolerant: any
uncaught exception produces a clean error line to stderr and a non-zero exit
code — tracebacks never surface to the operator.

Usage::

    python -m prompt_registry <command> [args]

Commands
--------
list
    Table of prompt IDs with pinned / remote-latest / newest-cached versions.

get <id> [--version v] [--raw]
    Print a resolved prompt body.  Without ``--version``, the full resolution
    chain is used.  With ``--version``, only the manifest / cache / baseline is
    searched for that specific version.

sync
    Fetch the remote manifest, verify every version (signature + guardrails),
    and pre-warm the disk cache.  Requires the registry to be enabled and a
    store to be configured.

pin <id> <version>
    Pin a specific cached / remote version as the preferred one.  Writes to
    ``.env`` via ``gui.env_io`` (CONSTRAINT #3 — only allowlisted keys).

rollback <id>
    Point the in-memory pin to the previous cached version and persist via
    ``gui.env_io``.

diff <id> <vA> <vB>
    Unified diff between two versions.  Use ``baseline`` as a version keyword
    to refer to the committed baseline file.

verify [<id>]
    Re-check signatures and guardrails for all cached versions of ``<id>``
    (or every known ID when omitted).  Exits non-zero on any failure.

publish <id> <file> --version <ver> [--author X] [--notes X]
    Sign ``<file>`` with ``PROMPT_REGISTRY_SIGNING_KEY`` and push it to the
    remote store.  Requires ``PROMPT_REGISTRY_PUBLISH_TOKEN``.  Exits non-zero
    when either credential is absent.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Internal imports
# ---------------------------------------------------------------------------

from prompt_registry.cache import CacheManager, list_baseline_ids, read_baseline
from prompt_registry.guardrails import validate_prompt
from prompt_registry.models import PromptRecord
from prompt_registry.registry import PromptRegistry, get_registry, reset_registry
from prompt_registry.signing import compute_sha256, sign, verify
from prompt_registry.store import ReadOnlyStoreError, RegistryFetchError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_body_for_version(
    reg: PromptRegistry,
    prompt_id: str,
    version: str,
) -> Optional[str]:
    """Locate the body for a *specific* version.

    Search order: manifest (fastest after a sync) → disk cache → baseline
    (only when *version* is ``"baseline"``).

    Returns ``None`` when not found — callers must handle this and exit
    non-zero rather than returning the sentinel string.
    """
    if version == "baseline":
        return read_baseline(prompt_id)

    # Manifest
    if reg._manifest is not None:
        record = reg._manifest.get_prompt(prompt_id, version)
        if record is not None:
            return record.body

    # Disk cache
    record = reg._cache.read(prompt_id, version)
    if record is not None:
        return record.body

    return None


def _all_known_ids(reg: PromptRegistry) -> List[str]:
    """Union of IDs known to the registry: baseline + manifest + pins."""
    ids: set[str] = set(list_baseline_ids())
    if reg._manifest is not None:
        ids.update(reg._manifest.prompts.keys())
    ids.update(reg._pins.keys())
    return sorted(ids)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

def cmd_list(reg: PromptRegistry) -> int:
    """Print a table of known prompt IDs with version metadata."""
    ids = _all_known_ids(reg)
    if not ids:
        print("No prompts registered.")
        return 0

    rows = []
    for pid in ids:
        pinned = reg._pins.get(pid, "—")
        latest = "—"
        if reg._manifest is not None:
            pv = reg._manifest.prompts.get(pid)
            if pv is not None:
                latest = pv.latest
        cached = reg._cache.list_versions(pid)
        newest = cached[0] if cached else "—"
        has_baseline = read_baseline(pid) is not None
        source = "baseline" if has_baseline else "cache"
        rows.append((pid, pinned, latest, newest, source))

    # Dynamic column width for the ID column
    w = max(len(r[0]) for r in rows)
    header = (
        f"{'Prompt ID':<{w}}  {'Pinned':>10}  {'Remote':>10}  "
        f"{'Cached':>10}  Source"
    )
    print(header)
    print("─" * len(header))
    for pid, pinned, latest, newest, source in rows:
        print(f"{pid:<{w}}  {pinned:>10}  {latest:>10}  {newest:>10}  {source}")

    return 0


def cmd_get(
    reg: PromptRegistry,
    prompt_id: str,
    version: Optional[str],
    raw: bool,
) -> int:
    """Print a prompt body, resolved or version-specific."""
    if version is not None:
        body = _resolve_body_for_version(reg, prompt_id, version)
        if body is None:
            print(
                f"Error: version {version!r} of {prompt_id!r} not found "
                "in manifest, cache, or baseline.",
                file=sys.stderr,
            )
            return 1
    else:
        body = reg.get(prompt_id)
        if body.startswith("[PROMPT UNAVAILABLE"):
            print(
                f"Error: {prompt_id!r} has no body in the registry, "
                "cache, or committed baseline.",
                file=sys.stderr,
            )
            return 1

    if not raw:
        ver_label = f" @ {version}" if version else " (resolved)"
        print(f"# {prompt_id}{ver_label}")
        print()

    print(body)
    return 0


def cmd_sync(reg: PromptRegistry) -> int:
    """Fetch the remote manifest and pre-warm the cache."""
    if not reg._enabled:
        print(
            "Registry is disabled (PROMPT_REGISTRY_ENABLED=false). "
            "Nothing to sync.",
            file=sys.stderr,
        )
        return 1

    if reg._store is None:
        print(
            "No remote store configured. "
            "Set PROMPT_REGISTRY_URL and PROMPT_REGISTRY_ENABLED=true.",
            file=sys.stderr,
        )
        return 1

    ok = reg.sync()
    if ok:
        print("Sync complete.")
        if reg._manifest is not None:
            print(f"  Manifest version : {reg._manifest.registry_version}")
            print(f"  Prompts in manifest : {len(reg._manifest.prompts)}")
        return 0
    else:
        print(
            "Sync failed. Check logs for details.",
            file=sys.stderr,
        )
        return 1


def cmd_pin(reg: PromptRegistry, prompt_id: str, version: str) -> int:
    """Pin a prompt ID to a specific version and persist to .env."""
    # Verify the version is accessible before committing the pin
    body = _resolve_body_for_version(reg, prompt_id, version)
    if body is None:
        print(
            f"Error: version {version!r} of {prompt_id!r} not found "
            "in manifest or cache.",
            file=sys.stderr,
        )
        print(
            "Run 'sync' first to populate the cache, "
            "or check the version string.",
            file=sys.stderr,
        )
        return 1

    # Update the in-memory pin
    reg._pins[prompt_id] = version

    # Persist to .env via gui.env_io (allowlist-bounded)
    try:
        from gui.env_io import write_setting  # noqa: PLC0415
        pins_json = json.dumps(dict(sorted(reg._pins.items())))
        write_setting("PROMPT_REGISTRY_PINS", pins_json)
        print(f"Pinned {prompt_id!r} → {version!r}  (saved to .env).")
    except Exception as exc:
        # The in-memory pin is live; the .env write may fail when
        # PROMPT_REGISTRY_PINS has not yet been added to ALLOWED_KEYS (Stage 6).
        print(
            f"Warning: in-memory pin set but .env write failed: {exc}",
            file=sys.stderr,
        )
        print(
            f"  Add  PROMPT_REGISTRY_PINS={json.dumps({prompt_id: version})!r}  "
            "to .env manually, or upgrade to Stage 6.",
            file=sys.stderr,
        )
        print(f"Pinned {prompt_id!r} → {version!r}  (in-memory only this session).")

    return 0


def cmd_rollback(reg: PromptRegistry, prompt_id: str) -> int:
    """Roll back to the previous cached version."""
    previous = reg.rollback(prompt_id)
    if previous is None:
        print(
            f"Cannot roll back {prompt_id!r}: "
            "no older cached version available.",
            file=sys.stderr,
        )
        return 1

    # Persist to .env via gui.env_io
    try:
        from gui.env_io import write_setting  # noqa: PLC0415
        pins_json = json.dumps(dict(sorted(reg._pins.items())))
        write_setting("PROMPT_REGISTRY_PINS", pins_json)
        print(f"Rolled back {prompt_id!r} → {previous!r}  (saved to .env).")
    except Exception as exc:
        print(
            f"Warning: rollback set to {previous!r} in-memory but .env write failed: {exc}",
            file=sys.stderr,
        )
        print(f"Rolled back {prompt_id!r} → {previous!r}  (in-memory only this session).")

    return 0


def cmd_diff(
    reg: PromptRegistry,
    prompt_id: str,
    version_a: str,
    version_b: str,
) -> int:
    """Print a unified diff between two versions."""
    body_a = _resolve_body_for_version(reg, prompt_id, version_a)
    body_b = _resolve_body_for_version(reg, prompt_id, version_b)

    if body_a is None:
        print(
            f"Error: version {version_a!r} of {prompt_id!r} not found.",
            file=sys.stderr,
        )
        return 1
    if body_b is None:
        print(
            f"Error: version {version_b!r} of {prompt_id!r} not found.",
            file=sys.stderr,
        )
        return 1

    lines_a = body_a.splitlines(keepends=True)
    lines_b = body_b.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        lines_a,
        lines_b,
        fromfile=f"{prompt_id}@{version_a}",
        tofile=f"{prompt_id}@{version_b}",
        lineterm="",
    ))

    if not diff:
        print(f"No differences between {version_a!r} and {version_b!r}.")
        return 0

    for line in diff:
        print(line)
    return 0


def cmd_verify(reg: PromptRegistry, prompt_id: Optional[str]) -> int:
    """Re-check signatures and guardrails for cached versions.

    Exit 0 only when every checked version passes both gates.
    Exit 1 when any cached version fails, or when verify is called with a
    specific ID that has no cached versions at all.
    """
    signing_key = reg._signing_key

    ids_to_check = [prompt_id] if prompt_id is not None else _all_known_ids(reg)

    all_pass = True
    total_checked = 0
    total_failed = 0

    for pid in ids_to_check:
        versions = reg._cache.list_versions(pid)
        if not versions:
            if prompt_id is not None:
                # Single-ID verify with no cached data → treat as failure
                print(
                    f"  {pid}: no cached versions to verify.",
                    file=sys.stderr,
                )
                all_pass = False
                total_failed += 1
            else:
                print(f"  {pid}: no cached versions — skipped")
            continue

        for ver in versions:
            record = reg._cache.read(pid, ver)
            if record is None:
                print(f"  {pid}@{ver}: FAIL — could not read cache entry")
                all_pass = False
                total_failed += 1
                continue

            total_checked += 1
            issues: list[str] = []

            if signing_key:
                if not verify(record.body, record.signature, signing_key):
                    issues.append("HMAC-SHA256 signature mismatch")

            ok, guard_issues = validate_prompt(pid, record.body)
            if not ok:
                issues.extend(guard_issues)

            if issues:
                joined = "; ".join(issues)
                print(f"  {pid}@{ver}: FAIL — {joined}")
                all_pass = False
                total_failed += 1
            else:
                print(f"  {pid}@{ver}: OK")

    if total_checked == 0 and total_failed == 0 and prompt_id is None:
        print("No cached versions to verify.")
        return 0

    print(
        f"\nChecked {total_checked + total_failed} version(s): "
        f"{total_checked} passed, {total_failed} failed."
    )
    return 0 if all_pass else 1


def cmd_publish(
    reg: PromptRegistry,
    prompt_id: str,
    file_path: str,
    version: str,
    author: str,
    notes: str,
) -> int:
    """Sign and publish a new prompt version to the remote store.

    Exits non-zero without a traceback when:
    - ``PROMPT_REGISTRY_PUBLISH_TOKEN`` is absent
    - ``PROMPT_REGISTRY_SIGNING_KEY`` is absent
    - the body fails guardrail validation
    - no remote store is configured
    - the store raises ``ReadOnlyStoreError``
    """
    # ── Credential gates ────────────────────────────────────────────────────
    publish_token = os.environ.get("PROMPT_REGISTRY_PUBLISH_TOKEN") or None
    if not publish_token:
        print(
            "Error: PROMPT_REGISTRY_PUBLISH_TOKEN is not set.\n"
            "This credential is required to publish new versions.\n"
            "Set it in .env  (secrets are never GUI-writable — CONSTRAINT #3).",
            file=sys.stderr,
        )
        return 1

    signing_key = os.environ.get("PROMPT_REGISTRY_SIGNING_KEY") or None
    if not signing_key:
        print(
            "Error: PROMPT_REGISTRY_SIGNING_KEY is not set.\n"
            "All published versions must be signed.",
            file=sys.stderr,
        )
        return 1

    # ── Read body ───────────────────────────────────────────────────────────
    try:
        body = Path(file_path).read_text(encoding="utf-8")
    except Exception as exc:
        print(f"Error: cannot read {file_path!r}: {exc}", file=sys.stderr)
        return 1

    # ── Guardrail pre-check ─────────────────────────────────────────────────
    ok, guard_issues = validate_prompt(prompt_id, body)
    if not ok:
        print(
            f"Error: {file_path!r} fails guardrail checks:",
            file=sys.stderr,
        )
        for issue in guard_issues:
            print(f"  • {issue}", file=sys.stderr)
        print(
            "Fix the body and try again.",
            file=sys.stderr,
        )
        return 1

    # ── Sign ────────────────────────────────────────────────────────────────
    sha = compute_sha256(body)
    sig = sign(body, signing_key)
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # ── Store check ─────────────────────────────────────────────────────────
    if reg._store is None:
        print(
            "Error: no remote store configured. "
            "Set PROMPT_REGISTRY_URL and PROMPT_REGISTRY_ENABLED=true.",
            file=sys.stderr,
        )
        return 1

    # ── Publish ─────────────────────────────────────────────────────────────
    try:
        reg._store.publish(
            prompt_id,
            version,
            body,
            sha,
            sig,
            author=author,
            notes=notes,
            created_at=created_at,
        )
    except ReadOnlyStoreError as exc:
        print(f"Error: store does not support publishing — {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error publishing: {exc}", file=sys.stderr)
        return 1

    print(f"Published {prompt_id!r} @ {version!r}")
    print(f"  SHA-256   : {sha}")
    print(f"  Signature : {sig[:24]}…")
    print(f"  Timestamp : {created_at}")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m prompt_registry",
        description="Prompt Registry — versioned, cryptographically-signed AI prompts.",
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # list
    sub.add_parser(
        "list",
        help="Table of prompt IDs with pinned / remote-latest / newest-cached versions.",
    )

    # get
    p_get = sub.add_parser("get", help="Print a resolved prompt body.")
    p_get.add_argument("id", help="Prompt registry ID (e.g. gravity.system).")
    p_get.add_argument(
        "--version", "-v",
        default=None,
        metavar="VER",
        help="Specific version (e.g. 1.0.0 or 'baseline'). Default: full resolution chain.",
    )
    p_get.add_argument(
        "--raw",
        action="store_true",
        help="Suppress the header comment line — print body text only.",
    )

    # sync
    sub.add_parser(
        "sync",
        help="Fetch the remote manifest and pre-warm the disk cache.",
    )

    # pin
    p_pin = sub.add_parser(
        "pin",
        help="Pin a prompt ID to a specific version.",
    )
    p_pin.add_argument("id", help="Prompt registry ID.")
    p_pin.add_argument("version", help="Version to pin (e.g. 1.2.3).")

    # rollback
    p_rb = sub.add_parser(
        "rollback",
        help="Roll back to the previous cached version.",
    )
    p_rb.add_argument("id", help="Prompt registry ID.")

    # diff
    p_diff = sub.add_parser(
        "diff",
        help="Unified diff between two versions of a prompt.",
    )
    p_diff.add_argument("id", help="Prompt registry ID.")
    p_diff.add_argument("version_a", metavar="vA", help="From-version (or 'baseline').")
    p_diff.add_argument("version_b", metavar="vB", help="To-version (or 'baseline').")

    # verify
    p_verify = sub.add_parser(
        "verify",
        help="Re-check signatures and guardrails for cached versions.",
    )
    p_verify.add_argument(
        "id",
        nargs="?",
        default=None,
        help="Specific prompt ID (default: all known IDs).",
    )

    # publish
    p_pub = sub.add_parser(
        "publish",
        help="Sign a file and push it to the remote store (publish credentials required).",
    )
    p_pub.add_argument("id", help="Prompt registry ID.")
    p_pub.add_argument("file", help="Path to the file containing the prompt body.")
    p_pub.add_argument(
        "--version", "-v",
        required=True,
        metavar="VER",
        help="Version string to publish (e.g. 1.1.0).",
    )
    p_pub.add_argument("--author", default="", help="Author identifier.")
    p_pub.add_argument("--notes", default="", help="Changelog note.")

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Parameters
    ----------
    argv:
        Argument list (default: ``sys.argv[1:]``).  Pass a list in tests to
        avoid subprocess overhead.

    Returns
    -------
    int
        Exit code — 0 = success, non-zero = failure.  **Never raises** —
        all exceptions are caught and printed as clean error lines.
    """
    parser = _build_parser()

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0

    if args.command is None:
        parser.print_help()
        return 1

    try:
        reg = get_registry()

        if args.command == "list":
            return cmd_list(reg)
        if args.command == "get":
            return cmd_get(reg, args.id, args.version, args.raw)
        if args.command == "sync":
            return cmd_sync(reg)
        if args.command == "pin":
            return cmd_pin(reg, args.id, args.version)
        if args.command == "rollback":
            return cmd_rollback(reg, args.id)
        if args.command == "diff":
            return cmd_diff(reg, args.id, args.version_a, args.version_b)
        if args.command == "verify":
            return cmd_verify(reg, args.id)
        if args.command == "publish":
            return cmd_publish(
                reg, args.id, args.file, args.version, args.author, args.notes
            )

        print(f"Error: unknown command {args.command!r}.", file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:
        # argparse calls sys.exit() on --help or invalid command; surface the code.
        return int(exc.code) if exc.code is not None else 0
    except Exception as exc:
        # Dead-letter tolerance — no traceback, clean one-line message
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

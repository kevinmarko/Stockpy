"""
scripts/build_local_prompt_registry.py
=======================================
Seeds a signed ``registry.json`` manifest for the Prompt Registry's
``local`` backend (``PROMPT_REGISTRY_BACKEND=local``) from the committed
baseline prompt bodies in ``prompt_registry/baseline/``.

Why this exists
----------------
``prompt_registry/__main__.py``'s ``publish`` CLI command signs a body and
calls the configured store's ``publish()`` method — but none of the three
concrete stores (``LocalJSONStore``, ``HTTPStore``, ``FirestoreStore``)
override that method, so it always raises ``ReadOnlyStoreError`` today.
Until that's implemented, this script is the practical way to hand-author a
valid signed manifest for local/offline use: it computes the same
``sha256`` + ``HMAC-SHA256`` signature ``publish`` would have, using
``prompt_registry.signing`` directly.

What it does
------------
For every prompt ID in ``prompt_registry.cache.list_baseline_ids()``, reads
the baseline body and wraps it as version ``1.0.0`` (or ``--version``) in a
``RegistryManifest``, matching the exact on-wire schema documented in
``prompt_registry/models.py``. Writes the result as JSON to
``--output`` (default: ``output/prompt_registry_local.json``).

This is a **one-time seed** — the resulting file is not meant to track the
baseline automatically. To publish a real update later, edit the generated
JSON by hand (bump the version, recompute sha256 + signature via
``prompt_registry.signing.sign()``), or re-run this script to reset back to
the baseline bodies.

Usage
-----
    python -m scripts.build_local_prompt_registry
    python -m scripts.build_local_prompt_registry --output output/my_registry.json --version 1.0.0

Requires ``PROMPT_REGISTRY_SIGNING_KEY`` to be set (in ``.env`` or the shell
environment) so the generated signatures verify against the same key the
running platform will use to check them.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from prompt_registry.cache import list_baseline_ids, read_baseline
from prompt_registry.guardrails import validate_prompt
from prompt_registry.models import PromptRecord, PromptVersion, RegistryManifest
from prompt_registry.signing import compute_sha256, sign

_DEFAULT_OUTPUT = Path("output") / "prompt_registry_local.json"
_DEFAULT_VERSION = "1.0.0"


def build_manifest(
    signing_key: str,
    *,
    version: str = _DEFAULT_VERSION,
    author: str = "local-setup",
    notes: str = "Seeded from committed baseline",
) -> RegistryManifest:
    """Build a signed :class:`RegistryManifest` from every baseline prompt.

    Skips (with a printed warning) any baseline body that fails the
    guardrail pre-check (``prompt_registry.guardrails.validate_prompt``) —
    the same check ``publish`` runs — rather than seeding a manifest entry
    that would fail verification on read.
    """
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    prompts: dict[str, PromptVersion] = {}

    for prompt_id in list_baseline_ids():
        body = read_baseline(prompt_id)
        if body is None:
            print(f"  skip {prompt_id!r}: no baseline body found", file=sys.stderr)
            continue

        ok, issues = validate_prompt(prompt_id, body)
        if not ok:
            print(f"  skip {prompt_id!r}: fails guardrail checks: {issues}", file=sys.stderr)
            continue

        sha = compute_sha256(body)
        sig = sign(body, signing_key)
        record = PromptRecord(
            body=body,
            sha256=sha,
            signature=sig,
            created_at=created_at,
            author=author,
            notes=notes,
        )
        prompts[prompt_id] = PromptVersion(latest=version, versions={version: record})
        print(f"  seeded {prompt_id!r} @ {version} (sha256={sha[:12]}…)")

    return RegistryManifest(
        registry_version=created_at,
        signing_alg="HMAC-SHA256",
        prompts=prompts,
    )


def write_manifest(manifest: RegistryManifest, output_path: Path) -> None:
    """Write *manifest* to *output_path* as pretty-printed JSON (parent dirs created)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point: ``python -m scripts.build_local_prompt_registry``."""
    load_dotenv(override=False)

    parser = argparse.ArgumentParser(
        description="Seed a signed registry.json for the Prompt Registry's local backend "
                     "from the committed baseline prompts.",
    )
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help=f"Output path for the generated manifest. Default: {_DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--version",
        default=_DEFAULT_VERSION,
        help=f"Version string to assign every seeded prompt. Default: {_DEFAULT_VERSION}",
    )
    args = parser.parse_args(argv)

    signing_key = os.environ.get("PROMPT_REGISTRY_SIGNING_KEY")
    if not signing_key:
        print(
            "Error: PROMPT_REGISTRY_SIGNING_KEY is not set.\n"
            "Set it in .env first so the generated signatures verify against "
            "the same key the running platform will check them with.",
            file=sys.stderr,
        )
        return 1

    print(f"Seeding local prompt registry (version={args.version})…")
    manifest = build_manifest(signing_key, version=args.version)

    if not manifest.prompts:
        print("Error: no prompts were seeded (all failed guardrails or had no baseline).", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    write_manifest(manifest, output_path)
    print(f"\nWrote {len(manifest.prompts)} prompt(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

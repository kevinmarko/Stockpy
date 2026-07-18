"""scripts/build_command_manifest.py — regenerate cli_introspect/command_manifest.json.

Offline build step (run manually, like ``scripts/build_ticker_sector_map.py``).
Introspects every entry point in ``cli_introspect.targets.TARGETS`` — each in an
isolated subprocess so their heavy imports never touch this process — and writes
the flat JSON manifest that shell completion and the Pilots PWA consume.

Dead-letter, don't crash: an entry point that fails to introspect (import error,
timeout, exits before parse_args) is logged and listed under ``dead_letters`` in
the manifest, never aborting the whole build.

    python scripts/build_command_manifest.py
    python scripts/build_command_manifest.py --json   # print the manifest too
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cli_introspect.capture import capture_command
from cli_introspect.targets import TARGETS

logger = logging.getLogger("build_command_manifest")

MANIFEST_PATH = _REPO_ROOT / "cli_introspect" / "command_manifest.json"


def build_manifest() -> dict:
    commands: list[dict] = []
    dead_letters: list[str] = []
    for t in TARGETS:
        spec = capture_command(t.kind, t.target, t.name, t.invocation)
        if spec is None:
            dead_letters.append(t.name)
        else:
            commands.append(spec)
            logger.info("introspected %s (%d option(s))", t.name, len(spec.get("options", [])))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command_count": len(commands),
        "dead_letters": dead_letters,
        "commands": commands,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Regenerate the CLI command manifest.")
    parser.add_argument("--json", action="store_true", help="also print the manifest to stdout")
    parser.add_argument(
        "--output",
        default=str(MANIFEST_PATH),
        help=f"manifest output path (default: {MANIFEST_PATH})",
    )
    args = parser.parse_args()

    manifest = build_manifest()
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    logger.info(
        "wrote %s — %d command(s), %d dead-letter(s)%s",
        out_path,
        manifest["command_count"],
        len(manifest["dead_letters"]),
        f": {', '.join(manifest['dead_letters'])}" if manifest["dead_letters"] else "",
    )
    if args.json:
        print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading -- must run before any third-party/project
# import below (see scripts/_bootstrap.py's module docstring for why).
from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from cli_introspect.capture import capture_command
from cli_introspect.targets import TARGETS

logger = logging.getLogger("build_command_manifest")

MANIFEST_PATH = _REPO_ROOT / "cli_introspect" / "command_manifest.json"

_STRATEGY_REGISTRY_TIMEOUT = 60


def _fetch_strategy_registry(*, timeout: int = _STRATEGY_REGISTRY_TIMEOUT) -> list[str]:
    """Fetch ``sorted(STRATEGY_REGISTRY.keys())`` via an isolated subprocess.

    This is the single source of truth the webapp Commands screen's
    ``--strategy``/``--strategies`` pickers read (`pilots/commands.py` passes
    the resulting ``strategy_registry`` manifest field straight through to
    ``GET /commands``) instead of a hand-maintained TS constant that can (and
    did) silently drift.

    Mirrors ``cli_introspect/capture.py``'s isolation philosophy rather than a
    plain top-level/in-process import: ``scripts.refresh_validations``
    heavy-imports pandas/numpy/the quant engines, and a bare try/except around
    an in-process import only catches a clean exception -- not a hang or a
    native crash, exactly the failure mode ``capture_command``'s
    subprocess+timeout was built to contain for every other introspection
    target. Degrades to ``[]`` (never raises -- dead-letter, don't crash) on
    ANY failure: timeout, non-zero exit, or unparseable/wrong-shaped output.
    """
    child_code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(_REPO_ROOT)!r})\n"
        "from scripts.refresh_validations import STRATEGY_REGISTRY\n"
        "print(json.dumps(sorted(STRATEGY_REGISTRY.keys())))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("strategy_registry: fetch timed out after %ss -- degraded to []", timeout)
        return []

    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or "").strip().splitlines()
        logger.warning(
            "strategy_registry: fetch failed (exit %s)%s -- degraded to []",
            proc.returncode,
            f": {detail[-1]}" if detail else "",
        )
        return []

    try:
        names = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("strategy_registry: unparseable output: %s -- degraded to []", exc)
        return []

    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        logger.warning("strategy_registry: unexpected shape %r -- degraded to []", type(names).__name__)
        return []
    return names


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
    strategy_registry = _fetch_strategy_registry()
    logger.info("strategy_registry: %d strategy name(s)", len(strategy_registry))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command_count": len(commands),
        "dead_letters": dead_letters,
        "commands": commands,
        "strategy_registry": strategy_registry,
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

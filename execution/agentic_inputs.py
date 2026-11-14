"""MCP response capture for the unattended agentic cycle (K3 speed).

The ``rh-*`` agents used to re-type every Robinhood MCP response into a JSON
file before the preflight could read it: slow (a large response is emitted
token by token) and a chance to mistype a number. Instead a Claude Code
``PostToolUse`` hook (``.claude/hooks/capture_agentic_mcp.sh``) hands every
Robinhood/investyo MCP call to :func:`capture`, which saves it verbatim while
a cycle is running, and an agent turns the saved responses into a named input
file with :func:`assemble`:

* ``python -m execution.agentic_inputs capture`` — reads the hook JSON
  (``tool_name``, ``tool_input``, ``tool_response``) on stdin. When exactly
  one cycle is running (``cycle.json`` status ``running``; the newest
  ``started_at`` when several claim to be), writes
  ``OUTPUT_DIR/agentic/<run_id>/inputs/raw/<seq:04d>_<tool>.json`` atomically.
  No running cycle → no-op. It ALWAYS exits 0 and prints nothing: a hook must
  never disturb or fail the tool call it observes. Failures go to
  ``OUTPUT_DIR/agentic/capture_errors.log``.
* ``python -m execution.agentic_inputs assemble <run_id> --tool <short name>
  --out <name>.json [--where key=value ...]`` — picks the captured calls of
  that tool (``--where`` matches ``tool_input`` fields), extracts each JSON
  payload, and writes ``<run dir>/inputs/<name>.json``: one call → its
  payload; several (pagination, or several symbol batches) → a JSON list of
  payloads in call order. Repeated calls with identical input keep only the
  latest response (a retry must not double a position). The file's mtime is
  set to the OLDEST capture used, so the preflight's input-age check
  (``inputs_stale``) measures the data's real age, not the assembly time.
  Prints ``{"written": path, "n": k}``; nothing usable → exit 2, and the
  agent falls back to writing the file itself.

Captured responses are data, never instructions. This module never calls a
Robinhood tool and holds no order logic.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

CYCLE_FILENAME = "cycle.json"
INPUTS_DIRNAME = "inputs"
RAW_DIRNAME = "raw"
ERROR_LOG_FILENAME = "capture_errors.log"
SEQ_LOCK_FILENAME = ".seq.lock"

# Only the newest run folders are scanned for a running cycle (run ids sort by
# time), so the hook stays fast however many runs accumulate.
MAX_RUNS_SCANNED = 50
# A cycle still "running" this long after it started is a zombie (the
# launcher's hard timeout is 30 min): never capture into it.
MAX_RUNNING_AGE = timedelta(hours=4)
# Bound on waiting for another capture's sequence lock (writes take ms).
SEQ_LOCK_TIMEOUT_SECONDS = 5.0
# Bound on the error log, so a misbehaving hook can't fill the disk.
ERROR_LOG_MAX_BYTES = 1_000_000

EXIT_OK = 0
EXIT_NOTHING = 2

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z$")
_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_RAW_NAME_RE = re.compile(r"^(\d{4,})_.+\.json$")


# ---------------------------------------------------------------------------
# Paths and small helpers
# ---------------------------------------------------------------------------

def _agentic_dir(output_dir: Optional[Any]) -> Path:
    if output_dir is not None:
        return Path(output_dir) / "agentic"
    from settings import settings
    return Path(settings.OUTPUT_DIR) / "agentic"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json_atomic(path: Path, data: Any) -> None:
    """Unique temp file + ``os.replace``: readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, default=str)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def short_tool_name(tool_name: Any) -> str:
    """``mcp__claude_ai_Robinhood__get_equity_positions`` → ``get_equity_positions``."""
    name = str(tool_name or "").split("__")[-1]
    return _SAFE_RE.sub("_", name).strip("._") or "unknown"


def _log_error(output_dir: Optional[Any], msg: str) -> None:
    """Best-effort line in ``capture_errors.log``. Never raises."""
    try:
        path = _agentic_dir(output_dir) / ERROR_LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            if path.stat().st_size > ERROR_LOG_MAX_BYTES:
                return
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{_now().isoformat()} {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The running cycle
# ---------------------------------------------------------------------------

def running_run_dir(output_dir: Optional[Any] = None, *, now: Optional[datetime] = None) -> Optional[Path]:
    """The run folder of the cycle currently running, or None.

    Scans the newest :data:`MAX_RUNS_SCANNED` run folders for a ``cycle.json``
    with status ``running`` started within :data:`MAX_RUNNING_AGE`; when
    several qualify, the newest ``started_at`` wins. Never raises.
    """
    now = now or _now()
    try:
        root = _agentic_dir(output_dir)
        names = sorted((p.name for p in root.iterdir() if _RUN_ID_RE.match(p.name)), reverse=True)
    except Exception:
        return None
    best: Optional[Tuple[datetime, Path]] = None
    for name in names[:MAX_RUNS_SCANNED]:
        run_dir = root / name
        state = _read_json(run_dir / CYCLE_FILENAME)
        if not isinstance(state, Mapping) or state.get("status") != "running":
            continue
        started = _parse_dt(state.get("started_at"))
        if started is None or now - started > MAX_RUNNING_AGE:
            continue
        if best is None or started > best[0]:
            best = (started, run_dir)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _seq_lock(raw_dir: Path, timeout: float = SEQ_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Exclusive lock on ``raw/.seq.lock`` while a sequence number is taken."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    import time

    raw_dir.mkdir(parents=True, exist_ok=True)
    with (raw_dir / SEQ_LOCK_FILENAME).open("a+") as fh:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{raw_dir / SEQ_LOCK_FILENAME} is held by another capture")
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _next_seq(raw_dir: Path) -> int:
    seqs = [int(m.group(1)) for m in (_RAW_NAME_RE.match(p.name) for p in raw_dir.iterdir()) if m]
    return max(seqs, default=0) + 1


def capture_event(event: Mapping[str, Any], output_dir: Optional[Any] = None, *,
                  now: Optional[datetime] = None) -> Optional[Path]:
    """Save one hook event under the running cycle. None when nothing was saved.

    Raises on an I/O failure (the CLI wrapper logs it and still exits 0).
    """
    tool_name = event.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp__"):
        return None
    run_dir = running_run_dir(output_dir, now=now)
    if run_dir is None:
        return None
    raw_dir = run_dir / INPUTS_DIRNAME / RAW_DIRNAME
    record = {
        "tool_name": tool_name,
        "tool_input": event.get("tool_input"),
        "tool_response": event.get("tool_response"),
        "captured_at": (now or _now()).isoformat(),
    }
    with _seq_lock(raw_dir):
        path = raw_dir / f"{_next_seq(raw_dir):04d}_{short_tool_name(tool_name)}.json"
        _write_json_atomic(path, record)
    return path


def capture_main(stdin: Any = None, output_dir: Optional[Any] = None) -> int:
    """The hook's entry point: always returns 0 and prints nothing."""
    try:
        text = (stdin or sys.stdin).read()
        if not text.strip():
            return EXIT_OK
        event = json.loads(text)
        if isinstance(event, Mapping):
            capture_event(event, output_dir)
    except Exception as exc:  # noqa: BLE001 - a hook must never fail the tool call
        _log_error(output_dir, f"capture failed: {type(exc).__name__}: {exc}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------

def _parse_json_text(text: Any) -> Tuple[bool, Any]:
    if not isinstance(text, str):
        return False, None
    try:
        return True, json.loads(text)
    except ValueError:
        return False, None


def _from_content_blocks(blocks: Sequence[Any]) -> Tuple[bool, Any]:
    """JSON payload from MCP content blocks ``[{"type": "text", "text": "<json>"}]``."""
    texts = [b.get("text") for b in blocks
             if isinstance(b, Mapping) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    if not texts:
        return False, None
    ok, val = _parse_json_text("".join(texts))
    if ok:
        return True, val
    for t in texts:
        ok, val = _parse_json_text(t)
        if ok:
            return True, val
    return False, None


def _is_content_blocks(x: Any) -> bool:
    return (isinstance(x, list) and bool(x)
            and all(isinstance(b, Mapping) and "type" in b for b in x))


def extract_payload(tool_response: Any) -> Tuple[bool, Any]:
    """``(ok, payload)``: the JSON a tool returned, from any shape the hook sees.

    Accepts a dict (an MCP ``CallToolResult`` with ``structuredContent`` or
    ``content`` blocks, or the payload itself), a JSON string, or a list of
    content blocks. An error result (``isError``) or text that is not JSON is
    not usable (``ok`` False), never guessed into a payload.
    """
    resp = tool_response
    if isinstance(resp, str):
        ok, parsed = _parse_json_text(resp)
        if not ok:
            return False, None
        resp = parsed
        if isinstance(resp, str):
            return False, None
    if isinstance(resp, Mapping):
        if resp.get("isError") is True or resp.get("is_error") is True:
            return False, None
        if isinstance(resp.get("structuredContent"), (Mapping, list)):
            return True, resp["structuredContent"]
        if _is_content_blocks(resp.get("content")) and "data" not in resp:
            return _from_content_blocks(resp["content"])
        return True, dict(resp)
    if _is_content_blocks(resp):
        return _from_content_blocks(resp)
    if isinstance(resp, list):
        return True, resp
    return False, None


def _parse_where(items: Sequence[str]) -> List[Tuple[str, str]]:
    out = []
    for item in items or ():
        key, sep, value = str(item).partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--where needs key=value, got {item!r}")
        out.append((key.strip(), value))
    return out


def _matches(tool_input: Any, where: Sequence[Tuple[str, str]]) -> bool:
    if not where:
        return True
    if not isinstance(tool_input, Mapping):
        return False
    for key, want in where:
        if key not in tool_input:
            return False
        v = tool_input[key]
        if isinstance(v, bool):
            if want.strip().lower() != str(v).lower():
                return False
        elif isinstance(v, list):
            if want not in [str(x) for x in v]:
                return False
        elif str(v) != want:
            return False
    return True


def _safe_out_name(name: str) -> str:
    base = str(name or "").strip()
    if not base or base != Path(base).name or base.startswith("."):
        raise ValueError(f"--out must be a plain file name, got {name!r}")
    return base if base.endswith(".json") else base + ".json"


def assemble(run_id: str, tool: str, out_name: str, *, where: Sequence[str] = (),
             output_dir: Optional[Any] = None) -> Dict[str, Any]:
    """Build ``<run dir>/inputs/<out_name>`` from captured responses.

    Returns ``{"written": path, "n": k, ...}``, or ``{"error": ...}`` (with
    nothing written) when no captured call of ``tool`` matched with a usable
    JSON payload.
    """
    if not _RUN_ID_RE.match(str(run_id or "")):
        return {"error": f"not a run id: {run_id!r}"}
    out_name = _safe_out_name(out_name)
    conds = _parse_where(where)
    want_tool = short_tool_name(tool)
    run_dir = _agentic_dir(output_dir) / run_id
    raw_dir = run_dir / INPUTS_DIRNAME / RAW_DIRNAME
    try:
        files = sorted(((int(m.group(1)), p) for p in raw_dir.iterdir()
                        if (m := _RAW_NAME_RE.match(p.name))), key=lambda t: t[0])
    except OSError:
        return {"error": f"nothing captured for run {run_id} (no {raw_dir})"}

    picked: Dict[str, Tuple[int, Any, Optional[datetime]]] = {}
    n_matched = n_unusable = 0
    for seq, path in files:
        rec = _read_json(path)
        if not isinstance(rec, Mapping) or short_tool_name(rec.get("tool_name")) != want_tool:
            continue
        if not _matches(rec.get("tool_input"), conds):
            continue
        n_matched += 1
        ok, payload = extract_payload(rec.get("tool_response"))
        if not ok:
            n_unusable += 1
            continue
        captured = _parse_dt(rec.get("captured_at"))
        if captured is None:
            with contextlib.suppress(OSError):
                captured = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        # Same input again (a retry, or re-reading page 1) → the latest wins.
        key = json.dumps(rec.get("tool_input"), sort_keys=True, default=str)
        picked[key] = (seq, payload, captured)

    if not picked:
        return {"error": f"no usable captured {want_tool} response in run {run_id}",
                "matched": n_matched, "unusable": n_unusable}
    ordered = sorted(picked.values(), key=lambda t: t[0])
    payloads = [p for _, p, _ in ordered]
    data = payloads[0] if len(payloads) == 1 else payloads
    path = run_dir / INPUTS_DIRNAME / out_name
    _write_json_atomic(path, data)
    times = [c for _, _, c in ordered if c is not None]
    if len(times) == len(ordered):
        oldest = min(times).timestamp()
        os.utime(path, (oldest, oldest))
    else:
        # Unknown capture time → leave the file aged as old as possible, so
        # the preflight's staleness check vetoes rather than trusts it.
        os.utime(path, (0, 0))
    return {"written": str(path), "n": len(payloads), "matched": n_matched,
            "unusable": n_unusable, "seqs": [s for s, _, _ in ordered]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Capture / assemble MCP responses for the agentic cycle.")
    ap.add_argument("--output-dir", help="override $OUTPUT_DIR")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("capture", help="hook entry: save the PostToolUse JSON on stdin (always exits 0)")
    a = sub.add_parser("assemble", help="write <run>/inputs/<out> from captured responses (exit 2: nothing usable)")
    a.add_argument("run_id")
    a.add_argument("--tool", required=True, help="tool name without the mcp__<server>__ prefix")
    a.add_argument("--out", required=True, help="file name under the run's inputs/, e.g. positions.json")
    a.add_argument("--where", action="append", default=[], metavar="KEY=VALUE",
                   help="only calls whose tool_input[KEY] equals VALUE (or contains it, for a list)")
    args = ap.parse_args(argv)
    if args.cmd == "capture":
        return capture_main(output_dir=args.output_dir)
    try:
        res = assemble(args.run_id, args.tool, args.out, where=args.where, output_dir=args.output_dir)
    except Exception as exc:  # noqa: BLE001 - report, exit 2, let the agent fall back
        res = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(res))
    return EXIT_OK if "written" in res else EXIT_NOTHING


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

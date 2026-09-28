"""
scripts/compare_shadow_queue.py
===============================
Read-only diff between main.py's real Robinhood execution queue and the
orchestrator daemon's SHADOW queue (step 5.2, gate (iii) of
``.claude/shrink_step5_retire_main_py_implementation_plan.md``).

It never writes anything.

What it compares
----------------
* **Real side:** ``OUTPUT_DIR/queue_sources/advisory.json`` and
  ``OUTPUT_DIR/execution_queue.json`` -- written by the 08:45 ``main.py`` run.
  The advisory source's ``generated_at`` is the reference time.
* **Shadow side:** the daemon's first shadow run AT OR AFTER that reference
  time. ``AgenticQueueStep`` (``DAEMON_AGENTIC_QUEUE_MODE=shadow``) keeps a
  timestamped copy of every cycle's shadow files in
  ``OUTPUT_DIR/shadow/history/``; the live ``OUTPUT_DIR/shadow/`` files are
  used only when no history copy qualifies. ``--max-lag-hours`` bounds how far
  after the reference a shadow run may be.

Per symbol it prints the advisory target (action, conviction,
``suggested_position_pct``) and the queue intent (side, conviction,
``target_notional``, ``gate_allowed``, ``allow_place``) on both sides, and
marks every difference. A symbol present on one side only is listed too.

Note: ``compose_and_emit`` leaves the previous queue in place when nothing is
composable, so a queue file can be older than its advisory source. The report
prints every file's ``generated_at`` so that is visible.

Usage
-----
    python scripts/compare_shadow_queue.py                 # default OUTPUT_DIR
    python scripts/compare_shadow_queue.py --output-dir ~/.stockpy_local/output
    python scripts/compare_shadow_queue.py --json          # machine-readable

Exit codes: 0 = compared and identical, 2 = compared with differences,
1 = nothing to compare (no real source, or no shadow run after it).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SHADOW_SUBDIR = "shadow"
HISTORY_SUBDIR = "history"
SOURCE_REL = Path("queue_sources") / "advisory.json"
QUEUE_NAME = "execution_queue.json"

TARGET_FIELDS = ("action", "conviction", "suggested_position_pct")
INTENT_FIELDS = ("side", "conviction", "target_notional", "gate_allowed", "allow_place")
_FLOAT_TOL = 1e-9


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _history_stamp(path: Path) -> Optional[datetime]:
    try:
        return datetime.strptime(path.name.split("_", 1)[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class ShadowRun:
    generated_at: datetime
    source: Dict[str, Any]
    queue: Optional[Dict[str, Any]]
    origin: str


def find_shadow_run(
    output_dir: Path, after: datetime, max_lag: Optional[timedelta] = None,
) -> Optional[ShadowRun]:
    """The earliest shadow run whose advisory source was generated at or after
    ``after`` (and within ``max_lag`` of it, when given)."""
    shadow = Path(output_dir) / SHADOW_SUBDIR
    candidates: List[ShadowRun] = []
    history = shadow / HISTORY_SUBDIR
    if history.is_dir():
        for src_path in sorted(history.glob("*_advisory.json")):
            stamp = _history_stamp(src_path)
            source = _read_json(src_path)
            if stamp is None or source is None:
                continue
            ts = _parse_ts(source.get("generated_at")) or stamp
            queue_path = src_path.with_name(src_path.name.replace("_advisory.json", "_execution_queue.json"))
            candidates.append(ShadowRun(ts, source, _read_json(queue_path) if queue_path.exists() else None,
                                        f"history/{src_path.name}"))
    live_source = _read_json(shadow / SOURCE_REL)
    if live_source is not None:
        ts = _parse_ts(live_source.get("generated_at"))
        if ts is not None and not any(c.generated_at == ts for c in candidates):
            live_queue = _read_json(shadow / QUEUE_NAME)
            # The live queue belongs to this run only if compose wrote it then.
            if live_queue is not None and _parse_ts(live_queue.get("generated_at")) != ts:
                live_queue = None
            candidates.append(ShadowRun(ts, live_source, live_queue, f"{SHADOW_SUBDIR}/{SOURCE_REL}"))
    eligible = [c for c in candidates if c.generated_at >= after
                and (max_lag is None or c.generated_at - after <= max_lag)]
    return min(eligible, key=lambda c: c.generated_at) if eligible else None


def _by_symbol(rows: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("symbol"):
            out[str(row["symbol"]).upper()] = row
    return out


def _differs(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return abs(float(a) - float(b)) > _FLOAT_TOL
    return a != b


def _diff_rows(real: Dict[str, Dict[str, Any]], shadow: Dict[str, Dict[str, Any]],
               fields: Tuple[str, ...]) -> List[Dict[str, Any]]:
    rows = []
    for symbol in sorted(set(real) | set(shadow)):
        r, s = real.get(symbol), shadow.get(symbol)
        changed = (
            ["<only in real>"] if s is None else
            ["<only in shadow>"] if r is None else
            [f for f in fields if _differs(r.get(f), s.get(f))]
        )
        rows.append({
            "symbol": symbol,
            "real": {f: r.get(f) for f in fields} if r else None,
            "shadow": {f: s.get(f) for f in fields} if s else None,
            "changed": changed,
        })
    return rows


@dataclass
class Comparison:
    ok: bool
    message: str
    real_source_generated_at: Optional[str] = None
    real_queue_generated_at: Optional[str] = None
    shadow_origin: Optional[str] = None
    shadow_source_generated_at: Optional[str] = None
    shadow_queue_generated_at: Optional[str] = None
    targets: List[Dict[str, Any]] = field(default_factory=list)
    intents: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def n_differences(self) -> int:
        return sum(1 for r in self.targets + self.intents if r["changed"])

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["n_differences"] = self.n_differences
        return d


def compare(output_dir: Path, max_lag: Optional[timedelta] = None) -> Comparison:
    output_dir = Path(output_dir)
    real_source = _read_json(output_dir / SOURCE_REL)
    if real_source is None:
        return Comparison(False, f"no real advisory source at {output_dir / SOURCE_REL}")
    ref = _parse_ts(real_source.get("generated_at"))
    if ref is None:
        return Comparison(False, "the real advisory source has no parseable generated_at")
    real_queue = _read_json(output_dir / QUEUE_NAME)
    run = find_shadow_run(output_dir, ref, max_lag)
    base = dict(
        real_source_generated_at=real_source.get("generated_at"),
        real_queue_generated_at=(real_queue or {}).get("generated_at"),
    )
    if run is None:
        return Comparison(False, f"no shadow run at or after {ref.isoformat()}"
                          + (f" within {max_lag}" if max_lag else ""), **base)
    return Comparison(
        True, "compared",
        shadow_origin=run.origin,
        shadow_source_generated_at=run.source.get("generated_at"),
        shadow_queue_generated_at=(run.queue or {}).get("generated_at"),
        targets=_diff_rows(_by_symbol(real_source.get("targets")),
                           _by_symbol(run.source.get("targets")), TARGET_FIELDS),
        intents=_diff_rows(_by_symbol((real_queue or {}).get("intents")),
                           _by_symbol((run.queue or {}).get("intents")), INTENT_FIELDS),
        **base,
    )


def _fmt(values: Optional[Dict[str, Any]], fields: Tuple[str, ...]) -> str:
    if values is None:
        return "-"
    return " ".join(f"{f}={values.get(f)}" for f in fields)


def render(c: Comparison) -> str:
    lines = [
        f"real advisory source  generated_at={c.real_source_generated_at}",
        f"real execution queue  generated_at={c.real_queue_generated_at}",
    ]
    if not c.ok:
        lines.append(f"NOT COMPARED: {c.message}")
        return "\n".join(lines)
    lines += [
        f"shadow run            {c.shadow_origin}",
        f"shadow advisory       generated_at={c.shadow_source_generated_at}",
        f"shadow queue          generated_at={c.shadow_queue_generated_at}",
        "",
    ]
    for title, rows, fields in (("ADVISORY TARGETS", c.targets, TARGET_FIELDS),
                                ("QUEUE INTENTS", c.intents, INTENT_FIELDS)):
        lines.append(f"{title} ({sum(1 for r in rows if r['changed'])} of {len(rows)} symbols differ)")
        for r in rows:
            mark = "DIFF" if r["changed"] else "same"
            lines.append(f"  {mark:4}  {r['symbol']:<6}  real:   {_fmt(r['real'], fields)}")
            lines.append(f"              shadow: {_fmt(r['shadow'], fields)}"
                         + (f"   changed: {', '.join(r['changed'])}" if r["changed"] else ""))
        lines.append("")
    lines.append(f"TOTAL: {c.n_differences} differing symbol rows")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output-dir", default=None,
                        help="OUTPUT_DIR to read (default: settings.OUTPUT_DIR)")
    parser.add_argument("--max-lag-hours", type=float, default=None,
                        help="ignore shadow runs more than this many hours after the real run")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    if args.output_dir is None:
        from settings import settings
        output_dir = Path(settings.OUTPUT_DIR)
    else:
        output_dir = Path(args.output_dir).expanduser()
    max_lag = timedelta(hours=args.max_lag_hours) if args.max_lag_hours is not None else None

    result = compare(output_dir, max_lag)
    print(json.dumps(result.to_dict(), indent=2, default=str) if args.json else render(result))
    if not result.ok:
        return 1
    return 2 if result.n_differences else 0


if __name__ == "__main__":
    from scripts._bootstrap import bootstrap

    bootstrap()
    sys.exit(main())

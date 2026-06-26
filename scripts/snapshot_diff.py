"""Snapshot rotation + "Δ Since Last Run" diff engine.

Each ``run_once()`` / ``main_orchestrator.run_pipeline()`` cycle writes a
fresh ``output/state_snapshot.json`` (the dashboard's current-state file)
AND, via :func:`rotate_snapshot`, a timestamped copy under
``output/history/state_snapshot_<UTC>.json``.  Snapshots older than
``settings.SNAPSHOT_HISTORY_DAYS`` are pruned in the same call so the
history dir never grows unbounded.

:func:`compute_diff` joins the two most-recent rotated snapshots on
ticker symbol and returns a structured :class:`SnapshotDiff` describing
what changed.  The daily HTML report (``diagnostics_and_visuals``) reads
that struct and renders a "Δ Since Last Run" band at the top of the
report so the operator immediately sees:

* new BUY recommendations,
* signals that flipped (e.g. BUY → HOLD),
* conviction scores that moved by ``|Δ| ≥ SNAPSHOT_CONVICTION_DELTA_THRESHOLD``,
* holdings that were added or dropped,
* regime changes.

The module is import-safe with no orchestrator dependencies: every load
is tolerant of missing/corrupt files (degrade to ``None``/empty diff —
never raise — CONSTRAINT #4 + #6) so a partial history can never abort
the report-generation step.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HISTORY_DIRNAME = "history"
SNAPSHOT_FILENAME_PREFIX = "state_snapshot_"
SNAPSHOT_FILENAME_SUFFIX = ".json"
DEFAULT_CONVICTION_DELTA_THRESHOLD = 0.2
DEFAULT_HISTORY_DAYS = 30


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SnapshotDiff:
    """Structured "what changed since the last snapshot" payload.

    All list fields are sorted alphabetically by symbol so the rendered
    Δ band is deterministic across runs (no spurious diff churn from
    dict-iteration ordering).
    """

    prev_ts: Optional[str]
    curr_ts: Optional[str]
    regime_change: Optional[Tuple[str, str]]  # (before, after) or None
    new_buys: List[str] = field(default_factory=list)
    action_flips: List[Dict[str, str]] = field(default_factory=list)
    conviction_deltas: List[Dict[str, Any]] = field(default_factory=list)
    added_holdings: List[str] = field(default_factory=list)
    dropped_holdings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when no material changes were detected."""
        return (
            self.regime_change is None
            and not self.new_buys
            and not self.action_flips
            and not self.conviction_deltas
            and not self.added_holdings
            and not self.dropped_holdings
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dict (Jinja-friendly).

        Tuple ``regime_change`` is unpacked to a 2-element list so the
        result round-trips through ``json.dumps`` without surprises.
        """
        d = asdict(self)
        if self.regime_change is not None:
            d["regime_change"] = list(self.regime_change)
        d["is_empty"] = self.is_empty
        return d


# ---------------------------------------------------------------------------
# Snapshot I/O
# ---------------------------------------------------------------------------

def load_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    """Load a single snapshot file, returning ``None`` on any failure.

    CONSTRAINT #4 / #6 — never raise.  A corrupt or missing snapshot
    must degrade gracefully so the daily report still renders.
    """
    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            logger.debug("Snapshot %s is not a JSON object — ignoring.", path)
            return None
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Snapshot %s is unreadable: %s", path, exc)
        return None


def _history_dir(output_dir: Path) -> Path:
    """Return ``output_dir/history`` (created if absent)."""
    d = Path(output_dir) / HISTORY_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot_filename(ts: datetime) -> str:
    """Compose a rotation filename from a UTC datetime.

    Format: ``state_snapshot_20260626T140530Z.json`` — colon-free so the
    file is portable to FAT/NTFS without escaping.
    """
    safe = ts.strftime("%Y%m%dT%H%M%SZ")
    return f"{SNAPSHOT_FILENAME_PREFIX}{safe}{SNAPSHOT_FILENAME_SUFFIX}"


def _parse_snapshot_filename(name: str) -> Optional[datetime]:
    """Inverse of :func:`_snapshot_filename`. Returns ``None`` on no match."""
    if not (name.startswith(SNAPSHOT_FILENAME_PREFIX)
            and name.endswith(SNAPSHOT_FILENAME_SUFFIX)):
        return None
    core = name[len(SNAPSHOT_FILENAME_PREFIX):-len(SNAPSHOT_FILENAME_SUFFIX)]
    try:
        return datetime.strptime(core, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def list_rotated_snapshots(output_dir: Path) -> List[Path]:
    """Return all rotated snapshot paths, sorted oldest → newest.

    Filenames that do not match the rotation pattern are ignored
    (so a misplaced ``state_snapshot.json`` in ``history/`` does no harm).
    """
    history = _history_dir(output_dir)
    entries: List[Tuple[datetime, Path]] = []
    for p in history.iterdir():
        if not p.is_file():
            continue
        ts = _parse_snapshot_filename(p.name)
        if ts is None:
            continue
        entries.append((ts, p))
    entries.sort(key=lambda x: x[0])
    return [p for _, p in entries]


def rotate_snapshot(
    snapshot: Dict[str, Any],
    output_dir: Path,
    *,
    max_age_days: int = DEFAULT_HISTORY_DAYS,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """Write a timestamped copy of ``snapshot`` and prune old history.

    Parameters
    ----------
    snapshot
        The dict already written to ``output/state_snapshot.json``.  The
        ``timestamp`` field (if present and parseable as ISO-8601) is
        preferred over wall-clock time so the on-disk filename always
        matches the snapshot's own ``timestamp`` field — useful when the
        same snapshot is replayed across machines.
    output_dir
        Project output directory (rotation goes under
        ``output_dir/history/``).
    max_age_days
        Snapshots strictly older than this many days are deleted.
        ``0`` disables pruning entirely.
    now
        Injectable wall-clock (tests).  Defaults to UTC ``datetime.now``.

    Returns
    -------
    Path or None
        The written rotation path, or ``None`` if write failed (logged at
        WARNING; never raises — CONSTRAINT #6).
    """
    now = now or datetime.now(timezone.utc)
    # Prefer the snapshot's own timestamp so on-disk name matches payload.
    ts = now
    raw_ts = snapshot.get("timestamp")
    if isinstance(raw_ts, str):
        try:
            parsed = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            ts = parsed.astimezone(timezone.utc)
        except ValueError:
            logger.debug("Snapshot timestamp %r is not ISO-8601; using now().", raw_ts)

    target = _history_dir(output_dir) / _snapshot_filename(ts)
    try:
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        tmp.replace(target)  # atomic on POSIX
    except OSError as exc:
        logger.warning("Snapshot rotation failed: %s", exc)
        return None

    if max_age_days and max_age_days > 0:
        cutoff = now - timedelta(days=max_age_days)
        for p in list_rotated_snapshots(output_dir):
            file_ts = _parse_snapshot_filename(p.name)
            if file_ts is None:
                continue
            if file_ts < cutoff:
                try:
                    p.unlink()
                except OSError as exc:
                    logger.debug("Could not prune old snapshot %s: %s", p, exc)

    return target


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

_BUY_TOKEN = "BUY"


def _signals_by_symbol(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Index ``snapshot['signals']`` by upper-cased symbol.

    Tolerant of missing/empty ``signals`` list and of rows with no
    ``symbol`` field (those are silently skipped — CONSTRAINT #4).
    """
    out: Dict[str, Dict[str, Any]] = {}
    for sig in snapshot.get("signals", []) or []:
        if not isinstance(sig, dict):
            continue
        sym = str(sig.get("symbol") or "").upper().strip()
        if not sym:
            continue
        out[sym] = sig
    return out


def _effective_action(sig: Dict[str, Any]) -> str:
    """Prefer advisory_action when set, fall back to wide-pipeline action."""
    av = str(sig.get("advisory_action") or "").strip().upper()
    if av:
        return av
    return str(sig.get("action") or "").strip().upper()


def _effective_conviction(sig: Dict[str, Any]) -> Optional[float]:
    """Prefer advisory_conviction; fall back to score-derived if present.

    Returns ``None`` when no conviction is available, so callers can
    distinguish "no signal" from "neutral conviction".
    """
    for key in ("advisory_conviction", "conviction"):
        v = sig.get(key)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f:  # NaN guard
            continue
        return f
    return None


def _holdings_from(snapshot: Dict[str, Any]) -> List[str]:
    """Return sorted list of symbols the account holds (qty > 0)."""
    explicit = snapshot.get("holdings")
    if isinstance(explicit, list):
        out = sorted({str(s).upper().strip() for s in explicit if str(s).strip()})
        return out
    # Backfill from signals[].shares if the snapshot writer didn't
    # surface a holdings list (older snapshots, paranoid robustness).
    seen = set()
    for sig in snapshot.get("signals", []) or []:
        if not isinstance(sig, dict):
            continue
        try:
            qty = float(sig.get("shares") or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty > 0:
            sym = str(sig.get("symbol") or "").upper().strip()
            if sym:
                seen.add(sym)
    return sorted(seen)


def compute_diff(
    prev: Optional[Dict[str, Any]],
    curr: Optional[Dict[str, Any]],
    *,
    conviction_delta_threshold: float = DEFAULT_CONVICTION_DELTA_THRESHOLD,
) -> SnapshotDiff:
    """Compare two snapshots and return a structured diff.

    Edge cases:

    * ``curr is None`` → empty diff with a single "no current snapshot" note.
    * ``prev is None`` (first ever run) → all current BUYs are reported
      as ``new_buys`` and all current holdings as ``added_holdings``.
    * Symbol set is the union — symbols in ``prev`` but not in ``curr``
      are not treated as flips (they may simply be off-universe this run);
      only holdings drops are reported.
    """
    if curr is None:
        return SnapshotDiff(
            prev_ts=(prev or {}).get("timestamp") if prev else None,
            curr_ts=None,
            regime_change=None,
            notes=["No current snapshot available — diff suppressed."],
        )

    curr_ts = curr.get("timestamp")
    prev_ts = (prev or {}).get("timestamp")
    curr_sigs = _signals_by_symbol(curr)
    prev_sigs = _signals_by_symbol(prev) if prev else {}
    curr_hold = set(_holdings_from(curr))
    prev_hold = set(_holdings_from(prev)) if prev else set()

    # Regime change (only when prev exists; first-run regime is not a "change").
    regime_change: Optional[Tuple[str, str]] = None
    if prev is not None:
        prev_regime = str(prev.get("market_regime") or "").strip()
        curr_regime = str(curr.get("market_regime") or "").strip()
        if prev_regime and curr_regime and prev_regime != curr_regime:
            regime_change = (prev_regime, curr_regime)

    new_buys: List[str] = []
    action_flips: List[Dict[str, str]] = []
    conviction_deltas: List[Dict[str, Any]] = []

    for sym in sorted(curr_sigs.keys()):
        curr_sig = curr_sigs[sym]
        prev_sig = prev_sigs.get(sym)
        curr_action = _effective_action(curr_sig)
        prev_action = _effective_action(prev_sig) if prev_sig else ""

        # New BUY = (no prior signal OR prior was not BUY) AND current is BUY.
        if _BUY_TOKEN in curr_action and _BUY_TOKEN not in prev_action:
            new_buys.append(sym)

        # Action flip (only when both sides have a signal AND they differ
        # AND it's not the "new buy" case already counted above).
        if (
            prev_sig is not None
            and prev_action
            and curr_action
            and prev_action != curr_action
            and sym not in new_buys
        ):
            action_flips.append({
                "symbol": sym,
                "before": prev_action,
                "after": curr_action,
            })

        # Conviction delta — only when both sides expose a value.
        curr_conv = _effective_conviction(curr_sig)
        prev_conv = _effective_conviction(prev_sig) if prev_sig else None
        if curr_conv is not None and prev_conv is not None:
            delta = curr_conv - prev_conv
            if abs(delta) >= conviction_delta_threshold:
                conviction_deltas.append({
                    "symbol": sym,
                    "before": round(prev_conv, 4),
                    "after": round(curr_conv, 4),
                    "delta": round(delta, 4),
                })

    added_holdings = sorted(curr_hold - prev_hold)
    dropped_holdings = sorted(prev_hold - curr_hold)

    return SnapshotDiff(
        prev_ts=prev_ts,
        curr_ts=curr_ts,
        regime_change=regime_change,
        new_buys=sorted(new_buys),
        action_flips=action_flips,
        conviction_deltas=conviction_deltas,
        added_holdings=added_holdings,
        dropped_holdings=dropped_holdings,
    )


def compute_diff_from_history(
    output_dir: Path,
    *,
    conviction_delta_threshold: float = DEFAULT_CONVICTION_DELTA_THRESHOLD,
) -> SnapshotDiff:
    """Convenience: read the two most-recent rotated snapshots and diff.

    Returns an empty :class:`SnapshotDiff` when fewer than two snapshots
    exist (first ever run case is handled by ``compute_diff`` itself —
    here we surface a note so the caller can hide the band entirely).
    """
    rotated = list_rotated_snapshots(output_dir)
    if not rotated:
        return SnapshotDiff(prev_ts=None, curr_ts=None, regime_change=None,
                            notes=["No rotated snapshots yet."])
    curr = load_snapshot(rotated[-1])
    prev = load_snapshot(rotated[-2]) if len(rotated) >= 2 else None
    return compute_diff(prev, curr,
                        conviction_delta_threshold=conviction_delta_threshold)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_markdown(diff: SnapshotDiff) -> str:
    """Render the diff as GitHub-flavored markdown."""
    lines: List[str] = ["# Δ Since Last Run"]
    if diff.prev_ts and diff.curr_ts:
        lines.append(f"_{diff.prev_ts} → {diff.curr_ts}_")
    elif diff.curr_ts:
        lines.append(f"_First run (no prior snapshot) — {diff.curr_ts}_")
    lines.append("")

    if diff.is_empty:
        lines.append("_No material changes._")
        for note in diff.notes:
            lines.append(f"> {note}")
        return "\n".join(lines).strip() + "\n"

    if diff.regime_change is not None:
        before, after = diff.regime_change
        lines.append(f"**Regime change:** {before} → {after}")
        lines.append("")

    if diff.new_buys:
        lines.append("## New BUYs")
        for sym in diff.new_buys:
            lines.append(f"- {sym}")
        lines.append("")

    if diff.action_flips:
        lines.append("## Action flips")
        for flip in diff.action_flips:
            lines.append(f"- {flip['symbol']}: {flip['before']} → {flip['after']}")
        lines.append("")

    if diff.conviction_deltas:
        lines.append("## Conviction movement")
        for d in diff.conviction_deltas:
            sign = "+" if d["delta"] >= 0 else ""
            lines.append(
                f"- {d['symbol']}: {d['before']:.2f} → {d['after']:.2f} "
                f"({sign}{d['delta']:.2f})"
            )
        lines.append("")

    if diff.added_holdings:
        lines.append("## Holdings added")
        for sym in diff.added_holdings:
            lines.append(f"- {sym}")
        lines.append("")

    if diff.dropped_holdings:
        lines.append("## Holdings dropped")
        for sym in diff.dropped_holdings:
            lines.append(f"- {sym}")
        lines.append("")

    for note in diff.notes:
        lines.append(f"> {note}")
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="snapshot_diff",
        description="Diff two state_snapshot.json files (or the latest two rotated).",
    )
    p.add_argument("prev", nargs="?", default=None,
                   help="Previous snapshot path (omit to use 2nd-newest in history/).")
    p.add_argument("curr", nargs="?", default=None,
                   help="Current snapshot path (omit to use newest in history/).")
    p.add_argument("--output-dir", default="output",
                   help="Project output dir (default: output) — used when prev/curr omitted.")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("--conviction-threshold", type=float,
                   default=DEFAULT_CONVICTION_DELTA_THRESHOLD,
                   help="|Δ conviction| at or above this is reported (default 0.2).")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint: ``python -m scripts.snapshot_diff prev.json curr.json``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_argparser().parse_args(argv)

    if args.prev and args.curr:
        prev = load_snapshot(Path(args.prev))
        curr = load_snapshot(Path(args.curr))
        diff = compute_diff(
            prev, curr,
            conviction_delta_threshold=args.conviction_threshold,
        )
    else:
        diff = compute_diff_from_history(
            Path(args.output_dir),
            conviction_delta_threshold=args.conviction_threshold,
        )

    if args.format == "json":
        print(json.dumps(diff.to_dict(), indent=2))
    else:
        print(format_markdown(diff))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

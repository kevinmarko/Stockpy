"""
scripts/track_record_status.py
==============================
Track-record progress report for the InvestYo advisory platform.

Answers the single operator question: *"How close am I to the 90-day
paper-trading go-live gate, and is the unattended advisory actually filling
up the calibration history?"*

It reports four things, all from local files (no network calls):

1. **Paper-trading gate** — days elapsed since ``PAPER_TRADING_START_DATE``
   (from ``.env`` / settings) versus the 90-day go-live gate, and days
   remaining.
2. **Calibration history depth** — row count of
   ``output/decision_log.jsonl`` (the per-decision calibration log that must
   fill to be useful).
3. **Calibration coverage** — the 30-day conviction-calibration MAE, REUSING
   ``scripts.daily_briefing._section_calibration`` (no duplicated SQL/logic).
4. **Last-run staleness** — age of ``output/heartbeat.txt`` and
   ``output/state_snapshot.json`` (so a silently-stopped scheduler is visible).

Design
------
*  **No live network calls** — reads only existing output files + settings.
*  **Dead-letter tolerant** (CONSTRAINT #6): every field is computed under
   try/except and degrades to a sane default (``None`` / ``0`` / a note).  A
   missing file or unset env-var never crashes the report.
*  **No fabricated metrics** (CONSTRAINT #4): missing dates / unset gate
   surface as ``None``, never as a flattering placeholder number.
*  **No secrets** — only counts, ages, and dates are printed; never any
   credential value.

Usage
-----
    python scripts/track_record_status.py            # human-readable report
    python scripts/track_record_status.py --json     # machine-readable JSON
    python scripts/track_record_status.py --output-dir /tmp/out

Wired into the unattended scheduler indirectly: the launchd job runs
``main.py``; run this report by hand (or from a cron/launchd job of your own)
to check progress.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Repo root resolution — invocable from any working directory.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "output"

# The go-live gate — must match scripts/preflight_check.check_paper_trading_duration.
GO_LIVE_GATE_DAYS = 90


# ===========================================================================
# Individual field computations — each never raises.
# ===========================================================================

def _read_paper_trading_start_date() -> Optional[str]:
    """Read ``PAPER_TRADING_START_DATE`` from settings.  Returns ``None`` on any failure."""
    try:
        from settings import settings

        val = getattr(settings, "PAPER_TRADING_START_DATE", None)
        return str(val) if val else None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("track_record_status: could not read settings: %s", exc)
        return None


def compute_gate_status(
    start_str: Optional[str],
    *,
    today: Optional[date] = None,
    gate_days: int = GO_LIVE_GATE_DAYS,
) -> Dict[str, Any]:
    """Compute paper-trading gate progress.

    Parameters
    ----------
    start_str:
        ISO ``YYYY-MM-DD`` string, or ``None`` if unset.
    today:
        Injected reference date for deterministic testing.  Defaults to
        ``date.today()``.
    gate_days:
        The go-live gate length in days (default 90).

    Returns
    -------
    dict with keys:
        ``start_date`` (str|None), ``gate_days`` (int),
        ``days_elapsed`` (int|None), ``days_remaining`` (int|None),
        ``gate_met`` (bool), ``go_live_date`` (str|None), ``note`` (str).
    Never raises.
    """
    if today is None:
        today = date.today()

    result: Dict[str, Any] = {
        "start_date": start_str,
        "gate_days": gate_days,
        "days_elapsed": None,
        "days_remaining": None,
        "gate_met": False,
        "go_live_date": None,
        "note": "",
    }

    if not start_str:
        result["note"] = (
            "PAPER_TRADING_START_DATE not set in .env — cannot measure gate progress."
        )
        return result

    try:
        start = date.fromisoformat(str(start_str).strip())
    except (ValueError, TypeError):
        result["note"] = f"Invalid PAPER_TRADING_START_DATE {start_str!r} — expected YYYY-MM-DD."
        return result

    from datetime import timedelta

    elapsed = (today - start).days
    remaining = max(0, gate_days - elapsed)
    go_live = start + timedelta(days=gate_days)

    result["days_elapsed"] = elapsed
    result["days_remaining"] = remaining
    result["gate_met"] = elapsed >= gate_days
    result["go_live_date"] = go_live.isoformat()
    if elapsed < 0:
        result["note"] = "Start date is in the future — check PAPER_TRADING_START_DATE."
    elif result["gate_met"]:
        result["note"] = f"Gate met ({elapsed} ≥ {gate_days} days)."
    else:
        result["note"] = f"{remaining} day(s) remaining until the {gate_days}-day gate."
    return result


def count_decision_log_rows(output_dir: Path) -> int:
    """Count non-blank lines in ``output/decision_log.jsonl``.

    Returns ``0`` when the file is missing or unreadable (CONSTRAINT #6).
    """
    path = Path(output_dir) / "decision_log.jsonl"
    try:
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count
    except Exception as exc:
        logger.debug("track_record_status: could not count decision log: %s", exc)
        return 0


# Regexes to salvage the MAE / bins headline from the reused calibration
# Markdown block, so the JSON output carries a numeric summary too.
_MAE_RE = re.compile(r"\*\*MAE:\*\*\s*([0-9]*\.?[0-9]+)")
_BINS_RE = re.compile(r"\*\*Bins w/ data:\*\*\s*(\d+)\s*/\s*(\d+)")
_TRADES_RE = re.compile(r"\*\*Total trades:\*\*\s*(\d+)")


def compute_calibration_status() -> Dict[str, Any]:
    """Reuse ``daily_briefing._section_calibration`` and parse a numeric summary.

    Returns a dict with ``markdown`` (the reused block, verbatim), plus best-effort
    parsed ``mae`` / ``bins_with_data`` / ``total_bins`` / ``total_trades`` /
    ``has_data``.  Never raises.
    """
    result: Dict[str, Any] = {
        "markdown": "",
        "mae": None,
        "bins_with_data": None,
        "total_bins": None,
        "total_trades": None,
        "has_data": False,
    }
    try:
        from scripts.daily_briefing import _section_calibration

        md = _section_calibration()
    except Exception as exc:
        logger.debug("track_record_status: calibration reuse failed: %s", exc)
        result["markdown"] = "_Calibration unavailable._"
        return result

    result["markdown"] = md.strip()

    mae_m = _MAE_RE.search(md)
    if mae_m:
        try:
            result["mae"] = float(mae_m.group(1))
            result["has_data"] = True
        except ValueError:
            pass
    bins_m = _BINS_RE.search(md)
    if bins_m:
        result["bins_with_data"] = int(bins_m.group(1))
        result["total_bins"] = int(bins_m.group(2))
    trades_m = _TRADES_RE.search(md)
    if trades_m:
        result["total_trades"] = int(trades_m.group(1))
    return result


def _file_age_seconds(path: Path, *, now: Optional[datetime] = None) -> Optional[float]:
    """Age of a file in seconds from its mtime.  ``None`` if missing/unreadable."""
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        if not path.exists():
            return None
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return max(0.0, (now - mtime).total_seconds())
    except Exception as exc:
        logger.debug("track_record_status: could not stat %s: %s", path, exc)
        return None


def compute_staleness(output_dir: Path, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Age (seconds/hours) of heartbeat.txt and state_snapshot.json.  Never raises."""
    output_dir = Path(output_dir)
    hb = _file_age_seconds(output_dir / "heartbeat.txt", now=now)
    snap = _file_age_seconds(output_dir / "state_snapshot.json", now=now)

    def _hours(sec: Optional[float]) -> Optional[float]:
        return round(sec / 3600.0, 2) if sec is not None else None

    return {
        "heartbeat_age_seconds": None if hb is None else round(hb, 1),
        "heartbeat_age_hours": _hours(hb),
        "snapshot_age_seconds": None if snap is None else round(snap, 1),
        "snapshot_age_hours": _hours(snap),
    }


# ===========================================================================
# Assembler
# ===========================================================================

def build_status(
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    *,
    today: Optional[date] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Assemble the full track-record status dict.  Never raises (CONSTRAINT #6)."""
    output_dir = Path(output_dir)
    start_str = _read_paper_trading_start_date()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate": compute_gate_status(start_str, today=today),
        "calibration_history_rows": count_decision_log_rows(output_dir),
        "calibration": compute_calibration_status(),
        "staleness": compute_staleness(output_dir, now=now),
    }


def format_status(status: Dict[str, Any]) -> str:
    """Render the status dict as a human-readable report."""
    gate = status.get("gate", {})
    cal = status.get("calibration", {})
    stale = status.get("staleness", {})

    lines = []
    lines.append("=" * 66)
    lines.append("  InvestYo — Paper-Trading Track-Record Status")
    lines.append(f"  Generated: {status.get('generated_at', '—')}")
    lines.append("=" * 66)

    # --- Gate ---
    lines.append("")
    lines.append("90-Day Go-Live Gate")
    lines.append("-" * 66)
    start = gate.get("start_date") or "— (unset)"
    lines.append(f"  Start date        : {start}")
    de = gate.get("days_elapsed")
    dr = gate.get("days_remaining")
    lines.append(f"  Days elapsed      : {de if de is not None else '—'}")
    lines.append(f"  Days remaining    : {dr if dr is not None else '—'} (gate = {gate.get('gate_days')} days)")
    lines.append(f"  Go-live date      : {gate.get('go_live_date') or '—'}")
    lines.append(f"  Gate met          : {'YES' if gate.get('gate_met') else 'no'}")
    if gate.get("note"):
        lines.append(f"  Note              : {gate['note']}")

    # --- Calibration history depth ---
    lines.append("")
    lines.append("Calibration History Depth")
    lines.append("-" * 66)
    rows = status.get("calibration_history_rows", 0)
    lines.append(f"  decision_log.jsonl rows : {rows}")

    # --- Calibration coverage ---
    lines.append("")
    lines.append("Conviction Calibration (reused from daily_briefing)")
    lines.append("-" * 66)
    if cal.get("has_data"):
        lines.append(f"  MAE               : {cal.get('mae')}")
        if cal.get("bins_with_data") is not None:
            lines.append(f"  Bins with data    : {cal.get('bins_with_data')}/{cal.get('total_bins')}")
        if cal.get("total_trades") is not None:
            lines.append(f"  Total trades      : {cal.get('total_trades')}")
    else:
        # Show the reused note (e.g. "need >= 30 trades") verbatim, first line.
        md = str(cal.get("markdown", "")).strip()
        note_line = next((ln.strip() for ln in md.splitlines() if ln.strip() and not ln.startswith("#")), "No data yet.")
        lines.append(f"  {note_line}")

    # --- Staleness ---
    lines.append("")
    lines.append("Last-Run Staleness")
    lines.append("-" * 66)

    def _fmt_age(hours: Optional[float]) -> str:
        return f"{hours:.2f} h" if hours is not None else "— (missing)"

    lines.append(f"  heartbeat.txt        : {_fmt_age(stale.get('heartbeat_age_hours'))}")
    lines.append(f"  state_snapshot.json  : {_fmt_age(stale.get('snapshot_age_hours'))}")

    lines.append("")
    lines.append("=" * 66)
    return "\n".join(lines)


# ===========================================================================
# CLI
# ===========================================================================

def main(argv: Optional[list[str]] = None) -> int:
    """Entry point: ``python scripts/track_record_status.py``."""
    parser = argparse.ArgumentParser(
        description="Report InvestYo paper-trading track-record progress "
                    "(90-day gate, calibration depth/coverage, last-run staleness).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help="Directory containing decision_log.jsonl, heartbeat.txt, "
             "state_snapshot.json. Default: ./output",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON instead of the human-readable report.",
    )
    args = parser.parse_args(argv)

    status = build_status(Path(args.output_dir))

    if args.as_json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(format_status(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())

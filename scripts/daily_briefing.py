"""
scripts/daily_briefing.py
=========================
Daily morning digest for the InvestYo advisory platform.

Reads the latest pipeline state and writes a compact Markdown briefing to
``output/briefing_YYYY-MM-DD.md`` so the operator gets an at-a-glance view
of the current market regime, the day's top advisory actions, what changed
since yesterday, any symbols that failed to process, and a 30-day calibration
score.

Design
------
*  **No live network calls** — reads only existing output files:
   ``output/state_snapshot.json``, ``output/dead_letter.json``, the history
   dir for snapshot diffs, and the SQLite database for calibration.
*  **Dead-letter tolerant** (CONSTRAINT #6): every section is wrapped in
   try/except and degrades gracefully to "No data yet" — a missing file or
   empty database never prevents the briefing from being written.
*  **No fabricated metrics** (CONSTRAINT #4): calibration MAE is ``NaN`` when
   fewer than 30 conviction-annotated trades exist; it is never defaulted to
   a nice-looking number.

Usage
-----
    python -m scripts.daily_briefing                 # write to output/
    python -m scripts.daily_briefing --print         # also print to stdout
    python -m scripts.daily_briefing --output-dir /tmp/briefings

Wire-up in ``launch.command``
------------------------------
After the ``python main.py`` line (single-run or interval), append::

    python -m scripts.daily_briefing --print

so each launch ends with a fresh briefing printed to the same Terminal window.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Repo root resolution — script is invocable from any working directory.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths — resolved lazily so the module imports cleanly without ``settings``
# (important for test environments that lack all env-vars).
# ---------------------------------------------------------------------------
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "output"


# ===========================================================================
# Section helpers — each returns a Markdown block (never raises).
# ===========================================================================

def _load_snapshot(output_dir: Path) -> Dict[str, Any]:
    """Load the latest ``state_snapshot.json``.  Returns ``{}`` on failure."""
    path = output_dir / "state_snapshot.json"
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("daily_briefing: could not load snapshot: %s", exc)
    return {}


def _section_regime(snap: Dict[str, Any]) -> str:
    """Regime + kill-switch status."""
    regime = snap.get("market_regime") or snap.get("regime") or "—"
    vix = snap.get("vix")
    ks = snap.get("kill_switch_active", False)
    hmm = snap.get("hmm_risk_on_probability")

    lines: List[str] = ["## 🌐 Macro Regime\n"]
    lines.append(f"**Regime:** {regime}")
    if vix is not None:
        try:
            lines.append(f"  |  **VIX:** {float(vix):.1f}")
        except (TypeError, ValueError):
            pass
    if hmm is not None:
        try:
            lines.append(f"  |  **HMM risk-on p:** {float(hmm):.2f}")
        except (TypeError, ValueError):
            pass
    if ks:
        lines.append("\n\n> ⚠️  **Kill switch is ACTIVE** — advisory paused.")
    ts = snap.get("timestamp")
    if ts:
        lines.append(f"\n\n_Snapshot: {ts}_")
    return "".join(lines) + "\n"


def _section_top_actions(snap: Dict[str, Any], n: int = 3) -> str:
    """Top N action signals sorted by conviction (BUY-side first)."""
    signals: List[Dict[str, Any]] = snap.get("signals", [])
    if not signals:
        return "## 🎯 Top Actions\n\n_No pipeline signals available._\n"

    # Score each signal: BUY-side first (positive); SELL-side second (negative);
    # HOLD last.  Within a group, sort by conviction descending.
    _ACTION_PRIORITY = {
        "STRONG BUY": 3,
        "BUY": 2,
        "RISK REDUCE": 1,
        "HOLD": 0,
        "SELL": -1,
        "STRONG SELL": -2,
    }

    def _sort_key(sig: Dict[str, Any]):
        action = str(sig.get("action", sig.get("action_signal", ""))).upper()
        conv = float(sig.get("advisory_conviction", sig.get("conviction", 0)) or 0)
        priority = max(
            (_ACTION_PRIORITY[k] for k in _ACTION_PRIORITY if k in action),
            default=0,
        )
        return (-priority, -conv)

    ranked = sorted(signals, key=_sort_key)
    top = ranked[:n]

    lines: List[str] = [f"## 🎯 Top {n} Actions\n\n"]
    for i, sig in enumerate(top, start=1):
        sym = sig.get("symbol", "?")
        action = sig.get("action", sig.get("action_signal", "—"))
        conv = sig.get("advisory_conviction", sig.get("conviction"))
        conv_str = f"{float(conv):.2f}" if conv is not None else "—"
        rationale = sig.get("rationale", "")
        # Truncate rationale to first sentence for brevity.
        first_sentence = rationale.split(".")[0].strip() if rationale else ""
        first_sentence = first_sentence[:120] + "…" if len(first_sentence) > 120 else first_sentence
        lines.append(f"{i}. **{sym}** — {action} (conviction {conv_str})")
        if first_sentence:
            lines.append(f"\n   _↳ {first_sentence}._")
        lines.append("\n")
    return "".join(lines) + "\n"


def _section_delta(output_dir: Path) -> str:
    """Δ since last run from snapshot history."""
    try:
        from scripts.snapshot_diff import compute_diff_from_history
        from settings import settings

        diff = compute_diff_from_history(
            output_dir,
            conviction_delta_threshold=getattr(settings, "SNAPSHOT_CONVICTION_DELTA_THRESHOLD", 0.2),
        )
    except Exception as exc:
        logger.debug("daily_briefing: snapshot diff failed: %s", exc)
        return "## 🔄 Δ Since Last Run\n\n_No comparison history yet._\n"

    if diff is None or diff.is_empty:
        return "## 🔄 Δ Since Last Run\n\n_No material changes detected._\n"

    lines: List[str] = ["## 🔄 Δ Since Last Run\n\n"]
    if diff.regime_change:
        lines.append(f"- **Regime:** {diff.regime_change[0]} → **{diff.regime_change[1]}**\n")
    if diff.new_buys:
        lines.append(f"- **New BUYs:** {', '.join(diff.new_buys)}\n")
    if diff.action_flips:
        flips = [f"{f['symbol']} ({f['before']} → {f['after']})" for f in diff.action_flips]
        lines.append(f"- **Signal flips:** {'; '.join(flips)}\n")
    if diff.conviction_deltas:
        deltas = [
            f"{d['symbol']} ({d['before']:.2f} → {d['after']:.2f})"
            for d in diff.conviction_deltas
            if isinstance(d.get("before"), (int, float)) and isinstance(d.get("after"), (int, float))
        ]
        if deltas:
            lines.append(f"- **Conviction moves:** {'; '.join(deltas)}\n")
    if diff.added_holdings:
        lines.append(f"- **Holdings added:** {', '.join(diff.added_holdings)}\n")
    if diff.dropped_holdings:
        lines.append(f"- **Holdings dropped:** {', '.join(diff.dropped_holdings)}\n")
    if diff.notes:
        for note in diff.notes:
            lines.append(f"  _{note}_\n")
    return "".join(lines) + "\n"


def _section_dead_letters(output_dir: Path) -> str:
    """Symbols that failed in the last pipeline run."""
    try:
        from gui.dead_letter import read_dead_letter

        report = read_dead_letter(output_dir / "dead_letter.json")
    except Exception as exc:
        logger.debug("daily_briefing: dead_letter read failed: %s", exc)
        return "## ☠️ Dead-Lettered Symbols\n\n_Could not read dead_letter.json._\n"

    if report is None:
        return "## ☠️ Dead-Lettered Symbols\n\n_No dead_letter.json found (no run completed yet)._\n"
    if report.is_clean:
        return "## ☠️ Dead-Lettered Symbols\n\n✅ None — last run was clean.\n"

    lines: List[str] = [f"## ☠️ Dead-Lettered Symbols ({len(report.entries)} failed)\n\n"]
    for entry in report.entries:
        lines.append(f"- **{entry.symbol}** @ `{entry.stage}` — {entry.error[:120]}\n")
    lines.append(f"\n_Run: {report.run_id}_\n")
    return "".join(lines) + "\n"


def _section_calibration() -> str:
    """30-day calibration score (MAE vs. perfect diagonal).

    Uses ``evaluation_engine.calibration_curve`` which reads from the SQLite
    ``TransactionsStore``.  Returns a "No conviction data yet" placeholder when
    fewer than 30 annotated trades exist — never fabricates a score.
    """
    try:
        from transactions_store import TransactionsStore
        from evaluation_engine import calibration_curve

        ts = TransactionsStore()
        cal_df = calibration_curve(ts, n_bins=10, min_trades_per_bin=5)
    except Exception as exc:
        logger.debug("daily_briefing: calibration failed: %s", exc)
        return "## 📐 30-Day Calibration\n\n_Could not compute (database unavailable)._\n"

    if cal_df.empty:
        return "## 📐 30-Day Calibration\n\n_No conviction-annotated closed trades yet (need ≥ 30)._\n"

    import math
    valid = cal_df.dropna(subset=["win_rate"])
    if valid.empty:
        return "## 📐 30-Day Calibration\n\n_Insufficient data in each bin (< 5 trades per bucket)._\n"

    mae = float((valid["win_rate"] - valid["perfect_calibration"]).abs().mean())
    n_bins_with_data = len(valid)
    overall_wr_row = cal_df.dropna(subset=["win_rate", "count"])
    total_trades = int(overall_wr_row["count"].sum()) if not overall_wr_row.empty else 0

    if math.isnan(mae):
        return "## 📐 30-Day Calibration\n\n_MAE: NaN (insufficient data)._\n"

    severity = "🟢 Good" if mae < 0.10 else ("🟡 Monitor" if mae < 0.15 else "🔴 Review")
    lines: List[str] = ["## 📐 30-Day Calibration\n\n"]
    lines.append(f"**MAE:** {mae:.3f}  |  **{severity}**  |  **Bins w/ data:** {n_bins_with_data}/10  |  **Total trades:** {total_trades}\n\n")
    lines.append("| Conviction range | Win rate | Perfect | Δ |\n")
    lines.append("|---|---|---|---|\n")
    for _, row in valid.iterrows():
        wr = float(row["win_rate"])
        pc = float(row["perfect_calibration"])
        delta = wr - pc
        delta_str = f"+{delta:.2f}" if delta >= 0 else f"{delta:.2f}"
        lines.append(
            f"| {row['bin_low']:.1f}–{row['bin_high']:.1f} "
            f"| {wr:.2f} | {pc:.2f} | {delta_str} |\n"
        )
    return "".join(lines) + "\n"


# ===========================================================================
# Main assembler
# ===========================================================================

def generate_briefing(output_dir: Path = _DEFAULT_OUTPUT_DIR) -> str:
    """Assemble the full daily briefing as a Markdown string.

    Parameters
    ----------
    output_dir:
        Directory containing ``state_snapshot.json``, ``dead_letter.json``,
        and the ``history/`` subfolder.  Defaults to ``./output``.

    Returns
    -------
    str
        Complete Markdown content — never raises (CONSTRAINT #6).
    """
    output_dir = Path(output_dir)
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    snap = _load_snapshot(output_dir)

    header = (
        f"# InvestYo Daily Briefing — {today}\n\n"
        f"_Generated: {now}_\n\n"
        "---\n\n"
    )

    sections = [
        header,
        _section_regime(snap),
        "\n---\n\n",
        _section_top_actions(snap, n=3),
        "\n---\n\n",
        _section_delta(output_dir),
        "\n---\n\n",
        _section_dead_letters(output_dir),
        "\n---\n\n",
        _section_calibration(),
        "\n---\n\n",
        "_Run `python -m scripts.daily_briefing` to refresh · "
        "[Report](output/daily_report.html)_\n",
    ]
    return "".join(sections)


def write_briefing(output_dir: Path = _DEFAULT_OUTPUT_DIR) -> Path:
    """Write the daily briefing to ``output/briefing_YYYY-MM-DD.md``.

    Returns the path of the written file.  Never raises — write failures are
    logged at ERROR level and the generated content is returned so callers can
    still print it (CONSTRAINT #6).
    """
    output_dir = Path(output_dir)
    content = generate_briefing(output_dir)
    today = date.today().isoformat()
    out_path = output_dir / f"briefing_{today}.md"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        logger.info("Daily briefing written to %s", out_path)
    except OSError as exc:
        logger.error("Could not write briefing to %s: %s", out_path, exc)
    return out_path


# ===========================================================================
# CLI
# ===========================================================================

def main(argv: List[str] | None = None) -> int:
    """Entry point: ``python -m scripts.daily_briefing``."""
    parser = argparse.ArgumentParser(
        description="Generate InvestYo daily advisory briefing (Markdown).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help="Directory containing state_snapshot.json, dead_letter.json, history/. "
             "Default: ./output",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_output",
        help="Also print the briefing to stdout after writing the file.",
    )
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    out_path = write_briefing(output_dir)

    if args.print_output:
        content = out_path.read_text(encoding="utf-8") if out_path.exists() else generate_briefing(output_dir)
        print("\n" + "=" * 70)
        print(content)
        print("=" * 70)
        print(f"  Briefing written to: {out_path}")
        print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())

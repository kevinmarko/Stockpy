"""Automated Markdown export script for Google NotebookLM ingestion.
Formats the platform's current state (portfolio, follows, macro regime) into a
structured document. Run via the `output/notebooklm_source.md` path.
"""

import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

# Repo-root import shim
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Venv re-exec + .env loading
from scripts._bootstrap import bootstrap  # noqa: E402
bootstrap()

from data.historical_store import HistoricalStore  # noqa: E402
from pilots.follows_store import FollowsStore  # noqa: E402
from pilots.portfolio import serialize_portfolio  # noqa: E402
from settings import settings  # noqa: E402

logger = logging.getLogger("notebooklm_export")


class _OneShotMacroDataEngine:
    """Adapter passed to ``HistoricalStore.get_macro(..., data_engine=...)``
    so the three independent VIX/T10Y2Y/HY-OAS lookups below share ONE live
    FRED fetch instead of each independently re-triggering
    ``fetch_macro_history()``.

    ``store`` below is constructed ``readonly=True`` (SQLite ``mode=ro``), so
    ``get_macro()``'s own cache-freshness top-up WRITE always fails and is
    silently swallowed -- meaning its staleness check never actually clears
    and every one of the three ``get_macro()`` calls would otherwise
    independently re-fetch ALL FRED series from the network, every single
    run. This wrapper caps that at exactly one live fetch per script
    invocation instead of up to three.
    """

    def __init__(self) -> None:
        self._df = None
        self._fetched = False

    def fetch_macro_history(self):
        if not self._fetched:
            self._fetched = True
            try:
                if settings.FRED_API_KEY:
                    from data_engine import DataEngine
                    self._df = DataEngine(settings.FRED_API_KEY).fetch_macro_history()
            except Exception as exc:
                logger.warning(f"NotebookLM export: one-shot macro fetch failed: {exc}")
            if self._df is None:
                import pandas as pd
                self._df = pd.DataFrame()
        return self._df


def _fmt_money(value) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    return f"${value:,.2f}"

def _fmt_num(value) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "N/A"
    return str(value)

def build_export() -> None:
    lines = []
    lines.append("# Stockpy System Export")
    lines.append(f"**Generated At (UTC):** {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    # Constructed once, up front, and shared by every section below — NOT
    # re-created per section. Sections deliberately do NOT share failure
    # state beyond this: a construction failure here degrades every section
    # to "unavailable" (store stays None), but each section still runs its
    # own try/except so a failure fetching macro data can never take down
    # the portfolio/follows sections that don't depend on it.
    #
    # Note: `readonly=True` is DB-write-enforced (SQLite `mode=ro`), but it
    # does NOT prevent `get_macro()`'s internal staleness top-up from making
    # a live FRED network call before its write attempt fails closed — see
    # `HistoricalStore.get_macro()`'s own docstring. `_OneShotMacroDataEngine`
    # below caps that at one live fetch per run instead of one per series.
    try:
        store = HistoricalStore(readonly=True)
    except Exception as exc:
        logger.warning(f"Failed to construct HistoricalStore: {exc}")
        store = None

    # 1. Macro Context
    lines.append("## Macro Context")
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        macro_engine = _OneShotMacroDataEngine()
        vix_series = store.get_macro("VIXCLS", data_engine=macro_engine)
        t10y2y_series = store.get_macro("T10Y2Y", data_engine=macro_engine)
        hy_oas_series = store.get_macro("BAMLH0A0HYM2", data_engine=macro_engine)

        has_macro = False
        if not vix_series.empty:
            lines.append(f"- **VIX**: {_fmt_num(vix_series.iloc[-1])}")
            has_macro = True
        if not t10y2y_series.empty:
            lines.append(f"- **10Y-2Y Spread**: {_fmt_num(t10y2y_series.iloc[-1])}%")
            has_macro = True
        if not hy_oas_series.empty:
            lines.append(f"- **High Yield OAS**: {_fmt_num(hy_oas_series.iloc[-1])}%")
            has_macro = True

        if not has_macro:
            lines.append("Macro data is currently unavailable.")
    except Exception as exc:
        logger.warning(f"Failed to fetch macro data: {exc}")
        lines.append("Macro data is currently unavailable.")
    lines.append("")

    # 2. Portfolio
    lines.append("## Current Portfolio")
    try:
        if store is None:
            raise RuntimeError("HistoricalStore unavailable")
        snap = store.latest_account_snapshot()
        if snap:
            # Built into a local buffer and only merged into `lines` once the
            # WHOLE section completes without raising — a later position that
            # fails to format (e.g. a hand-edited/legacy DB row) must never
            # leave earlier real lines in the document immediately followed
            # by the except branch's "unavailable" message below.
            section_lines = []
            port = serialize_portfolio(snap)
            section_lines.append(f"- **Total Equity**: {_fmt_money(port.get('total_equity'))}")
            section_lines.append(f"- **Buying Power**: {_fmt_money(port.get('buying_power'))}")
            fetched_at = port.get("fetched_at")
            if fetched_at:
                staleness = " (stale)" if port.get("is_stale") else ""
                section_lines.append(f"- **Snapshot As Of**: {fetched_at}{staleness}")
            section_lines.append("")
            positions = port.get("positions", [])
            if positions:
                section_lines.append("### Positions")
                for p in positions:
                    symbol = p.get('symbol', 'Unknown')
                    qty = _fmt_num(p.get('qty'))
                    avg_cost = _fmt_money(p.get('avg_cost'))
                    mkt_val = _fmt_money(p.get('market_value'))
                    name = p.get('name') or ''
                    name_str = f" ({name})" if name else ""
                    section_lines.append(f"- **{symbol}**{name_str}: {qty} shares @ {avg_cost} (Market Value: {mkt_val})")
            else:
                section_lines.append("No open positions.")
            lines.extend(section_lines)
        else:
            lines.append("Portfolio snapshot is unavailable.")
    except Exception as exc:
        logger.warning(f"Failed to fetch portfolio: {exc}")
        lines.append("Portfolio snapshot is unavailable.")
    lines.append("")

    # 3. Active Follows
    lines.append("## Active Pilot Follows")
    try:
        follows = FollowsStore().list_active()
        if follows:
            # Same buffer-then-commit discipline as the Portfolio section
            # above: a later follow row that fails to format must not leave
            # earlier real follow lines in the document.
            section_lines = []
            for f in follows:
                pilot_id = f.get('pilot_id', 'Unknown')
                amount = _fmt_money(f.get('amount'))
                status = f.get('status', 'Unknown')
                section_lines.append(f"- **Pilot ID**: {pilot_id} | **Amount**: {amount} | **Status**: {status}")
            lines.extend(section_lines)
        else:
            lines.append("No active pilot follows.")
    except Exception as exc:
        logger.warning(f"Failed to fetch active follows: {exc}")
        lines.append("Active pilot follows are unavailable.")

    out_dir = settings.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "notebooklm_source.md"

    # Atomic write (pid+tid-scoped temp file + rename), matching this repo's
    # established shared pattern (reporting/atomic_write.py::atomic_write_json,
    # pilots/follows_store.py::FollowsStore._save) so (a) two concurrent
    # invocations targeting the same path can never collide on the same temp
    # name — a bare `out_path.with_suffix(".tmp")` is NOT race-safe for that —
    # and (b) a write/rename failure logs, cleans up the stray temp file, and
    # re-raises rather than silently leaving one behind.
    tmp_path = out_path.with_name(f"{out_path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        tmp_path.write_text("\n".join(lines), encoding="utf-8")
        tmp_path.replace(out_path)
    except Exception as exc:
        logger.warning(f"Failed to write NotebookLM export to {out_path}: {exc}")
        tmp_path.unlink(missing_ok=True)
        raise
    logger.info(f"NotebookLM export successfully written to {out_path}")
    print(f"Export written to {out_path}")

if __name__ == "__main__":
    # This script takes no CLI arguments, but the `argparse` scaffolding
    # below is NOT dead code -- do not remove it as a cleanup.
    #
    # `scripts/build_command_manifest.py` (via `cli_introspect/capture.py`)
    # introspects every entry point in `cli_introspect/targets.py` -- this
    # script included -- by monkeypatching `ArgumentParser.parse_args` to
    # capture the built parser and unwind BEFORE any real work runs. That
    # harness needs `parse_args()` to actually be called at the top of
    # `__main__`, or it falls through, `build_export()` runs for real (a live
    # DB read + a real file write), and the target is dead-lettered out of
    # the manifest with "parse_args was never called" -- exactly what
    # happened when this scaffolding was previously removed as "dead" in PR
    # #971. It's also what makes `--help` side-effect-free for a human
    # operator, instead of silently running the real export.
    import argparse
    parser = argparse.ArgumentParser(description="Generate a NotebookLM export.")
    parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    build_export()

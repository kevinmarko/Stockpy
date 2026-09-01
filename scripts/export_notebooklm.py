"""Automated Markdown export script for Google NotebookLM ingestion.
Formats the platform's current state (portfolio, follows, macro regime) into a
structured document. Run via the `output/notebooklm_source.md` path.
"""

import logging
import sys
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
from settings import settings  # noqa: E402

# `api.pilots_api._serialize_portfolio` is deliberately NOT imported here at
# module top. `api/pilots_api.py` constructs a full FastAPI app and
# transitively imports a large, heavy module graph (gui/*, llm/*, ml/*,
# execution/*, agents.rag_orchestrator, ...) — if any of that ever hard-fails
# to import in a given environment, importing it at module top would crash
# THIS script before the Macro Context / Active Pilot Follows sections (which
# don't need it) ever ran, defeating the per-section degrade-don't-crash
# design below. It's imported lazily, inside the Portfolio section's own
# try/except instead, so an import failure there degrades only that one
# section (CONSTRAINT #6 — fail closed on the smallest possible unit).

logger = logging.getLogger("notebooklm_export")

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
    # `HistoricalStore.get_macro()`'s own docstring. That's expected,
    # pre-existing behavior shared by every caller of `get_macro()`, not
    # specific to this script.
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
        vix_series = store.get_macro("VIXCLS")
        t10y2y_series = store.get_macro("T10Y2Y")
        hy_oas_series = store.get_macro("BAMLH0A0HYM2")

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
        from api.pilots_api import _serialize_portfolio  # lazy — see import note above
        snap = store.latest_account_snapshot()
        if snap:
            port = _serialize_portfolio(snap)
            lines.append(f"- **Total Equity**: {_fmt_money(port.get('total_equity'))}")
            lines.append(f"- **Buying Power**: {_fmt_money(port.get('buying_power'))}")
            fetched_at = port.get("fetched_at")
            if fetched_at:
                staleness = " (stale)" if port.get("is_stale") else ""
                lines.append(f"- **Snapshot As Of**: {fetched_at}{staleness}")
            lines.append("")
            positions = port.get("positions", [])
            if positions:
                lines.append("### Positions")
                for p in positions:
                    symbol = p.get('symbol', 'Unknown')
                    qty = _fmt_num(p.get('qty'))
                    avg_cost = _fmt_money(p.get('avg_cost'))
                    mkt_val = _fmt_money(p.get('market_value'))
                    name = p.get('name') or ''
                    name_str = f" ({name})" if name else ""
                    lines.append(f"- **{symbol}**{name_str}: {qty} shares @ {avg_cost} (Market Value: {mkt_val})")
            else:
                lines.append("No open positions.")
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
            for f in follows:
                pilot_id = f.get('pilot_id', 'Unknown')
                amount = _fmt_money(f.get('amount'))
                status = f.get('status', 'Unknown')
                lines.append(f"- **Pilot ID**: {pilot_id} | **Amount**: {amount} | **Status**: {status}")
        else:
            lines.append("No active pilot follows.")
    except Exception as exc:
        logger.warning(f"Failed to fetch active follows: {exc}")
        lines.append("Active pilot follows are unavailable.")
    
    out_dir = settings.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "notebooklm_source.md"

    # Atomic write (temp file + rename), matching this repo's established
    # convention (e.g. execution/kill_switch.py, desktop/daemon_runtime.py's
    # _write_daemon_file) so a process kill mid-write can never leave a
    # truncated/corrupt notebooklm_source.md behind.
    tmp_path = out_path.with_suffix(".md.tmp")
    tmp_path.write_text("\n".join(lines), encoding="utf-8")
    tmp_path.replace(out_path)
    logger.info(f"NotebookLM export successfully written to {out_path}")
    print(f"Export written to {out_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate a NotebookLM export.")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    build_export()

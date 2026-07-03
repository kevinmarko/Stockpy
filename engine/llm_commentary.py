"""
engine/llm_commentary.py — CLI entry point for Tier 9 LLM commentary.
=====================================================================

Invocation::

    python -m engine.llm_commentary SYMBOL [--alert]

Default mode (no flag): builds a :class:`engine.advisory.Recommendation`
for ``SYMBOL`` using the default providers, calls
:func:`engine.advisory.enrich_with_llm_rationale`, and pretty-prints both
the deterministic template paragraph and the (optional) Claude-generated
``AnalystRationale`` fields.  Exits 0 on soft-fail — the deterministic
template is always shown.

``--alert`` mode: constructs a synthetic :class:`watch_engine.WatchAlert`
from the symbol's recommendation, dispatches it through
:func:`watch_engine.dispatch_watch_alerts` with ``alerting.notify``
monkey-stubbed to print rather than push, so the operator can preview the
augmented body without sending a real ntfy notification.

Soft-fail contract (CONSTRAINT #6): any provider / network / parse
failure leaves the template intact and exits 0 with an informational
note.  The CLI returns nonzero only for hard usage errors (unknown
argument, no symbol).

This module deliberately re-uses the existing advisory pipeline — it
does NOT re-implement signal scoring or rationale assembly.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

# Ensure the project root is importable when invoked as ``python -m
# engine.llm_commentary`` from outside the package (mirrors main.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _print_rec(rec) -> None:
    """Render a Recommendation (with optional llm_rationale) to stdout."""
    print(f"=== {rec.symbol} — {rec.action} ===")
    print(f"Strategy:      {rec.strategy}")
    print(f"Conviction:    {rec.conviction:.3f}")
    print(f"Forecast:      {rec.forecast}")
    print(f"Position %:    {rec.suggested_position_pct:.4f}")
    print(f"Data quality:  {rec.data_quality}")
    print()
    print("--- Deterministic rationale (always present) ---")
    print(rec.rationale)
    print()
    if rec.llm_rationale:
        print("--- Claude analyst commentary (Tier 9) ---")
        print(f"Headline:      {rec.llm_rationale.get('headline', '?')}")
        print()
        print(f"Why now:       {rec.llm_rationale.get('why_now', '?')}")
        print()
        risks = rec.llm_rationale.get("key_risks") or []
        if risks:
            print("Key risks:")
            for r in risks:
                print(f"  • {r}")
        print()
        print(f"Invalidation:  {rec.llm_rationale.get('invalidation', '?')}")
    else:
        print("--- Claude analyst commentary (Tier 9) ---")
        print("LLM commentary unavailable (provider returned None — falling back to template).")


def _print_alert_preview(symbol: str, dispatched_msg: str) -> None:
    print(f"=== Synthetic alert preview for {symbol} ===")
    print(dispatched_msg)


def _build_synthetic_watch_alert(rec):
    """Construct a synthetic WatchAlert from a Recommendation for --alert preview."""
    from watch_engine import WatchAlert  # noqa: PLC0415

    return WatchAlert(
        symbol=rec.symbol,
        rule_type="cli_preview",
        priority="default",
        title=f"Preview: {rec.symbol} — {rec.action}",
        message=f"{rec.symbol} action={rec.action} conviction={rec.conviction:.2f}",
        trigger_detail=f"action={rec.action}, conviction={rec.conviction:.2f}",
    )


def main(argv: Optional[list] = None) -> int:
    # Mirror the project convention: load .env on entry so credentials in
    # the user's local config reach pydantic-settings AND os.environ.
    load_dotenv(override=False)

    parser = argparse.ArgumentParser(
        prog="python -m engine.llm_commentary",
        description="Tier 9 — preview LLM-augmented advisory commentary for a single symbol.",
    )
    parser.add_argument("symbol", help="Uppercase ticker, e.g. AAPL")
    parser.add_argument(
        "--alert",
        action="store_true",
        help="Build a synthetic watch alert and print the augmented ntfy body.",
    )
    args = parser.parse_args(argv)

    symbol = (args.symbol or "").strip().upper()
    if not symbol:
        parser.error("symbol must not be empty")

    # Lazy imports — heavy engines and providers are only loaded when we
    # actually need them, mirroring engine/advisory.py's lazy pattern.
    from data.market_data import get_provider  # noqa: PLC0415
    from data.robinhood_portfolio import AccountSnapshot  # noqa: PLC0415
    from engine.advisory import enrich_with_llm_rationale, evaluate  # noqa: PLC0415

    try:
        market = get_provider()
    except Exception as exc:
        print(f"ERROR: failed to construct market-data provider: {exc}", file=sys.stderr)
        return 2

    snapshot: Optional[AccountSnapshot] = None
    try:
        from data.robinhood_portfolio import fetch_account_snapshot  # noqa: PLC0415

        snapshot = fetch_account_snapshot()
    except Exception as exc:
        logger.info("Robinhood snapshot unavailable (%s) — running without holding context.", exc)
        snapshot = None

    position = None
    if snapshot is not None and isinstance(getattr(snapshot, "positions", None), dict):
        position = snapshot.positions.get(symbol)

    try:
        rec = evaluate(
            symbol=symbol,
            position=position,
            market=market,
            snapshot=snapshot,
            macro_dto=None,
            transactions_store=None,
            context_extras=None,
        )
    except Exception as exc:
        print(f"ERROR: advisory evaluate() raised: {exc}", file=sys.stderr)
        return 3

    # On-demand LLM enrichment — soft-fails to the original rec.
    rec = enrich_with_llm_rationale(rec, context={})

    if args.alert:
        # Build a synthetic alert and run it through the real dispatch path
        # with `alerting.notify` redirected to print so nothing is pushed.
        import alerting  # noqa: PLC0415
        captured: list = []

        def _stub_notify(title, message, priority="default"):
            captured.append((title, message, priority))

        orig = getattr(alerting, "notify", None)
        try:
            alerting.notify = _stub_notify  # type: ignore[attr-defined]
            from watch_engine import dispatch_watch_alerts  # noqa: PLC0415

            wa = _build_synthetic_watch_alert(rec)
            dispatch_watch_alerts([wa])
        finally:
            if orig is not None:
                alerting.notify = orig  # type: ignore[attr-defined]

        if captured:
            for _t, m, _p in captured:
                _print_alert_preview(symbol, m)
        else:
            _print_alert_preview(symbol, "(no message captured — dispatch path soft-failed)")
        return 0

    _print_rec(rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())

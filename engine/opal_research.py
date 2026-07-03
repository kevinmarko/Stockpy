"""
engine/opal_research.py — CLI entry point for Tier 9 Scope 4 Opal.
=====================================================================

Invocation::

    python -m engine.opal_research SYMBOL

Calls :func:`llm.research.generate_research_brief` directly for ``SYMBOL``
and pretty-prints the resulting :class:`llm.schemas.ResearchBrief` fields —
or an "Opal research unavailable" sentinel when Opal is disabled (the
project default), misconfigured (no ``OPENAI_API_KEY``), or the call
soft-fails for any other reason (CONSTRAINT #6).

Exit code is always 0 on soft-fail — this CLI is a preview tool, not a
readiness gate. It returns nonzero only for hard usage errors (no symbol).

This module deliberately re-uses ``llm.research`` — it does NOT re-implement
grounding, caching, or provider-selection logic.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

# Ensure the project root is importable when invoked as ``python -m
# engine.opal_research`` from outside the package (mirrors
# engine/llm_commentary.py and main.py).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _print_brief(symbol: str, brief) -> None:
    """Render a ResearchBrief (or the unavailable sentinel) to stdout."""
    print(f"=== Opal research brief — {symbol} ===")
    if brief is None:
        print(
            "Opal research unavailable (disabled via OPAL_RESEARCH_ENABLED, "
            "no OPENAI_API_KEY configured, or the call soft-failed — falling "
            "back to no research context, exactly as if Opal didn't exist)."
        )
        return

    print(f"Thesis context:   {brief.thesis_context}")
    print()
    print("Catalysts:")
    for catalyst in brief.catalysts:
        print(f"  • {catalyst}")
    print()
    print("Risk factors:")
    for risk in brief.risk_factors:
        print(f"  • {risk}")
    if brief.recent_developments:
        print()
        print("Recent developments:")
        for dev in brief.recent_developments:
            print(f"  • {dev}")
    print()
    print(f"Data confidence:  {brief.data_confidence}")
    print(f"Sources note:     {brief.sources_note}")


def main(argv: Optional[list] = None) -> int:
    # Mirror the project convention: load .env on entry so credentials in
    # the user's local config reach pydantic-settings AND os.environ.
    load_dotenv(override=False)

    parser = argparse.ArgumentParser(
        prog="python -m engine.opal_research",
        description=(
            "Tier 9 Scope 4 — preview Opal's grounded research brief for a "
            "single symbol."
        ),
    )
    parser.add_argument("symbol", help="Uppercase ticker, e.g. AAPL")
    args = parser.parse_args(argv)

    symbol = (args.symbol or "").strip().upper()
    if not symbol:
        parser.error("symbol must not be empty")

    # Lazy import — keeps this CLI's own import-time surface free of any
    # LLM SDK reach; llm.research itself lazy-imports openai + the Finnhub
    # client, so importing it here does not import openai either.
    from llm.research import generate_research_brief  # noqa: PLC0415

    try:
        brief = generate_research_brief(symbol, context={})
    except Exception as exc:  # pragma: no cover - generate_research_brief already soft-fails
        logger.warning("generate_research_brief raised unexpectedly for %s: %s", symbol, exc)
        brief = None

    _print_brief(symbol, brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())

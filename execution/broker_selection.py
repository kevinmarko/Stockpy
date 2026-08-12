"""
execution/broker_selection.py
==============================
Single source of truth for "which broker backend should actually be used
this cycle."

``settings.BROKER_BACKEND`` selects between the real Alpaca broker and
``FMPPaperBroker`` (a local SQLite paper ledger — see CLAUDE.md's "FMP-based
paper trading engine" bullet). Two independent call sites resolve this
setting into an actual broker construction: ``main_orchestrator.py``'s
``_execute_broker_orders`` and ``robinhood_execution_mcp.py``'s
``_get_broker()``. Both MUST go through ``resolve_broker_backend()`` below
instead of re-implementing the "is this run genuinely going live" safety
check independently — a prior review found the two had drifted, with only
one of the two carrying the guard that force-falls-back to Alpaca when
``BROKER_BACKEND=='fmp_paper'`` while the run is configured to place real
orders.

This is a separate module (not ``execution/broker_base.py``, which is a
minimal, dependency-light ABC/dataclass file imported very broadly,
including by tests) so the ``settings``/``telemetry``/``observability.alerts``
imports needed here don't get pulled into every consumer of the base
interface types.
"""

from __future__ import annotations


def is_going_live() -> bool:
    """True when this process is configured to submit real, live orders.

    ``ADVISORY_ONLY`` is the Tier 5.1 execution quarantine; ``ALPACA_PAPER``
    is the paper-vs-live sandbox flag. A run is "going live" only when
    neither safety net is engaged. Read via ``getattr`` with the same
    defensive defaults used throughout this codebase (e.g.
    ``main_orchestrator.py``'s ``_execute_broker_orders``) so a stripped-down
    ``Settings`` stub in a test never raises.
    """
    from settings import settings

    advisory_only = getattr(settings, "ADVISORY_ONLY", True)
    alpaca_paper = getattr(settings, "ALPACA_PAPER", True)
    return not advisory_only and not alpaca_paper


def resolve_broker_backend() -> str:
    """Resolve ``settings.BROKER_BACKEND`` to the backend that should
    actually be constructed this cycle.

    This is the single source of truth for "which broker should actually be
    used" — both ``main_orchestrator.py``'s ``_execute_broker_orders`` and
    ``robinhood_execution_mcp.py``'s ``_get_broker()`` call this instead of
    each re-implementing the fmp_paper/live-trading safety check
    independently, so the two call sites can never drift again.

    ``BROKER_BACKEND='fmp_paper'`` routes orders to a local SQLite paper
    ledger (``execution/fmp_paper_broker.py``) rather than a real broker. If
    the run is genuinely going live (see ``is_going_live()``) while
    ``BROKER_BACKEND`` is still ``'fmp_paper'``, that is almost certainly an
    operator misconfiguration — silently paper-trading instead of placing
    real orders is a worse failure mode than falling back to Alpaca, so this
    logs CRITICAL, fires an alert, and forces ``'alpaca'``.
    """
    from settings import settings

    broker_backend = getattr(settings, "BROKER_BACKEND", "alpaca")
    if broker_backend == "fmp_paper" and is_going_live():
        from diagnostics_and_visuals import telemetry
        from observability.alerts import send_alert

        msg = (
            "BROKER_BACKEND='fmp_paper' is invalid for live trading. "
            "Forcing 'alpaca' fallback."
        )
        telemetry.error(msg)
        send_alert(level="CRITICAL", message=msg)
        return "alpaca"

    return broker_backend

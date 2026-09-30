"""
execution/broker_selection.py
==============================
Single source of truth for "which broker backend should actually be used
this cycle."

The automated pipeline has exactly one broker: ``FMPPaperBroker`` (a local
SQLite paper ledger that fills at live FMP quotes). Alpaca was removed on
2026-09-30. Real money moves only through the Robinhood execution queue,
with per-trade human confirmation — never through this module.

Two call sites resolve a broker here: ``main_orchestrator.py``'s
``_execute_broker_orders`` and ``broker_live_execution_mcp.py``'s
``_get_broker()``. Both MUST go through ``resolve_broker_backend()`` so the
"is this run going live" safety check can never drift between them.

This is a separate module (not ``execution/broker_base.py``, which is a
minimal, dependency-light ABC/dataclass file imported very broadly,
including by tests) so the ``settings``/``telemetry``/``observability.alerts``
imports needed here don't get pulled into every consumer of the base
interface types.
"""

from __future__ import annotations

from typing import Optional


def is_going_live() -> bool:
    """True when this process is configured for live (real-money) trading.

    ``ADVISORY_ONLY`` is the Tier 5.1 execution quarantine; ``PAPER_TRADING``
    is the paper-vs-live posture. A run is "going live" only when neither
    safety net is engaged. Read via ``getattr`` with the same defensive
    defaults used throughout this codebase so a stripped-down ``Settings``
    stub in a test never raises.
    """
    from settings import settings

    advisory_only = getattr(settings, "ADVISORY_ONLY", True)
    paper_trading = getattr(settings, "PAPER_TRADING", True)
    return not advisory_only and not paper_trading


def resolve_broker_backend() -> Optional[str]:
    """Return the broker backend the automated pipeline may use this cycle,
    or ``None`` when it must place no orders at all.

    Paper posture → ``'fmp_paper'``. Going live (see ``is_going_live()``) →
    ``None``: the automated pipeline has no live broker, and silently paper-
    trading while the operator believes they are live would be a worse
    failure mode than trading nothing. That case logs CRITICAL and fires an
    alert so the misconfiguration is visible; real orders go through the
    Robinhood queue instead.
    """
    if is_going_live():
        from diagnostics_and_visuals import telemetry
        from observability.alerts import send_alert

        msg = (
            "PAPER_TRADING=False with ADVISORY_ONLY=False: the automated "
            "pipeline has no live broker (Alpaca was removed), so it places no "
            "orders. Real-money trades go through the Robinhood execution queue."
        )
        telemetry.error(msg)
        send_alert(level="CRITICAL", message=msg)
        return None

    return "fmp_paper"

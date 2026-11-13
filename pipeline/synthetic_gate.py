"""The synthetic-data gate: one flag, set once per cycle, honored by every writer.

A cycle whose market data comes from ``MockDataEngine`` (flat $10 bars,
fabricated fundamentals and macro presets) must never leave durable or outward
output behind (CONSTRAINT #4: never fabricate). The cycle still computes, so
the wiring is exercised, but it writes nothing a reader could mistake for a
real result.

How it works
------------
* ``AsyncDataFetchStep`` (``pipeline/production_steps.py``) resolves the
  cycle's engine and calls ``data_engine.is_synthetic_provider(engine)``. When
  it is synthetic, or when the price fetch came back empty and the step fell
  back to ``MockDataEngine``, it calls :func:`mark_cycle_synthetic`, which sets
  ``ctx.context_extras["data_is_synthetic"] = True`` plus a reason, logs one
  CRITICAL line and sends one deduplicated CRITICAL alert.
* Every step that writes durable or outward output reads the flag through
  :func:`is_cycle_synthetic` and skips: ``AgenticQueueStep`` (queue, advisory
  source, watch state, pushes), ``BrokerExecutionStep`` (paper orders),
  ``StateSnapshotStep`` (``state_snapshot.json`` + ``history/``, the HTML
  dashboards, the printed payload) and the freshness marker
  ``last_data_refresh.txt``.
* ``RunPipelineStep`` builds its own inner ``RunContext`` in
  ``main_orchestrator.run_pipeline``; it hands the flag over with
  :func:`propagate_synthetic_flag` (silently: the outer cycle already logged
  and alerted), so the inner writers skip too: the forecast ledger
  (``update_actuals``/``record_forecasts``) and the model-artifact cache
  (``ForecastingStep``, via ``generate_forecast(persist=False)``), the
  ``macro_history`` top-up and the warm HMM fit (``MacroStep``), the PIT
  feature snapshot, ``dead_letter.json``, the cap-audit and symbol-rating
  rows and the sizing-cap alert (``StrategyEvalStep``). ``AdvisoryOverlayStep``
  passes ``persist_forecast=False`` to ``engine.advisory.evaluate`` so its
  own forecast ledger rows are skipped as well.
* The run is still recorded as ``succeeded`` (the ``GET /status`` contract is
  frozen). ``main_orchestrator._main_body_impl`` puts
  :func:`synthetic_progress_message` in the run's progress ``message`` (so
  ``RunRecord.progress["message"]`` and ``GET /run/{id}/status`` say why
  nothing was written) and ``_build_daily_summary`` adds a warning line.

Deliberately kept on a synthetic cycle: archives of REAL vendor data that do
not depend on the cycle's synthetic inputs (FMP analyst/earnings/insider/
sector rows, fundamentals and bars fetched through
``data.market_data.get_provider()``, news/social sentiment documents and
FinBERT scores, sector-selection correlations). A real cycle would write the
same rows. The full inventory, with the reason for each, is in
``.claude/synthetic_data_gate_inner_writers_walkthrough.md``.

This module is a dependency-light leaf (stdlib only at import time) so the MCP
server and the verify helper can import its constants cheaply.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("ProductionPipeline")

#: ``ctx.context_extras`` key: True when this cycle's data is synthetic.
SYNTHETIC_FLAG_KEY = "data_is_synthetic"
#: ``ctx.context_extras`` key: why (one of the ``REASON_*`` values).
SYNTHETIC_REASON_KEY = "data_is_synthetic_reason"

#: The cycle's engine is a ``MockDataEngine`` (no FRED key, a failed FRED
#: check in the daemon, or an injected engine).
REASON_MOCK_ENGINE = "mock_engine"
#: The real engine returned no price data and the step fell back to
#: ``MockDataEngine`` (a market-data outage).
REASON_EMPTY_PRICE_FETCH = "empty_price_fetch"

#: ``send_alert`` dedup key, stable for the condition (not the cycle), so a
#: deployment stuck on synthetic data alerts once per dedup window.
SYNTHETIC_ALERT_DEDUP_KEY = "synthetic_data_cycle"

#: Prefix of the run's terminal progress message on a synthetic cycle. Readers
#: (the MCP report tool) match on it.
SYNTHETIC_PROGRESS_MESSAGE_PREFIX = "SYNTHETIC DATA"

_REASON_TEXT = {
    REASON_MOCK_ENGINE: (
        "the cycle's data engine is MockDataEngine (no FRED_API_KEY, or the "
        "FRED configuration check failed when the daemon started)"
    ),
    REASON_EMPTY_PRICE_FETCH: (
        "the price fetch came back empty (market-data outage) and the cycle "
        "fell back to MockDataEngine"
    ),
}

_REMEDY_TEXT = {
    REASON_MOCK_ENGINE: "Set FRED_API_KEY and restart the daemon (the engine is built once at start).",
    REASON_EMPTY_PRICE_FETCH: "Check market-data connectivity (FMP / yfinance) and the daemon log.",
}

#: What a synthetic cycle does not write. Kept in one place so the log line,
#: the alert and the docs say the same thing.
SKIPPED_OUTPUTS = (
    "execution queue, advisory source, watch state, pushes, paper orders, "
    "state snapshot, HTML dashboards, data-freshness marker, forecast ledger, "
    "forecast model cache, macro history, PIT feature snapshot, dead-letter "
    "report, cap-audit or symbol-rating rows"
)


def describe_reason(reason: Optional[str]) -> str:
    """Human-readable cause for a ``REASON_*`` value (never raises)."""
    return _REASON_TEXT.get(str(reason), f"unrecognised reason {reason!r}")


def is_cycle_synthetic(ctx: Any) -> bool:
    """True when ``ctx`` was marked synthetic this cycle. Never raises."""
    try:
        extras = getattr(ctx, "context_extras", None) or {}
        return bool(extras.get(SYNTHETIC_FLAG_KEY))
    except Exception:  # noqa: BLE001 - a reader of a broken ctx must not crash
        return False


def synthetic_reason(ctx: Any) -> Optional[str]:
    """The recorded ``REASON_*`` value, or None when not synthetic."""
    if not is_cycle_synthetic(ctx):
        return None
    try:
        return (getattr(ctx, "context_extras", None) or {}).get(SYNTHETIC_REASON_KEY)
    except Exception:  # noqa: BLE001
        return None


def synthetic_progress_message(reason: Optional[str]) -> str:
    """Terminal progress message for a synthetic cycle (``RunRecord.progress``)."""
    return (
        f"{SYNTHETIC_PROGRESS_MESSAGE_PREFIX}: {describe_reason(reason)}. The cycle "
        f"computed but wrote no {SKIPPED_OUTPUTS}."
    )


def _alert_message(reason: Optional[str]) -> str:
    return (
        f"Pipeline cycle ran on SYNTHETIC data: {describe_reason(reason)}. "
        f"Nothing was written this cycle (no {SKIPPED_OUTPUTS}); the previous "
        f"files stay in place. {_REMEDY_TEXT.get(str(reason), '')}".rstrip()
    )


def send_synthetic_cycle_alert(reason: Optional[str]) -> None:
    """Send the deduplicated CRITICAL alert for a synthetic cycle. Never raises.

    Looks ``send_alert`` up on the module at call time, so a caller that swaps
    ``observability.alerts.send_alert`` (``scripts/step5_4_verify_cycle.py``)
    suppresses it too. Tests never reach the real dispatcher: the root
    ``conftest.py`` stubs this function (opt out with
    ``@pytest.mark.real_synthetic_cycle_alert``).
    """
    try:
        import observability.alerts as alerts_mod

        alerts_mod.send_alert(
            "CRITICAL", _alert_message(reason), dedup_key=SYNTHETIC_ALERT_DEDUP_KEY,
        )
    except Exception as exc:  # noqa: BLE001 - an alert failure never fails the cycle
        logger.warning("Synthetic-data alert could not be sent (non-critical): %s", exc)


def propagate_synthetic_flag(ctx: Any, reason: Optional[str]) -> None:
    """Mark an INNER context synthetic without logging or alerting again.

    ``main_orchestrator.run_pipeline`` builds its own ``RunContext`` for the
    inner steps (``MacroStep`` ... ``StrategyEvalStep``); the outer cycle has
    already called :func:`mark_cycle_synthetic`, so this only copies the flag
    and reason across. Deliberately NOT wrapped, for the same fail-closed
    reason as :func:`mark_cycle_synthetic`.
    """
    extras = ctx.context_extras
    extras[SYNTHETIC_FLAG_KEY] = True
    extras[SYNTHETIC_REASON_KEY] = reason


def mark_cycle_synthetic(ctx: Any, reason: str) -> None:
    """Mark this cycle synthetic: set the flag + reason, log CRITICAL, alert once.

    Idempotent within a cycle: the first reason wins and the log line and alert
    are emitted once. The log line and the alert never raise (CONSTRAINT #6).
    Setting the flag is deliberately NOT wrapped: if ``ctx.context_extras``
    itself is broken, failing loudly beats a cycle that silently stays
    "real" (fail closed); the step would hit the same dict elsewhere anyway.
    """
    extras = ctx.context_extras
    if extras.get(SYNTHETIC_FLAG_KEY):
        return
    extras[SYNTHETIC_FLAG_KEY] = True
    extras[SYNTHETIC_REASON_KEY] = reason
    try:
        logger.critical(
            "SYNTHETIC DATA: %s. This cycle still computes but writes no %s; the "
            "previous files stay in place. %s",
            describe_reason(reason), SKIPPED_OUTPUTS, _REMEDY_TEXT.get(reason, ""),
        )
    except Exception:  # noqa: BLE001
        pass
    send_synthetic_cycle_alert(reason)

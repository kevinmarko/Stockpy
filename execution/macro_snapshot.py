"""Zero-network, DB-cache-only ``MacroEconomicDTO`` loader for the preview-only
queue-builder bridges (``execution/queue_builder.py``,
``execution/options_queue_builder.py`` via ``execution/compose.py``, and
``execution/flatten_proposal.py``).

Why this exists
----------------
Each of those three bridges builds a ``RiskContext`` for
``execution.risk_gate.PreTradeRiskGate`` purely to ANNOTATE a preview
intent's ``gate_allowed``/``gate_reasons`` for a human/agent reviewer — none
of them ever place a real order. Historically all three hardcoded
``RiskContext(macro=None, ...)``, which makes ``macro_kill_switch_check``,
``stress_scenario_check``, and ``hmm_regime_check`` unconditionally PASS
("no macro context — skipping") regardless of real VIX/Sahm/regime state —
not a direct fail-open on capital (every one of these bridges structurally
forbids live placement outside narrow, already-gated conditions), but a
misleading annotation shown to whoever reviews the queue.

``main.py``'s advisory cycle already builds a real ``MacroEconomicDTO`` once
per cycle (``_build_macro_dto()``) and now threads it explicitly into
``compose_and_emit``/``emit_options_execution_queue``. But two callers have
no such value in scope: ``execution/kill_switch.py``'s
``GlobalKillSwitch.activate()`` (which can fire from a CLI invocation, a
watchdog, or an operator action — no pipeline ``RunResult`` anywhere nearby)
and ``pilots/mirror.py``'s follow-composer path. This module gives both a
lightweight fallback: read whatever macro data is already cached in
``HistoricalStore`` (populated by the advisory cycle's own FRED fetches) with
**zero network calls**, matching the zero-network invariant
``flatten_proposal._load_current_positions()`` already has for positions.

Honesty contract (CONSTRAINT #4)
---------------------------------
* If the DB has real cached data (even a day or two stale), build and return
  a DTO from it — never silently substitute the "everything is fine" neutral
  default over real cached data.
* If the DB has NO cached data for any of the four required series (a fresh
  install, or the macro engine has never run), return ``None`` and log a
  WARNING explicitly. This preserves today's fail-open behaviour at the gate
  level (``context.macro is None`` → checks pass, per ``risk_gate.py``'s own
  documented contract) but makes the gap audible instead of silent.
* ``inflation_rate``/``nominal_10y`` are required positional args of
  ``MacroEconomicDTO.__init__`` but are read by NONE of the three risk-gate
  macro checks (``macro_kill_switch_check``, ``stress_scenario_check``,
  ``hmm_regime_check``) — they're seeded with the same neutral placeholder
  values ``api/metrics_api.py::_neutral_macro()`` uses purely to satisfy the
  constructor, not as a claim about real inflation/rate data.
* ``hmm_risk_on_probability``/``hmm_regime_state`` stay ``None`` — computing
  the HMM's second opinion needs ~504 days of SPY bars and a fitted
  ``HMMRegimeDetector``, out of scope for a lightweight cache read.
  ``dto_models.MacroEconomicDTO``'s own docstring already treats ``None``
  here as "the HMM did not run this cycle" — a pre-existing, sanctioned
  degradation, not a new one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from dto_models import MacroEconomicDTO

logger = logging.getLogger(__name__)

# FRED series IDs → MacroEconomicDTO constructor kwargs. Mirrors
# main.py::_build_macro_dto()'s own mapping exactly, so a cached value here
# means the same thing it would have meant coming from a live fetch.
_REQUIRED_SERIES = {
    "VIXCLS": "vix_value",
    "SAHMREALTIME": "sahm_rule_indicator",
    "T10Y2Y": "yield_curve_10y_2y",
    "BAMLH0A0HYM2": "high_yield_oas",
}

# Neutral placeholder seed for the two required-but-unread-by-the-gate
# constructor args (see module docstring). Matches
# api/metrics_api.py::_neutral_macro()'s own values.
_NEUTRAL_INFLATION_RATE = 3.0
_NEUTRAL_NOMINAL_10Y = 4.5


def load_cached_macro_dto(now: Optional[datetime] = None) -> "Optional[MacroEconomicDTO]":
    """Build a ``MacroEconomicDTO`` from whatever macro data is already
    cached in ``HistoricalStore``, with ZERO network calls.

    Returns ``None`` (and logs a WARNING) when any of the four required
    series has no cached observation yet — callers should treat that exactly
    like today's ``RiskContext(macro=None, ...)`` fail-open, now explicit
    instead of silent. Never raises: any failure degrades to ``None``.
    """
    # `now` is reserved for a future as-of cutoff (currently unused) — kept in
    # the signature so callers can pass their own clock without a future
    # signature break.
    try:
        from data.historical_store import HistoricalStore  # lazy — avoid cycles
        from dto_models import MacroEconomicDTO

        store = HistoricalStore(readonly=True)
        values: dict[str, float] = {}
        missing: list[str] = []
        for series_id, kwarg in _REQUIRED_SERIES.items():
            try:
                # Private, cache-only read — deliberately NOT the public
                # get_macro(), which can trigger a live FRED top-up fetch.
                # See module docstring: this path must stay zero-network.
                df = store._read_macro_series(series_id)
            except Exception as exc:
                logger.debug("macro_snapshot: read of %s failed (%s)", series_id, exc)
                df = None
            if df is None or df.empty:
                missing.append(series_id)
                continue
            values[kwarg] = float(df.iloc[-1]["value"])

        if missing:
            logger.warning(
                "macro_snapshot: no cached macro data available (series=%s missing); "
                "RiskContext.macro will be None — macro_kill_switch/hmm_regime/"
                "stress_scenario checks fail-open as documented",
                missing,
            )
            return None

        return MacroEconomicDTO(
            yield_curve_10y_2y=values["yield_curve_10y_2y"],
            high_yield_oas=values["high_yield_oas"],
            inflation_rate=_NEUTRAL_INFLATION_RATE,
            nominal_10y=_NEUTRAL_NOMINAL_10Y,
            vix_value=values["vix_value"],
            sahm_rule_indicator=values["sahm_rule_indicator"],
        )
    except Exception as exc:  # pragma: no cover - belt-and-suspenders dead-letter
        logger.warning("macro_snapshot: load_cached_macro_dto failed (%s); returning None", exc)
        return None

"""Mutable state carrier threaded through the daemon pipeline's steps.

RunContext was written for ``main.py``'s ``run_once()`` (archived to ``legacy/``
in step 5.5), whose steps never imported ``main`` (that would be circular).
Instead ``run_once()`` built one ``RunContext`` per cycle and injected the seven
functions below as plain callables, read from its OWN module scope at call
time, which kept every ``mock.patch("main.<name>", ...)`` test seam working.

The live pipeline (``main_orchestrator.py``) builds one ``RunContext`` per cycle
too, but fills those seven fields with no-op stubs: no live step reads them
(``pipeline/production_steps.py`` uses ``ctx.symbols``, ``ctx.snapshot`` and
the other mutable fields below), so they are vestigial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from data.robinhood_portfolio import AccountSnapshot
    from data.market_data import MarketDataProvider
    from dto_models import MacroEconomicDTO
    from engine.advisory import Recommendation
    from reporting.progress import ProgressReporter


@dataclass
class RunContext:
    """Per-cycle mutable state for the daemon pipeline."""

    # ── Immutable run parameters ────────────────────────────────────────────
    force_account: bool
    started_at: datetime
    watchlist_file: str

    # ── Injected dependencies ────────────────────────────────────────────────
    # Bound from main.py's own module globals inside run_once() at call time
    # (see module docstring); main_orchestrator.py now passes no-op stubs.
    fetch_account_snapshot_fn: Callable[..., "AccountSnapshot"]
    build_universe_fn: Callable[["AccountSnapshot"], List[str]]
    build_macro_dto_fn: Callable[[], "MacroEconomicDTO"]
    get_provider_fn: Callable[[], "MarketDataProvider"]
    fetch_bars_fn: Callable[[List[str], "MarketDataProvider"], Dict[str, Any]]
    build_context_extras_fn: Callable[[List[str], Dict[str, Any], "MacroEconomicDTO", "MarketDataProvider"], Dict[str, Any]]
    advisory_evaluate_fn: Callable[..., "Recommendation"]

    # ── Mutable pipeline state, filled in by steps in order ─────────────────
    snapshot: Optional["AccountSnapshot"] = None
    symbols: List[str] = field(default_factory=list)
    macro_dto: Optional["MacroEconomicDTO"] = None
    market: Optional["MarketDataProvider"] = None
    bars_dict: Dict[str, Any] = field(default_factory=dict)
    context_extras: Dict[str, Any] = field(default_factory=dict)
    recommendations: List["Recommendation"] = field(default_factory=list)
    errors: List[dict] = field(default_factory=list)

    # ── Production-specific state (main_orchestrator.py) ─────────────────────
    engine_context: Optional[Any] = None
    tech_raw: Dict[str, Any] = field(default_factory=dict)
    fund_raw: Dict[str, Any] = field(default_factory=dict)
    macro_raw: Dict[str, Any] = field(default_factory=dict)
    dashboard_df: Optional[Any] = None
    dashboard_schema_validated: bool = False

    # ── Early-exit signaling ─────────────────────────────────────────────────
    # A step sets stopped=True (with an optional stop_reason) to short-circuit
    # every remaining step. PipelineRunner checks this via each step's
    # should_skip(ctx) before invoking it (see pipeline/base.py).
    stopped: bool = False
    stop_reason: Optional[str] = None

    # ── Progress instrumentation (reporting/progress.py) ─────────────────────
    # Set by PipelineRunner.run() from its own `progress` argument (never
    # constructed here) so the per-symbol steps in production_steps.py can call
    # ctx.progress.advance_symbol() without PipelineStep.run()
    # needing a second parameter. None (the default) is a complete no-op;
    # every read of this field elsewhere is guarded by `if ctx.progress is
    # not None`.
    progress: Optional["ProgressReporter"] = None

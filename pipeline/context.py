"""Mutable state carrier threaded through main.py's run_once() pipeline steps.

RunContext deliberately holds NO reference to ``main.py`` at runtime — the
pipeline steps in ``pipeline/steps.py`` never import ``main`` (that would be
circular, since ``main.run_once()`` imports the pipeline package). Instead,
``main.run_once()`` builds one ``RunContext`` per cycle and injects the seven
functions below as plain callables, read from its OWN module scope at call
time. This preserves every existing ``mock.patch("main.<name>", ...)`` test
seam exactly: those patches replace the attribute on the ``main`` module
object, and ``run_once()`` still resolves the bare name via ``main``'s module
globals when it builds the RunContext — the same resolution that already
made the pre-refactor mocks work.
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
    """Per-cycle mutable state for the run_once() pipeline."""

    # ── Immutable run parameters ────────────────────────────────────────────
    force_account: bool
    started_at: datetime
    watchlist_file: str

    # ── Injected dependencies ────────────────────────────────────────────────
    # Bound from main.py's own module globals inside run_once() at call time
    # (see module docstring). Never imported directly by pipeline/steps.py.
    fetch_account_snapshot_fn: Callable[..., "AccountSnapshot"]
    build_universe_fn: Callable[["AccountSnapshot"], List[str]]
    build_macro_dto_fn: Callable[[], "MacroEconomicDTO"]
    get_provider_fn: Callable[[], "MarketDataProvider"]
    fetch_bars_fn: Callable[[List[str], "MarketDataProvider"], Dict[str, Any]]
    build_context_extras_fn: Callable[[List[str], Dict[str, Any], "MacroEconomicDTO"], Dict[str, Any]]
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

    # ── Early-exit signaling ─────────────────────────────────────────────────
    # A step sets stopped=True (with an optional stop_reason) to short-circuit
    # every remaining step. PipelineRunner checks this via each step's
    # should_skip(ctx) before invoking it (see pipeline/base.py).
    stopped: bool = False
    stop_reason: Optional[str] = None

    # ── Progress instrumentation (reporting/progress.py) ─────────────────────
    # Set by PipelineRunner.run() from its own `progress` argument (never
    # constructed here) so AdvisoryEvalStep -- the only step with a per-symbol
    # loop -- can call ctx.progress.advance_symbol() without PipelineStep.run()
    # needing a second parameter. None (the default) is a complete no-op;
    # every read of this field elsewhere is guarded by `if ctx.progress is
    # not None`.
    progress: Optional["ProgressReporter"] = None

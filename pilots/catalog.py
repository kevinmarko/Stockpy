"""Static catalog of Stockpy "Pilots".

A **Pilot** packages one of Stockpy's own signal-module weight blends as a
copyable strategy, joined (where an honest backtest exists) to a validated,
PBO/DSR-gated strategy in ``scripts.refresh_validations.STRATEGY_REGISTRY``.

Design constraints (mirrors the wider codebase conventions):

* **Dependency-light** — imports only ``settings`` + stdlib/``dataclasses``/
  ``typing``. NEVER imports the heavy engines, so it is safe to import on the
  API read path.
* **No invented names** (Decision D1) — every key of ``Pilot.weights`` MUST be a
  real key of ``settings.SIGNAL_WEIGHTS`` (identical to the live signal modules'
  ``name`` attributes), and every non-``None`` ``validation_strategy_id`` MUST be
  a real key of ``STRATEGY_REGISTRY``. A Pilot with no honest backtest match sets
  ``validation_strategy_id=None`` rather than advertising another strategy's
  Sharpe.

D1 name-mismatch note
---------------------
Three separate namespaces exist and do NOT line up 1:1:

* ``settings.SIGNAL_WEIGHTS`` keys — the live signal-module ids
  (``macd_momentum``, ``aroon_trend``, ``multifactor`` …).
* ``STRATEGY_REGISTRY`` keys — the validated backtest ids
  (``macd_trend``, ``multifactor_lowvol_size``, ``coppock_momentum`` …).
* Human-facing Pilot slugs (``trend-following``, ``dip-buyer`` …).

The ``Pilot`` record is the explicit, reviewed join between them. Notable
honest caveats baked into the catalog below:

* ``multifactor`` Pilot uses the full 4-factor ``multifactor`` signal but joins
  the ``multifactor_lowvol_size`` backtest, which validates only the Low-Vol +
  Size factors (Value/Quality have no free point-in-time fundamentals). The
  headline Sharpe therefore reflects the honest, narrower proxy.
* ``cross-sectional-momentum``, the income/value single-factor Pilots, and both
  curated blends have **no** matching validated backtest, so
  ``validation_strategy_id=None`` (the UI shows "no backtest series yet").
* ``coppock_momentum`` exists in ``STRATEGY_REGISTRY`` but has no corresponding
  signal module in ``SIGNAL_WEIGHTS``, so it is deliberately NOT surfaced as a
  Pilot (a Pilot's weights must be real signal-module ids).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from settings import settings

__all__ = ["Pilot", "PILOTS", "list_pilots", "get_pilot"]


@dataclass(frozen=True)
class Pilot:
    """A copyable strategy defined as a weight blend over Stockpy signal modules.

    Attributes
    ----------
    id:
        Stable kebab-case slug used in URLs / joins (e.g. ``"trend-following"``).
    name:
        Human-friendly display name (e.g. ``"Trend Follower"``).
    category:
        One of ``"Momentum" | "Mean Reversion" | "Factor" | "Blend"`` — a
        rendering hint for marketplace grouping.
    description:
        Retail-friendly 1-2 sentence explainer.
    weights:
        Mapping of signal-module id -> weight. Every key MUST be a real key of
        ``settings.SIGNAL_WEIGHTS``. The values re-blend the already-persisted
        per-module raw scores; only their relative magnitude matters.
    long_only:
        Rendering / semantics hint — ``True`` for strategies that never short
        (e.g. the RSI(2) dip-buyer).
    validation_strategy_id:
        Join key into ``scripts.refresh_validations.STRATEGY_REGISTRY`` for the
        Pilot's honest, PBO/DSR-gated backtest, or ``None`` when no honest match
        exists.
    """

    id: str
    name: str
    category: str
    description: str
    weights: Dict[str, float] = field(default_factory=dict)
    long_only: bool = False
    validation_strategy_id: Optional[str] = None


def _full_blend_weights() -> Dict[str, float]:
    """Snapshot of the platform's full default signal blend.

    Copied from ``settings.SIGNAL_WEIGHTS`` at import time so the ``balanced-blend``
    Pilot always tracks the live default weight vector (including any operator
    override present at process start) without re-typing it.
    """
    return dict(settings.SIGNAL_WEIGHTS)


# ---------------------------------------------------------------------------
# The static catalog.
#
# Every ``weights`` key below is a verified real ``settings.SIGNAL_WEIGHTS`` key
# (== the live signal module's ``name``); every non-None ``validation_strategy_id``
# is a verified real ``STRATEGY_REGISTRY`` key. Enforced by
# ``tests/test_pilots_catalog.py``.
# ---------------------------------------------------------------------------
PILOTS: List[Pilot] = [
    Pilot(
        id="trend-following",
        name="Trend Follower",
        category="Momentum",
        description=(
            "Rides sustained multi-month price trends using time-series momentum. "
            "Leans into names that keep going up and steps aside when the trend fades."
        ),
        weights={"timeseries_momentum": 1.0},
        long_only=False,
        validation_strategy_id="timeseries_momentum",
    ),
    Pilot(
        id="cross-sectional-momentum",
        name="Momentum Leaders",
        category="Momentum",
        description=(
            "Ranks the universe by 12-month-minus-1-month return and favors the "
            "relative winners over the laggards — a classic cross-sectional momentum tilt."
        ),
        weights={"cross_sectional_momentum": 1.0},
        long_only=False,
        # No cross-sectional-momentum backtest in STRATEGY_REGISTRY (the two
        # momentum entries there are time-series / Coppock, not this factor).
        validation_strategy_id=None,
    ),
    Pilot(
        id="dip-buyer",
        name="Dip Buyer",
        category="Mean Reversion",
        description=(
            "Buys short-term oversold pullbacks in stocks still in a long-term uptrend "
            "using a Connors RSI(2) rule. Long-only, and it stands down in turbulent regimes."
        ),
        weights={"rsi2_mean_reversion": 1.0},
        long_only=True,
        validation_strategy_id="rsi2_mean_reversion",
    ),
    Pilot(
        id="macd-trend",
        name="MACD Trend",
        category="Momentum",
        description=(
            "Combines MACD momentum with an Aroon trend filter to catch cleaner "
            "directional moves and sidestep choppy, range-bound tape."
        ),
        weights={"macd_momentum": 1.0, "aroon_trend": 1.0},
        long_only=False,
        validation_strategy_id="macd_trend",
    ),
    Pilot(
        id="multifactor",
        name="Multifactor",
        category="Factor",
        description=(
            "A Fama-French-style blend of Value, Quality, Low-Volatility and Size. "
            "Note: the backtest validates the Low-Vol + Size sleeve only, since free "
            "point-in-time Value/Quality fundamentals don't exist."
        ),
        weights={"multifactor": 1.0},
        long_only=False,
        # The full 4-factor signal; honest backtest covers the Low-Vol + Size subset.
        validation_strategy_id="multifactor_lowvol_size",
    ),
    Pilot(
        id="dividend-income",
        name="Dividend Income",
        category="Factor",
        description=(
            "Tilts toward durable dividend payers with healthy, well-covered yields — "
            "an income-oriented quality screen rather than a trading signal."
        ),
        weights={"dividend_quality": 1.0},
        long_only=True,
        # No income/dividend backtest exists in STRATEGY_REGISTRY.
        validation_strategy_id=None,
    ),
    Pilot(
        id="deep-value",
        name="Deep Value",
        category="Factor",
        description=(
            "Screens for stocks trading cheap versus their Graham intrinsic value — "
            "a bargain-hunter's margin-of-safety approach."
        ),
        weights={"graham_value": 1.0},
        long_only=True,
        # No standalone value backtest exists in STRATEGY_REGISTRY.
        validation_strategy_id=None,
    ),
    Pilot(
        id="value-quality",
        name="Value & Quality",
        category="Blend",
        description=(
            "Pairs cheap Graham-value names with durable dividend quality and a "
            "multifactor tilt — a conservative, fundamentals-first blend."
        ),
        weights={
            "graham_value": 1.0,
            "dividend_quality": 1.0,
            "multifactor": 1.0,
        },
        long_only=True,
        validation_strategy_id=None,
    ),
    Pilot(
        id="balanced-blend",
        name="Balanced Blend",
        category="Blend",
        description=(
            "Stockpy's full house blend — every signal module at its default weight, "
            "diversified across momentum, mean-reversion, value, quality and macro."
        ),
        weights=_full_blend_weights(),
        long_only=False,
        # An ensemble of all modules; no single backtest honestly represents it.
        validation_strategy_id=None,
    ),
]


# Fast id -> Pilot index (built once at import; catalog is static).
_BY_ID: Dict[str, Pilot] = {p.id: p for p in PILOTS}


def list_pilots() -> List[Pilot]:
    """Return all Pilots in catalog (marketplace) order."""
    return list(PILOTS)


def get_pilot(pilot_id: str) -> Optional[Pilot]:
    """Return the Pilot with ``pilot_id``, or ``None`` if unknown."""
    return _BY_ID.get(pilot_id)

"""
gui/robinhood_mode.py
=====================
Persistent Robinhood execution-mode banner helpers for :mod:`gui.app`.

The Robinhood execution queue (Tier 8, ``execution/queue_builder.py``) is
strictly staged ``off → review → live``.  In every non-``off`` posture
there IS a real proposed-order queue on disk that a Claude Code agent
can act on — the operator must not be able to miss which posture is
active.  This module derives the mode state from
``settings.ROBINHOOD_EXECUTION_MODE`` + ``ROBINHOOD_MAX_NOTIONAL_PER_ORDER``
so the banner is a pure function of those two fields — no new state.

Independent of :mod:`gui.run_mode`: Robinhood's execution mode is a
different flag from the Alpaca ``DRY_RUN``/``ALPACA_PAPER`` posture
(CLAUDE.md Tier 8, "Relationship to ADVISORY_ONLY"), so we render its
own banner rather than folding it into the existing run-mode header.

Public API
----------
``RobinhoodModeState`` — frozen dataclass consumed by the banner.
``read_robinhood_execution_mode`` — pure function; the sole entry point.
``BannerVariant`` — Literal["hidden", "info", "warning", "error"].

CONSTRAINT #5 (on-demand)
--------------------------
No scheduler; state is re-derived on every Streamlit render pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

BannerVariant = Literal["hidden", "info", "warning", "error"]

# Canonical modes accepted by ``settings.ROBINHOOD_EXECUTION_MODE`` (mirrors
# ``execution.queue_builder.VALID_MODES``).  Anything else is coerced to
# ``"off"`` by the settings validator — we still treat unknown values as
# hidden here for defence-in-depth.
_KNOWN_MODES = frozenset({"off", "review", "live"})


@dataclass(frozen=True)
class RobinhoodModeState:
    """Snapshot of the active Robinhood execution posture.

    Attributes
    ----------
    mode :
        Canonical lowercase string: ``"off"`` / ``"review"`` / ``"live"``
        (unknown values fall back to ``"off"``).
    variant :
        Which Streamlit banner shape the caller should render.  ``"hidden"``
        for ``off`` — the caller renders nothing.  ``"warning"`` (amber) for
        ``review``.  ``"error"`` (red) for ``live``.
    icon :
        Emoji prefix for the banner text.  Empty string when ``variant`` is
        ``"hidden"`` so a stray call still yields no visible artifact.
    label :
        Full operator-readable banner string.  Empty string when
        ``variant`` is ``"hidden"``.
    notional_cap :
        Live per-order USD cap from ``ROBINHOOD_MAX_NOTIONAL_PER_ORDER``
        (never negative — coerced to ``0.0`` on parse failure).
    notional_cap_set :
        ``True`` when ``notional_cap > 0`` (i.e. actually configured).
        The ``live`` posture is documented as requiring a positive cap; when
        this is ``False`` in ``live`` mode the banner surfaces an extra
        "cap unset — no orders will be placeable" note.
    """

    mode: str
    variant: BannerVariant
    icon: str
    label: str
    notional_cap: float
    notional_cap_set: bool


def _coerce_mode(raw: Any) -> str:
    if not isinstance(raw, str):
        return "off"
    lowered = raw.strip().lower()
    return lowered if lowered in _KNOWN_MODES else "off"


def _coerce_cap(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value < 0 or value != value:  # NaN guard (CONSTRAINT #4)
        return 0.0
    return value


def read_robinhood_execution_mode(settings_obj: Optional[Any] = None) -> RobinhoodModeState:
    """Derive the current :class:`RobinhoodModeState` — pure function.

    Parameters
    ----------
    settings_obj :
        Optional pre-loaded settings-like object.  Injectable for tests.
        When ``None``, imports ``settings.settings`` lazily.

    Returns
    -------
    RobinhoodModeState
        Never raises — settings-lookup failure degrades to
        ``mode="off"`` / ``variant="hidden"`` (CONSTRAINT #6).
    """
    try:
        if settings_obj is None:
            from settings import settings as _s  # noqa: PLC0415
            settings_obj = _s
        mode = _coerce_mode(getattr(settings_obj, "ROBINHOOD_EXECUTION_MODE", "off"))
        cap = _coerce_cap(getattr(settings_obj, "ROBINHOOD_MAX_NOTIONAL_PER_ORDER", 0.0))
    except Exception as exc:
        logger.debug("read_robinhood_execution_mode fell through: %s", exc)
        mode = "off"
        cap = 0.0

    cap_set = cap > 0.0

    if mode == "off":
        return RobinhoodModeState(
            mode="off",
            variant="hidden",
            icon="",
            label="",
            notional_cap=cap,
            notional_cap_set=cap_set,
        )

    if mode == "review":
        return RobinhoodModeState(
            mode="review",
            variant="warning",
            icon="🟡",
            label=(
                "🟡 **Robinhood execution: REVIEW (paper/dry-run)** — a proposed-order "
                "queue is being written to `output/execution_queue.json`, but every "
                "intent is marked `allow_place=False`. The execution agent will "
                "only `review_equity_order` — no real orders will be placed."
            ),
            notional_cap=cap,
            notional_cap_set=cap_set,
        )

    # mode == "live"
    if cap_set:
        cap_note = f"per-order cap ${cap:,.2f}"
    else:
        cap_note = (
            "**per-order cap UNSET** — set `ROBINHOOD_MAX_NOTIONAL_PER_ORDER` in `.env` "
            "before any placement will succeed"
        )
    return RobinhoodModeState(
        mode="live",
        variant="error",
        icon="🔴",
        label=(
            "🔴 **Robinhood execution: LIVE** — the proposed-order queue at "
            "`output/execution_queue.json` may include `allow_place=true` intents "
            f"that the execution agent can place against a real Robinhood account ({cap_note}). "
            "Every placement still requires explicit per-trade human confirmation."
        ),
        notional_cap=cap,
        notional_cap_set=cap_set,
    )

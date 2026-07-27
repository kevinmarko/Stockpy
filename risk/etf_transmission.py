"""
InvestYo Quant Platform - ETF Volatility Transmission Overlay
==============================================================
Ben-David, Franzoni & Moussawi (2018), "Do ETFs Increase Volatility?",
*Journal of Finance* 73(6): ETF arbitrage propagates a demand/liquidity shock
hitting ONE constituent into its otherwise-healthy peers in the same basket.
A heavily ETF-wrapped name therefore carries extra variance that is
(a) non-fundamental -- nothing changed about the business -- and
(b) non-diversifiable within an equity book, because it arrives through the
same basket every other constituent sits in.

Neither of the platform's per-name sizing formulas can see this. Both
``sizing/kelly.py`` and ``sizing/vol_target.py`` observe only a single
symbol's own return series; the transmitted component is already baked into
that series indistinguishably from fundamental volatility, and the
cross-sectional structure that makes it non-diversifiable is invisible to a
univariate estimator. Hence a separate, explicit overlay.

Why a post-multiplier and NOT vol-inflation into Kelly
------------------------------------------------------
Inflating the ``realized_vol`` input to ``_calculate_kelly_sizing`` looks
like the natural lever and is a BROKEN one.
``sizing.kelly.kelly_sizing_for_strategy`` reads ``realized_vol`` **only**
in its ``< MIN_TRADES_REQUIRED`` cold-start branch; once a strategy has
>= 30 closed trades the weight comes from a 1,000-resample bootstrap of
realized trade returns and the vol input is never read at all. A risk
control that fires on a cold-start book and then silently disappears the
moment the book matures is the worst possible failure profile for a risk
control -- it is present exactly when it matters least. This overlay
therefore post-multiplies the already-composed weight
(``sizing/position_sizer.py::size_position``, step 3), where it applies
identically on every sizing path.

Design constraints
------------------
* **Explicit, monotone, bounded, two knobs.** No fitted parameters, no
  regression, no regime switching -- an operator must be able to predict
  what this does to a position from the two inputs and the two settings.
* **Exactly 1.0 on any missing input.** See ``transmission_multiplier``'s
  own docstring: a NaN multiplier would poison ``final_weight``, and a
  non-finite ``final_weight`` is EXCLUDED from ``apply_portfolio_gross_cap``'s
  gross sum -- so a coverage gap would shrink the gross denominator and
  silently LOOSEN the portfolio-wide risk ceiling for every other name. A
  data outage must never relax a risk limit.
* **Pure / no IO**, matching ``sizing/position_sizer.py``'s convention.
"""

from __future__ import annotations

import math
from typing import Optional

__all__ = ["transmission_multiplier"]


def _finite_float(value: Optional[float]) -> Optional[float]:
    """``float(value)`` when it is a real finite number, else ``None``.

    Accepts anything (numpy scalars, ``None``, strings out of a CSV, a
    ``pd.NA``) and never raises -- the callers of this module read from a
    dashboard DataFrame whose cells are honestly NaN when a measurement
    wasn't available (CONSTRAINT #4).
    """
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(as_float):
        return None
    return as_float


def _clip01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def transmission_multiplier(
    ownership_pct: float,
    comovement_r2: float,
    *,
    max_derate: float,
    ownership_reference: float,
    floor: float,
) -> float:
    """Bounded, monotone position-sizing derate for ETF volatility transmission.

    .. code-block:: text

        m = 1 - max_derate * clip(ownership_pct / ownership_reference, 0, 1)
                           * clip(comovement_r2, 0, 1)
        m = max(m, floor)

    Both factors are dimensionless fractions in ``[0, 1]``, so ``m`` is
    monotonically NON-INCREASING in each argument and always lands in
    ``[floor, 1.0]``. Two intuitions are encoded, and only two:

    * **How much of the name is wrapped** -- ``ownership_pct`` is the
      fraction of shares outstanding held by ETFs. Scaled against
      ``ownership_reference`` (``settings.ETF_TRANSMISSION_OWNERSHIP_REFERENCE``,
      the ownership level at which the derate is considered fully "earned")
      and clipped, so a name past the reference cannot keep escalating.
    * **How much the name actually moves with its wrapper** -- ``comovement_r2``
      is the R-squared of the constituent's returns on its ETF's returns.
      Heavy ETF ownership that does NOT translate into co-movement transmits
      nothing, and correctly derates nothing.

    Parameters
    ----------
    ownership_pct : float
        Fraction (NOT percent) of shares outstanding held by ETFs, e.g.
        ``0.14`` for 14%. NaN / None when unmeasured.
    comovement_r2 : float
        R-squared of the constituent-on-ETF return regression, in ``[0, 1]``.
        NaN / None when unmeasured.
    max_derate : float
        ``settings.ETF_TRANSMISSION_MAX_DERATE`` -- the largest fraction of
        the weight this overlay may ever remove (clipped into ``[0, 1]``).
    ownership_reference : float
        ``settings.ETF_TRANSMISSION_OWNERSHIP_REFERENCE`` -- the ETF-ownership
        fraction that saturates the ownership factor at 1.0. Must be > 0.
    floor : float
        ``settings.ETF_TRANSMISSION_MIN_MULTIPLIER`` -- a hard lower bound on
        the returned multiplier (clipped into ``[0, 1]``), so no combination
        of inputs or knob settings can zero a position out through this path.

    Returns
    -------
    float
        The multiplier in ``[floor, 1.0]``. **Exactly ``1.0`` -- never NaN --
        whenever any input is missing/NaN/None or the knobs are unusable
        (non-finite, or ``ownership_reference <= 0``).**

    Why missing input returns 1.0 and not NaN
    -----------------------------------------
    A NaN here would flow into ``size_position``'s step-3 composition, and
    ``sizing.position_sizer.clamp_with_binding`` deliberately passes NaN
    straight through (rather than fabricating a capped 0.0), so
    ``final_weight`` would become NaN. ``apply_portfolio_gross_cap`` then
    EXCLUDES non-finite weights from the gross-exposure sum -- correct in
    isolation, catastrophic here: an ETF-coverage gap on 30 of 40 names
    would shrink the gross denominator and silently LOOSEN the portfolio
    cap for the surviving 10. A data outage must never relax a risk limit.

    This is NOT a CONSTRAINT #4 violation. The measured COLUMNS
    (``ETF_Ownership_Pct`` / ``ETF_Comovement_R2``) stay honestly NaN when
    unmeasured -- that is a claim about the world. The MULTIPLIER is not a
    measurement at all: it is the amount of derating to apply, and "apply no
    derating" is exactly ``1.0``. Different question, different answer.
    """
    ownership = _finite_float(ownership_pct)
    comovement = _finite_float(comovement_r2)
    if ownership is None or comovement is None:
        return 1.0

    reference = _finite_float(ownership_reference)
    derate = _finite_float(max_derate)
    lower_bound = _finite_float(floor)
    if reference is None or reference <= 0.0 or derate is None or lower_bound is None:
        # Unusable knobs -- degrade to the no-op rather than guessing a
        # derate the operator never configured.
        return 1.0

    derate = _clip01(derate)
    lower_bound = _clip01(lower_bound)

    ownership_factor = _clip01(ownership / reference)
    comovement_factor = _clip01(comovement)

    multiplier = 1.0 - derate * ownership_factor * comovement_factor
    return max(multiplier, lower_bound)

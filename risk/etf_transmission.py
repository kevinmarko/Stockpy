"""ETF volatility-transmission measurement (Ben-David, Franzoni & Moussawi 2018).

*"Do ETFs Increase Volatility?"*, **Journal of Finance 73(6), 2471-2535**.

The mechanism: authorized participants close the ETF-vs-index price gap by
creating/redeeming whole baskets. A shock that hits ONE constituent therefore
propagates into the ETF price, and the arbitrage trade pushes that same shock
back out into every OTHER constituent of the basket -- including names with no
fundamental exposure to the original event. A heavily ETF-wrapped stock thus
carries extra **non-fundamental, non-diversifiable** variance, and its
volatility becomes tethered to that of its basket peers.

This module is the **pure-math measurement layer** for that effect. It performs
ZERO I/O: holdings dicts and OHLCV DataFrames are handed in by the caller
(``pipeline/production_steps.py::_apply_etf_transmission``), which owns every
network call and every settings gate. Keeping it I/O-free is what lets it be
unit-tested without ``main_orchestrator``'s heavy import chain, and what lets
the honesty contract below be verified in isolation.

Why market-residualized R², not naive R²
----------------------------------------
A naive R² of a stock's returns on its sector-ETF composite is high for
*every* large-cap regardless of how ETF-wrapped it is, because both legs load
on the same market/industry factor. Shipping that number would be a
market-beta derate wearing an ETF costume. Both legs are therefore
residualized against the market first::

    beta_i = Cov(r_i, r_mkt) / Var(r_mkt)      u_t = r_i,t - beta_i * r_mkt,t
    beta_E = Cov(r_E, r_mkt) / Var(r_mkt)      e_t = r_E,t - beta_E * r_mkt,t
    ETF_Comovement_R2 = corr(u, e)**2

``r_E`` is the ownership-weighted composite over **non-market wrappers only**
-- the market proxy (``settings.ETF_HOLDINGS_MARKET_PROXY``, default ``SPY``)
is excluded from the composite and used solely as the market leg. A deliberate,
load-bearing consequence: if a name's only covered wrapper IS the market proxy,
then ``e_t == 0`` identically and the partial R² is **NaN**, not a fabricated
number. The identification limit surfaces as missing data (CONSTRAINT #4).

What is deliberately NOT implemented
------------------------------------
The literal arbitrage-gap regression from the paper (mispricing
``delta_t = p_t - iota_t`` between the ETF price and its intraday indicative
value, with the constituent's next-day return regressed on lagged ``delta_t``)
is the most direct statement of the mechanism, but reconstructing the synthetic
basket requires price history for the FULL constituent set (SPY = ~500 names)
while this pipeline's ``tech_raw`` only carries the operator universe (~20-60
names), and free daily NAV/IIV history is unavailable. A low-coverage basket
proxy would be fabricated data, so it is left as a Phase-2 follow-up gated on a
real NAV source. See ``docs/signals/etf_transmission.md``.

Causality
---------
``compute_market_residual_r2`` follows ``processing_engine.calculate_rolling_beta``
exactly: contemporaneous ``.rolling(window)`` statistics over an
``join="inner"`` alignment, **never** forward-filled. The value at date *t*
consumes only rows in ``[t-window+1, t]``, so it is lookahead-free by
construction -- pinned by the perturbation test in
``tests/test_etf_transmission_lookahead.py``.
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from data.etf_holdings import ETFHolding

logger = logging.getLogger(__name__)

# Below this, a residual series is treated as identically zero (a degenerate
# composite -- e.g. the SPY-only case where e_t == 0 by construction) and the
# partial R² is reported as NaN rather than as a meaningless 0/0 correlation.
_DEGENERATE_STD = 1e-12


def _finite(value: Any) -> bool:
    """True only for a real, finite float (rejects None/NaN/inf/non-numeric)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(f))


def _as_float(value: Any) -> float:
    """Coerce to float, or ``NaN`` when the value is missing/non-numeric.

    Deliberately NOT written as ``float(value or default)`` -- that idiom
    collapses a genuine ``0.0`` into the default, which is exactly the class of
    silent fabrication CONSTRAINT #4 forbids.
    """
    return float(value) if _finite(value) else float("nan")


def filter_holdings_as_of(
    holdings_by_etf: Mapping[str, Sequence["ETFHolding"]],
    as_of: Optional[date] = None,
) -> dict[str, list[Any]]:
    """Drop future-dated holdings rows, then keep the latest row per constituent.

    Two separate jobs, both required before any math touches a holdings list:

    1. **Causality (belt-and-suspenders).** ``data/etf_holdings.py``'s provider
       is expected to honor its own ``as_of`` argument, but a holdings row
       stamped AFTER the cycle's as-of date must never influence a measurement
       for that date regardless of what the provider returned. When ``as_of``
       is ``None`` no date filtering is applied (the caller is asserting it has
       no as-of constraint), only the dedupe below.
    2. **Dedupe.** A basket file can legitimately carry more than one row for
       the same constituent (multiple share classes, or successive as-of
       snapshots concatenated). Summing them would double-count ownership, so
       only the row with the greatest ``as_of_date`` per
       ``(etf_symbol, holding_symbol)`` survives.

    Rows missing a usable ``holding_symbol`` are dropped. Never raises: a
    malformed row is skipped, not propagated (CONSTRAINT #6).
    """
    out: dict[str, list[Any]] = {}
    for etf_symbol, rows in (holdings_by_etf or {}).items():
        best: dict[str, Any] = {}
        for row in rows or []:
            try:
                symbol = str(getattr(row, "holding_symbol", "") or "").upper().strip()
                if not symbol:
                    continue
                row_date = getattr(row, "as_of_date", None)
                if as_of is not None and isinstance(row_date, date) and row_date > as_of:
                    continue
                prior = best.get(symbol)
                if prior is None:
                    best[symbol] = row
                    continue
                prior_date = getattr(prior, "as_of_date", None)
                if (
                    isinstance(row_date, date)
                    and isinstance(prior_date, date)
                    and row_date > prior_date
                ):
                    best[symbol] = row
            except Exception:  # pragma: no cover - defensive per-row skip
                continue
        out[str(etf_symbol).upper().strip()] = list(best.values())
    return out


def compute_etf_ownership(
    holdings_by_etf: Mapping[str, Sequence["ETFHolding"]],
    shares_outstanding: Mapping[str, float],
    *,
    exclude_symbols: frozenset[str] = frozenset(),
) -> dict[str, float]:
    """Fraction of each constituent's shares outstanding held by covered ETFs.

    ``ownership_i = sum_E shares_held(E, i) / shares_outstanding(i)``, summed
    over every ETF in ``holdings_by_etf`` (the market proxy INCLUDED -- being
    wrapped by the largest basket in the market is precisely the exposure this
    column measures; only the *return composite* excludes it).

    Args:
        holdings_by_etf: ``{etf_symbol: [ETFHolding, ...]}``, the shape
            returned by ``data.etf_holdings.get_etf_holdings``. Consumed
            duck-typed (``holding_symbol`` / ``shares_held`` / ``as_of_date``)
            so this module never imports the provider.
        shares_outstanding: ``{symbol: shares}``. A symbol whose value is
            missing, non-finite, or ``<= 0`` yields ``NaN``.
        exclude_symbols: constituents to omit entirely from the result --
            used by the caller to drop symbols that are THEMSELVES ETFs
            (a wrapper's ownership/co-movement against itself is 1.0/1.0,
            i.e. a maximum derate for a trivially wrong reason).

    Returns:
        ``{symbol: fraction}`` covering only symbols that appear in at least
        one basket. Value is ``NaN`` -- never ``0.0`` -- whenever ownership is
        unknowable: shares outstanding missing/non-positive, or ANY covered
        basket reporting a non-finite ``shares_held`` for that symbol (a
        partially-reported basket set would systematically UNDERSTATE
        ownership, which is an active false claim, not a gap; CONSTRAINT #4).
        Symbols absent from every basket are absent from the dict, so the
        caller's ``.map()`` leaves them ``NaN`` too.

    Never raises (CONSTRAINT #6).
    """
    excluded = {str(s).upper().strip() for s in (exclude_symbols or frozenset())}
    shares_out = {
        str(k).upper().strip(): v for k, v in (shares_outstanding or {}).items()
    }

    totals: dict[str, float] = {}
    unusable: set[str] = set()
    try:
        deduped = filter_holdings_as_of(holdings_by_etf, as_of=None)
        for _etf_symbol, rows in deduped.items():
            for row in rows:
                symbol = str(getattr(row, "holding_symbol", "") or "").upper().strip()
                if not symbol or symbol in excluded:
                    continue
                held = getattr(row, "shares_held", float("nan"))
                if not _finite(held) or float(held) < 0.0:
                    # Unknown contribution -> the SUM is unknowable, not smaller.
                    unusable.add(symbol)
                    totals.setdefault(symbol, float("nan"))
                    continue
                totals[symbol] = totals.get(symbol, 0.0) + float(held)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("compute_etf_ownership failed (non-fatal): %s", exc)
        return {}

    result: dict[str, float] = {}
    for symbol, held_total in totals.items():
        outstanding = shares_out.get(symbol)
        if symbol in unusable or not _finite(outstanding) or float(outstanding) <= 0.0:
            result[symbol] = float("nan")
            continue
        result[symbol] = float(held_total) / float(outstanding)
    return result


def build_etf_return_composite(
    holdings_by_etf: Mapping[str, Sequence["ETFHolding"]],
    etf_bars: Mapping[str, pd.DataFrame],
    *,
    market_proxy: str = "SPY",
) -> dict[str, pd.Series]:
    """Per-constituent, ownership-weighted daily return series of its wrappers.

    For each constituent *i*, the composite is the weighted mean of the daily
    returns of every NON-market ETF that holds it::

        r_E,t = sum_E ( w_E,i * r_E,t ) / sum_E w_E,i

    Args:
        holdings_by_etf: ``{etf_symbol: [ETFHolding, ...]}`` (duck-typed).
        etf_bars: ``{etf_symbol: OHLCV DataFrame}`` with a ``Close`` column and
            a ``DatetimeIndex`` -- the same shape ``DataEngine.fetch_technical_raw_cached``
            returns. An ETF with no bars simply does not contribute.
        market_proxy: excluded from the composite entirely (it is the MARKET
            leg of ``compute_market_residual_r2``, not a sector wrapper). This
            is what makes a market-proxy-only name's residual identically zero,
            and therefore its R² ``NaN`` rather than fabricated.

    Weighting basis (per constituent, one basis only -- never mixed):

    * exactly one contributing wrapper -> weight is trivially 1.0;
    * every contributing wrapper reports a finite positive ``shares_held``
      -> weight by ``shares_held`` (true relative ownership);
    * else, every contributing wrapper reports a finite positive ``weight``
      -> weight by NAV ``weight``. This is a **disclosed proxy**: it mixes by
      how important the name is to each basket rather than by how much of the
      name each basket owns. It is only ever a relative mixing weight between
      wrappers -- it is never reported as, or converted into, an ownership
      quantity (that is ``compute_etf_ownership``'s job, which has no such
      fallback);
    * else -> no composite is produced for that constituent (it is absent from
      the returned dict, so the caller reads ``NaN``).

    Returns:
        ``{symbol: pd.Series}`` of composite daily returns indexed by date.
        Symbols with no usable non-market wrapper are absent. Never raises
        (CONSTRAINT #6) -- a failure returns ``{}``.
    """
    proxy = str(market_proxy or "").upper().strip()
    try:
        deduped = filter_holdings_as_of(holdings_by_etf, as_of=None)

        # ETF -> daily return series, computed once per ETF (not per constituent).
        etf_returns: dict[str, pd.Series] = {}
        for etf_symbol, bars in (etf_bars or {}).items():
            key = str(etf_symbol).upper().strip()
            if key == proxy:
                continue
            if bars is None or getattr(bars, "empty", True) or "Close" not in bars.columns:
                continue
            series = bars["Close"].dropna()
            if len(series) < 2:
                continue
            rets = series.sort_index().pct_change().dropna()
            if rets.empty:
                continue
            etf_returns[key] = rets

        # Constituent -> [(etf_symbol, shares_held, nav_weight), ...]
        contributors: dict[str, list[tuple[str, float, float]]] = {}
        for etf_symbol, rows in deduped.items():
            if etf_symbol == proxy or etf_symbol not in etf_returns:
                continue
            for row in rows:
                symbol = str(getattr(row, "holding_symbol", "") or "").upper().strip()
                if not symbol:
                    continue
                contributors.setdefault(symbol, []).append((
                    etf_symbol,
                    _as_float(getattr(row, "shares_held", None)),
                    _as_float(getattr(row, "weight", None)),
                ))

        composites: dict[str, pd.Series] = {}
        for symbol, entries in contributors.items():
            if not entries:
                continue
            if len(entries) == 1:
                composites[symbol] = etf_returns[entries[0][0]].copy()
                continue

            shares = [s for _e, s, _w in entries]
            navw = [w for _e, _s, w in entries]
            if all(_finite(s) and s > 0.0 for s in shares):
                weights = shares
            elif all(_finite(w) and w > 0.0 for w in navw):
                weights = navw
            else:
                continue

            total = float(sum(weights))
            if not _finite(total) or total <= 0.0:
                continue

            frame = pd.concat(
                [etf_returns[e].rename(e) for e, _s, _w in entries],
                axis=1,
                join="inner",
            ).sort_index()
            if frame.empty:
                continue
            normalized = np.asarray(weights, dtype=float) / total
            composites[symbol] = pd.Series(
                frame.to_numpy(dtype=float) @ normalized,
                index=frame.index,
                name=f"{symbol}_etf_composite",
            )
        return composites
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("build_etf_return_composite failed (non-fatal): %s", exc)
        return {}


def compute_market_residual_r2(
    stock_bars: pd.DataFrame,
    composite_returns: pd.Series,
    market_bars: pd.DataFrame,
    *,
    window: int = 60,
    min_obs: int = 60,
) -> float:
    """Partial R² of a stock on its ETF composite, both residualized on the market.

    See the module docstring for the full formula and for why the naive
    (non-residualized) R² would be a market-beta derate in ETF clothing.

    Args:
        stock_bars: OHLCV DataFrame with ``Close`` and a ``DatetimeIndex``.
        composite_returns: daily RETURN series from
            :func:`build_etf_return_composite` (already differenced -- not prices).
        market_bars: OHLCV DataFrame for ``settings.ETF_HOLDINGS_MARKET_PROXY``.
        window: rolling window in trading days (default 60).
        min_obs: minimum aligned overlapping observations required (default 60).

    Returns:
        ``float`` in ``[0, 1]``, or ``NaN``. **NaN, never 0.0**, whenever:

        * any input is empty / missing ``Close`` / not a usable Series;
        * the three-way ``join="inner"`` overlap has fewer than
          ``max(window, min_obs)`` usable return observations. This is the
          deliberate **NaN-until-full-window-coverage** choice for composition
          drift: a name added to a wrapper last week has no tethered history,
          so a partial-window R² would UNDERSTATE transmission with a
          confident-looking number. Missing beats understated;
        * market variance over the window is zero/non-finite (no market leg to
          residualize against);
        * either residual series is degenerate (``std < 1e-12``). This is the
          market-proxy-only case: with the proxy excluded from the composite,
          ``e_t == 0`` identically and there is nothing to correlate.

    Never raises (CONSTRAINT #6).
    """
    try:
        if (
            stock_bars is None or getattr(stock_bars, "empty", True)
            or "Close" not in getattr(stock_bars, "columns", [])
            or market_bars is None or getattr(market_bars, "empty", True)
            or "Close" not in getattr(market_bars, "columns", [])
            or composite_returns is None or len(composite_returns) == 0
        ):
            return float("nan")

        window = max(2, int(window))
        required = max(window, max(2, int(min_obs)))

        # Contemporaneous inner join, never forward-filled -- identical
        # alignment contract to processing_engine.calculate_rolling_beta.
        aligned = pd.concat(
            [
                stock_bars["Close"].rename("stock"),
                market_bars["Close"].rename("market"),
                pd.Series(composite_returns).rename("composite"),
            ],
            axis=1,
            join="inner",
        ).sort_index()
        if aligned.empty:
            return float("nan")

        returns = pd.DataFrame({
            "stock": aligned["stock"].pct_change(),
            "market": aligned["market"].pct_change(),
            "composite": aligned["composite"],
        }).replace([np.inf, -np.inf], np.nan).dropna()

        if len(returns) < required:
            return float("nan")

        r_i = returns["stock"]
        r_m = returns["market"]
        r_e = returns["composite"]

        var_m = r_m.rolling(window).var().iloc[-1]
        if not _finite(var_m) or float(var_m) <= 0.0:
            return float("nan")

        beta_i = r_i.rolling(window).cov(r_m).iloc[-1] / float(var_m)
        beta_e = r_e.rolling(window).cov(r_m).iloc[-1] / float(var_m)
        if not _finite(beta_i) or not _finite(beta_e):
            return float("nan")

        win = returns.iloc[-window:]
        u = win["stock"] - float(beta_i) * win["market"]
        e = win["composite"] - float(beta_e) * win["market"]
        if float(u.std()) < _DEGENERATE_STD or float(e.std()) < _DEGENERATE_STD:
            return float("nan")

        corr = u.corr(e)
        if not _finite(corr):
            return float("nan")
        return float(min(max(float(corr) ** 2, 0.0), 1.0))

    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("compute_market_residual_r2 failed (non-fatal): %s", exc)
        return float("nan")


def primary_wrapper(
    holdings_by_etf: Mapping[str, Sequence["ETFHolding"]],
) -> dict[str, str]:
    """Largest-weight covered ETF per constituent -- the operator-explainability key.

    Without this, "why is AAPL derated?" is unanswerable from the dashboard
    alone (``sizing/position_sizer.py`` names exactly that question as a design
    goal). Ranks by NAV ``weight`` when any contributing basket reports one,
    otherwise by ``shares_held``; ties break on ETF symbol for determinism.

    Includes the market proxy: a name whose largest wrapper IS the market proxy
    is a genuinely useful thing for an operator to see -- and if that is its
    ONLY wrapper, ``ETF_Comovement_R2`` will read ``NaN``, which is the
    identification limit showing up honestly rather than as a silent zero.

    Returns ``{symbol: etf_symbol}``, omitting any constituent for which
    neither ranking key is finite anywhere. Never raises (CONSTRAINT #6).
    """
    try:
        deduped = filter_holdings_as_of(holdings_by_etf, as_of=None)
        by_symbol: dict[str, list[tuple[str, float, float]]] = {}
        for etf_symbol, rows in deduped.items():
            for row in rows:
                symbol = str(getattr(row, "holding_symbol", "") or "").upper().strip()
                if not symbol:
                    continue
                by_symbol.setdefault(symbol, []).append((
                    etf_symbol,
                    _as_float(getattr(row, "weight", None)),
                    _as_float(getattr(row, "shares_held", None)),
                ))

        out: dict[str, str] = {}
        for symbol, entries in by_symbol.items():
            ranked = [(e, w) for e, w, _s in entries if _finite(w)]
            if not ranked:
                ranked = [(e, s) for e, _w, s in entries if _finite(s)]
            if not ranked:
                continue
            out[symbol] = max(ranked, key=lambda pair: (pair[1], pair[0]))[0]
        return out
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("primary_wrapper failed (non-fatal): %s", exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────
# Sizing-path derating lever (built on the measurements above)
# ─────────────────────────────────────────────────────────────────────────
# Neither ``sizing/kelly.py`` nor ``sizing/vol_target.py`` can see ETF
# transmission -- both observe only a single symbol's own return series, so
# the transmitted component is baked in indistinguishably from fundamental
# volatility, and the cross-sectional structure that makes it
# non-diversifiable is invisible to a univariate estimator. Hence a separate,
# explicit post-multiplier rather than inflating either formula's vol input.
#
# Why a post-multiplier and NOT vol-inflation into Kelly: inflating the
# ``realized_vol`` input to ``_calculate_kelly_sizing`` looks like the
# natural lever and is a BROKEN one. ``sizing.kelly.kelly_sizing_for_strategy``
# reads ``realized_vol`` ONLY in its ``< MIN_TRADES_REQUIRED`` cold-start
# branch; once a strategy has >= 30 closed trades the weight comes from a
# 1,000-resample bootstrap of realized trade returns and the vol input is
# never read at all. A risk control that fires on a cold-start book and then
# silently disappears the moment the book matures is the worst possible
# failure profile -- present exactly when it matters least. This overlay
# therefore post-multiplies the already-composed weight
# (``sizing/position_sizer.py::size_position``, step 3), applying identically
# on every sizing path.

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


# ─────────────────────────────────────────────────────────────────────────
# Portfolio-level covariance inflation (built on the measurements above)
# ─────────────────────────────────────────────────────────────────────────
# The transmission_multiplier lever above derates a NAME's own weight. But
# the mechanism this whole module is named for raises COVARIANCE between
# co-held names -- a portfolio-level effect a per-name derate cannot see no
# matter how it is composed. This section feeds an ETF-co-ownership-inflated
# covariance matrix into sizing.position_sizer.apply_portfolio_gross_cap's
# EXISTING risk-aware path (sizing.vol_target.portfolio_vol_target), rather
# than building a second portfolio-cap mechanism: apply_portfolio_gross_cap
# already accepts cov_matrix/target_vol and only needs a caller to supply
# them (see pipeline/production_steps.py::_build_etf_transmission_cov_matrix).


def _pairwise_etf_overlap(
    symbols: Sequence[str],
    holdings_by_etf: Mapping[str, Sequence["ETFHolding"]],
) -> pd.DataFrame:
    """Cosine similarity of each symbol pair's ETF-ownership-weight vectors.

    For each symbol, builds a vector over the covered ETF universe (one
    coordinate per ETF, valued at that ETF's NAV ``weight`` for the symbol,
    falling back to ``shares_held`` when ``weight`` is unusable -- same
    fallback basis as :func:`build_etf_return_composite`). Two symbols held
    by exactly the same wrappers in the same proportions score ``1.0``; two
    symbols sharing no wrapper at all score ``0.0``. Never negative (weights
    are non-negative NAV/share quantities), so this is a proper ``[0, 1]``
    overlap measure, not a general-purpose cosine similarity.

    A symbol absent from every covered basket gets the zero vector and
    therefore scores ``0.0`` overlap against everything -- correct: no
    measured tethering means no covariance inflation for that pair, exactly
    mirroring ``transmission_multiplier``'s "no coverage -> no-op" contract.

    Returns a symmetric ``len(symbols) x len(symbols)`` DataFrame indexed and
    columned by ``symbols`` with a zero diagonal. Never raises -- an
    unusable ``holdings_by_etf`` (empty, malformed rows) simply yields an
    all-zero overlap matrix (CONSTRAINT #6).
    """
    try:
        deduped = filter_holdings_as_of(holdings_by_etf, as_of=None)
        etfs = sorted(deduped.keys())
        symbol_list = list(symbols)
        symbol_set = set(symbol_list)
        vectors = {s: np.zeros(len(etfs)) for s in symbol_list}

        for j, etf_symbol in enumerate(etfs):
            for row in deduped.get(etf_symbol, []):
                sym = str(getattr(row, "holding_symbol", "") or "").upper().strip()
                if sym not in symbol_set:
                    continue
                w = _as_float(getattr(row, "weight", None))
                if not _finite(w) or w < 0.0:
                    w = _as_float(getattr(row, "shares_held", None))
                if _finite(w) and w > 0.0:
                    vectors[sym][j] = w

        n = len(symbol_list)
        overlap = np.zeros((n, n))
        norms = {s: float(np.linalg.norm(vectors[s])) for s in symbol_list}
        for i, si in enumerate(symbol_list):
            if norms[si] <= 0.0:
                continue
            for k, sk in enumerate(symbol_list):
                if i == k or norms[sk] <= 0.0:
                    continue
                overlap[i, k] = float(
                    np.dot(vectors[si], vectors[sk]) / (norms[si] * norms[sk])
                )
        return pd.DataFrame(overlap, index=symbol_list, columns=symbol_list)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("_pairwise_etf_overlap failed (non-fatal): %s", exc)
        n = len(symbols)
        return pd.DataFrame(np.zeros((n, n)), index=list(symbols), columns=list(symbols))


def _nearest_psd(matrix: np.ndarray, epsilon: float = 1e-10) -> np.ndarray:
    """Eigenvalue-clip a symmetric matrix back to positive semi-definite.

    Off-diagonal inflation in :func:`build_transmission_adjusted_cov` can
    push an otherwise-valid covariance matrix outside the PSD cone (a
    genuine risk this module's own design notes call out --
    ``portfolio_vol_target`` has no PSD check of its own, and would compute
    a nonsensical, even negative, ``w' Sigma w`` from an indefinite matrix
    without one). This clips every eigenvalue below ``epsilon`` up to
    ``epsilon`` and reconstructs -- the standard nearest-PSD-by-eigenvalue-
    clipping repair. (Higham (2002)'s alternating-projections algorithm finds
    the nearest correlation matrix more exactly; that precision is not needed
    here because the input is already close to PSD by construction -- only
    the off-diagonal was multiplicatively perturbed, nothing was
    reconstructed from an arbitrary starting matrix.)

    Symmetrizes the result once more before returning: ``eigh`` assumes (and
    ``build_transmission_adjusted_cov`` guarantees) a symmetric input, but the
    reconstruction ``V @ diag(clipped) @ V.T`` can pick up float roundoff
    asymmetry that would otherwise propagate into a NON-symmetric "covariance
    matrix" being handed to ``portfolio_vol_target``.
    """
    eigvals, eigvecs = np.linalg.eigh(matrix)
    clipped = np.clip(eigvals, epsilon, None)
    repaired = eigvecs @ np.diag(clipped) @ eigvecs.T
    return (repaired + repaired.T) / 2.0


def build_transmission_adjusted_cov(
    returns_df: pd.DataFrame,
    holdings_by_etf: Mapping[str, Sequence["ETFHolding"]],
    *,
    inflation: float,
    window: int = 60,
) -> Optional[pd.DataFrame]:
    """Covariance matrix with off-diagonals inflated by pairwise ETF co-ownership.

    ``cov_adj[i,j] = cov[i,j] * (1 + inflation * overlap[i,j])`` for ``i !=
    j``; the diagonal (each name's OWN variance) is left untouched. This
    models the paper's actual claim -- ETF arbitrage raises CO-MOVEMENT
    between co-held names -- rather than inflating any single name's own
    variance, which is a different, already-handled question
    (:func:`transmission_multiplier`'s per-name sizing derate).

    Args:
        returns_df: daily simple-return DataFrame, one column per symbol,
            ``DatetimeIndex``. Only the trailing ``window`` rows are used;
            the caller is responsible for supplying data computed strictly
            prior to the current bar (this function performs no causality
            checks itself, matching ``sizing.vol_target.portfolio_vol_target``'s
            own contract).
        holdings_by_etf: ``{etf_symbol: [ETFHolding, ...]}`` (duck-typed, the
            shape ``data.etf_holdings.get_etf_holdings`` returns).
        inflation: ``settings.ETF_TRANSMISSION_COV_INFLATION`` -- the
            fractional off-diagonal inflation at maximum (``overlap == 1.0``)
            co-ownership.
        window: trailing trading-day window (default 60, mirrors
            ``ETF_TRANSMISSION_COV_WINDOW_DAYS``).

    Returns:
        A symmetric, positive-semi-definite ``pd.DataFrame`` indexed and
        columned by ``returns_df``'s columns, or ``None`` when the input is
        unusable: fewer than 2 symbols, fewer than ``window`` fully-aligned
        return rows, or a base covariance that couldn't be computed cleanly.
        ``None`` is this function's honest "cannot produce a usable
        covariance matrix this cycle" signal -- the caller's documented
        response is to fall back to ``apply_portfolio_gross_cap``'s
        ``cov_matrix=None`` sum-of-|weight| path, never to a partially-
        covered matrix (``portfolio_vol_target`` zeroes out any symbol
        missing from ``cov_matrix``, which is a far harsher outcome for a
        coverage gap than the existing fallback -- see the caller for why
        that substitution is deliberately never made here).

    Never raises (CONSTRAINT #6).
    """
    try:
        if returns_df is None or getattr(returns_df, "empty", True):
            return None
        symbols = list(returns_df.columns)
        if len(symbols) < 2:
            return None

        window = max(2, int(window))
        aligned = returns_df.dropna(how="any")
        if len(aligned) < window:
            return None
        trimmed = aligned.tail(window)

        base_cov = trimmed.cov().reindex(index=symbols, columns=symbols)
        if base_cov.isna().any().any():
            return None

        overlap = _pairwise_etf_overlap(symbols, holdings_by_etf)
        overlap = overlap.reindex(index=symbols, columns=symbols).fillna(0.0)

        # copy=True is load-bearing, not defensive style: pandas' internal
        # reference-tracking can hand back a READ-ONLY view from to_numpy()
        # depending on what else has touched this DataFrame's block manager
        # (observed depending on test execution order/other pandera/pandas
        # activity in-process), and np.fill_diagonal below mutates in place.
        cov_arr = base_cov.to_numpy(dtype=float, copy=True)
        overlap_arr = overlap.to_numpy(dtype=float, copy=True)
        np.fill_diagonal(overlap_arr, 0.0)  # never inflate variance, only co-movement

        adjusted = cov_arr * (1.0 + float(inflation) * overlap_arr)
        # Symmetrize defensively: cov and overlap are each symmetric by
        # construction, but float roundoff in the elementwise multiply could
        # otherwise introduce a tiny asymmetry ahead of the eigendecomposition.
        adjusted = (adjusted + adjusted.T) / 2.0

        psd = _nearest_psd(adjusted)
        return pd.DataFrame(psd, index=symbols, columns=symbols)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("build_transmission_adjusted_cov failed (non-fatal): %s", exc)
        return None

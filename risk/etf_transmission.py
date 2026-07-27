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

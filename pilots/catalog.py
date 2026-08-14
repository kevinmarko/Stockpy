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
  Size factors (free point-in-time Value/Quality fundamentals didn't exist when
  this backtest was built — see the next bullet for the fix).
* ``dividend-income``/``deep-value``/``value-quality`` join real SEC EDGAR
  point-in-time (PIT) fundamentals backtests (``dividend_yield_edgar_pit`` /
  ``deep_value_edgar_pit`` / ``value_quality_edgar_pit``) — each an honest,
  narrower single/dual-factor proxy of the live signal(s), not a literal
  reimplementation (e.g. ``deep-value``'s backtest is a P/B "cheapness" tilt,
  not a Graham Number reconstruction — see ``scripts/refresh_validations.py``
  for why). Requires the EDGAR PIT backfill to have been run
  (``scripts/backfill_edgar_fundamentals.py``); degrades to an honest
  "insufficient data" report otherwise, never fabricated.
* ``cross-sectional-momentum`` joins ``cross_sectional_momentum``, a faithful
  price-only reimplementation of the live 12-1m signal over a 30-name liquid
  large-cap universe (no proxy narrowing needed).
* ``edge-garch`` joins ``garch_vol_target``, a RiskMetrics EWMA vol-timing proxy
  on SPY — this backtests only the GARCH tail-risk-veto half of the live signal;
  the ``edge_ratio`` half (which depends on real closed-trade history) still
  isn't backtested standalone, so the curve is an honest, narrower proxy — the
  same scope-narrowing precedent as the ``multifactor`` Pilot's own backtest.
* ``rsi-reversal``/``relative-strength``/``risk-adjusted`` join real price-only
  backtests (``rsi14_extremes`` / ``relative_strength_xsec`` / ``sortino_drawdown``).
* ``regime-navigator`` joins ``macro_regime_pit`` (2026-07) — a real
  point-in-time reconstruction of the live ``dto_models.MacroEconomicDTO``
  regime classification from persisted FRED history, NOT price/volume alone;
  see its own catalog entry below for the two documented v1 caveats (no HMM
  overlay replay, current-snapshot sector).
* ``forecast-aligned`` joins ``forecast_direction_arima_hw`` (2026-07) — a
  NARROWER ARIMA+Holt-Winters-only proxy of the live 5-model forecast
  ensemble, bounded to the last 5 years with weekly (not daily) refits (the
  full ensemble re-fit at every historical date is computationally
  infeasible). ``news-catalyst`` (point-in-time news) still stays
  ``validation_strategy_id=None`` — its signal can't yet be honestly
  reconstructed from price/volume alone; forward-archiving to
  ``HistoricalStore.news_history`` started 2026-07 (see its own catalog
  entry) but no real backtest is possible for many months yet.
* ``balanced-blend`` joins ``signal_replay_balanced_blend`` (2026-07) — the
  first adapter in ``STRATEGY_REGISTRY`` to replay the REAL
  ``SignalAggregator``/``SignalRegistry`` weighted-sum code path across
  history rather than hand-writing a standalone proxy formula. 3 of the 17
  modules are excluded for the whole backtest window (``news_catalyst``,
  ``lgbm_ranker``, ``forecast_alignment`` — see its own catalog entry for
  why each), and 2 of the surviving 14 (``graham_value``,
  ``dividend_quality``) genuinely degrade to their own real "no data"
  branches since EDGAR PIT fundamentals can't safely supply the inputs they
  need. NOT a literal reconstruction of the full 17-module blend.
* ``coppock_momentum`` exists in ``STRATEGY_REGISTRY`` but has no corresponding
  signal module in ``SIGNAL_WEIGHTS``, so it is deliberately NOT surfaced as a
  Pilot (a Pilot's weights must be real signal-module ids).
* ``ml-cross-sectional-rank`` joins ``lgbm_ranker`` (2026-08) — the first
  ``STRATEGY_REGISTRY`` adapter that genuinely RETRAINS a fresh model per CPCV
  fold (native (date, ticker) MultiIndex CPCV, PR #648) rather than replaying
  a static precomputed return series. This validates the METHODOLOGY, not the
  exact currently-deployed ``ml/models/lgbm_latest.pkl`` artifact the live
  signal module actually loads — see the Pilot's own inline comment and
  ``docs/signals/lgbm_ranker.md``'s Backtest Validation section.
* ``vrp-premium-selling`` joins ``vrp_premium_selling`` (2026-08) — the
  first OPTIONS-SELLING ``STRATEGY_REGISTRY`` entry, gated by the 4-scenario
  tail-risk stress gate (``validation/stress_scenarios.py``,
  ``StrategyValidationHarness(is_options_selling=True, ...)``) on top of the
  usual PBO/DSR/Sharpe/MaxDD gates. No historical options-chain data exists
  anywhere in this codebase, so True_IVR/VRP are documented, real-price-driven
  proxies, and CREDIT-EVENT detection is only real from 2023-08-08 onward
  (``BAMLH0A0HYM2``'s real FRED coverage start) — VIX gating is real across
  the full history. See ``docs/signals/vrp_premium_selling.md``'s Backtest
  Validation section and ``validation/options_selling_backtest.py``'s module
  docstring for the full honesty contract.
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
        # Faithful price-only reimplementation of the live 12-1m signal
        # (signals/cross_sectional_momentum.py) over a 30-name liquid
        # large-cap universe (scripts.refresh_validations._XSEC_UNIVERSE_30).
        validation_strategy_id="cross_sectional_momentum",
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
        # Single-factor cross-sectional tilt on real SEC EDGAR point-in-time
        # dividend_yield (see scripts/refresh_validations.py's EDGAR PIT note).
        validation_strategy_id="dividend_yield_edgar_pit",
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
        # A price-to-book "cheapness" tilt on real SEC EDGAR PIT fundamentals —
        # NOT a literal Graham Number reconstruction (see
        # scripts/refresh_validations.py's _build_deep_value_adapter docstring
        # for why: deriving book value in dollars from a stored ratio would mix
        # price vintages).
        validation_strategy_id="deep_value_edgar_pit",
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
        # Real SEC EDGAR PIT Value(P/B) + Quality(ROE+op margin) composite — a
        # narrower, honest proxy of the full three-signal blend above (the same
        # scope-narrowing precedent as the "multifactor" Pilot's own backtest).
        validation_strategy_id="value_quality_edgar_pit",
    ),
    Pilot(
        id="edge-garch",
        name="Edge & Volatility",
        category="Factor",
        description=(
            "Per-symbol statistical edge ratio combined with a GARCH tail-risk "
            "volatility veto — rewards names with a favorable historical "
            "risk/reward profile, penalized in high-volatility regimes."
        ),
        weights={"edge_garch": 1.0},
        long_only=False,
        # Honest price-only RiskMetrics EWMA vol-timing proxy on SPY — covers
        # only the GARCH tail-risk-veto half of this signal. The edge_ratio
        # half (depends on real closed-trade history, evaluation_engine's
        # post-trade MFE/MAE) still isn't backtested standalone; same
        # narrower-proxy precedent as the "multifactor" Pilot's own backtest.
        validation_strategy_id="garch_vol_target",
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
        # Real backtest (2026-07): signal_replay_balanced_blend replays the
        # REAL SignalAggregator/SignalRegistry weighted-sum code path across
        # history (not a hand-rolled proxy formula, unlike every other
        # adapter in scripts/refresh_validations.py). NOT a literal
        # reconstruction of the full 17-module blend above: 3 modules are
        # excluded for the whole backtest window --
        # news_catalyst (live Finnhub calls), lgbm_ranker (always loads the
        # CURRENT persisted model regardless of historical date),
        # forecast_alignment (only backtestable within forecast_direction_arima_hw's
        # own bounded 5yr window -- excluded here to keep one consistent
        # 14/17-module composition across the whole window). The 14
        # surviving modules' weights are renormalized proportionally back to
        # the original total mass. Two of the 14 survivors (graham_value,
        # dividend_quality) genuinely degrade to their own real "no data"
        # branches -- EDGAR PIT fundamentals don't safely carry a dollar
        # book-value-per-share or payout ratio without repeating the
        # mixed-price-vintage bug _build_deep_value_adapter's docstring
        # already warns against, so those two fields are fed NaN rather than
        # a fabricated derivation. See
        # scripts/refresh_validations.py's _build_signal_replay_adapter
        # docstring for the full honesty contract.
        validation_strategy_id="signal_replay_balanced_blend",
    ),
    # ── Dedicated Pilots for the previously catalog-uncovered signal modules ──
    # Every ``weights`` key is a real signal module; the four price-only
    # backtestable ones carry a real ``validation_strategy_id``, the rest stay
    # honestly curveless (their signals need macro / news / forecast / fundamental
    # inputs that can't be reconstructed from price alone).
    Pilot(
        id="regime-navigator",
        name="Regime Navigator",
        category="Macro",
        description=(
            "Top-down macro regime read — leans defensive in Recession/Credit-Event "
            "regimes and rotates toward risk-on sectors when the systemic backdrop clears."
        ),
        weights={"macro_regime": 1.0},
        long_only=False,
        # Real point-in-time backtest (2026-07): macro_regime_pit reconstructs
        # the REAL dto_models.MacroEconomicDTO.market_regime/.killSwitch
        # classification at every historical date from real FRED series
        # (VIXCLS/T10Y2Y/BAMLH0A0HYM2/UNRATE persisted in HistoricalStore),
        # reusing the live DTO class directly -- not a re-implementation.
        # TWO documented v1 caveats (see scripts/refresh_validations.py's
        # _build_macro_regime_adapter docstring): (1) the HMM regime-downgrade
        # overlay is NOT replayed -- correctly replaying its calendar-gated
        # expanding-window refit is a materially larger task, deferred to a
        # future v2; (2) sector is a CURRENT snapshot applied across the full
        # backtest history (GICS reclassifications are rare for this universe
        # but not impossible). Honest caveat carried from before: the live
        # signal's only per-row input beyond the shared macro state is
        # `sector`, so within a given regime every stock in the same sector
        # scores identically -- this Pilot is deliberately a top-down,
        # macro+sector-driven read, not a claim of stock-specific analysis.
        validation_strategy_id="macro_regime_pit",
    ),
    Pilot(
        id="rsi-reversal",
        name="RSI Reversal",
        category="Mean Reversion",
        description=(
            "Fades short-term extremes with the classic RSI(14) rule — buys oversold "
            "washouts and trims overbought spikes back toward the mean."
        ),
        weights={"rsi_extremes": 1.0},
        long_only=False,
        # Honest price-only RSI(14) 30/70 backtest on SPY.
        validation_strategy_id="rsi14_extremes",
    ),
    Pilot(
        id="relative-strength",
        name="Relative Strength",
        category="Momentum",
        description=(
            "Favors the names outrunning the S&P 500 — a relative-strength tilt that "
            "holds the market's leaders and sidesteps the laggards."
        ),
        weights={"relative_strength": 1.0},
        long_only=False,
        # Honest price-only cross-sectional relative-strength-vs-SPY backtest.
        validation_strategy_id="relative_strength_xsec",
    ),
    Pilot(
        id="news-catalyst",
        name="News Catalyst",
        category="Sentiment",
        description=(
            "Reacts to fresh headline sentiment and earnings catalysts, dampening "
            "signals right around scheduled events where the reaction is unpredictable."
        ),
        weights={"news_catalyst": 1.0},
        long_only=False,
        # Point-in-time news/sentiment history isn't available to backtest honestly.
        # As of the 2026-07 forward-archive change, NewsCatalystSignal.pre_compute()
        # now persists each cycle's live score to HistoricalStore's news_history
        # table (data/historical_store.py) -- this accumulates real history going
        # forward but does NOT unblock a backtest today; validation_strategy_id
        # stays None until enough real history exists (roughly 6-12+ months).
        validation_strategy_id=None,
    ),
    Pilot(
        id="forecast-aligned",
        name="Forecast Aligned",
        category="Forecast",
        description=(
            "Tilts toward names whose projected multi-horizon forecast points to "
            "meaningful upside, and away from those forecast to decline."
        ),
        weights={"forecast_alignment": 1.0},
        long_only=False,
        # Real backtest (2026-07): forecast_direction_arima_hw. A NARROWER
        # proxy of the live 5-model ensemble (ARIMA + Holt-Winters only --
        # CNN-LSTM/Prophet re-fits at every historical date are computationally
        # infeasible), bounded to the last 5 years with WEEKLY (not daily)
        # refits, over the same 10-ticker universe as the EDGAR PIT adapters.
        # Reuses the REAL ForecastAlignmentSignal().compute() scoring, not a
        # reimplementation. See scripts/refresh_validations.py's
        # _build_forecast_direction_adapter docstring for the full cost
        # accounting and honesty contract.
        validation_strategy_id="forecast_direction_arima_hw",
    ),
    Pilot(
        id="risk-adjusted",
        name="Risk-Adjusted",
        category="Risk",
        description=(
            "Rewards durable risk-adjusted performance — favoring high Sortino names "
            "while penalizing deep, painful drawdowns."
        ),
        weights={"sortino_drawdown": 1.0},
        long_only=False,
        # Honest price-only rolling-Sortino/drawdown-gate backtest on SPY.
        validation_strategy_id="sortino_drawdown",
    ),
    Pilot(
        id="sector-quality-rank",
        name="Sector Quality Rank",
        category="Factor",
        description=(
            "Screens for durable earnings quality — cash-backed profits over "
            "accounting accruals, and strong gross margins — ranked against "
            "peers in the SAME sector so a bank isn't judged on a software "
            "company's scale."
        ),
        weights={"sector_quality_rank": 15.0},
        # Real backtest (2026-08): long-only, equal-weighted, TOP-HALF WITHIN
        # SECTOR (not a literal top-decile -- see
        # scripts/refresh_validations.py's _build_sector_quality_rank_adapter
        # docstring for why a strict decile degenerates to ~1 name per sector
        # in a small peer group).
        long_only=True,
        # Real point-in-time backtest (2026-08): the first STRATEGY_REGISTRY
        # adapter to build a genuine (Date, Ticker) MultiIndex panel and
        # exercise CombinatorialPurgedCV's native MultiIndex support (PR
        # #648) end-to-end. Sources REAL accrual_ratio/gross_profitability
        # directly from SEC EDGAR company facts (a documented exception to
        # this file's usual "read only through HistoricalStore" EDGAR-PIT
        # convention -- neither field exists there yet). Measured 2010-01-01
        # -> 2026-08-08 over a deliberately narrow 12-ticker/2-sector
        # universe (Technology + Consumer Defensive -- the only two sectors
        # among this file's vetted names that clear SNEQR's own
        # MIN_SECTOR_SIZE=5 within-sector-ranking floor): Sharpe 1.10, PBO
        # 0.0, DSR 1.0, MaxDD 28.4% -- deployable=True, though MaxDD clears
        # the 30% gate by a narrow margin on this small a universe. The
        # LIVE signal module (signals/sector_quality_rank.py) remains
        # dormant in production (contributes 0.0 every cycle) until a
        # separate data-plumbing task wires these two inputs into
        # processing_engine.calculate_fundamental_metrics() -- surfaced
        # here regardless of that gap, matching the "news-catalyst" Pilot's
        # own precedent of shipping a Pilot ahead of full live wiring. See
        # docs/signals/sector_quality_rank.md's Backtest Validation section
        # and docs/VALIDATION_STRATEGY_FIX_LOG.md's 2026-08-08 entry for the
        # full construction and honesty caveats.
        validation_strategy_id="sector_quality_rank",
    ),
    Pilot(
        id="ml-cross-sectional-rank",
        name="ML Cross-Sectional Rank",
        category="Blend",
        description=(
            "A LightGBM ranking model that learns cross-sectional patterns across "
            "momentum, volatility, mean-reversion and fundamental features — a "
            "machine-learned complement to the platform's rules-based factor tilts."
        ),
        weights={"lgbm_ranker": 1.0},
        long_only=False,
        # Real backtest (2026-08): lgbm_ranker (scripts/refresh_validations.py::
        # _build_lgbm_ranker_adapter) genuinely RETRAINS a fresh
        # LGBMCrossSectionalRanker PER CPCV FOLD on real point-in-time
        # features, using native (date, ticker) MultiIndex CPCV
        # (PR #648 / use_native_multiindex_cv=True) with a real ~21-day
        # forward-return t1 -- not a static precomputed return series like
        # every other adapter in this registry. This validates the
        # METHODOLOGY (does a freshly-trained ranker generalize OOS on this
        # universe?), NOT the exact currently-deployed model: it does not
        # replay the single persisted ml/models/lgbm_latest.pkl artifact the
        # live signal module actually loads via LGBMCrossSectionalRanker.
        # load_latest() -- a materially different, weaker guarantee than
        # e.g. "cross-sectional-momentum"'s literal price-only
        # reimplementation. See docs/signals/lgbm_ranker.md's Backtest
        # Validation section for the honest before/after numbers and the
        # bounded 6-year feature-panel window / proxy-OHLCV caveats.
        validation_strategy_id="lgbm_ranker",
    ),
    Pilot(
        id="vrp-premium-selling",
        name="Volatility Premium Seller",
        category="Factor",
        description=(
            "Sells options premium only when the Volatility Risk Premium regime "
            "gate clears (True IVR > 50, VRP > 2%, VIX < 30, no Credit Event); "
            "otherwise stays in Cash/Wait."
        ),
        weights={"vrp_premium_selling": 1.0},
        long_only=False,
        # Real backtest (2026-08): validation/options_selling_backtest.py's
        # simulate_vrp_iron_condor_returns genuinely constructs and marks to
        # market a real Black-Scholes Iron Condor via the SAME
        # OptionsPricingRecommender the live pipeline uses -- not a
        # closed-form approximation. HONEST SCOPE: no historical
        # options-chain data exists anywhere in this codebase, so True_IVR/
        # VRP are documented, real-price-driven proxies (the identical
        # fallback tier build_premium_directive itself uses absent a live
        # chain), and CREDIT-EVENT detection is only real from 2023-08-08
        # onward (BAMLH0A0HYM2's real FRED coverage start) -- VIX gating is
        # real across the full history. See
        # docs/signals/vrp_premium_selling.md's Backtest Validation section
        # for the full honesty contract and measured numbers.
        validation_strategy_id="vrp_premium_selling",
    ),
    Pilot(
        id="options-flow-sentiment",
        name="Options Flow Sentiment",
        category="Factor",
        description=(
            "Tracks aggressive institutional options order flow (sweeps and blocks) "
            "to capture directional alpha and smart-money positioning."
        ),
        weights={"options_flow_sentiment": 1.0},
        long_only=False,
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

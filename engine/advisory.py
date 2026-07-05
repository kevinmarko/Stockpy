"""
engine/advisory.py — Per-Symbol Holding-Aware Advisory Engine
==============================================================
Produces a per-symbol BUY / SELL / HOLD recommendation tailored to the
holder's actual cost basis, unrealized P&L, and accumulated dividends.

Design principles
-----------------
* **Integrate, don't reinvent** (CONSTRAINT #7): calls the existing
  StrategyEngine, ForecastingEngine, ProcessingEngine,
  TechnicalOptionsEngine, and fractional_kelly — never re-implements
  their math.
* **Resilience** (CONSTRAINT #6): every module call is wrapped in
  try/except.  Missing outputs lower conviction and/or set
  ``data_quality="PARTIAL"`` rather than crashing.
* **Source separation** (CONSTRAINT #4): ``PortfolioPosition`` /
  ``AccountSnapshot`` (from Robinhood) are the source of truth for
  account state; ``MarketDataProvider`` is the source of truth for
  prices, indicators, and fundamentals.
* **No magic numbers**: every threshold lives in ``CONFIG`` at the top
  of this file with an explanatory comment.
* **Type-annotated public API**: all public functions carry full type
  hints; the module-level logger uses ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Literal, Optional

import pandas as pd

from data.market_data import MarketDataError, MarketDataProvider
from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition
from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
from sizing.kelly import estimate_win_rate_and_payoff, fractional_kelly
from sizing.vol_target import volatility_target_weight

# Module-level imports of the heavy engines so that test monkeypatching via
# mock.patch("engine.advisory.<ClassName>") resolves correctly.  These are
# never instantiated at import time — construction happens lazily inside
# evaluate() wrapped in try/except per CONSTRAINT #6.
from processing_engine import ProcessingEngine
from forecasting_engine import ForecastingEngine
from technical_options_engine import TechnicalOptionsEngine
from strategy_engine import StrategyEngine
from transactions_store import TransactionsStore
from settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CONFIG — single source of truth for ALL advisory thresholds.
# Edit values here; never embed literals in the decision logic below.
# ---------------------------------------------------------------------------
CONFIG: Dict[str, Any] = {
    # ── Score thresholds (mirror StrategyEngine Phase-5 score scale 0-100) ──
    # score ≥ strong_buy_score_threshold → base action = BUY (high conviction)
    "strong_buy_score_threshold": 75,
    # score ∈ [buy_score_threshold, strong_buy_score_threshold) → base = BUY
    "buy_score_threshold": 55,
    # score < sell_score_threshold → base action = SELL
    "sell_score_threshold": 35,

    # ── Holding-aware P&L thresholds ─────────────────────────────────────────
    # Effective unrealized gain percentage (total-return basis, i.e. including
    # dividends already received) above which a "neutral signal + holding" pair
    # is treated as HOLD instead of BUY — don't pile into a winner already captured.
    "unrealized_gain_hold_bias_pct": 10.0,   # percent, e.g. 10.0 = up 10 %

    # Below this loss percentage AND with a bearish 30-day forecast, escalate
    # from HOLD to SELL even when the raw signal is neutral.
    "unrealized_loss_sell_threshold_pct": -10.0,  # percent, e.g. -10.0 = down 10 %

    # ── DIVIDEND HOLD BIAS RULE ────────────────────────────────────────────
    # WHY: A holder accumulating a high forward yield or meaningful cumulative
    # dividends has already reduced their effective cost basis through income.
    # Forcing a sale on a weak-but-non-bearish signal sacrifices that
    # compounding without a structural reason to exit.
    #
    # FIRES when ALL of the following are true:
    #   (a) caller is currently holding (position is not None)
    #   (b) forward dividend_yield ≥ dividend_yield_hold_bias_threshold
    #       OR cumulative dividends_received ≥ dividend_total_received_hold_bias_usd
    #   (c) the base action is BUY or HOLD (i.e. not already in bearish territory)
    #   (d) the raw StrategyEngine score is below buy_score_threshold
    #       (i.e. the signal is not genuinely strong)
    # EFFECT: overrides BUY → HOLD, adds dividend drivers to the rationale.
    "dividend_yield_hold_bias_threshold": 0.04,      # ≥ 4 % forward annual yield
    "dividend_total_received_hold_bias_usd": 50.0,   # ≥ $50 cumulative dividends

    # ── Advisory-layer position size cap ─────────────────────────────────────
    # This advisory cap is deliberately tighter than settings.MAX_POSITION_WEIGHT
    # (1.0 = 100 %) used by the live execution layer; these recommendations are
    # guidance-only and should fit within a diversified single-name ceiling.
    "max_single_position_pct": 0.05,  # 5 % of portfolio per name

    # ── Kelly parameters ─────────────────────────────────────────────────────
    # Should stay in sync with settings.KELLY_FRACTION / KELLY_CAP; duplicated
    # here so CONFIG is self-contained and the advisory layer doesn't silently
    # inherit a live-execution setting change.
    "kelly_fraction": 0.5,   # half-Kelly
    "kelly_cap": 0.20,       # hard cap before the advisory max_single_position_pct clamp

    # ── Conviction levels ────────────────────────────────────────────────────
    "conviction_strong_buy": 0.85,
    "conviction_buy": 0.70,
    "conviction_hold": 0.55,
    "conviction_sell": 0.65,
    # Elevated conviction used when: holding below effective cost AND forecast is bearish
    "conviction_strong_sell": 0.80,

    # ── Conviction × data-quality coupling (A1) ──────────────────────────────
    # WHY: a recommendation built on degraded data must not carry the same
    # confidence as one built on clean data.  After the action/holding overlay
    # sets the base conviction, it is multiplied by the multiplier matching the
    # final ``data_quality`` label so the number the operator sees actually
    # reflects how much of the signal survived.  OK → ×1.0 (no key needed).
    "conviction_partial_multiplier": 0.6,   # one or more engine stages failed
    "conviction_stale_multiplier": 0.8,     # price quote flagged stale by provider

    # ── Forecast direction thresholds ────────────────────────────────────────
    # If (forecast_30d - current_price) / current_price < bearish threshold, the
    # 30-day forecast is classified "bearish" and increases SELL pressure; above
    # the (symmetric) bullish threshold it CONFIRMS a BUY — raising conviction
    # and preventing the Case-C "already up, hold instead of buy" override from
    # silencing a signal the forecast independently agrees with.
    "bearish_forecast_pct_threshold": -0.03,   # -3 %
    "bullish_forecast_pct_threshold": 0.03,    # +3 %

    # ── Data requirements ────────────────────────────────────────────────────
    # Minimum bars required before running full technical indicators / strategy
    # engine; below this we still return a recommendation but with PARTIAL quality.
    "min_history_bars": 30,

    # ── Macro-triggered advisory gating ──────────────────────────────────────
    # WHY: Systemic macro stress is a separate risk dimension from individual
    # security signals.  When macro conditions deteriorate past these thresholds
    # the advisory layer applies conservative overrides BEFORE the holding-aware
    # overlay runs — holding overlays can still escalate to SELL, but no new BUY
    # signals are issued into a regime-flagged environment.
    #
    # Hard gate (RECESSION or CREDIT EVENT macro regime):
    #   Any raw STRONG BUY / BUY → downgraded to HOLD so the platform never
    #   recommends fresh equity allocations into a systemic crisis.
    #
    # Soft gate (VIX > macro_vix_gate_threshold OR Sahm ≥ macro_sahm_gate_threshold):
    #   Apply a -macro_score_penalty pt penalty to the composite score before
    #   mapping it to a base action.  A score that was marginally bullish may
    #   become neutral or mildly bearish under stress.
    #
    # Sector veto (Finance / Real Estate under inverted curve or blown spreads):
    #   These sectors face direct structural headwinds from an inverted yield
    #   curve (net-interest-margin compression) or extreme HY OAS (credit market
    #   seizure).  Any BUY signal for a vetoed sector is suppressed to HOLD.
    "macro_vix_gate_threshold": 30.0,        # VIX above this → soft gate fires
    "macro_sahm_gate_threshold": 0.5,        # Sahm Rule at/above this → soft gate fires
    "macro_score_penalty": 25,               # pts subtracted from score under soft gate
    # Sectors with structural exposure to yield-curve / credit-spread stress:
    "macro_veto_sectors": [
        "Financials", "Financial Services", "Real Estate",
    ],
    # Yield curve (10y-2y spread) below this → veto macro_veto_sectors from fresh buys.
    "macro_veto_yield_curve_threshold": 0.0,
    # HY OAS above this → veto macro_veto_sectors from fresh buys.
    "macro_veto_oas_threshold": 6.0,

    # ── Verbose-rationale invalidation levels (Task 1.5) ─────────────────────
    # RSI levels used in section [C] to name mean-reversion void conditions.
    # Kept in CONFIG so the invalidation narrative stays consistent with the
    # signal-module parameters that produced the entry signal.
    "rsi_mean_reversion_exit_level": 35,     # RSI(14) above this → oversold bounce gone
    "rsi_2_mean_reversion_exit_level": 35,   # RSI(2) above this → ultra-short bounce gone
}


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Recommendation:
    """Immutable advisory recommendation for a single symbol.

    Attributes
    ----------
    symbol : str
        Uppercase ticker.
    action : Literal["BUY", "SELL", "HOLD"]
        Recommended action.
    strategy : str
        Human-readable description of the primary driver(s).
    conviction : float
        Confidence in the recommendation, in [0.0, 1.0].
    rationale : str
        One-paragraph plain-English explanation citing the top 2-3 drivers.
    suggested_position_pct : float
        Fraction of portfolio to allocate on a BUY; 0.0 for SELL / HOLD.
        Bounded by CONFIG["max_single_position_pct"] and Kelly sizing.
    forecast : float or None
        30-day blended price forecast; None when the forecasting engine
        failed or had insufficient history.
    key_indicators : dict[str, float]
        Subset of computed metrics for display / downstream consumption.
        Uses ``float("nan")`` for unavailable values rather than omitting
        the key, so consumers can always check a fixed set of keys.
    data_quality : Literal["OK", "STALE", "PARTIAL"]
        "OK"      — all sources returned fresh, parseable data.
        "STALE"   — price quote was flagged stale by the provider (yfinance
                    always returns stale; Alpaca only when > 60 s old).
        "PARTIAL" — one or more modules failed; recommendation is still
                    returned but with reduced conviction.
    synthetic_inputs : bool
        True when OHLCV bar history was unavailable and a flat synthetic bar
        (Open=High=Low=Close=price, Volume=0) was substituted.  In that case the
        technical indicators (RSI, ATR, Aroon, …) are meaningless, so they are
        omitted from ``rationale`` and reported as NaN in ``key_indicators``; the
        flag lets downstream consumers / the GUI badge the recommendation.
    """

    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    strategy: str
    conviction: float
    rationale: str
    suggested_position_pct: float
    forecast: Optional[float]
    key_indicators: Dict[str, float]
    data_quality: Literal["OK", "STALE", "PARTIAL"]
    # Tier 9 — Claude-generated analyst narrative (on-demand only, opt-in via
    # settings.LLM_COMMENTARY_ENABLED).  Carries an :class:`AnalystRationale`
    # ``.model_dump()`` dict on success, ``None`` on any failure — the
    # deterministic ``rationale`` field above is ALWAYS preserved regardless.
    # Typed as ``Dict[str, Any]`` (not the schema model directly) so this file
    # never needs to import ``llm.schemas`` — keeps the SDK reachability
    # surface lazy.  Default ``None`` keeps positional construction stable.
    llm_rationale: Optional[Dict[str, Any]] = None
    # Tier 9 Scope 4 — Opal (OpenAI) grounded research brief (on-demand only,
    # opt-in via settings.OPAL_RESEARCH_ENABLED, independent of
    # LLM_COMMENTARY_ENABLED).  Carries a :class:`llm.schemas.ResearchBrief`
    # ``.model_dump()`` dict on success, ``None`` on any failure or when Opal
    # is disabled.  Same lazy-typing rationale as ``llm_rationale`` above —
    # this file never imports ``llm.schemas`` at module level.  Additive:
    # existing positional ``Recommendation(...)`` constructions elsewhere in
    # the repo are unaffected by this new trailing field.
    research_brief: Optional[Dict[str, Any]] = None
    # A2 — True when technical inputs were computed on a flat synthetic bar
    # because real OHLCV history was missing.  Trailing default keeps existing
    # positional ``Recommendation(...)`` constructions unaffected.
    synthetic_inputs: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate(
    symbol: str,
    position: Optional[PortfolioPosition],
    market: MarketDataProvider,
    snapshot: Optional[AccountSnapshot],
    macro_dto: Optional[MacroEconomicDTO] = None,
    transactions_store: Optional[Any] = None,
    context_extras: Optional[Dict[str, Any]] = None,
) -> Recommendation:
    """Produce a holding-aware advisory recommendation for ``symbol``.

    This function orchestrates the full pipeline: market data fetch → technical
    indicators → GARCH vol → forecast → strategy signal → holding-aware overlay
    → Kelly sizing → recommendation assembly.  Every stage is wrapped in
    try/except; a failing stage degrades conviction / data_quality rather than
    raising to the caller.

    Parameters
    ----------
    symbol : str
        Ticker symbol (case-insensitive; normalised to uppercase internally).
    position : PortfolioPosition or None
        Current Robinhood position from ``data.robinhood_portfolio``, or
        ``None`` when the symbol is not held.  This is the **source of truth**
        for cost basis, quantity, and cumulative dividends (never use the
        market provider for these).
    market : MarketDataProvider
        Live market-data provider (``data.market_data.get_provider()``).
        Source of truth for prices, OHLCV bars, and fundamentals.
    snapshot : AccountSnapshot or None
        Full Robinhood account snapshot.  Used for total-equity denominator
        when computing suggested position size.  ``None`` is valid when
        Robinhood credentials are not configured — Kelly sizing still runs
        because it reads ``transactions_store``, not account equity.
    macro_dto : MacroEconomicDTO or None
        Pre-computed macro regime DTO (should carry ``hmm_risk_on_probability``
        when available).  When ``None`` a neutral/default DTO is constructed so
        the pipeline degrades gracefully on missing FRED data.
    transactions_store : TransactionsStore or None
        Optional injected transactions store for Kelly sizing (avoids the live
        DB in tests — pass ``TransactionsStore(db_url="sqlite:///:memory:")``).
    context_extras : dict or None
        Pre-computed universe-wide data from the orchestrator's
        ``global_registry.run_pre_compute()`` pass, keyed by:
          - ``'xsec_percentile_ranks'``: dict[str, float] — Jegadeesh-Titman
            12-1m cross-sectional rank per ticker.
          - ``'multifactor_scores'``: dict[str, dict] — Fama-French factor
            Z-scores per ticker (Value_Z, Quality_Z, LowVol_Z, Size_Z,
            Multifactor_Composite).
        When provided these are injected into the ``SignalContext`` passed to
        ``StrategyEngine.evaluate_security()`` so cross-sectional and
        multifactor signals score correctly instead of falling back to 0
        (their neutral value when the context dicts are empty).
        ``None`` (the default) reproduces pre-wiring behavior exactly.

    Returns
    -------
    Recommendation
        Frozen dataclass.  Never raises.
    """
    symbol = symbol.upper().strip()
    partial_flags: list[str] = []   # reasons why data_quality might be PARTIAL
    is_stale: bool = False

    # ──────────────────────────────────────────────────────────────────────────
    # Step 1 — Fetch live quote and OHLCV bars
    # ──────────────────────────────────────────────────────────────────────────
    bars_df: Optional[pd.DataFrame] = None
    current_price: float = 0.0

    try:
        quote = market.get_latest_quote(symbol)
        current_price = quote.price
        is_stale = quote.is_stale
    except Exception as exc:
        logger.warning("advisory[%s]: quote fetch failed — %s", symbol, exc)
        partial_flags.append("quote_unavailable")

    try:
        bars_df = market.get_intraday_bars(symbol, lookback_days=252)
        if bars_df is not None and not bars_df.empty and current_price == 0.0:
            # Fall back to the last bar's close when the quote endpoint failed.
            current_price = float(bars_df["Close"].iloc[-1])
    except Exception as exc:
        logger.warning("advisory[%s]: bars fetch failed — %s", symbol, exc)
        partial_flags.append("bars_unavailable")

    if current_price <= 0.0:
        # Cannot produce any meaningful recommendation without a price.
        logger.error("advisory[%s]: no usable price; returning HOLD/PARTIAL.", symbol)
        return _fallback_hold(symbol, "No usable price data — provider returned 0 or raised.")

    # ──────────────────────────────────────────────────────────────────────────
    # Step 2 — Build MarketBarDTO (required by StrategyEngine)
    # ──────────────────────────────────────────────────────────────────────────
    bar_dto: MarketBarDTO
    if bars_df is not None and not bars_df.empty:
        last = bars_df.iloc[-1]
        try:
            bar_idx = bars_df.index[-1]
            bar_date = bar_idx.to_pydatetime() if hasattr(bar_idx, "to_pydatetime") else datetime.now()
            bar_dto = MarketBarDTO(
                date=bar_date,
                ticker=symbol,
                open_price=float(last.get("Open", current_price)),
                high_price=float(last.get("High", current_price)),
                low_price=float(last.get("Low", current_price)),
                close_price=current_price,
                volume=int(last.get("Volume", 0)),
            )
        except Exception as exc:
            logger.warning("advisory[%s]: MarketBarDTO construction failed — %s; using synthetic.", symbol, exc)
            bar_dto = _synthetic_bar_dto(symbol, current_price)
            partial_flags.append("bar_dto_synthetic")
    else:
        bar_dto = _synthetic_bar_dto(symbol, current_price)
        partial_flags.append("bar_dto_synthetic")

    # ──────────────────────────────────────────────────────────────────────────
    # Step 3 — Fetch fundamentals and build FundamentalDataDTO
    # ──────────────────────────────────────────────────────────────────────────
    fund_dto: FundamentalDataDTO
    raw_fund_info: Dict[str, Any] = {}

    try:
        raw_fund_info = market.get_fundamentals(symbol) or {}
        fund_dto = FundamentalDataDTO.from_raw_dict(symbol, raw_fund_info)
    except Exception as exc:
        logger.warning("advisory[%s]: fundamentals fetch failed — %s; using defaults.", symbol, exc)
        fund_dto = _default_fund_dto(symbol)
        partial_flags.append("fundamentals_unavailable")

    # ──────────────────────────────────────────────────────────────────────────
    # Step 4 — Technical indicators (ProcessingEngine)
    # ──────────────────────────────────────────────────────────────────────────
    tech: Dict[str, Any] = {}
    has_sufficient_history = (
        bars_df is not None
        and not bars_df.empty
        and len(bars_df) >= CONFIG["min_history_bars"]
    )

    if has_sufficient_history:
        try:
            pe = ProcessingEngine()
            tech_results = pe.calculate_technical_metrics({symbol: bars_df.copy()})
            tech = tech_results.get(symbol, {})
        except Exception as exc:
            logger.warning("advisory[%s]: technical metrics failed — %s", symbol, exc)
            partial_flags.append("technical_metrics_failed")

    # ──────────────────────────────────────────────────────────────────────────
    # Step 5 — GJR-GARCH volatility (TechnicalOptionsEngine)
    # ──────────────────────────────────────────────────────────────────────────
    garch_vol: Optional[float] = None
    if has_sufficient_history:
        try:
            toe = TechnicalOptionsEngine()
            garch_vol = toe.estimate_gjr_garch_volatility(bars_df.copy())
        except Exception as exc:
            logger.warning("advisory[%s]: GARCH vol failed — %s", symbol, exc)
            partial_flags.append("garch_vol_failed")

    # ──────────────────────────────────────────────────────────────────────────
    # Step 6 — Multi-horizon forecast (ForecastingEngine)
    # ──────────────────────────────────────────────────────────────────────────
    forecast_price: Optional[float] = None
    if has_sufficient_history:
        try:
            fe = ForecastingEngine()
            fc_row = pd.Series({"sector": fund_dto.sector, "Symbol": symbol})
            fc_results = fe.generate_forecast(
                row=fc_row,
                current_price=current_price,
                history_series=bars_df["Close"],
                history_df=bars_df,
            )
            raw_f30 = fc_results.get("Forecast_30", 0.0)
            forecast_price = float(raw_f30) if raw_f30 and raw_f30 > 0 else None
        except Exception as exc:
            logger.warning("advisory[%s]: forecast failed — %s", symbol, exc)
            partial_flags.append("forecast_failed")

    # ──────────────────────────────────────────────────────────────────────────
    # Step 7 — Macro regime DTO (use provided or construct a safe default)
    # ──────────────────────────────────────────────────────────────────────────
    if macro_dto is None:
        # Neutral defaults: no recession indicators, moderate VIX, RISK ON regime.
        # The advisory layer will still produce a signal; macro data can be injected
        # by the orchestrator when FRED is available.
        macro_dto = MacroEconomicDTO(
            yield_curve_10y_2y=0.50,
            high_yield_oas=3.50,
            inflation_rate=3.0,
            nominal_10y=4.5,
            vix_value=18.0,
            sahm_rule_indicator=0.0,
        )
        # Note: NOT added to partial_flags — a missing macro_dto is an
        # intentional caller choice (e.g. orchestrator has no FRED key),
        # not a data failure.  Callers that need to surface this can check
        # key_indicators for any HMM probability being NaN.

    # ──────────────────────────────────────────────────────────────────────────
    # Step 8 — StrategyEngine.evaluate_security()
    # ──────────────────────────────────────────────────────────────────────────
    strategy_out: Dict[str, Any] = {}
    raw_signal = "HOLD"
    score: int = 50
    kelly_fraction_raw: float = 0.0

    try:
        ts = transactions_store if transactions_store is not None else TransactionsStore()
        se = StrategyEngine(transactions_store=ts)
        strategy_out = se.evaluate_security(
            bar=bar_dto,
            fundamentals=fund_dto,
            macro=macro_dto,
            forecast_price=forecast_price or 0.0,
            # trend_strength: Aroon Oscillator serves as the best single-value
            # trend proxy; fall back to 50 (neutral) when unavailable.
            trend_strength=float(tech.get("Aroon Oscillator") or 50.0),
            atr=float(tech.get("ATR") or 0.0),
            macd_line=float(tech.get("MACD_Line") or 0.0),
            macd_signal=float(tech.get("MACD_Signal") or 0.0),
            aroon_osc=tech.get("Aroon Oscillator"),
            rsi=tech.get("RSI"),
            sortino_ratio=tech.get("Sortino Ratio"),
            max_drawdown=tech.get("Max Drawdown"),
            relative_strength=tech.get("RS vs SPY"),
            garch_vol=garch_vol,
            edge_ratio=tech.get("RS-MACD"),
            # Chandelier Exit from processing_engine is the long (bull) exit level.
            chandelier_long=float(tech.get("Chandelier Exit") or 0.0),
            chandelier_short=0.0,
            roc_12m=float(tech.get("ROC_12M") or 0.0),
            sma_200=float(tech.get("SMA_200") or 0.0),
            rsi_2=float(tech.get("RSI_2") or 50.0),
            sma_5=tech.get("SMA_5"),
            # Pass pre-computed universe-wide ranks so cross-sectional momentum
            # and multifactor signals score correctly (not neutral/0) when called
            # per-symbol from the advisory path.
            context_extras=context_extras,
        )
        raw_signal = strategy_out.get("Action Signal", "HOLD")
        score = int(strategy_out.get("Score", 50))
        kelly_fraction_raw = float(strategy_out.get("Kelly Target", 0.0))
    except Exception as exc:
        logger.warning("advisory[%s]: strategy engine failed — %s", symbol, exc)
        partial_flags.append("strategy_engine_failed")

    # ──────────────────────────────────────────────────────────────────────────
    # Step 8b — Macro-triggered advisory gating
    # Systemic macro risk gates applied BEFORE the holding-aware overlay so
    # risk-off conditions consistently reduce position signals for all holders.
    # The gates never escalate a signal — they only suppress or penalise.
    # Existing holders may still receive a SELL from the overlay (Case A) even
    # when a macro gate is in place; this function only blocks fresh BUYs.
    # ──────────────────────────────────────────────────────────────────────────
    macro_gate_reason: str = ""
    adjusted_score: int = score  # may be reduced by soft gate below

    if macro_dto.market_regime in ("RECESSION", "CREDIT EVENT"):
        # Hard gate: any fresh BUY recommendation is a systemic-risk signal
        # that the advisory layer refuses to issue during a crisis regime.
        if raw_signal in ("STRONG BUY", "BUY"):
            raw_signal = "HOLD"
            adjusted_score = min(adjusted_score, CONFIG["buy_score_threshold"] - 1)
        macro_gate_reason = (
            f"Macro regime is {macro_dto.market_regime}: systemic risk gate "
            f"halts fresh equity allocations."
        )
        logger.info(
            "advisory[%s]: macro hard gate — regime=%s → signal capped at HOLD",
            symbol, macro_dto.market_regime,
        )
    elif (
        macro_dto.vix > CONFIG["macro_vix_gate_threshold"]
        or macro_dto.sahm_rule_indicator >= CONFIG["macro_sahm_gate_threshold"]
    ):
        # Soft gate: elevated systemic stress → penalty on composite score.
        adjusted_score = max(0, adjusted_score - CONFIG["macro_score_penalty"])
        _vix_part = f"VIX={macro_dto.vix:.1f}" if macro_dto.vix else ""
        _sahm_part = (
            f"Sahm={macro_dto.sahm_rule_indicator:.2f}"
            if macro_dto.sahm_rule_indicator else ""
        )
        _stress_desc = ", ".join(x for x in [_vix_part, _sahm_part] if x)
        macro_gate_reason = (
            f"Systemic stress indicators elevated ({_stress_desc}) — "
            f"-{CONFIG['macro_score_penalty']}pt score penalty applied."
        )
        logger.info(
            "advisory[%s]: macro soft gate — %s, score %d → %d",
            symbol, _stress_desc, score, adjusted_score,
        )

    # Sector-specific veto: Finance / Real Estate when yield curve is inverted
    # or HY credit spreads are at systemic-crisis levels.  These sectors face
    # direct structural headwinds that override individual security signals.
    # MacroEconomicDTO stores init param yield_curve_10y_2y as self.yield_curve
    # and high_yield_oas as self.credit_spread.
    _veto_sectors_lower = {s.lower() for s in CONFIG["macro_veto_sectors"]}
    _sector_lower = (fund_dto.sector or "").lower()
    _yield_inverted = (
        macro_dto.yield_curve < CONFIG["macro_veto_yield_curve_threshold"]
    )
    _spreads_extreme = (
        macro_dto.credit_spread > CONFIG["macro_veto_oas_threshold"]
    )
    if (
        _sector_lower in _veto_sectors_lower
        and (_yield_inverted or _spreads_extreme)
        and raw_signal in ("STRONG BUY", "BUY")
    ):
        raw_signal = "HOLD"
        adjusted_score = min(adjusted_score, CONFIG["buy_score_threshold"] - 1)
        _veto_conditions: list[str] = []
        if _yield_inverted:
            _veto_conditions.append(
                f"yield curve inverted ({macro_dto.yield_curve:.2f})"
            )
        if _spreads_extreme:
            _veto_conditions.append(
                f"HY OAS={macro_dto.credit_spread:.1f}%"
            )
        _veto_reason = (
            f"{fund_dto.sector} sector vetoed: "
            f"{' and '.join(_veto_conditions)} "
            f"create structural headwinds for this sector."
        )
        macro_gate_reason = (
            f"{macro_gate_reason} {_veto_reason}".strip()
            if macro_gate_reason else _veto_reason
        )
        logger.info(
            "advisory[%s]: sector veto — %s under %s",
            symbol, fund_dto.sector, " + ".join(_veto_conditions),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Step 9 — Holding-aware overlay
    # ──────────────────────────────────────────────────────────────────────────
    is_holding = position is not None and position.quantity > 0
    unrealized_pl_pct = 0.0
    dividends_received = 0.0
    dividend_yield = fund_dto.dividend_yield or 0.0

    if is_holding:
        # Total-return cost basis: reduce raw avg cost by dividends-per-share so
        # the P&L reflects the full economic gain (price + income).
        divs_per_share = (
            position.dividends_received / position.quantity
            if position.quantity > 0
            else 0.0
        )
        effective_cost = max(0.01, position.average_cost - divs_per_share)
        unrealized_pl_pct = ((current_price - effective_cost) / effective_cost) * 100.0
        dividends_received = position.dividends_received

    # Classify forecast direction (symmetric — A3)
    is_bearish_forecast = False
    is_bullish_forecast = False
    if forecast_price is not None and forecast_price > 0 and current_price > 0:
        forecast_chg = (forecast_price - current_price) / current_price
        is_bearish_forecast = forecast_chg < CONFIG["bearish_forecast_pct_threshold"]
        is_bullish_forecast = forecast_chg > CONFIG["bullish_forecast_pct_threshold"]

    # Derive flags for holding-aware rules
    _high_yield_holder = is_holding and (
        dividend_yield >= CONFIG["dividend_yield_hold_bias_threshold"]
        or dividends_received >= CONFIG["dividend_total_received_hold_bias_usd"]
    )
    _significant_gain = (
        is_holding and unrealized_pl_pct >= CONFIG["unrealized_gain_hold_bias_pct"]
    )
    _significant_loss = (
        is_holding and unrealized_pl_pct <= CONFIG["unrealized_loss_sell_threshold_pct"]
    )

    # Map raw StrategyEngine signal → base action and conviction
    if raw_signal in ("STRONG BUY", "BUY"):
        base_action: Literal["BUY", "SELL", "HOLD"] = "BUY"
        base_conviction = (
            CONFIG["conviction_strong_buy"]
            if raw_signal == "STRONG BUY"
            else CONFIG["conviction_buy"]
        )
    elif raw_signal == "HOLD":
        base_action = "HOLD"
        base_conviction = CONFIG["conviction_hold"]
    else:  # "RISK REDUCE" or any unknown signal → SELL
        base_action = "SELL"
        base_conviction = CONFIG["conviction_sell"]

    final_action = base_action
    final_conviction = base_conviction
    holding_override_reason = ""

    if is_holding:
        # ── CASE A: Below effective cost + bearish forecast → escalate to SELL ──
        # The signal may still be neutral but the position is structurally
        # deteriorating — cut losses when the market agrees with the P&L signal.
        if _significant_loss and is_bearish_forecast:
            if final_action in ("BUY", "HOLD"):
                final_action = "SELL"
            final_conviction = max(final_conviction, CONFIG["conviction_strong_sell"])
            holding_override_reason = (
                f"Position is {unrealized_pl_pct:.1f}% below the dividend-adjusted cost "
                f"basis and the 30-day forecast implies further downside. "
                f"Cutting losses is warranted."
            )

        # ── CASE B: DIVIDEND HOLD BIAS RULE ────────────────────────────────────
        # A high-yield / high-income holder on a weak-but-non-bearish signal
        # should retain the position rather than triggering a sale or adding more
        # capital on a sub-threshold signal.
        elif _high_yield_holder and final_action in ("BUY", "HOLD"):
            # If the signal is genuinely strong (adjusted_score ≥ buy_score_threshold),
            # the BUY stands — only suppress on weak/neutral readings.
            # adjusted_score already incorporates any macro score penalty.
            if adjusted_score < CONFIG["buy_score_threshold"]:
                final_action = "HOLD"
                final_conviction = max(final_conviction, CONFIG["conviction_hold"])
                holding_override_reason = (
                    f"Forward dividend yield of {dividend_yield * 100:.1f}% "
                    f"(${dividends_received:.0f} cumulative dividends received) "
                    f"supports retaining this position on a neutral signal. "
                    f"Dividend compounding reduces effective cost basis over time."
                )

        # ── CASE C: Meaningful unrealized gain + FLAT forecast → HOLD ──────────
        # Don't pile into a winner that has already appreciated past the gain
        # threshold — hold existing exposure instead of buying at elevated prices.
        # A3: only override when the forecast is genuinely flat.  A bullish
        # forecast (confirmed continuation) keeps the BUY — the gain-capture
        # heuristic must not silence a signal the forecast independently agrees
        # with.
        elif (
            _significant_gain
            and final_action == "BUY"
            and not is_bearish_forecast
            and not is_bullish_forecast
        ):
            final_action = "HOLD"
            final_conviction = max(final_conviction, CONFIG["conviction_hold"])
            holding_override_reason = (
                f"Position is already up {unrealized_pl_pct:.1f}% on a "
                f"dividend-adjusted cost basis. Forecast is flat, not bullish; "
                f"hold existing exposure rather than adding at elevated prices."
            )

    # A3 — Bullish-forecast confirmation of a surviving BUY.  Mirror image of
    # Case A (loss + bearish → conviction_strong_sell): when the action is still
    # BUY and the independent 30-day forecast confirms upside, raise conviction
    # to the strong-buy level.
    if final_action == "BUY" and is_bullish_forecast:
        final_conviction = max(final_conviction, CONFIG["conviction_strong_buy"])

    # ──────────────────────────────────────────────────────────────────────────
    # Step 10 — Kelly-based position sizing (BUY only)
    # ──────────────────────────────────────────────────────────────────────────
    suggested_position_pct: float = 0.0
    if final_action == "BUY":
        suggested_position_pct = _compute_kelly_sizing(
            garch_vol=garch_vol,
            transactions_store=transactions_store,
            max_pct=CONFIG["max_single_position_pct"],
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Step 10b — Task 1.5: verbose-rationale pre-computation
    # Runs only when RATIONALE_VERBOSITY=verbose; a single attribute read on
    # the standard path makes the overhead immeasurable.
    # All data gathered here is passed into _build_rationale() so that function
    # remains a pure string-builder with no I/O of its own.
    # ──────────────────────────────────────────────────────────────────────────
    _verbose_win_rate: Optional[tuple] = None   # (p, b, n_trades)
    _verbose_module_docs: Dict[str, str] = {}

    if settings.RATIONALE_VERBOSITY == "verbose":
        # Win-rate calibration — reuses the transactions_store already bound
        # by _compute_kelly_sizing; pre-computing here so _build_rationale is I/O-free.
        try:
            _ts_v = transactions_store if transactions_store is not None else TransactionsStore()
            _cdf = _ts_v.closed_trades_df()
            _vp, _vb, _vn = estimate_win_rate_and_payoff(_cdf, lookback_trades=100)
            if not (math.isnan(_vp) or math.isnan(_vb)):
                _verbose_win_rate = (_vp, _vb, _vn)
        except Exception:
            pass  # CONSTRAINT #6 — calibration failure must never abort the rationale

        # Active signal-module docstrings (lazy import to avoid circular imports)
        try:
            from signals.registry import global_registry as _gr
            for _mn, _mod in _gr.get_all().items():
                if not _mod.is_active_in_regime(macro_dto):
                    continue
                _cdoc = type(_mod).__doc__ or ""
                for _dl in _cdoc.splitlines():
                    _dl = _dl.strip()
                    # Skip empty lines, the boilerplate heading, and separator lines
                    if (
                        _dl
                        and "InvestYo Quant Platform" not in _dl
                        and not set(_dl).issubset(set("=-"))
                    ):
                        _verbose_module_docs[_mn] = _dl
                        break
        except Exception:
            pass  # CONSTRAINT #6 — docstring collection must never crash the pipeline

    # A2 — When OHLCV history was missing, the technical indicators were computed
    # on a flat synthetic bar and carry no information.  Suppress them from the
    # rationale (passing None makes _build_rationale skip each driver) so the
    # explanation never cites an "RSI"/"Aroon" that was fabricated from a
    # single price point.
    synthetic_inputs = "bar_dto_synthetic" in partial_flags
    _r_rsi = None if synthetic_inputs else tech.get("RSI")
    _r_aroon = None if synthetic_inputs else tech.get("Aroon Oscillator")
    _r_rsi_2 = None if synthetic_inputs else tech.get("RSI_2")
    _r_sma_200 = None if synthetic_inputs else tech.get("SMA_200")

    # ──────────────────────────────────────────────────────────────────────────
    # Step 11 — Plain-English rationale (top 2-3 drivers in standard mode;
    # four annotated verbose sections appended when RATIONALE_VERBOSITY=verbose)
    # ──────────────────────────────────────────────────────────────────────────
    rationale = _build_rationale(
        symbol=symbol,
        action=final_action,
        score=adjusted_score,
        raw_signal=raw_signal,
        macro_regime=macro_dto.market_regime,
        forecast_price=forecast_price,
        current_price=current_price,
        unrealized_pl_pct=unrealized_pl_pct,
        dividend_yield=dividend_yield,
        dividends_received=dividends_received,
        is_holding=is_holding,
        holding_override_reason=holding_override_reason,
        rsi=_r_rsi,
        aroon_osc=_r_aroon,
        garch_vol=garch_vol,
        macro_gate_reason=macro_gate_reason,
        # ── Task 1.5 verbose-rationale additions ────────────────────────────
        hmm_risk_on_probability=macro_dto.hmm_risk_on_probability,
        vix_value=macro_dto.vix,
        sahm_rule_indicator=macro_dto.sahm_rule_indicator,
        yield_curve=macro_dto.yield_curve,
        win_rate_data=_verbose_win_rate,
        active_module_docs=_verbose_module_docs,
        strategy_explainer_notes=strategy_out.get("Strategy Explainer Notes", ""),
        rsi_2=_r_rsi_2,
        sma_200=_r_sma_200,
        sector=fund_dto.sector or "",
    )

    # ──────────────────────────────────────────────────────────────────────────
    # Step 12 — key_indicators dict
    # ──────────────────────────────────────────────────────────────────────────
    nan = float("nan")
    forecast_30d_pct = nan
    if forecast_price is not None and current_price > 0:
        forecast_30d_pct = (forecast_price - current_price) / current_price

    key_indicators: Dict[str, float] = {
        "score": float(score),
        "rsi": _safe_float(tech.get("RSI"), nan),
        "rsi_2": _safe_float(tech.get("RSI_2"), nan),
        "macd_line": _safe_float(tech.get("MACD_Line"), nan),
        "atr": _safe_float(tech.get("ATR"), nan),
        "aroon_osc": _safe_float(tech.get("Aroon Oscillator"), nan),
        "sortino": _safe_float(tech.get("Sortino Ratio"), nan),
        "max_drawdown": _safe_float(tech.get("Max Drawdown"), nan),
        "rs_vs_spy": _safe_float(tech.get("RS vs SPY"), nan),
        "garch_vol": _safe_float(garch_vol, nan),
        "forecast_30d_pct": forecast_30d_pct,
        "unrealized_pl_pct": unrealized_pl_pct,
        "dividend_yield": dividend_yield,
        "kelly_raw": kelly_fraction_raw,
    }

    # A2 — bar-derived technicals are meaningless on a synthetic flat bar; report
    # them as NaN rather than a fabricated number so consumers can't be misled.
    if synthetic_inputs:
        for _tk in ("rsi", "rsi_2", "macd_line", "atr", "aroon_osc",
                    "sortino", "max_drawdown", "rs_vs_spy"):
            key_indicators[_tk] = nan

    # ──────────────────────────────────────────────────────────────────────────
    # Step 13 — Data quality
    # ──────────────────────────────────────────────────────────────────────────
    data_quality: Literal["OK", "STALE", "PARTIAL"]
    if partial_flags:
        data_quality = "PARTIAL"
    elif is_stale:
        data_quality = "STALE"
    else:
        data_quality = "OK"

    # A1 — decay conviction to match data quality.  The base conviction reflects
    # the action/holding overlay; here we discount it for degraded inputs so the
    # number the operator sees is honest.  OK keeps ×1.0.
    if data_quality == "PARTIAL":
        final_conviction *= CONFIG["conviction_partial_multiplier"]
    elif data_quality == "STALE":
        final_conviction *= CONFIG["conviction_stale_multiplier"]

    strategy_name = _derive_strategy_name(raw_signal, score, macro_dto.market_regime, partial_flags)

    return Recommendation(
        symbol=symbol,
        action=final_action,
        strategy=strategy_name,
        conviction=round(final_conviction, 4),
        rationale=rationale,
        suggested_position_pct=round(max(0.0, suggested_position_pct), 6),
        forecast=forecast_price,
        key_indicators={k: round(v, 6) if not math.isnan(v) else v for k, v in key_indicators.items()},
        data_quality=data_quality,
        synthetic_inputs=synthetic_inputs,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fallback_hold(symbol: str, reason: str) -> Recommendation:
    """Return a safe HOLD/PARTIAL recommendation when the price is unavailable."""
    return Recommendation(
        symbol=symbol,
        action="HOLD",
        strategy="fallback_no_data",
        conviction=0.0,
        rationale=f"Advisory engine could not produce a meaningful recommendation: {reason}",
        suggested_position_pct=0.0,
        forecast=None,
        key_indicators={"score": float("nan")},
        data_quality="PARTIAL",
    )


def _synthetic_bar_dto(symbol: str, price: float) -> MarketBarDTO:
    """Build a minimal MarketBarDTO when bar history is missing."""
    return MarketBarDTO(
        date=datetime.now(),
        ticker=symbol,
        open_price=price,
        high_price=price,
        low_price=price,
        close_price=price,
        volume=0,
    )


def _default_fund_dto(symbol: str) -> FundamentalDataDTO:
    """Build a neutral FundamentalDataDTO when fundamentals cannot be fetched."""
    return FundamentalDataDTO(
        ticker=symbol,
        pe_ratio=None,
        pb_ratio=None,
        dividend_yield=0.0,
        book_value=0.0,
        eps_trailing=0.0,
        dividend_growth_rate=0.02,
        payout_ratio=0.0,
        sector="Unknown",
        company_name=symbol,
    )


def _compute_kelly_sizing(
    garch_vol: Optional[float],
    transactions_store: Optional[Any],
    max_pct: float,
) -> float:
    """Compute a BUY position size using the canonical fractional-Kelly function.

    Falls back to the volatility-target weight when trade history is
    insufficient (< 30 closed trades).  The result is clamped to
    ``[0.0, max_pct]`` at the advisory layer regardless of what the live
    execution layer's MAX_POSITION_WEIGHT permits.

    Parameters
    ----------
    garch_vol : float or None
        Annualized GJR-GARCH realized volatility for the instrument.
        Used by the vol-target fallback.
    transactions_store :
        ``TransactionsStore`` instance or None.  When None a real store
        reading the production DB is used.
    max_pct : float
        Hard ceiling from CONFIG["max_single_position_pct"].
    """
    try:
        if transactions_store is None:
            transactions_store = TransactionsStore()

        closed_df = transactions_store.closed_trades_df()
        p, b, n_trades = estimate_win_rate_and_payoff(closed_df, lookback_trades=100)

        if not (math.isnan(p) or math.isnan(b)):
            # Canonical half-Kelly path (CONSTRAINT #7: use the standardised function)
            raw = fractional_kelly(
                p, b,
                fraction=CONFIG["kelly_fraction"],
                cap=CONFIG["kelly_cap"],
            )
            if not math.isnan(raw):
                return float(max(0.0, min(max_pct, raw)))

        # Insufficient trade history — fall back to volatility targeting
        if garch_vol is not None and garch_vol > 0.0:
            raw = volatility_target_weight(
                garch_vol,
                target_vol=0.10,
                max_leverage=2.0,
            )
            return float(max(0.0, min(max_pct, raw)))

    except Exception as exc:
        logger.warning("advisory._compute_kelly_sizing failed — %s; returning 0.0", exc)

    # Cannot size — 0.0 means "recommend BUY but defer to analyst discretion"
    return 0.0


def _build_rationale(
    symbol: str,
    action: str,
    score: int,
    raw_signal: str,
    macro_regime: str,
    forecast_price: Optional[float],
    current_price: float,
    unrealized_pl_pct: float,
    dividend_yield: float,
    dividends_received: float,
    is_holding: bool,
    holding_override_reason: str,
    rsi: Optional[float],
    aroon_osc: Optional[float],
    garch_vol: Optional[float],
    macro_gate_reason: str = "",
    # ── Task 1.5 — verbose-mode additions (all optional, safe defaults) ────────
    hmm_risk_on_probability: Optional[float] = None,
    vix_value: float = 18.0,
    sahm_rule_indicator: float = 0.0,
    yield_curve: float = 0.50,
    win_rate_data: Optional[tuple] = None,      # (p, b, n_trades) or None
    active_module_docs: Optional[Dict[str, str]] = None,
    strategy_explainer_notes: str = "",         # from StrategyEngine (informational)
    rsi_2: Optional[float] = None,
    sma_200: Optional[float] = None,
    sector: str = "",
) -> str:
    """Build a plain-English rationale for the advisory recommendation.

    Standard mode (``RATIONALE_VERBOSITY=standard``, the default):
        One paragraph citing the top 2-3 drivers — composite score,
        30-day forecast direction, and the most decisive holding-aware
        condition (if the symbol is held).

    Verbose mode (``RATIONALE_VERBOSITY=verbose``):
        The standard paragraph PLUS four annotated sections:

        ``[A] Regime context`` — HMM probability and FRED macro snapshot
        so an analyst can immediately understand whether the defensive filters
        are active or bypassed and why.

        ``[B] Historical calibration`` — strategy win-rate and Kelly edge
        estimate derived from closed trades in ``TransactionsStore``, so
        position-sizing conviction is grounded in a track record rather than
        a single-cycle signal.

        ``[C] Signal invalidation thresholds`` — explicit "flip points" that
        would void the current recommendation: RSI reversal levels, score
        breakdowns, macro gate triggers, and sector-veto conditions.

        ``[D] Indicator theory notes`` — first-line ``__doc__`` of each
        active signal module pulled dynamically via ``signals.registry``,
        providing the theoretical basis of each contributing model.

    When a macro gate overrode the signal, ``macro_gate_reason`` is
    prepended so the operator understands why a bullish individual signal
    resulted in a HOLD before reading anything else.
    """
    # ─────────────────────────────────────────────────────────────────────────
    # STANDARD PARAGRAPH — identical to pre-1.5 behaviour
    # ─────────────────────────────────────────────────────────────────────────
    drivers: list[str] = []

    # Driver 0 — macro gate (prepended when active so it is the first thing
    # the operator reads, not buried after the technical score).
    if macro_gate_reason:
        drivers.append(macro_gate_reason)

    # Driver 1 — composite signal score
    score_descriptor = "neutral"
    if score >= CONFIG["strong_buy_score_threshold"]:
        score_descriptor = "strongly bullish"
    elif score >= CONFIG["buy_score_threshold"]:
        score_descriptor = "moderately bullish"
    elif score < CONFIG["sell_score_threshold"]:
        score_descriptor = "bearish"
    drivers.append(
        f"The multi-signal composite score is {score}/100 ({score_descriptor}; "
        f"regime: {macro_regime})"
    )

    # Driver 2 — forecast / momentum
    if forecast_price is not None and forecast_price > 0 and current_price > 0:
        chg_pct = (forecast_price - current_price) / current_price * 100.0
        direction = "upside" if chg_pct >= 0 else "downside"
        drivers.append(
            f"the 30-day blended forecast implies {abs(chg_pct):.1f}% {direction} "
            f"(target ${forecast_price:.2f} vs current ${current_price:.2f})"
        )
    elif rsi is not None:
        rsi_desc = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
        drivers.append(f"RSI({rsi:.0f}) is {rsi_desc}")

    # Driver 3 — holding context or momentum confirmation
    if is_holding:
        if holding_override_reason:
            # The override already contains the key insight — use a condensed form.
            drivers.append(holding_override_reason)
        elif abs(unrealized_pl_pct) >= 2.0:
            gain_loss = "gain" if unrealized_pl_pct >= 0 else "loss"
            drivers.append(
                f"existing position shows a {abs(unrealized_pl_pct):.1f}% unrealised "
                f"{gain_loss} on a dividend-adjusted cost basis "
                f"(${dividends_received:.0f} cumulative dividends received)"
            )
        if dividend_yield >= CONFIG["dividend_yield_hold_bias_threshold"] and not holding_override_reason:
            drivers.append(
                f"forward dividend yield of {dividend_yield * 100:.1f}% provides ongoing income"
            )
    else:
        if aroon_osc is not None:
            trend_desc = (
                "strong uptrend" if aroon_osc > 50
                else "downtrend" if aroon_osc < -50
                else "choppy/neutral trend"
            )
            drivers.append(f"Aroon oscillator ({aroon_osc:.0f}) indicates a {trend_desc}")
        elif garch_vol is not None:
            vol_desc = "elevated" if garch_vol > 0.30 else "moderate" if garch_vol > 0.15 else "low"
            drivers.append(f"GARCH vol of {garch_vol * 100:.1f}% is {vol_desc}")

    # Assemble the standard one-paragraph rationale
    drivers_text = "; ".join(drivers[:3])  # cap at 3 for readability
    action_phrase = {
        "BUY":  "accumulate a new position",
        "SELL": "exit or reduce the position",
        "HOLD": "maintain existing exposure without adding capital",
    }.get(action, action)

    standard_para = (
        f"{symbol}: {action_phrase.capitalize()}. "
        f"{drivers_text.rstrip('.')}. "
        f"(Raw strategy signal: {raw_signal}.)"
    )

    # ─────────────────────────────────────────────────────────────────────────
    # VERBOSE SECTIONS (appended only when RATIONALE_VERBOSITY=verbose)
    # Each section is labelled [A]–[D] so compliance reviewers can cite them.
    # ─────────────────────────────────────────────────────────────────────────
    if settings.RATIONALE_VERBOSITY != "verbose":
        return standard_para

    verbose_parts: list[str] = []

    # ── [A] Regime Context ────────────────────────────────────────────────────
    # Explains whether the HMM and rules-based regime agree and surfaces the
    # key macro variables that drive or suppress the risk filters.
    if hmm_risk_on_probability is not None:
        if hmm_risk_on_probability >= 0.70:
            hmm_desc = f"HMM strongly confirms risk-on (p={hmm_risk_on_probability:.2f})"
        elif hmm_risk_on_probability >= 0.30:
            hmm_desc = f"HMM is uncertain (p={hmm_risk_on_probability:.2f})"
        else:
            hmm_desc = (
                f"HMM signals elevated risk-off pressure (p={hmm_risk_on_probability:.2f})"
                f" — RISK ON classification may be fleeting"
            )
    else:
        hmm_desc = "HMM regime estimate unavailable (insufficient FRED history or first run)"

    verbose_parts.append(
        f"[A] Regime context: {macro_regime} — {hmm_desc}. "
        f"VIX={vix_value:.1f}, Sahm Rule={sahm_rule_indicator:.2f}, "
        f"10y-2y spread={yield_curve:+.2f}."
    )

    # ── [B] Historical Calibration ────────────────────────────────────────────
    # Grounds position sizing in the strategy's actual closed-trade track record
    # so the operator can distinguish a high-conviction edge from a cold start.
    if win_rate_data is not None:
        _p, _b, _n = win_rate_data
        _edge = _p * _b - (1.0 - _p)
        _edge_desc = "positive — edge exists" if _edge > 0 else "negative — edge absent"
        verbose_parts.append(
            f"[B] Calibration: This multi-signal setup has shown a {_p * 100:.0f}% win rate "
            f"over {_n} closed trades (payoff ratio {_b:.1f}:1; "
            f"Kelly edge {_edge:.2f} — {_edge_desc})."
        )
    else:
        verbose_parts.append(
            "[B] Calibration: Insufficient closed-trade history (< 30 trades); "
            "position sizing defaults to volatility targeting."
        )

    # ── [C] Signal Invalidation Thresholds ───────────────────────────────────
    # Defines the explicit 'flip points' that would void or reverse the current
    # recommendation — essential for compliance review and stop-loss logic.
    _void: list[str] = []

    # Score-based action flip
    if action in ("BUY", "HOLD"):
        _void.append(
            f"score drop below {CONFIG['sell_score_threshold']} converts signal to RISK REDUCE"
        )
    else:
        _void.append(
            f"score recovery above {CONFIG['buy_score_threshold']} warrants re-evaluation"
        )

    # RSI mean-reversion void for oversold BUY entries
    if rsi is not None and rsi < 30 and action == "BUY":
        _rsi_exit = CONFIG["rsi_mean_reversion_exit_level"]
        _void.append(
            f"RSI rising above {_rsi_exit} (currently {rsi:.0f}) voids the oversold entry"
        )

    # RSI-2 void for ultra-short mean-reversion entries
    if rsi_2 is not None and rsi_2 < 10 and action == "BUY":
        _rsi2_exit = CONFIG["rsi_2_mean_reversion_exit_level"]
        _void.append(
            f"RSI(2) recovery above {_rsi2_exit} (currently {rsi_2:.0f}) voids the "
            f"ultra-oversold mean-reversion entry"
        )

    # Macro soft-gate flip points (always shown — operator must know the tripwires)
    _void.append(
        f"VIX > {CONFIG['macro_vix_gate_threshold']:.0f} or "
        f"Sahm Rule ≥ {CONFIG['macro_sahm_gate_threshold']:.1f} applies a "
        f"−{CONFIG['macro_score_penalty']}pt macro penalty"
    )

    # Sector-veto flip point (only surfaced for the affected sectors)
    _sector_lower = sector.lower()
    _veto_lower = {s.lower() for s in CONFIG.get("macro_veto_sectors", [])}
    if _sector_lower in _veto_lower:
        _void.append(
            f"yield curve inversion < 0 with HY OAS > "
            f"{CONFIG['macro_veto_oas_threshold']:.0f}% blocks fresh BUYs "
            f"in {sector or 'this'} sector"
        )

    # SMA-200 trend-filter break
    if sma_200 is not None and sma_200 > 0:
        _void.append(f"close below SMA-200 (${sma_200:.2f}) invalidates the uptrend filter")

    verbose_parts.append(
        "[C] Invalidation: " + "; ".join(_void) + "."
    )

    # ── [D] Active Indicator Theory Notes ────────────────────────────────────
    # Dynamically pulls the first-line __doc__ of each regime-active signal
    # module from signals.registry so the rationale is self-documenting.
    # Capped at 4 entries to remain readable; pre-filtered in evaluate().
    _mods = active_module_docs or {}
    _theory_items: list[str] = []
    for _mname, _mdoc in list(_mods.items())[:4]:
        _display = _mname.replace("_", " ").title()
        _theory_items.append(f"{_display}: {_mdoc}")
    if _theory_items:
        verbose_parts.append(
            "[D] Indicator notes: " + "; ".join(_theory_items) + "."
        )

    return f"{standard_para}\n\n" + "\n".join(verbose_parts)


def _derive_strategy_name(
    raw_signal: str,
    score: int,
    macro_regime: str,
    partial_flags: list[str],
) -> str:
    """Describe the primary driver in a short human-readable strategy label."""
    if partial_flags and "strategy_engine_failed" in partial_flags:
        return "fallback_no_strategy"
    if score >= CONFIG["strong_buy_score_threshold"]:
        base = "high-conviction multi-signal composite"
    elif score >= CONFIG["buy_score_threshold"]:
        base = "multi-signal composite"
    else:
        base = "risk-reduction signal"
    return f"{base} [{macro_regime}]"


def _safe_float(value: Any, default: float) -> float:
    """Safely coerce a value to float; return ``default`` on failure or None."""
    if value is None:
        return default
    try:
        f = float(value)
        return f
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Tier 9 — Claude analyst-rationale enrichment (on-demand only)
# ---------------------------------------------------------------------------
# This function is the ONLY callsite that may invoke an LLM from inside
# engine/advisory.py.  It is deliberately a sibling of ``evaluate()`` rather
# than a step inside it: the plan picked on-demand-only cadence, so every
# per-cycle ``evaluate()`` call must stay byte-identical to pre-Tier-9
# behaviour.  Operators reach this function via the CLI
# (``python -m engine.llm_commentary SYMBOL``) or a future GUI button.
#
# Soft-fail contract (CONSTRAINT #6): on any failure — LLM disabled, missing
# key, provider exception, schema mismatch — return the ORIGINAL recommendation
# unchanged.  The deterministic ``rec.rationale`` template text is never
# overwritten, only enriched alongside it via ``llm_rationale``.
#
# No-fabrication contract (CONSTRAINT #4): the LLM output flows ONLY into
# ``rec.llm_rationale`` (an Optional[Dict[str, Any]]).  It never touches
# ``score``, ``conviction``, ``suggested_position_pct``, ``forecast``,
# ``key_indicators``, or any numeric pipeline scalar.
#
# Tier 9 Scope 4 (Opal): the SAME contract extends to ``rec.research_brief``
# — Opal's grounded research brief is independently opt-in
# (``settings.OPAL_RESEARCH_ENABLED``) and, when generated, is threaded into
# ``context["research_brief"]`` BEFORE the Claude rationale call runs, so
# Claude's own synthesis can cite it.  Opal's failure never blocks Claude's
# call and vice versa — each is independently soft-fail (CONSTRAINT #6).

def enrich_with_llm_rationale(
    rec: Recommendation,
    context: Optional[Dict[str, Any]] = None,
    *,
    run_opal: bool = False,
) -> Recommendation:
    """Return ``rec`` with ``llm_rationale`` / ``research_brief`` populated.

    Parameters
    ----------
    rec :
        A deterministic recommendation produced by :func:`evaluate`.
    context :
        Optional extra payload to forward to the LLM commentary layer
        (macro snippet, regime DTO snapshot, etc.).  Never mutated in
        place — a local copy is used so Opal's injected
        ``"research_brief"`` key never leaks back into the caller's dict.
        A caller MAY pre-populate ``context["research_brief"]`` with an
        already-generated Opal brief (e.g. one cached from the GUI's
        dedicated Opal button); it is then threaded into Claude's prompt
        AND surfaced on the returned rec WITHOUT any new OpenAI call.
    run_opal :
        When ``True`` AND ``settings.OPAL_RESEARCH_ENABLED`` is on, generate
        a FRESH Opal research brief (an OpenAI call) before the Claude call.
        Defaults to ``False`` (Tier 9 Scope 4 decoupling): a plain Claude
        rationale request — the Reports/AI-Insights "Claude analyst note"
        button, the ``engine.llm_commentary`` CLI — must NOT incur a
        surprise OpenAI cost. Those surfaces instead reuse a
        caller-supplied ``context["research_brief"]`` (free) when present.

    Returns
    -------
    Recommendation
        ``rec`` unchanged when nothing was produced, or a new instance with
        ``llm_rationale`` and/or ``research_brief`` populated from whichever
        succeeded — either field independently.

    Notes
    -----
    The frozen dataclass is rebuilt via :func:`dataclasses.replace` so the
    immutability invariant holds.  ``llm.research.generate_research_brief``
    and ``llm.commentary.generate_analyst_rationale`` are each responsible
    for their own caching, schema validation, and soft-fail; this function
    is a thin orchestration layer that sequences Opal BEFORE Claude (so its
    output can enrich Claude's prompt) without letting either's failure
    affect the other.  Every mutation of ``rec`` (including the final
    :func:`dataclasses.replace` calls) is wrapped so the function NEVER
    raises — direct callers such as the ``engine.llm_commentary`` CLI rely
    on this "exit 0 on soft-fail" guarantee (CONSTRAINT #6).
    """
    from dataclasses import replace  # noqa: PLC0415 — stdlib, no SDK-lazy concern

    working_context: Dict[str, Any] = dict(context or {})
    research_brief_dict: Optional[Dict[str, Any]] = None

    # A caller may hand us an already-generated brief (GUI reuse path) — surface
    # it on the returned rec too, without a new OpenAI call.
    _supplied = working_context.get("research_brief")
    if isinstance(_supplied, dict) and _supplied:
        research_brief_dict = _supplied

    # Tier 9 Scope 4 — Opal runs FIRST (front-of-pipeline research), but ONLY
    # on an explicit opt-in (run_opal=True); its success/failure is entirely
    # independent of the Claude call below.
    if run_opal:
        try:
            if getattr(settings, "OPAL_RESEARCH_ENABLED", False):
                # Lazy import: keeps engine/advisory.py free of any LLM/SDK
                # reach at module-load time. Gravity step_74/77 source-grep
                # for top-level imports.
                from llm.research import generate_research_brief  # noqa: PLC0415

                brief = generate_research_brief(rec.symbol, working_context)
                if brief is not None:
                    research_brief_dict = brief.model_dump()
                    working_context["research_brief"] = research_brief_dict
        except Exception as exc:
            logger.warning(
                "enrich_with_llm_rationale: Opal research brief soft-failed for %s: %s",
                getattr(rec, "symbol", "?"),
                exc,
            )

    llm_rationale_dict: Optional[Dict[str, Any]] = None
    try:
        if getattr(settings, "LLM_COMMENTARY_ENABLED", False):
            # Lazy import: see note above.
            from dataclasses import asdict  # noqa: PLC0415
            from llm.commentary import generate_analyst_rationale  # noqa: PLC0415

            rec_skeleton = asdict(rec)
            result = generate_analyst_rationale(rec_skeleton, working_context)
            if result is not None:
                llm_rationale_dict = result.model_dump()
    except Exception as exc:
        logger.warning(
            "enrich_with_llm_rationale soft-failed for %s: %s — returning template-only rec.",
            getattr(rec, "symbol", "?"),
            exc,
        )

    # Apply each field independently, each guarded so a replace() failure
    # (e.g. a future Recommendation refactor) never propagates — the "never
    # raises" contract holds for the whole function body (Fix 7 / CONSTRAINT #6).
    if research_brief_dict is not None and research_brief_dict is not rec.research_brief:
        try:
            rec = replace(rec, research_brief=research_brief_dict)
        except Exception as exc:
            logger.warning(
                "enrich_with_llm_rationale: research_brief replace() failed for %s: %s",
                getattr(rec, "symbol", "?"),
                exc,
            )
    if llm_rationale_dict is not None:
        try:
            rec = replace(rec, llm_rationale=llm_rationale_dict)
        except Exception as exc:
            logger.warning(
                "enrich_with_llm_rationale: llm_rationale replace() failed for %s: %s",
                getattr(rec, "symbol", "?"),
                exc,
            )
    return rec

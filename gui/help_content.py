"""
gui/help_content.py
===================
Static help-content store for the InvestYo Command Center.

**Zero Streamlit imports.**  All content is defined here so GUI panels can
import exactly what they need without touching the rendering layer.  Every
lookup function returns a safe empty value for unknown keys — it never raises.

All threshold values are imported from ``settings``, ``validation.thresholds``,
and ``engine.advisory.CONFIG`` so the explanations automatically stay in sync
with the live configuration.  Never hard-code numeric thresholds here.

Exported public API
-------------------
``GlossaryEntry``
    Frozen dataclass: ``term``, ``plain_english``, ``guide_anchor``.
``TabHelp``
    Frozen dataclass: ``tab_id``, ``title``, ``description``,
    ``key_concepts`` (tuple of glossary term keys), ``guide_anchor``.

``GLOSSARY``
    ``Dict[str, GlossaryEntry]`` keyed by lower-cased term.
``TAB_HELP``
    ``Dict[str, TabHelp]`` keyed by the 14 Command Center tab IDs.
``SECTION_HELP``
    ``Dict[str, str]`` — one-sentence tooltip per named panel section.
``METRIC_HELP``
    ``Dict[str, str]`` — one-sentence tooltip per named column / metric.

``get_tab_help(tab_id) -> Optional[TabHelp]``
``get_glossary(term)   -> Optional[GlossaryEntry]``
``metric_help(key)     -> str``   (empty string for unknown keys)
``search_glossary(query) -> List[GlossaryEntry]``
``guide_url(anchor)    -> str``   (empty string when anchor is ``None`` / empty)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Live threshold imports — do NOT replace these with hard-coded literals.
from engine.advisory import CONFIG as _ADVISORY_CONFIG
from settings import settings
from validation.thresholds import (
    DSR_MIN,
    MAX_DRAWDOWN_MAX,
    NET_SHARPE_MIN,
    PBO_MAX,
    STRESS_MAX_DRAWDOWN,
)
from gui.robinhood_execution_panel import STALE_QUEUE_SECONDS as _RH_QUEUE_STALE_SECONDS

logger = logging.getLogger(__name__)

# Base path for guide links.  Anchors are appended verbatim.
_GUIDE_PATH = "docs/HOW_TO_GUIDE.md"

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlossaryEntry:
    """One glossary term — plain English for a non-quant operator.

    Attributes
    ----------
    term : str
        Display name (title-cased).
    plain_english : str
        One-to-three sentence explanation a non-quant can understand.
        Must not embed raw numeric thresholds — reference live values instead.
    guide_anchor : str or None
        GitHub-style heading slug from ``docs/HOW_TO_GUIDE.md`` (includes
        the leading ``#``).  ``None`` when no dedicated section exists yet.
    """

    term: str
    plain_english: str
    guide_anchor: Optional[str] = None


@dataclass(frozen=True)
class TabHelp:
    """Help text for one Command Center tab.

    Attributes
    ----------
    tab_id : str
        Identifier matching the key used in ``TAB_HELP``.
    title : str
        Human-readable tab name.
    description : str
        Two-to-four sentence plain-English description of the tab's purpose.
        Must reinforce that this is an **informational** tool — no orders are
        sent from the GUI.
    key_concepts : tuple[str, ...]
        Tuple of lower-cased glossary keys that are most relevant here.
    guide_anchor : str or None
        GitHub-style heading slug from ``docs/HOW_TO_GUIDE.md`` (includes
        the leading ``#``).
    """

    tab_id: str
    title: str
    description: str
    key_concepts: Tuple[str, ...] = ()
    guide_anchor: Optional[str] = None


# ---------------------------------------------------------------------------
# Convenience helpers (used by every dict below)
# ---------------------------------------------------------------------------

def _g(
    term: str,
    plain_english: str,
    guide_anchor: Optional[str] = None,
) -> GlossaryEntry:
    return GlossaryEntry(term=term, plain_english=plain_english, guide_anchor=guide_anchor)


def _t(
    tab_id: str,
    title: str,
    description: str,
    key_concepts: Tuple[str, ...] = (),
    guide_anchor: Optional[str] = None,
) -> TabHelp:
    return TabHelp(
        tab_id=tab_id,
        title=title,
        description=description,
        key_concepts=key_concepts,
        guide_anchor=guide_anchor,
    )


# ---------------------------------------------------------------------------
# GLOSSARY — keyed by lower-cased term
# ---------------------------------------------------------------------------
# Every value in this section uses live imported constants rather than hard-coded
# numbers.  f-strings are evaluated once at module-import time; if you need
# dynamic values, use a function instead of a module-level dict entry.

_VIX_THRESH = int(_ADVISORY_CONFIG["macro_vix_gate_threshold"])
_SAHM_THRESH = _ADVISORY_CONFIG["macro_sahm_gate_threshold"]
_KELLY_CAP_PCT = int(settings.KELLY_CAP * 100)
_KELLY_FRACTION = settings.KELLY_FRACTION
_CONV_DELTA = settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD
_RH_QUEUE_STALE_MIN = int(_RH_QUEUE_STALE_SECONDS // 60)
_RH_MAX_NOTIONAL = settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER
_PROGRESS_POLL_SECONDS = settings.PROGRESS_POLL_SECONDS
# Retrain window (days) used by the Analytics ML-model-monitoring section to
# flag a stale model. No dedicated setting exists, so this mirrors the default
# ml.meta_labeling.MetaLabeler(retrain_freq_days=30) cadence and the LGBM ranker's
# monthly retraining job. Imported by gui/panels/analytics.py so the panel and its
# help text stay driven by one source rather than two re-typed literals.
MODEL_RETRAIN_WINDOW_DAYS = 30

GLOSSARY: Dict[str, GlossaryEntry] = {
    # ── Action signals ────────────────────────────────────────────────────────
    "action signal": _g(
        "Action Signal",
        "The system's recommendation for each ticker: STRONG BUY, BUY, HOLD, "
        "RISK REDUCE, or AVOID.  "
        "This is purely informational — the platform is in advisory mode and "
        "does not send orders automatically.",
        "#7-reading-the-action-signals",
    ),
    "strong buy": _g(
        "Strong Buy",
        "The highest-conviction long recommendation.  Macro conditions, "
        "technicals, and fundamentals all point upward.  Still informational — "
        "no order is placed on your behalf.",
        "#7-reading-the-action-signals",
    ),
    "buy": _g(
        "Buy",
        "A long recommendation where conditions are favorable but not at "
        "maximum conviction.  Always informational — act on your own judgment.",
        "#7-reading-the-action-signals",
    ),
    "hold": _g(
        "Hold",
        "Keep an existing position; do not add.  May be forced by a macro "
        "gate (e.g. RECESSION regime) even when underlying signals are bullish.",
        "#7-reading-the-action-signals",
    ),
    "risk reduce": _g(
        "Risk Reduce",
        "Conditions are deteriorating.  Consider trimming the position and "
        "tightening your stop to the level shown in the Sell Zone.",
        "#7-reading-the-action-signals",
    ),
    "avoid": _g(
        "Avoid",
        "Do not open or add to a position.  If you already hold the stock, "
        "consider exiting based on your own risk tolerance.",
        "#7-reading-the-action-signals",
    ),

    # ── Sizing & Kelly ────────────────────────────────────────────────────────
    "kelly target": _g(
        "Kelly Target",
        f"The suggested fraction of your total capital to put into one position, "
        f"derived from the fractional Kelly formula using your real trade history.  "
        f"The formula's raw result is capped at {_KELLY_CAP_PCT}% "
        f"(KELLY_CAP) and then further capped by a per-name advisory ceiling.  "
        f"A value of 0.14 means 'up to 14% of your capital' — still advisory only.",
        "#8-understanding-position-sizing-kelly-target",
    ),
    "kelly fraction": _g(
        "Kelly Fraction",
        f"A safety multiplier applied to the raw Kelly bet.  The platform uses "
        f"{_KELLY_FRACTION} (half-Kelly), which cuts the theoretical bet in "
        f"half to reduce the risk of ruin from estimation errors in win-rate "
        f"and payoff calculations.",
        "#8-understanding-position-sizing-kelly-target",
    ),
    "vol-target fallback": _g(
        "Vol-Target Fallback",
        "When fewer than 30 closed trades exist in the database for a strategy, "
        "the Kelly formula can't be estimated reliably.  The platform falls back "
        "to sizing by volatility: target_vol ÷ realized_vol.  A stock with twice "
        "the target volatility gets half the weight.  Logged explicitly every time.",
        "#8-understanding-position-sizing-kelly-target",
    ),
    "hmm regime multiplier": _g(
        "HMM Regime Multiplier",
        "The Hidden Markov Model's probability that we are in a risk-on environment "
        "(0 to 1).  This number is multiplied by the Kelly Target, so bearish HMM "
        "readings shrink position-size suggestions proportionally.  When the HMM "
        "can't run, the multiplier defaults to 1.0 (no effect).",
        "#8-understanding-position-sizing-kelly-target",
    ),

    # ── Conviction & calibration ──────────────────────────────────────────────
    "conviction": _g(
        "Conviction",
        "A score between 0 and 1 indicating how confident the system is in its "
        "recommendation.  Higher conviction combines strong signal alignment, "
        "favorable macro, and a positive track record.  "
        "A conviction of 0.80 is NOT a promise of an 80% win rate — it reflects "
        "the system's certainty, which the Calibration chart helps you verify.",
        "#conviction-calibration-reports-tab",
    ),
    "conviction delta": _g(
        "Conviction Delta",
        f"The change in conviction between two pipeline runs for the same ticker.  "
        f"Moves of ≥ {_CONV_DELTA} are highlighted in the 'Δ Since Last Run' band "
        f"at the top of the HTML report so you can quickly spot meaningful shifts "
        f"without reading every row.",
        "#6-understanding-the-output",
    ),
    "calibration": _g(
        "Calibration",
        "A reliability check that asks: 'When the system says conviction 0.80, "
        "does it actually win 80% of the time?'  The Reports tab shows a chart "
        "comparing stated conviction to empirical win rate per bin.  Bars above "
        "the diagonal = underconfident; below = overconfident.",
        "#conviction-calibration-reports-tab",
    ),

    # ── Macro regime ──────────────────────────────────────────────────────────
    "macro regime": _g(
        "Macro Regime",
        "The system's assessment of the broad economic environment: RISK ON, "
        "NEUTRAL, RECESSION, or CREDIT EVENT.  Derived from the yield curve, "
        "high-yield credit spreads, the Sahm Rule, and VIX.  The regime gates "
        "every signal — a BUY in a RECESSION regime is forced to HOLD.",
        "#9-the-macro-regime-system",
    ),
    "risk on": _g(
        "Risk On",
        "The most favorable macro regime.  Yield curve is not inverted, credit "
        "spreads are low, and the Sahm Rule is benign.  Signals run at full "
        "strength.",
        "#the-four-regimes",
    ),
    "neutral": _g(
        "Neutral",
        "A cautious macro regime — mild deterioration, or the HMM disagrees with "
        "a RISK ON reading from the rules-based model.  Signals are still active "
        "but the HMM multiplier may reduce sizing.",
        "#the-four-regimes",
    ),
    "recession": _g(
        "Recession",
        "A deteriorating macro regime triggered by an inverted yield curve plus "
        "elevated credit spreads or Sahm Rule.  All BUY and STRONG BUY signals "
        "are forced to HOLD.  The kill switch may also activate.",
        "#the-four-regimes",
    ),
    "credit event": _g(
        "Credit Event",
        "The most severe macro regime — high-yield credit spreads above 6%.  "
        "Identical effect to RECESSION: BUYs become HOLDs and the kill switch "
        "may fire.",
        "#the-four-regimes",
    ),
    "vix": _g(
        "VIX",
        f"The CBOE Volatility Index — often called the 'fear gauge'.  "
        f"When VIX rises above {_VIX_THRESH}, the platform applies a soft macro "
        f"penalty to all BUY signals.  Above this threshold AND combined with "
        f"a Sahm reading ≥ {_SAHM_THRESH}, the kill switch can also activate.",
        "#9-the-macro-regime-system",
    ),
    "sahm rule": _g(
        "Sahm Rule",
        f"A recession indicator created by economist Claudia Sahm.  It fires when "
        f"the 3-month average of the US unemployment rate rises ≥ 0.5 percentage "
        f"points above its 12-month low (threshold: {_SAHM_THRESH}).  "
        f"When the Sahm Rule is ≥ {_SAHM_THRESH}, the advisory engine applies a "
        f"soft score penalty to all BUY signals.",
        "#9-the-macro-regime-system",
    ),
    "hmm": _g(
        "HMM (Hidden Markov Model)",
        "A statistical model that runs in the background to give a second opinion "
        "on the macro regime.  It analyzes SPY returns, realized volatility, VIX, "
        "and the yield curve to produce a 'risk-on probability' from 0 to 1.  "
        "Below 0.30, it can quietly downgrade the regime from RISK ON to NEUTRAL.  "
        "If the HMM can't run, the platform uses only the rules-based regime — "
        "no crash, no degradation.",
        "#the-hmm-second-opinion",
    ),
    "yield curve": _g(
        "Yield Curve",
        "The difference between the 10-year US Treasury yield and the 2-year yield "
        "(FRED series T10Y2Y).  When the 2-year yields more than the 10-year "
        "(negative spread = inverted), it historically signals an upcoming recession.  "
        "The platform uses it as a key input for the macro regime and sector vetoes.",
        "#9-the-macro-regime-system",
    ),

    # ── Technical indicators ──────────────────────────────────────────────────
    "rsi": _g(
        "RSI (Relative Strength Index)",
        "A momentum oscillator that measures how overbought or oversold a stock "
        "is, on a scale of 0–100.  Above 70 = overbought (potential sell signal), "
        "below 30 = oversold (potential buy signal).  The platform uses RSI(14) "
        "for general momentum and RSI(2) for ultra-short mean reversion.",
        "#7-reading-the-action-signals",
    ),
    "macd": _g(
        "MACD",
        "Moving Average Convergence/Divergence — a trend-following momentum "
        "indicator built from two exponential moving averages.  A bullish 'MACD "
        "crossover' occurs when the MACD line crosses above its signal line, "
        "suggesting upward momentum.  The platform's Aroon filter suppresses "
        "MACD signals during choppy, trendless markets.",
        "#7-reading-the-action-signals",
    ),
    "aroon oscillator": _g(
        "Aroon Oscillator",
        "An indicator that measures how recently a stock hit a new high or low.  "
        "Values near +100 signal a strong uptrend; near −100 signal a downtrend.  "
        "The platform uses Aroon as a 'chop filter' to avoid acting on MACD signals "
        "when the market has no clear direction.",
        "#7-reading-the-action-signals",
    ),
    "atr": _g(
        "ATR (Average True Range)",
        "A measure of a stock's average daily price swing, in dollars.  "
        "Used to set the Buy Zone width (how far below the current price is a "
        "good entry), the Sell Zone width (profit target), and trailing stops.  "
        "A stock with a $3 ATR moves $3 on an average day.",
        "#price-ranges",
    ),
    "garch vol": _g(
        "GARCH Vol (GJR-GARCH)",
        "A sophisticated volatility estimate that lets today's volatility depend "
        "more heavily on recent bad days than recent good days (the 'leverage "
        "effect').  More accurate than a simple moving standard deviation.  "
        "The platform uses it as the primary vol estimate for position sizing "
        "and options premium evaluation.",
        "#8-understanding-position-sizing-kelly-target",
    ),
    "chandelier exit": _g(
        "Chandelier Exit",
        "A trailing stop-loss strategy that ratchets upward as a stock rises, "
        "set at a fixed multiple of ATR below the highest close in a lookback "
        "window.  The platform shows this as the lower bound of the Sell Zone.",
        "#price-ranges",
    ),
    "buy zone": _g(
        "Buy Zone",
        "The price range where the system considers entry attractive: roughly "
        "current price ± ATR-based cushion.  Buying inside this range gives you "
        "a better risk/reward than chasing a breakout.  Always informational.",
        "#price-ranges",
    ),
    "sell zone": _g(
        "Sell Zone",
        "The upside price target range (profit zone) plus the trailing stop.  "
        "For RISK REDUCE / unknown signals it collapses to 'Sell Now @ market | "
        "Stop @ [Chandelier Exit]'.  Always informational — no orders are sent.",
        "#price-ranges",
    ),
    "graham number": _g(
        "Graham Number",
        "Benjamin Graham's formula for estimating the maximum fair price of a "
        "stock: sqrt(22.5 × EPS × Book Value per Share).  If the current price "
        "is below this number, the stock may be undervalued by classic value "
        "criteria.  The platform uses it as one input in the multi-signal score.",
        "#6-understanding-the-output",
    ),

    # ── Options ───────────────────────────────────────────────────────────────
    "iv rank": _g(
        "IV Rank (IVR)",
        "Implied Volatility Rank — where the current implied volatility sits "
        "relative to the past year's range.  An IVR of 80 means IV is in the "
        "top 20% of the past year — historically a good time to sell options "
        "premium.  The platform requires IVR > 50 before suggesting credit spreads.",
        "#7-reading-the-action-signals",
    ),
    "vrp": _g(
        "VRP (Volatility Risk Premium)",
        "The excess of implied volatility over realized volatility.  When IV "
        "charges more than the stock actually moves, there is a premium to collect "
        "by selling options.  A VRP > 0.02 is required before the platform "
        "recommends a premium-selling options strategy.",
        "#7-reading-the-action-signals",
    ),
    "put credit spread": _g(
        "Put Credit Spread",
        "An options strategy that sells a put at one strike and buys a protective "
        "put at a lower strike.  Collects premium if the stock stays above the "
        "short put.  Max loss is limited to the spread width minus premium received.  "
        "The platform recommends this when IVR, VRP, and macro conditions are all "
        "favorable.  **Advisory only — no orders are sent.**",
        "#7-reading-the-action-signals",
    ),
    "iron condor": _g(
        "Iron Condor",
        "An options strategy that combines a put credit spread (below the market) "
        "and a call credit spread (above the market), profiting if the stock "
        "stays within a defined range until expiry.  Requires favorable IV "
        "conditions.  **Advisory only — no orders are sent.**",
        "#7-reading-the-action-signals",
    ),
    "black-scholes greeks": _g(
        "Black-Scholes Greeks",
        "Sensitivities of an options price to market factors: Delta (price change "
        "per $1 move in the stock), Gamma (rate of Delta change), Vega (change per "
        "1% vol move), and Theta (daily time decay).  The Options tab shows these "
        "for the at-the-money strike at 30 days to expiry.",
        "#7-reading-the-action-signals",
    ),

    # ── Validation & deployment ───────────────────────────────────────────────
    "pbo": _g(
        "PBO (Probability of Backtest Overfitting)",
        f"A rigorous measure of how likely a strategy's backtest performance is "
        f"due to luck rather than real edge.  Computed via Combinatorial Purged "
        f"Cross-Validation (CPCV).  Must be < {PBO_MAX} for the strategy to be "
        f"considered deployable.  Lower is better — 0.5 is coin-flip territory.",
        "#10-validating-a-strategy-before-going-live",
    ),
    "dsr": _g(
        "DSR (Deflated Sharpe Ratio)",
        f"The Sharpe Ratio adjusted for the number of parameter combinations tested.  "
        f"The more combinations you try, the more the in-sample Sharpe is inflated "
        f"by chance.  DSR accounts for this inflation.  Must be > {DSR_MIN} to "
        f"deploy.  Protects against cherry-picking the best backtest out of many runs.",
        "#10-validating-a-strategy-before-going-live",
    ),
    "needs retrain": _g(
        "Needs Retrain",
        f"Flags an ML model whose last training run is older than the "
        f"{MODEL_RETRAIN_WINDOW_DAYS}-day retrain window.  A stale model's "
        f"validation metrics (DSR/PBO) describe an out-of-date fit, so it is "
        f"flagged for the next monthly retraining job.  Mirrors "
        f"ml.meta_labeling.MetaLabeler.needs_retrain().",
    ),
    "model freshness": _g(
        "Model Freshness",
        f"How many days have elapsed since an ML model was last trained.  Beyond "
        f"the {MODEL_RETRAIN_WINDOW_DAYS}-day retrain window the model is "
        f"considered stale and is flagged 'Needs Retrain'.  Freshness is separate "
        f"from deployability — a fresh model can still fail the DSR/PBO gate.",
    ),
    "sharpe ratio": _g(
        "Sharpe Ratio",
        f"Average return divided by the standard deviation of returns — a measure "
        f"of risk-adjusted performance.  A Sharpe of 1.0 means the strategy earned "
        f"one unit of return per unit of risk.  The platform requires a net-of-costs "
        f"Sharpe > {NET_SHARPE_MIN} for deployment.",
        "#10-validating-a-strategy-before-going-live",
    ),
    "max drawdown": _g(
        "Max Drawdown",
        f"The largest peak-to-trough decline in the strategy's equity curve "
        f"(expressed as a fraction of peak equity).  "
        f"Must be < {MAX_DRAWDOWN_MAX * 100:.0f}% for standard strategies, "
        f"< {STRESS_MAX_DRAWDOWN * 100:.0f}% in each dated shock window for "
        f"options-selling strategies.",
        "#10-validating-a-strategy-before-going-live",
    ),
    "walk-forward validation": _g(
        "Walk-Forward Validation",
        "A technique that trains a strategy on historical data, tests it on the "
        "next unseen period, then rolls forward and repeats.  If the out-of-sample "
        "Sharpe collapses relative to in-sample, the strategy is overfit.  The "
        "platform uses this to confirm stable performance across time.",
        "#walk-forward-stability",
    ),
    "cpcv": _g(
        "CPCV (Combinatorial Purged Cross-Validation)",
        "A rigorous backtesting framework that prevents data leakage by 'purging' "
        "data near each test boundary (so adjacent days can't bleed information) "
        "and running many test paths simultaneously to measure the distribution of "
        "out-of-sample performance.  Used to compute PBO and DSR.",
        "#10-validating-a-strategy-before-going-live",
    ),

    # ── Risk gate & kill switch ───────────────────────────────────────────────
    "kill switch": _g(
        "Kill Switch",
        "A file-based pause gate.  When active, the advisory pipeline skips "
        "generating new recommendations for the current cycle (advisory mode) or "
        "the order manager blocks all submissions (live mode).  "
        "Activate via the Launcher tab or `python -m execution.kill_switch --activate`.",
        "#15-the-kill-switch--pause-gate",
    ),
    "advisory mode": _g(
        "Advisory Mode",
        "The project default (`ADVISORY_ONLY=true`).  The full quant pipeline runs "
        "— data fetch, indicators, forecasts, signals, HTML report — but the broker "
        "execution layer is completely quarantined.  No orders are placed, ever.  "
        "The GUI shows a '📋 ADVISORY MODE' banner as a permanent reminder.",
        "#advisory-only-mode",
    ),
    "risk gate": _g(
        "Risk Gate",
        "Ten sequential pre-trade checks that every order must pass before reaching "
        "the broker.  Checks include: max position size, portfolio heat, correlation, "
        "daily loss limit, macro kill switch, HMM regime, stress scenario, market "
        "hours, minimum validation report, and order rate limit.  All checks pass "
        "conservatively when context data is missing.",
        "#safety-tab-formerly-gravity-audit--what-to-check-when-an-order-is-blocked",
    ),
    "portfolio heat": _g(
        "Portfolio Heat",
        "Total adverse (unrealized) P&L across all open positions as a percentage "
        "of portfolio equity.  If heat rises above 5%, the risk gate blocks new "
        "BUY orders to prevent compounding a bad day.  Displayed in the "
        "Observability tab and the HTML report.",
        "#12-the-observability-dashboard",
    ),
    "dead letter": _g(
        "Dead Letter",
        "A per-symbol failure record written when a single ticker's analysis "
        "crashes.  The platform never aborts the entire run because of one bad "
        "ticker — it logs the failure to `output/dead_letter.json` and continues.  "
        "The Launcher tab shows a Dead-Letter Queue with a 'Retry' button per symbol.",
        "#5-running-the-pipeline",
    ),

    # ── Signals & aggregation ─────────────────────────────────────────────────
    "signal weight": _g(
        "Signal Weight",
        "A number that controls how much each signal module contributes to the "
        "final composite score.  The total score = sum of (module_score × weight) "
        "across all active modules.  Weights are tunable in Settings or the "
        "Strategy Matrix tab.  The macro_regime module has the highest weight because "
        "it gates everything else.",
        "#17-adjusting-signal-weights",
    ),
    "multifactor signal": _g(
        "Multifactor Signal",
        "A Fama-French-inspired score that combines four factors: Value (price "
        "vs book/earnings), Quality (ROE and operating margin), Low Volatility, "
        "and Size (smaller stocks get a higher score).  Each factor is "
        "cross-sectionally ranked vs the other tickers in your universe before "
        "being averaged.  Microcap stocks (<$300M market cap) receive a neutral "
        "score to avoid noise.",
        "#17-adjusting-signal-weights",
    ),
    "time-series momentum": _g(
        "Time-Series Momentum",
        "A signal based on a stock's own past performance: if the 12-month return "
        "is positive, the signal is bullish.  Backed by Moskowitz, Ooi, and "
        "Pedersen (2012) research showing that recent winners tend to keep winning "
        "over medium horizons.",
        "#17-adjusting-signal-weights",
    ),
    "cross-sectional momentum": _g(
        "Cross-Sectional Momentum",
        "Ranks stocks in your universe by their 12-1 month return (12-month "
        "lookback, skipping the most recent month to avoid reversal bias).  "
        "Stocks in the top 50% get a positive score; bottom 50% get negative.  "
        "Based on Jegadeesh-Titman (1993) research.",
        "#17-adjusting-signal-weights",
    ),
    "meta label": _g(
        "Meta Label",
        "A secondary machine-learning model that predicts 'is the primary signal "
        "correct this time?' rather than 'which direction is the stock going?'.  "
        "When the meta-labeler's confidence is below the threshold, it zeroes out "
        "the Kelly Target for that cycle (reducing position size to zero).  "
        "It affects sizing only — not the BUY/HOLD/SELL recommendation.",
        "#6-understanding-the-output",
    ),

    # ── Observability ─────────────────────────────────────────────────────────
    "state snapshot": _g(
        "State Snapshot",
        "A JSON file (`output/state_snapshot.json`) written after every pipeline "
        "run.  Contains the macro regime, VIX, HMM probability, kill-switch status, "
        "and per-signal summary.  The Observability tab and the 'Δ Since Last Run' "
        "HTML report band both read from this file.",
        "#12-the-observability-dashboard",
    ),
    "heartbeat": _g(
        "Heartbeat",
        "A timestamp file (`output/heartbeat.txt`) written every 60 seconds by "
        "the async orchestrator while it is running.  The dashboard shows a "
        "'staleness warning' when this file is > 2 hours old, meaning no "
        "fresh data has been produced recently.",
        "#12-the-observability-dashboard",
    ),
    "snapshot diff": _g(
        "Snapshot Diff (Δ Since Last Run)",
        "A comparison between the current pipeline run and the previous one.  "
        "Highlights new BUY signals, action flips (e.g. HOLD → BUY), conviction "
        "changes above the threshold, and holdings added or dropped.  "
        "Rendered as the '🔔 Δ Since Last Run' band at the top of the HTML report.",
        "#6-understanding-the-output",
    ),
    "brinson-fachler": _g(
        "Brinson-Fachler Attribution",
        "A classic portfolio performance attribution method that splits 'how did "
        "we beat / underperform the benchmark?' into three effects: "
        "Allocation (did we overweight the right sectors?), "
        "Selection (did we pick the right stocks within each sector?), and "
        "Interaction (the combined effect of both).  Found in the Reports tab.",
        "#reports-tab--live-vs-backtested-provenance--drill-down",
    ),
    "dry run": _g(
        "Dry Run",
        "A mode where the pipeline runs normally including order generation, "
        "but the OrderManager intercepts every intent before reaching the broker.  "
        "Orders are logged but never submitted.  Safer than advisory mode for "
        "testing the full execution path without real trades.  "
        "Set `DRY_RUN=true` in `.env`.",
        "#advisory-only-mode",
    ),
    "paper trading": _g(
        "Paper Trading",
        "Running the pipeline with real market data and real logic, but against a "
        "simulated broker account (no real money).  Alpaca provides a free paper "
        "account.  The preflight check requires 90 days of paper trading before "
        "going live.  Only relevant when `ADVISORY_ONLY=false`.",
        "#11-paper-trading-workflow",
    ),
    "watch rules": _g(
        "Watch Rules",
        "YAML-configured rules that trigger phone push notifications (via ntfy) "
        "when an advisory signal changes or a conviction threshold is crossed.  "
        "Stored in `watch_rules.yaml` at the project root.  "
        "Useful for staying informed between scheduled runs without polling the dashboard.",
        "#symbol-watch-alerts-tier-14",
    ),
    "decision journal": _g(
        "Decision Journal",
        "A log where you record whether you acted on, passed, or modified each "
        "advisory recommendation.  Stored as `output/decision_log.jsonl`.  "
        "Used by the Conviction Calibration chart to filter to decisions you "
        "actually endorsed, and by the Live-vs-Recommendation Tracking section "
        "to measure whether your judgment adds alpha over the raw model.",
        "#manual-execution-journal-reports-tab",
    ),
    "live inventory": _g(
        "Live Inventory",
        "A combined view of your Robinhood held positions + all configured "
        "watchlists, with per-symbol market-data coverage status.  Coverage "
        "levels: FULL (quotes + bars + fundamentals), QUOTES_ONLY, EQUITY_ONLY "
        "(held but no live price), or UNCOVERED.  Only FULL symbols are used for "
        "pricing-dependent metrics.",
        "#4-choosing-your-ticker-universe",
    ),
    "preflight check": _g(
        "Preflight Check",
        "A suite of automated readiness checks (`python scripts/preflight_check.py`).  "
        "Confirms API keys, kill-switch status, heartbeat freshness, database "
        "existence, and validation report currency.  In advisory mode, broker "
        "checks are automatically skipped.  Exit 0 = all pass; exit 1 = any failure.",
        "#13-preflight-check--are-you-ready-to-go-live",
    ),
    "circuit breaker": _g(
        "Circuit Breaker",
        "A trip condition derived from the kill-switch sentinel or the last 24 hours "
        "of risk-gate block records.  Visible in the Safety tab.  CRITICAL trips "
        "halt everything; WARNING trips are per-symbol blocks.  In advisory mode "
        "these are informational — they show which checks would have blocked an "
        "order if the broker were active.",
        "#safety-tab-formerly-gravity-audit--what-to-check-when-an-order-is-blocked",
    ),

    # ── Autonomous agent + trade-signal alerts (Tier 6 / 6.1) ──────────────────
    "autonomous advisory agent": _g(
        "Autonomous Advisory Agent",
        "The self-pacing advisory loop (run with `python3 main.py --agent`).  It "
        "re-runs the analysis on an adaptive schedule — faster around the open/"
        "close and during volatility, slower overnight — and re-pings you about "
        "high-conviction signals you have not acted on.  It is still advisory: it "
        "never places an order on your behalf.",
        "#autonomous-advisory-agent",
    ),
    "backlog reminder": _g(
        "Backlog Reminder",
        "A push notification the autonomous agent sends when a high-conviction "
        "BUY/SELL has gone unactioned, escalating over time (roughly hourly, then "
        "every few hours, then daily).  It stops once you log a decision for that "
        "symbol, the signal goes stale, or the reminder cap is reached.",
        "#autonomous-advisory-agent",
    ),
    "conviction momentum": _g(
        "Conviction Momentum",
        "An early heads-up from the agent based on a symbol's conviction "
        "*trajectory* across cycles: 'building' when conviction is climbing "
        "steadily toward an entry, 'fading' when it is deteriorating on a name no "
        "longer rated a buy.  Each trend alerts once.  Informational only.",
        "#trade-signal-alerts",
    ),
    "stop and target proximity": _g(
        "Stop / Target Proximity Alert",
        "For your held positions the agent derives a volatility-based stop below "
        "your cost and a take-profit target from the forecast, and alerts you when "
        "the live price approaches or breaches either level.  A position-"
        "management nudge — it does not place or modify any order.",
        "#trade-signal-alerts",
    ),

    # ── Robinhood execution bridge (Tier 8) ────────────────────────────────────
    "robinhood execution bridge": _g(
        "Robinhood Execution Bridge",
        "The opt-in, paper-first path that lets the platform act on its advice via "
        "the Robinhood Trading MCP.  It is OFF by default.  The pipeline only "
        "writes a gated, dry-run proposed-order queue; a separate Claude Code "
        "agent (`/rh-execute`) is the only thing that ever contacts Robinhood, and "
        "in live mode it asks you to confirm every single order.",
        "#robinhood-execution-bridge",
    ),
    "execution mode": _g(
        "Robinhood Execution Mode",
        "The `ROBINHOOD_EXECUTION_MODE` setting, rolled out strictly off → review "
        "→ live.  'off' writes nothing (default); 'review' is paper/dry-run — the "
        "agent only simulates orders; 'live' can place real orders, but only when "
        "the risk gate passes, the kill switch is clear, a per-order dollar cap is "
        "set, and you confirm each one.",
        "#robinhood-execution-bridge",
    ),
    "agentic account": _g(
        "Agentic Account",
        "A dedicated, separately-funded Robinhood account that AI agents are "
        "allowed to trade in.  All your other Robinhood accounts stay read-only.  "
        "Fund it with a small, capped amount — it is the blast radius for any "
        "agent-placed order.",
        "#robinhood-execution-bridge",
    ),
    "execution queue": _g(
        "Execution Queue",
        "The `output/execution_queue.json` file the pipeline writes in review/live "
        "mode: a list of proposed orders, each already run through the risk gate "
        "and kill-switch in dry-run.  An order is marked placeable only in live "
        "mode with the gate passed and the kill switch clear.  The Claude Code "
        "agent reads it; it never auto-executes.",
        "#robinhood-execution-bridge",
    ),

    # ── Strategy Matrix score decomposition / comparison (namespaced) ────────
    "strategy_matrix.score_components": _g(
        "Score Component Decomposition",
        "Breaks a symbol's final aggregated score into each active signal "
        "module's weighted contribution (module score x its configured weight). "
        "Only modules that ran this cycle -- not disabled via the Strategy "
        "Matrix, not suppressed by a regime gate -- are shown.  The base neutral "
        "score is 50; the final score is 50 plus the sum of every shown "
        "contribution.",
        "#17-adjusting-signal-weights",
    ),
    "strategy_matrix.meta_label_composite": _g(
        "Meta-Label Confidence Distribution",
        "A histogram of `meta_label_composite` (the geometric mean of active "
        "signal modules' meta-label probabilities) across every symbol in the "
        "latest snapshot.  Until real MetaLabelers are trained and registered "
        "(the current default), every symbol shows exactly 1.0 by design -- a "
        "single spike, not an error.  It affects position sizing only, never "
        "the BUY/HOLD/SELL action.",
        "#6-understanding-the-output",
    ),
    "strategy_matrix.regime_multiplier": _g(
        "Regime-Multiplier Sizing Impact",
        "Compares the Kelly Target StrategyEngine computed before the HMM "
        "regime multiplier was applied against the final value after that "
        "multiplier (and the meta-label composite) were multiplied in and "
        "re-clamped to the single-name ceiling.  Shows exactly how much current "
        "macro conditions are discounting -- or occasionally boosting -- a "
        "symbol's suggested position size right now.",
        "#8-understanding-position-sizing-kelly-target",
    ),
    "comparison.symbol_comparison": _g(
        "Symbol Comparison",
        "A side-by-side view of 2-3 operator-chosen symbols: final score, "
        "Kelly Target, conviction, GARCH volatility, and the per-module score "
        "breakdown for each -- so a difference in ranking between two symbols "
        "has a direct, inspectable answer instead of just two numbers.",
        "#17-adjusting-signal-weights",
    ),
    # ── Sidebar: regime filter + CSV export ────────────────────────────────────
    "sidebar.regime_filter": _g(
        "Regime Filter",
        "A sidebar control that lets you narrow the platform's view to symbols "
        "last tagged with one specific macro regime (RISK ON / NEUTRAL / "
        "RECESSION / CREDIT EVENT) instead of all of them.  The choice is stored "
        "for the current browser session only — it does not change `.env` or "
        "affect the pipeline's own regime-gating logic.",
        "#9-the-macro-regime-system",
    ),
    # ── Reports tab: suggested SELL exit sizing ──────────────────────────────
    "reports.suggested_exit_pct": _g(
        "Suggested Exit %",
        "Fraction of your CURRENT held quantity recommended for exit on a SELL "
        "action — 100% when the loss+bearish-forecast escalation (Case A) fired, "
        "50% for a base-signal SELL/RISK REDUCE (a trim). Blank for BUY/HOLD or "
        "when the symbol isn't held — there is nothing to size in that case.",
        "#price-ranges",
    ),
}

# ---------------------------------------------------------------------------
# TAB_HELP — 14 Command Center tab IDs
# ---------------------------------------------------------------------------

TAB_HELP: Dict[str, TabHelp] = {
    "launcher": _t(
        "launcher",
        "🚀 Launcher",
        "Start a pipeline run, monitor progress, and view logs.  "
        "Two launch buttons: '▶️ Launch Pipeline' runs the full async orchestrator "
        "(data → signals → HTML report), while '🔄 Refresh Data (Advisory)' runs "
        "the faster synchronous advisory loop.  "
        "Both are purely informational — no broker orders are placed.",
        ("advisory mode", "heartbeat", "dead letter", "kill switch", "dry run"),
        "#5-running-the-pipeline",
    ),
    "reports": _t(
        "reports",
        "📈 Reports",
        "Analyze the most recent pipeline results.  Includes portfolio heat, "
        "MFE/MAE/Edge Ratio per signal, the Brinson-Fachler attribution section, "
        "the Conviction Calibration reliability diagram, the Decision Journal "
        "(log what you did with each signal), and the live-vs-backtested provenance "
        "banner.  All data sourced from files — this tab never calls the broker.",
        (
            "brinson-fachler",
            "calibration",
            "conviction",
            "decision journal",
            "portfolio heat",
            "snapshot diff",
        ),
        "#reports-tab--live-vs-backtested-provenance--drill-down",
    ),
    "report_library": _t(
        "report_library",
        "📁 Report Library",
        "Browse and view — inline, right in the app — every report file the "
        "platform generates.  Covers the live daily HTML report (regenerated on "
        "every advisory refresh cycle, so it is always current), the per-day "
        "daily briefings (one per day, and you can generate today's from here), "
        "the orchestrator dashboards (the daily report and its volatility bands, "
        "which only refresh when you kick off a manual full-orchestrator run), "
        "and validation reports (which appear once a strategy has been through "
        "the validation harness).  Every file can be viewed inline or "
        "downloaded.  Read-only and file-backed — this tab never calls the "
        "broker.",
        ("advisory mode", "dead letter", "walk-forward validation", "pbo", "dsr"),
        "#17-report-library",
    ),
    "validation_lab": _t(
        "validation_lab",
        "🔬 Validation Lab",
        "Run the strategy-validation harness on demand and read the results "
        "back, all from inside the app.  Pick one or more registered strategies "
        "and a backtest window, then launch the run — it executes as a "
        "background subprocess (the GUI stays responsive) and writes per-strategy "
        "validation reports to `reports/`.  The results section shows each "
        "strategy's deployable ✅/❌ verdict against the PBO / DSR / net-Sharpe / "
        "Max-Drawdown gates (thresholds imported from `validation.thresholds`), "
        "plus the rendered walk-forward / CPCV HTML report.  Read-only and "
        "file-backed — this tab never calls the broker.",
        ("walk-forward validation", "cpcv", "pbo", "dsr", "max drawdown"),
        "#18-validation-lab",
    ),
    "settings": _t(
        "settings",
        "⚙️ Settings",
        "Edit non-secret tunable parameters and save them to `.env`.  "
        "Secret values (API keys, passwords, TOTP) are shown masked and are "
        "read-only here — edit those directly in `.env`.  "
        "Changes take effect on the **next** pipeline launch, not immediately.",
        ("advisory mode", "dry run"),
        "#3-configuring-your-environment",
    ),
    "strategy_matrix": _t(
        "strategy_matrix",
        "🧩 Strategy Matrix",
        "Enable or disable individual signal modules, adjust their weights, "
        "view the strategy version registry (sha256 of each module file), "
        "and toggle the global execution mode between Simulation, Paper, and Live.  "
        "The mode toggle is suppressed while Advisory Mode is active — no accidental "
        "live-mode activation.",
        ("signal weight", "advisory mode", "kill switch"),
        "#strategy-matrix-tab--global-execution-mode-toggle",
    ),
    "paper_monitor": _t(
        "paper_monitor",
        "📒 Paper Monitor",
        "Side-by-side view of your Robinhood account snapshot (real account state: "
        "shares, average cost, unrealized P&L) against the pipeline's market-data "
        "projection per ticker.  The Robinhood snapshot is the source of truth for "
        "cost basis and shares; the pipeline is the source of truth for prices and "
        "indicators.  These roles never cross.",
        ("advisory mode", "kelly target", "conviction"),
        "#11-paper-trading-workflow",
    ),
    "gravity": _t(
        "gravity",
        "🛡️ Safety & Gravity Audit",
        "Three-panel safety overview: (1) Strategy Health — per-strategy "
        "PBO / DSR / Sharpe / Max Drawdown gate verdicts; "
        "(2) Circuit Breaker Dashboard — active kill-switch trips and risk-gate "
        "blocks from the last 24 hours; "
        "(3) Dependency Map — which tabs and reports lose coverage when a data "
        "source degrades; "
        "(4) Gravity AI Review — automated code-level audit.  "
        "Review this before authorizing any change in execution mode.",
        ("circuit breaker", "risk gate", "pbo", "dsr", "kill switch"),
        "#safety-tab-formerly-gravity-audit--what-to-check-when-an-order-is-blocked",
    ),
    "options": _t(
        "options",
        "🧮 Options",
        "Premium-selling strategy matrix for each active symbol.  "
        "Shows GJR-GARCH volatility, IV Rank proxy, Aroon/Coppock trend bias, "
        "the recommended strategy (Put Credit Spread, Iron Condor, Cash/Wait), "
        "strike levels, net premium, daily theta, and ATM Black-Scholes Greeks.  "
        "Gated by IVR > 50, VRP > 0.02, VIX < 30, and no CREDIT EVENT — "
        "Cash/Wait is returned when any gate fails.  "
        "All informational — no orders are submitted.",
        ("put credit spread", "iron condor", "iv rank", "vrp", "garch vol", "black-scholes greeks"),
        "#7-reading-the-action-signals",
    ),
    "market_data": _t(
        "market_data",
        "🛰️ Market Data",
        "Shows which data provider is active (Alpaca real-time or yfinance ~15-min "
        "delayed), quote freshness per symbol, and a sliding-window connectivity "
        "health badge.  Lets you fetch a batch of quotes with per-symbol error "
        "classification (Rate Limited, Not Found, Timeout, etc.) and a validation "
        "Status column to catch malformed quotes before they reach the quant pipeline.",
        ("advisory mode",),
        "#5-running-the-pipeline",
    ),
    "observability": _t(
        "observability",
        "📊 Observability",
        "The platform's single observability surface (the former standalone "
        "`streamlit run observability/dashboard.py` app has been retired): "
        "macro regime / VIX / HMM risk-on probability, account holdings & P&L, "
        "open positions vs. pipeline signals, portfolio heat/gross/net exposure, "
        "validation report status, recent closed trades, an equity-curve/"
        "drawdown/regime-overlay chart, the risk gate block log, a heartbeat "
        "trend sparkline, system resource metrics (CPU, memory, disk), a "
        "latency heatmap (populated by the Market Data tab), and a structured "
        "log viewer with contextual error classification.",
        ("heartbeat", "state snapshot", "macro regime", "vix", "hmm"),
        "#12-the-observability-dashboard",
    ),
    "analytics": _t(
        "analytics",
        "📊 Analytics",
        "Read-only performance & analytics, distinct from the Observability "
        "tab's *internal* paper-store view.  Sections: (1) Broker Realized "
        "Performance — realized P&L reconstructed from your Robinhood order "
        "history (FIFO round-trips), clearly labelled 'Broker (Robinhood order "
        "history)' so it is never confused with the internal TransactionsStore "
        "P&L; (2) Account Value Equity Curve — total account equity over time "
        "from stored account snapshots; (3) Recent Alerts Feed — a tail of the "
        "structured alert log.  It also hosts the ML registry, news-sentiment, "
        "and slippage/CoVaR analytics panels.  Every source is file-backed and "
        "degrades to an empty-state message rather than a fabricated value.",
        ("advisory mode", "state snapshot", "kelly target"),
        "#12-the-observability-dashboard",
    ),
    "pairs": _t(
        "pairs",
        "🔗 Pairs",
        "Read-only, advisory-only view over the pairs-trading (statistical "
        "arbitrage) engine. **Scan** takes a symbol list and ranks cointegrated "
        "candidate pairs by Engle-Granger p-value, keeping only those with a "
        "5–60 day mean-reversion half-life. **Analyze a pair** shows the current "
        "spread z-score, Kalman-filtered hedge ratio, rolling ADF p-value, and "
        "half-life for a chosen Y/X pair, plus a human-readable current signal "
        "(entry / hold / exit / stop / not-cointegrated) and a z-score chart. "
        "Entry at |z|>2, exit at the 0-cross or ADF p>0.10, stop at |z|>4 — all "
        "**informational**. No orders are ever placed from this tab.",
        ("advisory mode",),
        "#12-the-observability-dashboard",
    ),
    "live_inventory": _t(
        "live_inventory",
        "📦 Live Inventory",
        "Combines your Robinhood holdings, Robinhood watchlists, and any "
        "file-backed watchlists into one deduped universe.  Shows per-symbol "
        "coverage status (FULL / QUOTES_ONLY / EQUITY_ONLY / UNCOVERED) so "
        "you know which tickers can support pricing-dependent metrics.  "
        "'🔄 Sync Now' refreshes the universe and persists the result as "
        "`DEFAULT_TICKERS` in `.env` for the next pipeline run.",
        ("live inventory", "advisory mode"),
        "#4-choosing-your-ticker-universe",
    ),
    "help": _t(
        "help",
        "❓ Help",
        "In-app glossary and per-tab reference — search 60+ terms (Kelly Target, "
        "PBO, DSR, Sahm Rule, IVR, HMM, …) with plain-English definitions and "
        "'Read more →' links back to the How-To Guide, plus a tab-by-tab "
        "description of every Command Center tab.  Purely informational.",
        ("advisory mode",),
        "#in-app-help--glossary",
    ),
    "prompts": _t(
        "prompts",
        "📝 Prompts",
        "Shows the resolved version and source (pin / remote / disk cache / "
        "baseline) of every AI-facing prompt in the Prompt Registry, with a "
        "'🔄 Sync' button and a diff/rollback viewer.  Fetched prompts are "
        "advisory text only — they can change what an AI is told, never what "
        "the platform is permitted to do.",
        ("advisory mode",),
        "#16-remote-prompt-updates-prompt-registry",
    ),
    "ai_insights": _t(
        "ai_insights",
        "🪄 AI Insights",
        "Per-symbol AI reads layered on top of the pipeline's own signals: an "
        "Opal research brief (thesis/catalysts/risk factors grounded in real "
        "Finnhub news), a Claude analyst rationale note, a Gemini chart-pattern "
        "read, and a Claude-vs-Gemini disagreement view.  Every section is "
        "button-gated — nothing calls an AI provider until you click it — and "
        "purely informational: no AI output here places or modifies an order.",
        ("advisory mode",),
        "#ai-insights--ai-control-center",
    ),
    "ai_control_center": _t(
        "ai_control_center",
        "🎛️ AI Control Center",
        "One place to turn every AI option on or off, run each on demand, and "
        "start/stop a recurring pipeline run — all operator-triggered, nothing "
        "autonomous.  Section A toggles the master switches (Claude commentary, "
        "Gemini alerts, Gemini chart vision, Gravity AI runner, Opal research) and "
        "shows a ready / disabled / missing-key / not-built badge per capability.  "
        "Section B runs per-symbol Claude / Gemini-vision / Opal actions on demand.  "
        "Section C runs the Gravity AI audit.  Section D starts and stops an "
        "`--interval` or `--agent` run you can stop at any time.  Provider API keys "
        "stay secret-only in `.env` (never GUI-writable); toggles take effect on the "
        "next launch.",
        ("advisory mode", "kill switch"),
        "#advisory-only-mode",
    ),
}

# ---------------------------------------------------------------------------
# SECTION_HELP — section-level tooltip strings (used in panel headers)
# ---------------------------------------------------------------------------

SECTION_HELP: Dict[str, str] = {
    "brinson_fachler": (
        "Brinson-Fachler decomposes your portfolio return vs a benchmark into "
        "Allocation, Selection, and Interaction effects.  "
        "Enter sector weights and returns in the table, then click 'Compute'."
    ),
    "conviction_calibration": (
        "Reliability diagram — bars are empirical win rates per conviction bin.  "
        "The diagonal is 'perfect calibration'.  Requires conviction-annotated "
        "closed trades (accumulated from live advisory runs)."
    ),
    "decision_journal": (
        "Log whether you acted on, passed, or modified each advisory signal.  "
        "Entries are appended to output/decision_log.jsonl and linked to trades."
    ),
    "snapshot_diff": (
        "Changes since the last pipeline run: new BUYs, action flips, large "
        "conviction moves, and holdings added or dropped."
    ),
    "heartbeat_trend": (
        "Rolling sparkline of how stale the orchestrator's heartbeat is.  "
        "A rising trend means the pipeline is running less frequently than expected."
    ),
    "dead_letter_queue": (
        "Symbols whose analysis failed during the last run.  "
        "Use 'Retry' to re-evaluate a single ticker without re-running everything."
    ),
    "circuit_breaker_dashboard": (
        "Active risk-gate trips derived from the kill-switch sentinel and the "
        "last 24 hours of risk_gate_blocks.jsonl.  "
        "In advisory mode these are informational — no orders exist to block."
    ),
    "dependency_map": (
        "Shows which GUI panels and reports are affected when a data source "
        "(Alpaca, Finnhub, FRED, Robinhood) degrades or becomes unavailable."
    ),
    "strategy_version_registry": (
        "SHA-256 prefix and file mtime for each registered signal module.  "
        "If the hash hasn't changed, the file was not modified since last deploy."
    ),
    "recommendation_tracking": (
        "Model return = conviction-weighted paper return had you taken every BUY "
        "signal and held for the horizon.  Operator return = average actual "
        "closed-trade return from acted signals.  "
        "**Delta > 0 → your judgment adds alpha over the model.**"
    ),
    "correlation_clusters": (
        "Groups your symbols by how correlated their returns are, using "
        "hierarchical Ward-linkage clustering.  Helps identify concentration risk "
        "when multiple 'different' tickers actually move together."
    ),
    "latency_heatmap": (
        "Per-symbol quote latency (time from quote timestamp to ingestion).  "
        "Populated by the Market Data tab's 'Fetch quotes' batch.  "
        "High latency on a real-time provider (Alpaca) suggests network issues."
    ),
    "preflight_panel": (
        "Runs scripts/preflight_check.py and shows pass/fail per check.  "
        "In advisory mode, broker checks are automatically skipped.  "
        "Fix any blocking failure before changing execution mode."
    ),
    "launcher_safety_controls": (
        "Kill-switch toggle and DRY_RUN checkbox for the current session.  "
        "'Safe Mode' is ON when both the kill switch and DRY_RUN are active."
    ),
    "global_execution_mode": (
        "Simulation → Paper → Live mode selector.  "
        "Suppressed while ADVISORY_ONLY=true.  "
        "Live mode requires a deliberate 'CONFIRM LIVE PRODUCTION' button click."
    ),
    "robinhood_execution_bridge": (
        "Off by default. In 'review' mode the pipeline writes a gated, dry-run "
        f"order queue that only previews. A queue older than {_RH_QUEUE_STALE_MIN} "
        "minutes is treated as stale and the `/rh-execute` agent refuses to place "
        "from it — re-run the pipeline for a fresh one. Placement, when enabled, "
        "always requires per-order human confirmation in the agent session."
    ),
    "robinhood_execution.intent_status": (
        "Per-intent status derived by cross-referencing the queue against the "
        "agent's receipts log (`output/execution_receipts.jsonl`), matched by "
        "symbol + side. **queued** = waiting; **blocked** = the pre-trade risk "
        "gate refused it (reasons shown); **previewed** = the agent ran a "
        "review-only preview; **skipped** = the operator declined it; "
        "**placed** = a real order was submitted. This panel only reads these "
        "files — it never contacts Robinhood."
    ),
    "robinhood_execution.reconciliation": (
        "Cross-check of the append-only placement ledger "
        "(`output/execution_placed.jsonl`) against `placed` receipts, matched by "
        "symbol + side. A non-zero 'Unmatched' count means the ledger recorded a "
        "placement the receipts log doesn't confirm — investigate before the next "
        "run. The ledger is tolerated as absent (no placements yet)."
    ),
    "sidebar.regime_filter": (
        "Filters the 'symbols matching' count below to the selected macro "
        "regime. 'All regimes' shows every symbol from the last run regardless "
        "of the regime tag recorded at that time."
    ),
    "export.download_signals_csv": (
        "Exports the per-symbol rows from the last `state_snapshot.json` run — "
        "action, score, Kelly target, buy/sell ranges, macro status — as a flat "
        "CSV for offline post-trade analysis. Respects the regime filter above."
    ),
    "pipeline_progress": (
        "Percentage of pipeline stages completed for the current run.  "
        f"Refreshes every {_PROGRESS_POLL_SECONDS} seconds while a run is "
        "active.  Advisory-only — no orders are placed; this only reflects "
        "analysis progress."
    ),
    "observability.snapshot_summary": (
        "Summary of the file-backed state last written by the orchestrator."
    ),
    "observability.macro_gate_write": (
        "Writes `MACRO_REGIME_GATE_ENABLED` to `.env` via `gui/env_io.py`.  "
        "Change takes effect when the orchestrator next starts."
    ),
    "observability.recession_telemetry": (
        "Values are sourced from the last orchestrator run's state snapshot "
        "(FRED data).  They reflect conditions at pipeline execution time, "
        "not real-time — run the orchestrator to refresh."
    ),
    "strategy_health_gates": (
        "Sourced from `output/gravity_verification_report.json` (written by the "
        "Gravity AI Review Suite). Evaluated against thresholds in "
        "`validation/thresholds.py` — the same constants used by "
        "`validation/harness.py`."
    ),
    "options.matrix_intro": (
        "Hydrated premium-selling matrix: GJR-GARCH σ, realized-vol IVR proxy, "
        "Aroon+Coppock trend bias, ATM Black-Scholes Greeks, and the "
        "deterministic strategy directive with $0.50 strike-grid integrity checks."
    ),
    "options.matrix_methodology": (
        "σ from GJR-GARCH(1,1) with 20-day realized fallback; **IVR proxy** is a "
        "realized-vol percentile (true IVR requires an options chain). Trend bias is "
        "Aroon+Coppock sign agreement. **Stale=True** marks delayed (~15 min) yfinance "
        "quotes. Realizable Theta applies a DTE-scaled execution-friction haircut "
        "(40% @ 1DTE, 22% @ 7DTE, 12% @ 30DTE, 5% baseline)."
    ),
    "strategy_matrix.signal_modules": (
        "**Signal modules** — disable a module or adjust its weight; "
        "saved to `.env` and honored by `SignalAggregator` on next run."
    ),
    "strategy_matrix.regime_multiplier_note": (
        "Note: `regime_multiplier` must keep weight 0.0 — it carries the HMM "
        "second opinion as a sizing multiplier, not a score."
    ),
    "report_viewer.llm_commentary_off": (
        "LLM commentary is off.  Set `LLM_COMMENTARY_ENABLED=true` and "
        "`ANTHROPIC_API_KEY=…` in `.env`, then relaunch the GUI."
    ),
}

# ---------------------------------------------------------------------------
# METRIC_HELP — column/metric-level tooltip strings
# ---------------------------------------------------------------------------

METRIC_HELP: Dict[str, str] = {
    "Kelly Target": (
        f"Suggested position size as a fraction of capital (e.g. 0.14 = 14%).  "
        f"Capped at {_KELLY_CAP_PCT}% by KELLY_CAP and further capped by the "
        f"advisory single-name ceiling.  Always advisory — not an instruction to trade."
    ),
    "Conviction": (
        "Confidence score [0, 1].  Combined from signal strength, macro alignment, "
        "and historical calibration.  Higher = more signals agree.  "
        "See the Calibration chart to verify if conviction reflects reality."
    ),
    "Action Signal": (
        "STRONG BUY / BUY / HOLD / RISK REDUCE / AVOID.  "
        "Advisory only — no orders are placed.  "
        "May be overridden by macro gates (e.g. RECESSION forces BUY → HOLD)."
    ),
    "VIX": (
        f"CBOE Volatility Index.  "
        f"Above {_VIX_THRESH} triggers a soft macro score penalty on all BUY signals."
    ),
    "Sahm Rule": (
        f"Unemployment-rise recession indicator.  "
        f"At or above {_SAHM_THRESH} the platform applies a macro score penalty."
    ),
    "HMM Risk-On Probability": (
        "Hidden Markov Model probability that we are in a risk-on macro regime [0, 1].  "
        "Below 0.30 can downgrade the rules-based regime from RISK ON to NEUTRAL.  "
        "Below 0.20 (risk-off > 0.80) tightens the kill-switch trigger thresholds."
    ),
    "HMM_Risk_On_Probability": (
        "See 'HMM Risk-On Probability' — identical metric, underscore column key."
    ),
    "PBO": (
        f"Probability of Backtest Overfitting.  "
        f"Must be < {PBO_MAX} for the strategy to be deployable.  Lower = better."
    ),
    "DSR": (
        f"Deflated Sharpe Ratio — Sharpe adjusted for number of parameter trials.  "
        f"Must be > {DSR_MIN}."
    ),
    "Net Sharpe": (
        f"Return-to-risk ratio net of realistic transaction costs.  "
        f"Must be > {NET_SHARPE_MIN}."
    ),
    "Max Drawdown": (
        f"Peak-to-trough equity decline.  "
        f"Must be < {MAX_DRAWDOWN_MAX * 100:.0f}% for standard strategies."
    ),
    "GARCH Vol": (
        "GJR-GARCH annualized volatility estimate.  More sensitive to recent "
        "downside moves than a simple historical standard deviation.  "
        "Used for position sizing and options premium evaluation."
    ),
    "Sigma_GARCH": "Same as GARCH Vol — GJR-GARCH annualized volatility.",
    "IVR Proxy": (
        "Realized-vol IVR proxy [0–100].  Values above 50 suggest options IV is "
        "elevated relative to recent history — favorable for premium selling."
    ),
    "RSI": "Relative Strength Index (14-period).  Above 70 = overbought; below 30 = oversold.",
    "RSI_2": (
        "Ultra-short RSI (2-period) used for mean-reversion entries.  "
        "Signal fires when RSI(2) < 10 and price > SMA(200) (uptrend filter)."
    ),
    "ATR": "Average True Range — average daily price swing in dollars.",
    "Aroon": "Aroon Oscillator.  Near +100 = strong uptrend; near -100 = strong downtrend.",
    "MACD": "MACD crossover score.  Positive when the MACD line is above its signal line.",
    "Forecast 30d": (
        "30-day blended price forecast from ARIMA, Monte Carlo, Holt-Winters, "
        "and CNN-LSTM models.  Weighted by each model's recent forecast skill."
    ),
    "Buy Zone": "ATR-based entry price range.  Buying within this range improves risk/reward.",
    "Sell Zone": (
        "Upside profit target range plus trailing stop.  "
        "Computed from ATR, Chandelier Exit, and 30-day forecast."
    ),
    "Unrealized P&L": (
        "Unrealized profit or loss on the position: (current price − avg cost) × shares.  "
        "Sourced from the Robinhood account snapshot — the source of truth for account state."
    ),
    "Market Value": (
        "Current market value: shares × current price.  "
        "Sourced from the Robinhood snapshot for held positions; '—' for watchlist-only."
    ),
    "Portfolio Heat": (
        "Adverse unrealized P&L as % of equity.  "
        "Above 5% the risk gate blocks new BUY orders."
    ),
    "Coverage Status": (
        "Market-data coverage for this symbol: FULL (quotes+bars+fundamentals), "
        "QUOTES_ONLY, EQUITY_ONLY (held but no live price), or UNCOVERED.  "
        "Pricing-dependent metrics require FULL coverage."
    ),
    "CoverageStatus": "See 'Coverage Status' — identical metric, camelCase column key.",
    "Multifactor_Composite": (
        "Fama-French composite score: average of Value, Quality, Low-Vol, and Size "
        "z-scores, winsorized to ±3.  Mapped to [-1, +1] via tanh(z/2)."
    ),
    "News_Sentiment": (
        "Sentiment score from recent news headlines via FinBERT (neural) or "
        "keyword lexicon fallback.  In [-1, +1].  Suppressed within 48 h of earnings."
    ),
    "Earnings_Date": "Next expected earnings announcement date for this ticker.",
    "Sell Range": (
        "Sell Zone string showing upside target + trailing stop.  "
        "Same as Sell Zone — different column name in some report views."
    ),
    "Value Z": (
        "Cross-sectional z-score averaging book-to-market and earnings-yield "
        "vs. the rest of your universe, winsorized to ±3.  Positive = cheaper "
        "than peers.  Part of the multifactor composite (signals/multifactor.py)."
    ),
    "Quality Z": (
        "Cross-sectional z-score of ROE + operating margin (falls back to "
        "-debt/equity when unavailable), winsorized to ±3.  "
        "Positive = higher quality than peers."
    ),
    "LowVol Z": (
        "Cross-sectional z-score of negative 60-day realized volatility, "
        "winsorized to ±3.  Positive = lower volatility than peers."
    ),
    "Size Z": (
        "Cross-sectional z-score of negated log market cap, winsorized to ±3.  "
        "Positive = smaller than peers (small-cap premium prior)."
    ),
    "Value_Z": "See 'Value Z' — identical metric, underscore column key.",
    "Quality_Z": "See 'Quality Z' — identical metric, underscore column key.",
    "LowVol_Z": "See 'LowVol Z' — identical metric, underscore column key.",
    "Size_Z": "See 'Size Z' — identical metric, underscore column key.",
    "Multifactor Composite": (
        "Average of Value/Quality/LowVol/Size z-scores, re-clipped to ±3, "
        "then mapped to [-1, +1] via tanh(z/2) for the signal aggregator.  "
        "Microcap tickers (excluded from cross-sectional scoring) show 0 (neutral)."
    ),
    "XSec 12-1M Return": (
        "12-month return skipping the most recent month (Jegadeesh-Titman "
        "cross-sectional momentum convention — avoids short-term reversal bias)."
    ),
    "XSec Momentum Rank": (
        "This ticker's percentile rank (0-1) by 12-1 month return within your "
        "current universe.  Top half scores positive in the cross-sectional "
        "momentum signal module; bottom half scores negative."
    ),
    "Buy Range": "ATR-based entry price range.  Buying within this range improves risk/reward.",
    "Portfolio Delta": (
        "Unweighted sum of ATM Delta across held symbols with an actionable "
        "options directive.  Per-contract, not position-size-weighted — see "
        "the Options tab caption for why weighting isn't fabricated."
    ),
    "Portfolio Gamma": (
        "Unweighted sum of ATM Gamma across held symbols with an actionable "
        "options directive.  Per-contract, not position-size-weighted."
    ),
    "Portfolio Vega": (
        "Unweighted sum of ATM Vega across held symbols with an actionable "
        "options directive.  Per-contract, not position-size-weighted."
    ),
    "Portfolio Theta": (
        "Unweighted sum of ATM daily Theta across held symbols with an "
        "actionable options directive.  Per-contract, not position-size-weighted."
    ),
    "Theta Carry Projection": (
        "Cumulative Theta × 30, assuming price/IV stay flat for 30 days.  "
        "A mechanical 'time decay floor' reference — NOT a forecast of "
        "actual 30-day P&L, which also depends on gamma/vega repricing."
    ),
    "Meta-Label Composite": (
        "Geometric mean of active signal modules' meta-label confidence "
        "(Lopez de Prado 'is the primary signal correct?' probability), applied "
        "as a multiplier on Kelly Target before the position-size cap.  Always "
        "1.0 (no-op) until a MetaLabeler is trained and registered for a given "
        "signal — see ml/meta_labeling.py.  Not currently written to a "
        "per-symbol column in the dashboard; this platform surfaces it only "
        "as a multiplicative effect already baked into Kelly Target."
    ),
    "Intents Queued": (
        "Number of proposed orders in `output/execution_queue.json` this cycle.  "
        "Each has already been run through the risk gate in dry-run."
    ),
    "Placeable": (
        "Of the queued intents, how many are actually eligible to place: mode is "
        "'live', the risk gate passed, the kill switch is clear, and a per-order "
        "notional cap is set.  Zero is normal and expected in 'review' mode."
    ),
    "Queue Age": (
        f"Minutes since the queue was generated.  Past {_RH_QUEUE_STALE_MIN} "
        "minutes it is stale and `/rh-execute` will refuse to place from it."
    ),
    "Execution Mode": (
        "The active `ROBINHOOD_EXECUTION_MODE`: off (nothing written), review "
        "(paper/dry-run preview only), or live (placement possible, still "
        "gated and human-confirmed per order)."
    ),
    "Kill Switch": (
        "Whether `output/KILL_SWITCH` is active for THIS queue.  When active, "
        "placement is blocked for every intent regardless of mode — checked "
        "again by the agent immediately before each order."
    ),

    # ── Robinhood execution bridge — per-intent status + reconciliation ──────
    "robinhood_execution.placed_count": (
        "Rows in the append-only placement ledger "
        "(`output/execution_placed.jsonl`) — one per real order the agent "
        "submitted (each capped at "
        f"${_RH_MAX_NOTIONAL:,.2f} notional/order via "
        "`ROBINHOOD_MAX_NOTIONAL_PER_ORDER`)."
    ),
    "robinhood_execution.matched": (
        "Placement-ledger entries that have a corresponding `placed` receipt "
        "(matched by symbol + side).  A healthy run has matched == placed."
    ),
    "robinhood_execution.unmatched": (
        "Placement-ledger entries with NO matching `placed` receipt — a possible "
        "ledger/receipt divergence to investigate.  Should normally be zero."
    ),

    # ── Strategy Matrix score decomposition / comparison (namespaced) ────────
    "strategy_matrix.score_components": (
        "Weighted contribution (module score x weight) of each active signal "
        "module to this symbol's final aggregated score.  Disabled or "
        "regime-gated modules are omitted, never fabricated as zero-contribution rows."
    ),
    "strategy_matrix.meta_label_composite": (
        "Geometric mean of active modules' meta-label P(signal correct).  "
        "Exactly 1.0 for every symbol until a MetaLabeler is trained and "
        "registered — the expected pre-Stage-4-deployment state, not an error."
    ),
    "strategy_matrix.regime_multiplier": (
        "Kelly Target before vs. after the HMM regime multiplier and "
        "meta-label composite were applied and the result re-clamped to the "
        "single-name position ceiling."
    ),
    "strategy_matrix.kelly_target_pre_regime": (
        "The fractional-Kelly / vol-target sizing weight StrategyEngine "
        "computed BEFORE the HMM regime multiplier or meta-label composite "
        "were applied — already clamped to `settings.MAX_POSITION_WEIGHT`."
    ),
    "strategy_matrix.kelly_target_post_regime": (
        "The final Kelly Target after multiplying by the HMM regime "
        "multiplier and meta-label composite and re-clamping to "
        "`settings.MAX_POSITION_WEIGHT` — identical to the `Kelly Target` "
        "column shown elsewhere in the platform."
    ),
    "comparison.symbol_comparison": (
        "Side-by-side final score, Kelly Target, conviction, GARCH vol, and "
        "meta-label/regime-multiplier readouts for 2-3 selected symbols, plus "
        "each symbol's score-component breakdown for direct comparison."
    ),
    "sidebar.regime_match_count": (
        "Count of symbols from the last pipeline run whose recorded macro "
        "status matches the sidebar regime filter. Based on "
        "`output/state_snapshot.json` — refresh the pipeline to update it."
    ),
    "reports.suggested_exit_pct": (
        "Fraction of the currently held quantity recommended for exit on a "
        "SELL action — 100% for the Case A loss+bearish-forecast escalation, "
        "50% for a base-signal trim. Blank when not a held SELL."
    ),

    # ── Analytics tab — ML model freshness & deployability monitoring ────────
    "analytics.last_trained_age": (
        f"Days since the model's `trained_date` in `ml/registry.yaml`.  Beyond "
        f"the {MODEL_RETRAIN_WINDOW_DAYS}-day retrain window the model is flagged "
        f"'Needs Retrain'.  '—' when the registry carries no valid date "
        f"(never fabricated as 0)."
    ),
    "analytics.needs_retrain": (
        f"⚠️ when the last training run is older than the "
        f"{MODEL_RETRAIN_WINDOW_DAYS}-day retrain window (mirrors "
        f"ml.meta_labeling.MetaLabeler.needs_retrain()); ✅ when fresh.  '—' when "
        f"the model has no valid trained date to measure against."
    ),
    "analytics.cpcv_dsr": (
        f"Deflated Sharpe Ratio from CPCV path evaluation.  Must be > {DSR_MIN} "
        f"(with PBO < {PBO_MAX}) for the model to be deployable.  '—' when null — "
        f"never a fabricated 0."
    ),
    "analytics.pbo": (
        f"Probability of Backtest Overfitting from CPCV.  Must be < {PBO_MAX} "
        f"(with DSR > {DSR_MIN}) for the model to be deployable.  Lower is better; "
        f"'—' when null — never a fabricated 0."
    ),
    "analytics.deployable": (
        f"✅ only when `cpcv_dsr > {DSR_MIN}` AND `pbo < {PBO_MAX}` (plus Gravity "
        f"gates), per the registry's own gate.  ❌ otherwise; '—' when the "
        f"registry does not record a deployable flag.  Deployability is separate "
        f"from freshness — a deployable model can still be stale."
    ),
}

# ---------------------------------------------------------------------------
# Public lookup functions
# ---------------------------------------------------------------------------


def get_tab_help(tab_id: str) -> Optional[TabHelp]:
    """Return the ``TabHelp`` for *tab_id*, or ``None`` if not found."""
    return TAB_HELP.get(tab_id)


def get_glossary(term: str) -> Optional[GlossaryEntry]:
    """Return the ``GlossaryEntry`` for *term* (case-insensitive), or ``None``."""
    return GLOSSARY.get(term.lower())


def metric_help(key: str) -> str:
    """Return the tooltip string for column *key*, or empty string if unknown."""
    return METRIC_HELP.get(key, "")


def section_help(key: str) -> str:
    """Return the section-level explainer string for *key*, or empty string if unknown.

    Mirrors :func:`metric_help` — an empty string is the correct sentinel for a
    missing key (renders no caption, never raises — CONSTRAINT #6).  Never add a
    default-fallback value here; ``""`` is the intended miss behaviour.
    """
    return SECTION_HELP.get(key, "")


def search_glossary(query: str) -> List[GlossaryEntry]:
    """Return all entries whose term or plain_english contains *query* (case-insensitive).

    Returns an empty list for blank queries.  Never raises.
    """
    if not query or not query.strip():
        return []
    q = query.strip().lower()
    results: List[GlossaryEntry] = []
    try:
        for entry in GLOSSARY.values():
            if q in entry.term.lower() or q in entry.plain_english.lower():
                results.append(entry)
    except Exception:  # pragma: no cover — defensive guard only
        logger.warning("search_glossary: unexpected error for query %r", query)
    return results


def guide_url(anchor: Optional[str]) -> str:
    """Return the full relative path to *anchor* in the How-To Guide.

    Parameters
    ----------
    anchor : str or None
        A GitHub-style heading slug, e.g. ``"#7-reading-the-action-signals"``.
        Must begin with ``#``.

    Returns
    -------
    str
        ``"docs/HOW_TO_GUIDE.md#..."`` or ``""`` when *anchor* is ``None`` or
        empty.
    """
    if not anchor:
        return ""
    return f"{_GUIDE_PATH}{anchor}"

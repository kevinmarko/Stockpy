"""
shared/help_content.py
======================
Static help-content store: plain-English definitions, per-screen explainers,
section tooltips and metric tooltips for the platform's operator help.

The Pilots PWA (``webapp/``) is the only frontend and keeps its own help text in
``webapp/src/help/helpContent.ts`` (``TAB_HELP`` / ``GLOSSARY``).  This module is
the Python-side catalog.  It was written for the Streamlit "InvestYo Command
Center" (deleted 2026-09), so its ``TAB_HELP`` keys are the *legacy tab ids*,
kept as stable keys; each description now says which web app screen carries the
capability today, or says plainly that the surface was archived.  Its remaining
consumers are ``shared/help_widgets.py`` (Streamlit wrappers still imported by
the Gravity audit), the ``MODEL_RETRAIN_WINDOW_DAYS`` constant used by
``pilots/models.py`` / ``api/pilots_api.py``, and the tests.  Never describe a
deleted surface (the Streamlit app, the ``main.py`` ``--interval`` / ``--agent``
loops) as live here — ``tests/test_help_content_live_surfaces.py`` enforces that.

**Zero Streamlit imports.**  All content is defined here so callers can
import exactly what they need without touching a rendering layer.  Every
lookup function returns a safe empty value for unknown keys — it never raises.

All threshold values are imported from ``settings``, ``validation.thresholds``,
and ``engine.advisory.CONFIG`` so the explanations automatically stay in sync
with the live configuration.  Never hard-code numeric thresholds here.

**``settings.X`` values specifically must never be snapshotted into a
module-level constant.**  ``settings`` is expected to be hot-reloadable
in-process, so a plain ``_FOO = settings.BAR`` module-level assignment reads
once at import time and is then stale for the lifetime of the process — this
module is imported by live backend code (``api/pilots_api.py``,
``pilots/models.py``), not only the GUI, so that staleness is a real bug, not
a cosmetic one.  Any text that interpolates a ``settings.X`` value MUST be
built from a zero-arg callable (see the small ``_kelly_cap_pct()``-style
helpers below) evaluated at *read* time via ``resolved_plain_english()`` /
``metric_help()`` / ``section_help()`` — never read a bare module constant.

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
    ``Dict[str, TabHelp]`` keyed by the legacy tab IDs (stable keys; each entry
    describes the web app screen that replaced the tab).
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
from typing import Callable, Dict, List, Optional, Tuple, Union

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
from shared.robinhood_execution_panel import STALE_QUEUE_SECONDS as _RH_QUEUE_STALE_SECONDS

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
    plain_english : str or Callable[[], str]
        One-to-three sentence explanation a non-quant can understand.
        Must not embed raw numeric thresholds — reference live values instead.
        Entries whose text interpolates a live ``settings.X`` value store a
        zero-arg callable here (built once at import time, but not *invoked*
        until read) rather than a pre-formatted string, so the value reflects
        whatever ``settings.X`` holds at read time, not at import time.  Use
        :meth:`resolved_plain_english` to get the actual string — never read
        this field directly unless you are prepared to handle both shapes.
    guide_anchor : str or None
        GitHub-style heading slug from ``docs/HOW_TO_GUIDE.md`` (includes
        the leading ``#``).  ``None`` when no dedicated section exists yet.
    """

    term: str
    plain_english: Union[str, Callable[[], str]]
    guide_anchor: Optional[str] = None

    def resolved_plain_english(self) -> str:
        """Return ``plain_english`` as a string, calling it if it is callable.

        This is the correct way for any caller to read the explanation text —
        it transparently handles both the plain-string case and the
        live-config-sourced callable case.  Never raises: a callable is
        assumed to be one of this module's own zero-arg helper functions.
        """
        value = self.plain_english
        return value() if callable(value) else value


@dataclass(frozen=True)
class TabHelp:
    """Help text for one screen (keyed by its legacy Command Center tab id).

    Attributes
    ----------
    tab_id : str
        Identifier matching the key used in ``TAB_HELP``.
    title : str
        Human-readable tab name.
    description : str
        Two-to-four sentence plain-English description of the tab's purpose.
        Must say where the capability lives in the web app today (or that it was
        archived) and must not describe a deleted surface as live.
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
    plain_english: Union[str, Callable[[], str]],
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
# numbers.  Plain f-strings are evaluated once at module-import time — fine for
# constants that never change in-process (e.g. engine.advisory.CONFIG,
# validation.thresholds).  But settings.X in particular is expected to be
# hot-reloadable in-process (see settings.py), so ANY value read from
# `settings.X` MUST be wrapped in a zero-arg function (below) and referenced by
# calling that function *inside* the f-string/dict-value, never by reading a
# module-level constant that snapshotted settings.X once at import time.  A
# dict/GlossaryEntry value that itself interpolates one of these calls into a
# final string must ALSO become a zero-arg callable (`lambda: f"...{_fn()}..."`)
# rather than a plain string — otherwise the interpolation still only happens
# once, at import time, and the fix is a no-op.  See resolved_plain_english() /
# metric_help() / section_help(), which transparently call such callables.

_VIX_THRESH = int(_ADVISORY_CONFIG["macro_vix_gate_threshold"])
_SAHM_THRESH = _ADVISORY_CONFIG["macro_sahm_gate_threshold"]


def _kelly_cap_pct() -> int:
    return int(settings.KELLY_CAP * 100)


def _kelly_fraction() -> float:
    return settings.KELLY_FRACTION


def _conv_delta() -> float:
    return settings.SNAPSHOT_CONVICTION_DELTA_THRESHOLD


_RH_QUEUE_STALE_MIN = int(_RH_QUEUE_STALE_SECONDS // 60)


def _rh_max_notional() -> float:
    return settings.ROBINHOOD_MAX_NOTIONAL_PER_ORDER


def _progress_poll_seconds() -> int:
    return settings.PROGRESS_POLL_SECONDS


def _sizing_cap_alert_threshold_pct() -> int:
    return int(settings.SIZING_CAP_ALERT_THRESHOLD_PCT * 100)


def _sizing_cap_escalation_threshold_cycles() -> int:
    return settings.SIZING_CAP_ESCALATION_THRESHOLD_CYCLES


def _sizing_cap_escalation_factor() -> float:
    return settings.SIZING_CAP_ESCALATION_FACTOR


def _max_portfolio_heat_pct() -> float:
    return settings.MAX_PORTFOLIO_HEAT * 100


def _hmm_downgrade_threshold() -> float:
    return settings.HMM_RISK_ON_DOWNGRADE_THRESHOLD


def _hmm_risk_off_agreement() -> float:
    return settings.HMM_RISK_OFF_AGREEMENT_THRESHOLD


def _microcap_threshold_musd() -> int:
    return int(settings.MULTIFACTOR_MICROCAP_THRESHOLD / 1_000_000)


def _news_earnings_suppress_hours() -> float:
    return settings.NEWS_EARNINGS_SUPPRESS_HOURS


# Suggested-exit fractions come from engine.advisory.CONFIG (constant per
# process, like the VIX/Sahm thresholds above).
_EXIT_FRACTION_STRONG_PCT = int(_ADVISORY_CONFIG["exit_fraction_strong_sell"] * 100)
_EXIT_FRACTION_NORMAL_PCT = int(_ADVISORY_CONFIG["exit_fraction_normal_sell"] * 100)


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
        "This is informational — in the default advisory mode the platform sends "
        "no orders.  Outside it, orders go only to the local FMP paper ledger; "
        "real-money orders only ever go through the human-confirmed Robinhood "
        "queue.",
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
        lambda: (
            "The suggested fraction of your total capital to put into one position, "
            "derived from the fractional Kelly formula using your real trade history.  "
            f"The formula's raw result is capped at {_kelly_cap_pct()}% "
            "(KELLY_CAP) and then further capped by a per-name advisory ceiling.  "
            "A value of 0.14 means 'up to 14% of your capital' — still advisory only."
        ),
        "#8-understanding-position-sizing-kelly-target",
    ),
    "kelly fraction": _g(
        "Kelly Fraction",
        lambda: (
            "A safety multiplier applied to the raw Kelly bet.  The platform uses "
            f"{_kelly_fraction()} (half-Kelly), which cuts the theoretical bet in "
            "half to reduce the risk of ruin from estimation errors in win-rate "
            "and payoff calculations."
        ),
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
        lambda: (
            "The change in conviction between two pipeline runs for the same ticker.  "
            f"Moves of ≥ {_conv_delta()} are highlighted in the 'Δ Since Last Run' band "
            "at the top of the dashboard report so you can quickly spot meaningful shifts "
            "without reading every row."
        ),
        "#6-understanding-the-output",
    ),
    "calibration": _g(
        "Calibration",
        "A reliability check that asks: 'When the system says conviction 0.80, "
        "does it actually win 80% of the time?'  The Calibration screen shows a chart "
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
        lambda: (
            "A statistical model that runs in the background to give a second opinion "
            "on the macro regime.  It analyzes SPY returns, realized volatility, VIX, "
            "and the yield curve to produce a 'risk-on probability' from 0 to 1.  "
            f"Below {_hmm_downgrade_threshold()}, it can quietly downgrade the regime "
            "from RISK ON to NEUTRAL.  "
            "If the HMM can't run, the platform uses only the rules-based regime — "
            "no crash, no degradation."
        ),
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
        "The platform uses it as the primary vol estimate for position sizing.",
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

    # ── Options (archived) ────────────────────────────────────────────────────
    "iv rank": _g(
        "IV Rank (IVR) — archived",
        "Implied Volatility Rank (where current implied volatility sits in the "
        "past year's range) belonged to the options desk, which was archived to "
        "`legacy/` (steps 3d-4b).  The live pipeline no longer computes IVR or "
        "suggests premium-selling options strategies, so there is no IVR value "
        "to read in the web app.",
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
        "A file-based pause gate (`output/KILL_SWITCH`).  When active, the "
        "advisory pipeline skips generating new recommendations for the current "
        "cycle (advisory mode) or the order manager blocks all submissions "
        "(live mode).  Toggle it from Settings → General in the web app (or the "
        "Commands screen), or run `python -m execution.kill_switch --activate`.",
        "#15-the-kill-switch--pause-gate",
    ),
    "advisory mode": _g(
        "Advisory Mode",
        "The project default (`ADVISORY_ONLY=true`).  The full quant pipeline runs "
        "— data fetch, indicators, forecasts, signals, the dashboard report — but "
        "the broker execution layer is completely quarantined.  No orders are "
        "placed, ever.  The web app shows an 'Advisory Only Mode' status banner "
        "as a permanent reminder.",
        "#advisory-only-mode",
    ),
    "risk gate": _g(
        "Risk Gate",
        "Eleven sequential pre-trade checks that every order must pass before reaching "
        "the broker.  They are: kill-switch halt, max position size, portfolio heat, "
        "correlation, daily loss limit, macro kill switch, HMM regime, stress "
        "scenario, market hours, minimum validation report, and order rate limit.  "
        "A check passes conservatively when its context data is missing.  Blocks "
        "are listed on Mission Control (Risk gate block log).",
        "#safety-tab-formerly-gravity-audit--what-to-check-when-an-order-is-blocked",
    ),
    "portfolio heat": _g(
        "Portfolio Heat",
        lambda: (
            "Total adverse (unrealized) P&L across all open positions as a percentage "
            "of portfolio equity.  If heat rises above "
            f"{_max_portfolio_heat_pct():g}% (MAX_PORTFOLIO_HEAT), the risk gate "
            "blocks new BUY orders to prevent compounding a bad day.  Shown on "
            "Mission Control and in the dashboard report."
        ),
        "#12-the-observability-dashboard",
    ),
    "dead letter": _g(
        "Dead Letter",
        "A per-symbol failure record written when a single ticker's analysis "
        "crashes.  The platform never aborts the entire run because of one bad "
        "ticker — it logs the failure to `output/dead_letter.json` and continues.  "
        "The Pipeline Dashboard lists these failures.  Its 'Retry' button is not "
        "a per-symbol retry: it triggers one full orchestrator-daemon cycle that "
        "re-runs the whole universe and rewrites `dead_letter.json` (requires "
        "`DEAD_LETTER_RETRY_ENABLED`).",
        "#5-running-the-pipeline",
    ),

    # ── Signals & aggregation ─────────────────────────────────────────────────
    "signal weight": _g(
        "Signal Weight",
        "A number that controls how much each signal module contributes to the "
        "final composite score.  The total score = sum of (module_score × weight) "
        "across all active modules.  Weights and enabled modules are tunable in "
        "Settings → Strategy (SIGNAL_WEIGHTS).  By default the macro_regime module "
        "carries the largest weight because it gates everything else.",
        "#17-adjusting-signal-weights",
    ),
    "multifactor signal": _g(
        "Multifactor Signal",
        lambda: (
            "A Fama-French-inspired score that combines four factors: Value (price "
            "vs book/earnings), Quality (ROE and operating margin), Low Volatility, "
            "and Size (smaller stocks get a higher score).  Each factor is "
            "cross-sectionally ranked vs the other tickers in your universe before "
            "being averaged.  Microcap stocks "
            f"(<${_microcap_threshold_musd()}M market cap, MULTIFACTOR_MICROCAP_THRESHOLD) "
            "receive a neutral score to avoid noise."
        ),
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
        "and per-signal summary.  The web app's backend (Mission Control, symbol "
        "pages) reads it, and the 'Δ Since Last Run' band of the dashboard report "
        "is built from its rotated copies.",
        "#12-the-observability-dashboard",
    ),
    "heartbeat": _g(
        "Heartbeat",
        "A timestamp file (`output/heartbeat.txt`) rewritten every 60 seconds "
        "while a standalone `python3 main_orchestrator.py` run is in progress.  "
        "The persistent orchestrator daemon never writes it, so under the daemon "
        "Mission Control's Heartbeat section honestly reads 'No heartbeat' — "
        "use the Pipeline screen for daemon liveness.  When the file exists, "
        "Mission Control labels it Fresh, Slow or Stale by its age.",
        "#12-the-observability-dashboard",
    ),
    "snapshot diff": _g(
        "Snapshot Diff (Δ Since Last Run)",
        "A comparison between the current pipeline run and the previous one.  "
        "Highlights new BUY signals, action flips (e.g. HOLD → BUY), conviction "
        "changes above the threshold, and holdings added or dropped.  "
        "Rendered as the '🔔 Δ Since Last Run' band at the top of the dashboard "
        "report (`output/daily_report_dashboard.html`, listed in the Report Library).",
        "#6-understanding-the-output",
    ),
    "brinson-fachler": _g(
        "Brinson-Fachler Attribution",
        "A classic portfolio performance attribution method that splits 'how did "
        "we beat / underperform the benchmark?' into three effects: "
        "Allocation (did we overweight the right sectors?), "
        "Selection (did we pick the right stocks within each sector?), and "
        "Interaction (the combined effect of both).  Found on the Portfolio "
        "attribution screen.",
        "#reports-tab--live-vs-backtested-provenance--drill-down",
    ),
    "dry run": _g(
        "Dry Run",
        "A mode where the pipeline runs normally including order generation, "
        "but the OrderManager intercepts every intent before reaching the broker.  "
        "Orders are logged but never submitted, so it exercises the full "
        "execution path (risk gate included) without any fills.  Select it as "
        "Simulation mode in Settings → General (writes `ADVISORY_ONLY=false` and "
        "`DRY_RUN=true`).",
        "#advisory-only-mode",
    ),
    "paper trading": _g(
        "Paper Trading",
        "Running the pipeline with real market data and real logic, but against a "
        "simulated broker account (no real money): the local FMP paper ledger, "
        "which fills at live FMP quotes.  The preflight check requires a minimum "
        "paper-trading duration (measured from `PAPER_TRADING_START_DATE`) before "
        "going live.  Only relevant when `ADVISORY_ONLY=false`.",
        "#11-paper-trading-workflow",
    ),
    "watch rules": _g(
        "Watch Rules",
        "YAML-configured rules that trigger phone push notifications (via ntfy) "
        "when an advisory signal changes or a conviction threshold is crossed.  "
        "Stored in `watch_rules.yaml` at the project root and evaluated each cycle "
        "by the orchestrator daemon (its agentic-queue step, in `primary` mode).  "
        "Useful for staying informed between runs without polling the dashboard.",
        "#symbol-watch-alerts-tier-14",
    ),
    "decision journal": _g(
        "Decision Journal",
        "A log where you record whether you acted on, passed, or modified each "
        "advisory recommendation (Calibration and Agentic Trading screens).  "
        "Stored as `output/decision_log.jsonl`.  "
        "Used by the conviction calibration chart to filter to decisions you "
        "actually endorsed, and by the recommendation-tracking section "
        "to measure whether your judgment adds alpha over the raw model.",
        "#manual-execution-journal-reports-tab",
    ),
    "live inventory": _g(
        "Live Inventory",
        "A combined view of your Robinhood held positions + all configured "
        "watchlists, with per-symbol market-data coverage status — the coverage "
        "panel on Settings → Tracked Universe (Universe Transparency shows the "
        "read-only counts).  Coverage "
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
        "of risk-gate block records.  Visible in Mission Control's Circuit "
        "breakers section.  CRITICAL trips "
        "halt everything; WARNING trips are per-symbol blocks.  In advisory mode "
        "these are informational — they show which checks would have blocked an "
        "order if the broker were active.",
        "#safety-tab-formerly-gravity-audit--what-to-check-when-an-order-is-blocked",
    ),

    # ── Archived: autonomous agent loop + trade-signal alerts (Tier 6 / 6.1) ──
    # All four entries describe code that now lives in legacy/ (step 5.5).  They
    # are kept only so the glossary can say so honestly (the Gravity audit pins
    # the "autonomous advisory agent" / "conviction momentum" terms and their
    # guide anchors); none of them is a live surface.
    "autonomous advisory agent": _g(
        "Autonomous Advisory Agent (archived)",
        "A self-pacing advisory loop that was archived to `legacy/` along with "
        "`main.py` (step 5.5); the orchestrator daemon has no equivalent, so it is "
        "not available in a daemon-run deployment (the daemon's own timer and "
        "`POST /run` pace cycles instead).  While it existed it re-ran the "
        "analysis on an adaptive schedule and re-pinged you about high-conviction "
        "signals you had not acted on.  It never placed an order.",
        "#autonomous-advisory-agent",
    ),
    "backlog reminder": _g(
        "Backlog Reminder (archived)",
        "A push notification sent by the archived autonomous agent loop (not part "
        "of the orchestrator daemon) when a high-conviction BUY/SELL had gone "
        "unactioned.  It no longer exists in the live platform; the daemon's "
        "Symbol Watch alerts (see Watch Rules) are the active push alerts.",
        "#autonomous-advisory-agent",
    ),
    "conviction momentum": _g(
        "Conviction Momentum (archived)",
        "An early heads-up ('building' / 'fading') once sent by "
        "`engine/trade_signals.py` from a symbol's conviction trajectory across "
        "cycles.  That module was archived to `legacy/` with the agent loop "
        "(step 5.5), so the alert is no longer sent.  Conviction itself, and its "
        "change between runs (Conviction Delta), are still live.",
        "#trade-signal-alerts",
    ),
    "stop and target proximity": _g(
        "Stop / Target Proximity Alert (archived)",
        "An alert once sent by `engine/trade_signals.py` when a held position's "
        "live price approached a volatility-based stop or a forecast-derived "
        "target.  The module was archived to `legacy/` with the agent loop (step "
        "5.5), so the alert is no longer sent.  The Buy Zone / Sell Zone ranges on "
        "each recommendation are still live.",
        "#trade-signal-alerts",
    ),

    # ── Robinhood execution bridge (Tier 8) ────────────────────────────────────
    "robinhood execution bridge": _g(
        "Robinhood Execution Bridge",
        "The opt-in, paper-first path that lets the platform act on its advice via "
        "the Robinhood Trading MCP.  It is OFF by default.  The orchestrator "
        "daemon only writes a gated, dry-run proposed-order queue (its "
        "agentic-queue step, `DAEMON_AGENTIC_QUEUE_MODE=primary`); a separate Claude Code "
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
        "The `output/execution_queue.json` file the orchestrator daemon writes in review/live "
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
        "Only modules that ran this cycle -- not disabled in Settings → Strategy, "
        "not suppressed by a regime gate -- are shown (Signal Breakdown screen).  "
        "The base neutral "
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
        "A side-by-side view of 2-3 operator-chosen symbols (Pilot Strategy "
        "Comparison screen): final score, "
        "Kelly Target, conviction, GARCH volatility, and the per-module score "
        "breakdown for each -- so a difference in ranking between two symbols "
        "has a direct, inspectable answer instead of just two numbers.",
        "#17-adjusting-signal-weights",
    ),
    # ── Advisory field: suggested SELL exit sizing ───────────────────────────
    "reports.suggested_exit_pct": _g(
        "Suggested Exit %",
        lambda: (
            "The `suggested_exit_pct` advisory field: fraction of your CURRENT held "
            "quantity recommended for exit on a SELL action — "
            f"{_EXIT_FRACTION_STRONG_PCT}% when the loss+bearish-forecast escalation "
            f"(Case A) fired, {_EXIT_FRACTION_NORMAL_PCT}% for a base-signal "
            "SELL/RISK REDUCE (a trim). 0 for BUY/HOLD or when the symbol isn't "
            "held — there is nothing to size in that case."
        ),
        "#price-ranges",
    ),
}

# ---------------------------------------------------------------------------
# TAB_HELP — legacy tab ids -> the web app screen that replaced each tab
# ---------------------------------------------------------------------------
# The Streamlit Command Center that these ids came from was deleted in 2026-09.
# The ids stay as stable lookup keys (other code and tests reference them); every
# description below says where the capability lives in the Pilots PWA today.
# Whatever has no screen is said to be archived — never described as live.

TAB_HELP: Dict[str, TabHelp] = {
    "launcher": _t(
        "launcher",
        "🚀 Launcher (now Pipeline + Console)",
        "Legacy tab id.  Today: the **Pipeline** screen (`/pipeline`) shows the "
        "orchestrator daemon's status, run triggers (full advisory pipeline, data "
        "only, metrics only), run history and the dead-letter queue; the "
        "**Console** screen (`/console`) launches background jobs (preflight "
        "check, test suite, an advisory pipeline cycle, full verification, the "
        "Gravity audit, a validation backtest); the automated schedule is on "
        "Settings → Data & Automation.  A pipeline cycle is no longer purely "
        "informational: depending on the daemon's settings it can place orders on "
        "the local FMP paper ledger and write the Robinhood execution queue — it "
        "never places a real order.",
        ("advisory mode", "heartbeat", "dead letter", "kill switch", "dry run"),
        "#5-running-the-pipeline",
    ),
    "reports": _t(
        "reports",
        "📈 Reports (now Portfolio, Attribution, Calibration)",
        "Legacy tab id; the Reports tab was split across screens.  **Portfolio** "
        "(`/portfolio`): holdings, realized performance and held-vs-signal "
        "reconciliation.  **Portfolio attribution** (`/attribution`): "
        "Brinson-Fachler attribution, factor exposure and correlation clusters.  "
        "**Calibration** (`/calibration`): the conviction reliability diagram, "
        "recommendation tracking, MFE/MAE/Edge Ratio and the Decision Journal "
        "(log what you did with each signal).  **Mission Control** shows portfolio "
        "heat.  Apart from the Decision Journal write, these are analysis views.",
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
        "Browse every report file the platform generates, on the **Report "
        "Library** screen (`/operations/reports`).  It lists the orchestrator "
        "dashboards (`daily_report_dashboard.html` and the volatility-bands "
        "chart, rewritten by each full daemon cycle that reaches its snapshot "
        "step), daily briefings (one per day; the screen can generate today's), "
        "the NotebookLM export, and per-strategy validation reports (once a "
        "strategy has been through the validation harness).  The older "
        "`daily_report.html` was retired with `main.py` and is listed only if an "
        "old copy is still on disk.  HTML reports are download-only until you opt "
        "in to 'View inline'.",
        ("advisory mode", "dead letter", "walk-forward validation", "pbo", "dsr"),
        "#17-report-library",
    ),
    "validation_lab": _t(
        "validation_lab",
        "🔬 Validation Lab (now Commands + Strategy Health)",
        "Legacy tab id; there is no Validation Lab screen.  Run the validation "
        "harness from **Commands** (`/commands`: build a "
        "`python -m scripts.refresh_validations --strategies ...` run or press "
        "Bulk Validate All Strategies; executing from the web app needs "
        "`COMMAND_EXECUTION_ENABLED`) or from the **Console** screen's Run "
        "Backtest form.  It runs as a background job and writes "
        "`reports/*_validation_summary.json`.  Read the results on **Strategy "
        "Health** (each strategy's deployable verdict against the PBO / DSR / "
        "net-Sharpe / Max-Drawdown gates, thresholds imported from "
        "`validation.thresholds`) and in the **Report Library** (summaries plus "
        "the walk-forward / CPCV HTML report).  The harness never calls the "
        "broker.",
        ("walk-forward validation", "cpcv", "pbo", "dsr", "max drawdown"),
        "#18-validation-lab",
    ),
    "settings": _t(
        "settings",
        "⚙️ Settings",
        "The **Settings** screens (`/settings/*`) edit non-secret tunable "
        "parameters and save them through the backend's allowlisted `.env` writer "
        "(General, Data & Automation, Tracked Universe, Brokers, Modules, "
        "Strategy, Runtime Tunables, Feature Flags, the scoped sentiment / "
        "sector / FMP editors, AI, Prompts, and a read-only Settings Reference).  "
        "Secret values (API keys, passwords) are masked and read-only there — edit "
        "those directly in `.env`.  When a change takes effect depends on the "
        "setting: Settings Reference lists each one's liveness (immediately, "
        "restart required, no-op, or env-pinned).",
        ("advisory mode", "dry run"),
        "#3-configuring-your-environment",
    ),
    "strategy_matrix": _t(
        "strategy_matrix",
        "🧩 Strategy Matrix (now Settings → Strategy)",
        "Legacy tab id.  **Settings → Strategy** (`/settings/strategy`, 'Signal "
        "modules') enables or disables individual signal modules, adjusts their "
        "weights after a confirm step, and shows each module's version "
        "fingerprint (sha256 prefix + file modified time) and the meta-label "
        "confidence distribution.  The global execution-mode toggle moved to "
        "**Settings → General** (Advisory / Simulation / Paper / Live; each change "
        "needs typed confirmation).",
        ("signal weight", "advisory mode", "kill switch"),
        "#strategy-matrix-tab--global-execution-mode-toggle",
    ),
    "paper_monitor": _t(
        "paper_monitor",
        "📒 Paper Monitor (now Portfolio + Paper Broker)",
        "Legacy tab id.  The **Portfolio** screen (`/portfolio`) shows your real holdings from the "
        "Robinhood account snapshot (the source of truth for shares, average cost "
        "and unrealized P&L) reconciled against the pipeline's signals; prices "
        "and indicators come from the pipeline — these roles never cross.  The "
        "**Paper Broker** screen (`/paper-broker`) is the local simulated brokerage "
        "(the FMP paper ledger): positions, orders, closed trades, Quick Trade, and "
        "a reset.",
        ("advisory mode", "kelly target", "conviction"),
        "#11-paper-trading-workflow",
    ),
    "gravity": _t(
        "gravity",
        "🛡️ Safety & Gravity Audit (now Strategy Health + Mission Control)",
        "Legacy tab id.  **Strategy Health** (`/strategy-health`) shows each "
        "strategy's PBO / DSR / Sharpe / Max Drawdown gate verdicts and a "
        "Claude-vs-Gemini AI disagreement section.  **Mission Control**'s Circuit "
        "breakers and Risk gate block log show active kill-switch trips and "
        "risk-gate blocks from the last 24 hours.  Run the Gravity AI Review "
        "Suite from the **Console** screen.  The old Dependency Map view has no "
        "screen (the map is `shared/dependency_map.py::impacted_consumers`).  "
        "Review these before authorizing any change in execution mode.",
        ("circuit breaker", "risk gate", "pbo", "dsr", "kill switch"),
        "#safety-tab-formerly-gravity-audit--what-to-check-when-an-order-is-blocked",
    ),
    "options": _t(
        "options",
        "🧮 Options (archived)",
        "Archived.  The Options tab — the premium-selling strategy matrix with "
        "GJR-GARCH volatility, an IV Rank proxy, strike levels and Black-Scholes "
        "Greeks — belonged to the options desk, which was archived to `legacy/` "
        "(steps 3d-4b; see `legacy/README.md`).  There is no Options screen in the "
        "web app, and the live pipeline no longer computes IVR, VRP or options "
        "strategy suggestions.",
        ("iv rank",),
        "#7-reading-the-action-signals",
    ),
    "market_data": _t(
        "market_data",
        "🛰️ Market Data (now Data Explorer)",
        "Legacy tab id.  The **Data Explorer** screen (`/data-explorer`) has a "
        "'Market data connection' section: the active provider (FMP primary, "
        "yfinance fallback), delivery mode and quote TTL, a connection-health "
        "badge for this session, and a per-symbol quote check with typed failure "
        "classification (rate limited, no data, unauthorized, ...) and the "
        "client-observed latency.",
        ("advisory mode",),
        "#5-running-the-pipeline",
    ),
    "observability": _t(
        "observability",
        "📊 Observability (now Mission Control)",
        "The platform's single observability surface is **Mission Control** "
        "(`/observability`).  An attention strip flags anything that needs a look "
        "(circuit-breaker trips, portfolio heat over its limit, risk-gate blocks, "
        "sizing-cap escalations, a stale heartbeat, the macro gate being off), "
        "with an honest 'All clear' otherwise.  Below it: the macro regime gate "
        "control (regime, VIX, Sahm Rule, HY OAS, yield curve, HMM risk-on "
        "probability), portfolio risk and the equity / drawdown chart, then — "
        "under 'Background telemetry' — forecast skill (portfolio-wide and "
        "per-symbol), circuit breakers, the risk-gate block log, system "
        "telemetry, per-symbol data latency, the sizing-cap audit trail, "
        "heartbeat, strategy P&L and a log tail.  The former standalone "
        "`observability/dashboard.py` app is gone.",
        ("heartbeat", "state snapshot", "macro regime", "vix", "hmm"),
        "#12-the-observability-dashboard",
    ),
    "analytics": _t(
        "analytics",
        "📊 Analytics (now Portfolio, Trade History, Activity, Models)",
        "Legacy tab id; its sections now live on separate screens.  Realized "
        "performance and the account-value history are on **Portfolio**; the "
        "reconstructed Robinhood closed-trade ledger (FIFO round trips) is on "
        "**Trade History**; the structured alert log is the **Activity** feed; "
        "the ML model registry (CPCV DSR / PBO, training date, sample size) is on "
        "**The models** (`/models`); news sentiment is on **Sentiment "
        "Dynamics**.  Every source degrades to an empty-state message rather "
        "than a fabricated value.",
        ("advisory mode", "state snapshot", "kelly target"),
        "#12-the-observability-dashboard",
    ),
    "sentiment_dynamics": _t(
        "sentiment_dynamics",
        "💬 Sentiment Dynamics",
        "The **Sentiment Dynamics** screen (`/sentiment`) shows two DISTINCT, "
        "independent sentiment signals for a chosen symbol — never conflated. "
        "(1) News Catalyst Sentiment: the always-on "
        "pipeline's real `news_sentiment` field (FinBERT / keyword-lexicon "
        "over recent FMP headlines), read straight from the latest "
        "state snapshot. (2) Antigravity Agent + GJR-GARCH: an on-demand "
        "LLM-agent news read (sentiment / intensity / credibility) plus a "
        "real per-request GJR-GARCH(1,1,1) fit measuring the asymmetric "
        "leverage effect and shock persistence (α+β+γ/2) over price history. "
        "When the Antigravity agent is unavailable (SDK missing, no "
        "GEMINI_API_KEY, or a failed call) the screen shows an honest "
        "'unavailable' note and blank '—' fields rather than a guessed "
        "number — the GARCH persistence metric is computed independently "
        "and can still show a real value. Read-only and advisory-only; no "
        "orders are ever placed from this screen.",
        ("garch vol",),
        "#sentiment-dynamics-tab",
    ),
    "live_inventory": _t(
        "live_inventory",
        "📦 Live Inventory (now Tracked Universe)",
        "Legacy tab id.  The coverage panel on **Settings → Tracked Universe** "
        "combines your Robinhood holdings, Robinhood watchlists and file-backed "
        "watchlists into one deduped list with per-symbol coverage status (FULL / "
        "QUOTES_ONLY / EQUITY_ONLY / UNCOVERED), so you know which tickers can "
        "support pricing-dependent metrics; **Universe Transparency** "
        "(`/universe`) shows the read-only counts.  'Sync Now' (gated by "
        "`UNIVERSE_SYNC_ENABLED`) persists the discovered list as "
        "`DEFAULT_TICKERS` in `.env`; the daemon builds its own universe from "
        "held ∪ watchlist ∪ discovered symbols and uses `DEFAULT_TICKERS` only "
        "when that union is empty.",
        ("live inventory", "advisory mode"),
        "#4-choosing-your-ticker-universe",
    ),
    "help": _t(
        "help",
        "❓ Help",
        "The **Help & Glossary** screen (`/help`) is a searchable index over the "
        "web app's full glossary (kept in `webapp/src/help/helpContent.ts`), and "
        "each screen also has a 'How this works' panel.  Purely informational.",
        ("advisory mode",),
        "#in-app-help--glossary",
    ),
    "prompts": _t(
        "prompts",
        "📝 Prompts",
        "The **Prompt Registry** screen (`/settings/prompts`) shows the resolved "
        "version and source (pin / remote / disk cache / baseline) of every "
        "AI-facing prompt, with a Sync control (it runs "
        "`python -m prompt_registry sync` as a gated job), a version diff viewer "
        "and a pin / clear-pin control.  Fetched prompts are advisory text only — "
        "they can change what an AI is told, never what the platform is "
        "permitted to do.",
        ("advisory mode",),
        "#16-remote-prompt-updates-prompt-registry",
    ),
    "ai_insights": _t(
        "ai_insights",
        "🪄 AI Insights",
        "Per-symbol AI reads layered on top of the pipeline's own signals, on each "
        "symbol's **Symbol Detail** page (`/symbol/:ticker`): an Opal research "
        "brief (thesis / catalysts / risk factors grounded in real news), a "
        "Claude analyst note, and a Gemini chart-pattern read.  Each card is "
        "generated only when you click its button — nothing calls an AI provider "
        "otherwise — and the **Strategy Health** screen's AI disagreement section "
        "compares the latest Claude and Gemini verdicts per symbol.  Purely "
        "informational: no AI output places or modifies an order.",
        ("advisory mode",),
        "#ai-insights--ai-control-center",
    ),
    "ai_control_center": _t(
        "ai_control_center",
        "🎛️ AI Control Center",
        "The **AI Control Center** screen (`/settings/ai`) shows which LLM "
        "capabilities are on, which provider serves each one, and what happened on "
        "the last real call.  A Capabilities section has one master toggle per "
        "underlying switch (several capabilities share `LLM_COMMENTARY_ENABLED`) "
        "and a provider selector where a capability can be routed to more than "
        "one provider; a Provider telemetry section lists the last real call per "
        "capability.  Writes go to `.env` through the Pilots API "
        "(`LLM_WRITES_ENABLED`); provider API keys stay secret-only in `.env` "
        "(never writable from the app).  Everything is operator-triggered — the "
        "old Command Center's section that started a recurring pipeline run "
        "does not exist; use the Pipeline screen or the daemon's own timer.",
        ("advisory mode", "kill switch"),
        "#ai-insights--ai-control-center",
    ),
}

# ---------------------------------------------------------------------------
# SECTION_HELP — section-level tooltip strings (used in panel headers)
# ---------------------------------------------------------------------------
# Values are ordinarily plain strings.  A value that interpolates a live
# settings.X value is instead a zero-arg callable (see comment above GLOSSARY)
# so it is read fresh on every section_help() call — never call METRIC_HELP /
# SECTION_HELP dict values directly; always go through metric_help()/
# section_help(), which resolve either shape transparently.

SECTION_HELP: Dict[str, Union[str, Callable[[], str]]] = {
    "strategy_matrix.version_registry": (
        "Each module's deployment fingerprint — sha256 prefix + file mtime — "
        "joined with live enable/weight state from `settings`."
    ),
    "strategy_matrix.symbol_comparison": (
        "Compare final score, sizing, and signal-module drivers across 2-3 "
        "symbols side by side (Pilot Strategy Comparison screen)."
    ),
    "brinson_fachler": (
        "Brinson-Fachler decomposes your portfolio return vs a benchmark into "
        "Allocation, Selection, and Interaction effects (Portfolio attribution "
        "screen)."
    ),
    "conviction_calibration": (
        "Reliability diagram (Calibration screen) — bars are empirical win rates per "
        "conviction bin.  The diagonal is 'perfect calibration'.  Requires "
        "conviction-annotated closed trades (accumulated from live advisory runs)."
    ),
    "decision_journal": (
        "Log whether you acted on, passed, or modified each advisory signal "
        "(Calibration and Agentic Trading screens).  "
        "Entries are appended to output/decision_log.jsonl and linked to trades."
    ),
    "snapshot_diff": (
        "Changes since the last pipeline run: new BUYs, action flips, large "
        "conviction moves, and holdings added or dropped."
    ),
    "dead_letter_queue": (
        "Symbols whose analysis failed during the last run (Pipeline screen).  "
        "'Retry' is not a per-symbol retry: it triggers one full orchestrator-daemon "
        "cycle over the whole universe (requires DEAD_LETTER_RETRY_ENABLED)."
    ),
    "circuit_breaker_dashboard": (
        "Active risk-gate trips (Mission Control) derived from the kill-switch "
        "sentinel and the last 24 hours of risk_gate_blocks.jsonl.  "
        "In advisory mode these are informational — no orders exist to block."
    ),
    "dependency_map": (
        "`shared/dependency_map.py::impacted_consumers` maps a degraded data source "
        "(FMP, yfinance, FRED, Robinhood) to the consumers that lose coverage.  "
        "The web app has no screen for it."
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
        "Per-symbol quote latency on Mission Control (needs "
        "`MARKET_DATA_LATENCY_TRACKING_ENABLED`).  The Data Explorer's 'Market "
        "data connection' check shows the client-observed round trip per symbol.  "
        "High latency on a real-time provider (FMP) suggests network issues."
    ),
    "preflight_panel": (
        "`scripts/preflight_check.py` (the Console screen's Preflight Check launcher) "
        "reports pass/fail per check.  "
        "In advisory mode, broker checks are automatically skipped.  "
        "Fix any blocking failure before changing execution mode."
    ),
    "global_execution_mode": (
        "Advisory / Simulation / Paper / Live mode selector on Settings → General.  "
        "Each change writes ADVISORY_ONLY (plus DRY_RUN for non-advisory modes) "
        "together and needs typed confirmation; Live means the automated pipeline "
        "places no orders (real orders go only through the Robinhood queue)."
    ),
    "robinhood_execution_bridge": (
        "Off by default. In 'review' mode the orchestrator daemon writes a gated, "
        "dry-run order queue that only previews. A queue older than "
        f"{_RH_QUEUE_STALE_MIN} "
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
    "pipeline_progress": lambda: (
        "Percentage of pipeline stages completed for the current run "
        "(`output/progress.json`, or the daemon's run status for a daemon-launched "
        f"run).  Clients poll every {_progress_poll_seconds()} seconds while a run "
        "is active, and a progress file older than three polls is treated as "
        "stale (the reading then falls back to a coarse stage count).  The web "
        "app has no progress bar yet — the Pipeline screen shows run status and "
        "history.  This only reflects analysis progress; it places no orders."
    ),
    "observability.snapshot_summary": (
        "Summary of the file-backed state last written by the orchestrator."
    ),
    "observability.macro_gate_write": (
        "The Mission Control toggle writes `MACRO_REGIME_GATE_ENABLED` to `.env` "
        "via `shared/env_io.py`.  Check Settings Reference for when the change "
        "takes effect."
    ),
    "observability.recession_telemetry": (
        "Values are sourced from the last pipeline run's state snapshot "
        "(FRED data).  They reflect conditions at pipeline execution time, "
        "not real-time — trigger a daemon cycle (Pipeline screen) to refresh."
    ),
    "observability.sizing_cap_audit": lambda: (
        "Durable log of position-sizing guardrail events — `sizing/"
        "position_sizer.py`'s `was_capped`/`binding_constraint` telemetry, "
        "persisted every cycle (`sizing/cap_audit_store.py`) instead of being "
        "overwritten. Shows which names have hit KELLY_CAP, "
        "MAX_POSITION_WEIGHT, the portfolio-wide MAX_PORTFOLIO_GROSS cap, or "
        f"cap-aware escalation (a name capped {_sizing_cap_escalation_threshold_cycles()} "
        f"consecutive cycles is down-weighted {_sizing_cap_escalation_factor():.2f}x when "
        f"enabled), and how often — not just this cycle's snapshot. A "
        f"threshold alert fires when ≥{_sizing_cap_alert_threshold_pct()}% of names "
        "are capped in one cycle."
    ),
    "strategy_health_gates": (
        "On the Strategy Health screen, sourced from each strategy's "
        "`reports/<strategy_id>_validation_summary.json` (written by the "
        "validation harness). Evaluated against thresholds in "
        "`validation/thresholds.py` — the same constants used by "
        "`validation/harness.py`."
    ),
    "strategy_matrix.signal_modules": (
        "**Signal modules** (Settings → Strategy) — disable a module or adjust its "
        "weight; saved to `.env` and honored by `SignalAggregator`."
    ),
    "strategy_matrix.regime_multiplier_note": (
        "Note: `regime_multiplier` must keep weight 0.0 — it carries the HMM "
        "second opinion as a sizing multiplier, not a score."
    ),
    "sentiment_dynamics.news_catalyst": (
        "The always-on pipeline's real `news_sentiment` field for this symbol "
        "(FinBERT / keyword-lexicon over recent FMP headlines), read from "
        "the latest state snapshot — not the on-demand Antigravity section below."
    ),
    "sentiment_dynamics.antigravity_agent": (
        "On-demand: an Antigravity LLM agent reads recent headlines for "
        "sentiment/intensity/credibility, plus a real per-request GJR-GARCH(1,1,1) "
        "fit over price history for the asymmetric leverage effect and shock "
        "persistence. Independent of the News Catalyst section above."
    ),
}

# ---------------------------------------------------------------------------
# METRIC_HELP — column/metric-level tooltip strings
# ---------------------------------------------------------------------------
# Same rule as SECTION_HELP above: a value that interpolates a live settings.X
# value is a zero-arg callable, resolved transparently by metric_help().

METRIC_HELP: Dict[str, Union[str, Callable[[], str]]] = {
    "Kelly Target": lambda: (
        "Suggested position size as a fraction of capital (e.g. 0.14 = 14%).  "
        f"Capped at {_kelly_cap_pct()}% by KELLY_CAP and further capped by the "
        "advisory single-name ceiling.  Always advisory — not an instruction to trade."
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
    "HMM Risk-On Probability": lambda: (
        "Hidden Markov Model probability that we are in a risk-on macro regime [0, 1].  "
        f"Below {_hmm_downgrade_threshold()} can downgrade the rules-based regime from "
        "RISK ON to NEUTRAL.  When the risk-off probability (1 − this value) exceeds "
        f"{_hmm_risk_off_agreement()} during RECESSION, lowered kill-switch "
        "thresholds apply."
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
        "Used for position sizing."
    ),
    "Sigma_GARCH": "Same as GARCH Vol — GJR-GARCH annualized volatility.",
    "RSI": "Relative Strength Index (14-period).  Above 70 = overbought; below 30 = oversold.",
    "RSI_2": (
        "Ultra-short RSI (2-period) used for mean-reversion entries.  "
        "Signal fires when RSI(2) < 10 and price > SMA(200) (uptrend filter)."
    ),
    "ATR": "Average True Range — average daily price swing in dollars.",
    "Aroon": "Aroon Oscillator.  Near +100 = strong uptrend; near -100 = strong downtrend.",
    "MACD": "MACD crossover score.  Positive when the MACD line is above its signal line.",
    "Forecast 30d": (
        "30-day blended price forecast from the ARIMA, Monte Carlo, Holt-Winters, "
        "Prophet and CNN-LSTM models.  The blend can be weighted by each model's "
        "recent realized forecast skill (see Mission Control's forecast skill)."
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
    "Portfolio Heat": lambda: (
        "Adverse unrealized P&L as % of equity.  "
        f"Above {_max_portfolio_heat_pct():g}% (MAX_PORTFOLIO_HEAT) the risk gate "
        "blocks new BUY orders."
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
    "News_Sentiment": lambda: (
        "Sentiment score from recent news headlines via FinBERT (neural) or "
        "keyword lexicon fallback.  In [-1, +1].  Suppressed within "
        f"{_news_earnings_suppress_hours():g} h of earnings "
        "(NEWS_EARNINGS_SUPPRESS_HOURS)."
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
    "Meta-Label Composite": (
        "Geometric mean of active signal modules' meta-label confidence "
        "(Lopez de Prado 'is the primary signal correct?' probability), applied "
        "as a multiplier on Kelly Target before the position-size cap.  Always "
        "1.0 (no-op) until a MetaLabeler is trained and registered for a given "
        "signal — see ml/meta_labeling.py.  The web app shows it in Symbol "
        "Detail's sizing breakdown and as the confidence distribution on "
        "Settings → Strategy; it is otherwise baked into Kelly Target."
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
    "robinhood_execution.placed_count": lambda: (
        "Rows in the append-only placement ledger "
        "(`output/execution_placed.jsonl`) — one per real order the agent "
        "submitted (each capped at "
        f"${_rh_max_notional():,.2f} notional/order via "
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
    "reports.suggested_exit_pct": lambda: (
        "Fraction of the currently held quantity recommended for exit on a "
        f"SELL action — {_EXIT_FRACTION_STRONG_PCT}% for the Case A "
        f"loss+bearish-forecast escalation, {_EXIT_FRACTION_NORMAL_PCT}% for a "
        "base-signal trim. 0 when not a held SELL."
    ),

    # ── Models screen — ML model freshness & deployability monitoring ────────
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

    # ── Sentiment Dynamics screen ─────────────────────────────────────────
    "sentiment_dynamics.news_sentiment": (
        "The pipeline's real `news_sentiment` field for this symbol, roughly "
        "in [-1, +1] (FinBERT / keyword-lexicon over recent FMP headlines). "
        "This section is omitted entirely when the symbol has no scored news — "
        "never shown as a fabricated neutral 0."
    ),
    "sentiment_dynamics.sentiment_score": (
        "Antigravity agent's read of recent news, -1.0 (extremely negative) to "
        "1.0 (extremely positive). '—' when the agent is unavailable — never a "
        "fabricated 0."
    ),
    "sentiment_dynamics.sentiment_intensity": (
        "Antigravity agent's read of news volume / emotional magnitude, 0.1 to "
        "1.0. '—' when the agent is unavailable — never a fabricated default."
    ),
    "sentiment_dynamics.credibility_score": (
        "Antigravity agent's read of source credibility, 0.1 (rumor-like) to "
        "1.0 (official/verified). '—' when the agent is unavailable — never a "
        "fabricated default."
    ),
    "sentiment_dynamics.volatility_persistence": (
        "GJR-GARCH(1,1,1) α+β+γ/2 — how long a volatility shock takes to decay. "
        "Computed independently of the Antigravity agent from real price "
        "history, so it can still show a value even when the agent itself is "
        "unavailable. '—' when fewer than 100 daily return observations exist "
        "or the fit fails — never a fabricated default."
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
    """Return the tooltip string for column *key*, or empty string if unknown.

    A METRIC_HELP value that interpolates a live ``settings.X`` value is
    stored as a zero-arg callable (see the comment above METRIC_HELP) rather
    than a plain string — resolved here, on every call, so the returned text
    always reflects the current ``settings.X`` value, never a stale snapshot
    taken at module-import time.
    """
    value = METRIC_HELP.get(key, "")
    return value() if callable(value) else value


def section_help(key: str) -> str:
    """Return the section-level explainer string for *key*, or empty string if unknown.

    Mirrors :func:`metric_help` — an empty string is the correct sentinel for a
    missing key (renders no caption, never raises — CONSTRAINT #6).  Never add a
    default-fallback value here; ``""`` is the intended miss behaviour.  Also
    mirrors :func:`metric_help` in resolving a zero-arg-callable value (a
    live-``settings.X``-sourced entry) on every call, never at import time.
    """
    value = SECTION_HELP.get(key, "")
    return value() if callable(value) else value


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
            if q in entry.term.lower() or q in entry.resolved_plain_english().lower():
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

"""Advisory-cycle input builders, moved out of ``main.py`` (step 5.0).

These are the functions that build what ``engine.advisory.evaluate()`` needs
for one advisory cycle: the symbol universe, the macro DTO, the universe-wide
bars/fundamentals pre-fetch, and the cross-sectional/multifactor pre-compute
context. They used to be private helpers in ``main.py``. Step 5 of the shrink
plan (``.claude/shrink_step5_retire_main_py_implementation_plan.md``) moves the
agentic advisory into the orchestrator daemon, so the builders now live here
where the daemon can import them without importing ``main``.

The code is moved verbatim (PR 5.0 is a no-behaviour-change prep step); only
the leading underscore was dropped from each name. ``main.py`` re-exports every
old underscore name, and ``main.run_once()`` still binds the ``run_once``
injection seams (``_build_universe``, ``_build_macro_dto``,
``_fetch_bars_for_universe``, ``_build_context_extras``) from ``main``'s own
module globals, so ``patch("main._build_universe")`` and friends keep working.

Patch seams for calls made INSIDE this module (``discovery``,
``recently_closed_universe_symbols``, ``load_watchlist``, ``WATCHLIST_FILE``,
``get_macro_engine``, ``get_provider``, ``fetch_fundamentals_for_universe``,
``build_realized_vol_60d_map``) resolve through this module's globals, so a
test that wants to intercept one of them patches
``pipeline.advisory_inputs.<name>``. Patching the ``main.<name>`` re-export
would not reach these internal calls; ``tests/test_advisory_inputs_patch_seams.py``
fails CI if a test tries.

The logger keeps the ``InvestYo.main`` name so the advisory cycle's log output
is unchanged (``pipeline/steps.py`` uses the same logger for the same reason).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Optional

import pandas as pd

from data.market_data import MarketDataProvider, get_provider
from data.robinhood_portfolio import AccountSnapshot
from dto_models import FundamentalDataDTO, MacroEconomicDTO, MarketBarDTO
from pilots.discovery import discovery
from settings import settings
from signals import global_registry
from signals.base import SignalContext

__all__ = [
    "WATCHLIST_FILE",
    "UniverseBuild",
    "build_context_extras",
    "build_macro_dto",
    "build_realized_vol_60d_map",
    "build_universe",
    "build_universe_detailed",
    "fetch_bars_for_universe",
    "fetch_fundamentals_for_universe",
    "get_macro_engine",
    "load_watchlist",
    "recently_closed_universe_symbols",
    "reset_macro_engine_cache",
]

logger = logging.getLogger("InvestYo.main")

WATCHLIST_FILE = "watchlist.txt"      # one ticker per line; '#' lines ignored


# ---------------------------------------------------------------------------
# Per-process MacroEngine reuse (Task A4)
# ---------------------------------------------------------------------------
# build_macro_dto() is called once per run_once() cycle. In --interval / agent
# loop mode, run_once() is called repeatedly WITHIN THE SAME PROCESS. The
# regime/hmm_regime.py HMMRegimeDetector.fit() gate (retrain_freq_days) is only
# meaningful if the SAME detector instance persists across those cycles --
# constructing a fresh MacroEngine (and therefore a fresh, never-fitted
# HMMRegimeDetector) every cycle makes the gate a no-op and forces a full
# EM refit every single cycle. This module-level cache keeps one MacroEngine
# (and its DataEngine) alive for the lifetime of the process, keyed by the
# FRED API key so a mid-process key rotation still gets a correctly-scoped
# engine instead of silently reusing one built for a stale key.
_MACRO_ENGINE_CACHE: Dict[str, Any] = {}


def get_macro_engine(fred_key: str):
    """Return a process-lifetime MacroEngine for ``fred_key``, constructing it
    once and reusing it on subsequent calls so the HMMRegimeDetector's
    retrain_freq_days gate is honored across --interval / agent-loop cycles.

    A distinct cache entry per fred_key means rotating the key mid-process
    (rare, but handled) gets a fresh engine/detector rather than silently
    reusing one fit against the old key's data.
    """
    cached = _MACRO_ENGINE_CACHE.get(fred_key)
    if cached is not None:
        return cached

    from data_engine import DataEngine
    from macro_engine import MacroEngine

    de = DataEngine(fred_key)
    me = MacroEngine(data_engine=de)
    _MACRO_ENGINE_CACHE.clear()  # only one key's engine needs to live at a time
    _MACRO_ENGINE_CACHE[fred_key] = me
    return me


def reset_macro_engine_cache() -> None:
    """Test-only helper: clears the process-lifetime MacroEngine cache so each
    test gets a fresh engine/detector instead of bleeding HMM fit state across
    tests."""
    _MACRO_ENGINE_CACHE.clear()


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------

def load_watchlist(watchlist_file: Optional[str] = None) -> List[str]:
    """Return the union of uppercase tickers from WATCHLIST env var and watchlist.txt.

    Both sources are read (when present) and merged/deduped -- neither one
    takes precedence over the other. Returns an empty list when neither
    source is configured.

    Thin wrapper around ``data.portfolio_sync.load_env_watchlist`` — the
    logic now lives there so ``pipeline/production_steps.py``'s
    ``AsyncDataFetchStep`` (the daemon's per-cycle universe builder) can
    share it instead of never reading WATCHLIST/watchlist.txt at all, which
    was the root cause of a symbol silently never reaching the daemon's
    tracked universe. See docs/known_issues/daemon_universe_watchlist_divergence.md.
    ``WATCHLIST_FILE`` stays a module attribute (read here, not baked into a
    default argument) so a test can redirect it with
    ``monkeypatch.setattr(pipeline.advisory_inputs, "WATCHLIST_FILE", ...)``.

    ``watchlist_file`` overrides that module attribute for one call. The
    daemon's ``AsyncDataFetchStep`` passes its ``RunContext.watchlist_file``
    here (step 5.1); ``main.py`` passes nothing and reads ``WATCHLIST_FILE``.
    """
    from data.portfolio_sync import load_env_watchlist

    return load_env_watchlist(WATCHLIST_FILE if watchlist_file is None else watchlist_file)


def recently_closed_universe_symbols(held: set) -> set:
    """Symbols retained by settings.CLOSED_POSITION_RETENTION_DAYS (a
    fully-sold symbol stays visible to the advisory pipeline for a bounded
    window after its most recent real Robinhood SELL fill). Never raises;
    degrades to an empty set on any failure so a store outage can never
    shrink the universe (CONSTRAINT #6). `held` symbols are excluded --
    retention only matters for a symbol that has already dropped out of
    held positions.
    """
    try:
        retention_days = int(getattr(settings, "CLOSED_POSITION_RETENTION_DAYS", 0) or 0)
    except (TypeError, ValueError):
        return set()
    if retention_days <= 0:
        return set()
    try:
        from data.broker_fills_store import recently_closed_symbols

        recent = recently_closed_symbols(
            retention_days=retention_days,
            max_symbols=settings.CLOSED_POSITION_RETENTION_MAX_SYMBOLS,
        )
        return {s.upper() for s in recent} - held
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_recently_closed_universe_symbols failed (%s) — universe unaffected.", exc
        )
        return set()


@dataclass(frozen=True)
class UniverseBuild:
    """One cycle's resolved universe plus the per-source sets behind it.

    ``symbols`` is exactly what :func:`build_universe` returns. The other
    fields are the inputs, kept so the daemon's ``universe_funnel``
    diagnostic can report per-source counts without re-reading any source.
    """

    symbols: List[str]
    held: FrozenSet[str]
    watchlist: FrozenSet[str]
    discovered: FrozenSet[str]
    recently_closed: FrozenSet[str]

    @property
    def default_tickers_is_fallback(self) -> bool:
        """True when held ∪ watchlist ∪ discovered was empty, i.e. the
        ``DEFAULT_TICKERS`` fallback was eligible to fire this cycle (the
        same presence test the daemon's funnel used before step 5.1, now
        including ``held``). It ignores the rating-exclusion subtraction."""
        return not (self.held or self.watchlist or self.discovered)


def build_universe(snapshot: AccountSnapshot) -> List[str]:
    """Return the evaluation universe (``main.py``'s entry point).

    Thin wrapper around :func:`build_universe_detailed`, which holds the
    logic and is documented there. Since step 5.1 the daemon's
    ``AsyncDataFetchStep`` calls ``build_universe_detailed`` too, so the two
    orchestrators resolve their universe through one function.
    """
    return build_universe_detailed(snapshot).symbols


# Must equal main_orchestrator.PIPELINE_STRATEGY_ID (pinned by a test); not
# imported from there to keep this module off the orchestrator import chain.
_PIPELINE_STRATEGY_ID = "main_pipeline"


def _open_pipeline_paper_symbols() -> set:
    """Symbols the automated pipeline currently holds on the FMP paper ledger.

    Database only (no price marking). They must stay in the evaluation
    universe so the position keeps getting scored and can receive its exit
    signal even after it drops out of the watchlist/scan or is rating-excluded.
    Fails soft to an empty set (CONSTRAINT #6).
    """
    try:
        from data.paper_account_store import PaperAccountStore

        return PaperAccountStore(readonly=True).open_position_symbols(_PIPELINE_STRATEGY_ID)
    except Exception as exc:  # noqa: BLE001 -- universe build must never fail on this
        logger.warning("Universe: could not read open pipeline paper positions: %s", exc)
        return set()


def build_universe_detailed(
    snapshot: Optional[AccountSnapshot],
    *,
    watchlist_file: Optional[str] = None,
) -> UniverseBuild:
    """Return the evaluation universe: held symbols ∪ watchlist, deduped, sorted.

    Shared by ``main.py`` (via :func:`build_universe`) and the persistent
    daemon's ``pipeline/production_steps.py::AsyncDataFetchStep`` (step 5.1).
    ``watchlist_file`` defaults to the module's ``WATCHLIST_FILE``; the
    daemon passes its ``RunContext.watchlist_file``. A ``None`` snapshot (or
    one without positions) is treated as an account with no holdings.

    priority order when building the universe:
      1. Robinhood held positions (always included when available).
      2. WATCHLIST env var or watchlist.txt (always merged in when present).
      3. Discovered scan candidates from `scan_candidates.json` (always merged).
      4. `settings.DEFAULT_TICKERS` (fallback if 1+2+3 are empty).
      5. Recently-closed positions (settings.CLOSED_POSITION_RETENTION_DAYS,
         always merged in LAST — see below for why the ordering matters).

    When ``settings.SYMBOL_RATING_AUTO_DROP_ENABLED`` is on, the held ∪
    watchlist ∪ discovered union is additionally subtracted by whatever
    ``rating.symbol_rating_store.SymbolRatingStore.get_excluded_symbols``
    reports (a non-held symbol on a long enough consecutive-BAD streak — see
    ``rating/symbol_rating.py::should_exclude``). Held symbols are never
    dropped, and the lookup fails OPEN: any exception leaves the universe
    untouched and only logs a warning (CONSTRAINT #6). Both the exclusion
    and the ``DEFAULT_TICKERS`` fallback live inside
    ``data.portfolio_sync.compute_tracked_universe`` (shared with
    ``pipeline/production_steps.py``'s ``AsyncDataFetchStep`` so the daemon
    and this orchestrator can't silently diverge on the logic). If that whole
    union (including the ``DEFAULT_TICKERS`` fallback) is still empty, the
    universe is empty — the Google-Sheets Sheet2 last-resort fallback that
    used to sit here was retired in step 4e (2026-09); see CLAUDE.md.

    Source 5 (recently-closed retention) is unioned in LAST, after both the
    auto-drop subtraction and the empty-fallback decision, deliberately:
      * a retained symbol has ``held=False``, so unioning it before the
        auto-drop subtraction (inside ``compute_tracked_universe``) would let
        it be immediately re-subtracted -- reproducing the exact "sold
        symbol silently disappears" bug this feature exists to fix, through
        a different door;
      * unioning it before the ``if not universe:`` check would silently
        suppress the ``DEFAULT_TICKERS`` fallback on an otherwise-cold
        account (the fallback must be decided on the pre-retention set).
    """
    from data.portfolio_sync import compute_tracked_universe

    positions = getattr(snapshot, "positions", None) or {}
    held = set(positions.keys())
    # Call load_watchlist() with no argument on main.py's path so a test
    # that patches pipeline.advisory_inputs.load_watchlist with a zero-arg
    # stub keeps working.
    watchlist = set(load_watchlist() if watchlist_file is None else load_watchlist(watchlist_file))

    # 3. Discovered candidates
    discovered = set()
    try:
        candidates = discovery(limit=None).get("candidates", [])
        discovered = {c["symbol"].upper().strip() for c in candidates if c.get("symbol")}
        if discovered:
            logger.info("Loaded %d candidates from scan discovery.", len(discovered))
    except Exception as exc:
        logger.warning("Failed to load discovery candidates: %s", exc)

    # Union + rating-exclusion + DEFAULT_TICKERS-fallback-if-empty all live in
    # compute_tracked_universe() now, shared with pipeline/production_steps.py's
    # AsyncDataFetchStep (the daemon's own per-cycle universe builder) so the
    # two can no longer silently diverge on this logic.
    universe = compute_tracked_universe(
        held=held,
        watchlist=watchlist,
        discovered=discovered,
        default_tickers=settings.DEFAULT_TICKERS,
    )
    # If held ∪ watchlist ∪ discovered ∪ DEFAULT_TICKERS are all empty (or
    # rating-exclusion emptied them), `universe` is simply []. The Google
    # Sheets Sheet2 last-resort fallback that used to run here was retired
    # in step 4e (2026-09) — see CLAUDE.md.

    # 5. Recently-closed retention — applied LAST, after both the rating-
    # exclusion subtraction and the DEFAULT_TICKERS fallback decision above
    # (both now live inside compute_tracked_universe()), for the exact
    # reasons in this function's own docstring.
    recently_closed = recently_closed_universe_symbols(held)
    if recently_closed:
        logger.info(
            "Universe: retaining %d recently-closed symbol(s): %s",
            len(recently_closed), ", ".join(sorted(recently_closed)),
        )
    universe = sorted(set(universe) | recently_closed)

    # 6. Open pipeline paper positions -- unioned LAST for the same reasons as
    # retention: never rating-excluded, never suppressing the DEFAULT_TICKERS
    # decision, always scored so the position can receive its exit signal.
    # UniverseBuild.held stays Robinhood-only.
    pipeline_held = _open_pipeline_paper_symbols() - set(universe)
    if pipeline_held:
        logger.info(
            "Universe: retaining %d open pipeline paper position(s): %s",
            len(pipeline_held), ", ".join(sorted(pipeline_held)),
        )
        universe = sorted(set(universe) | pipeline_held)

    logger.info(
        "Universe: %d symbols (%d held, %d watchlist-only, %d discovered, %d recently-closed).",
        len(universe),
        len(held),
        len((watchlist - held) - discovered),
        len(discovered - held),
        len(recently_closed),
    )
    return UniverseBuild(
        symbols=universe,
        held=frozenset(held),
        watchlist=frozenset(watchlist),
        discovered=frozenset(discovered),
        recently_closed=frozenset(recently_closed),
    )


# ---------------------------------------------------------------------------
# Macro context (FRED + HMM second opinion)
# ---------------------------------------------------------------------------

def build_macro_dto() -> MacroEconomicDTO:
    """Fetch FRED macro data and build MacroEconomicDTO with HMM probability.

    Degrades gracefully to neutral defaults when FRED_API_KEY is absent or
    FRED is unreachable.  Never raises.

    data_unavailable is set True on every branch that returns a fully or
    partially fabricated DTO (no FRED_API_KEY, an exception mid-construction,
    or a live macro_raw missing T10Y2Y/BAMLH0A0HYM2/VIXCLS, or a Sahm value
    that came from calculate_sahm_rule's fallback) -- see
    dto_models.py::MacroEconomicDTO.killSwitch/_rules_based_regime for what
    this forces (CONSTRAINT #4/#6: a substituted benign default must never
    read as a real "risk on" measurement for this safety-critical gate).
    """
    # Read via the `settings` singleton, not os.environ — pydantic-settings
    # loads .env into Settings only, never into the real process environment.
    fred_key = (settings.FRED_API_KEY or "").strip()
    if not fred_key:
        logger.info("FRED_API_KEY not configured; using neutral macro defaults.")
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.50,
            high_yield_oas=3.50,
            inflation_rate=3.0,
            nominal_10y=4.5,
            vix_value=18.0,
            sahm_rule_indicator=0.0,
            data_unavailable=True,
        )

    try:
        from macro_engine import macro_killswitch_data_unavailable

        # Reuse ONE MacroEngine (and therefore one HMMRegimeDetector) across
        # every run_once() cycle within this process -- see get_macro_engine()
        # docstring / Task A4. This makes the HMM's retrain_freq_days gate
        # meaningful in --interval / agent-loop mode instead of forcing a full
        # refit every single cycle.
        me = get_macro_engine(fred_key)
        de = me.data_engine
        macro_raw = de.fetch_macro_raw()
        # Populated-but-fabricated blind spot: de.fetch_macro_raw()'s hardcoded
        # emergency fallback populates EVERY key with a benign literal, so
        # macro_killswitch_data_unavailable()'s plain presence check alone
        # would report "available" even during a total FRED outage. See
        # data_engine.py::fetch_macro_raw_detailed()'s docstring.
        macro_raw_fabricated_keys = getattr(de, "last_macro_raw_fabricated_keys", frozenset())

        # SPY history for the HMM regime detector now routes through
        # HistoricalStore.get_bars() (mirroring fetch_bars_for_universe's
        # DB-first pattern a few lines below) instead of always calling
        # DataEngine.fetch_technical_raw(["SPY"]) directly -- closing a gap
        # where this was the one bars fetch in this file that bypassed the
        # DB even though every other symbol's bars already went through it.
        # de.fetch_macro_raw() two lines above (the current-snapshot FRED
        # read feeding the kill switch) is deliberately left untouched --
        # that path must always see the freshest reading, never a cached one.
        spy_df: Optional[pd.DataFrame] = None
        spy_from_store = False
        if settings.HISTORICAL_STORE_ENABLED:
            try:
                from data.historical_store import HistoricalStore
                _spy_store = HistoricalStore()
                market = get_provider()
                _spy_candidate = _spy_store.get_bars("SPY", lookback_days=504, provider=market)
                if _spy_candidate is not None and not _spy_candidate.empty:
                    spy_df = _spy_candidate
                    spy_from_store = True
            except Exception as store_exc:
                logger.debug(
                    "HistoricalStore SPY fetch for HMM unavailable (%s); "
                    "falling back to direct DataEngine fetch.",
                    store_exc,
                )

        if not spy_from_store:
            try:
                spy_raw = de.fetch_technical_raw(["SPY"])
                spy_df = spy_raw.get("SPY")
            except Exception as spy_exc:
                logger.debug("SPY history for HMM unavailable: %s", spy_exc)

        hmm_result = me.compute_hmm_risk_on_probability(spy_df)
        hmm_prob = hmm_result["risk_on_probability"] if hmm_result else None
        hmm_state = hmm_result["regime_state_label"] if hmm_result else None

        # NOTE: SAHMREALTIME is never a key in macro_raw -- de.fetch_macro_raw()
        # only ever populates T10Y2Y/BAMLH0A0HYM2/UNRATE/VIXCLS (see
        # data_engine.py::fetch_macro_raw / _MACRO_HARDCODED_FALLBACK). Reading
        # macro_raw.get("SAHMREALTIME", 0.0) here was therefore dead code --
        # it silently returned 0.0 every cycle regardless of FRED health,
        # meaning this DTO's Sahm-driven kill-switch input never reflected a
        # real reading. Fixed by actually computing it via
        # MacroEngine._calculate_sahm_rule_detailed(), the same primitive
        # pipeline/production_steps.py's MacroStep already uses.
        sahm_val, sahm_used_fallback = me._calculate_sahm_rule_detailed()

        data_unavailable = (
            macro_killswitch_data_unavailable(macro_raw, fabricated_keys=macro_raw_fabricated_keys)
            or sahm_used_fallback
        )

        dto = MacroEconomicDTO(
            yield_curve_10y_2y=float(macro_raw.get("T10Y2Y", 0.5)),
            high_yield_oas=float(macro_raw.get("BAMLH0A0HYM2", 3.5)),
            inflation_rate=float(macro_raw.get("CPIAUCSL_YoY", 2.0)),
            nominal_10y=float(macro_raw.get("DGS10", 4.0)),
            vix_value=float(macro_raw.get("VIXCLS", 18.0)),
            sahm_rule_indicator=sahm_val,
            hmm_risk_on_probability=hmm_prob,
            hmm_regime_state=hmm_state,
            data_unavailable=data_unavailable,
        )
        logger.info(
            "Macro DTO built — regime=%s  VIX=%.1f  HMM=%.2f  data_unavailable=%s.",
            dto.market_regime,
            dto.vix,
            hmm_prob if hmm_prob is not None else float("nan"),
            data_unavailable,
        )
        return dto

    except Exception as exc:
        logger.warning("Macro DTO construction failed (%s); using neutral defaults.", exc)
        return MacroEconomicDTO(
            yield_curve_10y_2y=0.50,
            high_yield_oas=3.50,
            inflation_rate=3.0,
            nominal_10y=4.5,
            vix_value=18.0,
            sahm_rule_indicator=0.0,
            data_unavailable=True,
        )


# ---------------------------------------------------------------------------
# Context pre-compute (cross-sectional ranks + multifactor composites)
# ---------------------------------------------------------------------------

def fetch_bars_for_universe(
    symbols: List[str],
    market: MarketDataProvider,
) -> Dict[str, pd.DataFrame]:
    """Fetch ~450-day OHLCV history for all symbols via the market provider.

    The 12-1m cross-sectional momentum in ``build_context_extras`` needs
    ``252 + 22 + 1 = 275`` *trading* days; fetching only 252 leaves every
    symbol below that floor, so the xsec rank pass silently yields nothing.
    Request 450 calendar days (~310 trading days) to clear the floor with
    headroom (this also maps yfinance to its "2y" period, avoiding a short
    "1y" pull that tops out near 252 rows).

    Returns a dict symbol → DataFrame.  Failures are dead-lettered per symbol
    so one bad ticker never aborts the pre-compute pass.
    """
    _store = None
    if settings.HISTORICAL_STORE_ENABLED:
        try:
            from data.historical_store import HistoricalStore
            _store = HistoricalStore()
        except Exception as exc:
            logger.warning("HistoricalStore unavailable; using direct provider. %s", exc)

    bars: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            if _store is not None:
                df = _store.get_bars(sym, lookback_days=450, provider=market)
            else:
                df = market.get_intraday_bars(sym, lookback_days=450)
            if df is not None and not df.empty:
                bars[sym] = df
        except Exception as exc:
            logger.debug("Bars pre-fetch skipped for %s: %s", sym, exc)
    logger.info("Pre-fetched bars for %d / %d symbols.", len(bars), len(symbols))
    return bars


def fetch_fundamentals_for_universe(
    symbols: List[str],
    market: MarketDataProvider,
) -> Dict[str, FundamentalDataDTO]:
    """Fetch fundamentals for the full universe and build FundamentalDataDTOs.

    Feeds the multifactor raw-input pre-compute in ``build_context_extras``
    (value/quality/low-vol/size come from each DTO's ``raw_info`` via
    ``ProcessingEngine.calculate_fundamental_metrics()``). Same
    HistoricalStore-first-then-direct-provider routing as
    ``engine.advisory.evaluate()``'s own Step 3, generalized across the whole
    universe up front instead of one symbol at a time mid-loop.

    Returns a dict symbol → FundamentalDataDTO. Failures are dead-lettered per
    symbol so one bad ticker never aborts the pre-compute pass.
    """
    _store = None
    if settings.HISTORICAL_STORE_ENABLED:
        try:
            from data.historical_store import HistoricalStore
            _store = HistoricalStore()
        except Exception as exc:
            logger.warning(
                "HistoricalStore unavailable for fundamentals pre-fetch; using direct provider. %s", exc
            )

    fund_dtos: Dict[str, FundamentalDataDTO] = {}
    for sym in symbols:
        try:
            raw: Dict[str, Any] = {}
            if _store is not None:
                raw = _store.get_fundamentals_raw(
                    sym, max_age_days=settings.FUNDAMENTALS_REFRESH_DAYS, provider=market
                ) or {}
            if not raw:
                raw = market.get_fundamentals(sym) or {}
            if raw:
                fund_dtos[sym] = FundamentalDataDTO.from_raw_dict(sym, raw)
        except Exception as exc:
            logger.debug("Fundamentals pre-fetch skipped for %s: %s", sym, exc)
    logger.info("Pre-fetched fundamentals for %d / %d symbols.", len(fund_dtos), len(symbols))
    return fund_dtos


def build_realized_vol_60d_map(bars_dict: Dict[str, pd.DataFrame], processing_engine: Any) -> Dict[str, float]:
    """Per-ticker 60-day annualized realized vol, sourced from
    ``ProcessingEngine.calculate_momentum_metrics()`` (the SAME formula
    ``main_orchestrator.py``'s technical pipeline uses) so the multifactor
    low-volatility factor input is computed identically in both entry points.

    Feeds ``calculate_fundamental_metrics()``'s ``low_vol_score``. Missing or
    insufficient-history (< 253 rows) tickers are simply absent from the
    returned map — NaN downstream, never fabricated (CONSTRAINT #4).
    """
    realized_vol_60d_map: Dict[str, float] = {}
    for sym, df in bars_dict.items():
        try:
            momentum_df = processing_engine.calculate_momentum_metrics(df.copy())
            if momentum_df.empty:
                continue
            vol = momentum_df["Realized_Vol_60D"].iloc[-1]
            if pd.notna(vol):
                realized_vol_60d_map[sym] = float(vol)
        except Exception as exc:
            logger.debug("Realized_Vol_60D skipped for %s: %s", sym, exc)
    return realized_vol_60d_map


def build_context_extras(
    symbols: List[str],
    bars_dict: Dict[str, pd.DataFrame],
    macro_dto: MacroEconomicDTO,
    market: MarketDataProvider,
) -> Dict[str, Any]:
    """Build universe-wide pre-computed signal context for injection into advisory.

    Computes 12-1m cross-sectional momentum ranks and Fama-French multifactor
    composites by running global_registry.run_pre_compute() on a minimal
    universe DataFrame.  The result is passed as context_extras to each
    advisory.evaluate() call so cross-sectional and multifactor signals score
    with real data instead of their neutral-0 fallback.

    Returns an empty dict (and logs a warning) if pre_compute raises.
    """
    # This is a THIRD hand-duplicated copy of the same Jegadeesh-Titman (1993)
    # 12-1m momentum formula -- see main_orchestrator.py::compute_xsec_momentum_ranks
    # (the reference implementation) and pipeline/production_steps.py::
    # _compute_xsec_momentum (the live orchestrator-path copy, whose own
    # docstring names all three and cross-references this one). If
    # SKIP_DAYS/LOOKBACK_DAYS ever change here, change them in both of those
    # too -- tests/test_xsec_momentum_advisory_parity.py numerically verifies
    # all three stay in agreement at their shared default constants and will
    # fail CI on drift (this copy hardcodes the constants as locals rather
    # than parameters, so only the default-constants comparison applies to it).
    SKIP_DAYS = 22       # 1-month skip for Jegadeesh-Titman momentum
    LOOKBACK_DAYS = 252  # 12-month lookback
    REQUIRED = LOOKBACK_DAYS + SKIP_DAYS + 1

    try:
        # ── Step 1: compute 12-1m cross-sectional returns ────────────────────
        xsec_return: Dict[str, float] = {}
        for sym, df in bars_dict.items():
            close = df["Close"].dropna()
            if len(close) < REQUIRED:
                continue
            p_recent = float(close.iloc[-(SKIP_DAYS + 1)])
            p_old = float(close.iloc[-(LOOKBACK_DAYS + 1)])
            if p_old > 0:
                xsec_return[sym] = p_recent / p_old - 1.0

        if xsec_return:
            ret_series = pd.Series(xsec_return)
            xsec_rank_series = ret_series.rank(pct=True, ascending=True)
        else:
            xsec_rank_series = pd.Series(dtype=float)

        # ── Step 1b: fundamentals-derived multifactor raw inputs ─────────────
        # Mirrors main_orchestrator.py's calculate_fundamental_metrics() call so
        # signals/multifactor.py's Value/Quality/Low-Vol/Size composite gets real
        # inputs in this (main.py) advisory path too, instead of silently scoring
        # 0 for every symbol every cycle. Any failure here degrades to an empty
        # dict (below) rather than aborting the whole pre-compute pass.
        fund_metrics: Dict[str, Dict[str, Any]] = {}
        fund_dtos: Dict[str, FundamentalDataDTO] = {}
        try:
            from processing_engine import ProcessingEngine

            _pe = ProcessingEngine()
            realized_vol_60d_map = build_realized_vol_60d_map(bars_dict, _pe)
            fund_dtos = fetch_fundamentals_for_universe(symbols, market)
            fund_metrics = _pe.calculate_fundamental_metrics(fund_dtos, realized_vol_60d_map)
        except Exception as exc:
            logger.warning(
                "Multifactor raw-input pre-compute failed (%s); "
                "MultifactorSignal will score 0 for this cycle.", exc,
            )

        # ── Step 2: build a minimal universe DataFrame for pre_compute ────────
        rows = []
        for sym in symbols:
            df = bars_dict.get(sym)
            price = float(df["Close"].iloc[-1]) if df is not None and not df.empty else 0.0
            fm = fund_metrics.get(sym, {})
            rows.append({
                "Symbol": sym,
                "Price": price,
                "XSec_12_1M": xsec_return.get(sym, float("nan")),
                "XSec_Momentum_Rank": (
                    float(xsec_rank_series[sym])
                    if sym in xsec_rank_series.index
                    else float("nan")
                ),
                "Market Cap": fm.get("Market Cap", float("nan")),
                "book_to_market": fm.get("book_to_market", float("nan")),
                "earnings_yield": fm.get("earnings_yield", float("nan")),
                "quality_factor_score": fm.get("quality_factor_score", float("nan")),
                "low_vol_score": fm.get("low_vol_score", float("nan")),
                "log_market_cap": fm.get("log_market_cap", float("nan")),
            })
        universe_df = pd.DataFrame(rows)

        # ── Step 3: run global_registry.run_pre_compute() ────────────────────
        stub_bar = MarketBarDTO(
            date=datetime.now(),
            ticker="__UNIVERSE__",
            open_price=100.0,
            high_price=100.0,
            low_price=100.0,
            close_price=100.0,
            volume=0,
        )
        stub_fund = FundamentalDataDTO(
            ticker="__UNIVERSE__",
            pe_ratio=None,
            pb_ratio=None,
            dividend_yield=0.0,
            book_value=0.0,
            eps_trailing=0.0,
            dividend_growth_rate=0.0,
            payout_ratio=0.0,
            sector="Unknown",
            company_name="Universe stub",
        )
        shared_ctx = SignalContext(bar=stub_bar, fundamentals=stub_fund, macro=macro_dto)
        global_registry.run_pre_compute(universe_df, shared_ctx)

        logger.info(
            "Context pre-compute: %d xsec ranks, %d multifactor scores.",
            len(shared_ctx.xsec_percentile_ranks),
            len(shared_ctx.multifactor_scores),
        )
        extras: Dict[str, Any] = {
            "xsec_percentile_ranks": shared_ctx.xsec_percentile_ranks,
            "multifactor_scores": shared_ctx.multifactor_scores,
            # Raw 12-1m cross-sectional return per symbol (already computed above
            # as `xsec_return`); surfaced so the advisory path reaches parity with
            # main_orchestrator's rich snapshot for factors.xsec_12_1m. {} when no
            # symbol had enough history — a missing symbol degrades to NaN/null
            # downstream, never a fabricated 0.0 (CONSTRAINT #4).
            "xsec_12_1m": dict(xsec_return),
            "bars": bars_dict,
            "fundamentals": fund_dtos,
        }

        # news_catalyst.pre_compute() (run inside run_pre_compute above) wrote
        # per-symbol FinBERT scores onto shared_ctx.news_sentiment_scores. Empty /
        # absent when the module didn't run (news provider not configured, unregistered) — a
        # symbol absent then degrades to null downstream, never fabricated.
        _news = getattr(shared_ctx, "news_sentiment_scores", None)
        if isinstance(_news, dict) and _news:
            extras["news_sentiment"] = dict(_news)

        # CoVaR proxy (portfolio-wide max pairwise |corr|). Mirrors
        # processing_engine.calculate_technical_metrics()'s Topic-30 computation
        # over the universe returns matrix, so the advisory path reaches parity for
        # risk.covar_proxy. Portfolio-wide scalar (the advisory writer broadcasts it
        # to every symbol, same as the rich path). Emitted only when ≥2 symbols have
        # real returns AND the engine returns a non-zero value — its 0.0 no-data /
        # error sentinel is treated as "unavailable" (null), never a fabricated 0.0
        # (CONSTRAINT #4).
        try:
            from research_engine import AdvancedResearchEngine

            _returns = {
                _s: _d["Close"].pct_change(fill_method=None)
                for _s, _d in bars_dict.items()
                if _d is not None and not _d.empty and len(_d) >= 2
            }
            if len(_returns) >= 2:
                _covar = AdvancedResearchEngine().calculate_portfolio_covar_dependency(
                    pd.DataFrame(_returns)
                )
                if _covar and _covar != 0.0:
                    extras["covar_proxy"] = float(_covar)
        except Exception as _cov_exc:
            logger.debug("CoVaR proxy pre-compute skipped: %s", _cov_exc)

        # Per-symbol post-trade excursion (MFE / MAE / Edge Ratio / Realized
        # Slippage) from the latest CLOSED trade in the shared TransactionsStore +
        # the already-fetched bars. Reuses evaluation_engine.EvaluationEngine's
        # calculate_edge_ratio and calculate_realized_slippage — the SAME two
        # methods evaluate_portfolio() calls to populate dashboard_df's
        # 'MFE'/'MAE'/'Edge Ratio'/'Realized Slippage' columns on the rich
        # orchestrator path (pipeline/production_steps.py's evaluate_portfolio()
        # call), so this is a genuine parity fix, not a different metric under the
        # same name. NOTE: this is distinct from
        # research_engine.AdvancedResearchEngine.calculate_realized_slippage(
        # transactions_df) — a portfolio-wide bps scalar over a Trans-Code/Amount/
        # Commission transactions SHEET that neither path actually threads into the
        # dashboard's 'Realized Slippage' column (that column is overwritten by
        # evaluate_portfolio() later in the rich pipeline) — EvaluationEngine's
        # two-argument, per-symbol calculate_realized_slippage(entry_price,
        # arrival_price) is the real source, and needs only entry price (from the
        # trade record) + current price (the latest close, mirroring the rich
        # path's `row['Price']`), both already available here.
        # A symbol with no closed trade is omitted → NaN/null downstream (honest
        # by construction on a fresh install, lighting up as record_trade()/
        # Robinhood reconstruction accrue history).
        try:
            from data.market_data import get_provider
            from engine.advisory import _get_transactions_store
            from evaluation_engine import EvaluationEngine

            _store = _get_transactions_store()
            _ee = EvaluationEngine()
            # Opt-in intraday-hourly excursion (Phase-1 audit item B2,
            # settings.EXCURSION_INTRADAY_ENABLED): passed through on every
            # call below regardless of the flag -- calculate_edge_ratio itself
            # checks the setting and only uses these when it's True, so this
            # is a no-op (identical to the pre-existing daily-only call) when
            # the flag is off (the default).
            try:
                _intraday_provider = get_provider()
            except Exception:
                _intraday_provider = None
            _excursion: Dict[str, Dict[str, float]] = {}
            for _s, _d in bars_dict.items():
                if _d is None or _d.empty:
                    continue
                try:
                    _th = _store.get_trade_history(_s)
                except Exception:
                    continue
                if _th is None or _th.empty:
                    continue
                _th = _th.copy()
                _th["entry_ts"] = pd.to_datetime(_th["entry_ts"])
                _th = _th.sort_values("entry_ts", ascending=False)
                _latest = _th.iloc[0]
                _exit_ts = _latest.get("exit_ts")
                if _exit_ts is None or pd.isna(_exit_ts):
                    continue  # only a CLOSED trade has a defined hold window
                _entry_price = float(_latest["entry_price"])
                _res = _ee.calculate_edge_ratio(
                    _d, _entry_price, _latest["entry_ts"], _exit_ts,
                    symbol=_s, intraday_provider=_intraday_provider,
                )
                _arrival_price = float(_d["Close"].iloc[-1])
                _slippage = (
                    _ee.calculate_realized_slippage(_entry_price, _arrival_price)
                    if (_entry_price > 0 and _arrival_price > 0)
                    else float("nan")
                )
                _excursion[_s] = {
                    "MFE": _res.get("MFE", float("nan")),
                    "MAE": _res.get("MAE", float("nan")),
                    "Edge Ratio": _res.get("Edge Ratio", float("nan")),
                    "Realized Slippage": _slippage,
                }
            if _excursion:
                extras["excursion"] = _excursion
        except Exception as _exc_exc:
            logger.debug("Excursion pre-compute skipped: %s", _exc_exc)

        return extras

    except Exception as exc:
        logger.warning(
            "Context pre-compute failed (%s); cross-sectional signals will score 0.", exc
        )
        return {}


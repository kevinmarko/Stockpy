"""reporting/options_snapshot.py — persist the options premium matrix to JSON.
==============================================================================

The options premium-selling matrix is computed live in the Streamlit GUI
(``gui/panels/options_matrix.py`` → ``technical_options_engine.build_premium_directive``),
but it is NOT persisted anywhere, so the mobile PWA — whose API
(``api/pilots_api.py``) is AST-guarded against importing
``technical_options_engine`` — has no way to read it.

This module closes that gap the same way the state snapshot works: a
pipeline-side writer (heavy imports are fine here — this lives in ``reporting/``,
not the AST-guarded API) computes each symbol's premium directive and persists
the hydrated matrix to ``output/options_matrix.json``. The PWA then reads that
artifact through the pure ``pilots.options`` reader.

Invariants:

* **Opt-in** — gated behind ``settings.OPTIONS_MATRIX_ENABLED`` (default
  ``False``); returns ``None`` (writes nothing) when disabled, so fresh clones /
  CI are unaffected.
* **Honesty (CONSTRAINT #4)** — every uncomputable numeric leaf is persisted as
  ``null`` (NaN → ``None``), never a fabricated ``0.0``.
* **Dead-letter resilient (CONSTRAINT #6)** — one bad symbol degrades to an
  error-stub row; a total failure writes nothing and never raises into the
  pipeline.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from reporting.atomic_write import atomic_write_json
from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["write_options_matrix", "OPTIONS_MATRIX_FILENAME"]

OPTIONS_MATRIX_FILENAME = "options_matrix.json"


class _MacroProxy:
    """MacroEconomicDTO-shaped stub (``.vix`` / ``.market_regime`` only) so the
    directive's VRP regime gate (VIX ≥ 30 ∨ CREDIT EVENT) fires identically to the
    live GUI path. Mirrors ``gui/panels/options_matrix.py::_MacroProxy``."""

    def __init__(self, vix: float, market_regime: str):
        self.vix = vix
        self.market_regime = market_regime


def _json_safe(value: Any) -> Any:
    """Recursively null-shape a directive for honest JSON (NaN/inf → ``None``)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def write_options_matrix(
    symbols: List[str],
    *,
    vix: float = 15.0,
    market_regime: str = "RISK ON",
    target_dte: int = 30,
    provider: Any = None,
    output_dir: Optional[Path] = None,
) -> Optional[str]:
    """Compute + persist the premium-directive matrix for ``symbols``.

    Returns the written path (str) on success, or ``None`` when the feature is
    disabled or nothing could be written. Fetches quotes/bars via the shared
    market-data provider (like the GUI panel) — the provider's short-TTL bars
    cache means an in-cycle refetch is cheap. Never raises (CONSTRAINT #6).
    """
    if not getattr(settings, "OPTIONS_MATRIX_ENABLED", False):
        return None
    syms = [str(s).upper().strip() for s in (symbols or []) if str(s).strip()]
    if not syms:
        return None

    try:
        from technical_options_engine import build_premium_directive
        from data.market_data import get_provider, MarketDataError
    except Exception as exc:  # noqa: BLE001 — engine/provider unavailable
        logger.warning("options matrix writer unavailable: %s", exc)
        return None

    if provider is None:
        try:
            provider = get_provider()
        except Exception as exc:  # noqa: BLE001
            logger.warning("options matrix: provider construction failed: %s", exc)
            return None

    # ── Optional FMP fundamental-health overlay (settings.FMP_OPTIONS_HEALTH_ENABLED)
    # and earnings-proximity flag (settings.FMP_EARNINGS_ENABLED, reusing the
    # EXISTING durable earnings-events store rather than a fresh fetch — see
    # pipeline/production_steps.py's _apply_fmp_earnings for the identical
    # read pattern this mirrors). Both default False: when off, none of this
    # runs, zero extra network/DB calls, and build_premium_directive's new
    # kwargs all stay at their None defaults — byte-identical to pre-overlay
    # behavior. Import failures degrade the corresponding overlay off for the
    # whole run rather than aborting the writer (CONSTRAINT #6).
    fmp_health_enabled = bool(getattr(settings, "FMP_OPTIONS_HEALTH_ENABLED", False))
    fetch_financial_scores = fetch_key_ratios_ttm = fetch_realized_volatility = None
    if fmp_health_enabled:
        try:
            from data.fmp_feeds_company import fetch_financial_scores, fetch_key_ratios_ttm
            from data.fmp_feeds_market import fetch_realized_volatility
        except Exception as exc:  # noqa: BLE001
            logger.warning("options matrix: FMP health overlay unavailable: %s", exc)
            fmp_health_enabled = False

    # ── Optional FMP market/qualitative-context overlay (settings.
    # FMP_OPTIONS_CONTEXT_ENABLED) — news headlines + peer-group tickers. A
    # DIFFERENT overlay concept than the health block above (market/
    # qualitative context vs. balance-sheet health), so it gets its own gate,
    # even though the call-site pattern (bundled flag check before the loop,
    # independent try/except per sub-fetch inside it) is identical.
    fmp_context_enabled = bool(getattr(settings, "FMP_OPTIONS_CONTEXT_ENABLED", False))
    fetch_stock_news = fetch_peer_group = None
    if fmp_context_enabled:
        try:
            from data.fmp_feeds_company import fetch_stock_news
            from data.fmp_feeds_market import fetch_peer_group
        except Exception as exc:  # noqa: BLE001
            logger.warning("options matrix: FMP context overlay unavailable: %s", exc)
            fmp_context_enabled = False

    # ── Earnings-proximity (settings.FMP_EARNINGS_ENABLED) and analyst
    # consensus (settings.FMP_ANALYST_ENABLED) both reuse EXISTING durable
    # HistoricalStore tables rather than a fresh fetch of their own — see
    # pipeline/production_steps.py's _apply_fmp_earnings / _apply_fmp_analyst
    # for the identical read pattern this mirrors (that step runs earlier in
    # the same cycle, inside StrategyEvalStep, well before StateSnapshotStep
    # calls this writer, so the store is fresh by the time we read it here).
    # ONE shared HistoricalStore instance serves both blocks below.
    fmp_earnings_enabled = bool(getattr(settings, "FMP_EARNINGS_ENABLED", False))
    fmp_analyst_enabled = bool(getattr(settings, "FMP_ANALYST_ENABLED", False))
    store = None
    earnings_as_of: Optional[str] = None
    if fmp_earnings_enabled or fmp_analyst_enabled:
        try:
            from data.historical_store import HistoricalStore

            store = HistoricalStore()
            earnings_as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        except Exception as exc:  # noqa: BLE001
            logger.warning("options matrix: historical store unavailable: %s", exc)
            fmp_earnings_enabled = False
            fmp_analyst_enabled = False

    macro_proxy = _MacroProxy(float(vix), str(market_regime))
    directives: List[Dict[str, Any]] = []
    for symbol in syms:
        try:
            quote = provider.get_latest_quote(symbol)
            bars = provider.get_intraday_bars(symbol, lookback_days=252)

            # Each FMP sub-fetch is independently try/excepted: a failure
            # fetching (say) Altman Z for this symbol must not blank out a
            # days_to_earnings this same symbol already resolved, or prevent
            # the base directive from still being built (CONSTRAINT #6).
            altman_z_score: Optional[float] = None
            piotroski_f_score: Optional[int] = None
            net_debt_ebitda: Optional[float] = None
            fcf_yield: Optional[float] = None
            realized_vol_30d: Optional[float] = None
            days_to_earnings: Optional[int] = None
            analyst_target_consensus: Optional[float] = None
            analyst_target_upside: Optional[float] = None
            analyst_grade_score: Optional[float] = None
            news_snippets: Optional[List[Dict[str, Any]]] = None
            peers_list: Optional[List[str]] = None

            if fmp_health_enabled:
                try:
                    scores = fetch_financial_scores(symbol)
                    altman_z_score = scores.get("altman_z_score")
                    piotroski_f_score = scores.get("piotroski_f_score")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("options matrix: financial-scores failed for %s: %s", symbol, exc)
                try:
                    ratios = fetch_key_ratios_ttm(symbol)
                    net_debt_ebitda = ratios.get("net_debt_ebitda")
                    fcf_yield = ratios.get("fcf_yield")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("options matrix: ratios-ttm failed for %s: %s", symbol, exc)
                try:
                    vol = fetch_realized_volatility(symbol)
                    realized_vol_30d = vol.get("hv_30")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("options matrix: realized-volatility failed for %s: %s", symbol, exc)

            if fmp_context_enabled:
                try:
                    # Capped at 3 (not fetch_stock_news's own default of 5) to
                    # keep the JSON payload and options-matrix UI compact.
                    news_snippets = fetch_stock_news(symbol, limit=3)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("options matrix: stock news failed for %s: %s", symbol, exc)
                try:
                    peers_list = fetch_peer_group(symbol)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("options matrix: peer group failed for %s: %s", symbol, exc)

            if fmp_earnings_enabled and store is not None:
                try:
                    future_rows = store.get_earnings_events(
                        symbol, after=earnings_as_of, limit=1,
                    )
                    if future_rows:
                        event_date = future_rows[0].get("event_date")
                        if event_date:
                            d_next = datetime.strptime(str(event_date)[:10], "%Y-%m-%d").date()
                            d_as_of = datetime.strptime(earnings_as_of, "%Y-%m-%d").date()
                            days_to_earnings = (d_next - d_as_of).days
                except Exception as exc:  # noqa: BLE001
                    logger.debug("options matrix: days-to-earnings failed for %s: %s", symbol, exc)

            if fmp_analyst_enabled and store is not None:
                try:
                    snapshot = store.get_analyst_snapshot(symbol)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("options matrix: analyst snapshot failed for %s: %s", symbol, exc)
                    snapshot = None
                if snapshot:
                    tc = snapshot.get("target_consensus")
                    if tc is not None:
                        try:
                            analyst_target_consensus = float(tc)
                        except (TypeError, ValueError):
                            analyst_target_consensus = None
                    # Mirrors _apply_fmp_analyst's own upside calculation
                    # exactly: NaN/None unless the price is a valid positive
                    # float (a missing/zero/negative price can't anchor an
                    # upside ratio -- CONSTRAINT #4, never fabricate).
                    if analyst_target_consensus is not None:
                        try:
                            price_f = float(quote.price)
                        except (TypeError, ValueError):
                            price_f = None
                        if price_f is not None and price_f > 0:
                            analyst_target_upside = (analyst_target_consensus / price_f) - 1.0
                    gs = snapshot.get("grade_score")
                    if gs is not None:
                        try:
                            analyst_grade_score = float(gs)
                        except (TypeError, ValueError):
                            analyst_grade_score = None

            row = build_premium_directive(
                symbol,
                bars,
                spot_price=float(quote.price),
                is_stale=bool(quote.is_stale),
                target_dte=int(target_dte),
                macro_dto=macro_proxy,
                vrp=None,  # VRP needs an options chain — skip that gate here
                altman_z_score=altman_z_score,
                piotroski_f_score=piotroski_f_score,
                net_debt_ebitda=net_debt_ebitda,
                fcf_yield=fcf_yield,
                days_to_earnings=days_to_earnings,
                realized_vol_30d=realized_vol_30d,
                analyst_target_consensus=analyst_target_consensus,
                analyst_target_upside=analyst_target_upside,
                analyst_grade_score=analyst_grade_score,
                news_snippets=news_snippets,
                peers_list=peers_list,
            )
            directives.append(_json_safe(row))
        except MarketDataError as exc:  # noqa: PERF203
            logger.debug("options matrix: market data error for %s: %s", symbol, exc)
            directives.append(
                {"Symbol": symbol, "Strategy": None, "Action": None,
                 "Integrity_OK": False, "Integrity_Issues": [str(exc)]}
            )
        except Exception as exc:  # noqa: BLE001 — one bad symbol never aborts
            logger.debug("options matrix failed for %s: %s", symbol, exc)
            directives.append(
                {"Symbol": symbol, "Strategy": None, "Action": None,
                 "Integrity_OK": False, "Integrity_Issues": [str(exc)]}
            )

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_dte": int(target_dte),
        "vix": float(vix) if math.isfinite(float(vix)) else None,
        "market_regime": str(market_regime),
        "directives": directives,
    }
    out_dir = Path(output_dir) if output_dir is not None else settings.OUTPUT_DIR
    path = out_dir / OPTIONS_MATRIX_FILENAME
    try:
        atomic_write_json(path, payload)
        logger.info("Wrote options matrix (%d directives) → %s", len(directives), path)
        return str(path)
    except Exception as exc:  # noqa: BLE001 — write failure is non-fatal
        logger.warning("options matrix write failed: %s", exc)
        return None

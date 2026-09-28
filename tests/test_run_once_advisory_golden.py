"""
tests/test_run_once_advisory_golden.py -- frozen-input golden for main.run_once()
===============================================================================

Pins what ``main.run_once()`` recommends (action, conviction,
``suggested_position_pct``, ``suggested_exit_pct``, rationale and the
``key_indicators`` dict), and the ``queue_sources/advisory.json`` and
``execution_queue.json`` bytes ``main.py``'s ``_run_cycle`` writes from it, for
one fixed set of inputs. Step 5 of the
shrink plan (``.claude/shrink_step5_retire_main_py_implementation_plan.md``)
moves main.py's advisory input helpers into ``pipeline/advisory_inputs.py``
(PR 5.0) and later runs the same advisory inside the daemon (PR 5.1+). This
golden is the "nothing moved" gate for PR 5.0: it was captured on
``origin/main`` @ df72833d BEFORE any helper moved, and must stay
byte-identical after.

What is real and what is frozen
-------------------------------
Every input helper runs for real (universe build, macro DTO build, bars and
fundamentals pre-fetch, realized-vol map, cross-sectional/multifactor
pre-compute, CoVaR, excursion), and so does ``engine.advisory.evaluate()``
(technicals, GJR-GARCH, StrategyEngine, the holding-aware overlay, Kelly
sizing). Only the sources of data are frozen:

  * account snapshot   -- a fixed ``AccountSnapshot`` (``main.fetch_account_snapshot``)
  * market provider    -- a fake with seeded, fixed-date bars, fixed quotes and
                          fixed fundamentals (``main.get_provider`` and
                          ``data.market_data.get_provider``)
  * universe inputs    -- ``WATCHLIST`` setting, a real ``watchlist.txt`` in a
                          tmp CWD, a real ``scan_candidates.json`` in a tmp
                          ``OUTPUT_DIR``, retention via
                          ``data.broker_fills_store.recently_closed_symbols``
  * macro              -- fake ``DataEngine``/``MacroEngine`` classes patched at
                          their defining modules (``_get_macro_engine`` imports
                          them lazily), so the real ``_build_macro_dto`` runs
  * forecast           -- a fake ForecastingEngine (the real one fits
                          ARIMA/Holt-Winters/CNN-LSTM/Prophet; too slow and not
                          reproducible for a unit golden) returning a fixed
                          30-day multiple per symbol
  * models             -- no LGBM ranker, no meta-labelers
  * trade history      -- an in-memory TransactionsStore holding one closed MSFT trade
  * ``now``            -- fixed for the queue writers; bars carry fixed dates
  * concurrency        -- ``ADVISORY_MAX_CONCURRENCY=1``
  * network            -- ``socket.connect`` raises

Every patch target is either a ``run_once`` injection seam on ``main``
(which PR 5.0 keeps) or a defining module the moved code imports lazily, so
the golden itself needs no edit when the helpers move.

Regenerate (only when behaviour is SUPPOSED to change) with
``REGEN_RUN_ONCE_GOLDEN=1``. The three fixtures are JSON, stored with a
``.golden`` suffix because the repo ``.gitignore`` ignores ``*.json``.
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "run_once_advisory_golden"
_REC_GOLDEN = GOLDEN_DIR / "recommendations.golden"
_SOURCE_GOLDEN = GOLDEN_DIR / "advisory_source.golden"
_QUEUE_GOLDEN = GOLDEN_DIR / "execution_queue.golden"

_NOW = datetime(2026, 7, 17, 15, 0, tzinfo=timezone.utc)  # Fri 11:00 ET, inside RTH
_N_BARS = 320  # > 252 + 22 + 1, so the 12-1m cross-sectional pass has data

# symbol -> (start price, daily drift, daily vol, seed)
_PATHS: Dict[str, tuple] = {
    "AAPL": (150.0, 0.0012, 0.012, 1),
    "MSFT": (300.0, 0.0006, 0.010, 2),
    "KO": (62.0, -0.0010, 0.008, 3),
    "NVDA": (90.0, 0.0025, 0.020, 4),
    "JNJ": (160.0, 0.0001, 0.007, 5),
    "XOM": (100.0, 0.0018, 0.013, 6),
    "INTC": (40.0, -0.0015, 0.018, 7),
    "SPY": (450.0, 0.0005, 0.009, 8),
}

# symbol -> 30-day forecast as a multiple of the current price
_FORECAST_MULT: Dict[str, float] = {
    "AAPL": 1.06,
    "MSFT": 1.00,
    "KO": 0.93,
    "NVDA": 1.08,
    "JNJ": 1.01,
    "XOM": 1.05,
    "INTC": 0.95,
}

_FUNDAMENTALS: Dict[str, Dict[str, Any]] = {
    "AAPL": {"sector": "Technology", "trailingPE": 28.0, "priceToBook": 40.0,
             "returnOnEquity": 1.5, "operatingMargins": 0.30, "grossMargins": 0.44,
             "marketCap": 2.8e12, "dividendYield": 0.005, "debtToEquity": 150.0},
    "MSFT": {"sector": "Technology", "trailingPE": 33.0, "priceToBook": 12.0,
             "returnOnEquity": 0.38, "operatingMargins": 0.42, "grossMargins": 0.69,
             "marketCap": 3.0e12, "dividendYield": 0.008, "debtToEquity": 40.0},
    "KO": {"sector": "Consumer Defensive", "trailingPE": 24.0, "priceToBook": 10.0,
           "returnOnEquity": 0.40, "operatingMargins": 0.29, "grossMargins": 0.60,
           "marketCap": 2.6e11, "dividendYield": 0.031, "debtToEquity": 160.0},
    "NVDA": {"sector": "Technology", "trailingPE": 60.0, "priceToBook": 50.0,
             "returnOnEquity": 0.9, "operatingMargins": 0.55, "grossMargins": 0.72,
             "marketCap": 2.2e12, "dividendYield": 0.0003, "debtToEquity": 20.0},
    "JNJ": {"sector": "Healthcare", "trailingPE": 15.0, "priceToBook": 5.5,
            "returnOnEquity": 0.22, "operatingMargins": 0.25, "grossMargins": 0.68,
            "marketCap": 3.8e11, "dividendYield": 0.030, "debtToEquity": 45.0},
    "XOM": {"sector": "Energy", "trailingPE": 12.0, "priceToBook": 2.0,
            "returnOnEquity": 0.18, "operatingMargins": 0.15, "grossMargins": 0.30,
            "marketCap": 4.5e11, "dividendYield": 0.033, "debtToEquity": 20.0},
    "INTC": {"sector": "Technology", "trailingPE": 90.0, "priceToBook": 0.9,
             "returnOnEquity": -0.02, "operatingMargins": -0.05, "grossMargins": 0.40,
             "marketCap": 1.7e11, "dividendYield": 0.0, "debtToEquity": 50.0},
}


def _bars(symbol: str) -> pd.DataFrame:
    start, drift, vol, seed = _PATHS[symbol]
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, _N_BARS)
    close = start * np.exp(np.cumsum(rets))
    idx = pd.bdate_range(end="2026-07-16", periods=_N_BARS)
    return pd.DataFrame(
        {
            "Open": close * 0.998,
            "High": close * 1.006,
            "Low": close * 0.994,
            "Close": close,
            "Volume": np.full(_N_BARS, 1_000_000.0),
        },
        index=idx,
    )


_BARS: Dict[str, pd.DataFrame] = {s: _bars(s) for s in _PATHS}


class _FakeMarket:
    """Deterministic stand-in for data.market_data.MarketDataProvider."""

    def get_latest_quote(self, symbol: str):
        from data.market_data import Quote

        price = float(_BARS[symbol]["Close"].iloc[-1])
        return Quote(symbol=symbol, price=price, bid=price - 0.01, ask=price + 0.01,
                     timestamp=_NOW, is_stale=False, source="golden")

    def get_intraday_bars(self, symbol: str, lookback_days: int = 252) -> pd.DataFrame:
        return _BARS[symbol].copy()

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        raw = dict(_FUNDAMENTALS.get(symbol, {}))
        if raw:
            raw["currentPrice"] = float(_BARS[symbol]["Close"].iloc[-1])
            raw["shortName"] = symbol
        return raw


class _FakeForecastingEngine:
    def generate_forecast(self, row, current_price, **_kw) -> Dict[str, Any]:
        mult = _FORECAST_MULT[str(row["Symbol"])]
        return {"Forecast_30": current_price * mult, "Forecast_30_Is_Fallback": False}


class _FakeDataEngine:
    last_macro_raw_fabricated_keys = frozenset()

    def __init__(self, *_a, **_kw) -> None:
        pass

    def fetch_macro_raw(self) -> Dict[str, float]:
        return {"T10Y2Y": 0.42, "BAMLH0A0HYM2": 3.1, "UNRATE": 4.0, "VIXCLS": 16.5}

    def fetch_technical_raw(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        return {s: _BARS[s].copy() for s in symbols if s in _BARS}


class _FakeMacroEngine:
    def __init__(self, data_engine=None, **_kw) -> None:
        self.data_engine = data_engine

    def compute_hmm_risk_on_probability(self, spy_df):
        assert spy_df is not None and len(spy_df) == _N_BARS
        return {"risk_on_probability": 0.72, "regime_state_label": "bull"}

    def _calculate_sahm_rule_detailed(self):
        return (0.10, False)


def _position(symbol: str, qty: float, avg_cost: float, divs: float = 0.0):
    from data.robinhood_portfolio import PortfolioPosition

    price = float(_BARS[symbol]["Close"].iloc[-1])
    mv = qty * price
    pl = mv - qty * avg_cost
    return PortfolioPosition(
        symbol=symbol, quantity=qty, average_cost=avg_cost, current_price=price,
        market_value=mv, unrealized_pl=pl, unrealized_pl_pct=pl / (qty * avg_cost) * 100.0,
        dividends_received=divs, name=symbol,
    )


def _snapshot():
    from data.robinhood_portfolio import AccountSnapshot

    aapl_cost = float(_BARS["AAPL"]["Close"].iloc[-1]) * 0.80   # held at a gain
    msft_cost = float(_BARS["MSFT"]["Close"].iloc[-1]) * 0.95
    ko_cost = float(_BARS["KO"]["Close"].iloc[-1]) * 1.25       # held at a loss
    return AccountSnapshot(
        positions={
            "AAPL": _position("AAPL", 20.0, aapl_cost),
            "MSFT": _position("MSFT", 8.0, msft_cost, divs=40.0),
            "KO": _position("KO", 60.0, ko_cost, divs=300.0),
        },
        buying_power=25_000.0,
        total_equity=100_000.0,
        total_dividends=340.0,
        fetched_at=_NOW,
    )


@dataclass
class _Rendered:
    recommendations: bytes
    advisory_source: bytes
    execution_queue: bytes


def _install_frozen_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Freeze every input source (see the module docstring) and return the
    tmp ``OUTPUT_DIR``. Shared with
    ``tests/test_daemon_advisory_shadow_equivalence.py`` (step 5.2 gate (i)),
    which runs the daemon's advisory + shadow-queue steps on the same inputs."""
    import data.broker_fills_store as bfs
    import data.market_data as md
    import engine.advisory as adv
    import execution.kill_switch as ks
    import main
    import ml.lgbm_ranker as lgbm
    import ml.meta_bootstrap as meta_boot
    from settings import settings
    from transactions_store import TransactionsStore

    # ── network off ──────────────────────────────────────────────────────────
    def _no_network(*_a, **_kw):
        raise OSError("network disabled in run_once golden")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    # ── settings ─────────────────────────────────────────────────────────────
    out = tmp_path / "output"
    out.mkdir()
    for key, val in {
        "OUTPUT_DIR": out,
        "ADVISORY_MAX_CONCURRENCY": 1,
        "HISTORICAL_STORE_ENABLED": False,
        "FRED_API_KEY": "golden-dummy-key",
        "WATCHLIST": "NVDA",
        "DEFAULT_TICKERS": ["SPY"],
        "CLOSED_POSITION_RETENTION_DAYS": 180,
        "CLOSED_POSITION_RETENTION_MAX_SYMBOLS": 25,
        "SYMBOL_RATING_AUTO_DROP_ENABLED": False,
        "SYMBOL_RATING_ENABLED": False,
        "FMP_API_KEY": "",
        "FINNHUB_API_KEY": "",
        "FMP_NEWS_ENABLED": False,
        "ROBINHOOD_EXECUTION_MODE": "review",
        "ROBINHOOD_MAX_NOTIONAL_PER_ORDER": 0.0,
    }.items():
        monkeypatch.setattr(settings, key, val, raising=False)
    monkeypatch.setattr(ks, "KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH")
    monkeypatch.setattr(ks, "SOFT_HALT_FILE", tmp_path / "SOFT_HALT")

    # ── universe inputs ──────────────────────────────────────────────────────
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "watchlist.txt").write_text("# golden\nJNJ\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    (out / "scan_candidates.json").write_text(json.dumps({
        "generated_at": "2026-07-17T12:00:00+00:00",
        "candidates": [{"symbol": "XOM", "action": "BUY", "conviction": 0.7,
                        "scan_name": "golden", "scan_reason": "fixture"}],
    }), encoding="utf-8")
    monkeypatch.setattr(bfs, "recently_closed_symbols", lambda **_kw: ["INTC", "AAPL"])

    # ── data sources ─────────────────────────────────────────────────────────
    market = _FakeMarket()
    monkeypatch.setattr(main, "fetch_account_snapshot", lambda **_kw: _snapshot())
    monkeypatch.setattr(main, "get_provider", lambda: market)
    monkeypatch.setattr(md, "get_provider", lambda: market)
    monkeypatch.setattr("data_engine.DataEngine", _FakeDataEngine)
    monkeypatch.setattr("macro_engine.MacroEngine", _FakeMacroEngine)
    main._reset_macro_engine_cache()
    monkeypatch.setattr(adv, "_get_forecasting_engine", lambda: _FakeForecastingEngine())
    # A tmp FILE database, not ``sqlite:///:memory:``: SQLAlchemy gives each
    # thread its own connection to an in-memory SQLite DB, so a daemon step
    # run through AsyncPipelineRunner's to_thread would see an empty store.
    store = TransactionsStore(f"sqlite:///{tmp_path / 'golden_trades.db'}")
    # One closed MSFT round trip, so the excursion (MFE/MAE/Edge Ratio/
    # Realized Slippage) pre-compute has a real hold window to measure.
    _msft = _BARS["MSFT"]["Close"]
    _tid = store.record_trade("MSFT", "long", datetime(2026, 4, 1),
                              float(_msft.loc["2026-04-01"]), 5.0, strategy="golden")
    store.close_trade(_tid, datetime(2026, 5, 15), float(_msft.loc["2026-05-15"]))
    monkeypatch.setattr(adv, "_get_transactions_store", lambda: store)

    def _no_model():
        raise FileNotFoundError("no LGBM model in run_once golden")

    monkeypatch.setattr(lgbm.LGBMCrossSectionalRanker, "load_latest", staticmethod(_no_model))
    monkeypatch.setattr(meta_boot, "bootstrap_meta_registry", lambda *a, **kw: None)
    return out


def _recommendation_dicts(recommendations) -> List[Dict[str, Any]]:
    return [
        {
            "symbol": r.symbol,
            "action": r.action,
            "conviction": r.conviction,
            "suggested_position_pct": r.suggested_position_pct,
            "suggested_exit_pct": r.suggested_exit_pct,
            "rationale": r.rationale,
            # key_indicators carries what the moved pre-compute produced for
            # this symbol (xsec_12_1m/xsec_momentum_rank, the multifactor
            # z-scores, covar_proxy, the excursion fields) alongside the
            # technicals, so the golden pins the input builders' output
            # directly, not only through its effect on action/conviction.
            "key_indicators": r.key_indicators,
        }
        for r in recommendations
    ]


def _recommendation_bytes(recommendations) -> bytes:
    recs = _recommendation_dicts(recommendations)
    return (json.dumps(recs, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Rendered:
    import main
    from execution.compose import compose_and_emit, write_advisory_source

    out = _install_frozen_inputs(tmp_path, monkeypatch)
    try:
        result = main.run_once()
    finally:
        main._reset_macro_engine_cache()

    assert not result.errors, result.errors
    rec_bytes = _recommendation_bytes(result.recommendations)

    # Mirrors main.py's _run_cycle queue block, with ``now``/``output_dir`` pinned.
    write_advisory_source(result.recommendations, output_dir=out, now=_NOW)
    queue_path = compose_and_emit(result.snapshot, output_dir=out, now=_NOW,
                                  macro_dto=result.macro_dto)
    assert queue_path is not None, "compose must write a queue in review mode"
    return _Rendered(
        recommendations=rec_bytes,
        advisory_source=(out / "queue_sources" / "advisory.json").read_bytes(),
        execution_queue=Path(queue_path).read_bytes(),
    )


# The GJR-GARCH fit (arch's optimizer) lands ~0.2% apart across platforms
# (macOS vs the Linux CI runner), same as tests/test_volatility_garch.py's
# _GARCH_REL_TOL. Only the GARCH vol and the Kelly weights computed from it
# get a tolerance; every other field (actions, conviction, sizing, rationale,
# the pre-compute outputs) stays exact. The queue is capped at 5% per name,
# so it does not depend on these values and is still compared byte-for-byte.
_GARCH_DERIVED_KEYS = frozenset({
    "garch_vol", "kelly_raw", "kelly_target_pre_regime", "kelly_target_post_regime",
})
_GARCH_DERIVED_REL_TOL = 1e-2


def _assert_recommendations_match(actual: bytes, expected: bytes) -> None:
    if actual == expected:
        return
    got = json.loads(actual)
    want = json.loads(expected)
    assert [r["symbol"] for r in got] == [r["symbol"] for r in want]
    for g, w in zip(got, want):
        g_ki, w_ki = g["key_indicators"], w["key_indicators"]
        assert sorted(g_ki) == sorted(w_ki), g["symbol"]
        for key in _GARCH_DERIVED_KEYS & set(w_ki):
            assert g_ki[key] == pytest.approx(w_ki[key], rel=_GARCH_DERIVED_REL_TOL), (
                g["symbol"], key)
        exact_g = {**g, "key_indicators": {k: v for k, v in g_ki.items()
                                           if k not in _GARCH_DERIVED_KEYS}}
        exact_w = {**w, "key_indicators": {k: v for k, v in w_ki.items()
                                           if k not in _GARCH_DERIVED_KEYS}}
        assert json.dumps(exact_g, sort_keys=True) == json.dumps(exact_w, sort_keys=True), (
            g["symbol"])


def test_run_once_recommendations_and_queue_match_golden(tmp_path, monkeypatch):
    rendered = _render(tmp_path, monkeypatch)
    if os.environ.get("REGEN_RUN_ONCE_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        _REC_GOLDEN.write_bytes(rendered.recommendations)
        _SOURCE_GOLDEN.write_bytes(rendered.advisory_source)
        _QUEUE_GOLDEN.write_bytes(rendered.execution_queue)
        pytest.skip("run_once golden regenerated")
    _assert_recommendations_match(rendered.recommendations, _REC_GOLDEN.read_bytes())
    assert rendered.advisory_source == _SOURCE_GOLDEN.read_bytes()
    assert rendered.execution_queue == _QUEUE_GOLDEN.read_bytes()


def test_golden_is_not_vacuous():
    """The golden must cover the whole universe (held, watchlist env,
    watchlist.txt, discovery, retention) and put at least one real intent
    through the queue gate, or a byte match would prove little."""
    recs = json.loads(_REC_GOLDEN.read_text(encoding="utf-8"))
    assert [r["symbol"] for r in recs] == sorted(
        ["AAPL", "MSFT", "KO", "NVDA", "JNJ", "XOM", "INTC"]
    )
    assert {r["action"] for r in recs} >= {"BUY", "SELL"}
    # The pre-compute really ran: every symbol got a cross-sectional rank, a
    # multifactor composite and the portfolio CoVaR proxy, and the closed MSFT
    # trade produced a real excursion.
    import math

    for r in recs:
        ki = r["key_indicators"]
        for key in ("xsec_12_1m", "xsec_momentum_rank", "multifactor_composite", "covar_proxy"):
            assert math.isfinite(ki[key]), (r["symbol"], key)
    msft = next(r for r in recs if r["symbol"] == "MSFT")
    assert math.isfinite(msft["key_indicators"]["mfe"])
    queue = json.loads(_QUEUE_GOLDEN.read_text(encoding="utf-8"))
    assert queue["intents"], "the golden queue carries no intents"

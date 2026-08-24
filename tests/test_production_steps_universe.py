"""
tests/test_production_steps_universe.py
========================================
Regression coverage for the daemon universe-divergence fix (see
docs/known_issues/daemon_universe_watchlist_divergence.md).

Before this fix, pipeline/production_steps.py's AsyncDataFetchStep -- the
step main_orchestrator.py / desktop/daemon_runtime.py's persistent daemon
actually runs every cycle -- reimplemented its own narrower universe union
inline: it never read WATCHLIST env var / watchlist.txt at all (only
main.py::_build_universe() did), and it dropped settings.DEFAULT_TICKERS
outright whenever pilots.discovery.discovery() returned any candidate that
cycle instead of unioning it in. A symbol added via watchlist.txt or
POST /agentic/watch therefore reliably reached main.py's universe but never
the daemon's.

The fix routes both entry points through the same shared
data.portfolio_sync.compute_tracked_universe()/load_env_watchlist() pair.
These tests exercise AsyncDataFetchStep.run() directly with a hand-built
RunContext (ctx.market pre-set so the function skips straight past the
credentials.json/DataEngine-construction branch into
`ctx.symbols = base_symbols`), mirroring
tests/test_production_steps_broker_gate.py's approach of driving a single
production_steps.py entry point directly instead of paying for the full
async orchestrator's heavy engine import chain. Every dependency
AsyncDataFetchStep.run() touches beyond the universe-building lines under
test is patched to complete quickly and harmlessly.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest import mock

import pandas as pd

from pipeline.context import RunContext
from pipeline.production_steps import AsyncDataFetchStep


def _make_ctx() -> RunContext:
    ctx = RunContext(
        force_account=False,
        started_at=datetime.now(),
        watchlist_file="watchlist.txt",
        fetch_account_snapshot_fn=lambda *a, **k: None,
        build_universe_fn=lambda *a, **k: [],
        build_macro_dto_fn=lambda *a, **k: None,
        get_provider_fn=lambda *a, **k: None,
        fetch_bars_fn=lambda *a, **k: {},
        build_context_extras_fn=lambda *a, **k: {},
        advisory_evaluate_fn=lambda *a, **k: None,
    )
    # Pre-set ctx.market so AsyncDataFetchStep.run() takes the `else` branch
    # (`ctx.symbols = base_symbols`) and never touches the
    # credentials.json / DataEngine-construction branch at all.
    ctx.market = mock.MagicMock(name="fake_market_provider")
    return ctx


async def _ok_fetch(de, tickers):
    _ = de, tickers
    df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]})
    return {}, {}, {t: df for t in tickers} if tickers else {"AAPL": df}


def _inactive_kill_switch():
    return type("K", (), {"is_active": lambda self: False, "reason": lambda self: None})()


def _run_step(
    ctx: RunContext,
    *,
    watchlist_file_tickers=None,
    watchlist_env: str = "",
    discovered_candidates=None,
    default_tickers=(),
    tmp_path,
    monkeypatch,
):
    """Drive AsyncDataFetchStep.run() with every non-universe-building
    dependency patched to complete quickly, and the three real universe
    inputs (watchlist.txt, WATCHLIST env, discovery candidates) under the
    caller's control."""
    wl_path = tmp_path / "watchlist.txt"
    if watchlist_file_tickers:
        wl_path.write_text("\n".join(watchlist_file_tickers) + "\n")
    ctx.watchlist_file = str(wl_path)

    monkeypatch.setenv("WATCHLIST", watchlist_env)
    if not watchlist_env:
        monkeypatch.delenv("WATCHLIST", raising=False)

    monkeypatch.setattr(
        "pilots.discovery.discovery",
        lambda *a, **kw: {"candidates": [{"symbol": s} for s in (discovered_candidates or [])]},
    )
    monkeypatch.setattr("settings.settings.DEFAULT_TICKERS", list(default_tickers), raising=False)
    monkeypatch.setattr("settings.settings.SYMBOL_RATING_AUTO_DROP_ENABLED", False, raising=False)

    step = AsyncDataFetchStep()
    with mock.patch("main_orchestrator.fetch_account_snapshot", side_effect=RuntimeError("no RH in test")), \
         mock.patch("main_orchestrator.fetch_all_data_async", _ok_fetch), \
         mock.patch("main_orchestrator.GlobalKillSwitch", lambda *a, **k: _inactive_kill_switch()), \
         mock.patch("main_orchestrator._mark_data_refreshed", lambda: None):
        asyncio.run(step.run(ctx))
    return ctx


class TestDaemonUniverseReadsWatchlist:
    """The core Decision-A regression: a watchlist.txt-only symbol (no RH
    holding, no discovery candidate, no DEFAULT_TICKERS) must reach
    ctx.symbols after AsyncDataFetchStep.run() -- it never did before this
    fix."""

    def test_watchlist_file_symbol_reaches_ctx_symbols(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(),
            watchlist_file_tickers=["ZZZZ"],
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        assert "ZZZZ" in ctx.symbols

    def test_watchlist_env_var_alone_reaches_ctx_symbols(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(),
            watchlist_env="YYYY",
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        assert "YYYY" in ctx.symbols


class TestDaemonUniverseWatchlistSurvivesAlongsideDiscovery:
    """Before this fix, `base_symbols = discovered_symbols if discovered_symbols
    else DEFAULT_TICKERS` meant a watchlist.txt symbol was invisible any cycle
    scan-discovery had ANY candidate -- watchlist was never read at all, so it
    couldn't even participate in the union. This is the scenario where the old
    code and the fixed code produce genuinely different results: old code
    would have returned only ["DISC"] here, silently losing WLST."""

    def test_watchlist_and_discovered_both_present_default_tickers_excluded(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(),
            watchlist_file_tickers=["WLST"],
            discovered_candidates=["DISC"],
            default_tickers=["DFLT"],
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        assert "WLST" in ctx.symbols
        assert "DISC" in ctx.symbols
        # combined (watchlist ∪ discovered) is non-empty, so DEFAULT_TICKERS
        # is correctly NOT unioned in -- matching main.py's own long-standing
        # fallback-only semantics, unchanged by this fix.
        assert "DFLT" not in ctx.symbols

    def test_discovery_alone_correctly_excludes_default_tickers(self, tmp_path, monkeypatch):
        """Non-regression check: DEFAULT_TICKERS is fallback-only (used when
        the whole watchlist ∪ discovered union is empty), not "used whenever
        discovery is empty" -- this was already true before the fix and must
        stay true after it."""
        ctx = _run_step(
            _make_ctx(),
            discovered_candidates=["DISC"],
            default_tickers=["DFLT"],
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        assert ctx.symbols == ["DISC"]

    def test_no_discovery_still_falls_back_to_default_tickers(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(),
            default_tickers=["DFLT"],
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        assert ctx.symbols == ["DFLT"]


class TestDaemonUniverseUnionsAllSources:
    def test_watchlist_discovered_and_default_tickers_all_union(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(),
            watchlist_file_tickers=["WLST"],
            discovered_candidates=["DISC"],
            default_tickers=["DFLT"],
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        # watchlist ∪ discovered is non-empty, so DEFAULT_TICKERS is NOT
        # unioned in here (fallback-only semantics, matching main.py).
        assert "WLST" in ctx.symbols
        assert "DISC" in ctx.symbols
        assert "DFLT" not in ctx.symbols

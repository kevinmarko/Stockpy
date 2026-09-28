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

Step 5.1 (2026-09) goes one step further: AsyncDataFetchStep now calls
pipeline.advisory_inputs.build_universe_detailed() -- the exact function
main.py's _build_universe() wraps -- so the daemon also gains main.py's
held-in-fallback rule (DEFAULT_TICKERS fires only when held, watchlist AND
discovered are all empty) and closed-position retention. The
TestDaemonMatchesMainUniverse class pins that the two produce the identical
universe for the same inputs.
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
from datetime import datetime, timezone
from unittest import mock

import pandas as pd
import pytest

from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition
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


def _snapshot(held=()) -> AccountSnapshot:
    positions = {
        sym: PortfolioPosition(
            symbol=sym, quantity=1.0, average_cost=10.0, current_price=11.0,
            market_value=11.0, unrealized_pl=1.0, unrealized_pl_pct=10.0,
            dividends_received=0.0, name=sym,
        )
        for sym in held
    }
    return AccountSnapshot(
        positions=positions, buying_power=0.0, total_equity=0.0,
        total_dividends=0.0, fetched_at=datetime.now(timezone.utc),
    )


class _FakeRatingStore:
    def __init__(self, *, excluded=(), readonly=False):
        self._excluded = set(excluded)
        self.readonly = readonly

    def get_excluded_symbols(self, *, threshold_cycles, known_symbols=None):
        return set(self._excluded)


def _set_universe_inputs(
    *,
    watchlist_file_tickers=None,
    watchlist_env: str = "",
    discovered_candidates=None,
    default_tickers=(),
    recently_closed=(),
    rating_excluded=None,
    tmp_path,
    monkeypatch,
) -> str:
    """Pin every universe input both orchestrators read. Returns the
    watchlist file path."""
    wl_path = tmp_path / "watchlist.txt"
    if watchlist_file_tickers:
        wl_path.write_text("\n".join(watchlist_file_tickers) + "\n")

    monkeypatch.setattr("settings.settings.WATCHLIST", watchlist_env, raising=False)
    # Both orchestrators call discovery()/recently_closed_universe_symbols()
    # from inside pipeline.advisory_inputs, so patch them there.
    monkeypatch.setattr(
        "pipeline.advisory_inputs.discovery",
        lambda *a, **kw: {"candidates": [{"symbol": s} for s in (discovered_candidates or [])]},
    )
    retained = {s.upper() for s in recently_closed}
    monkeypatch.setattr(
        "pipeline.advisory_inputs.recently_closed_universe_symbols",
        lambda held: set(retained) - set(held),
    )
    monkeypatch.setattr("settings.settings.DEFAULT_TICKERS", list(default_tickers), raising=False)
    if rating_excluded is None:
        monkeypatch.setattr("settings.settings.SYMBOL_RATING_AUTO_DROP_ENABLED", False, raising=False)
    else:
        import rating.symbol_rating_store as rating_store_mod

        monkeypatch.setattr("settings.settings.SYMBOL_RATING_AUTO_DROP_ENABLED", True, raising=False)
        monkeypatch.setattr("settings.settings.SYMBOL_RATING_DROP_THRESHOLD_CYCLES", 5, raising=False)
        monkeypatch.setattr(
            rating_store_mod, "SymbolRatingStore",
            lambda **kw: _FakeRatingStore(excluded=rating_excluded, **kw),
        )
    return str(wl_path)


def _run_step(
    ctx: RunContext,
    *,
    held=None,
    tmp_path,
    monkeypatch,
    **inputs,
):
    """Drive AsyncDataFetchStep.run() with every non-universe-building
    dependency patched to complete quickly, and the real universe inputs
    (held positions, watchlist.txt, WATCHLIST env, discovery candidates,
    DEFAULT_TICKERS, retention, rating auto-drop) under the caller's
    control. ``held=None`` makes the account snapshot fetch fail."""
    ctx.watchlist_file = _set_universe_inputs(tmp_path=tmp_path, monkeypatch=monkeypatch, **inputs)

    if held is None:
        snapshot_patch = mock.patch(
            "main_orchestrator.fetch_account_snapshot", side_effect=RuntimeError("no RH in test"),
        )
    else:
        snapshot_patch = mock.patch(
            "main_orchestrator.fetch_account_snapshot", return_value=_snapshot(held),
        )

    step = AsyncDataFetchStep()
    with snapshot_patch, \
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


class TestDaemonHeldInFallbackRule:
    """Step 5.1: held symbols are part of the union compute_tracked_universe()
    decides the DEFAULT_TICKERS fallback on. Before 5.1 the daemon left held
    out, so an empty watchlist + empty discovery pulled DEFAULT_TICKERS in
    even while positions were held."""

    def test_held_positions_suppress_default_tickers_fallback(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(), held=["HELD"], default_tickers=["DFLT"],
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert ctx.symbols == ["HELD"]
        funnel = ctx.context_extras["universe_funnel"]
        assert funnel["default_tickers_is_fallback"] is False
        assert funnel["held_positions_added"] == 1
        assert funnel["tracked_universe_before_held"] == 0
        assert funnel["tracked_universe_total"] == 1

    def test_snapshot_failure_still_falls_back_to_default_tickers(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(), held=None, default_tickers=["DFLT"],
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert ctx.symbols == ["DFLT"]
        assert ctx.context_extras["robinhood_positions"] == {}
        assert ctx.context_extras["universe_funnel"]["default_tickers_is_fallback"] is True

    def test_held_symbols_still_reach_robinhood_positions(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(), held=["HELD"], watchlist_file_tickers=["WLST"],
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert set(ctx.context_extras["robinhood_positions"]) == {"HELD"}
        assert ctx.symbols == ["HELD", "WLST"]


class TestDaemonClosedPositionRetention:
    """Step 5.1: the daemon gains main.py's closed-position retention,
    unioned LAST -- after the rating auto-drop subtraction and after the
    DEFAULT_TICKERS fallback decision."""

    def test_retained_symbol_added(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(), held=["HELD"], recently_closed=["SOLD"],
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert ctx.symbols == ["HELD", "SOLD"]
        funnel = ctx.context_extras["universe_funnel"]
        assert funnel["recently_closed_added"] == 1
        # Neither held nor retained symbols count as "before held".
        assert funnel["tracked_universe_before_held"] == 0
        assert funnel["tracked_universe_total"] == 2

    def test_retention_does_not_suppress_default_tickers_fallback(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(), held=None, default_tickers=["DFLT"], recently_closed=["SOLD"],
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert ctx.symbols == ["DFLT", "SOLD"]

    def test_retention_survives_rating_auto_drop(self, tmp_path, monkeypatch):
        ctx = _run_step(
            _make_ctx(), held=["HELD"], watchlist_file_tickers=["BAD"],
            recently_closed=["SOLD"], rating_excluded={"BAD", "SOLD", "HELD"},
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        # BAD is watchlist-only and excluded; HELD is never dropped; SOLD is
        # unioned after the subtraction, so the exclusion can't reach it.
        assert ctx.symbols == ["HELD", "SOLD"]

    def test_mock_data_engine_mode_keeps_aapl_plus_held(self, tmp_path, monkeypatch):
        """No live data configured (the test-suite default): the universe is
        still AAPL plus held, as before 5.1. Retention/watchlist don't apply
        to synthetic cycles."""
        ctx = _make_ctx()
        ctx.market = None
        ctx = _run_step(
            ctx, held=["HELD"], watchlist_file_tickers=["WLST"], recently_closed=["SOLD"],
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert ctx.symbols == ["AAPL", "HELD"]


_PARITY_SCENARIOS = {
    "everything": dict(
        held=["HELD", "BOTH"], watchlist_file_tickers=["WLST", "BOTH"], watchlist_env="ENVW",
        discovered_candidates=["DISC"], default_tickers=["DFLT"], recently_closed=["SOLD", "HELD"],
    ),
    "held_only_with_defaults": dict(held=["HELD"], default_tickers=["DFLT"]),
    "empty_account_defaults_fallback": dict(held=[], default_tickers=["DFLT", "SPY"]),
    "empty_account_defaults_plus_retention": dict(
        held=[], default_tickers=["DFLT"], recently_closed=["SOLD"],
    ),
    "discovery_only": dict(held=[], discovered_candidates=["DISC"], default_tickers=["DFLT"]),
    "auto_drop": dict(
        held=["HELD"], watchlist_file_tickers=["BAD", "GOOD"], discovered_candidates=["DBAD"],
        recently_closed=["SOLD"], rating_excluded={"BAD", "DBAD", "HELD", "SOLD"},
    ),
    "auto_drop_empties_union": dict(
        held=[], watchlist_file_tickers=["BAD"], default_tickers=["DFLT"],
        rating_excluded={"BAD"},
    ),
    "nothing_at_all": dict(held=[]),
}


class TestDaemonMatchesMainUniverse:
    """The step 5.1 equivalence gate at the unit level: for identical inputs
    (held, WATCHLIST env, watchlist.txt, discovered, retention,
    DEFAULT_TICKERS fallback, rating auto-drop) the daemon's
    AsyncDataFetchStep and main.py's _build_universe() produce the same
    universe."""

    @pytest.mark.parametrize("scenario", sorted(_PARITY_SCENARIOS))
    def test_same_universe(self, scenario, tmp_path, monkeypatch):
        import main
        import pipeline.advisory_inputs as ai

        inputs = dict(_PARITY_SCENARIOS[scenario])
        held = inputs.pop("held")

        ctx = _run_step(_make_ctx(), held=held, tmp_path=tmp_path, monkeypatch=monkeypatch, **inputs)

        # main.py reads WATCHLIST_FILE; point it at the same file the daemon read.
        monkeypatch.setattr(ai, "WATCHLIST_FILE", ctx.watchlist_file)
        main_universe = main._build_universe(_snapshot(held))

        assert ctx.symbols == main_universe

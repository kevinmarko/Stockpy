"""
tests/test_advisory_inputs_watchlist_macro.py
=============================================
Offline unit tests for ``pipeline/advisory_inputs.py``'s ``load_watchlist()``,
``build_universe()``, ``get_macro_engine()`` and ``build_macro_dto()`` -- the
input builders the orchestrator daemon uses for the advisory overlay and the
universe.

Retargeted from ``tests/test_run_once.py`` (which exercised the same helpers
through ``main.py``'s re-exports) when ``main.py`` was archived in shrink step
5.5; the ``run_once()``/agent-loop tests went to ``legacy/tests/``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import pipeline.advisory_inputs as ai


# ---------------------------------------------------------------------------
# Test fixtures / factories
# ---------------------------------------------------------------------------

def _make_snapshot(
    positions: Optional[Dict[str, Any]] = None,
    buying_power: float = 50_000.0,
    total_equity: float = 100_000.0,
) -> MagicMock:
    """Return a MagicMock that behaves like AccountSnapshot."""
    snap = MagicMock()
    snap.positions = positions or {}
    snap.buying_power = buying_power
    snap.total_equity = total_equity
    snap.total_dividends = 0.0
    snap.fetched_at = datetime.now(timezone.utc)
    snap.age_hours.return_value = 0.1
    snap.is_stale.return_value = False
    return snap


def _make_position(symbol: str, qty: float = 10.0, avg_cost: float = 100.0) -> MagicMock:
    pos = MagicMock()
    pos.symbol = symbol
    pos.quantity = qty
    pos.average_cost = avg_cost
    pos.dividends_received = 5.0
    pos.market_value = qty * avg_cost
    pos.unrealized_pl = 0.0
    pos.name = symbol
    return pos


@pytest.fixture(autouse=True)
def _isolate_scan_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize discovery() (patched on pipeline.advisory_inputs, where
    build_universe() calls it since step 5.0) so every test's universe-building
    assertions are deterministic regardless of whether a real
    ~/.stockpy_local/output/scan_candidates.json happens to exist on the
    machine running the suite (e.g. from a real agentic-discovery skill
    run). build_universe() unconditionally unions discovery()'s scan
    candidates into the universe, and
    pilots.discovery.discovery() reads settings.OUTPUT_DIR -- a
    machine-global settings.LOCAL_DATA_ROOT path, not a repo/test-scoped
    one, so neither monkeypatch.chdir(tmp_path) nor a fresh checkout
    isolates it. A test that wants to exercise the merge itself should
    override this fixture's patch with its own monkeypatch.setattr call."""
    monkeypatch.setattr("pipeline.advisory_inputs.discovery", lambda *a, **kw: {"candidates": []})


# ---------------------------------------------------------------------------
# load_watchlist tests
# ---------------------------------------------------------------------------

class TestLoadWatchlist:
    """Tests for ai.load_watchlist()."""

    def test_from_env_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.setattr(ai.settings, "WATCHLIST", "AAPL, MSFT, GOOG")
        # Isolate from a real repo-root watchlist.txt (ai.load_watchlist() reads
        # a bare relative "watchlist.txt" from the CWD) -- without this, an
        # operator checkout with a populated watchlist.txt leaks its real
        # tickers into this env-var-only assertion.
        monkeypatch.chdir(tmp_path)
        result = ai.load_watchlist()
        assert result == ["AAPL", "MSFT", "GOOG"]

    def test_env_var_and_file_are_merged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """WATCHLIST env var and watchlist.txt are a genuine union, not precedence."""
        monkeypatch.setattr(ai.settings, "WATCHLIST", "TSLA")
        (tmp_path / "watchlist.txt").write_text("NVDA\nAMD\n")
        monkeypatch.chdir(tmp_path)
        result = ai.load_watchlist()
        assert set(result) == {"TSLA", "NVDA", "AMD"}

    def test_env_var_and_file_union_with_overlap_deduped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Different tickers in each source are unioned; overlapping ones are deduped."""
        monkeypatch.setattr(ai.settings, "WATCHLIST", "TSLA,NVDA")
        (tmp_path / "watchlist.txt").write_text("NVDA\nAMD\n")
        monkeypatch.chdir(tmp_path)
        result = ai.load_watchlist()
        assert set(result) == {"TSLA", "NVDA", "AMD"}
        assert len(result) == 3  # NVDA not duplicated

    def test_from_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(ai.settings, "WATCHLIST", "")
        wl = tmp_path / "watchlist.txt"
        wl.write_text("NVDA\n# comment line\nAMD\n\n  INTC  \n")
        monkeypatch.chdir(tmp_path)
        result = ai.load_watchlist()
        assert result == ["NVDA", "AMD", "INTC"]

    def test_empty_when_neither_configured(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(ai.settings, "WATCHLIST", "")
        monkeypatch.chdir(tmp_path)  # no watchlist.txt here
        assert ai.load_watchlist() == []

    def test_env_empty_string_treated_as_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(ai.settings, "WATCHLIST", "   ")
        monkeypatch.chdir(tmp_path)
        assert ai.load_watchlist() == []


# ---------------------------------------------------------------------------
# build_universe tests
# ---------------------------------------------------------------------------

class TestBuildUniverse:
    """Tests for build_universe()."""

    def test_held_only_no_watchlist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(ai.settings, "WATCHLIST", "")
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL"), "TSLA": _make_position("TSLA")})
        result = ai.build_universe(snap)
        assert set(result) == {"AAPL", "TSLA"}
        assert result == sorted(result)  # must be sorted

    def test_union_with_watchlist(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.setattr(ai.settings, "WATCHLIST", "NVDA,MSFT")
        # Isolate from a real repo-root watchlist.txt -- see test_from_env_var.
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = ai.build_universe(snap)
        assert set(result) == {"AAPL", "NVDA", "MSFT"}

    def test_deduplication(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ai.settings, "WATCHLIST", "AAPL,MSFT")
        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = ai.build_universe(snap)
        assert result.count("AAPL") == 1

    def test_empty_account_empty_watchlist(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Held ∪ watchlist ∪ discovered ∪ DEFAULT_TICKERS all empty -> the
        universe is []. The Google Sheets Sheet2 last-resort fallback that
        used to run here was retired in step 4e (2026-09)."""
        monkeypatch.setattr(ai.settings, "WATCHLIST", "")
        monkeypatch.setattr("pipeline.advisory_inputs.settings.DEFAULT_TICKERS", [])
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={})
        assert ai.build_universe(snap) == []

    def test_default_tickers_fallback_used_when_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """DEFAULT_TICKERS is the sole fallback now that Sheet2 is retired --
        consulted only when held + watchlist + discovered are all empty."""
        monkeypatch.setattr(ai.settings, "WATCHLIST", "")
        monkeypatch.setattr("pipeline.advisory_inputs.settings.DEFAULT_TICKERS", ["SPY", "QQQ"])
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={})
        result = ai.build_universe(snap)
        assert set(result) == {"SPY", "QQQ"}
        assert result == sorted(result)

    def test_build_universe_symbol_rating_exclusion_failure_handled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """When SYMBOL_RATING_AUTO_DROP_ENABLED is on but SymbolRatingStore fails,
        the exception should be caught and the universe returned unaffected."""
        monkeypatch.setattr("pipeline.advisory_inputs.settings.SYMBOL_RATING_AUTO_DROP_ENABLED", True)
        monkeypatch.setattr(ai.settings, "WATCHLIST", "NVDA,MSFT")
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})

        with patch("rating.symbol_rating_store.SymbolRatingStore.get_excluded_symbols", side_effect=Exception("mocked failure")):
            result = ai.build_universe(snap)

        # AAPL (held), NVDA, MSFT (watchlist) should remain in the universe
        assert set(result) == {"AAPL", "NVDA", "MSFT"}
        assert result == sorted(result)

    def test_watchlist_env_and_file_both_merged_into_universe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """WATCHLIST env var and watchlist.txt, populated with different tickers,
        are both merged into the universe -- a genuine 3-way union with held
        positions, per this repo's documented `positions ∪ WATCHLIST ∪
        watchlist.txt` convention."""
        monkeypatch.setattr(ai.settings, "WATCHLIST", "NVDA,MSFT")
        (tmp_path / "watchlist.txt").write_text("AMD\nINTC\n")
        monkeypatch.chdir(tmp_path)
        snap = _make_snapshot(positions={"AAPL": _make_position("AAPL")})
        result = ai.build_universe(snap)
        assert set(result) == {"AAPL", "NVDA", "MSFT", "AMD", "INTC"}
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# Task A4 — per-process MacroEngine reuse (advisory_inputs.get_macro_engine)
# ---------------------------------------------------------------------------

class TestMacroEngineReuse:
    """get_macro_engine() must construct exactly ONE MacroEngine per FRED key
    for the lifetime of the process, so the HMMRegimeDetector's
    retrain_freq_days gate is meaningful across cycles of a long-lived process
    (the orchestrator daemon calls build_macro_dto() each cycle)."""

    def setup_method(self) -> None:
        ai.reset_macro_engine_cache()

    def teardown_method(self) -> None:
        ai.reset_macro_engine_cache()

    def test_get_macro_engine_reuses_same_instance_for_same_key(self) -> None:
        # get_macro_engine() imports DataEngine/MacroEngine lazily inside the
        # function body (not at module scope), so they must be patched at
        # their defining modules.
        with patch("data_engine.DataEngine") as MockDE, patch("macro_engine.MacroEngine") as MockME:
            MockDE.return_value = MagicMock()
            MockME.side_effect = lambda data_engine: MagicMock(data_engine=data_engine)

            engine_1 = ai.get_macro_engine("FRED_KEY_A")
            engine_2 = ai.get_macro_engine("FRED_KEY_A")

            assert engine_1 is engine_2
            # MacroEngine (and therefore its HMMRegimeDetector) constructed
            # exactly once for repeated calls with the same key.
            MockME.assert_called_once()
            MockDE.assert_called_once()

    def test_get_macro_engine_rebuilds_on_key_rotation(self) -> None:
        with patch("data_engine.DataEngine") as MockDE, patch("macro_engine.MacroEngine") as MockME:
            MockDE.side_effect = lambda key: MagicMock(name=f"de_{key}")
            MockME.side_effect = lambda data_engine: MagicMock(data_engine=data_engine)

            engine_1 = ai.get_macro_engine("FRED_KEY_A")
            engine_2 = ai.get_macro_engine("FRED_KEY_B")

            assert engine_1 is not engine_2
            assert MockME.call_count == 2

    def test_build_macro_dto_reuses_engine_across_calls(
        self, monkeypatch: pytest.MonkeyPatch, disable_historical_store
    ) -> None:
        """build_macro_dto() (called once per cycle) must route
        through get_macro_engine() so two consecutive cycles within the same
        process reuse one MacroEngine instance instead of constructing a new
        one (and therefore a fresh, never-fitted HMMRegimeDetector) every
        cycle.

        Uses disable_historical_store since HISTORICAL_STORE_ENABLED defaults
        True and this test doesn't exercise the SPY-history routing itself
        (see TestBuildMacroDtoHistoricalStoreRouting below) -- without the
        fixture, _build_macro_dto's new HistoricalStore-first SPY fetch would
        construct a real on-disk HistoricalStore and hit the real
        get_provider() network path instead of exercising the mocked
        fake_de.fetch_technical_raw fallback this test actually verifies."""
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")

        with patch("data_engine.DataEngine") as MockDE, patch("macro_engine.MacroEngine") as MockME:
            fake_de = MagicMock()
            fake_de.fetch_macro_raw.return_value = {}
            fake_de.fetch_technical_raw.return_value = {}
            MockDE.return_value = fake_de

            fake_me = MagicMock()
            fake_me.data_engine = fake_de
            fake_me.compute_hmm_risk_on_probability.return_value = None
            # Without this, an unconfigured MagicMock()._calculate_sahm_rule_detailed()
            # call unpacks as an empty iterator (ValueError), silently caught by
            # build_macro_dto()'s outer except and falling through to the
            # neutral-fallback branch -- masking whether the intended live-fetch
            # success path (and its two engine-reuse assertions below) actually ran.
            # 0.37/False is a distinctive, non-fallback value that the
            # exception-fallback branch (which always hardcodes
            # sahm_rule_indicator=0.0) could never produce, so asserting it
            # below proves the live-fetch path actually executed both times.
            fake_me._calculate_sahm_rule_detailed.return_value = (0.37, False)
            MockME.return_value = fake_me

            dto_1 = ai.build_macro_dto()
            dto_2 = ai.build_macro_dto()

            # MacroEngine constructed once even though build_macro_dto() was
            # called twice (simulating two --interval cycles).
            MockME.assert_called_once()
            assert fake_me.compute_hmm_risk_on_probability.call_count == 2
            # Confirms the live-fetch success path actually ran both times
            # (not silently swallowed into the neutral-fallback except branch).
            assert dto_1.sahm_rule_indicator == 0.37
            assert dto_2.sahm_rule_indicator == 0.37
            # fetch_macro_raw() returns {} in this fixture (T10Y2Y/BAMLH0A0HYM2/
            # VIXCLS all missing) -- data_unavailable must still be True even
            # though the Sahm read itself succeeded (sahm_used_fallback=False).
            assert dto_1.data_unavailable is True
            assert dto_2.data_unavailable is True


class TestBuildMacroDtoHistoricalStoreRouting:
    """build_macro_dto()'s SPY-history fetch for the HMM detector must route
    through HistoricalStore.get_bars() when enabled and available, falling
    back to the original DataEngine.fetch_technical_raw(["SPY"]) call on any
    HistoricalStore failure or when HISTORICAL_STORE_ENABLED is False."""

    def setup_method(self) -> None:
        ai.reset_macro_engine_cache()

    def teardown_method(self) -> None:
        ai.reset_macro_engine_cache()

    def test_spy_history_routes_through_historical_store_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")
        synthetic_spy_df = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Volume": [1000, 1100],
            }
        )

        with patch("data_engine.DataEngine") as MockDE, \
             patch("macro_engine.MacroEngine") as MockME, \
             patch("data.historical_store.HistoricalStore") as MockHS, \
             patch("pipeline.advisory_inputs.get_provider") as mock_get_provider:
            fake_de = MagicMock()
            fake_de.fetch_macro_raw.return_value = {}
            MockDE.return_value = fake_de

            fake_me = MagicMock()
            fake_me.data_engine = fake_de
            fake_me.compute_hmm_risk_on_probability.return_value = None
            # Unconfigured, this unpacks as an empty iterator (ValueError),
            # silently caught by build_macro_dto()'s outer except -- masking
            # whether the assertions below actually ran against the intended
            # live-fetch path or a swallowed exception's fallback DTO.
            fake_me._calculate_sahm_rule_detailed.return_value = (0.0, False)
            MockME.return_value = fake_me

            fake_market = MagicMock()
            mock_get_provider.return_value = fake_market

            fake_store_instance = MagicMock()
            fake_store_instance.get_bars.return_value = synthetic_spy_df
            MockHS.return_value = fake_store_instance

            ai.build_macro_dto()

            fake_store_instance.get_bars.assert_called_once_with(
                "SPY", lookback_days=504, provider=fake_market
            )
            fake_de.fetch_technical_raw.assert_not_called()
            fake_me.compute_hmm_risk_on_probability.assert_called_once()
            call_args = fake_me.compute_hmm_risk_on_probability.call_args
            passed_df = call_args[0][0] if call_args[0] else call_args.kwargs.get("spy_price_df")
            assert passed_df is synthetic_spy_df

    def test_spy_history_falls_back_when_historical_store_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")
        fallback_spy_df = pd.DataFrame({"Close": [50.0, 51.0]})

        with patch("data_engine.DataEngine") as MockDE, \
             patch("macro_engine.MacroEngine") as MockME, \
             patch("data.historical_store.HistoricalStore") as MockHS:
            fake_de = MagicMock()
            fake_de.fetch_macro_raw.return_value = {}
            fake_de.fetch_technical_raw.return_value = {"SPY": fallback_spy_df}
            MockDE.return_value = fake_de

            fake_me = MagicMock()
            fake_me.data_engine = fake_de
            fake_me.compute_hmm_risk_on_probability.return_value = None
            fake_me._calculate_sahm_rule_detailed.return_value = (0.0, False)
            MockME.return_value = fake_me

            MockHS.side_effect = RuntimeError("simulated HistoricalStore construction failure")

            ai.build_macro_dto()

            fake_de.fetch_technical_raw.assert_called_once_with(["SPY"])
            fake_me.compute_hmm_risk_on_probability.assert_called_once()
            call_args = fake_me.compute_hmm_risk_on_probability.call_args
            passed_df = call_args[0][0] if call_args[0] else call_args.kwargs.get("spy_price_df")
            assert passed_df is fallback_spy_df

    def test_spy_history_uses_direct_fetch_when_historical_store_disabled(
        self, monkeypatch: pytest.MonkeyPatch, disable_historical_store
    ) -> None:
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")
        fallback_spy_df = pd.DataFrame({"Close": [60.0, 61.0]})

        with patch("data_engine.DataEngine") as MockDE, \
             patch("macro_engine.MacroEngine") as MockME, \
             patch("data.historical_store.HistoricalStore") as MockHS:
            fake_de = MagicMock()
            fake_de.fetch_macro_raw.return_value = {}
            fake_de.fetch_technical_raw.return_value = {"SPY": fallback_spy_df}
            MockDE.return_value = fake_de

            fake_me = MagicMock()
            fake_me.data_engine = fake_de
            fake_me.compute_hmm_risk_on_probability.return_value = None
            fake_me._calculate_sahm_rule_detailed.return_value = (0.0, False)
            MockME.return_value = fake_me

            ai.build_macro_dto()

            MockHS.assert_not_called()
            fake_de.fetch_technical_raw.assert_called_once_with(["SPY"])
            fake_me.compute_hmm_risk_on_probability.assert_called_once()


class TestBuildMacroDtoDataUnavailable:
    """build_macro_dto() must fail closed (CONSTRAINT #4/#6) rather than
    silently substitute a benign default for a missing FRED reading. Also
    regression-guards the fix to a second, independent bug found while
    building this: the advisory macro builder (then in main.py) used to read
    macro_raw.get("SAHMREALTIME", 0.0)
    -- a key DataEngine.fetch_macro_raw() never populates at all -- so this
    DTO's Sahm-driven kill-switch input was structurally dead (always 0.0)
    regardless of FRED health, before this fix."""

    def setup_method(self) -> None:
        ai.reset_macro_engine_cache()

    def teardown_method(self) -> None:
        ai.reset_macro_engine_cache()

    def test_healthy_cycle_reports_data_available(
        self, monkeypatch: pytest.MonkeyPatch, disable_historical_store
    ) -> None:
        """The byte-identical-when-healthy regression guard: a fully
        populated fetch_macro_raw() return (matching what
        DataEngine.fetch_macro_raw() actually returns on a real FRED success
        -- T10Y2Y/BAMLH0A0HYM2/UNRATE/VIXCLS, deliberately no SAHMREALTIME
        key) plus a real (non-fallback) Sahm reading must NOT set
        data_unavailable. This is the specific case an earlier draft of this
        fix would have gotten wrong (a formula that checked for the
        never-populated SAHMREALTIME key would have reported unavailable on
        every single healthy cycle)."""
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")

        with patch("data_engine.DataEngine") as MockDE, patch("macro_engine.MacroEngine") as MockME:
            fake_de = MagicMock()
            fake_de.fetch_macro_raw.return_value = {
                "T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0, "UNRATE": 3.8, "VIXCLS": 16.0,
            }
            fake_de.fetch_technical_raw.return_value = {}
            MockDE.return_value = fake_de

            fake_me = MagicMock()
            fake_me.data_engine = fake_de
            fake_me.compute_hmm_risk_on_probability.return_value = None
            fake_me._calculate_sahm_rule_detailed.return_value = (0.12, False)
            MockME.return_value = fake_me

            dto = ai.build_macro_dto()

            assert dto.data_unavailable is False
            assert dto.sahm_rule_indicator == 0.12
            assert dto.killSwitch is False

    def test_empty_macro_raw_sets_data_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, disable_historical_store
    ) -> None:
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")

        with patch("data_engine.DataEngine") as MockDE, patch("macro_engine.MacroEngine") as MockME:
            fake_de = MagicMock()
            fake_de.fetch_macro_raw.return_value = {}
            fake_de.fetch_technical_raw.return_value = {}
            MockDE.return_value = fake_de

            fake_me = MagicMock()
            fake_me.data_engine = fake_de
            fake_me.compute_hmm_risk_on_probability.return_value = None
            fake_me._calculate_sahm_rule_detailed.return_value = (0.12, False)
            MockME.return_value = fake_me

            dto = ai.build_macro_dto()

            assert dto.data_unavailable is True
            assert dto.killSwitch is True
            assert dto.market_regime == "RECESSION"

    def test_sahm_fallback_alone_sets_data_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, disable_historical_store
    ) -> None:
        """calculate_sahm_rule's own fallback firing (sahm_used_fallback=True)
        must set data_unavailable even when macro_raw itself is complete."""
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")

        with patch("data_engine.DataEngine") as MockDE, patch("macro_engine.MacroEngine") as MockME:
            fake_de = MagicMock()
            fake_de.fetch_macro_raw.return_value = {
                "T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.0, "UNRATE": 3.8, "VIXCLS": 16.0,
            }
            fake_de.fetch_technical_raw.return_value = {}
            MockDE.return_value = fake_de

            fake_me = MagicMock()
            fake_me.data_engine = fake_de
            fake_me.compute_hmm_risk_on_probability.return_value = None
            fake_me._calculate_sahm_rule_detailed.return_value = (0.0, True)  # fallback fired
            MockME.return_value = fake_me

            dto = ai.build_macro_dto()

            assert dto.data_unavailable is True
            assert dto.killSwitch is True

    def test_fabricated_but_populated_macro_raw_sets_data_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, disable_historical_store
    ) -> None:
        """The populated-but-fabricated blind spot: a fetch_macro_raw()
        return that is FULLY POPULATED (every key present, non-None -- e.g.
        DataEngine's hardcoded emergency fallback dict) but flagged via
        last_macro_raw_fabricated_keys must still fail closed. Plain
        key-presence checking alone (the pre-existing PR #854 fix) cannot
        see this -- it only catches a genuinely-missing key."""
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")

        with patch("data_engine.DataEngine") as MockDE, patch("macro_engine.MacroEngine") as MockME:
            fake_de = MagicMock()
            fake_de.fetch_macro_raw.return_value = {
                "T10Y2Y": 0.5, "BAMLH0A0HYM2": 3.5, "UNRATE": 3.8, "VIXCLS": 15.0,
            }
            fake_de.last_macro_raw_fabricated_keys = frozenset(
                {"T10Y2Y", "BAMLH0A0HYM2", "UNRATE", "VIXCLS"}
            )
            fake_de.fetch_technical_raw.return_value = {}
            MockDE.return_value = fake_de

            fake_me = MagicMock()
            fake_me.data_engine = fake_de
            fake_me.compute_hmm_risk_on_probability.return_value = None
            fake_me._calculate_sahm_rule_detailed.return_value = (0.0, False)
            MockME.return_value = fake_me

            dto = ai.build_macro_dto()

            assert dto.data_unavailable is True
            assert dto.killSwitch is True
            assert dto.market_regime == "RECESSION"

    def test_no_fred_key_branch_sets_data_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "")
        dto = ai.build_macro_dto()
        assert dto.data_unavailable is True
        assert dto.killSwitch is True

    def test_exception_fallback_branch_sets_data_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ai.settings, "FRED_API_KEY", "dummy_key_for_test")
        with patch("pipeline.advisory_inputs.get_macro_engine", side_effect=RuntimeError("boom")):
            dto = ai.build_macro_dto()
        assert dto.data_unavailable is True
        assert dto.killSwitch is True

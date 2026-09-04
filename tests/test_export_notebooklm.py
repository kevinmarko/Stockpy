"""Tests for scripts/export_notebooklm.py.

``build_export()`` composes three independently try/excepted sections (Macro
Context, Current Portfolio, Active Pilot Follows) into one Markdown document
written to ``settings.OUTPUT_DIR / "notebooklm_source.md"``. This suite
covers:

1. ``_fmt_money`` / ``_fmt_num`` — None/NaN -> "N/A", but a genuine 0/0.0
   renders as an honest zero (CONSTRAINT #4: missing data and a real zero
   balance must never be conflated).
2. ``build_export()`` happy path — real-shaped ``AccountSnapshot`` /
   ``PortfolioPosition`` DTOs and follow dicts produce the expected Markdown.
3. Each section degrades independently to its own honest "unavailable" text
   on failure, without crashing the whole export and without dragging the
   other two sections down with it.
4. ``HistoricalStore(readonly=True)`` construction failing degrades BOTH
   store-dependent sections (macro, portfolio) while the Follows section
   (independent of ``store``) still works.

The module performs a venv-reexec + ``.env``-load side effect at import time
via ``scripts._bootstrap.bootstrap()`` — but ``bootstrap()`` detects
``pytest`` in ``sys.modules`` and no-ops the re-exec (see
``scripts/_bootstrap.py``), so a direct module import under pytest is safe.
This mirrors ``tests/test_backfill_edgar_fundamentals.py``'s import style.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scripts import export_notebooklm as notebooklm
from data.robinhood_portfolio import AccountSnapshot, PortfolioPosition


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _real_snapshot() -> AccountSnapshot:
    """A real-shaped, non-degenerate ``AccountSnapshot`` with one position."""
    pos = PortfolioPosition(
        symbol="AAPL",
        quantity=10.0,
        average_cost=150.0,
        current_price=175.0,
        market_value=1750.0,
        unrealized_pl=250.0,
        unrealized_pl_pct=16.67,
        dividends_received=12.5,
        name="Apple Inc.",
    )
    return AccountSnapshot(
        positions={"AAPL": pos},
        buying_power=5000.0,
        total_equity=6750.0,
        total_dividends=12.5,
        fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _zero_balance_snapshot() -> AccountSnapshot:
    """A real, fully-liquidated account: genuinely $0 equity/buying power."""
    return AccountSnapshot(
        positions={},
        buying_power=0.0,
        total_equity=0.0,
        total_dividends=0.0,
        fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def _macro_series(value: float) -> pd.Series:
    idx = pd.to_datetime(["2026-07-30", "2026-07-31"])
    return pd.Series([value - 1.0, value], index=idx, name="value")


class _FakeHistoricalStore:
    """Stand-in for ``HistoricalStore`` with injectable per-method behavior."""

    def __init__(self, get_macro=None, latest_account_snapshot=None):
        self._get_macro = get_macro or (lambda series_id: pd.Series(dtype=float))
        self._latest_account_snapshot = latest_account_snapshot or (lambda: None)

    def get_macro(self, series_id, *args, **kwargs):
        return self._get_macro(series_id)

    def latest_account_snapshot(self):
        return self._latest_account_snapshot()


class _FakeFollowsStore:
    def __init__(self, rows=None, raise_exc=None):
        self._rows = rows or []
        self._raise_exc = raise_exc

    def list_active(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._rows


def _read_export(tmp_path: Path) -> str:
    out_path = tmp_path / "notebooklm_source.md"
    assert out_path.exists(), "build_export() must write notebooklm_source.md"
    return out_path.read_text(encoding="utf-8")


def _patch_output_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(notebooklm.settings, "OUTPUT_DIR", tmp_path)


# ---------------------------------------------------------------------------
# _fmt_money / _fmt_num
# ---------------------------------------------------------------------------

class TestFmtMoney:
    def test_none_is_na(self):
        assert notebooklm._fmt_money(None) == "N/A"

    def test_nan_is_na(self):
        assert notebooklm._fmt_money(float("nan")) == "N/A"

    def test_zero_renders_as_honest_zero_not_na(self):
        # A genuine $0 balance must never be conflated with missing data.
        assert notebooklm._fmt_money(0.0) == "$0.00"
        assert notebooklm._fmt_money(0) == "$0.00"

    def test_positive_value_formatted(self):
        assert notebooklm._fmt_money(1234.5) == "$1,234.50"
        assert notebooklm._fmt_money(6750.0) == "$6,750.00"

    def test_negative_value_formatted(self):
        assert notebooklm._fmt_money(-99.9) == "$-99.90"


class TestFmtNum:
    def test_none_is_na(self):
        assert notebooklm._fmt_num(None) == "N/A"

    def test_nan_is_na(self):
        assert notebooklm._fmt_num(float("nan")) == "N/A"

    def test_zero_renders_as_honest_zero_not_na(self):
        assert notebooklm._fmt_num(0) == "0"
        assert notebooklm._fmt_num(0.0) == "0.0"

    def test_positive_value_formatted(self):
        assert notebooklm._fmt_num(42) == "42"
        assert notebooklm._fmt_num(3.14) == "3.14"


# ---------------------------------------------------------------------------
# build_export() happy path
# ---------------------------------------------------------------------------

class TestBuildExportHappyPath:
    def test_writes_expected_markdown(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)

        fake_store = _FakeHistoricalStore(
            get_macro=lambda series_id: {
                "VIXCLS": _macro_series(18.5),
                "T10Y2Y": _macro_series(0.42),
                "BAMLH0A0HYM2": _macro_series(3.75),
            }.get(series_id, pd.Series(dtype=float)),
            latest_account_snapshot=_real_snapshot,
        )
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)

        follow_rows = [
            {"pilot_id": "pilot-alpha", "amount": 2500.0, "status": "active"},
            {"pilot_id": "pilot-beta", "amount": 1000.0, "status": "active"},
        ]
        monkeypatch.setattr(
            notebooklm, "FollowsStore", lambda: _FakeFollowsStore(rows=follow_rows)
        )

        notebooklm.build_export()

        text = _read_export(tmp_path)

        # Top-level shape.
        assert text.startswith("# Stockpy System Export")
        assert "**Generated At (UTC):**" in text

        # Macro Context section.
        assert "## Macro Context" in text
        assert "**VIX**: 18.5" in text
        assert "**10Y-2Y Spread**: 0.42%" in text
        assert "**High Yield OAS**: 3.75%" in text

        # Portfolio section.
        assert "## Current Portfolio" in text
        assert "**Total Equity**: $6,750.00" in text
        assert "**Buying Power**: $5,000.00" in text
        assert "### Positions" in text
        assert "**AAPL** (Apple Inc.): 10.0 shares @ $150.00 (Market Value: $1,750.00)" in text

        # Follows section.
        assert "## Active Pilot Follows" in text
        assert "**Pilot ID**: pilot-alpha | **Amount**: $2,500.00 | **Status**: active" in text
        assert "**Pilot ID**: pilot-beta | **Amount**: $1,000.00 | **Status**: active" in text

    def test_respects_output_dir(self, tmp_path: Path, monkeypatch):
        custom_dir = tmp_path / "custom_output"
        monkeypatch.setattr(notebooklm.settings, "OUTPUT_DIR", custom_dir)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        assert not custom_dir.exists()
        notebooklm.build_export()

        assert (custom_dir / "notebooklm_source.md").exists()

    def test_zero_balance_account_renders_honest_zeros(self, tmp_path: Path, monkeypatch):
        """A genuinely fully-liquidated ($0) account must render $0.00, not N/A."""
        _patch_output_dir(monkeypatch, tmp_path)
        fake_store = _FakeHistoricalStore(latest_account_snapshot=_zero_balance_snapshot)
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export()
        text = _read_export(tmp_path)

        assert "**Total Equity**: $0.00" in text
        assert "**Buying Power**: $0.00" in text
        assert "No open positions." in text
        # Never fabricate: no "N/A" masquerading where a real 0.0 was measured.
        assert "Total Equity**: N/A" not in text
        assert "Buying Power**: N/A" not in text

    def test_no_positions_renders_no_open_positions(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)
        snap = AccountSnapshot(
            positions={},
            buying_power=1000.0,
            total_equity=1000.0,
            total_dividends=0.0,
            fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        fake_store = _FakeHistoricalStore(latest_account_snapshot=lambda: snap)
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export()
        text = _read_export(tmp_path)
        assert "No open positions." in text

    def test_no_active_follows_renders_honest_message(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore(rows=[]))

        notebooklm.build_export()
        text = _read_export(tmp_path)
        assert "No active pilot follows." in text


# ---------------------------------------------------------------------------
# Degraded paths — each section fails independently, others unaffected
# ---------------------------------------------------------------------------

class TestBuildExportDegradedSections:
    def test_macro_section_degrades_others_unaffected(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)

        def _boom_get_macro(series_id):
            raise RuntimeError("FRED unavailable")

        fake_store = _FakeHistoricalStore(
            get_macro=_boom_get_macro, latest_account_snapshot=_real_snapshot
        )
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        follow_rows = [{"pilot_id": "pilot-alpha", "amount": 500.0, "status": "active"}]
        monkeypatch.setattr(
            notebooklm, "FollowsStore", lambda: _FakeFollowsStore(rows=follow_rows)
        )

        notebooklm.build_export()
        text = _read_export(tmp_path)

        assert "Macro data is currently unavailable." in text
        # Other sections must still render correctly.
        assert "**Total Equity**: $6,750.00" in text
        assert "**Pilot ID**: pilot-alpha" in text

    def test_macro_section_empty_series_degrades_honestly(self, tmp_path: Path, monkeypatch):
        """All three macro series empty (not an exception) -> honest unavailable text."""
        _patch_output_dir(monkeypatch, tmp_path)
        fake_store = _FakeHistoricalStore(
            get_macro=lambda series_id: pd.Series(dtype=float),
            latest_account_snapshot=_real_snapshot,
        )
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export()
        text = _read_export(tmp_path)

        assert "Macro data is currently unavailable." in text
        assert "**Total Equity**: $6,750.00" in text

    def test_portfolio_section_degrades_others_unaffected(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)

        def _boom_snapshot():
            raise RuntimeError("DB read failed")

        fake_store = _FakeHistoricalStore(
            get_macro=lambda series_id: _macro_series(20.0),
            latest_account_snapshot=_boom_snapshot,
        )
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        follow_rows = [{"pilot_id": "pilot-gamma", "amount": 750.0, "status": "active"}]
        monkeypatch.setattr(
            notebooklm, "FollowsStore", lambda: _FakeFollowsStore(rows=follow_rows)
        )

        notebooklm.build_export()
        text = _read_export(tmp_path)

        assert "Portfolio snapshot is unavailable." in text
        # Other sections must still render correctly.
        assert "**VIX**: 20.0" in text
        assert "**Pilot ID**: pilot-gamma" in text

    def test_portfolio_none_snapshot_degrades_honestly(self, tmp_path: Path, monkeypatch):
        """latest_account_snapshot() returning None (no exception) -> honest text."""
        _patch_output_dir(monkeypatch, tmp_path)
        fake_store = _FakeHistoricalStore(latest_account_snapshot=lambda: None)
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export()
        text = _read_export(tmp_path)
        assert "Portfolio snapshot is unavailable." in text

    def test_follows_section_degrades_others_unaffected(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)
        fake_store = _FakeHistoricalStore(
            get_macro=lambda series_id: _macro_series(15.0),
            latest_account_snapshot=_real_snapshot,
        )
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(
            notebooklm,
            "FollowsStore",
            lambda: _FakeFollowsStore(raise_exc=RuntimeError("follows.json corrupt")),
        )

        notebooklm.build_export()
        text = _read_export(tmp_path)

        assert "Active pilot follows are unavailable." in text
        # Other sections must still render correctly.
        assert "**VIX**: 15.0" in text
        assert "**Total Equity**: $6,750.00" in text

    def test_all_three_sections_fail_independently(self, tmp_path: Path, monkeypatch):
        """Every section fails at once -> build_export still completes and writes
        three honest, independent "unavailable" messages, never crashing."""
        _patch_output_dir(monkeypatch, tmp_path)

        def _boom(*_a, **_k):
            raise RuntimeError("boom")

        fake_store = _FakeHistoricalStore(get_macro=_boom, latest_account_snapshot=_boom)
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(
            notebooklm, "FollowsStore", lambda: _FakeFollowsStore(raise_exc=RuntimeError("x"))
        )

        notebooklm.build_export()  # must not raise
        text = _read_export(tmp_path)

        assert "Macro data is currently unavailable." in text
        assert "Portfolio snapshot is unavailable." in text
        assert "Active pilot follows are unavailable." in text


# ---------------------------------------------------------------------------
# HistoricalStore construction failure
# ---------------------------------------------------------------------------

class TestHistoricalStoreConstructionFailure:
    def test_store_construction_failure_degrades_macro_and_portfolio_only(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_output_dir(monkeypatch, tmp_path)

        def _boom_construct(readonly=True):
            raise RuntimeError("cannot open database file")

        monkeypatch.setattr(notebooklm, "HistoricalStore", _boom_construct)

        follow_rows = [{"pilot_id": "pilot-delta", "amount": 300.0, "status": "active"}]
        monkeypatch.setattr(
            notebooklm, "FollowsStore", lambda: _FakeFollowsStore(rows=follow_rows)
        )

        notebooklm.build_export()  # must not raise
        text = _read_export(tmp_path)

        assert "Macro data is currently unavailable." in text
        assert "Portfolio snapshot is unavailable." in text
        # Follows is independent of `store` and must still succeed.
        assert "**Pilot ID**: pilot-delta | **Amount**: $300.00 | **Status**: active" in text
        assert "Active pilot follows are unavailable." not in text


# ---------------------------------------------------------------------------
# Never-fabricate sanity sweep
# ---------------------------------------------------------------------------

class TestNeverFabricates:
    def test_missing_macro_never_substitutes_zero(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)
        fake_store = _FakeHistoricalStore(
            get_macro=lambda series_id: pd.Series(dtype=float),
            latest_account_snapshot=lambda: None,
        )
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export()
        text = _read_export(tmp_path)

        # No fabricated "0"/"$0.00" anywhere a real value could not be measured.
        assert "**VIX**: 0" not in text
        assert "**10Y-2Y Spread**: 0" not in text
        assert "**High Yield OAS**: 0" not in text
        assert "Macro data is currently unavailable." in text
        assert "Portfolio snapshot is unavailable." in text

    def test_missing_position_field_stays_na_not_zero(self, tmp_path: Path, monkeypatch):
        """A position with a NaN qty/avg_cost must render N/A, never a fabricated 0."""
        _patch_output_dir(monkeypatch, tmp_path)
        pos = PortfolioPosition(
            symbol="TSLA",
            quantity=float("nan"),
            average_cost=float("nan"),
            current_price=200.0,
            market_value=float("nan"),
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
            dividends_received=0.0,
            name="Tesla Inc.",
        )
        snap = AccountSnapshot(
            positions={"TSLA": pos},
            buying_power=100.0,
            total_equity=100.0,
            total_dividends=0.0,
            fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        fake_store = _FakeHistoricalStore(latest_account_snapshot=lambda: snap)
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export()
        text = _read_export(tmp_path)

        assert "**TSLA** (Tesla Inc.): N/A shares @ N/A (Market Value: N/A)" in text


# ---------------------------------------------------------------------------
# Modular Multi-Source Tests (Phase 2)
# ---------------------------------------------------------------------------

class TestModularExportOutputs:
    def test_build_export_generates_all_five_modular_files_by_default(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore(
                get_macro=lambda s: _macro_series(15.0),
                latest_account_snapshot=_real_snapshot,
            )
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export()

        # Check consolidated
        assert (tmp_path / "notebooklm_source.md").exists()

        # Check modular directory and all 5 files
        mod_dir = tmp_path / "notebooklm"
        assert mod_dir.is_dir()
        expected_files = [
            "01_macro_and_regime.md",
            "02_portfolio_and_greeks.md",
            "03_strategy_signals_and_picks.md",
            "04_trade_journal_and_ledger.md",
            "05_options_directives_and_matrix.md",
        ]
        for fname in expected_files:
            p = mod_dir / fname
            assert p.exists(), f"Expected {fname} to be written"
            content = p.read_text(encoding="utf-8")
            assert len(content) > 20, f"{fname} should have substantial content"

    def test_modular_only_flag_skips_consolidated(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export(modular=True, consolidated=False)

        assert not (tmp_path / "notebooklm_source.md").exists()
        assert (tmp_path / "notebooklm" / "01_macro_and_regime.md").exists()

    def test_consolidated_only_flag_skips_modular(self, tmp_path: Path, monkeypatch):
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export(modular=False, consolidated=True)

        assert (tmp_path / "notebooklm_source.md").exists()
        assert not (tmp_path / "notebooklm" / "01_macro_and_regime.md").exists()

    def test_section_filtering_generates_only_requested_section(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export(section="macro")

        mod_dir = tmp_path / "notebooklm"
        assert (mod_dir / "01_macro_and_regime.md").exists()
        assert not (mod_dir / "02_portfolio_and_greeks.md").exists()
        assert not (mod_dir / "03_strategy_signals_and_picks.md").exists()
        assert not (mod_dir / "04_trade_journal_and_ledger.md").exists()
        assert not (mod_dir / "05_options_directives_and_matrix.md").exists()


class TestModularGenerators:
    def test_generate_macro_regime_source_with_state_snapshot(
        self, tmp_path: Path, monkeypatch
    ):
        ss_file = tmp_path / "state_snapshot.json"
        ss_file.write_text(
            '{"market_regime": "BULL", "hmm_regime_state": "bull_trend", "hmm_risk_on_probability": 0.85, "macro_kill_switch": false, "sahm_rule": 0.12}'
        )
        fake_store = _FakeHistoricalStore(
            get_macro=lambda s: _macro_series(16.5) if s == "VIXCLS" else pd.Series(dtype=float)
        )

        md = notebooklm.generate_macro_regime_source(fake_store, output_dir=tmp_path)
        assert "# Market Regime & Macroeconomic Risk Assessment" in md
        assert "**Market Regime**: BULL" in md
        assert "**HMM Regime State**: bull_trend" in md
        assert "**HMM Risk-On Probability**: 85.00%" in md
        assert "**VIX (CBOE Volatility Index)**: 16.5" in md
        assert "**Sahm Rule Indicator**: 0.12" in md

    def test_generate_portfolio_greeks_source(self, tmp_path: Path, monkeypatch):
        fake_store = _FakeHistoricalStore(latest_account_snapshot=_real_snapshot)
        fake_greeks = {
            "net_delta_shares": 10.0,
            "net_dollar_delta": 1750.0,
            "net_gamma": 0.05,
            "net_theta_daily": -12.5,
            "net_vega_1pct": 45.0,
            "beta_weighted_delta_spy": 8.5,
            "spy_spot": 500.0,
            "positions_with_missing_data": [],
            "symbols_with_estimated_beta": ["AAPL"],
        }
        monkeypatch.setattr(
            "pilots.options_risk.calculate_portfolio_greeks", lambda: fake_greeks
        )

        md = notebooklm.generate_portfolio_greeks_source(fake_store, output_dir=tmp_path)
        assert "# Portfolio Holdings, Allocation & Net Risk Greeks" in md
        assert "**Total Equity**: $6,750.00" in md
        assert "**Net Delta (Shares)**: 10.0" in md
        assert "**Net Dollar Delta ($)**: $1,750.00" in md
        assert "**Net Daily Theta ($/day)**: $-12.50" in md
        assert "**Beta-Weighted SPY Delta**: 8.5" in md
        assert "**Symbols Using Estimated Beta (1.0)**: AAPL" in md
        assert "**AAPL** (Apple Inc.)" in md

    def test_generate_signals_picks_source(self, tmp_path: Path, monkeypatch):
        ss_file = tmp_path / "state_snapshot.json"
        ss_file.write_text(
            json.dumps({
                "signals": [
                    {
                        "symbol": "NVDA",
                        "action": "STRONG BUY",
                        "conviction": 0.9,
                        "buy_range": "$120 - $125",
                        "sell_range": "$140 - $145",
                        "kelly_target": 0.15,
                        "score": 88.5,
                        "value_z": 0.5,
                        "quality_z": 1.8,
                        "xsec_12_1m": 2.1,
                        "lowvol_z": -0.2,
                        "size_z": 1.5,
                        "multifactor_composite": 1.25,
                        "sizing_was_capped": True,
                        "sizing_binding_constraint": "kelly_cap",
                        "etf_transmission_multiplier": 0.95,
                    }
                ]
            })
        )
        monkeypatch.setattr(
            notebooklm, "FollowsStore", lambda: _FakeFollowsStore(
                rows=[{"pilot_id": "trend-pilot", "amount": 1500.0, "status": "active"}]
            )
        )

        md = notebooklm.generate_signals_picks_source(output_dir=tmp_path)
        assert "# Quantitative Strategy Signals, Tactical Execution & Pilot Follows" in md
        assert "**trend-pilot** | $1,500.00 | active" in md
        assert "**NVDA** | STRONG BUY | 0.9 | $120 - $125 | $140 - $145 | 15.00% | 88.5" in md
        assert "| **NVDA** | 0.5 | 1.8 | 2.1 | -0.2 | 1.5 | 1.25 |" in md
        assert "| **NVDA** | True | kelly_cap | 0.95 |" in md

    def test_generate_trade_journal_source(self, tmp_path: Path, monkeypatch):
        fake_th = {
            "summary": {
                "n_trades": 10,
                "win_rate": 0.70,
                "profit_factor": 2.5,
                "total_realized_pnl": 500.0,
                "gross_profit": 800.0,
                "gross_loss": -300.0,
                "avg_win": 114.29,
                "avg_loss": -100.0,
                "avg_return_pct": 5.2,
                "avg_holding_days": 14.3,
                "best_trade_pnl": 250.0,
                "worst_trade_pnl": -150.0,
            },
            "trades": [
                {
                    "symbol": "MSFT",
                    "quantity": 5.0,
                    "entry_ts": "2026-08-01T10:00:00",
                    "exit_ts": "2026-08-15T15:00:00",
                    "holding_days": 14.2,
                    "entry_price": 400.0,
                    "exit_price": 420.0,
                    "realized_pnl": 100.0,
                    "return_pct": 5.0,
                }
            ],
        }
        monkeypatch.setattr("pilots.trade_history.trade_history_view", lambda **kwargs: fake_th)

        md = notebooklm.generate_trade_journal_source(output_dir=tmp_path)
        assert "# Quantitative Trade Journal & Realized Performance" in md
        assert "**Total Closed Trades**: 10" in md
        assert "**Win Rate**: 70.00%" in md
        assert "**Profit Factor**: 2.50" in md
        assert "**Total Realized P&L**: $500.00" in md
        assert "| **MSFT** | 5.0 | 2026-08-01 | 2026-08-15 | 14.2 | $400.00 | $420.00 | $100.00 | 5.00% |" in md

    def test_generate_options_matrix_source(self, tmp_path: Path):
        om_file = tmp_path / "options_matrix.json"
        om_file.write_text(
            json.dumps({
                "target_dte": 30,
                "vix": 19.5,
                "market_regime": "RISK_ON",
                "directives": [
                    {
                        "Symbol": "SPY",
                        "Strategy": "Put Credit Spread",
                        "Action": "Sell to Open",
                        "Price": 500.0,
                        "Short_Strike": 490.0,
                        "Short_Delta": -0.25,
                        "Long_Strike": 480.0,
                        "Long_Delta": -0.10,
                        "Net_Premium": 1.85,
                        "True_IVR": 65.4,
                        "Trend_Bias": "Bullish",
                        "Altman_Z_Score": 3.2,
                        "Piotroski_F_Score": 8,
                        "Days_To_Earnings": None,
                        "Earnings_Risk": False,
                        "News_Snippets": [{"title": "Markets rally on macro data"}],
                    }
                ]
            })
        )

        md = notebooklm.generate_options_matrix_source(output_dir=tmp_path)
        assert "# Options Strategy Directives & Volatility Matrix" in md
        assert "**Target DTE**: 30 days" in md
        assert "**Reference VIX**: 19.5" in md
        assert "| **SPY** | Put Credit Spread | Sell to Open | $500.00 | 490.0 (Δ -0.25) | 480.0 (Δ -0.1) | $1.85 | 65.40% | Bullish |" in md
        assert "Altman Z-Score: 3.2" in md
        assert "Markets rally on macro data" in md


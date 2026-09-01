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
5. A later item in a multi-position/multi-follow list that fails to format
   must never leave earlier real lines in the document alongside the
   section's "unavailable" fallback (each section commits its buffered
   output atomically, all-or-nothing).
6. The atomic write step (temp file + rename) cleans up its stray temp file
   and re-raises on a genuine I/O failure, and ``_OneShotMacroDataEngine``
   only ever performs one live macro fetch per script invocation.

The module performs a venv-reexec + ``.env``-load side effect at import time
via ``scripts._bootstrap.bootstrap()`` — but ``bootstrap()`` detects
``pytest`` in ``sys.modules`` and no-ops the re-exec (see
``scripts/_bootstrap.py``), so a direct module import under pytest is safe.
This mirrors ``tests/test_backfill_edgar_fundamentals.py``'s import style.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

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
# Partial-append protection — a later item's failure must not leak earlier
# real data alongside the section's "unavailable" fallback.
# ---------------------------------------------------------------------------

class TestPartialAppendProtection:
    def test_portfolio_later_position_failure_leaves_no_partial_data(
        self, tmp_path: Path, monkeypatch
    ):
        """A second position with a non-numeric field must not leave the
        first position's line (or Total Equity/Buying Power) in the document
        alongside 'Portfolio snapshot is unavailable.' -- the whole section
        commits atomically, all-or-nothing."""
        _patch_output_dir(monkeypatch, tmp_path)
        good_pos = PortfolioPosition(
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
        # A dataclass has no runtime type enforcement -- this mirrors a
        # hand-edited/legacy DB row whose column held a string, which
        # `_fmt_money`'s `f"${value:,.2f}"` raises on.
        bad_pos = PortfolioPosition(
            symbol="TSLA",
            quantity=5.0,
            average_cost="corrupted",  # type: ignore[arg-type]
            current_price=200.0,
            market_value=1000.0,
            unrealized_pl=0.0,
            unrealized_pl_pct=0.0,
            dividends_received=0.0,
            name="Tesla Inc.",
        )
        snap = AccountSnapshot(
            positions={"AAPL": good_pos, "TSLA": bad_pos},
            buying_power=5000.0,
            total_equity=6750.0,
            total_dividends=12.5,
            fetched_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        fake_store = _FakeHistoricalStore(latest_account_snapshot=lambda: snap)
        monkeypatch.setattr(notebooklm, "HistoricalStore", lambda readonly=True: fake_store)
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        notebooklm.build_export()
        text = _read_export(tmp_path)

        assert "Portfolio snapshot is unavailable." in text
        assert "Total Equity" not in text
        assert "AAPL" not in text

    def test_follows_later_row_failure_leaves_no_partial_data(
        self, tmp_path: Path, monkeypatch
    ):
        """A second follow row with a non-numeric amount must not leave the
        first follow's line in the document alongside 'Active pilot follows
        are unavailable.'."""
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        follow_rows = [
            {"pilot_id": "pilot-alpha", "amount": 500.0, "status": "active"},
            {"pilot_id": "pilot-beta", "amount": "corrupted", "status": "active"},
        ]
        monkeypatch.setattr(
            notebooklm, "FollowsStore", lambda: _FakeFollowsStore(rows=follow_rows)
        )

        notebooklm.build_export()
        text = _read_export(tmp_path)

        assert "Active pilot follows are unavailable." in text
        assert "pilot-alpha" not in text


# ---------------------------------------------------------------------------
# Atomic write — temp-file cleanup on failure, re-raise, one-shot macro fetch
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_write_failure_cleans_up_temp_file_and_reraises(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        def _boom_write_text(self, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom_write_text)

        with pytest.raises(OSError):
            notebooklm.build_export()

        # No stray "<name>.tmp.<pid>.<tid>" file left behind on failure.
        assert list(tmp_path.glob("notebooklm_source.md.tmp.*")) == []
        assert not (tmp_path / "notebooklm_source.md").exists()

    def test_temp_filename_is_pid_tid_scoped_not_a_bare_tmp_suffix(
        self, tmp_path: Path, monkeypatch
    ):
        """Two concurrent invocations must never share the same temp
        filename -- a bare `.with_suffix(".tmp")` is not race-safe."""
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        seen_tmp_names = []
        original_write_text = Path.write_text

        def _spy_write_text(self, *args, **kwargs):
            seen_tmp_names.append(self.name)
            return original_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", _spy_write_text)

        notebooklm.build_export()

        assert len(seen_tmp_names) == 1
        tmp_name = seen_tmp_names[0]
        assert tmp_name != "notebooklm_source.md.tmp"
        assert tmp_name.startswith("notebooklm_source.md.tmp.")
        # "<name>.tmp.<pid>.<tid>" -- both segments after "tmp." are digits.
        pid_str, tid_str = tmp_name.rsplit(".tmp.", 1)[1].split(".")
        assert pid_str.isdigit()
        assert tid_str.isdigit()


class TestOneShotMacroDataEngine:
    def test_fetch_macro_history_only_fetches_once_per_instance(self, monkeypatch):
        call_count = []

        class _FakeRealDataEngine:
            def __init__(self, fred_api_key):
                pass

            def fetch_macro_history(self):
                call_count.append(1)
                return pd.DataFrame({"value": [1.0]})

        monkeypatch.setattr(notebooklm.settings, "FRED_API_KEY", "fake-key")
        monkeypatch.setattr("data_engine.DataEngine", _FakeRealDataEngine)

        engine = notebooklm._OneShotMacroDataEngine()
        df1 = engine.fetch_macro_history()
        df2 = engine.fetch_macro_history()
        df3 = engine.fetch_macro_history()

        assert len(call_count) == 1
        assert df1 is df2 is df3

    def test_no_fred_key_degrades_to_empty_dataframe_without_raising(self, monkeypatch):
        monkeypatch.setattr(notebooklm.settings, "FRED_API_KEY", "")

        engine = notebooklm._OneShotMacroDataEngine()
        df = engine.fetch_macro_history()

        assert df.empty

"""Tests for scripts/export_notebooklm.py.

``build_export()`` writes a consolidated ``notebooklm_source.md`` (Macro
Context, Current Portfolio, Active Pilot Follows -- unchanged from the
original single-file export) PLUS 5 modular per-domain files under
``notebooklm/`` (macro & regime, portfolio & Greeks, signals & picks, trade
journal, options matrix). Part 1 of this suite (below) covers the original
consolidated-only behavior:

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

Part 2 (appended further down) covers the 5 modular generators and the
knowledge-pack driver, with regression tests for 7 real bugs found and
fixed during that rebuild -- most importantly, every fixture there is
grounded in the REAL producer files (``reporting/state_snapshot.py``,
``technical_options_engine.py``, ``pilots/trade_history.py``, etc.) rather
than a hand-invented shape, since a prior abandoned attempt at this same
feature shipped 7 real bugs that its own test suite missed for exactly that
reason.

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

import numpy as np
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
        filename -- a bare `.with_suffix(".tmp")` is not race-safe.

        Scoped to `consolidated=True, modular=False` -- `build_export()`'s
        bare/default call now also drives the 5 modular knowledge-pack
        files (each independently atomic-written via the same
        `_atomic_write_file` helper this test is really about), so a bare
        call legitimately performs 6 writes, not 1. This test's actual
        subject is the atomic-write helper's temp-naming scheme, which is
        exercised identically and more simply via the single consolidated
        file alone.
        """
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

        notebooklm.build_export(consolidated=True, modular=False)

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


# ===========================================================================
# Part 2: the 5 modular generators + the knowledge-pack driver
#
# FIXTURE REALISM is the single most important property of everything
# below: every fixture that encodes an assumption about production shape
# cites the real file:line it was verified against. A prior, abandoned
# attempt at this same feature shipped 7 real bugs that its own 30-test
# suite completely missed, because every fixture was hand-shaped to the
# author's ASSUMPTION of the real data rather than the real shape.
# ===========================================================================


# ---------------------------------------------------------------------------
# Realistic fixture builders (grounded in the real producer files)
# ---------------------------------------------------------------------------

def _real_signal_with_pipe_ranges() -> dict:
    """One ``signals[]`` entry shaped exactly like
    ``reporting/state_snapshot.py``'s per-recommendation dict (fields
    ``symbol``, ``advisory_action``, ``kelly_target``, ``buy_range``,
    ``sell_range`` -- reporting/state_snapshot.py:93-141), with REALISTIC
    (not sanitized) ``buy_range``/``sell_range`` strings.

    ``buy_range``/``sell_range`` mirror ``strategy_engine.py``'s
    RISK-REDUCE/AVOID format strings (``f"Trim @ ${{trim_point:.2f}} | Stop
    @ ${{stop_loss:.2f}}"`` / ``f"Sell Now @ market | Stop @
    ${{stop_loss:.2f}}"``) -- a literal `` | `` substring is the normal,
    everyday shape for this field, not an edge case.
    """
    return {
        "symbol": "XYZ",
        "action": "RISK REDUCE",
        "advisory_action": "RISK REDUCE",
        "kelly_target": 0.0421,
        "buy_range": "Trim @ $13.30 | Stop @ $13.07",
        "sell_range": "Sell Now @ market | Stop @ $13.07",
    }


def _malformed_kelly_target_signal() -> dict:
    """A ``signals[]`` entry with a non-numeric ``kelly_target`` -- simulates
    a hand-edited/legacy ``state_snapshot.json`` row. Used to prove
    ``generate_signals_picks_source`` degrades honestly rather than taking
    down the whole export.
    """
    return {
        "symbol": "BADCO",
        "action": "HOLD",
        "advisory_action": "HOLD",
        "kelly_target": "not-a-number",
        "buy_range": "Hold Range: $10.00 - $12.00",
        "sell_range": "Sell Zone: $13.00 - $15.00 | Stop @ $9.50",
    }


def _real_directive_true_ivr_null_ivr_proxy_present() -> dict:
    """One ``directives[]`` row shaped like ``build_premium_directive``'s
    return dict (technical_options_engine.py: ``row["IVR_Proxy"]`` and
    ``row["True_IVR"]`` are BOTH always-present keys, defaulting to
    ``nan``/``null`` after ``reporting/options_snapshot.py``'s
    ``_json_safe()`` NaN->None pass). This is the REAL common production
    shape when ``settings.OPTIONS_TRUE_IVR_ENABLED`` is off (the platform
    default): ``True_IVR`` is present but null, ``IVR_Proxy`` is present and
    a real, finite realized-vol-rank percentile.
    """
    return {
        "Symbol": "MSFT",
        "Strategy": "Iron Condor",
        "Action": "Sell",
        "True_IVR": None,
        "IVR_Proxy": 53.35,
        "Integrity_OK": True,
        "Integrity_Issues": [],
    }


def _directive_both_ivr_keys_absent() -> dict:
    """A directive row where BOTH ``True_IVR`` and ``IVR_Proxy`` keys are
    entirely absent (not merely null) -- simulates the dead-letter error-stub
    row ``reporting/options_snapshot.py``'s writer appends on a per-symbol
    failure. Must render "N/A" without a ``KeyError``.
    """
    return {
        "Symbol": "FAILCO",
        "Strategy": None,
        "Action": None,
        "Integrity_OK": False,
        "Integrity_Issues": ["MarketDataError: no quote"],
    }


def _real_options_matrix_payload(directives: list) -> dict:
    """Wraps ``directives`` in the real persisted shape written by
    ``reporting/options_snapshot.py::write_options_matrix``.
    """
    return {
        "timestamp": "2026-09-05T12:00:00+00:00",
        "target_dte": 30,
        "vix": 18.2,
        "market_regime": "RISK ON",
        "directives": directives,
    }


def _real_state_snapshot_payload(signals: list) -> dict:
    """Wraps ``signals`` in the real persisted top-level shape written by
    ``reporting/state_snapshot.py::write_state_snapshot``.
    """
    return {
        "timestamp": "2026-09-05T12:00:00+00:00",
        "tickers": [s.get("symbol") for s in signals],
        "holdings": [],
        "market_regime": "RISK ON",
        "vix": 18.2,
        "signals": signals,
    }


def _real_greeks_dict() -> dict:
    """A REALISTIC, non-empty/non-zero portfolio Greeks dict matching
    ``pilots/options_risk.py::calculate_portfolio_greeks``'s real populated
    return shape -- every key that function actually returns, with genuine
    non-zero values so a test can prove the generator surfaced the REAL
    numbers rather than silently substituting a flat/empty result.
    """
    return {
        "total_positions": 3,
        "stock_positions_count": 1,
        "option_positions_count": 2,
        "net_delta_shares": 42.5,
        "net_dollar_delta": 8500.25,
        "net_gamma": 0.0231,
        "net_theta_daily": -12.75,
        "net_vega_1pct": 3.4,
        "beta_weighted_delta_spy": 15.2,
        "positions_with_missing_data": [],
        "beta_excluded_symbols": [],
        "symbols_with_estimated_beta": ["AAPL"],
        "spy_spot": 550.10,
        "spy_spot_resolved": True,
        "positions": [{"symbol": "AAPL", "position_delta": 42.5}],
    }


def _empty_book_greeks_dict() -> dict:
    """The REAL all-zero (genuinely zero, not missing) shape
    ``calculate_portfolio_greeks`` returns for an empty book."""
    return {
        "total_positions": 0,
        "stock_positions_count": 0,
        "option_positions_count": 0,
        "net_delta_shares": 0.0,
        "net_dollar_delta": 0.0,
        "net_gamma": 0.0,
        "net_theta_daily": 0.0,
        "net_vega_1pct": 0.0,
        "beta_weighted_delta_spy": 0.0,
        "positions_with_missing_data": [],
        "beta_excluded_symbols": [],
        "symbols_with_estimated_beta": [],
        "spy_spot": None,
        "spy_spot_resolved": True,
        "positions": [],
    }


def _real_trade_history_unavailable_view() -> dict:
    """The real ``available: False`` cold-start/failure shape from
    ``pilots/trade_history.py::_empty_view`` -- distinct from a genuine "0
    real trades" view (see ``_real_trade_history_zero_trades_view`` below):
    this is "data has never been ingested", NOT "we ingested and found
    nothing".
    """
    return {
        "trades": [],
        "summary": {"n_trades": 0, "total_realized_pnl": 0.0},
        "total": 0,
        "limit": 50,
        "offset": 0,
        "symbols": [],
        "available": False,
        "source": "durable_store",
        "last_ingested_at": None,
    }


def _real_trade_history_zero_trades_view() -> dict:
    """The real ``available: True, summary.n_trades: 0`` shape
    ``trade_history_view`` returns when the durable store HAS been ingested
    but the account genuinely has zero CLOSED trades yet.
    """
    return {
        "trades": [],
        "summary": {
            "n_trades": 0,
            "total_realized_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "win_rate": None,
            "profit_factor": None,
        },
        "total": 0,
        "limit": 50,
        "offset": 0,
        "symbols": [],
        "available": True,
        "source": "durable_store",
        "last_ingested_at": "2026-09-01T08:00:00+00:00",
    }


def _real_trade_history_populated_view() -> dict:
    """A durable-store view with one real closed trade, shaped per
    ``pilots/trade_history.py::trade_history_view``'s success return and
    ``pilots.realized._trade_to_json`` row shape.
    """
    return {
        "trades": [
            {
                "symbol": "NVDA",
                "quantity": 10.0,
                "entry_ts": "2026-08-01T14:30:00+00:00",
                "exit_ts": "2026-08-15T15:00:00+00:00",
                "entry_price": 118.25,
                "exit_price": 132.40,
                "realized_pnl": 141.50,
                "return_pct": 11.97,
                "holding_days": 14,
            }
        ],
        "summary": {
            "n_trades": 1,
            "total_realized_pnl": 141.50,
            "gross_profit": 141.50,
            "gross_loss": 0.0,
            "win_rate": 1.0,
            "profit_factor": None,
        },
        "total": 1,
        "limit": 50,
        "offset": 0,
        "symbols": ["NVDA"],
        "available": True,
        "source": "durable_store",
        "last_ingested_at": "2026-09-01T08:00:00+00:00",
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _count_unescaped_pipe_cells(markdown_row: str) -> int:
    """Split a Markdown table row on UNESCAPED '|' only, mirroring how a
    real Markdown renderer (and NotebookLM's own table ingestion) would
    parse it -- an escaped ``\\|`` must NOT count as a column delimiter.
    """
    sentinel = "\x00ESCAPED_PIPE\x00"
    protected = markdown_row.replace("\\|", sentinel)
    cells = protected.split("|")
    if cells and cells[0].strip() == "":
        cells = cells[1:]
    if cells and cells[-1].strip() == "":
        cells = cells[:-1]
    return len(cells)


def _mock_all_modular_upstreams(monkeypatch, tmp_path: Path) -> None:
    """Shared setup for the CLI-flag-filtering tests below: mocks every
    upstream dependency the 5 modular generators need so `build_export()`
    can run end-to-end without hitting real stores/network."""
    _patch_output_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
    )
    monkeypatch.setattr(
        "pilots.paper_broker.get_portfolio_greeks", lambda: _empty_book_greeks_dict()
    )
    monkeypatch.setattr(
        "pilots.trade_history.trade_history_view",
        lambda **kwargs: _real_trade_history_unavailable_view(),
    )


# ---------------------------------------------------------------------------
# _is_missing
# ---------------------------------------------------------------------------

class TestIsMissing:
    """``_is_missing`` is the shared missingness primitive ``_fmt_money``/
    ``_fmt_num``/``_fmt_pct`` consolidate onto -- the key regression this
    enables over a bare ``isinstance(value, float) and value != value``
    check is catching ``pandas.NA``/``pandas.NaT`` (see
    ``TestNaNFormattingAcrossHelpers`` below).
    """

    def test_none_is_missing(self):
        assert notebooklm._is_missing(None)

    def test_float_nan_is_missing(self):
        assert notebooklm._is_missing(float("nan"))

    def test_numpy_nan_is_missing(self):
        assert notebooklm._is_missing(np.float64("nan"))

    def test_pandas_na_is_missing(self):
        assert notebooklm._is_missing(pd.NA)

    def test_pandas_nat_is_missing(self):
        assert notebooklm._is_missing(pd.NaT)

    def test_genuine_zero_is_not_missing(self):
        # CONSTRAINT #4: a real 0 must never be conflated with missing data.
        assert not notebooklm._is_missing(0)
        assert not notebooklm._is_missing(0.0)

    def test_genuine_value_is_not_missing(self):
        assert not notebooklm._is_missing(42.5)
        assert not notebooklm._is_missing(-3)


# ---------------------------------------------------------------------------
# _fmt_pct
# ---------------------------------------------------------------------------

class TestFmtPct:
    def test_none_is_na(self):
        assert notebooklm._fmt_pct(None) == "N/A"

    def test_nan_is_na(self):
        assert notebooklm._fmt_pct(float("nan")) == "N/A"

    def test_zero_renders_as_honest_zero_not_na(self):
        assert notebooklm._fmt_pct(0.0) == "0.00%"

    def test_positive_value_default_precision(self):
        # 53.35 is the exact IVR_Proxy value from
        # _real_directive_true_ivr_null_ivr_proxy_present() above.
        assert notebooklm._fmt_pct(53.35) == "53.35%"

    def test_custom_precision(self):
        assert notebooklm._fmt_pct(53.3546, precision=1) == "53.4%"
        assert notebooklm._fmt_pct(53.3546, precision=0) == "53%"


# ---------------------------------------------------------------------------
# _md_escape -- pipe-character escaping (bug class #4's underlying primitive)
# ---------------------------------------------------------------------------

class TestMdEscape:
    def test_escapes_literal_pipe_character(self):
        """REGRESSION (bug class #4): a realistic buy_range/sell_range value
        containing a literal ' | ' must come back with the pipe escaped so
        it cannot be misread as a Markdown table column delimiter."""
        raw = "Trim @ $13.30 | Stop @ $13.07"
        escaped = notebooklm._md_escape(raw)
        assert "\\|" in escaped
        import re
        unescaped_pipes = re.findall(r"(?<!\\)\|", escaped)
        assert unescaped_pipes == []

    def test_none_renders_default(self):
        assert notebooklm._md_escape(None) == "N/A"
        assert notebooklm._md_escape(None, default="") == ""
        assert notebooklm._md_escape(None, default="Headline") == "Headline"

    def test_multiple_pipes_all_escaped(self):
        raw = "A | B | C"
        escaped = notebooklm._md_escape(raw)
        import re
        assert re.findall(r"(?<!\\)\|", escaped) == []


# ---------------------------------------------------------------------------
# _load_json_file / _atomic_write_file
# ---------------------------------------------------------------------------

class TestLoadJsonFile:
    def test_valid_file_parses(self, tmp_path: Path):
        p = tmp_path / "x.json"
        p.write_text(json.dumps({"a": 1}), encoding="utf-8")
        assert notebooklm._load_json_file(p) == {"a": 1}

    def test_missing_file_degrades_to_empty_dict(self, tmp_path: Path):
        assert notebooklm._load_json_file(tmp_path / "does_not_exist.json") == {}

    def test_malformed_json_degrades_to_empty_dict(self, tmp_path: Path):
        p = tmp_path / "corrupt.json"
        p.write_text("{not valid json", encoding="utf-8")
        assert notebooklm._load_json_file(p) == {}


class TestAtomicWriteFile:
    def test_writes_content_and_leaves_no_stray_temp_file(self, tmp_path: Path):
        target = tmp_path / "out.md"
        notebooklm._atomic_write_file(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"
        assert list(tmp_path.glob("out.md.tmp.*")) == []

    def test_overwrites_existing_file(self, tmp_path: Path):
        target = tmp_path / "out.md"
        notebooklm._atomic_write_file(target, "first")
        notebooklm._atomic_write_file(target, "second")
        assert target.read_text(encoding="utf-8") == "second"


# ---------------------------------------------------------------------------
# BUG CLASS #5 regression: NaN/NA/NaT formatting across all three helpers
# ---------------------------------------------------------------------------

class TestNaNFormattingAcrossHelpers:
    """REGRESSION: a naive ``value is None or (isinstance(value, float) and
    value != value)`` check silently mis-renders ``pandas.NA``/
    ``pandas.NaT`` (neither is a ``float`` instance) as whatever
    ``str(value)`` produces (e.g. a literal ``"<NA>"``/``"NaT"`` string),
    rather than the honest "N/A" every other missing-data path produces.
    """

    @pytest.mark.parametrize("value", [pd.NA, pd.NaT, np.float64("nan"), None, float("nan")])
    def test_fmt_num_never_renders_raw_sentinel_strings(self, value):
        result = notebooklm._fmt_num(value)
        assert result == "N/A"
        assert "<NA>" not in result
        assert "NaT" not in result

    @pytest.mark.parametrize("value", [pd.NA, pd.NaT, np.float64("nan"), None, float("nan")])
    def test_fmt_money_never_renders_raw_sentinel_strings(self, value):
        result = notebooklm._fmt_money(value)
        assert result == "N/A"
        assert "<NA>" not in result
        assert "NaT" not in result

    @pytest.mark.parametrize("value", [pd.NA, pd.NaT, np.float64("nan"), None, float("nan")])
    def test_fmt_pct_never_renders_raw_sentinel_strings(self, value):
        result = notebooklm._fmt_pct(value)
        assert result == "N/A"
        assert "<NA>" not in result
        assert "NaT" not in result


# ---------------------------------------------------------------------------
# BUG CLASS #1: Portfolio Greeks correct delegation
# ---------------------------------------------------------------------------

class TestPortfolioGreeksDelegation:
    def test_delegates_to_paper_broker_get_portfolio_greeks_exactly_once(
        self, tmp_path: Path, monkeypatch
    ):
        """REGRESSION (CRITICAL fabrication bug): the prior generator
        reinvented Greeks wiring instead of delegating to
        ``pilots.paper_broker.get_portfolio_greeks()`` (which itself
        resolves a real SPY spot and never fabricates a default price).
        Proves the generator calls THAT function specifically -- not
        ``pilots.options_risk.calculate_portfolio_greeks`` directly -- with
        realistic non-zero output surviving into the rendered text."""
        call_log = []

        def _fake_get_portfolio_greeks():
            call_log.append(1)
            return _real_greeks_dict()

        monkeypatch.setattr(
            "pilots.paper_broker.get_portfolio_greeks", _fake_get_portfolio_greeks
        )

        text = notebooklm.generate_portfolio_greeks_source(store=None, output_dir=tmp_path)

        assert len(call_log) == 1, (
            "generate_portfolio_greeks_source must call "
            "pilots.paper_broker.get_portfolio_greeks() exactly once"
        )
        assert "42.5" in text  # net_delta_shares
        assert notebooklm._fmt_money(8500.25) in text  # net_dollar_delta
        assert "15.2" in text  # beta_weighted_delta_spy
        assert notebooklm._fmt_money(550.10) in text  # spy_spot

    def test_never_calls_calculate_portfolio_greeks_directly(self, tmp_path: Path, monkeypatch):
        """The generator must not bypass pilots.paper_broker and call
        pilots.options_risk.calculate_portfolio_greeks itself."""
        direct_call_log = []

        def _boom_if_called_directly(*args, **kwargs):
            direct_call_log.append(1)
            return _real_greeks_dict()

        monkeypatch.setattr(
            "pilots.options_risk.calculate_portfolio_greeks", _boom_if_called_directly
        )
        monkeypatch.setattr(
            "pilots.paper_broker.get_portfolio_greeks", lambda: _real_greeks_dict()
        )

        notebooklm.generate_portfolio_greeks_source(store=None, output_dir=tmp_path)

        assert direct_call_log == [], (
            "generate_portfolio_greeks_source must delegate to "
            "pilots.paper_broker.get_portfolio_greeks(), never call "
            "pilots.options_risk.calculate_portfolio_greeks() itself"
        )

    def test_empty_book_renders_honest_zeros_not_na(self, tmp_path: Path, monkeypatch):
        """A genuinely empty paper book must render as honest zeros, not
        N/A -- CONSTRAINT #4 applies symmetrically to a real 0 as much as
        it does to a fabricated one. `store=None` here is deliberate (the
        Greeks section has no dependency on `store` at all) and legitimately
        makes the SEPARATE portfolio-snapshot section report "unavailable"
        (already covered by its own tests) -- this test scopes its
        assertion to the Greeks section specifically, not the whole
        document."""
        monkeypatch.setattr(
            "pilots.paper_broker.get_portfolio_greeks", lambda: _empty_book_greeks_dict()
        )
        text = notebooklm.generate_portfolio_greeks_source(store=None, output_dir=tmp_path)
        greeks_section = text.split("## Net Portfolio Greeks", 1)[1]
        greeks_section = greeks_section.split("## Open Positions", 1)[0]
        assert "0" in greeks_section
        assert "unavailable" not in greeks_section.lower()

    def test_upstream_failure_degrades_honestly(self, tmp_path: Path, monkeypatch):
        def _boom():
            raise RuntimeError("PaperAccountStore DB unavailable")

        monkeypatch.setattr("pilots.paper_broker.get_portfolio_greeks", _boom)
        text = notebooklm.generate_portfolio_greeks_source(store=None, output_dir=tmp_path)
        assert "unavailable" in text.lower()


# ---------------------------------------------------------------------------
# BUG CLASS #3: True_IVR/IVR_Proxy fallback
# ---------------------------------------------------------------------------

class TestTrueIvrFallback:
    def test_null_true_ivr_falls_back_to_real_ivr_proxy(self, tmp_path: Path):
        """REGRESSION: a directive with True_IVR PRESENT-BUT-NULL alongside
        a real IVR_Proxy (the actual common production shape) must render
        the real IVR_Proxy value, not "N/A". The fixture only populates
        Symbol/Strategy/Action/True_IVR/IVR_Proxy -- every OTHER column
        (Price/Short Leg/Long Leg/Net Premium/Trend Bias) genuinely has no
        data and correctly renders "N/A" too, so this checks the specific
        IV Rank cell, not "no N/A anywhere in the row"."""
        directive = _real_directive_true_ivr_null_ivr_proxy_present()
        payload = _real_options_matrix_payload([directive])
        _write_json(tmp_path / "options_matrix.json", payload)

        text = notebooklm.generate_options_matrix_source(output_dir=tmp_path)

        assert "53.35%" in text
        row_lines = [ln for ln in text.splitlines() if "MSFT" in ln]
        assert row_lines, "expected a rendered row for MSFT"
        # The IV Rank cell specifically -- an unambiguous, precise value
        # (not a substring that could collide with another column).
        assert "| 53.35% |" in row_lines[0]

    def test_both_ivr_keys_entirely_absent_renders_na_without_keyerror(
        self, tmp_path: Path
    ):
        """A directive missing BOTH True_IVR and IVR_Proxy keys entirely
        must render "N/A" and must NOT raise KeyError."""
        directive = _directive_both_ivr_keys_absent()
        payload = _real_options_matrix_payload([directive])
        _write_json(tmp_path / "options_matrix.json", payload)

        text = notebooklm.generate_options_matrix_source(output_dir=tmp_path)  # must not raise

        row_lines = [ln for ln in text.splitlines() if "FAILCO" in ln]
        assert row_lines, "expected a rendered row for FAILCO"
        assert "N/A" in row_lines[0]

    def test_true_ivr_real_value_preferred_over_ivr_proxy(self, tmp_path: Path):
        """When True_IVR is a genuine finite value, it must win over
        IVR_Proxy."""
        directive = dict(_real_directive_true_ivr_null_ivr_proxy_present())
        directive["True_IVR"] = 61.2
        directive["IVR_Proxy"] = 53.35
        payload = _real_options_matrix_payload([directive])
        _write_json(tmp_path / "options_matrix.json", payload)

        text = notebooklm.generate_options_matrix_source(output_dir=tmp_path)

        row_lines = [ln for ln in text.splitlines() if "MSFT" in ln]
        assert row_lines
        assert "61.2" in row_lines[0]
        assert "53.35" not in row_lines[0]


# ---------------------------------------------------------------------------
# BUG CLASS #4: pipe-character escaping in a fully rendered table row
# ---------------------------------------------------------------------------

class TestPipeEscapingEndToEnd:
    def test_buy_sell_range_pipe_does_not_corrupt_table_column_count(
        self, tmp_path: Path
    ):
        """REGRESSION: a realistic buy_range/sell_range value containing a
        literal ' | ' must not silently split into extra Markdown table
        columns. Verified by actually splitting the rendered row on
        UNESCAPED '|' and counting cells, not just checking substring
        presence."""
        signal = _real_signal_with_pipe_ranges()
        snap = _real_state_snapshot_payload([signal])
        _write_json(tmp_path / "state_snapshot.json", snap)

        text = notebooklm.generate_signals_picks_source(output_dir=tmp_path)

        lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
        assert len(lines) >= 2, "expected at least a header + separator row"
        header_line = lines[0]
        header_cell_count = _count_unescaped_pipe_cells(header_line)

        data_lines = [ln for ln in lines if "XYZ" in ln]
        assert data_lines, "expected a rendered row for XYZ"
        data_cell_count = _count_unescaped_pipe_cells(data_lines[0])

        assert data_cell_count == header_cell_count, (
            f"row has {data_cell_count} cells but header defines "
            f"{header_cell_count} -- an unescaped '|' inside buy_range/"
            f"sell_range corrupted the table structure"
        )
        assert "13.30" in data_lines[0]
        assert "13.07" in data_lines[0]


# ---------------------------------------------------------------------------
# BUG CLASS #6: trade journal 0-trades vs fetch-failed
# ---------------------------------------------------------------------------

class TestTradeJournalAvailability:
    def test_unavailable_view_renders_distinct_message_from_zero_trades_view(
        self, monkeypatch, tmp_path: Path
    ):
        """REGRESSION: ``trade_history_view`` returning the real
        ``available=False`` cold-start/failure shape must render TEXT
        DISTINCT from the real ``available=True, summary.n_trades=0``
        shape."""
        monkeypatch.setattr(
            "pilots.trade_history.trade_history_view",
            lambda **kwargs: _real_trade_history_unavailable_view(),
        )
        unavailable_text = notebooklm.generate_trade_journal_source(output_dir=tmp_path)

        monkeypatch.setattr(
            "pilots.trade_history.trade_history_view",
            lambda **kwargs: _real_trade_history_zero_trades_view(),
        )
        zero_trades_text = notebooklm.generate_trade_journal_source(output_dir=tmp_path)

        assert unavailable_text != zero_trades_text
        assert len(unavailable_text.strip()) > 0
        assert len(zero_trades_text.strip()) > 0

    def test_populated_view_renders_real_trade_row(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            "pilots.trade_history.trade_history_view",
            lambda **kwargs: _real_trade_history_populated_view(),
        )
        text = notebooklm.generate_trade_journal_source(output_dir=tmp_path)
        assert "NVDA" in text
        assert notebooklm._fmt_money(141.50) in text or "141.50" in text

    def test_calls_trade_history_view_with_expected_pagination_args(
        self, monkeypatch, tmp_path: Path
    ):
        seen_kwargs = {}

        def _spy(**kwargs):
            seen_kwargs.update(kwargs)
            return _real_trade_history_zero_trades_view()

        monkeypatch.setattr("pilots.trade_history.trade_history_view", _spy)
        notebooklm.generate_trade_journal_source(output_dir=tmp_path)

        assert seen_kwargs.get("limit") == 50
        assert seen_kwargs.get("offset") == 0

    def test_upstream_exception_degrades_honestly(self, monkeypatch, tmp_path: Path):
        def _boom(**kwargs):
            raise RuntimeError("BrokerFillsStore DB locked")

        monkeypatch.setattr("pilots.trade_history.trade_history_view", _boom)
        text = notebooklm.generate_trade_journal_source(output_dir=tmp_path)
        assert "unavailable" in text.lower()


# ---------------------------------------------------------------------------
# BUG CLASS #2: per-generator crash isolation
# ---------------------------------------------------------------------------

class TestPerGeneratorCrashIsolation:
    def test_signals_generator_raising_does_not_prevent_other_modular_files(
        self, tmp_path: Path, monkeypatch
    ):
        """REGRESSION (CRITICAL crash-cascade bug): one generator raising an
        uncaught exception must not prevent build_export() from writing the
        other 4 modular files. Forces the crash by monkeypatching the
        generator directly so this test conclusively proves build_export()'s
        OWN outer crash isolation, independent of any individual generator's
        internal robustness."""
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore", lambda readonly=True: _FakeHistoricalStore()
        )
        monkeypatch.setattr(
            "pilots.paper_broker.get_portfolio_greeks", lambda: _real_greeks_dict()
        )
        monkeypatch.setattr(
            "pilots.trade_history.trade_history_view",
            lambda **kwargs: _real_trade_history_populated_view(),
        )
        payload = _real_options_matrix_payload(
            [_real_directive_true_ivr_null_ivr_proxy_present()]
        )
        _write_json(tmp_path / "options_matrix.json", payload)

        def _boom(*args, **kwargs):
            raise RuntimeError("signals generator exploded")

        monkeypatch.setattr(notebooklm, "generate_signals_picks_source", _boom)

        notebooklm.build_export(modular=True, consolidated=False)  # must not raise

        modular_dir = tmp_path / "notebooklm"
        journal_text = (modular_dir / "04_trade_journal_and_ledger.md").read_text(
            encoding="utf-8"
        )
        matrix_text = (modular_dir / "05_options_directives_and_matrix.md").read_text(
            encoding="utf-8"
        )
        signals_path = modular_dir / "03_strategy_signals_and_picks.md"

        assert "NVDA" in journal_text
        assert "53.35%" in matrix_text or "MSFT" in matrix_text

        assert signals_path.exists()
        signals_text = signals_path.read_text(encoding="utf-8")
        assert len(signals_text.strip()) > 0
        assert "unavailable" in signals_text.lower() or "error" in signals_text.lower() or "failed" in signals_text.lower()

    def test_malformed_kelly_target_degrades_honestly_when_called_directly(
        self, tmp_path: Path
    ):
        """Complementary, more surgical case: a malformed (non-numeric)
        kelly_target in a REAL state_snapshot.json must not raise when
        generate_signals_picks_source is called directly."""
        snap = _real_state_snapshot_payload([_malformed_kelly_target_signal()])
        _write_json(tmp_path / "state_snapshot.json", snap)

        text = notebooklm.generate_signals_picks_source(output_dir=tmp_path)  # must not raise
        assert isinstance(text, str)
        assert len(text.strip()) > 0

    def test_portfolio_greeks_crash_does_not_prevent_macro_or_options(
        self, tmp_path: Path, monkeypatch
    ):
        _patch_output_dir(monkeypatch, tmp_path)
        monkeypatch.setattr(
            notebooklm, "HistoricalStore",
            lambda readonly=True: _FakeHistoricalStore(
                get_macro=lambda series_id: pd.Series([18.5], index=pd.to_datetime(["2026-07-31"]))
            ),
        )
        payload = _real_options_matrix_payload(
            [_real_directive_true_ivr_null_ivr_proxy_present()]
        )
        _write_json(tmp_path / "options_matrix.json", payload)
        monkeypatch.setattr(
            "pilots.trade_history.trade_history_view",
            lambda **kwargs: _real_trade_history_unavailable_view(),
        )

        def _boom():
            raise RuntimeError("greeks exploded")

        monkeypatch.setattr(notebooklm, "generate_portfolio_greeks_source", _boom)

        notebooklm.build_export(modular=True, consolidated=False)  # must not raise

        modular_dir = tmp_path / "notebooklm"
        macro_text = (modular_dir / "01_macro_and_regime.md").read_text(encoding="utf-8")
        matrix_text = (modular_dir / "05_options_directives_and_matrix.md").read_text(
            encoding="utf-8"
        )
        greeks_path = modular_dir / "02_portfolio_and_greeks.md"

        assert "18.5" in macro_text
        assert "MSFT" in matrix_text
        assert greeks_path.exists()
        assert len(greeks_path.read_text(encoding="utf-8").strip()) > 0


# ---------------------------------------------------------------------------
# Individual generator happy-path / degraded-path coverage
# ---------------------------------------------------------------------------

class TestGenerateMacroRegimeSource:
    def test_happy_path(self, tmp_path: Path):
        fake_store = _FakeHistoricalStore(
            get_macro=lambda series_id: {
                "VIXCLS": pd.Series([17.0, 18.5], index=pd.to_datetime(["2026-07-30", "2026-07-31"])),
                "T10Y2Y": pd.Series([0.40, 0.42], index=pd.to_datetime(["2026-07-30", "2026-07-31"])),
                "BAMLH0A0HYM2": pd.Series([3.70, 3.75], index=pd.to_datetime(["2026-07-30", "2026-07-31"])),
            }.get(series_id, pd.Series(dtype=float))
        )
        text = notebooklm.generate_macro_regime_source(fake_store, tmp_path)
        assert "18.5" in text
        assert "0.42" in text
        assert "3.75" in text

    def test_store_none_degrades_honestly(self, tmp_path: Path):
        text = notebooklm.generate_macro_regime_source(None, tmp_path)
        assert "unavailable" in text.lower()

    def test_get_macro_raises_degrades_honestly(self, tmp_path: Path):
        def _boom(series_id, **kwargs):
            raise RuntimeError("FRED down")

        fake_store = _FakeHistoricalStore(get_macro=_boom)
        text = notebooklm.generate_macro_regime_source(fake_store, tmp_path)
        assert "unavailable" in text.lower()


class TestGenerateSignalsPicksSource:
    def test_happy_path(self, tmp_path: Path):
        snap = _real_state_snapshot_payload([_real_signal_with_pipe_ranges()])
        _write_json(tmp_path / "state_snapshot.json", snap)
        text = notebooklm.generate_signals_picks_source(output_dir=tmp_path)
        assert "XYZ" in text

    def test_missing_state_snapshot_degrades_honestly(self, tmp_path: Path):
        text = notebooklm.generate_signals_picks_source(output_dir=tmp_path)  # must not raise
        assert isinstance(text, str)
        assert len(text.strip()) > 0

    def test_empty_signals_list_renders_honest_message(self, tmp_path: Path):
        snap = _real_state_snapshot_payload([])
        _write_json(tmp_path / "state_snapshot.json", snap)
        text = notebooklm.generate_signals_picks_source(output_dir=tmp_path)
        assert "no" in text.lower() or "unavailable" in text.lower()


class TestGenerateTradeJournalSource:
    def test_happy_path(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            "pilots.trade_history.trade_history_view",
            lambda **kwargs: _real_trade_history_populated_view(),
        )
        text = notebooklm.generate_trade_journal_source(output_dir=tmp_path)
        assert "NVDA" in text

    def test_construction_failure_degrades_honestly(self, monkeypatch, tmp_path: Path):
        def _boom(**kwargs):
            raise RuntimeError("store construction failed")

        monkeypatch.setattr("pilots.trade_history.trade_history_view", _boom)
        text = notebooklm.generate_trade_journal_source(output_dir=tmp_path)
        assert "unavailable" in text.lower()


class TestGenerateOptionsMatrixSource:
    def test_happy_path(self, tmp_path: Path):
        payload = _real_options_matrix_payload(
            [_real_directive_true_ivr_null_ivr_proxy_present()]
        )
        _write_json(tmp_path / "options_matrix.json", payload)
        text = notebooklm.generate_options_matrix_source(output_dir=tmp_path)
        assert "MSFT" in text

    def test_missing_options_matrix_json_degrades_honestly(self, tmp_path: Path):
        text = notebooklm.generate_options_matrix_source(output_dir=tmp_path)  # must not raise
        assert isinstance(text, str)
        assert len(text.strip()) > 0

    def test_empty_directives_list_renders_honest_message(self, tmp_path: Path):
        payload = _real_options_matrix_payload([])
        _write_json(tmp_path / "options_matrix.json", payload)
        text = notebooklm.generate_options_matrix_source(output_dir=tmp_path)
        assert "no" in text.lower() or "unavailable" in text.lower()


class TestGenerateConsolidatedSourceModularNote:
    def test_happy_path_includes_all_five_section_headings_worth_of_content(
        self, tmp_path: Path, monkeypatch
    ):
        fake_store = _FakeHistoricalStore(
            get_macro=lambda series_id: pd.Series([18.5], index=pd.to_datetime(["2026-07-31"])),
            latest_account_snapshot=_real_snapshot,
        )
        monkeypatch.setattr(notebooklm, "FollowsStore", lambda: _FakeFollowsStore())

        text = notebooklm.generate_consolidated_source(fake_store, tmp_path)

        assert "18.5" in text  # macro
        assert "## Modular Sources Note" in text
        for fname in notebooklm._MODULAR_SECTION_FILENAMES:
            assert fname in text


# ---------------------------------------------------------------------------
# build_export() CLI-flag-equivalent filtering behavior
# ---------------------------------------------------------------------------

class TestBuildExportFiltering:
    def test_section_options_only_writes_the_options_matrix_file(
        self, tmp_path: Path, monkeypatch
    ):
        """e.g. section="options" must write ONLY
        05_options_directives_and_matrix.md, not the other 4 modular
        files."""
        _mock_all_modular_upstreams(monkeypatch, tmp_path)
        payload = _real_options_matrix_payload(
            [_real_directive_true_ivr_null_ivr_proxy_present()]
        )
        _write_json(tmp_path / "options_matrix.json", payload)

        notebooklm.build_export(section="options")

        modular_dir = tmp_path / "notebooklm"
        assert (modular_dir / "05_options_directives_and_matrix.md").exists()
        for other in (
            "01_macro_and_regime.md",
            "02_portfolio_and_greeks.md",
            "03_strategy_signals_and_picks.md",
            "04_trade_journal_and_ledger.md",
        ):
            assert not (modular_dir / other).exists(), f"{other} must not be written"

        text = (modular_dir / "05_options_directives_and_matrix.md").read_text(encoding="utf-8")
        assert "MSFT" in text

    def test_modular_only_writes_no_consolidated_file(self, tmp_path: Path, monkeypatch):
        _mock_all_modular_upstreams(monkeypatch, tmp_path)
        notebooklm.build_export(modular=True, consolidated=False)
        assert not (tmp_path / "notebooklm_source.md").exists()
        assert (tmp_path / "notebooklm").exists()

    def test_consolidated_only_writes_no_modular_directory_files(
        self, tmp_path: Path, monkeypatch
    ):
        _mock_all_modular_upstreams(monkeypatch, tmp_path)
        notebooklm.build_export(modular=False, consolidated=True)
        assert (tmp_path / "notebooklm_source.md").exists()
        modular_dir = tmp_path / "notebooklm"
        if modular_dir.exists():
            assert list(modular_dir.glob("*.md")) == []


class TestMainCliFlags:
    def test_section_flag_forwards_to_build_export(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            notebooklm, "build_export", lambda *a, **k: calls.append((a, k))
        )
        monkeypatch.setattr("sys.argv", ["export_notebooklm.py", "--section", "options"])
        notebooklm.main()
        assert calls, "main() must call build_export()"
        _, kwargs = calls[-1]
        assert kwargs.get("section") == "options"

    def test_modular_only_flag_forwards_consolidated_false(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            notebooklm, "build_export", lambda *a, **k: calls.append((a, k))
        )
        monkeypatch.setattr("sys.argv", ["export_notebooklm.py", "--modular-only"])
        notebooklm.main()
        assert calls
        _, kwargs = calls[-1]
        assert kwargs.get("consolidated") is False
        assert kwargs.get("modular", True) is True

    def test_consolidated_only_flag_forwards_modular_false(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            notebooklm, "build_export", lambda *a, **k: calls.append((a, k))
        )
        monkeypatch.setattr("sys.argv", ["export_notebooklm.py", "--consolidated-only"])
        notebooklm.main()
        assert calls
        _, kwargs = calls[-1]
        assert kwargs.get("modular") is False
        assert kwargs.get("consolidated", True) is True

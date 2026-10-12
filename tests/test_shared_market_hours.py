"""
tests/test_shared_market_hours.py -- US market-hours helpers
============================================================
Covers ``shared/market_hours.py``: RTH / extended-hours detection,
``is_automatic_run_gated`` (the daemon's automatic-run window gate) and the
holiday-aware, FMP-backed ``is_us_market_open_now`` used before order
submission.

These helpers lived in ``engine/advisory_agent.py`` (main.py's ``--agent``
loop) until shrink step 5.5 archived the agent loop and moved them here; the
tests moved with them unchanged apart from the import path. Fully offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.market_hours import (
    is_automatic_run_gated,
    is_extended_hours,
    is_us_market_open,
)


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Construct a UTC datetime that maps to the given America/New_York wall-clock.

    We compute it by going through ZoneInfo so DST is correctly respected.
    """
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    local = datetime(year, month, day, hour, minute, tzinfo=et)
    return local.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Market-hours detection
# ---------------------------------------------------------------------------


class TestMarketHours:
    def test_rth_open_at_0930_et(self):
        # Pick a known weekday: 2025-06-30 is a Monday.
        # 09:30 ET = 13:30 UTC during DST.
        assert is_us_market_open(_et(2025, 6, 30, 9, 30)) is True

    def test_rth_close_at_1600_et(self):
        assert is_us_market_open(_et(2025, 6, 30, 16, 0)) is True

    def test_rth_just_before_open(self):
        assert is_us_market_open(_et(2025, 6, 30, 9, 29)) is False

    def test_rth_just_after_close(self):
        assert is_us_market_open(_et(2025, 6, 30, 16, 1)) is False

    def test_weekend_is_closed(self):
        # 2025-06-28 is a Saturday.
        assert is_us_market_open(_et(2025, 6, 28, 12, 0)) is False

    def test_sunday_is_closed(self):
        # 2025-06-29 is a Sunday.
        assert is_us_market_open(_et(2025, 6, 29, 12, 0)) is False

    def test_naive_datetime_is_promoted_to_utc(self):
        # A naive datetime at 14:00 UTC = 10:00 ET on a Monday → open.
        naive = datetime(2025, 6, 30, 14, 0)
        assert is_us_market_open(naive) is True

    def test_extended_hours_includes_premarket(self):
        # 07:00 ET on a Monday → extended hours yes, RTH no.
        t = _et(2025, 6, 30, 7, 0)
        assert is_extended_hours(t) is True
        assert is_us_market_open(t) is False

    def test_extended_hours_excludes_overnight(self):
        # 03:00 ET on a Monday → before extended hours window.
        t = _et(2025, 6, 30, 3, 0)
        assert is_extended_hours(t) is False

    def test_extended_hours_excludes_weekend(self):
        t = _et(2025, 6, 28, 12, 0)
        assert is_extended_hours(t) is False


class TestIsAutomaticRunGated:
    """is_automatic_run_gated -- the single shared predicate
    desktop/daemon_runtime.py calls instead of re-deriving
    `extended_hours_only and not is_extended_hours(now)`."""

    def test_gated_when_enabled_and_outside_window(self):
        t = _et(2025, 6, 30, 3, 0)  # 3am ET Monday -- outside the window
        assert is_automatic_run_gated(t, extended_hours_only=True) is True

    def test_not_gated_when_enabled_and_inside_window(self):
        t = _et(2025, 6, 30, 12, 0)  # noon ET Monday -- inside the window
        assert is_automatic_run_gated(t, extended_hours_only=True) is False

    def test_never_gated_when_disabled(self):
        t = _et(2025, 6, 30, 3, 0)  # outside the window, but the flag is off
        assert is_automatic_run_gated(t, extended_hours_only=False) is False


# ---------------------------------------------------------------------------
# is_us_market_open_now -- holiday/early-close aware gate for order submission
# ---------------------------------------------------------------------------


class TestIsUsMarketOpenNow:
    @staticmethod
    def _utc(y, mo, d, h, mi):
        from zoneinfo import ZoneInfo
        return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)

    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        import shared.market_hours as aa
        aa.reset_market_open_cache()
        yield
        aa.reset_market_open_cache()

    def test_fmp_closed_on_a_weekday_counts_as_closed(self, monkeypatch):
        import shared.market_hours as aa
        monkeypatch.setattr(aa, "_fmp_market_is_open", lambda: False)
        assert aa.is_us_market_open_now(self._utc(2026, 10, 2, 11, 0)) is False

    def test_fmp_open_counts_as_open(self, monkeypatch):
        import shared.market_hours as aa
        monkeypatch.setattr(aa, "_fmp_market_is_open", lambda: True)
        assert aa.is_us_market_open_now(self._utc(2026, 10, 2, 11, 0)) is True

    @pytest.mark.parametrize("day", [(2026, 11, 26), (2026, 12, 25)])
    def test_fallback_is_closed_on_nyse_holidays(self, monkeypatch, day):
        import shared.market_hours as aa
        monkeypatch.setattr(aa, "_fmp_market_is_open", lambda: None)
        assert aa.is_us_market_open_now(self._utc(*day, 11, 0)) is False

    def test_fallback_open_on_a_normal_weekday_in_hours(self, monkeypatch):
        import shared.market_hours as aa
        monkeypatch.setattr(aa, "_fmp_market_is_open", lambda: None)
        assert aa.is_us_market_open_now(self._utc(2026, 10, 2, 11, 0)) is True

    def test_answer_is_cached_for_sixty_seconds(self, monkeypatch):
        import shared.market_hours as aa
        calls = []
        monkeypatch.setattr(aa, "_fmp_market_is_open", lambda: calls.append(1) or True)
        t0 = self._utc(2026, 10, 2, 11, 0)
        aa.is_us_market_open_now(t0)
        aa.is_us_market_open_now(t0 + timedelta(seconds=30))
        assert len(calls) == 1
        aa.is_us_market_open_now(t0 + timedelta(seconds=61))
        assert len(calls) == 2

    def test_fmp_payload_parsing(self, monkeypatch):
        import shared.market_hours as aa
        monkeypatch.setattr("data.fmp_client.exchange_market_hours",
                            lambda exchange="NASDAQ": [{"exchange": "NASDAQ", "isMarketOpen": False}])
        assert aa._fmp_market_is_open() is False
        monkeypatch.setattr("data.fmp_client.exchange_market_hours",
                            lambda exchange="NASDAQ": [{"exchange": "NASDAQ"}])
        assert aa._fmp_market_is_open() is None

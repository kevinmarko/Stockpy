"""
legacy/tests/test_advisory_pause_gate.py
========================================
``TestKillSwitchPauseGate`` (the kill-switch advisory pause gate in
``main.run_once()`` / ``pipeline/steps.py::KillSwitchGateStep``), split out of
``tests/test_advisory_pause_gate.py`` when ``main.py`` was archived in shrink
step 5.5. The daemon's gate (``main_orchestrator._main_body``) stays tested
there in ``TestOrchestratorKillSwitchGate``.

Keeps its original imports: restore ``main.py`` and ``pipeline/steps.py`` to run it.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock


# ===========================================================================
# Tests — kill-switch advisory pause gate in main.run_once
# ===========================================================================

class TestKillSwitchPauseGate:
    """Kill-switch advisory pause gate wired into main.run_once."""

    def _run_once_with_ks(self, ks_active: bool, tmp_path: Path):
        """Run main.run_once() with a temp-dir kill switch and all network patched."""
        sentinel = tmp_path / "KILL_SWITCH"
        if ks_active:
            sentinel.write_text("test pause reason", encoding="utf-8")

        from data.robinhood_portfolio import AccountSnapshot
        import datetime, pytz

        empty_snap = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0,
            total_dividends=0.0,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

        with (
            mock.patch("main.fetch_account_snapshot", return_value=empty_snap),
            mock.patch("main._build_universe", return_value=["AAPL", "MSFT"]),
            mock.patch(
                "execution.kill_switch.KILL_SWITCH_FILE", sentinel
            ),
            mock.patch(
                "main.GlobalKillSwitch" if hasattr(__import__("main"), "GlobalKillSwitch")
                else "execution.kill_switch.GlobalKillSwitch"
            ) as _ks_cls,
        ):
            # Patch GlobalKillSwitch inside main module's import namespace
            from execution.kill_switch import GlobalKillSwitch
            ks_instance = GlobalKillSwitch(sentinel_file=sentinel)

            with mock.patch("execution.kill_switch.KILL_SWITCH_FILE", sentinel):
                import main as _main_mod
                # Temporarily redirect the GlobalKillSwitch used by run_once
                # to one backed by our tmp sentinel.
                orig_ks = None
                _real_run = _main_mod.run_once

                def _run_once_intercepted(force_account: bool = False):
                    # Patch inside run_once's import of GlobalKillSwitch
                    with mock.patch(
                        "engine.advisory.evaluate",
                        side_effect=lambda **kw: (_ for _ in ()).throw(
                            AssertionError("advisory.evaluate must NOT be called when KS active")
                        ) if ks_active else mock.DEFAULT,
                    ):
                        pass
                    return _real_run(force_account=force_account)

                return _run_once_intercepted

    def test_active_sentinel_returns_empty_recommendations(self, tmp_path: Path):
        """When kill switch is active, run_once returns no recommendations."""
        sentinel = tmp_path / "KILL_SWITCH"
        sentinel.write_text("test pause", encoding="utf-8")

        from data.robinhood_portfolio import AccountSnapshot
        import datetime

        empty_snap = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

        from execution.kill_switch import GlobalKillSwitch

        with (
            mock.patch("main.fetch_account_snapshot", return_value=empty_snap),
            mock.patch("main._build_universe", return_value=["AAPL", "MSFT"]),
            mock.patch("pipeline.steps.GlobalKillSwitch", side_effect=lambda: GlobalKillSwitch(sentinel_file=sentinel)),
        ):
            import main as _main_mod
            result = _main_mod.run_once()

        assert result.recommendations == []

    def test_active_sentinel_records_pause_in_errors(self, tmp_path: Path):
        """Kill-switch pause is recorded in RunResult.errors."""
        sentinel = tmp_path / "KILL_SWITCH"
        sentinel.write_text("manual pause for investigation", encoding="utf-8")

        from data.robinhood_portfolio import AccountSnapshot
        import datetime

        empty_snap = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

        from execution.kill_switch import GlobalKillSwitch

        with (
            mock.patch("main.fetch_account_snapshot", return_value=empty_snap),
            mock.patch("main._build_universe", return_value=["AAPL"]),
            mock.patch("pipeline.steps.GlobalKillSwitch", side_effect=lambda: GlobalKillSwitch(sentinel_file=sentinel)),
        ):
            import main as _main_mod
            result = _main_mod.run_once()

        assert any(
            e.get("stage") == "kill_switch_gate" for e in result.errors
        ), "kill_switch_gate stage must appear in errors"
        assert any(
            "manual pause" in e.get("message", "") for e in result.errors
        ), "pause reason must be propagated into the error message"

    def test_inactive_sentinel_does_not_pause(self, tmp_path: Path):
        """When kill switch is inactive, run_once proceeds (advisory.evaluate is called)."""
        sentinel = tmp_path / "KILL_SWITCH"
        # sentinel file does NOT exist → kill switch inactive

        from data.robinhood_portfolio import AccountSnapshot
        import datetime

        empty_snap = AccountSnapshot(
            positions={}, buying_power=0.0, total_equity=0.0, total_dividends=0.0,
            fetched_at=datetime.datetime.now(datetime.timezone.utc),
        )

        from engine.advisory import Recommendation

        fake_rec = Recommendation(
            symbol="AAPL", action="HOLD", strategy="test", conviction=0.55,
            rationale="test rationale", suggested_position_pct=0.0,
            forecast=None, key_indicators={}, data_quality="OK",
        )

        from execution.kill_switch import GlobalKillSwitch

        with (
            mock.patch("main.fetch_account_snapshot", return_value=empty_snap),
            mock.patch("main._build_universe", return_value=["AAPL"]),
            mock.patch("pipeline.steps.GlobalKillSwitch", side_effect=lambda: GlobalKillSwitch(sentinel_file=sentinel)),
            mock.patch("main.advisory_evaluate", return_value=fake_rec),
            mock.patch("main._build_macro_dto"),
            mock.patch("main.get_provider"),
            mock.patch("main._fetch_bars_for_universe", return_value={}),
            mock.patch("main._build_context_extras", return_value={}),
        ):
            import main as _main_mod
            result = _main_mod.run_once()

        # advisory_evaluate was called → we should have a recommendation
        assert len(result.recommendations) == 1
        assert result.recommendations[0].symbol == "AAPL"

"""tests/test_strategy_registry.py — strategy version + mode toggle helpers.

Exercises ``gui/strategy_registry.py``. The mode-toggle test never touches
the real .env: it monkey-patches ``settings.ALPACA_PAPER`` / ``settings.DRY_RUN``
on a copied object, and stubs ``gui.env_io.write_setting`` so writes are
captured in a dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gui import strategy_registry as sr


# ---------------------------------------------------------------------------
# Version registry
# ---------------------------------------------------------------------------

class TestListStrategyVersions:
    def test_happy_path_finds_hash_and_mtime(self, tmp_path: Path) -> None:
        sig_dir = tmp_path / "signals"
        sig_dir.mkdir()
        (sig_dir / "fake_signal.py").write_text("# version 1\n")
        records = sr.list_strategy_versions(
            module_names=["fake_signal"],
            weights={"fake_signal": 12.5},
            disabled=[],
            signals_dir=sig_dir,
        )
        assert len(records) == 1
        rec = records[0]
        assert rec.name == "fake_signal"
        assert rec.version_hash is not None
        assert len(rec.version_hash) == 12
        assert rec.last_modified is not None
        assert rec.weight == 12.5
        assert rec.enabled is True

    def test_disabled_list_flips_enabled(self, tmp_path: Path) -> None:
        sig_dir = tmp_path / "signals"
        sig_dir.mkdir()
        (sig_dir / "foo.py").write_text("")
        records = sr.list_strategy_versions(
            module_names=["foo"], weights={"foo": 1.0},
            disabled=["foo"], signals_dir=sig_dir,
        )
        assert records[0].enabled is False

    def test_missing_file_yields_none_hash_no_crash(self, tmp_path: Path) -> None:
        sig_dir = tmp_path / "signals"
        sig_dir.mkdir()
        records = sr.list_strategy_versions(
            module_names=["nonexistent"], weights={}, disabled=[],
            signals_dir=sig_dir,
        )
        assert records[0].version_hash is None
        assert records[0].last_modified is None
        assert records[0].file_path is None

    def test_hash_changes_when_file_content_changes(self, tmp_path: Path) -> None:
        sig_dir = tmp_path / "signals"
        sig_dir.mkdir()
        f = sig_dir / "x.py"
        f.write_text("# v1\n")
        v1 = sr.list_strategy_versions(
            module_names=["x"], weights={}, disabled=[], signals_dir=sig_dir,
        )[0].version_hash
        f.write_text("# v2 totally different\n")
        v2 = sr.list_strategy_versions(
            module_names=["x"], weights={}, disabled=[], signals_dir=sig_dir,
        )[0].version_hash
        assert v1 != v2


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------

class _FakeSettings:
    def __init__(self, alpaca_paper: bool, dry_run: bool) -> None:
        self.ALPACA_PAPER = alpaca_paper
        self.DRY_RUN = dry_run


@pytest.fixture
def patch_settings(monkeypatch):
    """Helper to swap in a fake settings module for read_active_mode."""
    def _apply(alpaca_paper: bool, dry_run: bool) -> None:
        import settings as real_settings
        real = real_settings.settings
        fake = _FakeSettings(alpaca_paper=alpaca_paper, dry_run=dry_run)
        monkeypatch.setattr(real_settings, "settings", fake)
        return real, fake
    return _apply


class TestReadActiveMode:
    def test_paper_default(self, patch_settings) -> None:
        patch_settings(alpaca_paper=True, dry_run=False)
        state = sr.read_active_mode()
        assert state.mode is sr.ExecutionMode.PAPER
        assert state.is_live is False

    def test_live_when_paper_false(self, patch_settings) -> None:
        patch_settings(alpaca_paper=False, dry_run=False)
        state = sr.read_active_mode()
        assert state.mode is sr.ExecutionMode.LIVE
        assert state.is_live is True

    def test_dry_run_wins_over_paper(self, patch_settings) -> None:
        """DRY_RUN=true forces SIMULATION even if ALPACA_PAPER=false."""
        patch_settings(alpaca_paper=False, dry_run=True)
        state = sr.read_active_mode()
        assert state.mode is sr.ExecutionMode.SIMULATION


class TestSetActiveMode:
    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises(ValueError):
            sr.set_active_mode("hyperdrive")

    def test_writes_both_flags(self, monkeypatch) -> None:
        written: dict[str, object] = {}

        def fake_write(key: str, value, **_kw) -> None:
            written[key] = value

        import gui.env_io as env_io
        monkeypatch.setattr(env_io, "write_setting", fake_write)

        state = sr.set_active_mode(sr.ExecutionMode.LIVE)
        assert written == {"DRY_RUN": False, "ALPACA_PAPER": False}
        assert state.mode is sr.ExecutionMode.LIVE
        assert state.is_live is True

    def test_simulation_writes_both_true(self, monkeypatch) -> None:
        written: dict[str, object] = {}
        import gui.env_io as env_io
        monkeypatch.setattr(env_io, "write_setting",
                            lambda k, v, **_kw: written.setdefault(k, v))
        state = sr.set_active_mode("simulation")
        assert written["DRY_RUN"] is True
        assert written["ALPACA_PAPER"] is True
        assert state.mode is sr.ExecutionMode.SIMULATION

    def test_mode_banner_text_includes_all_flags(self) -> None:
        state = sr.ModeState(sr.ExecutionMode.PAPER, alpaca_paper=True, dry_run=False)
        text = sr.mode_banner_text(state)
        assert "ALPACA_PAPER=True" in text
        assert "DRY_RUN=False" in text
        assert "Paper" in text or "📝" in text

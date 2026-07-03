"""
tests/test_robinhood_mode.py
=============================
Unit tests for ``gui.robinhood_mode.read_robinhood_execution_mode`` and the
banner wiring in ``gui/app.py`` (Tier 8 §Domain-note follow-up).

Coverage
--------
TestModeCoercion         — canonical lowercase, whitespace trim, unknown → off.
TestCapCoercion          — negative + NaN clamp to 0; non-numeric → 0.
TestOffMode              — variant='hidden', empty icon/label (never renders).
TestReviewMode           — variant='warning', amber icon, notes review-only.
TestLiveMode             — variant='error', red icon; cap-set vs cap-unset copy.
TestDegradesGracefully   — settings lookup failure → off/hidden (CONSTRAINT #6).
TestAppWiring            — gui/app.py imports the helper and renders after the
                           existing mode banner (no autonomous action).
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from gui.robinhood_mode import (
    BannerVariant,
    RobinhoodModeState,
    read_robinhood_execution_mode,
    _coerce_cap,
    _coerce_mode,
)


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _fake_settings(mode: str = "off", cap: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        ROBINHOOD_EXECUTION_MODE=mode,
        ROBINHOOD_MAX_NOTIONAL_PER_ORDER=cap,
    )


# ---------------------------------------------------------------------------
# TestModeCoercion
# ---------------------------------------------------------------------------


class TestModeCoercion:
    @pytest.mark.parametrize("raw", ["off", "OFF", " off ", "Off"])
    def test_canonical_off(self, raw):
        assert _coerce_mode(raw) == "off"

    @pytest.mark.parametrize("raw", ["review", "REVIEW", " review "])
    def test_canonical_review(self, raw):
        assert _coerce_mode(raw) == "review"

    @pytest.mark.parametrize("raw", ["live", "LIVE", "  Live "])
    def test_canonical_live(self, raw):
        assert _coerce_mode(raw) == "live"

    @pytest.mark.parametrize("raw", ["", "paper", "on", "yes", "1"])
    def test_unknown_string_coerces_to_off(self, raw):
        assert _coerce_mode(raw) == "off"

    @pytest.mark.parametrize("raw", [None, 0, 1, True, ["live"], {"mode": "live"}])
    def test_non_string_coerces_to_off(self, raw):
        assert _coerce_mode(raw) == "off"


# ---------------------------------------------------------------------------
# TestCapCoercion
# ---------------------------------------------------------------------------


class TestCapCoercion:
    def test_positive_cap(self):
        assert _coerce_cap(500.0) == 500.0

    def test_zero_cap(self):
        assert _coerce_cap(0) == 0.0

    def test_string_number(self):
        assert _coerce_cap("250") == 250.0

    def test_negative_clamps_to_zero(self):
        assert _coerce_cap(-1.0) == 0.0

    def test_nan_clamps_to_zero(self):
        assert _coerce_cap(float("nan")) == 0.0

    @pytest.mark.parametrize("raw", ["not-a-number", None, [500]])
    def test_non_numeric_coerces_to_zero(self, raw):
        assert _coerce_cap(raw) == 0.0


# ---------------------------------------------------------------------------
# TestOffMode
# ---------------------------------------------------------------------------


class TestOffMode:
    def test_off_returns_hidden_variant(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="off"))
        assert state.mode == "off"
        assert state.variant == "hidden"
        assert state.icon == ""
        assert state.label == ""

    def test_off_never_shows_a_label(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="off", cap=10_000.0))
        assert state.label == ""

    def test_unknown_mode_falls_through_to_off(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="paper"))
        assert state.mode == "off"
        assert state.variant == "hidden"


# ---------------------------------------------------------------------------
# TestReviewMode
# ---------------------------------------------------------------------------


class TestReviewMode:
    def test_review_returns_warning_variant(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="review"))
        assert state.mode == "review"
        assert state.variant == "warning"
        assert state.icon == "🟡"

    def test_review_label_mentions_dry_run(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="review"))
        # Must be unambiguous that no real orders can be placed.
        assert "REVIEW" in state.label
        assert "allow_place=False" in state.label

    def test_review_ignores_cap(self):
        # Cap is irrelevant in review mode — every intent is allow_place=False.
        state = read_robinhood_execution_mode(_fake_settings(mode="review", cap=500))
        assert state.variant == "warning"


# ---------------------------------------------------------------------------
# TestLiveMode
# ---------------------------------------------------------------------------


class TestLiveMode:
    def test_live_returns_error_variant(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="live", cap=500.0))
        assert state.mode == "live"
        assert state.variant == "error"
        assert state.icon == "🔴"
        assert state.notional_cap_set is True

    def test_live_label_includes_cap_when_set(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="live", cap=1234.5))
        assert "$1,234.50" in state.label
        assert "LIVE" in state.label

    def test_live_label_flags_missing_cap(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="live", cap=0.0))
        assert state.variant == "error"
        assert state.notional_cap_set is False
        assert "UNSET" in state.label
        # And still MUST NOT claim orders will be placed silently.
        assert "before any placement will succeed" in state.label.lower()

    def test_live_label_mentions_human_confirmation(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="live", cap=500.0))
        assert "human confirmation" in state.label.lower()

    def test_live_negative_cap_treated_as_unset(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="live", cap=-5.0))
        assert state.notional_cap == 0.0
        assert state.notional_cap_set is False
        assert "UNSET" in state.label


# ---------------------------------------------------------------------------
# TestDegradesGracefully — CONSTRAINT #6
# ---------------------------------------------------------------------------


class _BrokenSettings:
    def __getattr__(self, name):
        raise RuntimeError("settings blew up")


class TestDegradesGracefully:
    def test_no_settings_arg_never_raises(self, monkeypatch):
        # When settings_obj is None, the module lazy-imports settings.settings.
        # We simulate that failing by ensuring the returned state falls back
        # to a hidden banner. This isn't a real broken settings import — it's
        # a "no exception propagates past this function" invariant.
        state = read_robinhood_execution_mode(None)
        assert isinstance(state, RobinhoodModeState)

    def test_broken_settings_object_never_raises(self):
        # A settings-like object whose attribute access raises must fall
        # through to the hidden default.
        state = read_robinhood_execution_mode(_BrokenSettings())
        assert state.mode == "off"
        assert state.variant == "hidden"

    def test_returns_dataclass_instance(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="live", cap=1))
        assert isinstance(state, RobinhoodModeState)

    def test_state_is_frozen(self):
        state = read_robinhood_execution_mode(_fake_settings(mode="review"))
        with pytest.raises(FrozenInstanceError):
            state.mode = "live"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestAppWiring — source-grep guards
# ---------------------------------------------------------------------------


class TestAppWiring:
    def test_gui_app_imports_the_helper(self):
        src = (_REPO_ROOT / "gui" / "app.py").read_text(encoding="utf-8")
        assert "from gui.robinhood_mode import read_robinhood_execution_mode" in src

    def test_gui_app_renders_error_and_warning_but_not_hidden(self):
        src = (_REPO_ROOT / "gui" / "app.py").read_text(encoding="utf-8")
        # Search only the tail (after the RH banner marker) so we don't
        # accidentally match st.error() calls unrelated to the Robinhood
        # banner (e.g. safe_panel's own error rendering).
        anchor = "Tier 8: Robinhood execution-mode banner"
        assert anchor in src
        tail = src[src.index(anchor):]
        assert 'variant == "error"' in tail
        assert 'variant == "warning"' in tail
        assert "_rh_mode_state.label" in tail

    def test_banner_rendered_after_advisory_and_run_mode_banners(self):
        src = (_REPO_ROOT / "gui" / "app.py").read_text(encoding="utf-8")
        advisory_idx = src.index("ADVISORY MODE")
        rh_idx = src.index("Tier 8: Robinhood execution-mode banner")
        assert advisory_idx < rh_idx, (
            "Robinhood banner must render AFTER the ADVISORY / run-mode "
            "header so the operator sees both when both apply."
        )

    def test_banner_soft_fails_never_blocks_app(self):
        src = (_REPO_ROOT / "gui" / "app.py").read_text(encoding="utf-8")
        anchor = "Tier 8: Robinhood execution-mode banner"
        # Slice a generous window after the anchor so both the comment block
        # AND the following try/except live inside it.
        tail = src[src.index(anchor):src.index(anchor) + 2000]
        # A bare try/except with a debug log — a broken banner must never
        # crash the whole GUI.
        assert "try:" in tail
        assert "except Exception" in tail

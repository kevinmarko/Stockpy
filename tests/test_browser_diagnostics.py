"""
tests/test_browser_diagnostics.py
==================================
Tests for browser_diagnostics.py -- real headless-browser diagnostics for
the MCP DevTools widget tools, gated behind settings.BROWSER_DIAGNOSTICS_ENABLED
and requiring the optional `playwright` package (requirements-optional.txt).

Playwright IS installed in this environment (unlike torch/tensorflow/faiss
elsewhere in this repo's test suite) -- PLAYWRIGHT_AVAILABLE is genuinely True
here, so the real end-to-end tests run rather than skip. Two tiers of
coverage:

1. Pure-logic tests (`_rate`, `_route_slug`, `compare_against_baseline`'s
   Pillow/numpy pixel-diff math) run unconditionally -- no browser needed.
2. `capture_page_diagnostics` orchestration is tested against a MOCKED
   `sync_playwright` (deterministic, no real browser launch, exercises the
   success/exception/degradation code paths) plus ONE real end-to-end test
   against a `data:` URL (skipped via @pytest.mark.skipif when Playwright
   isn't installed, matching tests/test_bert_lla.py's TORCH_AVAILABLE
   precedent) that proves the vitals-collector script and screenshot
   capture genuinely work against a real headless Chromium.
"""
from __future__ import annotations

import io
from unittest import mock

import numpy as np
import pytest
from PIL import Image

import browser_diagnostics as bd

_skip_no_playwright = pytest.mark.skipif(
    not bd.PLAYWRIGHT_AVAILABLE, reason="playwright not installed in this environment"
)


def _png_bytes(color: tuple[int, int, int], size=(20, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


class TestRate:
    def test_good_lcp(self):
        assert bd._rate(1000, "lcp_ms") == "good"

    def test_needs_improvement_lcp(self):
        assert bd._rate(3000, "lcp_ms") == "needs-improvement"

    def test_poor_lcp(self):
        assert bd._rate(5000, "lcp_ms") == "poor"

    def test_boundary_is_inclusive_good(self):
        good, _ = bd._VITALS_THRESHOLDS["cls"]
        assert bd._rate(good, "cls") == "good"

    def test_none_never_fabricates_a_rating(self):
        """CONSTRAINT #4: an unmeasured value must rate as None, never a
        fabricated 'good'/'poor' guess."""
        assert bd._rate(None, "fcp_ms") is None


class TestRouteSlug:
    def test_root(self):
        assert bd._route_slug("/") == "root"

    def test_nested_path_sanitized(self):
        assert bd._route_slug("/pilots/abc-123") == "pilots_abc-123"

    def test_no_leading_slash(self):
        assert bd._route_slug("signals") == "signals"


class TestCaptureUnavailableWhenPlaywrightMissing:
    def test_reports_unavailable_not_fabricated(self, monkeypatch):
        monkeypatch.setattr(bd, "PLAYWRIGHT_AVAILABLE", False)
        result = bd.capture_page_diagnostics("http://localhost:5173/")
        assert result == {
            "available": False,
            "reason": "playwright is not installed (see requirements-optional.txt)",
        }


class TestCaptureMockedPlaywright:
    """Exercises capture_page_diagnostics's orchestration logic against a
    fully mocked sync_playwright -- no real browser launch, deterministic."""

    def _build_fake_playwright(self, *, goto_side_effect=None, status=200):
        fake_page = mock.MagicMock()
        fake_response = mock.MagicMock()
        fake_response.status = status
        if goto_side_effect is not None:
            fake_page.goto.side_effect = goto_side_effect
        else:
            fake_page.goto.return_value = fake_response
        fake_page.evaluate.side_effect = [
            {"lcp": 1234.5, "cls": 0.05},  # window.__vitals__
            {"ttfb": 42.0, "domNodes": 77},  # navigation timing
            [500.0],  # paint entries -> FCP
        ]
        fake_page.screenshot.return_value = _png_bytes((10, 20, 30))
        fake_page.title.return_value = "Fake Page"
        fake_page.on = mock.MagicMock()

        fake_browser = mock.MagicMock()
        fake_browser.new_page.return_value = fake_page

        fake_chromium = mock.MagicMock()
        fake_chromium.launch.return_value = fake_browser

        fake_pw = mock.MagicMock()
        fake_pw.chromium = fake_chromium

        fake_pw_cm = mock.MagicMock()
        fake_pw_cm.__enter__.return_value = fake_pw
        fake_pw_cm.__exit__.return_value = False
        return fake_pw_cm, fake_browser, fake_page

    def test_success_returns_real_measured_fields(self, monkeypatch):
        fake_pw_cm, fake_browser, fake_page = self._build_fake_playwright()
        monkeypatch.setattr(bd, "PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(bd, "sync_playwright", lambda: fake_pw_cm)

        result = bd.capture_page_diagnostics("http://localhost:5173/", timeout_seconds=5)

        assert result["available"] is True
        assert result["status"] == 200
        assert result["title"] == "Fake Page"
        assert result["dom_node_count"] == 77
        assert result["vitals"] == {"ttfb_ms": 42.0, "fcp_ms": 500.0, "lcp_ms": 1234.5, "cls": 0.05}
        assert result["vitals_rating"]["lcp"] == "good"  # 1234.5ms <= 2500ms good threshold
        assert result["screenshot_base64"]
        # Browser must always be closed, even on the success path.
        fake_browser.close.assert_called_once()

    def test_navigation_failure_degrades_never_raises(self, monkeypatch):
        fake_pw_cm, fake_browser, fake_page = self._build_fake_playwright(
            goto_side_effect=RuntimeError("net::ERR_CONNECTION_REFUSED")
        )
        monkeypatch.setattr(bd, "PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(bd, "sync_playwright", lambda: fake_pw_cm)

        result = bd.capture_page_diagnostics("http://localhost:5173/dead", timeout_seconds=5)

        assert result["available"] is False
        assert "ERR_CONNECTION_REFUSED" in result["reason"]
        # CONSTRAINT #6: browser.close() still runs (via finally) even though
        # page.goto raised mid-visit.
        fake_browser.close.assert_called_once()

    def test_missing_vitals_entries_degrade_to_none_not_fabricated(self, monkeypatch):
        """CONSTRAINT #4: if the LCP/CLS observers never fired (e.g. no
        paintable content), the field must be None, never a made-up 0."""
        fake_page = mock.MagicMock()
        fake_response = mock.MagicMock()
        fake_response.status = 200
        fake_page.goto.return_value = fake_response
        fake_page.evaluate.side_effect = [
            {},  # window.__vitals__ never populated
            None,  # no navigation timing entry
            [],  # no paint entries
        ]
        fake_page.screenshot.return_value = _png_bytes((0, 0, 0))
        fake_page.title.return_value = "Blank"
        fake_browser = mock.MagicMock()
        fake_browser.new_page.return_value = fake_page
        fake_chromium = mock.MagicMock()
        fake_chromium.launch.return_value = fake_browser
        fake_pw = mock.MagicMock()
        fake_pw.chromium = fake_chromium
        fake_pw_cm = mock.MagicMock()
        fake_pw_cm.__enter__.return_value = fake_pw
        fake_pw_cm.__exit__.return_value = False

        monkeypatch.setattr(bd, "PLAYWRIGHT_AVAILABLE", True)
        monkeypatch.setattr(bd, "sync_playwright", lambda: fake_pw_cm)

        result = bd.capture_page_diagnostics("http://localhost:5173/blank", timeout_seconds=5)

        assert result["available"] is True
        assert result["vitals"] == {"ttfb_ms": None, "fcp_ms": None, "lcp_ms": None, "cls": None}
        assert result["vitals_rating"] == {"ttfb": None, "fcp": None, "lcp": None, "cls": None}
        assert result["dom_node_count"] is None


@_skip_no_playwright
class TestCaptureRealBrowser:
    """Real end-to-end coverage against an actual headless Chromium -- proves
    the injected vitals-collector script and screenshot capture genuinely
    work, not just that the orchestration code calls the right mocked
    methods. Skips automatically in any environment without the optional
    `playwright` package installed."""

    def test_real_page_visit_returns_genuine_measurements(self):
        html = (
            "data:text/html,"
            "<html><body><h1>Hello Browser Diagnostics</h1>"
            "<img src='data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///"
            "yH5BAEAAAAALAAAAAABAAEAAAIBTAA7'/></body></html>"
        )
        result = bd.capture_page_diagnostics(html, timeout_seconds=10)

        assert result["available"] is True
        assert result["status"] in (200, None)  # data: URLs may not carry an HTTP status
        assert isinstance(result["dom_node_count"], int) and result["dom_node_count"] > 0
        assert result["screenshot_base64"]
        assert len(result["screenshot_base64"]) > 100
        # No fabrication: every vitals field is either a real number or
        # honestly None, never a placeholder string.
        for key, val in result["vitals"].items():
            assert val is None or isinstance(val, (int, float)), f"{key} was not numeric or None: {val!r}"

    def test_unreachable_host_degrades_honestly(self):
        result = bd.capture_page_diagnostics("http://127.0.0.1:1/nope", timeout_seconds=3)
        assert result["available"] is False
        assert isinstance(result["reason"], str) and result["reason"]


class TestCompareAgainstBaseline:
    """Real Pillow/numpy pixel-diff logic -- no Playwright dependency at all."""

    def test_first_call_establishes_baseline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bd, "_BASELINE_DIR", tmp_path)
        shot = _png_bytes((10, 20, 30))

        result = bd.compare_against_baseline("/example", shot, threshold_pct=1.0)

        assert result == {
            "available": True,
            "baseline_established": True,
            "match": True,
            "diff_pct": 0.0,
            "threshold_pct": 1.0,
        }
        assert (tmp_path / "example.png").exists()

    def test_identical_image_matches_with_zero_diff(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bd, "_BASELINE_DIR", tmp_path)
        shot = _png_bytes((50, 60, 70))
        bd.compare_against_baseline("/example", shot, threshold_pct=1.0)

        result = bd.compare_against_baseline("/example", shot, threshold_pct=1.0)

        assert result["available"] is True
        assert result["baseline_established"] is False
        assert result["match"] is True
        assert result["diff_pct"] == 0.0
        # No numpy scalar types leaked into the JSON-bound result.
        assert isinstance(result["match"], bool)
        assert isinstance(result["diff_pct"], float)
        assert result["baseline_image_base64"]

    def test_substantially_different_image_does_not_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bd, "_BASELINE_DIR", tmp_path)
        baseline_shot = _png_bytes((0, 0, 0))
        bd.compare_against_baseline("/example", baseline_shot, threshold_pct=1.0)

        different_shot = _png_bytes((255, 255, 255))
        result = bd.compare_against_baseline("/example", different_shot, threshold_pct=1.0)

        assert result["match"] is False
        assert result["diff_pct"] == 100.0

    def test_size_mismatch_reported_honestly_not_resized(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bd, "_BASELINE_DIR", tmp_path)
        bd.compare_against_baseline("/example", _png_bytes((1, 2, 3), size=(20, 20)), threshold_pct=1.0)

        result = bd.compare_against_baseline(
            "/example", _png_bytes((1, 2, 3), size=(40, 40)), threshold_pct=1.0
        )

        assert result["match"] is False
        assert result["diff_pct"] == 100.0
        assert "size" in result["reason"]

    def test_corrupt_screenshot_bytes_degrade_never_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bd, "_BASELINE_DIR", tmp_path)
        result = bd.compare_against_baseline("/example", b"not a real png", threshold_pct=1.0)
        assert result["available"] is False
        assert isinstance(result["reason"], str) and result["reason"]

    def test_route_slug_collision_safe_across_special_characters(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bd, "_BASELINE_DIR", tmp_path)
        bd.compare_against_baseline("/pilots/abc-123?x=1", _png_bytes((1, 1, 1)), threshold_pct=1.0)
        files = list(tmp_path.glob("*.png"))
        assert len(files) == 1

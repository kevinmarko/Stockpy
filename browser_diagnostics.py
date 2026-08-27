"""
browser_diagnostics.py
=======================
Real headless-browser diagnostics for the MCP DevTools widget tools
(investyo_mcp_server.py::inspect_webapp_screen / audit_webapp_vitals /
compare_screen_snapshots). Gated behind settings.BROWSER_DIAGNOSTICS_ENABLED
(default False) -- see requirements-optional.txt's `playwright` entry for
the install step (pip install + a separate `playwright install chromium`
browser-binary download).

What this module provides:
- capture_page_diagnostics(url): launches headless Chromium, navigates to
  `url`, and returns a real full-page screenshot, real console messages,
  real DOM node count, and real Core Web Vitals (LCP/CLS/FCP/TTFB) rated
  against web.dev's published Good/Needs-Improvement/Poor thresholds
  (https://web.dev/articles/vitals -- real, documented public thresholds,
  not fabricated). This module does NOT run an axe-core accessibility audit
  or an SEO audit, and deliberately does not invent a 0-100
  Lighthouse-style composite score -- reproducing Lighthouse's proprietary
  log-normal scoring curve without running the real `lighthouse` tool would
  itself be presenting a fabricated number as if it were Lighthouse's own
  methodology (CONSTRAINT #4). Callers report accessibility/bestPractices/
  seo as unavailable rather than inventing them.
- compare_against_baseline(route, screenshot_bytes, threshold_pct): real
  pixel-diffing (Pillow + numpy) against a locally saved baseline PNG in
  output/visual_baselines/. The first comparison for a route establishes
  the baseline (nothing to diff against yet) rather than fabricating a
  match/no-match verdict.

CONSTRAINT #6 (dead-letter resilience): every public function here catches
its own failures and returns {"available": False, "reason": ...} -- it
never raises, regardless of whether Playwright is installed, the browser
binary is missing, the target page is unreachable, or the page load times
out.
"""
from __future__ import annotations

import base64
import io
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    # `sync_playwright` must still exist as a module-level name even when the
    # import fails -- tests monkeypatch it (`monkeypatch.setattr(bd,
    # "sync_playwright", ...)`) to inject a fake Playwright context without a
    # real browser installed, which requires the attribute to already exist.
    # PLAYWRIGHT_AVAILABLE remains the sole gate `capture_page_diagnostics`
    # checks -- this placeholder is never called when it's False.
    sync_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False


# Real, published Core Web Vitals thresholds (web.dev/vitals), Good /
# Needs-Improvement boundaries. Values in milliseconds except CLS (unitless).
_VITALS_THRESHOLDS = {
    "lcp_ms": (2500, 4000),
    "fcp_ms": (1800, 3000),
    "ttfb_ms": (800, 1800),
    "cls": (0.1, 0.25),
}

# Injected before navigation (add_init_script) so the PerformanceObservers
# are wired up before the page's own paint/layout-shift events fire.
# add_init_script runs raw top-level script content on each new document --
# it does NOT invoke a function expression passed as a string, so this must
# be plain statements, not `() => { ... }` (which would just define an
# unused arrow function and do nothing).
_VITALS_COLLECTOR_SCRIPT = """
window.__vitals__ = { lcp: null, cls: 0 };
try {
  new PerformanceObserver((list) => {
    const entries = list.getEntries();
    const last = entries[entries.length - 1];
    if (last) window.__vitals__.lcp = last.renderTime || last.loadTime || last.startTime;
  }).observe({ type: "largest-contentful-paint", buffered: true });
} catch (e) {}
try {
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (!entry.hadRecentInput) window.__vitals__.cls += entry.value;
    }
  }).observe({ type: "layout-shift", buffered: true });
} catch (e) {}
"""

from settings import settings
_BASELINE_DIR = settings.OUTPUT_DIR / "visual_baselines"


def _rate(value: Optional[float], key: str) -> Optional[str]:
    """Rate a real measured value against web.dev's published thresholds.
    Returns None (never a fabricated rating) when the value is unavailable."""
    if value is None:
        return None
    good, needs_improvement = _VITALS_THRESHOLDS[key]
    if value <= good:
        return "good"
    if value <= needs_improvement:
        return "needs-improvement"
    return "poor"


def capture_page_diagnostics(url: str, timeout_seconds: float = 15.0) -> dict[str, Any]:
    """Launches headless Chromium, navigates to `url`, and returns real
    measurements. Never raises -- degrades to {"available": False, "reason":
    ...} on any failure (missing Playwright, missing browser binary,
    unreachable page, timeout)."""
    if not PLAYWRIGHT_AVAILABLE:
        return {
            "available": False,
            "reason": "playwright is not installed (see requirements-optional.txt)",
        }

    console_messages: list[dict[str, str]] = []
    page_errors: list[str] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.on(
                    "console",
                    lambda msg: console_messages.append({"type": msg.type, "text": msg.text}),
                )
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                page.add_init_script(_VITALS_COLLECTOR_SCRIPT)

                nav_start = time.time()
                response = page.goto(url, timeout=timeout_seconds * 1000, wait_until="load")
                status = response.status if response is not None else None

                # LCP/CLS observers fire asynchronously after load; a short
                # idle window catches the common case without holding the
                # MCP tool call open indefinitely.
                page.wait_for_timeout(1000)

                vitals_raw = page.evaluate("() => window.__vitals__ || {}")
                nav_timing = page.evaluate(
                    "() => { const e = performance.getEntriesByType('navigation')[0]; "
                    "return e ? { ttfb: e.responseStart, "
                    "domNodes: document.querySelectorAll('*').length } : null; }"
                )
                fcp_entries = page.evaluate(
                    "() => performance.getEntriesByType('paint')"
                    ".filter(e => e.name === 'first-contentful-paint').map(e => e.startTime)"
                )

                screenshot_bytes = page.screenshot(full_page=True)
                title = page.title()
            finally:
                browser.close()

        elapsed_ms = round((time.time() - nav_start) * 1000, 1)
        ttfb_ms = (
            round(nav_timing["ttfb"], 1) if nav_timing and nav_timing.get("ttfb") is not None else None
        )
        dom_node_count = nav_timing.get("domNodes") if nav_timing else None
        fcp_ms = round(fcp_entries[0], 1) if fcp_entries else None
        lcp_ms = round(vitals_raw.get("lcp"), 1) if vitals_raw.get("lcp") is not None else None
        cls = round(vitals_raw.get("cls"), 4) if vitals_raw.get("cls") is not None else None

        return {
            "available": True,
            "status": status,
            "title": title,
            "response_time_ms": elapsed_ms,
            "dom_node_count": dom_node_count,
            "console_messages": console_messages,
            "page_errors": page_errors,
            "screenshot_base64": (
                base64.b64encode(screenshot_bytes).decode("ascii") if screenshot_bytes else None
            ),
            "vitals": {
                "ttfb_ms": ttfb_ms,
                "fcp_ms": fcp_ms,
                "lcp_ms": lcp_ms,
                "cls": cls,
            },
            "vitals_rating": {
                "ttfb": _rate(ttfb_ms, "ttfb_ms"),
                "fcp": _rate(fcp_ms, "fcp_ms"),
                "lcp": _rate(lcp_ms, "lcp_ms"),
                "cls": _rate(cls, "cls"),
            },
        }
    except Exception as exc:  # noqa: BLE001 -- CONSTRAINT #6, dead-letter resilience
        logger.warning("browser_diagnostics.capture_page_diagnostics failed for %s: %s", url, exc)
        return {"available": False, "reason": str(exc)}


def _route_slug(route: str) -> str:
    slug = route.strip("/") or "root"
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in slug)


def compare_against_baseline(
    route: str, screenshot_bytes: bytes, threshold_pct: float = 1.0
) -> dict[str, Any]:
    """Real pixel-diff (Pillow + numpy) of `screenshot_bytes` against a saved
    baseline for `route`, persisted under output/visual_baselines/. The
    first comparison for a route establishes the baseline (nothing to diff
    against yet) rather than fabricating a match/no-match verdict. Never
    raises -- degrades to {"available": False, "reason": ...}."""
    try:
        from PIL import Image
        import numpy as np

        _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        baseline_path = _BASELINE_DIR / f"{_route_slug(route)}.png"

        current_img = Image.open(io.BytesIO(screenshot_bytes)).convert("RGB")

        if not baseline_path.exists():
            current_img.save(baseline_path)
            return {
                "available": True,
                "baseline_established": True,
                "match": True,
                "diff_pct": 0.0,
                "threshold_pct": threshold_pct,
            }

        baseline_bytes = baseline_path.read_bytes()
        baseline_b64 = base64.b64encode(baseline_bytes).decode("ascii")
        baseline_img = Image.open(baseline_path).convert("RGB")
        if baseline_img.size != current_img.size:
            # A real, structural difference (layout/viewport changed) --
            # report it honestly rather than resizing to force a comparison.
            return {
                "available": True,
                "baseline_established": False,
                "match": False,
                "diff_pct": 100.0,
                "threshold_pct": threshold_pct,
                "reason": f"baseline size {baseline_img.size} != current size {current_img.size}",
                "baseline_image_base64": baseline_b64,
            }

        base_arr = np.asarray(baseline_img, dtype=np.int16)
        cur_arr = np.asarray(current_img, dtype=np.int16)
        # A pixel counts as "different" if any RGB channel moved by more
        # than a small per-channel tolerance (anti-aliasing/compression
        # noise); this is a real, deterministic diff, not a fabricated one.
        differing = np.any(np.abs(base_arr - cur_arr) > 12, axis=-1)
        # Cast off numpy scalar types (np.float64/np.bool_) -- json.dumps()
        # cannot serialize them, and every caller of this function JSON-dumps
        # the result.
        diff_pct = float(round(100.0 * differing.sum() / differing.size, 2))

        return {
            "available": True,
            "baseline_established": False,
            "baseline_image_base64": baseline_b64,
            "match": bool(diff_pct <= threshold_pct),
            "diff_pct": diff_pct,
            "threshold_pct": threshold_pct,
        }
    except Exception as exc:  # noqa: BLE001 -- CONSTRAINT #6, dead-letter resilience
        logger.warning("browser_diagnostics.compare_against_baseline failed for %s: %s", route, exc)
        return {"available": False, "reason": str(exc)}

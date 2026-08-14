"""
mcp_widget_resources.py
========================
Vendors and serves the static MCP "Apps SDK" widget HTML resources consumed
by investyo_mcp_server.py's Pilot-picker flow (list_pilots / get_pilot_detail
/ follow_pilot). Degrades gracefully (returns False, logs one actionable
warning) when the one-time npm build step (mcp_widgets/build/) has never
been run locally — every other MCP tool must keep working with no widgets
registered at all in that case; this is cosmetic, additive functionality,
not load-bearing for the platform.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

WIDGET_DIR = Path(__file__).parent / "mcp_widgets"
BUNDLE_PATH = WIDGET_DIR / "vendor" / "ext-apps-bundle.js"
TEMPLATES_DIR = WIDGET_DIR / "templates"

_BUNDLE_PLACEHOLDER = "/*__EXT_APPS_BUNDLE__*/"
_CSS_PLACEHOLDER = "/*__WIDGET_COMMON_CSS__*/"
_JS_PLACEHOLDER = "/*__WIDGET_COMMON_JS__*/"

# (template filename, ui:// resource URI, human-readable title)
_WIDGET_RESOURCES = [
    ("pilot-picker.html", "ui://widgets/pilot-picker.html", "Pilot Picker"),
    ("pilot-detail.html", "ui://widgets/pilot-detail.html", "Pilot Detail"),
    ("follow-result.html", "ui://widgets/follow-result.html", "Follow Confirmation"),
    ("pilot-compare.html", "ui://widgets/pilot-compare.html", "Pilot Comparison"),
    ("pilot-portfolio.html", "ui://widgets/pilot-portfolio.html", "Portfolio by Pilot"),
    ("equity-curve.html", "ui://widgets/equity-curve.html", "Equity Curve"),
    ("risk-matrix.html", "ui://widgets/risk-matrix.html", "Risk Matrix"),
    ("signal-tree.html", "ui://widgets/signal-tree.html", "Signal Tree"),
    ("execution-queue.html", "ui://widgets/execution-queue.html", "Execution Queue"),
    ("devtools-inspector.html", "ui://widgets/devtools-inspector.html", "DevTools Inspector"),
    ("lighthouse-scorecard.html", "ui://widgets/lighthouse-scorecard.html", "Lighthouse Scorecard"),
    ("backtest-tearsheet.html", "ui://widgets/backtest-tearsheet.html", "Backtest Tear-Sheet"),
    ("macro-regime-radar.html", "ui://widgets/macro-regime-radar.html", "Macro Regime Radar"),
    ("order-ticket.html", "ui://widgets/order-ticket.html", "Order Ticket"),
    ("visual-diff.html", "ui://widgets/visual-diff.html", "Visual Diff"),
    ("network-trace.html", "ui://widgets/network-trace.html", "Network Trace"),
    ("pit-audit-matrix.html", "ui://widgets/pit-audit-matrix.html", "PIT Audit Matrix"),
    ("model-diagnostics.html", "ui://widgets/model-diagnostics.html", "Model Diagnostics"),
    ("strategy-tuner.html", "ui://widgets/strategy-tuner.html", "Strategy Tuner"),
]

_FIX_IT_HINT = "cd mcp_widgets/build && npm install && npm run build"


def render_widget_html(template_filename: str) -> str | None:
    """Read the vendored ext-apps bundle + shared CSS/JS + the named template
    and substitute all three placeholders. Returns None (never raises) if the
    vendored bundle doesn't exist -- callers must treat that as "widgets
    unavailable this run", not an error."""
    if not BUNDLE_PATH.exists():
        return None
    template_path = TEMPLATES_DIR / template_filename
    if not template_path.exists():
        return None
    common_css_path = TEMPLATES_DIR / "_common.css"
    common_js_path = TEMPLATES_DIR / "_common.js"
    if not common_css_path.exists() or not common_js_path.exists():
        return None
    bundle_js = BUNDLE_PATH.read_text(encoding="utf-8")
    common_css = common_css_path.read_text(encoding="utf-8")
    common_js = common_js_path.read_text(encoding="utf-8")
    html = template_path.read_text(encoding="utf-8")
    html = html.replace(_BUNDLE_PLACEHOLDER, bundle_js)
    html = html.replace(_CSS_PLACEHOLDER, common_css)
    html = html.replace(_JS_PLACEHOLDER, common_js)
    return html


def _make_static_resource(html: str):
    """Returns a zero-argument closure over `html`. This is deliberately NOT
    a default-arg lambda (`lambda html=html: html`) -- FastMCP.resource()'s
    decorator inspects inspect.signature(fn) and treats ANY function
    parameter (default or not) as a URI-template variable that must appear
    in the uri string as `{param}`; a plain "ui://..." uri has none, so a
    default-arg lambda raises `ValueError: Mismatch between URI parameters
    set() and function parameters {'html'}` at registration time. A true
    zero-param closure (produced fresh per call, avoiding the classic
    late-binding-loop-variable bug) has no such parameters and registers as
    a plain static resource. Verified empirically against the installed SDK
    (mcp==1.28.1) -- see this module's docstring / the introducing PR for
    the throwaway repro script used to confirm this."""

    def _resource() -> str:
        return html

    return _resource


def register_widget_resources(mcp: "FastMCP") -> bool:
    """Registers the 3 ui:// widget resources on `mcp` if (and only if) the
    vendored bundle has been built locally. Returns True on success, False
    (with a logged, actionable warning) if the one-time npm build step was
    never run -- in which case NO resources are registered at all (a host
    must never be pointed at a ui:// resource that doesn't exist)."""
    rendered: dict[str, tuple[str, str]] = {}
    for template_filename, uri, title in _WIDGET_RESOURCES:
        html = render_widget_html(template_filename)
        if html is None:
            logger.warning(
                "MCP widget assets not built -- Pilot-picker widgets disabled "
                "(all tools still work as plain-text). Fix: run `%s` from the "
                "repo root, then restart this server.",
                _FIX_IT_HINT,
            )
            return False
        rendered[uri] = (html, title)

    for uri, (html, title) in rendered.items():
        mcp.resource(uri, name=title, mime_type="text/html;profile=mcp-app")(
            _make_static_resource(html)
        )
    return True

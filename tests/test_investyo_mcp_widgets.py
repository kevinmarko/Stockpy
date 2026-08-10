"""
tests/test_investyo_mcp_widgets.py
====================================
Unit tests for the MCP Apps SDK widget-rendering layer added on top of
``investyo_mcp_server.py`` (see ``tests/test_investyo_mcp_server.py`` for
the base server test file this one deliberately mirrors the conventions
of): the new ``mcp_widget_resources.py`` module (bundle+template
substitution, resource registration) and the ``_bearer_auth_asgi_middleware``
raw-ASGI helper gating the new ``streamable-http`` transport.

Written against a fixed, pre-agreed contract for two sibling modules
(``mcp_widget_resources.py`` and the ``investyo_mcp_server.py`` widget-wiring
additions) that were being authored CONCURRENTLY by a different agent while
this file was drafted -- so it was written to run standalone against
self-built ``tmp_path`` fixtures wherever possible:

* ``TestRenderWidgetHtml``, ``TestRenderWidgetHtmlMissingBundle``, and
  ``TestRegisterWidgetResourcesDegrade``/``Success`` build their own fixtures
  entirely inside ``tmp_path`` via ``monkeypatch.setattr`` on
  ``mcp_widget_resources.BUNDLE_PATH``/``TEMPLATES_DIR`` and never read the
  repo's real ``mcp_widgets/`` tree.
* ``TestToolMetaWiringConsistency``, ``TestRealBundleIfPresent``,
  ``TestToolOutputUnaffectedByMetaChange``, and ``TestBearerAuthMiddleware``
  additionally depend on ``investyo_mcp_server.py`` actually defining
  ``_WIDGETS_AVAILABLE``/``_PILOT_PICKER_UI``/``_PILOT_DETAIL_UI``/
  ``_FOLLOW_RESULT_UI``/``_bearer_auth_asgi_middleware`` -- both sibling
  modules landed by the time this suite was run, and all 14 tests pass
  (verified: ``pytest tests/test_investyo_mcp_widgets.py -v``).
"""

from __future__ import annotations

import json
import logging
from unittest.mock import Mock

import pytest

import mcp_widget_resources

# ---------------------------------------------------------------------------
# render_widget_html
# ---------------------------------------------------------------------------


def _write_common_assets(tmp_path, css="/* FAKE CSS */", js="/* FAKE JS */"):
    (tmp_path / "_common.css").write_text(css)
    (tmp_path / "_common.js").write_text(js)


class TestRenderWidgetHtml:
    def test_substitutes_all_three_placeholders_and_preserves_static_text(
        self, monkeypatch, tmp_path
    ):
        bundle_path = tmp_path / "fake-bundle.js"
        bundle_path.write_text("globalThis.ExtApps={FAKE:1};")
        monkeypatch.setattr(mcp_widget_resources, "BUNDLE_PATH", bundle_path)
        monkeypatch.setattr(mcp_widget_resources, "TEMPLATES_DIR", tmp_path)

        _write_common_assets(tmp_path)

        template = tmp_path / "fake-template.html"
        template.write_text(
            "<!doctype html>\n"
            "<style>\n"
            "/*__WIDGET_COMMON_CSS__*/\n"
            "</style>\n"
            "<body>STATIC-MARKER-BODY</body>\n"
            "<script>\n"
            "/*__EXT_APPS_BUNDLE__*/\n"
            "/*__WIDGET_COMMON_JS__*/\n"
            "</script>\n"
        )

        result = mcp_widget_resources.render_widget_html("fake-template.html")

        assert result is not None
        # (a) none of the placeholder tokens remain
        assert "__EXT_APPS_BUNDLE__" not in result
        assert "__WIDGET_COMMON_CSS__" not in result
        assert "__WIDGET_COMMON_JS__" not in result
        # (b) the fake bundle/CSS/JS content IS present
        assert "globalThis.ExtApps={FAKE:1};" in result
        assert "/* FAKE CSS */" in result
        assert "/* FAKE JS */" in result
        # (c) surrounding static text from the template is preserved
        assert "STATIC-MARKER-BODY" in result


class TestRenderWidgetHtmlMissingBundle:
    def test_missing_bundle_returns_none_without_raising(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mcp_widget_resources, "BUNDLE_PATH", tmp_path / "does-not-exist.js")
        monkeypatch.setattr(mcp_widget_resources, "TEMPLATES_DIR", tmp_path)
        _write_common_assets(tmp_path)
        (tmp_path / "fake-template.html").write_text("<p>hi</p>")

        result = mcp_widget_resources.render_widget_html("fake-template.html")

        assert result is None

    def test_missing_template_returns_none_without_raising(self, monkeypatch, tmp_path):
        bundle_path = tmp_path / "fake-bundle.js"
        bundle_path.write_text("globalThis.ExtApps={FAKE:1};")
        monkeypatch.setattr(mcp_widget_resources, "BUNDLE_PATH", bundle_path)
        monkeypatch.setattr(mcp_widget_resources, "TEMPLATES_DIR", tmp_path)
        _write_common_assets(tmp_path)

        result = mcp_widget_resources.render_widget_html("does-not-exist.html")

        assert result is None


# ---------------------------------------------------------------------------
# register_widget_resources
# ---------------------------------------------------------------------------


class TestRegisterWidgetResourcesDegrade:
    def test_missing_bundle_registers_nothing_and_warns(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setattr(mcp_widget_resources, "BUNDLE_PATH", tmp_path / "does-not-exist.js")
        monkeypatch.setattr(mcp_widget_resources, "TEMPLATES_DIR", tmp_path)
        _write_common_assets(tmp_path)
        for name in ("pilot-picker.html", "pilot-detail.html", "follow-result.html", "pilot-compare.html"):
            (tmp_path / name).write_text("<p>placeholder</p>")

        fake_mcp = Mock()

        with caplog.at_level(logging.WARNING):
            result = mcp_widget_resources.register_widget_resources(fake_mcp)

        assert result is False
        fake_mcp.resource.assert_not_called()
        assert any("npm install && npm run build" in rec.message for rec in caplog.records)


class TestRegisterWidgetResourcesSuccess:
    def _build_full_fixture(self, tmp_path):
        bundle_path = tmp_path / "fake-bundle.js"
        bundle_path.write_text("globalThis.ExtApps={FAKE:1};")
        _write_common_assets(tmp_path)
        for name in ("pilot-picker.html", "pilot-detail.html", "follow-result.html", "pilot-compare.html"):
            (tmp_path / name).write_text(
                "<!doctype html>\n"
                "<style>/*__WIDGET_COMMON_CSS__*/</style>\n"
                f"<body>{name}</body>\n"
                "<script>\n"
                "/*__EXT_APPS_BUNDLE__*/\n"
                "/*__WIDGET_COMMON_JS__*/\n"
                "</script>\n"
            )
        return bundle_path

    def test_all_four_registered_with_correct_uris_and_mime_type(self, monkeypatch, tmp_path):
        bundle_path = self._build_full_fixture(tmp_path)
        monkeypatch.setattr(mcp_widget_resources, "BUNDLE_PATH", bundle_path)
        monkeypatch.setattr(mcp_widget_resources, "TEMPLATES_DIR", tmp_path)

        fake_mcp = Mock()
        fake_mcp.resource.return_value = Mock(return_value=Mock())

        result = mcp_widget_resources.register_widget_resources(fake_mcp)

        assert result is True
        assert fake_mcp.resource.call_count == 4

        expected_uris = {
            "ui://widgets/pilot-picker.html",
            "ui://widgets/pilot-detail.html",
            "ui://widgets/follow-result.html",
            "ui://widgets/pilot-compare.html",
        }
        seen_uris = set()
        for call in fake_mcp.resource.call_args_list:
            args, kwargs = call
            uri = args[0] if args else kwargs.get("uri")
            seen_uris.add(uri)
            assert kwargs.get("mime_type") == "text/html;profile=mcp-app"

        assert seen_uris == expected_uris


# ---------------------------------------------------------------------------
# investyo_mcp_server.py wiring (requires the sibling module + server edit)
# ---------------------------------------------------------------------------

_EXPECTED_UI_URIS = {
    "_PILOT_PICKER_UI": "ui://widgets/pilot-picker.html",
    "_PILOT_DETAIL_UI": "ui://widgets/pilot-detail.html",
    "_FOLLOW_RESULT_UI": "ui://widgets/follow-result.html",
    "_PILOT_COMPARE_UI": "ui://widgets/pilot-compare.html",
}


class TestToolMetaWiringConsistency:
    def test_widgets_available_is_internally_consistent(self):
        import investyo_mcp_server as srv

        assert isinstance(srv._WIDGETS_AVAILABLE, bool)

        if srv._WIDGETS_AVAILABLE:
            for attr_name, expected_uri in _EXPECTED_UI_URIS.items():
                value = getattr(srv, attr_name)
                assert value is not None, f"{attr_name} should be a dict when widgets are available"
                assert value == {"ui": {"resourceUri": expected_uri}}
        else:
            for attr_name in _EXPECTED_UI_URIS:
                assert getattr(srv, attr_name) is None


class TestRealBundleIfPresent:
    @pytest.mark.skipif(
        not mcp_widget_resources.BUNDLE_PATH.exists(),
        reason=(
            "vendored ext-apps bundle not built locally; run: "
            "cd mcp_widgets/build && npm install && npm run build"
        ),
    )
    def test_real_bundle_renders_and_widgets_available(self):
        import investyo_mcp_server as srv

        result = mcp_widget_resources.render_widget_html("pilot-picker.html")
        assert result is not None
        assert "globalThis.ExtApps=" in result
        assert srv._WIDGETS_AVAILABLE is True


class TestPilotCompareWidgetSmoke:
    """Mirrors ``tests/test_mcp_oauth_flow_smoke.py``'s pattern of exercising
    the real flow headlessly rather than only unit-testing pieces -- runs the
    real vendored bundle (skipped, same condition as TestRealBundleIfPresent,
    when it hasn't been built locally) instead of a synthetic tmp_path
    fixture."""

    @pytest.mark.skipif(
        not mcp_widget_resources.BUNDLE_PATH.exists(),
        reason=(
            "vendored ext-apps bundle not built locally; run: "
            "cd mcp_widgets/build && npm install && npm run build"
        ),
    )
    def test_pilot_compare_renders_with_no_leftover_placeholders(self):
        result = mcp_widget_resources.render_widget_html("pilot-compare.html")
        assert result is not None
        assert "__EXT_APPS_BUNDLE__" not in result
        assert "__WIDGET_COMMON_CSS__" not in result
        assert "__WIDGET_COMMON_JS__" not in result
        assert "globalThis.ExtApps=" in result

    @pytest.mark.skipif(
        not mcp_widget_resources.BUNDLE_PATH.exists(),
        reason=(
            "vendored ext-apps bundle not built locally; run: "
            "cd mcp_widgets/build && npm install && npm run build"
        ),
    )
    def test_pilot_compare_bundle_contains_new_render_functions(self):
        result = mcp_widget_resources.render_widget_html("pilot-compare.html")
        assert result is not None
        assert "function renderComparePanel" in result
        assert "function renderEquityOverlaySvg" in result
        # Also reuses the existing shared helpers verbatim (not re-implemented).
        assert "function deployableBadge" in result
        assert "function categoryChip" in result

    def test_pilot_compare_ui_wiring_consistent_with_widgets_available(self):
        import investyo_mcp_server as srv

        if srv._WIDGETS_AVAILABLE:
            assert srv._PILOT_COMPARE_UI == {"ui": {"resourceUri": "ui://widgets/pilot-compare.html"}}
        else:
            assert srv._PILOT_COMPARE_UI is None

    def test_compare_pilots_tool_meta_matches_constant(self):
        import investyo_mcp_server as srv

        tool = srv.mcp._tool_manager.get_tool("compare_pilots")
        assert tool is not None
        assert tool.meta == srv._PILOT_COMPARE_UI


# ---------------------------------------------------------------------------
# Regression tripwire: the meta= decorator edit changed nothing observable
# about list_pilots / get_pilot_detail / follow_pilot's actual output.
# ---------------------------------------------------------------------------


class TestToolOutputUnaffectedByMetaChange:
    def test_list_pilots_output_unchanged(self, monkeypatch):
        import investyo_mcp_server as srv
        import pilots.scoring as scoring_mod

        monkeypatch.setattr(scoring_mod, "load_snapshot", lambda *a, **k: None)

        result = srv.list_pilots()

        assert isinstance(result, str)
        assert "# Pilots Marketplace" in result
        assert "```json" in result
        payload = json.loads(result.split("```json")[1].split("```")[0])
        assert isinstance(payload, list) and payload

    def test_get_pilot_detail_output_unchanged(self, monkeypatch):
        import investyo_mcp_server as srv
        import pilots.performance as performance_mod
        import pilots.scoring as scoring_mod

        fake_snapshot = {"timestamp": "2026-01-01T00:00:00Z", "signals": []}
        monkeypatch.setattr(scoring_mod, "load_snapshot", lambda *a, **k: fake_snapshot)
        monkeypatch.setattr(
            scoring_mod,
            "pilot_holdings",
            lambda pilot, snap, top_n=None: [
                {"symbol": "AAPL", "weight": 0.6, "score": 0.5, "price": 150.0, "sector": "Technology"}
            ],
        )
        monkeypatch.setattr(
            scoring_mod, "sector_allocation", lambda holdings: [{"sector": "Technology", "weight": 0.6}]
        )
        monkeypatch.setattr(
            scoring_mod,
            "pilot_trades",
            lambda pilot, **k: [{"date": "2026-01-02", "symbol": "AAPL", "side": "ENTER", "weight_delta": 0.6}],
        )
        monkeypatch.setattr(
            performance_mod,
            "pilot_headline",
            lambda pilot, **k: {"sharpe": 1.2, "dsr": 0.99, "pbo": 0.1, "max_drawdown": 0.2, "deployable": True},
        )

        result = srv.get_pilot_detail("trend-following")

        assert isinstance(result, str)
        assert "AAPL" in result
        assert "Technology" in result
        assert "```json" in result

    def test_follow_pilot_output_unchanged(self, monkeypatch):
        import data.historical_store as hs_mod
        import execution.kill_switch as ks_mod
        import investyo_mcp_server as srv
        import pilots.follows_store as fs_mod
        import pilots.mirror as mirror_mod
        import pilots.scoring as scoring_mod

        monkeypatch.setattr(ks_mod.GlobalKillSwitch, "is_active", lambda self: False)
        monkeypatch.setattr(
            fs_mod.FollowsStore, "upsert", lambda self, pid, amt: {"pilot_id": pid, "amount": amt}
        )
        monkeypatch.setattr(scoring_mod, "load_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(hs_mod.HistoricalStore, "latest_account_snapshot", lambda self: None)
        monkeypatch.setattr(
            mirror_mod,
            "plan_follow",
            lambda pilot, amount, account_snapshot, snapshot=None: {
                "planned_intents": [],
                "mode": "off",
                "queue_written": False,
            },
        )

        result = srv.follow_pilot("trend-following", 500)

        assert isinstance(result, str)
        assert "no account snapshot" in result
        assert '"queue_written": false' in result


# ---------------------------------------------------------------------------
# _bearer_auth_asgi_middleware
# ---------------------------------------------------------------------------


class _RecordingLifespanApp:
    """Bare ASGI app that just records whether it was invoked with a
    lifespan scope, standing in for a full Starlette app in the one
    sub-case where building an entire app is unnecessary (see the task
    spec: "you may need a minimal fake inner app ... rather than a full
    Starlette app, for this specific sub-case")."""

    def __init__(self):
        self.called_with_lifespan = False

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            self.called_with_lifespan = True
        # Never actually await receive/send here -- the test only checks
        # that the middleware passed the lifespan scope straight through
        # without 401ing it, not that a full lifespan protocol completed.


class TestBearerAuthMiddleware:
    TOKEN = "s3cr3t-token"

    def _make_inner_app(self):
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        async def _ok(request):
            return PlainTextResponse("ok", status_code=200)

        return Starlette(routes=[Route("/", _ok)])

    def _make_client(self):
        from starlette.testclient import TestClient

        import investyo_mcp_server as srv

        inner = self._make_inner_app()
        wrapped = srv._bearer_auth_asgi_middleware(inner, self.TOKEN)
        return TestClient(wrapped)

    def test_missing_authorization_header_returns_401(self):
        client = self._make_client()
        resp = client.get("/")
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self):
        client = self._make_client()
        resp = client.get("/", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_correct_token_passes_through(self):
        client = self._make_client()
        resp = client.get("/", headers={"Authorization": f"Bearer {self.TOKEN}"})
        assert resp.status_code == 200
        assert resp.text == "ok"

    def test_lifespan_scope_passes_through_untouched(self):
        import asyncio

        import investyo_mcp_server as srv

        inner = _RecordingLifespanApp()
        wrapped = srv._bearer_auth_asgi_middleware(inner, self.TOKEN)

        scope = {"type": "lifespan"}

        async def _noop_receive():  # pragma: no cover - never actually called
            return {"type": "lifespan.startup"}

        async def _noop_send(message):  # pragma: no cover - never actually called
            pass

        asyncio.run(wrapped(scope, _noop_receive, _noop_send))

        assert inner.called_with_lifespan is True

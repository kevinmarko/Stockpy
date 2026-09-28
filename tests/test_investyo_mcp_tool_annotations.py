"""
tests/test_investyo_mcp_tool_annotations.py
====================================
Regression test for a small, deliberate follow-up to the ``investyo_mcp_server.py``
Pilot widget tools shipped in PR #631: the two read-only Pilot tools --
``list_pilots`` and ``get_pilot_detail`` -- now carry
``annotations=ToolAnnotations(readOnlyHint=True)`` on their ``@mcp.tool()``
decorators, so an MCP host's tool-selection reasoning can distinguish them
from tools with side effects.

Extended for the "PR A" Pilot marketplace tools: ``get_quote`` is read-only
analytics (readOnlyHint=True, same pattern as above). ``follow_pilot`` and
``unfollow_pilot`` used to write state and deliberately carried no
annotation; since Follow-a-Pilot was archived (2026-09, step 4c) they and
``get_follows``/``get_portfolio_by_pilot`` are retired stubs that do nothing
but return a notice, so they are now marked read-only.

Verified against the real installed SDK (``mcp==1.28.1``, pinned via
``mcp<2.0.0`` in requirements.txt) rather than assumed:
* ``mcp.types.ToolAnnotations`` is a pydantic model with fields ``title``,
  ``readOnlyHint``, ``destructiveHint``, ``idempotentHint``, ``openWorldHint``
  -- all ``Optional``, defaulting to ``None``.
* ``FastMCP.tool(...)`` accepts an ``annotations: ToolAnnotations | None``
  kwarg and stores it verbatim on the registered
  ``mcp.server.fastmcp.tools.base.Tool`` -- retrievable via
  ``FastMCP._tool_manager.get_tool(name).annotations`` (no MCP transport
  layer involved, matching this repo's existing
  ``tests/test_investyo_mcp_server.py``/``tests/test_investyo_mcp_widgets.py``
  convention of calling tools/inspecting server internals as plain Python,
  not over JSON-RPC).
* A tool registered with no ``annotations=`` kwarg (e.g.
  ``execute_paper_trade``) has ``Tool.annotations is None`` -- there is no default
  ``ToolAnnotations()`` instance with every hint ``None``, it's a bare
  ``None``.
"""

from __future__ import annotations

import investyo_mcp_server as srv
from mcp.types import ToolAnnotations


def _get_tool(name: str):
    tool = srv.mcp._tool_manager.get_tool(name)
    assert tool is not None, f"no tool registered under name {name!r}"
    return tool


class TestReadOnlyPilotToolAnnotations:
    def test_list_pilots_is_marked_read_only(self):
        tool = _get_tool("list_pilots")
        assert tool.annotations is not None
        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.readOnlyHint is True

    def test_get_pilot_detail_is_marked_read_only(self):
        tool = _get_tool("get_pilot_detail")
        assert tool.annotations is not None
        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.readOnlyHint is True

    def test_compare_pilots_is_marked_read_only(self):
        tool = _get_tool("compare_pilots")
        assert tool.annotations is not None
        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.readOnlyHint is True

    def test_other_write_tools_are_not_incidentally_marked_read_only(self):
        """Spot-check a couple of other clearly-not-read-only tools to
        make sure this change was scoped to exactly the two intended
        tools and didn't leak via some shared decorator/helper."""
        for name in ("execute_paper_trade", "update_watch_rules"):
            tool = _get_tool(name)
            assert tool.annotations is None or tool.annotations.readOnlyHint is not True

    def test_get_quote_is_marked_read_only(self):
        tool = _get_tool("get_quote")
        assert tool.annotations is not None
        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.readOnlyHint is True

    def test_get_robinhood_account_snapshot_is_marked_read_only(self):
        """get_robinhood_account_snapshot exposes the real Robinhood account
        (equity, buying power, positions) but strictly read-only -- it must
        carry readOnlyHint=True so an MCP host's tool-selection reasoning
        (and a human skimming the tool list before pointing a coding agent
        at this server) can see it never places, cancels, or exercises an
        order."""
        tool = _get_tool("get_robinhood_account_snapshot")
        assert tool.annotations is not None
        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.readOnlyHint is True

    def test_retired_follow_stubs_are_marked_read_only(self):
        """The Follow-a-Pilot tool names survive only as retired stubs
        (2026-09, step 4c) that persist nothing, so readOnlyHint=True is
        accurate for all four."""
        for name in ("follow_pilot", "unfollow_pilot", "get_follows", "get_portfolio_by_pilot"):
            tool = _get_tool(name)
            assert tool.annotations is not None
            assert tool.annotations.readOnlyHint is True

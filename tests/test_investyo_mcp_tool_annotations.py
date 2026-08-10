"""
tests/test_investyo_mcp_tool_annotations.py
====================================
Regression test for a small, deliberate follow-up to the ``investyo_mcp_server.py``
Pilot widget tools shipped in PR #631: the two read-only Pilot tools --
``list_pilots`` and ``get_pilot_detail`` -- now carry
``annotations=ToolAnnotations(readOnlyHint=True)`` on their ``@mcp.tool()``
decorators, so an MCP host's tool-selection reasoning can distinguish them
from tools with side effects. ``follow_pilot`` (persists a follow, builds an
order-queue preview) deliberately does NOT get this annotation and must never
be given it by a future edit.

Extended for the 3 new Pilot marketplace tools ("PR A"): ``get_quote`` and
``get_portfolio_by_pilot`` are read-only analytics (readOnlyHint=True, same
pattern as above); ``unfollow_pilot`` writes state (cancels a follow via
``FollowsStore.upsert(pilot_id, 0.0)``) and must never carry the annotation,
same as ``follow_pilot``.

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
* A tool registered with no ``annotations=`` kwarg (e.g. ``follow_pilot``)
  has ``Tool.annotations is None`` -- there is no default
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

    def test_follow_pilot_is_not_marked_read_only(self):
        """follow_pilot writes state (persists a follow, builds an
        order-queue preview) -- it must never carry readOnlyHint=True.
        The real installed SDK leaves ``Tool.annotations`` as a bare
        ``None`` (not a ``ToolAnnotations()`` with every field defaulted)
        when no ``annotations=`` kwarg was passed to ``@mcp.tool()``, so
        that is the exact condition asserted here rather than assuming a
        vacuous default instance."""
        tool = _get_tool("follow_pilot")
        assert tool.annotations is None or tool.annotations.readOnlyHint is not True

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

    def test_get_portfolio_by_pilot_is_marked_read_only(self):
        tool = _get_tool("get_portfolio_by_pilot")
        assert tool.annotations is not None
        assert isinstance(tool.annotations, ToolAnnotations)
        assert tool.annotations.readOnlyHint is True

    def test_unfollow_pilot_is_not_marked_read_only(self):
        """unfollow_pilot writes state (cancels a follow via
        FollowsStore.upsert(pilot_id, 0.0)) -- it must never carry
        readOnlyHint=True, same as follow_pilot."""
        tool = _get_tool("unfollow_pilot")
        assert tool.annotations is None or tool.annotations.readOnlyHint is not True

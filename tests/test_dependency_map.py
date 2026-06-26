"""tests/test_dependency_map.py — declarative dependency-graph helper.

Covers ``gui/dependency_map.py``. Three things matter here:

1.  Every registered :class:`DataSource` resolves to at least one Consumer
    (the graph isn't silently missing edges).
2.  ``impacted_consumers`` is deterministic and handles unknown sources
    without fabricating impact.
3.  ``render_edges`` is non-empty and symmetric with ``CONSUMERS``.
"""

from __future__ import annotations

import pytest

from gui.dependency_map import (
    CONSUMERS,
    Consumer,
    DataSource,
    all_consumers,
    impacted_consumers,
    render_edges,
)


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_every_real_source_has_consumers(self) -> None:
        """Every non-UNKNOWN source must have at least one Consumer."""
        for src in DataSource:
            if src is DataSource.UNKNOWN:
                continue
            assert CONSUMERS.get(src), (
                f"DataSource {src} has no registered consumers — "
                "extend gui/dependency_map.py CONSUMERS."
            )

    def test_all_consumers_dedup_by_name(self) -> None:
        names = [c.name for c in all_consumers()]
        assert len(names) == len(set(names))

    def test_consumer_kinds_are_restricted(self) -> None:
        """Consumer.kind values are part of the GUI contract."""
        allowed = {"strategy", "report", "tab", "engine"}
        for c in all_consumers():
            assert c.kind in allowed, f"Unexpected kind {c.kind!r} on {c.name!r}"


# ---------------------------------------------------------------------------
# impacted_consumers
# ---------------------------------------------------------------------------

class TestImpactedConsumers:
    def test_empty_input_empty_output(self) -> None:
        assert impacted_consumers([]) == []

    def test_known_source_returns_consumers(self) -> None:
        records = impacted_consumers([DataSource.FRED])
        assert len(records) == 1
        assert records[0].source is DataSource.FRED
        assert records[0].consumer_count > 0

    def test_accepts_string_input(self) -> None:
        records = impacted_consumers(["fred"])
        assert records and records[0].source is DataSource.FRED

    def test_unknown_string_maps_to_UNKNOWN_with_empty_impact(self) -> None:
        """We must NOT fabricate impact when we don't know what depends on a source."""
        records = impacted_consumers(["mystery_feed"])
        assert len(records) == 1
        assert records[0].source is DataSource.UNKNOWN
        assert records[0].consumer_count == 0

    def test_duplicates_collapsed(self) -> None:
        records = impacted_consumers([DataSource.FRED, "fred", DataSource.FRED])
        assert len(records) == 1


# ---------------------------------------------------------------------------
# render_edges
# ---------------------------------------------------------------------------

class TestRenderEdges:
    def test_non_empty(self) -> None:
        edges = render_edges()
        assert len(edges) > 0

    def test_edges_match_consumer_map(self) -> None:
        edges = render_edges()
        # Edge count must equal sum of (consumers per source) — symmetry guard.
        expected = sum(len(c) for c in CONSUMERS.values())
        assert len(edges) == expected

    def test_edge_tuple_shape(self) -> None:
        for src_label, consumer_name, kind in render_edges():
            assert isinstance(src_label, str) and src_label
            assert isinstance(consumer_name, str) and consumer_name
            assert kind in {"strategy", "report", "tab", "engine"}

"""
tests/test_snapshot_cache_freshness.py
======================================
Phase 3c — the state_snapshot.json loaders must key their Streamlit cache on the
file's mtime so a fresh orchestrator/advisory run is reflected on the next render
instead of after up to DASHBOARD_REFRESH_SECONDS (default 30 min) of staleness.

We don't depend on a Streamlit runtime (``@st.cache_data`` is a no-op without
one). Instead we replace the cached inner function with a spy and assert the
public loader computes + forwards the mtime as part of the cache key, and that a
changed file produces a changed key (→ cache miss → fresh read).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


class TestPanelsSnapshotFreshness:
    def test_loader_forwards_changing_mtime(self, monkeypatch, tmp_path) -> None:
        import gui.panels as panels

        monkeypatch.setattr(panels.settings, "OUTPUT_DIR", tmp_path, raising=False)
        snap = tmp_path / "state_snapshot.json"

        calls: list[tuple[str, float]] = []

        def _spy(path: str, mtime: float) -> dict:
            calls.append((path, mtime))
            p = Path(path)
            return json.loads(p.read_text()) if p.exists() else {}

        monkeypatch.setattr(panels, "_load_state_snapshot_cached", _spy)

        # 1. Missing file → mtime 0.0, empty dict.
        assert panels.load_state_snapshot() == {}
        assert calls[-1][1] == 0.0

        # 2. File written → positive mtime, fresh content read.
        snap.write_text(json.dumps({"market_regime": "RISK ON"}), encoding="utf-8")
        assert panels.load_state_snapshot() == {"market_regime": "RISK ON"}
        m1 = calls[-1][1]
        assert m1 > 0.0

        # 3. File modified → mtime in the cache key changes (would be a cache miss).
        os.utime(snap, (m1 + 10, m1 + 10))
        snap.write_text(json.dumps({"market_regime": "RECESSION"}), encoding="utf-8")
        os.utime(snap, (m1 + 10, m1 + 10))
        assert panels.load_state_snapshot() == {"market_regime": "RECESSION"}
        assert calls[-1][1] != m1

    def test_inner_cached_reads_by_path(self, monkeypatch, tmp_path) -> None:
        import gui.panels as panels

        snap = tmp_path / "state_snapshot.json"
        snap.write_text(json.dumps({"vix": 21.0}), encoding="utf-8")
        # The inner function reads strictly from its `path` arg (mtime is key-only).
        assert panels._load_state_snapshot_cached(str(snap), snap.stat().st_mtime) == {"vix": 21.0}
        assert panels._load_state_snapshot_cached(str(tmp_path / "nope.json"), 0.0) == {}

    def test_dashboard_uses_same_pattern(self) -> None:
        # Source-level guard: observability/dashboard.py applies the identical
        # mtime-keyed idiom (kept in sync with gui/panels.py).
        src = Path("observability/dashboard.py").read_text(encoding="utf-8")
        assert "_load_state_snapshot_cached" in src
        assert "st_mtime" in src

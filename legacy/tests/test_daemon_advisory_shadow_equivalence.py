"""
legacy/tests/test_daemon_advisory_shadow_equivalence.py -- step 5.2 gate (i)
============================================================================

Archived in shrink step 5.5 with ``main.py``. This is the one test from
``tests/test_daemon_advisory_shadow_equivalence.py`` that ran ``main.run_once()``
next to the daemon and required the daemon's shadow queue, recommendations and
``Advisory_*`` columns to equal main.py's byte for byte. The gate passed before
the step 5.3 cutover; the active file now pins the daemon against the committed
goldens directly (``TestDaemonShadowMatchesGolden``).

It keeps its original imports: restore ``main.py`` and
``tests/test_run_once_advisory_golden.py`` (both under ``legacy/``) to run it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from tests.test_daemon_advisory_shadow_equivalence import (
    _make_daemon_ctx,
    _run_advisory_and_queue,
    _run_daemon_fetch,
)
from tests.test_run_once_advisory_golden import (
    _BARS,
    _NOW,
    _QUEUE_GOLDEN,
    _REC_GOLDEN,
    _SOURCE_GOLDEN,
    _assert_recommendations_match,
    _install_frozen_inputs,
    _recommendation_bytes,
)


def _run_main(out: Path):
    """main.run_once() then _run_cycle's queue block, into the REAL OUTPUT_DIR."""
    import main
    from execution.compose import compose_and_emit, write_advisory_source

    try:
        result = main.run_once()
    finally:
        main._reset_macro_engine_cache()
    assert not result.errors, result.errors
    write_advisory_source(result.recommendations, output_dir=out, now=_NOW)
    assert compose_and_emit(result.snapshot, output_dir=out, now=_NOW,
                            macro_dto=result.macro_dto) is not None
    return result


def _real_queue_files(out: Path) -> Dict[str, bytes]:
    files = {}
    for rel in ("queue_sources/advisory.json", "execution_queue.json",
                "execution_queue_notified.json", "risk_gate_blocks.jsonl"):
        p = out / rel
        files[rel] = p.read_bytes() if p.exists() else b"<absent>"
    return files


class TestGateIDaemonShadowEqualsMainPy:
    def test_shadow_queue_and_recommendations_equal_main_run_once(self, tmp_path, monkeypatch):
        from settings import settings

        out = _install_frozen_inputs(tmp_path, monkeypatch)
        monkeypatch.setattr(settings, "ADVISORY_REUSE_PIPELINE_COMPUTE", False, raising=False)
        monkeypatch.setattr(settings, "DAEMON_AGENTIC_QUEUE_MODE", "shadow", raising=False)
        pushes: List[Any] = []
        monkeypatch.setattr("alerting.notify", lambda *a, **kw: pushes.append((a, kw)))

        main_result = _run_main(out)
        pushes_after_main = len(pushes)
        real_before = _real_queue_files(out)

        ctx = _make_daemon_ctx()
        _run_daemon_fetch(ctx, monkeypatch)
        # Same universe: the daemon's own AsyncDataFetchStep built it.
        assert ctx.symbols == [r.symbol for r in main_result.recommendations]
        assert ctx.snapshot is not None and ctx.snapshot.total_equity == 100_000.0
        # Stand-ins for RunPipelineStep's outputs (see the module docstring).
        ctx.macro_dto = main_result.macro_dto
        ctx.dashboard_df = pd.DataFrame({
            "Symbol": list(ctx.symbols),
            "Price": [float(_BARS[s]["Close"].iloc[-1]) for s in ctx.symbols],
        })
        _run_advisory_and_queue(ctx)

        assert ctx.context_extras.get("advisory_overlay_ok") is True
        assert not ctx.errors, ctx.errors
        # Per-symbol Recommendation fields (action, conviction, sizing, exit,
        # rationale, the full key_indicators dict). Both sides ran in this
        # process, so even the GJR-GARCH-derived keys must match exactly; the
        # tolerance only applies to the cross-platform committed golden.
        assert _recommendation_bytes(ctx.recommendations) == \
            _recommendation_bytes(main_result.recommendations)
        _assert_recommendations_match(
            _recommendation_bytes(ctx.recommendations), _REC_GOLDEN.read_bytes(),
        )
        # The shadow files equal main.py's real ones byte for byte...
        shadow = out / "shadow"
        assert (shadow / "queue_sources" / "advisory.json").read_bytes() == \
            (out / "queue_sources" / "advisory.json").read_bytes()
        assert (shadow / "execution_queue.json").read_bytes() == \
            (out / "execution_queue.json").read_bytes()
        # ...and the committed run_once golden.
        assert (shadow / "queue_sources" / "advisory.json").read_bytes() == _SOURCE_GOLDEN.read_bytes()
        assert (shadow / "execution_queue.json").read_bytes() == _QUEUE_GOLDEN.read_bytes()
        # The Advisory_* dashboard columns carry the same recommendations.
        by_symbol = {r.symbol: r for r in main_result.recommendations}
        for _, row in ctx.dashboard_df.iterrows():
            rec = by_symbol[row["Symbol"]]
            assert row["Advisory_Action"] == rec.action
            assert row["Advisory_Conviction"] == round(rec.conviction, 4)
            assert row["Advisory_Position_Pct"] == round(rec.suggested_position_pct, 6)
            assert row["Advisory_Rationale"] == rec.rationale

        # The shadow run touched none of the real queue files and sent no push.
        assert _real_queue_files(out) == real_before
        assert len(pushes) == pushes_after_main
        assert pushes_after_main >= 1, "main.py's compose should have pushed (fixture sanity)"
        assert not (shadow / "execution_queue_notified.json").exists()
        assert not (shadow / "risk_gate_blocks.jsonl").exists()

        # The gate-(iii) diff script, run on these files, sees no difference.
        from scripts import compare_shadow_queue as csq

        comparison = csq.compare(out)
        assert comparison.ok and comparison.shadow_origin.startswith("history/")
        assert comparison.n_differences == 0
        assert comparison.intents, "the comparison must cover real intents"

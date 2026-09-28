"""tests/test_execution_universe_boundary.py

Work Package A ("structural regression test") of
`.claude/decouple-explore-execute_implementation_plan.md`, built directly on
top of the live trace recorded in
`.claude/decouple-explore-execute_wave0_checklist.md`. Mechanically guards
the property that plan exists to confirm and freeze:

    A symbol reachable only through browsing (Symbol Screener / FMP search /
    Quick Trade) can never enter either orchestrator's autonomous per-cycle
    universe.

This file intentionally does **not** repeat the inclusion-side coverage
`tests/test_production_steps_universe.py` already owns (watchlist/discovery/
DEFAULT_TICKERS correctly unioning IN) -- per the Wave 0 checklist's §5,
that side was already tested and this file's job is the previously-untested
inverse: proving exclusion.

Two layers of coverage, matching the Wave 0 checklist's own findings:

1. ``TestNoScreenerImportInExecutionPath`` -- a static AST guard (mirroring
   ``tests/test_broker_fills_store.py``'s ``TestSizingIsolation`` import-
   boundary convention) proving there is no possible *code path* -- not
   merely "no call site found this session" -- from
   ``data/portfolio_sync.py`` or ``pipeline/production_steps.py`` into
   ``data/fmp_screener.py`` (the Symbol Screener's backend module) via an
   import.
2. ``TestComputeTrackedUniverseIsAPureFunctionOfItsNamedArgs`` -- a runtime
   check that ``data.portfolio_sync.compute_tracked_universe()`` -- the one
   function both orchestrators (``main.py::_build_universe()`` and
   ``pipeline/production_steps.py::AsyncDataFetchStep``) share for universe
   resolution, per Wave 0 §1 -- returns strictly a function of its four
   named arguments (``held``, ``watchlist``, ``discovered``,
   ``default_tickers``) and nothing else.

(Step 4b, options desk archive: a third layer used to live here,
``TestOptionsAutoScanDefaultScopeIsWatchlistOnly``, pinning the Wave 0
checklist's §3 finding that the fully-automated options auto-scan
(``execution/options_lifecycle.py`` + ``execution/options_paper_executor.py``,
both now archived to ``legacy/``) resolved its scan universe from raw
``settings.WATCHLIST`` alone. That whole feature -- and the auto-scan
universe-scoping property it guarded -- no longer exists in active code, so
this class was removed rather than moved: nothing at ``legacy/tests/``
exercises it either, since the archived module itself is what the property
was about. The equity-only universe-boundary coverage below is unaffected.)

See the bottom of this file for the mandatory break-then-revert proof
record (plan §5 / this repo's `test_measure_settings_census.py`
convention): a real, temporary violation was introduced into
`data/portfolio_sync.py`, this suite was re-run and observed to fail with a
clear message, and the violation was reverted -- not merely asserted in
prose.
"""

from __future__ import annotations

import ast
import pathlib

from data.portfolio_sync import compute_tracked_universe

# ---------------------------------------------------------------------------
# 1. Structural AST import-boundary guard
# ---------------------------------------------------------------------------

# The two modules that resolve the daemon's per-cycle equity universe per
# the Wave 0 checklist §1: compute_tracked_universe() and its one caller in
# pipeline/production_steps.py. (Step 4b, options desk archive: this used to
# also guard execution/options_paper_executor.py + execution/options_lifecycle.py
# for the options auto-scan's own universe scoping, per Wave 0 §3 -- both
# archived to legacy/; see the module docstring.)
_GUARDED_MODULES = [
    "data/portfolio_sync.py",
    "pipeline/production_steps.py",
]

_FORBIDDEN_MODULE = "data.fmp_screener"


def _imported_dotted_names(path: pathlib.Path) -> set:
    """Return every fully-qualified module path a file imports, via a static
    `ast.parse` -- e.g. `import data.fmp_screener` contributes
    `"data.fmp_screener"`; `from data.fmp_screener import search_symbols`
    contributes `"data.fmp_screener"`; `from data import fmp_screener`
    contributes `"data.fmp_screener"` too (reconstructed from the
    `from data import fmp_screener` shape), not just the bare `"data"` root
    -- unlike `tests/test_broker_fills_store.py`'s `_import_roots` helper
    (which intentionally only needs the top-level root for its own check),
    this guard needs the full dotted path since `data` itself is a
    legitimate, common import root across this codebase."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            # `from data import fmp_screener` -- module="data", and one of
            # the imported names is the submodule itself.
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
    return names


def _references_forbidden_module(path: pathlib.Path, forbidden: str) -> bool:
    imported = _imported_dotted_names(path)
    return any(name == forbidden or name.startswith(f"{forbidden}.") for name in imported)


class TestNoScreenerImportInExecutionPath:
    """No possible code path from the daemon's autonomous universe-builder
    or the options auto-scan can reach the FMP Symbol Screener's backend
    module via an import. This is deliberately a static, structural check
    (not "we grepped for a call site and found none this session") -- an
    import is the only way one of these modules *could* reach screener
    data, so ruling it out here rules out the whole class of regression."""

    def test_guarded_module_list_itself_is_accurate(self):
        # Guard against this test file silently testing nothing because a
        # path was renamed/moved out from under it.
        for relpath in _GUARDED_MODULES:
            assert pathlib.Path(relpath).exists(), f"expected guarded module to exist: {relpath}"
        assert pathlib.Path("data/fmp_screener.py").exists(), (
            "data/fmp_screener.py itself is missing -- this guard's premise "
            "(the Symbol Screener backend module) no longer holds; update "
            "this test rather than leaving it silently vacuous."
        )

    def test_portfolio_sync_never_imports_fmp_screener(self):
        path = pathlib.Path("data/portfolio_sync.py")
        assert not _references_forbidden_module(path, _FORBIDDEN_MODULE), (
            f"{path} imports from {_FORBIDDEN_MODULE} -- this would give "
            "compute_tracked_universe() (both orchestrators' shared "
            "universe resolver) a code path into Symbol Screener data."
        )

    def test_production_steps_never_imports_fmp_screener(self):
        path = pathlib.Path("pipeline/production_steps.py")
        assert not _references_forbidden_module(path, _FORBIDDEN_MODULE), (
            f"{path} imports from {_FORBIDDEN_MODULE} -- this would give "
            "the persistent daemon's per-cycle AsyncDataFetchStep a code "
            "path into Symbol Screener data."
        )

    def test_no_guarded_module_even_mentions_fmp_screener_textually(self):
        """Belt-and-suspenders: a plain substring scan, matching the house
        convention already established by
        `tests/test_broker_fills_store.py::TestSizingIsolation
        .test_no_sizing_or_execution_module_imports_broker_fills_store`.
        Catches an import written in a form the AST walk above wouldn't
        (e.g. a dynamic `importlib.import_module("data.fmp_screener")`
        string), at the cost of being purely textual."""
        offenders = []
        for relpath in _GUARDED_MODULES:
            src = pathlib.Path(relpath).read_text(encoding="utf-8")
            if "fmp_screener" in src:
                offenders.append(relpath)
        assert offenders == [], (
            f"fmp_screener referenced (textually) in: {offenders} -- "
            "even a non-import reference here is worth a human look."
        )


# ---------------------------------------------------------------------------
# 2. Runtime exclusion test: compute_tracked_universe() is a pure function
#    of its four named arguments.
# ---------------------------------------------------------------------------


class TestComputeTrackedUniverseIsAPureFunctionOfItsNamedArgs:
    """`data.portfolio_sync.compute_tracked_universe()` is the one function
    both `main.py::_build_universe()` and
    `pipeline/production_steps.py::AsyncDataFetchStep` share (Wave 0 §1).
    Its signature is exactly `held`/`watchlist`/`discovered`/
    `default_tickers`/`apply_rating_exclusion` -- proving a symbol absent
    from all four collections never appears in the result, regardless of
    what else is true in the wider system (an unrelated live setting, a
    file on disk, a screener result), is the exclusion-side counterpart to
    `tests/test_production_steps_universe.py`'s existing inclusion tests."""

    def test_symbol_absent_from_all_named_args_is_excluded(self):
        result = compute_tracked_universe(
            held=["AAA"],
            watchlist=["BBB"],
            discovered=["CCC"],
            default_tickers=["DDD"],
            apply_rating_exclusion=False,
        )
        assert "ZZZZ" not in result
        # DEFAULT_TICKERS is fallback-only (used only when the
        # held/watchlist/discovered union is empty) -- confirming this
        # alongside the exclusion check keeps this test honest about what
        # "union" actually means here, matching
        # tests/test_production_steps_universe.py's own documented
        # fallback-only semantics.
        assert set(result) == {"AAA", "BBB", "CCC"}
        assert "DDD" not in result

    def test_empty_universe_falls_back_to_default_tickers_only(self):
        result = compute_tracked_universe(
            held=[],
            watchlist=[],
            discovered=[],
            default_tickers=["DDD"],
            apply_rating_exclusion=False,
        )
        assert set(result) == {"DDD"}
        assert "ZZZZ" not in result

    def test_result_ignores_settings_watchlist_entirely(self, monkeypatch):
        """compute_tracked_universe() takes `watchlist` as an explicit
        argument -- it must never itself reach into `settings.WATCHLIST`
        (that env-var read is `load_env_watchlist()`'s job, called by each
        orchestrator BEFORE this function, not inside it). Proves the
        function's output cannot be perturbed by a global a caller forgot
        to pass through."""
        monkeypatch.setattr("settings.settings.WATCHLIST", "SNEAKY", raising=False)
        result = compute_tracked_universe(
            held=["AAA"],
            watchlist=[],
            discovered=[],
            default_tickers=[],
            apply_rating_exclusion=False,
        )
        assert result == ["AAA"]
        assert "SNEAKY" not in result

    def test_no_reference_to_screener_watchlist_file_or_request_state_in_signature(self):
        """Structural companion to the exclusion tests above: the function
        signature itself carries no Symbol-Screener/FMP-search/Quick-Trade/
        watchlist-file parameter for a symbol to sneak in through. If a
        future change added one, this test's own hardcoded expected
        parameter set would need an explicit update -- which is the point:
        it can't happen silently."""
        import inspect

        params = set(inspect.signature(compute_tracked_universe).parameters.keys())
        assert params == {
            "held",
            "watchlist",
            "discovered",
            "default_tickers",
            "apply_rating_exclusion",
        }


# ---------------------------------------------------------------------------
# Break-then-revert proof record (plan §5 / test_measure_settings_census.py
# convention) -- performed live this session, 2026-09-11, on branch
# `decouple-explore-execute`:
#
# 1. `data/portfolio_sync.py::compute_tracked_universe()` was temporarily
#    edited to add `combined |= {"INJECTED_LEAK_TICKER"}` immediately before
#    its final `return sorted(combined)`. Re-running this file
#    (`python3 -m pytest tests/test_execution_universe_boundary.py -v`) was
#    observed to FAIL
#    `TestComputeTrackedUniverseIsAPureFunctionOfItsNamedArgs
#    ::test_symbol_absent_from_all_named_args_is_excluded` (assertion on the
#    resulting set no longer matching `{"AAA", "BBB", "CCC"}`) and
#    `::test_empty_universe_falls_back_to_default_tickers_only` and
#    `::test_result_ignores_settings_watchlist_entirely`, with a clear
#    pytest AssertionError pointing at the mismatched set contents. The edit
#    was then reverted via `git checkout -- data/portfolio_sync.py`, and
#    `git diff data/portfolio_sync.py` was confirmed empty. Re-running this
#    file afterward passed cleanly again.
# 2. `execution/options_paper_executor.py::get_actionable_directives()`'s
#    raw-parse fallback line (`raw = getattr(settings, "WATCHLIST", "") or
#    ""`) was temporarily edited to
#    `raw = (getattr(settings, "WATCHLIST", "") or "") + ",LEAKEDSYM"`. Re-
#    running this file was observed to FAIL all three tests in
#    `TestOptionsAutoScanDefaultScopeIsWatchlistOnly` --
#    `::test_no_override_scans_exactly_the_parsed_watchlist` and
#    `::test_held_watchlist_file_and_screener_symbols_excluded_when_absent_from_watchlist`
#    (captured symbols included the injected `"LEAKEDSYM"`, which
#    `assert set(captured) == {"AAA", "BBB"}` does not permit) and, more than
#    expected going in, `::test_empty_watchlist_yields_empty_scan_not_a_broader_fallback`
#    too (`assert captured == []` failed with `['LEAKEDSYM']`, since the
#    injected leak fires even when `WATCHLIST` is empty) -- 3 failed, 10
#    passed, each with a clear pytest AssertionError showing the extra
#    symbol in the captured set. The edit was then reverted via
#    `git checkout -- execution/options_paper_executor.py`, and
#    `git diff execution/options_paper_executor.py` was confirmed empty.
#    Re-running this file afterward passed cleanly again (13 passed).
#    (Step 4b, options desk archive: `TestOptionsAutoScanDefaultScopeIsWatchlistOnly`
#    itself, and `execution/options_paper_executor.py`/
#    `execution/options_lifecycle.py`, were later removed/archived to
#    `legacy/` -- this item stays as the historical record of the proof
#    that was actually performed against the code as it existed then.)
#
# Both violations were introduced, observed to fail this suite with the
# real pytest output captured above, reverted, and re-confirmed passing --
# this proof was performed, not merely asserted.
# ---------------------------------------------------------------------------

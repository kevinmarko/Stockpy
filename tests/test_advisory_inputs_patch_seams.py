"""Patch-seam guards for the step 5.0 move of main.py's advisory input helpers.

``pipeline/advisory_inputs.py`` now holds the universe/macro/pre-compute
builders; ``main.py`` re-exports them under their old underscore names.
Two kinds of patch target exist after the move:

* ``run_once`` injection seams (``_build_universe``, ``_build_macro_dto``,
  ``_fetch_bars_for_universe``, ``_build_context_extras``, plus
  ``fetch_account_snapshot``/``get_provider``/``advisory_evaluate``).
  ``main.run_once()`` reads these from ``main``'s globals when it builds the
  ``RunContext``, so ``patch("main.<name>")`` still intercepts them.
* Calls made INSIDE ``pipeline/advisory_inputs.py`` (``build_universe`` ->
  ``discovery``/``load_watchlist``/``recently_closed_universe_symbols``,
  ``build_macro_dto`` -> ``get_macro_engine``, ``build_context_extras`` ->
  ``fetch_fundamentals_for_universe``/``build_realized_vol_60d_map``, and the
  ``WATCHLIST_FILE`` read). Patching the ``main`` re-export does NOT reach
  these -- the patch "succeeds" and silently does nothing, so a test that
  meant to isolate e.g. a real ``scan_candidates.json`` would quietly read
  the operator's real one. They must be patched on
  ``pipeline.advisory_inputs``.

The scan below fails CI if any test patches one of the second kind through
``main``.
"""
from __future__ import annotations

import re
from pathlib import Path

import main
import pipeline.advisory_inputs as ai

TESTS_DIR = Path(__file__).resolve().parent

# Old main-module name -> new pipeline.advisory_inputs name, for every helper
# whose only callers are inside pipeline/advisory_inputs.py.
_INTERNAL_ONLY = {
    "discovery": "discovery",
    "WATCHLIST_FILE": "WATCHLIST_FILE",
    "_load_watchlist": "load_watchlist",
    "_recently_closed_universe_symbols": "recently_closed_universe_symbols",
    "_get_macro_engine": "get_macro_engine",
    "_fetch_fundamentals_for_universe": "fetch_fundamentals_for_universe",
    "_build_realized_vol_60d_map": "build_realized_vol_60d_map",
}

_NAMES = "|".join(re.escape(n) for n in _INTERNAL_ONLY)
# patch("main.X") / monkeypatch.setattr("main.X", ...) / mock.patch("main.X")
_STRING_TARGET = re.compile(r"""["']main\.(%s)["']""" % _NAMES)
# monkeypatch.setattr(m, "X", ...) / patch.object(main, "X", ...), where the
# first argument is a name bound to the main module in that file.
_OBJECT_TARGET = re.compile(r"""(?:setattr|patch\.object)\(\s*(\w+)\s*,\s*["'](%s)["']""" % _NAMES)
_MAIN_ALIAS = re.compile(r"^\s*import main(?: as (\w+))?\s*$", re.MULTILINE)


def _main_aliases(text: str) -> set:
    return {m.group(1) or "main" for m in _MAIN_ALIAS.finditer(text)}


def test_no_test_patches_an_internal_helper_through_main():
    offenders = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        aliases = _main_aliases(text)
        for lineno, line in enumerate(text.splitlines(), 1):
            hit = _STRING_TARGET.search(line)
            if hit:
                offenders.append(f"{path.name}:{lineno}: main.{hit.group(1)}")
                continue
            obj = _OBJECT_TARGET.search(line)
            if obj and obj.group(1) in aliases:
                offenders.append(f"{path.name}:{lineno}: {obj.group(1)}.{obj.group(2)}")
    assert not offenders, (
        "These patches target main's re-export, which no longer reaches the "
        "call inside pipeline/advisory_inputs.py. Patch "
        "pipeline.advisory_inputs.<new name> instead:\n  " + "\n  ".join(offenders)
    )


def test_main_reexports_are_the_moved_objects():
    """Direct callers of the old names (e.g. main._build_universe(snap)) must
    get exactly the moved function, and the macro-engine cache must be one
    dict shared by both names."""
    pairs = {
        "_build_universe": ai.build_universe,
        "_build_macro_dto": ai.build_macro_dto,
        "_fetch_bars_for_universe": ai.fetch_bars_for_universe,
        "_build_context_extras": ai.build_context_extras,
        "_reset_macro_engine_cache": ai.reset_macro_engine_cache,
        "_MACRO_ENGINE_CACHE": ai._MACRO_ENGINE_CACHE,
        **{old: getattr(ai, new) for old, new in _INTERNAL_ONLY.items() if old != "discovery"},
    }
    for old, obj in pairs.items():
        assert getattr(main, old) is obj, old


def test_main_does_not_rebind_discovery():
    """main.py no longer imports discovery, so a stale patch("main.discovery")
    raises instead of silently patching a name nothing reads."""
    assert not hasattr(main, "discovery")

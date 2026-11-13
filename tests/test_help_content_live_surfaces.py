"""
tests/test_help_content_live_surfaces.py
========================================
Guards ``shared/help_content.py`` against documenting a DELETED surface as live.

The Python help catalog was written for the Streamlit "InvestYo Command Center"
(deleted 2026-09) and the ``main.py`` ``--interval`` / ``--agent`` loops
(archived to ``legacy/`` in step 5.5).  The Pilots PWA (``webapp/``) is the only
frontend, so help text must point at the web app screen that replaced a tab, or
say plainly that the surface was archived.

Rules enforced here (all offline, no network):

* No sentence in any help text (glossary, tab help, section help, metric help)
  may mention a deleted surface (Streamlit, the Command Center, the
  ``--agent`` / ``--interval`` loops, ``main.py``, a "<Name> tab" of the old
  app, ``gui/`` paths) unless that same sentence also says it is
  archived / deleted / retired / legacy / old / no longer there.
* Every route written as `` `/path` `` in a ``TAB_HELP`` description exists in
  ``webapp/src/App.tsx``.
* Every ``TAB_HELP`` description either names a web app route or says the
  surface was archived — a legacy tab id with no pointer is not help.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, List, Set, Tuple

import pytest

_REPO = Path(__file__).resolve().parent.parent
_APP_TSX = _REPO / "webapp" / "src" / "App.tsx"

# A mention of any of these describes the deleted app / archived loops.
_DELETED_SURFACE = re.compile(
    r"streamlit"
    r"|command[\s-]+center"
    r"|--agent\b"
    r"|--interval\b"
    r"|\bmain\.py\b"
    r"|\bgui/"
    r"|\b(?:launcher|reports|safety|options|strategy matrix|market data|paper monitor"
    r"|pairs|observability|analytics|validation lab|live inventory|ai insights"
    r"|ai control center|help|prompts|settings) tab\b",
    re.IGNORECASE,
)

# A sentence that also contains one of these says the surface is gone.
_SAYS_GONE = re.compile(
    r"archived|deleted|retired|legacy|\bold\b|no longer|formerly|\bwas\b|\bgone\b",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")


def _resolve(value) -> str:
    return value() if callable(value) else value


def _all_texts() -> Iterator[Tuple[str, str]]:
    """Yield ``(label, text)`` for every operator-facing string in the catalog."""
    from shared import help_content as hc

    for key, entry in hc.GLOSSARY.items():
        yield f"GLOSSARY[{key!r}]", f"{entry.term}. {entry.resolved_plain_english()}"
    for key, tab in hc.TAB_HELP.items():
        yield f"TAB_HELP[{key!r}]", f"{tab.title}. {tab.description}"
    for key, value in hc.SECTION_HELP.items():
        yield f"SECTION_HELP[{key!r}]", _resolve(value)
    for key, value in hc.METRIC_HELP.items():
        yield f"METRIC_HELP[{key!r}]", _resolve(value)


def _offending_sentences() -> List[str]:
    bad: List[str] = []
    for label, text in _all_texts():
        for sentence in _SENTENCE_SPLIT.split(text):
            match = _DELETED_SURFACE.search(sentence)
            if match and not _SAYS_GONE.search(sentence):
                bad.append(f"{label}: {match.group(0)!r} in: {sentence.strip()[:140]}")
    return bad


class TestNoDeletedSurfaceAsLive:
    def test_help_text_does_not_describe_deleted_surfaces_as_live(self) -> None:
        bad = _offending_sentences()
        assert not bad, (
            "Help text mentions a deleted surface (Streamlit Command Center, the "
            "--agent / --interval loops, main.py, an old '<Name> tab') without saying "
            "it is archived/deleted. Point at the web app screen instead:\n  "
            + "\n  ".join(bad)
        )

    def test_detector_flags_known_stale_phrases(self) -> None:
        """The detector itself must trip on the exact stale wording this guard
        was written for (so a regex edit cannot silently neuter it)."""
        stale = [
            "Section D starts and stops an `--interval` or `--agent` run you can stop at any time.",
            "Activate via the Launcher tab or `python -m execution.kill_switch --activate`.",
            "Found in the Reports tab.",
            "The Streamlit app shows a banner.",
            "Writes the flag via `gui/env_io.py`.",
        ]
        for sentence in stale:
            assert _DELETED_SURFACE.search(sentence), sentence
            assert not _SAYS_GONE.search(sentence), sentence

    def test_detector_accepts_explicitly_archived_wording(self) -> None:
        ok = (
            "A self-pacing loop that was archived to `legacy/` along with `main.py` (step 5.5)."
        )
        assert _DELETED_SURFACE.search(ok) and _SAYS_GONE.search(ok)

    def test_ai_control_center_has_no_run_loop_section(self) -> None:
        """The reported example: /settings/ai has no section that starts a
        recurring ``--interval`` / ``--agent`` run."""
        from shared.help_content import TAB_HELP

        text = TAB_HELP["ai_control_center"].description
        assert "Section D" not in text
        for sentence in _SENTENCE_SPLIT.split(text):
            assert not re.search(r"--agent|--interval", sentence)

    def test_pairs_tab_dropped(self) -> None:
        from shared.help_content import TAB_HELP

        assert "pairs" not in TAB_HELP


def _app_routes() -> Set[str]:
    src = _APP_TSX.read_text(encoding="utf-8")
    paths = re.findall(r'path="([^"]*)"', src)
    routes: Set[str] = set()
    for p in paths:
        if p == "*":
            continue
        if p.startswith("/"):
            routes.add(p)
        else:
            routes.add("/settings/" + p)  # the only nested <Route path="child"> block
    return routes


def _route_matches(token: str, routes: Set[str]) -> bool:
    if token.endswith("/*"):
        base = token[:-2]
        return base in routes or any(r.startswith(base + "/") for r in routes)
    if token in routes:
        return True
    # `/symbol/:ticker`-style tokens already match literally; also allow a
    # concrete value standing in for a ":param" segment.
    for r in routes:
        if ":" in r:
            pattern = "^" + re.sub(r":[^/]+", r"[^/]+", re.escape(r).replace(r"\:", ":")) + "$"
            if re.match(pattern, token):
                return True
    return False


_ROUTE_IN_BACKTICKS = re.compile(r"`(/[a-z][a-z0-9_/:*-]*)`")


@pytest.mark.skipif(not _APP_TSX.exists(), reason="webapp/src/App.tsx not present")
class TestTabHelpRoutes:
    def test_routes_named_in_tab_help_exist_in_app(self) -> None:
        from shared.help_content import TAB_HELP

        routes = _app_routes()
        assert "/pipeline" in routes and "/settings/ai" in routes  # parser sanity
        missing: List[str] = []
        for key, tab in TAB_HELP.items():
            for token in _ROUTE_IN_BACKTICKS.findall(tab.description):
                if not _route_matches(token, routes):
                    missing.append(f"TAB_HELP[{key!r}] names {token!r}")
        assert not missing, "Routes not in webapp/src/App.tsx:\n  " + "\n  ".join(missing)

    def test_every_tab_help_points_somewhere(self) -> None:
        from shared.help_content import TAB_HELP

        unhelpful: List[str] = []
        for key, tab in TAB_HELP.items():
            has_route = bool(_ROUTE_IN_BACKTICKS.search(tab.description))
            says_archived = bool(re.search(r"archived", tab.description, re.IGNORECASE))
            if not (has_route or says_archived):
                unhelpful.append(key)
        assert not unhelpful, (
            "TAB_HELP entries that neither name a web app route nor say the surface "
            f"was archived: {unhelpful}"
        )

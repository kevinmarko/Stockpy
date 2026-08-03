"""
tests/test_robinhood_login_worker.py
=====================================
Phrase-literal pinning test for data/robinhood_login_worker.py's _PHRASES
tuple.

That module scans robin_stocks' bare print() output for known phase phrases
(e.g. "Check robinhood app for device approvals") to drive the login UI's
progress phase -- see data/robinhood_login_worker.py's own module docstring
and its _PHRASES comment. These phrases are NOT part of any documented or
stable robin_stocks API; they are literal substrings of that library's own
internal print() calls. A routine robin_stocks version upgrade could
silently reword or remove one, which would leave the login UI's progress
phase stuck on whatever phase was last set (e.g. "authenticating") forever,
with no exception and no test failure to catch it -- the authoritative
success/failure signal comes from the login call's own control flow, not
these phrases, so nothing else would ever notice.

This test asserts every phrase in _PHRASES still appears verbatim in the
INSTALLED robin_stocks library's actual source, so that drift fails CI
loudly and by name instead of silently degrading the login UX.
"""

from __future__ import annotations

import inspect

import robin_stocks.robinhood.authentication as robin_stocks_authentication

from data.robinhood_login_worker import _PHRASES


def test_phrases_tuple_is_not_empty() -> None:
    """Sanity check on the fixture itself -- if _PHRASES were ever emptied
    out, every check below would trivially and silently pass."""
    assert len(_PHRASES) > 0


def test_every_phrase_appears_verbatim_in_installed_robin_stocks_source() -> None:
    """One aggregate assertion naming every phrase that failed to match, in
    case more than one has drifted at once."""
    source = inspect.getsource(robin_stocks_authentication)
    missing = [needle for needle, _phase in _PHRASES if needle not in source]
    assert not missing, (
        "The following phrase(s) scanned by data/robinhood_login_worker.py's "
        "_PHRASES tuple no longer appear verbatim in the installed "
        "robin_stocks.robinhood.authentication source -- a robin_stocks "
        "version upgrade likely reworded its internal print() output, which "
        "will silently leave the login UI's progress phase stuck forever. "
        f"Missing phrase(s): {missing!r}. Update _PHRASES in "
        "data/robinhood_login_worker.py to match the library's new wording."
    )


def test_each_phrase_individually_for_a_precise_failure_message() -> None:
    """Same check as above, parametrized-by-hand so a single drifted phrase
    reports its own dedicated failure naming that exact phrase (and the
    phase it drives), rather than being buried inside one aggregate
    assertion message."""
    source = inspect.getsource(robin_stocks_authentication)
    for needle, phase in _PHRASES:
        assert needle in source, (
            f"Phrase {needle!r} (mapped to phase {phase!r}) was not found "
            "verbatim in robin_stocks.robinhood.authentication's installed "
            "source -- see data/robinhood_login_worker.py's _PHRASES tuple "
            "and this test module's docstring for why this matters."
        )

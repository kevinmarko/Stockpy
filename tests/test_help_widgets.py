"""
tests/test_help_widgets.py
==========================
Offline unit tests for ``gui/help_widgets.py`` (§6 of the GUI Help
Explainers plan, Prompt 2 deliverable).

All Streamlit I/O is monkeypatched — no Streamlit runtime, no network.

Coverage
--------
* All 5 public functions are importable and callable.
* ``explain``: known tab → ``st.expander`` opened with ❓ title + description
  rendered; ``expanded=`` kwarg forwarded; unknown tab → no-op, no exception.
* ``metric_with_help``: known key → ``help=`` is a non-empty string; unknown
  key → ``help=None``; extra kwargs forwarded to ``st.metric``.
* ``help_expander``: non-empty body → expander + markdown; empty/None body
  → no-op.
* ``glossary_chip``: known term → popover (or caption fallback) rendered;
  unknown term → no-op, no exception.
* ``why_callout``: non-empty text → ``st.info`` called; empty/None → no-op.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

@contextmanager
def _noop_cm(*args: Any, **kwargs: Any):
    """No-op context manager used as a monkeypatch stand-in for st.expander
    and st.popover in tests that don't need to inspect their internals."""
    yield


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class TestImport:
    def test_module_importable(self) -> None:
        import gui.help_widgets  # noqa: F401

    def test_all_public_functions_callable(self) -> None:
        from gui.help_widgets import (
            explain,
            glossary_chip,
            help_expander,
            metric_with_help,
            why_callout,
        )
        for fn in (explain, help_expander, metric_with_help, glossary_chip, why_callout):
            assert callable(fn), f"{fn.__name__} is not callable"


# ---------------------------------------------------------------------------
# explain()
# ---------------------------------------------------------------------------


class TestExplain:
    def test_known_tab_opens_expander_with_icon(self, monkeypatch: pytest.MonkeyPatch) -> None:
        expander_titles: list[str] = []
        markdowns: list[str] = []

        @contextmanager
        def fake_expander(title: str, **kw: Any):
            expander_titles.append(title)
            yield

        monkeypatch.setattr("streamlit.expander", fake_expander)
        monkeypatch.setattr("streamlit.markdown", lambda t, **kw: markdowns.append(t))

        from gui.help_widgets import explain
        explain("launcher")

        assert len(expander_titles) == 1, "expander should be called once"
        assert "❓" in expander_titles[0], "expander title should contain the help icon"
        assert len(markdowns) == 1, "description should be rendered via st.markdown"

    def test_expanded_kwarg_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        expanded_values: list[bool] = []

        @contextmanager
        def fake_expander(title: str, expanded: bool = False, **kw: Any):
            expanded_values.append(expanded)
            yield

        monkeypatch.setattr("streamlit.expander", fake_expander)
        monkeypatch.setattr("streamlit.markdown", lambda t, **kw: None)

        from gui.help_widgets import explain
        explain("reports", expanded=True)

        assert expanded_values == [True]

    def test_unknown_tab_does_not_open_expander(self, monkeypatch: pytest.MonkeyPatch) -> None:
        expander_calls: list[bool] = []

        @contextmanager
        def fake_expander(*args: Any, **kw: Any):
            expander_calls.append(True)
            yield

        monkeypatch.setattr("streamlit.expander", fake_expander)

        from gui.help_widgets import explain
        explain("tab_id_that_does_not_exist_xyz")

        assert expander_calls == [], "expander must NOT be opened for an unknown tab"

    def test_unknown_tab_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("streamlit.expander", _noop_cm)

        from gui.help_widgets import explain
        explain("zzz_totally_unknown_tab")  # must not raise


# ---------------------------------------------------------------------------
# metric_with_help()
# ---------------------------------------------------------------------------


class TestMetricWithHelp:
    def test_known_key_provides_non_empty_help(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_metric(label: str, value: Any, **kw: Any) -> None:
            captured.update(kw)

        monkeypatch.setattr("streamlit.metric", fake_metric)

        from gui.help_widgets import metric_with_help
        metric_with_help("Kelly Target", 0.14, "Kelly Target")

        assert "help" in captured, "help= kwarg should be forwarded"
        assert captured["help"] is not None, "known key should produce a non-None help string"
        assert isinstance(captured["help"], str) and captured["help"].strip()

    def test_unknown_key_passes_none_help(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_metric(label: str, value: Any, **kw: Any) -> None:
            captured.update(kw)

        monkeypatch.setattr("streamlit.metric", fake_metric)

        from gui.help_widgets import metric_with_help
        metric_with_help("Mystery", 42, "zzz_no_such_metric_key")

        assert captured.get("help") is None, "unknown key should yield help=None"

    def test_extra_kwargs_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_metric(label: str, value: Any, **kw: Any) -> None:
            captured.update(kw)

        monkeypatch.setattr("streamlit.metric", fake_metric)

        from gui.help_widgets import metric_with_help
        metric_with_help("Label", 1, "zzz_unknown", delta=0.5)

        assert captured.get("delta") == 0.5, "extra kwargs must be forwarded to st.metric"


# ---------------------------------------------------------------------------
# help_expander()
# ---------------------------------------------------------------------------


class TestHelpExpander:
    def test_non_empty_body_renders_expander(self, monkeypatch: pytest.MonkeyPatch) -> None:
        titles: list[str] = []
        markdowns: list[str] = []

        @contextmanager
        def fake_expander(title: str, **kw: Any):
            titles.append(title)
            yield

        monkeypatch.setattr("streamlit.expander", fake_expander)
        monkeypatch.setattr("streamlit.markdown", lambda t, **kw: markdowns.append(t))

        from gui.help_widgets import help_expander
        help_expander("Calibration Help", "Here is what calibration means.")

        assert titles == ["Calibration Help"]
        assert "Here is what calibration means." in markdowns

    def test_empty_body_no_ops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        titles: list[str] = []

        @contextmanager
        def fake_expander(title: str, **kw: Any):
            titles.append(title)
            yield

        monkeypatch.setattr("streamlit.expander", fake_expander)

        from gui.help_widgets import help_expander
        help_expander("Title", "")

        assert titles == [], "empty body must not open expander"

    def test_none_body_no_ops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        titles: list[str] = []

        @contextmanager
        def fake_expander(title: str, **kw: Any):
            titles.append(title)
            yield

        monkeypatch.setattr("streamlit.expander", fake_expander)

        from gui.help_widgets import help_expander
        help_expander("Title", None)

        assert titles == [], "None body must not open expander"


# ---------------------------------------------------------------------------
# glossary_chip()
# ---------------------------------------------------------------------------


class TestGlossaryChip:
    def test_known_term_renders_via_popover(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captions: list[str] = []

        @contextmanager
        def fake_popover(label: str, **kw: Any):
            yield

        monkeypatch.setattr("streamlit.popover", fake_popover)
        monkeypatch.setattr("streamlit.caption", lambda t: captions.append(t))

        from gui.help_widgets import glossary_chip
        glossary_chip("VIX")

        assert len(captions) == 1, "plain_english should be rendered inside the popover"

    def test_unknown_term_does_not_render(self, monkeypatch: pytest.MonkeyPatch) -> None:
        popovers: list[bool] = []

        @contextmanager
        def fake_popover(*args: Any, **kw: Any):
            popovers.append(True)
            yield

        monkeypatch.setattr("streamlit.popover", fake_popover)

        from gui.help_widgets import glossary_chip
        glossary_chip("zzz_nonexistent_term_xyz")

        assert popovers == [], "popover must NOT be opened for an unknown term"

    def test_unknown_term_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("streamlit.popover", _noop_cm)
        monkeypatch.setattr("streamlit.caption", lambda t: None)

        from gui.help_widgets import glossary_chip
        glossary_chip("totally_made_up_no_match_term")  # must not raise


# ---------------------------------------------------------------------------
# why_callout()
# ---------------------------------------------------------------------------


class TestWhyCallout:
    def test_non_empty_text_renders_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        infos: list[str] = []
        monkeypatch.setattr("streamlit.info", lambda t: infos.append(t))

        from gui.help_widgets import why_callout
        why_callout("The VIX spiked above 30 — macro soft gate applied.")

        assert len(infos) == 1
        assert "VIX" in infos[0]

    def test_empty_string_no_ops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        infos: list[str] = []
        monkeypatch.setattr("streamlit.info", lambda t: infos.append(t))

        from gui.help_widgets import why_callout
        why_callout("")

        assert infos == [], "empty string must not call st.info"

    def test_none_no_ops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        infos: list[str] = []
        monkeypatch.setattr("streamlit.info", lambda t: infos.append(t))

        from gui.help_widgets import why_callout
        why_callout(None)

        assert infos == [], "None must not call st.info"

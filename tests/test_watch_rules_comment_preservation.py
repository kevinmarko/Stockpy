"""
Regression tests: ``watch_rules.yaml`` comment preservation across a rule write.
===============================================================================

``watch_rules.yaml`` is 90 lines, **76 of them documentation** — the rule schema,
the edge-trigger semantics, the ntfy setup steps, and worked examples. It is the
only place any of that is written down.

`investyo_mcp_server.py::update_watch_rules` did
``yaml.safe_load -> mutate -> yaml.safe_dump``. PyYAML drops comments, so a
single add/update/remove call collapsed the file from 4246 bytes to 206 — a total
documentation wipe, silently, with no error.

Same bug class as `tests/test_registry_yaml_comment_preservation.py` covers for
``ml/registry.yaml``; this file pins the equivalent invariant here. Comments in
this file are NOT confined to the header (they sit between rules and after the
last one), so header-only preservation is not sufficient and is not what is
tested for.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_WATCH_RULES = REPO_ROOT / "watch_rules.yaml"


def _comment_lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.lstrip().startswith("#")]


@pytest.fixture()
def watch_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A byte-for-byte copy of the real file, with CWD pointed at it.

    ``update_watch_rules`` resolves ``watch_rules.yaml`` relative to the process
    CWD, so chdir is how the tool is redirected at the copy.
    """
    shutil.copyfile(REPO_WATCH_RULES, tmp_path / "watch_rules.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path / "watch_rules.yaml"


def _update(**kwargs):
    from investyo_mcp_server import update_watch_rules

    fn = getattr(update_watch_rules, "fn", update_watch_rules)
    return fn(**kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# The core invariant — one case per action the tool supports
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"symbol": "ZZZZ", "action": "add", "alert_on": "action_change"}, id="add"),
        pytest.param(
            {"symbol": "ZZZZ", "action": "update", "alert_on": "conviction_above", "threshold": 0.8},
            id="update-new-symbol",
        ),
        pytest.param({"symbol": "*", "action": "remove"}, id="remove"),
    ],
)
def test_rule_write_preserves_every_comment_line(watch_rules: Path, kwargs):
    before = watch_rules.read_text(encoding="utf-8")
    before_comments = _comment_lines(before)
    assert len(before_comments) > 50, "fixture precondition: this file is mostly documentation"

    result = _update(**kwargs)
    assert "Successfully" in result, result

    after_comments = _comment_lines(watch_rules.read_text(encoding="utf-8"))
    missing = [c for c in before_comments if c not in after_comments]
    assert not missing, (
        f"{len(missing)} of {len(before_comments)} comment line(s) were silently "
        f"dropped by a '{kwargs['action']}' write. First few: {missing[:5]}"
    )


def test_rule_write_does_not_collapse_the_file(watch_rules: Path):
    """The measured symptom: 4246 bytes -> 206 on a single call."""
    before = len(watch_rules.read_text(encoding="utf-8"))
    _update(symbol="ZZZZ", action="add", alert_on="action_change")
    after = len(watch_rules.read_text(encoding="utf-8"))
    assert after > before * 0.9, f"file collapsed from {before} to {after} bytes"


def test_comments_between_and_after_rules_survive(watch_rules: Path):
    """Header-only preservation is NOT sufficient for this file."""
    text = watch_rules.read_text(encoding="utf-8")
    body_comments = [
        ln.rstrip()
        for ln in text.split("rules:", 1)[1].splitlines()
        if ln.lstrip().startswith("#")
    ]
    assert body_comments, "fixture precondition: comments exist below `rules:`"

    _update(symbol="ZZZZ", action="add", alert_on="action_change")

    after = watch_rules.read_text(encoding="utf-8")
    missing = [c for c in body_comments if c not in after]
    assert not missing, f"comments below `rules:` were dropped: {missing[:5]}"


# ──────────────────────────────────────────────────────────────────────────────
# The write must still actually do its job
# ──────────────────────────────────────────────────────────────────────────────

def test_add_actually_adds_the_rule(watch_rules: Path):
    _update(symbol="zzzz", action="add", alert_on="conviction_above",
            threshold=0.75, priority="high", label="My Rule")
    rules = yaml.safe_load(watch_rules.read_text(encoding="utf-8"))["rules"]
    added = [r for r in rules if r.get("symbol") == "ZZZZ"]
    assert len(added) == 1
    assert added[0] == {
        "symbol": "ZZZZ", "alert_on": "conviction_above",
        "threshold": 0.75, "priority": "high", "label": "My Rule",
    }


def test_remove_actually_removes_the_rule(watch_rules: Path):
    before = yaml.safe_load(watch_rules.read_text(encoding="utf-8"))["rules"]
    target = before[0]["symbol"]
    _update(symbol=target, action="remove")
    after = yaml.safe_load(watch_rules.read_text(encoding="utf-8"))["rules"]
    assert not [r for r in after if str(r.get("symbol")).upper() == str(target).upper()]
    assert len(after) < len(before)


def test_update_replaces_rather_than_duplicates(watch_rules: Path):
    _update(symbol="ZZZZ", action="add", alert_on="action_change")
    _update(symbol="ZZZZ", action="update", alert_on="conviction_below", threshold=0.2)
    rules = yaml.safe_load(watch_rules.read_text(encoding="utf-8"))["rules"]
    matches = [r for r in rules if r.get("symbol") == "ZZZZ"]
    assert len(matches) == 1
    assert matches[0]["alert_on"] == "conviction_below"


def test_unremovable_symbol_reports_honestly_and_writes_nothing(watch_rules: Path):
    before = watch_rules.read_text(encoding="utf-8")
    result = _update(symbol="NOSUCHSYMBOL", action="remove")
    assert "No watch rules found" in result
    assert watch_rules.read_text(encoding="utf-8") == before

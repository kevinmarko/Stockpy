"""The test suite must never resolve the operator's live OUTPUT_DIR.

The root conftest sets OUTPUT_DIR to a temp dir before any platform import
(docs/known_issues/output_dir_test_contamination_2026_10.md).
"""
from pathlib import Path

from settings import settings


def test_output_dir_is_not_the_live_default():
    live = (Path(settings.LOCAL_DATA_ROOT) / "output").resolve()
    assert Path(settings.OUTPUT_DIR).resolve() != live


def test_default_advisory_source_write_lands_outside_live_output():
    """The exact write that emptied the live advisory.json on 2026-10-05."""
    from execution.compose import write_advisory_source

    path = write_advisory_source([])
    assert path is not None
    live = (Path(settings.LOCAL_DATA_ROOT) / "output").resolve()
    assert live not in Path(path).resolve().parents

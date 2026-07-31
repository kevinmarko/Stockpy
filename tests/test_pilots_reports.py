"""Tests for pilots/reports.py (the manifest + content reader) and
GET /reports, GET /reports/{name}.

Mirrors the honesty posture of every other pilots/*.py reader: a missing
directory/file degrades to an empty manifest / ``None`` lookup, never an
exception, never a fabricated entry. The security-critical property
(``get_report_content`` resolves ``name`` ONLY against its own catalog, never
by joining the raw string onto a path) is tested directly at the reader level
so it holds independent of Starlette's own path-segment routing behavior.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from settings import settings
from pilots import reports as reports_reader
import api.pilots_api as pilots_api

# Starlette's TestClient defaults request.client.host to the literal string
# "testclient" -- NOT loopback -- which would trip api.auth.require_read_token's
# fail-closed-when-non-loopback branch. An explicit loopback host is what every
# other test file in this suite already uses.
client = TestClient(pilots_api.app, client=("127.0.0.1", 54123))


# --------------------------------------------------------------------------- #
# list_reports / get_report_content (reader-level)
# --------------------------------------------------------------------------- #
def _write_output(output_dir: Path) -> None:
    (output_dir / "daily_report.html").write_text("<html>daily</html>", encoding="utf-8")
    (output_dir / "daily_report_dashboard.html").write_text("<html>dash</html>", encoding="utf-8")
    (output_dir / "volatility_bands_dashboard.html").write_text("<html>vol</html>", encoding="utf-8")
    (output_dir / "briefing_2026-07-30.md").write_text("# Briefing 30", encoding="utf-8")
    (output_dir / "briefing_2026-07-29.md").write_text("# Briefing 29", encoding="utf-8")


def test_list_reports_finds_every_kind(tmp_path: Path):
    _write_output(tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "trend_validation_summary.json").write_text(
        json.dumps({"strategy_id": "trend", "deployable": True}), encoding="utf-8"
    )
    (reports_dir / "validation_trend_20260730.html").write_text("<html>v</html>", encoding="utf-8")

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        out = reports_reader.list_reports(reports_dir=str(reports_dir))

    assert out["reason"] is None
    kinds = {r["kind"] for r in out["reports"]}
    assert kinds == {"daily_report", "dashboard", "briefing", "validation_summary", "validation_html"}
    names = {r["name"] for r in out["reports"]}
    assert "briefing_2026-07-30.md" in names
    for row in out["reports"]:
        assert row["size"] is not None
        assert row["mtime"] is not None


def test_list_reports_briefings_are_newest_first(tmp_path: Path):
    _write_output(tmp_path)
    import os
    import time

    older = tmp_path / "briefing_2026-07-29.md"
    newer = tmp_path / "briefing_2026-07-30.md"
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))

    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        out = reports_reader.list_reports(reports_dir=str(tmp_path / "reports"))

    briefings = [r["name"] for r in out["reports"] if r["kind"] == "briefing"]
    assert briefings == ["briefing_2026-07-30.md", "briefing_2026-07-29.md"]


def test_list_reports_empty_universe_is_honest_not_fabricated(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        out = reports_reader.list_reports(reports_dir=str(tmp_path / "does_not_exist"))
    assert out["reports"] == []
    assert out["reason"]


def test_get_report_content_briefing_is_markdown_text(tmp_path: Path):
    _write_output(tmp_path)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        result = reports_reader.get_report_content(
            "briefing_2026-07-30.md", reports_dir=str(tmp_path / "reports")
        )
    assert result is not None
    assert result["content_type"] == "markdown"
    assert result["text"] == "# Briefing 30"
    assert result["json"] is None
    assert result["reason"] is None


def test_get_report_content_daily_report_is_html_text(tmp_path: Path):
    _write_output(tmp_path)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        result = reports_reader.get_report_content("daily_report.html")
    assert result is not None
    assert result["content_type"] == "html"
    assert result["text"] == "<html>daily</html>"


def test_get_report_content_validation_summary_is_parsed_json(tmp_path: Path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "trend_validation_summary.json").write_text(
        json.dumps({"strategy_id": "trend", "pbo": 0.2, "deployable": True}), encoding="utf-8"
    )
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        result = reports_reader.get_report_content(
            "trend_validation_summary.json", reports_dir=str(reports_dir)
        )
    assert result is not None
    assert result["content_type"] == "json"
    assert result["json"] == {"strategy_id": "trend", "pbo": 0.2, "deployable": True}
    assert result["text"] is None


def test_get_report_content_literal_nan_in_summary_is_nulled_not_reserialized(tmp_path: Path):
    # json.loads accepts a bare NaN/Infinity token as a Python extension; this
    # reader must never hand that back out as an invalid-JSON literal
    # (CONSTRAINT #4, mirrors the bug class fixed in pilots/validation_trend.py
    # and pilots/live_inventory.py).
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "nanstrat_validation_summary.json").write_text(
        '{"strategy_id": "nanstrat", "pbo": NaN, "sharpe": Infinity, '
        '"nested": {"max_drawdown": -Infinity, "ok": [1, NaN, 3]}}',
        encoding="utf-8",
    )
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        result = reports_reader.get_report_content(
            "nanstrat_validation_summary.json", reports_dir=str(reports_dir)
        )
    assert result is not None
    assert result["json"]["pbo"] is None
    assert result["json"]["sharpe"] is None
    assert result["json"]["nested"]["max_drawdown"] is None
    assert result["json"]["nested"]["ok"] == [1, None, 3]
    assert json.dumps(result["json"])  # round-trips as valid JSON


def test_get_report_content_corrupt_json_degrades_never_raises(tmp_path: Path):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "bad_validation_summary.json").write_text("{not valid json,,,", encoding="utf-8")
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        result = reports_reader.get_report_content(
            "bad_validation_summary.json", reports_dir=str(reports_dir)
        )
    assert result is not None
    assert result["json"] is None
    assert result["reason"]


# --------------------------------------------------------------------------- #
# Security: name is resolved ONLY against the catalog, never path-joined.
# --------------------------------------------------------------------------- #
def test_get_report_content_unknown_name_returns_none(tmp_path: Path):
    _write_output(tmp_path)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        assert reports_reader.get_report_content("nonexistent.html") is None


def test_get_report_content_rejects_path_traversal_attempts(tmp_path: Path):
    _write_output(tmp_path)
    # A real secret file that exists OUTSIDE any directory this module ever
    # globs -- proves a traversal string can't reach it via string-building.
    secret_dir = tmp_path.parent / f"secret-sibling-{tmp_path.name}"
    secret_dir.mkdir()
    (secret_dir / "settings.py").write_text("SECRET = 'do-not-leak'", encoding="utf-8")
    try:
        with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
            for traversal in (
                "../../../etc/passwd",
                f"../{secret_dir.name}/settings.py",
                "..",
                "...",
                "/etc/passwd",
                "daily_report.html/../../../etc/passwd",
            ):
                assert reports_reader.get_report_content(traversal) is None, traversal
    finally:
        (secret_dir / "settings.py").unlink()
        secret_dir.rmdir()


# --------------------------------------------------------------------------- #
# GET /reports
# --------------------------------------------------------------------------- #
def test_reports_endpoint_shape(tmp_path: Path):
    _write_output(tmp_path)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        with mock.patch.object(pilots_api, "_reports_dir", lambda: str(tmp_path / "reports")):
            resp = client.get("/reports")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason"] is None
    names = {r["name"] for r in body["reports"]}
    assert "daily_report.html" in names


def test_reports_endpoint_cold_start_is_honest_not_500(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        with mock.patch.object(pilots_api, "_reports_dir", lambda: str(tmp_path / "does_not_exist")):
            resp = client.get("/reports")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reports"] == []
    assert body["reason"]


def test_reports_endpoint_fail_open_no_token(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/reports")
    assert resp.status_code == 200


def test_reports_endpoint_401_on_wrong_token(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        resp = client.get("/reports", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# GET /reports/{name}
# --------------------------------------------------------------------------- #
def test_report_content_endpoint_returns_briefing_text(tmp_path: Path):
    _write_output(tmp_path)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/reports/briefing_2026-07-30.md")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_type"] == "markdown"
    assert body["text"] == "# Briefing 30"


def test_report_content_endpoint_unknown_name_is_404(tmp_path: Path):
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        resp = client.get("/reports/nonexistent.html")
    assert resp.status_code == 404


def test_report_content_endpoint_path_traversal_is_404_never_a_read(tmp_path: Path):
    """Explicit path-traversal-rejection test at the HTTP layer. Starlette's
    own routing collapses a %2F-containing name before it ever reaches the
    handler (also asserted here), and the reader-level test above proves the
    same property holds for any single-path-segment traversal string too."""
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path):
        for traversal in ("..%2F..%2Fsettings.py", "..%2Fsettings.py", "%2e%2e", ".."):
            resp = client.get(f"/reports/{traversal}")
            assert resp.status_code == 404, traversal
            assert "SECRET" not in resp.text


def test_report_content_endpoint_fail_open_no_token(tmp_path: Path):
    _write_output(tmp_path)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", None):
        resp = client.get("/reports/daily_report.html")
    assert resp.status_code == 200


def test_report_content_endpoint_401_on_wrong_token(tmp_path: Path):
    _write_output(tmp_path)
    with mock.patch.object(settings, "OUTPUT_DIR", tmp_path), mock.patch.object(settings, "STATE_API_TOKEN", "read-tok"):
        resp = client.get("/reports/daily_report.html", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401

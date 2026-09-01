"""pilots/reports.py — file-backed manifest + content reader for generated
reports, backing ``GET /reports`` / ``GET /reports/{name}``.

Ports ``gui/panels/reports_library.py``'s enumeration logic (the Streamlit
Report Library tab) into a dependency-light read: the daily report
(``output/daily_report.html``), the two orchestrator dashboards
(``output/daily_report_dashboard.html`` / ``output/volatility_bands_dashboard.html``),
daily briefings (``output/briefing_*.md``), the NotebookLM export
(``output/notebooklm_source.md``, see ``scripts/export_notebooklm.py``), and
validation reports (``<reports_dir>/*_validation_summary.json`` /
``<reports_dir>/validation_*.html``).

Security — the whole point of this module's shape
---------------------------------------------------
``get_report_content()`` resolves the caller-supplied ``name`` ONLY by exact
match against ``_catalog()``, the list of real files THIS module itself
found by globbing the two known report directories. It never joins a
client-supplied string onto a filesystem path. Mirrors
``pilots.commands.resolve_command``'s identical discipline for
``POST /jobs``' command execution: a name that isn't in the manifest
(including any ``../`` traversal attempt, which can never match a real
globbed basename) resolves to ``None`` rather than attempting a read.

Dependency-light (stdlib + ``settings`` only) — pinned by
``tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light``.

Honesty (CONSTRAINT #4/#6): a missing directory/file degrades to an empty
manifest / a ``None`` lookup, never an exception, never a fabricated entry.
A validation-summary JSON file is written with plain ``json.dumps`` (see
``ValidationReport.to_summary_dict()``), so in principle it could carry a
literal ``NaN``/``Infinity`` token — the same class of bug fixed in
``pilots/validation_trend.py`` — which would otherwise re-serialize as
invalid JSON; ``_sanitize_json`` recursively nulls any non-finite float
before this module ever returns it.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from settings import settings

logger = logging.getLogger(__name__)

__all__ = ["list_reports", "get_report_content"]

_NO_REPORTS_REASON = (
    "No reports generated yet — run the pipeline (daily report / dashboards), "
    "generate a daily briefing, or run the validation harness."
)

# kind -> content_type for every text-rendered kind. "validation_summary" is
# handled separately below (parsed JSON, not raw text).
_TEXT_CONTENT_TYPES: Dict[str, str] = {
    "daily_report": "html",
    "dashboard": "html",
    "briefing": "markdown",
    "notebooklm_export": "markdown",
    "validation_html": "html",
}


def _list_glob(directory: Path, pattern: str) -> List[Path]:
    """Files in ``directory`` matching glob ``pattern``, newest-first by
    mtime. ``[]`` on a missing directory or any error — never raises
    (CONSTRAINT #6)."""
    try:
        if not directory.exists() or not directory.is_dir():
            return []
        files = [p for p in directory.glob(pattern) if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files
    except Exception as exc:  # noqa: BLE001 — dead-letter, never raise
        logger.debug("pilots.reports: glob(%s, %s) failed: %s", directory, pattern, exc)
        return []


def _catalog(reports_dir: Optional[str] = None) -> List[Tuple[str, str, Path]]:
    """``(name, kind, absolute_path)`` for every report file currently on
    disk. THE SOLE place a filesystem path is resolved in this module —
    ``get_report_content`` looks ``name`` up against this list and never
    builds a path from client input.

    ``reports_dir`` mirrors ``api/pilots_api.py``'s own ``_reports_dir()`` —
    ``None`` means "use the real ``reports/`` directory"; tests pass an
    explicit override (matching ``pilots.performance``'s identical
    convention). The daily report / dashboards / briefings always resolve
    off ``settings.OUTPUT_DIR`` directly, matching every other reader in
    this package.
    """
    output_dir = settings.OUTPUT_DIR
    entries: List[Tuple[str, str, Path]] = []

    daily = output_dir / "daily_report.html"
    if daily.is_file():
        entries.append((daily.name, "daily_report", daily))

    for dash_name in ("daily_report_dashboard.html", "volatility_bands_dashboard.html"):
        p = output_dir / dash_name
        if p.is_file():
            entries.append((p.name, "dashboard", p))

    for p in _list_glob(output_dir, "briefing_*.md"):
        entries.append((p.name, "briefing", p))

    notebooklm = output_dir / "notebooklm_source.md"
    if notebooklm.is_file():
        entries.append((notebooklm.name, "notebooklm_export", notebooklm))

    reports_root = Path(reports_dir) if reports_dir is not None else Path("reports")
    for p in _list_glob(reports_root, "*_validation_summary.json"):
        entries.append((p.name, "validation_summary", p))
    for p in _list_glob(reports_root, "validation_*.html"):
        entries.append((p.name, "validation_html", p))

    return entries


def _stat(path: Path) -> Tuple[Optional[int], Optional[str]]:
    """``(size, mtime_iso)`` — ``(None, None)`` on any stat failure (a file
    that existed at glob time but vanished/became unreadable by read time;
    CONSTRAINT #6)."""
    try:
        st = path.stat()
        return st.st_size, datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except OSError as exc:
        logger.debug("pilots.reports: stat failed for %s: %s", path, exc)
        return None, None


def _sanitize_json(obj: Any) -> Any:
    """Recursively null any non-finite float — a validation-summary JSON
    file is written with plain ``json.dumps`` and could in principle carry a
    literal ``NaN``/``Infinity`` token that would otherwise re-serialize as
    invalid JSON (CONSTRAINT #4, mirrors ``pilots/validation_trend.py``'s
    ``_clean_float``, generalized to an arbitrary nested shape since this
    module passes the summary through as-is rather than a fixed schema)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    return obj


def list_reports(reports_dir: Optional[str] = None) -> Dict[str, Any]:
    """``GET /reports`` manifest: ``name``/``kind``/``size``/``mtime`` (ISO
    8601 UTC) per file, in catalog order (daily report, dashboards,
    briefings newest-first, the NotebookLM export, validation summaries,
    validation HTML reports). Never raises; an empty universe degrades to
    ``reports: []`` plus an honest ``reason`` (CONSTRAINT #6)."""
    rows: List[Dict[str, Any]] = []
    for name, kind, path in _catalog(reports_dir):
        size, mtime = _stat(path)
        rows.append({"name": name, "kind": kind, "size": size, "mtime": mtime})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reports": rows,
        "reason": None if rows else _NO_REPORTS_REASON,
    }


def get_report_content(name: str, reports_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Content for one report, resolved ONLY against ``_catalog()`` (see
    module docstring) — ``name`` is never joined onto a path. Returns
    ``None`` when ``name`` matches no catalog entry (the caller — ``GET
    /reports/{name}`` — turns that into an honest 404, never an attempted
    read of an unresolved path).

    A matched entry that fails to read at content time (a race, a
    permissions change, corrupt JSON) still returns a dict with
    ``text``/``json`` left ``None`` and ``reason`` set — CONSTRAINT #6,
    never a 500."""
    match = next((e for e in _catalog(reports_dir) if e[0] == name), None)
    if match is None:
        return None
    _, kind, path = match
    size, mtime = _stat(path)

    result: Dict[str, Any] = {
        "name": name,
        "kind": kind,
        "content_type": "json" if kind == "validation_summary" else _TEXT_CONTENT_TYPES.get(kind, "text"),
        "text": None,
        "json": None,
        "size": size,
        "mtime": mtime,
        "reason": None,
    }

    if kind == "validation_summary":
        try:
            result["json"] = _sanitize_json(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001 — dead-letter, never raise
            logger.warning("pilots.reports: could not parse %s: %s", path, exc)
            result["reason"] = f"Could not parse {name}."
        return result

    try:
        result["text"] = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("pilots.reports: could not read %s: %s", path, exc)
        result["reason"] = f"Could not read {name}."
    return result

"""Agents 1-2 (Research Scout, Strategy Analyst) artifacts for the Robinhood queue: research.json and theses.json schemas plus their validators. The thesis validator enforces the Analyst's honesty rule in code: every number in a thesis must cite a field in the queue or a saved raw MCP response, and must equal it."""

# =============================================================================
# MODULE: AGENTIC RESEARCH  (four-agent Robinhood plan, Phase 3)
# File: execution/agentic_research.py
#
# Plan: .claude/robinhood_four_agent_phase3_implementation_plan.md
#
# The Research Scout writes  OUTPUT_DIR/agentic/<run_id>/research.json
# The Strategy Analyst writes OUTPUT_DIR/agentic/<run_id>/theses.json
# (run_id = the queue's generated_at, same folder as the Phase 2 preflight).
#
# Both are written by LLM agents, so this module treats them as untrusted:
# `validate_research` / `validate_theses` return a list of human-readable
# errors (empty = clean) and never raise. The key rule (CONSTRAINT #4): a
# thesis driver's number must carry a `cite` that resolves — into the queue
# (`queue:<path>`) or into a raw MCP response the Analyst saved verbatim under
# `inputs/` (`file:<name>:<path>`) — and the cited value must equal it. A
# number with no source, a dangling cite, or a mismatch is an error, so a
# made-up figure cannot reach the operator's phone as if it were measured.
#
# Path grammar (after the `queue:` / `file:<name>:` prefix): dot-separated keys,
# with `[N]` (list index) or `[field=value]` (first list element whose `field`
# equals `value`, compared as strings). Example:
#     queue:intents[client_order_id=abc123].conviction
#     file:signals_T.json:data.modules[name=momentum].score
#
# This module never places, declines or sizes anything.
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from execution.agentic_preflight import PreflightReport, _output_dir, run_id_for

logger = logging.getLogger(__name__)

RESEARCH_FILENAME = "research.json"
THESES_FILENAME = "theses.json"
INPUTS_DIRNAME = "inputs"

THESIS_MAX_CHARS = 280
INVALIDATION_MAX_CHARS = 160
_REL_TOL = 1e-9
_ABS_TOL = 1e-12
_MISSING = object()
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+\.json$")
_SEGMENT = re.compile(r"([^.\[\]]+)|\[([^\]]*)\]")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def run_dir_for_queue(queue: Mapping[str, Any], output_dir: Optional[Any] = None) -> Path:
    """``OUTPUT_DIR/agentic/<run_id>`` for this queue (shared with the preflight)."""
    stub = PreflightReport(queue.get("generated_at"), "", "", [], [], {})
    return _output_dir(output_dir) / "agentic" / run_id_for(stub)


def _read_json(path: Path) -> Tuple[Any, Optional[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"{path.name} not found"
    except Exception as exc:
        return None, f"{path.name} unreadable ({type(exc).__name__})"


# ---------------------------------------------------------------------------
# Cite resolution
# ---------------------------------------------------------------------------

def resolve_path(doc: Any, path: str) -> Any:
    """Walk ``path`` through ``doc``; return the value or ``_MISSING``."""
    cur = doc
    pos = 0
    path = path.strip()
    if not path:
        return _MISSING
    for m in _SEGMENT.finditer(path):
        if m.start() != pos and path[pos:m.start()] != ".":
            return _MISSING
        pos = m.end()
        key, bracket = m.group(1), m.group(2)
        if key is not None:
            if not isinstance(cur, Mapping) or key not in cur:
                return _MISSING
            cur = cur[key]
            continue
        if not isinstance(cur, list):
            return _MISSING
        if "=" in bracket:
            field, want = bracket.split("=", 1)
            match = next((el for el in cur if isinstance(el, Mapping)
                          and str(el.get(field.strip())) == want.strip()), _MISSING)
            if match is _MISSING:
                return _MISSING
            cur = match
        else:
            try:
                cur = cur[int(bracket)]
            except (ValueError, IndexError):
                return _MISSING
    if pos != len(path):
        return _MISSING
    return cur


def resolve_cite(cite: Any, queue: Mapping[str, Any], cite_sources: Mapping[str, Any]) -> Tuple[Any, Optional[str]]:
    """Resolve a ``queue:...`` or ``file:<name>:...`` cite. Returns ``(value, error)``."""
    if not isinstance(cite, str) or ":" not in cite:
        return _MISSING, f"cite {cite!r} is not 'queue:<path>' or 'file:<name>:<path>'"
    kind, rest = cite.split(":", 1)
    if kind == "queue":
        value = resolve_path(queue, rest)
    elif kind == "file":
        if ":" not in rest:
            return _MISSING, f"cite {cite!r} has no path after the file name"
        name, path = rest.split(":", 1)
        if not _SAFE_FILENAME.match(name):
            return _MISSING, f"cite {cite!r}: file name must be a plain *.json name in inputs/"
        if name not in cite_sources:
            return _MISSING, f"cite {cite!r}: inputs/{name} was not saved"
        value = resolve_path(cite_sources[name], path)
    else:
        return _MISSING, f"cite {cite!r}: unknown source {kind!r}"
    if value is _MISSING:
        return _MISSING, f"cite {cite!r} does not resolve"
    return value, None


def _as_number(x: Any) -> Optional[float]:
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def values_match(claimed: Any, source: Any) -> bool:
    """Numbers (incl. numeric strings in raw MCP output) within 1e-9 relative;
    anything else must be exactly equal."""
    if isinstance(claimed, bool) or isinstance(source, bool):
        # Python's True == 1: a boolean only ever matches the same boolean.
        return isinstance(claimed, bool) and isinstance(source, bool) and claimed is source
    a, b = _as_number(claimed), _as_number(source)
    if a is not None and b is not None:
        if not (math.isfinite(a) and math.isfinite(b)):
            return False
        return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
    if isinstance(claimed, (int, float)) and not isinstance(claimed, bool):
        return False  # a number claimed against a non-numeric source
    return claimed == source


_NUMBER_IN_TEXT = re.compile(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?(%)?")


def _numbers_in(text: Any) -> List[Tuple[float, bool]]:
    """Numeric tokens in free text, as ``(value, is_percent)``. ``$27.48`` →
    27.48; ``1,200`` → 1200; ``12%`` → (12, True). Digits inside a word or a
    ticker (``S&P500``, ``Q3``) are not tokens."""
    if not isinstance(text, str):
        return []
    out = []
    for m in _NUMBER_IN_TEXT.finditer(text.replace("$", " ")):
        raw = m.group(0).rstrip("%").replace(",", "")
        try:
            out.append((float(raw), bool(m.group(1))))
        except ValueError:
            continue
    return out


def _is_gap_value(x: Any) -> bool:
    if x is None:
        return True
    v = _as_number(x)
    return v is not None and not math.isfinite(v)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _queue_intents(queue: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {str(i.get("client_order_id")): i for i in (queue.get("intents") or [])
            if isinstance(i, Mapping) and i.get("client_order_id")}


def _check_queue_match(doc: Mapping[str, Any], queue: Mapping[str, Any], label: str) -> List[str]:
    if doc.get("queue_generated_at") != queue.get("generated_at"):
        return [f"{label}: queue_generated_at {doc.get('queue_generated_at')!r} is not this queue "
                f"({queue.get('generated_at')!r})"]
    return []


def validate_research(doc: Any, queue: Mapping[str, Any]) -> List[str]:
    """Errors in a Scout ``research.json`` (empty list = clean)."""
    if not isinstance(doc, Mapping):
        return ["research: not a JSON object"]
    errors = _check_queue_match(doc, queue, "research")
    symbols = doc.get("symbols")
    if not isinstance(symbols, Mapping):
        return errors + ["research: 'symbols' must be an object keyed by symbol"]
    queue_syms = {str(i.get("symbol", "")).upper() for i in _queue_intents(queue).values()}
    for sym, entry in symbols.items():
        where = f"research[{sym}]"
        if str(sym).upper() != sym:
            errors.append(f"{where}: symbol keys must be upper case")
        if not isinstance(entry, Mapping):
            errors.append(f"{where}: not an object")
            continue
        gaps = entry.get("data_gaps", [])
        if not isinstance(gaps, list):
            errors.append(f"{where}.data_gaps: must be a list")
            gaps = []
        ne = entry.get("next_earnings")
        if not isinstance(ne, Mapping):
            errors.append(f"{where}.next_earnings: required (a date, or date null with a reason)")
        else:
            d = ne.get("date")
            if d is None:
                if not ne.get("reason") and "next_earnings" not in gaps:
                    errors.append(f"{where}.next_earnings: date is null with no reason")
            else:
                try:
                    date.fromisoformat(str(d))
                except ValueError:
                    errors.append(f"{where}.next_earnings.date {d!r} is not YYYY-MM-DD")
            if not ne.get("source"):
                errors.append(f"{where}.next_earnings: missing 'source'")
        ratings = entry.get("analyst_ratings", _MISSING)
        if ratings is None or ratings is _MISSING:
            if "analyst_ratings" not in gaps:
                errors.append(f"{where}.analyst_ratings: missing without a data_gaps entry")
        elif isinstance(ratings, Mapping):
            for k in ("buy", "hold", "sell"):
                v = ratings.get(k)
                if v is not None and (_as_number(v) is None or _as_number(v) < 0):
                    errors.append(f"{where}.analyst_ratings.{k} {v!r} is not a non-negative number")
            if not ratings.get("source"):
                errors.append(f"{where}.analyst_ratings: missing 'source'")
        else:
            errors.append(f"{where}.analyst_ratings: must be an object or null")
        for kind, url_key in (("news", "url"), ("recent_filings", "url")):
            items = entry.get(kind, [])
            if not isinstance(items, list):
                errors.append(f"{where}.{kind}: must be a list")
                continue
            for n, item in enumerate(items):
                if not isinstance(item, Mapping) or not str(item.get(url_key) or "").startswith(("http://", "https://")):
                    errors.append(f"{where}.{kind}[{n}]: needs an http(s) {url_key}")
    missing = sorted(queue_syms - {str(s).upper() for s in symbols})
    if missing:
        errors.append(f"research: no entry for queue symbol(s) {', '.join(missing)}")
    return errors


def validate_theses(doc: Any, queue: Mapping[str, Any], cite_sources: Mapping[str, Any]) -> Dict[str, List[str]]:
    """Errors in an Analyst ``theses.json``, keyed by ``client_order_id``
    (``"_doc"`` for document-level problems). An empty dict means clean."""
    out: Dict[str, List[str]] = {}

    def err(key: str, msg: str) -> None:
        out.setdefault(key, []).append(msg)

    if not isinstance(doc, Mapping):
        return {"_doc": ["theses: not a JSON object"]}
    for e in _check_queue_match(doc, queue, "theses"):
        err("_doc", e)
    theses = doc.get("theses")
    if not isinstance(theses, Mapping):
        err("_doc", "theses: 'theses' must be an object keyed by client_order_id")
        return out
    intents = _queue_intents(queue)
    for coid, t in theses.items():
        if coid not in intents:
            err(coid, "no queue intent has this client_order_id")
            continue
        if not isinstance(t, Mapping):
            err(coid, "thesis is not an object")
            continue
        intent = intents[coid]
        if str(t.get("symbol", "")).upper() != str(intent.get("symbol", "")).upper():
            err(coid, f"symbol {t.get('symbol')!r} does not match the queue ({intent.get('symbol')!r})")
        if str(t.get("side", "")).lower() != str(intent.get("side", "")).lower():
            err(coid, f"side {t.get('side')!r} does not match the queue ({intent.get('side')!r})")
        for field, limit in (("thesis", THESIS_MAX_CHARS), ("invalidation", INVALIDATION_MAX_CHARS)):
            text = t.get(field)
            if not isinstance(text, str) or not text.strip():
                err(coid, f"'{field}' is required")
            elif len(text) > limit:
                err(coid, f"'{field}' is {len(text)} chars (max {limit})")
        gaps = t.get("data_gaps", [])
        if not isinstance(gaps, list):
            err(coid, "'data_gaps' must be a list")
            gaps = []
        drivers = t.get("drivers", [])
        if not isinstance(drivers, list):
            err(coid, "'drivers' must be a list")
            continue
        # Numbers in the thesis SENTENCE (what the operator reads on the phone)
        # must each equal a cited driver value; a % may match value×100. The
        # invalidation line states a condition, not a measurement, so it is
        # exempt.
        cited = [_as_number(d.get("value")) for d in drivers if isinstance(d, Mapping)]
        cited = [c for c in cited if c is not None and math.isfinite(c)]
        for token, is_pct in _numbers_in(t.get("thesis")):
            ok = any(math.isclose(token, c, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
                     or (is_pct and math.isclose(token, c * 100, rel_tol=1e-6, abs_tol=1e-9))
                     for c in cited)
            if not ok:
                err(coid, f"thesis text quotes {token:g}{'%' if is_pct else ''}, which is not a cited driver value")
        for n, d in enumerate(drivers):
            if not isinstance(d, Mapping) or not d.get("name"):
                err(coid, f"drivers[{n}]: needs a 'name'")
                continue
            name, value = str(d["name"]), d.get("value", _MISSING)
            if value is _MISSING:
                err(coid, f"driver {name!r}: no 'value'")
                continue
            if _is_gap_value(value):
                if name not in gaps:
                    err(coid, f"driver {name!r} is missing/NaN but not listed in data_gaps")
                continue
            source, cite_err = resolve_cite(d.get("cite"), queue, cite_sources)
            if cite_err:
                err(coid, f"driver {name!r}: {cite_err}")
            elif not values_match(value, source):
                err(coid, f"driver {name!r}: value {value!r} does not match its source {source!r}")
    return out


def valid_thesis_ids(theses_doc: Any, errors: Mapping[str, List[str]]) -> List[str]:
    """client_order_ids that have a thesis and no errors (and no doc-level error)."""
    if "_doc" in errors or not isinstance(theses_doc, Mapping):
        return []
    theses = theses_doc.get("theses")
    if not isinstance(theses, Mapping):
        return []
    return [coid for coid in theses if coid not in errors]


# ---------------------------------------------------------------------------
# Bridges to the preflight
# ---------------------------------------------------------------------------

def earnings_dates_from_research(doc: Any) -> Dict[str, Optional[date]]:
    """``{SYM: date | None}`` for the preflight's earnings blackout.

    ``None`` = the Scout checked and nothing is scheduled (``date: null`` with a
    reason). A symbol whose date is unparseable or whose entry is malformed is
    LEFT OUT, which the preflight treats as "never checked" → BUY vetoed.
    """
    out: Dict[str, Optional[date]] = {}
    symbols = doc.get("symbols") if isinstance(doc, Mapping) else None
    if not isinstance(symbols, Mapping):
        return out
    for sym, entry in symbols.items():
        ne = entry.get("next_earnings") if isinstance(entry, Mapping) else None
        if not isinstance(ne, Mapping):
            continue
        raw = ne.get("date")
        if raw is None:
            if ne.get("reason"):
                out[str(sym).upper()] = None
            continue
        try:
            out[str(sym).upper()] = date.fromisoformat(str(raw))
        except ValueError:
            continue
    return out


def load_cite_sources(run_dir: Path) -> Tuple[Dict[str, Any], List[str]]:
    """Every ``inputs/*.json`` the agents saved, by file name."""
    sources: Dict[str, Any] = {}
    errors: List[str] = []
    inputs = run_dir / INPUTS_DIRNAME
    if not inputs.is_dir():
        return sources, errors
    for p in sorted(inputs.glob("*.json")):
        data, e = _read_json(p)
        if e:
            errors.append(f"inputs/{e}")
        else:
            sources[p.name] = data
    return sources, errors


def load_run_artifacts(queue: Mapping[str, Any], output_dir: Optional[Any] = None) -> Dict[str, Any]:
    """Read and validate both artifacts for THIS queue. Never raises.

    Returns ``{"research", "theses", "research_errors", "thesis_errors",
    "valid_thesis_ids", "input_errors"}``; a missing file is reported as an
    error and its document is ``None``.
    """
    run_dir = run_dir_for_queue(queue, output_dir)
    sources, input_errors = load_cite_sources(run_dir)
    research, r_err = _read_json(run_dir / RESEARCH_FILENAME)
    theses, t_err = _read_json(run_dir / THESES_FILENAME)
    research_errors = [r_err] if r_err else validate_research(research, queue)
    thesis_errors = {"_doc": [t_err]} if t_err else validate_theses(theses, queue, sources)
    return {
        "research": research if not r_err else None,
        "theses": theses if not t_err else None,
        "research_errors": research_errors,
        "thesis_errors": thesis_errors,
        "valid_thesis_ids": valid_thesis_ids(theses, thesis_errors) if not t_err else [],
        "input_errors": input_errors,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the Scout's research.json and the Analyst's theses.json for a queue.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--queue", help="queue path (default: $OUTPUT_DIR/execution_queue.json)")
    v.add_argument("--output-dir", help="override $OUTPUT_DIR")
    args = ap.parse_args(argv)

    out_dir = _output_dir(args.output_dir)
    queue_path = Path(args.queue) if args.queue else out_dir / "execution_queue.json"
    queue, q_err = _read_json(queue_path)
    if q_err or not isinstance(queue, Mapping):
        print(json.dumps({"ok": False, "error": f"queue unreadable: {queue_path}"}))
        return 2
    res = load_run_artifacts(queue, out_dir)
    ok = not res["research_errors"] and not res["thesis_errors"] and not res["input_errors"]
    print(json.dumps({
        "ok": ok,
        "run_dir": str(run_dir_for_queue(queue, out_dir)),
        "research_errors": res["research_errors"],
        "thesis_errors": res["thesis_errors"],
        "input_errors": res["input_errors"],
        "valid_thesis_ids": res["valid_thesis_ids"],
    }, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

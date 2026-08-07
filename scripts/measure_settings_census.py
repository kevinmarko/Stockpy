"""
scripts/measure_settings_census.py
===================================
Reproducible census of ``settings.Settings`` and its write-gate surface.

Why this exists
----------------
The settings layer (a pydantic-settings singleton in ``settings.py``) and its
GUI-writable allowlist (``gui/env_io.py``) are the substrate for an in-progress
settings hot-reload effort.  Several prior rounds of analysis were done by hand
against commits that were stale by the time anyone acted on them, and were
repeatedly found wrong on re-measurement.  This script exists so that the
numbers are *derived*, never transcribed: it emits a machine-readable
``docs/settings_field_census.json`` and a generated
``docs/settings_field_census.md`` from a single pass over the live tree.

Everything it does is read-only.  It imports ``settings`` and ``gui.env_io``
for their real runtime values (field set, allowlists) and uses ``ast`` — never
a text grep — for every source-level question, because this codebase binds the
settings singleton under at least two dozen distinct local names
(``settings``, ``_settings``, ``_s``, ``_s2``, ``platform_settings``,
``_oos_gate_settings``, ``_settings_mod.settings``, …).  A naive grep for the
literal ``settings.`` undercounts, which is exactly how earlier manual counts
went wrong.

What it measures
----------------
1. Field-type breakdown of ``Settings.model_fields`` (+ ``_ENABLED`` count).
2. ``gui/env_io.py`` list sizes, including duplicate detection in
   ``ALLOWED_KEYS`` (a known, still-unfixed bug — reported, never fixed here).
3. The SECRET / IN_ALLOWED_KEYS / UNCLASSIFIED partition over every field.
4. ``SECRET_KEYS`` sanity: phantom entries, plus a credential-shaped-name
   sweep for fields that *should* plausibly be secret but are not.
5. Fields whose ``settings.py`` comment or ``Field(description=...)`` claims
   they are deliberately hand-set-only, cross-referenced against whether they
   are *actually* absent from ``ALLOWED_KEYS`` right now.
6. Inventory of every ``api/pilots_api.py`` write endpoint: which write
   ``.env``, which do a live in-process ``setattr``, and what their response's
   ``applies`` field claims.
7. Read-form census across production code: ``settings.KEY`` attribute reads,
   ``getattr(settings, "KEY", ...)`` literal reads, ``getattr(settings, var)``
   dynamic reads, and ``os.environ``-style reads of a field name.

This script writes nothing outside ``docs/`` and mutates no runtime state.

Usage
-----
    python3 scripts/measure_settings_census.py            # print summary only
    python3 scripts/measure_settings_census.py --write    # + regenerate docs/
    python3 scripts/measure_settings_census.py --json     # dump raw JSON

The ``bootstrap()`` call lives inside ``if __name__ == "__main__":`` (rather
than at module top) so this module stays safely importable as a library by a
later analysis pass — matching ``scripts/snapshot_diff.py``'s convention. See
``scripts/_bootstrap.py``'s docstring for the full rationale.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import argparse
import ast
import json
import re
import types
import typing
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

DOCS_DIR = _REPO_ROOT / "docs"
JSON_OUT = DOCS_DIR / "settings_field_census.json"
MD_OUT = DOCS_DIR / "settings_field_census.md"

# Directories never walked for the read-form census. `tests` / `webapp` /
# `.venv` / `node_modules` are excluded by the task's definition of
# "production code"; the rest are build/output artifacts that contain no
# hand-written source. `.claude` / `.gemini` also each nest a `worktrees/`
# subdirectory holding OTHER agents' full checkouts (often on a different
# branch) — walking into one contaminates the census with read-form sites
# from unrelated code, exactly the class of bug this file's `meta` comment
# above (no baked-in repo_root) already exists to avoid one layer up. Skip
# both wholesale, same coarse granularity already used for `.claude`.
_SKIP_DIRS = {
    ".venv",
    ".git",
    "node_modules",
    "tests",
    "webapp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "output",
    "cache",
    ".claude",
    ".gemini",
    "build",
    "dist",
    ".ipynb_checkpoints",
}

# Individual files excluded from "production code" regardless of directory.
_SKIP_FILE_PATTERNS = (
    re.compile(r"^test_.*\.py$"),
    re.compile(r"^.*_test\.py$"),
    re.compile(r"^conftest\.py$"),
)

# Case-insensitive name pattern for "this field looks like a credential".
# Specified by the audit brief; see `_credential_shaped_report` for the
# type-based false-positive filter applied on top of it.
_CREDENTIAL_PATTERN = re.compile(
    r"TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|MFA", re.IGNORECASE
)

# A deliberately wider supplementary sweep, reported separately. The brief's
# pattern above misses at least these credential shapes by construction.
_CREDENTIAL_PATTERN_WIDE = re.compile(
    r"TOTP|PASSPHRASE|PRIVATE_KEY|WEBHOOK|CLIENT_ID|CLIENT_SECRET|_PW\b|AUTH",
    re.IGNORECASE,
)

# Phrases in a settings.py comment or Field(description=...) that mark a field
# as deliberately excluded from the GUI write allowlist. Several variants
# because the phrasing is not consistent across the file.
#
# Every pattern must be WRITE-GATE-SPECIFIC. An earlier, looser version of
# this list matched a bare "deliberately excluded", which fired on
# ETF_HOLDINGS_MARKET_PROXY's "Deliberately EXCLUDED from the
# ownership-weighted return composite" -- a sentence about return maths, not
# about ALLOWED_KEYS. Anything referring to exclusion must therefore name
# ALLOWED_KEYS (or GUI-writability) explicitly.
_HAND_SET_PATTERNS = [
    re.compile(r"never\s+GUI-writable", re.IGNORECASE),
    re.compile(r"not\s+GUI-writable", re.IGNORECASE),
    re.compile(r"never\s+be\s+GUI-\w+", re.IGNORECASE),
    re.compile(r"hand-?\s?set\s+in\s+`?\.env`?\s+only", re.IGNORECASE),
    re.compile(r"deliberately\s+(?:NOT\s+)?(?:excluded\s+from|in)\b[^.;]{0,80}ALLOWED_KEYS",
               re.IGNORECASE),
    re.compile(r"deliberately\s+NOT\s+in\b[^.;]{0,80}ALLOWED_KEYS", re.IGNORECASE),
    re.compile(r"not\s+in\s+(?:gui/env_io\.py'?s?\s+)?ALLOWED_KEYS", re.IGNORECASE),
    re.compile(r"never\s+allowlisted", re.IGNORECASE),
]

# Sentence splitter used to bound a marker's attribution window.
_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+")

# An ALL_CAPS token that might be another Settings field name.
_FIELDNAME_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")

# A sentence opening that makes a preceding field name COMPARATIVE ("this field
# behaves like that one") rather than the sentence's subject. Without this, the
# six `*_WRITES_ENABLED` flags -- all documented as "Mirrors
# AUTOMATION_WRITES_ENABLED exactly: ... deliberately NOT in ... ALLOWED_KEYS"
# -- would be wrongly attributed to the field they are compared against.
_COMPARATIVE_OPENER = re.compile(
    r"^\s*(?:Mirrors|Same\b|Like\b|Identical\b|Matches\b|As\s+with\b|Follows\b)",
    re.IGNORECASE,
)

# env_io write functions whose presence in a handler means "this writes .env".
_ENV_WRITE_FUNCS = {"write_setting", "write_many", "write_many_atomic"}

# `gui/daemon_client.py` setters: a THIRD mutation mechanism, distinct from both
# the `.env` write and the in-process `setattr` -- these push a new value over
# HTTP into a SEPARATELY RUNNING orchestrator daemon process. Any liveness model
# that only considers "this process's singleton" misses these entirely.
_DAEMON_PUSH_PREFIX = "daemon_client."


# ---------------------------------------------------------------------------
# 1. Field-type breakdown
# ---------------------------------------------------------------------------

def _type_label(ann: Any) -> str:
    """Render a type annotation as a stable, readable canonical label.

    Recurses through ``Optional`` / ``Union`` / ``list`` / ``dict`` so that
    every distinct ``dict[...]`` shape gets its own label (the brief asks for
    these to be counted separately, since a future kind-derivation switch
    needs one branch per shape).
    """
    if ann is None:
        return "None"
    if ann is type(None):
        return "None"

    # Plain, non-generic types.
    origin = typing.get_origin(ann)
    if origin is None:
        name = getattr(ann, "__name__", None)
        if name:
            return name
        return str(ann).replace("typing.", "")

    args = typing.get_args(ann)

    # Optional[X] / X | None -> collapse to Optional[X] when exactly one
    # non-None arm remains; otherwise render the full Union.
    if origin is typing.Union or origin is getattr(types, "UnionType", object()):
        non_none = [a for a in args if a is not type(None)]
        has_none = len(non_none) != len(args)
        if has_none and len(non_none) == 1:
            return f"Optional[{_type_label(non_none[0])}]"
        inner = ", ".join(_type_label(a) for a in args)
        return f"Union[{inner}]"

    origin_name = getattr(origin, "__name__", str(origin))
    if not args:
        return origin_name
    inner = ", ".join(_type_label(a) for a in args)
    return f"{origin_name}[{inner}]"


# Labels the brief calls out explicitly. Anything outside this set (other than
# a dict[...] shape, which is always recognised) lands in "other/unhandled".
_RECOGNISED_LABELS = {
    "bool",
    "int",
    "float",
    "str",
    "Optional[str]",
    "list[str]",
    "list[int]",
    "Path",
    "PosixPath",
    "WindowsPath",
}


def _is_recognised(label: str) -> bool:
    return label in _RECOGNISED_LABELS or label.startswith("dict[")


def collect_field_types(model_fields: Dict[str, Any]) -> Dict[str, Any]:
    labels: Dict[str, str] = {}
    for name, info in model_fields.items():
        labels[name] = _type_label(info.annotation)

    counter = Counter(labels.values())
    other = sorted(n for n, lab in labels.items() if not _is_recognised(lab))
    dict_shapes = Counter(lab for lab in labels.values() if lab.startswith("dict["))
    enabled = sorted(n for n in model_fields if n.endswith("_ENABLED"))

    return {
        "total_fields": len(model_fields),
        "label_by_field": labels,
        "counts_by_label": dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        "dict_shapes": dict(sorted(dict_shapes.items())),
        "other_unhandled_fields": other,
        "other_unhandled_count": len(other),
        "enabled_suffix_fields": enabled,
        "enabled_suffix_count": len(enabled),
    }


# ---------------------------------------------------------------------------
# 2 + 3 + 4. env_io lists, partition, secret sanity
# ---------------------------------------------------------------------------

def collect_env_io_lists(env_io: Any) -> Dict[str, Any]:
    allowed = tuple(env_io.ALLOWED_KEYS)
    secret = tuple(env_io.SECRET_KEYS)
    json_keys = frozenset(getattr(env_io, "_JSON_KEYS", frozenset()))
    excluded = frozenset(getattr(env_io, "EXCLUDED_FROM_GUI", frozenset()))

    allowed_dupes = {k: c for k, c in Counter(allowed).items() if c > 1}
    secret_dupes = {k: c for k, c in Counter(secret).items() if c > 1}

    return {
        "allowed_keys_len": len(allowed),
        "allowed_keys_unique_len": len(set(allowed)),
        "allowed_keys_duplicates": dict(sorted(allowed_dupes.items())),
        "allowed_keys_duplicate_total_extra": len(allowed) - len(set(allowed)),
        "secret_keys_len": len(secret),
        "secret_keys_unique_len": len(set(secret)),
        "secret_keys_duplicates": dict(sorted(secret_dupes.items())),
        "json_keys_name": "_JSON_KEYS",
        "json_keys_len": len(json_keys),
        "json_keys": sorted(json_keys),
        "excluded_from_gui_name": "EXCLUDED_FROM_GUI",
        "excluded_from_gui_len": len(excluded),
        "excluded_from_gui": sorted(excluded),
        "allowed_and_secret_overlap": sorted(set(allowed) & set(secret)),
    }


def collect_partition(
    model_fields: Dict[str, Any],
    env_io: Any,
    descriptions: Dict[str, str],
    field_lines: Dict[str, int],
) -> Dict[str, Any]:
    allowed = set(env_io.ALLOWED_KEYS)
    secret = set(env_io.SECRET_KEYS)
    excluded = frozenset(getattr(env_io, "EXCLUDED_FROM_GUI", frozenset()))

    buckets: Dict[str, List[str]] = {"SECRET": [], "IN_ALLOWED_KEYS": [], "UNCLASSIFIED": []}
    for name in model_fields:
        if name in secret:
            buckets["SECRET"].append(name)
        elif name in allowed:
            buckets["IN_ALLOWED_KEYS"].append(name)
        else:
            buckets["UNCLASSIFIED"].append(name)

    unclassified_detail = []
    for name in sorted(buckets["UNCLASSIFIED"]):
        desc = (descriptions.get(name) or "").strip()
        one_line = re.sub(r"\s+", " ", desc)
        if len(one_line) > 200:
            one_line = one_line[:197] + "..."
        unclassified_detail.append(
            {
                "field": name,
                "in_excluded_from_gui": name in excluded,
                "settings_py_line": field_lines.get(name),
                "description": one_line,
            }
        )

    return {
        "counts": {k: len(v) for k, v in buckets.items()},
        "secret": sorted(buckets["SECRET"]),
        "in_allowed_keys": sorted(buckets["IN_ALLOWED_KEYS"]),
        "unclassified": sorted(buckets["UNCLASSIFIED"]),
        "unclassified_detail": unclassified_detail,
        "unclassified_covered_by_excluded_from_gui": sorted(
            n for n in buckets["UNCLASSIFIED"] if n in excluded
        ),
        "unclassified_not_covered_anywhere": sorted(
            n for n in buckets["UNCLASSIFIED"] if n not in excluded
        ),
    }


def collect_secret_sanity(
    model_fields: Dict[str, Any],
    env_io: Any,
    type_labels: Dict[str, str],
) -> Dict[str, Any]:
    secret = set(env_io.SECRET_KEYS)
    allowed = set(env_io.ALLOWED_KEYS)
    names = set(model_fields)

    phantoms = sorted(secret - names)

    def _sweep(pattern: re.Pattern) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        protected: List[Dict[str, Any]] = []
        flagged: List[Dict[str, Any]] = []
        for name in sorted(names):
            if not pattern.search(name):
                continue
            label = type_labels.get(name, "?")
            row = {
                "field": name,
                "type": label,
                "in_secret_keys": name in secret,
                "in_allowed_keys": name in allowed,
            }
            if name in secret:
                protected.append(row)
            else:
                # A field typed int/float/bool is never actually a credential
                # regardless of a name match (e.g. an int chunk-size whose
                # name happens to contain TOKENS). Only str-shaped matches
                # can hold secret material.
                is_string_shaped = label in ("str", "Optional[str]")
                row["string_shaped"] = is_string_shaped
                row["flagged_as_gap"] = is_string_shaped
                flagged.append(row)
        return protected, flagged

    protected, unprotected = _sweep(_CREDENTIAL_PATTERN)
    wide_protected, wide_unprotected = _sweep(_CREDENTIAL_PATTERN_WIDE)

    real_gaps = [r for r in unprotected if r["flagged_as_gap"]]
    wide_gaps = [
        r
        for r in wide_unprotected
        if r["flagged_as_gap"] and not _CREDENTIAL_PATTERN.search(r["field"])
    ]

    return {
        "phantom_secret_keys": phantoms,
        "phantom_count": len(phantoms),
        "pattern": _CREDENTIAL_PATTERN.pattern,
        "pattern_matches_protected": protected,
        "pattern_matches_unprotected": unprotected,
        "pattern_real_gaps": real_gaps,
        "pattern_real_gap_count": len(real_gaps),
        "wide_pattern": _CREDENTIAL_PATTERN_WIDE.pattern,
        "wide_pattern_extra_gaps": wide_gaps,
        "wide_pattern_extra_gap_count": len(wide_gaps),
    }


# ---------------------------------------------------------------------------
# settings.py source parse: field line numbers, descriptions, comments
# ---------------------------------------------------------------------------

def parse_settings_source(model_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Extract per-field source line numbers from ``settings.py``'s AST, plus
    the raw source lines for the hand-set-only comment sweep."""
    src_path = _REPO_ROOT / "settings.py"
    source = src_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(src_path))

    field_lines: Dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "Settings":
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if stmt.target.id in model_fields:
                    field_lines[stmt.target.id] = stmt.lineno
            elif isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name) and tgt.id in model_fields:
                        field_lines[tgt.id] = stmt.lineno

    descriptions: Dict[str, str] = {}
    for name, info in model_fields.items():
        if getattr(info, "description", None):
            descriptions[name] = info.description

    return {
        "field_lines": field_lines,
        "descriptions": descriptions,
        "source_lines": lines,
        "fields_without_source_line": sorted(set(model_fields) - set(field_lines)),
    }


def _marker_match(text: str, self_field: str, all_fields: Set[str]) -> Optional[Dict[str, Any]]:
    """Return the marker hit for ``text`` if it genuinely describes ``self_field``.

    Two disambiguation rules, both learned from real false positives in this
    file (see ``_HAND_SET_PATTERNS``' comment for the first):

    1. The marker must be write-gate-specific (enforced by the patterns).
    2. Attribution is per-SENTENCE. A sentence that names a DIFFERENT
       ``Settings`` field before the marker phrase is rejected ONLY when the
       marker sits inside the same parenthetical as that other field name, or
       when the sentence is not a comparative construction. The real rejection
       case: ``RAG_PORTFOLIO_CONTEXT_PROVIDER``'s description ends
       "...(ANTHROPIC_API_KEY or GEMINI_API_KEY, both classified as
       SECRET_KEYS - never GUI-writable)" -- a statement about the two API
       keys, made inside a parenthetical. The case that must NOT be rejected:
       "Mirrors AUTOMATION_WRITES_ENABLED exactly: ... deliberately NOT in
       gui/env_io.py's ALLOWED_KEYS", where the other name is comparative and
       the marker genuinely describes the field it is attached to.
    """
    flat = re.sub(r"\s+", " ", text)
    for sentence in _SENTENCE_SPLIT.split(flat):
        for pat in _HAND_SET_PATTERNS:
            m = pat.search(sentence)
            if not m:
                continue
            before = sentence[: m.start()]
            others_before = {
                tok
                for tok in _FIELDNAME_TOKEN.findall(before)
                if tok in all_fields and tok != self_field
            }
            reason = None
            if others_before:
                # Is the marker inside a parenthetical that also holds one of
                # those names? Track the innermost unclosed "(" before it.
                open_at = None
                depth = 0
                for i, ch in enumerate(before):
                    if ch == "(":
                        if depth == 0:
                            open_at = i
                        depth += 1
                    elif ch == ")" and depth:
                        depth -= 1
                        if depth == 0:
                            open_at = None
                inside_paren_with_other = open_at is not None and any(
                    tok in before[open_at:] for tok in others_before
                )
                if inside_paren_with_other:
                    reason = "marker sits inside a parenthetical naming another field"
                elif not _COMPARATIVE_OPENER.search(sentence):
                    reason = "another field is named before the marker, non-comparative sentence"
            if reason:
                return {
                    "ambiguous": True,
                    "sentence": sentence[:220],
                    "matched": m.group(0),
                    "attributed_instead_to": sorted(others_before),
                    "reason": reason,
                }
            return {
                "ambiguous": False,
                "sentence": sentence[:220],
                "matched": m.group(0),
            }
    return None


def collect_hand_set_markers(
    model_fields: Dict[str, Any],
    env_io: Any,
    parsed: Dict[str, Any],
) -> Dict[str, Any]:
    """Find fields marked "hand-set / never GUI-writable" in settings.py, and
    cross-reference against whether they are ACTUALLY absent from ALLOWED_KEYS
    right now (a comment can go stale if someone allowlists the field later).
    """
    allowed = set(env_io.ALLOWED_KEYS)
    secret = set(env_io.SECRET_KEYS)
    all_fields = set(model_fields)
    field_lines: Dict[str, int] = parsed["field_lines"]
    descriptions: Dict[str, str] = parsed["descriptions"]
    lines: List[str] = parsed["source_lines"]

    hits: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    rejected: List[Dict[str, Any]] = []

    # (a) description-based attribution.
    for name, desc in descriptions.items():
        res = _marker_match(desc, name, all_fields)
        if res is None:
            continue
        site = {
            "file": "settings.py",
            "line": field_lines.get(name, -1),
            "origin": "Field(description=...)",
            "text": res["sentence"],
            "matched": res["matched"],
        }
        if res["ambiguous"]:
            rejected.append({"candidate_field": name, **site,
                             "reason": res["reason"],
                             "attributed_instead_to": res["attributed_instead_to"]})
        else:
            hits[name].append(site)

    # (b) comment-based attribution: attach a matching `#` comment block to the
    # NEXT field declared at or after it (within a 40-line window) — this file
    # is written with the rationale comment immediately preceding its field.
    sorted_fields = sorted(field_lines.items(), key=lambda kv: kv[1])
    # Join contiguous comment runs so a phrase split across two `#` lines
    # (very common here, e.g. "hand-set in\n# .env only") is still matched.
    idx = 1
    while idx <= len(lines):
        if not lines[idx - 1].strip().startswith("#"):
            idx += 1
            continue
        start = idx
        block: List[str] = []
        while idx <= len(lines) and lines[idx - 1].strip().startswith("#"):
            block.append(lines[idx - 1].strip().lstrip("#").strip())
            idx += 1
        blob = " ".join(block)
        owner = None
        for name, lineno in sorted_fields:
            if lineno >= start:
                if lineno - start <= 40:
                    owner = name
                break
        if not owner:
            continue
        res = _marker_match(blob, owner, all_fields)
        if res is None:
            continue
        site = {
            "file": "settings.py",
            "line": start,
            "origin": "# comment",
            "text": res["sentence"],
            "matched": res["matched"],
        }
        if res["ambiguous"]:
            rejected.append({"candidate_field": owner, **site,
                             "reason": res["reason"],
                             "attributed_instead_to": res["attributed_instead_to"]})
        else:
            hits[owner].append(site)

    rows = []
    for name in sorted(hits):
        rows.append(
            {
                "field": name,
                "settings_py_line": field_lines.get(name),
                "marker_sites": sorted(hits[name], key=lambda s: s["line"]),
                "currently_in_allowed_keys": name in allowed,
                "currently_in_secret_keys": name in secret,
                "comment_claim_holds": name not in allowed,
            }
        )

    contradictions = [r for r in rows if not r["comment_claim_holds"]]
    return {
        "marked_field_count": len(rows),
        "marked_fields": rows,
        "contradiction_count": len(contradictions),
        "contradictions": contradictions,
        "rejected_ambiguous_markers": rejected,
        "rejected_ambiguous_count": len(rejected),
    }


# ---------------------------------------------------------------------------
# 6. pilots_api.py write-endpoint inventory
# ---------------------------------------------------------------------------

def _decorator_route(dec: ast.expr) -> Optional[Tuple[str, str]]:
    """Return (http_method, route_path) for an ``@app.<method>(...)`` decorator."""
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return None
    method = func.attr
    if method not in {"put", "post", "patch", "delete"}:
        return None
    if not isinstance(func.value, ast.Name):
        return None
    path = None
    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
        path = dec.args[0].value
    return method, (path or "<non-literal>")


def _called_names(node: ast.AST) -> Set[str]:
    """Every simple call name reachable in a subtree (``f()`` -> ``f``,
    ``a.b.f()`` -> ``f`` and ``a.b.f``)."""
    out: Set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if isinstance(f, ast.Name):
            out.add(f.id)
        elif isinstance(f, ast.Attribute):
            out.add(f.attr)
            parts = []
            cur: Any = f
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                out.add(".".join(reversed(parts)))
    return out


def _live_setattr_sites(node: ast.AST) -> List[Dict[str, Any]]:
    """Find in-process mutations of the settings singleton: both
    ``setattr(settings, ...)`` and a direct ``settings.X = ...`` assignment."""
    sites: List[Dict[str, Any]] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "setattr":
            target = sub.args[0] if sub.args else None
            tname = None
            if isinstance(target, ast.Name):
                tname = target.id
            elif isinstance(target, ast.Attribute):
                tname = target.attr
            if tname and "settings" in tname.lower():
                sites.append({"kind": "setattr", "line": sub.lineno, "target": tname})
        elif isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name):
                    if "settings" in tgt.value.id.lower():
                        sites.append(
                            {
                                "kind": "attribute_assign",
                                "line": sub.lineno,
                                "target": f"{tgt.value.id}.{tgt.attr}",
                            }
                        )
    return sites


def _literal_strings(value: ast.expr, scope: ast.AST) -> List[str]:
    """Resolve an ``applies`` value expression to the literal strings it can
    evaluate to.

    Three shapes occur in ``api/pilots_api.py`` and all three must be handled,
    because a naive "is this an ``ast.Constant``?" check reports
    ``applies: (none)`` for most of the write endpoints:

      * ``"applies": "next_daemon_restart"``                     -> Constant
      * ``"applies": "immediately" if live.ok else "next_..."``  -> IfExp
      * ``"applies": applies``                                   -> Name, bound
        earlier in the same function (usually to an IfExp)
    """
    if isinstance(value, ast.Constant):
        return [str(value.value)] if isinstance(value.value, str) else []
    if isinstance(value, ast.IfExp):
        return _literal_strings(value.body, scope) + _literal_strings(value.orelse, scope)
    if isinstance(value, ast.Name):
        out: List[str] = []
        for sub in ast.walk(scope):
            if isinstance(sub, ast.Assign):
                for tgt in sub.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == value.id:
                        out.extend(_literal_strings(sub.value, scope))
        return out
    return []


def _applies_values(node: ast.AST) -> List[str]:
    """Every value an ``"applies"`` response key can take inside this function
    (dict-literal key or ``applies=`` keyword)."""
    out: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for k, v in zip(sub.keys, sub.values):
                if isinstance(k, ast.Constant) and k.value == "applies":
                    out.extend(_literal_strings(v, node))
        elif isinstance(sub, ast.keyword) and sub.arg == "applies":
            out.extend(_literal_strings(sub.value, node))
    return sorted(set(out))


def collect_write_endpoints(rel_path: str) -> Dict[str, Any]:
    src_path = _REPO_ROOT / rel_path
    if not src_path.exists():
        return {"file": rel_path, "error": "file not found", "endpoints": []}
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(src_path))

    # Pass 1: module-level helpers that themselves write .env, so a handler
    # that only calls a helper is still correctly reported as writing .env
    # (one level of indirection — deeper chains are reported as "unresolved").
    helper_writes: Set[str] = set()
    helper_setattr: Set[str] = set()
    helper_applies: Dict[str, List[str]] = {}
    route_handler_names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(_decorator_route(d) for d in node.decorator_list):
                route_handler_names.add(node.name)
            if _called_names(node) & _ENV_WRITE_FUNCS:
                helper_writes.add(node.name)
            if _live_setattr_sites(node):
                helper_setattr.add(node.name)
            av = _applies_values(node)
            if av:
                helper_applies[node.name] = av

    endpoints: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = [r for r in (_decorator_route(d) for d in node.decorator_list) if r]
        if not routes:
            continue
        called = _called_names(node)
        direct_writes = sorted(called & _ENV_WRITE_FUNCS)
        indirect_writes = sorted(called & helper_writes)
        setattrs = _live_setattr_sites(node)
        indirect_setattr = sorted(called & helper_setattr)
        daemon_pushes = sorted(
            c for c in called
            if c.startswith(_DAEMON_PUSH_PREFIX)
            and c.split(".")[-1].startswith(("set_", "patch_", "update_"))
        )
        own_applies = _applies_values(node)
        # A handler that delegates its whole response to a shared helper (e.g.
        # `_validate_and_write_payload`) declares `applies` only inside that
        # helper -- attribute it back to the route, or five of the eleven
        # write endpoints get falsely reported as declaring nothing.
        via_helper_applies: List[str] = []
        for h in sorted(called & set(helper_applies)):
            if h == node.name or h in route_handler_names:
                continue
            via_helper_applies.extend(helper_applies[h])
        for method, path in routes:
            endpoints.append(
                {
                    "route": path,
                    "method": method.upper(),
                    "function": node.name,
                    "line": node.lineno,
                    "writes_env": bool(direct_writes or indirect_writes),
                    "env_write_calls_direct": direct_writes,
                    "env_write_calls_via_helper": indirect_writes,
                    "live_setattr": bool(setattrs or indirect_setattr),
                    "live_setattr_sites": setattrs,
                    "live_setattr_via_helper": indirect_setattr,
                    "live_daemon_push": bool(daemon_pushes),
                    "live_daemon_push_calls": daemon_pushes,
                    "applies_values": sorted(set(own_applies) | set(via_helper_applies)),
                    "applies_declared_directly": own_applies,
                    "applies_declared_via_helper": sorted(set(via_helper_applies)),
                }
            )

    endpoints.sort(key=lambda e: e["line"])
    writers = [
        e for e in endpoints
        if e["writes_env"] or e["live_setattr"] or e["live_daemon_push"]
    ]
    return {
        "file": rel_path,
        "total_write_method_routes": len(endpoints),
        "mutating_settings_routes": len(writers),
        "env_writing_routes": len([e for e in endpoints if e["writes_env"]]),
        "live_setattr_routes": len([e for e in endpoints if e["live_setattr"]]),
        "live_daemon_push_routes": len([e for e in endpoints if e["live_daemon_push"]]),
        "routes_declaring_applies": len([e for e in writers if e["applies_values"]]),
        # Shared helpers only — a route handler that happens to write .env
        # itself is already listed in the endpoint table and is not a "helper".
        "helper_functions_writing_env": sorted(helper_writes - route_handler_names),
        "helper_functions_live_setattr": sorted(helper_setattr - route_handler_names),
        "endpoints": endpoints,
    }


# ---------------------------------------------------------------------------
# 7. Read-form census
# ---------------------------------------------------------------------------

class _ReadVisitor(ast.NodeVisitor):
    """Collects settings read sites in one module.

    Alias resolution is the whole point of using AST here. Three binding
    shapes are recognised, all of which occur in this repo:

      * ``from settings import settings [as X]``      -> X reads fields directly
      * ``import settings [as M]``                    -> ``M.settings.FIELD``
      * ``def f(cfg: Settings)`` / ``Settings`` alias -> ``cfg`` reads fields

    Lazy, function-scope imports count too (``ast.walk`` sees them), which is
    load-bearing: a large fraction of this codebase imports the singleton
    inside function bodies to dodge import cycles.
    """

    def __init__(self, field_names: Set[str], rel_path: str) -> None:
        self.fields = field_names
        self.rel_path = rel_path
        self.singleton_aliases: Set[str] = set()
        self.module_aliases: Set[str] = set()
        self.class_aliases: Set[str] = {"Settings"}
        self.attr_reads: List[Tuple[str, int]] = []
        self.getattr_literal: List[Tuple[str, int]] = []
        self.getattr_dynamic: List[Dict[str, Any]] = []
        self.env_reads: List[Tuple[str, int, str]] = []
        # Every bare string literal equal to a field name. This is how a field
        # with no statically-attributable read can still be read at runtime:
        # the name is passed as DATA to a factory that dynamically getattrs it
        # (e.g. api/data_api.py's require_ai_capability_enabled).
        self.name_literals: List[Tuple[str, int]] = []

    # -- binding discovery ------------------------------------------------
    def discover_bindings(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "settings":
                for alias in node.names:
                    if alias.name == "settings":
                        self.singleton_aliases.add(alias.asname or "settings")
                    elif alias.name == "Settings":
                        self.class_aliases.add(alias.asname or "Settings")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "settings":
                        self.module_aliases.add(alias.asname or "settings")
        # Parameters annotated with the Settings class also hold the singleton.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                for arg in list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs):
                    ann = arg.annotation
                    label = None
                    if isinstance(ann, ast.Name):
                        label = ann.id
                    elif isinstance(ann, ast.Constant) and isinstance(ann.value, str):
                        label = ann.value.strip("\"'")
                    elif isinstance(ann, ast.Attribute):
                        label = ann.attr
                    if label and label.split("[")[0].strip() in self.class_aliases:
                        self.singleton_aliases.add(arg.arg)

    # -- helpers ----------------------------------------------------------
    def _is_settings_ref(self, node: ast.expr) -> bool:
        """True if `node` evaluates to the settings singleton."""
        if isinstance(node, ast.Name):
            return node.id in self.singleton_aliases
        if isinstance(node, ast.Attribute) and node.attr == "settings":
            return isinstance(node.value, ast.Name) and node.value.id in self.module_aliases
        return False

    @staticmethod
    def _is_environ(node: ast.expr) -> Optional[str]:
        """Classify an ``os.environ`` / ``environ`` reference."""
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            return "os.environ"
        if isinstance(node, ast.Name) and node.id == "environ":
            return "environ"
        return None

    # -- visitors ---------------------------------------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in self.fields and self._is_settings_ref(node.value):
            self.attr_reads.append((node.attr, node.lineno))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value in self.fields:
            self.name_literals.append((node.value, node.lineno))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        kind = self._is_environ(node.value)
        if (
            kind
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            # Load context only. ``os.environ["X"] = v`` and ``del os.environ["X"]``
            # are WRITES to the process environment, not reads of a setting; counting
            # them inflates the os_environ form. Concrete case that surfaced this:
            # ``Gravity AI Review Suite.py``'s execution-mode audit harness sets
            # ``ROBINHOOD_EXECUTION_MODE``/``ROBINHOOD_MAX_NOTIONAL_PER_ORDER`` via
            # subscript assignment, never reads them that way.
            and isinstance(node.ctx, ast.Load)
        ):
            if node.slice.value in self.fields:
                self.env_reads.append((node.slice.value, node.lineno, f"{kind}[...]"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        # getattr(<settings>, ...)
        if isinstance(f, ast.Name) and f.id == "getattr" and node.args:
            if self._is_settings_ref(node.args[0]):
                key_node = node.args[1] if len(node.args) > 1 else None
                if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                    if key_node.value in self.fields:
                        self.getattr_literal.append((key_node.value, node.lineno))
                else:
                    self.getattr_dynamic.append(
                        {
                            "file": self.rel_path,
                            "line": node.lineno,
                            "expr": _safe_unparse(node),
                        }
                    )
        # os.environ.get("KEY") / environ.get("KEY")
        if isinstance(f, ast.Attribute) and f.attr == "get":
            kind = self._is_environ(f.value)
            if kind and node.args and isinstance(node.args[0], ast.Constant):
                v = node.args[0].value
                if isinstance(v, str) and v in self.fields:
                    self.env_reads.append((v, node.lineno, f"{kind}.get()"))
        # os.getenv("KEY") / getenv("KEY")
        is_getenv = (isinstance(f, ast.Attribute) and f.attr == "getenv") or (
            isinstance(f, ast.Name) and f.id == "getenv"
        )
        if is_getenv and node.args and isinstance(node.args[0], ast.Constant):
            v = node.args[0].value
            if isinstance(v, str) and v in self.fields:
                self.env_reads.append((v, node.lineno, "os.getenv()"))
        self.generic_visit(node)


def _safe_unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return "<unparseable>"


def _production_py_files() -> List[Path]:
    out: List[Path] = []
    for path in sorted(_REPO_ROOT.rglob("*.py")):
        rel_parts = path.relative_to(_REPO_ROOT).parts
        if any(part in _SKIP_DIRS for part in rel_parts[:-1]):
            continue
        if rel_parts[0] in _SKIP_DIRS:
            continue
        if any(pat.match(path.name) for pat in _SKIP_FILE_PATTERNS):
            continue
        out.append(path)
    return out


def collect_read_forms(model_fields: Dict[str, Any]) -> Dict[str, Any]:
    fields = set(model_fields)
    files = _production_py_files()

    form_a: Counter = Counter()
    form_b: Counter = Counter()
    form_d: Counter = Counter()
    form_d_shapes: Counter = Counter()
    dynamic_sites: List[Dict[str, Any]] = []
    unparseable: List[Dict[str, str]] = []
    per_file_alias_summary: Dict[str, List[str]] = {}
    name_literal_sites: Dict[str, List[str]] = defaultdict(list)
    # settings.py and gui/env_io.py mention every field name by construction;
    # counting them would drown the signal.
    _literal_scan_skip = {"settings.py", "gui/env_io.py"}

    for path in files:
        rel = str(path.relative_to(_REPO_ROOT))
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unparseable.append({"file": rel, "error": f"read failed: {exc}"})
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            unparseable.append({"file": rel, "error": f"SyntaxError: {exc}"})
            continue

        v = _ReadVisitor(fields, rel)
        v.discover_bindings(tree)
        if not (v.singleton_aliases or v.module_aliases):
            # Still scan for os.environ reads — those need no settings import.
            pass
        v.visit(tree)

        for key, _ln in v.attr_reads:
            form_a[key] += 1
        for key, _ln in v.getattr_literal:
            form_b[key] += 1
        for key, _ln, shape in v.env_reads:
            form_d[key] += 1
            form_d_shapes[shape] += 1
        dynamic_sites.extend(v.getattr_dynamic)
        if rel not in _literal_scan_skip:
            for key, ln in v.name_literals:
                name_literal_sites[key].append(f"{rel}:{ln}")
        if v.singleton_aliases or v.module_aliases:
            per_file_alias_summary[rel] = sorted(
                v.singleton_aliases | {f"{m}.settings" for m in v.module_aliases}
            )

    reached_a = set(form_a)
    reached_b = set(form_b)
    reached_d = set(form_d)
    reached_any = reached_a | reached_b | reached_d

    only_b_or_d = sorted((reached_b | reached_d) - reached_a)
    only_b = sorted(reached_b - reached_a - reached_d)
    only_d = sorted(reached_d - reached_a - reached_b)
    no_static_read = sorted(fields - reached_any)
    # A field with no statically-attributable read may still be read at
    # runtime via a dynamic getattr whose key arrived as a string literal.
    no_static_read_detail = [
        {
            "field": n,
            "name_literal_sites": sorted(name_literal_sites.get(n, []))[:8],
            "name_literal_site_count": len(name_literal_sites.get(n, [])),
            "likely_dynamic_read": bool(name_literal_sites.get(n)),
        }
        for n in no_static_read
    ]

    all_aliases = sorted({a for v in per_file_alias_summary.values() for a in v})

    return {
        "files_scanned": len(files),
        "files_unparseable_count": len(unparseable),
        "files_unparseable": unparseable,
        "distinct_settings_aliases_found": all_aliases,
        "distinct_settings_alias_count": len(all_aliases),
        "form_a_attribute": {
            "total_reads": sum(form_a.values()),
            "distinct_fields": len(reached_a),
            "counts": dict(sorted(form_a.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        "form_b_getattr_literal": {
            "total_reads": sum(form_b.values()),
            "distinct_fields": len(reached_b),
            "counts": dict(sorted(form_b.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        "form_c_getattr_dynamic": {
            "total_sites": len(dynamic_sites),
            "sites": sorted(dynamic_sites, key=lambda s: (s["file"], s["line"])),
        },
        "form_d_os_environ": {
            "total_reads": sum(form_d.values()),
            "distinct_fields": len(reached_d),
            "counts": dict(sorted(form_d.items(), key=lambda kv: (-kv[1], kv[0]))),
            "shapes": dict(sorted(form_d_shapes.items())),
        },
        "fields_reached_by_any_form": len(reached_any),
        "fields_only_via_b_or_d": only_b_or_d,
        "fields_only_via_b_or_d_count": len(only_b_or_d),
        "fields_only_via_b": only_b,
        "fields_only_via_d": only_d,
        "fields_no_static_read": no_static_read,
        "fields_no_static_read_count": len(no_static_read),
        "fields_no_static_read_detail": no_static_read_detail,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def collect_census() -> Dict[str, Any]:
    """Run every measurement and return one JSON-serialisable payload."""
    import subprocess

    # Project imports are deliberately function-local: this keeps module scope
    # importable without bootstrap() having run (see module docstring).
    from settings import Settings
    from gui import env_io

    model_fields = dict(Settings.model_fields)

    try:
        commit = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
    except Exception:  # pragma: no cover - defensive
        commit = "<unavailable>"

    # The ONE existing in-process hot-reload beachhead: keys that
    # `PUT /llm/setting` will apply to the live singleton via setattr.
    try:
        from gui.ai_control_center import LIVE_PATCHABLE_KEYS as _live_keys
        live_patchable = sorted(_live_keys)
        live_patchable_error = None
    except Exception as exc:  # pragma: no cover - defensive
        live_patchable, live_patchable_error = [], repr(exc)

    types_data = collect_field_types(model_fields)
    parsed = parse_settings_source(model_fields)
    env_lists = collect_env_io_lists(env_io)
    partition = collect_partition(
        model_fields, env_io, parsed["descriptions"], parsed["field_lines"]
    )
    secret_sanity = collect_secret_sanity(model_fields, env_io, types_data["label_by_field"])
    hand_set = collect_hand_set_markers(model_fields, env_io, parsed)
    pilots = collect_write_endpoints("api/pilots_api.py")
    other_apis = [
        collect_write_endpoints(p)
        for p in ("api/control_api.py", "api/state_api.py", "api/data_api.py", "api/metrics_api.py")
    ]
    reads = collect_read_forms(model_fields)

    return {
        # No `repo_root` here on purpose: it used to bake in an ABSOLUTE path to
        # whichever checkout (often a `.claude/worktrees/...` clone) generated
        # the file, which made the committed artifact worktree-dependent and
        # its diffs noisy across machines. Nothing in the tree reads it.
        "meta": {
            "git_commit": commit,
            "generator": "scripts/measure_settings_census.py",
        },
        "field_types": types_data,
        "settings_source": {
            "fields_without_source_line": parsed["fields_without_source_line"],
            "field_lines": parsed["field_lines"],
        },
        "env_io_lists": env_lists,
        "live_patchable_keys": {
            "source": "gui/ai_control_center.py::LIVE_PATCHABLE_KEYS",
            "count": len(live_patchable),
            "keys": live_patchable,
            "all_are_real_fields": sorted(set(live_patchable) - set(model_fields)) == [],
            "not_real_fields": sorted(set(live_patchable) - set(model_fields)),
            "error": live_patchable_error,
        },
        "partition": partition,
        "secret_sanity": secret_sanity,
        "hand_set_markers": hand_set,
        "write_endpoints_pilots_api": pilots,
        "write_endpoints_other_apis": other_apis,
        "read_forms": reads,
    }


def print_summary(data: Dict[str, Any]) -> None:
    ft = data["field_types"]
    el = data["env_io_lists"]
    pa = data["partition"]
    ss = data["secret_sanity"]
    hs = data["hand_set_markers"]
    we = data["write_endpoints_pilots_api"]
    rf = data["read_forms"]

    p = print
    p("=" * 78)
    p(f"SETTINGS FIELD CENSUS  @ {data['meta']['git_commit'][:12]}")
    p("=" * 78)

    p("\n[1] FIELD TYPES")
    p(f"  Settings.model_fields ............. {ft['total_fields']}")
    for label, n in ft["counts_by_label"].items():
        p(f"    {label:<46} {n}")
    p(f"  other/unhandled ................... {ft['other_unhandled_count']}")
    if ft["other_unhandled_fields"]:
        for n in ft["other_unhandled_fields"]:
            p(f"      ! {n} :: {ft['label_by_field'][n]}")
    p(f"  fields ending in _ENABLED ......... {ft['enabled_suffix_count']}")

    p("\n[2] gui/env_io.py LISTS")
    p(f"  ALLOWED_KEYS ...................... {el['allowed_keys_len']} "
      f"(unique {el['allowed_keys_unique_len']}, "
      f"{el['allowed_keys_duplicate_total_extra']} duplicate entries)")
    if el["allowed_keys_duplicates"]:
        for k, c in el["allowed_keys_duplicates"].items():
            p(f"      dup x{c}: {k}")
    # Every value printed below the "SECRET_KEYS"/"phantom_secret_keys"/etc. labels
    # in this function is a Settings.model_fields FIELD NAME (a Python identifier
    # like "FRED_API_KEY"), never a real credential value -- this script never
    # instantiates Settings() or reads .env, so no actual secret material ever
    # exists in memory here. CodeQL's py/clear-text-logging-sensitive-data query
    # flags these heuristically on the "secret"-shaped variable/label names alone;
    # the lgtm suppressions below are a deliberate, reviewed false-positive
    # dismissal, not a statement that real secrets are safe to log.
    p(f"  SECRET_KEYS ....................... {el['secret_keys_len']} "  # lgtm[py/clear-text-logging-sensitive-data]
      f"(unique {el['secret_keys_unique_len']})")
    p(f"  _JSON_KEYS ........................ {el['json_keys_len']}")
    p(f"  EXCLUDED_FROM_GUI ................. {el['excluded_from_gui_len']}")
    p(f"  ALLOWED n SECRET overlap .......... {len(el['allowed_and_secret_overlap'])}")

    p("\n[3] PARTITION")
    for k, v in pa["counts"].items():
        p(f"  {k:<20} {v}")
    p(f"  of UNCLASSIFIED, covered by EXCLUDED_FROM_GUI: "
      f"{len(pa['unclassified_covered_by_excluded_from_gui'])}")
    p(f"  of UNCLASSIFIED, covered NOWHERE:              "
      f"{len(pa['unclassified_not_covered_anywhere'])}")
    for n in pa["unclassified_not_covered_anywhere"]:
        p(f"      ! {n}")

    p("\n[4] SECRET_KEYS SANITY")
    p(f"  phantom SECRET_KEYS entries ....... {ss['phantom_count']}")  # lgtm[py/clear-text-logging-sensitive-data]
    for n in ss["phantom_secret_keys"]:
        p(f"      ! {n}")  # lgtm[py/clear-text-logging-sensitive-data] -- field NAME, not its value
    p(f"  credential-pattern matches ........ "
      f"{len(ss['pattern_matches_protected'])} protected / "
      f"{len(ss['pattern_matches_unprotected'])} not in SECRET_KEYS")
    p(f"  REAL GAPS (str-shaped, unprotected) {ss['pattern_real_gap_count']}")  # lgtm[py/clear-text-logging-sensitive-data]
    for r in ss["pattern_real_gaps"]:
        p(f"      !! {r['field']} :: {r['type']} (in ALLOWED_KEYS={r['in_allowed_keys']})")  # lgtm[py/clear-text-logging-sensitive-data]
    p(f"  wide-pattern extra gaps ........... {ss['wide_pattern_extra_gap_count']}")  # lgtm[py/clear-text-logging-sensitive-data]
    for r in ss["wide_pattern_extra_gaps"]:
        p(f"      ? {r['field']} :: {r['type']} (in ALLOWED_KEYS={r['in_allowed_keys']})")  # lgtm[py/clear-text-logging-sensitive-data]

    p("\n[5] HAND-SET-ONLY MARKERS IN settings.py")
    p(f"  fields carrying a marker .......... {hs['marked_field_count']}")
    p(f"  markers CONTRADICTED by ALLOWED_KEYS {hs['contradiction_count']}")
    for r in hs["contradictions"]:
        p(f"      ! {r['field']} (settings.py:{r['settings_py_line']}) IS in ALLOWED_KEYS")

    p("\n[6] WRITE ENDPOINTS - api/pilots_api.py")
    p(f"  PUT/POST/PATCH/DELETE routes ...... {we['total_write_method_routes']}")
    p(f"  routes that mutate settings ....... {we['mutating_settings_routes']}")
    p(f"    ... write .env .................. {we['env_writing_routes']}")
    p(f"    ... live in-process setattr ..... {we['live_setattr_routes']}")
    p(f"    ... live push to daemon process . {we['live_daemon_push_routes']}")
    p(f"  routes declaring an `applies` ..... {we['routes_declaring_applies']}")
    for e in we["endpoints"]:
        if e["writes_env"] or e["live_setattr"] or e["live_daemon_push"]:
            p(f"      {e['method']:<6} {e['route']:<34} {e['function']} "
              f"(L{e['line']}) env={int(e['writes_env'])} setattr={int(e['live_setattr'])} "
              f"push={int(e['live_daemon_push'])} applies={e['applies_values'] or '-'}")
    lp = data["live_patchable_keys"]
    p(f"  LIVE_PATCHABLE_KEYS (in-process) .. {lp['count']} "
      f"(all real fields: {lp['all_are_real_fields']})")
    for other in data["write_endpoints_other_apis"]:
        if other.get("mutating_settings_routes"):
            p(f"  [also] {other['file']}: {other['mutating_settings_routes']} mutating route(s)")

    p("\n[7] READ FORMS (production code only)")
    p(f"  files scanned ..................... {rf['files_scanned']}")
    p(f"  files that failed to parse ........ {rf['files_unparseable_count']}")
    for u in rf["files_unparseable"]:
        p(f"      ! {u['file']}: {u['error']}")
    p(f"  distinct settings aliases resolved  {rf['distinct_settings_alias_count']}")
    p(f"  (a) settings.KEY .................. {rf['form_a_attribute']['total_reads']} reads, "
      f"{rf['form_a_attribute']['distinct_fields']} distinct fields")
    p(f"  (b) getattr(settings,\"KEY\",d) ..... {rf['form_b_getattr_literal']['total_reads']} reads, "
      f"{rf['form_b_getattr_literal']['distinct_fields']} distinct fields")
    p(f"  (c) getattr(settings, <var>) ...... {rf['form_c_getattr_dynamic']['total_sites']} sites")
    for s in rf["form_c_getattr_dynamic"]["sites"]:
        p(f"      - {s['file']}:{s['line']}  {s['expr'][:90]}")
    p(f"  (d) os.environ/getenv(\"KEY\") ...... {rf['form_d_os_environ']['total_reads']} reads, "
      f"{rf['form_d_os_environ']['distinct_fields']} distinct fields")
    p(f"  fields reached by ANY form ........ {rf['fields_reached_by_any_form']}")
    p(f"  fields reachable ONLY via (b)/(d) . {rf['fields_only_via_b_or_d_count']}")
    for n in rf["fields_only_via_b_or_d"]:
        via = []
        if n in rf["form_b_getattr_literal"]["counts"]:
            via.append("b")
        if n in rf["form_d_os_environ"]["counts"]:
            via.append("d")
        p(f"      - {n}  (via {'+'.join(via)})")
    p(f"  fields with NO static read ........ {rf['fields_no_static_read_count']}")
    for r in rf["fields_no_static_read_detail"]:
        tag = ("likely read dynamically via a name literal"
               if r["likely_dynamic_read"] else "no name literal either")
        p(f"      - {r['field']}  ({tag}"
          + (f": {', '.join(r['name_literal_sites'][:2])})" if r["likely_dynamic_read"] else ")"))
    p("")


# ---------------------------------------------------------------------------
# Markdown rendering (generated -- never hand-edited)
# ---------------------------------------------------------------------------

def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def render_markdown(data: Dict[str, Any]) -> str:
    ft = data["field_types"]
    el = data["env_io_lists"]
    pa = data["partition"]
    ss = data["secret_sanity"]
    hs = data["hand_set_markers"]
    we = data["write_endpoints_pilots_api"]
    rf = data["read_forms"]
    commit = data["meta"]["git_commit"]

    L: List[str] = []
    a = L.append

    a("# Settings field census")
    a("")
    a("> **Generated file — do not hand-edit.** Every number below is produced by")
    a("> `scripts/measure_settings_census.py` and re-derived on each run. Regenerate with:")
    a("> `python3 scripts/measure_settings_census.py --write`")
    a("")
    a(f"- Measured at commit: `{commit}`")
    a(f"- Machine-readable companion: [`settings_field_census.json`](settings_field_census.json)")
    a("- Prose triage of these findings: [`settings_partition_notes.md`](settings_partition_notes.md)")
    a("")
    a("This is a point-in-time snapshot of `settings.Settings` and every mechanism that can")
    a("currently change a setting. It exists so that later work (a static liveness classifier,")
    a("a key-partition design) can build on measured numbers instead of re-deriving them.")
    a("")

    # 1
    a("## 1. Field-type breakdown")
    a("")
    a(f"`len(Settings.model_fields)` = **{ft['total_fields']}**")
    a("")
    a(_md_table(
        ["Annotation", "Count"],
        [[f"`{lab}`", n] for lab, n in ft["counts_by_label"].items()],
    ))
    a("")
    a(f"Fields whose name ends in `_ENABLED`: **{ft['enabled_suffix_count']}**")
    a("")
    a(f"Distinct `dict[...]` shapes: **{len(ft['dict_shapes'])}**")
    if ft["dict_shapes"]:
        a("")
        a(_md_table(["dict shape", "Count"], [[f"`{k}`", v] for k, v in ft["dict_shapes"].items()]))
    a("")
    if ft["other_unhandled_fields"]:
        a(f"### other/unhandled bucket — **{ft['other_unhandled_count']}** field(s)")
        a("")
        a("A future kind-derivation switch needs an explicit branch for each of these:")
        a("")
        a(_md_table(
            ["Field", "Annotation"],
            [[f"`{n}`", f"`{ft['label_by_field'][n]}`"] for n in ft["other_unhandled_fields"]],
        ))
    else:
        a("**other/unhandled bucket: 0 fields.** Every field falls into a recognised kind, so a")
        a("kind-derivation switch over the categories above is currently total.")
    a("")

    # 2
    a("## 2. `gui/env_io.py` list sizes")
    a("")
    a(_md_table(
        ["Name", "len()", "len(set())", "Note"],
        [
            ["`ALLOWED_KEYS`", el["allowed_keys_len"], el["allowed_keys_unique_len"],
             f"{el['allowed_keys_duplicate_total_extra']} duplicate entries "
             f"({'STILL PRESENT' if el['allowed_keys_duplicate_total_extra'] else 'clean'})"],
            ["`SECRET_KEYS`", el["secret_keys_len"], el["secret_keys_unique_len"],
             f"{el['secret_keys_len'] - el['secret_keys_unique_len']} duplicate entries"],
            ["`_JSON_KEYS`", el["json_keys_len"], el["json_keys_len"], "frozenset"],
            ["`EXCLUDED_FROM_GUI`", el["excluded_from_gui_len"], el["excluded_from_gui_len"],
             "frozenset; third classification bucket"],
        ],
    ))
    a("")
    if el["allowed_keys_duplicates"]:
        a(f"### `ALLOWED_KEYS` duplicate entries (**{len(el['allowed_keys_duplicates'])}** distinct keys repeated)")
        a("")
        a("Reported, **not fixed** — this census is measurement-only.")
        a("")
        a(_md_table(["Key", "Occurrences"],
                    [[f"`{k}`", v] for k, v in el["allowed_keys_duplicates"].items()]))
        a("")
    a(f"`ALLOWED_KEYS ∩ SECRET_KEYS` overlap: **{len(el['allowed_and_secret_overlap'])}** "
      f"{'— ' + ', '.join('`' + k + '`' for k in el['allowed_and_secret_overlap']) if el['allowed_and_secret_overlap'] else '(clean — no key is both writable and secret)'}")
    a("")

    # 3
    a("## 3. The partition")
    a("")
    a("Every `Settings.model_fields` name classified into exactly one bucket.")
    a("")
    a(_md_table(
        ["Bucket", "Count", "Definition"],
        [
            ["`SECRET`", pa["counts"]["SECRET"], "in `env_io.SECRET_KEYS`"],
            ["`IN_ALLOWED_KEYS`", pa["counts"]["IN_ALLOWED_KEYS"], "in `env_io.ALLOWED_KEYS`"],
            ["`UNCLASSIFIED`", pa["counts"]["UNCLASSIFIED"], "in neither"],
        ],
    ))
    a("")
    a(f"Of the {pa['counts']['UNCLASSIFIED']} `UNCLASSIFIED` fields, "
      f"**{len(pa['unclassified_covered_by_excluded_from_gui'])}** are accounted for by the third "
      f"`EXCLUDED_FROM_GUI` bucket and "
      f"**{len(pa['unclassified_not_covered_anywhere'])}** are accounted for nowhere.")
    a("")
    a("### Every `UNCLASSIFIED` field")
    a("")
    a(_md_table(
        ["Field", "settings.py", "In `EXCLUDED_FROM_GUI`", "What it is"],
        [
            [f"`{r['field']}`",
             f"L{r['settings_py_line']}" if r["settings_py_line"] else "-",
             "yes" if r["in_excluded_from_gui"] else "**no**",
             r["description"] or "_(no Field description)_"]
            for r in pa["unclassified_detail"]
        ],
    ))
    a("")

    # 4
    a("## 4. `SECRET_KEYS` sanity check")
    a("")
    a(f"**Phantom entries** (in `SECRET_KEYS` but not a real `model_fields` name): "
      f"**{ss['phantom_count']}**")
    if ss["phantom_secret_keys"]:
        a("")
        for n in ss["phantom_secret_keys"]:
            a(f"- `{n}`")
    a("")
    a(f"### Credential-shaped name sweep — pattern `{ss['pattern']}` (case-insensitive)")
    a("")
    a(f"- matches already in `SECRET_KEYS`: **{len(ss['pattern_matches_protected'])}**")
    a(f"- matches NOT in `SECRET_KEYS`: **{len(ss['pattern_matches_unprotected'])}**")
    a(f"- of those, genuinely credential-shaped (`str` / `Optional[str]`): "
      f"**{ss['pattern_real_gap_count']}**")
    a("")
    a("A field typed `int` / `float` / `bool` cannot hold secret material regardless of a name")
    a("match, so those are listed as filtered false positives rather than gaps.")
    a("")
    if ss["pattern_matches_unprotected"]:
        a(_md_table(
            ["Field", "Type", "In `ALLOWED_KEYS`", "Verdict"],
            [
                [f"`{r['field']}`", f"`{r['type']}`",
                 "yes" if r["in_allowed_keys"] else "no",
                 "**GAP — string-shaped credential name not in SECRET_KEYS**"
                 if r["flagged_as_gap"] else "false positive (non-string type)"]
                for r in ss["pattern_matches_unprotected"]
            ],
        ))
        a("")
    a(f"### Supplementary wider sweep — pattern `{ss['wide_pattern']}`")
    a("")
    a("Not requested by the brief, run because the primary pattern misses several credential")
    a("shapes by construction. Extra string-shaped, unprotected matches: "
      f"**{ss['wide_pattern_extra_gap_count']}**")
    if ss["wide_pattern_extra_gaps"]:
        a("")
        a(_md_table(
            ["Field", "Type", "In `ALLOWED_KEYS`"],
            [[f"`{r['field']}`", f"`{r['type']}`", "yes" if r["in_allowed_keys"] else "no"]
             for r in ss["wide_pattern_extra_gaps"]],
        ))
    a("")

    # 5
    a("## 5. Hand-set-only write-gate flags")
    a("")
    a("Fields whose `settings.py` comment or `Field(description=...)` claims they are")
    a("deliberately never GUI-writable, cross-referenced against **actual** current")
    a("`ALLOWED_KEYS` membership.")
    a("")
    a(f"- fields carrying such a marker: **{hs['marked_field_count']}**")
    a(f"- markers **contradicted** by current `ALLOWED_KEYS` membership: "
      f"**{hs['contradiction_count']}**")
    a("")
    a(_md_table(
        ["Field", "Marker site(s)", "In `ALLOWED_KEYS` now", "In `SECRET_KEYS`", "Claim holds"],
        [
            [f"`{r['field']}`",
             ", ".join(f"`settings.py:{m['line']}`" for m in r["marker_sites"]),
             "**yes**" if r["currently_in_allowed_keys"] else "no",
             "yes" if r["currently_in_secret_keys"] else "no",
             "yes" if r["comment_claim_holds"] else "**NO — contradicted**"]
            for r in hs["marked_fields"]
        ],
    ))
    a("")

    # 6
    a("## 6. Live-write endpoint inventory — `api/pilots_api.py`")
    a("")
    a(f"- `PUT`/`POST`/`PATCH`/`DELETE` routes total: **{we['total_write_method_routes']}**")
    a(f"- routes that mutate a setting: **{we['mutating_settings_routes']}**")
    a("")
    a("Three *distinct* mutation mechanisms exist — a liveness model that only considers")
    a("\"this process's singleton\" would miss two of them:")
    a("")
    a(_md_table(
        ["Mechanism", "Routes", "Effect"],
        [
            ["`.env` write via `env_io.write_*`", we["env_writing_routes"],
             "durable; takes effect on the **next** process launch"],
            ["in-process `setattr(settings, ...)`", we["live_setattr_routes"],
             "patches THIS process's singleton only"],
            ["push to the daemon via `daemon_client.set_*`", we["live_daemon_push_routes"],
             "HTTP call into a **separately running** daemon process"],
        ],
    ))
    a("")
    a(f"Routes declaring an `applies` value in their response: "
      f"**{we['routes_declaring_applies']}** of {we['mutating_settings_routes']}.")
    a("")
    a("Resolution is AST-based and follows one level of indirection: a handler that only calls a")
    a("module-level helper which itself calls `env_io.write_*` (or builds the response carrying")
    a("`applies`) is still attributed correctly. `applies` values are resolved through `Constant`,")
    a("`IfExp`, and locally-bound `Name` expressions — a Constant-only check reports")
    a("`(none)` for most of this table.")
    a("")
    writers = [e for e in we["endpoints"]
               if e["writes_env"] or e["live_setattr"] or e["live_daemon_push"]]
    a(_md_table(
        ["Route", "Method", "Handler", "Line", "`.env`", "`setattr`", "daemon push",
         "`applies` claims"],
        [
            [f"`{e['route']}`", e["method"], f"`{e['function']}`", e["line"],
             "yes" if e["writes_env"] else "no",
             "yes" if e["live_setattr"] else "no",
             "yes" if e["live_daemon_push"] else "no",
             ", ".join(f"`{v}`" for v in e["applies_values"]) or "_(none)_"]
            for e in writers
        ],
    ))
    a("")
    lp = data["live_patchable_keys"]
    a(f"### Existing in-process hot-reload beachhead — `{lp['source']}`")
    a("")
    a(f"`PUT /llm/setting` is the only route that patches the live singleton, and it does so only")
    a(f"for the **{lp['count']}** keys on this allowlist (all of which are real `Settings` fields:")
    a(f"`{lp['all_are_real_fields']}`). Everything else in the table above is `.env`-only.")
    a("")
    if lp["keys"]:
        a("```")
        for k in lp["keys"]:
            a(k)
        a("```")
        a("")
    if we["helper_functions_writing_env"]:
        a("Module-level helpers in this file that write `.env` directly: "
          + ", ".join(f"`{h}`" for h in we["helper_functions_writing_env"]))
        a("")
    other_writers = [o for o in data["write_endpoints_other_apis"] if o.get("mutating_settings_routes")]
    a("### Other `api/*.py` modules (supplementary — not requested, included for the "
      "\"how many ways can a setting change\" count)")
    a("")
    if other_writers:
        a(_md_table(
            ["File", "Mutating routes", "Writes `.env`", "Live `setattr`"],
            [[f"`{o['file']}`", o["mutating_settings_routes"], o["env_writing_routes"],
              o["live_setattr_routes"]] for o in other_writers],
        ))
    else:
        a("None of `api/control_api.py`, `api/state_api.py`, `api/data_api.py`, or")
        a("`api/metrics_api.py` contains a route that writes `.env` or does a live `setattr`.")
    a("")

    # 7
    a("## 7. Read-form census")
    a("")
    a(f"Scope: **{rf['files_scanned']}** production `.py` files "
      f"(excludes `tests/`, `test_*.py`, `conftest.py`, `.venv/`, `webapp/`, `node_modules/`).")
    a("")
    a(f"Files that could not be parsed: **{rf['files_unparseable_count']}**"
      + ("" if not rf["files_unparseable"] else
         " — " + ", ".join(f"`{u['file']}` ({u['error']})" for u in rf["files_unparseable"])))
    a("")
    a(f"The singleton is bound under **{rf['distinct_settings_alias_count']}** distinct local names")
    a("across the tree, which is why this is an AST pass and not a grep:")
    a("")
    a("```")
    a(", ".join(rf["distinct_settings_aliases_found"]))
    a("```")
    a("")
    a(_md_table(
        ["Form", "Total reads", "Distinct fields reached"],
        [
            ["(a) `settings.KEY`", rf["form_a_attribute"]["total_reads"],
             rf["form_a_attribute"]["distinct_fields"]],
            ["(b) `getattr(settings, \"KEY\", default)`", rf["form_b_getattr_literal"]["total_reads"],
             rf["form_b_getattr_literal"]["distinct_fields"]],
            ["(c) `getattr(settings, <var>)` (dynamic)",
             f"{rf['form_c_getattr_dynamic']['total_sites']} sites", "n/a — key not statically known"],
            ["(d) `os.environ` / `os.getenv(\"KEY\")`", rf["form_d_os_environ"]["total_reads"],
             rf["form_d_os_environ"]["distinct_fields"]],
        ],
    ))
    a("")
    a(f"Fields reached by at least one form: **{rf['fields_reached_by_any_form']}** of "
      f"{ft['total_fields']}.")
    a("")
    a(f"### Fields with NO statically-attributable read — **{rf['fields_no_static_read_count']}**")
    a("")
    a("**These are not necessarily dead.** A field whose name is passed as a *string literal* to a")
    a("factory that then does a dynamic `getattr` is read at runtime while being invisible to every")
    a("form above. The name-literal column is the evidence: a non-empty value means the key is")
    a("referenced by name somewhere and is probably read dynamically.")
    a("")
    a(_md_table(
        ["Field", "Name-literal sites", "Verdict"],
        [
            [f"`{r['field']}`",
             ", ".join(f"`{s}`" for s in r["name_literal_sites"]) or "_none_",
             "likely read dynamically" if r["likely_dynamic_read"]
             else "no read and no name reference found"]
            for r in rf["fields_no_static_read_detail"]
        ],
    ))
    a("")
    a(f"### Fields reachable ONLY via form (b) or (d), never via (a) — **{rf['fields_only_via_b_or_d_count']}**")
    a("")
    a("These are exactly the keys an attribute-only static analysis would miss entirely.")
    a("")
    if rf["fields_only_via_b_or_d"]:
        a(_md_table(
            ["Field", "Reached via", "(b) count", "(d) count"],
            [
                [f"`{n}`",
                 "+".join(x for x in (
                     "b" if n in rf["form_b_getattr_literal"]["counts"] else "",
                     "d" if n in rf["form_d_os_environ"]["counts"] else "") if x),
                 rf["form_b_getattr_literal"]["counts"].get(n, 0),
                 rf["form_d_os_environ"]["counts"].get(n, 0)]
                for n in rf["fields_only_via_b_or_d"]
            ],
        ))
    else:
        a("_None._")
    a("")
    a(f"### Dynamic `getattr` sites (form c) — **{rf['form_c_getattr_dynamic']['total_sites']}**")
    a("")
    a("The key is not a literal, so no static analysis can attribute these to a field name.")
    a("")
    if rf["form_c_getattr_dynamic"]["sites"]:
        a(_md_table(
            ["Site", "Expression"],
            [[f"`{s['file']}:{s['line']}`", f"`{s['expr'][:120]}`"]
             for s in rf["form_c_getattr_dynamic"]["sites"]],
        ))
    else:
        a("_None._")
    a("")
    if rf["form_d_os_environ"]["counts"]:
        a(f"### Fields read via `os.environ` (form d) — {rf['form_d_os_environ']['distinct_fields']} field(s)")
        a("")
        a("`.env` is loaded into the `Settings` model directly by pydantic-settings; it is only")
        a("copied into the real `os.environ` when something calls `load_dotenv()`. A field read")
        a("this way therefore reads a *different source* than `settings.KEY` does — see CLAUDE.md's")
        a("\"Credential reads MUST go through `settings.X`\" convention for the class of bug this causes.")
        a("")
        a(_md_table(
            ["Field", "Reads", "Also read via (a)"],
            [[f"`{k}`", v, "yes" if k in rf["form_a_attribute"]["counts"] else "**no**"]
             for k, v in rf["form_d_os_environ"]["counts"].items()],
        ))
        a("")
    a("---")
    a("")
    a("_Regenerate: `python3 scripts/measure_settings_census.py --write`_")
    a("")
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2] if __doc__ else None)
    ap.add_argument("--write", action="store_true",
                    help="regenerate docs/settings_field_census.{json,md}")
    ap.add_argument("--json", action="store_true", help="print the raw JSON payload to stdout")
    ap.add_argument("--quiet", action="store_true", help="suppress the human summary")
    args = ap.parse_args(argv)

    data = collect_census()
    # `data` is pure structural census metadata (field names, counts, line numbers)
    # from Settings.model_fields and an AST parse of settings.py -- this function
    # never instantiates Settings() or reads .env, so no real secret VALUE is ever
    # present in `data`. It happens to contain a "secret_keys_sanity" section whose
    # payload is itself a list of field NAME strings (e.g. "FRED_API_KEY"), which is
    # what trips CodeQL's naming-heuristic clear-text-logging query below.

    if not args.quiet and not args.json:
        print_summary(data)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=False))  # lgtm[py/clear-text-logging-sensitive-data]
    if args.write:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        JSON_OUT.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")  # lgtm[py/clear-text-logging-sensitive-data]
        MD_OUT.write_text(render_markdown(data), encoding="utf-8")
        if not args.quiet:
            print(f"wrote {JSON_OUT.relative_to(_REPO_ROOT)}")
            print(f"wrote {MD_OUT.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    # Venv re-exec + .env loading -- placed here (not at module top) because
    # this module is designed to also be imported as a library by a later
    # analysis pass, and a module-top call would fire the re-exec check (and,
    # in the wrong environment, spawn a subprocess and sys.exit()) on every
    # such import rather than only when this file is the CLI entry point.
    # See scripts/_bootstrap.py's module docstring for the full rationale.
    from scripts._bootstrap import bootstrap
    bootstrap()
    raise SystemExit(main())

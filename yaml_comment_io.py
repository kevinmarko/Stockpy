"""
InvestYo Quant Platform — comment-preserving YAML writes
========================================================
PyYAML does not preserve comments.  Any writer that does
``yaml.safe_load → mutate → yaml.safe_dump`` therefore erodes a file's
documentation a little more on every write, silently and with no error.

This repo has been bitten by that at least three times on two different files
(``ml/registry.yaml``'s schema header, twice; ``watch_rules.yaml``'s 76-line
rule-schema header, which a single ``update_watch_rules`` MCP call collapsed
from 4246 bytes to 206).  ``ruamel.yaml`` — the usual round-trip answer — is not
a dependency of this repo and is deliberately not being added.

So this module provides the two primitives a comment-safe writer needs:

* :func:`leading_comment_block` — read a file's own header back off disk, so a
  writer never re-emits a *hardcoded copy* that can drift out of sync (exactly
  how ``ml/registry.yaml`` lost four lines in a 2026-09-04 retrain).
* :func:`splice_sequence_section` — apply an add/remove/update to a top-level
  YAML *sequence* by re-serializing only the list items that actually changed,
  carrying every other byte through verbatim.

``ml/registry_io.py`` holds the mapping-shaped equivalent for its own
``models:`` block; it imports :func:`leading_comment_block` from here rather
than keeping a second copy.

Both splicers follow the same two rules:

1. **Never write a partially-applied result.** Anything outside what the splicer
   can apply exactly returns ``None``, and the caller falls back to a full dump.
2. **Verify before trusting.** The spliced text is re-parsed and compared to the
   intended state before it is returned (CONSTRAINT #6 — fail closed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

__all__ = [
    "dump_yaml",
    "leading_comment_block",
    "splice_sequence_section",
    "write_yaml_preserving_header",
]


def dump_yaml(obj: Any, *, width: int = 100) -> str:
    """Canonical PyYAML serialization for this repo's config files."""
    return yaml.safe_dump(
        obj,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=width,
    )


def leading_comment_block(text: str) -> Optional[str]:
    """Return a file's leading run of comment/blank lines, verbatim.

    Returns ``None`` when there is no leading comment block at all, so a caller
    can fall back to a bootstrap header rather than emitting nothing.
    """
    lines = text.splitlines(keepends=True)
    end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            end = i + 1
            continue
        break
    block = lines[:end]
    # Trim trailing blanks — the caller re-adds the single separator blank.
    while block and block[-1].strip() == "":
        block.pop()
    if not block or not any(ln.lstrip().startswith("#") for ln in block):
        return None
    out = "".join(block)
    return out if out.endswith("\n") else out + "\n"


def _trailing_comment_run(lines: list[str], start: int, end: int) -> int:
    """Index where this block's trailing blank/comment run begins.

    Blank lines and comments at the end of a block belong to whatever comes
    next (a separator, a note introducing the following entry, or a file-level
    footer).  Splitting them off means they survive even when the block itself
    is rewritten or removed entirely.
    """
    idx = end
    while idx - 1 >= start and (
        lines[idx - 1].strip() == "" or lines[idx - 1].lstrip().startswith("#")
    ):
        idx -= 1
    return idx


def _find_sequence_blocks(
    lines: list[str], section_key: str
) -> Optional[tuple[int, int, list[tuple[int, int]]]]:
    """Locate ``<section_key>:`` and the line span of each ``- `` item under it."""
    head = None
    for i, line in enumerate(lines):
        if line.rstrip("\n").rstrip() == f"{section_key}:":
            head = i
            break
    if head is None:
        return None

    section_end = len(lines)
    for j in range(head + 1, len(lines)):
        stripped = lines[j].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not lines[j].startswith(" "):
            section_end = j
            break

    starts: list[int] = []
    indent: Optional[int] = None
    for j in range(head + 1, section_end):
        line = lines[j]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            continue
        this_indent = len(line) - len(stripped)
        if indent is None:
            indent = this_indent
        if this_indent != indent:
            continue
        starts.append(j)

    if not starts:
        return None
    spans = [
        (s, starts[k + 1] if k + 1 < len(starts) else section_end)
        for k, s in enumerate(starts)
    ]
    return head, section_end, spans


def _serialize_sequence_item(item: Any, indent: int, width: int) -> str:
    """Serialize one list item as a ``- `` block at the given indent."""
    dumped = dump_yaml([item], width=width)
    pad = " " * indent
    return "".join(
        (pad + ln) if ln.strip() else ln for ln in dumped.splitlines(keepends=True)
    )


def splice_sequence_section(
    text: str,
    data: dict,
    section_key: str,
    *,
    width: int = 100,
) -> Optional[str]:
    """Apply ``data`` onto ``text`` where ``data[section_key]`` is a list.

    Handles the add / remove / update shapes real callers produce: the new list
    must be the old list with items removed and/or items appended.  Kept items
    are copied through **verbatim** (their own formatting and any comments
    attached to them), so only genuinely-new items are ever re-serialized.

    Returns ``None`` — never a partial result — when the file's shape or the
    nature of the change is outside that, so the caller can fall back safely.
    """
    try:
        current = yaml.safe_load(text)
    except Exception:
        return None
    if not isinstance(current, dict) or not isinstance(data, dict):
        return None
    if set(current.keys()) != set(data.keys()):
        return None
    for key in data:
        if key != section_key and current[key] != data[key]:
            return None

    old_items, new_items = current.get(section_key), data.get(section_key)
    if not isinstance(old_items, list) or not isinstance(new_items, list):
        return None
    if old_items == new_items:
        return text  # nothing to change — keep the file byte-identical

    lines = text.splitlines(keepends=True)
    located = _find_sequence_blocks(lines, section_key)
    if located is None:
        return None
    head, section_end, spans = located
    if len(spans) != len(old_items):
        return None  # could not account for every item — do not guess

    indent = len(lines[spans[0][0]]) - len(lines[spans[0][0]].lstrip())

    out: list[str] = list(lines[: spans[0][0]])
    j = 0
    for i, (start, end) in enumerate(spans):
        split = _trailing_comment_run(lines, start, end)
        content, trailing = lines[start:split], lines[split:end]
        if j < len(new_items) and old_items[i] == new_items[j]:
            out.extend(content)  # unchanged — verbatim, comments and all
            j += 1
        elif old_items[i] in new_items[j:]:
            return None  # a reorder, not an add/remove — outside our contract
        # else: this item was removed; its content is dropped
        out.extend(trailing)  # separators/footers survive either way

    for item in new_items[j:]:
        out.append(_serialize_sequence_item(item, indent, width))

    if not new_items:
        # A bare `key:` with nothing under it parses back as None, not [], so an
        # emptied sequence has to be written explicitly. (Without this the
        # verify step below correctly refuses the write — but declining means
        # falling back to a full dump, which loses the body comments we are
        # here to protect.)
        out[head] = f"{section_key}: []\n"

    out.extend(lines[section_end:])
    spliced = "".join(out)
    if not spliced.endswith("\n"):
        spliced += "\n"

    # Verify before trusting (CONSTRAINT #6).
    try:
        if yaml.safe_load(spliced) != data:
            return None
    except Exception:
        return None
    return spliced


def write_yaml_preserving_header(
    data: dict,
    path: Path,
    *,
    bootstrap_header: Optional[str] = None,
    width: int = 100,
) -> None:
    """Full YAML dump that still re-emits the file's REAL on-disk header.

    This is the fallback for when a splice declines.  It preserves the leading
    comment block only — comments further down are lost, which is why callers
    should try a splice first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header: Optional[str] = None
    if path.exists():
        try:
            header = leading_comment_block(path.read_text(encoding="utf-8"))
        except Exception:
            header = None
    if header is None:
        header = bootstrap_header

    with open(path, "w", encoding="utf-8") as f:
        if header:
            f.write(header)
            f.write("\n")
        f.write(dump_yaml(data, width=width))

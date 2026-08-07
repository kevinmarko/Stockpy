#!/usr/bin/env bash
# PostToolUse hook (Edit|Write) -- keeps CLAUDE.md and AGENTS.md at the repo
# root byte-identical mirrors of each other.
#
# THE DRIFT PROBLEM THIS FIXES: CLAUDE.md and AGENTS.md are meant to be exact
# mirrors -- AGENTS.md's own first line is literally "# CLAUDE.md", proving
# the intent -- but nothing enforced that, and the two files had already
# drifted by one real bullet before this hook existed. Whichever of the two
# was just edited is copied onto the other so they can never silently drift
# apart again.
#
# WHY A PLAIN FILESYSTEM `cp` AND NOT THE EDIT/WRITE TOOL: writing the
# destination via the Edit/Write tool would itself fire this same PostToolUse
# hook again (Edit|Write matcher), recursively re-triggering. A bare `cp`
# bypasses the tool layer entirely so there's nothing to re-trigger.
set -uo pipefail

input=""
if [ ! -t 0 ]; then
  if IFS= read -r -t 1 first_line; then
    input="$(printf '%s\n' "$first_line"; cat)"
  fi
fi
file_path="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' <<<"$input")"

[ -n "$file_path" ] || exit 0

root="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$root" ] || exit 0

name="$(basename "$file_path")"
case "$name" in
  CLAUDE.md)
    source="$root/CLAUDE.md"
    dest="$root/AGENTS.md"
    ;;
  AGENTS.md)
    source="$root/AGENTS.md"
    dest="$root/CLAUDE.md"
    ;;
  *)
    exit 0
    ;;
esac

# Sanity-check the edited file is actually AT the repo root, not some
# nested same-named file elsewhere in the tree (e.g. docs/CLAUDE.md).
dir="$(dirname "$file_path")"
resolved_dir="$(cd "$dir" 2>/dev/null && pwd)"
[ -n "$resolved_dir" ] || exit 0
[ "$resolved_dir" = "$root" ] || exit 0

[ -f "$source" ] || exit 0

# Already identical -- nothing to do.
if diff -q "$source" "$dest" >/dev/null 2>&1; then
  exit 0
fi

cp "$source" "$dest"

jq -n --arg from "$(basename "$source")" --arg to "$(basename "$dest")" \
  '{systemMessage: ("Synced " + $from + " -> " + $to + " to keep the two knowledge-base mirrors identical.")}'

exit 0

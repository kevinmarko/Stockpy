#!/usr/bin/env bash
# Antigravity PostToolUse hook (edit_file|create_file) -- keeps CLAUDE.md and
# AGENTS.md at the repo root as exact mirrors of each other.
#
# CLAUDE.md and AGENTS.md are meant to be byte-identical (AGENTS.md's own
# first line is literally "# CLAUDE.md", proving the intent is exact
# mirroring). Whichever of the two an agent just edited is copied onto the
# other, so the two never drift apart from a single-file edit. This is a
# plain `cp`, not a recursive/generic sync -- it does not retrigger this hook
# itself, since hooks fire on agent tool calls, not on a hook script's own
# filesystem writes.
#
# VERIFIED against a live Antigravity runtime. The exact Antigravity arg schema
# for write_to_file/replace_file_content/multi_replace_file_content uses TargetFile.
# Note: These hooks do not natively intercept file-editing tools in the IDE runtime,
# so this remains policy-only for Antigravity sessions.
set -uo pipefail

input="$(cat)"

candidates="$(printf '%s' "$input" | jq -r '.toolCall.args.TargetFile // empty' 2>/dev/null)"

root="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$root" ] || exit 0

source=""
dest=""

while IFS= read -r c; do
  [ -z "$c" ] && continue

  # Resolve candidate to an absolute path: use as-is if already absolute,
  # otherwise join against the repo root (the most likely base for a
  # relative path an agent tool would report). Pragmatic, not perfect --
  # a false negative (hook doesn't fire) is the safe failure mode here.
  case "$c" in
    /*) resolved="$c" ;;
    *)  resolved="$root/$c" ;;
  esac

  base="$(basename -- "$resolved")"
  dir="$(dirname -- "$resolved")"

  if [ "$base" = "CLAUDE.md" ] && [ "$dir" = "$root" ]; then
    source="$root/CLAUDE.md"
    dest="$root/AGENTS.md"
    break
  fi
  if [ "$base" = "AGENTS.md" ] && [ "$dir" = "$root" ]; then
    source="$root/AGENTS.md"
    dest="$root/CLAUDE.md"
    break
  fi
done <<<"$candidates"

[ -n "$source" ] || exit 0
[ -f "$source" ] || exit 0

if diff -q "$source" "$dest" >/dev/null 2>&1; then
  exit 0
fi

cp "$source" "$dest"
echo "Synced $(basename "$source") -> $(basename "$dest")" >&2

exit 0

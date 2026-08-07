#!/usr/bin/env bash
# PostToolUse hook (Edit|Write) -- keeps .claude/launch.json and
# .gemini/launch.json byte-identical mirrors of each other, the same way
# sync_agent_docs.sh mirrors CLAUDE.md/AGENTS.md.
#
# WHY: .claude/launch.json is a per-repo dev-preview config (which command to
# run, which port) consumed by Claude Code's Browser preview tool. Gemini CLI
# reads the equivalent config from .gemini/launch.json. There's no reason the
# two tools should need separately hand-maintained preview configs for the
# same dev server -- whichever one was just edited is copied onto the other.
#
# WHY A PLAIN FILESYSTEM `cp` AND NOT THE EDIT/WRITE TOOL: writing the
# destination via the Edit/Write tool would itself fire this same PostToolUse
# hook again (Edit|Write matcher), recursively re-triggering. A bare `cp`
# bypasses the tool layer entirely so there's nothing to re-trigger.
#
# Both files are covered by the repo's blanket `*.json` .gitignore rule, so
# this hook only keeps two local, untracked files in sync with each other --
# it has no effect on what gets committed.
set -uo pipefail

input="$(cat)"
file_path="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' <<<"$input")"

[ -n "$file_path" ] || exit 0

root="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$root" ] || exit 0

# Resolve the edited file's directory so we match on repo-root-relative path,
# not just basename -- a nested same-named file elsewhere in the tree (e.g.
# some_tool/launch.json) must not trigger this.
dir="$(dirname "$file_path")"
resolved_dir="$(cd "$dir" 2>/dev/null && pwd)"
[ -n "$resolved_dir" ] || exit 0
name="$(basename "$file_path")"

[ "$name" = "launch.json" ] || exit 0

case "$resolved_dir" in
  "$root/.claude")
    source="$root/.claude/launch.json"
    dest="$root/.gemini/launch.json"
    ;;
  "$root/.gemini")
    source="$root/.gemini/launch.json"
    dest="$root/.claude/launch.json"
    ;;
  *)
    exit 0
    ;;
esac

[ -f "$source" ] || exit 0

mkdir -p "$(dirname "$dest")"

# Already identical -- nothing to do.
if [ -f "$dest" ] && diff -q "$source" "$dest" >/dev/null 2>&1; then
  exit 0
fi

cp "$source" "$dest"

jq -n --arg from "$(basename "$(dirname "$source")")/launch.json" --arg to "$(basename "$(dirname "$dest")")/launch.json" \
  '{systemMessage: ("Synced " + $from + " -> " + $to + " to keep the dev-preview configs identical.")}'

exit 0

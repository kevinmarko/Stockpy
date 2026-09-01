#!/usr/bin/env bash
# Antigravity PostToolUse hook (edit_file|create_file) -- keeps two classes
# of mirrored files in sync:
#   1. CLAUDE.md and AGENTS.md at the repo root.
#   2. .claude/skills/<name>/SKILL.md <-> .agents/skills/<name>/SKILL.md,
#      for the handful of skills confirmed to be intended as exact,
#      byte-identical mirrors (see MIRRORED_SKILL_NAMES below).
#
# CLAUDE.md and AGENTS.md are meant to be byte-identical (AGENTS.md's own
# first line is literally "# CLAUDE.md", proving the intent is exact
# mirroring). Whichever of the two an agent just edited is copied onto the
# other, so the two never drift apart from a single-file edit. The
# `stockpy-master-prompt`/`stockpy-quant-integrity` SKILL.md pair
# (.agents/skills/ vs .claude/skills/) is the identical problem one level
# down -- see docs/known_issues/skill_directory_manual_copy_drift.md for the
# incident this closes. MIRRORED_SKILL_NAMES only covers the two skills
# confirmed to have no deliberate per-platform porting preamble; keep it in
# sync with tests/test_skill_directory_parity.py::EXACT_MIRROR_SKILLS and
# with .claude/hooks/sync_agent_docs.sh's own copy of this list, which is
# the Claude Code side of the same fix. This is a plain `cp`, not a
# recursive/generic sync -- it does not retrigger this hook itself, since
# hooks fire on agent tool calls, not on a hook script's own filesystem
# writes.
#
# VERIFIED against a live Antigravity runtime. The exact Antigravity arg schema
# for write_to_file/replace_file_content/multi_replace_file_content uses TargetFile.
# Note: These hooks do not natively intercept file-editing tools in the IDE runtime,
# so this remains policy-only for Antigravity sessions.
set -uo pipefail

# Keep this in sync with tests/test_skill_directory_parity.py::EXACT_MIRROR_SKILLS
# and .claude/hooks/sync_agent_docs.sh's own copy of this list.
MIRRORED_SKILL_NAMES=(
  "stockpy-master-prompt"
  "stockpy-quant-integrity"
)

input=""
if [ ! -t 0 ]; then
  if IFS= read -r -t 1 first_line; then
    input="$(printf '%s\n' "$first_line"; cat)"
  fi
fi

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
  if [ "$base" = "SKILL.md" ]; then
    skill_name="$(basename -- "$dir")"
    for candidate in "${MIRRORED_SKILL_NAMES[@]}"; do
      [ "$skill_name" = "$candidate" ] || continue
      if [ "$dir" = "$root/.claude/skills/$candidate" ]; then
        source="$root/.claude/skills/$candidate/SKILL.md"
        dest="$root/.agents/skills/$candidate/SKILL.md"
      elif [ "$dir" = "$root/.agents/skills/$candidate" ]; then
        source="$root/.agents/skills/$candidate/SKILL.md"
        dest="$root/.claude/skills/$candidate/SKILL.md"
      fi
      break
    done
    [ -n "$source" ] && break
  fi
done <<<"$candidates"

[ -n "$source" ] || exit 0
[ -f "$source" ] || exit 0
# Never create a brand-new destination file the hook wasn't asked to sync --
# both sides of a real mirror pair already exist in this repo.
[ -f "$dest" ] || exit 0

if diff -q "$source" "$dest" >/dev/null 2>&1; then
  exit 0
fi

cp "$source" "$dest"
echo "Synced $(basename "$source") -> $(basename "$dest")" >&2

exit 0

#!/usr/bin/env bash
# PostToolUse hook (Edit|Write) -- keeps two classes of mirrored files in
# sync:
#   1. CLAUDE.md / AGENTS.md at the repo root.
#   2. .claude/skills/<name>/SKILL.md <-> .agents/skills/<name>/SKILL.md,
#      for the handful of skills confirmed to be intended as exact,
#      byte-identical mirrors (see MIRRORED_SKILL_NAMES below).
#
# THE DRIFT PROBLEM THIS FIXES: CLAUDE.md and AGENTS.md are meant to be exact
# mirrors -- AGENTS.md's own first line is literally "# CLAUDE.md", proving
# the intent -- but nothing enforced that, and the two files had already
# drifted by one real bullet before this hook existed. Whichever of the two
# was just edited is copied onto the other so they can never silently drift
# apart again. The `stockpy-master-prompt`/`stockpy-quant-integrity` SKILL.md
# pair (.agents/skills/ vs .claude/skills/) is the identical problem one
# level down: PR #970 shipped a stale "mirror" nobody diffed before merge,
# and a follow-up sweep found a real, wrong claim drift into a THIRD skill
# (agentic-discovery) the same way -- see
# docs/known_issues/skill_directory_manual_copy_drift.md for the full
# incident writeup. MIRRORED_SKILL_NAMES only covers the two skills
# confirmed to have no deliberate per-platform porting preamble; keep it in
# sync with tests/test_skill_directory_parity.py::EXACT_MIRROR_SKILLS, which
# is the test-level enforcement backstop for any edit this hook doesn't see
# (e.g. one made outside Claude Code's own Edit/Write tools, or from
# Antigravity -- see .agents/hooks/sync_agent_docs.sh for that side).
#
# WHY A PLAIN FILESYSTEM `cp` AND NOT THE EDIT/WRITE TOOL: writing the
# destination via the Edit/Write tool would itself fire this same PostToolUse
# hook again (Edit|Write matcher), recursively re-triggering. A bare `cp`
# bypasses the tool layer entirely so there's nothing to re-trigger.
set -uo pipefail

# Keep this in sync with tests/test_skill_directory_parity.py::EXACT_MIRROR_SKILLS.
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
file_path="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' <<<"$input")"

[ -n "$file_path" ] || exit 0

root="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$root" ] || exit 0

name="$(basename "$file_path")"
dir="$(dirname "$file_path")"
resolved_dir="$(cd "$dir" 2>/dev/null && pwd)"
[ -n "$resolved_dir" ] || exit 0

source=""
dest=""
source_label=""
dest_label=""

case "$name" in
  CLAUDE.md)
    # Sanity-check the edited file is actually AT the repo root, not some
    # nested same-named file elsewhere in the tree (e.g. docs/CLAUDE.md).
    if [ "$resolved_dir" = "$root" ]; then
      source="$root/CLAUDE.md"
      dest="$root/AGENTS.md"
      source_label="CLAUDE.md"
      dest_label="AGENTS.md"
    fi
    ;;
  AGENTS.md)
    if [ "$resolved_dir" = "$root" ]; then
      source="$root/AGENTS.md"
      dest="$root/CLAUDE.md"
      source_label="AGENTS.md"
      dest_label="CLAUDE.md"
    fi
    ;;
  SKILL.md)
    # Only for a skill in MIRRORED_SKILL_NAMES, and only when the edited
    # file is exactly .claude/skills/<name>/SKILL.md or
    # .agents/skills/<name>/SKILL.md -- not some other nested SKILL.md
    # (this repo has many skills that are deliberately NOT exact mirrors).
    skill_name="$(basename "$resolved_dir")"
    for candidate in "${MIRRORED_SKILL_NAMES[@]}"; do
      [ "$skill_name" = "$candidate" ] || continue
      if [ "$resolved_dir" = "$root/.claude/skills/$candidate" ]; then
        source="$root/.claude/skills/$candidate/SKILL.md"
        dest="$root/.agents/skills/$candidate/SKILL.md"
        source_label=".claude/skills/$candidate/SKILL.md"
        dest_label=".agents/skills/$candidate/SKILL.md"
      elif [ "$resolved_dir" = "$root/.agents/skills/$candidate" ]; then
        source="$root/.agents/skills/$candidate/SKILL.md"
        dest="$root/.claude/skills/$candidate/SKILL.md"
        source_label=".agents/skills/$candidate/SKILL.md"
        dest_label=".claude/skills/$candidate/SKILL.md"
      fi
      break
    done
    ;;
  *)
    ;;
esac

[ -n "$source" ] || exit 0
[ -f "$source" ] || exit 0
# Never create a brand-new destination file the hook wasn't asked to sync --
# both sides of a real mirror pair already exist in this repo.
[ -f "$dest" ] || exit 0

# Already identical -- nothing to do.
if diff -q "$source" "$dest" >/dev/null 2>&1; then
  exit 0
fi

cp "$source" "$dest"

jq -n --arg from "$source_label" --arg to "$dest_label" \
  '{systemMessage: ("Synced " + $from + " -> " + $to + " to keep the two knowledge-base mirrors identical.")}'

exit 0

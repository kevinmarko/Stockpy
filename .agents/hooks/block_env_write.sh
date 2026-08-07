#!/usr/bin/env bash
# Antigravity PreToolUse hook (edit_file|create_file) -- requires operator
# approval before any edit that would write to the literal `.env` file.
#
# .env holds live secrets (STATE_API_TOKEN, FOLLOW_API_TOKEN, RH_PASSWORD,
# RH_MFA_SECRET, DATABASE_URL, ...) and every *_WRITES_ENABLED-style flag in
# this repo is deliberately hand-set-only (see CLAUDE.md/AGENTS.md and
# settings.py's own field descriptions, e.g. STRATEGY_WRITES_ENABLED /
# AUTOMATION_WRITES_ENABLED / BROKERAGE_CONNECT_ENABLED -- "Never GUI-writable
# ... hand-set in .env only") -- an agent must never write this file without
# the operator explicitly approving that specific edit first. `.env.example`
# and other dotenv-like files are NOT blocked; only the exact basename `.env`
# is. Ports the same purpose as this repo's Claude Code guardrail
# (.claude/hooks/block_env_write.sh) to Antigravity's hook I/O contract, which
# differs (stdin/stdout JSON shape, tool/arg names).
#
# VERIFIED against a live Antigravity runtime. The exact Antigravity arg schema
# for write_to_file/replace_file_content/multi_replace_file_content uses TargetFile.
# Note: These hooks do not natively intercept file-editing tools in the IDE runtime,
# so this remains policy-only for Antigravity sessions -- "ask" here documents the
# required approval-first behavior for prompt adherence, it is not (yet) a
# system-level gate the way the Claude Code hook is.
set -uo pipefail

input=""
if [ ! -t 0 ]; then
  if IFS= read -r -t 1 first_line; then
    input="$(printf '%s\n' "$first_line"; cat)"
  fi
fi

candidates="$(printf '%s' "$input" | jq -r '.toolCall.args.TargetFile // empty' 2>/dev/null)"

found=0
while IFS= read -r c; do
  [ -z "$c" ] && continue
  if [ "$(basename -- "$c")" = ".env" ]; then
    found=1
    break
  fi
done <<<"$candidates"

if [ "$found" = "1" ]; then
  jq -n '{decision:"ask", reason:".env holds live secrets and every write-gate flag in this repo is hand-set-only by design (CLAUDE.md/AGENTS.md, settings.py) -- get explicit operator approval for this specific edit before writing it."}'
fi

exit 0

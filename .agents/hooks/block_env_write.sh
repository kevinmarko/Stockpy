#!/usr/bin/env bash
# Antigravity PreToolUse hook (edit_file|create_file) -- blocks any edit that
# would write to the literal `.env` file.
#
# .env holds live secrets (STATE_API_TOKEN, FOLLOW_API_TOKEN, RH_PASSWORD,
# RH_MFA_SECRET, DATABASE_URL, ...) and every *_WRITES_ENABLED-style flag in
# this repo is deliberately hand-set-only (see CLAUDE.md/AGENTS.md and
# settings.py's own field descriptions, e.g. STRATEGY_WRITES_ENABLED /
# AUTOMATION_WRITES_ENABLED / BROKERAGE_CONNECT_ENABLED -- "Never GUI-writable
# ... hand-set in .env only") -- an agent should never be the one editing this
# file. `.env.example` and other dotenv-like files are NOT blocked; only the
# exact basename `.env` is. Ports the same purpose as this repo's Claude Code
# guardrail (.claude/hooks/block_env_write.sh) to Antigravity's hook I/O
# contract, which differs (stdin/stdout JSON shape, tool/arg names).
#
# VERIFIED against a live Antigravity runtime. The exact Antigravity arg schema
# for write_to_file/replace_file_content/multi_replace_file_content uses TargetFile.
# Note: These hooks do not natively intercept file-editing tools in the IDE runtime,
# so this remains policy-only for Antigravity sessions.
set -uo pipefail

input="$(cat)"

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
  jq -n '{decision:"deny", reason:".env holds live secrets and every write-gate flag in this repo is hand-set-only by design (CLAUDE.md/AGENTS.md, settings.py) -- edit it yourself outside the agent."}'
fi

exit 0

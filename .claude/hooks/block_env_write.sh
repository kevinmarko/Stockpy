#!/usr/bin/env bash
# PreToolUse hook (Edit|Write) -- blocks any edit to the literal `.env` file.
#
# .env holds live secrets (STATE_API_TOKEN, FOLLOW_API_TOKEN, RH_PASSWORD,
# RH_MFA_SECRET, DATABASE_URL, ...) and every *_WRITES_ENABLED-style flag in
# this repo is deliberately hand-set-only (see CLAUDE.md/AGENTS.md and
# settings.py's own field descriptions, e.g. STRATEGY_WRITES_ENABLED /
# AUTOMATION_WRITES_ENABLED / BROKERAGE_CONNECT_ENABLED -- "Never GUI-writable
# ... hand-set in .env only") -- an agent should never be the one editing this
# file. `.env.example` and other dotenv-like files are NOT blocked; only the
# exact basename `.env` is.
set -uo pipefail

input="$(cat)"
file_path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')"

if [ -n "$file_path" ] && [ "$(basename -- "$file_path")" = ".env" ]; then
  jq -n '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: ".env holds live secrets and every write-gate flag in this repo is hand-set-only by design (CLAUDE.md/AGENTS.md, settings.py) — edit it yourself outside Claude Code."
    }
  }'
fi

#!/usr/bin/env bash
# Antigravity PostToolUse hook (edit_file|create_file) -- runs the webapp
# TypeScript typecheck after any edit under webapp/src/**.
#
# This IS the mock/live API parity gate: webapp/src/api/client.ts's
# `export const api: typeof liveApi = USE_MOCK ? mockApi : liveApi;` is a
# compile-time-only check that mockApi and liveApi haven't drifted apart
# (missing method, wrong return type). Nothing else catches that drift until
# someone happens to run `npm run build` -- a real bug already shipped from
# exactly this gap (see webapp/src/api/client.ts's own comment). Runs the
# project's own unmodified `typecheck` script; no shortcuts. Ports the same
# purpose as this repo's Claude Code guardrail
# (.claude/hooks/webapp_typecheck.sh) to Antigravity's hook I/O contract,
# which differs (stdin/stdout JSON shape, tool/arg names, and PostToolUse
# here cannot block -- the edit already happened).
#
# VERIFIED against a live Antigravity runtime. The exact Antigravity arg schema
# for write_to_file/replace_file_content/multi_replace_file_content uses TargetFile.
# Note: These hooks do not natively intercept file-editing tools in the IDE runtime,
# so this remains policy-only for Antigravity sessions.
set -uo pipefail

input="$(cat)"

candidates="$(printf '%s' "$input" | jq -r '.toolCall.args.TargetFile // empty' 2>/dev/null)"

matched=0
while IFS= read -r c; do
  [ -z "$c" ] && continue
  case "$c" in
    *webapp/src/*) matched=1; break ;;
  esac
done <<<"$candidates"

[ "$matched" = "1" ] || exit 0
[ -f webapp/package.json ] || exit 0

if ! output="$(npm run --prefix webapp -s typecheck 2>&1)"; then
  printf '%s\n' "$output" >&2
fi

exit 0

#!/usr/bin/env bash
# PostToolUse hook (Edit|Write) -- runs the webapp TypeScript typecheck after
# any edit under webapp/src/**.
#
# This IS the mock/live API parity gate: webapp/src/api/client.ts's
# `export const api: typeof liveApi = USE_MOCK ? mockApi : liveApi;` is a
# compile-time-only check that mockApi and liveApi haven't drifted apart
# (missing method, wrong return type). Nothing else catches that drift until
# someone happens to run `npm run build` -- a real bug already shipped from
# exactly this gap (see webapp/src/api/client.ts's own comment). Runs the
# project's own unmodified `typecheck` script; no shortcuts.
set -uo pipefail

input="$(cat)"
file_path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_response.filePath // empty')"

case "$file_path" in
  *webapp/src/*)
    [ -f webapp/package.json ] || exit 0
    npm run --prefix webapp -s typecheck
    ;;
esac

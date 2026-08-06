#!/usr/bin/env bash
# PostToolUse hook (Edit|Write) -- after an edit to a tracked *.py file, runs
# that module's mapped tests/test_<basename>.py file (if one already exists)
# and surfaces failures inline as additionalContext.
#
# NON-BLOCKING / informative only: this is a PostToolUse hook, so the tool
# call already happened -- there is nothing left to deny. It never emits
# hookSpecificOutput.permissionDecision (that's a PreToolUse-only concept);
# it only ever emits additionalContext on failure, and exits 0 unconditionally
# so it can never itself fail the turn. A module with no existing test file
# is left alone -- this only enforces tests that already exist, it never
# demands new ones.
set -uo pipefail

input="$(cat)"
file_path="$(jq -r '.tool_input.file_path // .tool_response.filePath // empty' <<<"$input")"

# Only care about real .py source files.
case "$file_path" in
  *.py) ;;
  *) exit 0 ;;
esac

# Skip non-source directories -- editing a test file itself doesn't need "a
# test for the test", and docs/.claude/.agents are never test-mapped.
case "$file_path" in
  tests/*|docs/*|.claude/*|.agents/*) exit 0 ;;
esac

base="$(basename "$file_path" .py)"
test_file="tests/test_${base}.py"

# Nothing to enforce yet if this module has no mapped test.
[ -f "$test_file" ] || exit 0

# Locate a usable python interpreter; if none, don't block silently.
if [ -x ".venv/bin/python3" ]; then
  python_bin=".venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
else
  exit 0
fi

# Locate a timeout wrapper -- GNU coreutils' `timeout` isn't present on stock
# macOS (only via `brew install coreutils`, as `gtimeout`); degrade to running
# without a timeout wrapper rather than hard-failing the whole hook when
# neither is available.
if command -v timeout >/dev/null 2>&1; then
  timeout_bin="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  timeout_bin="gtimeout"
else
  timeout_bin=""
fi

if [ -n "$timeout_bin" ]; then
  output="$("$timeout_bin" 90 "$python_bin" -m pytest -q "$test_file" -m "not network and not slow" 2>&1)"
else
  output="$("$python_bin" -m pytest -q "$test_file" -m "not network and not slow" 2>&1)"
fi
status=$?

if [ "$status" -eq 0 ]; then
  # Quiet on success -- avoid noisy spam on every edit.
  exit 0
fi

tail_output="$(printf '%s\n' "$output" | tail -n 40)"

jq -n \
  --arg ctx "Targeted test tests/test_${base}.py FAILED after editing ${file_path}. Fix before considering this change done. Output (tail):
${tail_output}" \
  '{hookSpecificOutput:{hookEventName:"PostToolUse", additionalContext:$ctx}}'

exit 0

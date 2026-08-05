#!/usr/bin/env bash
# Stop hook -- THE enforcement gate for this repo's targeted-test convention.
#
# WHAT THIS ENFORCES: before a turn is allowed to end, if there are
# uncommitted Python source changes with a mapped tests/test_<basename>.py
# file, that mapped test suite must pass. This is the blocking counterpart to
# verify_targeted_tests.sh's non-blocking PostToolUse nudge -- that hook can
# only leave a note after an edit; this one can actually stop the turn from
# ending while a change is left in a known-broken state.
#
# WHY THE RETRY CAP EXISTS: a test can be flaky, or depend on something this
# sandboxed session genuinely cannot fix (missing network, a pre-existing
# unrelated failure, a fixture only valid in CI). Without a cap, this hook
# could deadlock a session forever -- block, "fix" attempt, block again,
# indefinitely. The marker-file counter allows at most 2 blocking attempts
# per session; on the 3rd consecutive would-be block, the gate releases
# itself, resets, and instead emits a loud systemMessage telling the operator
# to verify manually. This trades a small risk of missing a real regression
# for a hard guarantee against an unfixable hang.
#
# FAIL-OPEN GUARANTEE: this hook never blocks unless it has actually proven
# there is something to catch. It exits 0 (allow stop) whenever it cannot
# establish all of: a usable cwd, a stable session_id to key the retry
# counter, a non-empty diff of changed non-test/doc .py files, at least one
# of those files having an existing mapped test, and a usable python
# interpreter to run that test with.
set -uo pipefail

input="$(cat)"
cwd="$(jq -r '.cwd // empty' <<<"$input")"
session_id="$(jq -r '.session_id // empty' <<<"$input")"

# Can't operate safely without a working directory.
[ -n "$cwd" ] || exit 0

# No stable key to bound retries against -- fail open rather than risk an
# unbounded block.
[ -n "$session_id" ] || exit 0

cd "$cwd" || exit 0

# Changed python files: staged + unstaged + untracked, excluding test/doc/
# config dirs. NOTE: git status --porcelain's rename syntax ("old -> new")
# is not specially handled here -- taking $2 for a rename line yields the
# old path, which is an acceptable simplification for this gate (a rename
# with no other content change is not the case this hook exists to catch).
mapfile -t changed_py < <(
  git status --porcelain -- '*.py' 2>/dev/null \
    | awk '{print $2}' \
    | grep -vE '^(tests/|docs/|\.claude/|\.agents/)' || true
)

# Build the deduped list of mapped test files that actually exist on disk.
declare -A seen=()
test_files=()
for f in "${changed_py[@]:-}"; do
  [ -n "$f" ] || continue
  base="$(basename "$f" .py)"
  t="tests/test_${base}.py"
  [ -f "$t" ] || continue
  if [ -z "${seen[$t]:-}" ]; then
    seen["$t"]=1
    test_files+=("$t")
  fi
done

# Nothing to enforce -- doc-only turn, chat-only turn, or changes to modules
# with no existing test.
[ "${#test_files[@]}" -gt 0 ] || exit 0

marker="${TMPDIR:-/tmp}/claude-stop-verify-${session_id}.count"

count=0
if [ -f "$marker" ]; then
  count="$(cat "$marker" 2>/dev/null || echo 0)"
  case "$count" in
    ''|*[!0-9]*) count=0 ;;
  esac
fi

if [ "$count" -ge 2 ]; then
  # 3rd consecutive block attempt: release the gate instead of risking a
  # deadlocked session. Reset the counter for future turns.
  rm -f "$marker" 2>/dev/null || true
  jq -n --arg files "$(printf '%s ' "${test_files[@]}")" \
    '{systemMessage: ("Verification gate released after 2 failed attempts -- targeted tests still failing or unresolved: " + $files + ". Please verify manually before treating this as done.")}'
  exit 0
fi

# Locate a usable python interpreter; if none, can't enforce -- don't block.
if [ -x ".venv/bin/python3" ]; then
  python_bin=".venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
else
  exit 0
fi

output="$(timeout 100 "$python_bin" -m pytest -q "${test_files[@]}" -m "not network and not slow" 2>&1)"
status=$?

if [ "$status" -eq 0 ]; then
  rm -f "$marker" 2>/dev/null || true
  exit 0
fi

new_count=$((count + 1))
mkdir -p "$(dirname "$marker")" 2>/dev/null || true
printf '%s' "$new_count" > "$marker" 2>/dev/null || true

tail_output="$(printf '%s\n' "$output" | tail -n 40)"

jq -n \
  --arg reason "Uncommitted Python changes have a failing targeted test -- do not consider this task done yet.

Failing/needs-attention: $(printf '%s ' "${test_files[@]}")

${tail_output}" \
  '{decision:"block", reason:$reason}'

exit 0

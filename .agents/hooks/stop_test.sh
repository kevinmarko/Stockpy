#!/usr/bin/env bash
# Antigravity Stop hook -- ADVISORY-ONLY targeted-test check.
#
# Ports the intent of this repo's Claude Code guardrails
# (.claude/hooks/verify_targeted_tests.sh's mapped-test-lookup convention:
# an edited tracked `<module>.py` maps to `tests/test_<module>.py` iff that
# file already exists) to Antigravity's Stop event. It deliberately does NOT
# port .claude/hooks/verify_before_stop.sh's *blocking* behavior -- per
# CLAUDE.md's "Agent Workflow: Verification & Planning" section, "Live
# validation confirms that [Antigravity's] Stop hook does not expose 'force
# continuation' semantics", so there is no block/deny decision field this
# hook could set that Antigravity would actually honor. This hook only ever
# re-runs the mapped test(s) for whatever tracked .py files have uncommitted
# changes and prints a clear advisory to stderr on failure -- it never emits
# any block/deny/decision field, and always exits 0. Because it cannot
# actually stop the turn, there is no deadlock risk and therefore no retry
# cap (unlike verify_before_stop.sh's 2-attempt cap, which exists solely to
# bound a blocking mechanism this hook doesn't have).
#
# UNVERIFIED against a live Antigravity runtime -- this environment has no
# Antigravity instance available to test against. In particular: (1) the
# exact shape of the JSON given to a Stop hook is unconfirmed, so this script
# deliberately avoids depending on any specific field of it (e.g. `.cwd`) and
# instead derives the repo root via `git rev-parse --show-toplevel`, the same
# defensive choice .agents/hooks/sync_agent_docs.sh already makes; (2) the
# output convention below (plain text to stderr, no structured "advisory"
# JSON field) mirrors .agents/hooks/webapp_typecheck.sh's own precedent for
# "PostToolUse/Stop can't block, so just surface the output" in this port --
# not a confirmed Antigravity contract for a dedicated advisory-message
# field. If this doesn't fire, or fires but the message isn't visible to the
# operator, as expected the first time an Antigravity session stops with a
# failing targeted test, that's the first thing to check.
set -uo pipefail

# Drain stdin for consistency with the other Antigravity hooks in this repo
# (block_env_write.sh / sync_agent_docs.sh / webapp_typecheck.sh all read the
# full hook input even when, as here, no field of it is actually consumed).
input="$(cat)"

root="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$root" ] || exit 0
cd "$root" || exit 0

# Changed tracked .py files: unstaged + staged, excluding test/doc/config
# dirs -- same exclusion set as verify_before_stop.sh's changed_py filter.
# Untracked (new, not yet `git add`ed) files are intentionally out of scope
# here, matching the task's own "against tracked .py files" framing.
changed_py="$(
  {
    git diff --name-only -- '*.py' 2>/dev/null
    git diff --cached --name-only -- '*.py' 2>/dev/null
  } | grep -vE '^(tests/|docs/|\.claude/|\.agents/)' | sort -u
)"

# Build the deduped list of mapped test files that actually exist on disk.
seen=""
test_files=()
while IFS= read -r f; do
  [ -n "$f" ] || continue
  # Skip a deleted-on-disk path (e.g. `git rm`'d but not yet committed) --
  # nothing to map a test failure back to.
  [ -f "$f" ] || continue
  base="$(basename "$f" .py)"
  t="tests/test_${base}.py"
  [ -f "$t" ] || continue
  case " $seen " in
    *" $t "*) continue ;;
  esac
  seen="$seen $t"
  test_files+=("$t")
done <<<"$changed_py"

# Nothing to check -- doc-only change, chat-only turn, or changes to modules
# with no existing test file.
[ "${#test_files[@]}" -gt 0 ] || exit 0

# Locate a usable python interpreter; if none, don't block silently -- just
# skip (this hook can't block anyway, but a missing interpreter also means
# it has nothing useful to report).
if [ -x ".venv/bin/python3" ]; then
  python_bin=".venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
else
  exit 0
fi

# Locate a timeout wrapper -- GNU coreutils' `timeout` isn't present on stock
# macOS (only via `brew install coreutils`, as `gtimeout`); degrade to
# running without a timeout wrapper rather than hard-failing the hook.
if command -v timeout >/dev/null 2>&1; then
  timeout_bin="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  timeout_bin="gtimeout"
else
  timeout_bin=""
fi

if [ -n "$timeout_bin" ]; then
  output="$("$timeout_bin" 100 "$python_bin" -m pytest -q "${test_files[@]}" -m "not network and not slow" 2>&1)"
else
  output="$("$python_bin" -m pytest -q "${test_files[@]}" -m "not network and not slow" 2>&1)"
fi
status=$?

# Quiet on success -- avoid noisy spam on every stop.
[ "$status" -eq 0 ] && exit 0

tail_output="$(printf '%s\n' "$output" | tail -n 40)"

{
  printf 'ADVISORY (non-blocking -- Antigravity Stop hooks cannot halt a turn): targeted test(s) failing for uncommitted changes: %s\n' "$(printf '%s ' "${test_files[@]}")"
  printf 'Please verify manually before treating this as done.\n\n'
  printf '%s\n' "$tail_output"
} >&2

exit 0

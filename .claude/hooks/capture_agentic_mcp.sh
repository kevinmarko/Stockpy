#!/usr/bin/env bash
# PostToolUse hook (Robinhood / investyo MCP tools) -- saves each MCP response
# under the RUNNING agentic cycle's run folder
# (OUTPUT_DIR/agentic/<run_id>/inputs/raw/<seq>_<tool>.json) so the rh-* agents
# can build their input files with `python -m execution.agentic_inputs assemble`
# instead of re-typing every response. With no running cycle it is a no-op.
#
# It must never disturb the tool call it observes: it prints nothing, is
# bounded by a 10 s timeout, and exits 0 unconditionally. Failures are logged
# by the Python side to OUTPUT_DIR/agentic/capture_errors.log.
set -uo pipefail

# Nothing on stdin (run by hand from a terminal) -> nothing to capture.
[ -t 0 ] && exit 0

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || exit 0
repo_root="$(cd "$script_dir/../.." 2>/dev/null && pwd)" || exit 0

# Interpreter: this checkout's .venv -> the main checkout's .venv (a git
# worktree under .claude/worktrees/<name>/ has none of its own; its
# --git-common-dir is the main checkout's .git). No system-python fallback:
# without the repo's deps settings can't resolve OUTPUT_DIR, so skip quietly.
python_bin=""
if [ -x "$repo_root/.venv/bin/python3" ]; then
  python_bin="$repo_root/.venv/bin/python3"
else
  common_dir="$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null)" || common_dir=""
  if [ -n "$common_dir" ]; then
    main_root="$(cd "$repo_root" 2>/dev/null && cd "$common_dir/.." 2>/dev/null && pwd)" || main_root=""
    if [ -n "$main_root" ] && [ -x "$main_root/.venv/bin/python3" ]; then
      python_bin="$main_root/.venv/bin/python3"
    fi
  fi
fi
[ -n "$python_bin" ] || exit 0

cd "$repo_root" 2>/dev/null || exit 0

# Bound the run: GNU `timeout` / `gtimeout` when present, else perl's alarm
# (stock macOS has perl but no `timeout`).
if command -v timeout >/dev/null 2>&1; then
  timeout 10 "$python_bin" -m execution.agentic_inputs capture >/dev/null 2>&1
elif command -v gtimeout >/dev/null 2>&1; then
  gtimeout 10 "$python_bin" -m execution.agentic_inputs capture >/dev/null 2>&1
elif command -v perl >/dev/null 2>&1; then
  perl -e 'alarm shift; exec @ARGV or exit 0' 10 "$python_bin" -m execution.agentic_inputs capture >/dev/null 2>&1
else
  "$python_bin" -m execution.agentic_inputs capture >/dev/null 2>&1
fi

exit 0

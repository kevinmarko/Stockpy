#!/usr/bin/env bash
# PreToolUse hook (Bash) -- denies specific dangerous flags on Bash commands
# that are otherwise allowlisted by a plain prefix match. `Bash(git commit *)`,
# `Bash(git add *)`, and `Bash(ruff check *)` in .claude/settings.json each
# match more than their intended safe subset (a prefix-match allowlist cannot
# exclude specific flags on its own) -- see code-review findings #1-#3 on
# PR #960. This hook is the flag-level backstop those allowlist entries can't
# express, mirroring block_env_write.sh's PreToolUse deny-output contract.
set -uo pipefail

input=""
if [ ! -t 0 ]; then
  if IFS= read -r -t 1 first_line; then
    input="$(printf '%s\n' "$first_line"; cat)"
  fi
fi
command="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')"

[ -z "$command" ] && exit 0

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

# Whitespace tokenization -- good enough for flag detection, not a full shell
# parser (quoted arguments with embedded spaces, e.g. a commit -m message,
# split into multiple tokens; that's fine here since we only ever match on
# bare flag tokens like `-a`/`--amend`, never on message content).
read -r -a tokens <<< "$command"

is_git_commit=false
is_git_add=false
is_ruff_check=false
for i in "${!tokens[@]}"; do
  if [ "${tokens[$i]}" = "git" ] && [ "${tokens[$((i + 1))]:-}" = "commit" ]; then
    is_git_commit=true
  fi
  if [ "${tokens[$i]}" = "git" ] && [ "${tokens[$((i + 1))]:-}" = "add" ]; then
    is_git_add=true
  fi
  if [ "${tokens[$i]}" = "ruff" ] && [ "${tokens[$((i + 1))]:-}" = "check" ]; then
    is_ruff_check=true
  fi
done

if $is_git_commit; then
  for t in "${tokens[@]}"; do
    case "$t" in
      --amend | --no-verify | -n | -a | -am)
        deny "git commit with -a/--amend/--no-verify/-n requires manual approval -- the allowlist only covers a plain 'git commit -m ...'. Run it yourself, or ask for explicit confirmation."
        ;;
    esac
  done
fi

if $is_git_add; then
  for t in "${tokens[@]}"; do
    case "$t" in
      -A | --all | -p | --patch | -f | --force | .)
        deny "git add with -A/--all/-p/--patch/-f/--force/'.' requires manual approval -- the allowlist only covers explicitly-named files. Run it yourself, or ask for explicit confirmation."
        ;;
    esac
  done
fi

if $is_ruff_check; then
  for t in "${tokens[@]}"; do
    case "$t" in
      --fix | --unsafe-fixes)
        deny "ruff check --fix mutates source files -- the allowlist only covers a read-only lint pass. Run it yourself, or ask for explicit confirmation."
        ;;
    esac
  done
fi

exit 0

# Task Tracker: Prompt Registry unsigned remote-store adoption fix

Branch: `fix-prompt-registry-unsigned-adopt`

- [x] Verify the reported gap directly against the current codebase
      (`prompt_registry/registry.py`, `settings.py` defaults,
      `prompt_registry/guardrails.py`).
- [x] Confirm every production `PromptRegistry(` construction site
      (`prompt_registry/registry.py`, `Gravity AI Review Suite.py`) and
      every test construction site to scope the fix precisely (grep
      confirmed no test constructs a real `HTTPStore`/`FirestoreStore`
      with `signing_key=None` — so a constructor-level guard cannot break
      any existing test).
- [x] Read `scripts/preflight_check.py`'s check-function pattern
      (`check_macro_regime_gate_enabled` as the mirror), `CheckResult`
      dataclass, `ALL_CHECKS` registration, `_ADVISORY_AUTO_SKIP`.
- [x] Implement `PromptRegistry.__init__` construction-time guard
      (raises `ValueError` for `HTTPStore`/`FirestoreStore` + no key).
- [x] Implement `_build_registry_from_settings()` pre-dispatch refusal
      (CRITICAL log + alert + fallback to `store=None`) so the factory
      never crashes `get_registry()`.
- [x] Correct `prompt_registry/registry.py`'s module + class docstrings.
- [x] Add `scripts/preflight_check.py::check_prompt_registry_signing_key_configured`,
      register in `ALL_CHECKS`, add docstring trailer entry.
- [x] Fix stale `investyo_mcp_server.py:590` comment (cosmetic).
- [x] Add regression tests: constructor-guard coverage, factory-refusal
      coverage, end-to-end tamper-scenario-closed test, preflight-check
      coverage.
- [x] Run full relevant test suites — 441 + 31 passed.
- [x] Run `ruff check . --select=F821,F822,F823,E9` genuine-bug gate — clean.
- [x] Regenerate `docs/settings_field_census.md`/`.json` and
      `docs/settings_liveness.json` (new `settings.PROMPT_REGISTRY_*`
      read sites shifted the census counts) — required for
      `test_measure_settings_census.py`/`test_settings_liveness.py` to
      stay green.
- [x] Write `docs/known_issues/prompt_registry_unsigned_remote_adoption.md`
      + `docs/known_issues/README.md` index row.
- [x] Write PR artifacts (`.claude/prompt_registry_unsigned_remote_adoption_*`).
- [ ] Open PR against `main`.

## Incident during this session (disclosed for the record)

Mid-task, a `git stash` / `git stash pop` pair used to compare against a
pre-edit baseline collided with another concurrent Claude Code session
sharing this exact worktree — the shared stash stack meant `git stash pop`
applied a *different* session's stash entry instead of mine, and my five
edited files reverted to `origin/main`'s content on disk. Recovered by
redoing all five edits from the full diffs already present in this
session's own context (no data was permanently lost — the risk was purely
a mid-session working-tree race, not a git-history loss). Going forward in
this session, `git stash` was avoided entirely in favor of `git diff`
against the specific file to check isolation after each edit.

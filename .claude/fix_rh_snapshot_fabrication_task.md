# Task tracker: fix Robinhood live-snapshot $0 fabrication

Branch: `fix-rh-snapshot-fabrication`

- [x] Read and independently confirm the reported CONSTRAINT #4 finding
      against current source (`data/robinhood_portfolio.py`).
- [x] Confirm root cause against the installed `robin_stocks` library
      source (`request_get(..., dataType='indexzero')` swallow-on-failure
      behavior; `build_holdings()`'s internal profile-call coupling).
- [x] Implement the fix: raise on empty `load_portfolio_profile()`/
      `load_account_profile()`, deliberately leave `build_holdings()=={}`
      unguarded (documented scope decision).
- [x] Fix the related exception-fallback DB-tier asymmetry.
- [x] Update the one pre-existing test whose fixture incidentally relied on
      the old (buggy) tolerant behavior.
- [x] Add regression tests for Scenario A, Scenario B (documented
      non-raising scope boundary), genuinely-empty-account, and the
      three-tier-fallback end-to-end engagement test.
- [x] Add DB-tier exception-fallback regression tests.
- [x] Write `docs/known_issues/robinhood_snapshot_fabricated_zero_on_swallowed_api_failure.md`.
- [x] Index it in `docs/known_issues/README.md`.
- [x] Add CLAUDE.md bullet (auto-mirrored to AGENTS.md).
- [x] Run targeted test file — 67/67 pass.
- [x] Run related Robinhood test files — 180/180 pass.
- [x] Run `/verify` skill gate (ruff genuine-bug rules + full offline
      suite) — clean; 5 pre-existing unrelated failures confirmed via a
      clean-checkout reproduction (missing optional `openai`/`google.genai`
      deps in this sandbox).
- [x] Recover from an unrelated git-stash cross-worktree collision
      encountered mid-task (see incident note in the PR description /
      walkthrough) — both my own work and the other session's displaced
      work fully recovered and preserved.
- [x] Create PR artifacts under `.claude/` with branch-scoped unique names.
- [ ] Open PR.

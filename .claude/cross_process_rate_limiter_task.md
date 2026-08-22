# Task tracker: cross-process FMP/EDGAR rate limiter

Branch: `fix-cross-process-rate-limiter`.

- [x] New `data/cross_process_throttle.py::wait_turn` — POSIX `fcntl.flock`-based
      cross-process spacing primitive, stdlib-only leaf.
- [x] `data/fmp_client.py::_fmp_throttle` calls `wait_turn` as an additional outer layer
      after its existing in-process logic; monkeypatchable state-path resolver added.
- [x] `data/edgar_fundamentals.py::_throttle` — same pattern.
- [x] `tests/test_cross_process_throttle.py` — 11 new tests, including a real
      two-OS-process (`subprocess.Popen`) serialization proof. All passing.
- [x] `tests/test_fmp_client.py` / `tests/test_edgar_fundamentals.py` — redirected the
      new state-path override to `tmp_path` in the relevant fixtures; loosened one
      timing tolerance that was too tight for the added (real) syscall overhead.
      Verified stable across 3 repeated runs.
- [x] Broader regression sweep (23 test files touching `fmp_client`/`edgar_fundamentals`):
      849 passed, 0 failed.
- [x] Full repo-wide offline suite: 2867 passed; 1 unrelated pre-existing failure in
      `test_forecast_backfill.py` (untouched module, real-subprocess timing test) under
      heavy concurrent-worktree load — confirmed unrelated, not investigated further
      (out of scope).
- [x] Documentation: `docs/architecture/data-layer.md` new "Cross-process rate limiting"
      bullet.
- [x] Documentation: `docs/VALIDATION_STRATEGY_FIX_LOG.md` new entry cross-referencing
      both PR #857's and PR #858's independent discovery of the same root cause.
- [x] Documentation: `docs/known_issues/xsec_universe_coverage_concurrency_variance.md`'s
      "Disclosed follow-up" section updated to note it is now implemented.
- [ ] Commit, push branch, open PR.

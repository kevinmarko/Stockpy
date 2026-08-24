# CI `test (offline suite)` failures around PR #898 (2026-08-24)

## Status

**Investigated and resolved — neither failure was a real regression in `main`.**
One was a pre-existing, self-documented timing-sensitive flaky test; the other
was a genuine bug, but in a *different* PR's own diff, not in `main`. A
CI-flakiness hardening fix was applied to the second issue's test. No change
was needed to `main`'s own content.

## Background

PR #898 ("chore(ml): update lgbm_ranker registry to 2026-08-24 retrain",
commit `e229ffd2`) is a metadata-only change to `ml/registry.yaml` (plus its
own test file) — no `settings.py`, `scripts/`, or engine-code changes. After
it merged to `main`, its own push-triggered `test (offline suite)` CI check
showed `conclusion: failure`. Around the same time, PR #899 (an unrelated
`cache_long_short` wash-sale fix) had `e229ffd2` auto-merged into its branch
by the repo's `auto-update-pr-branches` automation, and its own CI run failed
two *different* tests:

- `tests/test_settings_liveness.py::TestCommittedArtifactIsFresh::test_committed_json_matches_a_fresh_run`
- `tests/test_build_command_manifest.py::test_fetch_strategy_registry_real_invocation_returns_nonempty_list_of_strings`

This surfaced as "PR #898 broke `main`'s CI, and PR #899 inherited the
breakage" — investigated below and found to be three unrelated things
that happened to land in the same ~30-minute window.

## Finding 1 — `main`'s own CI failure was a pre-existing flaky test

`e229ffd2`'s push-triggered run
([32764701597](https://github.com/kevinmarko/Stockpy/actions/runs/32764701597))
failed exactly one test:
`tests/test_edgar_fundamentals.py::TestThreadSafety::test_throttle_serializes_request_issuance`,
with one of 11 measured inter-request gaps (out of 12 concurrent threads)
dropping fractionally below its `0.04 * 0.6 = 0.024s` floor. This test's own
docstring already discloses this exact failure mode: *"At the original 0.02s
interval / 0.8x tolerance this occasionally clipped below the floor by ~1ms
under 12-thread contention (measured, not theoretical) — bumped to 0.04s /
0.6x here to keep comfortable margin ... while still failing hard on a
genuinely unlocked/broken throttle."* It is a real-wall-clock-timing
assertion under thread contention, and the shared GitHub Actions runner was
unusually loaded at that moment — roughly ten other `ci.yml` runs were firing
across the repo in the same half-hour window (visible in the Actions run
list), consistent with several agents/PRs being pushed concurrently.

`scripts/refresh_validations.py`'s `STRATEGY_REGISTRY` construction (see
Finding 2) is unrelated to `ml/registry.yaml`'s *content* — only its
`trained_date`/metrics fields changed, not which strategies exist or what
they import — so nothing in PR #898 plausibly caused either failure.

Re-ran the failed job only (`gh run rerun 32764701597 --failed`); confirmed
green on re-run with zero code changes. **No fix was needed on `main`.**

## Finding 2 — the `settings_liveness.json` staleness was PR #899's own bug, not `main`'s

`docs/settings_liveness.json` records exact `file:line` "site" strings for
every settings-field read site found by static analysis
(`scripts/settings_liveness.py`). PR #899's own commit (`1ee4cf45`, "fix:
cache_long_short wash-sale rule + correlation-drift docstring accuracy")
inserted a `_naive_utc` helper into `data/cache_long_short_store.py` *before*
several existing settings-read sites (`DATABASE_URL`, `DB_POOL_SIZE`,
`DB_MAX_OVERFLOW`, `MCP_DATABASE_URL_RO`), shifting their line numbers by 14
— e.g. `data/cache_long_short_store.py:86` → `:100`. The PR branch's own
`docs/settings_liveness.json` was never regenerated after that edit, so
`TestCommittedArtifactIsFresh` correctly failed on that branch.

Verified directly: regenerating `docs/settings_liveness.json` on `main`
(`e229ffd2`) via `python3 scripts/settings_liveness.py --write` produces a
byte-identical file — `main` was never stale. Regenerating it on PR #899's
merge commit (`ce6b81b1`) produces exactly the 6 site-line-number diffs
described above, nothing else.

This was already fixed on the PR #899 branch by commit `6b23ac3d` ("fix:
regenerate stale settings_liveness.json (site line numbers)") before this
investigation reached that point — independently confirmed correct by
re-deriving the same root cause from a fresh diff. See CLAUDE.md's PR
Artifacts convention and the `docs/settings_liveness.json` freshness gate in
`tests/test_settings_liveness.py` — any PR that shifts line numbers in a
file with settings reads below the inserted code must re-run
`python3 scripts/settings_liveness.py --write` before merging. This is a
recurring gotcha (see the git history for a near-identical prior fix,
"fix: regenerate stale docs/settings_liveness.json after line-number
shift").

## Finding 3 — `test_fetch_strategy_registry_real_invocation_...` is a CI-environment-only flake

`scripts/build_command_manifest.py::_fetch_strategy_registry` fetches
`sorted(STRATEGY_REGISTRY.keys())` via an isolated subprocess that imports
`scripts.refresh_validations` (which heavy-imports pandas/numpy/lightgbm/the
quant engines) and dead-letters ANY subprocess failure to `[]` by design
(CONSTRAINT #6 — never crash the manifest build). On PR #899's CI run
([32767829034](https://github.com/kevinmarko/Stockpy/actions/runs/32767829034)),
the real-invocation smoke test for this failed with `assert 0 > 0` — the
subprocess returned `[]`. The actual warning logged by the subprocess
wrapper was:

```
strategy_registry: fetch failed (exit -6): terminate called without an active exception -- degraded to []
```

Exit `-6` is `SIGABRT`; "terminate called without an active exception" is a
native (C/C++ runtime) abort, not a Python exception — consistent with this
codebase's other documented native-library collision issues
(`lightgbm_faiss_libomp_collision_segfault.md`, `cnn_lstm_tf_deadlock.md`),
though this occurred on the `ubuntu-latest` CI runner rather than the
macOS-specific case those documents cover.

**Confirmed NOT a deterministic regression:**
- Reproducing the identical child-process invocation directly (`python3 -c
  "from scripts.refresh_validations import STRATEGY_REGISTRY; ..."`) on this
  exact commit, locally, succeeds every time (macOS).
- The identical test, against the identical `STRATEGY_REGISTRY` construction
  code, **passed** in an earlier CI run of PR #898's own branch
  ([32761093086](https://github.com/kevinmarko/Stockpy/actions/runs/32761093086))
  and in `main`'s own push run
  ([32764701597](https://github.com/kevinmarko/Stockpy/actions/runs/32764701597)).
  Same code, alternating pass/fail across runs → environmental, not causal.
- `ci.yml` runs the offline suite with `pytest -n auto --dist loadgroup` (no
  `pytest-rerunfailures`) on a shared, resource-constrained GitHub-hosted
  runner; this test spawns its own full-weight Python subprocess while
  ~10+ other xdist workers are concurrently running heavy tests, which is a
  plausible trigger for a native runtime abort under memory/CPU pressure
  that doesn't reproduce in a quieter environment.

**Fix applied** (`tests/test_build_command_manifest.py`,
`test_fetch_strategy_registry_real_invocation_returns_nonempty_list_of_strings`):
retry the real invocation up to 3 times before asserting, breaking on the
first non-empty result. This does not weaken the test's regression-catching
power — a genuine regression (e.g. a broken import inside
`STRATEGY_REGISTRY`'s construction) fails the same way on every attempt, so
only a transient environmental abort is filtered out. The sibling
`test_fetch_options_strategy_registry_real_invocation_...` (imports the
lighter `validation.options_harness.STANDARD_OPTIONS_STRATEGIES`) has not
shown this failure mode in any observed run and was left unchanged.

## Other open PRs

Checked the other 2 PRs open at the time of this investigation
(`fix-brinson-fachler-interaction-effect` / #900, since merged; and
`commands-options-strategy-realism` / #901) directly against
`docs/settings_liveness.json` freshness and `test_build_command_manifest.py`
— both clean, neither needed a rebase or fix related to this investigation.

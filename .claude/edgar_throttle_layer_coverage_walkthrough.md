# EDGAR/FMP/GDELT throttle layer-coverage fix — walkthrough

## The finding (verified by sabotage, not by reading)

`tests/test_edgar_fundamentals.py::TestThreadSafety::test_throttle_serializes_request_issuance`
asserts that under 12 concurrent `_http_get` calls, consecutive requests are
issued `>= _REQUEST_DELAY * 0.6` apart. `_throttle()` in
`data/edgar_fundamentals.py` has TWO independent spacing layers protecting
that property:

1. The original in-process `threading.Lock` block.
2. `data/cross_process_throttle.py::wait_turn` (`flock`-based), added later
   by the F8 work in `docs/module_efficiency_redundancy_audit.md`.

Direct sabotage (temporarily replacing each layer's `time.sleep(...)` call
with `pass`, one at a time, then restoring) showed:

| Layer 1 (in-process) | Layer 2 (cross-process) | `TestThreadSafety` result |
|---|---|---|
| intact | intact | PASS |
| **broken** | intact | **PASS** (bug: should catch this) |
| intact | **broken** | **PASS** (bug: should catch this) |
| broken | broken | FAIL |

So the two layers are fully redundant for that one assertion — either can be
completely dead (removed, deadlocked, silently returning early) with nothing
noticing, even though they protect two *different* things in production: the
in-process lock spaces threads within one process, `wait_turn` spaces
requests across this repo's many concurrent git worktrees (separate OS
processes, each of which otherwise believes it owns the whole ~10 req/s SEC
budget).

`data/fmp_client.py::_fmp_throttle` and
`data/sentiment_sources.py::_gdelt_throttle` have the identical two-layer
shape (same F8 work added `wait_turn` to all three). Their own throttle-spacing
tests were checked the same way:

- **FMP** (`tests/test_fmp_client.py::TestThrottleSpacing`): the `clock`
  fixture patches only `data.fmp_client.time`, not
  `data.cross_process_throttle.time` — so `wait_turn` really sleeps in the
  background during these tests, but its sleeps never land in the
  `clock.sleeps` list the assertions check. Sabotaging `wait_turn` (or
  deleting the call to it) leaves `TestThrottleSpacing` green; sabotaging the
  in-process layer correctly fails it. One layer blind, not both.
- **GDELT** (`tests/test_gdelt_rate_limiter.py::TestThrottleSpacing`): the
  `clock` fixture patches **both** `data.sentiment_sources.time` and
  `data.cross_process_throttle.time` to the *same* `FakeClock` instance
  (deliberately, to avoid a real 5s sleep per call — see that fixture's own
  comment and the "5m40s → 1.5s" fix in git history). That means either
  layer's sleep lands in the identical `clock.sleeps` list, so **neither**
  layer's sabotage is individually detectable — the same fully-redundant
  shape as the original EDGAR finding, verified the same way.

## The fix

Added a `TestThrottleLayersIndependently` class to each of the three test
files. Each neutralizes one layer and proves the other alone still enforces
the spacing, then the mirror case:

- **`tests/test_edgar_fundamentals.py`**: reuses the same 12-thread,
  `0.15s`/`0.6x` setup as `TestThreadSafety` (unchanged — no tolerance
  widening anywhere in this diff). One test patches
  `data.cross_process_throttle.time` to a sleep-no-op wrapper (`_NoSleepClock`,
  delegates everything except `sleep`); the mirror patches
  `data.edgar_fundamentals.time` the same way.
- **`tests/test_fmp_client.py`**: one test monkeypatches
  `data.cross_process_throttle.wait_turn` to a no-op (cleanest way to remove
  it from consideration entirely) and reuses the existing `clock.sleeps`
  assertion. The mirror defeats the in-process layer by no-op'ing the
  existing fake clock's `.sleep`, then measures *real* wall-clock gaps
  between request issuances (safe here because FMP's own `clock` fixture
  already leaves `wait_turn` sleeping for real — this file's convention, not
  new to this change) with an 0.8x tolerance (sequential single-threaded
  calls, no thread-contention jitter to budget for, unlike the 12-way
  concurrent EDGAR case).
- **`tests/test_gdelt_rate_limiter.py`**: since GDELT's fixture shares one
  fake clock between both layers, the fix instead gives each layer its own
  **disjoint** fake clock inside the new tests, staying fully deterministic
  (no real sleeps — preserving the file's explicit "no real sleeps, ever"
  convention/history). One test no-ops `wait_turn` and checks the in-process
  clock; the mirror gives the in-process clock a `_NoOpSleepFakeClock` (sleep
  does nothing, time never advances) and checks a *separate* fake clock
  patched only onto `data.cross_process_throttle.time`.

## Verification (each new test individually sabotage-checked)

For all six new tests, confirmed via the same kind of direct sabotage used to
find the bug — not asserted from reading the code:

- Baseline (no sabotage): all 6 PASS.
- In-process layer broken (3 sabotage sites, one per module): the
  `*_in_process_layer_alone` test for that module FAILS; the
  `*_cross_process_layer_alone` test for that module still PASSES.
- Cross-process layer broken (its `time.sleep` made a no-op): the
  `*_cross_process_layer_alone` test FAILS; `*_in_process_layer_alone` still
  PASSES.
- The call to `wait_turn` removed entirely (a distinct sabotage from breaking
  its sleep — proves the *wiring*, not just the implementation, is covered):
  `*_cross_process_layer_alone` FAILS; `*_in_process_layer_alone` still
  PASSES.

Full 6×4 matrix run and the git tree confirmed clean (no leftover sabotage)
after every run.

## No tolerance widening

Nothing in this diff changes `_REQUEST_DELAY`, `0.6`, `5.0`, `0.25`, or any
other existing interval/tolerance constant belonging to the pre-existing
tests. `git diff` on all three files shows **zero removed lines** — this is a
pure addition of new test classes. The new EDGAR tests reuse
`TestThreadSafety`'s exact `0.15s` / `0.6x` numbers verbatim.

## Full-suite result

`pytest tests/test_edgar_fundamentals.py tests/test_fmp_client.py
tests/test_gdelt_rate_limiter.py tests/test_cross_process_throttle.py -q`:
134 passed. `ruff check . --select=F821,F822,F823,E9` (the actual CI-enforced
gate, per `.github/workflows/ci.yml`): clean.

# Robinhood login single-flight + daemon-scheduled daily login — walkthrough

Branch: `rh-scheduled-login-single-flight`. Context: shrink step 5
(`.claude/shrink_step5_retire_main_py_implementation_plan.md`), operator
decision 6 and section 5 "Traps" → "Robinhood login".

## Why

- The 08:45 ET weekday launchd run of `main.py` is what triggers the day's
  Robinhood device-approval push today. `main.py` is being retired, so the
  daemon needs to start that login itself at a fixed time the operator can
  approve (decision 6).
- `start_login` had no single-flight guard (plan trap). A daemon refresh and a
  webapp `POST /brokerage/refresh` could each launch a worker: two pushes, two
  competing sessions.

## 1. Single-flight in `data/robinhood_login.py`

- New module lock `_start_lock`, held across "is a job running?" → `Popen` →
  register. `_active_job` points at the last started job, the only one that can
  still be running. (`_jobs_lock` stays separate so status polls never wait on
  a `Popen`.)
- Semantics:
  - `refresh` while a `refresh` runs → returns the running job (join). No new
    worker, no second push. `login_blocking("refresh")` then waits on it.
  - Any other combination while a job runs (`connect` while anything runs;
    `refresh` while a `connect` runs) → raises `RobinhoodLoginInProgress`
    (subclass of `RobinhoodLoginFailed`; `.job`, `.requested_mode`). Reason: a
    `connect` carries candidate credentials the running job was not started
    with, and a `connect` job fetches no snapshot, so joining across modes
    would silently answer a different question.
  - Once the running job is terminal (succeeded/failed/timeout/cancelled) the
    next start launches a new worker.
- API: `api/pilots_api.py` maps `RobinhoodLoginInProgress` to HTTP 409 on
  `/brokerage/connect` and `/brokerage/refresh`, with a plain-string detail
  from `api/_rh_login.py::login_in_progress_detail` (job id + mode only, never
  credentials; a string so the webapp's `String(body.detail)` shows it as is).
  The refresh endpoint catches it before its generic `except Exception → 502`.
- Scope limit (disclosed): the guard is per process. The daemon hosts both the
  scheduled login and the Pilots API routes in one process, which is the trap
  named in the plan. A separate process (the standalone Data/Metrics APIs
  calling `fetch_account_snapshot` with `ROBINHOOD_AUTO_REFRESH_ENABLED=true`,
  which is the live value) can still start its own worker. A cross-process
  lock (e.g. `fcntl.flock` held by the parent for the job's life) is a possible
  follow-up; it was left out because it needs per-test lock-path isolation
  under xdist and was not asked for.

## 2. Scheduled daily login in `desktop/daemon_runtime.py`

- `maybe_run_scheduled_robinhood_login(now_utc=None) -> Optional[str]`,
  gated by `ROBINHOOD_SCHEDULED_LOGIN_ENABLED` (default False → returns before
  any I/O). Called from both `_timer_loop` per-wake spots next to the other
  self-gated hooks. Never raises (outer `try/except`, CONSTRAINT #6).
- Fires on a US/Eastern weekday at/after `ROBINHOOD_SCHEDULED_LOGIN_TIME_ET`
  (default `08:40`), once per ET day. No holiday calendar (same limitation as
  `is_extended_hours`).
- Dedup / restart safety: the day is claimed BEFORE anything that could prompt
  — first `self._scheduled_login_claimed_date`, then
  `OUTPUT_DIR/robinhood_scheduled_login_state.json`
  (`last_attempted_et_date`, `last_outcome`, `job_id`, `last_error_code`,
  `updated_at`; atomic write via `reporting.atomic_write.atomic_write_json`).
  "Attempted" = the later of the in-process claim and the durable date, so a
  same-day restart does not prompt again, and a failed state write still
  blocks a same-process re-prompt. A corrupt state file reads as "not
  attempted" (fail toward prompting once, never toward silently never
  prompting).
- Skips (still claiming the day): no `RH_USERNAME`/`RH_PASSWORD`
  (`skipped_no_credentials`); cached snapshot newer than today's target
  (`skipped_fresh`, via `_latest_account_snapshot_fetched_at()` = max
  `fetched_at` of `HistoricalStore(readonly=True).latest_account_snapshot()`
  and the JSON cache — read-only, never logs in); a running `connect`
  (`skipped_login_in_progress`). `start_login` raising anything else →
  `start_failed` + WARNING alert. Otherwise `started` + INFO alert
  ("approve the push within 180s").
- Freshness rule follows the spec literally: a snapshot taken before today's
  08:40 (even at 08:30) is "older than today's scheduled time", so the hook
  still prompts.
- Non-blocking: `start_login("refresh")` spawns the killable worker and
  returns. A daemon watcher thread polls `get_login_state` every 2 s (stops on
  daemon shutdown; bounded by the job's own `RH_LOGIN_DEADLINE_SECONDS`) and
  records `last_outcome`/`last_error_code`, then sends INFO (succeeded) or
  WARNING (failed/timeout/cancelled, "use Refresh in the webapp"). All alerts
  use `dedup_key="robinhood_scheduled_login"`.
- It does not trigger a pipeline cycle after the login; the next interval
  cycle (or the step-5.3 skill's `POST /run`) picks up the fresh snapshot.

### Waking near 08:40 with `ORCHESTRATOR_INTERVAL_SECONDS=3600`

- `seconds_until_scheduled_login(now)`: seconds to the next due target
  (`0.0` if due now and not yet attempted; skips weekends and an
  already-attempted today), `None` when disabled/invalid.
- `_bounded_wait_timeout(base)`: `min(base, max(until + 1 s, 5 s))`; exactly
  `base` when disabled.
- Parked branch (interval ≤ 0): `wait(_bounded_wait_timeout(3600))`.
- Interval branch: `_wait_out_interval(interval)` replaces the single
  `wait(interval)`. It waits in slices ending just after the due time, runs
  the hook on an early wake, and resumes waiting to the ORIGINAL deadline, so
  the interval-cycle cadence is not shortened. With the flag off the first
  slice is exactly `wait(interval)` (pinned by the pre-existing
  `TestTimerLoopRaceOrdering` test, which asserts the timeout equals 300).
- Result: the hook fires ~1 s after 08:40 regardless of the interval.
- `start()` also creates the timer thread when this flag is on at interval 0
  (startup snapshot, like `WEEKLY_DIGEST_ENABLED`/`GOOGLE_TRENDS_ENABLED`).

## 3. Settings plumbing

- `settings.py`: `ROBINHOOD_SCHEDULED_LOGIN_ENABLED: bool = False`,
  `ROBINHOOD_SCHEDULED_LOGIN_TIME_ET: str = "08:40"`, module helper
  `parse_scheduled_login_time()`. The field validator normalizes a valid time
  to `HH:MM` and keeps an invalid one verbatim with a warning (never raises —
  a bad `.env` value must not take down settings load); the daemon treats it
  as disabled and warns once per value.
- `shared/env_io.py` `ALLOWED_KEYS`: both (non-secret).
- `settings_keysets.SAFETY_CRITICAL_KEY_REASONS`: the ENABLED flag (so a
  `DANGEROUS_KEYS` member, typed confirmation on write, shown in Feature Flags
  under "Write & Execution Gates"). Same class as `BROKERAGE_REFRESH_ENABLED`
  (a real login), but unattended and recurring. The time is ordinary.
  Note: `ROBINHOOD_AUTO_REFRESH_ENABLED` itself is NOT in `DANGEROUS_KEYS`; the
  closer precedent for "starts a real login" is `BROKERAGE_REFRESH_ENABLED`.
- `webapp/src/api/mock.ts`: added to `MOCK_DANGEROUS_KEYS` and
  `FEATURE_FLAGS_TUNABLE_DEFS` (parity test
  `tests/test_feature_flags_registry.py::test_mock_ts_feature_flags_parity`).
- `.env.example` documented. Not added to `_TUNABLE_GROUPS`; the Settings
  Reference screen's universal boolean toggle covers the flag automatically.
  No `help_content` entry was needed by any test.
- Regenerated `docs/settings_field_census.{json,md}` and
  `docs/settings_liveness.json` (both new fields classify `live_safe`; the
  timer-thread-at-interval-0 caveat is the same as `WEEKLY_DIGEST_ENABLED`'s
  and is stated in the field description).

## 4. Tests

- `tests/test_robinhood_login.py::TestSingleFlight` (stub worker, no real
  Robinhood): join same-mode refresh (one `Popen`), refuse connect-while-
  refresh, refresh-while-connect, connect-while-connect, subclass check, new
  job after terminal, cancelled job frees the slot, 8 concurrent refresh
  callers → exactly one worker, `login_blocking` waits on the existing job,
  `login_blocking` raises in-progress when a connect runs. New autouse fixture
  resets `_active_job` and kills any job a test leaves running. The stub
  gained a `delayed_success` behavior.
- `tests/test_brokerage_connect.py`: 409 on refresh and connect when a login
  is running; detail is a string, names the job, never echoes credentials.
- `tests/test_daemon_runtime.py::TestScheduledRobinhoodLogin` (frozen `now_utc`,
  fake `start_login`, tmp `OUTPUT_DIR`): disabled no-op + no file, defaults,
  before-time, at-time, later-same-day, weekends, DST (EST and EDT), once per
  day then next weekday, restart-safe, in-process claim survives a failed
  state write, fresh snapshot skip, stale/missing snapshot prompts, no
  credentials, running connect, start failure contained + alerted + not
  retried, unexpected exception contained, corrupt state file, invalid time
  warns once, watcher records/alerts outcome, `seconds_until` math (incl.
  Friday → Monday), `_bounded_wait_timeout`, timer thread at interval 0, a
  3600 s interval wait is cut to ~121 s, `_wait_out_interval` keeps its
  deadline and runs the hook. Plus `TestLatestAccountSnapshotFetchedAt`.

## Enabling (operator, step 5.3 cutover)

See `docs/RUNBOOK.md` §5.1a. Do it in the same change that unloads the
`com.investyo.daily-advisory` launchd job, or no morning prompt arrives.

## Open / uncertain

- Cross-process single-flight (see §1).
- It prompts on market holidays (no holiday calendar).
- A daemon first started after 08:40 on an un-attempted weekday prompts
  immediately, even late in the evening. A cut-off (e.g. only until 20:00 ET)
  would be a small follow-up if wanted.
- `AsyncDataFetchStep` can still block a cycle up to 180 s on a live login
  when `ROBINHOOD_AUTO_REFRESH_ENABLED=true` (separate plan trap, not touched
  here: `pipeline/production_steps.py` was off-limits).

## Follow-up (branch `rh-login-evening-cutoff-cross-process`)

Closes the first two "Open / uncertain" items above.

### A. Evening cut-off for the scheduled login

- New setting `ROBINHOOD_SCHEDULED_LOGIN_CUTOFF_ET` (str, default `"18:00"`),
  same HH:MM validator/normalizer as `ROBINHOOD_SCHEDULED_LOGIN_TIME_ET` (one
  validator covers both; the warning names the field). `ALLOWED_KEYS`,
  `.env.example`, census + liveness regenerated (classifies `live_safe`).
  Not a `DANGEROUS_KEYS` member: it can only narrow when a prompt is sent.
- `desktop/daemon_runtime.py`: `_scheduled_login_window()` returns
  `(start, cutoff)` or `None` (disabled, invalid time, invalid cut-off, or
  cut-off <= start; each warns once per value pair). A login is only STARTED
  in `[start, cutoff)` ET on a weekday.
- At/after the cut-off, `maybe_run_scheduled_robinhood_login` returns `None`
  BEFORE the claim: no in-process claim, no state-file write. So a daemon
  first woken at 19:00 does nothing that day and fires the next weekday at
  the target. One INFO line per ET day per process.
- `seconds_until_scheduled_login` skips today once past the cut-off, so the
  next target is the next weekday's start: `_bounded_wait_timeout` stops
  shortening waits in the evening (no wake every 5 s until midnight).
- The in-progress skip handles a login in ANOTHER process (`exc.job is
  None`): logs the owner, stores the owner's `job_id` if present.
- Tests (`TestScheduledRobinhoodLogin`, frozen times): 17:59 fires; 18:00,
  18:01, 19:00 don't and leave no state file / claim; a daemon at 19:00
  waits for Tuesday 08:40 and fires then; `seconds_until` rolls over at
  18:00; Friday evening → Monday; bounded wait not shortened after the
  cut-off; DST (winter EST, and the first weekday after DST ends); custom
  cut-off; invalid / non-increasing cut-off disables with one warning;
  default + normalization; another process's login is left alone.

### B. Cross-process single-flight in `data/robinhood_login.py`

- Inside `_start_lock`, after the in-process check, `start_login` takes
  `fcntl.flock(LOCK_EX | LOCK_NB)` on `<OUTPUT_DIR>/robinhood_login.lock`
  and writes `<OUTPUT_DIR>/robinhood_login_owner.json` (pid, job_id, mode,
  started_at; temp + `os.replace`). The fd lives on the job (`_xlock_fd`);
  `_xlock_holder` tracks which job in this process holds it.
- Release (idempotent, `_release_cross_process_lock`): in `_enforce_deadline`'s
  `finally` -- that thread runs until the job is terminal no matter who made
  it terminal (event drain, `cancel_login`, deadline) -- and immediately if
  `Popen` fails. Because a second `flock` from the SAME process on a new fd
  conflicts, `start_login` releases a terminal holder's lock itself before
  re-acquiring (the deadline thread polls every 0.5 s). Sidecar deleted (if
  still ours) before unlocking.
- Held by another process → `RobinhoodLoginInProgress(None, mode, owner=...)`
  for any mode; `.owner` keeps only the four diagnostic fields; message
  never includes credentials. Same-process behaviour from #1082 unchanged.
- Callers: `api/_rh_login.py::login_in_progress_detail` handles `job=None`
  (409 detail: "in another process (pid …, job_id …)"); `fetch_account_snapshot`
  tier 3 already catches any exception and falls back to DB/JSON cache (now
  tested end to end with a real held lock); the daemon hook (see A).
- No stale locks: kernel drops an `flock` with the process. The worker does
  not inherit the fd, so an orphaned worker does not hold it.
- No `fcntl`, or lock file can't be opened/locked → per-process only, one
  warning.
- Tests isolation: root `conftest.py::_isolate_robinhood_login_lock_in_tests`
  sets `_lock_dir_override` to a per-test temp dir (otherwise xdist workers
  would refuse each other) and resets `_xlock_holder`.
- Tests (`tests/test_robinhood_login.py::TestCrossProcessLock`, stub worker
  only): default path is `OUTPUT_DIR`; lock held on another open file
  description refuses refresh and connect with owner info, no `Popen`, no
  credential leakage; sidecar written while held and removed on release;
  `Popen` failure releases; no-fcntl fallback warns once; **two real
  processes** (`tests/fixtures/robinhood_login_lock_holder.py`): the second
  is refused while the first holds the lock, and can start once the first
  is SIGKILLed (its orphaned stub worker still alive); and once the first
  finishes its job and exits normally. Plus
  `tests/test_brokerage_connect.py` (409 for both endpoints with `job=None`)
  and `tests/test_robinhood_portfolio.py` (tier-3 fallback to stale cache
  while another process holds the lock, no worker spawned).

### Still open / uncertain

- Disclosed edge: if the lock-holding process dies while its worker is
  still waiting for approval, the orphaned worker can still complete its own
  login while another process starts a new one (two prompts, rare).
- `main.py --refresh-account` (`force=True`) while another process holds
  the lock now falls back to the cached snapshot instead of logging in; the
  operator retries after the other login finishes.
- The lock file lives in `OUTPUT_DIR`; processes configured with different
  `LOCAL_DATA_ROOT`/`OUTPUT_DIR` values do not share it.
- Holiday calendar still not handled.

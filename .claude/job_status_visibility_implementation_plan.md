# Implementation Plan: Global Job-Status Visibility in the Pilots PWA

**Audience note:** this document is written to be self-contained. You should not need
any conversation history to execute it — everything you need is below or in the cited
files. If you find a claim here that no longer matches the code (line numbers drift),
trust the code and adapt; the *intent* of each step is what matters.

**Branch:** `job-status-visibility` (this file already lives on it — commit your work
here, don't create a second branch). Open the PR from this branch when done.

**Repo conventions you must follow** (see the repo's own `CLAUDE.md` for the full
text — this is the subset that applies to this change):
- This touches `api/_jobs.py`/`api/control_api.py` (runtime backend) — never commit
  directly to `main`; this branch + a PR is required.
- `webapp/src/api/` changes must keep `mockApi` (`webapp/src/api/mock.ts`) and
  `liveApi` (`webapp/src/api/client.ts`) in exact parity, with explicit `http<T>()`
  generics. Run the `api-parity-reviewer` review step (described in this repo's agent
  definitions) after touching those three files.
- `webapp/src/` changes need `npm run --prefix webapp typecheck` clean AND an actual
  browser check for anything UI-visible — a clean typecheck alone does not prove a
  screen renders or behaves correctly.
- Do not create any new settings flag for this feature (see Non-goals).
- Every Implementation Plan in this repo must include a documentation-update step —
  see "Documentation updates" below; it's part of the deliverable, not optional
  cleanup.

---

## 1. Problem statement

The operator (this platform's one human user) cannot tell, from the Pilots PWA
webapp, whether a **data backfill** job or a **model retraining** job is currently
running, whether a launch attempt got rejected because something else is already
running, or whether something has stopped. This is a real, verified gap, not a
misunderstanding:

- Four data-ingestion backfill scripts (`scripts/backfill_edgar_fundamentals.py`,
  `scripts/backfill_news_history.py`, `scripts/backfill_news_history_from_audit.py`,
  `scripts/backfill_sentiment_history.py`) are CLI-only today. They are **not** in
  `cli_introspect/targets.py`'s `TARGETS` list, so they never made it into
  `cli_introspect/command_manifest.json`, and the webapp's Commands screen cannot
  launch or track them at all. The only status signal today is raw stdout in a
  terminal someone has to be watching.
- Model retraining (`train_lgbm`/`train_meta`) genuinely *is* wired into the backend
  Jobs system (`api/_jobs.py`'s `JobManager`), but that status is trapped in each
  screen's own local React state. `Commands.tsx`, `Console.tsx`, and `Models.tsx`
  each poll independently and lose track of an in-flight job the instant their
  component unmounts (navigate away, close a modal) — even though the job is still
  running server-side.
- There is no backend endpoint to list *all* tracked jobs — only single-job lookup by
  an ID the caller already has to already know.
- `TopStatusBar.tsx` is the one piece of UI chrome rendered on every screen (mounted
  once in `App.tsx`, above `<Routes>`) and already shows daemon heartbeat, execution
  mode, macro regime, and kill-switch state — but has zero awareness of the Jobs
  system today. There is no persistent, cross-screen "something is running"
  indicator anywhere in the app.
- The Jobs system is single-flight/reject-on-conflict, not a real queue. A launch
  attempt while something's already running today gets a bare `RuntimeError` → HTTP
  409 with a plain string message — no structured way for the UI to say "here's the
  job that's already running, want to see it?"

## 2. Goal

Make "is anything running right now, and what" a genuine, always-visible answer in
the webapp — visible from any screen, surviving navigation — and get the four
backfill scripts onto the same tracked-job rails model retraining already partially
has, by joining the *existing*, working manifest-command pipeline rather than
building something new for them.

## 3. Non-goals — do not build these

- **No real job queue.** Do not build a dispatch system that auto-runs job B once
  job A finishes. The existing single-flight-reject-with-409 behavior stays exactly
  as-is; this plan only makes that behavior *visible* (a clear "already running as
  job X" message with a way to jump to and see it) instead of a dead-end error
  string.
- **No new settings flag.** This feature rides entirely on the existing
  `JOBS_API_ENABLED` (default `True`) and `COMMAND_EXECUTION_ENABLED` (default
  `False`) gates. Do not add anything to `settings.py` for this.
- **No persistence of job history across an API process restart.**
  `JobManager._jobs` stays a plain in-memory dict, exactly as it is today. This is a
  disclosed, accepted limitation — do not add a database table or JSON file for job
  history as part of this change.
- **`scripts/retrain_models.py` stays out of the webapp entirely.** This is the
  combined LGBM+meta-labeler orchestrator used by the monthly `launchd` cron and the
  `trigger_model_retraining` MCP tool. It has no Jobs-system path today and isn't
  reachable from the webapp at all. Adding a whole new launcher for it is a
  materially bigger piece of work than "join the existing pipeline" (what the four
  backfill scripts get) — leave it alone. Do not add it to `cli_introspect/targets.py`.
- **No consolidation of the three screens' existing local polling loops.**
  `RunCommandControl.tsx`, `Console.tsx`, and `Models.tsx` keep their own local
  `useState`/polling exactly as today (their own log streams, their own "job I just
  launched" UI). They only gain one new `catch` branch each (see §9). The new global
  store described below is *additive*, not a replacement for these.

---

## 4. Backend — `api/_jobs.py`

Current relevant state (`JobManager`, confirmed in the file as of this writing):

```python
class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def start_job(self, job_type: JobType, params: Optional[Dict[str, Any]] = None) -> JobRecord:
        ...
        with self._lock:
            ...
            for rec in self._jobs.values():
                if not rec.handle.is_running():
                    continue
                if job_type == JobType.COMMAND:
                    if rec.job_type == JobType.COMMAND and rec.command_name == command_name:
                        raise RuntimeError(
                            f"Command '{command_name}' is already running (ID: {rec.job_id})"
                        )
                elif (rec.single_flight_key or rec.job_type.value) == (single_flight_key or job_type.value):
                    raise RuntimeError(
                        f"Job of type '{job_type.value}' conflicts with already-running "
                        f"job '{rec.job_type.value}' (ID: {rec.job_id})"
                    )
            ...

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        ...
```

### 4a. Add `JobConflictError`

Add this class near the top of the module, after the `JobType` enum and before
`JobRecord`:

```python
class JobConflictError(RuntimeError):
    """Raised by JobManager.start_job when a same-type (or, for
    TRAIN_LGBM/TRAIN_META, same-single-flight-group) job is already running, or a
    same-command_name COMMAND job is already running. Carries the existing job's
    identity as structured fields -- not just a prose message -- so
    api/control_api.py's 409 handler can build a body the frontend can act on
    (attach to / poll the existing job) instead of a dead-end string. Subclasses
    RuntimeError so any code that only catches RuntimeError keeps working
    unmodified."""

    def __init__(
        self,
        message: str,
        *,
        existing_job_id: str,
        existing_job_type: str,
        existing_command_name: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.existing_job_id = existing_job_id
        self.existing_job_type = existing_job_type
        self.existing_command_name = existing_command_name
```

### 4b. Replace the two `raise RuntimeError(...)` sites

Inside `JobManager.start_job`'s conflict-detection loop, replace both raises with
`JobConflictError`, keeping the **exact same message text** (only the exception type
and the new keyword-only fields change):

```python
                if job_type == JobType.COMMAND:
                    if rec.job_type == JobType.COMMAND and rec.command_name == command_name:
                        raise JobConflictError(
                            f"Command '{command_name}' is already running (ID: {rec.job_id})",
                            existing_job_id=rec.job_id,
                            existing_job_type=rec.job_type.value,
                            existing_command_name=rec.command_name,
                        )
                elif (rec.single_flight_key or rec.job_type.value) == (single_flight_key or job_type.value):
                    raise JobConflictError(
                        f"Job of type '{job_type.value}' conflicts with already-running "
                        f"job '{rec.job_type.value}' (ID: {rec.job_id})",
                        existing_job_id=rec.job_id,
                        existing_job_type=rec.job_type.value,
                        existing_command_name=rec.command_name,
                    )
```

### 4c. Add `JobManager.list_jobs`

Add this method after `get_job`:

```python
    def list_jobs(self) -> List[JobRecord]:
        """Every job this process has tracked, most-recently-created first.
        In-memory only -- lost on API process restart, same as self._jobs already
        is; no new persistence added here."""
        with self._lock:
            records = list(self._jobs.values())
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records
```

---

## 5. Backend — `api/control_api.py`

Current relevant state (confirmed): `POST /jobs` (`create_job`), `GET /jobs/{job_id}`
(`get_job_status`), `POST /jobs/{job_id}/cancel` (`cancel_job`), and
`GET /jobs/{job_id}/stream` (SSE log tail) all live in a block gated by
`_require_jobs_api_enabled()` (checks `settings.JOBS_API_ENABLED`). `create_job`'s
current exception mapping:

```python
    try:
        rec = job_manager.start_job(jtype, body.params)
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=redact_line(str(err))) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=redact_line(str(err))) from err
    except PermissionError as err:
        raise HTTPException(status_code=403, detail=redact_line(str(err))) from err
```

### 5a. Add the `JobConflictError` branch — BEFORE `except RuntimeError`

`JobConflictError` subclasses `RuntimeError`, so Python's `except` matches the
**first** matching clause top-to-bottom — this branch must come first or it will
never fire:

```python
    from api._jobs import JobConflictError, JobType, job_manager  # add JobConflictError to the existing import

    try:
        rec = job_manager.start_job(jtype, body.params)
    except JobConflictError as err:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": redact_line(str(err)),
                "job_id": err.existing_job_id,
                "job_type": err.existing_job_type,
                "command_name": err.existing_command_name,
            },
        ) from err
    except RuntimeError as err:
        raise HTTPException(status_code=409, detail=redact_line(str(err))) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=redact_line(str(err))) from err
    except PermissionError as err:
        raise HTTPException(status_code=403, detail=redact_line(str(err))) from err
```

FastAPI serializes `detail={...}` as `{"detail": {...}}` in the response body. This
reproduces the exact structured-409 shape `api/pilots_api.py`'s
`run_forecast_backfill_endpoint` already uses for the identical problem
(`{"detail": {"detail": "...", "job_id": "<id>"}}`) — read that function for the
precedent if anything here is ambiguous.

### 5b. Add `GET /jobs`

Add this endpoint next to the existing `POST /jobs`/`GET /jobs/{job_id}` routes, gated
identically to `GET /jobs/{job_id}` (read-only — `require_read_token`, not
`require_command_token`):

```python
@app.get(
    "/jobs",
    dependencies=[Depends(require_read_token), Depends(_require_jobs_api_enabled)],
)
def list_jobs(active_only: bool = False, limit: int = 50) -> Dict[str, Any]:
    """List jobs this process has tracked (in-memory only), most-recently-created
    first. Powers the webapp's global job-status indicator so an in-flight job
    started from one screen stays visible from every other screen, and so a
    same-type/same-command launch attempt can be checked before it's even
    attempted.

    `active_only=true` filters to jobs still running. `limit` (default 50, clamped
    to [1, 200]) bounds the response -- JobManager._jobs is never evicted for the
    life of the process, so this endpoint does not add eviction, only response-side
    pagination.
    """
    from api._jobs import job_manager

    limit = max(1, min(limit, 200))
    records = job_manager.list_jobs()
    if active_only:
        records = [r for r in records if r.handle.is_running()]
    records = records[:limit]
    return {
        "jobs": [
            {
                "job_id": rec.job_id,
                "job_type": rec.job_type.value,
                "status": rec.status(),
                "exit_code": rec.exit_code(),
                "is_running": rec.handle.is_running(),
                "cancellable": rec.cancellable,
                "command_name": rec.command_name,
                "created_at": rec.created_at,
            }
            for rec in records
        ]
    }
```

Place this route so it does not collide with `GET /jobs/{job_id}` — FastAPI/Starlette
matches `/jobs` (no path segment) against the static route regardless of declaration
order relative to `/jobs/{job_id}`, but for readability put `GET /jobs` right after
`POST /jobs` and before `GET /jobs/{job_id}`.

---

## 6. Manifest — `cli_introspect/targets.py`, regeneration, freshness test

### 6a. New `TARGETS` entries

Open `cli_introspect/targets.py` and find the existing entry for
`scripts/repair_price_bars_adjustment.py` — copy its exact shape for these four new
entries (append them to the `TARGETS` list):

```python
Target(
    "path",
    "scripts/backfill_edgar_fundamentals.py",
    "backfill_edgar_fundamentals.py",
    "python scripts/backfill_edgar_fundamentals.py",
),
Target(
    "path",
    "scripts/backfill_news_history.py",
    "backfill_news_history.py",
    "python scripts/backfill_news_history.py",
),
Target(
    "path",
    "scripts/backfill_news_history_from_audit.py",
    "backfill_news_history_from_audit.py",
    "python scripts/backfill_news_history_from_audit.py",
),
Target(
    "path",
    "scripts/backfill_sentiment_history.py",
    "backfill_sentiment_history.py",
    "python scripts/backfill_sentiment_history.py",
),
```

(Match the real `Target(...)` constructor signature already used in this file — the
positional args above are illustrative of intent; read the file's actual dataclass/
namedtuple definition and conform to it exactly.)

CLI args each script exposes (auto-captured by `cli_introspect.capture` from each
script's real `argparse` block — do not hand-author JSON for these):
- `backfill_edgar_fundamentals.py`: `--tickers` (**required**, str), `--since`
  (optional, default `"2015-01-01"`).
- `backfill_news_history.py`: `--tickers` (optional, default `"all"`), `--months`
  (optional, float, default `6.0`).
- `backfill_news_history_from_audit.py`: `--tickers` (optional, default `"all"`),
  `--months` (optional, float, default `6.0`).
- `backfill_sentiment_history.py`: `--tickers` (optional, default `"all"`),
  `--months` (optional, float, default `5.0`), `--sources` (optional, default
  `"gdelt,edgar,finnhub,reddit"`), `--max-seconds-per-symbol` (optional, float,
  default `300.0`).

### 6b. Guardrails — deliberately none beyond what already exists

Do **not** add any of these four to `HIGH_STAKES_COMMANDS` (in
`gui/orchestrator_runner.py`). All four are upsert/insert-only against their own
tables (no destructive delete step), strictly less risky than
`repair_price_bars_adjustment.py` (which deletes rows before refetching) — and that
script isn't gated either. Adding a gate here would be *inconsistent* with that
existing precedent, not more consistent with it.

Do add a one-line comment near the new `TARGETS` entries noting: `--tickers all` runs
of `backfill_edgar_fundamentals.py`/`backfill_sentiment_history.py` can take many
minutes against real, paid/rate-limited external APIs (FMP, EDGAR, GDELT, Finnhub,
Reddit), and the spawned subprocess has no wall-clock timeout of its own — the
Commands screen's existing Cancel button is the mitigation, same as it already is for
`repair_price_bars_adjustment.py --tickers all`.

### 6c. Regenerate the manifest

Run:

```bash
python scripts/build_command_manifest.py
```

and commit the resulting diff to `cli_introspect/command_manifest.json`. Confirm the
diff adds **exactly** the four new commands (a `generated_at` timestamp field
changing is expected and fine; no other unrelated churn should appear).

### 6d. Close the freshness gap

`tests/test_command_manifest_freshness.py` today only checks `strategy_registry` /
`options_strategy_registry` / `paper_broker_options_strategy_registry` against their
live Python registries — it does **not** check that the manifest's `commands` list
matches `cli_introspect.targets.TARGETS`. This means forgetting step 6c silently
produces no CI failure. Add a new test to this file:

```python
def test_manifest_commands_match_targets_names_exactly():
    from cli_introspect.targets import TARGETS
    data = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_names = {c["name"] for c in data.get("commands", [])}
    dead_letters = set(data.get("dead_letters", []))
    target_names = {t.name for t in TARGETS}
    # A target that dead-lettered (introspection genuinely failed) is not "missing"
    # in the drift sense this test checks -- only a target that silently never made
    # it into either list indicates a stale, un-regenerated manifest.
    missing = target_names - manifest_names - dead_letters
    assert not missing, (
        "cli_introspect/command_manifest.json is missing TARGETS entries -- "
        "regenerate with `python scripts/build_command_manifest.py`.\n"
        f"Missing: {sorted(missing)}"
    )
```

(Adapt field names to whatever `_MANIFEST_PATH`/JSON shape this file already uses —
read the existing tests in it first for the exact conventions: fixture names, how the
manifest JSON is loaded, etc.)

Also add one parametrized test to `tests/test_cli_introspect.py` confirming each of
the four new scripts introspects cleanly (produces a non-`None`/non-dead-lettered
spec with the args listed in §6a) — this is the thing that would tell you if one of
these scripts' `argparse` block is structured in a way `cli_introspect.capture`
can't handle.

---

## 7. Frontend — new shared job-status store

Mirror the existing `webapp/src/components/ExecutionModeContext.tsx` pattern
**exactly** — it is a confirmed 3-file split, done this way specifically so Vite Fast
Refresh doesn't invalidate on every edit:

- `webapp/src/context/executionModeContext.ts` — pure `createContext` + types.
- `webapp/src/hooks/useExecutionMode.ts` — pure `useContext` hook.
- `webapp/src/components/ExecutionModeContext.tsx` — the actual `*Provider`
  component (`useApi` + `useAutoPoll`/`usePoll`).

Read all three of those files before writing the new ones below — copy their
conventions (naming, export shape, how `useApi`/`usePoll` are used) precisely.

### 7a. `webapp/src/context/jobStatusContext.ts` (new)

```typescript
import { createContext } from "react";
import type { JobRecord } from "../api/types";

export interface JobStatusState {
  /** Every job GET /jobs returned on the last successful poll, most-recent first
   *  (server-sorted). Includes terminal jobs up to the endpoint's `limit` -- NOT
   *  filtered to active-only; use activeJobs / the helpers below for that. */
  jobs: JobRecord[];
  /** Derived: jobs not in a terminal status. */
  activeJobs: JobRecord[];
  loading: boolean;
  error: string | null;
  reload: () => void;
  /** True if a job of this job_type is currently active (job_type !== "command";
   *  for "command" jobs use isCommandActive). */
  isJobTypeActive: (jobType: string) => boolean;
  /** True if a "command"-type job with this exact command_name is active. */
  isCommandActive: (commandName: string) => boolean;
}

export const DEFAULT_JOB_STATUS_STATE: JobStatusState = {
  jobs: [],
  activeJobs: [],
  loading: true,
  error: null,
  reload: () => {},
  isJobTypeActive: () => false,
  isCommandActive: () => false,
};

export const JobStatusCtx = createContext<JobStatusState>(DEFAULT_JOB_STATUS_STATE);
```

### 7b. `webapp/src/hooks/useJobStatus.ts` (new)

```typescript
import { useContext } from "react";
import { JobStatusCtx, type JobStatusState } from "../context/jobStatusContext";

export function useJobStatus(): JobStatusState {
  return useContext(JobStatusCtx);
}
export type { JobStatusState };
```

### 7c. `webapp/src/components/JobStatusContext.tsx` (new)

```typescript
import type { ReactNode } from "react";
import { api } from "../api/client";
import { useApi } from "../hooks/useApi";
import { usePoll } from "../hooks/usePoll";
import { JobStatusCtx, type JobStatusState } from "../context/jobStatusContext";

const TERMINAL_STATUSES = new Set(["success", "failed", "cancelled", "unknown"]);
const POLL_MS = 3000;

export function JobStatusProvider({ children }: { children: ReactNode }) {
  const { data, loading, error, reload } = useApi(() => api.listJobs(), []);
  // A plain usePoll (not useAutoPoll) -- deliberately independent of the
  // market-session/tab-visibility/category auto-refresh gates, mirroring
  // TopStatusBar's own kill-switch poll: whether *anything* is running is an
  // operational-awareness signal, not a battery-optimization concern.
  usePoll(reload, POLL_MS, true);

  const jobs = data?.jobs ?? [];
  const activeJobs = jobs.filter((j) => !TERMINAL_STATUSES.has(j.status));

  const value: JobStatusState = {
    jobs,
    activeJobs,
    loading,
    error: error != null ? String(error) : null,
    reload,
    isJobTypeActive: (jobType) => activeJobs.some((j) => j.job_type === jobType),
    isCommandActive: (commandName) =>
      activeJobs.some((j) => j.job_type === "command" && j.command_name === commandName),
  };

  return <JobStatusCtx.Provider value={value}>{children}</JobStatusCtx.Provider>;
}
```

### 7d. Wire into `App.tsx`

Add the import and nest `JobStatusProvider` alongside the existing
`ExecutionModeProvider` (same nesting level — order between the two doesn't matter,
they're independent) so it wraps `TopStatusBar` and `<Routes>`.

---

## 8. Frontend — `webapp/src/api/` (types.ts, client.ts, mock.ts)

Read `webapp/src/api/types.ts`'s `ApiError` and `ForecastBackfillConflictError`
classes, and `webapp/src/api/client.ts`'s `runForecastBackfill` function, before
writing this section — they are the exact precedent to copy.

### 8a. `types.ts` — add `JobConflictError`

```typescript
export class JobConflictError extends ApiError {
  existingJobId: string;
  existingJobType: string;
  commandName: string | null;
  constructor(message: string, existingJobId: string, existingJobType: string, commandName: string | null) {
    super(message, 409);
    this.name = "JobConflictError";
    this.existingJobId = existingJobId;
    this.existingJobType = existingJobType;
    this.commandName = commandName;
  }
}
```

Also add a `JobsListResponse` interface: `{ jobs: JobRecord[] }` (reuse the existing
`JobRecord` type already defined in this file for `createJob`/`getJobStatus`).

### 8b. `client.ts` — add `listJobs`, change `createJob`

Add, next to the existing `createJob`/`getJobStatus`/`cancelJob` block:

```typescript
listJobs: (activeOnly = false, limit = 50) =>
  http<JobsListResponse>(`/jobs?active_only=${activeOnly}&limit=${limit}`),
```

Change `createJob` from its current shared-`http<T>()` form to a bespoke `fetch()`
copying `runForecastBackfill`'s exact structure (same base-URL resolution, same
header construction, same try/catch-network-error shape) — the reason: `http()`'s
generic error path does `String(body.detail)`, which on a structured 409 body
produces the literal string `"[object Object]"` and loses the existing job's id
entirely. This is `runForecastBackfill`'s own already-documented reason for
bypassing `http()`; copy that reasoning and structure verbatim, adapted for
`/jobs`'s response shape and `JobConflictError`'s fields instead of
`ForecastBackfillConflictError`'s.

### 8c. `mock.ts` — parity

Add `mockApi.listJobs` returning the same shape `mockApi.getJobStatus` already
derives (running-for-N-seconds-then-terminal simulation — reuse whatever helper that
function already uses).

Add conflict simulation to `mockApi.createJob`: before creating a new mock job, scan
existing in-flight mock jobs for one with the same `job_type` (or, for `"command"`,
the same `command_name` param) that's still simulated as running, and if found throw
`JobConflictError` with that entry's real id/type/command_name. This is required —
if the live path can throw `JobConflictError` and the mock path can't produce the
same shape, mock/live parity is broken and any offline component test exercising the
conflict branch (§9) cannot run against mock data.

---

## 9. Frontend — `TopStatusBar.tsx` new chip

Read the existing kill-switch dialog code in this file first (`Modal`, `Chip` are
already imported here) — reuse both rather than inventing a new UI primitive.

Add `import { useJobStatus } from "../hooks/useJobStatus";`, a `jobsModalOpen`
state, and a chip between the existing Kill Switch and Session indicators:

```typescript
const { activeJobs } = useJobStatus();
const [jobsModalOpen, setJobsModalOpen] = useState(false);
...
<button
  onClick={() => setJobsModalOpen(true)}
  disabled={activeJobs.length === 0}
  data-testid="jobs-status-chip"
  // style to visually match the existing Chip usages in this file; tone should
  // read as neutral/idle when empty and "something's happening" when active
>
  {activeJobs.length === 0 ? "Jobs: idle" : `Jobs: ${activeJobs.length} running`}
</button>
```

On click (only reachable when `activeJobs.length > 0`), open a `Modal` listing each
active job: `job_type` (or `command_name` when `job_type === "command"`), elapsed
time via this file's existing `timeAgo()` helper, and — for `cancellable` jobs — a
Cancel button calling `api.cancelJob(job_id)` then `reload()` (from
`useJobStatus()`).

---

## 10. Frontend — `JobConflictError` handling at the three launch sites

### 10a. `RunCommandControl.tsx`

In `runCommand()`'s `catch` block, add a branch before the generic error handling:

```typescript
if (err instanceof JobConflictError) {
  setActiveJob(await api.getJobStatus(err.existingJobId));
  toast.error(`${label} is already running as job ${err.existingJobId}`);
  return;
}
```

Keep all existing local state (`activeJob`, `recentJobs`, `error`, `pendingConfirm`)
exactly as-is — only the `catch` branch changes.

### 10b. `Console.tsx`

Identical treatment in `handleLaunch()`'s `catch` block — attach to the existing job
id instead of just toasting a dead-end error. Keep `activeJob`/`jobHistory` and the
existing `GET /jobs/{id}` polling loop as-is.

### 10c. `Models.tsx` — real bug fix, not just cosmetic

Current `handleRetrain()` catch (approximately):

```typescript
catch (e) {
  const msg = e instanceof ApiError && e.status === 409
    ? "Another training job is already running."
    : ...;
  setRetrainErrors((prev) => ({ ...prev, [m.name]: msg }));
}
```

Change to:

```typescript
catch (e) {
  if (e instanceof JobConflictError && (e.existingJobType === "train_lgbm" || e.existingJobType === "train_meta")) {
    // Attach to the existing job instead of dead-ending -- a double-click or a
    // second tab's "Retrain Now" can now be tracked here too.
    setTrainingJobs((prev) => ({ ...prev, [m.name]: e.existingJobId }));
  } else {
    const msg = e instanceof ApiError && e.status === 409
      ? "Another training job is already running."
      : ...; // keep existing fallback logic
    setRetrainErrors((prev) => ({ ...prev, [m.name]: msg }));
  }
}
```

Today, a double-click or a second browser tab's "Retrain Now" gets a dead-end error
with no way to see the real job's progress. After this change it correctly attaches
to and displays the already-running job. This is worth calling out explicitly in
your PR description as a fixed bug, not just new functionality.

---

## 11. Documentation updates (required, part of this PR)

- **`CLAUDE.md`** (this repo auto-syncs `CLAUDE.md` → `AGENTS.md` via
  `.claude/hooks/sync_agent_docs.sh` — edit `CLAUDE.md` only, do not hand-edit
  `AGENTS.md`): add a new bullet near the existing `COMMAND_EXECUTION_ENABLED`/Jobs-
  system bullets covering: `GET /jobs` (what it returns, how it's gated),
  `JobConflictError`'s structured 409 body, the four new manifest-listed backfill
  commands, and the new `JobStatusContext`/`TopStatusBar` chip. Explicitly restate
  the non-goal (no real dispatch queue — single-flight-reject made visible, not
  replaced) so a future reader doesn't assume this is a queue.
- **`docs/architecture/observability-and-apis.md`**: find the existing "Background
  job execution" bullet (covers `api/_jobs.py`) and *extend* it in the same style as
  its existing follow-up sentences (this file's convention is one long evolving
  bullet per subsystem with dated additions, not a new top-level bullet per change).
  Document `GET /jobs` (shape, `active_only`/`limit` params, same auth as
  `GET /jobs/{job_id}`), `JobConflictError`'s structured 409 body, and the four new
  `TARGETS` entries joining the existing `launch_manifest_command` pipeline.
- **`docs/architecture/webapp-and-gui.md`**: add a new bullet (near this file's
  existing "Background job execution" / "Command execution from the Commands screen"
  bullets) documenting `JobStatusContext`/`useJobStatus`/the `TopStatusBar` jobs
  chip — name the 3-file split, the 3s polling cadence, and which three screens now
  branch on `JobConflictError`.
- No change needed to `docs/signals/*.md`, `docs/VALIDATION_STRATEGY_FIX_LOG.md`, or
  `docs/RUNBOOK.md` — this isn't a signal/strategy change and adds no new
  operational runbook step.

---

## 12. Tests required

- `tests/test_control_api.py::TestJobsApi` (existing class) — extend with:
  - list-all returns all tracked jobs, newest first
  - `active_only=true` filters to running jobs only
  - `limit` clamps to `[1, 200]`
  - `POST /jobs` on conflict returns the structured 409 body with the existing job's
    id (cover both the plain-type conflict and the `command_name`-scoped conflict,
    plus the `train_lgbm`/`train_meta` shared single-flight group)
  - `GET /jobs` respects the same `JOBS_API_ENABLED`/`require_read_token` gating as
    `GET /jobs/{job_id}` (mirror however the existing tests in this class check that)
- `tests/test_command_manifest_freshness.py` — the new test from §6d.
- `tests/test_cli_introspect.py` — the new parametrized test from §6d.
- `webapp/src/components/JobStatusContext.test.tsx` (new) — provider fetches
  `GET /jobs`, derives `activeJobs` correctly, `isJobTypeActive`/`isCommandActive`
  behave correctly, polls on the fixed interval.
- `webapp/src/components/TopStatusBar.test.tsx` (existing — **must be updated**):
  every render helper needs a `<JobStatusProvider>` wrapper now that `TopStatusBar`
  calls `useJobStatus()`. Add new tests: chip shows "Jobs: idle" with no active
  jobs, shows "Jobs: N running" and opens the modal with real job rows when active
  jobs exist, modal's Cancel button calls `api.cancelJob` and reloads.
- `RunCommandControl`/`Console`/`Models` existing test files — extend each with one
  new test asserting the `JobConflictError` branch attaches to the existing job
  rather than showing a generic error.

Run the `api-parity-reviewer` review (an agent definition in this repo, triggered by
"anything under `webapp/src/api/`") after `client.ts`/`mock.ts`/`types.ts` land — it
checks `listJobs`'s `http<T>()` generic is explicit, `createJob`'s bespoke-fetch
live/mock parity, and that new exports have real callers.

---

## 13. Verification checklist — run this before opening the PR

1. **Backend tests:**
   ```bash
   pytest tests/test_control_api.py::TestJobsApi tests/test_command_manifest_freshness.py tests/test_cli_introspect.py
   ```
   All new + existing tests pass.
2. **Manifest regeneration:**
   ```bash
   python scripts/build_command_manifest.py
   ```
   Confirm the diff to `cli_introspect/command_manifest.json` adds exactly the four
   new commands (plus the expected `generated_at` churn), then re-run the freshness
   test to confirm it now passes against the regenerated file.
3. **Typecheck:**
   ```bash
   npm run --prefix webapp typecheck
   ```
   Must be clean.
4. **Webapp unit tests:**
   ```bash
   npm run --prefix webapp test -- JobStatusContext TopStatusBar RunCommandControl Console Models
   ```
   All pass.
5. **Browser check** (launch the app locally — this repo's own `verify-webapp` skill
   or `npm run --prefix webapp dev` + manual navigation):
   a. Set `COMMAND_EXECUTION_ENABLED=true` and `JOBS_API_ENABLED=true` (or use mock
      mode, `VITE_USE_MOCK=true`, which exercises the same UI path via the updated
      `mockApi`).
   b. Navigate to the Commands screen, launch `backfill_news_history_from_audit.py`
      (the cheapest of the four — zero network calls) with `--tickers AAPL`.
   c. Confirm `RunCommandControl`'s own status line shows "running", **and** confirm
      `TopStatusBar`'s new chip simultaneously flips to "Jobs: 1 running".
   d. Navigate to a different screen — confirm the `TopStatusBar` chip **still**
      shows the job as running. This is the core "doesn't disappear on navigation"
      requirement — if this fails, the feature has not achieved its purpose.
   e. Click the chip, confirm the modal lists the running job with its real
      `command_name`/elapsed time.
   f. Wait for the job to finish (or cancel it via the modal's Cancel button) —
      confirm the chip returns to "Jobs: idle".
   g. Attempt to launch the same command a second time while the first is still
      running (fast double-click, or two browser tabs) — confirm the UI shows the
      "already running as job X" message, not a raw 409/500, and that it attaches
      to (rather than errors out on) the existing job's log.
   h. Repeat step (g) via `Models.tsx`'s "Retrain Now" (`train_lgbm`) to confirm the
      training-job conflict path surfaces correctly there too.
   i. Toggle `COMMAND_EXECUTION_ENABLED=false` and reload — confirm the Commands
      screen degrades honestly (existing behavior, unchanged) and that
      `TopStatusBar`'s chip still functions (`GET /jobs` is gated only by
      `JOBS_API_ENABLED`, independent of `COMMAND_EXECUTION_ENABLED`).

## 14. When you're done

Commit incrementally on this branch (`job-status-visibility`), make sure every item
in §13 actually passed (not "should pass" — CLAUDE.md's own stated policy in this
repo is that verification is mandatory, not advisory), and open the PR from this
branch. In the PR description, call out the `Models.tsx` double-click/second-tab bug
fix from §10c explicitly — it's a real behavior fix bundled into this feature, worth
flagging so a reviewer doesn't miss it as "just" new functionality.

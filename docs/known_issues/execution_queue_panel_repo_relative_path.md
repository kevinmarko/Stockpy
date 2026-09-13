# Known issue (2026-09-12, fixed 2026-09-13): `shared/robinhood_execution_panel.py` read the Robinhood execution queue from a repo-root-relative path instead of `settings.OUTPUT_DIR`

**Status: fixed.** Found live during a 2026-09-12 audit session; fixed on
branch `fix-execution-queue-local-data-root`.

## What happened

`shared/robinhood_execution_panel.py` (the read side of the Tier 8
Robinhood execution bridge — see CLAUDE.md's "Branch Workflow" era docs and
`docs/architecture/execution.md`) hardcoded its four canonical file paths as:

```python
_REPO_ROOT = Path(__file__).resolve().parent.parent
EXECUTION_QUEUE_PATH: Path = _REPO_ROOT / "output" / "execution_queue.json"
EXECUTION_RECEIPTS_PATH: Path = _REPO_ROOT / "output" / "execution_receipts.jsonl"
NOTIFIED_STATE_PATH: Path = _REPO_ROOT / "output" / "execution_queue_notified.json"
EXECUTION_PLACED_PATH: Path = _REPO_ROOT / "output" / "execution_placed.jsonl"
```

This is the same bug class documented in `docs/known_issues/forecast_tracker_local_data_root_split.md`
(PR #720) and CLAUDE.md's `settings.LOCAL_DATA_ROOT` bullet (PR #718): a
plain `Path(__file__).resolve()` anchor instead of resolving through
`settings.OUTPUT_DIR` (itself `settings.LOCAL_DATA_ROOT`-anchored, default
`~/.stockpy_local/output`).

Unlike those two prior instances, this was **reader/writer skew within the
same feature**, not a store that never migrated its own default. Both real
writers of these four files already resolved correctly:

- `execution/queue_builder.py::emit_execution_queue()` — writes
  `execution_queue.json` + `execution_queue_notified.json` via
  `Path(settings.OUTPUT_DIR)` (lazily imported, "avoid import cycle").
- `execution/receipts_store.py::_resolve_output_dir()` — writes
  `execution_receipts.jsonl` + `execution_placed.jsonl` via the identical
  `settings.OUTPUT_DIR` pattern, with a `Path("./output")` fallback if
  importing `settings` itself fails.
- `investyo_mcp_server.py`'s `get_execution_queue` MCP tool independently
  reads `settings.OUTPUT_DIR / "execution_queue.json"` (fixed earlier — see
  `tests/test_investyo_mcp_server.py`'s own docstring for that history).

`shared/robinhood_execution_panel.py` — the module backing `GET
/agentic/status` / `GET /agentic/discovery`'s queue summary
(`api/pilots_api.py`) and the two consuming Pilots PWA components
(`components/Pilots/ExecutionQueue.tsx`, `components/ExecutionQueueSection.tsx`)
— was the one piece of this bridge still pointing at the old location.

## Real impact

This repo runs many concurrent git worktrees. An untracked file like
`output/execution_queue.json` is worktree-local in git, so a queue written
by the real orchestrator daemon in one worktree/checkout is invisible from
every other one's repo-relative `output/` directory — **even within a
single worktree**, since `settings.OUTPUT_DIR` defaults to
`~/.stockpy_local/output`, not the checkout's own `output/` directory, once
`LOCAL_DATA_ROOT` is in effect (the default for every install since PR
#718).

Confirmed live on this machine at the time of the audit:

- Repo-root `output/` (this worktree, and the primary checkout): held only
  a **stale** `execution_queue.json` (Aug 6) and
  `execution_queue_notified.json` (Aug 6) in the primary checkout — this
  worktree had neither file at all.
- `~/.stockpy_local/output/` (the real, `LOCAL_DATA_ROOT`-anchored
  location): held the **live**, more recent `execution_queue.json` (Aug 21,
  `mode: "live"`, 5 real gated intents) and `execution_queue_notified.json`
  (Aug 21).

Before the fix, `read_execution_queue()` called with no explicit `path` (the
real production call pattern — `api/pilots_api.py::execution_panel.read_execution_queue()`
takes no argument) returned `None` in this worktree, and a **stale, Aug-6
snapshot** in the primary checkout — never the live queue. This was not
user-facing-crashing: both consuming Pilots PWA components degrade honestly
to an empty-queue message (CONSTRAINT #4/#6) rather than fabricating or
raising, so the failure mode was silent under-reporting, not an error.

## The fix

`shared/robinhood_execution_panel.py` now resolves its output directory via
a new `_resolve_output_dir()` helper, deliberately mirroring
`execution/receipts_store.py::_resolve_output_dir` — its writer-side
sibling in the same Tier 8 bridge — field for field: `settings` is imported
lazily (inside the function, not at module top level, preserving this
module's "stays importable in minimal test environments" design intent),
and a `Path("./output")` fallback applies only if importing `settings`
itself raises. The four module-level path constants
(`EXECUTION_QUEUE_PATH`, `EXECUTION_RECEIPTS_PATH`, `NOTIFIED_STATE_PATH`,
`EXECUTION_PLACED_PATH`) are still plain `Path` objects computed once at
import time — every existing external consumer (tests that
`monkeypatch.setattr(mod, "EXECUTION_QUEUE_PATH", ...)`,
`legacy/streamlit_command_center/panels/launcher.py`'s `.name` display use)
keeps working unchanged.

No data migration was needed: `execution_queue.json` and
`execution_queue_notified.json` are atomically **overwritten** every cycle
(`tmp.write_text(...); tmp.replace(path)`), not accumulated history like
`forecast_errors` was in the PR #720 incident — the stale repo-root file has
no ongoing value and was left in place, superseded by the live one at the
correct location. `execution_receipts.jsonl`/`execution_placed.jsonl` had no
content at either location at the time of the fix (the human-in-the-loop
`robinhood-execution` skill had not yet appended any receipts on this
machine), so there was nothing to reconcile there either. An operator who
wants the general-purpose `scripts/migrate_to_local_data_root.py` migration
tool anyway (e.g. to clear out other stale repo-relative artifacts) can
still run it — item 1 of its `build_items()` list already covers the whole
`output/` directory as one `dir` move.

## Verification

- `read_execution_queue()` (no explicit path) in this worktree now returns
  the real, live snapshot (`mode="live"`, 5 intents) instead of `None`.
- `tests/test_robinhood_execution_panel.py::TestModuleSurface` gained three
  regression tests: the four constants pin against `settings.OUTPUT_DIR` (not
  a hardcoded string, so the assertion stays correct under an operator's
  own `LOCAL_DATA_ROOT`/`OUTPUT_DIR` override too), `_resolve_output_dir()`
  reflects a monkeypatched `settings.OUTPUT_DIR` live, and it falls back to
  `./output` if importing `settings` itself fails. All three were confirmed
  to genuinely fail against the pre-fix code (via a revert-and-rerun) before
  being locked in.
- Full `tests/test_robinhood_execution_panel.py` (69/69, minus 4 pre-existing,
  unrelated `numba`/`pandas_ta_classic` caching failures in this Python 3.14
  environment — reproduced independently of this change via a bare `import
  shared.help_content`), `tests/test_pilots_execution_queue.py`,
  `tests/test_queue_builder.py`, `tests/test_pilots_api.py::TestAgenticStatus`,
  and `tests/test_investyo_mcp_server.py -k ExecutionQueue` all pass.

## Related, out of scope for this fix

`shared/dead_letter.py::DEAD_LETTER_PATH` has the identical bug pattern
(`_REPO_ROOT / "output" / "dead_letter.json"`, no `settings.OUTPUT_DIR`
resolution) — same bug class, different artifact (the pipeline dead-letter
report, not the execution queue), flagged separately rather than fixed
inline here to keep this PR scoped to the reported issue.

## Related

- CLAUDE.md's `settings.LOCAL_DATA_ROOT` bullet — the migration this
  incident is a gap in.
- `docs/known_issues/forecast_tracker_local_data_root_split.md` — the first
  confirmed instance of this bug class (PR #720).
- `execution/receipts_store.py::_resolve_output_dir` — the pattern this fix
  mirrors.

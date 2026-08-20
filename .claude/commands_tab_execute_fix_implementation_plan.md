# Commands tab: fix Configure→Execute no-op stub — Implementation Plan

## Context

The Pilots PWA's Commands screen has two ways to run a command:

1. **Free-text Command Bar → Run** — fully wired: creates a job, shows toasts, inline
   errors, a live job-status line, and a streaming log. Works correctly and is tested.
2. **"🛠️ Configure" card button → "Execute Command 🚀"** (form-builder modal) — was a
   **no-op stub**. `Commands.tsx`'s `onRunCommand` prop passed to `<CommandFormBuilder>`
   was an empty function with a misleading comment ("Managed inside CommandLauncher via
   job creation"). Clicking it did nothing and silently closed the modal — zero feedback
   because no attempt was ever made. Zero test coverage existed for this path.

Along the way we also found the grid card's 📋 copy button gave no "Copied" confirmation
(unlike every other copy control on this screen), and that `CommandFormBuilder`'s own
`argTokens` computation had a latent bug: it bundled the subcommand name into the same
token list as flags/values, the wrong shape for the job-creation API (`RunCommandControl`
keeps the subcommand name separate from `args`, matching `commandParse.ts`'s convention).

Separately, the user initially asked to also flip `COMMAND_EXECUTION_ENABLED`'s default to
`True` in `settings.py` and "update the .env" to make execution "fully live." Investigation
found the user's real `.env` (main checkout, not this worktree) **already has**
`COMMAND_EXECUTION_ENABLED=true`, `JOBS_API_ENABLED=true`, and a matching
`ORCHESTRATOR_DAEMON_TOKEN`, with the orchestrator daemon + APIs already running live.
Asked the user directly whether to still flip the code default given `settings_keysets.py`
calls this "the highest-risk flag in this group" (gates the global kill switch and a forced
Robinhood re-login) and there's a same-day precedent (PR #812) of reverting a blanket
`default=True` flip as scope creep — **user chose to leave the code default `False`**. This
PR therefore touches only the two frontend bugs above; no `settings.py`/`.env` changes.

## Scope

1. Extract `RunCommandControl` out of `Commands.tsx`'s free-text `CommandBar` into its own
   reusable component, and embed it inside `CommandFormBuilder`'s modal so "Execute" reuses
   the exact same tested job-creation/toast/status/log/cancel logic Path 1 already has —
   instead of a second, easily-forgotten implementation.
2. Fix `CommandFormBuilder`'s `argTokens` computation to keep the subcommand name separate
   from flag/value tokens, matching `commandParse.ts`'s contract.
3. Give the grid card's 📋 copy button a "Copied" (✅) confirmation state.
4. Update/add tests for both fixes.

**Out of scope:** `settings.py` default change, any `.env` edit, new required-field
validation in the form builder, any change to `COMMAND_EXECUTION_ENABLED`/
`JOBS_API_ENABLED`/`ORCHESTRATOR_DAEMON_TOKEN` behavior.

## Design

- **New file** `webapp/src/components/RunCommandControl.tsx` — pure move of the function
  out of `Commands.tsx`, exported, behavior unchanged.
- **`Commands.tsx`** — imports `RunCommandControl` from the new location instead of
  defining it locally; drops the no-op `onRunCommand` stub prop; adds a `copiedName` state
  to `CommandLauncher` so the grid's copy button flips to ✅ on click.
- **`CommandFormBuilder.tsx`** — drops the `onRunCommand` prop from its public API; derives
  a `subcommandSpec: CommandSpec | null` alongside its existing `activeSpec`; stops folding
  the subcommand name into `argTokens`; replaces the old "Execute Command 🚀" button with an
  embedded `<RunCommandControl command={command} subcommand={subcommandSpec}
  argTokens={argTokens} disabled={false} composed={composed} resetKey={composed} />`
  rendered below the Reset/Close row. Execute no longer auto-closes the modal — the operator
  watches the job status/log in place and dismisses manually via "Close".

### Accepted trade-offs (documented, not blockers)
- A high-stakes command's confirm dialog now renders as a `<Modal>` nested inside
  `CommandFormBuilder`'s own `<Modal>`. Verified working end-to-end in a real browser
  (desktop viewport) — functions correctly, cosmetically double-darkens the backdrop.
- The Run button's label changes from "Execute Command 🚀" to the shared control's "Run".
- Closing the modal mid-run drops the UI (job keeps running server-side) — mirrors existing
  behavior when navigating away from the Command Bar mid-run.
- No new required-field validation was added to the form builder (pre-existing gap, out of
  scope for this fix).

## Documentation-update assessment

Checked `docs/architecture/webapp-and-gui.md` and the root `CLAUDE.md`/`AGENTS.md` for
references to `Commands.tsx`/`CommandFormBuilder.tsx` component internals specific enough to
need a one-line update for the new `RunCommandControl.tsx` file. Neither enumerates
component-level file structure for this screen (both describe capability/behavior, which is
unchanged by this fix — same manifest resolution, same `COMMAND_EXECUTION_ENABLED` gating,
same `HIGH_STAKES_COMMANDS` confirmation requirement). No doc changes required.

## Verification performed

- `npm run --prefix webapp typecheck` — clean.
- `npm run --prefix webapp test` (vitest) — full suite green: 168 files / 1778 tests passing
  (one `StrategyMatrix.test.tsx` failure was observed once, confirmed a pre-existing flake
  unrelated to this change — reproduced clean on both the pre-change baseline and on a
  re-run with this change applied).
- Live browser check (desktop viewport, mock API, this worktree's own dev server):
  - Configure → main.py → Run (no high-stakes flag) → job created, "Job … — success" status
    line, live log region, and Recent Execution Runs entry all appeared inside the modal;
    modal did not auto-close. No new console errors.
  - Configure → main.py → toggle `--refresh-account` → Run → nested "Confirm command"
    dialog opened inside the form-builder modal with the correct high-stakes reason text →
    "Yes, run it" → job created ("Job … — running"), Cancel button present, log region
    present, Recent Execution Runs updated to 2 entries. No new console errors.
  - Grid card's 📋 copy button on main.py flipped to a green ✅ after click.
  - Mobile viewport (375×812): layout renders correctly (grid, nav, cards). The interactive
    click-through on the nested confirm drawer could not be completed in this session due to
    a Browser-pane tooling timeout unrelated to the app (screenshots/read_page kept working;
    only click dispatch stalled) — not a discovered app defect, but the mobile-specific
    interactive path is unverified beyond the desktop-equivalent logic already confirmed.

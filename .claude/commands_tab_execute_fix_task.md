# Commands tab: fix Configure→Execute no-op stub — Task Tracker

- [x] Investigate root cause: two run paths, Configure→Execute is a no-op stub
- [x] Confirm live `.env` state (main checkout already has execution fully live; code
      default left `False` per user's explicit decision after being asked)
- [x] Extract `RunCommandControl` into `webapp/src/components/RunCommandControl.tsx`
- [x] Wire `Commands.tsx` to import the extracted component; drop the no-op stub prop
- [x] Add grid card "Copied" (✅) feedback state to `CommandLauncher`
- [x] Fix `CommandFormBuilder`'s subcommand/argTokens split bug
- [x] Embed `RunCommandControl` inside `CommandFormBuilder`'s modal; drop `onRunCommand` prop
- [x] Rewrite obsolete `CommandFormBuilder.test.tsx` tests for the new contract
- [x] Add subcommand-args-separation test
- [x] Add nested high-stakes-confirm-dialog test
- [x] Add `Commands.test.tsx` integration test (Configure → subcommand → Run)
- [x] Add `Commands.test.tsx` copy-feedback test
- [x] `npm run --prefix webapp typecheck` clean
- [x] `npm run --prefix webapp test` full suite green (168/168 files, 1778/1778 tests)
- [x] Live browser verification: plain run via Configure → Run
- [x] Live browser verification: high-stakes nested confirm dialog → confirm → job created
- [x] Live browser verification: grid copy button ✅ feedback
- [x] Live browser verification: mobile viewport layout (interactive click-through blocked
      by an unrelated Browser-pane tooling timeout — not an app defect; documented as a
      residual gap)
- [x] Documentation-update assessment (no doc changes required — capability/behavior
      unchanged, only internal wiring fixed)
- [x] Write PR artifacts (implementation plan, task tracker, walkthrough)
- [ ] Commit, push, open PR

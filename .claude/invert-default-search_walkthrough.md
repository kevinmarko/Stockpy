# Invert The Default — Universe-First Search — Walkthrough

## Status: NOT STARTED

This plan and its companion task tracker (`invert-default-search_implementation_plan.md`,
`invert-default-search_task.md`) were handed to the 2026-09-07 audit session alongside the
**Explain This Ticker** plan. Confirmed by inspecting the diff of the feature branch
(`bright_quasar_rises_14h56`, merge-based at `e359775a`) that implemented Explain This Ticker:
**zero files related to this plan were touched.** Specifically, none of the following exist in
that branch's diff:

- No change to `webapp/src/components/SymbolInput.tsx` (the shared combobox this plan's §4/§5
  centers on).
- No change to `webapp/src/screens/Marketplace.tsx` or `webapp/src/navigation.tsx` beyond the
  unrelated `/universe` nav entry added for the Universe Transparency screen.
- No rollback-toggle mechanism (Wave 0 scaffold) anywhere in the diff.
- None of the 9 `SymbolInput` call sites listed in the plan's §0/§3 were touched.

**Why:** the actual prompt handed to the Antigravity build team (see
`explain-ticker_prompt_draft.md` / `explain-ticker_original_request.md` in this same `.claude/`
directory) only requested "Explain This Ticker" + "Universe Transparency" — "Invert The Default"
was a separate plan pair the user is holding for a later session, not part of what was delegated
here. This walkthrough exists so a future session (or the same operator) doesn't have to
rediscover that fact by re-diffing the branch — the plan and task files are committed for
reference and are ready to be picked up whenever this work is actually assigned.

## What's still true and still worth re-checking before starting

- §0's dependency checks are all still open — none were answered by this session.
- The plan's own §6 ("why this plan is riskier than its companion") stands: **do not** start this
  by flipping `SymbolInput`'s default blindly across all 9 call sites. Sector Selection's
  documented `enableFmpSuggestions={false}` opt-out (see `CLAUDE.md`'s "Widened symbol lookup"
  bullet) must be independently re-confirmed as still correct and still the *only* site with that
  specific dead-end risk before any other site's default is flipped.
- The companion "Universe Transparency" terminology this plan's Wave 2 wants to reuse now has
  **two** live implementations in this repo as of this session — `webapp/src/components/
  UniverseCoverage.tsx` (already shipped, embedded in `SettingsUniverse.tsx` at
  `/settings/universe`) and the new `webapp/src/screens/UniverseTransparency.tsx` (shipped in the
  Explain This Ticker branch this session merged and audited, at the new top-level `/universe`
  route). Whichever terminology Wave 2 of this plan settles on must match whatever the
  Explain-This-Ticker audit pass settled on for those two screens — see
  `explain-ticker_walkthrough.md` in this same directory for that resolution before starting
  Wave 2 here, so this plan doesn't invent a third, inconsistent name for the same three-way
  tracked/forecast-covered/everything-else distinction.

No code changes were made for this plan in this session.

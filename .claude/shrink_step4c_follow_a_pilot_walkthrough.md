# Step 4c · Follow-a-Pilot archive — walkthrough

Branch `archive-follow-a-pilot` (from `origin/main` @ 80c32cee). Plan:
`.claude/shrink_step4_archive_implementation_plan.md` §4c.

## Why
The operator dropped Follow-a-Pilot and every follow was already cancelled.
Kept: the Pilots catalog/marketplace (browse, compare, detail), the advisory
execution queue, agentic Robinhood trading, the MCP server, and
`FOLLOW_API_TOKEN` (the general command token for ~20 endpoints).

## Commits
1. **Golden first.** `tests/test_compose_advisory_only_golden.py` +
   `tests/fixtures/compose_advisory_only_queue.golden`, captured by running the
   test with `REGEN_COMPOSE_GOLDEN=1` against untouched `execution/compose.py`
   on 80c32cee. Scenario: NVDA BUY (not held), AAPL BUY (held), MSFT SELL
   (held, full exit) and a below-floor TSLA BUY; fixed `now` inside RTH, an
   explicit calm macro DTO, and kill-switch paths pinned to `tmp_path`, so the
   gate genuinely evaluates and passes. (`.golden`, not `.json`, because
   `.gitignore` ignores `*.json` and the rules forbid `git add -f`.)
2. **The archive** (everything below).

## compose.py (trap 2)
The `FOLLOW_MIN_CONVICTION` import was already inlined as `0.0` on main
(#1065). This PR removes the whole follow branch: `write_follow_source`,
`follow_source_id`, `FollowSourceClaims`, the `FollowsStore` enumeration,
`extra_follow_pilot_ids`, the netting/force-exit math and its helpers. It
keeps `read_source`/`write_source`/`write_advisory_source`, the corrupt/stale
refusal, macro threading, and `ComposedIntent.sources` (queue_builder still
emits `overridden: []`). Evidence:
- The byte comparison against the pre-change golden passes after the
  rewrite. It covers every payload field, including `client_order_id`,
  `sources`, `overridden`, gate verdicts and sizing.
- `strategy_id` is not a payload key; it is folded into `client_order_id`.
  `test_golden_actually_covers_the_ids_and_attribution_fields` recomputes each
  id with `strategy_id="advisory"` and requires an exact match.
- `tests/test_compose.py::TestComposeHasNoFollowDependency` checks that
  compose has no `pilots` import at all. It also checks that the queue is
  still written with `pilots.mirror`, `pilots.follows_store` and
  `pilots.portfolio_attribution` blocked via `sys.modules[...] = None`.
- `test_leftover_follow_state_on_disk_is_ignored`: a stale `follows.json` plus
  a corrupt `queue_sources/follow-*.json` no longer block or change the queue.

## API (`api/pilots_api.py`)
Removed `GET/PUT /follows`, `POST /pilots/{id}/follow`, their request models and
`_NOT_FOLLOWABLE_DETAIL`. `_pilot_summary` drops `aum_proxy`,
`followers_proxy` and `followable`. They are removed rather than nulled: with no
follows they were always 0, and every consumer (PilotCard, Marketplace's
popularity sort, Comparison, the MCP pilot-picker) only used them for follow
UX. `/agentic/status` drops `follows`; `/thresholds` drops `follow_min_amount`.
`Follow:<id>` queue attribution (`/execution-queue` `follow_type`) is kept.
Tests: `TestFollowEndpointsRemoved` (the routes are not registered, and
authenticated calls get 404/405 and write no `follows.json`), plus absence
asserts on the pilot, agentic and thresholds shapes.

## MCP (`investyo_mcp_server.py`)
Chose **retired stubs** for all four (`follow_pilot`, `unfollow_pilot`,
`get_follows`, `get_portfolio_by_pilot`). They keep their signatures so a client
with a cached tool list gets a clear message, never raise, touch no state, and
are `readOnlyHint=True` with no widget meta. `get_portfolio_by_pilot` is stubbed,
not rewired: its attribution is built only from follow targets, so without
follows every position would land in "Unattributed". `get_robinhood_account_snapshot`
and `get_portfolio_summary` already cover the real account. `list_pilots` keeps
working and drops its AUM column and proxies. The `follow-result.html` and
`pilot-portfolio.html` widgets, plus their `_common.js` renderers
(`renderFollowForm`, `renderPortfolioByPilotPanel`, `renderFollowResultCard`,
P&L helpers) and CSS, are removed. The picker and detail widgets no longer
render a Follow form. That leaves 17 widgets.

## Other removals
- `scripts/export_notebooklm.py`: the `FollowsStore` import, the consolidated
  "Active Pilot Follows" section and the signals file's "Active Pilot Strategy
  Subscriptions" section.
- Gravity `step_92_pilots_mirror_quarantine_audit` and its call.
  `compose.py`'s no-order-function guarantee is still enforced by
  `tests/test_pipeline_smoke.py::TestNoOrderFunctions`.
- `tests/test_queue_builder.py`: dropped the test that imported
  `pilots.mirror._follow_rationale`.
- `tests/test_store_isolation_contract.py`: removed the follows_store entry and
  excluded `legacy/` from the `*_store.py` scan. No conftest fixture existed
  for the follows store.
- `tests/test_pilots_strategy_matrix.py`: dropped the now-unused
  `mirror`/`follows_store` import allowances.

## Webapp
Delegated to a subagent against the contract above; I re-ran the checks
myself. Changes:
- Deleted `FollowModal.tsx` (+ test) and `resolveMinAmount.ts`.
- Removed the Follow buttons (PilotDetail, Comparison) and Comparison's
  AUM/Followers/Actions rows.
- Removed the Marketplace "Most Popular" rail and `PopularCard`.
- Removed the "Active follows" sections in Portfolio and SettingsModules, and
  AgenticTrading's follows chip and row.
- NotebookMLExport no longer fetches or exports `followed_pilots`.
- Onboarding drops its "Set amount" step, which only sized a follow; it is now
  two steps.
- `client.ts`/`mock.ts`/`types.ts` lose the follow methods and types.
- `helpContent.ts` loses the `follow minimum` glossary key (also removed from
  keyConcepts) and its copy is reworded.

## Settings
`FOLLOW_MIN_AMOUNT` keeps its field (and its ALLOWED_KEYS/settings_domains
listing) for 4f; its readers (`/thresholds`, the follow response, mirror) are
gone and its description says so. Regenerated `docs/settings_liveness.json` and
`docs/settings_field_census.{json,md}`. The only liveness shift is
`FOLLOW_MIN_AMOUNT` moving live_safe to restart_required: its only remaining
mention is the `settings_domains` name literal.

## Verification
- AST import scan over 437 active `.py` files (excluding `tests/`, `legacy/`
  and `webapp/`): no import of `pilots.mirror`, `pilots.follows_store` or
  `pilots.portfolio_attribution`.
- With those three modules blocked via `sys.modules[...] = None`, the
  following still import: `execution.compose`, `api.pilots_api`,
  `investyo_mcp_server`, `main`, `main_orchestrator`,
  `scripts.export_notebooklm` and `api.data_api`.
- ruff (F821,F822,F823,E9): clean.
- Offline suite: see the PR report.
- Webapp: `tsc --noEmit` is clean and vitest passes 153 files / 1786 tests.
  The subagent loaded the affected routes in mock mode in a real browser; the
  only console errors were the dev-mode service-worker fetch and the live
  `/ws/ticks` socket, neither related to this change.

## Left for later
- `pilots/catalog.py`'s `followable` field (the options Pilots that set it go in 4a).
- The `FOLLOW_MIN_AMOUNT` field itself, in 4f.
- Historical follow comments in `execution/queue_builder.py`,
  `scripts/refresh_validations.py` and `pilots/options_sor.py` (the last two
  are touched by 4a/4b). The rest of `docs/` describes following historically,
  under the new banners.

## Rebase onto 4a (#1067) + 4d (#1068)
The Onboarding "Set amount" step removal was confirmed by the coordinator
(it only sized a follow) and is kept. Conflicts and how they were resolved:
- `CLAUDE.md`/`AGENTS.md`: kept main's 4a and 4d bullets, then this PR's 4c
  bullet; `AGENTS.md` copied from `CLAUDE.md`.
- `docs/architecture/observability-and-apis.md`: kept both the 4a and 4c banners.
- `legacy/README.md` (add/add): main's intro + step 4d section, then this PR's
  step 4c section, in one file.
- `scripts/export_notebooklm.py`: main's 4d sizing-guardrails comment (ETF
  column dropped) with this PR's section numbering; main's "portfolio
  holdings" wording (4a dropped Greeks) without "follows".
- `tests/test_export_notebooklm.py`: this PR's docstring with main's
  "portfolio holdings" wording.
- `tests/test_pilots_api.py`: this PR's side. The follow endpoint tests, incl.
  4a's synthetic non-followable Pilot fixture, only tested follow semantics
  and are dropped with the routes.
- `webapp/src/api/mock.ts`: main's side (4a removed the options Pilots this
  PR had only stripped follow fields from).
- `webapp/src/help/helpContent.ts`: main's editor list (4d dropped the ETF
  editor) minus this PR's "active Pilot follows".
- `webapp/src/screens/Comparison.test.tsx`, `PilotDetail.test.tsx`: this PR's
  side; 4a's followable:false (regime-navigator) Follow-button tests test UI
  that no longer exists.
- Settings artifacts: took main's, then regenerated with
  `scripts/settings_liveness.py --write` and `scripts/measure_settings_census.py --write`.

# Dead code left by shrink step 5: removal plan (PLAN ONLY, nothing changed)

Date: 2026-11-10. Branch at time of writing: `claude/ecstatic-maxwell-f6ruct`.
Tracker line this answers: `.claude/cleanup_hardening_sweep_2026_10_task.md` ("Dead code left by step 5").
CLAUDE.md requires this plan to be reviewed before any code is written (orchestrators/execution are touched).

Method (all excluding `legacy/`, `.venv`, `node_modules`, `.git`): a repo-wide text grep of every file type (py, ts, md, sh,
Makefile, plists, json), plus an AST scan of every non-legacy `.py` file (imports, `from X import`, names, attributes, keyword
args, string constants incl. `getattr`/`patch("...")` strings, docstrings, comments). Dynamic access was checked: the only
`importlib`/`__import__` uses are `importlib.util.find_spec` probes in `shared/ai_control_center.py:233` and
`pilots/settings_meta.py:486` (neither names any symbol here), `scripts/step5_4_verify_cycle.py:257` (imports
`main_orchestrator`) and `data/paper_account_store.py:65` (`threading`). No `from <module> import *`
of `pipeline.advisory_inputs/agentic_queue/context` exists. No `__getattr__`/`pkgutil` module discovery exists.
The scan script lives only in the scratchpad (`refcheck.py`); it is not part of the repo.

Baseline on the current tree (before any edit): `tests/test_engine_status.py tests/test_orchestrator_runner.py
tests/test_universe_retention.py tests/test_advisory_inputs_watchlist_macro.py tests/test_production_steps_universe.py
tests/test_pipeline_package.py tests/test_pipeline_runner.py tests/test_main_py_archive_import_smoke.py
tests/test_daemon_agentic_queue_primary.py` = **147 passed**.
`tests/test_measure_settings_census.py::TestCommittedArtifactIsFresh::test_committed_json_matches_a_fresh_run` is **already
red** in the shared working tree: other concurrent agents have uncommitted edits to `settings.py`, `shared/env_io.py`,
`pilots/settings_domains.py`, `api/pilots_api.py` (git status shows 8 modified files that are not mine). That red is not
caused by this plan, but it means the census/liveness regeneration below must be the LAST step, run once, after every agent's
edits have landed.

---

## 0. Headline findings

| Candidate | Verdict | Why (short) |
|---|---|---|
| `shared/engine_status.py` | **DEAD in production**, test-only + one stale list entry | no importer except `tests/test_engine_status.py` and a string in the archive smoke test |
| `pipeline/agentic_queue.py::daemon_owns_agentic_side_effects` | **DEAD in production**, no test | only `legacy/main.py:655` (archived) called it |
| `pipeline/advisory_inputs.py::build_universe` | **DEAD in production**, but **live in 3 test files** and in `__all__` | one-line wrapper over `build_universe_detailed`; ~19 test call sites pin real behaviour through it |
| 7 `*_fn` fields on `RunContext` | **DEAD in production** (never read), but **constructed at 17 sites** incl. one non-obvious one | read only by archived `legacy/pipeline/steps.py` |
| ML-audit orphans (6 more modules) | all six are *production-orphans* (the heuristic is right); **none is a simple delete** | see section 5 |

**Cross-cutting risk the tracker did not mention: the documented rollback.** `docs/RUNBOOK.md` §3.15 ("Rollback (after step
5.5)") and `legacy/README.md:29` say the way back to `main.py` is `git revert` of the 5.5 PR. `legacy/main.py` still imports
`build_universe` (`legacy/main.py:176`), calls `daemon_owns_agentic_side_effects` (`legacy/main.py:655`) and builds `RunContext`
with all seven `*_fn` fields (`legacy/main.py:313`), and `legacy/pipeline/steps.py:75` reads `ctx.build_universe_fn`. Removing
symbols 2-4 therefore ends the "revert the 5.5 PR" rollback (the revert would restore a `main.py` that fails with
ImportError/TypeError). Symbol 1 has no such coupling. This is Decision D1 below. The live daemon has run
`DAEMON_AGENTIC_QUEUE_MODE=primary` since 2026-10-12 (29 days as of today); that is an operator judgement, not mine.

---

## 1. `shared/engine_status.py` (108 lines)

### Evidence: every remaining reference, outside `legacy/`
| file:line | kind |
|---|---|
| `shared/engine_status.py:1-2,17,53,92` | definition + own docstrings (`engine_status()` at :53, `_paused_for_market_hours()` at :91) |
| `shared/orchestrator_runner.py:1386-1394` | docstring of `heartbeat_age_seconds` ("combine both (see `shared/engine_status.py`)") - comment only |
| `tests/test_engine_status.py` (188 lines, 17 tests) | **test-only**; patches `engine_status_module.orchestrator_runner.*` |
| `tests/test_main_py_archive_import_smoke.py:47` | **test-only**; the string `"shared.engine_status"` in `_ENTRY_POINTS` (the smoke test imports it with archived modules blocked) |
| `tests/test_orchestrator_runner.py:108` | comment only ("gui/engine_status.py depends on this") |
| `docs/FEATURE_TIER_HISTORY.md:2343` | docs-only history entry (leave; history) |
| `.claude/shrink_step5_5_*_plan.md:33,76`, `..._walkthrough.md:20,44`, `.claude/gui_shared_legacy_split_implementation_plan.md:50,134,181`, `.claude/cleanup_hardening_sweep_2026_10_task.md:36` | docs-only (history/trackers) |

Not referenced by: any Python production file, Makefile, `*.command`/`*.sh`, plists, `webapp/` (the badge strings "Engine live",
"Engine idle", "Automatic runs paused", "Engine not started" appear nowhere else), `investyo_mcp_server.py`,
`api/*`, `desktop/*`, `conftest.py`, `docs/architecture/*.md`, `docs/architecture/testing.md` (it does not list this test),
CLAUDE.md/AGENTS.md. The module's own docstring already says "no production code imports this module any more (only its tests do)".
Git: last touched in the 2026-10-29 comments sweep; its consumer (the Streamlit sidebar) was deleted in 2026-09.

### Second-order effect (verified)
`shared/orchestrator_runner.py::state_snapshot_age_seconds` (def at :1405) is read in production ONLY by `engine_status.py:43`.
After deletion its sole callers are three tests (`tests/test_orchestrator_runner.py:125,131,143`). `heartbeat_age_seconds`
stays live (`pilots/observability.py:1730-1742`; `pilots/run_status.py` has its own copy). `shared/market_hours.is_extended_hours`
stays live (daemon, `scripts/preflight_check.py:862`).

### Proposed action
Delete `shared/engine_status.py` and `tests/test_engine_status.py` (precedent: the Streamlit app was "deleted (git history
only)", not archived to `legacy/`; this is a Streamlit sidebar helper, not a restorable runtime path). Then:
- `tests/test_main_py_archive_import_smoke.py`: remove `"shared.engine_status"` from `_ENTRY_POINTS` (:47). Nothing else in the
  file counts the list length (the `== 5` assertion at :65 is for `_ARCHIVED_DOTTED_MODULES`, not this list).
- `shared/orchestrator_runner.py:1393-1394`: drop the "or combine both (see `shared/engine_status.py`)" clause.
- `tests/test_orchestrator_runner.py:104-110`: reword the comment.
- `state_snapshot_age_seconds` + its 3 tests: **Decision D2** (delete as now-orphaned, or keep as a small tested reader).
  Default recommendation: delete both in the same commit, because a function with no production caller is the same class of dead
  code, and its docstring (:1409+) describes `main.py` interval mode. UNVERIFIED: whether an operator script outside the repo
  calls it.

### Tests
Delete: `tests/test_engine_status.py` (all 17 tests exist only to cover dead code). Change: the smoke-test list entry,
and (if D2 = delete) remove `test_state_snapshot_age_seconds_*` x3 at `tests/test_orchestrator_runner.py:125-143`.
Keep: `test_heartbeat_age_seconds_*` (pins live behaviour).

### Docs
None required in CLAUDE.md/AGENTS.md (neither names the module; no mirror edit), `docs/architecture/*.md`, or `testing.md`.
Optional: a one-line dated entry in `docs/FEATURE_TIER_HISTORY.md` recording the removal.
**Settings census / liveness impact: yes.** The module reads `settings.ORCHESTRATOR_EXTENDED_HOURS_ONLY` and is a scanned
production `.py` file: `docs/settings_field_census.json` `read_forms.files_scanned` (369 -> 368, line 2942) and the per-field
read counts (e.g. `form_a_attribute` for `ORCHESTRATOR_EXTENDED_HOURS_ONLY`, ~line 3032) change, and
`docs/settings_liveness.json` `files_scanned` (line 134) changes. Both freshness tests will fail until regenerated (section 8).

### Risk to the live daemon
None at import time: nothing imports it (verified above, AST and text). The auditor's own orphan finding agrees.

---

## 2. `pipeline/agentic_queue.py::daemon_owns_agentic_side_effects` (lines 57-65)

### Evidence
| file:line | kind |
|---|---|
| `pipeline/agentic_queue.py:57` | definition (body: `return resolve_daemon_agentic_queue_mode(value) == "primary"`) |
| `pipeline/agentic_queue.py:9` | module docstring mention |
| `pipeline/production_steps.py:3096` | `AgenticQueueStep` docstring mention (comment-only) |
| `docs/architecture/execution.md:56` | docs-only prose ("`main.py` skipped its own calls in primary via ...") |
| `.claude/shrink_step5_3_agentic_queue_primary_walkthrough.md:26` | docs-only history |
| `legacy/main.py:655-657` | archived caller (out of scope, but see D1) |

No tests (`grep` over `tests/` and `conftest.py` is empty), no `__all__`, no string/`getattr` access, no webapp/MCP/script use.
The live neighbour `resolve_daemon_agentic_queue_mode` is NOT dead (`pipeline/production_steps.py:3002,3140` + tests) and stays.

### Proposed action
Delete the 9-line function. Rewrite the three prose mentions so they stop naming it (`agentic_queue.py:9` docstring,
`production_steps.py:3096` docstring, `docs/architecture/execution.md:56`). Also fix the stale sentence "Deliberately light
(stdlib only at import time) because `main.py` imported it too" (`agentic_queue.py` module docstring): keep the stdlib-only
property (it is still true) but drop the `main.py` reason.

### Tests
None to delete or change. (The primary-mode behaviour pins in `tests/test_daemon_agentic_queue_primary.py` use
`resolve_daemon_agentic_queue_mode`/the step, not this function.)

### Docs
`docs/architecture/execution.md:56` only. CLAUDE.md/AGENTS.md: no change (mention only `DAEMON_AGENTIC_QUEUE_MODE`).
Census/liveness: no settings read in this function; docstring edits in `pipeline/*.py` shift no recorded line numbers
(neither file has a census/liveness site entry; verified by grep of both JSON files).

### Risk to the live daemon
`pipeline/agentic_queue.py` is imported at daemon start (via `production_steps.py:3002`). Deleting a never-called top-level
function cannot fail import. The function is not in the module's `__all__` (none exists).
This is the one the live `primary` queue writer sits next to, so keep the diff to this function and docstrings only
(AST-compare: every other top-level def/class in the file byte-identical).

---

## 3. `pipeline/advisory_inputs.py::build_universe` (lines 199-207)

### Evidence
| file:line | kind |
|---|---|
| `pipeline/advisory_inputs.py:199` | definition: `return build_universe_detailed(snapshot).symbols` |
| `pipeline/advisory_inputs.py:54` | listed in `__all__` |
| `pipeline/advisory_inputs.py:177-180` | `UniverseBuild` docstring ("exactly what :func:`build_universe` returns") |
| `settings.py:1830` | comment (`pipeline/advisory_inputs.py::build_universe`) |
| `tests/test_advisory_inputs_watchlist_macro.py:151,160,166,179,190,205,222` | **test**: `TestBuildUniverse` (7 tests) calls `ai.build_universe(snap)`; docstrings/comments at :1,:5,:60-65,:139,:143 |
| `tests/test_universe_retention.py:28` (import), `:65,84,98,108,117,133,300,310` | **test**: retention/auto-drop behaviour pinned through the wrapper; docstring :6,:42 |
| `tests/test_production_steps_universe.py:397` | **test**: `TestDaemonMatchesAdvisoryInputsUniverse` parity check; docstring :22,:382,:395 |
| `docs/architecture/data-layer.md:27`, `docs/architecture/orchestration-entrypoints.md:15` | docs-only (name `build_universe` in the API list) |
| `docs/known_issues/daemon_universe_watchlist_divergence.md:178`, `.claude/shrink_step5_*` | docs-only history |
| `legacy/main.py:176`, `legacy/tests/test_advisory_inputs_patch_seams.py:87` | archived callers (D1) |

Production callers: **none**. The daemon's `AsyncDataFetchStep` calls `build_universe_detailed` directly
(`pipeline/production_steps.py:153-155`); `api/data_api.py:623` and `data/portfolio_sync.py:898` also name only the detailed builder.
The wrapper's own docstring already says "only tests call this wrapper now that `main.py` is archived".
No `patch("pipeline.advisory_inputs.build_universe")` strings and no `monkeypatch.setattr(ai, "build_universe", ...)`
exist (checked as exact string constants and word-boundary mentions).

### NOT a pure delete
The three test files exercise **live** logic (held/watchlist/discovered union, rating auto-drop, retention, default-ticker
fallback) through the wrapper. Deleting it means rewriting call sites to `build_universe_detailed(snap).symbols`, not deleting
tests. CLAUDE.md names `build_universe_detailed()` as "one builder", so the end state matches the rule.

### Proposed action
Delete `build_universe`, remove `"build_universe"` from `__all__` (:54), fix the `UniverseBuild` docstring (:177-180) and the
`settings.py:1830` comment (same line count: edit in place, do not add/remove a line, or the census line map moves).
Mechanical test edit: replace `ai.build_universe(x)` / `build_universe(x)` with `ai.build_universe_detailed(x).symbols` /
`build_universe_detailed(x).symbols` at the ~19 sites above (and the import at `test_universe_retention.py:28`).
Keep every test; rename `TestBuildUniverse` docstring only. Note `TestDaemonMatchesAdvisoryInputsUniverse` (the step 5.1
equivalence gate) compares the step to `build_universe_detailed`, which the step itself calls (`production_steps.py:153`), so it
becomes a near-tautology; keep it (it still catches the step passing the wrong `watchlist_file`/snapshot), but say so in the
docstring. Whether to drop it is Decision D4 (low stakes).

### Docs
`docs/architecture/data-layer.md:27` (the "`build_universe`/`load_watchlist`" phrase and the "archived `main.py::_build_universe()`
wrapped it too" clause), `docs/architecture/orchestration-entrypoints.md:15` (the "Input builders" list names `build_universe`)
and :16. `docs/known_issues/daemon_universe_watchlist_divergence.md:96-106,156,187` mention `build_universe_fn` (see section 4).
CLAUDE.md/AGENTS.md: no change (they name only `build_universe_detailed()`).

### Risk to the live daemon
`pipeline/advisory_inputs.py` is imported at daemon start. Removing a name from `__all__` and a function that nothing imports
cannot break import; the daemon path (`build_universe_detailed`) is untouched. The only way to break it is a typo while editing
near the function: AST-compare the rest of the module before/after.

---

## 4. `RunContext` `*_fn` fields (`pipeline/context.py:41-47`)

### The seven fields and who reads them
| field | production readers (non-legacy) | test readers | archived readers |
|---|---|---|---|
| `fetch_account_snapshot_fn` | none | none | `legacy/pipeline/steps.py:38` |
| `build_universe_fn` | none | none | `legacy/pipeline/steps.py:75` |
| `build_macro_dto_fn` | none | none | `legacy/pipeline/steps.py:128` |
| `get_provider_fn` | none | none | `legacy/pipeline/steps.py:150` |
| `fetch_bars_fn` | none | none | `legacy/pipeline/steps.py:151` |
| `build_context_extras_fn` | none | none | `legacy/pipeline/steps.py:152` |
| `advisory_evaluate_fn` | none | none | `legacy/pipeline/steps.py:188` |

"None" is established by: no `ast.Attribute` with these names anywhere outside `legacy/`; no `getattr(ctx, "<name>")` (all
`getattr(ctx, ...)` uses name other fields: `scripts/step5_4_verify_cycle.py:323-356`, `main_orchestrator.py:1293,1304`); no
`dataclasses.asdict/fields/replace/vars(ctx)`/`__dict__`/`__dataclass_fields__` on `RunContext`; no test asserts the field set
(`tests/test_pipeline_package.py::TestRunContext` pins `snapshot/symbols/.../stopped` defaults and the three required params only).
All seven legacy reader lines above were checked with grep (`ctx.<field>` in `legacy/pipeline/steps.py`).
Fields that ARE live and must stay: `force_account` (`production_steps.py:3363`), `started_at` (:3280),
`watchlist_file` (:155), and every mutable field below the `*_fn` block.

### Who constructs `RunContext` (AST count: 17 call sites, 0 positional args, all keyword)
Production (3):
- `main_orchestrator.py:397` (`_run_pipeline` sync body, kwargs at :401-407; `build_universe_fn=lambda *a: tickers`)
- `main_orchestrator.py:1416` (`_main_body`, kwargs :1420-1426; the live per-cycle path)
- **`Gravity AI Review Suite.py:3433`** (`_overlay_ctx` in step 28 `run_run_once_orchestrator_audit`, kwargs :3437-3443).
  Easy to miss: the file has a space in its name so it cannot be imported, `tests/` do not exercise this step
  (UNVERIFIED: whether any test runs the suite end-to-end), but it IS launched in production by
  `shared/orchestrator_runner.py:954-996::launch_gravity_audit` (operator-triggered). If the fields are removed from
  `RunContext` and this site is forgotten, step 28 fails at runtime with `TypeError` in a path no test catches.

Tests (14 call sites in 12 files; each passes all seven kwargs; two are `**overrides` factories):
`tests/test_progress_emission.py:102,192` | `tests/test_step5_4_verify_cycle.py:743` | `tests/test_pipeline_runner.py:69`
(factory `_make_ctx`, kwargs :60-66) | `tests/test_production_steps_fund_dtos_dead_letter.py:55` | `tests/test_daemon_advisory_shadow_equivalence.py:66` |
`tests/test_macro_step.py:56` | `tests/test_forecasting_step_fallback_disclosure.py:40` | `tests/test_pipeline_package.py:52`
(factory, kwargs :43-49) | `tests/test_pipeline_smoke.py:211` | `tests/test_production_steps_broker_gate.py:50,207` |
`tests/test_trend_volatility_step.py:65` | `tests/test_production_steps_universe.py:52`.
No test passes any `*_fn` as an override and none reads one back (the `_make_ctx(**overrides)` factories are only called with
non-`*_fn` keys; AST confirmed no `kw:` hit beyond the default blocks).
Archived: `legacy/main.py:313`, `legacy/tests/test_progress_emission.py:228`, `legacy/tests/test_pipeline_package.py:47,125,138`
(not collected by pytest; D1).

### Proposed action
Delete the seven fields from `pipeline/context.py`, drop `Callable` from the `typing` import (all other imports are still
used: `AccountSnapshot` by `snapshot`, `MarketDataProvider` by `market`, `MacroEconomicDTO` by `macro_dto`, `Recommendation`
by `recommendations`, `Any/Dict/List/Optional` throughout), and rewrite the module docstring (it describes `main.py`'s
injected callables and "vestigial" stubs). Then remove the seven kwargs at each of the 17 sites. Because the fields are
required (no default), a missed site fails loudly with `TypeError` at construction, which is the safety net; a leftover
extra kwarg after the field removal also fails loudly, so the change cannot silently half-apply.

Add one cheap guard (allowed under the freeze: tests): an AST test that every `RunContext(...)` call in non-legacy `.py` files
(including `"Gravity AI Review Suite.py"`, read as text, since it cannot be imported) passes only names that are fields of the
dataclass. This is the test that would have caught the Gravity site. UNVERIFIED: that no existing guard already does this.

### Tests
No test exists only for these fields. Change = delete 7 kwarg lines at 14 test sites (mechanical). `TestRunContext`
(`tests/test_pipeline_package.py:70-127`) stays as is (it pins live defaults).

### Docs
`docs/known_issues/daemon_universe_watchlist_divergence.md:96-106,156,187` ("`build_universe_fn` is still an unused stub",
"optional follow-up cleanup"): add a dated status line that the stub was removed. `docs/architecture/execution-boundary.md:73`
mentions `main_orchestrator.py`'s `RunContext(...)` construction only for `watchlist_file` (stays true). CLAUDE.md/AGENTS.md:
no mention. Census/liveness: removing the 7 lines at `Gravity AI Review Suite.py:3437-3443` shifts later recorded sites
(`Gravity AI Review Suite.py:10639` in both `docs/settings_liveness.json:2284` and `docs/settings_field_census.json:3407,3842`),
and `main_orchestrator.py` edits sit after its only recorded site (`main_orchestrator.py:118`) so they do not move it:
regenerate (section 8).

### Risk to the live daemon (highest of the four)
`main_orchestrator.py:1416` runs **every cycle** (`_main_body`), and the daemon's cycle depends on it. A wrong edit there
(a leftover kwarg, or removing the wrong line) raises `TypeError` before any step runs: every cycle would fail closed (no
queue write), not fabricate data, but the live `primary` queue writer would silently stop producing. Mitigations: (1) edit
`pipeline/context.py` and BOTH `main_orchestrator.py` sites and Gravity in ONE commit; (2) import smoke
(`tests/test_main_py_archive_import_smoke.py`) does not construct a `RunContext`, so it will not catch this: also run
`tests/test_main_orchestrator.py`, `tests/test_pipeline_smoke.py`, `tests/test_step5_4_verify_cycle.py` (they build real
contexts); (3) run one real dry cycle: `make verify` (queue mode forced off, no queue/orders/pushes; it fails on synthetic data
so it needs real FMP/FRED keys; UNVERIFIED whether this sandbox has them) or `python3 main_orchestrator.py` dry-run.
Do NOT restart/flip the live daemon's mode as part of this change.

---

## 5. ML-audit "orphaned module" list (verified individually)

How it runs: `.venv/bin/python scripts/local_ci.py --list` shows step `ml-audit` =
`.venv/bin/python scripts/auditor/stockpy_codebase_auditor.py --root . --fail-on HIGH` (profiles pr,quick; only if
`ml/*`, `sizing/*`, `scripts/*forecast_backfill*`, `forecasting_engine.py`, `cnn_lstm_worker.py` changed). I ran the same
command: 372 modules, 18 findings, 0 HIGH/CRITICAL. The `orphaned_module` rule is `LOW`, so it can never fail the gate; removing
or keeping these modules has no `make local-ci` effect. The rule (`scripts/auditor/stockpy_codebase_auditor.py:503-519`) counts a
module as orphaned when no *non-test* module imports it (tests are excluded unless `--include-tests`) and it is not an
`__init__`/entry-point name. Re-running with `--include-tests` shows the real picture: only
`execution/overnight_guardrails.py` is imported by nothing at all; the other five (and `engine_status`) are imported only by tests.

Findings: `execution/overnight_guardrails.py`, `shared/engine_status.py` (section 1), `shared/regime_filter.py`,
`shared/robinhood_mode.py`, `shared/symbol_search.py`, `signals/sentiment_index.py`, `validation/walk_forward.py`.
(The other 11 findings are INVESTYO_* env vars, an `cli_introspect` import cycle and docstring/typing nits: out of scope.)

None is a false positive in the sense "the heuristic missed a production user": I found no production importer, no string/
dynamic load, no webapp/MCP/script/plist use for any of them. Three are "not dead by intent":

| module | prod users | test users | status | proposed action |
|---|---|---|---|---|
| `execution/overnight_guardrails.py` (55 lines) | none (not even tests) | none | **Keep, deliberately unwired.** Its docstring says "NOT YET WIRED ... needs an explicit operator decision"; `docs/module_efficiency_redundancy_audit.md:561` and `docs/test_coverage_analysis.md:184,465` say the same. It is in `execution/` (live-order path) | Keep. Decision D5 (wire / delete / leave). Do not wire as cleanup |
| `validation/walk_forward.py` (439 lines) | none | `tests/test_walk_forward.py` (210) | **Keep.** Module docstring + test header call it a reviewed false positive, "kept, general-purpose". `docs/module_efficiency_redundancy_audit.md:552` calls it superseded by inline walk-forward in `validation/harness.py` (two disagreeing notes) | Keep unless operator decides. D5 |
| `signals/sentiment_index.py` (122 lines) | none; reads `settings.SECTOR_SELECTION_W1/W2` (`:81`), referenced by `settings.py:2925,2930,3365` and `data/sentiment_source_class.py:15` text | `tests/test_sentiment_index.py`, `tests/test_sentiment_index_lookahead.py` | **Keep.** An unwired, tested feature piece of an in-progress (feature-frozen) sentiment/sector effort; removing it also orphans two settings descriptions | Keep. D5 |
| `shared/regime_filter.py` (156) | none | `tests/test_regime_filter.py` | **Dead (Streamlit sidebar helper; docstring says `gui/regime_filter.py` / `gui/app.py`).** Note `docs/architecture/webapp-and-gui.md:16` wrongly lists it among "live backend modules ... used by production code": a factual doc error to fix regardless | Delete module + test (D3) |
| `shared/robinhood_mode.py` (174) | none; `shared/robinhood_execution_panel.py:14,575` only mention it in docstring/comment | `tests/test_robinhood_mode.py` (220) | **Dead (Streamlit helper; header says `gui/robinhood_mode.py`)**; last touched in the Alpaca removal | Delete module + test; fix the 2 docstring mentions (D3) |
| `shared/symbol_search.py` (82) | none | `tests/test_symbol_search.py` (143) | **Dead (Streamlit helper; `gui/symbol_search.py`)** | Delete module + test (D3) |

Heuristic limits worth recording (not defects): it ignores tests (so test-only modules look orphaned), and it cannot see
string/subprocess launches (`python -m`), so it should not be the sole evidence for a deletion. Nothing here depends on that
limit. Side finding (not for this plan): `shared/help_content.py:902,1642` keeps a `sidebar.regime_filter` help entry for the
deleted sidebar; belongs with the tracker's "audit the Python help catalog" item.

For the three Streamlit relics: tests: delete `tests/test_regime_filter.py`, `tests/test_robinhood_mode.py`,
`tests/test_symbol_search.py` (they exist only to cover those modules). Docs: `docs/architecture/webapp-and-gui.md:16` (remove
`regime_filter.py` from the live list); `docs/plans/GUI_OPERATIONAL_IMPROVEMENTS_PLAN.md` is a historical plan (leave).
Each module reads no setting except `robinhood_mode` (`ROBINHOOD_EXECUTION_MODE`/caps via the passed settings object; census
effect UNVERIFIED, regenerate and diff anyway). Daemon risk: none (nothing imports them).

---

## 6. Archive vs delete

Default for everything above: **delete** (git history is the archive), matching the repo's own precedent for the Streamlit
app. `legacy/` is for "working code moved out so it can be restored" and cannot hold a function or dataclass field. The
modules in section 1 and the three Streamlit helpers are not restorable runtime paths. If the operator wants the rollback path
kept (D1), do NOT remove symbols 2-4; there is no partial archive.
If symbols 2-4 are removed, add a short `legacy/README.md` section ("Post-5.5 dead-code removal") recording: what was removed,
the commit that removed it, and that restoring `legacy/main.py` now means checking out the parent of that commit for
`pipeline/{advisory_inputs,agentic_queue,context}.py` as well, and update `legacy/README.md:29` and `docs/RUNBOOK.md` §3.15
"Rollback (after step 5.5)" (`docs/RUNBOOK.md:858` onward) and the paragraph at `docs/RUNBOOK.md:972` ("restore both by reverting the 5.5 PR") accordingly.

---

## 7. Safe ordering (one commit per step, each green before the next)

Branch + PR (runtime/orchestrator files are touched, so not direct-to-main). Plan-review first (this document). Name the PR
artifacts `.claude/cleanup_hardening_sweep_2026_10_dead_code_removal_{task,walkthrough}.md` (never bare names).

1. **Step A: `engine_status`** (no legacy coupling, no import-time risk): delete module + `test_engine_status.py`, smoke-list
   entry, `orchestrator_runner.py` docstring, test comment; D2 for `state_snapshot_age_seconds`.
2. **Step B: `daemon_owns_agentic_side_effects`** (function + 3 prose mentions + execution.md). Needs D1 = yes.
3. **Step C: `build_universe` wrapper** (function, `__all__`, 3 test files, docstrings, 2 architecture docs). Needs D1 = yes.
4. **Step D: `RunContext` `*_fn` fields** (context.py + 2 main_orchestrator sites + Gravity + 14 test sites + new AST guard +
   known-issue doc). Needs D1 = yes. Highest risk; do last of the code changes, alone in its commit.
5. **Step E (only if D3 = yes): Streamlit helpers** (`regime_filter`, `robinhood_mode`, `symbol_search` + 3 tests + 2 docstring
   mentions + webapp-and-gui.md:16).
6. **Step F: docs + RUNBOOK/legacy README rollback text + FEATURE_TIER_HISTORY entry + tick the tracker line**
   (`.claude/cleanup_hardening_sweep_2026_10_task.md:36`).
7. **Step G (last, once, after all four agents' edits are on the branch): regenerate census and liveness**
   `python3 scripts/measure_settings_census.py --write` and `.venv/bin/python3 scripts/settings_liveness.py --write`; read the
   diff and confirm it contains only `files_scanned`, read-count and line-shift changes (no field added/removed). These two
   artifacts are a merge-conflict hotspot among concurrent agents; do not hand-merge them, regenerate.

---

## 8. Verification (run and shown green, not assumed)

Targeted, after each step (the PostToolUse hook also runs mapped tests per edited `.py`):
```
.venv/bin/python -m pytest tests/test_main_py_archive_import_smoke.py -q          # import smoke (entry points with archived modules blocked)
# Step A
.venv/bin/python -m pytest tests/test_orchestrator_runner.py tests/test_main_py_archive_import_smoke.py -q
# Step B
.venv/bin/python -m pytest tests/test_daemon_agentic_queue_primary.py tests/test_main_py_archive_import_smoke.py -q
# Step C
.venv/bin/python -m pytest tests/test_advisory_inputs_watchlist_macro.py tests/test_universe_retention.py tests/test_production_steps_universe.py tests/test_symbol_rating_wiring.py -q
# Step D (every file that constructs RunContext + the daemon path)
.venv/bin/python -m pytest tests/test_pipeline_package.py tests/test_pipeline_runner.py tests/test_pipeline_smoke.py tests/test_progress_emission.py \
  tests/test_step5_4_verify_cycle.py tests/test_production_steps_fund_dtos_dead_letter.py tests/test_daemon_advisory_shadow_equivalence.py \
  tests/test_macro_step.py tests/test_forecasting_step_fallback_disclosure.py tests/test_production_steps_broker_gate.py \
  tests/test_trend_volatility_step.py tests/test_production_steps_universe.py tests/test_main_orchestrator.py -q
.venv/bin/python -m ruff check . --select=F821,F822,F823,E9                          # undefined names (catches a leftover reference)
# Gravity site: not importable; at minimum
.venv/bin/python -m py_compile "Gravity AI Review Suite.py" && grep -n "_fn=" "Gravity AI Review Suite.py"   # expect no RunContext *_fn kwargs
# Steps F/G
.venv/bin/python -m pytest tests/test_measure_settings_census.py tests/test_settings_liveness.py -q
```
(Both `tests/test_symbol_rating_wiring.py` and `tests/test_main_orchestrator.py` exist in the current tree; checked with `ls`.)

Gates: `make ci` (offline suite, `pytest -m "not network and not slow" -n auto --dist loadgroup`) with zero failures, then
`make local-ci` (the pre-merge gate) and paste its table into the PR. Expected deltas to explain in the PR: test count drops by
17 (`test_engine_status`) + 3 if D2 + (D3: the three helper test files); coverage floor is 74% and the modules removed are
fully covered by their own tests, so the percentage should move by well under a point (UNVERIFIED; the `coverage` step reports it).
`ml-audit` should show the orphan findings reduced by the modules removed; it cannot go red from these changes.
Mutation check for the new guard (CLAUDE.md-style): re-add one `build_universe_fn=` kwarg in a scratch copy and confirm the AST
guard fails.

No `webapp/src` files are touched, so `npm run typecheck`/browser checks are not required. Do not run anything that flips
`DAEMON_AGENTIC_QUEUE_MODE`, writes `.env`, or touches the runtime-flags store.

---

## 9. Symbols found NOT dead (do not delete)

- `pipeline/agentic_queue.py::resolve_daemon_agentic_queue_mode` and `DAEMON_AGENTIC_QUEUE_MODES` (read by `AgenticQueueStep`,
  `production_steps.py:3002,3140`).
- `pipeline/advisory_inputs.py::build_universe_detailed`, `UniverseBuild`, `recently_closed_universe_symbols`, and the rest of
  `__all__` (production callers at `production_steps.py:153`, `api/data_api.py`, `data/portfolio_sync.py`).
- `RunContext.force_account`, `started_at`, `watchlist_file` and all mutable fields (read by `production_steps.py:155,3280,3363`).
- `shared/orchestrator_runner.py::heartbeat_age_seconds` (used by `pilots/observability.py`) and `shared/market_hours.is_extended_hours`.
- `execution/overnight_guardrails.py`, `validation/walk_forward.py`, `signals/sentiment_index.py`: production-orphaned but
  deliberately kept/unwired (section 5); not safe to delete without an operator call.
- The archived-side readers `legacy/main.py`, `legacy/pipeline/steps.py`, `legacy/tests/*` are the only remaining users of
  symbols 2-4: that is a rollback dependency, not a live one (D1).

---

## 10. Decisions the operator must make

- **D1 (gates steps B, C, D).** Retire the "revert the 5.5 PR" rollback to `main.py`? Removing `build_universe`,
  `daemon_owns_agentic_side_effects` and the `RunContext` `*_fn` fields breaks `legacy/main.py` + `legacy/pipeline/steps.py`
  (RUNBOOK §3.15, `legacy/README.md:29`). If no: do Step A only (and E). If yes: also rewrite those two rollback instructions.
- **D2.** Delete `state_snapshot_age_seconds` + its 3 tests with `engine_status` (my recommendation), or keep?
- **D3.** Delete the three Streamlit-era helpers `shared/regime_filter.py`, `shared/robinhood_mode.py`, `shared/symbol_search.py`
  with their tests (my recommendation: yes, same precedent as the Streamlit deletion), or leave them?
- **D4.** Keep `TestDaemonMatchesAdvisoryInputsUniverse` once it compares the step to the function the step calls (near-tautology)?
- **D5.** `overnight_guardrails.py`, `walk_forward.py`, `sentiment_index.py`: keep as is (my recommendation, they are disclosed
  as intentional), or decide to wire/delete? Wiring `OvernightGuardrails` into the order path is an execution-path change and
  needs its own plan; the freeze applies to new capabilities.
- **D6.** Confirm the work is sequenced after the other three agents' edits land (census/liveness regeneration must be last and
  is currently red in the shared tree because of their uncommitted `settings.py` changes).

## Addendum (2026-11-10): `PROGRESS_POLL_SECONDS` and `compute_run_progress`

Found after the plan above was written. **Verified, not changed (only the setting's description was made truthful).**

- `shared/orchestrator_runner.py::compute_run_progress` has **no production caller** (repo-wide grep excluding `legacy/`: only its own module comments, `shared/env_io.py`'s comment and `tests/test_run_progress.py`). Its only caller was the deleted Streamlit Launcher's progress bar.
- `settings.PROGRESS_POLL_SECONDS` is read only inside that function (as a 3x freshness window), so nothing in production reads it either. It is still in `shared/env_io.ALLOWED_KEYS`, `pilots/settings_domains.py`, the `api/pilots_api.py` tunables list, `mock.ts`, `.env.example`, and `shared/help_content.py` (the `pipeline_progress` section text, which `tests/test_help_content*.py` pins as "every N seconds").
- Proposed (decision **D7**): remove the setting, the function, its tests and the `pipeline_progress` help section together, the same way `DASHBOARD_REFRESH_SECONDS` was removed (old `.env` keeps loading: `extra="ignore"`). Also needs the Gravity suite checked: steps around line 11420 assert the setting exists and is allowlisted.

## Done (2026-11-10, operator: "keep going")
- **Step A done:** deleted `shared/engine_status.py` + `tests/test_engine_status.py`; dropped `"shared.engine_status"` from the import-smoke list; reworded the `orchestrator_runner.py` docstring and the test comment. **D2 left as KEEP**: `state_snapshot_age_seconds` and its 3 tests stay (a small tested reader; deleting it is still your call).
- **Step E done (D3):** deleted `shared/regime_filter.py`, `shared/robinhood_mode.py`, `shared/symbol_search.py` and their tests; fixed the two `robinhood_execution_panel.py` docstring mentions and the wrong `docs/architecture/webapp-and-gui.md` line that listed `regime_filter.py` as live.
- Census/liveness regenerated (files scanned 371 -> 367). Gravity suite checked: it names none of the four modules.
- **Not done, still need you:** D1 (Steps B-D, which break the "revert the 5.5 PR" rollback), D2, D4-D7.

- **D1 = KEEP (operator, 2026-11-10).** The "revert the 5.5 PR" rollback stays, so Steps B-D (`daemon_owns_agentic_side_effects`, the `build_universe` wrapper, `RunContext` `*_fn` fields) are **not** done and stay in place for `legacy/main.py`.
- **D2 done:** `state_snapshot_age_seconds` + its 3 tests deleted; the `heartbeat_age_seconds` docstring now says what to read instead.
- **D7 done:** `compute_run_progress`, `RunProgress` and their private helpers (the whole "Real 0-100% progress bar" block of `shared/orchestrator_runner.py`), `tests/test_run_progress.py`, the `PROGRESS_POLL_SECONDS` setting (settings, `env_io` allowlist, settings domain, tunables list, `mock.ts`, `.env.example`) and the `pipeline_progress` help section + its test. Gravity step 74 checks 6/7 now pin the removal (run directly: PASSED). `reporting/progress.py` stays (live: the daemon's run status).

## Final decisions (operator, 2026-11-13)

Branch `cleanup-dead-code-d3-d5-nightly`. Every decision in section 10 now has an outcome:

| Decision | Outcome | Evidence |
|---|---|---|
| **D1** | **KEEP the rollback** (unchanged from 2026-11-10). `build_universe`, `daemon_owns_agentic_side_effects` and the `RunContext` `*_fn` fields stay for `legacy/main.py`; Steps B-D are not done. | Not touched on this branch. |
| **D2** | **Done** 2026-11-10 (`state_snapshot_age_seconds` + 3 tests deleted). | See "Done" above. |
| **D3** | **YES, already done** (Step E, merged in #1133 as 68d9e69). Nothing left to delete on this branch. | On `origin/main` (d8e53dfc) none of `shared/regime_filter.py`, `shared/robinhood_mode.py`, `shared/symbol_search.py`, `tests/test_regime_filter.py`, `tests/test_robinhood_mode.py`, `tests/test_symbol_search.py` exists; `git show --stat 68d9e69f` lists all six as deleted. Importer check (excluding `legacy/`): no `shared.regime_filter`/`shared.robinhood_mode`/`shared.symbol_search` import or string anywhere in `.py`, `investyo_mcp_server.py`, `"Gravity AI Review Suite.py"`, `engine/gravity_ai_runner.py`, `ai_verification_prompts.py`, scripts, plists or the webapp. The remaining word matches are unrelated names (`settings._coerce_robinhood_mode`, `scripts/preflight_check.py::_robinhood_mode`, `api/data_api.py::get_symbol_search` and its tests), a provenance comment in `shared/robinhood_execution_panel.py` naming the deleted `gui/robinhood_mode.py`, and historical plans/docs. |
| **D4** | **Rewritten** (not removed). `TestDaemonMatchesAdvisoryInputsUniverse` compared `AsyncDataFetchStep` to `build_universe()`, a wrapper over `build_universe_detailed()`, which the step itself calls: it could only catch wrong arguments. Replaced by `TestDaemonUniverseMatchesDocumentedRules` (`tests/test_production_steps_universe.py`): the same 8 input scenarios, each with the expected universe written by hand from the documented rules (union, auto-drop never drops held, DEFAULT_TICKERS only on an empty post-drop union, retention unioned last, sorted). | Mutation checks: (a) the step dropping `watchlist_file=ctx.watchlist_file` fails 2/8 (`everything`, `auto_drop`); (b) `compute_tracked_universe` dropping held symbols on auto-drop (`combined -= excluded`) fails `auto_drop`. The old class passed (b), since both sides moved together. |
| **D5** | **KEEP as is**: `execution/overnight_guardrails.py`, `validation/walk_forward.py`, `signals/sentiment_index.py`. No code change. | Decision only. They remain LOW `orphaned_module` findings in the ML audit, which cannot fail the gate. |
| **D6** | **Moot / resolved.** The concurrent edits it waited on have all landed on `main`, and the census/liveness artifacts were regenerated with Steps A/E; this branch changes no production `.py` file. | `tests/test_measure_settings_census.py` and `tests/test_settings_liveness.py` pass on the branch (see the PR). |
| **D7** | **Done** 2026-11-10. | See "Done" above. |

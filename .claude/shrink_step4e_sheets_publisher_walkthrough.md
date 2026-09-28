# Step 4e: Google Sheet publisher -- walkthrough

Branch: archive-sheets-publisher (based on origin/main @ 80c32cee, which
already includes step 4 prep PR #1065 -- data_engine.live_data_configured()
FRED-key gate + execution/compose.py FOLLOW_MIN_CONVICTION literal fix).

## Scope

Retire the Google Sheet output sink. Trap #1 from the plan (credentials.json
doubling as the real-vs-mock data switch) was already fixed on main before
this PR started -- verified, not re-fixed.

## Changes

### main.py
- Removed the reporting.sheets_client / reporting.sheet_publisher top-level
  imports and the _write_to_sheet(result, market=market) call site (Stage F
  of run_once()). The market = get_provider() line that only fed that call
  was removed too (get_provider is still imported/used elsewhere in the
  file, at lines 505/892/1031).
- Removed _load_tickers_from_sheet2() and its use as the Sheet2 universe
  fallback inside _build_universe().
- New fallback behavior: compute_tracked_universe() already handles the
  held-union-watchlist-union-discovered union, rating-exclusion, and the
  settings.DEFAULT_TICKERS fallback-if-empty internally (this was already
  true before this PR -- the Sheet2 branch only ever ran after that whole
  function had already tried and failed to produce anything). So removing
  the Sheet2 branch leaves the universe empty ([]) when the whole union
  (including DEFAULT_TICKERS) is empty (or rating-exclusion emptied it) --
  no code path changed besides deleting the dead-in-that-case Sheet2
  attempt. Confirmed via
  tests/test_run_once.py::TestBuildUniverse::test_empty_account_empty_watchlist
  (now asserts [] directly, no patch needed) and the renamed
  test_default_tickers_fallback_used_when_empty (proves DEFAULT_TICKERS is
  still the fallback).
- Updated the module docstring stage list (F/G/H -> F/G) and
  _build_universe docstring (5 sources -> 4, updated cross-references).

### pipeline/steps.py
- Dropped the "from reporting.sheets_client import SHEET_NAME" import and
  its one use (a log line inside UniverseStep.run() naming Sheet2 column A
  as a 4th remediation path). The warning now names 3 remediation paths
  (RH env vars, WATCHLIST, watchlist.txt).

### reporting/ moved to legacy/reporting/
- sheet_publisher.py and sheets_client.py relocated (version-control move),
  preserving relative structure under legacy/reporting/.
- New legacy/reporting/__init__.py (docstring only).
- Updated sheet_publisher.py internal import
  (from reporting.sheets_client import ... -> from legacy.reporting.sheets_client import ...)
  and both files module docstrings to note the archive.

### Tests
- tests/test_reporting_package.py (mixed file, split per common rules):
  removed TestSheetsClient/TestSheetPublisher (and the main._write_to_sheet
  alias-pin test, which now has nothing to alias to -- main no longer
  defines it at all). TestHtmlPublisher (unaffected) stayed in place.
- legacy/tests/test_sheet_publisher.py (new): the two removed classes,
  imports repointed at legacy.reporting.*.
- tests/test_config.py: removed the entire TestAdvisoryColumnCoverage class
  (exercises the now-archived rec_to_sheet_row) plus its
  _make_position/_make_snapshot/_make_recommendation helpers and the
  now-unused rec_to_sheet_row/Recommendation/MagicMock/dataclass/field/
  datetime/timezone/List/Optional imports.
- legacy/tests/test_advisory_column_coverage.py (new): the moved class,
  import repointed at legacy.reporting.sheet_publisher.
- tests/test_run_once.py: removed the _load_tickers_from_sheet2 import and
  the whole TestLoadTickersFromSheet2 class; removed
  test_sheet2_fallback_used_when_empty / test_sheet2_not_called_when_*
  (their sole purpose was proving Sheet2 was/wasnt consulted, which no
  longer exists); rewrote test_empty_account_empty_watchlist to assert []
  directly and added test_default_tickers_fallback_used_when_empty in
  place of the old Sheet2-fallback test, proving DEFAULT_TICKERS is the
  real fallback now.
- tests/test_universe_retention.py:
  test_retention_does_not_suppress_default_tickers_fallback patched
  main._load_tickers_from_sheet2, which no longer exists (AttributeError
  in the full suite run) -- removed the now-unneeded patch() context
  manager, the assertion itself is unchanged.
- tests/test_no_missing_call_timeouts.py: the AST-guard line-number-keyed
  allowlist entry for main.py venv-reexec guard shifted from line 70 to
  line 68 (net -2 lines from the import/function removals) -- updated the
  allowlist tuple.

### config.py
- Checked for SHEET_NAME/TAB_NAME_OUTPUT/CREDENTIALS_FILE constants: none
  exist there (they lived only in reporting/sheets_client.py, now moved).
  Nothing to remove.
- Not touched, noted for the record: config.get_headers() and
  config.get_rename_mapping() are now reachable only from
  legacy/reporting/sheet_publisher.py and from tests (test_config.py
  TestColumnSchemaIntegrity/TestFMPDiagnosticColumns exercise them
  directly as COLUMN_SCHEMA consistency checks, not as production
  callers). Removing them is a schema-trim-shaped change (step 4f
  territory per the plan), out of this PR stated scope -- left alone.

### requirements.txt
- Removed gspread==6.2.1 and gspread-dataframe==4.0.0 (no remaining
  importer anywhere, confirmed by repo-wide search).
- Removed google-auth-oauthlib==1.4.0: pip show gspread was its only
  consumer (Requires: google-auth, google-auth-oauthlib); with gspread
  gone, nothing needs it.
- Kept google-auth==2.55.0: confirmed via pip show google-genai (Requires:
  ... google-auth ...) and pip show google-cloud-language (Requires: ...
  google-auth ...) that it is still a real transitive dependency of two
  other already-declared packages. Direct-import search (google.auth,
  google.oauth2, googleapiclient) across every .py file in the repo found
  zero hits -- the Gemini/genai code in llm/__init__.py / api/data_api.py
  / etc. imports google.genai, never google.auth directly -- but the task
  own "keep if anything else uses it" test is satisfied by the transitive
  dependency, so the pin stays (removing it would just make a future
  resolve non-deterministic for a package still actually needed).

### legacy/README.md
- Created (did not exist before). Documents what moved and why, and
  states plainly that credentials.json is no longer read by any active
  code (it is not tracked and the operator manages it -- left untouched,
  as instructed).

### Docs
- CLAUDE.md/AGENTS.md: one bullet added immediately before the "Options
  signal modules retired..." bullet (per the common rules anchor), then
  AGENTS.md regenerated as a copy of CLAUDE.md (byte-identical, confirmed
  via diff).
- docs/architecture/orchestration-entrypoints.md: added an archive banner
  under the page header, plus fixed 3 pre-existing stale mentions of
  Sheet2/_write_to_sheet/_load_tickers_from_sheet2 in the .env-loading
  bullet, the main.py bullet, and the recently-closed-retention bullet.
- docs/architecture.md: removed the GS (Google Sheet) node and its edge
  from the Mermaid data-flow diagram.
- docs/RUNBOOK.md: fixed the empty-universe troubleshooting entry (Sheet2
  removed from the remediation list, DEFAULT_TICKERS added since it is
  the real fallback now).
- docs/HOW_TO_GUIDE.md: rewrote Section 4 universe-building subsection
  (it was already stale before this PR -- claimed main.py "does not use
  DEFAULT_TICKERS", which was false even on main before this PR, since
  compute_tracked_universe() already had that fallback; fixed while
  touching this exact section since leaving it half-wrong would be worse
  than leaving it alone). Rewrote the "Offline / mock mode" and "legacy
  orchestrator" subsections to describe the real FRED-key gate instead of
  the retired credentials.json gate. Rewrote Section 18 ("Google Sheets
  Integration") as a retirement notice (kept the anchor so the TOC and
  cross-references elsewhere in the file still resolve). Fixed the
  "credentials.json not found..." troubleshooting entry to match the
  real warning text ("FRED_API_KEY not configured...").

## Traps handled

- Trap #1 (credentials.json real-data gate): already fixed on main by PR
  #1065 before this branch started. Verified both call sites
  (pipeline/production_steps.py:133, desktop/daemon_runtime.py:431) call
  data_engine.live_data_configured(), not
  os.path.exists("credentials.json"). No action needed in this PR beyond
  confirming and documenting it.
- No other traps in the plan list apply to 4e (those are for 4a-4d, 4f).

## Verification

- Import smoke check: import main, pipeline.steps, reporting,
  legacy.reporting.sheet_publisher, legacy.reporting.sheets_client --
  clean. Confirmed hasattr(main, "_write_to_sheet") and
  hasattr(main, "_load_tickers_from_sheet2") are both False.
- Legacy tests run standalone (not collected by the real suite, sanity
  check only): pytest legacy/tests/test_sheet_publisher.py
  legacy/tests/test_advisory_column_coverage.py -- 9 passed.
- pytest collection: confirmed legacy/ is not collected by the default
  suite (pytest.ini testpaths = tests already excludes it -- no
  norecursedirs change needed).
- Ruff (--select=F821,F822,F823,E9): clean.
- Full offline suite (-m "not network and not slow" -n auto --dist
  loadgroup -q -p no:randomly): first run surfaced 6 failures, all fixed
  in-branch:
  - tests/test_universe_retention.py
    test_retention_does_not_suppress_default_tickers_fallback -- fixed
    (patched a function that no longer exists).
  - tests/test_no_missing_call_timeouts.py::test_no_missing_call_timeouts
    -- fixed (allowlist line-number shift).
  - tests/test_settings_liveness.py / tests/test_measure_settings_census.py
    TestCommittedArtifactIsFresh (3 tests) -- fixed by regenerating
    docs/settings_liveness.json / docs/settings_field_census.json and .md
    via scripts/settings_liveness.py --write and
    scripts/measure_settings_census.py --write. The only real diff
    besides the commit-hash metadata stamp was files_scanned: 438 -> 439
    -- the net file-count change from moving 2 files out of reporting/
    (not SKIP_DIRS-excluded) and adding 3 into legacy/reporting/ (also
    not excluded; legacy/tests/ IS excluded, since its directory name
    matches the literal "tests" skip entry).
  - tests/test_robinhood_login.py
    TestStartLoginSuccess::test_successful_job_reaches_succeeded -- NOT
    related to this PR (no reference to Sheets/main.py/pipeline/steps.py
    anywhere in that test file); passed cleanly in isolation and did not
    recur on the second full-suite run. Timing-sensitive subprocess
    test, flaky under -n auto parallel load.
  - Second full run (after the fixes above): 2 new failures,
    tests/test_edgar_fundamentals.py
    test_in_process_layer_alone_serializes_request_issuance and
    test_throttle_serializes_request_issuance -- also unrelated
    (thread-serialization timing tests, no reference to anything this PR
    touches), passed cleanly in isolation (2 passed).
  - Both full runs: 13072-13076 passed, 0 unexplained failures (only
    known-flaky timing tests recurred, never the same one twice).
- Webapp: not touched, per common rules skip.
- CLI manifest: no CLI target changed; not regenerated.

## Left undone / disclosed

- config.get_headers()/get_rename_mapping() in config.py are now
  reachable only from archived code and from tests validating
  COLUMN_SCHEMA shape -- a genuine "no remaining production reader"
  situation, but removing them is a schema-trim-shaped change
  explicitly scoped to step 4f in the plan, not 4e. Left in place.
- The pre-existing staleness in docs/HOW_TO_GUIDE.md claiming main.py
  "does not use DEFAULT_TICKERS" predates this PR (it was already
  wrong on main) -- fixed anyway since this exact section was being
  edited for the Sheet2 removal and leaving it half-wrong would be
  worse.
- This branch was interrupted once by an API spend-limit error and
  resumed in a fresh turn; the resumed turn re-verified branch/worktree
  state before continuing rather than assuming prior claims held.

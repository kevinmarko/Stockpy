# Artifact freshness guards — audit + fixes

## Motivation

PR #1044 fixed a bug class where `ml/registry.yaml` / `watch_rules.yaml` were
written by `yaml.safe_load -> mutate -> yaml.safe_dump`, silently erasing
their doc comments on every write. While fixing that, CI caught a *different*
staleness problem in three artifacts that DO have freshness tests
(`docs/settings_liveness.json`, `docs/settings_field_census.{json,md}`).

This task: audit every committed, machine-generated file in the repo for the
same asymmetry (guarded vs. unguarded), and add freshness tests where a real
gap exists — not a padded inventory.

## Audit inventory

| Artifact | Writer | Guard today | Verdict |
|---|---|---|---|
| `cli_introspect/command_manifest.json` | `scripts/build_command_manifest.py` | `tests/test_command_manifest_freshness.py` (3 registry fields + `test_manifest_commands_match_targets`) | Already guarded — no action. |
| `docs/settings_liveness.json` | `scripts/settings_liveness.py --write` | `tests/test_settings_liveness.py::TestCommittedArtifactIsFresh` | Already guarded — no action. |
| `docs/settings_field_census.json` / `.md` | `scripts/measure_settings_census.py --write` | `tests/test_measure_settings_census.py::TestCommittedArtifactIsFresh` | Already guarded — no action. |
| `ml/registry.yaml` (+ `ml/registry_io.py`) | `scripts/retrain_models.py` / `scripts/train_lgbm.py` via `ml/registry_io.py` | Comment-preservation fixed on `fix-registry-yaml-comment-preservation` (PR #1044, out of scope here — explicitly not touched per task instructions). | Not this task's job. |
| `completions/investyo.bash` / `.zsh` | `scripts/generate_shell_completion.py`, reading the committed `cli_introspect/command_manifest.json` | **None.** `tests/test_shell_completion.py` only exercises `render_bash`/`render_zsh` against a synthetic in-memory manifest — it never reads the committed files, so a manifest change (which itself has its own freshness test) can leave these two static files silently stale forever. | **Real, live bug — fixed.** Confirmed stale in the current tree: regenerating produced a 10-line bash diff (missing `app_shell.py`, 5 newer entry points not listed, 2 updated `kind='opts'` flag lists). Added `TestCommittedArtifactIsFresh` to `tests/test_shell_completion.py`; regenerated the two files for real via the documented script (never hand-edited). |
| `CLAUDE.md` / `AGENTS.md` | Hand-edited by an agent/human; kept in sync only by `.claude/hooks/sync_agent_docs.sh` / `.agents/hooks/sync_agent_docs.sh` | **None.** No test asserted the two files match. CLAUDE.md's own text records they "had already drifted by one real bullet" before the hook existed — a hook only fires for a session with hooks enabled, so this is not a guard. Diffed: currently byte-identical (lucky, not guaranteed). | **Real gap — fixed.** Added `tests/test_agent_docs_sync.py` asserting byte-identity. |
| `scripts/build_local_prompt_registry.py` output | writes to `output/prompt_registry_local.json` | N/A | `output/` is git-ignored runtime output, not a committed artifact — no guard needed. |
| `scripts/export_notebooklm.py` output | writes to `output/notebooklm*.md` | N/A | Same — `output/` is git-ignored, not committed. |
| `scripts/build_ticker_sector_map.py` output | `forecasting/data/ticker_sectors.csv` (committed) | N/A | The committed file is explicitly documented as NOT the literal output of a live run of this script (a hand-curated seed with a `--output` flag for a *different* target path); drift here would be a deliberate editorial change, not silent generator/consumer drift. Not guarded, not padding the inventory with a test that would have nothing real to assert against.
| `docs/README.md`, `docs/architecture/*.md`, `docs/known_issues/*.md`, `docs/VALIDATION_STRATEGY_FIX_LOG.md`, etc. | Hand-maintained prose | N/A | Not machine-generated; drift here is an editorial/content problem outside this task's scope (no script regenerates them from a canonical source). |
| `docs/HOW_TO_GUIDE.md` anchors ↔ `shared/help_content.py` | Hand-maintained; cross-referenced | `tests/test_help_content.py::TestAnchorValidity` | Already guarded — no action. |

## What was NOT guarded, and why

- `ml/registry.yaml` / `watch_rules.yaml` comment preservation — explicitly out of scope (PR #1044, mid-merge, must not be touched or re-implemented here).
- `forecasting/data/ticker_sectors.csv` — not actually the live output of `scripts/build_ticker_sector_map.py`'s default invocation; a freshness test here would either be vacuous or would incorrectly demand the file always equal one specific `--output` invocation the doc explicitly disclaims.
- `docs/README.md` / hand-written `docs/*.md` — no generator exists; nothing to diff against.

## Fix verification (fail → pass proof)

1. `tests/test_shell_completion.py::TestCommittedArtifactIsFresh` — confirmed
   both new tests FAIL against the pre-existing (stale) committed files
   (regeneration produced a real diff), regenerated the files for real via
   `python scripts/generate_shell_completion.py`, confirmed both tests PASS.
   Additionally damaged the regenerated file with an appended marker line and
   confirmed the bash test fails again, then restored and re-confirmed green.
2. `tests/test_agent_docs_sync.py::test_claude_md_and_agents_md_are_byte_identical`
   — confirmed PASS against the current (already-synced) tree, then appended a
   line to `AGENTS.md` and confirmed the test FAILS with a clear diff, then
   restored via `git checkout -- AGENTS.md` and re-confirmed green.

## Non-goals

- No change to `ml/registry.yaml`/`watch_rules.yaml` or their writers.
- No hand-editing of any artifact — the one stale artifact found
  (`completions/investyo.{bash,zsh}`) was regenerated via its own documented
  script only.

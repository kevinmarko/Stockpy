# gui/ -> shared/ + legacy/streamlit_command_center/ split — Task Tracker

- [x] 1. Investigate `gui/`'s real import graph (2 Explore agents) — found `gui/` is not
      self-contained: 31 top-level modules are live shared logic imported directly by
      `api/pilots_api.py`/`api/data_api.py`/`api/_jobs.py`/`pilots/*`/`main.py`/etc.
- [x] 2. Design the concrete migration plan (1 Plan agent) — corrected several wrong
      assumptions from the initial fact-gathering (the `env_io.py` shim, `gui/app.py`'s
      mixed import, several path-depth `Path(__file__).resolve().parent...` computations).
- [x] 3. Mechanical import rewrite: `gui.<31 shared modules>` -> `shared.<name>` and
      `gui.panels`/`gui.app` -> `legacy.streamlit_command_center.{panels,app}` repo-wide
      (Python script, not shell sed — macOS BSD sed silently doesn't support `\b`).
- [x] 4. `git mv` the 31 shared modules + repo-root `env_io.py` into `shared/`; delete the
      dead `gui/env_io.py` re-export shim.
- [x] 5. Special-case fixes: reverse the `report_viewer_helpers.py` <-> `panels/_shared.py`
      circular import (GICS_SECTORS/_BF_EDITOR_COLUMNS now live natively in
      `shared/report_viewer_helpers.py`); confirm the `ai_control_center.py` naming
      collision is non-colliding once split across two packages.
- [x] 6. `git mv` `gui/app.py` + `gui/panels/` -> `legacy/streamlit_command_center/`; fix
      `_REPO_ROOT`/`parents[N]` depth computations in `app.py`, `panels/_shared.py`,
      `panels/analytics.py`, `panels/analytics_signals.py`.
- [x] 7. `git mv` `app_shell.py` + `desktop/{engine_supervisor,ui_server,net_util}.py` ->
      `legacy/streamlit_command_center/{app_shell.py,desktop_shell/}`; fix
      `ui_server.py`'s `_REPO_ROOT` to anchor off `settings.ENV_PATH` (nesting-depth-independent).
- [x] 8. Remove `gui/` entirely (`git rm gui/__init__.py`); write `shared/__init__.py`
      (import-inert, enforced by a repointed `tests/test_pilots_api.py` guard test).
- [x] 9. Fix `launch_app.command`/`launch_gui.command`'s internal invocation (paths stay
      at repo root, only the target path changed); `scripts/build_macos_app.command`
      needed no changes.
- [x] 10. Delete the decommissioned `app_shell.py` `Target` from `cli_introspect/targets.py`
      rather than repathing it; regenerate `cli_introspect/command_manifest.json`.
- [x] 11. Fix ~15 additional gaps a first mechanical pass missed: hardcoded `"gui/app.py"`/
      `"gui/panels/X.py"` string-literal path reads (not import statements) across
      `Gravity AI Review Suite.py`, several test files, `desktop/ui_server.py`; string-based
      `mock.patch("env_io.X")`/`monkeypatch.setattr("env_io.X", ...)` targets; an AST-based
      test asserting a literal import name; a codebase-auditor's dead-code-exemption prefix
      list; `engine/gravity_ai_runner.py`'s file-map for LLM-audited source; `settings.py`
      field-description prose (18 occurrences).
- [x] 12. Regenerate the two other committed derived artifacts that drifted:
      `docs/settings_liveness.json` (`scripts/settings_liveness.py --write`) and
      `docs/settings_field_census.{json,md}` (`scripts/measure_settings_census.py --write`)
      — each needed 2 regeneration passes as later fixes changed what they measure.
- [x] 13. Docs pass: CLAUDE.md's "Frontend strategy" section rewritten (`shared/` explicitly
      called out as NOT frozen); ~45 further stale path mentions across CLAUDE.md's
      changelog fixed for internal consistency; `docs/architecture/webapp-and-gui.md`
      thoroughly rewritten; 21 further "current" reference docs audited and fixed;
      historical/dated docs (`docs/plans/`, `docs/known_issues/`, `FEATURE_TIER_HISTORY.md`,
      `.claude/*_task.md` etc.) deliberately left untouched.
- [x] 14. 6-agent verification wave: 2 pytest partitions (A-L, M-Z), docs sweep,
      `webapp-and-gui.md` dedicated rewrite, live Streamlit smoke test + ruff + manifest
      freshness, adversarial re-verification of the 3 special-case fixes + optional
      `.coveragerc`/CI bandit config updates.
- [x] 15. Final full-suite pytest run (twice, after every fix) + final repo-wide grep sweep
      for any remaining stale reference, across all file types.

## Verification evidence

- `python3 -m pytest -m "not network and not slow" -q --ignore=tests/test_sizing_properties.py`
  → **12,727 passed**, 33 skipped, 92 deselected — run twice after all fixes, identical result.
  The only 6 failures (`test_data_api_chat.py`, `test_gemini_live_chat.py`) are missing
  `openai`/`google.genai` optional dependencies, confirmed pre-existing and unrelated.
- Live: `streamlit run legacy/streamlit_command_center/app.py --server.headless true` boots
  and serves cleanly (verified via `curl`).
- `python3 -m ruff check . --select=F821,F822,F823,E9` → all checks passed.
- `python3 scripts/build_command_manifest.py` / `settings_liveness.py --write` /
  `measure_settings_census.py --write` all regenerated and their freshness tests pass.
- Final repo-wide grep sweep (all file types, excluding `.venv`/`.git`/`node_modules`):
  zero remaining functional `gui.X`/`gui/X.py`/`desktop.{engine_supervisor,ui_server,net_util}`
  references outside `legacy/streamlit_command_center/`'s own historical "was `gui/...`"
  framing and deliberately-untouched historical/dated docs.

## Real bugs found and fixed along the way (not part of the original scope, surfaced by the move)

- `scripts/measure_settings_census.py`'s `_literal_scan_skip` set matched the OLD
  `"env_io.py"` relative path, silently reactivating a false-positive-suppressing skip
  the moment the file moved to `shared/env_io.py` — fixed to `"shared/env_io.py"`.
- `tests/test_command_manifest_freshness.py`'s `dead_letters` handling assumed a
  `list[dict]` shape (`dl["name"]`) when the builder has always produced a plain
  `list[str]` — a latent bug never exercised because `dead_letters` had always been empty
  until this migration's manifest regen surfaced a genuine (pre-existing, unrelated)
  `export_notebooklm.py` dead-letter.
- `tests/test_app_shell.py`'s fake-module `sys.modules` injection registered a fake
  top-level `desktop` package that would have shadowed the real one; simplified to fake
  only the 3 leaf modules once the parent packages became real, tiny, side-effect-free
  packages on disk.

## Explicitly out of scope

- `scripts/export_notebooklm.py`'s `--help` executing a real export instead of printing
  help — confirmed unrelated (zero diff, no dependency on anything touched here) and
  flagged as a separate background task (`task_a5cbca89`), now running independently.

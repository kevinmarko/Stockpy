# Walkthrough: Split `gui/` into `shared/` (live) + `legacy/streamlit_command_center/` (frozen)

CLAUDE.md already declared the Streamlit "InvestYo Command Center" decommissioned, but the
frozen boundary lived only in a CLAUDE.md paragraph — the code itself still sat in `gui/`
and `desktop/`, indistinguishable at a glance from live surface. This PR moves the actually-
decommissioned code into an archive folder so the repo structure signals frozen directly,
and moves the code `gui/` was also quietly hosting — logic genuinely used by live production
services — into a new, non-frozen `shared/` package.

## Why this was bigger than "move a folder"

Investigation found `gui/` was never self-contained. Only `gui/app.py` and `gui/panels/*`
(22 files) are the actual Streamlit rendering layer. The other 31 top-level `gui/*.py`
modules — `env_io.py`, `orchestrator_runner.py`, `daemon_client.py`, `strategy_registry.py`,
`help_content.py`, and 26 others — are imported directly, today, by production code CLAUDE.md
itself calls "Unaffected" backend infrastructure: `api/pilots_api.py`, `api/data_api.py`,
`api/_jobs.py`, `pilots/observability.py`, `pilots/models.py`, `pilots/calibration.py`,
`main.py`, `evaluation_engine.py`, `alerting.py`, `diagnostics_and_visuals.py`, and several
`scripts/*.py`. A blind move of all of `gui/` would have broken every one of those.

## Summary of changes

**New `shared/` package** — the 31 live modules, `git mv`'d from `gui/*.py`, plus repo-root
`env_io.py` (which had already been relocated out of `gui/` by an earlier refactor, F13,
leaving `gui/env_io.py` as a 47-line `sys.modules` re-export shim — that shim is now deleted
outright rather than moved, its sole purpose gone). `shared/__init__.py` is a plain,
import-inert marker; `tests/test_pilots_api.py`'s existing guard test (previously asserting
`gui/__init__.py` stays import-inert, since `api/pilots_api.py` importing `shared.daemon_client`
executes it as a side effect) is repointed at `shared`.

**New `legacy/streamlit_command_center/` package** — the actual frozen Streamlit UI + native
shell: `app.py` (was `gui/app.py`), `panels/` (was `gui/panels/`, 22 files), `app_shell.py`
(was repo-root `app_shell.py`), `desktop_shell/{engine_supervisor,ui_server,net_util}.py`
(was `desktop/`'s native-shell trio — the rest of `desktop/` is untouched, live backend infra).

**`gui/` no longer exists.** `desktop/` keeps only `daemon_runtime.py`, `orchestrator_daemon.py`,
`run_history_store.py`, `daemon_status.py`, `assets/`.

**Launchers unmoved.** `launch_app.command`, `launch_gui.command`, `scripts/build_macos_app.command`
stay at their original repo-root/`scripts/` paths — they're real, documented, user-facing
double-click entry points; moving shell scripts buys nothing for the "signal frozen via
directory structure" goal and risks breaking a saved Finder/Dock shortcut. Only their
internal invocation changed (`python app_shell.py` -> `python -m legacy.streamlit_command_center.app_shell`;
`streamlit run gui/app.py` -> `streamlit run legacy/streamlit_command_center/app.py`).

## Three special-case fixes

1. **Circular-import reversal.** `gui/report_viewer_helpers.py` used to lazily import
   `GICS_SECTORS`/`_BF_EDITOR_COLUMNS` from `gui/panels/_shared.py` inside 3 functions,
   specifically to dodge a circular import (`gui/panels/__init__.py` imports
   `report_viewer`, which imports this module). Now that `report_viewer_helpers.py` lives
   in `shared/` — outside the archived UI entirely — the two constants are defined natively
   there, and `legacy/streamlit_command_center/panels/_shared.py` imports them back
   (reversed direction) and re-exports them, so every panel's existing
   `from ...panels._shared import GICS_SECTORS` keeps working unchanged.
2. **`ai_control_center.py` naming collision.** `shared/ai_control_center.py` (headless
   logic) and `legacy/streamlit_command_center/panels/ai_control_center.py` (Streamlit
   wrapper) now live in different packages — no collision at the destination; the
   one-directional dependency (panel imports the shared module) was mechanically rewritten.
   Verified the same headless/wrapper split for `ai_insights_panel.py`/`ai_insights.py` and
   `gravity_ai_panel.py`/`gravity_audit.py`.
3. **`ui_server.py`'s repo-root computation.** Was `Path(__file__).resolve().parent.parent`
   (correct only one directory below repo root); now `ENV_PATH.parent` (imported from
   `settings.py`), nesting-depth-independent since this file has now moved twice across
   refactors.

## Mechanical approach, and why it needed correcting mid-flight

The first attempt used shell `sed -i '' -E "s/\bgui\.env_io\b/.../"` — macOS's BSD `sed`
does not support `\b` word-boundary syntax the way GNU sed does, so every substitution
silently no-op'd (confirmed: `sed` exited 0 having changed nothing). Switched to small,
targeted Python scripts using `re.sub` for every mechanical rewrite pass instead, which is
also what caught many more call sites than an initial hand-curated file list did (a repo-wide
regex sweep found 856 replacements across 133 files on the first real pass, versus ~15 files
originally enumerated by hand).

A second class of gap surfaced only via the Stop-hook's targeted-test enforcement and 6
follow-up verification agents: hardcoded path/module-name **string literals** that don't
look like Python import syntax and so don't match an import-rewrite regex —
`Path("gui/app.py")`, `types.ModuleType("desktop.net_util")`,
`mock.patch("env_io.write_setting")`, an AST test asserting a literal `"env_io"` import
name, a codebase auditor's dead-code-exemption path-prefix list, and ~18 occurrences of
`gui/env_io.py` inside `settings.py`'s Pydantic field-description prose (user-facing via
`Settings.model_fields[x].description`). All were found and fixed; the final full-suite run
came back clean.

## Verification

- `python3 -m pytest -m "not network and not slow" -q --ignore=tests/test_sizing_properties.py`
  → **12,727 passed**, 33 skipped, 92 deselected, run twice after all fixes with an
  identical result. The only failures (6, `test_data_api_chat.py`/`test_gemini_live_chat.py`)
  are missing `openai`/`google.genai` optional dependencies, confirmed present before this
  work too.
- Live: `streamlit run legacy/streamlit_command_center/app.py --server.headless true` boots
  and serves (`curl` confirms a 200).
- `ruff check . --select=F821,F822,F823,E9` → clean.
- Three committed derived artifacts regenerated and confirmed fresh:
  `cli_introspect/command_manifest.json`, `docs/settings_liveness.json`,
  `docs/settings_field_census.{json,md}`.
- CLAUDE.md/AGENTS.md confirmed byte-identical via the repo's `sync_agent_docs.sh` hook.
- A dedicated adversarial-verification agent independently re-tested (not just re-read) all
  three special-case fixes above, plus the sibling path-depth fixes in `panels/_shared.py`/
  `panels/analytics.py`/`panels/analytics_signals.py`/`app.py` — all confirmed correct.

## Two real, pre-existing bugs found and fixed as a byproduct

- `scripts/measure_settings_census.py`'s literal-scan skip-list matched the file's OLD
  relative path (`"env_io.py"`); once the file moved, the skip silently stopped applying,
  which the census's own committed-artifact freshness test caught.
- `tests/test_command_manifest_freshness.py` assumed `dead_letters` was a `list[dict]`
  (`dl["name"]`) when the builder script has always produced a plain `list[str]` — a latent
  bug never exercised because `dead_letters` had always been empty in every prior committed
  manifest, until this migration's regeneration surfaced a genuine (unrelated,
  pre-existing) `export_notebooklm.py` dead-letter.

## Explicitly out of scope

`scripts/export_notebooklm.py`'s `--help` flag executing a real export instead of printing
help — confirmed unrelated to this migration (zero diff against `origin/main`, no dependency
on anything touched here) and flagged separately (background task `task_a5cbca89`, now
running independently in its own session).

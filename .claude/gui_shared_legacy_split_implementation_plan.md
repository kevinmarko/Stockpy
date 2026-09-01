# Split gui/ into shared/ (live) + legacy/streamlit_command_center/ (frozen)

## Context

CLAUDE.md declares the Streamlit "InvestYo Command Center" (`gui/`, `app_shell.py`,
`desktop/{engine_supervisor,ui_server,net_util}.py`, and their launcher scripts) frozen —
no new development, kept runnable for existing local setups only. The user asked to move
all of that code out of the active tree into an archive folder so agents stop treating it
as live surface (fewer files to grep through every session, no need to re-derive the
"this is frozen" boundary from a CLAUDE.md paragraph — the file tree says it directly).

Investigation (2 Explore agents + 1 Plan agent, all findings verified live against this
worktree) found `gui/` is **not** a self-contained decommissioned folder: only
`gui/app.py` + `gui/panels/*` (22 files) are the actual Streamlit rendering layer. The
other 31 top-level `gui/*.py` modules are live shared logic imported directly, today, by
production backend code CLAUDE.md explicitly calls "Unaffected": `api/pilots_api.py`,
`api/data_api.py`, `api/_jobs.py`, `api/_redact.py`, `pilots/observability.py`,
`pilots/models.py`, `pilots/calibration.py`, `main.py`, `evaluation_engine.py`,
`alerting.py`, `diagnostics_and_visuals.py`, `investyo_mcp_server.py`,
`data/portfolio_sync.py`, `runtime_flags_writer.py`, `prompt_registry/__main__.py`,
`conftest.py`, `scripts/daily_briefing.py`, `scripts/measure_settings_census.py`.

User's decision (via AskUserQuestion): **full move + untangle**. Split `gui/` into a new
top-level `shared/` package (the 31 live modules) and an archive folder (the Streamlit UI
+ native shell), updating every real import site so nothing breaks. Everything below has
been spot-verified directly against the live worktree (not just trusted from subagent
output) — line numbers, file contents, and the `env_io.py` shim were all re-confirmed.

**One correction that changes the mechanics**: `env_io.py`'s real implementation already
lives at repo root (a prior refactor moved it there); `gui/env_io.py` today is a 47-line
`sys.modules[__name__] = _env_io` identity-alias shim (confirmed by reading it — its own
docstring documents a real prior incident where a naive `import *` shim caused two tests
to silently write to a real `.env` file). So this move is `git mv env_io.py shared/env_io.py`
(repo root → `shared/`), not `gui/env_io.py` → `shared/`, and the shim gets deleted, not moved.

## Target layout

```
shared/                              # NEW top-level package — live, not frozen
  __init__.py                        # plain marker, no real imports (see §5)
  env_io.py                          # was repo-root env_io.py (NOT gui/env_io.py — that's a dead shim)
  ai_control_center.py               # was gui/ai_control_center.py
  ai_insights_panel.py
  circuit_breakers.py
  command_runner.py
  daemon_client.py
  dead_letter.py
  decision_log.py
  dependency_map.py
  engine_status.py
  export_utils.py
  gravity_ai_panel.py
  help_content.py
  help_widgets.py
  llm_commentary_panel.py
  market_data_diagnostics.py
  observability_panel_helpers.py
  observability_telemetry.py
  onboarding.py
  orchestrator_runner.py
  preflight_runner.py
  progress_ui.py
  regime_filter.py
  report_viewer_helpers.py           # gains GICS_SECTORS/_BF_EDITOR_COLUMNS natively (see §4a)
  robinhood_execution_panel.py
  robinhood_mode.py
  run_mode.py
  strategy_health.py
  strategy_registry.py
  styling.py
  symbol_search.py

legacy/                              # NEW — frozen, no-new-development
  __init__.py
  streamlit_command_center/
    __init__.py                      # rewritten from gui/__init__.py, kept substantive
    app.py                           # was gui/app.py
    panels/                          # was gui/panels/, all 22 files, structure unchanged
      __init__.py
      _shared.py                     # re-exports GICS_SECTORS/_BF_EDITOR_COLUMNS from shared/
      ai_control_center.py
      ai_insights.py
      analytics.py
      analytics_signals.py
      gravity_audit.py
      help.py
      launcher.py
      live_inventory.py
      market_data.py
      observability.py
      options_matrix.py
      pairs.py
      paper_monitor.py
      prompt_registry.py
      report_viewer.py
      reports_library.py
      sentiment_dynamics.py
      settings_manager.py
      strategy_matrix.py
      validation_lab.py
    app_shell.py                     # was app_shell.py (repo root)
    desktop_shell/
      __init__.py
      engine_supervisor.py           # was desktop/engine_supervisor.py
      ui_server.py                   # was desktop/ui_server.py
      net_util.py                    # was desktop/net_util.py

# gui/ ceases to exist entirely once emptied (git has no empty-dir concept).
# desktop/ keeps: __init__.py, daemon_runtime.py, orchestrator_daemon.py,
#                 run_history_store.py, daemon_status.py, assets/
# launch_app.command, launch_gui.command, scripts/build_macos_app.command
#   STAY at their current repo-root/scripts/ locations (see rationale below) —
#   only their internal references to app_shell.py's new path change.
```

**Why launchers stay put**: they're real, user-facing double-click entry points
(documented in `docs/RUNBOOK.md`, `docs/HOW_TO_GUIDE.md`, `README.md`, and referenced by a
possible real Finder/Dock shortcut via `scripts/build_macos_app.command`). Moving shell
scripts buys nothing for the "signal frozen via directory structure" goal (they're not
importable Python), and risks breaking a saved shortcut for zero benefit. Only their
internal invocation line changes.

## Execution order (rewrite-then-move at each stage, so every step is independently
## verifiable and a broken intermediate state never spans both an old and new import path)

### Step 1 — Rewrite `gui.X` → `shared.X` imports repo-wide, files still at old paths

For each of the 31 shared-module basenames, rewrite every `gui.<name>` reference
(`from gui.<name> import`, `import gui.<name>`, `from gui import <name>[, ...]` when every
named symbol is one of the 31):

```bash
MODS="ai_control_center ai_insights_panel circuit_breakers command_runner daemon_client \
dead_letter decision_log dependency_map engine_status env_io export_utils gravity_ai_panel \
help_content help_widgets llm_commentary_panel market_data_diagnostics \
observability_panel_helpers observability_telemetry onboarding orchestrator_runner \
preflight_runner progress_ui regime_filter report_viewer_helpers robinhood_execution_panel \
robinhood_mode run_mode strategy_health strategy_registry styling symbol_search"

for mod in $MODS; do
  grep -rlZ --include='*.py' -E "\bgui\.${mod}\b" . \
    | xargs -0 -r sed -i '' -E "s/\bgui\.${mod}\b/shared.${mod}/g"
done
```

Plus bare (never-went-through-`gui.env_io`) `env_io` imports, in exactly these files —
production: `api/_redact.py`, `api/data_api.py`, `api/pilots_api.py`, `conftest.py`,
`data/portfolio_sync.py`, `investyo_mcp_server.py`, `prompt_registry/__main__.py`,
`runtime_flags_writer.py`, `scripts/measure_settings_census.py`; tests:
`test_ai_control_center.py`, `test_gui_env_io.py`, `test_gui_env_io_atomic_write.py`,
`test_data_api.py`, `test_data_api_ai.py`, `test_gui_forecast_skill_panel.py`,
`test_launcher_safety_controls.py`, `test_investyo_mcp_server.py`,
`test_macro_regime_gate_toggle.py`, `test_portfolio_sync.py`, `test_prompt_registry_gui.py`,
`test_runtime_flags_writer.py`, `test_sector_backtest_settings.py`,
`test_strategy_registry.py`:

```bash
for f in api/_redact.py api/data_api.py api/pilots_api.py conftest.py data/portfolio_sync.py \
         investyo_mcp_server.py prompt_registry/__main__.py runtime_flags_writer.py \
         scripts/measure_settings_census.py \
         tests/test_ai_control_center.py tests/test_gui_env_io.py \
         tests/test_gui_env_io_atomic_write.py tests/test_data_api.py tests/test_data_api_ai.py \
         tests/test_gui_forecast_skill_panel.py tests/test_launcher_safety_controls.py \
         tests/test_investyo_mcp_server.py tests/test_macro_regime_gate_toggle.py \
         tests/test_portfolio_sync.py tests/test_prompt_registry_gui.py \
         tests/test_runtime_flags_writer.py tests/test_sector_backtest_settings.py \
         tests/test_strategy_registry.py; do
  sed -i '' -E 's/^(\s*)from env_io import/\1from shared.env_io import/; s/^(\s*)import env_io\b/\1import shared.env_io/' "$f"
done
```

**Verify this step**: `grep -rn '\bgui\.\(env_io\|orchestrator_runner\|...\)\b'` (all 31 names)
returns zero hits anywhere (imports will legitimately be broken until Step 2 — that's fine,
don't try to run anything yet).

### Step 2 — Move the 31 shared modules + env_io special case

```bash
mkdir -p shared
for f in ai_control_center ai_insights_panel circuit_breakers command_runner \
         daemon_client dead_letter decision_log dependency_map engine_status \
         export_utils gravity_ai_panel help_content help_widgets \
         llm_commentary_panel market_data_diagnostics observability_panel_helpers \
         observability_telemetry onboarding orchestrator_runner preflight_runner \
         progress_ui regime_filter report_viewer_helpers robinhood_execution_panel \
         robinhood_mode run_mode strategy_health strategy_registry styling symbol_search; do
  git mv "gui/${f}.py" "shared/${f}.py"
done
git mv env_io.py shared/env_io.py
git rm gui/env_io.py          # dead shim — its sole purpose (aliasing) is gone
```

**Verify**: `python -c "import shared.env_io, shared.orchestrator_runner, shared.daemon_client, shared.strategy_registry, shared.regime_filter, shared.report_viewer_helpers, shared.ai_control_center, shared.robinhood_mode, shared.help_content"`.

### Step 3 — Special-case fixes (all depend on `shared/` existing from Step 2)

**(a) Un-invert the `report_viewer_helpers.py` ↔ `panels/_shared.py` circular import.**
Today `gui/report_viewer_helpers.py` lazily imports `GICS_SECTORS`/`_BF_EDITOR_COLUMNS`
from `gui/panels/_shared.py` (3 function-local call sites, deliberate, documented). Since
`report_viewer_helpers.py` is now in `shared/` and `_shared.py` will move to the archive,
reverse the direction: define `GICS_SECTORS`/`_BF_EDITOR_COLUMNS` natively in
`shared/report_viewer_helpers.py` (move the definitions currently in
`gui/panels/_shared.py` lines 34–55 there, as plain top-level values — the 3 lazy-import
call sites in the same file become direct references). `gui/panels/_shared.py` will later
import them back from `shared.report_viewer_helpers` (handled in Step 4, since `_shared.py`
hasn't moved yet). Also repoint `tests/test_report_viewer_helpers.py` and
`Gravity AI Review Suite.py`'s line-5730 block (see Step 4 note) at
`shared.report_viewer_helpers` directly — that import site pulls in `GICS_SECTORS` +
4 Brinson-Fachler helper functions, none of which are actual Streamlit `render_*`
functions, so despite going through `gui.panels` today it's really a shared-module
consumer, not a panels consumer. Same correction applies to
`tests/test_brinson_fachler_ui.py` (its only `gui.*` import is this exact symbol set).

**(b) `ai_control_center.py` naming collision.** `shared/ai_control_center.py` and (later)
`legacy/streamlit_command_center/panels/ai_control_center.py` live in different packages —
no collision at the destination. The one-directional dependency (panels' 2 lazy
`from gui.ai_control_center import (...)` sites) will become
`from shared.ai_control_center import (...)` in Step 4. Same non-colliding pattern for
`ai_insights_panel.py` ↔ `panels/ai_insights.py` and `gravity_ai_panel.py` ↔
`panels/gravity_audit.py` — standard rewrite, no special handling needed.

**Verify**: re-run the Step 2 smoke imports plus
`python -c "import main, alerting, evaluation_engine, diagnostics_and_visuals"` and
`python -c "import api.pilots_api, api.data_api, api._jobs, api._redact"` and
`python -c "import pilots.models, pilots.observability, pilots.calibration"`.

### Step 4 — Rewrite `gui.panels` / `gui.app` → archive path, files still at old paths

```bash
grep -rlZ --include='*.py' -E "\bgui\.panels\b" . | xargs -0 -r sed -i '' -E \
  's/\bgui\.panels\b/legacy.streamlit_command_center.panels/g'
grep -rlZ --include='*.py' -E "\bgui\.app\b" . | xargs -0 -r sed -i '' -E \
  's/\bgui\.app\b/legacy.streamlit_command_center.app/g'
```

This mechanical pass covers ~20 test files (`test_launcher_maintenance.py`,
`test_launcher_safety_controls.py`, `test_gravity_audit_panel_helpers.py`,
`test_gui_pairs_panel.py`, `test_perf_gui_cache.py`, `test_gui_forecast_skill_panel.py`,
`test_gui_ml_monitoring.py`, `test_analytics_signals.py`, `test_gui_analytics_panels.py`,
`test_validation_lab_panel.py`, `test_snapshot_cache_freshness.py`,
`test_gui_env_io_etf_transmission_keys.py`, `test_gui_env_io_new_keys.py`,
`test_ai_control_center.py`, `test_reports_library.py`, `test_preflight_runner.py`,
`test_prompt_registry_gui.py`, `test_brinson_fachler_ui.py` — **except** its symbol set
per 3(a), skip/revert that one — `test_strategy_matrix_helpers.py`), all 22
`gui/panels/*.py` files' sibling references, and 5 genuine sites in
`Gravity AI Review Suite.py` (lines ~6941, 7079, 8389, 10097, 11805 —
**not** line 5730, which is the `report_viewer_helpers` case from 3(a): change that one to
`from shared.report_viewer_helpers import (...)` by hand instead).

**Hand-fix these — a line-based sed will miss string literals and mixed imports**:
- `tests/test_ai_control_center.py:725` (string `"from gui.ai_control_center import (\n"` → `shared.`) and `:732` (`"from gui.panels.ai_control_center import ..."` → archive path)
- `tests/test_reports_library.py:112` (string literal `"from gui.panels.reports_library import _html_file_block\n"` → archive path)
- `Gravity AI Review Suite.py` lines 7173, 8212, 11813, 13978, 14434 — hardcoded
  `Path("gui/app.py")` / `(repo_root / "gui" / "app.py")` string-path reads → update to
  `legacy/streamlit_command_center/app.py`
- `tests/test_run_mode.py:183`, `tests/test_robinhood_mode.py:227/231/243/252`,
  `tests/test_ui_server.py:68`, `tests/test_validation_lab_panel.py:135`,
  `tests/test_ai_control_center.py:566`, `tests/test_advisory_only.py:103`,
  `tests/test_prompt_registry_gui.py:438/442/446/451`, `tests/test_ai_insights_panel.py:304`
  — same hardcoded `"gui/app.py"` / `("gui", "app.py")` string pattern
- `gui/app.py:78` — mixed import `from gui import panels, run_mode, styling` needs manual
  splitting (see Step 5)
- `desktop/ui_server.py:78` — the literal `"gui/app.py"` argv element passed to
  `subprocess.Popen` (fixed alongside its `_REPO_ROOT` fix in Step 6)
- Also fold in the two extra "needs both" test files found during verification:
  `tests/test_gui_env_io_etf_transmission_keys.py` and `tests/test_gui_env_io_new_keys.py`
  each import `gui.env_io` (already handled, shared) **and** `gui.panels.settings_manager`
  (archive) in the same file.

**Verify**: `grep -rln '"gui/' --include='*.py' .` and
`grep -rn '\bgui\.\(panels\|app\)\b' --include='*.py' .` both return zero hits.

### Step 5 — Move `gui/app.py` + `gui/panels/`, fix path-depth + mixed import

```bash
mkdir -p legacy/streamlit_command_center
git mv gui/app.py legacy/streamlit_command_center/app.py
git mv gui/panels legacy/streamlit_command_center/panels
```

Then, in `legacy/streamlit_command_center/app.py`:
- Line ~61: `_REPO_ROOT = Path(__file__).resolve().parent.parent` →
  `.parent.parent.parent` (file is now 2 directories deeper below repo root, was 1).
- Split the mixed import (line 78): replace
  `from gui import panels, run_mode, styling` with:
  ```python
  from shared import run_mode, styling
  from . import panels
  ```

In `legacy/streamlit_command_center/panels/_shared.py`:
- Line 29: `_REPO_ROOT = Path(__file__).resolve().parent.parent.parent` →
  `.parent.parent.parent.parent` (one more level of nesting than before).
- Replace the local `GICS_SECTORS`/`_BF_EDITOR_COLUMNS` definitions (moved to
  `shared/report_viewer_helpers.py` in Step 3a) with
  `from shared.report_viewer_helpers import GICS_SECTORS, _BF_EDITOR_COLUMNS` and keep
  re-exporting them — every other panel file's `from ...panels._shared import GICS_SECTORS`
  reference (already rewritten to the archive path in Step 4) keeps working unchanged.

In `legacy/streamlit_command_center/panels/analytics.py:54` and
`legacy/streamlit_command_center/panels/analytics_signals.py:40`:
- `_REPO_ROOT = Path(__file__).resolve().parents[2]` → `parents[3]` (one more nesting level;
  used for real I/O against `ml/registry.yaml`, so getting this wrong silently breaks a
  real file read, not just an import).

**Verify**: `python -c "import ast; ast.parse(open('legacy/streamlit_command_center/app.py').read())"`
(a full `import` will legitimately fail outside a real Streamlit runtime — same as today,
not a regression); `python -c "import legacy.streamlit_command_center.panels"`; then a live
check: `streamlit run legacy/streamlit_command_center/app.py --server.headless true &`,
curl `127.0.0.1:8501`, kill it.

### Step 6 — Move `app_shell.py` + the 3 desktop native-shell files

```bash
mkdir -p legacy/streamlit_command_center/desktop_shell
git mv app_shell.py legacy/streamlit_command_center/app_shell.py
git mv desktop/engine_supervisor.py legacy/streamlit_command_center/desktop_shell/engine_supervisor.py
git mv desktop/ui_server.py legacy/streamlit_command_center/desktop_shell/ui_server.py
git mv desktop/net_util.py legacy/streamlit_command_center/desktop_shell/net_util.py
```

In `legacy/streamlit_command_center/desktop_shell/ui_server.py` — it already has a
top-level `from settings import settings` (unlike its two siblings, which only import
lazily), so fix its repo-root computation to be nesting-depth-independent instead of
re-counting `.parent`s:
```python
from settings import settings, ENV_PATH
...
_REPO_ROOT = ENV_PATH.parent   # was Path(__file__).resolve().parent.parent
```
(Confirmed safe: `settings.ENV_PATH = Path(__file__).resolve().parent / ".env"` at repo
root, and `settings.py` has zero `gui`/`desktop` imports at module level — no circularity.)
Also fix its line-78 argv string: `"gui/app.py"` → `"legacy/streamlit_command_center/app.py"`.

`engine_supervisor.py`'s only internal reference (`gui.orchestrator_runner.*`) was already
rewritten to `shared.orchestrator_runner.*` in Step 1. `net_util.py` is pure stdlib, no
changes needed beyond the move itself.

Create two new marker files: `legacy/__init__.py` and
`legacy/streamlit_command_center/desktop_shell/__init__.py` (one-line docstrings).

**Verify**: `python -c "import legacy.streamlit_command_center.app_shell"`,
`python -c "import legacy.streamlit_command_center.desktop_shell.engine_supervisor, legacy.streamlit_command_center.desktop_shell.ui_server, legacy.streamlit_command_center.desktop_shell.net_util"`.

### Step 7 — Remove `gui/` entirely

```bash
git rm gui/__init__.py
```
(`gui/` disappears once its last tracked file is gone — git has no empty-directory
concept, so nothing else to do.) No stub/shim package — leaving one behind would just be
new frozen-shaped surface to maintain, defeating the point of the move.

### Step 8 — Write `shared/__init__.py`

Replace the old `gui/__init__.py` docstring (which described the whole package as the
Streamlit app) with a plain marker — keep it free of any real import statement, since
`tests/test_pilots_api.py` (~line 2176) has an existing guard test
(`import gui; ast.parse(Path(gui.__file__)...)`) that asserts the package `__init__.py`
has zero real imports, specifically to catch anything piggy-backing into `api/pilots_api.py`
via an eager import. **Repoint that test at `shared`** (`import shared; ast.parse(...)`) —
it's now protecting the thing that actually matters.

```python
"""
shared/ — live backend logic shared by the Pilots PWA/API layer and the
frozen legacy Streamlit Command Center (legacy/streamlit_command_center/).

These modules originated in gui/ but are NOT part of the decommissioned UI —
they are imported directly by production code (api/pilots_api.py,
api/data_api.py, api/_jobs.py, pilots/*, main.py, evaluation_engine.py,
alerting.py, diagnostics_and_visuals.py, and scripts/). Keep this file free
of any real import statement (tests/test_pilots_api.py enforces this).
"""
from __future__ import annotations
```

### Step 9 — Fix the two launchers' one-line internal references

- `launch_app.command`: `python app_shell.py &` → `python -m legacy.streamlit_command_center.app_shell &`
  (module invocation, not a file path, so it doesn't care about `$SCRIPT_DIR` nesting —
  confirmed `app_shell.py` has no relative-import assumptions that would break under `-m`).
- `launch_gui.command`: `streamlit run gui/app.py` → `streamlit run legacy/streamlit_command_center/app.py`.
- `scripts/build_macos_app.command`: **no changes** — it only references `launch_app.command`
  (which isn't moving) and `desktop/assets/app_icon.png` (which isn't moving either).

### Step 10 — `cli_introspect/targets.py`

Delete the `Target("path", "app_shell.py", "app_shell.py", "python3 app_shell.py")` entry
outright (line 30) rather than repathing it — a decommissioned entry point shouldn't be
offered as a runnable command from the webapp Commands screen, and this sidesteps
`runpy.run_path`'s sys.path[0]-is-script-dir behavior at the new nested location entirely.
Then regenerate the committed manifest: `python scripts/build_command_manifest.py` (a
freshness test, `tests/test_command_manifest_freshness.py`, fails CI if this drifts).

### Step 11 — Docs pass

**CLAUDE.md** — replace the "Frontend strategy: web app only — desktop app decommissioned"
section with updated paths: point at `legacy/streamlit_command_center/{app.py,panels/,
app_shell.py,desktop_shell/}`, explicitly call out `shared/` as NOT part of the frozen
surface (it's live logic used by `api/pilots_api.py`/`api/data_api.py`/`api/_jobs.py`/
`pilots/*`/`main.py`/etc.), and note the launchers are unmoved (only their internal
reference changed — the operator-facing `./launch_app.command` / `./launch_gui.command`
commands are textually identical to before). Add `shared/` to the "Unaffected" backend-
infrastructure list alongside `main.py`, `main_orchestrator.py`, `api/*.py`. Since
`.claude/hooks/sync_agent_docs.sh` auto-mirrors CLAUDE.md → AGENTS.md, edit only CLAUDE.md.

**`docs/architecture/webapp-and-gui.md`** — update its `gui/`/`app_shell.py`/`desktop/`
path references (roughly lines 7, 51–62) to the new locations.

**Optional, low-priority**: add `legacy/*` to `.coveragerc`'s `omit` list (so frozen code
doesn't skew the `fail_under = 58` coverage floor) and to `.github/workflows/ci.yml`'s
Bandit `-x` exclude list (which already excludes `./gui`, precedent for excluding frozen
UI from security scanning).

## Verification (this is "Everything else" tier per CLAUDE.md — touches `api/*.py` and
## `desktop/` imports — full pass required, branch + PR, never direct to main)

```bash
# Smoke imports (production + shared + archive) — see per-step verify notes above for
# the full list; run them all again together as a final gate:
python -c "import shared.env_io, shared.orchestrator_runner, shared.daemon_client, \
shared.strategy_registry, shared.regime_filter, shared.report_viewer_helpers, \
shared.ai_control_center, shared.robinhood_mode, shared.help_content"
python -c "import api.pilots_api, api.data_api, api._jobs, api._redact"
python -c "import main, alerting, evaluation_engine, diagnostics_and_visuals"
python -c "import pilots.models, pilots.observability, pilots.calibration"
python -c "import investyo_mcp_server, runtime_flags_writer, data.portfolio_sync"
python -c "import legacy.streamlit_command_center.panels"
python -c "import legacy.streamlit_command_center.app_shell"
python -c "import legacy.streamlit_command_center.desktop_shell.engine_supervisor, \
legacy.streamlit_command_center.desktop_shell.ui_server, \
legacy.streamlit_command_center.desktop_shell.net_util"

# Residue check — must return nothing:
grep -rn '"gui/' --include='*.py' . | grep -v '^\./legacy/'
grep -rn '\bgui\.[a-z_]*\b' --include='*.py' . | grep -v '^\./legacy/' | grep -v 'streamlit'

# Manifest freshness:
python scripts/build_command_manifest.py
git status --short cli_introspect/command_manifest.json   # should show only the removed entry

# Full targeted + offline suites:
pytest tests/test_pilots_api.py tests/test_app_shell.py tests/test_engine_supervisor.py \
       tests/test_ui_server.py tests/test_net_util.py \
       tests/test_orchestrator_runner_daemon_cutover.py tests/test_report_viewer_helpers.py \
       tests/test_brinson_fachler_ui.py tests/test_ai_control_center.py \
       tests/test_reports_library.py tests/test_gui_env_io_etf_transmission_keys.py \
       tests/test_gui_env_io_new_keys.py tests/test_command_manifest_freshness.py -v
pytest -m "not network and not slow"    # full offline suite
make verify                              # or ./verify.command — this repo's full gate

# Live Streamlit smoke check (the one thing pytest can't cover):
streamlit run legacy/streamlit_command_center/app.py --server.headless true &
sleep 3 && curl -sf 127.0.0.1:8501 >/dev/null && echo OK; kill %1
```

Stay on the current branch `claude/investyo-command-center-cleanup-e9cd33` (already
up to date with `origin/main`, no divergence) and open a PR once verification is green —
per CLAUDE.md, do not push this directly to main since it touches `desktop/`/`api/*.py`
import wiring even though the change is a pure rename/move.

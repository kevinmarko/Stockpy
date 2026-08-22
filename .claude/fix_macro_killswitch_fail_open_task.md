# Task tracker: fix-macro-killswitch-fail-open

Branch: `fix-macro-killswitch-fail-open`

## Checklist

- [x] Explore: trace `macro_raw` → `run_macro_killswitch()` / `MacroEconomicDTO.killSwitch`
      call chain (two parallel Explore agents).
- [x] Design: draft implementation plan (2-field scope per the original task wording).
- [x] Design review: independent Plan-agent review of the draft — found (a) the
      scope should extend to `VIXCLS`/Sahm since they're the actually load-bearing
      fields for `killSwitch`'s base condition, (b) a landmine in the `main.py`
      `SAHMREALTIME` formula, (c) `stress_scenario_check`'s independent raw-VIX
      read bypasses `killSwitch` entirely.
- [x] Revise plan to fold in both design-review findings; user approved via
      `ExitPlanMode`.
- [x] `macro_engine.py`: `macro_killswitch_data_unavailable()` helper,
      `REGIME_CRITICAL_MACRO_KEYS`/`KILLSWITCH_CRITICAL_MACRO_KEYS`,
      `MacroDataSchema.data_unavailable` field, `run_macro_killswitch()` fail-closed
      regime override.
- [x] `macro_engine.py`: `MacroEngine._calculate_sahm_rule_detailed()` (backward-compatible
      split of `calculate_sahm_rule()`).
- [x] `dto_models.py`: `MacroEconomicDTO.data_unavailable` constructor kwarg;
      `killSwitch`/`_rules_based_regime` fail-closed short-circuits.
- [x] `main.py::_build_macro_dto()`: both synthetic-fallback branches set
      `data_unavailable=True`; live-fetch path fixed to call
      `_calculate_sahm_rule_detailed()` instead of reading the dead `SAHMREALTIME`
      key; computes `data_unavailable` via the shared helper + sahm fallback flag.
- [x] `pipeline/production_steps.py::OptionsAnalysisStep`: same wiring.
- [x] `execution/risk_gate.py::stress_scenario_check`: independent
      `data_unavailable` check (getattr-defensive).
- [x] `processing_engine.py::process_macro_regime()`: same treatment for the
      defensive dict→DTO branch.
- [x] Tests: rewrote the one test whose expected value changed
      (`test_missing_macro_keys_use_documented_defaults` →
      `test_missing_macro_keys_fail_closed_to_recession`), added unit tests for
      the helper/detailed-sahm split, DTO-level end-to-end tests, a new dedicated
      `tests/test_options_analysis_step_macro_dto.py`, `main.py`-level tests
      (healthy path + both fallback branches + missing-data path), and
      `stress_scenario_check` regression tests — 308 tests green across all
      touched files. Also fixed 4 pre-existing `fake_me = MagicMock()` test
      setups whose unconfigured `_calculate_sahm_rule_detailed()` call would have
      silently masked an exception-fallback path.
- [x] Docs: `docs/architecture/signal-engines.md`'s `macro_engine.py` entry,
      `CLAUDE.md`'s "Macro Regime Gate" bullet (auto-mirrored to `AGENTS.md`),
      new `docs/known_issues/macro_killswitch_fail_open_on_missing_fred_data.md`,
      cross-linked from that doc's `README.md` index.
- [x] Real-data verification: ran against the operator's own live `FRED_API_KEY`
      (direct `DataEngine.fetch_macro_raw()`, `main._build_macro_dto()`, and a
      full `main.run_once()` cycle over 32 real symbols) — confirmed
      `data_unavailable=False`/`killSwitch=False` on healthy data, zero errors,
      and a real non-zero Sahm reading in `main.py`'s DTO (previously always
      `0.0` due to the dead-key bug).
- [x] Lint: `ruff check --select=F821,F822,F823,E9` (the repo's actual CI gate,
      confirmed via `.github/workflows/ci.yml`) on every touched file — clean.
- [x] Full offline `pytest` suite: 11881 passed, 31 skipped, 6 failed. All 6
      failures confirmed pre-existing and unrelated (`ModuleNotFoundError:
      openai`, `ImportError: google.genai`, a stale
      `docs/settings_liveness.json` artifact from unrelated prior work) — none
      touch macro/DTO/risk-gate code.
- [x] Rebased onto `origin/main` (which had advanced during this session via
      other concurrent work) — clean rebase, no conflicts; re-ran the targeted
      suite post-rebase, still 308/308 green.
- [x] Copy PR artifacts into `.claude/` with unique, feature-scoped names (this
      file + the implementation plan + the walkthrough).
- [ ] Open PR against `main`.

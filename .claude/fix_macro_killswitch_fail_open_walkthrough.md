# Walkthrough: fix-macro-killswitch-fail-open

## Problem

`macro_engine.py:258-259` (`MacroEngine.run_macro_killswitch`) substituted
hardcoded "benign" defaults (`T10Y2Y=0.5`, `BAMLH0A0HYM2=3.5`) whenever those
FRED series were missing from `macro_raw`. During a real FRED outage, this
let the killswitch's regime classification silently read as "not stressed"
rather than surfacing the outage — a CONSTRAINT #4 (never fabricate a
safety-critical input) / CONSTRAINT #6 (fail closed) violation.

## Investigation

Two Explore agents traced the full call chain in parallel: one through
`macro_engine.py`/`dto_models.py`/`main.py`/`pipeline/production_steps.py`
and the existing test coverage; the other through the repo's established
NaN/fail-closed conventions (`data/historical_store.py`,
`risk/etf_transmission.py`, `execution/risk_gate.py`,
`execution/kill_switch.py`) and the formal CONSTRAINT #4/#6 wording.

Key finding: `run_macro_killswitch()`'s output is **not** the live kill
switch — in `pipeline/production_steps.py::OptionsAnalysisStep`, its
returned DataFrame is computed and never read again. The real
`MacroEconomicDTO` (which `execution/risk_gate.py::PreTradeRiskGate` reads
for order approval) is built independently in `main.py::_build_macro_dto()`
and `pipeline/production_steps.py::OptionsAnalysisStep.run()`, both from
`macro_raw.get(key, default)`. `MacroEconomicDTO.killSwitch`'s actual base
condition (`sahm_rule_indicator >= 0.5 or vix > 30.0`) is driven by
`VIXCLS`/Sahm, **not** the two fields named in the original report — those
only feed the regime classification, a secondary path. This meant the
originally-scoped fix (2 fields, `killSwitch` only) would have left the
primary attack surface open.

## Design review (before writing code)

A Plan agent independently reviewed the draft design (not just rubber-
stamped it) and found two problems that had to be fixed, not just noted:

1. **A landmine**: the draft's `main.py` formula checked
   `"SAHMREALTIME" not in macro_raw`. Verified via `grep`:
   `SAHMREALTIME` never appears anywhere in `data_engine.py` — it's a key
   `fetch_macro_raw()` never populates. Checking for its absence would have
   been unconditionally `True` every cycle, permanently forcing the kill
   switch on for `main.py --interval` (the default production refresh loop).
   Root cause: `main.py:476`'s pre-existing
   `macro_raw.get("SAHMREALTIME", 0.0)` was **already dead code** — always
   `0.0`, regardless of FRED health, uncaught by any existing test.
2. **A second live gate**: `execution/risk_gate.py::stress_scenario_check`
   blocks premium-selling orders purely on `context.macro.vix > 30.0`,
   independent of `killSwitch`. A `killSwitch`-only fix would have left this
   gate exposed to the exact same fabricated-VIX problem.

Both were folded into the plan before any code was written.

## What changed

| File | Change |
|---|---|
| `macro_engine.py` | New `macro_killswitch_data_unavailable(macro_raw, keys=...)` helper + `REGIME_CRITICAL_MACRO_KEYS`/`KILLSWITCH_CRITICAL_MACRO_KEYS`; `MacroDataSchema` gained a `data_unavailable: bool` field; `run_macro_killswitch()` forces `"RECESSION"` (not silently "RISK ON") when `T10Y2Y`/`BAMLH0A0HYM2` are missing; `calculate_sahm_rule()` split into a byte-identical delegate over new `_calculate_sahm_rule_detailed() -> (value, used_fallback)`. |
| `dto_models.py` | `MacroEconomicDTO.__init__` gained `data_unavailable: bool = False`; `killSwitch`/`_rules_based_regime` both fail closed (`True`/`"RECESSION"`) as their first check when set. Default `False` preserves every untouched call site's behavior exactly. |
| `main.py` | `_build_macro_dto()`'s two synthetic-fallback branches now set `data_unavailable=True`; the live-fetch path stopped reading the dead `SAHMREALTIME` key and now calls `_calculate_sahm_rule_detailed()` — fixing the independent dead-code bug as a side effect of correctly computing `data_unavailable`. |
| `pipeline/production_steps.py` | `OptionsAnalysisStep.run()` switched to `_calculate_sahm_rule_detailed()` and computes `data_unavailable` the same way. |
| `execution/risk_gate.py` | `stress_scenario_check` independently checks `getattr(context.macro, "data_unavailable", False)`. |
| `processing_engine.py` | `process_macro_regime()`'s defensive dict→DTO branch gets the same treatment (previously never set `vix_value`/`sahm_rule_indicator` from the dict at all). |

## Tests

308 tests green across every touched file
(`tests/test_macro_engine.py`, `tests/test_macro_hmm_integration.py`,
`tests/test_run_once.py`, `tests/test_risk_gate.py`,
`tests/test_production_steps_options_columns.py`,
`tests/test_options_analysis_step_macro_dto.py` (new file),
`tests/test_processing_engine.py`, `tests/test_bug_fixes.py`,
`tests/test_vrp_premium_selling.py`,
`tests/test_gravity_mirrored_invariants.py`), including:

- The one pre-existing test whose expected value genuinely changes
  (`test_missing_macro_keys_use_documented_defaults` → rewritten as
  `test_missing_macro_keys_fail_closed_to_recession`).
- New unit tests for `macro_killswitch_data_unavailable()` and
  `_calculate_sahm_rule_detailed()` in isolation.
- DTO-level end-to-end tests proving `data_unavailable=True` forces
  `killSwitch=True`/`market_regime="RECESSION"` regardless of otherwise-benign
  fields, and that `data_unavailable=False` (default) is byte-identical to
  pre-fix behavior.
- A new dedicated file, `tests/test_options_analysis_step_macro_dto.py`,
  exercising `OptionsAnalysisStep.run()`'s macro-DTO construction end-to-end
  (healthy/missing-data/sahm-fallback-only cases) with `ctx.symbols=[]` to
  keep the per-ticker options/GARCH loop out of scope.
- `main.py`-level tests for `_build_macro_dto()`: a healthy real-shaped
  `macro_raw` (matching what `fetch_macro_raw()` actually returns —
  no `SAHMREALTIME` key) reporting `data_unavailable=False`; an empty
  `macro_raw`; the Sahm-fallback-alone case; both synthetic branches.
- `stress_scenario_check` regression tests: blocks on `data_unavailable=True`
  despite a benign VIX; passes when available; degrades safely via `getattr`
  when the macro object lacks the attribute entirely.
- Found and fixed 4 pre-existing tests in `tests/test_run_once.py`
  (`fake_me = MagicMock()` setups) whose unconfigured
  `_calculate_sahm_rule_detailed()` call would have silently raised and been
  swallowed by `_build_macro_dto()`'s own exception handler — the tests still
  passed, but for the wrong reason (their intended live-fetch-success path
  was never actually exercised). Fixed by explicitly configuring the mock's
  return value and adding assertions that only hold if the live path ran.

## Real-data verification

Per the operator's request, verified against their own live `.env`
`FRED_API_KEY` (never written to any file in this worktree — extracted as a
shell variable and passed only as a transient environment variable for the
verification process):

1. `DataEngine.fetch_macro_raw()` direct call: real values
   (`T10Y2Y=0.68`, `BAMLH0A0HYM2=3.93`, `UNRATE=3.4`, `VIXCLS=20.26`),
   `macro_killswitch_data_unavailable()` correctly `False`.
2. `main._build_macro_dto()` end-to-end: `sahm_rule_indicator=0.03` (a real,
   non-zero FRED-derived reading — confirming the `SAHMREALTIME` dead-key
   fix works), `data_unavailable=False`, `killSwitch=False`,
   `market_regime="RISK ON"`.
3. A full `main.run_once(force_account=False)` cycle (32 real symbols from
   the operator's actual held-positions + watchlist union): completed in
   ~64s with **0 errors**, macro DTO built as `regime=NEUTRAL VIX=20.3
   data_unavailable=False` (NEUTRAL via the pre-existing, unmodified HMM
   disagreement-downgrade logic — HMM risk-on probability was low that
   cycle, unrelated to this fix).

## Verification (offline)

- `ruff check --select=F821,F822,F823,E9` (the repo's actual CI lint gate,
  confirmed via `.github/workflows/ci.yml` rather than assumed) on every
  touched file: clean.
- Full offline pytest suite: **11881 passed, 31 skipped, 6 failed.** All 6
  failures independently confirmed pre-existing and unrelated: 2 are
  `ModuleNotFoundError`/`ImportError` for optional deps not installed in this
  venv (`openai`, `google.genai`), and 1 is a stale
  `docs/settings_liveness.json` census artifact drifted by unrelated
  concurrent work on `alerting_mcp/notifier.py` (this PR adds no new
  `settings.*` field). None touch `macro_engine.py`, `dto_models.py`,
  `main.py`'s macro path, `pipeline/production_steps.py`'s macro step,
  `execution/risk_gate.py`'s macro checks, or `processing_engine.py`.
- Rebased onto `origin/main` cleanly (it had advanced during this session via
  other concurrent work in sibling worktrees sharing this repo's `.git`) —
  no conflicts, re-ran the full targeted suite post-rebase, still 308/308.

## What's explicitly out of scope (disclosed)

- `data_engine.py::fetch_macro_raw()`'s own `_MACRO_HARDCODED_FALLBACK` —
  pre-existing, deliberate, already logged; untouched.
- `investyo_mcp_server.py::trigger_macro_engine`'s hardcoded
  `"kill_switch_active": False` — a separate, adjacent bug in an MCP
  diagnostic tool.
- `engine/advisory.py`'s soft VIX/Sahm score-penalty branch (the hard
  `market_regime` gate is fixed transitively; the soft branch is a
  scoring-cosmetics gap during an outage, not trade-blocking).
- The pre-existing, separately-pinned `sahm_rule_indicator=None` coercion
  gap (`tests/test_gravity_mirrored_invariants.py`) — unrelated, untouched.

See `docs/known_issues/macro_killswitch_fail_open_on_missing_fred_data.md`
for the full incident write-up.

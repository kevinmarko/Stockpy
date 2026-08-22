# Known issue (2026-08-22): macro kill switch silently read fabricated FRED defaults as "risk on"

**Status: fixed.** Branch `fix-macro-killswitch-fail-open`.

## What happened

`macro_engine.py::MacroEngine.run_macro_killswitch()` substituted hardcoded
"benign" literal defaults (`T10Y2Y=0.5`, `BAMLH0A0HYM2=3.5`) whenever those
FRED series were missing from its `macro_raw` input — both values chosen to
land on the enum's least-severe branch (`"RISK ON"`). During a genuine FRED
outage, this let the classification silently read as "not stressed" instead
of surfacing the outage.

Tracing the full call chain (an Explore pass plus a Plan-agent design review
before implementation) found the bug was both real and larger than that one
function suggested:

- **`run_macro_killswitch()`'s output is not the live kill switch at all.**
  In `pipeline/production_steps.py::OptionsAnalysisStep` (the
  `main_orchestrator.py` production pipeline), its returned DataFrame is
  computed and never read again — the `MacroEconomicDTO` that
  `execution/risk_gate.py::PreTradeRiskGate` actually reads for order
  approval is built three lines later, directly from `ctx.macro_raw`,
  independently re-deriving the same `.get(key, default)` pattern.
- **The two real construction sites** — `main.py::_build_macro_dto()` and
  `pipeline/production_steps.py::OptionsAnalysisStep.run()` — build
  `MacroEconomicDTO` from `macro_raw.get(key, default)` for `T10Y2Y`,
  `BAMLH0A0HYM2`, `VIXCLS`, and (`main.py` only) `SAHMREALTIME`.
- **`MacroEconomicDTO.killSwitch`'s actual base condition is
  `sahm_rule_indicator >= 0.5 or vix > 30.0`** (`dto_models.py`) — driven by
  `VIXCLS`/Sahm, *not* the two fields named in the original report.
  `T10Y2Y`/`BAMLH0A0HYM2` only feed `_rules_based_regime`, gating a
  secondary HMM-agreement lower-threshold branch. The two fields originally
  flagged were the **less** load-bearing half of the bug — a fix scoped to
  only those two would have left the primary attack surface (a fabricated
  VIX/Sahm reading feeding `killSwitch` directly) untouched.

A design-review pass (an independent Plan-agent review of the first draft,
before any code was written) caught two further problems the initial design
would have introduced or missed:

1. **A landmine in the first-draft `main.py` fix.** The draft computed
   `main.py`'s missing-data flag partly via `"SAHMREALTIME" not in
   macro_raw`. Verified directly: `SAHMREALTIME` never appears anywhere in
   `data_engine.py` — neither `fetch_macro_raw()`'s success dict nor its
   `_MACRO_HARDCODED_FALLBACK` contains that key. `main.py`'s existing
   `sahm_rule_indicator=float(macro_raw.get("SAHMREALTIME", 0.0))` was
   therefore **already dead code** before this fix — it silently returned
   `0.0` every cycle regardless of FRED health, and no existing test caught
   it (`tests/test_run_once.py` had zero assertions on `sahm` before this
   change). Checking for that key's absence would have been unconditionally
   `True` every cycle, permanently forcing the kill switch on for
   `main.py --interval` (the default production refresh loop, since
   `ORCHESTRATOR_DAEMON_ENABLED` defaults `False`) — silently converting
   "sometimes fails open" into "always fails closed."
2. **Patching `killSwitch` alone would have left a second live gate exposed.**
   `execution/risk_gate.py::stress_scenario_check` blocks premium-selling
   orders (credit spreads, naked short options — the highest tail-risk
   strategy class) purely on `context.macro.vix > 30.0`, a raw field read
   independent of `killSwitch`. During an outage this would still read the
   fabricated benign default and let premium-selling orders through
   untouched even after `killSwitch` itself was fixed.

## Real impact

`settings.MACRO_REGIME_GATE_ENABLED` defaults `True` ("autonomous mode") and
is specifically meant to veto new BUY orders during systemic stress (Sahm
Rule ≥ 0.5, VIX > 30, or the derived regime reaching RECESSION/CREDIT EVENT).
A FRED outage or dead-letter path (`macro_raw={}` from
`main_orchestrator.fetch_all_data_async()`'s exception handler, or FRED
simply being unreachable) is exactly the kind of degraded-data condition this
gate exists to catch — and it was silently computing "all clear" in that
exact scenario instead. No live incident is known to have been caused by
this; it was found during a proactive review, not from an observed bad
trade.

## How it was discovered

Operator-directed review request naming the two hardcoded literals at
`macro_engine.py:258-260`. Two parallel Explore passes traced the full call
chain from `DataEngine.fetch_macro_raw()` through both `MacroEconomicDTO`
construction sites to every consumer of `.killSwitch`/`.market_regime`; a
subsequent Plan-agent design review, run before writing any code, caught the
two additional problems described above.

## The fix

- `macro_engine.py::macro_killswitch_data_unavailable(macro_raw, keys=...)` —
  a shared pure helper checking presence of the FRED series
  `killSwitch`/regime classification actually depend on
  (`KILLSWITCH_CRITICAL_MACRO_KEYS = ("T10Y2Y", "BAMLH0A0HYM2", "VIXCLS")`;
  `run_macro_killswitch()` passes the narrower `REGIME_CRITICAL_MACRO_KEYS`
  since it never receives `VIXCLS` as an argument at all).
- `MacroEconomicDTO.__init__` gained `data_unavailable: bool = False`.
  `killSwitch` and `_rules_based_regime` (and therefore `market_regime`,
  which calls through it) both check `data_unavailable` first and fail
  closed (`killSwitch=True`, regime forced to `"RECESSION"`) before computing
  anything else. Every pre-existing construction site not touched by this
  fix defaults `data_unavailable=False` and is byte-identical.
- `run_macro_killswitch()` gained the same fail-closed override (forcing its
  `"RISK ON"` fallthrough to `"RECESSION"` when `T10Y2Y`/`BAMLH0A0HYM2` are
  missing) plus a new `data_unavailable: bool` column on its output
  DataFrame/`MacroDataSchema`.
- `MacroEngine.calculate_sahm_rule()`'s body was extracted into a new
  private `_calculate_sahm_rule_detailed(fallback_val=0.0) ->
  tuple[float, bool]` reporting whether the fallback was actually used;
  `calculate_sahm_rule()` becomes a byte-identical one-line delegate over
  it (verified against every existing caller/test).
- `main.py::_build_macro_dto()`: both synthetic-fallback branches (no
  `FRED_API_KEY`; the outer exception handler) now set
  `data_unavailable=True` unconditionally. The live-fetch path stopped
  reading the dead `SAHMREALTIME` key and now calls
  `_calculate_sahm_rule_detailed()` instead (the same primitive
  `pipeline/production_steps.py` already used) — fixing the independent
  dead-code bug as a necessary side effect of correctly computing
  `data_unavailable`. `data_unavailable = macro_killswitch_data_unavailable(macro_raw)
  or sahm_used_fallback`.
- `pipeline/production_steps.py::OptionsAnalysisStep.run()`: switched to
  `_calculate_sahm_rule_detailed()` and computes `data_unavailable` the same
  way.
- `execution/risk_gate.py::stress_scenario_check`: now also blocks
  premium-sell orders when `getattr(context.macro, "data_unavailable",
  False)` is `True`, independent of the (possibly fabricated) raw VIX
  reading.
- `processing_engine.py::process_macro_regime()`'s defensive
  `isinstance(macro_dto, dict)` branch (previously never set
  `vix_value`/`sahm_rule_indicator` at all, silently keeping the DTO class
  defaults) got the same treatment.

Verified against real, live FRED data (the operator's own `FRED_API_KEY`,
2026-08-22): a healthy fetch (`T10Y2Y=0.68`, `BAMLH0A0HYM2=3.93`,
`VIXCLS=20.26`, real Sahm reading `0.03`) correctly produces
`data_unavailable=False`, `killSwitch=False`, `market_regime="RISK ON"` —
confirming the fix is behavior-neutral when data is actually available, and
that the `SAHMREALTIME` dead-key fix now surfaces a real, non-zero Sahm
reading in `main.py`'s DTO instead of the previous structurally-dead `0.0`.

## Follow-up fix (2026-08): the populated-but-fabricated blind spot

The three items below were disclosed as out of scope by the original fix.
A follow-up pass (branch `fix-macro-fallback-fabrication-visibility`) found
that the first of them was not cosmetic — it was the actual remaining live
gap, and arguably worse than the original bug: `macro_killswitch_data_unavailable()`
checks **key presence** (`k not in macro_raw or macro_raw.get(k) is None`).
`data_engine.py::DataEngine.fetch_macro_raw()`'s `_MACRO_HARDCODED_FALLBACK`
always populates *every* killswitch-critical key (`T10Y2Y`, `BAMLH0A0HYM2`,
`VIXCLS`) with a fabricated literal — so a caller falling back to it silently
(no exception raised outward) made `macro_killswitch_data_unavailable()`
report `False` (every key "present") at all three of its real call sites
(`main.py::_build_macro_dto()`, `main_orchestrator.py::fetch_all_data_async()`
→ `pipeline/production_steps.py::OptionsAnalysisStep`, and
`investyo_mcp_server.py`'s two `trigger_macro_engine`/`trigger_full_pipeline`
call sites). The original fix's own key-presence check only ever covered a
`macro_raw = {}` total-absence case (e.g. the async dead-letter path), not
this dict-populated-with-fabricated-values case.

A closer read of `fetch_macro_raw()` also found a narrower, independent
fabrication path *inside* its "success" branch: the VIXCLS read is wrapped
in its own inner `try/except` that silently substitutes `vix = 15.0` on
failure while `T10Y2Y`/`BAMLH0A0HYM2`/`UNRATE` succeed — the function
returns as a fully-successful FRED read, never reaching the
fallback-tracking/warning-log code at all. VIX is the single most
load-bearing field for `killSwitch` (`vix > 30.0` fires it directly, no HMM
agreement needed), so this could silently fabricate the kill switch's most
sensitive input even when every other series was real.

**Fixed via**: `DataEngine.fetch_macro_raw_detailed() -> Tuple[Dict[str, Any],
FrozenSet[str]]` (mirroring `_calculate_sahm_rule_detailed()`'s tuple-return
pattern) reports which returned keys are fabricated placeholders, stashed on
`self.last_macro_raw_fabricated_keys` as a side effect so a caller of the
plain `fetch_macro_raw()` can recover it too (`getattr(de,
"last_macro_raw_fabricated_keys", frozenset())`). `fetch_macro_raw()` itself
becomes a byte-identical one-line delegate. `macro_killswitch_data_unavailable(macro_raw,
keys=..., fabricated_keys=frozenset())` gained the new `fabricated_keys`
parameter (default reproduces every pre-existing call site exactly);
`run_macro_killswitch(..., fabricated_keys=frozenset())` threads the same
signal into its own internal check. All three real DTO-construction call
sites now pass `fabricated_keys` through. `investyo_mcp_server.py::trigger_macro_engine`
was additionally rewritten to build a real `MacroEconomicDTO` and report
`kill_switch_active=bool(macro_dto.killSwitch)` instead of a hardcoded
`False` literal, plus a new `data_unavailable` field in its payload and
honestly-populated `high_yield_oas`/`yield_curve` (previously always `None`).
`engine/advisory.py`'s soft score-penalty branch gained an `or
macro_dto.data_unavailable` clause alongside its VIX/Sahm threshold checks —
traced to be currently unreachable in practice (the hard gate immediately
above always fires first whenever `data_unavailable=True`, since that forces
`market_regime` to `"RECESSION"`), but added anyway as the same
defense-in-depth precedent this fix's own original pass set for
`execution/risk_gate.py::stress_scenario_check`.

Tests: `tests/test_fmp_macro.py::TestFetchMacroRawFabricatedKeys`,
`tests/test_macro_engine.py` (new cases in `TestMacroKillswitchDataUnavailable`/
`TestRunMacroKillswitch`), `tests/test_run_once.py::TestBuildMacroDtoDataUnavailable`,
`tests/test_options_analysis_step_macro_dto.py`,
`tests/test_investyo_mcp_server.py::TestTriggerMacroEngineKillSwitchActive`,
`tests/test_advisory_pause_gate.py::TestMacroTriggeredGating::test_soft_gate_data_unavailable_defense_in_depth`.

## What is still open

- `dto_models.py`'s existing, separately-pinned gap
  (`tests/test_gravity_mirrored_invariants.py::test_sahm_rule_indicator_none_is_not_coerced_and_crashes_downstream`)
  — `sahm_rule_indicator=None` is not coerced through `_to_float()` like its
  sibling fields and crashes on first `killSwitch`/`market_regime` access —
  is unrelated to and untouched by either fix (neither pass ever passes
  `None` for that field).

## Related

- CLAUDE.md's "Macro Regime Gate" bullet — updated alongside this fix to
  describe the new fail-closed behavior.
- `docs/architecture/signal-engines.md`'s `macro_engine.py` entry — updated
  with the full technical detail.
- `.claude/skills/stockpy-quant-integrity/SKILL.md`'s CONSTRAINT #4 ("never
  fabricate a metric") and CONSTRAINT #6 ("fail closed") — the two
  constraints this fix exists to satisfy; CONSTRAINT #6's own wording
  ("a failed HMM regime detector should degrade to neutral, not to risk on")
  is the closest existing formal articulation of the principle this bug
  violated.

# Fix: macro killswitch fails open on missing FRED data

## Context

`macro_engine.py:258-259` (`MacroEngine.run_macro_killswitch`) substitutes
hardcoded "benign" defaults (`T10Y2Y=0.5`, `BAMLH0A0HYM2=3.5`) when those FRED
series are missing from `macro_raw`. The reported concern: during a real FRED
outage, the killswitch computes its regime/verdict off fabricated data that
reads as "not stressed," rather than failing closed.

Tracing the full call chain (two Explore passes, direct reads of the source,
and an independent Plan-agent review of the draft design) shows the bug is
real but **larger than the two named fields, and the first-draft fix for it
was itself incomplete in two separate ways that the design review caught
before implementation**. Both are folded into this plan.

### What's actually going on

- **`run_macro_killswitch()`'s output is structurally disconnected from the
  real kill switch.** In `pipeline/production_steps.py`'s `OptionsAnalysisStep`
  (the `main_orchestrator.py` pipeline), its returned DataFrame is computed
  and never read again — `ctx.macro_dto` (the thing `PreTradeRiskGate`
  actually reads) is built three lines later, directly from `ctx.macro_raw`,
  independently re-deriving the same `.get(key, default)` pattern. Still
  worth fixing because it's surfaced via `investyo_mcp_server.py`'s
  `trigger_macro_engine`/`trigger_full_pipeline` MCP diagnostic tools, and has
  an existing test asserting the *current* (wrong) behavior.
- **The two real construction sites are `main.py::_build_macro_dto()`
  (lines ~470-479) and `pipeline/production_steps.py::OptionsAnalysisStep.run()`
  (lines ~200-209).** Both build `MacroEconomicDTO` directly from
  `macro_raw.get(key, default)`.
- **`MacroEconomicDTO.killSwitch`'s actual base condition is
  `sahm_rule_indicator >= 0.5 or vix > 30.0`** (`dto_models.py:358`) — driven
  by `VIXCLS`/Sahm, *not* the two fields named in the report. `T10Y2Y`/
  `BAMLH0A0HYM2` only feed `_rules_based_regime`, gating a secondary
  HMM-agreement branch. **The two fields named in the task are the less
  load-bearing half of this bug** — a fix scoped to only those two would
  leave the primary attack surface untouched.
- `DataEngine.fetch_macro_raw()`'s own internal fallback
  (`_MACRO_HARDCODED_FALLBACK`, `data_engine.py:90-97`) is a **separate,
  already-disclosed, already-logged CONSTRAINT #4 exception** with an
  explicit code comment ("Do not 'improve' these numbers here") pointing at
  `settings.FMP_MACRO_ENABLED` as the real replacement path. Not touched by
  this plan.

### Two problems the design-review pass found in the first draft — both must be fixed, not just noted

**1. A landmine in the originally-proposed `main.py` fix.** The first draft
computed `main.py`'s `data_unavailable` flag partly via `"SAHMREALTIME" not in
macro_raw`. Verified directly (`grep -n "SAHMREALTIME" data_engine.py` →
zero hits): **`SAHMREALTIME` never appears anywhere in `data_engine.py`** —
neither `fetch_macro_raw()`'s success dict nor its `_MACRO_HARDCODED_FALLBACK`
contains that key. `main.py:476`'s existing
`sahm_rule_indicator=float(macro_raw.get("SAHMREALTIME", 0.0))` is therefore
**already dead code today** — it silently reads `0.0` every single cycle,
FRED healthy or not, and no existing test (`tests/test_run_once.py` has zero
assertions on `sahm`) has ever caught this. Naively checking
`"SAHMREALTIME" not in macro_raw` would therefore be `True` unconditionally,
permanently forcing `data_unavailable=True` → `killSwitch=True` on every
future cycle through `main.py --interval` — i.e. it would silently convert
"sometimes fails open" into "always fails closed," blocking every future BUY
order regardless of real market conditions. Per `CLAUDE.md`'s own
"Persistent orchestrator daemon" bullet, `ORCHESTRATOR_DAEMON_ENABLED`
defaults `False`, so `main.py --interval N` is not a minor path — it's the
default production refresh loop.

  **Fix folded into this plan**: `main.py::_build_macro_dto()`'s live-fetch
  path must actually call `MacroEngine._calculate_sahm_rule_detailed()` (see
  step 3 below) for its Sahm value, the same way
  `pipeline/production_steps.py` already correctly does via
  `calculate_sahm_rule()` — instead of reading a `macro_raw` key that
  `fetch_macro_raw()` never populates. This closes a second, independent,
  pre-existing live bug (the exact same class of bug `tests/test_bug_fixes.py`'s
  BUG-1 regression guard already caught and fixed for
  `pipeline/production_steps.py`, but which was apparently never mirrored
  into `main.py`'s separate `_build_macro_dto()` implementation) as a
  necessary side effect of correctly computing `data_unavailable` there.

**2. Patching `killSwitch` alone is insufficient — `market_regime` and raw
`.vix` are read directly by other live gates.** At least one other pre-trade
risk-gate check bypasses `killSwitch` entirely:
`execution/risk_gate.py::stress_scenario_check` (`risk_gate.py:543-557`,
verified directly) blocks premium-selling orders (credit spreads, naked short
options — the highest tail-risk strategy class, gated on
`context.is_premium_sell_strategy`, confirmed set `True` by
`execution/options_queue_builder.py` in the real order-queue builder) purely
on `context.macro.vix > 30.0` — a raw field read, independent of `killSwitch`.
During a FRED outage this still reads the fabricated benign default (15-18),
so **premium-selling orders would sail through this second gate untouched**
even after `killSwitch` itself is fixed. Separately, `market_regime`
(`dto_models.py`'s `cached_property`, derived from `_rules_based_regime`) is
read directly by `engine/advisory.py`'s regime-gating step, and by
`signals/macro_regime.py`/`signals/rsi2_mean_reversion.py`/
`signals/news_catalyst.py`'s `is_active_in_regime` gates — all of which would
still see a fabricated "RISK ON"/benign classification during an outage.
CONSTRAINT #6's own wording (which the original report already invokes):
*"a failed HMM regime detector should degrade to neutral, not to risk on"* —
applied consistently, not just to one property.

  **Fix folded into this plan**: extend the same `data_unavailable` fail-closed
  short-circuit to `_rules_based_regime` (which `market_regime` already calls
  through, so no separate change needed there), and add a `context.macro.data_unavailable`
  check to `stress_scenario_check` directly (see steps 5 and 7 below).

### Scope decision (confirmed via design review)

Fix all four load-bearing fields (`T10Y2Y`, `BAMLH0A0HYM2`, `VIXCLS`, Sahm —
via `calculate_sahm_rule`'s fallback signal, not the dead `SAHMREALTIME` key),
extend the fix to `market_regime` in addition to `killSwitch`, and patch
`stress_scenario_check`. This is **larger** than the letter of the original
task (which named two fields and didn't mention `market_regime` or
`stress_scenario_check` at all), but the design review confirmed a
`killSwitch`-only, two-field fix would leave the actual failure mode (a
premium-selling order surviving a FRED outage, or an advisory-scoring branch
silently reading "RISK ON") open. Existing convention to explicitly
*not* change: `execution/risk_gate.py`'s stated house rule that missing
*context* (`context.macro is None`) passes conservatively — that's about a
wholly absent DTO, a narrower, different case than this bug (a DTO that
exists but was built from silently-substituted placeholder inputs).

## Implementation

### 1. Branch
`git checkout -b fix-macro-killswitch-fail-open` (signal/orchestration logic
— "Everything else" tier, no direct commits to `main`).

### 2. New detection helper — `macro_engine.py`
Module-level, pure function:

```python
# Keys MacroEconomicDTO.killSwitch's base condition and regime
# classification actually depend on (see dto_models.py::killSwitch /
# ::_rules_based_regime). CPIAUCSL_YoY / DGS10 feed inflation/real_yield only
# and are deliberately excluded.
KILLSWITCH_CRITICAL_MACRO_KEYS = ("T10Y2Y", "BAMLH0A0HYM2", "VIXCLS")

def macro_killswitch_data_unavailable(macro_raw: Dict[str, Any]) -> bool:
    """True if any FRED series MacroEconomicDTO.killSwitch's base condition
    or regime classification depends on is absent from macro_raw for this
    cycle. A caller substituting a benign literal in place of a missing key
    must not let that read as a real "risk on" measurement -- see
    dto_models.py::MacroEconomicDTO's data_unavailable param."""
    return any(k not in macro_raw or macro_raw.get(k) is None for k in KILLSWITCH_CRITICAL_MACRO_KEYS)
```

`run_macro_killswitch()` doesn't receive `VIXCLS` (only `macro_raw` +
`sahm_rule_val`), so its own use of this helper only ever evaluates
`T10Y2Y`/`BAMLH0A0HYM2` presence — correct, matches this function's actual
inputs.

### 3. `MacroEngine.calculate_sahm_rule` — backward-compatible fallback signal
Extract the existing body into a private `_calculate_sahm_rule_detailed(self,
fallback_val=0.0) -> tuple[float, bool]` returning `(value, used_fallback)`.
The public `calculate_sahm_rule()` becomes a one-line delegate:
`return self._calculate_sahm_rule_detailed(fallback_val)[0]` — every existing
caller/test of `calculate_sahm_rule()` is untouched (verified against all 6
`TestCalculateSahmRule` tests: none assert a tuple). Both `main.py` and
`pipeline/production_steps.py` switch to calling
`_calculate_sahm_rule_detailed()` directly to get the fallback flag (see
steps 6-7).

### 4. `run_macro_killswitch()` — fail closed + new schema field
- Compute `data_unavailable = macro_killswitch_data_unavailable(macro_raw)`.
- When `True`: force `regime = "RECESSION"` instead of letting the literal
  defaults (0.5/3.5) resolve to "RISK ON". The `sahm_rule_val >= 0.6` /
  high-credit-spread branches still take priority if they'd independently
  produce a worse classification — this only overrides the fallthrough
  "RISK ON" case.
- Add a `data_unavailable: bool` column to the output dict/DataFrame and a
  matching `pa.Field(nullable=False)` on `MacroDataSchema` (`strict=True`, so
  this must be added to the schema, not just the frame). Verified this
  doesn't break `investyo_mcp_server.py`'s `trigger_macro_engine`/
  `trigger_full_pipeline` — both only read `macro_df["market_regime"]` by
  column name.

### 5. `dto_models.py::MacroEconomicDTO` — the core fix, extended to regime
- Add `data_unavailable: bool = False` to `__init__`, stored as
  `self.data_unavailable`.
- `killSwitch` property: as its first line, `if self.data_unavailable:
  return True` — fail closed, before computing `base_kill`.
- `_rules_based_regime` property: as its first line, `if self.data_unavailable:
  return "RECESSION"` — the same conservative override, applied here too so
  `market_regime` (which calls through `_rules_based_regime`) and every
  consumer of `market_regime` inherit the fail-closed posture automatically,
  without needing to be individually patched.
- Document both short-circuits together in one place (CONSTRAINT #4/#6
  rationale). Every existing caller not touched by this PR (api/metrics_api.py's
  `_MacroProxy` stub, engine/advisory.py's `macro_dto is None` fallback,
  ml/forecast_backfill.py, scripts/refresh_validations.py,
  validation/options_selling_backtest.py) keeps the default `False` and is
  byte-identical — confirmed each of these is a neutral-default/backtest/PIT
  reconstruction site, not the live-cycle safety gate, and out of scope.

### 6. `main.py::_build_macro_dto()` — fixes the SAHMREALTIME dead-key bug too
- Both synthetic-fallback branches (no `FRED_API_KEY`, lines 411-420; the
  outer `except Exception`, lines 488-497) already construct a 100%
  fabricated DTO — set `data_unavailable=True` unconditionally there.
- Live-fetch success path (lines 470-479): **stop reading
  `macro_raw.get("SAHMREALTIME", 0.0)` (dead — `fetch_macro_raw()` never
  populates this key).** Call `sahm_val, sahm_used_fallback =
  me._calculate_sahm_rule_detailed()` instead (mirroring
  `production_steps.py`), pass `sahm_rule_indicator=sahm_val`, and compute
  `data_unavailable = macro_killswitch_data_unavailable(macro_raw) or
  sahm_used_fallback`. This is a real behavior change beyond the flag itself
  — `main.py`'s Sahm-driven kill-switch branch goes from structurally dead
  (always fed `0.0`) to actually computed — call this out explicitly in the
  PR description/walkthrough as a second, independent bug fixed in the same
  pass, not silently folded in.

### 7. `pipeline/production_steps.py::OptionsAnalysisStep.run()`
- `sahm_val, sahm_used_fallback = me._calculate_sahm_rule_detailed()`
  (replacing the current `me.calculate_sahm_rule()` call).
- `data_unavailable = macro_killswitch_data_unavailable(ctx.macro_raw) or
  sahm_used_fallback`, passed into `MacroEconomicDTO(...)`.

### 8. `execution/risk_gate.py::stress_scenario_check` — the second live gate
Currently (`risk_gate.py:543-557`): blocks premium-sell orders only on
`context.macro.vix > 30.0`. Add a `context.macro.data_unavailable` check
before/alongside that comparison — when the macro DTO reports its inputs
were incomplete, block premium-selling orders regardless of the (fabricated)
raw VIX reading, with a distinct reason string
(`"macro data unavailable — blocking premium-sell orders (fail closed)"`).
Use `getattr(context.macro, "data_unavailable", False)` for the same
defensive-`getattr` style already used elsewhere in this file (e.g.
`hmm_regime_check`), so a `context.macro` built by test code or an older
in-flight object without the new attribute degrades to `False`, not a crash.

### 9. `processing_engine.py::process_macro_regime()` — same bug class, found in passing
Its `isinstance(macro_dto, dict)` defensive branch (lines 92-98) builds a
`MacroEconomicDTO` from a raw dict without ever setting `vix_value`/
`sahm_rule_indicator` at all (silently keeping class defaults 15.0/0.0).
Confirmed this branch is dead in the live path (never called with a dict
outside tests) but apply the same `data_unavailable` treatment for
consistency, using the same helper against the incoming dict.

### 10. Explicitly out of scope (disclosed, not silently dropped)
- `data_engine.py::fetch_macro_raw()`'s own `_MACRO_HARDCODED_FALLBACK` —
  pre-existing, deliberate, already logged; not touched.
- `investyo_mcp_server.py`'s `trigger_macro_engine` (payload dict around line
  2198-2206) hardcodes `"kill_switch_active": False` unconditionally,
  regardless of any real computation — a separate, adjacent fabrication bug
  in a diagnostic/MCP tool, not the live trading gate. Worth a follow-up, not
  pulled into this PR.
- `engine/advisory.py`'s *soft* score-penalty branch on raw
  `macro_dto.vix`/`sahm_rule_indicator` (distinct from its hard `market_regime`
  gate, which step 5 above does fix transitively) — the hard BUY→HOLD
  override already happens upstream via `strategy_engine.py`'s killSwitch
  check, so the soft branch's residual distortion during an outage is a
  scoring-cosmetics issue, not a trade-blocking one. Noted as a known,
  disclosed residual gap rather than pulled into this PR's blast radius.
- Historical/backtest DTO reconstructions (`scripts/refresh_validations.py`,
  `validation/options_selling_backtest.py`, `ml/forecast_backfill.py`,
  `api/metrics_api.py`) — different concern, left untouched.

## Tests

- **`tests/test_macro_engine.py`**: rewrite
  `TestRunMacroKillswitch::test_missing_macro_keys_use_documented_defaults`
  to assert the new fail-closed behavior (`market_regime == "RECESSION"`,
  `data_unavailable is True`) instead of "RISK ON"; add direct unit tests for
  `macro_killswitch_data_unavailable()` (all-present/all-missing/partial) and
  for `_calculate_sahm_rule_detailed()`'s fallback flag across
  `TestCalculateSahmRule`'s existing scenarios (fallback flag `True` on
  no-data-engine/FRED-exception paths, `False` on direct-SAHMREALTIME/
  UNRATE-computed paths).
- **`tests/test_macro_hmm_integration.py`** (or a new dedicated section):
  `MacroEconomicDTO(..., data_unavailable=True).killSwitch is True` and
  `.market_regime == "RECESSION"` regardless of otherwise-benign vix/sahm/
  yield_curve/credit_spread; confirm `data_unavailable=False` (default)
  reproduces every existing test in this file byte-for-byte.
- **`tests/test_run_once.py`**: extend `_build_macro_dto` coverage —
  (a) mock `fetch_macro_raw()` to return `{}` / a partial dict, assert
  `data_unavailable=True`/`killSwitch is True`; (b) **explicitly test the
  healthy path**: a realistic, fully-populated `fetch_macro_raw()` return
  (`T10Y2Y`/`BAMLH0A0HYM2`/`UNRATE`/`VIXCLS` all present, no `SAHMREALTIME`
  key — matching what `fetch_macro_raw()` actually returns) plus a
  successful `calculate_sahm_rule()` path, asserting `data_unavailable is
  False` — this is the test that would have caught the SAHMREALTIME landmine
  immediately, so it's added as a first-class case, not an afterthought;
  (c) confirm both existing synthetic-fallback branches now set
  `data_unavailable=True`; (d) confirm `sahm_rule_indicator` on the resulting
  DTO now reflects a real `calculate_sahm_rule()` value instead of the old
  dead-key `0.0`.
- **`tests/test_production_steps_options_columns.py`** (or a new file):
  `ctx.macro_raw` missing keys → `ctx.macro_dto.data_unavailable is True`;
  a case where `calculate_sahm_rule`'s own fallback fires while
  `ctx.macro_raw` is otherwise complete, confirming that alone still sets it;
  and a fully-healthy case confirming `data_unavailable is False`.
- **`tests/test_risk_gate.py`**:
  - `TestMacroKillSwitchCheck`: add a case — `MacroEconomicDTO(data_unavailable=True)`
    with otherwise-benign fields blocks a BUY order through the existing,
    unmodified `macro_kill_switch_check` code path.
  - New/extended `TestStressScenarioCheck`-style case: a premium-sell
    `RiskContext` with `context.macro.data_unavailable=True` and
    `vix=15.0` (benign) is blocked; confirm a `context.macro` object lacking
    the attribute entirely (`getattr` default) still passes exactly as today.
- Delegate the mechanical parts (new unit tests for the pure helper function,
  the `_calculate_sahm_rule_detailed` split) to the `test-writer` subagent per
  CLAUDE.md's convention; keep the DTO/risk-gate end-to-end tests in the
  primary pass.

## Documentation updates (per CLAUDE.md's mandatory doc-update step)

1. **`docs/architecture/signal-engines.md`**'s `macro_engine.py` bullet —
   describe `run_macro_killswitch`'s new fail-closed regime override, the new
   `data_unavailable` schema field, and the `MacroEconomicDTO.data_unavailable`
   flag's propagation into both `killSwitch` and `_rules_based_regime`/
   `market_regime`.
2. **`CLAUDE.md`**'s "Macro Regime Gate" bullet — add: `killSwitch` and
   `market_regime` now also fail closed (force `True`/`"RECESSION"`) when the
   FRED snapshot behind the DTO was missing `T10Y2Y`/`BAMLH0A0HYM2`/`VIXCLS`
   or the Sahm value came from `calculate_sahm_rule`'s fallback, distinct
   from the existing operator-disable branch this bullet already documents;
   and that `stress_scenario_check` (the premium-sell VIX gate) now also
   blocks on the same `data_unavailable` signal. Also document, as a
   separate bullet or an addendum note, the independent `main.py`
   `SAHMREALTIME` dead-key fix (main.py's Sahm-driven kill-switch input goes
   from always-`0.0` to a real `calculate_sahm_rule()` value). (Edits to
   `CLAUDE.md` auto-mirror to `AGENTS.md` via
   `.claude/hooks/sync_agent_docs.sh` — no manual duplication needed.)
3. New **`docs/known_issues/macro_killswitch_fail_open_on_missing_fred_data.md`**
   following the existing known-issues format (root cause, what was found —
   including the two issues the design-review pass caught before
   implementation — fix, status), cross-linked from
   `docs/known_issues/README.md`.

## PR workflow

- Copy the plan/tracker/walkthrough into `.claude/` with unique, feature-scoped
  filenames, e.g. `.claude/fix_macro_killswitch_fail_open_implementation_plan.md`,
  `.claude/fix_macro_killswitch_fail_open_task.md`,
  `.claude/fix_macro_killswitch_fail_open_walkthrough.md`.
- Open a PR against `main` (no direct commits — this is orchestration/signal
  logic).

## Verification

- `pytest tests/test_macro_engine.py tests/test_macro_hmm_integration.py tests/test_run_once.py tests/test_risk_gate.py tests/test_production_steps_options_columns.py -q`
  must be green, including the rewritten/added tests above.
- Full `make verify` (or `pytest` + the offline gate) before opening the PR.
- Manually confirm: constructing `MacroEconomicDTO` with today's full set of
  kwargs and no `data_unavailable` arg is unaffected (default `False`,
  `killSwitch`/`market_regime` computed exactly as before) — the
  byte-identical regression guarantee for every untouched call site.
- Manually confirm the healthy-path case end-to-end: a realistic
  `fetch_macro_raw()` return plus a successful `calculate_sahm_rule()` call
  produces `data_unavailable=False` in both `main.py` and
  `pipeline/production_steps.py` — the specific case the design review
  flagged as the one most likely to silently regress.

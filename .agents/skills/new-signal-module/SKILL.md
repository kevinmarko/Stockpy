---
name: new-signal-module
description: >-
  Add a new SignalModule to signals/ (the pluggable quantitative scoring
  system feeding StrategyEngine.evaluate_security()). Use when asked to add,
  wire up, or expose a new trading/scoring signal module -- covers the
  SignalModule ABC contract, when a module needs the two-phase
  pre_compute/compute hook pattern vs. per-ticker compute() alone,
  SIGNAL_WEIGHTS registration, the mandatory docs/signals/<name>.md writeup,
  the mandatory no-lookahead-bias perturbation test, and the pilots/catalog.py
  entry that makes the module copyable in the Pilots PWA.
---

<!--
  Ported from this repo's Claude Code sibling skill (`.claude/skills/new-signal-module/SKILL.md`)
  to Antigravity's skill format. Frontmatter and body content are carried over verbatim --
  Antigravity's own `google-antigravity-sdk` skill and this repo's existing `.agents/skills/supabase`
  skill both use the same minimal `name` + `description` frontmatter shape Claude's SKILL.md already
  used here, so no restructuring was required for this port beyond this note.
-->

# Adding a signal module to `signals/`

This repo has 17 registered `SignalModule` implementations, all following the
same shape. `docs/signals/README.md`'s own "Adding a New Signal Module"
section is the checklist; this skill fills in the *why* and the concrete
file:line references so each step is done right the first time instead of
half-copied from the nearest existing module.

## 1. Pick the base-class contract you actually need

Every module implements `signals.base.SignalModule` (`signals/base.py:106`).
Two methods matter:

- **`compute(self, row: pd.Series, context: SignalContext) -> SignalOutput`**
  (`signals/base.py:200`, `@abstractmethod`) — called once per ticker per
  cycle. `SignalOutput` (`signals/base.py:70`) is `score` (`[-1.0, +1.0]`),
  `confidence` (`[0.0, 1.0]`), `explanation` (a rationale string;
  `WARNING:`/`DETAIL:`-prefixed lines are collected separately by the
  aggregator), and `meta_label_proba` (Stage 4 placeholder — leave at the
  default `1.0` unless you're wiring a real meta-label model).
- **`compute_vectorized(self, df: pd.DataFrame, context: SignalContext) -> pd.DataFrame`**
  (`signals/base.py:168`) — the base implementation just calls `compute()`
  row-by-row via `df.apply`, which is O(n) Python-loop-equivalent. Per
  CLAUDE.md's "Technical/fundamental math is vectorized" convention and the
  "Signal Engine Vectorization" note (Phase 4 — the whole `SignalAggregator`
  path is natively vectorized), **override `compute_vectorized` directly with
  real pandas/numpy ops** rather than relying on the fallback. See
  `signals/rsi_extremes.py:16` (`RSIExtremesSignal.compute_vectorized`) for
  the pattern: boolean-mask the DataFrame, assign scores in bulk, return a
  `DataFrame` with columns `score`/`confidence`/`explanation`/`meta_label_proba`
  indexed identically to the input. Keep a scalar `compute()` too (still
  `@abstractmethod`, and simpler to unit-test row-by-row) — `rsi_extremes.py`
  keeps both, with `compute_vectorized` as the real hot path.

Two optional overrides:

- **`is_active_in_regime(self, macro: MacroEconomicDTO) -> bool`**
  (`signals/base.py:124`, default always `True`). Override to return `False`
  during regimes where your signal's edge is known to invert or degrade —
  `signals/rsi2_mean_reversion.py:53` returns `False` in `RECESSION`/
  `CREDIT EVENT` or when `macro.vix > 30`. When `False`, `SignalAggregator`
  skips both the score contribution *and* the explanation line for that
  module this cycle — `compute()` still runs and its raw output stays in the
  `outputs` dict for introspection, it just doesn't move `final_score`. Do
  **not** try to replicate this by having `compute()` self-zero; the central
  gate is what makes the suppression impossible to forget per-module (see
  CLAUDE.md's "Conventions enforced in this codebase").
- **`required_features: List[str]`** class attribute (default `[]`) — column
  names `SignalRegistry.compute_all`/`compute_all_vectorized`
  (`signals/registry.py:75`, `:98`) will hard-`ValueError` on if missing from
  the row/DataFrame. Only list columns your module genuinely can't degrade
  gracefully without — `signals/rsi2_mean_reversion.py:48` requires
  `["Close", "RSI_2", "SMA_5", "SMA_200"]`; most modules leave this `[]` and
  handle a missing/NaN input via `row.get(...)` + `pd.isna()` instead (see
  `rsi_extremes.py:42`), which is more forgiving of a partial-data ticker.

## 2. Decide: per-ticker only, or two-phase `pre_compute`?

Most modules (RSI, MACD, Graham, dividend quality, ...) are **purely
row-wise** — one ticker's score depends only on that ticker's own current
features. These need nothing beyond `compute()`/`compute_vectorized()`; the
inherited `pre_compute(universe_df, context)` no-op (`signals/base.py:147`)
is correct as-is and `SignalRegistry.run_pre_compute()`
(`signals/registry.py:35`) will call it and return immediately.

**Cross-sectional** modules — where a ticker's score depends on its rank or
relationship *relative to the rest of the current cycle's universe*
(momentum rank, cross-sectional z-scores) — need the two-phase pattern.
Study `signals/cross_sectional_momentum.py` end to end as the reference
implementation:

- **`pre_compute(universe_df, context)`** (`cross_sectional_momentum.py:77`)
  runs **once per cycle**, before the per-ticker loop, via
  `global_registry.run_pre_compute(universe_df, context)`
  (`signals/registry.py:35`). It reads a column already computed
  vectorized-and-lookahead-free upstream by the orchestrator (here,
  `XSec_12_1M` — see `main_orchestrator.py`'s
  `compute_xsec_momentum_ranks()`, which uses `shift(22)`/`shift(252)`, no
  `iterrows`), computes a universe-wide statistic in one vectorized call
  (`Series.rank(pct=True)`), and stores the result keyed by ticker on
  `context.xsec_percentile_ranks` (or, for your module, a new field you add
  to `SignalContext` — see `signals/base.py:24` for the existing per-module
  fields like `multifactor_scores`, `lgbm_scores`, `news_sentiment_scores`;
  follow that pattern, don't overload an existing field for a different
  signal's data).
- **`compute(row, context)`** (`cross_sectional_momentum.py:133`) then does
  cheap per-ticker work only: look up this ticker's pre-computed value from
  the context dict, map it to `[-1, +1]`, return a `SignalOutput`. Never
  recompute the universe-wide statistic here — that's exactly the redundant
  per-ticker cost the two-phase pattern exists to avoid (see
  `signals/base.py:113`'s docstring).
- A missing/never-populated context entry must degrade to a neutral
  `score=0.0, confidence=0.0` with a `WARNING:`-prefixed explanation
  (`cross_sectional_momentum.py:151`), never a `KeyError` or a fabricated
  guess.

If in doubt which camp you're in: does your signal's score for ticker X
change if the *set of other tickers in this cycle's universe* changes, holding
X's own data fixed? If yes, two-phase. If no, plain `compute()`.

## 3. Register the module

Three registration points, all required:

1. **`global_registry.register(<YourClass>())` at module bottom** — see
   `signals/rsi_extremes.py:63` / `cross_sectional_momentum.py:201`. This is
   what makes `import signals.<name>` (next step) actually wire the module
   into the live `SignalRegistry` singleton (`signals/registry.py:107`).
2. **`import signals.<name>` in `signals/__init__.py`** — without this the
   registration call in step 1 never executes; nothing imports `signals/`
   submodules automatically.
3. **A weight entry in `settings.SIGNAL_WEIGHTS`** (`settings.py:1904`,
   a `Field(default_factory=lambda: {...})` dict literal) — add
   `"<name>": <default_weight>` matching your class's `name` attribute
   exactly. Look at the existing entries' comments for two conventions worth
   following: a genuinely alpha-free module documents why its weight is
   pinned to `0.0` (see the `regime_multiplier` comment,
   `settings.py:1920`), and a module still building a track record starts at
   a modest weight like `0.10` (`lgbm_ranker`) rather than a full-size one.
   Cross-check `docs/signals/README.md`'s module index table — your new row
   goes there too (its own step, see §7 below), and the "Score Contribution
   Summary" tier table classifies modules by `|weight|` magnitude
   (Dominant/Strong/Supporting/Tiebreaker/Sizing-only) — place your module in
   the right tier.

If your module writes new output columns to the dashboard DataFrame (not just
an internal score), add `{"header": ..., "key": ..., "format": ...}` entries
to `config.COLUMN_SCHEMA` (`config.py:27`) — this is the single source of
truth Pandera validation and every downstream sink (Sheets, state snapshot,
HTML report) derives from; see `multifactor`'s `Value_Z`/`Quality_Z`/
`LowVol_Z`/`Size_Z`/`Multifactor_Composite` columns for a worked example of a
module whose *outputs*, not just inputs, need writing back
(`main_orchestrator.py` does this immediately after
`global_registry.run_pre_compute()`).

## 4. Write `docs/signals/<name>.md`

Required — not optional polish. Use `docs/signals/rsi_extremes.md` as the
template; every existing file follows this section order:

1. **Header block**: `# Signal: \`<name>\`` followed by a bullet list —
   `**File:**`, `**Default weight:**`, `**Score range:**`, `**Regime gate:**`
   (state the gate condition, or `Always active` if you didn't override
   `is_active_in_regime`), `**Pilot:**` (the `pilots/catalog.py` entry this
   module backs — see §6).
2. **`## Rationale`** — the academic/empirical basis. Cite real papers (this
   repo's convention throughout `docs/signals/`), not vague "momentum is a
   known effect" hand-waving.
3. **`## Signal Logic`** — a table of condition → points/score → interpretation,
   plus the exact normalization formula (raw points / max magnitude).
4. **`## Failure Modes`** — a table: what happens on missing/NaN input, edge
   cases, and known false-signal scenarios. Never omit this section; CONSTRAINT
   #4/#6 (never fabricate, never crash) apply to every module and this section
   is where you demonstrate compliance.
5. **`## Interaction with Other Modules`** — how your signal's contribution
   combines with or gets offset by other active modules in the aggregate
   score. Required if your module can plausibly conflict with an existing one
   (most can).
6. **`## Empirical Notes`** — any parameter-sensitivity findings, known
   regime-specific quirks.
7. **`## Backtest Validation`** — only add this section once your module has
   gone through the `strategy-validation` skill's workflow (a real
   `STRATEGY_REGISTRY` adapter run through `validation/harness.py`). Until
   then, omit the section entirely rather than stubbing it — the module index
   table's `Backtest` column uses `—` for "no honest backtest exists yet"
   (see `docs/signals/README.md`'s legend), which is preferable to a
   half-filled section.

Then add your row to `docs/signals/README.md`'s module index table
(`docs/signals/README.md:37`) — Module / Weight / File / Description / Pilot /
Backtest columns, `—` for Pilot/Backtest until those exist.

## 5. Write the mandatory no-lookahead-bias perturbation test

CLAUDE.md: *"Every indicator and forecaster must be verified to have zero
lookahead bias using the perturbation tests in `tests/`."* The shared harness
is `tests/lookahead_check.py::verify_no_lookahead(func, data, t)` — it runs
`func(data, t)`, then perturbs every column of `data` at indices `> t` to an
extreme sentinel value, re-runs `func(data, t)`, and asserts the two outputs
are identical (within `np.isclose` tolerance). If your module's score at time
`t` changes when you corrupt data strictly *after* `t`, it's leaking future
information.

Follow `tests/test_signals_lookahead.py` as the concrete pattern for
per-row signal modules — it wraps `signal.compute_vectorized(data, None)` in a
local `func(data, t)` closure that returns `out["score"].iloc[t]`, then calls
`verify_no_lookahead(func, df, 50)`:

```python
from signals.<name> import <YourClass>
from tests.lookahead_check import verify_no_lookahead

def test_<name>_lookahead():
    df = pd.DataFrame({...}, index=pd.date_range("2026-01-01", periods=100))
    signal = <YourClass>()

    def func(data, t):
        out = signal.compute_vectorized(data, None)
        return out["score"].iloc[t]

    assert verify_no_lookahead(func, df, 50)
```

This pattern only applies cleanly when `compute_vectorized`/`compute` don't
touch `context` (pass `None` and let a purely row-wise module ignore it, as
`GrahamValueSignal`/`DividendQualitySignal` do). If your module genuinely
needs a real `context` (macro DTO, cross-sectional ranks), a bare
`context=None` will raise `AttributeError` inside a broad except and silently
pass a no-op test — `test_signals_lookahead.py`'s own module docstring
documents exactly this trap for `RegimeMultiplierSignal` and
`CrossSectionalMomentumSignal`. In that case, either (a) build a real
`SignalContext` per `tests/test_xsec_momentum.py`'s `_make_context()` helper
and perturb the underlying DataFrame that feeds `pre_compute`, matching
`test_xsec_momentum.py::test_no_lookahead_12m_skips_recent_month`'s approach
of asserting a return value is unchanged when only future-relative prices
change, or (b) if the module has no row/time dependency at all (like
`regime_multiplier`, which reads only `context.macro.hmm_risk_on_probability`),
state explicitly in a comment why a perturbation test is structurally
inapplicable and cover the real contract with a normal unit test instead —
don't force a perturbation test that can't actually exercise the logic.

Beyond the lookahead test, `docs/signals/README.md`'s step 8 also requires:
score-range assertions, the regime gate (if any), NaN-input handling, and (if
two-phase) `pre_compute` behavior — see `tests/test_xsec_momentum.py` for a
full worked example covering all of these for a two-phase module.

**Numeric drift tolerance**: per CLAUDE.md, any indicator math your module
depends on (if it computes a new technical/fundamental value rather than
consuming an existing column) must stay within `1e-5` of hand-verified/
reference values — assert with `np.isclose(actual, expected, atol=1e-5)`
or tighter, not a loose `pytest.approx()` default.

## 6. Add a `pilots/catalog.py` entry

CLAUDE.md: *"Each module also has an entry in `pilots/catalog.py` (a
Pilot)"* — this is what makes your module a standalone, copyable strategy in
the Pilots PWA. `pilots/catalog.py`'s own header docstring (`pilots/catalog.py:1`)
spells out the constraints: dependency-light (only `settings` + stdlib —
never a heavy engine, since this is imported on the API read path), and
**no invented names** — every key of `Pilot.weights` must be a real
`settings.SIGNAL_WEIGHTS` key, enforced by `tests/test_pilots_catalog.py`.

Add a `Pilot(...)` entry (`pilots/catalog.py:94` for the dataclass fields) to
the `PILOTS` list (`pilots/catalog.py:149`):

```python
Pilot(
    id="<kebab-case-slug>",
    name="<Human-Friendly Name>",
    category="Momentum" | "Mean Reversion" | "Factor" | "Blend",
    description="Retail-friendly 1-2 sentence explainer.",
    weights={"<name>": 1.0},
    long_only=False,  # True only if the strategy never shorts
    validation_strategy_id=None,  # or a real STRATEGY_REGISTRY key once
                                  # the strategy-validation skill's workflow
                                  # has produced an honest backtest for it
),
```

Set `validation_strategy_id=None` honestly until a real
`STRATEGY_REGISTRY` adapter exists (see the `strategy-validation` skill) —
never point it at another strategy's backtest just to show a curve
(CONSTRAINT #4, and `pilots/catalog.py`'s own D1 "no invented names" rule).
Update `docs/signals/README.md`'s Pilot column to cross-link once the entry
exists.

## 7. Add a Gravity audit step

`docs/signals/README.md`'s step 9. Open `Gravity AI Review Suite.py` and find
the highest existing `step_NN_*` method (as of this writing, `step_94` is the
latest) — add `step_<NN+1>_<name>_audit(self)` following the shape of a
recent per-signal audit step (schema hydration, score-range check, regime
gate behavior). This is what gets your new module covered by the platform's
own automated AI-review pass, not just pytest.

## Checklist recap

1. `signals/<name>.py` implementing `SignalModule` (`compute` +
   `compute_vectorized`; `pre_compute` only if cross-sectional;
   `is_active_in_regime` only if regime-fragile).
2. `global_registry.register(<YourClass>())` at module bottom.
3. `import signals.<name>` in `signals/__init__.py`.
4. `"<name>": <weight>` in `settings.SIGNAL_WEIGHTS` (`settings.py:1904`).
5. New output columns (if any) added to `config.COLUMN_SCHEMA`.
6. `docs/signals/<name>.md` written (all sections except Backtest Validation,
   which comes later).
7. `docs/signals/README.md` index table row added.
8. `tests/test_<name>.py`: score range, regime gate, NaN inputs,
   `pre_compute` (if two-phase), and a lookahead perturbation test via
   `tests/lookahead_check.py::verify_no_lookahead`.
9. `pilots/catalog.py` `Pilot(...)` entry, `validation_strategy_id=None`
   until a backtest exists.
10. A new `step_NN_<name>_audit` in `Gravity AI Review Suite.py`.

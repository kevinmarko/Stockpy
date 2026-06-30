# InvestYo Platform — Improvement & Efficiency Plan

**Status:** Active — Phases 0–1 in progress.
**Authored:** 2026-06-29
**Owner:** Claude Code session.

Phased plan to address the structural, efficiency, and hygiene findings from the
2026-06-29 code review. Phases are ordered **risk-ascending and dependency-correct** —
each is independently shippable as its own PR (matching the branch-per-feature workflow
in `CLAUDE.md`), and earlier phases de-risk later ones.

## Guiding principles

1. **Never refactor structure and change behavior in the same PR.** If a later phase
   regresses, `git bisect` lands on a single-concern commit.
2. **Domain ownership** (`CLAUDE.md`): `gui/` and Gravity tooling are **Antigravity's**
   domain; `signals/`, `strategy_engine`, `main.py`, `settings.py` are Claude Code /
   shared. Phases that cross into Antigravity territory are flagged ⚠️.
3. **Baseline-anchored** — Phase 0 captures a green test + Gravity + latency baseline that
   every later phase diffs against.

---

## Phase 0 — Baseline & safety net

| | |
|---|---|
| **Branch** | (no commits — measurement only) |
| **Risk** | None |
| **Effort** | 20 min |

1. Green test baseline: `.venv/bin/pytest -q` → expect **1574 passed**.
2. Gravity baseline JSON: `.venv/bin/python "Gravity AI Review Suite.py"` → save output.
3. Advisory cycle latency: `time .venv/bin/python3 main.py` → record wall-clock (the
   Phase 3a target).

**Exit criterion:** baseline artifacts saved; no code changes.

---

## Phase 1 — Zero-risk hygiene (quick wins)

| | |
|---|---|
| **Branch** | `agent/claude-code/improvement-plan-phase1` |
| **Risk** | Very low — no behavior change |
| **Effort** | ~1.5 h |
| **Domain** | Shared + Claude Code |

- **1.0 (added) Fix non-reproducible `test_delisted_tickers_file`** ✅ — **Real root cause:**
  `data/delisted_tickers.csv` (a hand-curated survivorship-bias fixture: Lehman, Bear
  Stearns, Enron, …) is excluded by the blanket `*.csv` rule in `.gitignore` and was
  **never committed**. It existed only as an untracked local file in the top-level repo,
  so the worktree / CI / any fresh clone is missing it and the test fails (`assert 0 >= 30`).
  (My first hypothesis — a CWD-leak — was wrong; the file was simply absent from git.)
  **Fix:** (a) `!data/delisted_tickers.csv` exception in `.gitignore` + commit the fixture;
  (b) defensively anchor `universe_engine.CACHE_PATH`/`DELISTED_PATH` to `_MODULE_DIR`
  (`os.path.abspath(__file__)`) instead of CWD, matching `data/robinhood_portfolio.py`.
  **Result: 1571 passed, 0 failed — now reproducible on a clean checkout.**
- **1.1 Untrack `quant_platform.db`** — ⏸ **DEFERRED pending operator decision.**
  Discovery: the committed DB has **0 rows** in both `trades` and `Transactions` — it
  ships **empty**. This contradicts the "169 seeded trades" claim repeated in `CLAUDE.md`
  (L124), `docs/HOW_TO_GUIDE.md` (L80/365/420/1317), and `docs/signals/*.md`. Options to
  decide: (a) accept empty + untrack + sweep all "169" docs to "ships empty, rebuild via
  `database_setup.py`"; (b) regenerate the 169 trades (needs the Robinhood order-history
  source); (c) leave tracked, just document the discrepancy. **No data is at risk** — the
  artifact is already empty.
- **1.2 Reword the lone TODO** ✅ — `forecasting_engine.py` `TODO(Stage 4)` → "Future
  direction (Stage 4): …" so it reads as a tracked design decision, not a defect, and no
  longer trips TODO-grep tooling.
- **1.3 Stale `CLAUDE.md` failure claims** — N/A: the "pre-existing failures" note lived
  in session context, not the committed `CLAUDE.md` (grep finds nothing). The real stale
  claim in `CLAUDE.md` is the "169 trades" note (L124), folded into 1.1's decision.

**Exit criterion:** `pytest -q` green (**1571 passed**); cwd-isolation bug fixed; DB
untracking + "169" doc sweep awaiting operator decision.

---

## Phase 2 — `settings.py` cosmetic grouping (approach A)

| | |
|---|---|
| **Branch** | `agent/claude-code/settings-grouping` |
| **Risk** | Low (cosmetic only — flat access preserved) |
| **Effort** | ~1 h |
| **Domain** | Shared (flag in PR) |

**Chosen approach: (A) cosmetic.** ✅ **Done.** The file was already loosely
banner-grouped, so the realized work was: (1) a **24-section index** comment block at the
top of `Settings` documenting declaration order + an explicit "do NOT nest — flat names
are load-bearing" note; (2) **deduped an accidental duplicate** — `RH_USERNAME` /
`RH_PASSWORD` / `RH_MFA_SECRET` were each defined **twice** (the second silently
overriding the first), now a single definition with the merged richer descriptions.
**Flat field names preserved** (zero call-site changes). `model_fields` = 73; clean-env
defaults test + full suite (1571 passed) green.

> Follow-up (deferred): approach (B) true nested Pydantic models with `@property` shims —
> only if nested access is later desired.

---

## Phase 3 — Efficiency wins (behavior-preserving)

Three independent sub-PRs, any order.

### 3a — Async-parallelize the advisory loop

| | |
|---|---|
| **Branch** | `agent/claude-code/advisory-async-loop` |
| **Risk** | Medium — concurrency in the hot path |
| **Effort** | 2–3 h |
| **Domain** | Shared (`main.py`) |

**Done.** Implementation notes / deviations from the original sketch:
1. Independence confirmed: engines are constructed **per-call** inside
   `engine.advisory.evaluate` (not shared singletons); the advisory path is **read-only**
   for trades; shared inputs (snapshot, market, macro_dto, context_extras) are read-only
   during the loop. Bars + macro are fetched **before** the loop, so the only concurrent
   DB writer is fundamentals caching.
2. **Chose `ThreadPoolExecutor` over asyncio.** `run_once` is synchronous and called
   synchronously everywhere (`main()`, `make verify`, tests). A thread pool keeps the
   signature unchanged (zero blast radius) and is the right tool for parallelizing N
   independent **sync** I/O+native-compute calls — asyncio would have forced `run_once`
   async and rippled to every caller for no benefit.
3. **Enabling fix:** added `PRAGMA busy_timeout=5000` to `HistoricalStore._connect` so
   concurrent fundamentals writers WAIT for the WAL write-lock instead of immediately
   raising `SQLITE_BUSY`. Small, broadly-beneficial robustness fix.
4. **Determinism preserved:** results are collected then reassembled in original symbol
   order, so Sheet/HTML/snapshot output and logs are byte-identical to the sequential
   path. New `ADVISORY_MAX_CONCURRENCY` (default 8; `1` = original sequential path).
5. Dead-letter preserved exactly: `_eval_one` never raises; per-symbol failures become
   `RunResult.errors` entries in order.
6. **Verification:** 3 new equivalence tests (`TestAdvisoryConcurrency`) prove
   sequential (workers=1) and parallel (workers=8) produce identical ordered
   recommendations + identical dead-lettering; full suite **1574 passed**. Live latency
   benchmark deferred to the operator (a real `main.py` run can block on a Robinhood MFA
   prompt — unsafe to run unattended).

### 3b — Two-tier Pandera validation

| | |
|---|---|
| **Branch** | `agent/claude-code/pandera-two-tier` |
| **Risk** | Low | **Effort** 1 h | **Domain** Shared |

**Done.** Extracted the inline validation block into a testable
`main_orchestrator._validate_dashboard(final_df, *, strict)` helper:
- **Production (default):** `DashboardSchema.validate(final_df, lazy=True)` — aggregates
  *all* violations across the wide frame into one report (vs. aborting at the first bad
  column), logs them, and **continues** (the report still has value; CONSTRAINT #6).
- **`--strict` (CI / schema-drift gate):** a validation failure is fatal (`sys.exit(1)`).
- Threaded `strict` through `main(strict=...)` → `_main_body(..., strict=...)` and added the
  `--strict` CLI flag.
- 5 new tests (`tests/test_dashboard_validation.py`): empty-frame valid, conformant-frame
  passes strict, invalid-frame non-strict returns False (never raises), invalid-frame
  strict exits 1, and the flag is wired through `main`/`_main_body`/CLI. Full suite 1579.

### 3c — Streamlit cache freshness ⚠️ Antigravity

| | |
|---|---|
| **Branch** | `agent/antigravity/streamlit-cache-mtime` |
| **Risk** | Low | **Effort** 1 h |

Key the `state_snapshot.json` cache on file `mtime` so it invalidates exactly on change
instead of a fixed 300s TTL (currently shows up to 4-cycle-stale data on a 60s refresh).

---

## Phase 4 — Structural splits (when GUI is quiet)

Pure file moves, no logic changes — large diffs, so merge when target files are quiet.

### 4a — Split `gui/panels.py` (3,824 lines) ⚠️ Antigravity

`gui/panels/` package, one file per tab, `__init__.py` re-exports every `render_*` so
`gui/app.py` is untouched. Verify: `pytest -k "panels or gui"` + 11-tab streamlit smoke.

### 4b — Split `Gravity AI Review Suite.py` (10,494 lines)

| **Branch** | `agent/claude-code/gravity-step-modules` |

`gravity/steps/step_NN_*.py` autodiscovered by `gravity/registry.py`; root file becomes a
thin shim preserving the CLI entry point. Verify: output JSON byte-identical to Phase 0
baseline (modulo timestamps).

### 4c — Remove lazy `gui` imports ⚠️ Antigravity

After 4a, promote the 23 lazy `from gui import …` calls to module-top where the cycle has
dissolved; comment where a genuine cycle remains.

---

## Phase 5 — Dependency drift

| **Branch** | `agent/claude-code/deps-low-risk` (+ separate `deps-pandas3-spike`) |

1. **Batch 1 (low-risk):** ✅ **Done.**
   - `yfinance 1.4.1 → 1.5.1` (pin `>=1.5,<1.6`) ✅
   - `pandera 0.31.1 → 0.32.1` (pin loosened `==0.31.1` → `>=0.32,<0.33`) ✅
   - **`numpy` held at 2.2.6 — NOT upgraded.** numpy 2.5 install surfaced
     `numba 0.61.2 requires numpy<2.3`; numba is a **hard transitive dep of
     `pandas-ta` + `vectorbt`** and compiles against the numpy ABI, so 2.5 would
     risk JIT/array failures. Instead the misleading `numpy>=2.0,<2.5` pin was
     **tightened to `<2.3`** (numba's real ceiling) with a comment so a future
     `pip install -U` can't silently break numba. Full suite **1574 passed**.
2. **pandas 3.0 — separate spike, do not merge speculatively.** Major breaking release
   (copy-on-write default). Triage failures, go/no-go. NOTE: pandas 3.0 is itself
   likely gated by the same numba `<2.3` numpy ceiling — confirm during the spike.
3. **FinBERT preload (optional):** warm the `transformers` singleton at orchestrator
   startup when `FINBERT_ENABLED`, so the ~30s cold start is visible-at-launch.

---

## Phase 6 — Aggregator vectorization (stretch / conditional)

| **Branch** | `agent/claude-code/aggregator-vectorize` |
| **Gate** | **Only if the universe grows past ~50 tickers** |
| **Risk** | High — touches core scoring math |

Refactor `signals/aggregator.py` so modules consume the full universe DataFrame once and
return a per-ticker score vector. Heaviest validation: numeric-drift < 1e-5 vs current
output + lookahead-perturbation re-run. Defer unless post-Phase-3 profiling shows the
aggregator is the bottleneck.

---

## Sequencing

```
Phase 0  baseline ──────────────────────────────► (gate for everything)
Phase 1  hygiene ──────► PR
Phase 2  settings ─────► PR
Phase 3a advisory async ─► PR ┐
Phase 3b pandera tier ───► PR ├─ independent, any order
Phase 3c streamlit cache ─► PR ┘  ⚠️ Antigravity
Phase 4a panels split ───► PR ┐  ⚠️ Antigravity
Phase 4b gravity split ──► PR ├─ do when GUI quiet; 4c after 4a
Phase 4c lazy imports ───► PR ┘  ⚠️ Antigravity
Phase 5  deps (batch 1) ─► PR ; pandas3 spike separate
Phase 6  aggregator ─────► PR (conditional — profile first)
```

~2–3 focused days for Phases 1–5; Phase 6 is the only multi-day item and may be skipped.

## Progress log

| Phase | Status | PR | Notes |
|---|---|---|---|
| 0 Baseline | ✅ done | — | 1571 passed, reproducible on clean checkout |
| 1.0 delisted fixture fix | ✅ done | — | committed gitignored fixture + module-anchored paths |
| 1.1 Untrack DB | ⏸ deferred | — | DB ships empty — "169 trades" doc drift; needs decision |
| 1.2 TODO reword | ✅ done | — | forecasting_engine Stage-4 note |
| 1.3 Stale CLAUDE notes | ✅ n/a | — | not in committed file |
| 2 Settings (A) | ✅ done | — | section index + deduped accidental RH_* triple; 73 fields, flat names preserved |
| 3a Advisory parallel | ✅ done | — | ThreadPoolExecutor (sync run_once preserved); +SQLite busy_timeout; 3 equivalence tests; 1574 passed |
| 3b Pandera tier | ✅ done | — | _validate_dashboard helper, lazy=True prod / --strict fatal; 5 tests; 1579 passed |
| 3c Streamlit cache | ⬜ planned | — | ⚠️ Antigravity |
| 4a Panels split | ⬜ planned | — | ⚠️ Antigravity |
| 4b Gravity split | ⬜ planned | — | |
| 4c Lazy imports | ⬜ planned | — | ⚠️ Antigravity |
| 5 Deps (batch 1) | ✅ done | — | yfinance 1.5.1, pandera 0.32.1; numpy held @2.2.6 (numba <2.3 cap); 1574 passed |
| 6 Aggregator | ⬜ conditional | — | profile first |

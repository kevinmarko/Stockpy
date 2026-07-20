# Known issue: CNN-LSTM forecaster deadlocks on TensorFlow eager execution

**Status: root cause found, fix implemented and verified end-to-end (2026-07-20,
Round 3) — fix PR open, not yet merged.** Tracked in
[issue #381](https://github.com/kevinmarko/Stockpy/issues/381). Discovered
2026-07-19/20 while enabling the CNN-LSTM forecaster path in
`forecasting_engine.py` (tracked in
[PR #377](https://github.com/kevinmarko/Stockpy/pull/377), which shipped only the
safe half — the idempotent `setup.sh` and the numpy-safe `requirements-optional.txt`
— and explicitly deferred this deadlock as follow-up work).

**Round 3 result, in one line:** the deadlock is triggered by *import order*, not
merely by which libraries are present. `pandas` (imported directly by
`forecasting_engine.py`, and transitively by `prophet`/`statsmodels`) eagerly
imports `pyarrow` purely to version-gate a feature flag — if that happens before
TensorFlow's own import, the first *real* (non-trivial, multi-threaded) TF eager op
deadlocks; if TensorFlow is imported first, the identical training call completes
cleanly. The fix — reordering `forecasting_engine.py`'s imports so `tensorflow` is
imported before `pandas`/`prophet`/`statsmodels` — is implemented and verified with
real, non-zero, non-hung forecasts produced end-to-end through the actual
`run_cnn_lstm_forecast()` code path (see "Round 3" below and
[PR #387](https://github.com/kevinmarko/Stockpy/pull/387)). CNN-LSTM itself is
still gated behind optional TensorFlow installation
(`requirements-optional.txt`, unchanged scope) — this fix removes the reason it
was kept dormant when TensorFlow *is* installed, but promoting TF to a default
dependency is a separate decision outside this doc's scope.

## Why this matters

`pipeline/production_steps.py` runs the forecasting step across the whole symbol
universe on **worker threads inside one process** (`ThreadPoolExecutor`), not
separate OS processes. If this deadlock reproduces in a real pipeline run, the
first CNN-LSTM fit would silently hang the entire forecasting cycle forever — no
crash, no log line, just stuck. That is a strictly worse failure mode than today's
`TENSORFLOW_AVAILABLE=False` graceful degradation (an honest all-zero result), so
CNN-LSTM must stay dormant until this is fixed.

## Environment

- macOS arm64 (Apple Silicon), macOS 26.5
- Python 3.12.12 via Homebrew's `python@3.12` — **the Framework build**. `.venv/bin/python3`
  is a symlink that resolves to
  `/opt/homebrew/Cellar/python@3.12/3.12.12_1/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python`,
  a GUI-capable `.app`-bundle launcher, not a plain CLI interpreter.
- `tensorflow==2.21.0`, which pulls in standalone Keras 3 (`keras==3.15.x` — `tensorflow.keras`
  is a compatibility shim in TF ≥2.16, not the bundled Keras 2 engine the code was
  originally written against).
- `pyarrow==24.0.0` (`requirements.txt`, first-class dependency, not incidental).

## Reproduction

```bash
./.venv/bin/pip install -r requirements-optional.txt   # installs tensorflow>=2.19
PYTHONPATH=. ./.venv/bin/python3 -c "
from data.historical_store import HistoricalStore
from forecasting_engine import ForecastingEngine

bars = HistoricalStore(readonly=True).get_bars('AAPL', lookback_days=504)
print(f'{len(bars)} bars pulled')
ForecastingEngine().run_cnn_lstm_forecast(bars, horizons=(10, 30, 60, 90), ticker='AAPL')
print('unreachable if the bug reproduces')
"
```

The process prints the bar count, then hangs indefinitely at 0% CPU. `ps -o stat`
shows `S` (sleeping/blocked), not `R` (running) — this is a **deadlock, not slow
training**. A stuck process burning CPU would at least indicate real (if slow) work;
0% CPU means it is blocked waiting on something that never arrives.

## Evidence, graded by rigor

Three attempts were made. Only the first has a confirmed matching native stack
trace — the other two share the *symptom* but were killed on a process-state
heuristic (`ps` showing `0.0% CPU` / `S`), not a verified identical stack frame.
Treating all three as strictly "the same bug" is currently an assumption backed by
one attempt's worth of hard evidence, not three.

### Attempt 1 — default environment (confirmed, full stack trace)

Captured via `sample <pid> 3` (1ms interval, 3-second window). The main thread
(`DispatchQueue_1: com.apple.main-thread`) sat in the **identical frame across all
2,601 samples** — zero variation over the full window:

<details>
<summary>Full native stack trace (main thread, 2601/2601 samples)</summary>

```
start (dyld)
  Py_BytesMain
    pymain_main
      Py_RunMain
        pymain_run_file → pymain_run_file_obj → _PyRun_AnyFileObject
          _PyRun_SimpleFileObject → pyrun_file → run_mod → run_eval_code_obj
            PyEval_EvalCode
              _PyEval_EvalFrameDefault → _PyObject_MakeTpCall → slot_tp_call
                _PyObject_Call_Prepend → _PyObject_FastCallDictTstate
                  _PyEval_EvalFrameDefault → method_vectorcall
                    [... nested Python call frames repeat several times ...]
                      _PyObject_MakeTpCall → cfunction_call
                        pybind11::cpp_function::dispatcher(...)                     (in _pywrap_tfe.so)
                          pybind11::cpp_function::initialize<...>::__invoke(...)     (in _pywrap_tfe.so)
                            tensorflow::TFE_Py_ExecuteCancelable_wrapper(...)        (in _pywrap_tfe.so)
                              TFE_Py_ExecuteCancelable(...)                          (in lib_pywrap_tensorflow_common.dylib)
                                TFE_Execute(...)                                     (in libtensorflow_cc.2.dylib)
                                  tensorflow::CustomDeviceOpHandler::Execute(...)
                                    tensorflow::EagerOperation::Execute(...)
                                      tensorflow::DoEagerExecute(...)
                                        tensorflow::EagerLocalExecute(...)
                                          tensorflow::EagerExecutor::SyncExecute(...)
                                            tensorflow::ExecuteNode::Run()
                                              tensorflow::EagerKernelExecute(...)
                                                tensorflow::KernelAndDeviceFunc::Run(...)
                                                  tensorflow::ProcessFunctionLibraryRuntime::RunSync(...)  (in libtensorflow_framework.2.dylib)
                                                    absl::Notification::WaitForNotification() const        (in libtensorflow_framework.2.dylib)
                                                      absl::Mutex::LockSlowWithDeadline(...)
                                                        absl::Mutex::Block(...)
                                                          AbslInternalPerThreadSemWait_lts_20250814          (in libarrow.2400.dylib)  ← see below
                                                            absl::synchronization_internal::PthreadWaiter::Wait(...)
                                                              _pthread_cond_wait
                                                                __psynch_cvwait                              (libsystem_kernel.dylib)
```

</details>

Simultaneously, every worker thread sampled was idle: `start_wqthread → _pthread_wqthread
→ __workq_kernreturn` (libdispatch/GCD workqueue signature, not Eigen's own
`pthread_cond_wait` spin — see the correction below). The aggregate "sort by top of
stack" counts across all threads (83,232 `__psynch_cvwait` + 2,601 `__workq_kernreturn`,
both well above the 2,601-per-thread baseline of a single example thread) imply on
the order of **30+ idle worker threads**, none ever dispatched. A full pool sitting
permanently idle while the main thread waits on exactly one of them is the real
anomaly — some idle threads is normal TF behavior; *all* of them staying idle forever
is not.

`lsof` on the stuck process confirmed every TF/h5py/numba/llvmlite/arch shared
library was already mapped — this is **not** an import-time hang, it happens during
actual eager op execution (the first kernel dispatched inside `run_cnn_lstm_forecast`,
consistent with either model construction or the very first training step).

`quant_platform.db` (confirmed `PRAGMA journal_mode=WAL`) was separately open
read-write by an unrelated, actively-running production daemon
(`desktop.orchestrator_daemon --interval 300`) at the time. Ruled out as the cause:
WAL-mode readers don't block on writers, and the verification script's own DB read
(a readonly `HistoricalStore` connection) had already completed — the bar-count print
line succeeded — before the hang began.

### Attempts 2 and 3 — env-var fixes, symptom-only evidence

Two standard fixes were tried, neither resolved it, but neither has a confirmed
matching stack trace (both were killed on the same `ps` heuristic, not a re-sampled
trace):

- **Attempt 2**: `TF_NUM_INTEROP_THREADS=1 TF_NUM_INTRAOP_THREADS=1 OMP_NUM_THREADS=1`
  (the standard fix for thread-pool oversubscription / GCD contention). Same hang
  signature.
- **Attempt 3**: `TF_USE_LEGACY_KERAS=1` + the `tf-keras==2.21.0` package (forcing
  the pre-Keras-3 `tf.keras` compatibility engine instead of standalone Keras 3 —
  TF's own documented escape hatch for Keras-3-migration regressions). Reproduced
  twice (once before an unrelated machine reboot destroyed the process/logs, once
  after on a fresh identical script). The post-reboot run's log reached the same
  "bars pulled" print with a different row count (504 vs. 346) and one extra benign
  DB-fallback warning — that is **not** evidence of reaching further into different
  code; both attempts hang at the identical logical point (entry into
  `run_cnn_lstm_forecast`), the differing numbers just reflect incremental-cache
  state at the time, unrelated to the deadlock.

## What's actually confirmed vs. still a hypothesis

**Confirmed, directly, by binary inspection** — not assumed: TensorFlow and PyArrow
each ship an independently-compiled copy of the *identical-versioned* Abseil
synchronization primitive:

```
$ nm .venv/lib/python3.12/site-packages/tensorflow/libtensorflow_framework.2.dylib | grep SemWait
0000000001677108 T _AbslInternalPerThreadSemWait_lts_20250814

$ nm .venv/lib/python3.12/site-packages/pyarrow/libarrow.2400.dylib | grep SemWait
0000000001291a3c T _AbslInternalPerThreadSemWait_lts_20250814
```

Both `T` (defined, not just referenced) in their respective library. This is a
genuine ODR (One Definition Rule) violation: two separately-compiled copies of the
same synchronization primitive exist in one process, and the stack trace shows
TensorFlow's own `Mutex::Block` call resolving into **Arrow's** copy rather than
its own — dyld crossed the wires. `pyarrow>=15.0.0` is a first-class
`requirements.txt` dependency (installed version 24.0.0 matches `libarrow.2400.dylib`'s
"2400" exactly), so this is present in every real run of this codebase, not an
artifact of the verification script.

**Not yet confirmed — two live theories:**

1. **Framework-build Python / GCD main-thread integration.** Homebrew's `python@3.12`
   ships as a `.app`-bundle Framework build whose main thread is a GCD
   `com.apple.main-thread` serial dispatch queue — a known general class of macOS
   issue for GUI-capable Python builds running heavy native multithreaded C++
   extensions. One refinement worth noting: the idle worker threads' stack signature
   (`__workq_kernreturn`, a libdispatch/GCD-managed workqueue thread) is *not* what
   TF's own Eigen threadpool normally shows (a raw `pthread_cond_wait` inside Eigen's
   own spin loop) — this points more specifically at a GCD-integrated component such
   as Apple's Accelerate/vecLib BLAS backend (which TF's macOS build commonly
   delegates linear algebra to, and which itself uses GCD internally) rather than TF's
   threading model in general.
2. **Abseil ODR violation via the confirmed symbol collision above.** If construction
   of the `Notification`/`Mutex` happens via one compiled copy's code path and the
   wait happens via the *other* copy (exactly the kind of crossover the stack trace
   shows), the two could disagree about internal state such that a signal is set but
   never observed by the waiter. This is a well-documented bug class across the
   TF + pyarrow + grpc ecosystem when multiple wheels vendor different builds of the
   same Abseil version without hiding their symbols.

The evidence doesn't cleanly discriminate between these — both would produce an
identical outward hang. See Attempts 4-6 below, which executed exactly this
discriminator and produced a surprising result: **neither theory reproduces the
hang in isolation.**

### Attempts 4-6 — the discriminator experiments (executed 2026-07-20)

The binary-search experiment proposed above was run, plus one further narrowing
step. All three completed cleanly — **no hang in any of them**:

| # | Script | Result |
|---|---|---|
| 4 | `import tensorflow` only, one trivial eager op (`tf.constant(1) + tf.constant(1)`) | ✅ Completed instantly. |
| 5 | `import pyarrow` then `tensorflow`, same trivial op | ✅ Completed instantly. |
| 6 | The **real** `Sequential([Conv1D, MaxPooling1D, LSTM, Dense])` architecture, real `.compile()` + `.fit()` call (50 epochs, `validation_split=0.2`, `EarlyStopping`) on synthetic random data shaped to match production (`X: (160, 60, 10)`, `Y: (160, 4)`) — **no** `pyarrow`, `numba`, `arch`, or `HistoricalStore` in the process at all | ✅ `model.fit()` returned, `model.predict()` produced a real result. |

This is a meaningfully different outcome than the two theories predicted. Both
theory 1 (bare Framework-Python/GCD vs. any TF eager op) and theory 2 (mere
co-loading of PyArrow) are now **disconfirmed in their simplest form** — a trivial
op runs fine in both cases, and even the *real* model architecture with the *real*
`.fit()` training call runs fine in total isolation from PyArrow, numba, and arch.

**What this actually establishes:** the deadlock requires something specific to
the *full* combination present in `forecasting_engine.py`'s real import graph and
execution context — not TensorFlow/Keras training in isolation, and not merely
PyArrow being co-loaded. The confirmed Attempt-1 trace still stands (the hang is
genuinely inside TF's eager execution, not in numba/pandas-ta feature engineering
beforehand), so the trigger is some interaction between TF's eager execution and
one or more of the other libraries loaded before it in the real code path — numba/
llvmlite (via `pandas_ta`), `arch` (GARCH), h5py, SQLAlchemy, or some combination,
possibly still involving the confirmed Abseil/PyArrow ODR collision but only when
triggered alongside something else. The Abseil symbol-collision finding (directly
confirmed via `nm`, see above) is not disconfirmed by this — Attempt 5 shows mere
*presence* of PyArrow isn't sufficient on its own, which narrows the collision's
role without ruling it out as a contributing factor once other pieces are present.

## Round 3 (2026-07-20): the recommended next step, executed — root cause found, fix verified

### Attempt 7 — the recipe as literally written (300 rows): a false negative caused by a separate, real bug

Running the exact recipe above (`PYTHONPATH=. .venv/bin/python3 -u` script, real
`forecasting_engine.ForecastingEngine`, 300 synthetic rows, `horizons=(10, 30, 60,
90)`) **did not hang** — but it also never reached `model.fit()`. It returned
`{10: 0.0, 30: 0.0, 60: 0.0, 90: 0.0}` in well under a second, which looks like a
"clean run" but is actually the `zero_result` sentinel from a silent early return.

Root cause of the false negative: `run_cnn_lstm_forecast`'s insufficient-history
gate (`forecasting_engine.py` ~line 609,
`if len(df_features) < n_reserve + lookback + 10: return zero_result`, where
`n_reserve = lookback + max_h = 60 + 90 = 150`) requires only 220 rows to pass, but
`make_direct_multistep_windows` (~line 472) needs the **train** slice
(`len(df_features) - n_reserve`) to itself be `>= n_reserve` to build even one
supervised window — i.e. the true minimum is `2 * n_reserve = 300` rows, not 220.
With exactly 300 raw bars, `build_lstm_features` drops ~21 warm-up rows, leaving
279 — enough to pass the gate (220) but not enough to build a single window (300),
so `make_direct_multistep_windows` returns empty arrays and
`if len(X_seq) == 0: return zero_result` fires silently with **no exception, no log
line** (this is a distinct code path from the caught-and-logged `except Exception`
at the bottom of the function). **This is a real, separate, previously-unknown
latent bug** in the production gate — worth its own fix, but out of scope for this
deadlock investigation since it never affects the ~504-day (`BARS_BACKFILL_DAYS`)
real-data path this codebase actually uses; flagged as a follow-up.

### Attempt 7c — corrected recipe (600 rows): reproduces the deadlock on pure synthetic data

Re-running the same real `ForecastingEngine` with 600 synthetic rows (579 after
feature dropna, comfortably above the true 300-row minimum) actually reached
`model.fit()` — and **hung**, sustained 0% CPU / `S` state for 2m38s before being
killed. A fresh `sample <pid> 3` capture showed the **identical** stack signature to
Attempt 1 (2577/2577 samples pinned to one frame):

```
TFE_Execute (libtensorflow_cc.2.dylib)
  → tensorflow::EagerKernelExecute → ... → ProcessFunctionLibraryRuntime::RunSync
    → absl::Notification::WaitForNotification() (libtensorflow_framework.2.dylib)
      → absl::Mutex::Block → AbslInternalPerThreadSemWait_lts_20250814 (libarrow.2400.dylib)
        → PthreadWaiter::Wait → _pthread_cond_wait → __psynch_cvwait
```

34 total threads sampled: 1 main thread stuck as above, 32 idle in
`Eigen::ThreadPoolTempl::WaitForWork`, 2 idle in GCD `__workq_kernreturn` — the same
"full idle pool, main thread deadlocked waiting on one of them" shape as Attempt 1.

**This directly answers the question the recommended next step was designed to
answer: the deadlock reproduces on pure synthetic data through
`forecasting_engine.py`'s real import graph and execution context. It is NOT
something about the real AAPL data path, `HistoricalStore`, or the real database.**

### Attempts 8–13 — narrowing beyond Attempts 4-6: the real trigger is import ORDER, not import PRESENCE

Attempts 4-6 (previous round) concluded that neither "TF alone" nor "TF + PyArrow"
reproduces the hang with a *trivial* op, and that even the real Conv1D/LSTM
architecture with a real `.fit()` call runs clean *in isolation from PyArrow*. Round
3 re-examined that isolation and found it was never actually achieved:

| # | Script | Result |
|---|---|---|
| 8 | `statsmodels` (ARIMA + ExponentialSmoothing, actually `.fit()`) + `sklearn.MinMaxScaler` + TF, real Conv1D/LSTM/Dense `.fit()` | **Hung** (fresh matching stack trace, `libarrow.2400.dylib` symbol present). |
| 9 | Instrumented import trace: `sys.modules` checked after each import in Attempt 8's sequence | `pyarrow` appears in `sys.modules` immediately after `from statsmodels.tsa.arima.model import ARIMA` — **before sklearn or tensorflow are even imported**. |
| — | `python3 -c "import pandas as pd"` alone, nothing else | `pyarrow` already in `sys.modules` afterward. Confirmed: **pandas 2.3.3's `pandas.compat.pyarrow` unconditionally does `import pyarrow as pa` at pandas-import time**, purely to version-gate a feature flag — regardless of whether any Arrow-backed dtype is ever used anywhere in this codebase (it never is). |
| 10 | `pandas` alone (no statsmodels/sklearn) + TF, real `.fit()` | **Hung** (sustained 0% CPU, 1m50s+). |
| 11 | Explicit `import pyarrow` (PR #386's own Attempt 5 import) + TF, but a **real** `.fit()` instead of Attempt 5's trivial op | **Hung**, reproduced twice (2/2). This means Attempt 5's "no hang" result was a property of the *trivial op*, not of PyArrow's absence — Attempt 5 never actually tested a real training call. |
| 12 | Clean baseline: `numpy` + `tensorflow` only (**no** explicit pandas/pyarrow import at all) + real `.fit()` | **No hang**, 3/3 repeat runs, ~0.8-0.9s each, real predictions returned. |
| — | `lsof` on the Attempt-12 process | **Surprise**: `libarrow.2400.dylib` (and the rest of PyArrow's native libraries) were already mapped into the process, and `'pyarrow' in sys.modules` was `True` — **merely `import tensorflow` transitively imports and fully initializes the real `pyarrow` package** in this dependency set, with no pandas or pyarrow import anywhere in the user script. |
| 13 | Import-**order** swap: `import tensorflow` FIRST, `import pandas` SECOND (pandas finds pyarrow already in `sys.modules` from TF's own transitive import and just rebinds the name — no re-initialization), then real `.fit()` | **No hang**, 3/3 repeat runs, real predictions each time. |

**What this establishes, correcting the previous round's interpretation:** PyArrow's
native libraries are *always* loaded once TensorFlow is imported in this dependency
set (TF 2.21.0 + pyarrow 24.0.0, macOS arm64) — Attempts 4 and 5 never had a true
"PyArrow-absent" baseline, they only ever tested a trivial op, which apparently
never engages the code path where the collision matters. The real, load-bearing
discriminator is **which library's Python-level module initialization happens
first**: when `pandas` (or an explicit `import pyarrow`) runs its own full
Python-level init *before* TensorFlow's, the two libraries' independently-compiled
copies of `AbslInternalPerThreadSemWait_lts_20250814` end up in a state where a real
multi-threaded TF eager op (Conv1D/LSTM `.fit()`, not a trivial constant-add) can
signal a `Notification` that the waiting thread never observes. When TensorFlow's
own import runs first — whether or not `pandas`/`pyarrow` are imported afterward —
this does not happen, 3/3 clean runs with no counterexample found. `prophet`
(confirmed installed, v1.3.0, itself pandas-based) was also confirmed to trigger the
same transitive pyarrow-before-TF ordering in the *original* file, since its `try`
block sits before the `tensorflow` `try` block — so the fix has to move TF's import
above **both** `pandas` and `prophet`, not just the explicit `import pandas as pd`
line.

This is consistent with — and sharpens rather than contradicts — the confirmed
Abseil ODR collision (`nm` evidence, unchanged from the original writeup): the
collision is real and always latent once both libraries are loaded, but it only
*manifests* as an observable deadlock when (a) a substantial multi-threaded TF eager
op actually exercises the racing code path, and (b) PyArrow's own Python-level
initialization — not just its `.dylib` being mapped — ran before TensorFlow's.

**Caveat on determinism:** this is empirically a very strong, repeatable pattern (5/5
hangs across every "pyarrow-initialized-before-TF + real training" trial; 6/6 clean
runs across every "TF-initialized-first" trial, whether or not pandas followed) but
the underlying bug class (a race on a duplicated synchronization primitive) is not
guaranteed deterministic in principle. No counterexample was observed in this round,
but treat "import TF first" as a very strong empirical mitigation rather than a
mathematically proven guarantee.

### The fix

`forecasting_engine.py`'s import block was reordered so the `tensorflow`/`keras`
`try/except` runs first — before `numpy`, `pandas`, `statsmodels`, `scikit-learn`,
and `prophet` — with `TENSORFLOW_AVAILABLE` computed exactly as before and no
behavioral change to any other forecaster. See
[PR #387](https://github.com/kevinmarko/Stockpy/pull/387) for the exact diff.

### End-to-end verification

With the reordered import applied to the real `forecasting_engine.py` (not a copy —
verified against the actual file on the fix branch), the exact Attempt-7c synthetic
recipe (600 rows, `horizons=(10, 30, 60, 90)`, driven through `ForecastingEngine()
.run_cnn_lstm_forecast(...)`) now returns real, varied, non-zero predictions in
~1-2 seconds, e.g.:

```
RESULT={10: 177.996..., 30: 163.417..., 60: 168.583..., 90: 159.735...}  (took 1.5s)
```

Reproduced 4/4 across the isolated microbenchmark (Attempt 13) and the real-module
test (Attempts 14/15), with zero hangs. The full existing test suite for
forecasting was also re-run against the fix (`tests/test_forecasting_engine.py`,
`tests/test_forecasting_lookahead.py`, `tests/test_forecasting_improvements.py`,
`tests/test_forecast_model_persistence.py`, `tests/test_forecast_parallel.py`,
`tests/test_forecasting_engine_config_loader.py`, everything matching `-k forecast`
repo-wide, plus `test_bug_fixes.py`/`test_engine_context.py`/
`test_sector_forecast_backtest.py`/`test_forecast_tracker.py`/
`test_forecast_skill_uplift.py`/`test_quantitative_models.py`/`test_metrics_api.py`/
`test_advisory.py`) — **all passing, zero failures**, confirming the reorder has no
observable effect on any other forecaster or on the mocked-TensorFlow test suite
(those tests inject a mock into `sys.modules['tensorflow']` before importing
`forecasting_engine`, so internal import order within the module is irrelevant to
them).

### Follow-up flagged, not fixed here

The insufficient-history gate bug found in Attempt 7 (`forecasting_engine.py` ~line
609 undercounts the rows required for `make_direct_multistep_windows` to produce
any training windows when `max(horizons)` is large relative to available history —
true minimum is `2 * (lookback + max_h)`, the gate only checks
`lookback + max_h + lookback + 10`) is real but does not affect the production
504-day backfill path; it's flagged as a separate, smaller follow-up rather than
bundled into this fix.

## What was ruled out

- SQLite/WAL lock contention with the concurrently-running orchestrator daemon (WAL
  readers don't block on writers; the DB read had already completed before the hang).
- An import-time hang (all native libraries were confirmed already loaded via `lsof`
  before the hang began).
- Thread-pool oversubscription in the ordinary sense (`TF_NUM_INTEROP/INTRAOP_THREADS=1`,
  `OMP_NUM_THREADS=1` — no change).
- The new-vs-legacy Keras engine as the sole cause (`TF_USE_LEGACY_KERAS=1` + `tf-keras`
  — no change; though note this doesn't fully rule out Keras 3 as *a* contributing
  factor, only that reverting it alone isn't sufficient).
- TensorFlow eager execution in isolation as the sole cause (Attempt 4 — no hang).
- Mere co-loading of PyArrow as the sole trigger (Attempt 5 — no hang; the confirmed
  Abseil ODR collision may still be a contributing factor once other pieces are
  present, but presence alone isn't sufficient).
- The real Conv1D/LSTM model architecture and `.fit()` training call as the sole
  cause, independent of the rest of `forecasting_engine.py`'s import graph
  (Attempt 6 — no hang on synthetic data with the exact real architecture; **note,
  Round 3**: this was later understood to be because Attempt 6, like 4 and 5, never
  achieved a true PyArrow-absent baseline — TensorFlow itself transitively loads
  PyArrow's native libraries in this dependency set — see Attempt 12).
- The real AAPL data path / `HistoricalStore` / the live database as a required
  ingredient (Round 3, Attempt 7c — the deadlock reproduces on pure synthetic data
  through the real `forecasting_engine.py` import graph, with a stack trace matching
  Attempt 1 exactly).
- `numba`/`pandas_ta_classic`, `arch`/GARCH, and SQLAlchemy/`HistoricalStore` as
  *necessary* ingredients (Round 3, Attempts 8-10 — none of these were imported
  anywhere in the reproducing scripts; `statsmodels` + `sklearn` + `pandas`, or even
  `pandas` alone, combined with a real TF `.fit()` call, is sufficient).
- Mere *presence* of PyArrow's native libraries being mapped into the process as
  sufficient on its own (Round 3, Attempt 12 — TensorFlow itself transitively loads
  them and it still doesn't hang; the load-bearing factor is *which library's
  Python-level module init ran first*, not merely whether PyArrow's `.dylib` is
  mapped — see Attempts 9-13).

## Related

- [Issue #381](https://github.com/kevinmarko/Stockpy/issues/381) — the tracking
  ticket for this deadlock; this doc is the full technical record it links to.
- [PR #377](https://github.com/kevinmarko/Stockpy/pull/377) — shipped the safe tooling
  half (idempotent `setup.sh`, `requirements-optional.txt`) and deferred this deadlock.
- [PR #380](https://github.com/kevinmarko/Stockpy/pull/380) — the original writeup
  (Attempt 1, the confirmed native stack trace, the `nm`-verified Abseil ODR
  collision).
- [PR #386](https://github.com/kevinmarko/Stockpy/pull/386) — Attempts 4-6, the
  discriminator experiments that (in hindsight, per Round 3) never achieved a true
  PyArrow-absent baseline.
- [PR #387](https://github.com/kevinmarko/Stockpy/pull/387) — the Round 3 import-order fix to
  `forecasting_engine.py`, verified end-to-end with real, non-hung, non-zero
  forecasts.
- `forecasting_engine.py`'s `TENSORFLOW_AVAILABLE` guard (now the very first
  executable import block in the file, before `pandas`/`prophet`/`statsmodels`) and
  `run_cnn_lstm_forecast` (~line 500) — the code path this blocks/blocked.
- `forecasting/forecast_tracker.py` — the skill tracker CNN-LSTM would feed once
  this is resolved; currently has zero `cnn_lstm` rows in the live database.

# Known issue: CNN-LSTM forecaster deadlocks on TensorFlow eager execution

**Status: open, blocking.** Tracked in
[issue #381](https://github.com/kevinmarko/Stockpy/issues/381). Discovered
2026-07-19/20 while enabling the CNN-LSTM forecaster path in
`forecasting_engine.py` (tracked in
[PR #377](https://github.com/kevinmarko/Stockpy/pull/377), which shipped only the
safe half — the idempotent `setup.sh` and the numpy-safe `requirements-optional.txt`
— and explicitly deferred this deadlock as follow-up work). Do not consider
CNN-LSTM "enabled" until this is resolved and a real, non-hung forecast is produced
end-to-end.

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
identical outward hang. See the recommended next step below.

## Recommended next diagnostic step

Re-running attempts 2/3 with a fresh trace would only confirm the env-var fixes
didn't change the *symptom* — it can't separate the two theories, since both predict
the same observable hang. A cheaper, more decisive experiment:

1. Run a **minimal script that imports only `tensorflow`** (no `pyarrow`, no
   `pandas`, no `HistoricalStore`) and executes one trivial eager op:
   ```python
   import tensorflow as tf
   print(tf.constant(1) + tf.constant(1))
   ```
   under the same Framework Python.
   - **Hangs anyway** → isolates the cause to Python/TF/macOS threading alone,
     independent of Arrow. Points at theory 1 (Accelerate/GCD).
   - **Does not hang** → re-add just `import pyarrow` before the same trivial op.
     If *that* combination hangs, it strongly implicates the Abseil symbol collision
     (theory 2) as the actual trigger, not merely a coincidental hazard sitting on
     the call path.

This binary-search approach resolves the ambiguity in one bounded test rather than
repeated full-cost reproductions of the real forecaster.

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

## Related

- [Issue #381](https://github.com/kevinmarko/Stockpy/issues/381) — the tracking
  ticket for this deadlock; this doc is the full technical record it links to.
- [PR #377](https://github.com/kevinmarko/Stockpy/pull/377) — shipped the safe tooling
  half (idempotent `setup.sh`, `requirements-optional.txt`) and deferred this deadlock.
- `forecasting_engine.py`'s `TENSORFLOW_AVAILABLE` guard (~line 40) and
  `run_cnn_lstm_forecast` (~line 500) — the code path this blocks.
- `forecasting/forecast_tracker.py` — the skill tracker CNN-LSTM would feed once
  this is resolved; currently has zero `cnn_lstm` rows in the live database.

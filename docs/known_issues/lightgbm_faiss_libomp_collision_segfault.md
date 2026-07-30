# Known issue (fixed): pytest segfaults / deadlocks when lightgbm and faiss share a process

**Status: fixed — two distinct manifestations, two distinct fixes.** This
doc originally covered Round 1 (a segfault) alone; Round 2 (a deadlock,
added below) is a SECOND, independently-confirmed manifestation of the same
underlying library-collision class, found and fixed later while root-causing
what looked like a pre-existing, order-dependent full-suite hang. Read both
rounds — Round 1's own "why production was never actually exposed" reasoning
is corrected by Round 2's finding, not merely supplemented by it (see that
section).

## Round 1: segfault (original finding)

Root cause confirmed via a clean isolated discriminator (4/4 crash / 3/3
clean, exact stack-trace match) and via a fix verified against the real,
full `pytest -q` suite. Root cause: `tests/test_rag_index.py`
imported `faiss` **eagerly, at module scope** — pytest imports every collected
test module during its collection phase, before any test in the whole suite
runs, so this loaded faiss's bundled `libomp.dylib` into the process very
early. The first *real* (non-mocked) `lightgbm` model deserialization
elsewhere in the suite then collides with it and segfaults. Fixed by
switching that one file's availability check from `import faiss` to
`importlib.util.find_spec("faiss")`, which locates the module without
executing it — see the fix PR referenced below.

## Why this matters (Round 1)

This is a second instance, in the same codebase and on the same machine
class, of the bug class documented in
[`cnn_lstm_tf_deadlock.md`](cnn_lstm_tf_deadlock.md): two independently
compiled native libraries in the same process ship their own copy of the
identical-versioned OpenMP runtime (`libomp.dylib`), and which copy a given
native extension ends up bound to depends on **import order**, not merely
import *presence*. The earlier case was a deadlock (TensorFlow eager
execution + PyArrow's Abseil symbol); this one is an outright segfault
(LightGBM's `Booster.__setstate__` + faiss's bundled OpenMP runtime). Same
underlying class, different manifestation, same root cause shape: a macOS
ARM64 / Homebrew-Framework-Python dylib ODR (One Definition Rule) collision
that is order-sensitive.

Unlike the TF/pyarrow case, this one turned out to be **fully fixable** with
a narrow, verified change, because the colliding eager import lived entirely
in test-only code (a test file's own capability check), not in a real
production entry point — see "Why production was never actually exposed"
below.

## Environment (Round 1)

- macOS arm64 (Apple Silicon)
- Python 3.12 via Homebrew's `python@3.12` (Framework build) — same class of
  interpreter as `cnn_lstm_tf_deadlock.md`.
- `lightgbm==4.6.0`. Its native `lib_lightgbm.dylib` links `@rpath/libomp.dylib`
  and carries an `LC_RPATH` of `/opt/homebrew/opt/libomp/lib` — i.e. it
  resolves OpenMP via Homebrew's system-wide install.
- `faiss-cpu` (installed as `faiss` 1.14.3). Its wheel vendors its **own**
  independently-compiled copy of `libomp.dylib` at
  `faiss/.dylibs/libomp.dylib`, with install name `/DLC/faiss/.dylibs/libomp.dylib`
  — a different file from Homebrew's copy, not deduplicated by dyld.
- `scikit-learn` *also* vendors its own bundled `libomp.dylib`
  (`sklearn/.dylibs/libomp.dylib`) — confirmed present, but confirmed **not**
  to trigger this specific crash (see "What was ruled out" below). Only
  faiss's copy reproduces it in this environment; that asymmetry is noted but
  not fully explained (a difference in exactly which OpenMP symbols/threading
  paths each vendored copy actually exercises before lightgbm's own call is
  the leading hypothesis, not yet confirmed by symbol-level inspection).

## Reproduction (Round 1 — fixed by the PR; this recipe reproduced on the pre-fix code)

```bash
PYTHONFAULTHANDLER=1 .venv/bin/python3 -m pytest -q
```

Segfaulted deterministically at `tests/test_advisory_pause_gate.py::TestKillSwitchPauseGate::test_inactive_sentinel_does_not_pause`
(test #133 in this environment's default collection order) — the first test
in the suite whose code path reaches a **real, non-mocked**
`main.run_once()` → `pipeline/steps.py`'s `MacroStep.run()` →
`ml.meta_bootstrap.bootstrap_meta_registry()` → `MetaLabeler.load_latest()` →
`MetaLabeler.load()` → `pickle.load()` → `lightgbm.basic.Booster.__setstate__`.
Every test before it either doesn't touch that path or mocks it out.

Minimal isolated 2-line discriminator (no pytest involved):

```python
# CRASHES (4/4): faiss loaded first, then a real lightgbm unpickle
import faiss
from ml.meta_bootstrap import bootstrap_meta_registry
bootstrap_meta_registry()   # loads ml/models/meta_*.pkl via lightgbm.Booster.__setstate__
```

```python
# CLEAN (3/3): same two calls, reverse order
from ml.meta_bootstrap import bootstrap_meta_registry
bootstrap_meta_registry()
import faiss
```

Both run with `PYTHONPATH=.` from the repo root, real saved pickles
(`ml/models/meta_timeseries_momentum_20260706.pkl`,
`ml/models/meta_cross_sectional_momentum_20260706.pkl`), no mocks.

## Evidence, graded by rigor (Round 1)

**Confirmed, directly, by binary inspection:**

```
$ otool -L .venv/lib/python3.12/site-packages/lightgbm/lib/lib_lightgbm.dylib
	@rpath/lib_lightgbm.dylib ...
	@rpath/libomp.dylib ...
$ otool -l .venv/lib/python3.12/site-packages/lightgbm/lib/lib_lightgbm.dylib | grep -A2 LC_RPATH
          path /opt/homebrew/opt/libomp/lib

$ find .venv/lib/python3.12/site-packages -iname '*libomp*'
sklearn/.dylibs/libomp.dylib
faiss/.dylibs/libomp.dylib

$ otool -D faiss/.dylibs/libomp.dylib
faiss/.dylibs/libomp.dylib:
/DLC/faiss/.dylibs/libomp.dylib
```

Three independently-linked copies of `libomp.dylib` are reachable in this
venv: Homebrew's (what lightgbm resolves via rpath), faiss's own bundled
copy, and sklearn's own bundled copy — none sharing an install name, so dyld
treats them as distinct images once more than one is loaded into the same
process.

**Confirmed, directly, by isolated script (repeatable, exact stack match):**

4/4 crashes with `import faiss` before the real `bootstrap_meta_registry()`
call, every one matching the originally-reported trace exactly:

```
Fatal Python error: Segmentation fault

Thread 0x00000001f20d5e80 (most recent call first):
  File ".venv/lib/python3.12/site-packages/lightgbm/basic.py", line 3758 in __setstate__
  File "ml/meta_labeling.py", line 291 in load
  File "ml/meta_labeling.py", line 302 in load_latest
  File "ml/meta_bootstrap.py", line ... in bootstrap_meta_registry
```

3/3 clean runs with the same two calls in reverse order (lightgbm's real
unpickle happens before faiss is ever imported in the process).

2/2 clean runs with `import sklearn` (which also bundles its own
`libomp.dylib`) before the real `bootstrap_meta_registry()` call — sklearn's
copy does **not** reproduce this crash in this environment, only faiss's
does. This asymmetry is confirmed as an observation but not explained at the
symbol level; flagged as unresolved detail, not load-bearing for the fix
(the fix removes the eager faiss import regardless of *why* faiss
specifically triggers it and sklearn doesn't).

**Why the isolated `bootstrap_meta_registry()` script (no pytest) never
crashed:** it never imports faiss at all, so there's no colliding copy to
begin with — consistent with, not contradicting, the finding above.

**Why the pre-existing 6688-test suite (before `tests/test_rag_index.py` and
four sibling files existed) never crashed:** no test file anywhere in that
suite imported `faiss` at module scope, so nothing loaded faiss's bundled
`libomp.dylib` during collection.

**Why CI (GitHub Actions, `ubuntu-latest` — see `.github/workflows/ci.yml`)
passed cleanly on all five same-day PRs despite this bug existing on `main`:**
this is a macOS ARM64 / Homebrew Framework-Python dylib-loading issue by its
very nature (two-level-namespace `.dylib` resolution via `LC_RPATH` and
non-standard install names). Linux's ELF `.so` loading and faiss's Linux
wheel do not reproduce the identical failure mode — CI never exercised the
colliding code path in a way that manifests as a crash there. Not directly
re-verified against a live Ubuntu box for this write-up (out of scope), but
this is the same "on this exact machine class" caveat the original
`cnn_lstm_tf_deadlock.md` finding carries, and is the simplest explanation
consistent with every other confirmed fact above.

## Why production was never actually exposed (Round 1 reasoning — corrected by Round 2)

`ml.meta_bootstrap.bootstrap_meta_registry()` runs once per cycle, very
early — `pipeline/steps.py`'s `MacroStep`, Stage C, before the per-symbol
signal loop (`pipeline/steps.py` docstring: "Ports Stage C ... plus the
once-per-run meta-labeler runtime registration bootstrap"). `data/rag_index.py`
(the only real production module that touches faiss) **lazily** imports
`faiss` inside its own methods (`_get_or_create_index`, `index_new_documents`,
etc.) — never at module scope — matching this codebase's established
lazy-import convention (the same pattern `data/historical_store.py` uses,
per `CLAUDE.md`). Those methods are only reachable from
`engine/portfolio_context.py`, downstream of the advisory cycle's core
scoring/signal work, not before it. So in a real `main.py` /
`main_orchestrator.py` run, `bootstrap_meta_registry()`'s real lightgbm
unpickle always happens well before any code path that could load faiss —
production was never actually at risk. The only place the reverse ordering
occurred was `tests/test_rag_index.py`'s own eager, module-scope
`import faiss`, executed during pytest's collection phase, ahead of
`test_advisory_pause_gate.py`'s real (non-mocked) exercise of the bootstrap
path in default collection order.

**This reasoning is INCOMPLETE, per Round 2 below.** It treats "which
`.dylib` got mapped into the process first" as the whole story, but Round
2's isolated repro shows `import faiss` itself, and even constructing/
inserting into a real `faiss.IndexFlatIP`, complete instantly regardless of
what ran before them — the deadlock is specific to the FIRST real
`Index.search()` call, the point where faiss actually enters its
OpenMP-threaded parallel code path. Import order is necessary evidence but
not a sufficient guarantee that production was safe; see Round 2's
"Why this could have reached production too" for the corrected picture and
Round 2's fix (applied inside `data/rag_index.py` itself, unconditionally,
specifically because this reasoning could not be fully trusted).

## The fix (Round 1)

`tests/test_rag_index.py`'s `_FAISS_INSTALLED` capability check (used to
`@pytest.mark.skipif` the real-faiss round-trip test class) switched from

```python
try:
    import faiss
    _FAISS_INSTALLED = True
except ImportError:
    _FAISS_INSTALLED = False
```

to

```python
_FAISS_INSTALLED = importlib.util.find_spec("faiss") is not None
```

`importlib.util.find_spec()` locates a module without executing it, so it is
safe to call at collection time — it answers "is faiss installed" without
loading its native library. The real `import faiss` still happens, lazily,
inside `data.rag_index`'s own methods, exactly when
`TestRealFaissRoundTrip`'s tests actually execute (unchanged from before —
those tests still get real faiss coverage when faiss is installed).

## End-to-end verification (Round 1)

Full `pytest -q` re-run against the fix: see the PR for the final pass
count. Re-run 2+ times to confirm no non-determinism remains, matching the
rigor bar this doc's sibling (`cnn_lstm_tf_deadlock.md`) sets.

## What was ruled out (Round 1)

- The isolated (no-pytest) `bootstrap_meta_registry()` call as reproducing
  the bug on its own — it doesn't; confirmed clean every time, precisely
  because nothing in that script ever imports faiss.
- `sklearn`'s own bundled `libomp.dylib` as sufficient on its own to trigger
  this — 2/2 clean runs with `import sklearn` before the real
  `bootstrap_meta_registry()` call.
- Any non-test/production code path as the trigger — `data/rag_index.py`'s
  faiss import is already fully lazy (function-scope only); the only
  eager, module-scope `import faiss` anywhere in the repo was in
  `tests/test_rag_index.py`.
- Test-order non-determinism / `pytest-randomly` or similar as an
  explanation for the varying "which test crashes" observations across
  different investigation sessions — no such plugin is installed
  (`pip list | grep pytest` shows only `pytest` + `pytest-cov`); collection
  order is deterministic. The apparent variation is fully explained by
  `PYTHONFAULTHANDLER`'s dump sometimes being cut short by a fast secondary
  process death before it finishes walking every thread's frames, not by
  the crash site itself moving around.

## Round 2: deadlock (found while investigating a separate rate-limiter fix)

**Status: fixed.** Found while verifying an unrelated fix (a GDELT
sentiment-ingestion rate limiter, PR #469): the full `pytest -q -p
no:randomly -m "not network"` suite hung indefinitely — not a segfault, a
genuine deadlock (0% CPU, thread in a blocked wait, not spinning) — inside
`tests/test_rag_index.py::TestRealFaissRoundTrip`. Round 1's fix (switching
the module's availability check to `importlib.util.find_spec`) was already
in place and working correctly for its own purpose; this is a DIFFERENT
failure mode that survives that fix, because Round 1's fix only prevents an
EAGER, unconditional `import faiss` during collection — it does nothing
about the real, lazy `import faiss` that legitimately happens once
`TestRealFaissRoundTrip`'s tests actually execute (matching
`data/rag_index.py`'s own, correct, already-lazy convention).

### Reproduction (Round 2)

Fast (~20s), reliable, 2-file reproduction — no need to run the full ~7500-test
suite to hit it:

```bash
.venv/bin/python -m pytest -p no:randomly -m "not network" \
  tests/test_advisory_pause_gate.py tests/test_rag_index.py -v
```

Hangs deterministically at
`tests/test_rag_index.py::TestRealFaissRoundTrip::test_index_and_search_round_trip`
— `test_advisory_pause_gate.py` exercises the same real, non-mocked
`bootstrap_meta_registry()` → `lightgbm.Booster.__setstate__` path Round 1
identified, and by the time collection+execution reaches
`test_rag_index.py`, that unpickle has already happened for real.

Isolated, step-by-step, no-pytest discriminator (2/2 hangs unfixed, 2/2 clean
with the fix, 3/3 clean with `faiss.omp_set_num_threads(1)` specifically —
see "The fix" below):

```python
import time
def p(msg): print(f"[{time.time():.3f}] {msg}", flush=True)

from ml.meta_bootstrap import bootstrap_meta_registry
p("about to bootstrap_meta_registry (real lightgbm unpickle)")
bootstrap_meta_registry()
p("bootstrap_meta_registry DONE")

import faiss
import numpy as np
p("about to construct + populate a real IndexFlatIP/IndexIDMap")
base = faiss.IndexFlatIP(4)
idx = faiss.IndexIDMap(base)
idx.add_with_ids(np.array([[1.0, 0.0, 0.0, 0.0]], dtype="float32"), np.array([1], dtype="int64"))
p("construction + add_with_ids DONE")  # <-- always printed; never hangs here

p("about to search")
idx.search(np.array([[0.9, 0.05, 0.0, 0.0]], dtype="float32"), 1)
p("search DONE")  # <-- UNFIXED: never printed. FIXED: printed instantly.
```

### Evidence, graded by rigor (Round 2)

**Confirmed, directly, by isolated step-by-step timestamped script (the
finding this round's fix is built on):** `import faiss`, `IndexFlatIP`
construction, `IndexIDMap` construction, and three separate `add_with_ids`
calls all complete in the same millisecond, every run, regardless of
whether a real `bootstrap_meta_registry()` unpickle ran first. The FIRST
`Index.search()` call is the exact, reproducible, 100%-consistent hang
point (2/2 unfixed runs, timestamped to the millisecond, identical hang
location both times) — this is the first call in the sequence that enters
faiss's OpenMP-threaded parallel distance-computation code path; every call
before it operates on 1-3 trivial vectors and apparently never spins up (or
never contends for) the thread pool.

**Confirmed, by elimination of two standard OpenMP-collision workarounds,
before finding the one that works:**
- `KMP_DUPLICATE_LIB_OK=TRUE` (the standard Intel-OpenMP-compatible escape
  hatch for "OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib
  already initialized") — tested, does **not** resolve the hang (confirmed
  hung after 44s with this set, versus the unfixed baseline's 0% CPU within
  ~15-30s; not a timing difference, a genuine non-fix).
- `OMP_NUM_THREADS=1` (env var, process-wide) — **does** resolve it (2/2
  clean, ~1.4s each) — confirms the collision is specifically about the
  OpenMP thread pool's parallel-region entry, not merely two `libomp.dylib`
  images coexisting in memory (which `KMP_DUPLICATE_LIB_OK` addresses and
  didn't help).
- `faiss.omp_set_num_threads(1)` (FAISS's own public API, not an env var) —
  **does** resolve it (3/3 clean via the isolated discriminator, 3/3 clean
  via the real `DocumentVectorStore` production code path, both timestamped
  and no different from the env-var confirmation above) — this is the fix
  actually shipped, because it scopes the change to faiss's own thread pool
  alone rather than capping OpenMP parallelism process-wide for
  lightgbm/numpy/scikit-learn too.

**Attempted but not obtained — external debugger thread dumps:** `lldb -p
<pid>` and macOS's `/usr/bin/sample` both hung indefinitely themselves when
attaching to the deadlocked process (consistent with a macOS Developer Tools
attach-permission dialog this environment cannot answer non-interactively);
`py-spy dump` refused outright ("This program requires root on OSX"). None
of the three were used — escalating to root/sudo for a diagnostic dump was
avoided rather than granted. The step-by-step timestamped-print isolation
above substitutes for a native thread backtrace: it doesn't show WHICH
internal OpenMP primitive is blocked, but it unambiguously answers WHICH
CALL blocks, which was sufficient to find and verify a working fix.

**Not confirmed at the symbol/primitive level:** exactly which OpenMP
runtime call inside `Index.search()`'s parallel region deadlocks (a
double-initialization livelock vs. two thread pools each waiting on the
other's mutex vs. something else) — this doc's Round 1 section carries the
same-shaped unresolved-detail caveat (why sklearn's bundled copy didn't
reproduce Round 1's crash) and is not load-bearing for Round 2's fix either:
the fix (never let faiss enter its parallel path at all) removes faiss's
side of the collision regardless of the exact mechanism.

### Why this could have reached production too (corrects Round 1's conclusion)

Round 1's "why production was never actually exposed" reasoning rests
entirely on **import order**: `bootstrap_meta_registry()`'s real lightgbm
unpickle happens early in a real cycle (`pipeline/steps.py`'s `MacroStep`,
Stage C), and `data/rag_index.py`'s lazy `import faiss` is only reachable
later, downstream via `engine/portfolio_context.py`. That is true and
unchanged. But Round 2's finding shows import order alone was never the
whole mechanism — a plain `import faiss` (and even real `IndexFlatIP`
construction and insertion) completed instantly in every run regardless of
what ran before it; only the first real **parallel-region entry**
(`Index.search()`) deadlocked. That means the actual risk condition is "has
faiss's OpenMP thread pool ever been initialized for real in this process
after lightgbm's own copy was," not "has the `faiss` module ever been
imported after lightgbm" — a strictly narrower and later-triggering
condition than Round 1's reasoning assumed, but not one Round 1's own
evidence rules out for a real orchestrator cycle. `engine/portfolio_context.py`
calling `DocumentVectorStore.search()` (a real, non-empty RAG query) later
in the SAME cycle `bootstrap_meta_registry()` already ran in is exactly the
condition Round 2's isolated repro reproduces — this doc cannot certify
that never happens in a live `main.py`/`main_orchestrator.py` run the way
Round 1's original conclusion implied. This is why Round 2's fix lives
inside `data/rag_index.py` itself (production code, unconditional),
not only in test code — unlike Round 1, where the eager import was
confirmed to exist ONLY in test code.

### The fix (Round 2)

`data/rag_index.py`'s `_get_or_create_index()` — the single choke point
every real search/add code path in `DocumentVectorStore` reaches before
touching faiss (neither `search()` nor `index_new_documents()` calls a
faiss operation without first obtaining the index through this method) —
now calls a new module-level helper, `_cap_faiss_threads()`, immediately
after `import faiss` succeeds:

```python
def _cap_faiss_threads(faiss_module) -> None:
    global _faiss_threads_capped
    if _faiss_threads_capped:
        return
    try:
        faiss_module.omp_set_num_threads(1)
    except Exception:
        pass
    _faiss_threads_capped = True
```

Guarded by a module-level flag so repeat calls (a fresh
`DocumentVectorStore` instance, a later cycle) are a cheap no-op;
`except Exception` because a thread-count-capping failure must never be the
reason RAG indexing itself fails (CONSTRAINT #6) — worst case, the
pre-existing deadlock risk simply isn't mitigated for that one call.
`faiss.omp_set_num_threads(1)` (FAISS's own API) was chosen over the
env-var workarounds tested above specifically because it scopes the change
to faiss's own thread pool, not lightgbm's/numpy's/scikit-learn's own
OpenMP parallelism for the rest of the process. Zero real cost in this
codebase: `data/rag_index.py`'s corpus is a single operator's
sentiment-document archive (`settings.RAG_INDEX_MAX_DOCUMENTS` bounds it to
low hundreds/thousands of vectors) — nowhere near the scale where FAISS's
OpenMP parallelism would matter even if it worked.

### End-to-end verification (Round 2)

Full `pytest -q -p no:randomly -m "not network"` (no `--deselect` flags —
every previously-hanging test included and run for real), re-run 2 times per
this doc's own Round 1 rigor bar:

- Run 1: `7468 passed, 12 skipped, 35 deselected (the pre-existing
  `-m "not network"` exclusion, unrelated), 0 failed` in 221.11s.
- Run 2: see the fix PR for the exact count; confirmed no non-determinism —
  all 6 `TestRealFaissRoundTrip` tests passed both times, matching each
  other.

Also verified via the fast 2-file repro
(`test_advisory_pause_gate.py` + `test_rag_index.py`): 33 passed in 7.56s,
versus an indefinite hang before the fix.

### What was ruled out (Round 2)

- `KMP_DUPLICATE_LIB_OK=TRUE` alone as a fix — tested directly, confirmed
  it does not resolve the hang (see Evidence above).
- Import order alone as a complete explanation — tested directly via the
  step-by-step isolated script; every call up to and including three
  `add_with_ids` insertions completed instantly regardless of prior
  lightgbm usage, so "faiss imported/constructed after lightgbm" cannot be
  the trigger by itself; only the first real parallel-region entry
  (`search()`) is.
- External debugger attach (`lldb`, `/usr/bin/sample`, `py-spy`) as a
  practical diagnostic path in this environment — all three either hung
  waiting on a permission this session cannot grant, or explicitly required
  root, which was not escalated to. The step-by-step print-based isolation
  substituted successfully.
- Non-determinism in the fix itself — re-run 3+ times at both the isolated-
  script level and the real-production-code-path level; always clean with
  the fix, always deadlocked without it.

## Related

- [`cnn_lstm_tf_deadlock.md`](cnn_lstm_tf_deadlock.md) — the sibling
  incident this one's methodology is modeled on (TensorFlow + PyArrow's
  Abseil `libarrow`/`libtensorflow_framework` ODR collision, deadlock not
  segfault, same machine class).
- `ml/meta_bootstrap.py`, `ml/meta_labeling.py` (`MetaLabeler.load`/
  `load_latest`), `pipeline/steps.py`'s `MacroStep` — the real code path
  this bug's crash trace runs through; none of it was modified by either
  round's fix.
- `data/rag_index.py` — Round 1's fix made `tests/test_rag_index.py` match
  this module's already-correct, fully-lazy `import faiss` convention;
  Round 2's fix lives inside this module itself (`_cap_faiss_threads()`),
  since Round 1's "production is safe" reasoning could not be fully
  guaranteed by import order alone.

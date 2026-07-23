# Known issue (fixed): pytest segfaults on lightgbm's first real model unpickle when faiss loaded earlier in the process

**Status: fixed.** Root cause confirmed via a clean isolated discriminator
(4/4 crash / 3/3 clean, exact stack-trace match) and via a fix verified
against the real, full `pytest -q` suite. Root cause: `tests/test_rag_index.py`
imported `faiss` **eagerly, at module scope** — pytest imports every collected
test module during its collection phase, before any test in the whole suite
runs, so this loaded faiss's bundled `libomp.dylib` into the process very
early. The first *real* (non-mocked) `lightgbm` model deserialization
elsewhere in the suite then collides with it and segfaults. Fixed by
switching that one file's availability check from `import faiss` to
`importlib.util.find_spec("faiss")`, which locates the module without
executing it — see the fix PR referenced below.

## Why this matters

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

## Environment

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

## Reproduction (fixed by the PR — this recipe reproduced on the pre-fix code)

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

## Evidence, graded by rigor

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

## Why production was never actually exposed

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

## The fix

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

## End-to-end verification

Full `pytest -q` re-run against the fix: see the PR for the final pass
count. Re-run 2+ times to confirm no non-determinism remains, matching the
rigor bar this doc's sibling (`cnn_lstm_tf_deadlock.md`) sets.

## What was ruled out

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

## Related

- [`cnn_lstm_tf_deadlock.md`](cnn_lstm_tf_deadlock.md) — the sibling
  incident this one's methodology is modeled on (TensorFlow + PyArrow's
  Abseil `libarrow`/`libtensorflow_framework` ODR collision, deadlock not
  segfault, same machine class).
- `ml/meta_bootstrap.py`, `ml/meta_labeling.py` (`MetaLabeler.load`/
  `load_latest`), `pipeline/steps.py`'s `MacroStep` — the real code path
  this bug's crash trace runs through; none of it was modified by the fix.
- `data/rag_index.py` — the production module whose already-correct,
  fully-lazy `import faiss` convention `tests/test_rag_index.py`'s fix now
  matches.

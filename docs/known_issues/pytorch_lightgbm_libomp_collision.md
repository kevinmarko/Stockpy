# Known issue (fixed): PyTorch and LightGBM OpenMP (libomp) collision on macOS

**Status: fixed (2026-10, Rounds 1 & 2).** Eager `lightgbm` import pre-emption and `torch.set_num_threads(1)` intra-op thread capping in `signals/news_catalyst.py`, `data/sector_embeddings.py`, `agents/rag_orchestrator.py`, and `legacy/forecasting/bert_lla.py` prevent SIGSEGV and deadlock across both import/execution orders. Regression tests in `tests/test_news_catalyst.py::TestFinbertOpenMPCoexistence` (and `legacy/tests/test_bert_lla.py::TestOpenMPCoexistence`) use `importlib.util.find_spec("torch")` and a `sys.modules["transformers"]` stub so the test harness itself never imports `torch` ahead of `_get_finbert_pipeline()`'s `lightgbm` pre-emption.

---

## Symptom

When PyTorch (`torch>=2.0`) is installed alongside LightGBM (`lightgbm==4.6.0`) on macOS Apple Silicon (ARM64), running workloads or tests that invoke both frameworks in the same process encountered two distinct failure modes depending on execution order:

1. **SIGSEGV / OMP Error #179 / `__kmp_suspend_initialize_thread` crash (Torch imported first, LightGBM executes with `num_threads > 1`):**
   ```text
   OMP: Error #179: Function pthread_mutex_init failed:
   OMP: System error #22: Invalid argument
   zsh: segmentation fault (core dumped) / EXIT_CODE=139 (or 134 abort)
   ```
   In macOS CrashReporter (`EXC_BAD_ACCESS (SIGSEGV)` at `0x0000000000000580`), LightGBM's OpenMP worker thread (`Thread 4`) spawns in Homebrew's `libomp.dylib` (`__kmp_launch_worker` → `__kmp_fork_barrier` → `kmp_flag_64<false, true>::wait`) and resolves `__kmp_suspend_64` → `__kmp_suspend_initialize_thread + 32` into PyTorch's bundled `torch/lib/libomp.dylib`, dereferencing an uninitialized thread structure at `0x580`.

2. **Deadlock / Permanent Hang (LightGBM executes first, PyTorch multi-threading executes):**
   When `lightgbm.train()` or `booster.predict()` executed first, initializing the OpenMP thread pool, subsequent PyTorch operations with multiple threads (e.g. FinBERT inference, `torch.softmax`, or `nn.LSTM` forward pass) hung indefinitely waiting on worker threads in Apple's thread pool.

---

## Root Cause

This is a third instance of the native macOS ARM64 / Homebrew Python dylib ODR (One Definition Rule) collision bug class documented in:
- [`lightgbm_faiss_libomp_collision_segfault.md`](lightgbm_faiss_libomp_collision_segfault.md) (LightGBM + Faiss `libomp.dylib`)
- [`cnn_lstm_tf_deadlock.md`](cnn_lstm_tf_deadlock.md) (TensorFlow + PyArrow Abseil symbol collision)

### The Dylib Split
- **LightGBM:** `lib_lightgbm.dylib` links `@rpath/libomp.dylib` with `LC_RPATH` pointing to `/opt/homebrew/opt/libomp/lib/libomp.dylib` (system/Homebrew OpenMP runtime).
- **PyTorch:** `torch` bundles its own independently compiled OpenMP library at `torch/lib/libomp.dylib` with install name `@rpath/libomp.dylib`.

When dyld resolves symbols, the two runtimes are not deduplicated. When both frameworks attempt to manage POSIX mutexes and thread pools in the same process:
- If PyTorch's `libomp` initializes first, LightGBM's subsequent `pthread_mutex_init` / `__kmp_suspend_initialize_thread` calls in worker threads fail (`EINVAL` Error #22 / `SIGSEGV` at `0x580`).
- If LightGBM's `libomp` initializes first, PyTorch's multi-threaded OpenMP scheduler deadlocks when spawning or synchronizing worker threads.

---

## Fix (Round 1 — production entry points)

The fix addresses both directions at all PyTorch / HuggingFace entry points (`signals/news_catalyst.py::_get_finbert_pipeline()`, `data/sector_embeddings.py`, `agents/rag_orchestrator.py`, and `legacy/forecasting/bert_lla.py`):

1. **Import Pre-emption:**
   ```python
   try:
       import lightgbm  # noqa: F401
   except ImportError:
       pass
   ```
   Ensures LightGBM establishes its Homebrew OpenMP binding before PyTorch loads its bundled dylib.

2. **Single-Thread Intra-Op Parallelism Cap:**
   ```python
   try:
       import torch
       torch.set_num_threads(1)
   except Exception:
       pass
   ```
   Capping PyTorch's intra-op threads to `1` instructs PyTorch to bypass worker thread synchronization in OpenMP for tensor and transformer operations. For batched CPU headline scoring and lightweight sequence models, single-threaded execution also avoids thread scheduling overhead.

---

## Round 2: `TestFinbertOpenMPCoexistence` self-inflicted `torch`-first import under `xdist` / `-k`

### Symptom
Running `pytest tests/test_news_catalyst.py -k TestFinbertOpenMPCoexistence` in isolation (or under `pytest -n auto --dist loadgroup` when `xdist` scheduled `TestFinbertOpenMPCoexistence` on a worker that had not yet imported `lightgbm`) still crashed with exit code 134/139 (`Thread 4 Crashed: libomp.dylib __kmp_suspend_initialize_thread + 32`).

### Root Cause
1. `TestFinbertOpenMPCoexistence` called `pytest.importorskip("torch")` on the line **before** `import lightgbm as lgb` and before `nc._get_finbert_pipeline()`, loading `torch/lib/libomp.dylib` ahead of Homebrew's `libomp.dylib` and defeating `_get_finbert_pipeline()`'s `import lightgbm` pre-emption.
2. `with patch("transformers.pipeline", return_value=MagicMock()):` resolved `transformers.pipeline` on `__enter__` **before** calling `nc._get_finbert_pipeline()`. Because `transformers` uses `_LazyModule`, resolving `.pipeline` both imported `transformers.pipelines` → `torch` ahead of `_get_finbert_pipeline()` and swapped `sys.modules["transformers"]`, leaving `from transformers import pipeline` inside `_get_finbert_pipeline()` bound to the real unmocked HuggingFace loader.
3. Single-file `pytest tests/test_news_catalyst.py` runs had masked this only because `TestPreCompute::test_pre_compute_empty_universe` (and `get_symbol_news_catalyst_details` on empty headline lists) previously called the unmocked `_get_finbert_pipeline()` earlier in the file.

### Fix (Round 2)
- `TestFinbertOpenMPCoexistence` now checks `importlib.util.find_spec("torch")` (mirroring `tests/test_rag_index.py`'s `find_spec("faiss")` pattern) and injects a lightweight `types.ModuleType("transformers")` stub into `sys.modules` via `monkeypatch.setitem` before calling `nc._get_finbert_pipeline()`, importing `lightgbm` and `torch` only after `_get_finbert_pipeline()` has run its own `lightgbm` → `torch` initialization sequence.
- `get_symbol_news_catalyst_details()` skips calling `_get_finbert_pipeline()` when `headline_texts` is empty, and `test_pre_compute_empty_universe` / `TestGetSymbolNewsCatalystDetails` mock `_get_finbert_pipeline` so unit tests never load the 440MB FinBERT weights.
- `data/sector_embeddings.py` checks `importlib.util.find_spec("sentence_transformers")` before importing `lightgbm` and `torch` at module top, and `agents/rag_orchestrator.py` applies the same `lightgbm` pre-emption + `torch.set_num_threads(1)` guard before importing `sentence_transformers`.
- Synthetic `types.ModuleType("tensorflow*")` stubs in `tests/test_cnn_lstm_worker.py`, `tests/test_forecasting_lookahead.py`, `tests/test_lstm_attention_worker.py`, and `tests/test_cnn_lstm_isolation_dispatch.py` populate `__spec__ = importlib.machinery.ModuleSpec(name, None)` so `importlib.util.find_spec("tensorflow")` never raises `ValueError: tensorflow.__spec__ is None`.

---

## Verification

- **Isolated Reproduction:** `pytest tests/test_news_catalyst.py -k TestFinbertOpenMPCoexistence` (which previously crashed 100% deterministically with exit 134 / `__kmp_suspend_initialize_thread + 32`) passes cleanly in <0.5s.
- **Combined Test Run:** `pytest tests/test_news_catalyst.py tests/test_train_lgbm.py tests/test_cnn_lstm_worker.py tests/test_sector_embeddings.py tests/test_rag_orchestrator.py` and full `make ci` pass with zero crashes.


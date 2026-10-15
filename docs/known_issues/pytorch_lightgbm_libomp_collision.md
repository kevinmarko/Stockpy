# Known issue (fixed): PyTorch and LightGBM OpenMP (libomp) collision on macOS

**Status: fixed (2026-10).** Eager `lightgbm` import pre-emption and `torch.set_num_threads(1)` intra-op thread capping in `signals/news_catalyst.py`, `data/sector_embeddings.py`, and `legacy/forecasting/bert_lla.py` prevent SIGSEGV and deadlock across both import/execution orders. Regression tests added in `tests/test_news_catalyst.py::TestFinbertOpenMPCoexistence` (and `legacy/tests/test_bert_lla.py::TestOpenMPCoexistence`).

---

## Symptom

When PyTorch (`torch>=2.0`) is installed alongside LightGBM (`lightgbm==4.6.0`) on macOS Apple Silicon (ARM64), running workloads or tests that invoke both frameworks in the same process encountered two distinct failure modes depending on execution order:

1. **SIGSEGV / OMP Error #179 (Torch imported first, LightGBM executes):**
   ```text
   OMP: Error #179: Function pthread_mutex_init failed:
   OMP: System error #22: Invalid argument
   zsh: segmentation fault (core dumped) / EXIT_CODE=139
   ```
   This crashed tests like `tests/test_train_lgbm.py` or `tests/test_orchestrator_e2e.py` when LightGBM ranker or model evaluation executed after PyTorch was imported (e.g. via `signals/news_catalyst.py::_get_finbert_pipeline()`).

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
- If PyTorch's `libomp` initializes first, LightGBM's subsequent `pthread_mutex_init` calls fail with `EINVAL` (Error #22), causing an immediate SIGSEGV.
- If LightGBM's `libomp` initializes first, PyTorch's multi-threaded OpenMP scheduler deadlocks when spawning or synchronizing worker threads.

---

## Fix

The fix addresses both directions at all PyTorch / HuggingFace entry points (`signals/news_catalyst.py::_get_finbert_pipeline()`, `data/sector_embeddings.py`, and `legacy/forecasting/bert_lla.py`):

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

3. **Regression Tests:**
   `tests/test_news_catalyst.py::TestFinbertOpenMPCoexistence` asserts that `_get_finbert_pipeline()` caps `torch.get_num_threads() == 1` and verifies that LightGBM training/prediction and PyTorch forward passes execute sequentially in the same process without SIGSEGV or hangs.

---

## Verification

- **Isolated Reproduction:** Verified that without the guard, running `test_pre_compute_empty_universe` (FinBERT) followed by `test_training_produces_model_and_real_metrics` (LightGBM) crashed with `Fatal Python error: Segmentation fault` (exit 139); with the fix, exit code is 0.
- **Combined Test Run:** `pytest tests/test_news_catalyst.py tests/test_train_lgbm.py tests/test_orchestrator_e2e.py tests/test_forecasting_f4_archive_import_smoke.py` passed cleanly with zero crashes.

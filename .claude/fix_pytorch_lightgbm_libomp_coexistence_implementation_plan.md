# Implementation Plan: Fix PyTorch + LightGBM `libomp.dylib` Crash in `TestFinbertOpenMPCoexistence` & Remaining Entry Points

## Context & Problem
After PR #1129 added `lightgbm` import pre-emption and `torch.set_num_threads(1)` in `signals/news_catalyst.py::_get_finbert_pipeline()` and `data/sector_embeddings.py`, running `pytest` under `pytest-xdist` (`make ci`) or running `pytest tests/test_news_catalyst.py -k TestFinbertOpenMPCoexistence` in isolation still crashed with `EXC_BAD_ACCESS (SIGSEGV)` in `libomp.dylib` (`Thread 4 Crashed: __kmp_suspend_initialize_thread + 32`, exit code 134/139).

### Root Causes
1. **Self-inflicted `torch`-first import in `TestFinbertOpenMPCoexistence`:**
   - `tests/test_news_catalyst.py::TestFinbertOpenMPCoexistence` called `pytest.importorskip("torch")` on line 1991 **before** `import lightgbm as lgb` (line 1992) and before `nc._get_finbert_pipeline()`. In any process where `lightgbm` had not already been imported, `pytest.importorskip("torch")` loaded `torch/lib/libomp.dylib` (`0x120368000`) ahead of Homebrew's `/opt/homebrew/opt/libomp/lib/libomp.dylib` (`0x12a0c0000`), defeating `_get_finbert_pipeline()`'s `import lightgbm` pre-emption and causing `lgb.train({"verbosity": -1, "num_threads": 2}, ...)` to segfault in `__kmp_suspend_initialize_thread + 32`.
   - `with patch("transformers.pipeline", return_value=MagicMock()):` resolved `transformers.pipeline` on `__enter__` before calling `nc._get_finbert_pipeline()`. Because `transformers` uses `_LazyModule`, resolving `.pipeline` both imported `transformers.pipelines` -> `torch` ahead of `_get_finbert_pipeline()` and swapped `sys.modules["transformers"]`, leaving `from transformers import pipeline` inside `_get_finbert_pipeline()` bound to the real unmocked HuggingFace loader.
   - Single-file `pytest tests/test_news_catalyst.py` runs had masked this only because `TestPreCompute::test_pre_compute_empty_universe` (and `get_symbol_news_catalyst_details` on empty headline lists) previously called the unmocked `_get_finbert_pipeline()` earlier in the file.
2. **Additional PyTorch / SBERT entry points & `tensorflow.__spec__` test stubs:**
   - `agents/rag_orchestrator.py` imported `from sentence_transformers import SentenceTransformer` without `lightgbm` import pre-emption and `torch.set_num_threads(1)`.
   - `data/sector_embeddings.py` ran `import lightgbm` and `import torch` unconditionally at module top even when `sentence-transformers` was not installed (`importlib.util.find_spec("sentence_transformers") is None`).
   - `tests/test_cnn_lstm_worker.py`, `tests/test_forecasting_lookahead.py`, `tests/test_lstm_attention_worker.py`, and `tests/test_cnn_lstm_isolation_dispatch.py` installed `types.ModuleType("tensorflow*")` stubs in `sys.modules` with `__spec__ = None`, causing `importlib.util.find_spec("tensorflow")` (called by `torch._dynamo` and others) to raise `ValueError: tensorflow.__spec__ is None`.

## Proposed Changes
1. **`tests/test_news_catalyst.py`**:
   - In `TestFinbertOpenMPCoexistence`, replace `pytest.importorskip("torch")` with `importlib.util.find_spec("torch")` and replace `patch("transformers.pipeline", ...)` with a `types.ModuleType("transformers")` stub in `sys.modules` before calling `nc._get_finbert_pipeline()`, importing `lightgbm` and `torch` only after `_get_finbert_pipeline()` runs.
   - Mock `_get_finbert_pipeline` in `TestPreCompute::test_pre_compute_empty_universe` and the `TestGetSymbolNewsCatalystDetails` tests that mock `score_headlines`.
2. **`signals/news_catalyst.py`**:
   - In `get_symbol_news_catalyst_details()`, only call `_get_finbert_pipeline()` when `headline_texts` is non-empty.
3. **`agents/rag_orchestrator.py` & `data/sector_embeddings.py`**:
   - Add `lightgbm` pre-emption and `torch.set_num_threads(1)` before importing `sentence_transformers` in `agents/rag_orchestrator.py`.
   - Guard `data/sector_embeddings.py`'s module-level `lightgbm`/`torch`/`sentence_transformers` import block with `importlib.util.find_spec("sentence_transformers") is not None`.
4. **TensorFlow mock test modules (`tests/test_cnn_lstm_worker.py`, `tests/test_forecasting_lookahead.py`, `tests/test_lstm_attention_worker.py`, `tests/test_cnn_lstm_isolation_dispatch.py`)**:
   - Set `__spec__ = importlib.machinery.ModuleSpec(name, None)` on synthetic `tensorflow*` `ModuleType` stubs.
5. **Documentation (`docs/known_issues/pytorch_lightgbm_libomp_collision.md`)**:
   - Document Round 2 root cause, reproduction, and fix.

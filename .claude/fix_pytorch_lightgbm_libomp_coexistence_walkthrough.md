# Walkthrough: Fix PyTorch + LightGBM `libomp.dylib` Coexistence (Round 2)

## Root Cause of the Crash
Running `pytest tests/test_news_catalyst.py -k TestFinbertOpenMPCoexistence` in isolation (or under `pytest -n auto --dist loadgroup` when `xdist` scheduled `TestFinbertOpenMPCoexistence` on a worker that had not yet imported `lightgbm`) crashed with `EXC_BAD_ACCESS (SIGSEGV)` at `0x0000000000000580` in `Thread 4: libomp.dylib __kmp_suspend_initialize_thread + 32` (exit code 134/139):

1. `TestFinbertOpenMPCoexistence` called `pytest.importorskip("torch")` on the line **before** `import lightgbm as lgb` and before `nc._get_finbert_pipeline()`, loading `torch/lib/libomp.dylib` (`0x120368000`) ahead of Homebrew's `/opt/homebrew/opt/libomp/lib/libomp.dylib` (`0x12a0c0000`) and defeating `_get_finbert_pipeline()`'s `import lightgbm` pre-emption.
2. `with patch("transformers.pipeline", return_value=MagicMock()):` resolved `transformers.pipeline` on `__enter__` **before** calling `nc._get_finbert_pipeline()`. Because `transformers` uses `_LazyModule`, resolving `.pipeline` both imported `transformers.pipelines` -> `torch` ahead of `_get_finbert_pipeline()` and swapped `sys.modules["transformers"]`, leaving `from transformers import pipeline` inside `_get_finbert_pipeline()` bound to the real unmocked HuggingFace loader.
3. Single-file `pytest tests/test_news_catalyst.py` runs had masked this only because `TestPreCompute::test_pre_compute_empty_universe` (and `get_symbol_news_catalyst_details` on empty headline lists) previously called the unmocked `_get_finbert_pipeline()` earlier in the file.

## Changes Made
- **`tests/test_news_catalyst.py`**:
  - `TestFinbertOpenMPCoexistence` now checks `importlib.util.find_spec("torch")` without importing `torch`, installs a lightweight `types.ModuleType("transformers")` stub in `sys.modules` via `monkeypatch.setitem`, calls `nc._get_finbert_pipeline()` so `_get_finbert_pipeline()` itself runs `import lightgbm` -> `import torch; torch.set_num_threads(1)`, and only then imports `lightgbm` and `torch` in the test body to run `lgb.train(..., {"num_threads": 2})` and `torch.softmax(...)`.
  - Added `patch("signals.news_catalyst._get_finbert_pipeline", return_value=None)` to `test_pre_compute_empty_universe` and the `TestGetSymbolNewsCatalystDetails` tests that mock `score_headlines`.
- **`signals/news_catalyst.py`**:
  - `get_symbol_news_catalyst_details()` now only calls `_get_finbert_pipeline()` when `headline_texts` is non-empty.
- **`agents/rag_orchestrator.py` & `data/sector_embeddings.py`**:
  - Added `lightgbm` import pre-emption and `torch.set_num_threads(1)` before `from sentence_transformers import SentenceTransformer` in `agents/rag_orchestrator.py`.
  - Guarded `data/sector_embeddings.py`'s module-level `lightgbm`/`torch`/`sentence_transformers` import block with `importlib.util.find_spec("sentence_transformers") is not None` and regenerated `docs/settings_liveness.json`.
- **TensorFlow mock test modules (`tests/test_cnn_lstm_worker.py`, `tests/test_forecasting_lookahead.py`, `tests/test_lstm_attention_worker.py`, `tests/test_cnn_lstm_isolation_dispatch.py`)**:
  - Populated `__spec__ = importlib.machinery.ModuleSpec(name, None)` on synthetic `types.ModuleType("tensorflow*")` stubs so `importlib.util.find_spec("tensorflow")` never raises `ValueError: tensorflow.__spec__ is None`.
- **`docs/known_issues/pytorch_lightgbm_libomp_collision.md`**:
  - Documented Round 2 symptom, root cause, fix, and verification.

## Verification
- `NO_VENV_REEXEC=1 /Users/kevinlee/Stockpy-live/.venv/bin/python -m pytest tests/test_news_catalyst.py -k TestFinbertOpenMPCoexistence -q` -> `1 passed` (previously crashed 100% deterministically with exit code 134 / `SIGSEGV` in `libomp.dylib`).
- `NO_VENV_REEXEC=1 /Users/kevinlee/Stockpy-live/.venv/bin/python -m pytest tests/test_news_catalyst.py tests/test_train_lgbm.py tests/test_cnn_lstm_worker.py tests/test_forecasting_lookahead.py tests/test_lstm_attention_worker.py tests/test_cnn_lstm_isolation_dispatch.py tests/test_sector_embeddings.py tests/test_rag_orchestrator.py tests/test_settings_liveness.py -q` -> `302 passed, 0 warnings`.
- `NO_VENV_REEXEC=1 PATH="/Users/kevinlee/Stockpy-live/.venv/bin:$PATH" make ci` -> `11774 passed, 15 skipped, 0 failed in 137.85s`.

## Hardening and re-verification (2026-11-11)
- Brought up to date with `main` at `88cbe8d8` (six Robinhood-agent commits, #1134-#1139) by a merge commit; no conflicts.
- `data/sector_embeddings.py`: the `find_spec("sentence_transformers")` guard raised `ValueError` (not `ImportError`) when `sys.modules` held a spec-less stub, so the module failed to import where `main` degraded to `SBERT_AVAILABLE = False`. The probe now lives in `_sbert_installed()`, which treats that `ValueError` as "not installed". Regression tests: `tests/test_sector_embeddings.py::TestSbertAvailabilityProbe`. `docs/settings_liveness.json` regenerated (site line 67 -> 84).
- Verified on Linux (Python 3.13, torch 2.15, transformers 5.22, lightgbm 4.7; sentence-transformers not installed, matching the optional-dependency default):
  - `-k TestFinbertOpenMPCoexistence` in isolation: 1 passed. The same test on `main` fails in isolation here (`assert None is not None`): `patch("transformers.pipeline")` leaves the real FinBERT loader bound, which tries to download the model. That is root cause 2 above, seen without the macOS crash.
  - Targeted suite (the list above plus `tests/test_agent_docs_sync.py`), `-n 4 --dist loadgroup`: 305 passed.
  - Full offline suite (`-m "not network and not slow" -n 4 --dist loadgroup`): 12,090 passed, 28 skipped, 12 failed. The 12 are 11 LightGBM training tests that hit the 300 s timeout under four parallel workers (the oversubscription hang documented in the open cleanup PR #1133) and one DevTools widget test; all 51 tests in those files pass serially on both this branch and `main`.
- Not reproduced here: the macOS Apple Silicon `libomp.dylib` SIGSEGV itself (Linux wheels share one OpenMP runtime).

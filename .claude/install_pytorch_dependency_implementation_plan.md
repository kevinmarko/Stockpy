# Implementation Plan - Promote PyTorch to Core Dependency

Promote PyTorch (`torch>=2.0`) from an optional dependency in `requirements-optional.txt` to a base dependency in `requirements.txt`, install it into the virtual environment, resolve macOS OpenMP runtime collisions between PyTorch and LightGBM, and align unit test assertions for environments with PyTorch installed.

## User Review Required

> [!NOTE]
> The user confirmed in conversation to promote PyTorch to a core dependency by adding `torch>=2.0` to `requirements.txt` and installing it into `.venv`.

## Proposed Changes

### Dependencies

#### [requirements.txt](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/requirements.txt)
- Add `torch>=2.0` under transformers/ML dependencies.
- Add context comments explaining that `torch>=2.0` powers FinBERT local sentiment scoring in `signals/news_catalyst.py`.

#### [requirements-optional.txt](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/requirements-optional.txt)
- Remove `torch>=2.0`.
- Update header commentary to state that PyTorch was promoted to `requirements.txt`, leaving TensorFlow, FAISS, Sentence-Transformers, and Playwright in optional dependencies.

### Runtime Stability (macOS OpenMP Collision Resolution)

#### [signals/news_catalyst.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/signals/news_catalyst.py), [data/sector_embeddings.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/data/sector_embeddings.py), & [legacy/forecasting/bert_lla.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/legacy/forecasting/bert_lla.py)
- Import `lightgbm` before `torch` / `transformers` / `sentence_transformers` to pre-empt OpenMP binding on macOS Apple Silicon.
- Call `torch.set_num_threads(1)` immediately upon torch import to prevent thread-pool contention and deadlocks when LightGBM initializes an OpenMP context in the same process.

### Tests

#### [tests/test_news_catalyst.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/tests/test_news_catalyst.py) & [legacy/tests/test_bert_lla.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/legacy/tests/test_bert_lla.py)
- Add `TestFinbertOpenMPCoexistence` in `tests/test_news_catalyst.py` verifying intra-op thread count cap in `_get_finbert_pipeline()` and mutual sequential execution with LightGBM.
- Update `legacy/tests/test_bert_lla.py` (`TestAbsentTorchDegradation` + `TestOpenMPCoexistence`) for environments with torch installed.

### Documentation

#### [docs/known_issues/pytorch_lightgbm_libomp_collision.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/docs/known_issues/pytorch_lightgbm_libomp_collision.md) & [docs/known_issues/README.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/docs/known_issues/README.md)
- Full root-cause analysis and fix documentation for macOS OpenMP collision.

#### [docs/signals/news_catalyst.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/docs/signals/news_catalyst.md) & [docs/architecture/ml-and-reports.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/docs/architecture/ml-and-reports.md)
- Updated references to PyTorch dependency status.

## Verification Plan

### Automated Tests
- Run `pytest tests/test_news_catalyst.py tests/test_sector_descriptions.py tests/test_train_lgbm.py tests/test_orchestrator_e2e.py tests/test_forecasting_f4_archive_import_smoke.py`
- Run agent docs sync check: `pytest tests/test_agent_docs_sync.py`
- Run code quality gate: `make lint`

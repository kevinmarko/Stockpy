# Walkthrough: Install PyTorch Dependency

Promoted PyTorch (`torch>=2.0`) to a core dependency in `requirements.txt`, installed it into `.venv`, rebased onto `origin/main` (incorporating `#1127`), resolved macOS OpenMP runtime collisions with LightGBM across active and archived modules, and added regression coverage.

## Changes

### Dependency Configuration
- **[requirements.txt](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/requirements.txt)**: Added `torch>=2.0` with doc comments explaining its role backing `transformers>=4.35.0` in `signals/news_catalyst.py` (FinBERT).
- **[requirements-optional.txt](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/requirements-optional.txt)**: Removed `torch>=2.0` and noted its promotion to core requirements (resolved merge conflict cleanly with `origin/main`'s `#1127` F4 BERT-LLA archive).
- **Environment**: Installed `torch==2.14.1` into `.venv` using `uv` (MPS backend available on Apple Silicon).

### Runtime Stability & macOS OpenMP Collision Resolution
- **[signals/news_catalyst.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/signals/news_catalyst.py)**, **[data/sector_embeddings.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/data/sector_embeddings.py)**, & **[legacy/forecasting/bert_lla.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/legacy/forecasting/bert_lla.py)**:
  - Eagerly imported `lightgbm` before `torch` / `transformers` / `sentence_transformers` to pre-empt Homebrew OpenMP runtime binding (`libomp.dylib`).
  - Added `torch.set_num_threads(1)` immediately upon torch import to cap intra-op parallelism to 1 thread. This prevents OpenMP thread pool contention, deadlocks, and SIGSEGV crashes between LightGBM and PyTorch on macOS Apple Silicon.

### Test Suite Alignment & Regression Tests
- **[tests/test_news_catalyst.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/tests/test_news_catalyst.py)**:
  - Added `TestFinbertOpenMPCoexistence` verifying that `_get_finbert_pipeline()` caps `torch.get_num_threads() == 1` and that LightGBM and PyTorch execute sequentially in the same process without SIGSEGV or hangs.
- **[legacy/tests/test_bert_lla.py](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/legacy/tests/test_bert_lla.py)**:
  - Updated absent-torch degradation checks and added `TestOpenMPCoexistence` in the archived BERT-LLA test suite.

### Documentation
- **[docs/known_issues/pytorch_lightgbm_libomp_collision.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/docs/known_issues/pytorch_lightgbm_libomp_collision.md)**: Documented the macOS OpenMP dylib collision bug class, reproduction steps, root cause, and fix.
- **[docs/known_issues/README.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/docs/known_issues/README.md)**: Added entry to known issues table.
- **[docs/signals/news_catalyst.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/docs/signals/news_catalyst.md)**: Updated reference to note that `torch>=2.0` is provided in `requirements.txt`.
- **[docs/architecture/ml-and-reports.md](file:///Users/kevinlee/.gemini/antigravity/worktrees/Stockpy-live/install_pytorch_dependency/docs/architecture/ml-and-reports.md)**: Updated notes regarding torch availability.

## Verification Results

### Automated Tests
- `pytest tests/test_news_catalyst.py tests/test_sector_descriptions.py tests/test_train_lgbm.py tests/test_orchestrator_e2e.py tests/test_forecasting_f4_archive_import_smoke.py`:
  - All passed cleanly with 0 failures.
- `make lint`:
  - **All checks passed!**
- `pytest tests/test_agent_docs_sync.py`:
  - **1 passed**.

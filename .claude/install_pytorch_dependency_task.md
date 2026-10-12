# Task: Install PyTorch Dependency

- [x] Check Python environment, uv package manager, and dependency files
- [x] Run dry-run installation with uv to ensure no dependency collisions (e.g. numpy ceiling <2.3)
- [x] Confirm with user to promote PyTorch to core dependency in `requirements.txt`
- [x] Install `torch>=2.0` into `.venv` (completed: `torch==2.14.1`, MPS available: True)
- [x] Add `torch>=2.0` to `requirements.txt` with descriptive comments
- [x] Update `requirements-optional.txt` to reflect PyTorch promotion to core dependency
- [x] Rebase onto `origin/main` (incorporating F4 BERT-LLA archive `#1127`) and resolve `requirements-optional.txt` conflict
- [x] Resolve macOS OpenMP collision between PyTorch and LightGBM in `signals/news_catalyst.py`, `data/sector_embeddings.py`, and `legacy/forecasting/bert_lla.py`
- [x] Add regression tests for OpenMP coexistence in `tests/test_news_catalyst.py` (`TestFinbertOpenMPCoexistence`) and `legacy/tests/test_bert_lla.py` (`TestOpenMPCoexistence`)
- [x] Document OpenMP collision in `docs/known_issues/pytorch_lightgbm_libomp_collision.md` and index in `docs/known_issues/README.md`
- [x] Update architecture and signal documentation (`docs/signals/news_catalyst.md`, `docs/architecture/ml-and-reports.md`)
- [x] Verify targeted test suites, `make lint`, and `tests/test_agent_docs_sync.py`
- [x] Push branch, open PR, and merge when green

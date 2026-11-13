# Task Tracker: Fix PyTorch + LightGBM `libomp.dylib` Coexistence (Round 2)

- [x] Reproduce `Thread 4 Crashed: libomp.dylib __kmp_suspend_initialize_thread + 32` deterministically via `pytest tests/test_news_catalyst.py -k TestFinbertOpenMPCoexistence`
- [x] Fix `TestFinbertOpenMPCoexistence` (`find_spec("torch")` + `sys.modules["transformers"]` stub before `_get_finbert_pipeline()`)
- [x] Skip `_get_finbert_pipeline()` on empty `headline_texts` in `get_symbol_news_catalyst_details()` and mock `_get_finbert_pipeline` in unit tests that mock `score_headlines` / empty universe
- [x] Add `lightgbm` pre-emption + `torch.set_num_threads(1)` to `agents/rag_orchestrator.py` and guard `data/sector_embeddings.py` with `find_spec("sentence_transformers")`
- [x] Set `ModuleSpec` on synthetic `tensorflow*` stubs in `test_cnn_lstm_worker.py`, `test_forecasting_lookahead.py`, `test_lstm_attention_worker.py`, and `test_cnn_lstm_isolation_dispatch.py`
- [x] Update `docs/known_issues/pytorch_lightgbm_libomp_collision.md` (Round 2) and regenerate `docs/settings_liveness.json`
- [x] Verify targeted test suite (`249 passed`) and full `make ci` (`11774 passed, 15 skipped, 0 failed`)
- [x] Commit artifacts, push branch, and open PR

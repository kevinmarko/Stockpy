# Task: Fix transformer-forecaster macro publication-lag lookahead + dead macro-series request

- [x] Branch `fix-transformer-macro-publication-lag` created from `origin/main`
- [x] Bug A fixed: `ml/transformer_vol_forecaster.py::_align_macro_causal` publication-lag handling
- [x] `build_tft_model()` reproducibility seed added (`TFT_RANDOM_SEED = 42`)
- [x] Bug B fixed: `data_engine.py::DataEngine.fetch_macro_history()` fetches `BAMLC0A0CM`/`FEDFUNDS`
- [x] Tests added: `tests/test_transformer_vol_forecaster.py` (+3), `tests/test_data_engine_macro_history.py` (+3 methods in a new class, 2 existing tests updated)
- [x] Docs updated: `docs/architecture/ml-and-reports.md` transformer-forecaster bullet, dated 2026-08-24 addendum
- [x] Combined verification: `pytest tests/test_transformer_vol_forecaster.py tests/test_data_engine_macro_history.py -q` — **25 passed, 0 failed** (19 + 6, 1.07s)
- [x] `ruff check` on the 4 changed/touched code files — 129 pre-existing findings across the full files (UP006, BLE001, UP045, UP035, PIE790, B023, DTZ005, I001, RUF059, B006, UP007, F841, RUF012, RUF046, S110), informational only, none attributable to this diff, no fixes attempted per task instructions
- [ ] PR opened (orchestrator, after this artifact is written)

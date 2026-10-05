# Walkthrough: keep real faiss out of pytest worker processes

Branch: `isolate-faiss-tests-from-lightgbm`

## Problem

On macOS arm64, once faiss's bundled `libomp.dylib` is loaded, a later real
lightgbm train in the same process segfaults (the three-libomp collision in
`docs/known_issues/lightgbm_faiss_libomp_collision_segfault.md`). The real-faiss
tests in `tests/test_rag_index.py` load faiss at test time. Under
`pytest -n auto --dist loadgroup` that only crashed when xdist put them on the
same worker as a lightgbm test, so it showed up as a random "node down".

Reproduced on origin/main:

```
NO_VENV_REEXEC=1 .venv/bin/python -m pytest tests/test_rag_index.py \
  tests/test_train_meta_labelers.py -q -p no:randomly -p no:xdist
...Fatal Python error: Segmentation fault  (lightgbm/basic.py __init_from_np2d)
```

## What changed (tests only, no production code)

- `conftest.py`: new autouse `_block_real_faiss_in_pytest_process`. Sets
  `sys.modules["faiss"] = None` for every test (so `import faiss` raises,
  including inside `data.rag_index`), and fails the test if real faiss is
  already loaded. Skipped when `STOCKPY_REAL_FAISS_SUBPROCESS=1`. Done by hand,
  not with `monkeypatch`, because requesting `monkeypatch` from an early
  autouse fixture changed teardown order and broke
  `tests/test_quantitative_models.py::test_garch_and_edge_scoring`.
- `tests/test_rag_index.py`:
  - `TestRealFaissRoundTrip` and `TestFaissThreadCapRegression` keep their
    bodies and assertions; they now run only in the child process.
  - A module-scoped fixture runs one child
    `python -m pytest <those two classes> -p no:xdist -p no:randomly
    --junitxml=...` with `timeout=600` and the env flag set.
  - `test_real_faiss_in_subprocess[<Class::test>]` (one per real test) fails
    unless that test passed in the child;
    `test_real_faiss_subprocess_ran_exactly_the_expected_tests` checks the
    child ran exactly those tests and exited 0. All pinned to one xdist
    worker (`xdist_group`) so the child runs once per run.
  - Still skipped when `find_spec("faiss")` is None, as before.
  - Guards: `test_real_faiss_is_blocked_in_this_process` and an AST scan,
    `test_no_test_module_imports_faiss_outside_the_subprocess_classes`.
- `pytest.ini`: registers the `xdist_group` marker so `-p no:xdist` runs
  (the child, and the repro command) still collect under `--strict-markers`.
- Docs: Round 3 section in the known-issues doc; CLAUDE.md/AGENTS.md faiss
  bullet.

Other test files mentioning faiss (`test_portfolio_context.py`,
`test_historical_store_sentiment_audit.py`, `test_browser_diagnostics.py`)
only mention it in strings or column names; none import it.
`pytest-forked` is not installed, so no new dependency was added.

## Verification

- Repro command: 3/3 runs `47 passed, 9 skipped`.
- `tests/test_rag_index.py` alone: `18 passed, 9 skipped`.
- Broke one assertion in `TestRealFaissRoundTrip` on purpose: the wrapper
  failed with the child's traceback; reverted.
- Full offline suite with scratch `LOCAL_DATA_ROOT`
  (`-m "not network and not slow" -n auto --dist loadgroup -q -p no:randomly`):
  3 consecutive runs `11689 passed, 27 skipped`, no "node down". One earlier
  run had a single failure in
  `test_daemon_runtime.py::TestScheduledRobinhoodLogin::test_watcher_records_and_alerts_the_outcome`
  (a 3-second poll timing test, unrelated).
- `ruff check --select=F821,F822,F823,E9 .`: clean.

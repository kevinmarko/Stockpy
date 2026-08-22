# Task tracker — sentiment dedup / confidence / NaN guard fix

- [x] Fix 1: cross-source dedup pass in `CompositeSentimentSource.fetch_all()` (`data/sentiment_sources.py`)
- [x] Fix 2: honest `confidence` derivation in `NewsCatalystSignal.compute()` (`signals/news_catalyst.py`)
- [x] Fix 3: NaN guard on the live social-blend read in `compute()` (`signals/news_catalyst.py`)
- [x] Regression tests: `tests/test_sentiment_sources.py::TestCrossSourceDedup` (5 new tests)
- [x] Regression tests: `tests/test_news_catalyst.py::TestSignalCompute` (4 new tests + 1 updated)
- [x] Docs: `data/sentiment_sources.py` module docstring, `docs/signals/news_catalyst.md`
- [x] `ruff check . --select=F821,F822,F823,E9` — clean
- [x] `pytest tests/test_sentiment_sources.py tests/test_news_catalyst.py tests/test_pilots_news_catalyst.py -q` — 249 passed
- [x] Full offline suite run + pre-existing-failure verification via `git stash` against clean `main`
- [ ] Open PR against `main`

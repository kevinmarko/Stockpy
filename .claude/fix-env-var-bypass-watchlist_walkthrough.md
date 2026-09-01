# Walkthrough

- Modified `data/portfolio_sync.py` to use `settings.WATCHLIST` instead of `os.environ.get("WATCHLIST")`.
- Modified `pilots/watchlist_writer.py` to add `from settings import settings` and use `settings.WATCHLIST`.
- Modified `gui/panels/_shared.py` to use `settings.WATCHLIST`.
- Updated `tests/test_portfolio_sync.py` to correctly mock `settings.WATCHLIST` instead of `os.environ`.
- Verified changes by running `pytest tests/test_portfolio_sync.py tests/test_watchlist_writer.py` and GUI tests, all of which pass successfully.

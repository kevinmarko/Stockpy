# Implementation Plan

## Goal Description
Fix the OS environment variable bypass for WATCHLIST by swapping `os.environ.get("WATCHLIST")` to `settings.WATCHLIST` (or `settings.settings.WATCHLIST`) in `data/portfolio_sync.py`, `pilots/watchlist_writer.py`, and `gui/panels/_shared.py`.

## Proposed Changes
- `data/portfolio_sync.py::load_env_watchlist`: Replace `os.environ.get("WATCHLIST", "").strip()` with `settings.WATCHLIST.strip()`.
- `pilots/watchlist_writer.py`: Import `settings` and replace `os.environ.get("WATCHLIST", "")` with `settings.WATCHLIST`.
- `gui/panels/_shared.py::_watchlist_symbols`: Replace `os.environ.get("WATCHLIST", "").strip()` with `settings.WATCHLIST.strip()`.
- `tests/test_portfolio_sync.py`: Update `monkeypatch.setenv("WATCHLIST", ...)` to `monkeypatch.setattr(ps.settings, "WATCHLIST", ...)` to ensure tests pass.

## Verification
- Run `pytest tests/test_portfolio_sync.py tests/test_watchlist_writer.py -q`.
- Run GUI panel tests.

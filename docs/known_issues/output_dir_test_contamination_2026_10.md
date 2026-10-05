# Test suite wrote into the live OUTPUT_DIR (2026-10)

**Status:** Fixed (2026-10-05).

## What happened
The root `conftest.py` isolated the SQLite stores and the runtime-flags store, but not `OUTPUT_DIR` (`~/.stockpy_local/output`). Tests that write through default `OUTPUT_DIR` paths therefore wrote into the operator's live output directory on every run, from every worktree. One controlled run touched:

- `queue_sources/advisory.json`: replaced with an empty `targets: []`. That afternoon (16:27 ET) it overwrote `main.py`'s real 08:47 ET advisory source. The step-5 shadow comparison then showed all 23 advisory targets as "only in shadow".
- `risk_gate_blocks.jsonl` (appended fake AAPL/NVDA blocks), `progress.json`, `daemon.tmp`, `watch_state.json`, `dead_letter.json`, `last_data_refresh.txt`, `llm_status.json`, and the HTML dashboards / `artifacts/`.

`execution_queue.json`, the real Robinhood queue, was not overwritten in that run.

## Fix
At import time the root `conftest.py` sets the `OUTPUT_DIR` environment variable to a fresh temp dir, before any platform module is imported, unless the shell already set one. Real environment variables take top precedence (`settings.py`). This covers `settings.OUTPUT_DIR` and every module-level path derived from it at import.
- `tests/test_output_dir_test_isolation.py` pins this, including a default `write_advisory_source` call.
- Three tests in `tests/test_robinhood_execution_panel.py` that asserted the literal folder name `output` now compare against the resolved `settings.OUTPUT_DIR`.

## Clean-up
The files above are rewritten by the next daemon or `main.py` cycle. The exception is `risk_gate_blocks.jsonl`, an append-only log that keeps the fake rows; treat AAPL/NVDA `strategy_id=advisory` rows in it with suspicion. The real advisory source is rewritten by `main.py` at 08:45 ET on the next weekday.

Worktrees still on pre-fix code keep writing into the live dir when their tests run, until they pull this change.

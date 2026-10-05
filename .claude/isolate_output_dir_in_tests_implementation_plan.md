# Isolate OUTPUT_DIR in tests

Problem: tests wrote into the live `~/.stockpy_local/output`. That emptied `queue_sources/advisory.json`, which broke the step-5 shadow comparison, and appended fake rows to `risk_gate_blocks.jsonl`, among others.

Fix: the root `conftest.py` sets the `OUTPUT_DIR` env var to a temp dir before any platform import, unless the shell already set one. Env vars outrank the store and `.env`, and import-time path constants derive from the setting.

Tests:
- `tests/test_output_dir_test_isolation.py` (new guard);
- three path tests in `tests/test_robinhood_execution_panel.py` compare against the resolved setting.

Docs:
- known issue `output_dir_test_contamination_2026_10.md` and its index row;
- a CLAUDE.md/AGENTS.md test-isolation sentence.

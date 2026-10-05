# Walkthrough: isolate OUTPUT_DIR in tests

**How it was found.** The step-5 shadow comparison showed an empty real advisory source, rewritten at 16:27 ET. A controlled test run with the live files backed up confirmed that the suite rewrote `queue_sources/advisory.json` to `targets: []` and touched about 11 other live output files.

**The fix.** It is one block at the top of the root `conftest.py` that sets the `OUTPUT_DIR` env var to a temp dir when the shell hasn't set one. It has to run before `settings` is imported, because several modules compute paths at import time. A monkeypatch fixture wouldn't reach those.

**Verification.**
- After the fix, the full `make ci` (11767 passed) touched 0 files under `~/.stockpy_local/output`, checked with a timestamp marker and `find -newer`.
- `tests/test_output_dir_test_isolation.py` repeats the exact default `write_advisory_source` call that emptied the live file.

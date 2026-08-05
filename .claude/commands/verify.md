---
description: Run the fast offline verification gate (ruff genuine-bug lint + offline pytest suite) before considering work complete
---

Run the same offline gate CI runs on every PR (`.github/workflows/ci.yml`'s
`test` job), and report the result honestly.

1. **Lint (ruff, genuine-bug rules only):**
   ```
   python -m ruff check . --select=F821,F822,F823,E9
   ```
   This is scoped to undefined-name/syntax-error classes of bug, not the
   full default ruleset (the codebase has ~1200 pre-existing style
   violations against that this gate deliberately does not enforce).

2. **Offline test suite:**
   ```
   make ci
   ```
   This mirrors CI's `test` job: `pytest -m "not network and not slow"` —
   deterministic, no live upstream/broker calls.

Report the result of each step verbatim (pass/fail, and on failure the
actual error output — not a paraphrase). If either step fails, fix the
underlying issue and re-run from step 1; do not report this as done while
either step is red.

Note: `make verify` runs a deeper gate on top of this (env-var check, the
full test suite, and one **live** `run_once()` cycle that touches real
broker/credential paths). It's available for a more thorough check but
should be offered to the operator, not run unprompted.

$ARGUMENTS

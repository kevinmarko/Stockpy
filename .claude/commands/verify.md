---
description: Run the fast offline verification gate (ruff genuine-bug lint + offline pytest suite) before considering work complete
---

Run the fast offline gate (the `lint` and `offline` steps of `make local-ci`; the
GitHub workflow that used to run it was deleted on 2026-10-30), and report the
result honestly.

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
full test suite, and one standalone dry-run pipeline cycle via
`scripts/step5_4_verify_cycle.py`, in its own process; it never contacts the
daemon). That cycle forces `DAEMON_AGENTIC_QUEUE_MODE` to `off` in-process,
turns off symbol-rating and cap-audit recording, never starts a Robinhood
login, and suppresses every alert, so it writes no execution queue and sends
no pushes or orders — but it still fetches live market data and rewrites
`state_snapshot.json`, the dashboards and `dead_letter.json`. It exits
non-zero if the cycle fails, does not refresh the snapshot, or runs on
synthetic data (so a data outage or a missing `FRED_API_KEY` fails it rather
than passing). It's available for a more thorough check but should be offered
to the operator, not run unprompted.

$ARGUMENTS

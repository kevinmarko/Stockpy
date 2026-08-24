# Implementation Plan: fix earnings_crush.py BMO/AMC bar-alignment blind spot

Branch: `fix-earnings-crush-bmo-amc-blindspot`

## Context

An audit of `pilots/earnings_crush.py` found a confirmed, reproduced HIGH-severity bug:
`get_historical_earnings_moves` always computed the realized post-earnings gap as
`|Open[event_date] - Close[event_date-1]|` — correct only when a company reports before
market open (BMO). For an after-market-close (AMC) reporter — the majority case for
large-cap tech, and this module's own default universe — the real reaction lands one
trading day later and was never measured, reproducibly reporting `≈0%` for a real ~14.66%
synthetic AMC reaction. Since `crush_edge_ratio = expected_move_pct / realized_move_pct`,
this understated denominator inflates the edge ratio and can flip `is_recommended=True` for
a candidate with no genuine edge — firing a real `dispatch_earnings_crush_alert`. The same
root cause let the front-week expiration picker select an expiration dated exactly
`event_date`, which for an AMC reporter expires before the reaction happens.

Verified (3 independent Explore agents + direct WebFetch/WebSearch against FMP's own
published API docs) that no real BMO/AMC field exists anywhere in this codebase's earnings
data — `data/fmp_client.py::earnings()`, `data/fmp_feeds_company.py::fetch_earnings_rows()`,
and the `earnings_events` table DDL all carry only `symbol`, `event_date`, `eps_actual/
estimated`, `revenue_actual/estimated`, `last_updated`, `source`, `fetched_at`. Per
CONSTRAINT #4 the fix infers the reaction session from real bar data rather than fabricating
a timing label.

## Approach

1. `get_historical_earnings_moves` (`pilots/earnings_crush.py`) now computes two real gaps
   per quarter — the existing BMO hypothesis and a new AMC hypothesis
   (`|Open[event_date+1] - Close[event_date]| / Close[event_date]`) — and takes whichever is
   larger (conservative: can only increase, never decrease, the realized-move denominator).
   Each move now carries `reaction_session_inferred: "bmo"|"amc"`; the function's return dict
   carries `timing_data_available: False` on every code path.
2. `evaluate_earnings_crush_candidates`'s front-week expiration picker now requires
   `ed > event_date` (was `ed >= event_date`), so the chosen expiration always clears the
   earnings date entirely regardless of session.
3. Hand-verified (and confirmed by an Explore agent's full fixture review) that both changes
   are a no-op against every pre-existing test in `tests/test_earnings_crush.py`.
4. Documentation: `docs/signals/earnings_crush.md` addendum, a new
   `docs/known_issues/earnings_crush_bmo_amc_bar_alignment.md` full incident write-up plus its
   `docs/known_issues/README.md` index row, and a `CLAUDE.md` bullet (auto-mirrored to
   `AGENTS.md`).

## Scope

Findings #2-9 from the same audit (`historical_moves` not reaching the API response, dead
IV-burst detection in `pilots/unusual_options_flow.py`, mid-block conviction weighting,
missing honesty flags for two proxy substitutions, per-contract error isolation, "nothing
found" vs "fetch failed" ambiguity, a non-atomic JSON write, a missing `net_credit` field) are
explicitly out of scope for this PR — flagged as a follow-up task.

## Verification

- `pytest tests/test_earnings_crush.py -q` — 20/20 passed (17 pre-existing + 3 new).
- `pytest tests/test_options_paper_executor.py tests/test_options_desk_deployability_runtime_gap.py tests/test_pilots_paper_broker.py tests/test_pilots_api.py tests/test_options_lifecycle.py tests/test_options_alerts.py tests/test_pilots_strategy_matrix.py -q` (every other test file referencing `earnings_crush`) — 751/751 passed.
- `python -m ruff check . --select=F821,F822,F823,E9` (CI's genuine-bug lint gate) — clean.
- `make ci` (full offline suite) — run and reported in the PR walkthrough.

# Walkthrough: fix earnings_crush.py BMO/AMC bar-alignment blind spot

## The bug

`pilots/earnings_crush.py::get_historical_earnings_moves` measured every quarter's realized
post-earnings move as `|Open[event_date] - Close[event_date-1]|` — correct only when a
company reports **before market open (BMO)**. `event_bar["Close"]` was never read and no bar
after `event_date` was ever consulted, so an **after-market-close (AMC)** reaction (the
majority case for large-cap tech, and this module's own default universe — NVDA, AAPL, MSFT,
TSLA, AMZN, GOOGL, META, AMD, NFLX, DIS) was structurally invisible.

Reproduced: synthetic bars simulating a true 14.66% AMC overnight reaction (flat everywhere
else) reported `median_move_pct ≈ 0.0` under the old code.

**Why this matters**: `crush_edge_ratio = expected_move_pct / realized_move_pct`. Understating
the realized-move denominator inflates the edge ratio and can flip `is_recommended=True` for a
candidate with no genuine edge, firing a real `dispatch_earnings_crush_alert`. The identical
root cause let the front-week expiration picker select an expiration dated exactly
`event_date`, which for an AMC reporter expires before the reaction happens.

## Why not just read a real BMO/AMC field?

Verified — not assumed — that none exists. Three independent Explore agents plus direct
WebFetch/WebSearch against FMP's own published `/earnings` API docs confirmed
`data/fmp_client.py::earnings()`, `data/fmp_feeds_company.py::fetch_earnings_rows()`, and the
`earnings_events` table DDL all carry exactly `symbol`, `event_date`, `eps_actual/estimated`,
`revenue_actual/estimated`, `last_updated`, `source`, `fetched_at` — no timing/session field.
A repo-wide grep for BMO/AMC hits only two `.claude/` pre-implementation planning docs
(aspirational prose from before this pilot was built, never backed by a real ingested field).

## The fix

`get_historical_earnings_moves` now computes **two** real, bar-derived gaps per quarter and
takes the larger:

- BMO hypothesis (unchanged): `|Open[bar_idx] - Close[bar_idx-1]| / Close[bar_idx-1]`.
- AMC hypothesis (new): `|Open[bar_idx+1] - Close[bar_idx]| / Close[bar_idx]`, computed only
  when a `bar_idx+1` bar exists (degrades to BMO-only otherwise — today's exact prior
  behavior).

This is deliberate, not arbitrary: both gaps are real observations (never fabricated,
CONSTRAINT #4); a genuine reaction dominates ordinary single-day noise so the larger gap
correctly identifies which session held it in the overwhelming majority of cases; and it's the
conservative direction for this exact bug — taking the max can only *increase* the
realized-move denominator, which can only *decrease* `crush_edge_ratio`, the opposite of the
bug's actual danger.

Each move now carries `reaction_session_inferred: "bmo" | "amc"` (explicitly labeled
*inferred*), and the function's return dict carries `timing_data_available: False` on every
path — self-documenting, forward-compatible if a real timing field is ever integrated.

`evaluate_earnings_crush_candidates`'s expiration picker now requires `ed > event_date` (was
`ed >= event_date`), guaranteeing the chosen expiration always clears the earnings date.

## Verification

- `pytest tests/test_earnings_crush.py -q` → **20 passed** (17 pre-existing + 3 new:
  `test_amc_reaction_captured_via_next_day_open`, `test_bmo_reaction_still_captured_correctly`,
  `test_same_day_expiration_rejected_in_favor_of_later_one`).
- Hand-verified (and confirmed via an Explore agent's line-by-line fixture review) that both
  changes are a no-op against every pre-existing fixture: they only ever vary `Open` on the
  event date (never `Close`), and no existing expiration fixture is ever exactly equal to
  `event_date`.
- `pytest tests/test_options_paper_executor.py tests/test_options_desk_deployability_runtime_gap.py tests/test_pilots_paper_broker.py tests/test_pilots_api.py tests/test_options_lifecycle.py tests/test_options_alerts.py tests/test_pilots_strategy_matrix.py -q` (every other file referencing `earnings_crush`) → **751 passed**.
- `python -m ruff check . --select=F821,F822,F823,E9` (CI's genuine-bug lint gate) → clean.
- Full offline suite (`pytest -m "not network and not slow" -n auto --dist loadgroup`) →
  **12152 passed, 31 skipped, 5 failed** in 123.95s. All 5 failures are pre-existing and
  unrelated to this change: `tests/test_data_api_chat.py::TestMultiProviderRouting::*` (3) and
  `tests/test_gemini_live_chat.py::TestLiveChatSession::*` (2) fail with
  `ImportError: cannot import name 'genai' from 'google'` — the `google-genai` package isn't
  installed in this sandbox. Confirmed by re-running the same two representative failing tests
  on a clean `git stash` (this branch's diff fully reverted) — identical failures, same
  ImportError, proving they are an environment gap, not a regression from this PR.

## Scope note

The audit that surfaced this bug also raised 8 other findings (`historical_moves` not
reaching the API response, dead IV-burst detection in `pilots/unusual_options_flow.py`,
mid-block conviction weighting, missing honesty flags on two proxy substitutions, per-contract
error isolation, "nothing found" vs "fetch failed" ambiguity, a non-atomic JSON write, a
missing `net_credit` field). Deliberately out of scope for this PR — flagged as a follow-up.

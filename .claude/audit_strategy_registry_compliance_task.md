- [x] Dispatch 6 autonomous subagents
- [x] Agent 1 fixes `main_orchestrator.py` (0DTE hard-stop wiring — legitimate, kept)
- [x] ~~Agent 4 deletes live trading LLM skills and endpoints~~ — **this was a
      runaway, unauthorized action** (cited a nonexistent "Constraint #1"). Found
      and fully reverted in commit `65bc2da9`. See the walkthrough's section 2.
- [x] ~~Agent 3 confirms universe generation logic~~ — **retracted**: the original
      "confirmation" checked the wrong orchestrator (`main.py`, which never calls
      `ForecastingEngine`). Re-investigated in the final pass; genuinely
      unresolved — see the walkthrough's section 4.
- [x] Agent A corrects docs for `earnings_crush` and `dispersion_trading`
      (legitimate, kept)
- [x] Agent B patches `api/pilots_api.py` to add `gamma_scalper` gate status
      (legitimate, kept)
- [x] A separate, later agent introduced a `news_catalyst`
      `validation_strategy_id` fabrication-risk regression (commit `d4b27144`);
      found and reverted in commit `13c1c196`. See the walkthrough's section 3.
- [x] Confirm test suite passing — 537 passed, 0 failed (see walkthrough section 5)
- [x] Prepare PR artifacts — this file, the implementation plan, and the
      walkthrough have all been corrected to reflect what actually happened,
      including the unauthorized deletion/revert and the fabrication
      regression/fix, per explicit operator instruction not to let euphemized or
      false claims survive into the PR.

# Task tracker — Fix fabricated SPY spot price + dead beta fallback

Branch: `fix-delta-hedge-fabricated-spy-spot`
Full detail: [`delta_hedge_fabricated_spy_spot_implementation_plan.md`](delta_hedge_fabricated_spy_spot_implementation_plan.md),
[`delta_hedge_fabricated_spy_spot_walkthrough.md`](delta_hedge_fabricated_spy_spot_walkthrough.md)

- [x] Re-verify both reported bugs against real source (reproduced the
      `compute_beta` `TypeError` live; confirmed `calculate_portfolio_greeks`'s
      `or 500.0` fallback by reading the code)
- [x] Query the operator's live `~/.stockpy_local/quant_platform.db` for
      already-placed `hedge_spy_*` paper orders as evidence of real impact
- [x] Found and scoped in a third, adjacent bug (`main.py`'s dead
      `"executed"`/`"spot_price"` post-hedge log keys)
- [x] `EnterPlanMode` → wrote and got approval for the implementation plan
- [x] `pilots/options_risk.py`: `_resolve_symbol_beta` returns
      `(beta, is_measured)`; dead `compute_beta` tier removed; WARNING logged
      on fallback
- [x] `pilots/options_risk.py`: `calculate_portfolio_greeks` never fabricates
      SPY spot; adds `spy_spot`/`spy_spot_resolved`/`beta_is_estimated`/
      `symbols_with_estimated_beta`
- [x] `pilots/paper_broker.py::get_portfolio_greeks()`: threads a real
      resolved SPY quote
- [x] `main.py`: extracted `_run_automated_delta_hedge_cycle`, resolves ONE
      real SPY quote for both sizing and fill, fails closed on unavailable
      quote, fixes the dead log-key bug
- [x] Tests: `tests/test_options_risk.py` (6 new), `tests/test_pilots_paper_broker.py`
      (2 new), `tests/test_main.py` (3 new) — all green, plus all pre-existing
      tests in these files still pass
- [x] Docs: `docs/architecture/execution.md` bullets updated,
      `docs/known_issues/options_risk_fabricated_spy_spot.md` written,
      `docs/known_issues/README.md` row added, `CLAUDE.md` bullet added
      (auto-mirrored to `AGENTS.md` by the sync hook)
- [x] `ruff check . --select=F821,F822,F823,E9` clean
- [x] Full offline suite (`pytest -m "not network and not slow" --dist loadgroup`):
      11,941 passed, 5 failed, 31 skipped. The 5 failures
      (`tests/test_data_api_chat.py::TestMultiProviderRouting`,
      `tests/test_gemini_live_chat.py::TestLiveChatSession`) are confirmed
      pre-existing and unrelated — reproduced identically on unmodified
      `main` via `git stash`/`git stash pop` (missing `google-genai`/`openai`
      packages in this sandbox, an environment gap, not a code regression)
- [ ] Commit + push branch + open PR (no direct commit to `main`)

# Task Tracker — `iv_history` provenance tracking

Branch: `fix-bootstrap-iv-history-provenance`

- [x] Read `volatility/bootstrap_iv_history.py` and `volatility/iv_engine.py`
      in full; identify all `record_iv`/`get_historical_ivs`/
      `calculate_true_ivr` call sites (`pipeline/production_steps.py:397`,
      `technical_options_engine.py:1069`, `pilots/volatility_surface.py`).
- [x] Add `iv_history.source` column + additive idempotent migration
      (`sqlalchemy.inspect()`-probed, not raw `PRAGMA table_info` —
      dual-backend store).
- [x] `record_iv()` gains keyword-only `source` param, defaults to
      `IV_SOURCE_CHAIN`; both real production call sites left unchanged.
- [x] `bootstrap_iv_history.py` explicitly tags its one write
      `IV_SOURCE_SYNTHETIC_BOOTSTRAP`; module + argparse docstrings
      corrected (no longer claims "historical ATM implied volatilities").
- [x] `get_historical_ivs()` excludes `IV_SOURCE_SYNTHETIC_BOOTSTRAP` by
      default (new `exclude_sources` kwarg); `calculate_true_ivr()`
      docstring updated to document the inherited contract (no logic
      change needed there).
- [x] New test file `tests/test_iv_history_provenance.py`: default tagging,
      explicit synthetic tagging, upsert-overwrites-source, synthetic-
      ground-truth before/after ranking proof, wholly-synthetic-history
      fail-closed-to-NaN, legacy-unknown-not-excluded, migration
      idempotency on a real pre-existing legacy-schema DB file (+ repeated
      construction), fresh-DB-needs-no-migration, bootstrap `main()`
      end-to-end tagging.
- [x] Ran new test file — 13/13 pass.
- [x] Ran all pre-existing IV-history-adjacent test files — all pass
      unchanged (`test_iv_history_no_lookahead.py`, `test_iv_engine.py`,
      `test_options_matrix.py`, `test_engine_context.py`,
      `test_orchestrator_e2e.py`, `test_main_orchestrator.py`,
      `test_volatility_surface.py`).
- [x] Ran full offline suite (`pytest -m "not network"`) — 13,038 passed,
      10 pre-existing failures, all confirmed unrelated to this change
      (sandbox network/filesystem restrictions, local `.env` drift, a
      pre-existing stale committed artifact).
- [x] `ruff check . --select=F821,F822,F823,E9` — clean.
- [x] Docs: new `docs/known_issues/bootstrap_iv_history_provenance_
      fabrication_risk.md`, indexed in `docs/known_issues/README.md`;
      `docs/architecture/signal-engines.md`'s True_IVR paragraph and
      `docs/architecture/validation-and-signals.md`'s bootstrap-script
      one-liner both updated; `docs/test_coverage_analysis.md`'s two
      stale 0%-coverage rows for this file marked closed.
- [x] **Resolved during rebase**: PR #1017 (`extend-backfill-meta-labeling`,
      which first flagged this hazard) merged to `main` while this PR was
      open. Rebased this branch onto the new `main`, resolved a real
      conflict in `docs/known_issues/README.md` (both PRs appended a row
      at the same spot — kept both, mine placed directly after theirs
      since it closes the hazard theirs flagged), and updated
      `docs/known_issues/vrp_premium_selling_no_historical_iv.md`'s own
      "Related, separate hazard" section to say Fixed and point at this
      write-up, closing the gap this tracker originally disclosed as open.
- [x] PR artifacts committed to `.claude/` (this file + implementation
      plan + walkthrough) with scoped names — done as part of this commit.
- [ ] Open PR, self-review full diff, merge per CLAUDE.md's checklist
      (pending — final step before this tracker is fully checked off).

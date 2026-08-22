# Known issue (2026-08-22): cross-sectional `deployable` verdicts silently depended on which random ticker subset happened to download that run

**Status: fixed.** Branch `fix-xsec-universe-coverage-visibility`.

## What happened

An audit of `scripts/refresh_validations.py`'s full 29-strategy validation run found
that `cross_sectional_momentum` and `sector_quality_rank`'s `deployable` verdicts had
swung wildly across dozens of real runs recorded in the platform's durable
`validation_runs` DB table (`validation/validation_history_store.py`) over roughly 36
hours — e.g. `cross_sectional_momentum`: PBO ranging 0.11→0.69, Sharpe 0.68→1.01,
`deployable` flipping True/False repeatedly, **with no code changes in between**.

## Root cause, empirically confirmed (not theorized)

`cross_sectional_momentum` was run three times:

1. Once inside a `--workers 6` full-registry run under heavy concurrent FMP load. That
   run's own log showed `FMP cooldown active for another 299s/300s after 12/15
   consecutive failed requests` and dozens of `Shares-outstanding fetch failed ... FMP
   returned HTTP 429` lines.
2. Twice more in complete isolation — nothing else running on the machine.

All three runs produced **bit-identical output**:
`sharpe=0.9570882384457532, pbo=0.6888888888888889, dsr=0.999988566583595,
max_drawdown=0.2498381690107834`.

This proves the strategy logic itself is fully deterministic. The swings recorded in
the DB history come from *other* concurrent runs (other worktrees/sessions on this
shared machine, all pointed at the same FMP rate limit) hitting FMP's cooldown circuit
breaker at different points, each ending up with a **differently-incomplete universe**.
`_download_closes`/`_download_ohlcv` (`scripts/refresh_validations.py`) correctly and
intentionally drop a ticker that failed to fetch that run rather than fabricate data
(CONSTRAINT #4) — but the consequence, before this fix, was that a cross-sectional
strategy's whole ranking, and hence its whole Sharpe/PBO/DSR/`deployable` verdict,
silently depended on exactly which subset of the ~500-ticker universe happened to
succeed that particular run — with **zero indication of this anywhere in the report**.

`sector_quality_rank` is affected by the identical mechanism (both share
`_XSEC_UNIVERSE_CAPPED`/`_XSEC_UNIVERSE_WIDE`, and `sector_quality_rank` additionally
hits SEC EDGAR directly per ticker — see `docs/architecture/validation-and-signals.md`'s
`_build_sector_quality_rank_adapter` entry — a second, independent source of per-run
data variability under concurrent load). **This fix's `universe_coverage` field tracks
PRICE-data fetch coverage only** (`_download_closes`'s `available` vs. `universe`) — it
does NOT track EDGAR-fundamentals fetch coverage, which remains covered only by this
adapter's own pre-existing, separately-tested degrade-to-NaN handling
(`test_missing_edgar_data_degrades_to_nan_not_fabricated`). Confirmed live during this
fix's own re-validation run: `sector_quality_rank`'s price coverage reported 100/100
while the same run's log recorded a genuine EDGAR fetch timeout for one ticker (BBY,
`CIK 0000764478`) — the two coverage dimensions are independent, and this fix closes
only the price-coverage one. Extending coverage-tracking to EDGAR fundamentals is a
disclosed, out-of-scope follow-up (see "Disclosed follow-up" below).

## What was fixed

The fix makes universe coverage a first-class, visible, and (below a threshold)
**gating** part of every validation report — it does not touch the strategy math, which
was already correct:

- `validation/thresholds.py`: new `MIN_UNIVERSE_COVERAGE_PCT = 0.90`.
- `validation/harness.py`: `ValidationReport` gained `universe_coverage` (optional
  `{"requested", "fetched", "coverage_pct", "missing"}`) and a derived
  `universe_coverage_ok` property, ANDed into `deployable` alongside the existing
  PBO/DSR/Sharpe/MaxDD/stress gates. `to_summary_dict()` surfaces both fields;
  `_render_html_report` threads them into a new "Universe Coverage" card in
  `reports/validation_report_template.html.j2`.
- `scripts/refresh_validations.py`: `_validate_single_strategy` computes the coverage
  dict from data it already derives (`available` vs. `universe`) and passes it to
  `harness.run(universe_coverage=...)`. `_fail_reason` reports a coverage-shortfall
  reason FIRST (it can otherwise shadow every other gate's readout). `_print_summary_table`
  gained a `Coverage` column. The `--json` CLI line gained `universe_coverage_pct`/
  `universe_coverage_ok` per strategy.
- `ValidationHistoryStore.record_run()` (`validation/validation_history_store.py`)
  persists both new fields automatically via its existing full-summary JSON blob — no
  DB migration needed.

## Decision: fail-closed, unconditional, no settings flag

Per this repo's CONSTRAINT #6 convention (the options-selling stress gate fails closed
when never stress-tested; the VRP regime gate fails closed on NaN; the ETF-transmission
multiplier defaults to neutral rather than corrupting the portfolio cap on missing
data), a run whose declared universe was only partially fetched now **forces
`deployable=False`**, regardless of how good the other four numbers look — a confident
verdict computed off a random subset of the universe is not trustworthy.

This gate is deliberately **unconditional** — unlike
`settings.VALIDATION_HARNESS_OOS_GATE_ENABLED` (which changed the computation
methodology for every run and needed an opt-in flag to avoid silently invalidating the
whole registry's recorded numbers before re-verification), this gate only changes the
verdict on a **new, narrow failure mode**. A fully-covered run — the normal case for an
isolated run — is completely unaffected/bit-identical. Making it opt-in would just
perpetuate the exact invisible-failure-mode problem this fix exists to close.

## Prior recorded numbers: treat as unverified-coverage

Every number recorded in `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-21 "Tiered
universe widening" entry for the 7 strategies sharing `_XSEC_UNIVERSE_WIDE`/
`_XSEC_UNIVERSE_CAPPED` (`cross_sectional_momentum`, `relative_strength_xsec`,
`multifactor_lowvol_size`, `macro_regime_pit`, `signal_replay_balanced_blend`,
`lgbm_ranker`, `sector_quality_rank`) predates this fix and carried no coverage
measurement at all — treat all of them as **unverified-coverage** until re-run under
this fix. `cross_sectional_momentum` and `sector_quality_rank` were re-run in isolation
as part of this fix and their clean, coverage-verified numbers are recorded in
`docs/VALIDATION_STRATEGY_FIX_LOG.md`'s 2026-08-22 entry and their own
`docs/signals/<name>.md` files. The other 5 were **not** re-run as part of this change —
out of scope, flagged here as a follow-up rather than silently left unstated.

## Disclosed follow-up — now implemented (2026-08-22, `data/cross_process_throttle.py`)

The immediate, in-scope fix above made the existing behavior *visible* (and gated on
it), without eliminating the underlying concurrency. The cross-worktree coordination
mechanism disclosed here as out-of-scope — "a shared lock file" was the example named —
has since been implemented: `data/cross_process_throttle.py::wait_turn` adds a
`fcntl.flock`-based cross-process spacing throttle on top of (not instead of)
`data/fmp_client.py`'s and `data/edgar_fundamentals.py`'s existing in-process throttles,
so concurrent worktree sessions on this machine now jointly respect the real shared
FMP/SEC request budget instead of each independently believing it owns the full budget.
See `docs/architecture/data-layer.md`'s "Cross-process rate limiting" entry for the
full mechanism and `docs/VALIDATION_STRATEGY_FIX_LOG.md`'s matching 2026-08-22 entry
for the verification. This reduces how often the coverage gate above actually needs to
trip going forward — it does NOT replace the gate, which stays the correct fail-closed
backstop for whatever residual variance a rate limiter alone cannot eliminate (a
single-process run that itself has a flaky network connection, a genuinely down FMP/SEC
endpoint, etc.).

**Second, separate follow-up: EDGAR-fundamentals coverage tracking.** `sector_quality_rank`
hits SEC EDGAR directly per ticker in addition to the shared FMP price fetch this fix
tracks — confirmed live during this fix's own re-validation run (a genuine EDGAR fetch
timeout for BBY, `CIK 0000764478`, while price coverage reported 100/100). This fix's
`universe_coverage` field does not track EDGAR-fundamentals fetch success; a ticker's
missing EDGAR data currently degrades that ticker's quality-factor SCORE to NaN (already
correct, pre-existing behavior — CONSTRAINT #4) without being visible as a coverage
shortfall the way a missing PRICE ticker now is. Extending coverage-tracking to this
second dimension for `sector_quality_rank` specifically is disclosed here, not attempted.

## Tests

`tests/test_universe_coverage_gate.py` (`ValidationReport`-level: `None`-coverage
backward compatibility, full-vs-60%-coverage deployability divergence on otherwise
identical PBO/DSR/Sharpe/MaxDD, the `MIN_UNIVERSE_COVERAGE_PCT` boundary, `to_summary_dict()`
surfacing, and a real — non-mocked — Jinja2 render of the new HTML report section).
`tests/test_refresh_validations.py` (`TestUniverseCoverageDispatch`,
`TestFailReasonUniverseCoverage`, `TestPrintSummaryTableCoverageColumn`, and the
`--json` CLI output).

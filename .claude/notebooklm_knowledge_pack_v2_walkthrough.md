# NotebookLM Modular Multi-Source Knowledge Pack (v2 rebuild) — Walkthrough

## Context

A prior branch (`integrate_google_notebook_pipeline`) attempted this same
"modular multi-source knowledge pack" feature in isolation while `main`
independently merged and shipped its own, simpler, already-audited
single-file version of `scripts/export_notebooklm.py` (PRs #965/#968/#971/
#986/#991). A 6-agent audit of that abandoned branch found it stale
relative to `main` plus 7 real bugs in its own code (one CRITICAL data
fabrication, one CRITICAL crash-cascade, two HIGH, three MEDIUM/LOW). This
rebuild starts fresh from current `main`'s already-shipped
`scripts/export_notebooklm.py` / `pilots/portfolio.py` and re-implements the
modular idea on top of it via 8 parallel agents (5 generators + shared
helpers/driver + tests + docs), fixing every one of those 7 bugs along the
way, then integrated and verified by hand.

## What was built

`scripts/export_notebooklm.py` was extended from a single-file exporter
(`output/notebooklm_source.md`, 3 sections: Macro Context, Current
Portfolio, Active Pilot Follows) into a dual-mode exporter that also writes
5 separate, topic-scoped Markdown documents under `output/notebooklm/`:

| File | Covers |
|---|---|
| `01_macro_and_regime.md` | VIX / 10Y-2Y spread / HY OAS + HMM regime state, macro kill switch |
| `02_portfolio_and_greeks.md` | Live brokerage positions/equity/buying power + paper-engine net Greeks |
| `03_strategy_signals_and_picks.md` | Daily BUY/SELL/HOLD signals, multifactor z-scores, sizing guardrails, pilot follows |
| `04_trade_journal_and_ledger.md` | Closed-trade history & KPIs (FIFO-reconstructed) |
| `05_options_directives_and_matrix.md` | Options premium-selling directives, fundamental health, news |

New CLI flags: `--output-dir`, `--modular-only`, `--consolidated-only`,
`--section {macro,portfolio,signals,trades,options}`. Default (no flags)
writes both the consolidated file and all 5 modular files.

## Bugs fixed (found in the abandoned prior attempt, fixed here)

1. **Portfolio-Greeks fabrication (CRITICAL).** The abandoned attempt's
   `02_portfolio_and_greeks.md` generator called
   `pilots.options_risk.calculate_portfolio_greeks()` directly with no
   arguments. That function's real signature is
   `calculate_portfolio_greeks(positions=None, store=None,
   market_provider=None, spy_spot=None)` — called bare, `positions` and
   `store` are both `None`, so it silently falls into its own
   "no positions" branch and returns an all-zero portfolio regardless of
   what the operator's real account actually holds. Fixed by delegating to
   the existing, already-correct `pilots.paper_broker.get_portfolio_greeks()`
   — the same wrapper the Paper Broker screen's backend uses, which
   constructs a real `PaperAccountStore(readonly=True)`, resolves a real SPY
   spot quote, and threads both into
   `calculate_portfolio_greeks(store=..., spy_spot=...)`.

2. **No crash isolation between generators (CRITICAL).** The abandoned
   attempt ran all 5 modular generators in a flat sequence with no
   per-generator exception boundary in `build_export()`, so one generator's
   uncaught failure aborted the entire script (exit code 1) and silently
   prevented every generator scheduled after it from being written. Fixed
   by wrapping each of the 5 modular generator calls in its own isolated
   `try/except` inside `build_export()`: a failing section logs a warning
   and writes an honest one-line fallback file for itself
   (`# {title}\n\n_This source failed to generate this run: {exc}_\n`)
   while every sibling section still generates and writes normally. The
   consolidated file's write is deliberately left outside this per-section
   wrapping (see the code comment on that line) since it already has its
   own internal per-subsection try/excepts and its outer exception is meant
   to propagate, matching `tests/test_export_notebooklm.py::TestAtomicWrite`'s
   pre-existing contract.

3. **Dead `True_IVR`/`IVR_Proxy` fallback (HIGH).** `d.get("True_IVR",
   d.get("IVR_Proxy"))` never fell back to `IVR_Proxy` in practice, because
   `dict.get(key, default)`'s `default` only fires when the key is ABSENT,
   not when it's present with value `None` — and
   `technical_options_engine.py::build_premium_directive` always
   initializes BOTH keys (defaulting to `nan`/`null`), so `True_IVR` is
   always present, just usually null when the options-chain-derived IV rank
   feature is off (the common case). Fixed via a dedicated
   `_resolve_ivr(directive)` helper that explicitly checks for
   `None`/NaN before falling back.

4. **Unescaped `|` corrupting Markdown tables (HIGH).** Free-text fields
   rendered into table cells (buy/sell range strings like `"Trim @ $13.30 |
   Stop @ $13.07"`, news headlines) can contain a literal `|`, the Markdown
   table column delimiter — an unescaped one silently shifts every
   subsequent column in that row with no error raised. Fixed via a new
   `_md_escape(value, default="N/A") -> str` helper (escapes `|` → `\|`,
   collapses embedded newlines to spaces, and renders a `None` value as an
   explicit fallback string rather than a blank cell) applied wherever a
   free-text value is placed in a table cell or bullet.

5. **`_is_missing` didn't catch `pandas.NA`/`pandas.NaT` (MEDIUM).** The
   NaN check used everywhere (`isinstance(value, float) and value !=
   value`) doesn't catch `pandas.NA`/`pandas.NaT` (neither is a `float`
   instance), so a value of either type would `str()`-format as a literal
   `"<NA>"`/`"NaT"` string instead of the honest "N/A" every other
   missing-data path produces. Fixed via a shared `_is_missing()` helper
   using guarded `pandas.isna()`, used by `_fmt_money`/`_fmt_num`/`_fmt_pct`.

6. **0-trades vs. fetch-failed conflation (MEDIUM).** The trade-journal
   generator rendered "no closed trade history" identically whether the
   durable broker-fills store had never been ingested at all
   (`trade_history_view()`'s `available: False`) or had genuinely been
   ingested and found zero closed trades (`available: True, n_trades: 0`)
   — two structurally different situations. Fixed to render three distinct
   messages based on `available`/`n_trades`.

7. **Privacy disclosure gap (docs only, MEDIUM).** The abandoned attempt's
   own `docs/GOOGLE_NOTEBOOK_INTEGRATION.md` draft never disclosed that
   uploading the exported files to NotebookLM sends real brokerage account
   data (equity, positions, P&L, Greeks, options strikes/premiums) to
   Google's cloud infrastructure, and never mentioned NotebookLM's own
   data-handling posture. Fixed by writing `docs/GOOGLE_NOTEBOOK_INTEGRATION.md`
   fresh with a prominent, early "Privacy & Data Handling" section.

## Additional bugs found and fixed during integration (not present in any
## single agent's isolated slice — only visible once all 8 were assembled)

8. **Wrong `output_dir` routing in the driver (CRITICAL, integration-only).**
   `build_export()`'s modular dispatch loop passed `modular_dir`
   (`out_dir / "notebooklm"`, where the RENDERED `.md` OUTPUT files get
   written) as the `output_dir` INPUT parameter to every generator —
   meaning `generate_signals_picks_source`/`generate_trade_journal_source`/
   `generate_options_matrix_source` (which read `state_snapshot.json`/
   `options_matrix.json` from that directory) were silently looking in the
   wrong place and always degraded to "unavailable", even with real
   upstream data present. Caught by 5 failing integration tests once all 8
   agents' pieces were assembled and run together (none of the 8 slices
   could have caught this alone, since each tested its own generator by
   calling it directly with the correct directory, never through the full
   `build_export()` driver against a real on-disk fixture). Fixed by
   passing `out_dir` (the real `settings.OUTPUT_DIR`) to every generator,
   independently of `modular_dir` (which stays write-target-only).

9. **`_fmt_money` regression during assembly (integration-only).** While
   merging the 8 slices' independently-written `_fmt_money`/`_fmt_num`
   definitions into one canonical copy, an extra `try/except
   (TypeError, ValueError): return "N/A"` was added that none of the
   individual agents' versions had. This silently broke 2 of the
   pre-existing 23 tests (`TestPartialAppendProtection`), whose whole point
   is that a corrupted value (e.g. a hand-edited DB row with a string where
   a number belongs) must RAISE so the section's own buffer-then-commit
   try/except drops the WHOLE section honestly, rather than quietly
   rendering "N/A" for just that one field next to otherwise-good data.
   Reverted to the original (non-catching) behavior; `_fmt_pct` keeps its
   own deliberate internal try/except (a different, intentional design
   choice made by the agent that built it, for a different call-site
   pattern).
10. **Paper-account vs. live-account juxtaposition (integration-only,
    disclosure).** `pilots.paper_broker.get_portfolio_greeks()` reads
    `PaperAccountStore` (the platform's simulated paper-trading book),
    while the Account Liquidity summary directly above it in the same file
    reads the real brokerage account snapshot — two genuinely different,
    unrelated position sets. Live-verified on this machine's own data: the
    real account showed 25 real positions and $42,973.70 equity while the
    paper-account Greeks section showed all-zero (a real, empty paper
    book) — accurate for each store individually, but juxtaposed with no
    explanation this could read as one account's numbers. Added an inline
    note under the Greeks heading clarifying the two are separate books.

Item 8 in particular is the reason this rebuild deliberately does NOT trust
any single agent's self-reported "all tests passed" — every one of the 8
agents' individual test runs were real and honestly reported, and none of
them were wrong for what they tested; the bug only existed in how the
pieces fit together, which only running the FULL integrated test suite
(and a real end-to-end script invocation) could reveal.

## Corrections to this walkthrough's own first draft

This file's first draft (written before the code was assembled/tested)
described a "`--section` + `--consolidated-only` conflict (clear CLI
error)" — no such validation was actually built. In practice, passing both
flags together makes `--section` win (exactly one modular file is written,
the consolidated file is skipped) with no error — a debatable but
non-crashing edge case for an unusual flag combination not covered by
`docs/GOOGLE_NOTEBOOK_INTEGRATION.md`'s documented usage patterns; left
as-is rather than added as new, untested scope.

## How it was actually verified

- `python3 -m pytest tests/test_export_notebooklm.py -v` → **97 passed**
  (29 pre-existing consolidated-export tests, unchanged behavior, still
  green; 68 new tests covering all 10 bugs above, individual
  happy/degraded-path coverage for each of the 5 new generators, and
  `build_export()`/`main()` CLI-flag filtering).
- `ruff check scripts/export_notebooklm.py tests/test_export_notebooklm.py
  --select=F821,F822,F823,E9` (the genuine-bug lint gate this repo's
  `/verify` skill actually enforces) → clean, zero findings. A full
  default-ruleset `ruff check` surfaces only pre-existing-style
  `BLE001` (blind-except) findings, consistent with this repo's existing
  CONSTRAINT #6 dead-letter-resilience convention of broad `except
  Exception` at fail-closed boundaries — not new issues.
- Live end-to-end run: `python3 scripts/export_notebooklm.py --output-dir
  <tmp>` against this machine's real local `quant_platform.db` — exit code
  0, all 6 files written. Real, non-fabricated output confirmed by
  inspection: a real 25-position brokerage account ($42,973.70 equity), a
  real 173-trade FIFO-reconstructed trade journal (57.80% win rate, 2.26
  profit factor), and an honest all-"unavailable"/empty degrade for the
  macro-regime/signals/options-matrix files (this sandbox has no
  `state_snapshot.json`/`options_matrix.json` since no live pipeline cycle
  has run here) — no crashes, no literal `"None"`/`"nan"`/`"NaT"` leakage,
  no malformed tables. `--help` confirmed side-effect-free (required for
  `cli_introspect/capture.py`'s manifest-introspection harness — see the
  code comment on `main()`).

## Files touched

- `scripts/export_notebooklm.py` (rewritten: 5 new generators, shared
  formatting helpers, the fixed driver, real CLI flags)
- `tests/test_export_notebooklm.py` (extended: 68 new tests, one
  pre-existing test updated for the new multi-file default — see its own
  docstring)
- `docs/GOOGLE_NOTEBOOK_INTEGRATION.md` (new)
- `.claude/notebooklm_knowledge_pack_v2_implementation_plan.md` (new)
- `.claude/notebooklm_knowledge_pack_v2_task.md` (new)
- `.claude/notebooklm_knowledge_pack_v2_walkthrough.md` (new, this file)
- `CLAUDE.md` changelog bullet (inserted)
- `docs/README.md` one-line index addition (inserted)

# NotebookLM Modular Multi-Source Knowledge Pack (v2 rebuild)

Rebuild of `scripts/export_notebooklm.py` to add a second, modular output
mode alongside the existing single-file `output/notebooklm_source.md`
export: 5 separate, topic-scoped Markdown documents under
`output/notebooklm/`, so an operator uploading sources to Google NotebookLM
gets better-focused per-topic grounding (and better Audio Overview
generation) than one large document, without losing the existing
consolidated file for anyone who prefers it.

This is a **rebuild**, not a from-scratch design: a prior attempt at this
exact modular-export feature was started and abandoned before landing. This
plan explicitly enumerates the bugs found in that abandoned attempt and how
each is fixed here, per CLAUDE.md's requirement that a prior attempt's
disclosed gaps be treated as findings to close, not silently dropped.

## What's being added

- **5 new modular generator functions**, one per topic, each writing its own
  file under `output/notebooklm/` (or `<--output-dir>/notebooklm/`):
  - `01_macro_and_regime.md` — macro/regime indicators + HMM state.
  - `02_portfolio_and_greeks.md` — positions + portfolio net Greeks.
  - `03_strategy_signals_and_picks.md` — daily BUY/SELL/HOLD signals,
    multifactor z-scores, sizing guardrail telemetry, active pilot follows.
  - `04_trade_journal_and_ledger.md` — closed-trade history & KPIs.
  - `05_options_directives_and_matrix.md` — options premium-selling
    directives, fundamental health, news headlines.
- **A fixed driver** (`build_export`/`main`) that supports generating the
  consolidated file, the modular set, or both, and dispatches to one or all
  5 modular generators with per-generator crash isolation (see Bug 2 below).
- **New CLI flags**: `--output-dir <path>` (override `settings.OUTPUT_DIR`
  as the base directory for both output modes), `--modular-only` (skip the
  consolidated `notebooklm_source.md`), `--consolidated-only` (skip the 5
  modular files), `--section {macro,portfolio,signals,trades,options}`
  (generate exactly one modular file; implies modular generation scoped to
  that section, mutually exclusive with `--consolidated-only`).
- **A new `_md_escape(text: str) -> str` helper** used by every generator
  wherever a free-text field is interpolated into a Markdown table cell
  (buy/sell ranges, news headlines, strategy names) — see Bug 3.
- **The privacy-disclosure fix**: a fresh `docs/GOOGLE_NOTEBOOK_INTEGRATION.md`
  with a prominent, early "Privacy & Data Handling" section — see Bug 4.

## Bugs found in the prior abandoned attempt, and how each is fixed

### Bug 1 — Portfolio-Greeks generator fabricated an all-zero portfolio

The abandoned attempt's `02_portfolio_and_greeks.md` generator called
`pilots.options_risk.calculate_portfolio_greeks()` directly, with **no
arguments**. Reading that function's real signature
(`calculate_portfolio_greeks(positions=None, store=None,
market_provider=None, spy_spot=None)`), calling it bare means `positions is
None` and `store is None`, so it falls straight to its own
"no positions" branch and returns an honest-looking, but **completely
fabricated**, all-zero portfolio (`net_delta_shares: 0.0`, `net_gamma: 0.0`,
etc.) regardless of what the operator's real paper account actually holds —
a silent CONSTRAINT #4 violation, because the zeros are indistinguishable
from "you genuinely have no open positions."

**Fix**: the generator now calls `pilots.paper_broker.get_portfolio_greeks()`
— the existing, already-correct no-argument wrapper that constructs a real
`PaperAccountStore(readonly=True)`, resolves a real SPY spot quote via
`pilots.price_provider`, and threads both into
`calculate_portfolio_greeks(store=..., spy_spot=...)` before returning. This
is the same function the Paper Broker screen's backend already uses, so the
export now reports the operator's real net Greeks, not a fabricated zero
book.

### Bug 2 — No crash isolation between generators

The abandoned attempt's driver called all 5 modular generators (plus the
consolidated sections) in a flat sequence with no per-generator exception
boundary. A single unhandled exception in, say, the options-directives
generator (`05`) would propagate up and abort the whole script — meaning a
transient options-chain fetch failure could silently prevent
`01_macro_and_regime.md` through `04_trade_journal_and_ledger.md` from ever
being written, even though none of those four depend on options data at
all.

**Fix**: each of the 5 modular generators is wrapped in its own isolated
`try/except` at the driver level (the consolidated file's existing internal
per-section `try/except`s were already correct and are left untouched — see
the code comment on why the consolidated file's OWN write is deliberately
NOT re-wrapped at the driver level). A generator that raises logs a
`WARNING` and its file is still written, with an honest one-line fallback
body (`# {title}\n\n_This source failed to generate this run: {exc}_\n`) —
deliberately never omitted entirely, since an operator seeing a totally
missing file can't tell "this section crashed" from "this feature was never
enabled" — and every other generator still runs to completion with real
data.

### Bug 3 — Unescaped `|` in free-text table cells corrupted Markdown tables

Several fields interpolated into table-shaped Markdown output are genuinely
free text with no character restrictions: buy/sell range strings (e.g. an
operator-visible `"$100.00 - $105.00 | Stop $98.00"`-shaped tactical note)
and news headlines (which can contain a literal `|` character, e.g. a
headline quoting a ratio or written with a pipe as informal punctuation).
Since `|` is the Markdown table column delimiter, an unescaped `|` inside a
cell value silently shifts every column to its right in that row, corrupting
the table structure NotebookLM (and any human reader) parses — with no
error raised anywhere, so this would have shipped silently.

**Fix**: a new `_md_escape(text: str) -> str` helper (escaping `|` →
`\|`, and collapsing embedded newlines to spaces so a multi-line headline
can't break a table row either) is applied to every free-text value before
it's placed in a table cell, across all generators that render tables
(signals/picks, trade journal, options directives).

### Bug 4 — Disclosed privacy gap in the (never-shipped) integration doc

The abandoned attempt's own `docs/GOOGLE_NOTEBOOK_INTEGRATION.md` draft
never stated, anywhere, that manually uploading these files to NotebookLM
sends real brokerage account data — equity, buying power, position cost
basis/market value/P&L, portfolio Greeks, closed-trade P&L, live options
strikes/premiums — to Google's cloud infrastructure, and never mentioned
NotebookLM's own data-handling posture or warned against a shared/public
notebook.

**Fix**: `docs/GOOGLE_NOTEBOOK_INTEGRATION.md` is written fresh (it does not
exist on `main` yet) with a **prominent, early** "Privacy & Data Handling"
section — not buried at the bottom — explicitly naming every category of
sensitive data the exported files can contain, stating plainly that
uploading sends this data to Google's cloud, recommending the notebook stay
private/unshared, and explicitly telling the operator to verify Google's
current NotebookLM data-usage/retention terms themselves rather than this
repo asserting a third party's policy on their behalf.

## Documentation update step

Per CLAUDE.md's requirement that every Implementation Plan scope its own
doc updates:

1. **`docs/GOOGLE_NOTEBOOK_INTEGRATION.md`** — new file (does not exist on
   `main`). Full usage reference, the Privacy & Data Handling section (Bug 4
   fix), the 5-file schema table, example NotebookLM prompts, and known
   limitations.
2. **`CLAUDE.md`** (mirrored to `AGENTS.md` by the existing
   `sync_agent_docs.sh` hook) — one new changelog bullet describing the
   modular knowledge pack, the new CLI flags, and the 3 code-level bug fixes
   above (Bugs 1-3; Bug 4 is a docs-only fix, named in the bullet but not a
   code change). Bullet text is provided standalone in this PR's artifact
   set for the lead reviewer to insert (this rebuild does not hand-edit
   `CLAUDE.md`/`AGENTS.md` directly, per this repo's multi-agent-parallel
   convention for this task).
3. **`docs/README.md`** — one new row under its "Integration references"
   table (alongside `FMP_INTEGRATION.md` and `JULES_INTEGRATION.md`)
   pointing at the new `docs/GOOGLE_NOTEBOOK_INTEGRATION.md`.

## Verification plan

- `tests/test_export_notebooklm.py` extended with: one happy-path test per
  modular generator (real-shaped fixture data in, correct file written);
  a degraded-path test per generator (its own data source raises →
  file omitted/degraded, siblings still written in full — proving Bug 2's
  fix); a regression test asserting the portfolio-Greeks file's numbers come
  from `pilots.paper_broker.get_portfolio_greeks()` and are NOT all-zero
  when a fixture position is present (proving Bug 1's fix); a
  `_md_escape` unit test covering a `|`-containing headline/range string and
  an embedded-newline string (proving Bug 3's fix); and CLI-flag tests for
  `--output-dir`, `--modular-only`, `--consolidated-only`, and each
  `--section` value.
- **Actual result**: `python3 -m pytest tests/test_export_notebooklm.py -v`
  → **97 passed** (29 pre-existing + 68 new). Integration (merging all 8
  parallel slices into one file) surfaced 2 additional bugs no single
  slice's isolated testing could see — a wrong `output_dir` routed to the
  3 JSON-reading generators, and a `_fmt_money` regression introduced while
  reconciling 8 independently-written copies into one canonical helper —
  both found via the full assembled suite and a real end-to-end script run,
  both fixed; see the walkthrough (`.claude/notebooklm_knowledge_pack_v2_walkthrough.md`)
  for full detail. `ruff check --select=F821,F822,F823,E9` clean;
  `tests/test_command_manifest_freshness.py`/`tests/test_build_command_manifest.py`
  green after regenerating `cli_introspect/command_manifest.json` (the
  script now reports 4 real CLI options instead of 0).

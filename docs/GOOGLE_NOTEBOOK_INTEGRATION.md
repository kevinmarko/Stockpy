# Google NotebookLM Integration

`scripts/export_notebooklm.py` formats the platform's current state — macro
regime, portfolio positions and Greeks, daily signals, closed-trade history,
and options directives — into Markdown documents an operator can manually
upload into [Google NotebookLM](https://notebooklm.google.com), a third-party
cloud research/note-taking tool from Google. NotebookLM grounds its AI
analysis and "Audio Overview" (podcast-style) generation strictly in the
sources you give it, so the problem this script solves is getting NotebookLM
a fresh, accurate, well-scoped picture of *this specific account's* current
state instead of the operator hand-copying numbers into it or leaving it to
answer from general knowledge about markets.

This is a **read-only, advisory, out-of-band export** — see "Known
limitations" below. There is no live connection between this platform and
NotebookLM; the operator moves files manually.

## Privacy & Data Handling

**Read this before uploading anything to NotebookLM.**

The files this script generates contain real, current, sensitive financial
data pulled directly from your platform's local databases and live
brokerage connection. Depending on which sections/files you generate, this
includes:

- **Total account equity and buying power**
- **Every open position's symbol, quantity, cost basis, market value, and
  unrealized P&L**
- **Portfolio net Greeks** (net delta, gamma, theta, vega, beta-weighted
  SPY delta) — a direct read on your current risk exposure
- **Every closed trade's entry price, exit price, holding period, and
  realized P&L** (the full trade journal/ledger)
- **Live options-selling directives** — specific strikes, premiums, and
  delta targets the platform is currently recommending
- Daily BUY/SELL/HOLD signals, multifactor scores, and sizing guardrails
  for your tracked universe

**Uploading these files to NotebookLM sends this real financial data to
Google's cloud infrastructure.** That is true of any file you add as a
NotebookLM source — this is simply naming, explicitly, what these
particular files contain so there's no ambiguity about what you're sending.

Before uploading:

- **Keep the notebook private.** Do not share it, do not add it to a
  shared/team notebook, and do not enable any public-link sharing NotebookLM
  offers for a notebook containing these files.
- **This repository cannot tell you Google's current data-retention,
  training-use, or sharing policy for NotebookLM sources** — that policy is
  Google's to state and is subject to change independently of this codebase.
  **Check Google's current NotebookLM terms and data-usage documentation
  yourself** before uploading anything you consider sensitive, and re-check
  periodically since third-party terms can change without this doc being
  updated in lockstep.
- If you are not comfortable with a piece of data (e.g. exact position
  sizes, or the options directives) leaving your machine, use `--section`
  (below) to generate only the files that don't contain it, or don't
  generate/upload that file at all.

Nothing about this script changes based on your comfort level here — it
always writes plain Markdown to your local `output/` directory (see
"Usage"). The privacy decision is entirely at the manual-upload step, which
this script never performs on your behalf.

## Usage

```bash
# Generate everything: the single consolidated file AND all 5 modular files
python scripts/export_notebooklm.py

# Only the 5 modular files under output/notebooklm/ (skip the consolidated file)
python scripts/export_notebooklm.py --modular-only

# Only the single consolidated output/notebooklm_source.md (skip the modular files)
python scripts/export_notebooklm.py --consolidated-only

# Only one modular section (still writes just that one file under output/notebooklm/)
python scripts/export_notebooklm.py --section macro
python scripts/export_notebooklm.py --section portfolio
python scripts/export_notebooklm.py --section signals
python scripts/export_notebooklm.py --section trades
python scripts/export_notebooklm.py --section options

# Write everything under a custom directory instead of settings.OUTPUT_DIR
python scripts/export_notebooklm.py --output-dir /path/to/custom/dir
```

`--section` implies modular generation of that one file only (equivalent to
`--modular-only` scoped to a single section). Combining it with
`--consolidated-only` is not validated as an error — `--section` simply
wins (exactly one modular file is written, the consolidated file is
skipped) — so avoid passing both together rather than relying on any
particular precedence.

### Where output lands

By default, output is written under `settings.OUTPUT_DIR`, which (per this
repo's `LOCAL_DATA_ROOT` convention — see `settings.py`'s `OUTPUT_DIR` field
and `docs/architecture/data-layer.md`) resolves to `<LOCAL_DATA_ROOT>/output`
when not explicitly overridden, i.e. **`~/.stockpy_local/output/` by
default** on a fresh install — *not* a path inside the git checkout. This is
deliberate: it's the same machine-global, worktree-independent location
every other generated artifact (models, caches, `quant_platform.db`) lives
under, and it keeps these files out of any git worktree by construction.

- Consolidated file: `<OUTPUT_DIR>/notebooklm_source.md`
- Modular files: `<OUTPUT_DIR>/notebooklm/01_macro_and_regime.md`,
  `02_portfolio_and_greeks.md`, `03_strategy_signals_and_picks.md`,
  `04_trade_journal_and_ledger.md`, `05_options_directives_and_matrix.md`
- `--output-dir <path>` overrides the base directory for both, i.e. modular
  files land under `<path>/notebooklm/`.

### Getting the files into NotebookLM

**This is a manual, out-of-band step.** This repository has no Google Drive
API integration, no OAuth flow, and no auto-upload capability for
NotebookLM — nothing here pushes files to Google on your behalf. To use
them:

1. Run the script (above) to (re)generate the files.
2. Go to [notebooklm.google.com](https://notebooklm.google.com) and open (or
   create) a notebook.
3. Use "Add source" → "Upload" and select the generated `.md` file(s) from
   your `output/` directory (or `output/notebooklm/` for the modular set).
4. Re-run the steps above and re-upload whenever you want NotebookLM's
   sources to reflect a fresher platform state — see "Known limitations."

NotebookLM supports up to 50 sources per notebook; the 5 modular files (or
1 consolidated file) fit comfortably alongside any other sources you've
already added.

## The 5 modular knowledge-pack files

Each file is scoped to one topic so NotebookLM's per-source grounding and
Audio Overview generation stay focused, rather than diluting attention
across one giant document. Every file degrades independently — a fetch
failure inside one file's generator never blocks or corrupts any of the
others (see "Known limitations").

| File | Covers |
|---|---|
| `01_macro_and_regime.md` | Macro/regime indicators — VIX, 10Y-2Y yield spread, High Yield OAS — plus the current HMM (Hidden Markov Model) regime state and risk-on probability where available. |
| `02_portfolio_and_greeks.md` | Current open positions (symbol, quantity, avg cost, market value), total equity and buying power from your live brokerage account, PLUS portfolio-level net Greeks (net delta, gamma, daily theta, vega, beta-weighted SPY delta) computed over the platform's separate paper-trading engine positions — the same aggregation the Paper Broker screen uses. The file itself notes this distinction inline; the two position sets are not the same account. |
| `03_strategy_signals_and_picks.md` | Daily per-symbol BUY/SELL/HOLD signals for the tracked universe, multifactor cross-sectional z-scores (Value/Quality/LowVol/Size/Composite), position-sizing guardrail telemetry (Kelly Target, whether/why a position was capped), and the operator's active pilot follows. |
| `04_trade_journal_and_ledger.md` | Closed-trade history and derived KPIs (win rate, average trade, profit factor, etc.) from the durable trade ledger — entry/exit price, holding period, and realized P&L per closed trade. |
| `05_options_directives_and_matrix.md` | Current options premium-selling directives (strategy, strikes, premiums, delta targets) from the strategy pricing matrix, alongside relevant fundamental-health context and recent news headlines for the underlying names. |

## Recommended NotebookLM prompts

Once multiple sources are uploaded to the same notebook, NotebookLM can
reason across them. A few starting points:

- *"Given today's macro regime and my open positions' net Greeks, what's my
  biggest tail risk this week?"*
- *"Cross-reference my active BUY signals against my current holdings —
  which recommended names am I not yet holding, and which of my held
  positions have flipped to a SELL or HOLD signal?"*
- *"Looking at my trade journal, which of my past losing trades happened in
  a macro regime similar to what's described in today's macro context —
  and is there a pattern in how those trades were sized?"*
- *"Based on today's options directives and the current VIX/credit-event
  flags, which premium-selling recommendations are gated off right now, and
  why?"*

## Known limitations

- **Read-only / advisory only.** This export never places an order, never
  touches a broker or execution surface, and has no write path back into
  the platform. It is a one-way snapshot for an operator (or NotebookLM's
  AI) to read.
- **No auto-sync.** NotebookLM does not automatically see updates. To keep
  its sources current, re-run the script and manually re-upload (or replace
  the existing source) in the NotebookLM UI — there's no scheduled job or
  webhook doing this for you today.
- **Exported files are never committed.** `output/` (and everything under
  it, including `output/notebooklm/`) is gitignored, matching the rest of
  this repo's convention for `.env`/local state — these files stay on the
  machine that generated them unless you manually move or upload them
  yourself.

## Honesty guarantees (CONSTRAINT #4 / #6)

Any field the platform cannot currently measure (a missing quote, an
unavailable macro series, no trade history yet, etc.) renders as `N/A` in
the output rather than a fabricated value like `0` or `0.0`. Each of the 5
modular generators (and each section of the consolidated file) runs inside
its own isolated try/except at the driver level — a failure fetching one
category of data (e.g. macro data) degrades only that file/section to an
honest "unavailable" message and never prevents the other files/sections
from being generated with real data.

# Stockpy Codebase Audit Framework

A structured prompt + rubric for auditing the **InvestYo Quant Platform ("Stockpy")**
codebase — the automated quantitative-analysis pipeline that fetches market/macro
data, computes indicators, runs multi-horizon forecasts, backtests strategies,
persists signals, and publishes advisory recommendations.

This document is the **human/agent-facing companion** to
[`stockpy_codebase_auditor.py`](stockpy_codebase_auditor.py), the executable
static auditor. The script mechanizes the parts of this rubric that can be checked
statically; the prose below covers the judgement-heavy areas a reviewer (human or
AI) must reason about directly.

> **Guiding principle — report honestly.** State what you actually find, with file
> and line references. Do not assert a defect exists because a checklist expects
> one. A clean result for any area is a valid, valuable outcome. Never fabricate a
> "known issue" to fill a section.

---

## How to use this framework

1. **Run the static auditor first** to get the mechanical findings and a triage
   baseline:
   ```bash
   python stockpy_codebase_auditor.py --root . --json audit_report.json
   ```
2. **Work through the ten areas below**, using the auditor's JSON as evidence and
   reading the flagged files directly. The auditor points; you confirm.
3. **Classify every confirmed finding** by the severity model, cite the exact
   location, and propose a concrete remediation.
4. **Cross-check against the "Codebase conventions" section** — many would-be
   findings are actually deliberate, documented design decisions (advisory
   quarantine, NaN-not-0.0 discipline, fraction/percent scale rules). Verify
   against `CLAUDE.md` before reporting a convention as a bug.

### Severity model

| Severity | Meaning | Examples |
|----------|---------|----------|
| 🔴 CRITICAL | Exploitable / data-integrity / financial-safety failure | Hardcoded credential, committed `.env`, order code escaping the advisory quarantine, DSN with embedded password |
| 🟠 HIGH | Architectural or correctness defect likely to cause incidents | Cross-package circular import with real init-time side effects, conflicting position-sizing formulas, lookahead bias in a forecaster |
| 🟡 MEDIUM | Robustness / config / maintainability gap | Unguarded network I/O, undeclared env var, fabricated `0.0` metric where NaN is the convention |
| 🔵 LOW | Style / documentation / hygiene | Missing docstring, thin type-hint coverage, orphaned module |
| ⚪ INFO | Observation, no action required | — |

---

## Area 1 — Architecture & Dependencies

**Goal:** the flat "Engine" architecture stays legible and acyclic.

- Detect **circular imports**. The codebase deliberately uses *lazy* (in-function)
  imports to break cycles (`HistoricalStore`, `macro_engine`, `processing_engine`).
  A statically-detected cycle is a finding only if it is a *module-top* import cycle
  with real import-time side effects — confirm the mitigation before flagging HIGH.
- Detect **orphaned modules** (imported by nothing, not an entry point). Distinguish
  genuine dead code from legitimate launchers/CLIs/API services/standalone scripts.
- Confirm every engine is a **top-level module imported directly** (no hidden package
  indirection) and that data crossing into calculation code goes through the DTOs in
  `dto_models.py`, never raw dicts.
- Confirm **fetching is decoupled from calculation** (all fetches via `IDataProvider`
  / `MarketDataProvider`).

**Report:** cycle membership (full path), orphan list with a keep/remove judgement,
any DTO-bypass in calc code.

## Area 2 — Security

**Goal:** no secret material in the tree, ever.

- **Hardcoded secrets:** FRED keys (32 hex), Alpaca/Finnhub keys, bearer tokens,
  Slack/Discord webhook URLs, Postgres DSNs with `user:pass@host`, private-key
  blocks, generic `api_key = "…"` / `password = "…"` literals. Distinguish real
  secrets from `os.environ`/`settings.` references, `Field(...)` declarations,
  test fixtures, and `…EXAMPLE`/placeholder strings.
- **`.env` must be gitignored** and never committed. Verify `.gitignore` covers
  `.env`, `credentials.json`, and `*.db`.
- **Secret handling in logs:** confirm credentials/tokens are never logged (only key
  *names*/lengths per `gui/env_io.py` convention), and DSNs never log the full URL
  (`db_config.py` logs backend name only).
- **GUI env-write safety:** any `.env` write must route through `gui.env_io`
  (`ALLOWED_KEYS` allowlist + `SECRET_KEYS` denylist). A new GUI-writable setting
  that is a credential is a CRITICAL finding.

**Report:** each match with file:line, whether it is a true positive, and the
rotation/remediation step.

## Area 3 — Configuration

**Goal:** every runtime knob is declared, documented, and consistent.

- **Undeclared env vars:** any `os.environ[...]` / `getenv(...)` read whose name is
  absent from `settings.Settings` and `.env.example`.
- **Drift:** settings fields with no `.env.example` entry (and vice-versa).
- **Cache-TTL sanity:** quote (30 s), bars (300 s), fundamentals (6 h), macro (12 h)
  — confirm defaults are coherent and documented.
- **Fail-open vs fail-closed posture:** read tokens (`STATE_API_TOKEN`) fail *open*;
  command tokens (`ORCHESTRATOR_DAEMON_TOKEN`, `FOLLOW_API_TOKEN`) and
  `BROKERAGE_CONNECT_ENABLED` fail *closed*. Verify no command surface silently
  fails open.

**Report:** undeclared vars, drift table, any posture inversion.

## Area 4 — Data Pipeline

**Goal:** fetches are resilient and provider-agnostic; no cache cross-contamination.

- **Provider fallback:** Alpaca → yfinance quote/bar selection; Yahoo-computed →
  yfinance `.info` fundamentals fallback. Confirm each hop degrades gracefully.
- **Dead-letter resilience (CONSTRAINT #6):** per-ticker loops wrap each symbol in
  try/except so one bad symbol can't abort a batch. Network/file I/O sits inside
  try/except and degrades to a sentinel, never raises.
- **Cache isolation:** in-process caches key correctly by `(symbol, lookback)` and
  return defensive copies; stale/negative responses are cached deliberately, not by
  accident. Confirm no cache leakage across symbols.
- **Scale rules (do NOT "fix"):** `dividendYield` is a **fraction**, `debtToEquity`
  is **×100 percent**, GARCH vol is **annualized** (÷√252 for daily). Flag only a
  genuine violation of these documented rules.

**Report:** any unguarded I/O, fallback that raises instead of degrading, or cache
key collision.

## Area 5 — Strategy Layer

**Goal:** one signal contract, one sizing source of truth.

- **Signal-module consistency:** every signal implements `SignalModule` and scores in
  `[-1, +1]` (except the documented long-only `rsi2_mean_reversion` ∈ `[0, 1]`).
  Aggregation is vectorized weighted-sum via `SignalAggregator`.
- **Kelly single source of truth:** position sizing must come from
  `StrategyEngine._calculate_kelly_sizing` → `sizing.kelly` / `sizing.vol_target`.
  Any `win_prob = f(score/sortino/edge_ratio)` formula outside `sizing/` is a
  **conflicting Kelly implementation** — HIGH finding.
- **Regime gating:** `is_active_in_regime` suppression is enforced centrally in the
  aggregator, not per-module self-zeroing.
- **Meta-labeling:** `aggregate()` returns a 6-tuple; any unpack of 5 elements is a
  bug.

**Report:** divergent sizing formulas, non-conforming signal modules, incorrect
tuple unpacks.

## Area 6 — Backtesting Integrity

**Goal:** results are trustworthy; no lookahead, no survivorship blindness.

- **Lookahead bias:** scalers fit on train partition only; indicators/forecasters
  pass the perturbation tests in `tests/test_*_lookahead.py`. CNN-LSTM sequence
  creation and scaler fitting must be strictly train-only.
- **Survivorship bias:** every backtest prints the `universe_engine` warning/report.
- **Cost realism:** backtests use `TieredCostModel` (SEC/FINRA/spread/slippage), not
  static assumptions; cost scales with turnover.
- **Deployability gate (honest):** PBO < 0.5 AND DSR > 0.95 AND net-of-cost Sharpe
  > 0.5 AND MaxDD < 30%; options-selling adds the tail-stress gate. Thresholds are
  **never loosened** to force a green check — a strategy that fails a gate must
  report `deployable = False`.

**Report:** any scaler/indicator lookahead, missing survivorship warning, loosened
gate, or fabricated backtest metric.

## Area 7 — Robinhood Integration & Execution Safety

**Goal:** the advisory quarantine holds.

- **Order-verb quarantine:** no module outside `execution/` may define
  `submit_order` / `buy_order` / `sell_order` / `place_*` (mirrors
  `tests/test_pipeline_smoke.py::TestNoOrderFunctions`). A violation is HIGH/CRITICAL.
- **Advisory-only posture:** `data/robinhood_portfolio.py` and
  `data/robinhood_orders.py` are **read-only**; the MCP server exposes no
  order-submission code; the gated `execution/queue_builder.py` writes a dry-run
  queue and never contacts a broker.
- **MFA / credential handling:** `RH_MFA_SECRET` via `pyotp`; interactive fallback
  only when appropriate; `verify_credentials` never falls back to interactive
  prompting and never persists on a failed verify; credentials never logged.
- **Kill switch & risk gate:** `GlobalKillSwitch` checked before any order path;
  `PreTradeRiskGate.run_all` short-circuits at first failure.

**Report:** any order verb outside `execution/`, credential leak, or bypassed gate.

## Area 8 — Error Handling & Observability

**Goal:** the pipeline survives every partial failure and is diagnosable.

- **Dead-letter everywhere:** per-symbol failures append to an errors list and never
  abort the run; broker/DB outages degrade to offline stubs, not crashes.
- **No bare `except: pass`** that swallows a real error silently without logging.
- **Alerting:** channel failures (Discord/Slack/email/ntfy) are caught and logged,
  never propagate.
- **Telemetry parity:** both state-snapshot writers emit the same keys so switching
  orchestrators doesn't blank the GUI.

**Report:** unguarded I/O (from the auditor), silent excepts, alert paths that can
crash the pipeline.

## Area 9 — Code Quality

**Goal:** the code stays readable and matches repo conventions.

- **Docstrings:** module-level docstring on every non-`__init__` module; public
  functions documented.
- **Type hints:** the codebase is type-annotated by convention — flag modules with
  thin coverage.
- **Dead code:** unused imports, unreachable branches, orphaned helpers.
- **Vectorization:** no per-row `.iterrows()`/Python loops in technical/fundamental
  math (CONSTRAINT — vectorized pandas/numpy only).

**Report:** doc/type gaps by module, any row-wise loop in calc code.

## Area 10 — Known-Issue Heuristics

Targeted probes for the bug *classes* this platform has historically been sensitive
to. Each is a **pointer, not a verdict** — confirm by reading the code.

- **Fabricated metrics (CONSTRAINT #4):** a metric returning literal `0.0` where the
  convention is `NaN` for "not computable" (MFE/MAE, ratios, equity-curve stats).
- **CNN-LSTM / scaler leakage:** `fit_transform` on a full series before a train/test
  split.
- **Conflicting Kelly implementations:** win-probability derived from
  score/sortino/edge outside the sizing SSOT (see Area 5).
- **FRED-key / credential hardcoding:** see Area 2.
- **Backtest isolation:** missing survivorship warning or non-`TieredCostModel` cost.

**Report:** each heuristic hit with the confirming (or refuting) evidence.

---

## Codebase conventions (verify against these before flagging)

These are **deliberate, documented** decisions — do not report them as defects:

- **Advisory-only quarantine:** no order-submission code outside `execution/`; the
  whole platform is read-only advisory while `ADVISORY_ONLY=true`.
- **NaN, never fabricated 0.0** for any metric that cannot be computed (CONSTRAINT #4).
- **Dead-letter resilience** (CONSTRAINT #6): degrade to a sentinel, never raise, in
  fetch/persist paths.
- **Scale rules:** `dividendYield` fraction; `debtToEquity` ×100; GARCH vol annualized.
- **Lazy imports** to break `HistoricalStore`/engine circular dependencies.
- **DTO boundary:** raw dicts never enter calculation code.
- **Secrets** flow only through `os.environ`/`settings`; GUI writes only through the
  `gui.env_io` allowlist.
- **`.db` files are per-machine runtime state**, gitignored, never committed.

When in doubt, consult `CLAUDE.md` (the single source of architectural truth) and
`docs/FEATURE_TIER_HISTORY.md` (the dated changelog) before writing a finding.

---

## Output format

For each confirmed finding, produce:

```
[SEVERITY] Area N — <check name>
  Location : <file>:<line>
  Finding  : <what is wrong, concretely>
  Evidence : <the offending code / the convention it violates>
  Fix      : <specific remediation>
```

Close with a severity-tallied summary and an explicit statement of which areas came
back **clean** — a clean area is a result worth stating, not an omission.

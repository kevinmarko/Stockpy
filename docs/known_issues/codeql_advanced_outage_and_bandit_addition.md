# Known issue (resolved): CodeQL Advanced blocked on a GitHub-side outage; Bandit added as an independent SAST supplement

**Status: resolved.** Two independent problems, investigated together on
2026-08-17. The first was a real, fixable repo misconfiguration (fixed).
The second was a live GitHub.com incident affecting the code-scanning
SARIF-ingestion API specifically, external to this repo, that resolved on
its own. The response to both was to add `bandit` (Python SAST) as a
second, independent CI job that reports via plain exit code and never
depends on GitHub's code-scanning ingestion API at all — see
`.github/workflows/ci.yml`'s `bandit` job.

## What happened

`.github/workflows/codeql.yml` ("CodeQL Advanced") had failed on **every**
run since ~2026-07-20 with:

> CodeQL analyses from advanced configurations cannot be processed when the
> default setup is enabled

This was a genuine repo-config conflict: GitHub's own auto "default setup"
code scanning and this repo's custom "advanced" workflow file were both
enabled simultaneously, and GitHub refuses to ingest SARIF results in that
state. That conflict cleared on its own between 2026-08-12 and 2026-08-17
(`code-scanning/default-setup` now reports `"not-configured"`), and a
subsequent run completed real analysis successfully (950/950 Python files
scanned).

Once that config conflict was gone, every further CI run for the rest of
2026-08-17 kept failing anyway — but at a different, later step: CodeQL's
own analysis completed cleanly and exported SARIF, and the run only failed
on `Uploading results` / `Encountered an error while trying to determine
feature enablement`, both returning `"No server is currently available to
service your request."` from GitHub's own API. Cross-checked against
`githubstatus.com`, which reported a live "Partial System Outage" (Git
Operations, Issues, Copilot all degraded) with the same timestamp window.
This was **not** a code defect and not fixable from the repo — GitHub's own
Actions runners worked the entire time; only the code-scanning-specific
ingestion endpoint was affected. It resolved as the incident cleared.

## Why this justified adding a second, independent tool rather than just waiting

GitHub's outage specifically took down the code-scanning *ingestion* API,
not GitHub Actions itself. A tool that reports via plain CI exit code —
never uploading SARIF to that specific endpoint — is structurally immune to
this exact failure mode, regardless of which SAST tool it is. `bandit` was
picked over adding an org-level policy or waiting: it needs no external
API dependency at all (`pip install bandit && bandit -r .`), covers this
codebase's dominant language (~950 Python files vs. `webapp/`'s TypeScript,
already covered by the separate `webapp` CI job's own tooling), and is
explicitly a *supplement* to CodeQL, not a replacement — CodeQL is kept
running (free for this public repo, and its false-positive baseline was
independently reviewed and fixed in the same work session; see below).

## Bandit baseline triage (2026-08-17)

A first run at medium+ severity / medium+ confidence
(`bandit -r . -x './tests,./webapp,./.venv,./node_modules,./gui' -ll -ii`)
surfaced 44 findings across 5 rule IDs. Every one was reviewed individually
— none were skipped by rule ID wholesale, since a blanket per-rule skip
would blind the scanner to a genuinely new, real instance of the same
bug class introduced later. Disposition:

| Rule | Count | Verdict |
|---|---|---|
| `B608` (hardcoded SQL expressions) | 14 | False positive — every instance interpolates only a fixed-length `?`-placeholder count string or one of a small set of hardcoded literal clause fragments (`date_clause`, `limit_clause`, column-name constants); every real value is bound through parameterized `?` placeholders. Concentrated in `data/historical_store.py` (11) and `pilots/observability.py` (3). |
| `B301` (unsafe pickle deserialization) | 10 | False positive — every call loads either a local ML-model artifact this same pipeline trained and wrote (`ml/models/base.py`, `ml/lgbm_ranker.py`, `ml/meta_labeling.py`, `ml/options_meta_labeler.py`, `forecasting_engine.py`'s Prophet/scaler cache), a local operator credential file (`data/robinhood_session.py`'s `~/.tokens/robinhood.pickle`), or same-machine same-trust-boundary subprocess IPC (`cnn_lstm_process_pool.py`/`cnn_lstm_worker.py`'s stdin/stdout pipe between a parent process and the worker it itself spawned). Never externally-supplied data. |
| `B310` (urlopen with unaudited scheme) | 15 | False positive — every call's URL is either an operator-set config value (`ALERT_WEBHOOK_URL`, `ALERT_SLACK_WEBHOOK_URL`, `ALERT_NTFY_TOPIC`, `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL`, `OPTIONS_ALERT_WEBHOOK_URL`, `PROMPT_REGISTRY_URL`), a fixed SEC EDGAR API endpoint, or (`investyo_mcp_server.py`'s 5 instances) a hardcoded `http://localhost:5173`/`http://localhost:8602` scheme+host with only a path suffix appended — the scheme itself (Bandit's actual concern: `file://` disclosure) is never attacker-influenceable in any of the 15. |
| `B102` (use of `exec`) | 3 | 1 false positive (`Gravity AI Review Suite.py`'s `exec("e.symbol = 'MSFT'")` — a hardcoded literal string testing that a frozen dataclass rejects mutation, zero external input). 2 are real, already-sandboxed, adversarially-tested code paths: `validation/autonomous_backtest_runner.py::compile_and_extract_strategy` and `llm/research_copilot.py`'s signal-synthesis executor both AST-validate the candidate code first (`ASTSecurityValidator`/dunder-access/forbidden-import/forbidden-call rejection) and `exec()` only into a stripped-down restricted namespace (`create_safe_globals()`/`safe_builtins` with `eval`/`exec`/`open`/etc. removed) — not naive/unguarded `exec()`. |
| `B108` (hardcoded `/tmp` path) | 2 | False positive — both are `Gravity AI Review Suite.py` self-test fixtures pointing at a deliberately-nonexistent path (`/tmp/__nonexistent_dl__.json`, `/tmp/__no_gravity__.json`) to verify a missing-file-returns-empty contract; nothing is ever written to either path. |

Every false positive is marked `# nosec BXXX` at the exact flagged line
(not a blanket `skips:` config entry), with an inline comment explaining
why. The two real sandboxed `exec()` paths are marked the same way, with
the comment documenting *why* the pattern is safe (the validation/
restriction that runs before it) rather than asserting it's a false
positive. The `bandit` CI job runs at the same `-ll -ii` (medium+
severity, medium+ confidence) threshold as this baseline and is **exit-code
blocking** from day one — a fresh, unreviewed finding fails the build,
rather than this being a report-only step that nobody watches.

## Why medium+/medium+ and not the full ruleset

A first pass at Bandit's default (unrestricted) severity/confidence
surfaced 232 low-severity findings in addition to the 44 above — the same
"a fresh tool's full ruleset drowns a first PR in noise nobody will
triage" problem this repo already solved for `ruff` (CI's `Lint (ruff)`
step is deliberately scoped to `F821,F822,F823,E9`, not the ~1200
pre-existing style violations against the full default ruleset). Widening
the Bandit severity/confidence threshold is a legitimate, separate future
follow-up — not something this addition silently forecloses.

## What was actually done as a result

- No changes needed to `.github/workflows/codeql.yml` itself — its earlier
  default-setup/advanced-setup conflict had already cleared by the time
  this was investigated; CodeQL Advanced keeps running as before.
- Added a new `bandit` job to `.github/workflows/ci.yml` (medium+/medium+,
  exit-code blocking, excludes `tests/` and `webapp/`).
- Added `# nosec BXXX` suppression comments (with justification) at all 44
  baseline finding sites, across `data/historical_store.py`,
  `pilots/observability.py`, `cnn_lstm_process_pool.py`,
  `cnn_lstm_worker.py`, `data/robinhood_session.py`, `forecasting_engine.py`,
  `ml/lgbm_ranker.py`, `ml/meta_labeling.py`, `ml/models/base.py`,
  `ml/options_meta_labeler.py`, `alerting.py`, `alerting_mcp/notifier.py`,
  `data/edgar_fundamentals.py`, `data/etf_holdings.py`,
  `execution/order_manager.py`, `observability/alerts.py`,
  `pilots/options_alerts.py`, `prompt_registry/store.py`,
  `investyo_mcp_server.py`, `validation/autonomous_backtest_runner.py`,
  `llm/research_copilot.py`, and `Gravity AI Review Suite.py`.
- Verified: `bandit -r . -x './tests,./webapp,./.venv,./node_modules,./gui'
  -ll -ii` reports zero findings post-fix; `ruff --select=F821,F822,F823,E9`
  clean on every touched file; every touched Python module imports cleanly;
  the full set of existing tests covering these modules (830+ tests across
  `test_historical_store*.py`, `test_pilots_observability.py`,
  `test_cnn_lstm_*.py`, `test_robinhood_session.py`,
  `test_forecasting_engine.py`, `test_lgbm_ranker_*.py`,
  `test_meta_labeler_uplift.py`, `test_model_interface.py`,
  `test_alerting*.py`, `test_edgar_fundamentals.py`, `test_etf_holdings.py`,
  `test_order_manager_*.py`, `test_options_alerts.py`,
  `test_options_meta_labeler.py`, `test_prompt_registry_store.py`,
  `test_research_copilot.py`, `test_autonomous_backtest_runner.py`,
  `test_decision_log.py`) all still pass unchanged.

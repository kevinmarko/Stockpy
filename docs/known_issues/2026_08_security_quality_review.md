# Security & quality review (2026-08-05) — no open GitHub issues/PRs existed

**Status: findings fixed, documented below.** Triggered by an operator request to
"look into all the security and quality issues" via GitHub. `list_issues` /
`list_pull_requests` against `kevinmarko/Stockpy` returned zero open items (the
one existing closed issue, #381 CNN-LSTM deadlock, was already resolved — see
[`cnn_lstm_tf_deadlock.md`](cnn_lstm_tf_deadlock.md)) and this GitHub MCP server
exposes no Dependabot/code-scanning-alert listing tool, so this was a fresh,
from-scratch audit rather than triage of an existing report: `npm audit` /
`pip-audit` / `ruff` (CI's exact invocation) / `bandit` (not part of CI) run
against a real Python 3.12 venv and a fresh `npm install`, plus a manual sweep
of `webapp/src` for XSS-prone patterns. Three real, low-severity findings were
fixed; everything else checked out clean or was a confirmed false positive
(documented below so a future pass doesn't re-litigate the same bandit output).

## 1. `fast-uri@3.1.4` — GHSA-7p8r-x3mc-p8w7 (HIGH, npm audit)

`npm audit` on a fresh `webapp/` install flagged `fast-uri` (host confusion via
backslash authority introducer, CVSS 7.5), pulled in transitively via
`vite-plugin-pwa@1.3.0` → `workbox-build@7.4.1` → `ajv@8.20.0` → `fast-uri`.
Same low-exposure profile as the already-documented
[`vite_plugin_pwa_workbox_dev_chain_unfixable.md`](vite_plugin_pwa_workbox_dev_chain_unfixable.md)
chain — `ajv`/`fast-uri` here are build-time-only dependencies of
`workbox-build`'s service-worker generation (`vite build`), never shipped to
`dist/` or executed in an end user's browser. Fixed with a plain
`npm audit fix` (no `overrides` entry needed this time — `ajv`'s own
`package.json` range already permits the patched `fast-uri@3.1.5`, unlike the
`ejs`/`jake` chain in the sibling doc). `npm audit` now reports 0 findings.
Verified: `tsc --noEmit`, `npm run build` (service worker generated
identically — `precache 16 entries (1767.09 KiB)`, `dist/sw.js`/
`dist/workbox-*.js` present), and `npm run test` (104 files / 1259 tests, see
§3 below for the one file that needed an unrelated fix first) all pass.

## 2. SEC N-PORT XML parsed with stdlib `ElementTree` instead of `defusedxml` (bandit B314, MEDIUM)

`data/etf_holdings.py`'s two XML entry points (`extract_nport_series_id`,
`parse_nport_holdings`) parsed `xml_bytes` fetched live over the network from
SEC EDGAR with `xml.etree.ElementTree.fromstring`. Stdlib `ElementTree`
(backed by expat) does **not** resolve *external* entities, so classic
XXE-via-SSRF/file-disclosure was never reachable here — but it **does** expand
internal DTD entities by default, so a compromised or spoofed upstream
response could still trigger a "billion laughs"-style memory/CPU DoS.
Real-world exposure is low (`ETF_HOLDINGS_ENABLED` defaults `False`; SEC's
`.gov` domain is TLS-verified; the feature already has its own throttle/
circuit-breaker per `docs/architecture/data-layer.md`), but this is a
zero-behavior-change, defense-in-depth swap: `defusedxml.ElementTree.fromstring`
is API-compatible (returns ordinary `xml.etree.ElementTree.Element` instances,
confirmed via `isinstance()`), forbids DTDs/entities outright, and raises an
exception the existing `except Exception` at both call sites already degrades
to `None`/`[]` for — matching this module's CONSTRAINT #6 ("never raises").
Added `defusedxml>=0.7.1` to `requirements.txt`. Verified:
`tests/test_etf_holdings.py` (41/41 passed, including the malformed-XML
degrade-to-`[]`/`None` cases at the parser's existing edge-case coverage).

## 3. Three Jinja2 `Environment`/`Template` calls without `autoescape` (bandit B701, HIGH ×2 + one unflagged `Template()` site)

`validation/harness.py`'s `_render_html_report`/`_render_cpcv_report`
(`jinja2.Environment(loader=loader)`) and `diagnostics_and_visuals.py`'s
`generate_html_report` (`jinja2.Template(HTML_REPORT_TEMPLATE)` — the plain
`Template` shortcut also defaults to `autoescape=False`, but bandit's B701
check only pattern-matches `jinja2.Environment(...)` calls, not `Template()`,
so this third site wasn't in bandit's own output; found by grepping every
`jinja2` import in the repo for consistency once the first two turned up) all
rendered HTML reports without escaping. None of the three checked-in
`.j2`/inline templates use a `|safe` filter anywhere (grepped), so nothing
relies on unescaped raw-HTML injection — `autoescape=True` is a pure
hardening change with zero rendering-behavior difference for the numeric/
short-string values these templates interpolate (`"%.2f"|format(...)` output
contains no HTML-special characters either way). Without it, a
`STRATEGY_REGISTRY` name, a stress-scenario error string, or an
operator-controlled ticker/watchlist entry containing `<`/`&` could break the
rendered report's markup — low practical severity for a single-operator local
tool, but a real, free, correct fix. `{{ distribution|list | tojson }}`
(embedding a JSON array into an inline `<script>` block in
`cpcv_report.html.j2`) is unaffected either way — `tojson`'s output is
independently escape-safe regardless of the `Environment`'s `autoescape`
setting. Verified: 156 tests across `tests/test_html_report.py`,
`tests/test_operator_ergonomics.py`, `tests/test_validation_history.py`, and
every `tests/test_harness_*.py`/`tests/test_diagnostics_*.py` file all pass
unchanged. `bandit -r . -x '...'` before/after: 2 HIGH findings → 0.

## Confirmed false positives / accepted low-risk patterns (no code change)

Reviewed and NOT changed — bandit flags these conservatively by pattern, not
by actually tracing whether the interpolated value is attacker-controlled:

- **B608 "hardcoded_sql_expressions" (12 sites, `data/historical_store.py` +
  `gui/panels/observability.py`)** — every site builds a dynamic `IN (...)`
  clause or an optional `WHERE`/`ORDER`/`LIMIT` fragment via an f-string, but
  the fragment itself is always either a fixed `",".join("?" for _ in items)`
  placeholder string or a hardcoded literal chosen from a small closed set of
  branches (e.g. `date_clause = " AND as_of_date <= ?" if as_of else ""`) —
  every actual value crosses the query boundary through a bound `?`
  parameter in the accompanying `tuple(params)`/`cursor.execute(sql, params)`
  call, never string-concatenated in. Standard, safe SQLite dynamic-IN-clause
  construction, not injectable.
- **B301 "pickle" (9 sites: `data/robinhood_session.py`,
  `forecasting_engine.py`, `cnn_lstm_process_pool.py`, `ml/lgbm_ranker.py`,
  `ml/meta_labeling.py`, `ml/models/base.py`)** — every `pickle.load` reads a
  file this same application previously wrote to its own local cache/model
  directory (session-token cache, cached ML models/scalers), never a
  network-supplied or otherwise externally-controlled path or byte stream. An
  attacker who could plant a malicious pickle in one of these paths would
  already need local filesystem write access, at which point pickle
  deserialization isn't the weakest link. Migrating off pickle for local
  model persistence is a real but much larger architecture change, out of
  scope for this pass.
- **B310 "urllib.request.urlopen" (14 sites: `desktop/net_util.py`,
  `execution/order_manager.py`, `gui/daemon_client.py`,
  `observability/alerts.py`, `prompt_registry/store.py`)** — every URL is
  either a hardcoded `http://127.0.0.1:<port>` loopback call to the app's own
  daemon, or `settings.ALERT_WEBHOOK_URL`/`PROMPT_REGISTRY_URL`, an
  operator-set `.env` value — never per-request attacker-suppliable input, so
  this isn't the SSRF-via-user-input pattern the check exists to catch.
- **B105 "hardcoded_password_string" (2 sites)** — both are non-secret string
  constants bandit's name-matching heuristic misfires on: a Reddit OAuth
  *endpoint URL* (`_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"`
  in `data/sentiment_sources.py`) and a signal-direction literal
  (`_BUY_TOKEN = "BUY"` in `scripts/snapshot_diff.py`).
- **B607 "start_process_with_partial_path" (3 sites: `investyo_mcp_server.py`
  running `pytest`, `scripts/preflight_check.py`/
  `scripts/measure_settings_census.py` running `git`)** — dev/ops tooling
  invoked locally, not reachable from untrusted/remote input; PATH-hijack
  risk requires an attacker who can already modify the operator's `$PATH`.
- **B110/B112/B603 (try/except/pass, try/except/continue, subprocess without
  `shell=True`) — 107 sites total** — this codebase's documented
  dead-letter-resilience convention (CONSTRAINT #6: "never raises", per-ticker
  try/except so one bad symbol never aborts a batch) intentionally produces
  broad, silent-continue exception handling throughout the pipeline; `shell=True`
  is never used anywhere in the repo (confirmed via a separate grep) — B603's
  own recommended-safe pattern is what's already in use everywhere, bandit
  flags it as a reminder to review args, not because the code is wrong.

`pip-audit` (against a real `python3.12 -m venv` + `pip install -r
requirements.txt`, matching `.github/workflows/ci.yml`'s `test` job exactly)
reported **no known vulnerabilities**. `ruff check . --select=F821,F822,F823,E9`
(CI's exact lint invocation) reported **all checks passed**.

**Honesty note on the full-suite verification run**: the sandboxed review
environment used for this pass has only 4 CPUs (vs. CI's `ubuntu-latest`
runner), and `pytest -n auto --dist loadgroup` (CI's exact `test` job
invocation) crashed there twice with `[gwN] node down: Not properly
terminated` / `INTERNALERROR> KeyError: <WorkerController ...>` — an
xdist worker dying mid-run, at a *different* completion percentage each
time (38% with 4 workers, 15% with 2 workers) and with zero real
assertion failures logged either time (3562 and 1373 tests passed
respectively before each crash). Different crash points across reruns,
combined with zero real failures, point to a resource-constrained-sandbox
artifact rather than a reproducible regression from this PR's 4 changed
files (none of which touch multiprocessing/threading infrastructure).
This is corroborated by: every test file that actually exercises the
changed modules passing 100% in an isolated, non-xdist run (`tests/
test_etf_holdings.py` 41/41; `tests/test_html_report.py` +
`test_operator_ergonomics.py` + `test_validation_history.py` + every
`tests/test_harness_*.py`/`test_diagnostics_*.py` file, 156/156); a full
`--collect-only` pass across all 9213 tests completing with zero import
errors; and the webapp's own full suite (104 files / 1259 tests, no
xdist) passing cleanly. The real, resourced CI run on PR #608 is the
authoritative signal for the full suite.

## 4. Unrelated quality bug found + fixed while verifying fix #1: `Models.test.tsx` fixture time bomb

Running the full webapp suite after the `fast-uri` bump (an unrelated,
build-time-only dependency — confirmed not imported anywhere under
`webapp/src`) surfaced 2 failing tests in
`src/screens/Models.test.tsx`("the deployability filter buttons narrow the
rendered list"), reproduced identically on the pre-fix lockfile via
`git stash`, confirming it predates and is unrelated to the dependency bump.

Root cause: `webapp/src/api/mock.ts`'s `MODELS` fixture computed
`needs_retrain` as `daysSinceTrained(<hardcoded calendar date>) >=
MODEL_RETRAIN_WINDOW_DAYS` (30), where `daysSinceTrained` measures against
`Date.now()`. Two of the three dated fixture rows (`lgbm_ranker`,
`meta_labeler_timeseries_momentum`) were hardcoded to `"2026-07-06"` —
intentionally "fresh" (`needs_retrain: false`) when the fixture was written.
By 2026-08-05 that date is exactly 30 days old, crossing the retrain window
and silently flipping `needs_retrain` to `true` for both — breaking the
test's "exactly one stale row" assertion purely from the passage of real
time, with no code change involved. Fixed by computing `trained_date` as an
offset from `Date.now()` (`isoDaysAgo(6)` for the two fresh models,
`isoDaysAgo(45)` for the intentionally-stale one) instead of a fixed calendar
string, so the fixture's fresh/stale split can no longer rot. Verified: full
webapp suite (104 files / 1259 tests) passes; `tsc --noEmit` and `npm run
build` unaffected.

## 5. GitHub CodeQL, Workflow & Exception Sanitization Audit (2026-08-13)

A comprehensive audit of all 51 historical and open GitHub Security alerts (CodeQL, Secret Scanning, Dependabot) and Actions workflows identified four real remediation areas, two broken workflow files, and modernized false-positive suppressions.

### Full CodeQL Alert Ledger (Alerts #1 through #51 Accounting)

| Alert ID(s) | Rule / Tool | File Location | Status / Resolution |
| :--- | :--- | :--- | :--- |
| **#1, #2** | `actions/missing-workflow-permissions` | `.github/workflows/ci.yml` | **Fixed** in prior CI hardening. |
| **#3, #4** | `py/bind-socket-all-network-interfaces` | `desktop/net_util.py`, `tests/test_net_util.py` | **Fixed** in daemon networking hardening. |
| **#5** | `py/weak-sensitive-data-hashing` | `settings.py:51` | **False Positive** — Fingerprint check: SHA-256 is used solely to match the known leaked FRED key hash to detect reuse, not password hashing. |
| **#6, #7, #8** | `actions/missing-workflow-permissions` | `.github/workflows/{neuralegion,python-package-conda,makefile}.yml` | **Fixed** (workflows deleted in PR #721). |
| **#9** | `py/stack-trace-exposure` | `api/pilots_api.py` | **Fixed** in earlier pilots API error handling pass. |
| **#10** | — | — | *Non-existent / skipped ID in GitHub sequence.* |
| **#11** | `py/command-line-injection` (Critical) | `gui/orchestrator_runner.py:772` | **Fixed** — Added calendar date validation via `datetime.date.fromisoformat` and strategy name whitelist validation (`^[a-zA-Z0-9][a-zA-Z0-9_-]*$`) preventing CLI argument/flag injection. Verified `subprocess.Popen` uses argument list (`shell=False`). |
| **#12, #13, #14** | `py/path-injection` (High) | `prompt_registry/cache.py:168-207` | **Fixed** — Hardened `_sanitize_id` to `[a-zA-Z0-9_-]` and added directory confinement (`path.resolve().is_relative_to(base.resolve())`). |
| **#15** | `py/stack-trace-exposure` (Medium) | `api/pilots_api.py` via `pilots/prompt_registry.py:276` | **Fixed** — Replaced raw `str(exc)` in returned dict with `"Resolution failed: internal error"`; full error logged to server. |
| **#16** | — | — | *Non-existent / skipped ID.* |
| **#17, #18, #19, #20** | `py/overly-large-range` (Medium) | `data/emoji_lexicon.py:57` | **False Positive / Benign** — Intentional sentiment net over broad Unicode emoji blocks across planes. |
| **#21, #22** | `py/path-injection` (High) | `ml/forecast_backfill.py` | **Dismissed** as false positives in GitHub Security UI. |
| **#23–#32** | — | — | *Non-existent / skipped IDs.* |
| **#33–#41** | `py/clear-text-logging/storage-sensitive-data` (High) | `scripts/measure_settings_census.py:1224-1720` | **False Positive / Suppressions Modernized** — Script logs setting field *names*, not values. Updated deprecated `# lgtm[...]` tags to `# codeql[...]`. |
| **#42–#45** | — | — | *Non-existent / skipped IDs.* |
| **#46, #47** | `py/stack-trace-exposure` (Medium) | `api/control_api.py:816`, `api/pilots_api.py:3589` via `pilots/run_status.py:372` | **Fixed** — Replaced raw `str(exc)` on OSError with `"Unable to read crontab schedule"`; detailed error logged to server. |
| **#48** | `py/path-injection` (High) | `ml/forecast_backfill.py` | **Fixed** in earlier backfill hardening. |
| **#49** | `actions/missing-workflow-permissions` (Medium) | `.github/workflows/deploy_mcp_vm.yml` | **Fixed** — Broken workflow deleted (see below). |
| **#50** | `actions/missing-workflow-permissions` (Medium) | `.github/workflows/defender-for-devops.yml` | **Fixed** (workflow deleted in PR #721). |
| **#51** | `actions/missing-workflow-permissions` (Medium) | `.github/workflows/python-package.yml` | **Fixed** (workflow deleted in PR #722). |

### CI Workflow Cleanup & Tooling Trade-off Note

1. **Deleted `.github/workflows/deploy_mcp_vm.yml`**:
   - Contained placeholder values (`projects/123456789/...`, `my-project.iam.gserviceaccount.com`), lacked `permissions` block, and failed on every push to main.
2. **Deleted `.github/workflows/codacy.yml`**:
   - Lacked `CODACY_PROJECT_TOKEN`, ran 45 minutes on every PR/push, and crashed with Scala runtime errors.
   - **Trade-off Note:** CodeQL is a security/SAST scanner — it does not perform general code-quality or PEP8 style linting. `ruff check` in `.github/workflows/ci.yml` is currently scoped to `--select=F821,F822,F823,E9` (undefined-name and syntax-error checks only, not general style/quality linting), and there is no `.pre-commit-config.yaml` or lint step in `./setup.sh` in this repository. Codacy's general code-quality/style coverage (including the TS/JS webapp code it also scanned) is therefore not currently replaced by an equivalent local or CI gate — this is an accepted, currently-uncovered gap traded for removing the 45m unauthenticated Codacy bottleneck, not a like-for-like replacement.

### Repo-Wide Exception Sweep

A full codebase sweep across `api/` for unredacted `str(exc)` or `f"...{exc}..."` returns confirmed that all `api/` endpoint error handlers route through `api/_redact.py` (`redact_line`), with zero raw exception disclosures remaining in that layer. `pilots/run_status.py` and `pilots/prompt_registry.py` fix the same alert class (#15, #46, #47) independently, with hand-written generic replacement strings rather than by importing `redact_line` — `pilots/` is kept dependency-light of `api/`/FastAPI by design (enforced by `tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light`), so this is an intentionally separate, not-yet-consolidated fix, not a `redact_line` call site. (Note: `api/_redact.py` does not define a `RedactingJSONResponse` class — only `redact_line`, `_get_active_secret_values`, and `install_redacting_exception_handler`.)

### Secret Scanning Status (Alert #1)

- Leaked service account credentials in historical commit `afa761030a9814329492a7cf7e8eb983cdabef8c` (`credentials.json` from 2026-07-17) are removed from HEAD and ignored in `.gitignore`.
- **Action:** Key rotation on Google Cloud Console renders the key inert, allowing Alert #1 to be marked resolved in the GitHub Security tab. An optional history rewrite (`git filter-repo` / BFG) can be scheduled separately to purge the commit blob without blocking feature PR merges.

Verified with 32 unit regression tests in `tests/test_security_audit_fixes.py`.

## 6. Alert #91 (2026-08-27 follow-up) — `py/command-line-injection` in `launch_train_meta_labelers`

A fresh CodeQL scan of `main` (commit `2deb0365`, "Global job-status
visibility in the Pilots PWA" — [PR #917](https://github.com/kevinmarko/Stockpy/pull/917))
opened a new alert of the same rule already triaged as alert #11 above:
[alert #91](https://github.com/kevinmarko/Stockpy/security/code-scanning/91),
flagging `gui/orchestrator_runner.py`'s `launch_train_meta_labelers`
(`subprocess.Popen(cmd, ...)`), with the taint source traced to
`api/control_api.py`'s `POST /jobs` handler — an operator (or anyone holding
the command token) can `POST /jobs` with `job_type="train_meta"` and an
arbitrary `params.get("signal")`, which `api/_jobs.py:254` passes straight
into `launch_train_meta_labelers(signal=...)`.

Reviewed: **false positive, already mitigated**, same class as alert #11.
`launch_train_meta_labelers` already validated `signal` against the
hardcoded, exact-match `ml.meta_bootstrap.META_LABELED_SIGNAL_IDS` allowlist
(`("timeseries_momentum", "cross_sectional_momentum")`) — raising
`ValueError` on anything else — *before* appending it to the `cmd` list, and
`Popen` is called with a list and no `shell=True`. CodeQL's
`py/command-line-injection` query does not model an `if x not in
ALLOWLIST_TUPLE: raise` guard as a sanitizer, so it still flags the call
despite the input being fully controlled. Rather than leave this
undocumented, the fix applied the exact same treatment as alert #11: an
explanatory comment plus a `# codeql[py/command-line-injection]` suppression
annotation directly on the `Popen` call in
`gui/orchestrator_runner.py::launch_train_meta_labelers`, so the next CodeQL
scan (and the next reviewer) can see the reasoning inline rather than
re-litigating it. Added adversarial-input regression coverage —
`tests/test_security_audit_fixes.py::TestLaunchTrainMetaLabelersInputValidation`
(shell metacharacters, an injected CLI flag, a leading-dash flag-injection
attempt, path traversal, and a case-mismatched near-miss all assert
`ValueError`; a real allowlist member still launches correctly) — mirroring
`TestLaunchValidationInputValidation`'s existing coverage for alert #11.

Once this fix merges, alert #91 should be dismissed in the GitHub Security
tab as "used in tests" / reviewed-and-mitigated, consistent with how #21/#22
were handled.

## Related

- [`react_router_dom_ghsa_jjmj_open_redirect.md`](react_router_dom_ghsa_jjmj_open_redirect.md),
  [`vite_plugin_pwa_workbox_dev_chain_unfixable.md`](vite_plugin_pwa_workbox_dev_chain_unfixable.md) —
  the prior npm-audit review pass this one follows the same format as.
- [`pip_audit_stale_ambient_env_false_positive.md`](pip_audit_stale_ambient_env_false_positive.md) —
  why this review re-ran `pip-audit` against a real `python3.12` venv rather
  than trusting an ambient interpreter.

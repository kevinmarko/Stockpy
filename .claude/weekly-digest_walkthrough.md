# Weekly Digest Walkthrough

## Status: audited and fixed (2026-09) after two prior passes shipped real defects

This feature went through three rounds before it was actually correct end to
end. Recording all three here rather than only the final state, because the
gap between "looked right" and "was right" at each earlier round is the
actual lesson.

### Round 1 — original 8-agent build (`774b1623`)
Built the full feature per `.claude/weekly-digest_implementation_plan.md`'s
8-work-package split: scaffold, view-tracking store, sector-gap composition,
digest composer, daemon scheduling, delivery wiring, webapp panel, docs.
Shipped with a real, uncaught fabrication bug (below) and no wiring for the
view-tracking store's *write* side at all — `data/symbol_view_store.py`
existed and had tests, but nothing ever called `record_view()`.

### Round 2 — a follow-up "gap fix" (`7dbe8fea`)
Claimed to close two gaps: (1) wire `record_view()` into
`get_symbol_detail`/`explain_ticker`, and (2) sync `webapp/src/help/helpContent.ts`.
**This commit actually broke the build.** The view-tracking write it added
used `f\"...\"` — literal backslash-escaped quotes, invalid Python syntax —
in both `api/data_api.py` and `api/pilots_api.py`. Neither module could be
imported; the entire Pilots API and Data API FastAPI services were down on
this branch. `python3 -m ast.parse` on either file raised a `SyntaxError`.
This was caught only by re-auditing from scratch rather than trusting the
"gaps fixed, mirrors the plan precisely" claim that shipped with it.

### Round 3 — this pass: 4-agent audit against a verified baseline
Before dispatching agents, the coordinating session independently: fixed the
Round 2 syntax error; confirmed a critical fabrication bug in the digest
composer (below); confirmed the delivery dedup/throttle was not durable
across a daemon restart; confirmed the daemon's timer loop would likely
never wake up automatically under the plan's own recommended default
config; and confirmed `AGENTS.md` had drifted from `CLAUDE.md` (Round 2's
"docs sync" fix only touched `helpContent.ts`). Four agents then verified
and fixed these plus independently found more. See "Findings" below for the
complete list — nine real, verified defects in total across scope, style,
and correctness, not the two Round 2 claimed.

## What was built (current state)

1. **`pilots/digest_models.py`** — `DigestPayload`/`DigestItem` dataclasses.
   `DigestItem` carries `symbol`/`reason`/`selection_type`/`confidence_tier`
   (`"high"`/`"medium"`/`"low"`, mechanically derived from `selection_type`,
   never set independently). `DigestPayload` carries `items`/`generated_at`/
   `personalization_active`/`reason` — the latter two exist specifically so
   a degraded state (no view history yet, no radar candidates) can never
   render with the same visual/textual weight as a genuinely personalized
   digest.
2. **`data/symbol_view_store.py`** — a durable, isolated (never imported by
   `signals/`/`sizing/`/`execution/`, AST-guard-enforced) "most recent view
   per symbol" store. **Deliberately upsert-by-symbol, not append-only** —
   the plan/task both call it an "append-only log," but the only consumer
   (`get_recently_viewed_symbols`) only ever needs the most recent view
   timestamp, and true append-only would grow unboundedly for zero benefit;
   this deviation is documented in the module's own docstring. Written to
   from `api/pilots_api.py::get_symbol_detail` and
   `api/data_api.py::explain_ticker`, best-effort (a write failure never
   turns a 200 read into a 500).
3. **`pilots/sector_gap.py`** — diagnostic-only "underrepresented sector"
   composition (current paper holdings vs. tracked universe), bounded by a
   30s wall-clock budget since a cold fundamentals cache can still trigger a
   real network fetch per symbol despite the huge `max_age_days` passed to
   `HistoricalStore.get_fundamentals_raw` (documented in the module's own
   "Review notes" block). No `SignalModule`, no `SIGNAL_WEIGHTS` entry.
4. **`pilots/weekly_digest.py`** — the composer. Reuses
   `pilots.radar_ranking.radar_feed`'s Top-N verbatim (never recomputes
   scoring), combines it with the view-tracking log and sector-gap
   diagnostic, and implements the plan's honest 3-rung fallback ladder (see
   "Critical bug" below for what "honest" required fixing).
5. **`desktop/daemon_runtime.py`** — `OrchestratorDaemon.maybe_dispatch_weekly_digest()`,
   gated on `settings.WEEKLY_DIGEST_ENABLED`, throttled to
   `settings.WEEKLY_DIGEST_INTERVAL_HOURS` via a durable state file
   (`output/weekly_digest_state.json`, atomic write) — not just an
   in-process timestamp. `start()` now creates the timer thread whenever
   `WEEKLY_DIGEST_ENABLED` is set, even at `ORCHESTRATOR_INTERVAL_SECONDS<=0`
   (the documented default), and the loop's on-demand-only park is now
   bounded (hourly) instead of indefinite — see "Findings" #4 for why both
   of these were required, not optional polish. `WEEKLY_DIGEST_ENABLED`
   only has any effect while the persistent orchestrator daemon
   (`ORCHESTRATOR_DAEMON_ENABLED`, also default `False`) is running.
6. **Delivery** — `observability.alerts.send_alert("INFO", ..., dedup_key=...)`,
   kept as defense-in-depth against a rapid duplicate call within one
   process's own short window; the durable state file in (5) is the actual
   cross-restart protection.
7. **`api/pilots_api.py`** — `GET /pilots/weekly-digest` (fail-open read
   tier), returning `dataclasses.asdict(compose_digest(...))`.
8. **`webapp/`** — `DigestSection` on the Marketplace screen, mirroring the
   sibling `RadarSection`'s honesty pattern (see "Findings" #6).
9. **Docs** — `CLAUDE.md`/`AGENTS.md` (kept in sync — `GEMINI.md` does not
   exist in this repo and was correctly left untouched), `.env.example`,
   `docs/architecture/data-layer.md` / `docs/architecture/webapp-and-gui.md`,
   `webapp/src/help/helpContent.ts` (a `GLOSSARY` entry + `TAB_HELP.pilots`
   description/`keyConcepts`).

## Findings from this pass (all verified and fixed, most-severe first)

1. **Build-breaking syntax error** (`api/data_api.py`, `api/pilots_api.py`) —
   Round 2's `f\"...\"` literal. Both modules failed to `ast.parse`. Fixed
   by the coordinating session before any agent was dispatched.
2. **Critical fabrication bug** (`pilots/weekly_digest.py`) — with an empty
   view-tracking log (the realistic fresh-install state), every radar
   candidate was vacuously "not in an empty set" and got labeled
   `"Personalized"` — exactly the fabrication both `.claude/weekly-digest_task.md`'s
   checklist and the plan's §4/§6 explicitly warn against, and untested by
   every existing test (all mocked a non-empty view log). Fixed by gating
   the `"Personalized"` branch on `personalization_active = bool(recently_viewed)`;
   `DigestPayload.personalization_active`/`.reason` now disclose this state
   honestly instead of it being silently indistinguishable from a real
   personalized pick.
3. **Live production DB contamination, already manifested** —
   `tests/conftest.py`'s isolation fixture for `SymbolViewStore` was opt-in
   (requested only by its own test file), not autouse. Every other test
   exercising `get_symbol_detail`/`explain_ticker` via `TestClient` was
   writing real `record_view()` calls straight into the operator's shared
   `~/.stockpy_local/quant_platform.db`. Confirmed: that database already
   held a `symbol_views` table with 9 rows of obvious test-fixture symbols
   (`XYZ`, `OLDTICKER`, `GHOST`, ...) — the same contamination class as
   `docs/known_issues/pr872_live_db_test_contamination_2026.md`. Fixed by
   moving the fixture to root `conftest.py` as session-wide autouse
   (matching that doc's own established remediation pattern); the 9
   existing contaminating rows were **not** purged by this PR — that is an
   operator action per this repo's established norm (see that same doc),
   flagged to the operator separately.
4. **The digest would likely never fire automatically under the plan's own
   recommended default config** (`desktop/daemon_runtime.py`) — two
   independent, compounding bugs: (a) `OrchestratorDaemon.start()` only
   created a timer thread at all when `ORCHESTRATOR_INTERVAL_SECONDS > 0`,
   so under the documented default (`0`, "on-demand only") the loop never
   ran, not even once; (b) even when a thread does run, the on-demand-only
   park was an *unbounded* `wait()`, woken only by `set_interval()`/
   `shutdown()` — `trigger_run()` (a manual pipeline run) does not wake it.
   Net effect at default settings: the digest check fires once, at daemon
   startup, and then never again. Fixed via both a scoped timer-thread
   creation condition (`WEEKLY_DIGEST_ENABLED` also starts the thread) and
   a bounded (hourly) park.
5. **Delivery dedup/throttle was not durable across a restart** — the
   in-process `_last_weekly_digest_dispatch` timestamp reset on every daemon
   restart, and `send_alert()`'s `dedup_key` suppression is documented as a
   15-minute window (built for collapsing rapid re-evaluation within one
   run, not durable "already sent" protection hours/days later). A restart
   mid-week would have re-sent the digest immediately. Fixed via a durable,
   atomically-written state file (`output/weekly_digest_state.json`).
6. **Webapp panel silently hid on a legitimate empty digest** — unlike its
   own sibling `RadarSection` (which renders an honest `reason` on zero
   items), `DigestSection` `return null`'d on `items.length === 0`,
   indistinguishable from the feature not existing. Fixed to mirror
   `RadarSection`'s pattern, plus a visible note when
   `personalization_active` is `false`.
7. **`AGENTS.md` drifted from `CLAUDE.md`** — confirmed byte-identical
   before this PR; Round 1/2 only ever edited `CLAUDE.md`. Fixed by
   appending the identical bullet.
8. **A repo-wide AST import-boundary guard was never satisfied** —
   `tests/test_pilots_strategy_matrix.py::test_pilots_read_helpers_stay_dependency_light`
   auto-discovers every `pilots/*.py` file and enforces a per-module import
   allowlist; the three new files were never added to it. Caught by running
   the full targeted suite during final integration (none of the four
   agents' scopes happened to include this file). Fixed with per-module
   allowlist entries following the file's own established pattern.
9. **Committed settings census/liveness artifacts went stale** —
   `settings.py` gained two fields; `docs/settings_field_census.{json,md}`
   and `docs/settings_liveness.json` are committed, derived artifacts that
   must be regenerated whenever `settings.py`/API line numbers shift (per
   this repo's own documented convention). Regenerated via
   `scripts/measure_settings_census.py --write` and
   `scripts/settings_liveness.py --write`.

Also reviewed and deliberately left as-is: per-request `SymbolViewStore()`
construction (confirmed to match every other store in `api/pilots_api.py`/
`api/data_api.py` — no module in either service uses a singleton), and
full webapp-surfacing of a `send_alert()` per-channel delivery failure
(`send_alert()` has no return value distinguishing per-channel success from
failure — see `desktop/daemon_runtime.py::maybe_dispatch_weekly_digest`'s
own docstring for why only "an attempt was made, status X" can honestly be
recorded, not "confirmed delivered").

## What was tested

- **Data models**: max-5-items rejection, defaults.
- **View store**: record/get, cutoff filtering, readonly enforcement, the
  AST isolation guard (verified live — a real forbidden import was
  temporarily added and confirmed to fail the guard, then reverted).
- **Sector gap**: missing-sector / proportional-underrepresentation /
  graceful-degradation / empty-universe cases.
- **Digest composer**: all rungs of the fallback ladder explicitly, INCLUDING
  the empty-view-log regression test for finding #2 above.
- **Daemon scheduling & delivery**: throttling, the durability-across-restart
  regression test for finding #5 (a *second*, fresh `OrchestratorDaemon()`
  instance, not the same instance reused), corrupt-state-file degradation,
  failure-path handling, and an end-to-end proof that
  `maybe_dispatch_weekly_digest` gets repeated chances to fire while the
  timer is parked under `ORCHESTRATOR_INTERVAL_SECONDS<=0` (finding #4).
- **API**: mock and live routing, including the empty/honest-`reason` shape.
- **Webapp**: full typecheck clean; the full webapp vitest suite green
  (2043/2043 at time of this pass); dedicated tests for the honest-empty
  and `personalization_active: false` panel states.
- **Repo-wide gates**: `ruff check . --select=F821,F822,F823,E9` clean; the
  full offline suite (`pytest -m "not network and not slow"`, mirroring CI).

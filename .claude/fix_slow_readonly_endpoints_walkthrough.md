# Fix slow read-only endpoints — walkthrough (2026-10-02)

The tab audit timed three endpoints at 30–70 s: Universe Transparency, the Explain drawer and the Dashboard.

| Endpoint | Root cause | Fix | Before → after (measured in-process on live data) |
|---|---|---|---|
| `GET /data/sync-report` | (a) `fetch_account_snapshot(force=False)`: with `ROBINHOOD_AUTO_REFRESH_ENABLED` and a stale snapshot (243 h), this spawned a Robinhood device-approval login, which pushed a phone prompt and blocked up to `RH_LOGIN_DEADLINE_SECONDS`. (b) `build_sync_report` probed each symbol in turn, three network calls at about 2.5 s each. | (a) `allow_live_fetch=False`, like `/data/universe`. (b) 8-thread probe pool, with a per-symbol dead-letter. (c) 120 s response cache with a lock; failures are never cached. | about 70 s → 7.8 s cold, 0 s cached |
| `GET /data/explain/{symbol}` | Same login path as (a). | `allow_live_fetch=False`. | 54 s → under 0.1 s |
| `GET /observability/summary` | The two forecast-skill sections run about 12 full scans of `forecast_errors` (2.45 M rows, no index for a horizon-only filter). | 300 s cache for those two sections, keyed on horizon, skill settings and snapshot timestamp/symbols. Degraded results are never cached. | 8–64 s cold → about 0 s cached |

**Left alone, noted:**
- `GET /data/account` and `POST /data/sync` still allow the live login tier.
- The index `forecast_errors(horizon_days, forecast_ts)` would fix the scans themselves. It is a schema change on the shared 811 MB DB, so it needs operator sign-off.

**Verification:**
- **Full offline suite:** 11683 passed, 0 failed after regenerating the census/liveness artifacts.
- **New tests:**
  - reads never ask for a live fetch
  - sync-report cache: hit, failure not cached, expiry
  - parallel probe dead-letter and ordering
  - observability section cache: hit, key misses, degraded not cached, expiry
- **Test isolation:** a root `conftest.py` autouse fixture resets both caches per test.

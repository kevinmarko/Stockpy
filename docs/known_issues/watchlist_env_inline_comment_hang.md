# WATCHLIST `.env` inline comment silently hung the pipeline for 1h+ (2026-08-24)

## Status
**Fixed** (this PR) for the specific failure mode that hung the pipeline —
`load_env_watchlist()` now rejects implausible ticker strings before they can
reach a live fetch. **Fixed at the root (2026-09-04 follow-up)** — see
"Systemic fix shipped" below — for every field read via `settings.X`,
current and future. **Not fixed**: the actual live `.env` file still has
some of the malformed lines (operators must edit `.env` themselves — Claude
Code is hard-blocked from writing it, `.claude/hooks/block_env_write.sh`),
and any code that still reads one of these keys via a raw `os.environ.get(...)`
instead of `settings.X` remains exposed — see "Wider blast radius, not
fixed" below for what that follow-up found on this machine's real `.env`.

## Symptom
Operator reported: "it looks like the pipeline isn't working... I wasn't able
to run it when I opened the app." The webapp Pipeline screen showed a run
stuck in `state: "running"` for over an hour with no `duration`, and the
"Trigger a run" buttons disabled (correctly — a run was, in the daemon's own
bookkeeping, still in flight).

## Root cause
Two independent bugs stacked:

1. **python-dotenv does not strip an inline `# comment` from an unquoted,
   otherwise-blank value.** Verified directly against the installed library:

   ```python
   >>> dotenv.dotenv_values-equivalent parse of:
   ...   "WATCHLIST=                              # Plain text comma-separated fallback ticker list\n"
   {'WATCHLIST': '# Plain text comma-separated fallback ticker list'}
   ```

   `.env`'s `WATCHLIST=` line was written in this repo's common
   documentation style — `KEY=<blank>    # description of the key` — which
   works fine for keys read through a truthy/empty check *if* the reader
   also filters `#`-led values, but `WATCHLIST` is read via a raw
   `os.environ.get("WATCHLIST", "")` (documented in CLAUDE.md as
   intentionally NOT a pydantic-settings field), and nothing downstream
   filtered it.

2. **`load_env_watchlist()` (`data/portfolio_sync.py`) only filtered
   `#`-prefixed *lines* from the file source, never the env-var source.**
   The file source's `not line.startswith("#")` check has no equivalent for
   `WATCHLIST`'s comma-split tokens — so the literal string
   `"# Plain text comma-separated fallback ticker list"` (upper-cased) was
   treated as one bogus ticker and merged into the universe alongside the
   operator's real 6 `watchlist.txt` tickers.

That bogus "ticker" was then handed to `HistoricalStore.get_bars()`'s
cold-start path, which logged:

```
2026-08-24 15:47:12  INFO  data.historical_store — HistoricalStore: cold-start
backfill 504 days for # PLAIN TEXT COMMA-SEPARATED FALLBACK TICKER LIST.
```

...and the daemon's log went silent for the next hour-plus. A live `sample`
capture of the running daemon process (pid confirmed listening on the real
Control API port, `lsof -i :8601`) caught one worker thread genuinely
blocked in a `recv()` syscall (`sock_recv` → `sock_recv_guts` →
`kevent`/blocking read, not a busy spin) — the fetch attempt for this
malformed symbol is stuck on a network read with no bound, not merely slow.
`data/fmp_client.py`'s own request path is correctly timeout-bounded
(`FMP_TIMEOUT_SECONDS=10.0`, capped retries, a cooldown breaker), so this is
consistent with the malformed symbol falling through FMP to a fallback
provider path (yfinance/Yahoo) that does not carry the same timeout
discipline — not independently confirmed by module/line, but the live thread
trace, the timing, and the exact garbage string all correlate directly with
this one bogus ticker with no other plausible explanation found.

Net effect: one malformed universe entry, sourced from a doc-comment
placement mistake, hung the *entire* pipeline cycle — every other ticker's
fetch was blocked behind it in the same concurrent batch, and the daemon had
no way to notice or recover since `state="running"` never resolves until the
cycle itself returns.

## Fix (this PR)
`data/portfolio_sync.py::load_env_watchlist()` gained `_is_plausible_ticker()`,
applied to **every** candidate from **both** sources (env-var and file) before
it enters the universe. It rejects a candidate containing whitespace or `#`,
or longer than 15 characters — deliberately permissive on real ticker shape
(`AAPL`, `BRK.B`, `BTC-USD`), strict only against strings that can never be a
real symbol. A rejection is never silent: it's logged at WARNING with the
exact raw value and a pointer at the `.env` inline-comment gotcha, so a
future recurrence surfaces in the log within the same cycle instead of
hanging silently for an hour.

This is the single shared implementation both `main.py::_load_watchlist()`
and the daemon's `pipeline/production_steps.py::AsyncDataFetchStep` delegate
to (per the "Daemon universe-divergence fix" unification, CLAUDE.md), so the
fix covers both orchestrator entry points from one change.

Tests: `tests/test_portfolio_sync.py::TestLoadEnvWatchlist` — new cases cover
the exact reported inline-comment string, a mixed valid/garbage env value,
an embedded-whitespace file-line survivor, and an implausibly long candidate.

## Required operator action (not done by this PR)
1. **Fix the live `.env` line** (Claude Code cannot — `block_env_write.sh`
   hard-denies any write to a file literally named `.env`). Change:
   ```
   WATCHLIST=                              # Plain text comma-separated fallback ticker list
   ```
   to either put the comment on its own line above, or drop it:
   ```
   # Plain text comma-separated fallback ticker list
   WATCHLIST=
   ```
2. **Restart the orchestrator daemon.** The already-running process is still
   stuck mid-cycle on the hung network read from *before* this fix was
   applied — a code fix in a worktree has no effect on an already-running
   process from a different checkout, and the stuck cycle itself will not
   self-resolve. Restart via the webapp's daemon-restart control, or by
   stopping and relaunching `launch_webapp.command`.

## Wider blast radius, not fixed
The same python-dotenv inline-comment behavior currently affects **9 other**
live `.env` keys written in the identical `KEY=<blank>   # description`
style, confirmed live by loading the real `Settings()` singleton against this
machine's actual `.env`:

```
REDDIT_CLIENT_ID       = '# Reddit API OAuth2 script-app client ID (empty disables RedditSource)'
MCP_DATABASE_URL_RO    = '# Read-only Postgres DSN for restricted MCP query surface'
MARKET_DATA_WS_SYMBOLS = '# Comma-separated symbol override for WS subscription (unset = WATCHLIST)'
```
...and (not individually re-verified against the live singleton, but the
same `.env` line shape): `REDDIT_CLIENT_SECRET`, `ALERT_CHANNELS`,
`ALERT_NTFY_TOPIC`, `ALERT_SLACK_WEBHOOK_URL`, `ALERT_EMAIL_SMTP_HOST`,
`ALERT_EMAIL_SMTP_PASSWORD`.

Every one of these is meant to be genuinely empty by default (each gates a
disabled integration on an empty-string check), so each is *currently*
silently non-empty on this operator's machine — e.g. `RedditSource` may
believe it's configured with a garbage client ID rather than correctly
treating itself as disabled. None of these are known to have caused a hang
the way `WATCHLIST` did (no confirmed unbounded-network-call path behind any
of them the way bars-backfill was behind `WATCHLIST`), but the corruption
itself is real and live, not hypothetical.

**2026-09-04 re-check**: of the 9 keys named above, `REDDIT_CLIENT_ID`,
`ALERT_CHANNELS`, `ALERT_NTFY_TOPIC`, `ALERT_SLACK_WEBHOOK_URL`, and
`ALERT_EMAIL_SMTP_HOST` had since been hand-fixed in the live `.env` (their
lines no longer carry a trailing inline comment). `REDDIT_CLIENT_SECRET`,
`MCP_DATABASE_URL_RO`, and `ALERT_EMAIL_SMTP_PASSWORD` were still corrupted
— confirmed live, alongside `WATCHLIST` and `MARKET_DATA_WS_SYMBOLS`
themselves, which remain corrupted in the live `.env` too (the code-level
fixes above make that corruption harmless for these two specific fields,
but the `.env` file itself was never edited, per this doc's own "Claude
Code is hard-blocked from writing it" constraint).

## Systemic fix shipped (2026-09-04 follow-up)
The systemic fix this section originally deferred is now in place:
`settings.py`'s `Settings` class has a `model_validator(mode="before")`,
`_strip_dangling_env_inline_comments`, that strips ANY string-typed raw
value down to `""` whenever it consists entirely of a `#`-led comment
(after trimming whitespace) — regardless of field name — before any
field-level validation runs. This closes the bug class for every field
read via `settings.X`, current and future, not just the ones found by
hand above; verified live against this machine's real (still-uncorrected)
`.env` that all 10 previously-corrupted fields now resolve to `""`.

**Residual scope, disclosed, not closed by this fix**: this only protects
reads that go through `settings.X` — the sanctioned path per this repo's
own "credential reads MUST go through settings.X, never os.environ
directly" convention. A read via a raw `os.environ.get(...)` for one of
these keys (populated separately by whichever `load_dotenv()` call site
ran in that process, using the same underlying python-dotenv parser) is
NOT covered and would still see the corrupted comment text. No such bypass
is currently known for any of the 10 keys named in this doc (`WATCHLIST`
and `MARKET_DATA_WS_SYMBOLS` are both read via `settings.X` throughout —
see `data/portfolio_sync.py::load_env_watchlist` and
`data/market_data_ws.py::_resolve_symbols`), but this was not re-audited
across the whole codebase as part of this fix.

Tests: `settings.py`'s validator is exercised end-to-end by
`tests/test_market_data_ws.py`/`tests/test_portfolio_sync.py`'s existing
`WATCHLIST`-corruption regression cases now passing without needing the
per-key `_is_plausible_ticker` guard at all (that guard remains as
defense-in-depth for the two consuming modules above, not the only line of
defense it originally was).
folded into a pipeline-hang bugfix. Filed as a follow-up.

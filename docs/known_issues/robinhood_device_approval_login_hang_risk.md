# Known issue (mitigated by design): Robinhood device-approval login has no built-in timeout

**Status (2026-08): mitigated by construction, not yet verified against a real Robinhood
account.** Robinhood retired TOTP/SMS-code MFA in favor of a device-approval push
notification (the operator taps "approve" in the Robinhood app). `robin_stocks >= 3.4`
already implements the client side of this — omitting `mfa_code` from `login()` is what
triggers it — but the library's own implementation of the wait has two structural hazards
that would hang any process calling it directly:

1. **No timeout on the approval-poll loop.** `robin_stocks.robinhood.authentication`'s
   `_validate_sherrif_id()` polls Robinhood's `push/{id}/get_prompts_status/` endpoint in a
   bare `while True:` loop with no deadline of its own. If the operator never opens their
   phone, this blocks forever.
2. **A blocking `input()` on the SMS/email fallback path.** For an account where Robinhood
   still offers a text/email code as an alternative, the library falls through to a plain
   `input()` call expecting a human to type a code at a real terminal. In any process that
   isn't an interactive TTY (a FastAPI request handler, a cron-launched `main.py`, the
   orchestrator daemon), that `input()` blocks on stdin that will never receive a line.

Before this repo moved to device-approval login, neither hazard was reachable in practice —
the old TOTP/SMS-code flow (`RH_MFA_SECRET` + `pyotp`) never entered `robin_stocks`' push- or
prompt-based code paths at all. Retiring `RH_MFA_SECRET` removed the option to bypass this
workflow (passing `mfa_code` to `login()` short-circuits the device-approval workflow
entirely — Robinhood returns a token directly and never issues the challenge — which is
useful for automation but is also *why* a TOTP secret can no longer coexist with real device
approval), so every login this codebase performs now runs through code that has no timeout
of its own.

## The fix: never call `robin_stocks.login()` outside an isolated, killable subprocess

`data/robinhood_login_worker.py` is a small child process launched fresh per login attempt by
`data/robinhood_login.py`, with:
- `stdin=subprocess.DEVNULL` — turns the SMS/email `input()` hazard into an immediate,
  honestly-reported `EOFError` instead of a hang.
- `start_new_session=True` — its own process group, so the parent can `os.killpg()` the whole
  group (not just the top process) on a deadline.
- A parent-enforced deadline (`settings.RH_LOGIN_DEADLINE_SECONDS`, default 180s) — SIGTERM,
  then SIGKILL after `settings.RH_LOGIN_GRACE_SECONDS` (default 5s) if it hasn't exited.
- A separate, shorter startup deadline (`settings.RH_LOGIN_STARTUP_SECONDS`, default 30s) — if
  the child never even emits its first progress event, it's treated as a failed launch rather
  than waited out for the full login deadline.

`data/robinhood_portfolio.py::_login_with` structurally enforces that the ONLY code path that
can call `r.login()` at all is guarded by an `RH_LOGIN_WORKER=1` environment marker set solely
by the worker process — a future refactor that accidentally reintroduces a direct call from a
request handler will raise a plain `RuntimeError`, not silently reproduce the hang.

`data/robinhood_client.py::RobinhoodClient.login()` — a separate, legacy client used only by
the frozen `gui/panels/live_inventory.py` for watchlist discovery — is likewise gated behind
the same marker and now always returns `False` outside the worker, since nothing routes it
through the isolated flow. That degrades watchlist discovery to unavailable rather than
hanging the Streamlit process.

## What is, and isn't, verified

**Verified (2026-08):** the subprocess/pipe/deadline plumbing itself, end-to-end, against
Robinhood's real login endpoint with intentionally-fake credentials — the worker correctly
launches, the credential pipe round-trips, a real (fake-credential) login attempt completes
in well under a second with `error_code=auth_failed`, and the process exits cleanly with no
orphan left behind. The `no_credentials` short-circuit path is verified the same way. The
kill/timeout escalation path (SIGTERM → grace → SIGKILL) is verified via `tests/test_robinhood_login.py`
against a stub worker script that deliberately hangs — not against a real Robinhood account
that never approves, since that isn't something a test suite can trigger on demand.

**NOT verified, and cannot be from a sandboxed dev environment with no real Robinhood
account in hand:**
- That omitting `mfa_code` actually causes `robin_stocks` to issue a `verification_workflow`
  for a REAL account (the entire premise this design rests on).
- That a real push notification arrives and that approving it on a phone flips
  `get_prompts_status` to `validated` within the 180s deadline.
- Whether a *denied* approval is distinguishable from an *ignored* one. Reading
  `_validate_sherrif_id`'s source, the answer appears to be **no** — its loop only ever
  breaks on `challenge_status == "validated"`, with no separate branch for an explicit
  denial — so this codebase's UI copy deliberately says "no approval came through" rather
  than asserting the operator denied anything, since claiming that distinction would be
  fabricated.
- Whether `data/robinhood_session.py`'s session-pickle persistence (restoring a previously-
  approved `device_token` so the SAME device isn't re-challenged every login) measurably
  reduces how often a real account gets prompted, and by how much.
- Whether repeated login attempts trigger Robinhood-side rate-limiting or a security lockout
  — this is the finding that would validate (or invalidate) the 15-minute floor on the
  webapp's optional Robinhood auto-refresh category (`docs/architecture/webapp-and-gui.md`).

These must be exercised against a real account, with a real phone, before this design is
treated as fully proven rather than "correct by construction and unit-tested in isolation."

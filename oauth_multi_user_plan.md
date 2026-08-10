# Implementation Plan: Multi-User Auth for the MCP OAuth Authorization Server

## 0. Scope resolution (read first)

**"Multi-user account system" is ambiguous. Resolved from evidence, not assumption — recommendation: Option A, multiple named credentials sharing the one existing single-operator account, not genuine multi-tenancy.**

Evidence:
- `mcp_oauth_store.py`'s docstring calls `oauth_login_state` "a singleton row (`id=1`)" for "a minimal OAuth 2.1 authorization server" — deliberately single-subject.
- `mcp_oauth_provider.py`'s docstring: the real trust boundary is one `/login` password form (`settings.MCP_OAUTH_PASSWORD`) — one password, not a credential table.
- `docs/mcp_server_split_brain.md`'s OAuth addendum calls both bearer- and oauth-mode `streamable-http` instances **"ephemeral, developer-machine-local tooling"** — a personal connector, not a hosted service.
- Grepped `user_id`/`User_ID`/`tenant_id`/`account_id`/`owner` across `transactions_store.py`, `data/paper_account_store.py`, `pilots/follows_store.py`, `data/cache_long_short_store.py` — **zero hits in all four**. `CLAUDE.md`'s own Cache Long/Short bullet states outright: "Single-operator schema (no `User_ID`/`Portfolios`... not a multi-tenant design)." One brokerage connection, one `PaperAccount.id==1` singleton, one kill switch, one `FollowsStore`.
- Grepped `investyo_mcp_server.py`'s ~53 tools for `subject`, `AccessToken`, `get_access_token`, `request_context`, `current_user` — **zero hits outside the OAuth module**. The token's `subject` claim is never read downstream by any tool.

The two options, concretely:
- **Option A** — small `oauth_users` table (username/password-hash/display-name/active), per-user lockout, `subject` = username on tokens. Every user still reaches the exact same trading account/follows/paper account/kill switch as today. Scoped to `mcp_oauth_store.py` + `mcp_oauth_provider.py` + a provisioning CLI. One PR.
- **Option B** — genuine per-user isolation: own brokerage connection, own follows, own paper account, own kill switch per login. Requires adding a tenant dimension to `transactions_store.py`, `data/paper_account_store.py`, `pilots/follows_store.py`, `data/brokerage_credentials.py`, `execution/kill_switch.py`, and more — a cross-cutting platform rewrite, not an OAuth-layer change.

**Recommendation: Option A.** The task frames this as an OAuth-server change, not a request to re-architect trading/data stores; the platform is single-operator by deliberate, repeatedly-stated convention; `subject` already flows unused end-to-end (suggesting identity-labeling was anticipated, not data isolation); and "ephemeral developer-machine-local tooling" reads as "let a co-founder/accountant log in under their own name" rather than "build a SaaS platform." If Option B is actually wanted, that's a materially larger, separate project deserving its own dedicated plan — say so explicitly rather than have this guessed.

**This plan implements Option A.**

---

## 1. Data model changes (`mcp_oauth_store.py`)

New table `oauth_users`:

```python
class OAuthUser(Base):
    __tablename__ = "oauth_users"
    username = Column(String(255), primary_key=True)
    password_hash = Column(String(255), nullable=False)  # KDF output, see §5
    display_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)
```

`username` as PK (mirrors `OAuthClient.client_id`); `is_active` instead of hard delete (reversible, preserves audit trail on already-issued tokens' `subject`); no `role`/`scope` column in v1 — there's only one scope today and nothing downstream to enforce a role.

Existing tables: `oauth_clients`, `oauth_pending_authorizations`, `oauth_authorization_codes`, `oauth_access_tokens`, `oauth_refresh_tokens` need **no schema change** — their `subject` columns already exist and simply get populated with `username` instead of being unset/hardcoded.

**`oauth_login_state` must become per-user.** Today it's a global `id=1` singleton. With N credentials, a global lockout is wrong on two counts: one user's mistyped password locks out everyone else, and an attacker gets 5 total guesses across every account rather than 5 per account. Redesign:

```python
class OAuthLoginState(Base):
    __tablename__ = "oauth_login_state"
    username = Column(String(255), primary_key=True)
    fail_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(Float, nullable=True)
```

`record_login_failure`/`is_locked_out`/`reset_login_state` all gain a `username` parameter. **Enumeration tradeoff, stated explicitly**: per-user lockout can in principle leak whether a username exists. Mitigation: `login_post` returns the identical generic "incorrect password" response for both an unknown username and a wrong password on a known one — never a distinguishable status/message. A residual timing side-channel (KDF only runs for a real row) is accepted, consistent with this module's existing "defense-in-depth, not a hard guarantee" posture.

---

## 2. Provisioning

**Recommendation: a new `scripts/manage_oauth_users.py` CLI, not an HTTP endpoint.** Every credential-provisioning action in this codebase today is hand-run/operator-local (`.env` edits for `MCP_OAUTH_PASSWORD`, `ROBINHOOD_USERNAME/PASSWORD`). There's no precedent for an HTTP endpoint that creates login credentials for other humans — that would be new, unreviewed attack surface for something a local CLI does more simply and with zero network exposure.

Following `scripts/_bootstrap.py`'s venv-reexec + `.env`-load convention:

```
python scripts/manage_oauth_users.py add <username> [--display-name NAME]   # getpass x2, never argv
python scripts/manage_oauth_users.py deactivate <username>
python scripts/manage_oauth_users.py reactivate <username>
python scripts/manage_oauth_users.py list                                    # never prints a hash
python scripts/manage_oauth_users.py reset-password <username>
```

Password always via `getpass.getpass()`, never a CLI arg (avoids shell-history/`ps` leakage — matches this codebase's `SECRET_KEYS` discipline). `deactivate` does **not** cascade-revoke already-issued tokens in v1 — documented gap, not silently built; a follow-on `revoke-all-tokens` subcommand is a cheap, clearly-scoped future addition if needed.

**Does this need a new `_ENABLED` flag under the 2026-08-03 convention?** No — that convention is specifically about fail-closed, HTTP-reachable capability gates checked at request time. A local CLI a human runs by hand has no fail-open/fail-closed question to resolve, same as `.env` editing today needing no flag. A different flag *is* needed for the auth-mode cutover itself — see §6.

---

## 3. Auth subject propagation

The `subject` column already exists end-to-end on `oauth_authorization_codes`/`oauth_access_tokens`/`oauth_refresh_tokens` — no schema change needed for propagation, only for what gets written into it. **Caveat found during review**: tracing `authorize()` → `login_post()` → `save_authorization_code()` in the current code, `subject` is not actually set anywhere in today's flow (it's `None` on every issued code) — implementation should verify against the live code whether a literal `subject="operator"` hardcode exists elsewhere or whether introducing `subject=username` is genuinely new wiring, not a "change" to an existing hardcode. Either way the mechanism is the same: `/login` gains a `username` field, `login_post()` verifies it against `oauth_users`, and on success passes `subject=username` into `save_authorization_code`; `exchange_authorization_code()` already copies it onto both minted tokens with no change needed.

**Confirmed: nothing downstream reads `subject` today.** All ~53 tools in `investyo_mcp_server.py` execute identically regardless of who authenticated. This is an important finding either way — it confirms Option A's premise (pure identity-labeling change, zero behavioral difference between users) and marks `subject` as a natural but *unbuilt* extension point for future per-user audit trails, not something this plan wires up.

---

## 4. Rate limiting / lockout interaction

**No changes needed.** `mcp_oauth_rate_limit.py`'s `SlidingWindowLimiter` keys buckets by `f"{bucket}:{client_ip}"` only — purely IP-based, no username concept, and stays correct as-is: an attacker trying 50 different usernames from one IP should still hit the same per-IP budget. This is a genuinely distinct, complementary control from the per-user *lockout* in §1, which is the one that needed to change.

---

## 5. Security review

Today: `hmac.compare_digest(submitted_password, settings.MCP_OAUTH_PASSWORD)` — constant-time comparison against one plaintext secret held in trusted `.env` config, never persisted to the DB. This is safe only because there's exactly one secret in operator-controlled config.

Multi-user means N credentials stored in the DB. Storing raw/reversible passwords there means a DB compromise (backup theft, misconfigured Postgres ACL) exposes every user's real password — categorically worse than today. **A real KDF is required, not a bigger comparison.**

`bcrypt`/`argon2`/`passlib` are **not currently in `requirements.txt` or `requirements-optional.txt`** (confirmed by grep). `cryptography==50.0.0` **is already pinned** and ships `cryptography.hazmat.primitives.kdf.scrypt.Scrypt` — a real, salted, slow KDF with zero new dependency footprint.

**Recommendation:** a new small module `mcp_oauth_password.py` using `Scrypt` (OWASP-recommended parameters, e.g. N=2^14, r=8, p=1) with a self-describing stored format (`scrypt$N$r$p$salt_b64$hash_b64`) so future re-parameterization needs no schema change. `verify_password` must `hmac.compare_digest` the derived key bytes, never the encoded string or a naive `==`. Alternative considered and rejected for v1: adding `bcrypt` purely for its de-facto-standard status — `cryptography`'s `Scrypt` is already vetted and sufficient at this account-count scale (2-5 named humans, not public signup); revisit only if the operator specifically wants `argon2`/`bcrypt` interop with an external tool.

---

## 6. Settings/classification changes

New field, `settings.py`:

```python
MCP_OAUTH_MULTI_USER_ENABLED: bool = Field(
    default=False,
    description=(
        "Switches the OAuth /login form from the single-passphrase check "
        "(MCP_OAUTH_PASSWORD) to per-user credentials in oauth_users "
        "(mcp_oauth_store.py), provisioned via scripts/manage_oauth_users.py. "
        "False (default) preserves today's exact single-password behavior. "
        "GUI-writable (non-secret) but a settings_keysets.DANGEROUS_KEYS "
        "member -- flipping it changes the entire auth trust boundary, the "
        "same risk class MCP_OAUTH_ENABLED itself already carries."
    ),
)
```

**Default `False` — and deliberately classified under the *other* standing convention** ("new settings default to today's exact behavior"), **not** the 2026-08-03 "admin/write/execution capabilities default ON" bullet. Reasoning: that bullet is about fail-closed HTTP-reachable capability gates; this flag instead changes *which authentication mechanism governs the whole OAuth server* — its closest precedent is `MCP_OAUTH_ENABLED` itself, which also defaults `False`. A silent flip on `git pull` would be a surprising trust-boundary change for anyone already running `--auth-mode oauth`.

`MCP_OAUTH_PASSWORD` is untouched — stays `SECRET_KEYS`, stays required when `MCP_OAUTH_ENABLED=True`; when multi-user mode is on, `login_post()` simply ignores it. Add `MCP_OAUTH_MULTI_USER_ENABLED` to `gui/env_io.py`'s `ALLOWED_KEYS` (same non-secret bool treatment as `MCP_OAUTH_ENABLED`) and to `settings_keysets.SAFETY_CRITICAL_KEY_REASONS` (same `DANGEROUS_KEYS` treatment). No new `.env`/`SECRET_KEYS` entry is needed for the password hashes themselves — they live in the DB via the existing `DATABASE_URL` seam (already `SECRET_KEYS`), a different secret boundary than `gui/env_io.py` classifies.

---

## 7. Tests

Mirroring the existing per-module split (`tests/test_mcp_oauth_store.py`, `_provider.py`, `_login_route.py`, `_flow_smoke.py`, `_rate_limiting.py`):

- `test_mcp_oauth_store.py` — `oauth_users` CRUD; per-username `oauth_login_state` independence (two usernames lock out independently — the core regression to guard).
- New `test_mcp_oauth_password.py` — hash/verify round trip, salt uniqueness, tamper-detection on the stored format.
- `test_mcp_oauth_provider.py` — multi-user `login_post`: correct creds issue `subject=username`; wrong password locks out only that user after 5 attempts; unknown username gets the identical generic error; inactive user rejected even with correct password.
- `test_mcp_oauth_login_route.py` — rendered form gains `username` field only when multi-user mode is on; legacy form byte-identical when off.
- `test_mcp_oauth_flow_smoke.py` — extend with two users independently completing the full OAuth dance, each ending with a distinct token `subject`.
- New `test_manage_oauth_users.py` — CLI subcommands at the function level (not subprocess+getpass).
- **Explicit backward-compat regression test**: with the flag at default `False`, the entire pre-existing single-password suite passes unchanged — the concrete proof behind the "byte-identical" claim in §8, not just prose.

---

## 8. Backward compatibility & rollout

**Additive, opt-in — no forced migration.** At `MCP_OAUTH_MULTI_USER_ENABLED=False` (default), `login_post()` is byte-identical to today: one-field form, `MCP_OAUTH_PASSWORD` check, existing lockout semantics. `oauth_users` exists but is never queried while off.

The one non-purely-additive piece is `oauth_login_state`'s PK change (`id=1` → `username`). Recommended approach: reserve a sentinel username (e.g. `"__single_password__"`, validated at provisioning time to never collide with a real username) for the legacy path, add a `username` column via an idempotent additive `ALTER TABLE` (mirroring `data/historical_store.py`'s `schema_version`-style additive-migration precedent — this codebase has no formal migration framework), and leave the old `id` column in place unused rather than dropping it (SQLite `DROP COLUMN` support is version-fragile). Rejected alternative: a hard cutover assuming every deployment starts fresh — rejected because at least one real developer-machine-local deployment exists today (per `docs/mcp_server_split_brain.md`) and silently dropping its lockout state, while low-stakes, is an avoidable surprise.

Rollout: upgrade (flag off, no behavior change) → `scripts/manage_oauth_users.py add <username>` per named human → set `MCP_OAUTH_MULTI_USER_ENABLED=True` → restart. Downgrade is symmetric and lossless (`MCP_OAUTH_PASSWORD` must still be set for legacy mode to work).

---

## 9. Ordered implementation sequence

1. `mcp_oauth_password.py` (new, independently unit-testable, no DB dependency) — land and test first.
2. `mcp_oauth_store.py` — `OAuthUser` model + CRUD; per-username `OAuthLoginState` shape + additive migration + sentinel constant; update `record_login_failure`/`is_locked_out`/`reset_login_state` signatures.
3. `settings.py` — add `MCP_OAUTH_MULTI_USER_ENABLED`.
4. `gui/env_io.py` / `settings_keysets.py` — classify the new flag.
5. `mcp_oauth_provider.py` — conditional `username` field on the login form; `login_post()` branches legacy-vs-multi-user; sets `subject=username` on success.
6. `scripts/manage_oauth_users.py` (new CLI).
7. Tests (§7) — interleave with 1-6, but the backward-compat regression test must run explicitly before calling this done.
8. Docs: `docs/mcp_server_split_brain.md`'s OAuth addendum; update `mcp_oauth_store.py`'s own module docstring (its "singleton row" description of `oauth_login_state` would otherwise actively mislead); `AGENTS.md`'s env-write-safety bullet.
9. Verify (below).

---

## 10. Verification plan

1. Lint: `python -m ruff check . --select=F821,F822,F823,E9`
2. Targeted: `pytest tests/test_mcp_oauth_store.py tests/test_mcp_oauth_provider.py tests/test_mcp_oauth_login_route.py tests/test_mcp_oauth_flow_smoke.py tests/test_mcp_oauth_rate_limiting.py tests/test_mcp_oauth_password.py tests/test_manage_oauth_users.py -v`
3. Full offline suite: `make ci` (`pytest -m "not network and not slow"`) — every new test must be DB-local, no network dependency, matching the existing OAuth test files' convention.
4. Manual end-to-end smoke (operator-run): provision two users, enable the flag locally, complete `/login` for each in a browser, confirm distinct tokens and independent lockouts.
5. Backward-compat proof: run the full existing OAuth suite with the flag at default `False` against a pre-change baseline and confirm zero diff.
6. Do not run the deeper `make verify` (live-broker) gate unprompted — nothing here touches broker/execution code.

---

### Critical Files for Implementation
- `mcp_oauth_store.py`
- `mcp_oauth_provider.py`
- `mcp_oauth_password.py` (new)
- `scripts/manage_oauth_users.py` (new)
- `settings.py`

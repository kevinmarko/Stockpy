# InvestYo Advisory Platform — Incident Log

Chronological record of operational anomalies, pauses, and remediations. Each entry
is appended; never edited or deleted. Pair with `output/decision_log.jsonl` for the
per-signal operator log.

---

## Template (copy for new entries)

### YYYY-MM-DD — short title

- **Detected:** how the anomaly surfaced (preflight failure, calibration MAE
  spike, dead-letter queue entry, manual observation)
- **Symptom:** observable state at detection
- **Root cause:** what was actually wrong
- **Remediation:** what was done; reference commits/PRs by SHA or URL
- **Pause taken?** yes / no — if yes, link to the matching decision_log.jsonl entry
- **Follow-up:** open items, watchlist entries, Gravity steps added

---

## Entries

### 2026-07-29 — Google service-account private key exposed in old git history

- **Detected:** manual operator request ("hide credentials.json from github")
  prompted a full audit of every blob reachable from any ref on `origin`
  (all 288 remote branches, not just `main`).
- **Symptom:** `credentials.json` is correctly `.gitignore`d and untracked on
  `main` today, so no code change was needed there. However, commit `afa7610`
  ("Add files via upload", 2026-06-18) committed a real Google Cloud service
  account private key in cleartext:
  - Project: `stock-dashboard-py`
  - Service account: `sheets-updater@stock-dashboard-py.iam.gserviceaccount.com`
  - Private key ID: `57f802317bcd493c36589219062a58bccb8a2e19`

  That commit is **not** an ancestor of `main` — `main`'s history was rebuilt
  from an unrelated root at some point — but 273 of the 288 remote branches
  are built on the old lineage and still carry the key in their history, some
  with commits as recent as 2026-07-17.
- **Root cause:** the key was committed before `credentials.json` was added
  to `.gitignore`; the branch that introduced the `.gitignore` entry
  (`9a2f493`, "Centralize runtime config and remove hardcoded FRED API key")
  started from a disconnected, already-clean root, so `main` itself was never
  exposed, but the old branches predating that cleanup were never rewritten
  or deleted.
- **Remediation:** none performed by automation — revoking/rotating a live
  credential and force-rewriting 273 shared remote branches are both outside
  what this session can safely do unattended. **Operator action required:**
  revoke key ID `57f802317bcd493c36589219062a58bccb8a2e19` for
  `sheets-updater@stock-dashboard-py.iam.gserviceaccount.com` in Google Cloud
  Console (IAM & Admin → Service Accounts → Keys) and issue a new key.
- **Pause taken?** no.
- **Follow-up:** operator decided (2026-07-29) to rotate the key and leave
  the 273 affected branches as-is rather than force-purge git history, since
  many carry unreviewed, possibly-unmerged work and rewriting them would be
  more disruptive than the residual risk of a revoked key sitting in old
  history. No further action pending unless the key rotation above is done.

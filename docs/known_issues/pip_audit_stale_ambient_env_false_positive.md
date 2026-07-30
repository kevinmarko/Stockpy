# Known issue (resolved): "36 vulnerabilities" pip-audit report was scanning the wrong Python environment

**Status: resolved — false positive, root-caused and verified.** A
2026-07-29 security audit reported 36 known vulnerabilities across
`cryptography`, `httplib2`, `idna`, `pip`, `pyjwt`, `setuptools`,
`urllib3`, and `wheel`. Rebuilding `.venv` from `requirements.txt`
(Python 3.12, matching `.github/workflows/ci.yml`) and re-running
`pip-audit --desc` against it found **zero** vulnerabilities.

## What happened

The original report ran `pip-audit --desc` against, in its own words, the
"Default Python environment (outside venv)" — i.e. some ambient/global
Python install, not the project's own `.venv`. That environment happened to
have old versions of several packages installed (`cryptography==41.0.7`,
`setuptools==68.1.2`, `urllib3==2.6.3`, `wheel==0.42.0`, `pip==24.0`) that
have nothing to do with what `requirements.txt` actually pins. The report's
own "Summary: Installed vs. Pinned" section already noticed this
("`requirements.txt` has secure versions but the environment doesn't") —
this doc closes the loop by confirming what the *real* pinned environment
audits to.

## Verification

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip   # matches ci.yml's "Install dependencies" step
pip install -r requirements.txt
pip install pip-audit
pip-audit --desc
# -> No known vulnerabilities found
```

Per-package disposition, checked directly against the built venv
(`pip list`) and the OSV/PyPI advisory ranges the original report cited:

| Package | Original report | requirements.txt pin | Real venv resolves to | Verdict |
|---|---|---|---|---|
| `cryptography` | 41.0.7 (vulnerable) | `==49.0.0` | 49.0.0 | already fixed by the existing pin |
| `setuptools` | 68.1.2 (vulnerable) | `==83.0.0` | 83.0.0 | already fixed by the existing pin |
| `urllib3` | 2.6.3 (vulnerable) | `==2.7.0` | 2.7.0 | already fixed by the existing pin |
| `wheel` | 0.42.0 (vulnerable) | `==0.46.3` | 0.46.3 | already fixed by the existing pin |
| `idna` | 3.11 (vulnerable, needs 3.15+) | `==3.18` | 3.18 | already fixed by the existing pin (report didn't check this pin) |
| `pip` | 24.0 (vulnerable) | not pinned (tooling, not a runtime dep) | 26.2 (after `pip install --upgrade pip`, as `ci.yml` already does) | not a real finding — CI already upgrades pip before installing |
| `httplib2` | 0.20.4 (vulnerable) | not a dependency of anything in `requirements.txt` | **not installed at all** | not a real dependency of this project — leftover in the scanned ambient environment |
| `pyjwt` | 2.7.0 (vulnerable, needs 2.13.0+) | not pinned directly (transitive) | `PyJWT==2.13.0` (pip's resolver picked a safe version on its own) | already safe |

`httplib2` in particular was a red herring: nothing in `requirements.txt`
(`google-auth`, `google-auth-oauthlib`, `gspread`, `google-cloud-language`)
depends on it — modern `google-api-core`/`google-auth` use `requests`, not
`httplib2` (that was the older `oauth2client`/`google-api-python-client`
pattern, neither of which is a dependency here). It simply wasn't installed
when the real `requirements.txt` environment was built.

## What was actually done as a result

- No `requirements.txt` changes — every pin was already correct.
- Added a `pip-audit` step to `.github/workflows/ci.yml`, run against the
  real CI-installed environment (after CI's existing `pip install --upgrade
  pip` + `pip install -r requirements.txt`), so a *genuine* future
  regression (someone loosening a pin to a vulnerable version) is caught
  automatically instead of relying on an ad hoc scan of whatever Python
  happens to be on someone's PATH.

## Related

- [`react_router_dom_ghsa_jjmj_open_redirect.md`](react_router_dom_ghsa_jjmj_open_redirect.md) —
  the other alert reviewed in the same pass, on the npm/webapp side; unlike
  this one, that one is a real, currently-unpatched vulnerability, tracked
  separately because it can't be closed by a version bump.

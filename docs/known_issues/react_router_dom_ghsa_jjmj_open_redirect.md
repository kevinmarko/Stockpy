# Known issue (tracked, not fixable by version bump): react-router-dom 6.30.4 — GHSA-jjmj-jmhj-qwj2 / CVE-2026-53668

**Status: open, no upstream fix exists for the 6.x line.** Verified not
exploitable by this app's current code (see "Exploitability audit" below),
but the Dependabot alert cannot be closed by editing `webapp/package.json` —
every choice was checked and each one is worse than leaving the pin as-is.
Tracked here so this stays a documented, evidence-backed decision rather
than a silently ignored alert.

## The alert

Dependabot flags `react-router-dom@6.30.4` (resolved in
`webapp/package-lock.json`) for **GHSA-jjmj-jmhj-qwj2 / CVE-2026-53668**
("React Router: Open redirect leading to XSS", CVSS 3.1
`AV:N/AC:H/PR:N/UI:R/S:C/C:H/I:L/A:N`, MODERATE, published 2026-07-23).
Confirmed directly against the GitHub Advisory Database / OSV record
(`https://api.osv.dev/v1/vulns/GHSA-jjmj-jmhj-qwj2`):

- `react-router-dom` (npm): affected `>= 6.30.2, <= 6.30.4`, **patched:
  none** — no 6.x release fixes this.
- `react-router` (npm, the v7 package): affected `>= 7.9.6, <= 7.12.0`,
  fixed in `7.13.0`.

The root cause (fix commit
[`3a5b5ad`](https://github.com/remix-run/react-router/commit/3a5b5ad0e5cf9918c646509563f5c41a89226ff3),
PR [#14718](https://github.com/remix-run/react-router/pull/14718)) is in
`resolvePath()`: a relative navigation target containing a raw `:` (e.g.
`"foo:bar"`) could be mis-normalized in a way that lets it resolve to an
unintended absolute/external URL — an open redirect, and from there
`javascript:`-style XSS if the browser treats the resolved value as a
navigable URI. `resolvePath()` backs `useNavigate()` and `<Link to>` in
every React Router mode, including plain Declarative Mode
(`<BrowserRouter>`), so — unlike the sibling advisory below — this one is
not scoped to data routers/loaders/actions.

## Why a version change doesn't fix it

There is no `react-router-dom` 6.x release that avoids both this advisory
and the still-numerically-listed range of the earlier
[GHSA-2w69-qvjg-hvjx / CVE-2026-22029](https://github.com/remix-run/react-router/security/advisories/GHSA-2w69-qvjg-hvjx)
(`@remix-run/router < 1.23.2`, HIGH severity). Checked every 6.30.x release
directly against the npm registry (`npm view react-router-dom@<version>
dependencies`):

| react-router-dom | bundled `@remix-run/router` | GHSA-jjmj (this alert) | GHSA-2w69 (@remix-run/router range) |
|---|---|---|---|
| 6.30.0 / 6.30.1 | 1.23.0 | not affected | affected (`< 1.23.2`) |
| 6.30.2 | 1.23.1 | **affected** | affected (`< 1.23.2`) |
| 6.30.3 | 1.23.2 | **affected** | not affected |
| 6.30.4 (current pin) | 1.23.3 | **affected** | not affected |

Downgrading to 6.30.1 would close *this* Dependabot alert but reopen a
HIGH-severity one for `@remix-run/router@1.23.0` — a strictly worse trade,
even though GHSA-2w69 explicitly does not apply to us either way (see next
section). The only real fix is migrating off `react-router-dom` 6.x to
`react-router` 7.13+ (a major-version rewrite: new package name, new APIs,
every screen using `<Routes>/<Route>/useNavigate/<Link>` — `webapp/src/App.tsx`
and ~25 screen files). That's a deliberate, separately-scoped migration, not
a dependency-alert cleanup.

## Exploitability audit (why this is safe to leave pinned for now)

`webapp/src/main.tsx` wraps the app in plain `<BrowserRouter>`;
`webapp/src/App.tsx` uses only `<Routes>`/`<Route>` — no `createBrowserRouter`,
no loaders/actions, no Data/Framework/RSC mode. That alone rules out
GHSA-2w69 (its advisory text explicitly excludes Declarative Mode).

For GHSA-jjmj (this alert), grepped every `useNavigate()`/`<Link to>` call
site in `webapp/src` for a dynamic (non-static-string) navigation target,
since the vulnerable path requires an attacker-influenced string containing
a raw `:` to reach `resolvePath()`:

- `RecommendedStocks.tsx` / `UniverseManager.tsx`:
  `` nav(`/symbol/${encodeURIComponent(symbol)}`) `` — `encodeURIComponent`
  turns any `:` into `%3A` before it reaches react-router, which defeats the
  specific colon-parsing bug.
- `Onboarding.tsx`: `` nav(`/pilots/${pilotId}`) `` — `pilotId` comes from
  `api.listPilots()` (our own backend's strategy registry), not from a URL
  query param, referrer, or any other externally-suppliable string.
- `PilotCard.tsx` / `OptionsMatrix.tsx`: `<Link to={\`/pilots/${pilot.id}\`}>`,
  `<Link to={\`/symbol/${d.Symbol}\`}>` — same, both values are backend API
  data (strategy IDs, ticker symbols), never reflected user/URL input.

Every remaining `nav(...)`/`<Link to>` call in the app passes a static
string literal. No call site in this app hands `resolvePath()` untrusted,
unescaped, colon-bearing input — so the app does not hit the exploitable
pattern today, independent of the library version.

## What would change this

- A `react-router-dom` 6.x patch release ships (check
  `npm view react-router-dom versions` for anything past 6.30.4, or re-check
  the OSV record above for a `fixed` version on the react-router-dom range).
- The router is migrated to `react-router` 7.13+ — track as separate work if
  prioritized.
- A future change introduces a dynamic `nav()`/`<Link to>` call fed by
  unescaped, externally-controlled input (URL query params, redirect
  targets from user-editable settings, etc.) — re-run this audit before
  shipping such a call site while this pin remains unpatched.

## Related

- CI now runs `pip-audit` on the Python side against the real
  `requirements.txt`-resolved environment (see `.github/workflows/ci.yml`) —
  added as part of the same review that produced this doc, after confirming
  the previously-reported "36 vulnerabilities" was `pip-audit` run against
  an unrelated ambient Python environment, not this project's actual pins.
  Rebuilding `.venv` from `requirements.txt` (Python 3.12, matching CI) and
  re-running `pip-audit` there returned zero findings.

# Known issue (resolved): react-router-dom 6.30.4 — GHSA-jjmj-jmhj-qwj2 / CVE-2026-53668

**Status: resolved.** Fixed in [PR #475](https://github.com/kevinmarko/Stockpy/pull/475),
which bumped `webapp/package.json`'s `react-router-dom` from `^6.26.2` to
`^7.18.2` (resolved: `react-router` + `react-router-dom` both `7.18.2`).
That PR also cleared two other Dependabot advisories on the 6.x line this
doc's original review pass didn't reach — GHSA-337j-9hxr-rhxg (SSR
hydration constructor injection) and GHSA-wrjc-x8rr-h8h6 (backslash open
redirect) — both fixed upstream in `react-router@7.18.0`, same as this one.
`npm audit` post-fix confirmed 0 moderate findings; `tsc --noEmit`,
`npm run build`, and the full `vitest run` (66 files / 753 tests) all
passed unchanged. Left in place below as the record of why a same-6.x-line
version bump was ruled out, since that reasoning remains correct even
though the ultimate fix went a different route (a major-version migration,
not a 6.x patch).

**Follow-up (same review, later same day):** PR #475 explicitly left one
more advisory unfixed and documented why — GHSA-qwww-vcr4-c8h2 ("React
Router: RSC Mode CSRF Bypass Allows Action Execution Before 400 Response"),
affecting `react-router@7.18.2` (range `>=7.12.0 <8.3.0`), fixable only at
`8.3.0+`, which requires React `>=19.2.7`. Since this app doesn't use RSC
mode, PR #475 correctly treated it as accepted risk rather than force a
React major-version bump for an inapplicable advisory. This was
subsequently completed anyway (React 18→19 + `react-router-dom`→`react-router@8.3.0`,
plus the `recharts` 2→3 and `@testing-library/react` 14→16 bumps React 19
required as peers) once the operator asked to close out the remaining
alerts. Verified: `npm audit` now reports 0 findings for the entire
react-router dependency chain (the separate, unrelated `vite-plugin-pwa`
dev-tooling chain was resolved too, via an `ejs` override rather than a
version bump — see
[`vite_plugin_pwa_workbox_dev_chain_unfixable.md`](vite_plugin_pwa_workbox_dev_chain_unfixable.md)),
`tsc --noEmit`/`npm run build`/`vitest run` (66 files / 753 tests) all pass.
v8's `react-router-dom` package is retired — all imports moved to
`"react-router"` (the unified package re-exports `BrowserRouter` from its
core entry point for a plain Declarative Mode SPA like this one; `"react-router/dom"`
is only needed for RSC/streaming-SSR hydration, which this app doesn't use).

## The alert

Dependabot flagged `react-router-dom@6.30.4` (resolved in
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

## Why a same-line (6.x) version bump wasn't the fix (historical — this is why #475 went to v7 instead)

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
| 6.30.4 (pre-fix pin) | 1.23.3 | **affected** | not affected |

Downgrading to 6.30.1 would have closed *this* Dependabot alert but reopened
a HIGH-severity one for `@remix-run/router@1.23.0` — a strictly worse
trade, even though GHSA-2w69 explicitly doesn't apply to this app either
way (its advisory excludes Declarative Mode, which is what this app uses —
see the exploitability audit below). The only real fix available in the 6.x
line was none; #475 correctly went straight to the v7 migration instead.

## Exploitability audit (why the unpatched pin was safe to leave in place for the review pass that produced this doc)

`webapp/src/main.tsx` wrapped the app in plain `<BrowserRouter>`;
`webapp/src/App.tsx` used only `<Routes>`/`<Route>` — no `createBrowserRouter`,
no loaders/actions, no Data/Framework/RSC mode (this is still true post-#475
migration). That alone ruled out GHSA-2w69 (its advisory text explicitly
excludes Declarative Mode).

For GHSA-jjmj, grepped every `useNavigate()`/`<Link to>` call site in
`webapp/src` for a dynamic (non-static-string) navigation target, since the
vulnerable path requires an attacker-influenced string containing a raw
`:` to reach `resolvePath()`:

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

Every remaining `nav(...)`/`<Link to>` call passed a static string literal.
No call site handed `resolvePath()` untrusted, unescaped, colon-bearing
input — the app never hit the exploitable pattern in practice, independent
of the library version. That's why leaving the pin in place for one review
pass (rather than force a same-line downgrade that would have net-worsened
the security posture) was the right call at the time — but it was still
correctly treated as an open alert requiring a real fix, which #475
delivered.

## Related

- [PR #475](https://github.com/kevinmarko/Stockpy/pull/475) — the fix.
- [`pip_audit_stale_ambient_env_false_positive.md`](pip_audit_stale_ambient_env_false_positive.md) —
  the other alert reviewed in the same pass, on the Python side (a false
  positive from scanning the wrong environment, not a real vulnerability).

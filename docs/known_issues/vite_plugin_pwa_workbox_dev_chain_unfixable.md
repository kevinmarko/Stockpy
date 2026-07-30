# Known issue (open, no compatible fix): vite-plugin-pwa's bundled workbox-build/rollup/jake dev-tooling chain

**Status: open, tracked, accepted risk.** `npm audit` reports 8 high-severity
findings rooted in `vite-plugin-pwa`'s transitive `workbox-build` →
`@trickfilm400/rollup-plugin-off-main-thread` → `ejs` → `jake` → `filelist`
→ `minimatch` → `brace-expansion` chain. All 8 are the same underlying
cluster (one root cause fanning out through the dependency tree), not 8
independent vulnerabilities.

## Why `npm audit fix` doesn't work here

`vite-plugin-pwa@1.3.0` is the **latest published release** — there is no
newer version that drops the vulnerable `workbox-build` chain.
`npm audit`'s own suggested fix is to downgrade to `vite-plugin-pwa@1.2.0`,
but that version's peer dependency range is `vite: "^3.1.0 || ^4.0.0 ||
^5.0.0 || ^6.0.0 || ^7.0.0"` — it does not include `vite@8.x`, which is
what `webapp/package.json` already pins (`"vite": "8.1.5"`, unrelated to
this review). Confirmed directly:

```bash
npm view vite-plugin-pwa@1.2.0 peerDependencies
# { vite: '^3.1.0 || ^4.0.0 || ^5.0.0 || ^6.0.0 || ^7.0.0', ... }
npm view vite-plugin-pwa@1.3.0 peerDependencies
# { vite: '^3.1.0 || ^4.0.0 || ^5.0.0 || ^6.0.0 || ^7.0.0 || ^8.0.0', ... }
npm view vite-plugin-pwa dist-tags.latest
# 1.3.0
```

Attempting the downgrade (`npm install`) fails with `ERESOLVE`:

```
npm error peer vite@"^3.1.0 || ^4.0.0 || ^5.0.0 || ^6.0.0 || ^7.0.0" from vite-plugin-pwa@1.2.0
```

So the only two real options are: stay on `vite-plugin-pwa@1.3.0` (current
pin) with these 8 findings open, or downgrade `vite` itself from 8.x to
7.x to unlock the `vite-plugin-pwa@1.2.0` downgrade — a strictly worse
trade (reverting a major build-tool version to route around a dev-only
vulnerability chain).

## Why this is dev/build-time only, not a production runtime risk

Every package in this chain — `workbox-build`, `rollup-plugin-off-main-thread`,
`ejs`, `jake`, `filelist`, `minimatch`, `brace-expansion` — is a
**build-time dependency of `vite-plugin-pwa`**, invoked only during
`vite build` / `vite dev` to generate the service worker
(`workbox-build`'s `generateSW` mode, per `webapp/vite.config.ts`'s
`VitePWA({...})` call). None of it ships inside the built `dist/` bundle
or executes in an end user's browser. The vulnerabilities themselves
(`brace-expansion` DoS via unbounded regex expansion, and similar
resource-exhaustion issues further up the chain) require attacker-supplied
input reaching these functions — this app's build never processes
untrusted, externally-supplied strings through `workbox-build`/`jake`/`ejs`;
it only globs and hashes the app's own known asset files
(`globPatterns: ["**/*.{js,css,html,svg,png,ico,woff2}"]`).

## What would change this

- `vite-plugin-pwa` (or `workbox-build` directly) ships a release that
  drops the vulnerable chain while still supporting `vite@8.x` — re-check
  with `npm view vite-plugin-pwa versions` / re-run `npm audit`.
- The project deliberately downgrades `vite` below 8.x for an unrelated
  reason, at which point `vite-plugin-pwa@1.2.0` becomes installable again
  and should be re-evaluated (note: 1.2.0's `workbox-build: ^7.4.0` range
  is not confirmed to resolve outside the vulnerable range either — verify
  with a real install + `npm audit` before assuming it clears the chain).
- `workbox-build`/`vite-plugin-pwa` publish an advisory-specific patch
  release for the current major line.

## Related

- [`react_router_dom_ghsa_jjmj_open_redirect.md`](react_router_dom_ghsa_jjmj_open_redirect.md) —
  the other alert reviewed in the same pass; unlike this one, that had a
  real fix available (a major-version migration) and was completed.
- `webapp/vite.config.ts` — the `VitePWA({...})` call that pulls in this
  dependency chain.

# Known issue (resolved): vite-plugin-pwa's bundled workbox-build/rollup/jake dev-tooling chain

**Status: resolved**, via a targeted `overrides` entry in `webapp/package.json`
— not a version bump of `vite-plugin-pwa` itself (see "Why the obvious fixes
don't work" below; this doc was originally written when no fix looked
available at all). `npm audit` now reports **0 findings** (was 8 high).

## The alert

`npm audit` flagged 8 high-severity findings rooted in `vite-plugin-pwa`'s
transitive `workbox-build` → `@trickfilm400/rollup-plugin-off-main-thread`
→ `ejs` → `jake` → `filelist` → `minimatch` → `brace-expansion` chain. All
8 were the same underlying cluster (one root cause fanning out through the
dependency tree), not 8 independent vulnerabilities.

## Why the obvious fixes don't work

`vite-plugin-pwa@1.3.0` is the **latest published release** — no newer
version drops the vulnerable `workbox-build` chain. `npm audit`'s own
suggested fix (downgrade to `vite-plugin-pwa@1.2.0`) doesn't work either:
that version's peer range is `vite: "^3.1.0 || ... || ^7.0.0"`, which
doesn't include the already-pinned `vite@8.1.5` — confirmed by a failed
`ERESOLVE` install attempt (`npm error peer vite@"^3.1.0 || ... || ^7.0.0"
from vite-plugin-pwa@1.2.0`).

## The actual fix: an `overrides` entry, not a version bump

`npm ls @trickfilm400/rollup-plugin-off-main-thread ejs jake filelist
minimatch brace-expansion workbox-build vite-plugin-pwa` showed the real
shape of the problem — `workbox-build@7.4.1` resolves TWO separate
`minimatch`/`brace-expansion` chains:

```
vite-plugin-pwa@1.3.0
`-- workbox-build@7.4.1
  +-- @trickfilm400/rollup-plugin-off-main-thread@3.0.0-pre1  (vulnerable chain)
  |   `-- ejs@3.1.10
  |     `-- jake@10.9.4
  |       `-- filelist@1.0.6
  |         `-- minimatch@5.1.9
  |           `-- brace-expansion@2.1.4    <-- flagged (fix: >=5.0.8)
  `-- glob@11.1.0                          (already-safe chain)
    `-- minimatch@10.2.6
      `-- brace-expansion@5.0.9            <-- already safe
```

`glob`'s own chain already resolves to safe, current versions. The
vulnerable path is entirely inside `@trickfilm400/rollup-plugin-off-main-thread`'s
`ejs@^3.1.6` dependency range, which drags in `jake` → `filelist` →
`minimatch@5.1.9` → `brace-expansion@2.1.4`. Critically:

- `ejs@6.0.1` (the current latest — checked with `npm view ejs
  dist-tags.latest`) has **zero dependencies**. EJS dropped its `jake`
  dependency entirely in a later major version — `npm view ejs@3.1.10
  dependencies` shows `{ jake: '^10.8.5' }`; `npm view ejs@6.0.1
  dependencies` shows nothing.
- `@trickfilm400/rollup-plugin-off-main-thread`'s only use of `ejs` (checked
  directly in its published source, `index.js`) is a single call:
  `ejs.render(opts.loader, opts)` — the most basic, long-stable public API
  EJS has, present unchanged since EJS v2. A 3→6 major bump carries no
  realistic API-compatibility risk for this one call.

So overriding `ejs` alone to `^6.0.1` removes `jake`/`filelist`/the
vulnerable `minimatch`/`brace-expansion` instance from the tree entirely —
`@trickfilm400/rollup-plugin-off-main-thread` no longer needs them once its
own `ejs` dependency stops needing them.

```jsonc
// webapp/package.json
"overrides": {
  "ejs": "^6.0.1"
}
```

Post-override tree (`npm ls ejs jake filelist`):

```
vite-plugin-pwa@1.3.0
`-- workbox-build@7.4.1
  `-- @trickfilm400/rollup-plugin-off-main-thread@3.0.0-pre1
    `-- ejs@6.0.1 overridden
```

`jake` and `filelist` no longer appear in the tree at all.

## Why this is safe

Every package in the original chain (`workbox-build`,
`rollup-plugin-off-main-thread`, `ejs`, `jake`, `filelist`, `minimatch`,
`brace-expansion`) is a **build-time dependency of `vite-plugin-pwa`**,
invoked only during `vite build`/`vite dev` to generate the service worker
(`workbox-build`'s `generateSW` mode, per `webapp/vite.config.ts`'s
`VitePWA({...})` call) — none of it ships inside `dist/` or runs in an end
user's browser, so even before this fix the practical exposure was
low. The override only touches `ejs`'s resolved version for this one
transitive consumer (`overrides` in npm only replaces what's actually
requested at each point in the graph consistent with the override; nothing
else in `webapp/package.json`'s own dependencies uses `ejs` directly).

## Verification

```bash
npm install       # applies the override; "found 0 vulnerabilities"
npm run typecheck # tsc --noEmit — passes
npm run build     # vite build + PWA generateSW — dist/sw.js and
                   # dist/workbox-*.js generated identically to before
                   # ("precache 16 entries (1126.81 KiB)", same as pre-fix)
npm run test      # vitest run — 66 files / 753 tests pass, unchanged
```

## Related

- [`react_router_dom_ghsa_jjmj_open_redirect.md`](react_router_dom_ghsa_jjmj_open_redirect.md) —
  the other alert reviewed in the same overall pass.
- `webapp/vite.config.ts` — the `VitePWA({...})` call that pulls in this
  dependency chain.
- `webapp/package.json`'s `overrides` field — if a future `npm install`
  ever needs a different `ejs` consumer somewhere else in the tree, revisit
  whether the override should narrow to a nested path
  (`"@trickfilm400/rollup-plugin-off-main-thread": { "ejs": "^6.0.1" }`)
  instead of the current package-wide override, to avoid silently touching
  an unrelated `ejs` user.

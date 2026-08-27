# Gap 6: PWA Runtime Caching Implementation Plan

This plan details how we will introduce runtime caching to the Stockpy Pilots PWA so it caches API JSON requests via a NetworkFirst strategy, matching the requirements of Gap 6.

## Proposed Changes

### `webapp/`

#### [NEW] `webapp/vite.pwa-runtime-caching.ts`
We will create this module to export `buildApiRuntimeCaching(baseUrls: string[]): RuntimeCaching[]`.
It will contain a rule mapping `GET` requests matched against the given `baseUrls` (excluding those ending in `/stream` to avoid caching SSE endpoints).
The handler will be `"NetworkFirst"`, with the following options:
- `networkTimeoutSeconds`: 4
- `expiration`: `{ maxEntries: 100, maxAgeSeconds: 120 }`
- `cacheableResponse`: `{ statuses: [0, 200] }`

#### [MODIFY] `webapp/vite.config.ts`
We will modify the exported `defineConfig` to use the function form: `export default defineConfig(({ mode }) => { ... })`.
We will import `loadEnv` from `"vite"`.
We will call `loadEnv(mode, process.cwd(), "")` to read the environment variables, extract `VITE_API_BASE_URL`, `VITE_DATA_API_BASE_URL`, `VITE_METRICS_API_BASE_URL`, and `VITE_CONTROL_API_BASE_URL` (applying the default `http://localhost:860x` fallbacks if empty).
We will pass these resolved URLs to `buildApiRuntimeCaching(baseUrls)` and inject the result into `workbox.runtimeCaching` within the `VitePWA` options.

#### [NEW] `webapp/vite.pwa-runtime-caching.test.ts`
We will add tests to verify the routing logic. For example, verifying `urlPattern` matches the right origins, `GET` method, and correctly excludes `/stream` paths.

### `docs/`

#### [MODIFY] `docs/architecture/webapp-and-gui.md`
We will update this documentation to document the newly added PWA runtime caching architecture.

## Verification Plan
### Automated Tests
- `npm run --prefix webapp test vite.pwa-runtime-caching.test.ts`
- `npm run --prefix webapp test` (ensures nothing broke).
- `npm run --prefix webapp typecheck`

### Manual Verification
- Will verify Vite build succeeds.

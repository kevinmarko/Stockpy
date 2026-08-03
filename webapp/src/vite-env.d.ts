/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />
/// <reference types="vite-plugin-pwa/react" />

// Every VITE_* key this app reads. Keep in sync with `.env.example`,
// `README.md`'s env table, and `src/config/env.ts` — enforced by
// `src/config/envDrift.test.ts`. Without a declaration here a key typechecks
// only via vite/client's permissive index signature, so a typo'd name is
// silently `undefined` with no compile error.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DATA_API_BASE_URL?: string;
  readonly VITE_METRICS_API_BASE_URL?: string;
  readonly VITE_CONTROL_API_BASE_URL?: string;
  readonly VITE_API_TOKEN?: string;
  readonly VITE_USE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

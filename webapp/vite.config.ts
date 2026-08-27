/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { buildApiRuntimeCaching } from "./vite.pwa-runtime-caching";

// Stockpy Pilots — mobile-first installable PWA.
// The service worker + manifest are produced by vite-plugin-pwa.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const baseUrls = [
    env.VITE_API_BASE_URL || "http://localhost:8602",
    env.VITE_DATA_API_BASE_URL || "http://localhost:8603",
    env.VITE_METRICS_API_BASE_URL || "http://localhost:8604",
    env.VITE_CONTROL_API_BASE_URL || "http://localhost:8601",
  ];

  return {
    plugins: [
      react(),
      VitePWA({
        // "prompt" (not "autoUpdate") so a new SW version surfaces as a
        // `needRefresh` flag the UI can show (PwaStatusDrawer) instead of
        // silently force-reloading every open tab the instant it activates.
        registerType: "prompt",
        // We register the SW ourselves via `virtual:pwa-register/react`
        // (usePwaStatus.ts) so the UI can see its state — disable the
        // plugin's own auto-injected <script> to avoid double registration.
        injectRegister: false,
        includeAssets: ["favicon.svg", "icon.svg", "icon-192.png", "icon-512.png"],
        devOptions: {
          // let the SW register in `npm run dev` so the install flow is testable
          enabled: true,
        },
        workbox: {
          globPatterns: ["**/*.{js,css,html,svg,png,ico,woff2}"],
          navigateFallback: "index.html",
          // Workbox's own default (2 MiB) started rejecting the build outright
          // once the main index-*.js chunk crossed it (2,090 kB on main ->
          // 2,110 kB after the Symbol Screener screen landed, PR #826) --
          // vite-plugin-pwa treats exceeding the limit as a hard build error,
          // not a warning, so this isn't cosmetic. Raised with real headroom
          // (not just past today's size) since this is a growing single-page
          // app and the next screen added would trip the same wall again at
          // the old default. Actual code-splitting (dynamic import() per
          // route) is the real long-term fix for the underlying bundle-size
          // growth -- this raises the ceiling, it doesn't lower the bundle.
          maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
          runtimeCaching: buildApiRuntimeCaching(baseUrls),
        },
        manifest: {
          name: "Stockpy Pilots",
          short_name: "Pilots",
          description:
            "Browse and follow Stockpy quant strategy Pilots — honest backtests, paper-first.",
          theme_color: "#0b0e11",
          background_color: "#0b0e11",
          display: "standalone",
          orientation: "portrait",
          start_url: "/",
          scope: "/",
          icons: [
            {
              src: "icon.svg",
              sizes: "any",
              type: "image/svg+xml",
              purpose: "any maskable",
            },
            {
              src: "icon-192.png",
              sizes: "192x192",
              type: "image/png",
              purpose: "any",
            },
            {
              src: "icon-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "any",
            },
            {
              src: "icon-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "maskable",
            },
          ],
        },
      }),
    ],
    server: {
      host: true,
      port: 5173,
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes("node_modules")) {
              if (id.includes("recharts") || id.includes("d3-")) {
                return "vendor-charts";
              }
              if (
                id.includes("react-router") ||
                id.includes("/react-dom/") ||
                id.includes("/react/")
              ) {
                return "vendor-react";
              }
            }
          },
        },
      },
    },
    // Vitest: offline, deterministic unit tests — the mock API contract plus
    // screen/component tests (Testing Library) and the live client (mocked fetch).
    // jsdom gives the mock layer a localStorage (follows persistence) to run against.
    test: {
      environment: "jsdom",
      include: ["src/**/*.test.{ts,tsx}", "vite.pwa-runtime-caching.test.ts"],
      setupFiles: ["./src/test-setup.ts"],
      // Vitest's own default (5000ms) was the same ceiling as the explicit
      // `findByText(..., {timeout: 5000})` bumps that used to be applied to a
      // few async-job-status Settings.test.tsx assertions -- so under real CI
      // load the whole test was killed by THIS timeout at the same moment,
      // making those inner bumps a no-op. PR #597 later root-caused those
      // specific assertions as a stale-DOM-reference race (not a timing
      // problem) and replaced the ad hoc timeout bumps with a structural
      // `waitForPresence()` helper that re-queries fresh on every retry, at a
      // 5000ms inner budget (`ASYNC_JOB_CHAIN_TIMEOUT`) -- see that helper's
      // comment in Settings.test.tsx for the full writeup. This outer bound is
      // kept elevated above Vitest's 5000ms default regardless: main's own CI
      // has independently shown the identical Settings assertions occasionally
      // exceeding 1000-5000ms under sustained GitHub Actions runner
      // contention, a genuine (if separate) scheduling-latency concern a
      // higher ceiling costs nothing to accommodate on the happy path.
      testTimeout: 30000,
    },
  };
});

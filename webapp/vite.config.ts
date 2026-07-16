/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Stockpy Pilots — mobile-first installable PWA.
// The service worker + manifest are produced by vite-plugin-pwa.
export default defineConfig({
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
  // Vitest: offline, deterministic unit tests — the mock API contract plus
  // screen/component tests (Testing Library) and the live client (mocked fetch).
  // jsdom gives the mock layer a localStorage (follows persistence) to run against.
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
  },
});

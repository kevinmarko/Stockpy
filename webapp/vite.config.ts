import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// Stockpy Pilots — mobile-first installable PWA.
// The service worker + manifest are produced by vite-plugin-pwa.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg", "icon.svg"],
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
        ],
      },
    }),
  ],
  server: {
    host: true,
    port: 5173,
  },
});

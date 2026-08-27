import type { RuntimeCaching } from "workbox-build";

export function buildApiRuntimeCaching(baseUrls: string[]): RuntimeCaching[] {
  // Extract just the origins to avoid trailing slash mismatches
  const allowedOrigins = baseUrls
    .filter(Boolean)
    .map((url) => {
      try {
        return new URL(url).origin;
      } catch {
        return null;
      }
    })
    .filter(Boolean) as string[];

  // Return a single RuntimeCaching rule handling all API bases
  return [
    {
      urlPattern: ({ url, request }) => {
        return (
          request.method === "GET" &&
          allowedOrigins.includes(url.origin) &&
          !url.pathname.endsWith("/stream")
        );
      },
      // NetworkFirst, deliberately NOT StaleWhileRevalidate: this app
      // displays live quotes/positions/portfolio values, and SWR would
      // serve a stale cached financial figure as the FIRST paint on every
      // load, which risks reading as fresh data. NetworkFirst (with a
      // short networkTimeoutSeconds below) prefers live data whenever the
      // network is up and only falls back to cache on a genuine failure
      // or timeout.
      handler: "NetworkFirst",
      options: {
        cacheName: "api-cache",
        networkTimeoutSeconds: 4,
        expiration: {
          maxEntries: 100,
          maxAgeSeconds: 120,
        },
        cacheableResponse: {
          statuses: [0, 200],
        },
      },
    },
  ];
}

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

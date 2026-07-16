import { afterEach, describe, expect, it, vi } from "vitest";
import { readCacheEntry, writeCacheEntry } from "./offlineCache";

describe("offlineCache", () => {
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("round-trips a written value with an ISO cachedAt timestamp", () => {
    writeCacheEntry("/pilots", [{ id: "trend-following" }]);
    const entry = readCacheEntry<{ id: string }[]>("/pilots");
    expect(entry).not.toBeNull();
    expect(entry!.data).toEqual([{ id: "trend-following" }]);
    expect(Number.isNaN(Date.parse(entry!.cachedAt))).toBe(false);
  });

  it("namespaces keys so two different paths never collide", () => {
    writeCacheEntry("/pilots", "a");
    writeCacheEntry("/portfolio", "b");
    expect(readCacheEntry<string>("/pilots")?.data).toBe("a");
    expect(readCacheEntry<string>("/portfolio")?.data).toBe("b");
  });

  it("returns null for a key that was never written", () => {
    expect(readCacheEntry("/never-written")).toBeNull();
  });

  it("returns null (never throws) for malformed JSON already in storage", () => {
    localStorage.setItem("stockpy.cache.v1:/pilots", "{not json");
    expect(readCacheEntry("/pilots")).toBeNull();
  });

  it("returns null for a stored value missing the cachedAt field", () => {
    localStorage.setItem("stockpy.cache.v1:/pilots", JSON.stringify({ data: [] }));
    expect(readCacheEntry("/pilots")).toBeNull();
  });

  it("a write failure (quota exceeded) is swallowed, not thrown", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });
    expect(() => writeCacheEntry("/pilots", [1, 2, 3])).not.toThrow();
  });

  it("a corrupt localStorage.getItem throw degrades to null, not an exception", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });
    expect(readCacheEntry("/pilots")).toBeNull();
  });

  it("overwriting a key replaces the previous cachedAt/data pair", () => {
    writeCacheEntry("/pilots", "first");
    const first = readCacheEntry<string>("/pilots");
    writeCacheEntry("/pilots", "second");
    const second = readCacheEntry<string>("/pilots");
    expect(first!.data).toBe("first");
    expect(second!.data).toBe("second");
  });
});

/**
 * customViews.test.ts
 *
 * Covers the real behavior that matters for this being an honest feature and
 * not a repeat of PR #670's fabricated "Save to Dashboard" stub:
 * localStorage persistence actually round-trips, a repeat save with the same
 * name updates in place instead of duplicating, the property that makes the
 * sidebar update live without a page reload (both same-process subscribers
 * AND the cross-tab `storage` event path), and that a real localStorage
 * write failure is reported honestly rather than swallowed into a
 * fabricated-looking success (a code-review finding on this exact file).
 */
import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  addOrUpdateView,
  removeView,
  useCustomViews,
  __resetCustomViewsForTests,
  type CustomViewWidgets,
} from "./customViews";

const STORAGE_KEY = "stockpy.custom-views:v1";

const ALL_WIDGETS_OFF: CustomViewWidgets = {
  edgeByStrategy: false,
  symbolOverlay: false,
  aiChat: false,
  pilotsTable: false,
  sentimentMini: false,
  portfolioHeat: false,
  optionsDirective: false,
  signalBreakdown: false,
  macroRegime: false,
};
const ALL_WIDGETS: CustomViewWidgets = Object.fromEntries(
  Object.keys(ALL_WIDGETS_OFF).map((k) => [k, true])
) as unknown as CustomViewWidgets;
const NO_WIDGETS: CustomViewWidgets = ALL_WIDGETS_OFF;

beforeEach(() => {
  __resetCustomViewsForTests();
});

describe("customViews store", () => {
  it("creates a view with a slugified name and persists it to localStorage", () => {
    const { view, persisted } = addOrUpdateView({ name: "Momentum Desk!", widgets: ALL_WIDGETS });
    expect(persisted).toBe(true);
    expect(view.slug).toBe("momentum-desk");
    expect(view.name).toBe("Momentum Desk!");

    const raw = localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    const stored = JSON.parse(raw as string);
    expect(stored).toHaveLength(1);
    expect(stored[0].slug).toBe("momentum-desk");
  });

  it("a repeat save with the same name updates the existing view in place, not a duplicate", () => {
    const { view: first } = addOrUpdateView({ name: "Momentum Desk", widgets: ALL_WIDGETS });
    const { view: second } = addOrUpdateView({ name: "Momentum Desk", widgets: NO_WIDGETS });

    expect(second.id).toBe(first.id);
    expect(second.createdAt).toBe(first.createdAt);
    expect(second.widgets).toEqual(NO_WIDGETS);

    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
    expect(raw).toHaveLength(1);
  });

  it("the slug is the view's identity -- a different name slugifying to the same value renames the same view rather than colliding on the same /app/:slug route", () => {
    const { view: a } = addOrUpdateView({ name: "Foo Bar", widgets: ALL_WIDGETS });
    const { view: b } = addOrUpdateView({ name: "foo-bar", widgets: NO_WIDGETS });

    expect(b.id).toBe(a.id);
    expect(b.slug).toBe("foo-bar");
    expect(b.name).toBe("foo-bar");

    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
    expect(raw).toHaveLength(1);
  });

  it("REGRESSION (review finding): two DIFFERENT names that both collapse to no alphanumeric characters get DIFFERENT slugs, not a shared 'view' collision", () => {
    const { view: a } = addOrUpdateView({ name: "!!!", widgets: ALL_WIDGETS });
    const { view: b } = addOrUpdateView({ name: "???", widgets: ALL_WIDGETS });

    expect(a.slug).not.toBe(b.slug);
    expect(a.id).not.toBe(b.id);

    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
    expect(raw).toHaveLength(2);
  });

  it("the SAME punctuation-only name saved twice still updates in place (the fallback slug is deterministic)", () => {
    const { view: first } = addOrUpdateView({ name: "!!!", widgets: ALL_WIDGETS });
    const { view: second } = addOrUpdateView({ name: "!!!", widgets: NO_WIDGETS });

    expect(second.id).toBe(first.id);
    expect(second.slug).toBe(first.slug);
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
    expect(raw).toHaveLength(1);
  });

  it("removeView deletes the entry and persists the removal", () => {
    const { view } = addOrUpdateView({ name: "Temp View", widgets: ALL_WIDGETS });
    const { persisted } = removeView(view.id);
    expect(persisted).toBe(true);

    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
    expect(raw).toHaveLength(0);
  });

  it("loads existing views from localStorage on first read", () => {
    addOrUpdateView({ name: "Persisted View", widgets: ALL_WIDGETS });
    // Simulate a fresh module read (e.g. a different component mounting for
    // the first time) by resetting the in-memory copy from storage.
    __resetCustomViewsForTests();

    const { result } = renderHook(() => useCustomViews());
    expect(result.current.views).toHaveLength(1);
    expect(result.current.views[0].name).toBe("Persisted View");
  });

  it("two independent useCustomViews() subscribers stay in sync -- the property that keeps the sidebar live", () => {
    const subscriberA = renderHook(() => useCustomViews());
    const subscriberB = renderHook(() => useCustomViews());

    expect(subscriberA.result.current.views).toHaveLength(0);
    expect(subscriberB.result.current.views).toHaveLength(0);

    act(() => {
      subscriberA.result.current.addOrUpdateView({ name: "Shared View", widgets: ALL_WIDGETS });
    });

    // subscriberB never called addOrUpdateView itself -- it must still see
    // the new view, because both read from the one true external store.
    expect(subscriberB.result.current.views).toHaveLength(1);
    expect(subscriberB.result.current.views[0].name).toBe("Shared View");

    act(() => {
      subscriberB.result.current.removeView(subscriberB.result.current.views[0].id);
    });
    expect(subscriberA.result.current.views).toHaveLength(0);
  });

  it("REGRESSION (review finding): a real cross-tab `storage` event re-syncs subscribers from localStorage", () => {
    const { result } = renderHook(() => useCustomViews());
    expect(result.current.views).toHaveLength(0);

    // Simulate ANOTHER tab writing a view: mutate localStorage directly (not
    // via addOrUpdateView, which only fires this tab's own in-memory update)
    // then dispatch the real browser event a second tab's write would raise.
    // Per the DOM spec, the WRITING tab never receives its own `storage`
    // event -- only other tabs do -- which is exactly what this simulates.
    const otherTabView = {
      id: "other-tab-id",
      name: "From Another Tab",
      slug: "from-another-tab",
      widgets: ALL_WIDGETS,
      createdAt: "2026-01-01T00:00:00.000Z",
      updatedAt: "2026-01-01T00:00:00.000Z",
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify([otherTabView]));

    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    });

    expect(result.current.views).toHaveLength(1);
    expect(result.current.views[0].name).toBe("From Another Tab");
  });

  it("a `storage` event for an unrelated key is ignored", () => {
    const { result } = renderHook(() => useCustomViews());
    act(() => {
      addOrUpdateView({ name: "Mine", widgets: ALL_WIDGETS });
    });
    expect(result.current.views).toHaveLength(1);

    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: "some-other-key" }));
    });

    // Unaffected -- still the one real view, not reset to whatever
    // (nothing) localStorage under the wrong key would imply.
    expect(result.current.views).toHaveLength(1);
    expect(result.current.views[0].name).toBe("Mine");
  });

  describe("REGRESSION (honesty review finding): a real localStorage write failure is reported, never silently swallowed into a fabricated success", () => {
    let setItemSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      setItemSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("QuotaExceededError");
      });
    });

    afterEach(() => {
      setItemSpy.mockRestore();
    });

    it("addOrUpdateView reports persisted: false when the write throws, while still updating the in-memory store for this tab's session", () => {
      const { view, persisted } = addOrUpdateView({ name: "Doomed Save", widgets: ALL_WIDGETS });
      expect(persisted).toBe(false);

      // Still usable for the rest of this session -- the point is honest
      // REPORTING of the failure, not refusing to let the operator use the
      // view they just "saved".
      const { result } = renderHook(() => useCustomViews());
      expect(result.current.views.some((v) => v.id === view.id)).toBe(true);
    });

    it("removeView reports persisted: false when the write throws", () => {
      setItemSpy.mockRestore(); // let the initial create through
      const { view } = addOrUpdateView({ name: "To Delete", widgets: ALL_WIDGETS });

      setItemSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("QuotaExceededError");
      });
      const { persisted } = removeView(view.id);
      expect(persisted).toBe(false);
    });
  });
});

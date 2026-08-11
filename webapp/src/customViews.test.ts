/**
 * customViews.test.ts
 *
 * Covers the real behavior that matters for this being an honest feature and
 * not a repeat of PR #670's fabricated "Save to Dashboard" stub:
 * localStorage persistence actually round-trips, a repeat save with the same
 * name updates in place instead of duplicating, and -- the property that
 * makes the sidebar update live without a page reload -- two independent
 * `useCustomViews()` subscribers stay in sync with each other.
 */
import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import {
  addOrUpdateView,
  removeView,
  useCustomViews,
  __resetCustomViewsForTests,
  type CustomViewWidgets,
} from "./customViews";

const ALL_WIDGETS: CustomViewWidgets = { edgeByStrategy: true, symbolOverlay: true, aiChat: true };
const NO_WIDGETS: CustomViewWidgets = { edgeByStrategy: false, symbolOverlay: false, aiChat: false };

beforeEach(() => {
  __resetCustomViewsForTests();
});

describe("customViews store", () => {
  it("creates a view with a slugified name and persists it to localStorage", () => {
    const view = addOrUpdateView({ name: "Momentum Desk!", widgets: ALL_WIDGETS });
    expect(view.slug).toBe("momentum-desk");
    expect(view.name).toBe("Momentum Desk!");

    const raw = localStorage.getItem("stockpy.custom-views:v1");
    expect(raw).not.toBeNull();
    const stored = JSON.parse(raw as string);
    expect(stored).toHaveLength(1);
    expect(stored[0].slug).toBe("momentum-desk");
  });

  it("a repeat save with the same name updates the existing view in place, not a duplicate", () => {
    const first = addOrUpdateView({ name: "Momentum Desk", widgets: ALL_WIDGETS });
    const second = addOrUpdateView({ name: "Momentum Desk", widgets: NO_WIDGETS });

    expect(second.id).toBe(first.id);
    expect(second.createdAt).toBe(first.createdAt);
    expect(second.widgets).toEqual(NO_WIDGETS);

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(1);
  });

  it("the slug is the view's identity -- a different name slugifying to the same value renames the same view rather than colliding on the same /app/:slug route", () => {
    const a = addOrUpdateView({ name: "Foo Bar", widgets: ALL_WIDGETS });
    const b = addOrUpdateView({ name: "foo-bar", widgets: NO_WIDGETS });

    expect(b.id).toBe(a.id);
    expect(b.slug).toBe("foo-bar");
    expect(b.name).toBe("foo-bar");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(1);
  });

  it("removeView deletes the entry and persists the removal", () => {
    const view = addOrUpdateView({ name: "Temp View", widgets: ALL_WIDGETS });
    removeView(view.id);

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
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
});

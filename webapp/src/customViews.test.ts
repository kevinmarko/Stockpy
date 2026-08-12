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
  importViews,
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

  describe("addOrUpdateView persists widgetOrder/widgetConfigs", () => {
    it("defaults widgetOrder to the active widgets, in the caller-provided key order, when none is given", () => {
      const { view } = addOrUpdateView({ name: "Ordered", widgets: { ...ALL_WIDGETS_OFF, macroRegime: true, edgeByStrategy: true } });
      expect(view.widgetOrder).toEqual(["edgeByStrategy", "macroRegime"]);
      expect(view.widgetConfigs).toEqual({});
    });

    it("stores an explicit, already-well-formed widgetOrder/widgetConfigs unchanged", () => {
      // NOTE: this input's widgetOrder already exactly matches its active
      // widgets, so it round-trips unchanged even now that addOrUpdateView
      // reconciles widgetOrder against widgets (see the REGRESSION tests
      // below for cases where reconciliation actually changes the stored
      // order) -- this test was previously titled "...verbatim", which is
      // no longer literally true of the code path even though the observed
      // result here is identical.
      const { view } = addOrUpdateView({
        name: "Configured",
        widgets: { ...ALL_WIDGETS_OFF, symbolOverlay: true, macroRegime: true },
        widgetOrder: ["macroRegime", "symbolOverlay"],
        widgetConfigs: { symbolOverlay: { defaultTicker: "TSLA" } },
      });
      expect(view.widgetOrder).toEqual(["macroRegime", "symbolOverlay"]);
      expect(view.widgetConfigs).toEqual({ symbolOverlay: { defaultTicker: "TSLA" } });
    });

    it("REGRESSION (review finding): addOrUpdateView reconciles a caller-supplied widgetOrder against widgets instead of storing it verbatim -- an inactive widget in the order is dropped and an active widget missing from it is appended", () => {
      const { view } = addOrUpdateView({
        name: "Reconciled",
        widgets: { ...ALL_WIDGETS_OFF, symbolOverlay: true, macroRegime: true },
        // Lists an INACTIVE widget (pilotsTable) and OMITS an active one
        // (macroRegime) -- exactly the kind of stale/hand-built order a
        // duplicated or manually-constructed caller could pass, which
        // CustomView.tsx would previously have trusted verbatim (rendering
        // a phantom pilotsTable-shaped gap and silently never rendering
        // macroRegime).
        widgetOrder: ["pilotsTable", "symbolOverlay"],
      });
      expect(view.widgetOrder).toEqual(["symbolOverlay", "macroRegime"]);
    });

    it("REGRESSION (review finding): addOrUpdateView dedupes a caller-supplied widgetOrder that repeats the same key", () => {
      const { view } = addOrUpdateView({
        name: "Deduped",
        widgets: { ...ALL_WIDGETS_OFF, symbolOverlay: true, macroRegime: true },
        widgetOrder: ["symbolOverlay", "macroRegime", "symbolOverlay"],
      });
      expect(view.widgetOrder).toEqual(["symbolOverlay", "macroRegime"]);
    });

    it("editing by id and renaming to a name that collides with a DIFFERENT view's slug gets a disambiguated slug instead of overwriting that other view", () => {
      const { view: other } = addOrUpdateView({ name: "Momentum Desk", widgets: ALL_WIDGETS });
      const { view: mine } = addOrUpdateView({ name: "Original Name", widgets: NO_WIDGETS });

      const { view: renamed } = addOrUpdateView({ id: mine.id, name: "Momentum Desk", widgets: NO_WIDGETS });

      expect(renamed.id).toBe(mine.id);
      expect(renamed.slug).not.toBe(other.slug);

      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
      expect(raw).toHaveLength(2);
      // The other, pre-existing view under that real slug is untouched.
      expect(raw.find((v: any) => v.id === other.id).slug).toBe(other.slug);
    });
  });

  describe("importViews", () => {
    it("imports a well-formed export (as handleExport in CreateDataApp.tsx produces) as a new view", () => {
      const exported = JSON.stringify([
        {
          id: "foreign-id-1",
          name: "Imported View",
          slug: "imported-view",
          widgets: { ...ALL_WIDGETS_OFF, macroRegime: true },
          widgetOrder: ["macroRegime"],
          widgetConfigs: {},
          createdAt: "2026-01-01T00:00:00.000Z",
          updatedAt: "2026-01-01T00:00:00.000Z",
        },
      ]);

      const { importedCount, persisted, error } = importViews(exported);
      expect(error).toBeUndefined();
      expect(importedCount).toBe(1);
      expect(persisted).toBe(true);

      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
      expect(raw).toHaveLength(1);
      expect(raw[0].slug).toBe("imported-view");
      expect(raw[0].widgetOrder).toEqual(["macroRegime"]);
    });

    it("REGRESSION: never trusts the imported file's own `id` for a brand-new view -- generates a fresh one instead, so two independently-exported files can never collide on id", () => {
      const { view: local } = addOrUpdateView({ name: "Local View", widgets: ALL_WIDGETS });

      // A second, foreign file that happens to reuse the SAME id as the
      // local view above (plausible: both could have been created with the
      // same non-crypto fallback id generator, or hand-edited).
      const foreign = JSON.stringify([
        { id: local.id, name: "Foreign View", slug: "foreign-view", widgets: ALL_WIDGETS },
      ]);
      const { importedCount } = importViews(foreign);
      expect(importedCount).toBe(1);

      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
      expect(raw).toHaveLength(2);
      const ids = raw.map((v: any) => v.id);
      // Still exactly two DISTINCT ids -- the import did not clobber or
      // alias the pre-existing local view's id.
      expect(new Set(ids).size).toBe(2);
      expect(ids).toContain(local.id);
    });

    it("a view whose (recomputed) slug matches an existing view overwrites it in place, preserving id/createdAt", () => {
      const { view: original } = addOrUpdateView({ name: "Momentum Desk", widgets: NO_WIDGETS });

      const reimport = JSON.stringify([
        { id: "some-other-id", name: "Momentum Desk", widgets: ALL_WIDGETS, widgetOrder: ["macroRegime"] },
      ]);
      const { importedCount } = importViews(reimport);
      expect(importedCount).toBe(1);

      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
      expect(raw).toHaveLength(1);
      expect(raw[0].id).toBe(original.id); // id preserved, not the foreign file's id
      expect(raw[0].createdAt).toBe(original.createdAt);
      expect(raw[0].widgets).toEqual(ALL_WIDGETS);
    });

    it("sanitizes malformed widgets/widgetOrder instead of importing garbage -- unknown keys dropped, missing keys default false, an active widget missing from widgetOrder is appended rather than dropped", () => {
      const malformed = JSON.stringify([
        {
          name: "Malformed",
          widgets: { macroRegime: true, notARealWidget: true, symbolOverlay: "yes" },
          widgetOrder: ["notARealWidget", "macroRegime"], // omits symbolOverlay entirely
        },
      ]);
      const { importedCount, error } = importViews(malformed);
      expect(error).toBeUndefined();
      expect(importedCount).toBe(1);

      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
      expect(raw[0].widgets.macroRegime).toBe(true);
      expect(raw[0].widgets.symbolOverlay).toBe(true); // truthy string coerced to real boolean
      expect(raw[0].widgets).not.toHaveProperty("notARealWidget");
      // symbolOverlay is active but wasn't listed in the source widgetOrder --
      // it must still render, appended rather than silently dropped.
      expect(raw[0].widgetOrder).toEqual(["macroRegime", "symbolOverlay"]);
    });

    it("REGRESSION (review finding): a duplicate key already present in the imported widgetOrder is deduped, not rendered twice", () => {
      const withDuplicate = JSON.stringify([
        {
          name: "Duplicated Order",
          widgets: { macroRegime: true, symbolOverlay: true },
          widgetOrder: ["macroRegime", "symbolOverlay", "macroRegime"],
        },
      ]);
      const { importedCount, error } = importViews(withDuplicate);
      expect(error).toBeUndefined();
      expect(importedCount).toBe(1);

      const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) as string);
      expect(raw[0].widgetOrder).toEqual(["macroRegime", "symbolOverlay"]);
    });

    it("reports a clear error and imports nothing for invalid JSON", () => {
      const { importedCount, persisted, error } = importViews("not json");
      expect(importedCount).toBe(0);
      expect(persisted).toBe(false);
      expect(error).toBeTruthy();
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it("reports a clear error for a well-formed JSON array with no usable views", () => {
      const { importedCount, error } = importViews(JSON.stringify([{ name: "" }, { widgets: {} }]));
      expect(importedCount).toBe(0);
      expect(error).toBeTruthy();
    });
  });

  describe("loadFromStorage migration (via __resetCustomViewsForTests)", () => {
    it("derives widgetOrder for a legacy view (no widgetOrder/widgetConfigs field at all) from its widgets map, in canonical order", () => {
      const legacy = [
        {
          id: "legacy-1",
          name: "Legacy View",
          slug: "legacy-view",
          widgets: { ...ALL_WIDGETS_OFF, signalBreakdown: true, edgeByStrategy: true },
          createdAt: "2025-01-01T00:00:00.000Z",
          updatedAt: "2025-01-01T00:00:00.000Z",
        },
      ];
      localStorage.setItem(STORAGE_KEY, JSON.stringify(legacy));
      __resetCustomViewsForTests();

      const { result } = renderHook(() => useCustomViews());
      expect(result.current.views).toHaveLength(1);
      // Canonical order is edgeByStrategy before signalBreakdown regardless
      // of the two keys' order inside the legacy `widgets` object.
      expect(result.current.views[0].widgetOrder).toEqual(["edgeByStrategy", "signalBreakdown"]);
      expect(result.current.views[0].widgetConfigs).toEqual({});
    });

    it("drops a row with no real id/slug rather than surfacing an unusable entry", () => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([{ name: "No Identity", widgets: ALL_WIDGETS }]));
      __resetCustomViewsForTests();

      const { result } = renderHook(() => useCustomViews());
      expect(result.current.views).toHaveLength(0);
    });
  });
});

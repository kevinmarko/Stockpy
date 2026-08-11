import { useSyncExternalStore } from "react";

/**
 * customViews.ts — real, reactive, persisted storage for operator-created
 * "Data App" views (Create Data App screen -> /app/:slug renderer).
 *
 * Deliberately NOT built on `usePersistedState` (webapp/src/hooks/usePersistedState.ts)
 * or a Context Provider:
 *
 * - `usePersistedState` is a private per-component `useState` that happens to
 *   sync itself to localStorage. Two components calling it against the same
 *   key do NOT see each other's writes -- there is no cross-instance
 *   subscription. The sidebar (`Sidebar`/`BottomNav` in
 *   components/BottomNavigation.tsx) is mounted once, outside the router, for
 *   the app's whole lifetime; if it read custom views via its own
 *   `usePersistedState` call it would never notice a save made from the
 *   Create Data App screen without a full page reload. That's the same bug
 *   class (a form that claims to persist but doesn't actually take effect)
 *   PR #670's original "Save to Dashboard" stub shipped, just from a
 *   different root cause.
 * - A Context Provider would work, but the canonical value here is
 *   `localStorage` itself, not per-provider-instance React state -- there is
 *   nothing a Provider adds over a module-level store, and it would require
 *   wrapping <CustomViewsProvider> around the app in main.tsx for no benefit.
 *
 * `useSyncExternalStore` (React 18) is the correct primitive for "one true
 * external store, many independent React subscribers, must re-render every
 * subscriber on any writer's change" -- exactly this shape. A `storage` event
 * listener also keeps multiple browser tabs in sync (a real bonus, not
 * scope creep: the app is a PWA an operator may have open in more than one
 * tab/window).
 */

export interface CustomViewWidgets {
  edgeByStrategy: boolean;
  symbolOverlay: boolean;
  aiChat: boolean;
  pilotsTable: boolean;
  sentimentMini: boolean;
  portfolioHeat: boolean;
  optionsDirective: boolean;
  signalBreakdown: boolean;
  macroRegime: boolean;
}

export interface CustomView {
  id: string;
  name: string;
  slug: string;
  widgets: CustomViewWidgets;
  widgetOrder: (keyof CustomViewWidgets)[];
  widgetConfigs: Partial<Record<keyof CustomViewWidgets, any>>;
  createdAt: string; // ISO
  updatedAt: string; // ISO
}

const STORAGE_KEY = "stockpy.custom-views:v1";

/** The full, canonical set of widget keys, in stable display/migration
 * order. Single source of truth shared by `loadFromStorage` (legacy-data
 * migration), `importViews` (foreign-file ingestion), and the fallback
 * branch of `addOrUpdateView` -- previously each of these re-declared its
 * own local copy of this list (or, in `CreateDataApp.tsx`, derived it via
 * `Object.keys(WIDGET_LABELS) as any`), which is exactly the kind of
 * "three independent sources of truth that can drift" this codebase's own
 * conventions warn against. */
const ALL_WIDGET_KEYS: (keyof CustomViewWidgets)[] = [
  "edgeByStrategy", "symbolOverlay", "aiChat", "pilotsTable", "sentimentMini",
  "portfolioHeat", "optionsDirective", "signalBreakdown", "macroRegime",
];

/** Coerces an arbitrary (possibly foreign, possibly hand-edited) value into
 * a well-formed `CustomViewWidgets` -- exactly the known 9 keys, each a real
 * boolean. Unknown keys are dropped; a missing/malformed key defaults to
 * `false` rather than crashing the whole import (CONSTRAINT #6-style
 * degrade, applied to client-side schema drift). */
function sanitizeWidgets(raw: any): CustomViewWidgets {
  const out = {} as CustomViewWidgets;
  for (const k of ALL_WIDGET_KEYS) {
    out[k] = Boolean(raw && raw[k]);
  }
  return out;
}

/** Reconciles a candidate `widgetOrder` against the widgets that are
 * actually active. `CustomView.tsx` renders `widgetOrder` alone -- it does
 * NOT cross-check `widgets` -- so `widgetOrder` must be kept in sync with
 * `widgets` at every write path, not just the in-app editor's. Any active
 * widget the candidate order didn't mention (missing field, hand-edited
 * JSON, a widget added to `CustomViewWidgets` after the file was exported)
 * is appended in canonical order rather than silently dropped -- a widget
 * the operator explicitly enabled must still render somewhere. */
function sanitizeWidgetOrder(raw: any, widgets: CustomViewWidgets): (keyof CustomViewWidgets)[] {
  const fromRaw: (keyof CustomViewWidgets)[] = Array.isArray(raw)
    ? raw.filter((k: any): k is keyof CustomViewWidgets => ALL_WIDGET_KEYS.includes(k) && widgets[k as keyof CustomViewWidgets])
    : [];
  const missing = ALL_WIDGET_KEYS.filter((k) => widgets[k] && !fromRaw.includes(k));
  return [...fromRaw, ...missing];
}

/** Same fallback-ID shape `addOrUpdateView` always used, factored out so
 * `importViews` (which must never trust a foreign file's `id` -- see that
 * function's doc) generates new views the same way. */
function makeId(slug: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${slug}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function loadFromStorage(): CustomView[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    // Migration: populate widgetOrder/widgetConfigs for pre-existing views
    // saved before those fields existed, and reconcile+sanitize both fields
    // for every view (idempotent -- a view that already has a well-formed
    // widgetOrder/widgets round-trips unchanged). A row missing a real
    // id/slug (never produced by this module's own writers, but possible
    // from manually-edited localStorage) is dropped rather than kept: every
    // other write path assumes `id` is a stable, unique, non-empty key
    // (React list keys, removeView, loadViewForEditing, duplicateView).
    return parsed
      .filter((view: any) => view && typeof view.id === "string" && view.id && typeof view.slug === "string" && view.slug)
      .map((view: any): CustomView => {
        const widgets = sanitizeWidgets(view.widgets);
        return {
          ...view,
          widgets,
          widgetOrder: sanitizeWidgetOrder(view.widgetOrder, widgets),
          widgetConfigs: view.widgetConfigs && typeof view.widgetConfigs === "object" ? view.widgetConfigs : {},
        };
      });
  } catch {
    // Corrupt/unavailable storage degrades to "no saved views" -- never throws.
    return [];
  }
}

let views: CustomView[] = loadFromStorage();
const listeners = new Set<() => void>();

/**
 * Returns whether the write actually reached localStorage. A previous
 * version of this function swallowed the exception and returned nothing --
 * addOrUpdateView()/removeView() always reported success and the UI always
 * showed "Saved to the sidebar" even when the browser's storage was full or
 * blocked (private mode, quota exceeded), so a view could vanish on the next
 * reload with no explanation anywhere (CONSTRAINT #4/#6). The in-memory
 * `views` array is still updated either way -- this tab's own session isn't
 * degraded -- but callers can now tell the difference and say so honestly.
 */
function persist(): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(views));
    return true;
  } catch {
    return false;
  }
}

function notify() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): CustomView[] {
  return views;
}

// Keep multiple tabs/windows of the PWA in sync: a save/delete in one tab
// updates `views` and notifies subscribers in every OTHER tab too (the tab
// that made the write already updated its own in-memory `views` directly and
// does not receive its own `storage` event, per the DOM spec).
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key !== STORAGE_KEY) return;
    views = loadFromStorage();
    notify();
  });
}

/** Small deterministic string hash (same input always -> same output) --
 * NOT cryptographic, just enough spread that two different inputs are very
 * unlikely to collide at the scale of one operator's saved views. */
function hashString(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h).toString(36);
}

/**
 * `base || "view"` used to be a single, constant fallback for any name with
 * no alphanumeric characters at all (e.g. "!!!", "日本株", pure emoji) --
 * meaning any two such names, however different, silently collided on the
 * literal slug/route "view" and one would overwrite the other under
 * addOrUpdateView's existing-slug-match rule. That's a real instance of the
 * "two differently-named entries collide on one route" failure this
 * module's own addOrUpdateView doc explicitly says it was designed to avoid
 * -- it just missed this one path. Falling back to a hash of the actual
 * trimmed name keeps the invariant instead: the SAME odd name still maps to
 * the SAME slug (repeat-saves-update-in-place still holds), but two
 * DIFFERENT odd names no longer collide.
 */
function slugify(name: string): string {
  const trimmed = name.trim();
  const base = trimmed
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base || `view-${hashString(trimmed)}`;
}

/**
 * Creates a new view, or -- if a view with the same slugified name already
 * exists -- updates it in place (same id/createdAt, fresh updatedAt/widgets).
 * This is the real "repeat saves update instead of duplicating" behavior;
 * PR #670's original version claimed this while its save endpoint was a
 * no-op stub that persisted nothing.
 *
 * The slug IS the view's identity: two names that happen to slugify to the
 * same value (e.g. "Foo Bar" and "foo-bar") are treated as the same view,
 * the second save renaming/overwriting the first -- there is no separate
 * "edit" affordance in the UI, so this is the only way a rename can happen,
 * and there is deliberately no silent `-2` suffix path (it would let two
 * differently-named entries collide on the SAME sidebar route, `/app/foo-bar`,
 * which is a worse outcome than one view winning the name honestly).
 */
/** `persisted: false` means the view is only good for this browser tab's
 * current session -- it will NOT survive a reload. Callers must surface
 * this honestly (never a blanket "Saved" toast regardless of the result). */
export function addOrUpdateView(input: {
  id?: string;
  name: string;
  widgets: CustomViewWidgets;
  widgetOrder?: (keyof CustomViewWidgets)[];
  widgetConfigs?: Partial<Record<keyof CustomViewWidgets, any>>;
}): { view: CustomView; persisted: boolean } {
  const name = input.name.trim();
  let slug = slugify(name);
  
  // Find by ID first if provided, otherwise find by slug
  let existing = input.id ? views.find((v) => v.id === input.id) : views.find((v) => v.slug === slug);
  
  // If we are renaming an existing view, make sure the new slug doesn't collide with a DIFFERENT view
  if (existing && input.id && slug !== existing.slug) {
    let existingWithNewSlug = views.find((v) => v.slug === slug && v.id !== input.id);
    if (existingWithNewSlug) {
      slug = `${slug}-${hashString(input.id)}`;
    }
  }

  const now = new Date().toISOString();

  const widgetOrder = input.widgetOrder ?? (Object.keys(input.widgets) as (keyof CustomViewWidgets)[]).filter(k => input.widgets[k]);
  const widgetConfigs = input.widgetConfigs ?? {};

  let view: CustomView;
  if (existing) {
    view = { ...existing, name, slug, widgets: input.widgets, widgetOrder, widgetConfigs, updatedAt: now };
    views = views.map((v) => (v.id === existing.id ? view : v));
  } else {
    view = {
      id: makeId(slug),
      name,
      slug,
      widgets: input.widgets,
      widgetOrder,
      widgetConfigs,
      createdAt: now,
      updatedAt: now,
    };
    views = [...views, view];
  }
  const persisted = persist();
  notify();
  return { view, persisted };
}

/** See addOrUpdateView's `persisted` doc -- same honesty contract applies to deletion. */
export function removeView(id: string): { persisted: boolean } {
  views = views.filter((v) => v.id !== id);
  const persisted = persist();
  notify();
  return { persisted };
}

/**
 * Ingests a JSON file (as produced by `handleExport` in CreateDataApp.tsx,
 * or hand-edited/foreign) as an array of views. A view whose (recomputed,
 * not trusted-as-is) slug matches an existing view overwrites it IN PLACE
 * -- same `id`/`createdAt` preserved, matching `addOrUpdateView`'s own
 * update-in-place convention -- so importing an edited export of a view you
 * already have renames/updates it rather than duplicating it under a second
 * id. A genuinely new slug is appended as a brand-new view with a FRESH,
 * locally-generated id: the file's own `id` field is never trusted, because
 * two independently-exported files (e.g. from two browsers, or a hand-built
 * file) can easily collide on an id that was never meant to be globally
 * unique, and `id` uniqueness is an invariant the rest of this module (plus
 * every caller: React list keys, removeView, loadViewForEditing,
 * duplicateView) depends on unconditionally.
 */
export function importViews(jsonString: string): { importedCount: number; persisted: boolean; error?: string } {
  try {
    const parsed = JSON.parse(jsonString);
    if (!Array.isArray(parsed)) throw new Error("Invalid format: expected array of views");

    // Quick validation -- only `name` and `widgets` are load-bearing here;
    // `slug` is always recomputed from `name` below rather than trusted,
    // since a hand-edited file can easily have the two drift.
    const validRaw = parsed.filter((v) => v && typeof v.name === "string" && v.name.trim().length > 0 && v.widgets);
    if (validRaw.length === 0) throw new Error("No valid views found in the imported file");

    const now = new Date().toISOString();
    let importedCount = 0;
    // Builds a NEW array at every step (never `views.push`/`views[i] = ...`
    // in place) -- `useSyncExternalStore` decides whether to re-render by
    // `Object.is`-comparing the previous snapshot to `getSnapshot()`'s
    // return value, so mutating the existing `views` array in place makes
    // every subscriber's snapshot compare EQUAL even after `notify()` fires,
    // and the sidebar/list silently does not update until something else
    // (e.g. a route change) forces a fresh render. Caught by a real UI test
    // driving the actual file-input -> FileReader -> importViews path, not
    // by inspecting localStorage alone (which reads the same either way).
    let nextViews = views;
    for (const raw of validRaw) {
      const name = String(raw.name).trim();
      const slug = slugify(name);
      const widgets = sanitizeWidgets(raw.widgets);
      const widgetOrder = sanitizeWidgetOrder(raw.widgetOrder, widgets);
      const widgetConfigs = raw.widgetConfigs && typeof raw.widgetConfigs === "object" ? raw.widgetConfigs : {};

      const existingIdx = nextViews.findIndex((v) => v.slug === slug);
      if (existingIdx >= 0) {
        const existing = nextViews[existingIdx];
        const updated = { ...existing, name, slug, widgets, widgetOrder, widgetConfigs, updatedAt: now };
        nextViews = nextViews.map((v, i) => (i === existingIdx ? updated : v));
      } else {
        nextViews = [...nextViews, { id: makeId(slug), name, slug, widgets, widgetOrder, widgetConfigs, createdAt: now, updatedAt: now }];
      }
      importedCount++;
    }
    views = nextViews;

    const persisted = persist();
    notify();
    return { importedCount, persisted };
  } catch (e: any) {
    return { importedCount: 0, persisted: false, error: e.message || "Failed to parse JSON" };
  }
}


export function useCustomViews(): {
  views: CustomView[];
  addOrUpdateView: typeof addOrUpdateView;
  removeView: typeof removeView;
  importViews: typeof importViews;
} {
  const list = useSyncExternalStore(subscribe, getSnapshot);
  return { views: list, addOrUpdateView, removeView, importViews };
}

/** Exposed for tests to reset the shared in-memory store between cases --
 * mirrors help/thresholds.ts's `__resetThresholdsCache`. Necessary because
 * `views` is module-level state: the test-setup's `afterEach` clears real
 * jsdom `localStorage` between tests, but does not (and cannot, without
 * this) reset an already-imported module's in-memory copy. */
export function __resetCustomViewsForTests() {
  views = loadFromStorage();
  notify();
}

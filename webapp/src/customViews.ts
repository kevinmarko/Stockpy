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
  createdAt: string; // ISO
  updatedAt: string; // ISO
}

const STORAGE_KEY = "stockpy.custom-views:v1";

function loadFromStorage(): CustomView[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as CustomView[]) : [];
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
export function addOrUpdateView(input: { name: string; widgets: CustomViewWidgets }): { view: CustomView; persisted: boolean } {
  const name = input.name.trim();
  const slug = slugify(name);
  const existing = views.find((v) => v.slug === slug);
  const now = new Date().toISOString();

  let view: CustomView;
  if (existing) {
    view = { ...existing, name, widgets: input.widgets, updatedAt: now };
    views = views.map((v) => (v.id === existing.id ? view : v));
  } else {
    view = {
      id: typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${slug}-${now}`,
      name,
      slug,
      widgets: input.widgets,
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

export function useCustomViews(): {
  views: CustomView[];
  addOrUpdateView: typeof addOrUpdateView;
  removeView: typeof removeView;
} {
  const list = useSyncExternalStore(subscribe, getSnapshot);
  return { views: list, addOrUpdateView, removeView };
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

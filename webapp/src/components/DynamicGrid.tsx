import { useEffect, useMemo, useState, Children, isValidElement } from 'react';
import {
  ResponsiveGridLayout,
  useContainerWidth,
  verticalCompactor,
  type ResponsiveLayouts,
  type Layout,
  type LayoutItem,
} from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

export interface DynamicGridProps {
  layoutKey: string;
  defaultLayouts: ResponsiveLayouts;
  children: React.ReactNode;
  rowHeight?: number;
  /**
   * @deprecated Dragging is permanently disabled (see the module doc
   * comment on `DynamicGrid` below) -- this prop is accepted for call-site
   * compatibility but no longer wired to anything.
   */
  draggableHandle?: string;
  /**
   * @deprecated Resizing is permanently disabled (see the module doc
   * comment on `DynamicGrid` below) -- this prop is accepted for call-site
   * compatibility but no longer wired to anything.
   */
  isResizable?: boolean;
}

/**
 * Extracts the React `key` of every direct child, in order, skipping any
 * child without an explicit key. Every DynamicGrid consumer keys its grid
 * items with the same id used in `defaultLayouts`' `i` field -- this is how
 * `reconcileLayout` knows which items are "actually there" right now.
 *
 * Deliberately uses `Children.forEach` (not `Children.toArray`, which
 * rewrites keys to an internal `.$<key>` form for uniqueness) so the
 * returned strings match `LayoutItem.i` verbatim.
 */
function getChildKeys(children: React.ReactNode): string[] {
  const keys: string[] = [];
  Children.forEach(children, (child) => {
    if (isValidElement(child) && child.key != null) {
      keys.push(String(child.key));
    }
  });
  return keys;
}

/**
 * Reconciles a saved localStorage layout blob against the actual set of grid
 * item keys currently present in `children`.
 *
 * Historically used to reconcile a user's dragged/resized layout (saved to
 * localStorage) against live, day-to-day-changing data -- a layout saved on
 * a day with 8 items would otherwise be silently wrong once the list has 12.
 * `DynamicGrid` no longer persists or reads a saved layout (dragging/
 * resizing/reordering were removed -- see its own doc comment), so in
 * practice it now always calls this with `savedLayoutRaw=null`, which
 * short-circuits to `defaultLayouts` verbatim below. The function is kept
 * intact and exported as-is (rather than deleted) because it's still real,
 * independently-tested logic, and deleting it would be a pointless diff on
 * top of an already-large change.
 *
 * Pure and side-effect free (including its own JSON parsing) so it is
 * directly unit-testable without rendering anything -- see
 * `DynamicGrid.test.tsx`.
 *
 * @param savedLayoutRaw the raw JSON string as read from
 *   `localStorage.getItem(...)` (or `null`/`undefined` for a fresh mount
 *   with nothing saved yet). A malformed/corrupt blob falls back to
 *   `defaultLayouts` verbatim rather than throwing.
 */
export function reconcileLayout(
  savedLayoutRaw: string | null | undefined,
  defaultLayouts: ResponsiveLayouts,
  childKeys: string[]
): ResponsiveLayouts {
  if (!savedLayoutRaw) {
    return defaultLayouts;
  }

  let savedLayout: unknown;
  try {
    savedLayout = JSON.parse(savedLayoutRaw);
  } catch {
    return defaultLayouts;
  }

  if (typeof savedLayout !== 'object' || savedLayout === null || Array.isArray(savedLayout)) {
    return defaultLayouts;
  }

  const savedObj = savedLayout as Record<string, unknown>;
  const childKeySet = new Set(childKeys);
  const breakpoints = new Set([...Object.keys(savedObj), ...Object.keys(defaultLayouts)]);
  const result: ResponsiveLayouts = {};

  for (const bp of breakpoints) {
    const defaultArr: Layout = defaultLayouts[bp] ?? [];

    // No saved data at all for this breakpoint -- the caller-supplied
    // default is already computed fresh from the current children, so use
    // it verbatim rather than inventing a reconciliation for data we never
    // saved in the first place.
    const rawSavedArr = savedObj[bp];
    if (!Array.isArray(rawSavedArr)) {
      result[bp] = defaultArr;
      continue;
    }
    const savedArr = rawSavedArr as Layout;

    const defaultByKey = new Map(defaultArr.map((item) => [item.i, item]));

    // Keep the saved position/size for any key still present in children;
    // anything else is a stale item (no longer relevant) and is dropped.
    const kept = savedArr.filter(
      (item) => item && typeof item.i === 'string' && childKeySet.has(item.i)
    );
    const keptKeys = new Set(kept.map((item) => item.i));

    // Auto-place any child key with no entry in the saved layout -- a
    // genuinely new item -- stacked below the current max `y + h` so it
    // never overlaps an existing item.
    let nextY = kept.reduce((max, item) => Math.max(max, item.y + item.h), 0);
    const added: LayoutItem[] = [];
    for (const key of childKeys) {
      if (keptKeys.has(key)) continue;
      const template = defaultByKey.get(key);
      const w = template?.w ?? 4;
      const h = template?.h ?? 4;
      const item: LayoutItem = { i: key, x: 0, y: nextY, w, h };
      if (template?.minW != null) item.minW = template.minW;
      if (template?.minH != null) item.minH = template.minH;
      added.push(item);
      nextY += h;
    }

    result[bp] = [...kept, ...added];
  }

  return result;
}

/**
 * A wrapper around react-grid-layout used purely for its responsive,
 * multi-column placement of cards/tables -- NOT for end-user drag/resize/
 * reorder interaction, which this component deliberately does not offer.
 *
 * Dragging, resizing, the old "Reorder Widgets" toggle mode, and layout
 * persistence to localStorage were all removed (2026-08): they were the
 * root cause of a long string of regressions (click/drag bubbling
 * conflicts, infinite re-render loops on mount, stale/broken saved
 * layouts) across the ~30 screens that use this component, and were
 * removed at the operator's request rather than patched further. Every
 * screen now always renders at its author-supplied `defaultLayouts`
 * arrangement -- static, with no per-user drift.
 */
export function DynamicGrid({
  layoutKey,
  defaultLayouts,
  children,
  rowHeight = 30,
}: DynamicGridProps) {
  const { width, containerRef, mounted } = useContainerWidth();
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test" && !process.env.TEST_RENDER_DYNAMIC_GRID;

  // A stable signature of "what keys are actually in `children` right now".
  // Nearly every consumer passes `defaultLayouts` as an inline object
  // literal -- a fresh reference on every parent re-render -- so we
  // deliberately key off this derived signature (and `layoutKey`) rather
  // than the `defaultLayouts` object reference itself, otherwise a screen
  // polling live data every few seconds would recompute layout state on
  // every poll.
  const childKeys = useMemo(() => getChildKeys(children), [children]);
  const childKeysSignature = childKeys.join('|');

  // `layouts` deliberately starts `null` (never eagerly resolved via a
  // lazy useState initializer) so `ResponsiveGridLayout` below never mounts
  // on the very first render pass -- it mounts once, on the *second* pass,
  // already holding its real, fully-resolved layout. Combined with
  // `useContainerWidth`'s own `mounted`/`width` transitioning from their
  // initial values shortly after mount, eagerly resolving `layouts` on the
  // first render let `ResponsiveGridLayout` mount into that still-settling
  // window and re-measure/re-layout/re-report a real (if numerically
  // trivial) change on every one of several rapid initial renders --
  // enough in practice to trip React's "Maximum update depth exceeded"
  // safety net on mount, on every single DynamicGrid instance. Resolving
  // `layouts` via this effect instead (matching this component's original,
  // verified-safe behavior) delays RGL's first mount until after that
  // settling window has already passed.
  const [layouts, setLayouts] = useState<ResponsiveLayouts | null>(null);

  useEffect(() => {
    // No saved layout is ever read (persistence was removed) -- always
    // resolve to `defaultLayouts`, reconciled only for the (rare) case of a
    // child key with no matching `defaultLayouts` entry.
    setLayouts(reconcileLayout(null, defaultLayouts, childKeys));
    // Intentionally NOT depending on `defaultLayouts`/`children` by
    // reference -- see the comment on `childKeysSignature` above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey, childKeysSignature]);

  if (isTest) {
    return <div data-testid={`grid-${layoutKey}`} style={{ display: 'flex', flexDirection: 'column' }}>{children}</div>;
  }

  if (!layouts) return null;

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, position: 'relative' }}>
        {mounted && (
        <ResponsiveGridLayout
          className="dynamic-grid"
          layouts={layouts}
          width={width}
          breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
          cols={{ lg: 12, md: 10, sm: 6, xs: 4, xxs: 2 }}
          rowHeight={rowHeight}
          margin={[16, 16]}
          dragConfig={{ enabled: false }}
          resizeConfig={{ enabled: false }}
          compactor={verticalCompactor}
        >
          {children}
        </ResponsiveGridLayout>
      )}
      </div>
    </div>
  );
}

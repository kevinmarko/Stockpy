import { useEffect, useMemo, useRef, useState, Children, isValidElement } from 'react';
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
  draggableHandle?: string;
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
 * Several DynamicGrid consumers (OptionsMatrix, StrategyMatrix, PairsRadar,
 * Comparison, SymbolDetail, SignalBreakdown, PipelineDashboard, Portfolio)
 * generate their grid items from live, day-to-day-changing data -- a layout
 * saved on a day with 8 items is silently wrong once the list has 12. This
 * function is the fix: for every breakpoint present in the saved layout, it
 * keeps the saved position/size for any key still present in `children`,
 * drops any saved entry whose key is no longer present (stale item), and
 * auto-places any child key with no saved entry (a genuinely new item)
 * below the current content, non-overlapping, using that key's
 * `defaultLayouts` template (`w`/`h`/`minW`/`minH`) when available.
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
 * A wrapper around react-grid-layout that handles saving/loading from localStorage.
 */
export function DynamicGrid({
  layoutKey,
  defaultLayouts,
  children,
  rowHeight = 30,
  draggableHandle = '.drag-handle',
  isResizable = true
}: DynamicGridProps) {
  const { width, containerRef, mounted } = useContainerWidth();
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";

  // A stable signature of "what keys are actually in `children` right now".
  // Nearly every consumer passes `defaultLayouts` as an inline object
  // literal -- a fresh reference on every parent re-render -- so we
  // deliberately key off this derived signature (and `layoutKey`) rather
  // than the `defaultLayouts` object reference itself, otherwise a screen
  // polling live data every few seconds would re-read localStorage and
  // reset layout state on every poll.
  const childKeys = useMemo(() => getChildKeys(children), [children]);
  const childKeysSignature = childKeys.join('|');

  const [layouts, setLayouts] = useState<ResponsiveLayouts>(() => {
    const saved = localStorage.getItem(`grid-layout-${layoutKey}`);
    return reconcileLayout(saved, defaultLayouts, childKeys);
  });
  const [isMobile, setIsMobile] = useState(false);

  // Skip the very first run of the reconciliation effect below -- the lazy
  // useState initializer above already did that work for the initial
  // mount. The effect only needs to fire again when `layoutKey` or the
  // actual set of child keys genuinely changes.
  const isFirstRun = useRef(true);

  useEffect(() => {
    if (isFirstRun.current) {
      isFirstRun.current = false;
      return;
    }
    const saved = localStorage.getItem(`grid-layout-${layoutKey}`);
    setLayouts(reconcileLayout(saved, defaultLayouts, childKeys));
    // Intentionally NOT depending on `defaultLayouts`/`children` by
    // reference -- see the comment on `childKeysSignature` above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey, childKeysSignature]);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const onLayoutChange = (_currentLayout: any, allLayouts: ResponsiveLayouts) => {
    setLayouts(allLayouts);
    localStorage.setItem(`grid-layout-${layoutKey}`, JSON.stringify(allLayouts));
  };

  if (isTest) {
    return <div data-testid={`grid-${layoutKey}`} style={{ display: 'flex', flexDirection: 'column' }}>{children}</div>;
  }

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%' }}>
      {mounted && (
        <ResponsiveGridLayout
          className="dynamic-grid"
          layouts={layouts}
          onLayoutChange={onLayoutChange}
          width={width}
          breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
          cols={{ lg: 12, md: 10, sm: 6, xs: 4, xxs: 2 }}
          rowHeight={rowHeight}
          margin={[16, 16]}
          dragConfig={{ enabled: !isMobile, handle: draggableHandle }}
          resizeConfig={{ enabled: !isMobile && isResizable }}
          compactor={verticalCompactor}
        >
          {children}
        </ResponsiveGridLayout>
      )}
    </div>
  );
}

export function resetGridLayout(layoutKey: string) {
  const confirmed = window.confirm(
    "Reset this screen's layout to default? Any unsaved changes on this screen will be lost."
  );
  if (!confirmed) return;
  localStorage.removeItem(`grid-layout-${layoutKey}`);
  window.location.reload();
}

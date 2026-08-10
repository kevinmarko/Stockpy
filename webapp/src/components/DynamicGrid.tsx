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
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test" && !process.env.TEST_RENDER_DYNAMIC_GRID;

  // A stable signature of "what keys are actually in `children` right now".
  // Nearly every consumer passes `defaultLayouts` as an inline object
  // literal -- a fresh reference on every parent re-render -- so we
  // deliberately key off this derived signature (and `layoutKey`) rather
  // than the `defaultLayouts` object reference itself, otherwise a screen
  // polling live data every few seconds would re-read localStorage and
  // reset layout state on every poll.
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
  // verified-safe pre-reconciliation behavior) delays RGL's first mount
  // until after that settling window has already passed.
  const [layouts, setLayouts] = useState<ResponsiveLayouts | null>(null);
  const [isMobile, setIsMobile] = useState(false);
  const [isReorderMode, setIsReorderMode] = useState(false);
  const [currentBreakpoint, setCurrentBreakpoint] = useState<string>('lg');

  const moveWidget = (key: string, offset: -1 | 1) => {
    setLayouts(prevLayouts => {
      if (!prevLayouts) return prevLayouts;
      const bpLayout = prevLayouts[currentBreakpoint];
      if (!bpLayout) return prevLayouts;

      const sorted = [...bpLayout].sort((a, b) => {
        if (a.y !== b.y) return a.y - b.y;
        return a.x - b.x;
      });

      const currentIndex = sorted.findIndex(item => item.i === key);
      if (currentIndex === -1) return prevLayouts;
      
      const targetIndex = currentIndex + offset;
      if (targetIndex < 0 || targetIndex >= sorted.length) return prevLayouts;

      const a = sorted[currentIndex];
      const b = sorted[targetIndex];
      
      const nextBpLayout = bpLayout.map(item => {
        if (item.i === a.i) return { ...item, x: b.x, y: b.y };
        if (item.i === b.i) return { ...item, x: a.x, y: a.y };
        return item;
      });

      const newLayouts = { ...prevLayouts, [currentBreakpoint]: nextBpLayout };
      localStorage.setItem(`grid-layout-${layoutKey}`, JSON.stringify(newLayouts));
      return newLayouts;
    });
  };

  useEffect(() => {
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

  // react-grid-layout calls onLayoutChange not only on a real user drag/
  // resize, but also whenever it recomputes layout for ANY reason --
  // mounting, a width change as `useContainerWidth`'s ResizeObserver
  // settles, our own reconciliation effect calling setLayouts, internal
  // compaction, etc. Measured directly: under real-world timing/CPU
  // variance, this recomputation can genuinely fail to converge within
  // React's re-render safety budget on mount, throwing "Maximum update
  // depth exceeded" -- confirmed to still happen intermittently (varies
  // run to run, machine to machine) even after excluding onLayoutChange
  // calls that are merely RGL re-reporting an unchanged layout (that
  // exact-value dedup alone isn't sufficient, since a width that's
  // genuinely still settling produces genuinely different x/y values on
  // each pass, not just re-enriched-but-equal ones).
  //
  // The robust fix is to stop treating onLayoutChange as a general
  // "layout changed, please persist" signal at all. Instead, only ever
  // write back to `layouts` state (and localStorage) while a real user
  // drag or resize gesture is actively in progress -- tracked via
  // onDragStart/onDragStop/onResizeStart/onResizeStop, which fire only
  // for genuine pointer-driven interaction, never for mount/width/
  // reconciliation-driven recomputation. This makes a feedback loop
  // through onLayoutChange structurally impossible regardless of *why*
  // RGL recomputes outside of that window, rather than trying to
  // out-guess every possible non-user-driven trigger.
  const isInteractingRef = useRef(false);
  const beginInteraction = () => {
    isInteractingRef.current = true;
  };
  // Deferred (not synchronous) so that a same-tick onLayoutChange firing
  // as part of the same drag/resize-stop event batch still observes
  // isInteractingRef as true -- RGL's own onDragStop/onResizeStop vs.
  // onLayoutChange firing order isn't a contract this component can rely
  // on, so the flag is cleared one tick later instead of immediately.
  const endInteraction = () => {
    setTimeout(() => {
      isInteractingRef.current = false;
    }, 0);
  };
  const onLayoutChange = (_currentLayout: any, allLayouts: ResponsiveLayouts) => {
    if (!isInteractingRef.current) return;
    setLayouts(allLayouts);
    localStorage.setItem(`grid-layout-${layoutKey}`, JSON.stringify(allLayouts));
  };

  if (isTest) {
    return <div data-testid={`grid-${layoutKey}`} style={{ display: 'flex', flexDirection: 'column' }}>{children}</div>;
  }

  if (!layouts) return null;

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '0 16px 8px' }}>
        <button 
          type="button"
          onClick={() => setIsReorderMode(p => !p)}
          className={`btn btn-sm ${isReorderMode ? 'btn-primary' : 'btn-neutral'}`}
          aria-pressed={isReorderMode}
        >
          {isReorderMode ? 'Done Reordering' : 'Reorder Widgets'}
        </button>
      </div>
      <div style={{ flex: 1, position: 'relative' }}>
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
          onDragStart={beginInteraction}
          onDragStop={endInteraction}
          onResizeStart={beginInteraction}
          onResizeStop={endInteraction}
          onBreakpointChange={(newBp) => setCurrentBreakpoint(newBp)}
        >
          {Children.map(children, (child) => {
            if (!isValidElement(child)) return child;
            const key = String(child.key);
            return (
              <div key={key}>
                {isReorderMode && (
                  <div 
                    style={{ 
                      position: 'absolute', inset: 0, zIndex: 50, 
                      background: 'rgba(0,0,0,0.7)', 
                      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 16,
                      borderRadius: 'var(--radius-md)'
                    }}
                  >
                    <button 
                      className="btn btn-neutral" 
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); moveWidget(key, -1); }}
                      aria-label={`Move widget ${key} up`}
                    >
                      ↑ Up
                    </button>
                    <button 
                      className="btn btn-neutral" 
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); moveWidget(key, 1); }}
                      aria-label={`Move widget ${key} down`}
                    >
                      ↓ Down
                    </button>
                  </div>
                )}
                <div style={{ height: '100%', width: '100%', pointerEvents: isReorderMode ? 'none' : 'auto' }}>
                  {child}
                </div>
              </div>
            );
          })}
        </ResponsiveGridLayout>
      )}
      </div>
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

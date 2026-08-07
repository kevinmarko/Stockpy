import { useState, useEffect } from 'react';
import { ResponsiveGridLayout, useContainerWidth, ResponsiveLayouts, verticalCompactor } from 'react-grid-layout';
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
  const [layouts, setLayouts] = useState<ResponsiveLayouts | null>(null);
  // Detect if we are on a small screen to disable drag/resize
  const [isMobile, setIsMobile] = useState(false);
  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  
  useEffect(() => {
    const saved = localStorage.getItem(`grid-layout-${layoutKey}`);
    if (saved) {
      try {
        setLayouts(JSON.parse(saved));
      } catch {
        setLayouts(defaultLayouts);
      }
    } else {
      setLayouts(defaultLayouts);
    }

    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, [layoutKey, defaultLayouts]);

  const onLayoutChange = (_currentLayout: any, allLayouts: ResponsiveLayouts) => {
    setLayouts(allLayouts);
    localStorage.setItem(`grid-layout-${layoutKey}`, JSON.stringify(allLayouts));
  };

  if (!layouts) return null;

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
  localStorage.removeItem(`grid-layout-${layoutKey}`);
  window.location.reload();
}

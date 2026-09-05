import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import { TrendsStitchChart, TrendsCurve, formatUtcDate } from './TrendsStitchChart';

// Mock recharts in case it is currently used or after it gets rewritten
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
  LineChart: () => <div data-testid="recharts-line-chart">Chart</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
}));

// Mock echarts-for-react in case it is still used
vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts-mock">Chart</div>
}));

describe('TrendsStitchChart', () => {
  it('renders without crashing', () => {
    const rawCurves: TrendsCurve[] = [
      { name: 'Raw 1', data: [[1, 10], [2, 20]] }
    ];
    const stitchedCurve: TrendsCurve = { name: 'Stitched', data: [[1, 15], [2, 25]] };

    const { container } = render(
      <TrendsStitchChart rawCurves={rawCurves} stitchedCurve={stitchedCurve} />
    );
    expect(container).toBeTruthy();
  });

  it('gives its own wrapper a real, definite pixel height instead of a Tailwind-only class', () => {
    // Regression guard for the invisible-chart bug: this webapp ships NO
    // Tailwind CSS build at all (no tailwindcss dependency, no
    // tailwind.config, no @tailwind directives in index.css), so a
    // Tailwind-arbitrary-value class like `h-[400px]` produces ZERO real CSS
    // -- the element silently computes to a 0px height. Recharts'
    // ResponsiveContainer needs a definite, non-percentage/non-zero height
    // somewhere in its ancestry to render anything; with 0px it silently
    // renders a 0x0 SVG with no error and no console warning. The existing
    // `recharts` mock above replaces only ResponsiveContainer/LineChart/etc
    // internals -- the sized wrapper `<div style={{...}}>` around
    // <ResponsiveContainer> is written by TrendsStitchChart.tsx itself and is
    // still really rendered here, so this test can (and must) inspect its
    // actual inline style rather than anything recharts controls.
    const rawCurves: TrendsCurve[] = [
      { name: 'Raw 1', data: [[1, 10], [2, 20]] }
    ];
    const stitchedCurve: TrendsCurve = { name: 'Stitched', data: [[1, 15], [2, 25]] };

    const { container } = render(
      <TrendsStitchChart rawCurves={rawCurves} stitchedCurve={stitchedCurve} />
    );

    // The component's own sized wrapper div is the only element in this tree
    // carrying an inline `style` attribute (the mocked ResponsiveContainer
    // renders a bare, style-less `<div>`), so it's reliably selectable via
    // `[style]` without depending on any class name.
    const sizedEl = container.querySelector('[style]') as HTMLElement | null;
    expect(sizedEl).not.toBeNull();
    expect(sizedEl!.style.height).toBe('400px');
    expect(sizedEl!.style.width).toBe('100%');

    // Negative regression: the rendered markup must not contain a
    // Tailwind-arbitrary-value or Tailwind-only utility class fragment --
    // this is the exact bug class that shipped (a class with zero real
    // backing CSS in this project, silently producing a 0-height container).
    expect(container.innerHTML).not.toMatch(/h-\[|bg-zinc-|text-zinc-|border-zinc-/);
  });

  describe('formatUtcDate', () => {
    it('never shifts a UTC-midnight-encoded bar date back a calendar day', () => {
      // Regression guard for the off-by-one-day bug: the backend encodes each real
      // trading date as UTC midnight (api/data_api.py's to_curve() does
      // ts.timestamp() * 1000 on a tz-naive pandas Timestamp, which pandas treats
      // as UTC). Formatting with the runtime's LOCAL timezone (the pre-fix
      // behavior) would render this as one day earlier in any timezone behind
      // UTC -- e.g. America/New_York. formatUtcDate must always report the real
      // UTC calendar date regardless of what timezone this test happens to run in.
      const utcMidnightJan2_2026 = Date.UTC(2026, 0, 2, 0, 0, 0); // 1767312000000
      expect(formatUtcDate(utcMidnightJan2_2026)).toBe('1/2/2026');

      const utcMidnightAug28_2026 = Date.UTC(2026, 7, 28, 0, 0, 0);
      expect(formatUtcDate(utcMidnightAug28_2026)).toBe('8/28/2026');
    });

    it('returns an empty string for a falsy tick', () => {
      expect(formatUtcDate(0)).toBe('');
      expect(formatUtcDate(null)).toBe('');
    });
  });
});

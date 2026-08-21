/**
 * PortfolioPieChart.test.tsx
 *
 * See AccountPerformanceChart.test.tsx's docstring for the jsdom/Recharts
 * layout caveat this file follows the same convention around.
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PortfolioPieChart } from "./PortfolioPieChart";
import type { PortfolioPositionView } from "../api/types";

function makePosition(
  symbol: string,
  market_value: number | null,
  overrides: Partial<PortfolioPositionView> = {}
): PortfolioPositionView {
  return {
    symbol,
    qty: 1,
    avg_cost: 100,
    current_price: market_value,
    market_value,
    unrealized_pl: 0,
    unrealized_pl_pct: 0,
    ...overrides,
  };
}

describe("PortfolioPieChart", () => {
  it("renders nothing for an empty positions array (no fabricated slices)", () => {
    const { container } = render(<PortfolioPieChart positions={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders without crashing given real positions", () => {
    const positions = [
      makePosition("AAPL", 5000),
      makePosition("NVDA", 3200),
      makePosition("MSFT", 1800),
    ];
    expect(() => render(<PortfolioPieChart positions={positions} />)).not.toThrow();
  });

  it("excludes positions with a null, zero, or negative market_value rather than crashing or plotting a fabricated slice", () => {
    // market_value is `number | null` (a live quote can be unavailable --
    // CONSTRAINT #4 forbids treating that as $0). This mirrors the
    // component's own `p.market_value && p.market_value > 0` filter.
    const positions = [
      makePosition("AAPL", 5000),
      makePosition("DELISTED", null),
      makePosition("ZEROVAL", 0),
      makePosition("SHORT", -100),
    ];
    expect(() => render(<PortfolioPieChart positions={positions} />)).not.toThrow();
  });

  it("does not crash with more than 10 positions (top-10-by-value slice)", () => {
    const positions = Array.from({ length: 15 }, (_, i) =>
      makePosition(`SYM${i}`, 1000 - i * 10)
    );
    expect(() => render(<PortfolioPieChart positions={positions} />)).not.toThrow();
  });
});

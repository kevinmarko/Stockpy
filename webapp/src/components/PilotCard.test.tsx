/**
 * PilotCard.test.tsx — renders PilotCard/PopularCard against the REAL mock API
 * (no vi.mock — `api` resolves to `mockApi` by default), covering the
 * mini-sparkline's async fetch (skeleton while loading, chart once the curve
 * resolves, honest empty state when a pilot has no persisted backtest curve —
 * CONSTRAINT #4: never a fabricated line) plus the metadata tags each card
 * surfaces (category, long-only, deployable badge).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { PilotCard, PopularCard } from "./PilotCard";
import { api } from "../api/client";
import type { PilotSummary } from "../api/types";

let trendFollowing: PilotSummary; // hasCurve: true, deployable: true
let valueQuality: PilotSummary; // hasCurve: false, headline all null, long_only: true
let momentumBurst: PilotSummary; // hasCurve: true, deployable: false

beforeAll(async () => {
  const pilots = await api.listPilots();
  const byId = (id: string) => {
    const p = pilots.find((p) => p.id === id);
    if (!p) throw new Error(`fixture pilot "${id}" missing from mock catalog`);
    return p;
  };
  trendFollowing = byId("trend-following");
  valueQuality = byId("value-quality");
  momentumBurst = byId("momentum-burst");
});

function renderCard(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("PilotCard (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders name, category chip, and Sharpe headline, then the sparkline once performance resolves", async () => {
    renderCard(<PilotCard pilot={trendFollowing} />);

    expect(screen.getByText("Trend Follower")).toBeInTheDocument();
    expect(screen.getByText("Momentum")).toBeInTheDocument();
    expect(screen.getByText("1.12")).toBeInTheDocument();

    // Sparkline is an async fetch (api.getPerformance) — a skeleton placeholder
    // shows first, then the chart's SVG once the curve resolves.
    const card = screen.getByText("Trend Follower").closest("a")!;
    await waitFor(() => expect(card.querySelector(".recharts-responsive-container")).toBeInTheDocument());
  });

  it("value-quality (curve:null, headline all null) renders honest placeholders, never a fabricated Sharpe or sparkline", async () => {
    renderCard(<PilotCard pilot={valueQuality} />);

    expect(screen.getByText("Value + Quality")).toBeInTheDocument();
    // Sharpe AND Max DD are both honestly "—" for this all-null-headline pilot.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Long-only")).toBeInTheDocument();

    const card = screen.getByText("Value + Quality").closest("a")!;
    // Wait out the getPerformance() fetch (skeleton disappears once it
    // resolves) before asserting no chart ever appears.
    await waitFor(() => expect(card.querySelector(".skeleton")).toBeNull());
    expect(card.querySelector(".recharts-responsive-container")).toBeNull();
  });

  it("a non-deployable pilot (momentum-burst) still surfaces its badge honestly", async () => {
    renderCard(<PilotCard pilot={momentumBurst} />);

    expect(await screen.findByText(/not deployable/i)).toBeInTheDocument();
  });

  it("renders a top-holding symbol chip with a recognizable action indicator (trend-following's NVDA is a BUY pick)", () => {
    renderCard(<PilotCard pilot={trendFollowing} />);

    expect(trendFollowing.top_holdings[0].action).toBe("BUY");
    const chip = screen.getByText(trendFollowing.top_holdings[0].symbol);
    // The chip is colored via theme.growth for a BUY action -- assert the
    // recognizable visual indicator, not just that the symbol text exists.
    expect(chip).toHaveStyle({ color: "#10b981" });
  });

  it("shows the DSR chip when headline.dsr is non-null", () => {
    renderCard(<PilotCard pilot={trendFollowing} />);

    expect(trendFollowing.headline.dsr).not.toBeNull();
    expect(screen.getByText(/DSR/)).toBeInTheDocument();
  });
});

describe("PopularCard (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders followers/AUM tiles, category chip, Sharpe, and the sparkline once performance resolves", async () => {
    renderCard(<PopularCard pilot={trendFollowing} />);

    expect(screen.getByText("Trend Follower")).toBeInTheDocument();
    expect(screen.getByText("Momentum")).toBeInTheDocument();
    expect(screen.getByText(trendFollowing.followers_proxy.toLocaleString())).toBeInTheDocument();
    expect(screen.getByText("1.12")).toBeInTheDocument();

    const card = screen.getByText("Trend Follower").closest("a")!;
    await waitFor(() => expect(card.querySelector(".recharts-responsive-container")).toBeInTheDocument());
  });

  it("value-quality (curve:null) renders honestly — no fabricated sparkline or Sharpe", async () => {
    renderCard(<PopularCard pilot={valueQuality} />);

    expect(screen.getByText("Value + Quality")).toBeInTheDocument();
    expect(screen.getByText("Long-only")).toBeInTheDocument();

    const card = screen.getByText("Value + Quality").closest("a")!;
    await waitFor(() => expect(card.querySelector(".skeleton")).toBeNull());
    expect(card.querySelector(".recharts-responsive-container")).toBeNull();
  });

  it("a non-deployable pilot (momentum-burst) still surfaces its badge honestly", async () => {
    renderCard(<PopularCard pilot={momentumBurst} />);

    expect(await screen.findByText(/not deployable/i)).toBeInTheDocument();
  });
});

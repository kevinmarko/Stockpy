/**
 * SentimentDynamics.test.tsx — Antigravity agent sentiment + GJR-GARCH
 * asymmetric-volatility persistence. Covers the happy path (mock's
 * source: "antigravity_agent" populated example) and the honest
 * source: "unavailable" render branch (blank "—" tiles + a visible note,
 * never a guessed number).
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SentimentDynamics } from "./SentimentDynamics";
import { api } from "../api/client";

function renderScreen() {
  return render(
    <MemoryRouter>
      <SentimentDynamics />
    </MemoryRouter>
  );
}

describe("SentimentDynamics screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the populated tiles for the default symbol (mock's antigravity_agent example)", async () => {
    renderScreen();
    expect(await screen.findByText("Sentiment Score")).toBeInTheDocument();
    expect(screen.getByText("0.15")).toBeInTheDocument();
    expect(screen.getByText("0.94")).toBeInTheDocument();
    // No "unavailable" note for the populated happy path.
    expect(screen.queryByText(/Antigravity agent unavailable/)).not.toBeInTheDocument();
  });

  it("source: 'unavailable' renders honest blanks + a visible note, never a guessed number", async () => {
    vi.spyOn(api, "getSentimentDynamics").mockResolvedValueOnce({
      ticker: "AAPL",
      date: new Date().toISOString(),
      sentiment_score: null,
      sentiment_intensity: null,
      credibility_score: null,
      // Vol Persistence is computed independently of the agent, so it can
      // still be a real number even when the agent itself is unavailable.
      volatility_persistence: 0.93,
      source: "unavailable",
    });
    renderScreen();

    expect(
      await screen.findByText(/Antigravity agent unavailable for this request/)
    ).toBeInTheDocument();
    // The three agent-derived fields render "—" — never a fabricated 0 or stale number.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    // Vol Persistence still renders its real, independently-computed value.
    expect(screen.getByText("0.93")).toBeInTheDocument();
  });
});

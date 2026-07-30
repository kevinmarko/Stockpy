/**
 * SentimentDynamics.test.tsx — Antigravity agent sentiment + GJR-GARCH
 * asymmetric-volatility persistence. Covers the happy path (mock's
 * source: "antigravity_agent" populated example) and the honest
 * source: "unavailable" render branch (blank "—" tiles + a visible note,
 * never a guessed number).
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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

  describe("Sentiment vs. VIX chart", () => {
    it("renders both stacked panels for the default (mock-universe) symbol", async () => {
      renderScreen();
      const section = await screen.findByTestId("sentiment-vix-chart");
      expect(section).toHaveTextContent("Sentiment vs. VIX");
      expect(await screen.findByTestId("sentiment-vix-vix-panel")).toBeInTheDocument();
      expect(screen.getByTestId("sentiment-vix-sentiment-panel")).toBeInTheDocument();
    });

    it("shows the coverage notice, with the real aligned-day count, when history is too sparse for a trend read", async () => {
      vi.spyOn(api, "getMacroHistory").mockResolvedValueOnce({
        series_id: "VIXCLS",
        points: [
          { date: "2026-07-20", value: 16.0 },
          { date: "2026-07-21", value: 16.5 },
          { date: "2026-07-22", value: 17.0 },
        ],
        reason: null,
      });
      vi.spyOn(api, "getSentimentHistory").mockResolvedValueOnce({
        symbol: "AAPL",
        points: [
          { date: "2026-07-20", score: 0.1 },
          { date: "2026-07-21", score: 0.2 },
          { date: "2026-07-22", score: -0.1 },
        ],
        reason: null,
      });
      renderScreen();
      const notice = await screen.findByTestId("sentiment-vix-coverage-notice");
      expect(notice).toHaveTextContent("Only 3 aligned days");
      expect(notice).toHaveTextContent("minimum 14");
    });

    it("does not show the coverage notice once aligned history meets the minimum", async () => {
      const points20 = Array.from({ length: 20 }, (_, i) => ({
        date: `2026-07-${String(i + 1).padStart(2, "0")}`,
      }));
      vi.spyOn(api, "getMacroHistory").mockResolvedValueOnce({
        series_id: "VIXCLS",
        points: points20.map((p) => ({ ...p, value: 17.0 })),
        reason: null,
      });
      vi.spyOn(api, "getSentimentHistory").mockResolvedValueOnce({
        symbol: "AAPL",
        points: points20.map((p) => ({ ...p, score: 0.1 })),
        reason: null,
      });
      renderScreen();
      await screen.findByTestId("sentiment-vix-vix-panel");
      expect(screen.queryByTestId("sentiment-vix-coverage-notice")).not.toBeInTheDocument();
    });

    it("renders the honest empty state, not a fabricated chart, when neither series has any history", async () => {
      vi.spyOn(api, "getMacroHistory").mockResolvedValueOnce({
        series_id: "VIXCLS",
        points: [],
        reason: "No cached history for VIXCLS yet.",
      });
      vi.spyOn(api, "getSentimentHistory").mockResolvedValueOnce({
        symbol: "ZZZZ",
        points: [],
        reason: "No archived sentiment history for ZZZZ yet.",
      });
      renderScreen();
      expect(await screen.findByTestId("sentiment-vix-empty")).toBeInTheDocument();
      expect(screen.queryByTestId("sentiment-vix-vix-panel")).not.toBeInTheDocument();
    });

    it("never computes or displays a correlation/lead-lag number anywhere in the section", async () => {
      renderScreen();
      const section = await screen.findByTestId("sentiment-vix-chart");
      expect(section.textContent).not.toMatch(/correlation/i);
      expect(section.textContent).not.toMatch(/lead-lag (is|:|of) [-\d]/i);
      expect(section.textContent).toMatch(/no lead-lag relationship is (computed|implied)/i);
    });
  });
});

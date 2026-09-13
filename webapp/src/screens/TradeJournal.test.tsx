/**
 * TradeJournal.test.tsx — the Retrospective Learning Loop's primary read
 * surface. Exercises the real mock fixture (all 3 decision.state values +
 * the evaluation.available=false honesty branch + a null realized_pnl_pct)
 * via direct api spies, matching TradeHistory.test.tsx's established
 * convention, plus the hard "no combined cohort figure" requirement.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TradeJournal } from "./TradeJournal";
import { api } from "../api/client";
import { mockTradeJournalEntriesEmpty } from "../api/mock";
import type { TradeJournalInsights } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <TradeJournal />
    </MemoryRouter>
  );
}

function emptyInsights(): TradeJournalInsights {
  return {
    calibration: {
      bins: [],
      total: 0,
      overall_win_rate: null,
      calibration_error: null,
      n_scored_bins: 0,
      n_bins: 10,
      min_trades_per_bin: 5,
      reason: "No conviction-annotated trades yet.",
    },
    cohorts: {
      signal_driven: { n_trades: 0, win_rate: null, mean_realized_pnl_pct: null },
      manual: { n_trades: 0, win_rate: null, mean_realized_pnl_pct: null },
      unknown: { n_trades: 0, win_rate: null, mean_realized_pnl_pct: null },
    },
  };
}

describe("TradeJournal screen (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the tab guide", async () => {
    renderScreen();
    await waitFor(() => {
      expect(screen.getByTestId("tab-guide-trade-journal")).toBeInTheDocument();
    });
  });

  it("renders entries from the mock fixture", async () => {
    renderScreen();
    const entries = await screen.findAllByTestId("trade-journal-entry");
    expect(entries.length).toBe(4);
  });

  it("renders realized P&L with a percentage when available", async () => {
    renderScreen();
    const entries = await screen.findAllByTestId("trade-journal-entry");
    const aaplCard = entries.find((el) => within(el).queryByText("AAPL"));
    expect(aaplCard).toBeDefined();
    // AAPL: realized_pnl_pct = 0.0504 -> "+5.04%" parenthetical present.
    const outcome = within(aaplCard as HTMLElement).getByTestId("trade-journal-outcome");
    expect(outcome.textContent).toMatch(/\+\$130\.50/);
    expect(outcome.textContent).toMatch(/\+5\.04%/);
  });

  it("omits the percentage (never renders null%/NaN%) when realized_pnl_pct is null", async () => {
    renderScreen();
    const entries = await screen.findAllByTestId("trade-journal-entry");
    const tslaCard = entries.find((el) => within(el).queryByText("TSLA"));
    expect(tslaCard).toBeDefined();
    const card = tslaCard as HTMLElement;
    // The dollar amount renders, scoped to the OutcomeLine element itself
    // (not the free-text narrative, which separately mentions the same
    // figure in its own prose) ...
    const outcome = within(card).getByTestId("trade-journal-outcome");
    expect(outcome.textContent).toMatch(/\+\$1,027\.00/);
    // ...but no percentage parenthetical in THIS element, and CONSTRAINT
    // #4: never the literal strings "null%"/"NaN%" anywhere in the card.
    expect(outcome.textContent).not.toMatch(/\(/);
    expect(card.textContent).not.toMatch(/null%/i);
    expect(card.textContent).not.toMatch(/NaN%/i);
  });

  it("renders all three decision-state badges, each genuinely distinct", async () => {
    renderScreen();
    await screen.findAllByTestId("trade-journal-entry");

    const signalDriven = screen.getAllByText(/Signal-driven/)[0];
    const manual = screen.getByText(/🖐️ Manual/);
    const unknown = screen.getByText(/❓ Unknown/);

    expect(signalDriven).toBeInTheDocument();
    expect(manual).toBeInTheDocument();
    expect(unknown).toBeInTheDocument();

    // Genuinely different-looking, not just different text -- each badge's
    // tone maps to a distinct color token (never all three sharing one).
    const colors = new Set(
      [signalDriven, manual, unknown].map((el) => (el as HTMLElement).style.color)
    );
    expect(colors.size).toBe(3);
  });

  it('shows a real, visible "Evaluation data unavailable" badge with its real reason', async () => {
    renderScreen();
    const entries = await screen.findAllByTestId("trade-journal-entry");
    const gmeCard = entries.find((el) => within(el).queryByText("GME"));
    expect(gmeCard).toBeDefined();
    const badge = within(gmeCard as HTMLElement).getByText(
      /Evaluation data unavailable — no price history available for this hold period/
    );
    expect(badge).toBeInTheDocument();
  });

  it("does NOT show the evaluation-unavailable badge for a trade whose evaluation is available", async () => {
    renderScreen();
    const entries = await screen.findAllByTestId("trade-journal-entry");
    const aaplCard = entries.find((el) => within(el).queryByText("AAPL"));
    expect(aaplCard).toBeDefined();
    expect(
      within(aaplCard as HTMLElement).queryByText(/Evaluation data unavailable/)
    ).not.toBeInTheDocument();
  });

  it("renders the narrative sentence for each entry", async () => {
    renderScreen();
    await screen.findAllByTestId("trade-journal-entry");
    expect(
      screen.getByText(/You placed this trade manually — no model signal was behind it\./)
    ).toBeInTheDocument();
  });

  it("empty state renders honestly when there are zero closed trades", async () => {
    vi.spyOn(api, "getTradeJournalEntries").mockResolvedValueOnce(mockTradeJournalEntriesEmpty());
    renderScreen();
    expect(await screen.findByText("No trades yet")).toBeInTheDocument();
    expect(screen.queryByTestId("trade-journal-entry")).not.toBeInTheDocument();
  });

  it("Patterns panel renders exactly 3 cohort sections and no 4th combined/overall cohort card", async () => {
    renderScreen();
    await waitFor(() => {
      expect(screen.getByText("Signal-driven")).toBeInTheDocument();
    });
    expect(screen.getByText("Manual")).toBeInTheDocument();
    expect(screen.getByText("Unknown")).toBeInTheDocument();

    const cohortCards = screen.getAllByTestId(/^trade-journal-cohort-/);
    expect(cohortCards).toHaveLength(3);
    const titles = cohortCards.map((c) => c.getAttribute("data-testid"));
    expect(titles).toEqual([
      "trade-journal-cohort-signal-driven",
      "trade-journal-cohort-manual",
      "trade-journal-cohort-unknown",
    ]);
    expect(screen.queryByTestId("trade-journal-cohort-overall")).not.toBeInTheDocument();
    expect(screen.queryByTestId("trade-journal-cohort-combined")).not.toBeInTheDocument();
  });

  it("cohort cards render honest empty values (win_rate null -> —, never 0%) when a cohort has zero trades", async () => {
    vi.spyOn(api, "getTradeJournalInsights").mockResolvedValueOnce(emptyInsights());
    renderScreen();
    await waitFor(() => {
      expect(screen.getByText("Signal-driven")).toBeInTheDocument();
    });
    const cohortCards = screen.getAllByTestId(/^trade-journal-cohort-/);
    for (const card of cohortCards) {
      expect(within(card).getByText("0")).toBeInTheDocument(); // Trades: 0
      // At least one "—" for the null win rate / mean P&L tiles.
      expect(within(card).getAllByText("—").length).toBeGreaterThanOrEqual(1);
    }
  });

  it("renders the calibration reliability diagram from the reused Calibration component", async () => {
    renderScreen();
    await waitFor(() => {
      expect(screen.getByRole("img", { name: /reliability diagram/i })).toBeInTheDocument();
    });
  });

  it("bridge status note renders the disabled reason", async () => {
    renderScreen();
    await waitFor(() => {
      expect(screen.getByText(/Evaluation bridge \(disabled\)/)).toBeInTheDocument();
    });
  });

  it("symbol filter narrows the request", async () => {
    const spy = vi.spyOn(api, "getTradeJournalEntries");
    renderScreen();
    await screen.findAllByTestId("trade-journal-entry");

    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "aapl" } });

    await waitFor(() => {
      const lastCall = spy.mock.calls[spy.mock.calls.length - 1]?.[0];
      expect(lastCall).toMatchObject({ symbol: "AAPL" });
    });
  });
});

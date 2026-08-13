/**
 * ExecutionQueueSection.test.tsx — the shared, read-only Robinhood execution
 * queue view (Commands + Agentic Trading screens): the multi-attribute
 * filter bar (Side / Strategy / Status / Min Conviction), the collapsible
 * minimize toggle, and the honest empty-state copy (filtered-to-nothing vs.
 * a genuinely empty queue).
 *
 * The "Strategy" filter's options come from `available_follow_types` — the
 * REAL per-intent attribution (advisory / a followed Pilot's id), never a
 * hardcoded guess at pilot names (CONSTRAINT #4). This file pins that
 * contract against regressing back to a fixed, fictional dropdown.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExecutionQueueSection } from "./ExecutionQueueSection";
import { api } from "../api/client";

function renderSection() {
  return render(
    <MemoryRouter>
      <ExecutionQueueSection />
    </MemoryRouter>
  );
}

describe("ExecutionQueueSection (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the filter bar with real, non-fabricated Strategy options", async () => {
    renderSection();
    await screen.findAllByTestId("execution-intent-row");

    // Never the old hardcoded/fictional "MACD Trend" / "Composite Signal"
    // categories -- only real attribution values the mock queue actually
    // carries (a base advisory intent, and one attributed to the
    // "trend-following" mock Pilot).
    const strategySelect = screen.getByLabelText(/Strategy:/i) as HTMLSelectElement;
    const optionLabels = Array.from(strategySelect.options).map((o) => o.textContent);
    expect(optionLabels).toEqual(["All Strategies", "Advisory", "Trend Following"]);
    expect(optionLabels).not.toContain("MACD Trend");
    expect(optionLabels).not.toContain("Composite Signal");
  });

  it("filters to only SELL intents when the Side filter is changed, requesting the right params", async () => {
    const querySpy = vi.spyOn(api, "getExecutionQueue");
    renderSection();
    await screen.findAllByTestId("execution-intent-row");

    fireEvent.change(screen.getByLabelText(/Side:/i), { target: { value: "SELL" } });

    await waitFor(() => {
      const rows = screen.getAllByTestId("execution-intent-row");
      expect(rows).toHaveLength(1);
      expect(rows[0].textContent).toContain("TSLA");
    });
    expect(querySpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ action: "SELL" })
    );
  });

  it("filters to only Ready (placeable) intents via the Status filter", async () => {
    renderSection();
    await screen.findAllByTestId("execution-intent-row");

    fireEvent.change(screen.getByLabelText(/Status:/i), { target: { value: "Ready" } });

    await waitFor(() => {
      const rows = screen.getAllByTestId("execution-intent-row");
      expect(rows).toHaveLength(1);
      expect(rows[0].textContent).toContain("AAPL");
    });
  });

  it("shows a filter-specific empty state (not 'queue is empty') when a filter matches nothing", async () => {
    renderSection();
    await screen.findAllByTestId("execution-intent-row");

    // 100% min conviction excludes both mock intents (0.8 and 0.6).
    fireEvent.change(screen.getByLabelText(/Min Conviction/i), { target: { value: "100" } });

    expect(
      await screen.findByText("No execution items match the selected filter criteria.")
    ).toBeInTheDocument();
  });

  it("toggles minimize/expand without discarding the active filters", async () => {
    renderSection();
    await screen.findAllByTestId("execution-intent-row");

    fireEvent.change(screen.getByLabelText(/Side:/i), { target: { value: "BUY" } });
    await waitFor(() => {
      expect(screen.getAllByTestId("execution-intent-row")).toHaveLength(1);
    });

    fireEvent.click(screen.getByRole("button", { name: /Minimize/i }));
    expect(screen.queryByTestId("execution-intent-row")).not.toBeInTheDocument();
    expect((screen.getByLabelText(/Side:/i) as HTMLSelectElement).value).toBe("BUY");

    fireEvent.click(screen.getByRole("button", { name: /Expand/i }));
    await waitFor(() => {
      const rows = screen.getAllByTestId("execution-intent-row");
      expect(rows).toHaveLength(1);
      expect(rows[0].textContent).toContain("AAPL");
    });
  });

  it("Enter on the symbol Link doesn't also toggle the ancestor row's expand state", async () => {
    renderSection();
    const rows = await screen.findAllByTestId("execution-intent-row");
    const row = rows[0];
    const toggle = within(row).getByRole("button", { name: /Toggle details/i });
    const link = within(row).getByRole("link");

    expect(toggle).toHaveAttribute("aria-expanded", "false");

    link.focus();
    fireEvent.keyDown(link, { key: "Enter", code: "Enter" });

    // The Link's own onKeyDown stops the event from bubbling to the row's
    // onKeyDown -- pressing Enter to follow the link must not also expand
    // the row underneath it.
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("filters out items below a partial/fractional conviction value", async () => {
    const querySpy = vi.spyOn(api, "getExecutionQueue");
    renderSection();
    await screen.findAllByTestId("execution-intent-row");

    // AAPL has conviction 0.8, TSLA has conviction 0.6.
    // Setting to 70% (0.7) should filter out TSLA and keep AAPL.
    fireEvent.change(screen.getByLabelText(/Min Conviction/i), { target: { value: "70" } });

    await waitFor(() => {
      const rows = screen.getAllByTestId("execution-intent-row");
      expect(rows).toHaveLength(1);
      expect(rows[0].textContent).toContain("AAPL");
    });
    
    expect(querySpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ min_conviction: 0.7 })
    );
  });

  it("filters correctly by Strategy (follow_type) selection", async () => {
    const querySpy = vi.spyOn(api, "getExecutionQueue");
    renderSection();
    await screen.findAllByTestId("execution-intent-row");

    // Setting Strategy to "trend-following" (which maps to "Trend Following" label)
    fireEvent.change(screen.getByLabelText(/Strategy:/i), { target: { value: "trend-following" } });

    await waitFor(() => {
      const rows = screen.getAllByTestId("execution-intent-row");
      expect(rows).toHaveLength(1);
      expect(rows[0].textContent).toContain("TSLA");
    });

    expect(querySpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ follow_type: "trend-following" })
    );
  });

  describe("expanded row: signal panel + review-decision flow", () => {
    it("expanding a row renders its SignalContributionPanel and real strategy/sources/price metadata", async () => {
      renderSection();
      const rows = await screen.findAllByTestId("execution-intent-row");
      const aaplRow = rows.find((r) => r.textContent?.includes("AAPL"))!;

      fireEvent.click(within(aaplRow).getByRole("button", { name: /Toggle details/i }));

      // SignalContributionPanel's own module bars come from GET
      // /metrics/signals/{symbol} rendered via recharts, which never
      // measures real pixel sizes in jsdom -- so, matching this codebase's
      // chart-testing convention (see SignalContributionPanel.test.tsx),
      // assert on the chart's mount point rather than chart-internal tick
      // text, which is enough to prove the panel actually mounted and
      // fetched, not just that the drawer opened.
      await waitFor(() => {
        expect(aaplRow.querySelector(".recharts-responsive-container")).not.toBeNull();
      });

      // The queue's own real attribution fields (types.ts step 3 additions),
      // never fabricated client-side.
      expect(within(aaplRow).getByText(/timeseries_momentum/)).toBeInTheDocument();
      expect(within(aaplRow).getByText(/fmp_news, edgar_8k/)).toBeInTheDocument();
      expect(within(aaplRow).getByText(/\$231\.42/)).toBeInTheDocument();
    });

    it("'Mark as Reviewed' logs an 'acted' decision via api.logDecision and never touches an order-placement endpoint", async () => {
      const logSpy = vi.spyOn(api, "logDecision");
      renderSection();
      const rows = await screen.findAllByTestId("execution-intent-row");
      const aaplRow = rows.find((r) => r.textContent?.includes("AAPL"))!;
      fireEvent.click(within(aaplRow).getByRole("button", { name: /Toggle details/i }));

      fireEvent.click(await within(aaplRow).findByRole("button", { name: /Mark as Reviewed/i }));

      await waitFor(() => {
        expect(within(aaplRow).getByText("✓ Reviewed")).toBeInTheDocument();
      });
      expect(logSpy).toHaveBeenCalledTimes(1);
      expect(logSpy).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "AAPL", action_taken: "acted", signal_action: "BUY" })
      );
      // The only client exposing order placement is the Robinhood MCP itself
      // (reached solely from a live Claude Code session per this component's
      // own docstring) -- nothing on the `api` client used here has such a
      // method, so there is structurally no other call this component could
      // have made.
      expect(Object.keys(api)).not.toContain("placeOrder");
      expect(Object.keys(api).some((k) => /placeorder|submitorder|executeorder/i.test(k))).toBe(false);
    });

    it("'Pass' logs a 'passed' decision and shows the passed status, without calling logDecision twice", async () => {
      const logSpy = vi.spyOn(api, "logDecision");
      renderSection();
      const rows = await screen.findAllByTestId("execution-intent-row");
      const tslaRow = rows.find((r) => r.textContent?.includes("TSLA"))!;
      fireEvent.click(within(tslaRow).getByRole("button", { name: /Toggle details/i }));

      fireEvent.click(await within(tslaRow).findByRole("button", { name: /^Pass$/i }));

      await waitFor(() => {
        expect(within(tslaRow).getByText("✗ Passed")).toBeInTheDocument();
      });
      expect(logSpy).toHaveBeenCalledTimes(1);
      expect(logSpy).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "TSLA", action_taken: "passed" })
      );
      // Once reviewed, the Pass/Mark-as-Reviewed buttons are gone -- can't
      // double-log a decision for the same row.
      expect(within(tslaRow).queryByRole("button", { name: /^Pass$/i })).not.toBeInTheDocument();
      expect(within(tslaRow).queryByRole("button", { name: /Mark as Reviewed/i })).not.toBeInTheDocument();
    });

    it("shows the non-advisory operator-review copy pointing at the robinhood-execution skill when advisoryOnly is false", async () => {
      // ExecutionModeCtx defaults advisoryOnly=true with no provider; render
      // with a real ExecutionModeProvider whose underlying automation-status
      // fetch is live-mode so advisoryOnly resolves false.
      const { ExecutionModeProvider } = await import("./ExecutionModeContext");
      vi.spyOn(api, "getAutomationStatus").mockResolvedValue({
        advisory_only: false,
        alpaca_paper: false,
        dry_run: false,
        kill_switch: { active: false, reason: null },
        daemon: { alive: true },
        pipeline: { snapshot_age_seconds: 5 },
      } as any);

      render(
        <MemoryRouter>
          <ExecutionModeProvider>
            <ExecutionQueueSection />
          </ExecutionModeProvider>
        </MemoryRouter>
      );
      const rows = await screen.findAllByTestId("execution-intent-row");
      const aaplRow = rows.find((r) => r.textContent?.includes("AAPL"))!;
      fireEvent.click(within(aaplRow).getByRole("button", { name: /Toggle details/i }));

      expect(
        await within(aaplRow).findByText(/robinhood-execution skill in Claude Code/i)
      ).toBeInTheDocument();
    });
  });
});

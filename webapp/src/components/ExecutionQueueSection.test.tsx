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
});

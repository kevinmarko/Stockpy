/**
 * ExecutionQueue.test.tsx — Pilots Manager's read-only execution-queue card.
 * Renders against the REAL mock API (no vi.mock — `api` resolves to
 * `mockApi` by default), covering the MOCK_EXECUTION_QUEUE fixture (one
 * placeable BUY, one blocked SELL) and the honest empty state when the
 * queue genuinely has nothing queued (CONSTRAINT #4 — never fabricated
 * rows).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExecutionQueue } from "./ExecutionQueue";
import { api } from "../../api/client";
import type { ExecutionQueue as ExecutionQueueData } from "../../api/types";

function renderQueue() {
  return render(
    <MemoryRouter>
      <ExecutionQueue />
    </MemoryRouter>
  );
}

describe("ExecutionQueue (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the real queued intents from the mock fixture, not fabricated rows", async () => {
    renderQueue();

    const rows = await screen.findAllByTestId("pilots-queue-row");
    expect(rows).toHaveLength(2);

    const aaplRow = rows.find((r) => r.textContent?.includes("AAPL"));
    expect(aaplRow).toBeDefined();
    expect(aaplRow!.textContent).toContain("BUY");
    expect(aaplRow!.textContent).toContain("Ready to place");

    const tslaRow = rows.find((r) => r.textContent?.includes("TSLA"));
    expect(tslaRow).toBeDefined();
    expect(tslaRow!.textContent).toContain("SELL");
    expect(tslaRow!.textContent).toContain("Blocked");
  });

  it("never renders order-placement controls (Approve/Reject) -- this is a read-only view", async () => {
    renderQueue();
    await screen.findAllByTestId("pilots-queue-row");

    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reject/i })).not.toBeInTheDocument();
  });

  it("renders an honest 'No pending trades.' empty state when the queue is genuinely empty", async () => {
    vi.spyOn(api, "getExecutionQueue").mockResolvedValueOnce({
      generated_at: null,
      mode: "off",
      kill_switch_active: false,
      max_notional_per_order: 500,
      n_intents: 0,
      n_placeable: 0,
      stale: false,
      age_seconds: null,
      intents: [],
      reason: "No queue has been generated yet.",
    } satisfies ExecutionQueueData);

    renderQueue();

    await waitFor(() => expect(screen.getByText("No pending trades.")).toBeInTheDocument());
    expect(screen.getByText("No queue has been generated yet.")).toBeInTheDocument();
    expect(screen.queryByTestId("pilots-queue-row")).not.toBeInTheDocument();
  });
});

/**
 * FollowModal.test.tsx — this is the honesty-critical UI surface: it must
 * NEVER present a follow as an executed trade. Tests the amount input +
 * quick-amount chips, min-amount gating, the unmissable "gated queue" notice
 * (both before AND after submit), Cancel, and the queue preview against the
 * real mock `api.follow()`.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FollowModal } from "./FollowModal";
import type { PilotSummary } from "../api/types";

const PILOT: PilotSummary = {
  id: "trend-following",
  name: "Trend Follower",
  category: "Momentum",
  description: "test pilot",
  headline: { sharpe: 1.1, dsr: 0.97, pbo: 0.2, max_drawdown: 0.18, deployable: true },
  holdings_count: 5,
  aum_proxy: 10000,
  followers_proxy: 12,
  long_only: true,
};

describe("FollowModal (real mock API)", () => {
  it("shows the amount field, quick-amount chips, and the pre-submit gated-queue notice", () => {
    render(<FollowModal pilot={PILOT} onClose={vi.fn()} />);

    expect(screen.getByLabelText("Amount (USD)")).toHaveValue(500);
    expect(screen.getByRole("button", { name: "$250" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "$1000" })).toBeInTheDocument();
    expect(
      screen.getByText(/gated, paper-first order queue you must/i)
    ).toBeInTheDocument();
    expect(screen.getByText(/No order is placed automatically/i)).toBeInTheDocument();
  });

  it("a quick-amount chip updates the input value", async () => {
    const user = userEvent.setup();
    render(<FollowModal pilot={PILOT} onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "$2500" }));
    expect(screen.getByLabelText("Amount (USD)")).toHaveValue(2500);
  });

  it("Cancel calls onClose without submitting", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<FollowModal pilot={PILOT} onClose={onClose} />);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("submitting builds a queue preview that never claims an order was placed", async () => {
    const user = userEvent.setup();
    const onFollowed = vi.fn();
    render(<FollowModal pilot={PILOT} onClose={vi.fn()} onFollowed={onFollowed} />);

    await user.click(screen.getByRole("button", { name: "Preview queue" }));

    expect(await screen.findByText("Queue preview")).toBeInTheDocument();
    expect(screen.getByText("Execution mode")).toBeInTheDocument();
    // The notice text is always the honest gated-queue disclosure, never an
    // "order placed" confirmation.
    expect(
      screen.getByText(/gated, paper-first order queue that you must confirm/i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/order (has been|was) placed/i)).not.toBeInTheDocument();
    expect(onFollowed).toHaveBeenCalledTimes(1);
  });

  it("below the minimum allocation, Preview queue is disabled with a min-amount hint", async () => {
    const user = userEvent.setup();
    render(<FollowModal pilot={PILOT} onClose={vi.fn()} />);

    const input = screen.getByLabelText("Amount (USD)");
    await user.clear(input);
    await user.type(input, "10");

    expect(screen.getByText(/Minimum allocation is/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview queue" })).toBeDisabled();
  });
});

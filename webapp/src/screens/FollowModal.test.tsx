/**
 * FollowModal.test.tsx — this is the honesty-critical UI surface: it must
 * NEVER present a follow as an executed trade. Tests the amount input +
 * quick-amount chips, min-amount gating, the unmissable "gated queue" notice
 * (both before AND after submit), Cancel, and the queue preview against the
 * real mock `api.follow()`.
 *
 * Also covers the minimum-allocation honesty fix: the pre-submit minimum
 * must come from the live GET /thresholds fetch (never a re-typed literal
 * like the old hardcoded `100`), degrade to an honest "—" before that fetch
 * resolves, and defer to `result.min_amount` once a real follow response
 * exists (see `resolveMinAmount` in FollowModal.tsx).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FollowModal, resolveMinAmount } from "./FollowModal";
import { api } from "../api/client";
import { __resetThresholdsCache } from "../help/thresholds";
import type { FollowResult, PilotSummary, Thresholds } from "../api/types";

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

afterEach(() => {
  // The thresholds loader caches at module scope (by design — see
  // help/thresholds.ts) so a spy or a resolved value from one test would
  // otherwise leak into the next.
  __resetThresholdsCache();
  vi.restoreAllMocks();
});

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

    // The live GET /thresholds fetch (mock delay ~260ms) must resolve before
    // the below-minimum gate can honestly fire — findByText waits for it.
    expect(await screen.findByText(/Minimum allocation is/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview queue" })).toBeDisabled();
  });

  it("shows the live GET /thresholds follow_min_amount, not the old hardcoded 100, once it resolves", async () => {
    render(<FollowModal pilot={PILOT} onClose={vi.fn()} />);

    // Mock's getThresholds() returns follow_min_amount: 100 — same numeric
    // value as the old literal, but now sourced from a live fetch (proven by
    // the pre-load assertion in the next test, and by resolveMinAmount's
    // unit tests below).
    expect(await screen.findByText("Minimum allocation: $100.00")).toBeInTheDocument();
  });

  it("renders an honest '—' placeholder — never a fabricated number — before the live fetch resolves", () => {
    // A promise that never resolves within the test pins the component in
    // its pre-load state so the assertion below is deterministic rather than
    // racing the mock API's ~260ms delay.
    vi.spyOn(api, "getThresholds").mockImplementation(() => new Promise(() => {}));

    render(<FollowModal pilot={PILOT} onClose={vi.fn()} />);

    expect(screen.getByText("Minimum allocation: —")).toBeInTheDocument();
    expect(screen.queryByText(/\$100\.00/)).not.toBeInTheDocument();
  });
});

describe("resolveMinAmount (priority: result.min_amount > live thresholds > honest null)", () => {
  const THRESHOLDS: Thresholds = {
    pbo_max: 0.5,
    dsr_min: 0.95,
    net_sharpe_min: 0.5,
    max_drawdown_max: 0.3,
    stress_max_drawdown: 0.5,
    kelly_fraction: 0.5,
    kelly_cap: 0.2,
    robinhood_max_notional_per_order: 2500,
    follow_min_amount: 100,
    agentic_max_candidates: 25,
    retrain_window_days: 30,
  };

  // min_amount deliberately differs from THRESHOLDS.follow_min_amount to
  // prove precedence: a real follow response is authoritative even when it
  // disagrees with the cached GET /thresholds value (e.g. a server-side
  // override GET /thresholds wouldn't know about).
  const RESULT: FollowResult = {
    follow: {
      pilot_id: PILOT.id,
      amount: 500,
      created_at: "2026-07-18T00:00:00Z",
      updated_at: "2026-07-18T00:00:00Z",
      status: "active",
    },
    planned_intents: [],
    mode: "review",
    queue_written: false,
    notional_cap: 2500,
    min_amount: 250,
    notice: "test",
  };

  it("is honestly null when neither a follow result nor live thresholds have resolved", () => {
    expect(resolveMinAmount(null, null)).toBeNull();
  });

  it("uses the live GET /thresholds value before any follow response exists", () => {
    expect(resolveMinAmount(null, THRESHOLDS)).toBe(100);
  });

  it("prefers result.min_amount over the live threshold once a follow response exists", () => {
    expect(resolveMinAmount(RESULT, THRESHOLDS)).toBe(250);
  });

  it("still falls back to result.min_amount when live thresholds never resolved", () => {
    expect(resolveMinAmount(RESULT, null)).toBe(250);
  });
});

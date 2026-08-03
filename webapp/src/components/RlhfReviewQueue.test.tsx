/**
 * RlhfReviewQueue.test.tsx — the Agentic Trading screen's "RLHF Review
 * Queue" section (nested INSIDE /agentic, not a standalone route, and never
 * labeled "Calibration" -- see the component's own doc comment for why that
 * matters: this repo already has an unrelated `/calibration` screen).
 *
 * Covers the honesty branches MOCK_RLHF_PROPOSALS (api/mock.ts) was built to
 * exercise: a null price rendering "—" not "0"/blank, an auto-approved
 * proposal never reaching the pending list (while the KPI strip still counts
 * it), an honest empty-queue `reason`, and a real submit-review round trip
 * against the mock API that removes the row from the pending queue.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RlhfReviewQueue } from "./RlhfReviewQueue";
import { api } from "../api/client";
import type { RlhfSummary } from "../api/types";

function renderQueue(refreshToken = 0) {
  return render(<RlhfReviewQueue refreshToken={refreshToken} />);
}

describe("RlhfReviewQueue (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders a null price as '—', never '0' or blank", async () => {
    renderQueue();
    const rows = await screen.findAllByTestId("rlhf-proposal-row");
    const nvdaRow = rows.find((r) => r.textContent?.includes("NVDA"));
    expect(nvdaRow).toBeDefined();
    expect(nvdaRow!.textContent).toContain("—");
    expect(nvdaRow!.textContent).not.toMatch(/\$0(\.00)?\b/);
  });

  it("never lists an auto-approved (already-reviewed) proposal in the pending queue, but the KPI strip still counts it", async () => {
    renderQueue();
    await screen.findAllByTestId("rlhf-proposal-row");

    // TSLA is auto_approved + status:"reviewed" in the fixture -- it must
    // never appear as a row a human could "review" with no rating shell to
    // fill (the pending list and the KPI strip are both server-computed
    // from the same store, so this also pins the mock's own consistency).
    expect(screen.queryByText("TSLA")).not.toBeInTheDocument();

    const autoApprovedTile = screen.getByText("Auto-approved").closest(".tile");
    expect(autoApprovedTile?.textContent).toContain("1");
  });

  it("renders the summary's honest `reason` when the pending queue is empty", async () => {
    vi.spyOn(api, "getRlhfSummary").mockResolvedValueOnce({
      proposals: [],
      kpis: {
        pending_count: 0,
        reviewed_count: 0,
        average_human_rating: null,
        rating_distribution: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 },
        auto_approved_count: 0,
        sft_exported_count: 0,
      },
      writable: true,
      reason: "No proposals yet -- the agent hasn't run.",
    } satisfies RlhfSummary);

    renderQueue();

    expect(await screen.findByText("No proposals yet -- the agent hasn't run.")).toBeInTheDocument();
    // average_human_rating: null renders "—", never a fabricated "0".
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByTestId("rlhf-proposal-row")).not.toBeInTheDocument();
  });

  it("renders the AI's technical context (extra_context) in the review modal, not just RSI/sentiment", async () => {
    renderQueue();
    const rows = await screen.findAllByTestId("rlhf-proposal-row");
    const msftRow = rows.find((r) => r.textContent?.includes("MSFT"));
    fireEvent.click(within(msftRow!).getByRole("button", { name: "Review" }));

    const dialog = await screen.findByRole("dialog");
    // MSFT's fixture extra_context is { xsec_momentum_rank: 0.94 } -- a
    // human grading the rationale needs to see this, not just RSI/sentiment.
    // Runs BEFORE the submit-review test below, which mutates this same
    // MSFT row (module-level MOCK_RLHF_PROPOSALS state persists across
    // tests in this file) out of the pending list entirely.
    const context = within(dialog).getByTestId("rlhf-extra-context");
    expect(context.textContent).toContain("xsec_momentum_rank");
    expect(context.textContent).toContain("0.94");
  });

  it("submits a review through the mock API and removes the row from the pending list", async () => {
    const submitSpy = vi.spyOn(api, "submitRlhfReview");
    renderQueue();

    const rows = await screen.findAllByTestId("rlhf-proposal-row");
    const msftRow = rows.find((r) => r.textContent?.includes("MSFT"));
    expect(msftRow).toBeDefined();
    fireEvent.click(within(msftRow!).getByRole("button", { name: "Review" }));

    const dialog = await screen.findByRole("dialog");
    // Submit is disabled until a rating is chosen.
    expect(within(dialog).getByRole("button", { name: "Submit rating" })).toBeDisabled();

    fireEvent.click(within(dialog).getByRole("radio", { name: "5 stars" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Submit rating" }));

    await screen.findByTestId("rlhf-review-result");
    // MSFT's id in the fixture is 4.
    expect(submitSpy).toHaveBeenCalledWith(4, { human_rating: 5, human_correction: undefined });

    fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));

    await waitFor(() => {
      const remaining = screen.queryAllByTestId("rlhf-proposal-row");
      expect(remaining.some((r) => r.textContent?.includes("MSFT"))).toBe(false);
    });
  });

  it("when RLHF_CALIBRATION_ENABLED is off server-side, hides review/export controls instead of letting a submit 403", async () => {
    vi.spyOn(api, "getRlhfSummary").mockResolvedValueOnce({
      proposals: [
        {
          id: 99,
          created_at: new Date().toISOString(),
          symbol: "AMD",
          action: "BUY",
          quantity: 5,
          price: 150,
          rationale: "test",
          confidence: 0.6,
          rsi: 50,
          sentiment_score: 0.1,
          extra_context: null,
          status: "pending",
          human_rating: null,
          human_correction: null,
          reviewed_at: null,
          auto_approved: false,
          sft_exported: false,
        },
      ],
      kpis: {
        pending_count: 1,
        reviewed_count: 0,
        average_human_rating: null,
        rating_distribution: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 },
        auto_approved_count: 0,
        sft_exported_count: 0,
      },
      writable: false,
      reason: null,
    } satisfies RlhfSummary);

    renderQueue();

    await screen.findAllByTestId("rlhf-proposal-row");
    expect(screen.queryByRole("button", { name: "Review" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export to SFT dataset" })).not.toBeInTheDocument();
    expect(screen.getByText(/RLHF_CALIBRATION_ENABLED=false/)).toBeInTheDocument();
  });
});

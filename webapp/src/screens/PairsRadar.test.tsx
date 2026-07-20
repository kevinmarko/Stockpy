/**
 * PairsRadar.test.tsx — the pairs radar sub-page renders cointegrated pair
 * cards from the mock, and renders the honest empty state (with the persisted
 * reason) when no pairs are available — never a fabricated pair. Also covers
 * the on-demand "Analyze a pair" / "Scan for pairs" recompute sections added
 * for webapp porting backlog item 8a.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PairsRadar } from "./PairsRadar";
import { api, ApiError } from "../api/client";

function renderPairs() {
  return render(
    <MemoryRouter>
      <PairsRadar />
    </MemoryRouter>
  );
}

/** Renders the screen (if not already rendered) and expands the recompute section. */
async function openRecompute(opts: { alreadyRendered?: boolean } = {}) {
  if (!opts.alreadyRendered) renderPairs();
  const user = userEvent.setup();
  await user.click(await screen.findByText(/Recompute with custom symbols/));
  return user;
}

describe("PairsRadar screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the ranked pair cards from the mock", async () => {
    renderPairs();
    expect(await screen.findByRole("heading", { name: "Pairs radar" })).toBeInTheDocument();
    // The first mock pair (XOM / CVX) renders its tickers.
    expect(await screen.findByText(/XOM/)).toBeInTheDocument();
    expect(screen.getAllByText("z-score").length).toBeGreaterThan(0);
  });

  it("an empty radar renders the honest reason, never a fabricated pair", async () => {
    vi.spyOn(api, "getPairs").mockResolvedValueOnce({
      as_of: null,
      universe: [],
      pairs: [],
      reason: "Pairs radar not generated yet — enable PAIRS_SNAPSHOT_ENABLED.",
    });
    renderPairs();
    expect(
      await screen.findByText(/Pairs radar not generated yet/)
    ).toBeInTheDocument();
  });

  describe("Analyze a pair (on-demand recompute, backlog item 8a)", () => {
    it("is hidden until the recompute section is expanded", async () => {
      renderPairs();
      await screen.findByRole("heading", { name: "Pairs radar" });
      expect(screen.queryByText("Analyze a pair")).not.toBeInTheDocument();
      await openRecompute({ alreadyRendered: true });
      expect(await screen.findByText("Analyze a pair")).toBeInTheDocument();
    });

    it("Analyze is disabled until both symbols are filled and distinct", async () => {
      const user = await openRecompute();
      const analyzeButton = screen.getByRole("button", { name: "Analyze" });
      expect(analyzeButton).toBeDisabled();

      await user.type(screen.getByLabelText("Symbol Y (dependent)"), "AAPL");
      expect(analyzeButton).toBeDisabled(); // X still empty

      await user.type(screen.getByLabelText("Symbol X (hedge)"), "AAPL");
      expect(analyzeButton).toBeDisabled(); // identical symbols

      await user.clear(screen.getByLabelText("Symbol X (hedge)"));
      await user.type(screen.getByLabelText("Symbol X (hedge)"), "MSFT");
      expect(analyzeButton).toBeEnabled();
    });

    it("renders a successful analysis with its z-score chart", async () => {
      const user = await openRecompute();
      await user.type(screen.getByLabelText("Symbol Y (dependent)"), "Y");
      await user.type(screen.getByLabelText("Symbol X (hedge)"), "X");
      await user.click(screen.getByRole("button", { name: "Analyze" }));

      expect(await screen.findByText(/This is a displayed signal, not an order/)).toBeInTheDocument();
      expect(screen.getAllByText("z-score").length).toBeGreaterThan(0);
    });

    it("an unresolved pair renders the honest not-found reason, not an error", async () => {
      const user = await openRecompute();
      // "ZZZ" is the mock's dead-letter/no-data convention symbol.
      await user.type(screen.getByLabelText("Symbol Y (dependent)"), "ZZZ");
      await user.type(screen.getByLabelText("Symbol X (hedge)"), "AAPL");
      await user.click(screen.getByRole("button", { name: "Analyze" }));

      expect(
        await screen.findByText(/Insufficient aligned history for ZZZ\/AAPL/)
      ).toBeInTheDocument();
    });

    it("a server error renders inline, not a generic failure", async () => {
      vi.spyOn(api, "analyzePairs").mockRejectedValueOnce(
        new ApiError("Symbol Y and Symbol X must be different tickers.", 422)
      );
      const user = await openRecompute();
      await user.type(screen.getByLabelText("Symbol Y (dependent)"), "Y");
      await user.type(screen.getByLabelText("Symbol X (hedge)"), "Z");
      await user.click(screen.getByRole("button", { name: "Analyze" }));

      expect(
        await screen.findByText("Symbol Y and Symbol X must be different tickers.")
      ).toBeInTheDocument();
    });
  });

  describe("Scan for pairs (on-demand recompute, backlog item 8a follow-on)", () => {
    it("Scan stays disabled below the 2-symbol minimum and above the 15-symbol cap", async () => {
      const user = await openRecompute();
      const input = screen.getByLabelText("Symbols (comma or space separated)");
      const scanButton = screen.getByRole("button", { name: "Scan" });

      await user.type(input, "AAPL");
      expect(scanButton).toBeDisabled();

      await user.type(input, ", MSFT");
      expect(scanButton).toBeEnabled();

      await user.clear(input);
      await user.type(input, Array.from({ length: 16 }, (_, i) => `SYM${i}`).join(","));
      expect(scanButton).toBeDisabled();
    });

    it("renders scanned pairs and dead-letters an unresolved symbol into 'missing'", async () => {
      const user = await openRecompute();
      await user.type(
        screen.getByLabelText("Symbols (comma or space separated)"),
        "XOM, CVX, GHOST"
      );
      await user.click(screen.getByRole("button", { name: "Scan" }));

      expect(await screen.findByText(/No data for: GHOST/)).toBeInTheDocument();
      // "XOM" also appears in the persisted GET /pairs view above -- this
      // just confirms the scan result rendered at least one pair card too.
      expect(screen.getAllByText(/XOM/).length).toBeGreaterThan(1);
    });

    it("an honest empty scan (unknown universe) renders the reason, not an error", async () => {
      const user = await openRecompute();
      await user.type(
        screen.getByLabelText("Symbols (comma or space separated)"),
        "GHOST1, GHOST2"
      );
      await user.click(screen.getByRole("button", { name: "Scan" }));

      expect(
        await screen.findByText(/Insufficient aligned history to scan/)
      ).toBeInTheDocument();
    });
  });
});

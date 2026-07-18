/**
 * Models.test.tsx — the ML registry sub-page renders model cards with honest
 * deployable badges and renders "—" (never a fabricated 0) for null metrics.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Models } from "./Models";
import { api } from "../api/client";
import { __resetThresholdsCache } from "../help/thresholds";

function renderModels() {
  return render(
    <MemoryRouter>
      <Models />
    </MemoryRouter>
  );
}

describe("Models screen (real mock API)", () => {
  beforeEach(() => __resetThresholdsCache());
  afterEach(() => vi.restoreAllMocks());

  it("renders model rows with an honest not-deployable badge", async () => {
    renderModels();
    expect(await screen.findByText("lgbm_ranker")).toBeInTheDocument();
    // The mock models all fail a gate → not deployable, shown honestly.
    expect(screen.getAllByText("▲ Not deployable").length).toBeGreaterThan(0);
  });

  it("a null DSR/PBO renders '—', never a fabricated value", async () => {
    renderModels();
    // meta_labeler rows carry cpcv_dsr:null / pbo:null → the DSR/PBO badges
    // render an em-dash (e.g. "DSR —"), never a fabricated 0.
    await screen.findByText("meta_labeler_timeseries_momentum");
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });

  it("an empty registry renders the honest empty state", async () => {
    vi.spyOn(api, "getModels").mockResolvedValueOnce([]);
    renderModels();
    expect(
      await screen.findByText("No model registry available yet.")
    ).toBeInTheDocument();
  });

  it("renders live CPCV-DSR/PBO thresholds in the footer and drives the badge color, never a hard-coded literal", async () => {
    // lgbm_ranker's fixture pbo is 0.267 -- above this deliberately low
    // pbo_max, so a LIVE threshold flips its PBO badge to "not good" where
    // the old hard-coded 0.50 literal would have shown it passing.
    vi.spyOn(api, "getThresholds").mockResolvedValue({
      pbo_max: 0.2,
      dsr_min: 0.95,
      net_sharpe_min: 0.5,
      max_drawdown_max: 0.3,
      stress_max_drawdown: 0.5,
      kelly_fraction: 0.5,
      kelly_cap: 0.2,
    });
    renderModels();
    expect(
      await screen.findByText(/Deployable = CPCV-DSR > 0\.95 AND PBO < 0\.20\./)
    ).toBeInTheDocument();
    const pboBadge = await screen.findByText(/PBO 0\.27/);
    expect(pboBadge).toHaveClass("badge-warn");
  });

  it("footer and badge coloring degrade honestly (never a fabricated gate) when the threshold fetch fails", async () => {
    vi.spyOn(api, "getThresholds").mockRejectedValue(new Error("offline"));
    renderModels();
    expect(
      await screen.findByText(/Deployable = CPCV-DSR > — AND PBO < —\./)
    ).toBeInTheDocument();
    const pboBadge = await screen.findByText(/PBO 0\.27/);
    expect(pboBadge).toHaveClass("badge-neutral");
  });
});

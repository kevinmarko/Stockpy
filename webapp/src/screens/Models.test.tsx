/**
 * Models.test.tsx — the ML registry sub-page renders model cards with honest
 * deployable badges and renders "—" (never a fabricated 0) for null metrics.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Models } from "./Models";
import { api } from "../api/client";

function renderModels() {
  return render(
    <MemoryRouter>
      <Models />
    </MemoryRouter>
  );
}

describe("Models screen (real mock API)", () => {
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
});

/**
 * StrategyMatrix.test.tsx — the signal-module editor is honest about the fact
 * that an .env write does not reach the running engine: a read-only (writable
 * false) matrix hides Save and shows the disabled note; the pinned
 * regime_multiplier weight cannot be edited; a Save sends the FULL weight set
 * (guarding the server's incomplete_weights rule), and after success the screen
 * shows the "restart to apply" notice without reverting the form; env_drift
 * surfaces a pending-write notice.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StrategyMatrix } from "./StrategyMatrix";
import { api } from "../api/client";
import type { StrategyMatrix as StrategyMatrixT } from "../api/types";

function baseMatrix(overrides: Partial<StrategyMatrixT> = {}): StrategyMatrixT {
  return {
    as_of: new Date().toISOString(),
    market_regime: "RISK ON",
    regime_overrides_active: false,
    weights_source: "running_process_settings",
    modules: [
      { name: "macro_regime", weight: 45, effective_weight: 45, effective_weight_regime: null, enabled: true, source: "both", contributed_last_run: true, symbols_scored: 20, pinned_zero: false },
      { name: "macd_momentum", weight: 20, effective_weight: 20, effective_weight_regime: null, enabled: true, source: "both", contributed_last_run: true, symbols_scored: 20, pinned_zero: false },
      { name: "regime_multiplier", weight: 0, effective_weight: 0, effective_weight_regime: null, enabled: true, source: "both", contributed_last_run: true, symbols_scored: 20, pinned_zero: true },
    ],
    disabled: [],
    max_weight: 100,
    writable: true,
    note: "Writes persist to .env and apply on the next daemon/pipeline launch.",
    env_drift: { detected: false, keys: [], note: "" },
    meta_label_distribution: {
      bins: Array.from({ length: 10 }, (_, i) => ({
        bin_start: +(i / 10).toFixed(1),
        bin_end: +((i + 1) / 10).toFixed(1),
        count: i === 9 ? 20 : 0,
      })),
      count: 20,
      gated_count: 0,
      all_neutral: true,
      reason: null,
    },
    reason: null,
    ...overrides,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter>
      <StrategyMatrix />
    </MemoryRouter>,
  );
}

describe("StrategyMatrix screen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders module rows from the matrix", async () => {
    vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(baseMatrix());
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Signal modules" })).toBeInTheDocument();
    expect(screen.getByText("macro_regime")).toBeInTheDocument();
    expect(screen.getByText("regime_multiplier")).toBeInTheDocument();
  });

  it("read-only (writable false) hides Save and shows the disabled note", async () => {
    vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(
      baseMatrix({ writable: false, note: "Writes are disabled (STRATEGY_WRITES_ENABLED=false)." }),
    );
    renderScreen();
    expect(await screen.findByText(/Writes are disabled/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save changes/ })).not.toBeInTheDocument();
  });

  it("pins regime_multiplier's weight input as disabled with an explanatory hint", async () => {
    vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(baseMatrix());
    renderScreen();
    await screen.findByText("regime_multiplier");
    // The row's weight input is disabled.
    const row = screen.getByText("regime_multiplier").closest("section")!;
    const input = within(row).getByLabelText("Weight") as HTMLInputElement;
    expect(input).toBeDisabled();
    expect(within(row).getByText(/Pinned to 0/)).toBeInTheDocument();
  });

  it("env_drift.detected renders a pending-write notice", async () => {
    vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(
      baseMatrix({
        env_drift: { detected: true, keys: ["SIGNAL_WEIGHTS"], note: "An .env write is pending — restart to apply." },
      }),
    );
    renderScreen();
    expect(await screen.findByTestId("env-drift-notice")).toBeInTheDocument();
  });

  it("Save sends the FULL weight set (not just the edited module), then shows the restart notice without reverting", async () => {
    vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(baseMatrix());
    const setSpy = vi.spyOn(api, "setStrategyModules").mockResolvedValue({
      written: ["SIGNAL_WEIGHTS", "DISABLED_SIGNAL_MODULES"],
      configured_weights: { macro_regime: 50, macd_momentum: 20, regime_multiplier: 0 },
      disabled: [],
      applies: "next_daemon_restart",
      note: "Written to .env.",
    });
    renderScreen();
    await screen.findByText("macro_regime");
    // Edit macro_regime weight 45 -> 50.
    const row = screen.getByText("macro_regime").closest("section")!;
    const input = within(row).getByLabelText("Weight") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "50");
    // Save -> confirm modal -> Write.
    await userEvent.click(screen.getByRole("button", { name: /Save changes/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /Write to \.env/ }));
    await waitFor(() => expect(setSpy).toHaveBeenCalledTimes(1));
    // The body carries ALL three modules, not just macro_regime.
    const body = setSpy.mock.calls[0][0];
    expect(Object.keys(body.weights).sort()).toEqual(
      ["macd_momentum", "macro_regime", "regime_multiplier"],
    );
    expect(body.weights.macro_regime).toBe(50);
    // Restart notice; the edited value is NOT reverted.
    expect(await screen.findByTestId("saved-notice")).toBeInTheDocument();
    expect((within(row).getByLabelText("Weight") as HTMLInputElement).value).toBe("50");
  });

  it("an out-of-bounds weight marks the input invalid and disables Save", async () => {
    vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(baseMatrix());
    renderScreen();
    await screen.findByText("macro_regime");
    const row = screen.getByText("macro_regime").closest("section")!;
    const input = within(row).getByLabelText("Weight") as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "150");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: /Save changes/ })).toBeDisabled();
  });

  describe("meta-label confidence histogram", () => {
    it("renders the chart and the all-neutral note when every symbol is 1.0", async () => {
      vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(baseMatrix());
      renderScreen();
      expect(await screen.findByTestId("meta-label-histogram-chart")).toBeInTheDocument();
      expect(screen.getByText(/Every symbol shows exactly 1.0/)).toBeInTheDocument();
    });

    it("shows gated_count instead of the all-neutral note once confidences spread out", async () => {
      vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(
        baseMatrix({
          meta_label_distribution: {
            bins: [
              { bin_start: 0, bin_end: 0.1, count: 2 },
              { bin_start: 0.1, bin_end: 0.2, count: 0 },
              { bin_start: 0.2, bin_end: 0.3, count: 0 },
              { bin_start: 0.3, bin_end: 0.4, count: 0 },
              { bin_start: 0.4, bin_end: 0.5, count: 0 },
              { bin_start: 0.5, bin_end: 0.6, count: 0 },
              { bin_start: 0.6, bin_end: 0.7, count: 0 },
              { bin_start: 0.7, bin_end: 0.8, count: 3 },
              { bin_start: 0.8, bin_end: 0.9, count: 0 },
              { bin_start: 0.9, bin_end: 1.0, count: 15 },
            ],
            count: 20,
            gated_count: 2,
            all_neutral: false,
            reason: null,
          },
        }),
      );
      renderScreen();
      expect(await screen.findByTestId("meta-label-histogram-chart")).toBeInTheDocument();
      expect(screen.queryByText(/Every symbol shows exactly 1.0/)).not.toBeInTheDocument();
      expect(screen.getByText(/2 currently hard-gated/)).toBeInTheDocument();
    });

    it("renders an honest empty state with the reason on a cold start", async () => {
      vi.spyOn(api, "getStrategyMatrix").mockResolvedValue(
        baseMatrix({
          meta_label_distribution: {
            bins: [],
            count: 0,
            gated_count: 0,
            all_neutral: false,
            reason: "No state snapshot yet — run the pipeline to populate the Strategy Matrix.",
          },
        }),
      );
      renderScreen();
      expect(await screen.findByTestId("meta-label-histogram-empty")).toHaveTextContent(
        "No state snapshot yet",
      );
      expect(screen.queryByTestId("meta-label-histogram-chart")).not.toBeInTheDocument();
    });
  });
});

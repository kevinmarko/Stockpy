/**
 * OptionsDirectiveSummary.test.tsx
 *
 * Covers the happy-path render, loading state, error state, and the real
 * empty/cold-start state (never a fabricated row or 0 in place of a directive
 * that was never generated -- CONSTRAINT #4). `api` is already the mock
 * (VITE_USE_MOCK default-true) -- we never vi.mock the module; we spy on
 * individual api methods only for the fixtures each test needs, mirroring
 * SentimentMiniChart.test.tsx / OptionsMatrix.test.tsx's convention.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OptionsDirectiveSummary } from "./OptionsDirectiveSummary";
import { api, ApiError } from "../api/client";
import type { OptionsMatrix } from "../api/types";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("OptionsDirectiveSummary", () => {
  it("renders a real loading state before data arrives", async () => {
    let resolveFn: (v: OptionsMatrix) => void = () => {};
    vi.spyOn(api, "getOptions").mockImplementation(
      () => new Promise((res) => { resolveFn = res; })
    );

    const { container } = render(<OptionsDirectiveSummary />);

    expect(container.querySelector(".skeleton")).not.toBeNull();
    expect(screen.queryByTestId("optionsDirective-widget")).not.toBeInTheDocument();

    resolveFn({
      as_of: new Date().toISOString(),
      target_dte: 30,
      vix: 15.2,
      market_regime: "RISK ON",
      reason: null,
      directives: [
        {
          Symbol: "AAPL",
          Strategy: "Put Credit Spread",
          Action: "Sell to Open",
          Net_Premium: 1.24,
          IVR_Proxy: 58.4,
          True_IVR: null,
          Integrity_OK: true,
          Integrity_Issues: [],
          Legs: [],
        },
      ],
    });
    expect(await screen.findByTestId("optionsDirective-widget")).toBeInTheDocument();
  });

  it("renders the header row and a table row per directive on the happy path", async () => {
    vi.spyOn(api, "getOptions").mockResolvedValue({
      as_of: "2026-08-10T14:00:00Z",
      target_dte: 30,
      vix: 15.2,
      market_regime: "RISK ON",
      reason: null,
      directives: [
        {
          Symbol: "AAPL",
          Strategy: "Put Credit Spread",
          Action: "Sell to Open",
          Net_Premium: 1.24,
          IVR_Proxy: 58.4,
          True_IVR: 72.3,
          Integrity_OK: true,
          Integrity_Issues: [],
          Legs: [],
        },
        {
          // No real chain data this cycle -> must fall back to IVR_Proxy and
          // be labeled "proxy", not "chain"; also Integrity_OK: false ->
          // "Flagged", never silently rendered as OK.
          Symbol: "KO",
          Strategy: "Put Credit Spread",
          Action: "Sell to Open",
          Net_Premium: 0.42,
          IVR_Proxy: 61.2,
          True_IVR: null,
          Integrity_OK: false,
          Integrity_Issues: ["Short leg strike 59.37 is not on the $0.50 grid"],
          Legs: [],
        },
      ],
    } satisfies OptionsMatrix);

    render(<OptionsDirectiveSummary />);

    const widget = await screen.findByTestId("optionsDirective-widget");
    expect(widget).toBeInTheDocument();
    expect(screen.getByText("RISK ON")).toBeInTheDocument();
    expect(screen.getByText("15.2")).toBeInTheDocument();

    expect(screen.getByText("AAPL")).toBeInTheDocument();
    expect(screen.getByText("72 (chain)")).toBeInTheDocument();

    expect(screen.getByText("KO")).toBeInTheDocument();
    expect(screen.getByText("61 (proxy)")).toBeInTheDocument();
    expect(screen.getByText("Flagged")).toBeInTheDocument();
    expect(screen.getByText("OK")).toBeInTheDocument();
  });

  it("shows a real error state with Retry on a hard error", async () => {
    const spy = vi
      .spyOn(api, "getOptions")
      .mockRejectedValueOnce(new ApiError("boom", 500));

    render(<OptionsDirectiveSummary />);

    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
    expect(screen.getByText("boom")).toBeInTheDocument();
    expect(screen.queryByTestId("optionsDirective-widget")).not.toBeInTheDocument();

    spy.mockResolvedValueOnce({
      as_of: null,
      target_dte: null,
      vix: null,
      market_regime: null,
      reason: null,
      directives: [
        {
          Symbol: "AAPL",
          Strategy: "Cash",
          Action: "Wait",
          Net_Premium: 0.0,
          IVR_Proxy: 33.5,
          True_IVR: null,
          Integrity_OK: true,
          Integrity_Issues: [],
          Legs: [],
        },
      ],
    });
    screen.getByRole("button", { name: "Retry" }).click();
    expect(await screen.findByTestId("optionsDirective-widget")).toBeInTheDocument();
  });

  it("renders the honest gating reason (never an empty table with no explanation) when no directives were generated", async () => {
    vi.spyOn(api, "getOptions").mockResolvedValue({
      as_of: null,
      target_dte: null,
      vix: null,
      market_regime: null,
      directives: [],
      reason: "Gated by VRP/IVR/VIX regime rules: true_ivr <= 50 for every candidate symbol.",
    } satisfies OptionsMatrix);

    render(<OptionsDirectiveSummary />);

    expect(
      await screen.findByText("No options directives generated yet")
    ).toBeInTheDocument();
    expect(
      screen.getByText("Gated by VRP/IVR/VIX regime rules: true_ivr <= 50 for every candidate symbol.")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("optionsDirective-widget")).not.toBeInTheDocument();
  });

  it("renders a generic honest fallback hint when directives is empty and reason is null", async () => {
    vi.spyOn(api, "getOptions").mockResolvedValue({
      as_of: null,
      target_dte: null,
      vix: null,
      market_regime: null,
      directives: [],
      reason: null,
    } satisfies OptionsMatrix);

    render(<OptionsDirectiveSummary />);

    expect(
      await screen.findByText("No options directives generated yet")
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "The options matrix populates once the pipeline runs with premium selling enabled."
      )
    ).toBeInTheDocument();
  });
});

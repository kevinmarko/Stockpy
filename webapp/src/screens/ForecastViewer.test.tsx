/**
 * ForecastViewer.test.tsx — the Forecasts screen (forecasting rebuild F6b).
 *
 * Runs against the real mock API (`mockForecastsView` / `mockForecastSummary`),
 * whose fixtures carry one symbol per honesty branch: AAPL populated, MSFT
 * gate-published-naive, JNJ fallback + no band + pre-F3 ledger + no advisory
 * recommendation, AGNC unrecorded horizons + Case A + ledger read error, any
 * other symbol a ledger cold start, ZZZZ an unavailable ledger. Every null
 * must render as "—" / "unavailable" / "not recorded" with its reason, never 0.
 */
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForecastViewer } from "./ForecastViewer";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { mockForecastsView } from "../api/mock";

function renderScreen(path = "/forecast") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ForecastViewer />
    </MemoryRouter>
  );
}

async function panel() {
  // `?symbol=` mounts the panel at once; wait for /forecasts to resolve too.
  await screen.findByRole("table", { name: "Published forecasts" });
  return within(await screen.findByTestId("forecast-symbol-panel"));
}

describe("ForecastViewer — Forecasts screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("reads the persisted routes only, never the old refit endpoint", async () => {
    const spy = vi.spyOn(api, "getForecasts");
    renderScreen();
    await screen.findByRole("table", { name: "Published forecasts" });
    expect(spy).toHaveBeenCalled();
    expect("getForecastResult" in api).toBe(false);
  });

  it("renders the universe table, one row per snapshot symbol", async () => {
    renderScreen();
    const table = await screen.findByRole("table", { name: "Published forecasts" });
    for (const sym of ["AAPL", "AGNC", "JNJ", "MSFT"]) {
      expect(within(table).getByRole("button", { name: sym })).toBeInTheDocument();
    }
  });

  it("gate line and z line come from the response, not literals", async () => {
    const base = mockForecastsView();
    vi.spyOn(api, "getForecasts").mockResolvedValue({
      ...base,
      gate: { enabled: true, min_improvement: 0.01, min_obs: 40, window_days: 365 },
    });
    renderScreen();
    const line = await screen.findByTestId("forecast-gate-line");
    expect(line.textContent).toContain("Naive gate: on");
    expect(line.textContent).toContain("1.0%");
    expect(line.textContent).toContain("40 scored pairs");
    expect(line.textContent).toContain("365 days");
    expect(screen.getByText(/forecasting rebuild F5 is not built/, { selector: "div" })).toBeInTheDocument();
  });

  it("defaults the symbol panel to the first row and shows what the forecast changed", async () => {
    renderScreen();
    const p = await panel();
    expect(p.getByRole("heading", { name: "AAPL" })).toBeInTheDocument();
    const impact = within(await screen.findByTestId("forecast-impact"));
    expect(impact.getByText(/\+6\.0/)).toBeInTheDocument();
    expect(impact.getByText(/without the forecast: HOLD/)).toBeInTheDocument();
    expect(impact.getByText(/bullish forecast raised the BUY's conviction/)).toBeInTheDocument();
    expect(impact.getByText(/came from the 30-day forecast/)).toBeInTheDocument();
    // z is unavailable with the backend's reason, never a number.
    expect(screen.getByTestId("forecast-z-reason").textContent).toContain("F5 is not built");
  });

  it("AAPL ledger: admitted models and rejected models with their reasons", async () => {
    renderScreen("/forecast?symbol=AAPL");
    const gate = within(await screen.findByTestId("forecast-gate-block"));
    expect(gate.getByText(/admitted arima, holt_winters/)).toBeInTheDocument();
    const rejected = within(gate.getByRole("list", { name: "Rejected models" }));
    expect(rejected.getByText("cnn_lstm").parentElement?.textContent).toContain("too few scored pairs");
    expect(rejected.getByText("monte_carlo").parentElement?.textContent).toContain("does not beat naive");
    expect(await screen.findByRole("table", { name: "Skill vs naive" })).toBeInTheDocument();
    expect(screen.getByTestId("forecast-mc-coverage").textContent).toContain("nominal 90%");
  });

  it("AAPL 90d ledger horizon: empty skill and coverage carry their reasons", async () => {
    renderScreen("/forecast?symbol=AAPL");
    const ledger = within(await screen.findByTestId("forecast-ledger"));
    await ledger.findByTestId("forecast-gate-block");
    fireEvent.click(ledger.getByRole("tab", { name: "90d" }));
    expect(await ledger.findByText(/No skill rows: no completed forecasts in window/)).toBeInTheDocument();
    expect(ledger.getByTestId("forecast-mc-coverage").textContent).toContain("unavailable (insufficient_history");
  });

  it("MSFT: the gate published naive — naive badges and a naive gate evaluation", async () => {
    renderScreen("/forecast?symbol=MSFT");
    const p = await panel();
    expect(within(p.getByTestId("forecast-horizon-30")).getByText("naive")).toBeInTheDocument();
    expect(within(p.getByTestId("forecast-horizon-60")).getByText("naive")).toBeInTheDocument();
    // A gated-naive horizon is also flagged Is_Fallback by the engine; only the
    // accurate "naive" badge shows, not "models did not converge".
    expect(within(p.getByTestId("forecast-horizon-30")).queryByText("fallback")).toBeNull();
    expect(within(p.getByTestId("forecast-horizon-10")).queryByText("naive")).toBeNull();
    expect(await screen.findByText(/admits no model \(would fall back to naive\)/)).toBeInTheDocument();
    expect(screen.getByText("None fired.")).toBeInTheDocument();
    expect(screen.getByText(/came from the ATR leg/)).toBeInTheDocument();
  });

  it("JNJ: fallback with no band, unrecorded points, no advisory recommendation, pre-F3 ledger", async () => {
    renderScreen("/forecast?symbol=JNJ");
    const p = await panel();
    const h90 = within(p.getByTestId("forecast-horizon-90"));
    expect(h90.getByText("fallback")).toBeInTheDocument();
    expect(h90.getByText(/no band/)).toBeInTheDocument();
    const impact = within(screen.getByTestId("forecast-impact"));
    expect(impact.getByText(/not recorded/)).toBeInTheDocument();
    expect(impact.queryByText(/^\+?0\.0$/)).toBeNull();
    expect(impact.getByText(/No advisory recommendation recorded/)).toBeInTheDocument();
    expect(impact.getByText(/Not derivable: no active sell zone/)).toBeInTheDocument();
    expect(await screen.findByText(/none recorded \(ledger predates F3/)).toBeInTheDocument();
  });

  it("AGNC: unrecorded horizons render '—' / 'not recorded', Case A label, ledger read error", async () => {
    renderScreen("/forecast?symbol=AGNC");
    const p = await panel();
    const h60 = within(p.getByTestId("forecast-horizon-60"));
    expect(h60.getByText("—")).toBeInTheDocument();
    expect(h60.getByText("not recorded")).toBeInTheDocument();
    expect(screen.getByText(/Case A: a loss plus a bearish 30-day forecast/)).toBeInTheDocument();
    expect(await screen.findByText("No skill rows: error.")).toBeInTheDocument();
    expect(screen.getByText(/Evaluation now:/).parentElement?.textContent).toContain("unavailable (error)");
  });

  it("a symbol not in the snapshot: honest notice, no refit, ledger cold start", async () => {
    renderScreen("/forecast?symbol=tsla");
    const p = await panel();
    expect(p.getByRole("heading", { name: "TSLA" })).toBeInTheDocument();
    expect(p.getByText(/not in the latest pipeline snapshot/)).toBeInTheDocument();
    expect(p.queryByTestId("forecast-horizon-30")).toBeNull();
    expect(await screen.findByText("No matured forecast history for this symbol yet.")).toBeInTheDocument();
  });

  it("ZZZZ: the ledger reader is unavailable — reason shown, no table", async () => {
    renderScreen("/forecast?symbol=ZZZZ");
    expect((await screen.findAllByText("forecast ledger unavailable")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("table", { name: "Skill vs naive" })).toBeNull();
  });

  it("empty snapshot: 'No published forecasts' with the backend reason, and no symbol panel", async () => {
    const base = mockForecastsView();
    vi.spyOn(api, "getForecasts").mockResolvedValue({
      ...base,
      as_of: null,
      rows: [],
      reason: "No state snapshot yet — run the pipeline first.",
    });
    renderScreen();
    expect(await screen.findByText("No published forecasts")).toBeInTheDocument();
    expect(screen.getByText("No state snapshot yet — run the pipeline first.")).toBeInTheDocument();
    expect(screen.getByText("Pick a symbol")).toBeInTheDocument();
    expect(screen.queryByTestId("forecast-symbol-panel")).toBeNull();
    // With no rows there is no z_reason to quote; say so instead of inventing one.
    expect(screen.getByText(/not reported by the backend/)).toBeInTheDocument();
  });

  it("a hard error on /forecasts renders the error state with retry", async () => {
    vi.spyOn(api, "getForecasts").mockRejectedValueOnce(new ApiError("boom", 500));
    renderScreen();
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });

  it("clicking a universe row switches the symbol panel", async () => {
    renderScreen();
    const table = await screen.findByRole("table", { name: "Published forecasts" });
    fireEvent.click(within(table).getByRole("button", { name: "MSFT" }));
    expect((await panel()).getByRole("heading", { name: "MSFT" })).toBeInTheDocument();
  });

  it("changing the range toggle re-fetches bars with the new lookback", async () => {
    const spy = vi.spyOn(api, "getDataBars");
    renderScreen("/forecast?symbol=AAPL");
    await panel();
    await waitFor(() => expect(spy).toHaveBeenCalledWith("AAPL", 63));
    fireEvent.click(screen.getByRole("tab", { name: "1M" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("AAPL", 21));
  });

  it("bars-empty fallback: the forecast projection still renders with an honest note", async () => {
    vi.spyOn(api, "getDataBars").mockResolvedValue([]);
    renderScreen("/forecast?symbol=AAPL");
    expect(await screen.findByText(/No price history in the store for this symbol/)).toBeInTheDocument();
  });
});

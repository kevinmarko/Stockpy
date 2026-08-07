import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { test, expect, vi } from "vitest";
import { CacheLongShort } from "./CacheLongShort";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getClsDashboard: vi.fn(),
    getClsPendingApprovals: vi.fn(),
    getClsConcentratedPositions: vi.fn(),
    simulateCls: vi.fn(),
    startCls: vi.fn(),
    approveClsBulk: vi.fn(),
    // TabGuide (rendered by every screen) lazily fetches live thresholds --
    // resolving null exercises its documented non-fatal-failure path
    // rather than needing a full Thresholds fixture here.
    getThresholds: vi.fn().mockResolvedValue(null),
    getUniverse: vi.fn().mockResolvedValue({ symbols: [] }),
  },
  apiMeta: { useMock: false },
}));

function mockDashboardEnabled() {
  vi.mocked(api.getClsDashboard).mockResolvedValue({
    status: "enabled",
    tax_bank: 500,
    exposure: { long_exposure: 1000, short_exposure: 500, net_exposure: 500, gross_exposure: 1500 },
  });
}

test("renders Cache Long/Short screen and loads dashboard", async () => {
  mockDashboardEnabled();
  vi.mocked(api.getClsPendingApprovals).mockResolvedValue([]);
  vi.mocked(api.getClsConcentratedPositions).mockResolvedValue({ positions: [] });

  render(<CacheLongShort />);

  await waitFor(() => {
    expect(screen.getByText("Cache Long/Short")).toBeInTheDocument();
  });

  await waitFor(() => {
    expect(screen.getByText("Tax Bank")).toBeInTheDocument();
    expect(screen.getByText("+$500.00")).toBeInTheDocument();
  });
});

test("dashboard renders an honest disabled notice, not a zeroed-out chart", async () => {
  vi.mocked(api.getClsDashboard).mockResolvedValue({ status: "disabled" });

  render(<CacheLongShort />);

  await waitFor(() => {
    expect(screen.getByText(/currently disabled/i)).toBeInTheDocument();
  });
  expect(screen.queryByText("Tax Bank")).not.toBeInTheDocument();
});

test("approvals tab renders an honest empty state when nothing is pending", async () => {
  mockDashboardEnabled();
  vi.mocked(api.getClsPendingApprovals).mockResolvedValue([]);

  render(<CacheLongShort />);
  fireEvent.click(await screen.findByText("Approvals"));

  await waitFor(() => {
    expect(screen.getByText("No pending trades")).toBeInTheDocument();
  });
});

test("approvals tab renders flagged lots and allows bulk approval", async () => {
  mockDashboardEnabled();
  vi.mocked(api.getClsPendingApprovals).mockResolvedValue([
    { lot_id: 101, position_id: 1, cost_basis: 150.5, unrealized_loss_pct: -0.12 },
  ]);
  vi.mocked(api.approveClsBulk).mockResolvedValue({ status: "approved", count: 1 });

  render(<CacheLongShort />);
  fireEvent.click(await screen.findByText("Approvals"));

  const checkbox = await screen.findByLabelText("Select lot 101");
  fireEvent.click(checkbox);
  fireEvent.click(screen.getByText("Approve Selected (1)"));

  await waitFor(() => {
    expect(api.approveClsBulk).toHaveBeenCalledWith([101]);
  });
});

test("configurator renders an honest 'not found' reason instead of fabricated numbers", async () => {
  mockDashboardEnabled();
  vi.mocked(api.simulateCls).mockResolvedValue({
    found: false,
    reason: "Insufficient price history for ticker or suitable proxy",
    beta: null,
    proxy_ticker: null,
    correlation_coefficient: null,
  });

  render(<CacheLongShort />);
  fireEvent.click(await screen.findByText("Configurator"));
  fireEvent.click(screen.getByText("Simulate Strategy"));

  await waitFor(() => {
    expect(screen.getByText("Insufficient price history for ticker or suitable proxy")).toBeInTheDocument();
  });
  // The "Confirm & Start Strategy" button must stay disabled -- never
  // start a strategy against a simulation that found nothing.
  expect(screen.getByText(/Confirm & Start Strategy/i).closest("button")).toBeDisabled();
});

test("configurator allows starting a strategy after a successful simulation", async () => {
  mockDashboardEnabled();
  vi.mocked(api.simulateCls).mockResolvedValue({
    found: true,
    reason: null,
    beta: 1.2,
    proxy_ticker: "XLK",
    correlation_coefficient: 0.85,
  });
  vi.mocked(api.startCls).mockResolvedValue({ status: "started", position_id: 42, ticker: "AAPL" });

  render(<CacheLongShort />);
  fireEvent.click(await screen.findByText("Configurator"));
  fireEvent.click(screen.getByText("Simulate Strategy"));

  const startButton = await screen.findByText(/Confirm & Start Strategy/i);
  expect(startButton.closest("button")).not.toBeDisabled();
  fireEvent.click(startButton);

  await waitFor(() => {
    expect(api.startCls).toHaveBeenCalledWith({
      ticker: "AAPL",
      proxy_ticker: "XLK",
      allocation: 10000,
      correlation_coefficient: 0.85,
    });
  });
  expect(await screen.findByText(/Strategy started/i)).toBeInTheDocument();
});

test("configurator simulates the freshly typed ticker without requiring Enter first", async () => {
  // Regression test: the Concentrated Ticker SymbolInput hides its submit
  // button (hideButton), so a user's only visible affordance is typing then
  // clicking "Simulate Strategy" directly. Before this fix, ConfiguratorWizard
  // only wired SymbolInput's onSubmit (fires on Enter/suggestion-accept only)
  // and not onChange, so clicking Simulate without pressing Enter first
  // silently simulated the STALE default ticker ("AAPL") instead of whatever
  // was actually typed.
  mockDashboardEnabled();
  vi.mocked(api.simulateCls).mockResolvedValue({
    found: true,
    reason: null,
    beta: 0.9,
    proxy_ticker: "XLE",
    correlation_coefficient: 0.7,
  });

  render(<CacheLongShort />);
  fireEvent.click(await screen.findByText("Configurator"));

  const tickerInput = screen.getByLabelText("Concentrated Ticker");
  fireEvent.change(tickerInput, { target: { value: "tsla" } });
  // Deliberately no Enter/blur -- click straight through, matching how an
  // operator actually interacts with a button-less field.
  fireEvent.click(screen.getByText("Simulate Strategy"));

  await waitFor(() => {
    expect(api.simulateCls).toHaveBeenCalledWith({ ticker: "TSLA", allocation: 10000 });
  });
});

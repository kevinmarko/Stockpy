import { render, screen, waitFor } from "@testing-library/react";
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
  },
  apiMeta: { useMock: false },
}));

test("renders Cache Long/Short screen and loads dashboard", async () => {
  vi.mocked(api.getClsDashboard).mockResolvedValue({
    status: "enabled",
    tax_bank: 500,
    exposure: { long_exposure: 1000, short_exposure: 500, net_exposure: 500, gross_exposure: 1500 },
  });
  vi.mocked(api.getClsPendingApprovals).mockResolvedValue([]);
  vi.mocked(api.getClsConcentratedPositions).mockResolvedValue({ positions: [] });

  render(
    <CacheLongShort />
  );

  await waitFor(() => {
    expect(screen.getByText("Cache Long/Short")).toBeInTheDocument();
  });
  
  await waitFor(() => {
    expect(screen.getByText("Tax Bank")).toBeInTheDocument();
    expect(screen.getByText("+$500.00")).toBeInTheDocument();
  });
});

/**
 * ScanConfigCard.test.tsx — Pilots Manager's scanner-profile card. Renders
 * against the REAL mock API (no vi.mock). The mock seeds one enabled scan
 * config ("high_momentum_breakout") via localStorage-backed
 * readScanConfigs()/writeScanConfigs() -- covers the real listing plus a
 * genuine enable/disable round trip through api.putScanConfig() (not the
 * original stub's local, server-disconnected useState toggle).
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ScanConfigCard } from "./ScanConfigCard";
import { api } from "../../api/client";
import type { AgenticDiscovery } from "../../api/types";

describe("ScanConfigCard (real mock API)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("lists the real seeded scan config with its real enabled state", async () => {
    render(<ScanConfigCard />);

    const row = await screen.findByTestId("scan-config-row");
    expect(row.textContent).toContain("high_momentum_breakout");
    expect(row.textContent).toContain("enabled");
  });

  it("toggling Disable calls the real putScanConfig endpoint and flips the row to disabled", async () => {
    const putSpy = vi.spyOn(api, "putScanConfig");
    render(<ScanConfigCard />);

    await screen.findByTestId("scan-config-row");
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));

    await waitFor(() => expect(putSpy).toHaveBeenCalledWith({
      name: "high_momentum_breakout",
      filters: { min_price: 5, min_volume: 1_000_000, rsi_min: 50, rsi_max: 70 },
      enabled: false,
    }));

    // The reload this triggers briefly shows the Loading skeleton (which
    // unmounts the row entirely), so re-query fresh rather than reuse the
    // now-detached node from before the toggle.
    expect(await screen.findByRole("button", { name: "Enable" })).toBeInTheDocument();
    const row = await screen.findByTestId("scan-config-row");
    expect(row.textContent).toContain("disabled");
  });

  it("renders 'read-only' with no toggle button when the endpoint is not writable", async () => {
    vi.spyOn(api, "getAgenticDiscovery").mockResolvedValueOnce({
      generated_at: null,
      candidates: [],
      scan_configs: [
        {
          name: "sample",
          filters: {},
          enabled: true,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ],
      reason: null,
      writable: false,
      note: "AGENTIC_DISCOVERY_ENABLED is false.",
    } satisfies AgenticDiscovery);

    render(<ScanConfigCard />);

    await screen.findByTestId("scan-config-row");
    expect(screen.getByText("read-only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /enable|disable/i })).not.toBeInTheDocument();
  });

  it("renders an honest empty state when no scan configs exist", async () => {
    vi.spyOn(api, "getAgenticDiscovery").mockResolvedValueOnce({
      generated_at: null,
      candidates: [],
      scan_configs: [],
      reason: "No scan configs yet.",
      writable: true,
      note: "",
    } satisfies AgenticDiscovery);

    render(<ScanConfigCard />);

    expect(await screen.findByText("No scan configs yet")).toBeInTheDocument();
    expect(screen.queryByTestId("scan-config-row")).not.toBeInTheDocument();
  });
});

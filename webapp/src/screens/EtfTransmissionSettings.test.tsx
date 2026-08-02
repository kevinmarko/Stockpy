import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EtfTransmissionSettings } from "./EtfTransmissionSettings";
import { api } from "../api/client";
import type { TunablesResponse } from "../api/types";

function baseEtfTransmissionTunables(): TunablesResponse {
  return {
    applies: "next_daemon_restart",
    groups: [
      {
        name: "Holdings Ingestion",
        fields: [
          {
            key: "ETF_HOLDINGS_ENABLED",
            value: false,
            type: "boolean",
            default: false,
            description: "EDGAR N-PORT switch.",
          },
        ],
      },
      {
        name: "Measurement & Residualization",
        fields: [
          {
            key: "ETF_TRANSMISSION_ENABLED",
            value: false,
            type: "boolean",
            default: false,
            description: "Transmission measurement switch.",
          },
        ],
      },
    ],
    env_drift: { detected: false, keys: [], note: "" },
  };
}

function renderScreen() {
  return render(
    <MemoryRouter>
      <EtfTransmissionSettings />
    </MemoryRouter>
  );
}

describe("EtfTransmissionSettings screen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders screen title and groups fetched from API", async () => {
    vi.spyOn(api, "getEtfTransmissionSettings").mockResolvedValue(baseEtfTransmissionTunables());
    renderScreen();
    expect(await screen.findByRole("heading", { name: "ETF Volatility Transmission" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Holdings Ingestion" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Measurement & Residualization" })).toBeInTheDocument();
  });

  it("calls updateEtfTransmissionSettings when Save is clicked with modified fields", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getEtfTransmissionSettings").mockResolvedValue(baseEtfTransmissionTunables());
    const updateSpy = vi.spyOn(api, "updateEtfTransmissionSettings").mockResolvedValueOnce({
      written: { ETF_TRANSMISSION_ENABLED: true },
      rejected: {},
      applies: "next_daemon_restart",
      note: "Accepted values written to .env.",
    });

    renderScreen();
    await screen.findByRole("heading", { name: "ETF Volatility Transmission" });

    const toggle = screen.getByLabelText("ETF_TRANSMISSION_ENABLED");
    await user.click(toggle);

    const saveBtn = screen.getByRole("button", { name: /Save 1 change/i });
    expect(saveBtn).not.toBeDisabled();
    await user.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ ETF_TRANSMISSION_ENABLED: true });
    });
  });
});

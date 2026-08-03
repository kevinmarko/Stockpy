import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EtfTransmissionSettings } from "./EtfTransmissionSettings";
import { api, ApiError } from "../api/client";
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
            key: "Etf Transmission Enabled",
            value: false,
            type: "boolean",
            default: false,
            description: "Transmission measurement switch.",
          },
          {
            key: "Etf Transmission Wrappers",
            value: '["SPY","QQQ"]',
            type: "string",
            default: '["SPY","QQQ"]',
            description: "JSON array of candidate wrapper ETFs.",
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
      written: { "Etf Transmission Enabled": true },
      rejected: {},
      applies: "next_daemon_restart",
      note: "Accepted values written to .env.",
    });

    renderScreen();
    await screen.findByRole("heading", { name: "ETF Volatility Transmission" });

    const toggle = screen.getByLabelText("Etf Transmission Enabled");
    await user.click(toggle);

    const saveBtn = screen.getByRole("button", { name: /Save 1 change/i });
    expect(saveBtn).not.toBeDisabled();
    await user.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ "Etf Transmission Enabled": true });
    });
  });

  it("shows the honest cold-start state when GET 404s", async () => {
    vi.spyOn(api, "getEtfTransmissionSettings").mockRejectedValue(new ApiError("not found", 404));
    renderScreen();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });

  it("Save calls updateEtfTransmissionSettings, not the sentiment or general tunables endpoint", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getEtfTransmissionSettings").mockResolvedValue(baseEtfTransmissionTunables());
    const etfSpy = vi.spyOn(api, "updateEtfTransmissionSettings").mockResolvedValue({
      written: { "Etf Transmission Enabled": true },
      rejected: {},
      applies: "next_daemon_restart",
    });
    const sentimentSpy = vi.spyOn(api, "updateSentimentSettings");
    const generalSpy = vi.spyOn(api, "updateTunables");

    renderScreen();
    const toggle = await screen.findByLabelText("Etf Transmission Enabled");
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(etfSpy).toHaveBeenCalledTimes(1));
    expect(etfSpy.mock.calls[0][0]).toEqual({ "Etf Transmission Enabled": true });
    expect(sentimentSpy).not.toHaveBeenCalled();
    expect(generalSpy).not.toHaveBeenCalled();
  });

  it("renders a JSON-array field (Etf Transmission Wrappers) as an editable textarea and saves the edited JSON string as-is", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getEtfTransmissionSettings").mockResolvedValue(baseEtfTransmissionTunables());
    const etfSpy = vi.spyOn(api, "updateEtfTransmissionSettings").mockResolvedValue({
      written: { "Etf Transmission Wrappers": '["SPY","QQQ","IWM"]' },
      rejected: {},
      applies: "next_daemon_restart",
    });

    renderScreen();
    const textarea = (await screen.findByLabelText("Etf Transmission Wrappers")) as HTMLTextAreaElement;
    expect(textarea.value).toBe('["SPY","QQQ"]');

    fireEvent.change(textarea, { target: { value: '["SPY","QQQ","IWM"]' } });
    await user.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(etfSpy).toHaveBeenCalledTimes(1));
    expect(etfSpy.mock.calls[0][0]).toEqual({ "Etf Transmission Wrappers": '["SPY","QQQ","IWM"]' });
  });
});

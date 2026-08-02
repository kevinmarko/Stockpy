import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FmpSettings } from "./FmpSettings";
import { api } from "../api/client";
import type { TunablesResponse } from "../api/types";

function baseFmpTunables(): TunablesResponse {
  return {
    applies: "next_daemon_restart",
    groups: [
      {
        name: "Client & Resiliency",
        fields: [
          {
            key: "FMP_TIMEOUT_SECONDS",
            value: 15,
            type: "number",
            default: 15,
            description: "HTTP timeout.",
          },
        ],
      },
      {
        name: "Primary Feeds",
        fields: [
          {
            key: "FMP_QUOTES_ENABLED",
            value: false,
            type: "boolean",
            default: false,
            description: "Master switch.",
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
      <FmpSettings />
    </MemoryRouter>
  );
}

describe("FmpSettings screen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders screen title and groups fetched from API", async () => {
    vi.spyOn(api, "getFmpSettings").mockResolvedValue(baseFmpTunables());
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Financial Modeling Prep (FMP)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Client & Resiliency" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Primary Feeds" })).toBeInTheDocument();
  });

  it("calls updateFmpSettings when Save is clicked with modified fields", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "getFmpSettings").mockResolvedValue(baseFmpTunables());
    const updateSpy = vi.spyOn(api, "updateFmpSettings").mockResolvedValueOnce({
      written: { FMP_QUOTES_ENABLED: true },
      rejected: {},
      applies: "next_daemon_restart",
      note: "Accepted values written to .env.",
    });

    renderScreen();
    await screen.findByRole("heading", { name: "Financial Modeling Prep (FMP)" });

    const toggle = screen.getByLabelText("FMP_QUOTES_ENABLED");
    await user.click(toggle);

    const saveBtn = screen.getByRole("button", { name: /Save 1 change/i });
    expect(saveBtn).not.toBeDisabled();
    await user.click(saveBtn);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ FMP_QUOTES_ENABLED: true });
    });
  });
});

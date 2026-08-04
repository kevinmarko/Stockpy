/**
 * SettingsManager.test.tsx — the runtime-tunables editor is honest about the
 * fact that an .env write does not reach the running engine (persistent
 * "applies on next restart" notice), never fabricates a value for an absent
 * setting (a null number renders an empty input, not "0"), sends ONLY the
 * changed keys on Save, surfaces per-key `rejected` reasons without swallowing
 * them, and shows an honest empty state when the backend exposes no tunables.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsManager } from "./SettingsManager";
import { api, ApiError } from "../api/client";
import { readCacheEntry, writeCacheEntry } from "../api/offlineCache";
import type { TunablesResponse } from "../api/types";

function baseTunables(overrides: Partial<TunablesResponse> = {}): TunablesResponse {
  return {
    applies: "next_daemon_restart",
    groups: [
      {
        name: "Position Sizing",
        fields: [
          {
            key: "Kelly Fraction", value: 0.5, type: "number",
            min: 0, max: 1, step: 0.05, default: 0.5,
            description: "Fraction of full-Kelly used when sizing.",
          },
          {
            // Honest absent value -> empty input, never a fabricated 0.
            key: "Macro Refresh Hours", value: null, type: "number",
            min: 1, max: 168, step: 1, default: 12,
            description: "Hours before cached macro series re-fetch.",
          },
        ],
      },
      {
        name: "Forecasting",
        fields: [
          {
            key: "FORECAST_USE_GARCH_SIGMA", value: true, type: "boolean",
            default: true, description: "Use GJR-GARCH sigma for the forecast.",
          },
          {
            key: "Fundamentals Source", value: "yahoo", type: "enum",
            options: ["yahoo", "yfinance_info"], default: "yahoo",
            description: "Primary fundamentals provider.",
          },
          {
            key: "Default Tickers", value: "AAPL,MSFT", type: "string",
            default: "", description: "Universe when no watchlist is set.",
          },
        ],
      },
      {
        name: "Advanced / Config",
        fields: [
          {
            // "string" wire type carrying a JSON blob -> renders as a textarea
            // (content-sniffed), not a single-line input.
            key: "Cors Allowed Origins", value: '["http://localhost:5173"]', type: "string",
            default: '["http://localhost:5173"]',
            description: "Allowed browser origins for the CORS policy.",
          },
        ],
      },
    ],
    env_drift: { detected: false, keys: [], note: "" },
    ...overrides,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter>
      <SettingsManager />
    </MemoryRouter>,
  );
}

describe("SettingsManager screen", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("renders groups and a field of every widget type", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Runtime tunables" })).toBeInTheDocument();
    // group headings
    expect(screen.getByRole("heading", { name: "Position Sizing" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Forecasting" })).toBeInTheDocument();
    // number, boolean, enum, string widgets
    expect(screen.getByLabelText("Kelly Fraction")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "FORECAST_USE_GARCH_SIGMA" })).toBeInTheDocument();
    expect(screen.getByLabelText("Fundamentals Source").tagName).toBe("SELECT");
    expect(screen.getByLabelText("Default Tickers")).toBeInTheDocument();
    // persistent "applies on restart" notice
    expect(screen.getByTestId("applies-notice")).toBeInTheDocument();
  });

  it("renders a null number value as an empty input, not 0", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    const input = (await screen.findByLabelText("Macro Refresh Hours")) as HTMLInputElement;
    expect(input.value).toBe("");
    expect(input.value).not.toBe("0");
  });

  it("shows an honest empty state when the backend exposes no tunables", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables({ groups: [] }));
    renderScreen();
    expect(await screen.findByText("No tunables exposed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
  });

  it("shows the honest cold-start state when GET 404s", async () => {
    vi.spyOn(api, "getTunables").mockRejectedValue(new ApiError("not found", 404));
    renderScreen();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });

  it("Save sends ONLY the changed key", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    const spy = vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: { "Kelly Fraction": 0.6 },
      rejected: {},
      applies: "next_daemon_restart",
    });
    renderScreen();
    const input = (await screen.findByLabelText("Kelly Fraction")) as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "0.6");
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][0]).toEqual({ "Kelly Fraction": 0.6 });
  });

  it("surfaces per-key rejected reasons and keeps the key dirty", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: {},
      rejected: { "Kelly Fraction": "out_of_range: must be within [0, 1]." },
      applies: "next_daemon_restart",
    });
    renderScreen();
    const input = (await screen.findByLabelText("Kelly Fraction")) as HTMLInputElement;
    // A value that is valid client-side (in [0,1]) but the server still rejects.
    await userEvent.clear(input);
    await userEvent.type(input, "0.6");
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    expect(await screen.findByTestId("rejected-Kelly Fraction")).toHaveTextContent(/out_of_range/);
    // The key stays dirty -> Save remains enabled to fix and re-submit.
    expect(screen.getByRole("button", { name: /Save/ })).toBeEnabled();
  });

  it("surfaces written keys and resets the dirty baseline for them", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: { "Kelly Fraction": 0.6 },
      rejected: {},
      applies: "next_daemon_restart",
    });
    renderScreen();
    const input = (await screen.findByLabelText("Kelly Fraction")) as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "0.6");
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    expect(await screen.findByTestId("written-notice")).toHaveTextContent("Kelly Fraction");
    // No longer dirty -> Save disabled again.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Save/ })).toBeDisabled(),
    );
  });

  it("marks an out-of-bounds number invalid and disables Save", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    const input = (await screen.findByLabelText("Kelly Fraction")) as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "5"); // > max 1
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: /Save/ })).toBeDisabled();
  });

  it("env_drift.detected renders a pending-write notice with the differing keys", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(
      baseTunables({
        env_drift: {
          detected: true,
          keys: ["Kelly Fraction"],
          note: "An .env write is pending — restart to apply.",
        },
      }),
    );
    renderScreen();
    const notice = await screen.findByTestId("env-drift-notice");
    expect(notice).toHaveTextContent("Kelly Fraction");
  });

  it("no env_drift notice when nothing has drifted", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    await screen.findByRole("heading", { name: "Runtime tunables" });
    expect(screen.queryByTestId("env-drift-notice")).not.toBeInTheDocument();
  });

  it("a JSON-blob 'string' field renders as a multi-line textarea, not a single-line input", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    renderScreen();
    const field = (await screen.findByLabelText("Cors Allowed Origins")) as HTMLTextAreaElement;
    expect(field.tagName).toBe("TEXTAREA");
    expect(field.value).toBe('["http://localhost:5173"]');
  });

  it("editing the JSON textarea and saving sends the raw string, not a re-parsed object", async () => {
    vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
    const spy = vi.spyOn(api, "updateTunables").mockResolvedValue({
      written: { "Cors Allowed Origins": '["https://example.com"]' },
      rejected: {},
      applies: "next_daemon_restart",
    });
    renderScreen();
    const field = (await screen.findByLabelText("Cors Allowed Origins")) as HTMLTextAreaElement;
    // fireEvent (not userEvent.type) -- userEvent's keystroke simulation
    // treats "[" / "]" as special key-sequence delimiters, which would mangle
    // a literal JSON-array string typed keystroke-by-keystroke.
    fireEvent.change(field, { target: { value: '["https://example.com"]' } });
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][0]).toEqual({ "Cors Allowed Origins": '["https://example.com"]' });
  });

  describe("Danger Zone — Clear Data Cache", () => {
    // Regression coverage for the bug this screen shipped with: the button
    // used to show a native confirm() then just alert("Cache cleared.") with
    // no actual side effect. These tests pin the real side effect (the
    // browser's localStorage-backed offline-response cache from
    // api/offlineCache.ts is actually emptied) rather than merely asserting
    // an alert fired.

    it("confirming actually empties the offline-response cache and reports how many entries were cleared", async () => {
      writeCacheEntry("/pilots", { some: "data" });
      writeCacheEntry("/portfolio", { other: "data" });
      expect(readCacheEntry("/pilots")).not.toBeNull();
      expect(readCacheEntry("/portfolio")).not.toBeNull();

      vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
      renderScreen();
      await screen.findByRole("heading", { name: "Runtime tunables" });

      await userEvent.click(await screen.findByTestId("clear-cache-button"));
      expect(await screen.findByTestId("clear-cache-confirm")).toBeInTheDocument();
      await userEvent.click(screen.getByTestId("clear-cache-confirm-yes"));

      // The real side effect: both cache entries are actually gone.
      expect(readCacheEntry("/pilots")).toBeNull();
      expect(readCacheEntry("/portfolio")).toBeNull();

      const notice = await screen.findByTestId("cache-cleared-notice");
      expect(notice).toHaveTextContent("Cleared 2 cached responses from this browser.");
      // The confirmation dialog is dismissed after confirming.
      expect(screen.queryByTestId("clear-cache-confirm")).not.toBeInTheDocument();
    });

    it("reports honestly when there is nothing cached to clear, instead of a fabricated success", async () => {
      vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
      renderScreen();
      await screen.findByRole("heading", { name: "Runtime tunables" });

      await userEvent.click(await screen.findByTestId("clear-cache-button"));
      await userEvent.click(await screen.findByTestId("clear-cache-confirm-yes"));

      expect(await screen.findByTestId("cache-cleared-notice")).toHaveTextContent(
        "Nothing to clear",
      );
    });

    it("Cancel leaves the cache untouched and closes the dialog with no action taken", async () => {
      writeCacheEntry("/pilots", { some: "data" });

      vi.spyOn(api, "getTunables").mockResolvedValue(baseTunables());
      renderScreen();
      await screen.findByRole("heading", { name: "Runtime tunables" });

      await userEvent.click(await screen.findByTestId("clear-cache-button"));
      expect(await screen.findByTestId("clear-cache-confirm")).toBeInTheDocument();
      await userEvent.click(screen.getByTestId("clear-cache-cancel"));

      expect(screen.queryByTestId("clear-cache-confirm")).not.toBeInTheDocument();
      expect(screen.queryByTestId("cache-cleared-notice")).not.toBeInTheDocument();
      expect(readCacheEntry("/pilots")).not.toBeNull();
    });
  });
});

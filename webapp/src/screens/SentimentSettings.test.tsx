/**
 * SentimentSettings.test.tsx — the dedicated /settings/sentiment sub-route
 * screen (a thin wrapper around GenericSettingsEditor). Exercises the same
 * honesty invariants SettingsManager.test.tsx covers for the general
 * tunables editor -- GenericSettingsEditor is the shared engine behind both
 * screens, so this file is the primary coverage for that engine's dirty-
 * tracking, save-only-changed-keys, per-key rejection, and null-never-
 * fabricated-as-zero behavior, scoped through the sentiment screen's own
 * api methods and field set.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SentimentSettings } from "./SentimentSettings";
import { api, ApiError } from "../api/client";
import type { TunablesResponse } from "../api/types";

function baseSentimentTunables(overrides: Partial<TunablesResponse> = {}): TunablesResponse {
  return {
    applies: "next_daemon_restart",
    groups: [
      {
        name: "Sentiment Ingestion Core",
        fields: [
          {
            key: "SENTIMENT_INGESTION_ENABLED", value: false, type: "boolean",
            default: false, description: "Master switch for multi-source sentiment ingestion.",
          },
          {
            // Honest absent value -> empty input, never a fabricated 0.
            key: "SENTIMENT_INGESTION_LOOKBACK_DAYS", value: null, type: "number",
            min: 1, max: 90, step: 1, default: 1,
            description: "Calendar days of lookback per ingestion cycle.",
          },
        ],
      },
      {
        name: "AI Credibility Verification",
        fields: [
          {
            key: "SENTIMENT_LLM_VERIFICATION_PROVIDER", value: "none", type: "enum",
            options: ["claude", "gemini", "openai", "none"], default: "none",
            description: "Which LLM provider backs sentiment-document verification.",
          },
        ],
      },
      {
        name: "Sources — Reddit, StockTwits, EDGAR, GDELT, Google News",
        fields: [
          {
            key: "SENTIMENT_SOURCES", value: "yahoo_rss,gdelt,reddit,edgar", type: "string",
            default: "yahoo_rss,gdelt,reddit,edgar", description: "Enabled sentiment-source provider names.",
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
      <SentimentSettings />
    </MemoryRouter>,
  );
}

describe("SentimentSettings screen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders groups from GET /settings/sentiment, not the general tunables set", async () => {
    const spy = vi.spyOn(api, "getSentimentSettings").mockResolvedValue(baseSentimentTunables());
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Sentiment & News Ingestion" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sentiment Ingestion Core" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "AI Credibility Verification" })).toBeInTheDocument();
    expect(screen.getByLabelText("SENTIMENT_INGESTION_ENABLED")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("applies-notice")).toBeInTheDocument();
  });

  it("renders a null number value as an empty input, not 0", async () => {
    vi.spyOn(api, "getSentimentSettings").mockResolvedValue(baseSentimentTunables());
    renderScreen();
    const input = (await screen.findByLabelText("SENTIMENT_INGESTION_LOOKBACK_DAYS")) as HTMLInputElement;
    expect(input.value).toBe("");
    expect(input.value).not.toBe("0");
  });

  it("renders the enum widget as a select with the real backend options", async () => {
    vi.spyOn(api, "getSentimentSettings").mockResolvedValue(baseSentimentTunables());
    renderScreen();
    const select = (await screen.findByLabelText("SENTIMENT_LLM_VERIFICATION_PROVIDER")) as HTMLSelectElement;
    expect(select.tagName).toBe("SELECT");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["claude", "gemini", "openai", "none"]);
  });

  it("shows an honest empty state when the backend exposes no settings for this section", async () => {
    vi.spyOn(api, "getSentimentSettings").mockResolvedValue(baseSentimentTunables({ groups: [] }));
    renderScreen();
    expect(await screen.findByText("No settings exposed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
  });

  it("shows the honest cold-start state when GET 404s", async () => {
    vi.spyOn(api, "getSentimentSettings").mockRejectedValue(new ApiError("not found", 404));
    renderScreen();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });

  it("Save sends ONLY the changed key to updateSentimentSettings", async () => {
    vi.spyOn(api, "getSentimentSettings").mockResolvedValue(baseSentimentTunables());
    const spy = vi.spyOn(api, "updateSentimentSettings").mockResolvedValue({
      written: { SENTIMENT_INGESTION_ENABLED: true },
      rejected: {},
      applies: "next_daemon_restart",
    });
    renderScreen();
    await userEvent.click(await screen.findByRole("switch", { name: "SENTIMENT_INGESTION_ENABLED" }));
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][0]).toEqual({ SENTIMENT_INGESTION_ENABLED: true });
  });

  it("surfaces per-key rejected reasons from the backend and keeps the key dirty", async () => {
    vi.spyOn(api, "getSentimentSettings").mockResolvedValue(baseSentimentTunables());
    vi.spyOn(api, "updateSentimentSettings").mockResolvedValue({
      written: {},
      rejected: { SENTIMENT_INGESTION_LOOKBACK_DAYS: "out_of_range: must be within [1, 90]." },
      applies: "next_daemon_restart",
    });
    renderScreen();
    const input = (await screen.findByLabelText("SENTIMENT_INGESTION_LOOKBACK_DAYS")) as HTMLInputElement;
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    expect(await screen.findByTestId("rejected-SENTIMENT_INGESTION_LOOKBACK_DAYS")).toHaveTextContent(/out_of_range/);
    expect(screen.getByRole("button", { name: /Save/ })).toBeEnabled();
  });

  it("env_drift.detected renders a pending-write notice with the differing keys", async () => {
    vi.spyOn(api, "getSentimentSettings").mockResolvedValue(
      baseSentimentTunables({
        env_drift: {
          detected: true,
          keys: ["SENTIMENT_INGESTION_ENABLED"],
          note: "An .env write is pending — restart to apply.",
        },
      }),
    );
    renderScreen();
    const notice = await screen.findByTestId("env-drift-notice");
    expect(notice).toHaveTextContent("SENTIMENT_INGESTION_ENABLED");
  });

  it("a plain 'string' field (SENTIMENT_SOURCES) renders as a single-line input, not a textarea", async () => {
    vi.spyOn(api, "getSentimentSettings").mockResolvedValue(baseSentimentTunables());
    renderScreen();
    const field = (await screen.findByLabelText("SENTIMENT_SOURCES")) as HTMLInputElement;
    expect(field.tagName).toBe("INPUT");
    expect(field.value).toBe("yahoo_rss,gdelt,reddit,edgar");
  });

  it("editing a comma-separated string field sends the raw string on save", async () => {
    vi.spyOn(api, "getSentimentSettings").mockResolvedValue(baseSentimentTunables());
    const spy = vi.spyOn(api, "updateSentimentSettings").mockResolvedValue({
      written: { SENTIMENT_SOURCES: "yahoo_rss,gdelt" },
      rejected: {},
      applies: "next_daemon_restart",
    });
    renderScreen();
    const field = (await screen.findByLabelText("SENTIMENT_SOURCES")) as HTMLInputElement;
    fireEvent.change(field, { target: { value: "yahoo_rss,gdelt" } });
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][0]).toEqual({ SENTIMENT_SOURCES: "yahoo_rss,gdelt" });
  });
});

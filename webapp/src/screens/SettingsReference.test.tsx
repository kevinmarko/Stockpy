import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { SettingsReference } from "./SettingsReference";
import { api } from "../api/client";
import type { SettingsReferenceResponse } from "../api/types";

const mockResponse: SettingsReferenceResponse = {
  total: 4,
  domains: ["Financial/Risk/Sizing", "Options Desk", "Market Data/DB"],
  fields: [
    {
      key: "ADVISORY_ONLY",
      category: "allowed",
      value: true,
      default: true,
      type: "boolean",
      description: "When True, ALL broker order submission is suppressed.",
      domain: "Financial/Risk/Sizing",
      dangerous: true,
      liveness: {
        applies: "immediately",
        restart_reason: null,
        capture_sites: [],
        env_pinned: false,
        dangerous: true,
        source: "runtime_store",
      },
      editable_at: "/settings/tunables",
    },
    {
      key: "OPTIONS_EARNINGS_CRUSH_ENABLED",
      category: "allowed",
      value: false,
      default: false,
      type: "boolean",
      description: "Enable earnings crush options strategy module.",
      domain: "Options Desk",
      dangerous: false,
      liveness: {
        applies: "no_effect",
        restart_reason: null,
        capture_sites: [],
        env_pinned: false,
        dangerous: false,
        source: "env_file",
      },
      editable_at: null,
    },
    {
      key: "FRED_API_KEY",
      category: "secret",
      value: "•••• (set)",
      default: "",
      type: "string",
      description: "FRED API key. Required for live macroeconomic data.",
      domain: "Market Data/DB",
      dangerous: false,
      liveness: {
        applies: "next_daemon_restart",
        restart_reason: "Cached on start.",
        capture_sites: ["scripts/export.py:12"],
        env_pinned: false,
        dangerous: false,
        source: "env_file",
      },
      editable_at: null,
    },
    {
      key: "KELLY_FRACTION",
      category: "allowed",
      value: 0.5,
      default: 0.5,
      type: "number",
      description: "Fractional Kelly sizing multiplier.",
      domain: "Financial/Risk/Sizing",
      dangerous: false,
      liveness: {
        applies: "immediately",
        restart_reason: null,
        capture_sites: [],
        env_pinned: false,
        dangerous: false,
        source: "runtime_store",
      },
      editable_at: "/settings/tunables",
    },
  ],
};

describe("SettingsReference", () => {
  beforeEach(() => {
    vi.spyOn(api, "getSettingsReference").mockResolvedValue(mockResponse);
  });

  it("renders domain sections and field details", async () => {
    render(
      <MemoryRouter>
        <SettingsReference />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("ADVISORY_ONLY")).toBeInTheDocument();
    });
    expect(screen.getByText("Settings Reference")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Financial/Risk/Sizing" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Options Desk" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Market Data/DB" })).toBeInTheDocument();

    // Dangerous badge
    expect(screen.getByText("Dangerous")).toBeInTheDocument();

    // Secret badge
    expect(screen.getByText("Secret")).toBeInTheDocument();
    // Secret masked value rendered
    expect(screen.getByText("•••• (set)")).toBeInTheDocument();

    // No-op badge & notice
    expect(
      screen.getByText("⚠️ This field is not read anywhere in active production code — changing it has no effect.")
    ).toBeInTheDocument();

    // Edit link
    const editLink = screen.getByTestId("edit-link-ADVISORY_ONLY");
    expect(editLink).toHaveAttribute("href", "/settings/tunables");
  });

  it("filters fields by search query", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SettingsReference />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("ADVISORY_ONLY")).toBeInTheDocument();
    });

    const searchInput = screen.getByTestId("settings-reference-search");
    await user.type(searchInput, "crush");

    expect(screen.getByText("OPTIONS_EARNINGS_CRUSH_ENABLED")).toBeInTheDocument();
    expect(screen.queryByText("ADVISORY_ONLY")).not.toBeInTheDocument();
    expect(screen.queryByText("FRED_API_KEY")).not.toBeInTheDocument();
  });

  it("filters fields by domain selector", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SettingsReference />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("ADVISORY_ONLY")).toBeInTheDocument();
    });

    const domainSelect = screen.getByTestId("settings-reference-domain-filter");
    await user.selectOptions(domainSelect, "Options Desk");

    expect(screen.getByText("OPTIONS_EARNINGS_CRUSH_ENABLED")).toBeInTheDocument();
    expect(screen.queryByText("ADVISORY_ONLY")).not.toBeInTheDocument();
    expect(screen.queryByText("FRED_API_KEY")).not.toBeInTheDocument();
  });
});

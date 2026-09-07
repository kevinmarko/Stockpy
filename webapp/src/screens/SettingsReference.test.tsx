import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { SettingsReference } from "./SettingsReference";
import { api } from "../api/client";
import { mockApi } from "../api/mock";
import type { SettingsReferenceResponse } from "../api/types";

const mockResponse: SettingsReferenceResponse = {
  total: 5,
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
      writable: true,
      liveness: {
        applies: "immediately",
        restart_reason: null,
        capture_sites: [],
        env_pinned: false,
        dangerous: true,
        source: "runtime_store",
      },
      // ADVISORY_ONLY is in both _TUNABLE_GROUPS and (via DANGEROUS_KEYS)
      // _FEATURE_FLAGS_GROUPS; the real backend's editor-precedence order
      // resolves feature-flags first. See webapp/src/api/mock.ts's identical
      // fix + comment for the full explanation.
      editable_at: "/settings/feature-flags",
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
      // no_op (applies: "no_effect" below) -> never writable, even though
      // it's an ordinary allowed boolean -- a live-looking Toggle for a
      // field that provably does nothing would be misleading.
      writable: false,
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
      writable: false,
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
      writable: false,
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
    {
      key: "ORCHESTRATOR_DAEMON_ENABLED",
      category: "allowed",
      value: true,
      default: true,
      type: "boolean",
      description: "Run the continuous background pipeline timer daemon.",
      domain: "Financial/Risk/Sizing",
      dangerous: false,
      writable: true,
      liveness: {
        applies: "immediately",
        restart_reason: null,
        capture_sites: [],
        env_pinned: false,
        dangerous: false,
        source: "env_file",
      },
      editable_at: "/settings/tunables",
    },
  ],
};

describe("SettingsReference", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
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
    expect(editLink).toHaveAttribute("href", "/settings/feature-flags");
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

  it("renders a Toggle only for writable fields, not for a no_op/secret/number field", async () => {
    render(
      <MemoryRouter>
        <SettingsReference />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("ADVISORY_ONLY")).toBeInTheDocument();
    });

    // writable: true fields get a switch.
    expect(screen.getByTestId("toggle-ADVISORY_ONLY")).toBeInTheDocument();
    expect(screen.getByTestId("toggle-ORCHESTRATOR_DAEMON_ENABLED")).toBeInTheDocument();
    // writable: false fields (no_op boolean, secret string, ordinary number) do not.
    expect(screen.queryByTestId("toggle-OPTIONS_EARNINGS_CRUSH_ENABLED")).not.toBeInTheDocument();
    expect(screen.queryByTestId("toggle-FRED_API_KEY")).not.toBeInTheDocument();
    expect(screen.queryByTestId("toggle-KELLY_FRACTION")).not.toBeInTheDocument();
  });

  it("toggling an ordinary (non-dangerous) writable field writes directly and reloads", async () => {
    const user = userEvent.setup();
    const updateSpy = vi.spyOn(api, "updateSettingsReference").mockResolvedValue({
      written: { ORCHESTRATOR_DAEMON_ENABLED: false },
      rejected: {},
      applies: "immediately",
      per_key_applies: { ORCHESTRATOR_DAEMON_ENABLED: "immediately" },
      note: "Saved to .env and applied to the running process — no restart needed.",
    });

    render(
      <MemoryRouter>
        <SettingsReference />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByTestId("toggle-ORCHESTRATOR_DAEMON_ENABLED")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("toggle-ORCHESTRATOR_DAEMON_ENABLED"));

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith({ ORCHESTRATOR_DAEMON_ENABLED: false });
    });
    // No confirmation dialog for a non-dangerous field.
    expect(screen.queryByTestId("reference-dangerous-confirm")).not.toBeInTheDocument();
    // Reloaded after a successful write.
    await waitFor(() => {
      expect(api.getSettingsReference).toHaveBeenCalledTimes(2);
    });
  });

  it("toggling a dangerous field opens a typed confirmation instead of writing directly", async () => {
    const user = userEvent.setup();
    const updateSpy = vi.spyOn(api, "updateSettingsReference");

    render(
      <MemoryRouter>
        <SettingsReference />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByTestId("toggle-ADVISORY_ONLY")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("toggle-ADVISORY_ONLY"));

    // The confirm dialog opens instead of an immediate write.
    await waitFor(() => {
      expect(screen.getByTestId("reference-dangerous-confirm")).toBeInTheDocument();
    });
    expect(updateSpy).not.toHaveBeenCalled();

    // The confirm button stays disabled until the exact key is typed.
    const confirmButton = screen.getByTestId("reference-dangerous-confirm-yes");
    expect(confirmButton).toBeDisabled();

    updateSpy.mockResolvedValue({
      written: { ADVISORY_ONLY: false },
      rejected: {},
      applies: "immediately",
      per_key_applies: { ADVISORY_ONLY: "immediately" },
      note: "Saved to .env and applied to the running process — no restart needed.",
    });

    const input = screen.getByLabelText('Type "ADVISORY_ONLY" to confirm');
    await user.type(input, "ADVISORY_ONLY");
    expect(confirmButton).not.toBeDisabled();

    await user.click(confirmButton);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith(
        { ADVISORY_ONLY: false },
        { ADVISORY_ONLY: "ADVISORY_ONLY" },
      );
    });
    await waitFor(() => {
      expect(screen.queryByTestId("reference-dangerous-confirm")).not.toBeInTheDocument();
    });
  });

  it("the REAL mock module never reports a no_op field as writable (regression: caught live in the browser, not by any prior test here)", async () => {
    // Every other test in this file mocks `api.getSettingsReference` with a
    // hand-rolled inline fixture -- as flagged by this fix's own audit, that
    // structurally cannot catch a bug living inside `mock.ts` itself. This
    // test exercises the REAL `mockApi.getSettingsReference()` implementation
    // instead, the same one `VITE_USE_MOCK=true` wires up for local dev.
    //
    // The bug this guards against: OPTIONS_EARNINGS_CRUSH_ENABLED is a real
    // no_op (docs/settings_liveness.json), and mockSettingsReference()'s
    // `writable` derivation correctly excludes no_op fields -- but it reads
    // that from `mockLiveness(key).applies`, which (before this fix) had no
    // override for this specific key and fell through to the generic
    // live_safe/restart_required classification, reporting "immediately"
    // (a working Toggle) instead of "no_effect". Live-verified in the
    // browser via `npx vite` + manual click-through, not caught by any
    // automated test until this one.
    //
    // Load-bearing: `client.ts` exports `api = USE_MOCK ? mockApi : liveApi`
    // -- the SAME object reference, not a wrapper -- so this describe
    // block's own `beforeEach` (`vi.spyOn(api, "getSettingsReference")...`)
    // clobbers `mockApi.getSettingsReference` too. Restoring here undoes
    // that spy so this call reaches the REAL implementation; skipping this
    // line makes the test below pass unconditionally regardless of mock.ts's
    // actual behaviour, which is exactly how the bug above went undetected
    // by this test's own first draft.
    vi.restoreAllMocks();
    const real = await mockApi.getSettingsReference();
    const field = real.fields.find((f) => f.key === "OPTIONS_EARNINGS_CRUSH_ENABLED");
    expect(field).toBeDefined();
    expect(field!.liveness.applies).toBe("no_effect");
    expect(field!.writable).toBe(false);
  });

  it("cancelling the dangerous confirmation never writes anything", async () => {
    const user = userEvent.setup();
    const updateSpy = vi.spyOn(api, "updateSettingsReference");

    render(
      <MemoryRouter>
        <SettingsReference />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByTestId("toggle-ADVISORY_ONLY")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("toggle-ADVISORY_ONLY"));
    await waitFor(() => {
      expect(screen.getByTestId("reference-dangerous-confirm")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("reference-dangerous-confirm-cancel"));

    await waitFor(() => {
      expect(screen.queryByTestId("reference-dangerous-confirm")).not.toBeInTheDocument();
    });
    expect(updateSpy).not.toHaveBeenCalled();
  });
});

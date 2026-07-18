/**
 * AIControlCenter.test.tsx — the AI Control Center's write path (PUT
 * /llm/setting) plus the last-real-call telemetry section it inherited from
 * Settings.tsx's former inline "AI providers" section.
 *
 * Coverage:
 *  - Renders ONE toggle per UNIQUE toggle_key, not one per capability (three
 *    of the five registered capabilities share LLM_COMMENTARY_ENABLED).
 *  - A toggle flip calls putLlmSetting with the right key/value.
 *  - A provider <select> change calls putLlmSetting with the provider key.
 *  - writable:false renders a read-only notice and disables every control.
 *  - The invalid_key / missing_key telemetry notices still render (moved
 *    verbatim from the former Settings.tsx section).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AIControlCenter } from "./AIControlCenter";
import { api } from "../api/client";
import type { LlmCapabilityRow, LlmProviderName, LlmStatus } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <AIControlCenter />
    </MemoryRouter>
  );
}

const noCall = (provider: LlmProviderName) => ({
  provider,
  ok: null,
  error_kind: null,
  exception_type: null,
  http_status: null,
  checked_at: null,
  age_seconds: null,
  source: "none" as const,
});

const FIVE_CAPABILITIES: LlmCapabilityRow[] = [
  {
    key: "claude_commentary",
    label: "Analyst rationale commentary",
    trigger: "on_demand",
    toggle_key: "LLM_COMMENTARY_ENABLED",
    provider_selector_setting: "LLM_COMMENTARY_RATIONALE_PROVIDER",
    provider_keys: ["ANTHROPIC_API_KEY"],
    active_provider: "claude",
    invalid_provider: null,
    enabled: true,
    key_present: true,
    built: true,
    status: "ready",
  },
  {
    key: "gemini_alerts",
    label: "Alert commentary",
    trigger: "scheduled",
    toggle_key: "LLM_COMMENTARY_ENABLED",
    provider_selector_setting: "LLM_COMMENTARY_ALERT_PROVIDER",
    provider_keys: ["GEMINI_API_KEY"],
    active_provider: "gemini",
    invalid_provider: null,
    enabled: true,
    key_present: true,
    built: true,
    status: "ready",
  },
  {
    key: "gemini_vision",
    label: "Gemini chart vision",
    trigger: "on_demand",
    toggle_key: "LLM_COMMENTARY_ENABLED",
    provider_selector_setting: null,
    provider_keys: ["GEMINI_API_KEY"],
    active_provider: null,
    invalid_provider: null,
    enabled: true,
    key_present: true,
    built: true,
    status: "ready",
  },
  {
    key: "gravity_ai_runner",
    label: "Gravity AI runner (Claude + Gemini)",
    trigger: "on_demand",
    toggle_key: "GRAVITY_AI_RUNNER_ENABLED",
    provider_selector_setting: null,
    provider_keys: ["ANTHROPIC_API_KEY", "GEMINI_API_KEY"],
    active_provider: null,
    invalid_provider: null,
    enabled: false,
    key_present: false,
    built: true,
    status: "disabled",
  },
  {
    key: "opal_research",
    label: "Opal research agent",
    trigger: "on_demand",
    toggle_key: "OPAL_RESEARCH_ENABLED",
    provider_selector_setting: "OPAL_RESEARCH_PROVIDER",
    provider_keys: ["OPENAI_API_KEY"],
    active_provider: "openai",
    invalid_provider: null,
    enabled: false,
    key_present: false,
    built: true,
    status: "disabled",
  },
];

function llmStatus(overrides: Partial<LlmStatus> = {}): LlmStatus {
  return {
    capabilities: FIVE_CAPABILITIES,
    capabilities_source: "test",
    providers: { claude: noCall("claude"), gemini: noCall("gemini"), openai: noCall("openai") },
    providers_source: "test",
    telemetry_note: "Verdicts are recorded from REAL LLM calls only.",
    attention: false,
    attention_reason: null,
    writable: true,
    writable_note: "Toggle and provider writes persist to .env and apply on the next daemon restart.",
    ...overrides,
  };
}

describe("AIControlCenter screen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders ONE toggle per unique toggle_key, not one per capability", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(llmStatus());
    renderScreen();
    await screen.findByRole("heading", { name: "AI Control Center" });
    const switches = screen.getAllByRole("switch");
    // 5 capabilities, 3 unique toggle_keys (LLM_COMMENTARY_ENABLED shared by
    // claude_commentary/gemini_alerts/gemini_vision; GRAVITY_AI_RUNNER_ENABLED;
    // OPAL_RESEARCH_ENABLED).
    expect(switches).toHaveLength(3);
    expect(screen.getByText("LLM_COMMENTARY_ENABLED")).toBeInTheDocument();
    expect(screen.getByText("GRAVITY_AI_RUNNER_ENABLED")).toBeInTheDocument();
    expect(screen.getByText("OPAL_RESEARCH_ENABLED")).toBeInTheDocument();
  });

  it("the shared toggle's label lists every capability it covers", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(llmStatus());
    renderScreen();
    expect(
      await screen.findByRole("switch", {
        name: "Analyst rationale commentary + Alert commentary + Gemini chart vision",
      })
    ).toBeInTheDocument();
  });

  it("a successful toggle flip calls putLlmSetting with the right key/value", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(
      llmStatus({
        capabilities: FIVE_CAPABILITIES.map((c) =>
          c.toggle_key === "GRAVITY_AI_RUNNER_ENABLED" ? { ...c, enabled: false } : c
        ),
      })
    );
    const putSpy = vi.spyOn(api, "putLlmSetting").mockResolvedValue({
      written: ["GRAVITY_AI_RUNNER_ENABLED"],
      value: true,
      applies: "next_daemon_restart",
      note: "Written to .env.",
    });
    renderScreen();
    const sw = await screen.findByRole("switch", { name: "Gravity AI runner (Claude + Gemini)" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    await userEvent.click(sw);
    await waitFor(() => expect(putSpy).toHaveBeenCalledTimes(1));
    expect(putSpy).toHaveBeenCalledWith("GRAVITY_AI_RUNNER_ENABLED", true);
  });

  it("a provider <select> change calls putLlmSetting with the provider key", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(llmStatus());
    const putSpy = vi.spyOn(api, "putLlmSetting").mockResolvedValue({
      written: ["LLM_COMMENTARY_RATIONALE_PROVIDER"],
      value: "gemini",
      applies: "next_daemon_restart",
      note: "Written to .env.",
    });
    renderScreen();
    const select = await screen.findByLabelText("Analyst rationale commentary provider");
    await userEvent.selectOptions(select, "gemini");
    await waitFor(() => expect(putSpy).toHaveBeenCalledTimes(1));
    expect(putSpy).toHaveBeenCalledWith("LLM_COMMENTARY_RATIONALE_PROVIDER", "gemini");
  });

  it("writable:false renders a read-only notice and disables every control", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(
      llmStatus({
        writable: false,
        writable_note: "AI-capability writes are disabled (LLM_WRITES_ENABLED=false).",
      })
    );
    renderScreen();
    expect(
      await screen.findByText("AI-capability writes are disabled (LLM_WRITES_ENABLED=false).")
    ).toBeInTheDocument();
    for (const sw of screen.getAllByRole("switch")) {
      expect(sw).toBeDisabled();
    }
    const select = screen.getByLabelText("Analyst rationale commentary provider");
    expect(select).toBeDisabled();
  });

  it("a 403 from a disabled write surfaces the server's explanatory detail, not a raw error", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(llmStatus());
    vi.spyOn(api, "putLlmSetting").mockRejectedValue(
      new Error("LLM writes are disabled (LLM_WRITES_ENABLED=false).")
    );
    renderScreen();
    const sw = await screen.findByRole("switch", { name: "Gravity AI runner (Claude + Gemini)" });
    await userEvent.click(sw);
    expect(
      await screen.findByText("LLM writes are disabled (LLM_WRITES_ENABLED=false).")
    ).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // Telemetry section (moved from Settings.tsx's former inline section).
  // -------------------------------------------------------------------------

  it("renders an invalid_key capability with an amber notice naming the key setting", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(
      llmStatus({
        capabilities: [
          { ...FIVE_CAPABILITIES[0], invalid_provider: "claude", status: "invalid_key" },
        ],
        providers: {
          claude: {
            provider: "claude",
            ok: false,
            error_kind: "auth",
            exception_type: "AuthenticationError",
            http_status: 401,
            checked_at: new Date().toISOString(),
            age_seconds: 30,
            source: "last_call",
          },
          gemini: noCall("gemini"),
          openai: noCall("openai"),
        },
        attention: true,
        attention_reason: "invalid_key",
      })
    );
    renderScreen();
    expect(await screen.findByText(/was rejected as unauthenticated/i)).toBeInTheDocument();
    expect(screen.getByText("ANTHROPIC_API_KEY")).toBeInTheDocument();
  });

  it("a missing_key capability shows the unset-key notice", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(
      llmStatus({
        capabilities: [{ ...FIVE_CAPABILITIES[4], enabled: true, status: "missing_key" }],
      })
    );
    renderScreen();
    expect(
      await screen.findByText(/is unset in/i)
    ).toBeInTheDocument();
    expect(screen.getByText("OPENAI_API_KEY")).toBeInTheDocument();
  });

  it("a rate_limit failure is telemetry, NOT an invalid-key warning", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(
      llmStatus({
        capabilities: [FIVE_CAPABILITIES[0]],
        providers: {
          claude: {
            provider: "claude",
            ok: false,
            error_kind: "rate_limit",
            exception_type: "RateLimitError",
            http_status: 429,
            checked_at: new Date().toISOString(),
            age_seconds: 5,
            source: "last_call",
          },
          gemini: noCall("gemini"),
          openai: noCall("openai"),
        },
      })
    );
    renderScreen();
    expect(await screen.findByText(/Last call failed: rate_limit/i)).toBeInTheDocument();
    expect(screen.queryByText(/was rejected as unauthenticated/i)).not.toBeInTheDocument();
  });

  it("renders the telemetry note", async () => {
    vi.spyOn(api, "getLlmStatus").mockResolvedValue(llmStatus());
    renderScreen();
    expect(
      await screen.findByText(/Verdicts are recorded from REAL LLM calls only/i)
    ).toBeInTheDocument();
  });

  it("renders an ErrorState when the fetch fails", async () => {
    vi.spyOn(api, "getLlmStatus").mockRejectedValue(new Error("boom"));
    renderScreen();
    expect(await screen.findByText("boom")).toBeInTheDocument();
  });
});

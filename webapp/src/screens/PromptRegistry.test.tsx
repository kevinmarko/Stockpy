/**
 * PromptRegistry.test.tsx — the registry screen is honest about resolution
 * provenance and write-gating: a null resolved_version/source renders "—" not
 * a fabricated guess; an empty prompt list renders the server's `reason`; a
 * disabled registry shows the warning banner; the pin control disables itself
 * when the server reports `writable: false` instead of letting the operator
 * hit a surprise 403; a cold/hard error renders the honest error state.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PromptRegistry } from "./PromptRegistry";
import { api, ApiError } from "../api/client";
import type { PromptBody, PromptEntry, PromptListResponse } from "../api/types";

function entry(overrides: Partial<PromptEntry> = {}): PromptEntry {
  return {
    id: "gravity.system",
    resolved_version: "2.0.0",
    source: "remote",
    pinned_version: null,
    cached_version_count: 2,
    ...overrides,
  };
}

function listResponse(overrides: Partial<PromptListResponse> = {}): PromptListResponse {
  return {
    enabled: true,
    prompts: [entry()],
    reason: null,
    writable: true,
    note: "Pins persist to .env and apply on the next daemon restart.",
    ...overrides,
  };
}

function body(overrides: Partial<PromptBody> = {}): PromptBody {
  return {
    id: "gravity.system",
    version: "2.0.0",
    found: true,
    body: "You are Gravity, the AI code auditor.",
    source: "remote",
    reason: null,
    cached_versions: ["2.0.0", "1.0.0"],
    has_baseline: true,
    ...overrides,
  };
}

function renderScreen() {
  return render(
    <MemoryRouter>
      <PromptRegistry />
    </MemoryRouter>,
  );
}

describe("PromptRegistry screen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders prompt rows from the list", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse());
    renderScreen();
    expect(await screen.findByRole("heading", { name: "Prompt Registry" })).toBeInTheDocument();
    // The static screen title above renders before the async getPrompts()
    // fetch resolves (the same shared race documented in
    // FmpSettings.test.tsx / EtfTransmissionSettings.test.tsx), so the FIRST
    // post-load element must be awaited via findBy; everything queried
    // afterward is safe once React has settled.
    expect(await screen.findByTestId("prompt-row-gravity.system")).toBeInTheDocument();
    expect(screen.getByText("2.0.0")).toBeInTheDocument();
    expect(screen.getByText("🌐 remote")).toBeInTheDocument();
  });

  it("a null resolved_version/source renders an honest dash, never a fabricated value", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(
      listResponse({
        prompts: [entry({ id: "orphan.prompt", resolved_version: null, source: null, cached_version_count: 0 })],
      }),
    );
    renderScreen();
    const row = await screen.findByTestId("prompt-row-orphan.prompt");
    const cells = within(row).getAllByRole("cell");
    // resolved_version cell and source cell both render "—", not "null"/"0"/blank.
    expect(cells[1].textContent).toBe("—");
    expect(cells[2].textContent).toBe("—");
  });

  it("an empty prompt list renders the server's reason", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(
      listResponse({ prompts: [], reason: "No prompt IDs found — the committed baseline directory may be empty." }),
    );
    renderScreen();
    expect(
      await screen.findByText("No prompt IDs found — the committed baseline directory may be empty."),
    ).toBeInTheDocument();
  });

  it("a disabled registry shows the warning banner but still lists baseline prompts", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(
      listResponse({ enabled: false, prompts: [entry({ source: "baseline", resolved_version: "baseline" })] }),
    );
    renderScreen();
    expect(await screen.findByTestId("prompt-registry-disabled-notice")).toBeInTheDocument();
    expect(screen.getByTestId("prompt-row-gravity.system")).toBeInTheDocument();
  });

  it("a cold/hard error renders the honest error state, not a blank table", async () => {
    vi.spyOn(api, "getPrompts").mockRejectedValue(new ApiError("Service unavailable", 503));
    renderScreen();
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });

  it("clicking a row opens the detail modal and lazily fetches the resolved body", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse());
    vi.spyOn(api, "getPrompt").mockResolvedValue(body());
    renderScreen();
    await userEvent.click(await screen.findByTestId("prompt-row-gravity.system"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("gravity.system")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByTestId("prompt-view-resolved-toggle"));
    expect(await within(dialog).findByTestId("prompt-resolved-body")).toHaveTextContent(
      "You are Gravity, the AI code auditor.",
    );
  });

  it("an unresolved prompt (found: false) shows the honest reason, not an empty body", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse());
    vi.spyOn(api, "getPrompt").mockResolvedValue(
      body({ found: false, body: null, source: null, reason: "No body available for 'gravity.system'." }),
    );
    renderScreen();
    await userEvent.click(await screen.findByTestId("prompt-row-gravity.system"));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("No body available for 'gravity.system'.")).toBeInTheDocument();
  });

  it("diffs two versions and renders line-level differences", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse());
    vi.spyOn(api, "getPrompt").mockImplementation(async (_id: string, version?: string) => {
      if (version === "1.0.0") return body({ version: "1.0.0", body: "line one\nline two", source: null });
      if (version === "2.0.0") return body({ version: "2.0.0", body: "line one\nline TWO changed", source: null });
      return body();
    });
    renderScreen();
    await userEvent.click(await screen.findByTestId("prompt-row-gravity.system"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByTestId("prompt-diff-toggle"));
    await userEvent.click(within(dialog).getByTestId("prompt-diff-compare"));
    const output = await within(dialog).findByTestId("prompt-diff-output");
    expect(output.textContent).toContain("line one");
    expect(output).toHaveTextContent(/line two/);
    expect(output).toHaveTextContent(/line TWO changed/);
  });

  it("identical versions report no differences", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse());
    vi.spyOn(api, "getPrompt").mockResolvedValue(body({ body: "same text" }));
    renderScreen();
    await userEvent.click(await screen.findByTestId("prompt-row-gravity.system"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByTestId("prompt-diff-toggle"));
    await userEvent.click(within(dialog).getByTestId("prompt-diff-compare"));
    expect(await within(dialog).findByTestId("prompt-diff-output")).toHaveTextContent(
      "No differences between the two versions.",
    );
  });

  it("writable: false disables the pin control instead of allowing a doomed write", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(
      listResponse({ writable: false, note: "Pin writes are disabled (PROMPT_REGISTRY_WRITES_ENABLED=false)." }),
    );
    vi.spyOn(api, "getPrompt").mockResolvedValue(body());
    renderScreen();
    await userEvent.click(await screen.findByTestId("prompt-row-gravity.system"));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByTestId("prompt-pin-disabled-notice")).toBeInTheDocument();
    expect(within(dialog).queryByTestId("prompt-pin-set")).not.toBeInTheDocument();
  });

  it("setting a pin calls putPromptPin with the chosen version and refreshes the list", async () => {
    const getPromptsSpy = vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse());
    vi.spyOn(api, "getPrompt").mockResolvedValue(body());
    const pinSpy = vi.spyOn(api, "putPromptPin").mockResolvedValue({
      prompt_id: "gravity.system",
      version: "1.0.0",
      pins: { "gravity.system": "1.0.0" },
      applies: "next_daemon_restart",
      note: "Pinned 'gravity.system' -> '1.0.0'. Saved to .env; effective on next daemon restart.",
    });
    renderScreen();
    await userEvent.click(await screen.findByTestId("prompt-row-gravity.system"));
    const dialog = await screen.findByRole("dialog");
    const select = await within(dialog).findByTestId("prompt-pin-target");
    await userEvent.selectOptions(select, "1.0.0");
    await userEvent.click(within(dialog).getByTestId("prompt-pin-set"));
    await waitFor(() => expect(pinSpy).toHaveBeenCalledWith({ prompt_id: "gravity.system", version: "1.0.0" }));
    expect(await within(dialog).findByTestId("prompt-pin-message")).toHaveTextContent(/Pinned/);
    // The list is reloaded after a successful pin (a second getPrompts call).
    await waitFor(() => expect(getPromptsSpy.mock.calls.length).toBeGreaterThan(1));
  });

  it("clearing a pin calls putPromptPin with version: null", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse());
    vi.spyOn(api, "getPrompt").mockResolvedValue(body());
    const pinSpy = vi.spyOn(api, "putPromptPin").mockResolvedValue({
      prompt_id: "gravity.system",
      version: null,
      pins: {},
      applies: "next_daemon_restart",
      note: "Pin cleared for 'gravity.system'. Saved to .env; effective on next daemon restart.",
    });
    renderScreen();
    await userEvent.click(await screen.findByTestId("prompt-row-gravity.system"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByTestId("prompt-pin-clear"));
    await waitFor(() => expect(pinSpy).toHaveBeenCalledWith({ prompt_id: "gravity.system", version: null }));
  });

  it("Sync prompts creates a command job, polls to success, and reloads the list", async () => {
    const getPromptsSpy = vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse());
    const createJobSpy = vi.spyOn(api, "createJob").mockResolvedValue({
      job_id: "job-1",
      job_type: "command",
      status: "running",
      cancellable: false,
    });
    vi.spyOn(api, "getJobStatus").mockResolvedValue({
      job_id: "job-1",
      job_type: "command",
      status: "success",
      exit_code: 0,
      is_running: false,
      cancellable: false,
    });
    renderScreen();
    await userEvent.click(await screen.findByTestId("prompt-sync-now"));
    await waitFor(() =>
      expect(createJobSpy).toHaveBeenCalledWith("command", {
        command: "prompt_registry",
        subcommand: "sync",
        args: [],
        confirm: false,
      }),
    );
    expect(await screen.findByTestId("prompt-sync-message")).toHaveTextContent("Sync complete.");
    await waitFor(() => expect(getPromptsSpy.mock.calls.length).toBeGreaterThan(1));
  });

  it("Sync prompts is disabled when the registry itself is disabled", async () => {
    vi.spyOn(api, "getPrompts").mockResolvedValue(listResponse({ enabled: false }));
    renderScreen();
    expect(await screen.findByTestId("prompt-sync-now")).toBeDisabled();
  });
});

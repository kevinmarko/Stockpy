/**
 * Console.test.tsx — the One-Click Command Center: quick-action job launchers
 * plus the live log stream. Exercises the real mock API (createJob/getJobStatus/
 * cancelJob) so a drift between mockApi and liveApi's JobRecord shape would
 * surface here, and confirms Cancel only ever renders for a cancellable job.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Console } from "./Console";
import { api } from "../api/client";

function renderConsole() {
  return render(
    <MemoryRouter>
      <Console />
    </MemoryRouter>
  );
}

describe("Console screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the title and every quick-action card", () => {
    renderConsole();
    expect(
      screen.getByRole("heading", { name: "One-Click Command Center" })
    ).toBeInTheDocument();
    expect(screen.getByText("🛡️ Preflight Check")).toBeInTheDocument();
    expect(screen.getByText("🧪 Run Test Suite")).toBeInTheDocument();
    expect(screen.getByText("🚀 Advisory Pipeline")).toBeInTheDocument();
    expect(screen.getByText("⚡ Full Verification")).toBeInTheDocument();
  });

  it("launching a job shows the active job badge and a Cancel button", async () => {
    renderConsole();
    screen.getByText("🛡️ Preflight Check").click();

    expect(await screen.findByText(/Active Job:/)).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel Active Job" })).toBeInTheDocument();
  });

  it("does not render a Cancel button for a non-cancellable job", async () => {
    vi.spyOn(api, "createJob").mockResolvedValueOnce({
      job_id: "job-orchestrator-1",
      job_type: "orchestrator",
      status: "running",
      cancellable: false,
    });
    renderConsole();
    screen.getByText("🚀 Advisory Pipeline").click();

    expect(await screen.findByText(/Active Job:/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Cancel Active Job" })
    ).not.toBeInTheDocument();
  });

  it("polls status until the job reaches a terminal state", async () => {
    vi.spyOn(api, "createJob").mockResolvedValueOnce({
      job_id: "job-1",
      job_type: "pytest",
      status: "running",
      cancellable: true,
    });
    vi.spyOn(api, "getJobStatus").mockResolvedValueOnce({
      job_id: "job-1",
      job_type: "pytest",
      status: "success",
      exit_code: 0,
      is_running: false,
      cancellable: true,
    });

    renderConsole();
    screen.getByText("🧪 Run Test Suite").click();
    expect(await screen.findByText("running")).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText("success")).toBeInTheDocument(), {
      timeout: 3000,
    });
    // A terminal job has nothing left to cancel.
    expect(
      screen.queryByRole("button", { name: "Cancel Active Job" })
    ).not.toBeInTheDocument();
  });
});

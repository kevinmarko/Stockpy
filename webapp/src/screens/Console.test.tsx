/**
 * Console.test.tsx — the One-Click Command Center: quick-action job launchers
 * plus the live log stream. Exercises the real mock API (createJob/getJobStatus/
 * cancelJob) so a drift between mockApi and liveApi's JobRecord shape would
 * surface here, and confirms Cancel only ever renders for a cancellable job.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Console } from "./Console";
import { api } from "../api/client";
import { JobConflictError } from "../api/types";
import { DensityProvider } from "../components/DensityContext";
import { ToastProvider } from "../components/ToastProvider";

function renderConsole() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <DensityProvider>
          <Console />
        </DensityProvider>
      </ToastProvider>
    </MemoryRouter>
  );
}

describe("Console screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the title, the 'How this works' guide, and every quick-action card", () => {
    renderConsole();
    expect(
      screen.getByRole("heading", { name: "One-Click Command Center" })
    ).toBeInTheDocument();
    expect(screen.getByTestId("tab-guide-console")).toBeInTheDocument();
    expect(screen.getByText("🛡️ Preflight Check")).toBeInTheDocument();
    expect(screen.getByText("🧪 Run Test Suite")).toBeInTheDocument();
    expect(screen.getByText("🚀 Advisory Pipeline")).toBeInTheDocument();
    expect(screen.getByText("⚡ Full Verification")).toBeInTheDocument();
    expect(screen.getByText("🔍 Gravity Audit")).toBeInTheDocument();
    expect(screen.getByText("📊 Run Backtest")).toBeInTheDocument();
  });

  it("launching a job shows the active job badge and a Cancel button", async () => {
    renderConsole();
    screen.getByText("🛡️ Preflight Check").click();

    expect(await screen.findByText(/Active Job:/)).toBeInTheDocument();
    // "running" now appears twice: the Active Job badge and this session's
    // job-history table below it (both real, both the same job).
    expect(screen.getAllByText("running").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Cancel Active Job" })).toBeInTheDocument();
  });

  it("a JobConflictError from createJob shows an 'already running' toast naming the existing job, not a generic launch-failed error", async () => {
    vi.spyOn(api, "createJob").mockRejectedValueOnce(
      new JobConflictError(
        "Job of type 'preflight' conflicts with already-running job 'preflight' (ID: job-existing-1)",
        "job-existing-1",
        "preflight",
        null
      )
    );
    renderConsole();
    screen.getByText("🛡️ Preflight Check").click();

    expect(await screen.findByText("Already running")).toBeInTheDocument();
    expect(screen.getByText(/job-existing-1/)).toBeInTheDocument();
    expect(screen.queryByText(/failed to launch/)).not.toBeInTheDocument();
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

  it("the Advisory Pipeline card says it triggers a full daemon cycle that can place paper orders, not 'Run main.py cycle'", () => {
    renderConsole();
    const card = screen.getByText("🚀 Advisory Pipeline").closest("button")!;
    expect(card).toHaveTextContent(/full daemon pipeline cycle/);
    expect(card).toHaveTextContent(/paper orders/);
    expect(card).toHaveTextContent(/execution queue/);
    expect(card).not.toHaveTextContent(/main\.py/);
  });

  it("a real (mock-API) Advisory Pipeline launch is non-cancellable and the log panel explains the empty stream", async () => {
    renderConsole();
    // Before any job: the generic idle log copy, not the daemon explanation.
    expect(screen.queryByTestId("log-stream-empty-message")).not.toBeInTheDocument();

    screen.getByText("🚀 Advisory Pipeline").click();
    expect(await screen.findByText(/Active Job:/)).toBeInTheDocument();

    // advisory is daemon-hosted: no local process to cancel.
    expect(
      screen.queryByRole("button", { name: "Cancel Active Job" })
    ).not.toBeInTheDocument();
    expect(screen.getByText("No")).toBeInTheDocument(); // Cancellable column

    // ...and the (always empty) log stream says why instead of "No logs yet".
    const note = await screen.findByTestId("log-stream-empty-message");
    expect(note).toHaveTextContent(/runs inside the orchestrator daemon/);
    expect(note).toHaveTextContent(/cannot be cancelled/);
    expect(within(note).getByRole("link", { name: "Pipeline Dashboard" })).toHaveAttribute(
      "href",
      "/pipeline"
    );
  });

  it("Advisory Pipeline with the kill switch on surfaces the backend's refusal in the launch-failure toast, with no active job", async () => {
    // Skip past the 30s single-flight window the earlier real advisory launch
    // in this file may still occupy, so the kill-switch refusal (not a
    // conflict) is what is exercised.
    const realNow = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(realNow + 120_000);
    await api.pauseAutomation("manual halt");
    try {
      renderConsole();
      screen.getByText("🚀 Advisory Pipeline").click();

      expect(await screen.findByText("Job advisory failed to launch")).toBeInTheDocument();
      expect(
        screen.getByText(/The global kill switch is active, so the daemon will not start a pipeline cycle\. Reason: manual halt\./)
      ).toBeInTheDocument();
      // (No "Already running" assertion: react-hot-toast's store is global, so
      // an earlier test's conflict toast can still be mounted here.)
      expect(screen.queryByText(/Active Job:/)).not.toBeInTheDocument();
    } finally {
      await api.resumeAutomation("test cleanup");
    }
  });

  it("a cancellable subprocess job does NOT get the daemon explanation in its log panel", async () => {
    renderConsole();
    screen.getByText("🧪 Run Test Suite").click();
    expect(await screen.findByText(/Active Job:/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel Active Job" })).toBeInTheDocument();
    expect(screen.queryByTestId("log-stream-empty-message")).not.toBeInTheDocument();
  });

  it("an orchestrator job that fell back to a local subprocess (cancellable) is not mislabelled as daemon-hosted", async () => {
    vi.spyOn(api, "createJob").mockResolvedValueOnce({
      job_id: "job-orch-local",
      job_type: "orchestrator",
      status: "running",
      cancellable: true,
    });
    renderConsole();
    screen.getByText("🚀 Advisory Pipeline").click();
    expect(await screen.findByText(/Active Job:/)).toBeInTheDocument();
    expect(screen.queryByTestId("log-stream-empty-message")).not.toBeInTheDocument();
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
    await waitFor(() => expect(screen.getAllByText("running").length).toBeGreaterThan(0));

    await waitFor(() => expect(screen.getAllByText("success").length).toBeGreaterThan(0), {
      timeout: 3000,
    });
    // A terminal job has nothing left to cancel.
    expect(
      screen.queryByRole("button", { name: "Cancel Active Job" })
    ).not.toBeInTheDocument();
  });

  it("Gravity Audit launches a job directly, with no param form", async () => {
    const createJobSpy = vi.spyOn(api, "createJob");
    renderConsole();
    screen.getByText("🔍 Gravity Audit").click();

    expect(await screen.findByText(/Active Job:/)).toBeInTheDocument();
    expect(createJobSpy).toHaveBeenCalledWith("gravity", undefined);
  });

  it("Run Backtest opens a param form and requires strategies before launching", async () => {
    const createJobSpy = vi.spyOn(api, "createJob");
    renderConsole();
    screen.getByText("📊 Run Backtest").click();

    expect(await screen.findByText("Strategies (comma-separated ids)")).toBeInTheDocument();

    screen.getByRole("button", { name: "Run Backtest" }).click();
    expect(
      await screen.findByText("Enter at least one strategy id (comma-separated).")
    ).toBeInTheDocument();
    expect(createJobSpy).not.toHaveBeenCalled();
  });

  it("Run Backtest launches a validation job with the entered params", async () => {
    const createJobSpy = vi.spyOn(api, "createJob");
    renderConsole();
    screen.getByText("📊 Run Backtest").click();

    const strategiesInput = await screen.findByPlaceholderText(
      "rsi2_mean_reversion, macd_trend"
    );
    fireEvent.change(strategiesInput, {
      target: { value: "rsi2_mean_reversion, macd_trend" },
    });

    screen.getByRole("button", { name: "Run Backtest" }).click();

    await waitFor(() => expect(createJobSpy).toHaveBeenCalled());
    const [jobType, params] = createJobSpy.mock.calls[0];
    expect(jobType).toBe("validation");
    expect((params as any).strategies).toEqual(["rsi2_mean_reversion", "macd_trend"]);
    expect((params as any).start).toBeTruthy();
    expect((params as any).end).toBeTruthy();

    // The form closes once the job is launched.
    await waitFor(() =>
      expect(
        screen.queryByText("Strategies (comma-separated ids)")
      ).not.toBeInTheDocument()
    );
  });

  it("handles successful cancellation and updates active job status", async () => {
    vi.spyOn(api, "createJob").mockResolvedValueOnce({
      job_id: "job-cancel-1",
      job_type: "pytest",
      status: "running",
      cancellable: true,
      is_running: true,
    });
    vi.spyOn(api, "cancelJob").mockResolvedValueOnce({
      cancelled: true,
      job_id: "job-cancel-1",
    });
    vi.spyOn(api, "getJobStatus").mockResolvedValueOnce({
      job_id: "job-cancel-1",
      job_type: "pytest",
      status: "cancelled",
      cancellable: true,
      is_running: false,
    });

    renderConsole();
    screen.getByText("🧪 Run Test Suite").click();

    const cancelBtn = await screen.findByRole("button", { name: "Cancel Active Job" });
    fireEvent.click(cancelBtn);

    expect(await screen.findByText("Job cancelled")).toBeInTheDocument();
    expect(await screen.findByText("job-cancel-1 cancelled.")).toBeInTheDocument();
  });

  it("handles cancellation when job already completed (cancelled=false) with accurate toast", async () => {
    vi.spyOn(api, "createJob").mockResolvedValueOnce({
      job_id: "job-race-1",
      job_type: "pytest",
      status: "running",
      cancellable: true,
      is_running: true,
    });
    vi.spyOn(api, "cancelJob").mockResolvedValueOnce({
      cancelled: false,
      job_id: "job-race-1",
    });
    vi.spyOn(api, "getJobStatus").mockResolvedValueOnce({
      job_id: "job-race-1",
      job_type: "pytest",
      status: "success",
      cancellable: true,
      is_running: false,
    });

    renderConsole();
    screen.getByText("🧪 Run Test Suite").click();

    const cancelBtn = await screen.findByRole("button", { name: "Cancel Active Job" });
    fireEvent.click(cancelBtn);

    expect(await screen.findByText("Job completed")).toBeInTheDocument();
    expect(await screen.findByText("job-race-1 already finished (success).")).toBeInTheDocument();
  });

  it("handles cancellation when cancel not confirmed and job is still running", async () => {
    vi.spyOn(api, "createJob").mockResolvedValueOnce({
      job_id: "job-stuck-1",
      job_type: "pytest",
      status: "running",
      cancellable: true,
      is_running: true,
    });
    vi.spyOn(api, "cancelJob").mockResolvedValueOnce({
      cancelled: false,
      job_id: "job-stuck-1",
    });
    vi.spyOn(api, "getJobStatus").mockResolvedValueOnce({
      job_id: "job-stuck-1",
      job_type: "pytest",
      status: "running",
      cancellable: true,
      is_running: true,
    });

    renderConsole();
    screen.getByText("🧪 Run Test Suite").click();

    const cancelBtn = await screen.findByRole("button", { name: "Cancel Active Job" });
    fireEvent.click(cancelBtn);

    expect(await screen.findByText("Cancel requested")).toBeInTheDocument();
    expect(
      await screen.findByText("Could not be confirmed — the job may still be running.")
    ).toBeInTheDocument();
  });

  it("polls all in-flight jobs in jobHistory so superseded jobs also update upon completion", async () => {
    vi.spyOn(api, "createJob")
      .mockResolvedValueOnce({
        job_id: "job-first",
        job_type: "preflight",
        status: "running",
        cancellable: true,
        is_running: true,
      })
      .mockResolvedValueOnce({
        job_id: "job-second",
        job_type: "pytest",
        status: "running",
        cancellable: true,
        is_running: true,
      });

    vi.spyOn(api, "getJobStatus").mockImplementation(async (id: string) => {
      if (id === "job-first") {
        return {
          job_id: "job-first",
          job_type: "preflight",
          status: "success",
          cancellable: true,
          is_running: false,
        };
      }
      return {
        job_id: "job-second",
        job_type: "pytest",
        status: "running",
        cancellable: true,
        is_running: true,
      };
    });

    renderConsole();
    fireEvent.click(screen.getByText("🛡️ Preflight Check"));
    await screen.findByText(/Active Job:/);
    expect(screen.getAllByText("job-first").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByText("🧪 Run Test Suite"));
    await waitFor(() => expect(screen.getAllByText("job-second").length).toBeGreaterThan(0));

    // After polling fires, superseded job-first should update to success
    await waitFor(() => {
      expect(screen.getByText("success")).toBeInTheDocument();
    }, { timeout: 3500 });
  });

  it("renders 'Yes', 'No', and '—' in Cancellable column based on status and cancellable flag", async () => {
    vi.spyOn(api, "createJob").mockResolvedValueOnce({
      job_id: "job-canc-yes",
      job_type: "pytest",
      status: "running",
      cancellable: true,
      is_running: true,
    });

    renderConsole();
    fireEvent.click(screen.getByText("🧪 Run Test Suite"));
    await screen.findByText(/Active Job:/);

    // Running + cancellable -> "Yes"
    expect(screen.getByText("Yes")).toBeInTheDocument();
  });
});

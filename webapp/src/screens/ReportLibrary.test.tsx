/**
 * ReportLibrary.test.tsx — Report Library screen (G5).
 *
 * Covers the honesty branches per .claude/skills/new-pwa-screen/SKILL.md §3:
 * happy-path sections from the real mock fixture, the opt-in "View inline"
 * gate (content is fetched ONLY on click, never prefetched), the corrupt-
 * summary-at-read-time honesty branch (listed but unreadable — a `reason`,
 * not a crash), the cold-start empty manifest, and a hard error state.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportLibrary } from "./ReportLibrary";
import { api, ApiError } from "../api/client";
import type { ReportManifest } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <ReportLibrary />
    </MemoryRouter>
  );
}

const EMPTY_MANIFEST: ReportManifest = {
  generated_at: null,
  reports: [],
  reason: "No reports generated yet — run the pipeline, generate a briefing, or run the validation harness.",
};

describe("ReportLibrary screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders all five sections from the real mock manifest", async () => {
    renderScreen();
    expect(await screen.findByText("📰 Daily report")).toBeInTheDocument();
    expect(screen.getByText("📊 Orchestrator dashboards")).toBeInTheDocument();
    expect(screen.getByText("📝 Daily briefings")).toBeInTheDocument();
    expect(screen.getByText("🧠 NotebookLM export")).toBeInTheDocument();
    expect(screen.getByText("✅ Validation reports")).toBeInTheDocument();
    expect(screen.getByText("daily_report.html")).toBeInTheDocument();
    expect(screen.getByText("daily_report_dashboard.html")).toBeInTheDocument();
  });

  it("does not fetch an HTML report's content until 'View inline' is clicked (no prefetch)", async () => {
    // BriefingsSection DOES eagerly fetch the selected briefing's content
    // (it renders inline unconditionally, matching Streamlit) -- the "no
    // prefetch" guarantee this test covers is specific to the opt-in
    // HTML-kind reports (daily report / dashboards / validation HTML).
    const spy = vi.spyOn(api, "getReport");
    renderScreen();
    await screen.findByText("daily_report.html");
    expect(spy).not.toHaveBeenCalledWith("daily_report.html");

    const viewBtn = screen.getByTestId("view-inline-daily_report.html");
    fireEvent.click(viewBtn);
    await waitFor(() => expect(spy).toHaveBeenCalledWith("daily_report.html"));
  });

  it("View inline renders the fetched HTML in an iframe, and Hide removes it", async () => {
    renderScreen();
    await screen.findByText("daily_report.html");
    const viewBtn = screen.getByTestId("view-inline-daily_report.html");
    fireEvent.click(viewBtn);

    const iframe = await screen.findByTitle("daily_report.html");
    expect(iframe).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("view-inline-daily_report.html"));
    await waitFor(() => expect(screen.queryByTitle("daily_report.html")).not.toBeInTheDocument());
  });

  it("Download fetches content on demand and triggers a browser download", async () => {
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    let downloadedName: string | null = null;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      downloadedName = this.download;
    });

    renderScreen();
    await screen.findByText("daily_report.html");
    fireEvent.click(screen.getByTestId("download-daily_report.html"));

    await waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
    expect(downloadedName).toBe("daily_report.html");

    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it("renders a briefing's markdown inline by default (no gate, matches Streamlit)", async () => {
    renderScreen();
    await screen.findByText("📝 Daily briefings");
    // Two sections render MiniMarkdown on this page (briefings + the
    // NotebookLM export below), so "mini-markdown" alone is no longer
    // unique -- the specific heading is the scoped assertion.
    expect(await screen.findByRole("heading", { name: /Daily Briefing/ })).toBeInTheDocument();
    expect(screen.getAllByTestId("mini-markdown").length).toBeGreaterThan(0);
  });

  it("switching the briefing selector loads the other briefing's content", async () => {
    const user = userEvent.setup();
    renderScreen();
    await screen.findByRole("heading", { name: /Daily Briefing — 2026-07-30/ });
    const select = screen.getByTestId("briefing-select") as HTMLSelectElement;
    await user.selectOptions(select, "briefing_2026-07-29.md");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /2026-07-29/ })).toBeInTheDocument()
    );
  });

  it("a validation summary renders as a collapsed <details> that fetches JSON on expand", async () => {
    const spy = vi.spyOn(api, "getReport");
    renderScreen();
    const details = await screen.findByTestId(
      "validation-summary-trend_following_validation_summary.json"
    );
    expect(spy).not.toHaveBeenCalledWith("trend_following_validation_summary.json");
    fireEvent(details, new Event("toggle"));
    Object.defineProperty(details, "open", { value: true, configurable: true });
    fireEvent(details, new Event("toggle"));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("trend_following_validation_summary.json")
    );
    expect(await within(details).findByText(/timeseries_momentum/)).toBeInTheDocument();
  });

  it("a validation summary that is corrupt at read time renders the honest reason, never a 500 or fabricated JSON", async () => {
    renderScreen();
    const details = await screen.findByTestId(
      "validation-summary-corrupt_validation_summary.json"
    );
    Object.defineProperty(details, "open", { value: true, configurable: true });
    fireEvent(details, new Event("toggle"));
    expect(
      await within(details).findByText(/Could not parse corrupt_validation_summary\.json/)
    ).toBeInTheDocument();
  });

  it("Generate today's briefing posts the same command job Commands.tsx uses", async () => {
    const spy = vi.spyOn(api, "createJob").mockResolvedValue({
      job_id: "job-briefing-1",
      job_type: "command",
      status: "running",
      cancellable: true,
    });
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByTestId("generate-briefing-button"));
    expect(spy).toHaveBeenCalledWith("command", {
      command: "daily_briefing.py",
      subcommand: null,
      args: [],
      confirm: false,
    });
    expect(await screen.findByTestId("generate-briefing-status")).toHaveTextContent(
      "job-briefing-1"
    );
  });

  it("renders the NotebookLM export's markdown inline by default (no gate)", async () => {
    renderScreen();
    await screen.findByText("🧠 NotebookLM export");
    expect(await screen.findByText("Stockpy System Export")).toBeInTheDocument();
    expect(screen.getByTestId("download-notebooklm_source.md")).toBeInTheDocument();
  });

  it("Generate NotebookLM export posts the export_notebooklm.py command job", async () => {
    const spy = vi.spyOn(api, "createJob").mockResolvedValue({
      job_id: "job-notebooklm-1",
      job_type: "command",
      status: "running",
      cancellable: true,
    });
    const user = userEvent.setup();
    renderScreen();
    await user.click(await screen.findByTestId("generate-notebooklm-export-button"));
    expect(spy).toHaveBeenCalledWith("command", {
      command: "export_notebooklm.py",
      subcommand: null,
      args: [],
      confirm: false,
    });
    expect(await screen.findByTestId("generate-notebooklm-export-status")).toHaveTextContent(
      "job-notebooklm-1"
    );
  });

  it("Download on the NotebookLM export triggers a browser download of the already-loaded content", async () => {
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
    let downloadedName: string | null = null;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement
    ) {
      downloadedName = this.download;
    });

    renderScreen();
    const downloadBtn = await screen.findByTestId("download-notebooklm_source.md");
    fireEvent.click(downloadBtn);

    await waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
    expect(downloadedName).toBe("notebooklm_source.md");

    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
  });

  it("no NotebookLM export yet renders the honest empty state, not fabricated content", async () => {
    const realManifest = await api.getReports();
    const manifestWithoutExport: ReportManifest = {
      ...realManifest,
      reports: realManifest.reports.filter((r) => r.kind !== "notebooklm_export"),
    };
    vi.spyOn(api, "getReports").mockResolvedValueOnce(manifestWithoutExport);
    renderScreen();
    await screen.findByText("🧠 NotebookLM export");
    expect(await screen.findByText("No export yet")).toBeInTheDocument();
  });

  it("a cold-start empty manifest renders the honest empty state, not fabricated sections", async () => {
    vi.spyOn(api, "getReports").mockResolvedValueOnce(EMPTY_MANIFEST);
    renderScreen();
    expect(await screen.findByText("No reports generated yet")).toBeInTheDocument();
    expect(screen.queryByText("📰 Daily report")).not.toBeInTheDocument();
  });

  it("a hard error renders ErrorState with a retry, never a fabricated manifest", async () => {
    vi.spyOn(api, "getReports").mockRejectedValueOnce(new ApiError("db unreachable", 500));
    renderScreen();
    expect(await screen.findByText("Couldn't load")).toBeInTheDocument();
  });

  it("a 404 cold-start renders the honest 'Nothing here yet' state", async () => {
    vi.spyOn(api, "getReports").mockRejectedValueOnce(new ApiError("not found", 404));
    renderScreen();
    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();
  });
});

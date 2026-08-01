/**
 * ReportPreviewModal.test.tsx — the omni-search report quick-preview. Takes
 * a real GET /reports manifest name and renders GET /reports/{name}'s actual
 * content per content_type, never the old hardcoded "InvestYo Executive
 * Briefing" text shown regardless of which report was clicked.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportPreviewModal } from "./ReportPreviewModal";
import { api } from "../api/client";

describe("ReportPreviewModal", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders a markdown briefing's real content, keyed by the real report name", async () => {
    render(<ReportPreviewModal name="briefing_2026-07-30.md" onClose={vi.fn()} />);
    expect(await screen.findByText("Daily Briefing — 2026-07-30")).toBeInTheDocument();
    expect(screen.getByText(/NVDA: BUY, conviction 0.71/)).toBeInTheDocument();
    expect(screen.queryByText("InvestYo Executive Briefing")).not.toBeInTheDocument();
  });

  it("a second, different report name renders different real content, not the same canned text", async () => {
    render(<ReportPreviewModal name="briefing_2026-07-29.md" onClose={vi.fn()} />);
    expect(await screen.findByText("Daily Briefing — 2026-07-29")).toBeInTheDocument();
    expect(screen.getByText(/dead-lettered symbol \(ZZZZ/)).toBeInTheDocument();
  });

  it("renders a JSON validation summary as real pretty-printed JSON", async () => {
    render(<ReportPreviewModal name="trend_following_validation_summary.json" onClose={vi.fn()} />);
    expect(await screen.findByText(/"strategy_id": "timeseries_momentum"/)).toBeInTheDocument();
    expect(screen.getByText(/"deployable": true/)).toBeInTheDocument();
  });

  it("renders an HTML report inline via a sandboxed iframe", async () => {
    render(<ReportPreviewModal name="daily_report.html" onClose={vi.fn()} />);
    const iframe = await screen.findByTitle("daily_report.html");
    expect(iframe).toBeInTheDocument();
    expect(iframe.getAttribute("sandbox")).toBe("allow-scripts allow-same-origin");
  });

  it("a report that matched the manifest but failed to read shows its real reason, not fabricated content", async () => {
    render(<ReportPreviewModal name="corrupt_validation_summary.json" onClose={vi.fn()} />);
    expect(await screen.findByText(/corrupt|malformed|failed|unreadable/i)).toBeInTheDocument();
  });

  it("an unknown report name surfaces the real 404 error, not a silent blank modal", async () => {
    render(<ReportPreviewModal name="does_not_exist.md" onClose={vi.fn()} />);
    expect(await screen.findByText(/No report named/)).toBeInTheDocument();
  });

  it("fetches by the exact name prop, never a client-guessed path", async () => {
    const spy = vi.spyOn(api, "getReport");
    render(<ReportPreviewModal name="briefing_2026-07-30.md" onClose={vi.fn()} />);
    await screen.findByText("Daily Briefing — 2026-07-30");
    expect(spy).toHaveBeenCalledWith("briefing_2026-07-30.md");
  });
});

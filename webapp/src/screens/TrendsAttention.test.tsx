/**
 * TrendsAttention.test.tsx — Google Trends "Search Attention" screen.
 * Runs against the real mock API fixtures; per-test overrides spy on `api`.
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TrendsAttention } from "./TrendsAttention";
import { api } from "../api/client";
import { mockApi } from "../api/mock";

const SECTIONS = [
  "Raw windows",
  "Seams between windows",
  "Rescaled and stitched series",
  "ASVI (abnormal search volume)",
];

async function renderScreen() {
  render(<TrendsAttention />);
  await screen.findByTestId("trends-honesty-strip");
}

async function pick(term: string) {
  const select = await screen.findByLabelText("Term");
  fireEvent.change(select, { target: { value: term } });
}

describe("TrendsAttention screen (mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the default term (AAL) with all four section headings", async () => {
    await renderScreen();
    for (const h of SECTIONS) {
      expect(await screen.findByRole("heading", { name: h })).toBeInTheDocument();
    }
    expect(screen.queryByTestId("trends-mismatch-warning")).toBeNull();
    expect(screen.getByLabelText("Term")).toHaveValue("AAL");
    expect(screen.getByText("Trends data for 5 of 12 tracked symbols")).toBeInTheDocument();
    expect(screen.getByText("91 d / 30 d")).toBeInTheDocument();
  });

  it("switching to AM shows the recompute mismatch warning", async () => {
    await renderScreen();
    await pick("AM");
    const w = await screen.findByTestId("trends-mismatch-warning");
    expect(w).toHaveTextContent("does not match a fresh stitch");
    expect(w).toHaveTextContent("4.20");
  });

  it("AQN shows the degenerate guard badge", async () => {
    await renderScreen();
    await pick("AQN");
    expect(await screen.findByText(/guard: f=1/)).toBeInTheDocument();
  });

  it("AGNC (stitched_only) shows its reason, no seams table, still ASVI", async () => {
    await renderScreen();
    await pick("AGNC");
    expect(await screen.findByText(/no complete batch of raw windows remains/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Seams between windows" })).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("heading", { name: "ASVI (abnormal search volume)" })).toBeInTheDocument();
    expect(screen.getByText(/latest attempt/i)).toBeInTheDocument();
  });

  it("ARCC (partial_only) shows its reason", async () => {
    await renderScreen();
    await pick("ARCC");
    expect(await screen.findByText(/3 of 6 windows returned\); no complete batch/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Raw windows" })).toBeNull();
  });

  it("disabled, error and no_data terms show their reasons", async () => {
    const base = await mockApi.getTrendsTerms();
    const row = base.terms[0];
    vi.spyOn(api, "getTrendsTerms").mockResolvedValue({
      ...base,
      terms: [
        ...base.terms,
        { ...row, term: "ZZZZ" },
        { ...row, term: "ERR" },
        { ...row, term: "QQQ" },
      ],
    });
    await renderScreen();
    await pick("ZZZZ");
    expect(await screen.findByText("Trends refresh is disabled and no data is stored.")).toBeInTheDocument();
    await pick("ERR");
    expect(await screen.findByText(/could not be read: RuntimeError/)).toBeInTheDocument();
    await pick("QQQ");
    expect(
      await screen.findByText("No Google Trends batch or stitched series stored for this term.")
    ).toBeInTheDocument();
  });

  it("terms endpoint no_data shows its reason and fetches no SVI", async () => {
    const base = await mockApi.getTrendsTerms();
    vi.spyOn(api, "getTrendsTerms").mockResolvedValue({
      ...base,
      status: "no_data",
      reason: "No Trends downloads stored yet.",
      terms: [],
    });
    const svi = vi.spyOn(api, "getTrendsSvi");
    render(<TrendsAttention />);
    expect(await screen.findByText("No Trends downloads stored yet.")).toBeInTheDocument();
    expect(svi).not.toHaveBeenCalled();
  });

  it("null coverage renders an em dash, never 0", async () => {
    const base = await mockApi.getTrendsTerms();
    vi.spyOn(api, "getTrendsTerms").mockResolvedValue({
      ...base,
      tracked_universe_size: null,
      tracked_with_data: null,
    });
    await renderScreen();
    const strip = screen.getByTestId("trends-honesty-strip");
    expect(within(strip).getByText("Coverage").nextSibling).toHaveTextContent("—");
    expect(within(strip).queryByText(/tracked symbols/)).toBeNull();
  });

  it("a stale term shows a stale badge", async () => {
    await renderScreen();
    await pick("ABR");
    const strip = await screen.findByTestId("trends-honesty-strip");
    expect(within(strip).getByText("stale")).toBeInTheDocument();
    expect(within(strip).getByText(/2\.9 d ago/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "ABR (stale)" })).toBeInTheDocument();
  });

  it("a network error on the terms call shows the error state", async () => {
    vi.spyOn(api, "getTrendsTerms").mockRejectedValue(new Error("boom"));
    render(<TrendsAttention />);
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
  });
});

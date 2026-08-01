/**
 * Attribution.test.tsx — factor exposure + correlation cluster attribution
 * screen. Verifies the real mock renders both sections, and that every
 * honesty branch (no holdings, no matched factor data, null factor value,
 * unmatched symbols, empty clusters, heavy-concentration warning) degrades to
 * an explicit honest message rather than a fabricated 0 or blank chart.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Attribution } from "./Attribution";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import type { PortfolioAttribution } from "../api/types";

function renderScreen() {
  return render(
    <MemoryRouter>
      <Attribution />
    </MemoryRouter>
  );
}

const BASE: PortfolioAttribution = {
  as_of: "2026-07-11T21:05:00Z",
  factor_exposure: {
    as_of: "2026-07-11T21:05:00Z",
    exposures: {
      value_z: -0.4,
      quality_z: 1.2,
      lowvol_z: 0.3,
      size_z: -1.8,
      multifactor_composite: 0.25,
    },
    coverage: {
      held_count: 3,
      matched_count: 2,
      matched_value_pct: 0.8,
      unmatched_symbols: ["DUK"],
    },
    reason: null,
  },
  correlation_clusters: {
    clusters: [
      {
        cluster_id: 1,
        symbols: ["AAPL", "MSFT", "NVDA"],
        n_symbols: 3,
        avg_intra_corr: 0.71,
        weight_pct: 0.25, // below the 30% heavy-concentration threshold
        insufficient_history: false,
      },
      {
        cluster_id: 3,
        symbols: ["DUK"],
        n_symbols: 1,
        avg_intra_corr: null,
        weight_pct: 0.1,
        insufficient_history: false,
      },
    ],
    lookback_days: 60,
    reason: null,
  },
};

describe("Attribution screen (real mock API)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the factor exposure and correlation cluster sections from the mock", async () => {
    renderScreen();
    expect(
      await screen.findByRole("heading", { name: "Portfolio attribution" })
    ).toBeInTheDocument();
    expect(await screen.findByText("Factor exposure")).toBeInTheDocument();
    expect(await screen.findByText("Correlation clusters")).toBeInTheDocument();
    // At least one cluster card renders from the mock fixture (the mega-cap
    // tech cluster is genuinely concentrated enough to ALSO trip the
    // diversification warning -- "AAPL" legitimately appears more than once).
    expect((await screen.findAllByText(/AAPL/)).length).toBeGreaterThan(0);
  });

  it("no held positions renders the honest empty state, never a fabricated bar", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      as_of: null,
      factor_exposure: {
        as_of: null,
        exposures: {
          value_z: null, quality_z: null, lowvol_z: null,
          size_z: null, multifactor_composite: null,
        },
        coverage: { held_count: 0, matched_count: 0, matched_value_pct: null, unmatched_symbols: [] },
        reason: "no held positions",
      },
      correlation_clusters: { clusters: [], lookback_days: 60, reason: "no held positions" },
    });
    renderScreen();
    expect(await screen.findByText("No holdings yet")).toBeInTheDocument();
    expect(await screen.findByText("No clusters yet")).toBeInTheDocument();
    expect(screen.getByText("no held positions")).toBeInTheDocument();
  });

  it("held positions with no matched factor data render the honest reason, not zeros", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      ...BASE,
      factor_exposure: {
        as_of: null,
        exposures: {
          value_z: null, quality_z: null, lowvol_z: null,
          size_z: null, multifactor_composite: null,
        },
        coverage: { held_count: 2, matched_count: 0, matched_value_pct: null, unmatched_symbols: ["AAPL", "MSFT"] },
        reason: "no pipeline snapshot yet",
      },
    });
    renderScreen();
    expect(await screen.findByText("No factor data yet")).toBeInTheDocument();
    expect(screen.getByText("no pipeline snapshot yet")).toBeInTheDocument();
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });

  it("unmatched held symbols are surfaced in the coverage caption", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    expect(await screen.findByText(/Not yet scored: DUK/)).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 holdings scored/)).toBeInTheDocument();
  });

  it("a null factor value renders an em dash, never 0 or NaN", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      ...BASE,
      factor_exposure: {
        ...BASE.factor_exposure,
        exposures: { ...BASE.factor_exposure.exposures, size_z: null },
      },
    });
    renderScreen();
    await screen.findByText("Factor exposure");
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
    expect(screen.queryByText("NaN")).not.toBeInTheDocument();
  });

  it("an insufficient-history cluster (cluster_id 0) is flagged, not shown as a real grouping", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      ...BASE,
      correlation_clusters: {
        clusters: [
          {
            cluster_id: 0,
            symbols: ["ZZZZ"],
            n_symbols: 1,
            avg_intra_corr: null,
            weight_pct: 0.05,
            insufficient_history: true,
          },
        ],
        lookback_days: 60,
        reason: null,
      },
    });
    renderScreen();
    expect(
      await screen.findByText(/Not enough price history yet to correlate/)
    ).toBeInTheDocument();
  });

  it("a heavily concentrated cluster (>30% of book) shows the diversification warning", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce({
      ...BASE,
      correlation_clusters: {
        clusters: [
          {
            cluster_id: 1,
            symbols: ["AAPL", "MSFT", "NVDA"],
            n_symbols: 3,
            avg_intra_corr: 0.9,
            weight_pct: 0.55,
            insufficient_history: false,
          },
        ],
        lookback_days: 60,
        reason: null,
      },
    });
    renderScreen();
    expect(await screen.findByText(/High concentration/)).toBeInTheDocument();
  });

  it("no heavy concentration -> no warning banner rendered", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Correlation clusters");
    expect(screen.queryByText(/High concentration/)).not.toBeInTheDocument();
  });
});

describe("Brinson-Fachler manual-input calculator", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the 11-sector editable table (real mock GET, no server round-trip needed to seed it)", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    expect(
      await screen.findByText("Brinson-Fachler attribution")
    ).toBeInTheDocument();
    expect(screen.getByText("Energy")).toBeInTheDocument();
    expect(screen.getByText("Real Estate")).toBeInTheDocument();
    expect(screen.getByText("Information Technology")).toBeInTheDocument();
    // All 11 GICS sectors * 4 editable numeric cells each.
    expect(screen.getAllByRole("spinbutton")).toHaveLength(11 * 4);
  });

  it("all-zero default rows show the client-side weight-sum warning before any edit", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");
    expect(
      screen.getByText("Portfolio weights sum to 0.00% (expected ~100%).")
    ).toBeInTheDocument();
    expect(
      screen.getByText("Benchmark weights sum to 0.00% (expected ~100%).")
    ).toBeInTheDocument();
  });

  it("computing a single fully-weighted sector matches hand-computed effects", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    const row = screen.getByText("Energy").closest("tr") as HTMLElement;
    await user.clear(within(row).getByLabelText("Energy portfolio weight percent"));
    await user.type(within(row).getByLabelText("Energy portfolio weight percent"), "100");
    await user.clear(within(row).getByLabelText("Energy portfolio return percent"));
    await user.type(within(row).getByLabelText("Energy portfolio return percent"), "10");
    await user.clear(within(row).getByLabelText("Energy benchmark weight percent"));
    await user.type(within(row).getByLabelText("Energy benchmark weight percent"), "100");
    await user.clear(within(row).getByLabelText("Energy benchmark return percent"));
    await user.type(within(row).getByLabelText("Energy benchmark return percent"), "8");

    await user.click(screen.getByRole("button", { name: "Compute" }));

    // Single fully-weighted sector: Portfolio Return = 10%, Benchmark Return =
    // 8%, Active Return = 2% = Selection Effect (Allocation/Interaction = 0
    // since portfolio and benchmark weights are identical in every sector).
    expect(await screen.findByText("+10.00%")).toBeInTheDocument(); // Portfolio return
    expect(screen.getByText("+8.00%")).toBeInTheDocument(); // Benchmark return
    expect(screen.getAllByText("+2.00%").length).toBeGreaterThan(0); // Active + Selection effect
  });

  it("a 422 from the server renders the honest error message inline, not a generic failure", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    vi.spyOn(api, "getBrinsonFachlerAttribution").mockRejectedValueOnce(
      new ApiError("No rows with a non-blank sector name.", 422)
    );
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Compute" }));

    const errorBox = await screen.findByTestId("brinson-error");
    expect(errorBox).toHaveTextContent("No rows with a non-blank sector name.");
  });
});

describe("Brinson-Fachler bulk paste from spreadsheet", () => {
  afterEach(() => vi.restoreAllMocks());

  async function openPasteSection(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByText("Bulk paste from spreadsheet (TSV / CSV)"));
  }

  it("parses a TSV paste with a header row and replaces the table", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await openPasteSection(user);

    const textarea = screen.getByLabelText("Pasted sector matrix");
    await user.click(textarea);
    await user.paste(
      "Sector\tPortfolio Weight (%)\tPortfolio Return (%)\tBenchmark Weight (%)\tBenchmark Return (%)\n" +
        "Information Technology\t28\t12.4\t26\t10.1\n" +
        "Health Care\t20\t5.0\t18\t4.5"
    );
    await user.click(screen.getByRole("button", { name: "Parse pasted data" }));

    expect(
      await screen.findByText("Parsed 2 sector row(s) -- table updated below.")
    ).toBeInTheDocument();
    const itRow = screen.getByText("Information Technology").closest("tr") as HTMLElement;
    expect(within(itRow).getByLabelText("Information Technology portfolio weight percent")).toHaveValue(28);
    expect(within(itRow).getByLabelText("Information Technology portfolio return percent")).toHaveValue(12.4);
    expect(within(itRow).getByLabelText("Information Technology benchmark weight percent")).toHaveValue(26);
    expect(within(itRow).getByLabelText("Information Technology benchmark return percent")).toHaveValue(10.1);
  });

  it("parses a headerless positional CSV paste, including free-text sector names not on the GICS-11 list", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await openPasteSection(user);
    await user.click(screen.getByLabelText("Pasted sector matrix"));
    await user.paste("Tech,28,12.4,26,10.1\nHealth,20,5.0,18,4.5");
    await user.click(screen.getByRole("button", { name: "Parse pasted data" }));

    await screen.findByText("Parsed 2 sector row(s) -- table updated below.");
    expect(screen.getByText("Tech")).toBeInTheDocument();
    expect(screen.getByText("Health")).toBeInTheDocument();
    // Replaced, not appended -- the original 11 GICS rows are gone.
    expect(screen.queryByText("Energy")).not.toBeInTheDocument();
  });

  it("strips % suffixes and coerces an unparseable cell to 0, without rejecting the paste", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await openPasteSection(user);
    await user.click(screen.getByLabelText("Pasted sector matrix"));
    // A header row is required here: a LONE data row whose 4 numeric cells
    // aren't all numeric is (by design, matching the Python original) itself
    // detected as a header -- see tests/test_report_viewer_helpers.py's
    // test_parse_strips_percent_and_coerces_bad_cells_to_zero for the same
    // documented edge case.
    await user.paste(
      "Sector,Portfolio Weight (%),Portfolio Return (%),Benchmark Weight (%),Benchmark Return (%)\n" +
        "Tech,28%,foo,26,10.1"
    );
    await user.click(screen.getByRole("button", { name: "Parse pasted data" }));

    await screen.findByText("Parsed 1 sector row(s) -- table updated below.");
    const row = screen.getByText("Tech").closest("tr") as HTMLElement;
    expect(within(row).getByLabelText("Tech portfolio weight percent")).toHaveValue(28);
    expect(within(row).getByLabelText("Tech portfolio return percent")).toHaveValue(0);
  });

  it("a lone data row with a non-numeric cell is (by design) detected as a header, producing the honest 'no data rows' error", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await openPasteSection(user);
    await user.click(screen.getByLabelText("Pasted sector matrix"));
    await user.paste("Tech,28%,foo,26,10.1");
    await user.click(screen.getByRole("button", { name: "Parse pasted data" }));

    const errorBox = await screen.findByTestId("brinson-paste-error");
    expect(errorBox).toHaveTextContent("No data rows found after parsing.");
  });

  it("a wrong column count shows the exact error message inline, and leaves the table untouched", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await openPasteSection(user);
    await user.click(screen.getByLabelText("Pasted sector matrix"));
    await user.paste("Sector,Weight\nFinancials,10");
    await user.click(screen.getByRole("button", { name: "Parse pasted data" }));

    const errorBox = await screen.findByTestId("brinson-paste-error");
    expect(errorBox).toHaveTextContent(
      "Expected 5 columns (Sector, P-Weight, P-Return, B-Weight, B-Return); got 2."
    );
    // The original 11-row default table is untouched by a rejected paste.
    expect(screen.getAllByRole("spinbutton")).toHaveLength(11 * 4);
  });

  it("empty pasted text shows an error rather than silently doing nothing", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await openPasteSection(user);
    await user.click(screen.getByRole("button", { name: "Parse pasted data" }));

    const errorBox = await screen.findByTestId("brinson-paste-error");
    expect(errorBox).toHaveTextContent("Pasted text is empty.");
  });

  it("Reset to GICS 11 default restores the original all-zero table and clears any paste state", async () => {
    vi.spyOn(api, "getPortfolioAttribution").mockResolvedValueOnce(BASE);
    renderScreen();
    await screen.findByText("Brinson-Fachler attribution");

    const user = userEvent.setup();
    await openPasteSection(user);
    await user.click(screen.getByLabelText("Pasted sector matrix"));
    await user.paste("Tech,28,12.4,26,10.1");
    await user.click(screen.getByRole("button", { name: "Parse pasted data" }));
    await screen.findByText("Tech");

    await user.click(screen.getByRole("button", { name: "Reset to GICS 11 default" }));

    expect(screen.queryByText("Tech")).not.toBeInTheDocument();
    expect(screen.getByText("Energy")).toBeInTheDocument();
    expect(screen.getAllByRole("spinbutton")).toHaveLength(11 * 4);
    expect(
      screen.queryByText("Parsed 1 sector row(s) -- table updated below.")
    ).not.toBeInTheDocument();
  });
});

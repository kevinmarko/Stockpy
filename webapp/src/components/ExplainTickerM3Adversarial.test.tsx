import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, afterEach } from "vitest";
import { ExplainTickerDrawer } from "./ExplainTickerDrawer";
import { ExplainTickerButton, TickerWithExplain } from "./ExplainTickerButton";
import { ExplainTickerProvider } from "../context/ExplainTickerContext";
import { mockApi } from "../api/mock";
import { api } from "../api/client";
import type { ExplainTickerResponse } from "../api/types";

describe("Milestone M3 Frontend Adversarial Testing", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // --------------------------------------------------------------------------
  // 1. Mock Scenarios Contract Verification
  // --------------------------------------------------------------------------
  describe("Mock Scenarios Honesty & Coverage", () => {
    it("NVDA mock fixture has disabled profile and honest reason", async () => {
      const res = await mockApi.getExplainTicker("NVDA");
      expect(res.symbol).toBe("NVDA");
      expect(res.company_profile.available).toBe(false);
      expect(res.company_profile.description).toBeNull();
      expect(res.company_profile.reason).toContain("FMP_PROFILE_ENABLED=False");
      expect(res.tracking.tracked).toBe(true);
      expect(res.factor_breakdown.available).toBe(true);
    });

    it("XYZ mock fixture is untracked with no factors and no bars", async () => {
      const res = await mockApi.getExplainTicker("XYZ");
      expect(res.symbol).toBe("XYZ");
      expect(res.tracking.tracked).toBe(false);
      expect(res.tracking.held).toBe(false);
      expect(res.tracking.quantity).toBeNull();
      // "untracked" is the real literal api/data_api.py emits for a symbol
      // that's neither held nor watchlisted -- "uncovered" is a genuine
      // CoverageStatus value reserved for a *tracked* symbol the data
      // providers can't cover, a different fact (fixed 2026-09 audit: a
      // prior mock/backend drift had this test pinned to the wrong value).
      expect(res.tracking.coverage_status).toBe("untracked");
      expect(res.factor_breakdown.available).toBe(false);
      expect(res.factor_breakdown.reason).toContain("not tracked in the quantitative pipeline");
      expect(res.price_history_status.available).toBe(false);
      expect(res.price_history_status.bar_count).toBe(0);
      expect(res.price_history_status.status).toBe("no_data");
    });

    it("COST mock fixture has null factors with honest reason", async () => {
      const res = await mockApi.getExplainTicker("COST");
      expect(res.symbol).toBe("COST");
      expect(res.factor_breakdown.available).toBe(false);
      expect(res.factor_breakdown.multifactor).toBeNull();
      expect(res.factor_breakdown.momentum).toBeNull();
      expect(res.factor_breakdown.reason).toBe("No daily signals computed for this cycle");
      expect(res.price_history_status.available).toBe(true);
      expect(res.price_history_status.bar_count).toBeGreaterThan(0);
    });

    it("XOM mock fixture has zero bars and honest no_data status", async () => {
      const res = await mockApi.getExplainTicker("XOM");
      expect(res.symbol).toBe("XOM");
      expect(res.price_history_status.available).toBe(false);
      expect(res.price_history_status.bar_count).toBe(0);
      expect(res.price_history_status.status).toBe("no_data");
      expect(res.price_history_status.reason).toContain("unavailable in local store");
      expect(res.factor_breakdown.available).toBe(true);
    });

    it("arbitrary untracked symbol falls back cleanly in mock", async () => {
      const res = await mockApi.getExplainTicker("UNKNOWN_RANDOM_SYM");
      expect(res.symbol).toBe("UNKNOWN_RANDOM_SYM");
      expect(res.tracking.tracked).toBe(false);
      expect(res.company_profile.available).toBe(false);
      expect(res.company_profile.reason).toContain("Company profile not found");
    });
  });

  // --------------------------------------------------------------------------
  // 2. StopPropagation & PreventDefault Probing
  // --------------------------------------------------------------------------
  describe("ExplainTickerButton Event Containment (stopPropagation)", () => {
    it("prevents click from bubbling to parent clickable container / button role", async () => {
      const parentClickHandler = vi.fn();

      render(
        <ExplainTickerProvider>
          <div role="button" tabIndex={0} onClick={parentClickHandler} data-testid="parent-container">
            Parent Clickable Container
            <ExplainTickerButton symbol="AAPL" />
          </div>
        </ExplainTickerProvider>
      );

      const infoBtn = screen.getByTestId("explain-ticker-btn-AAPL");
      await userEvent.click(infoBtn);

      expect(parentClickHandler).not.toHaveBeenCalled();
    });

    it("prevents click from bubbling to parent link <a>", async () => {
      const parentLinkHandler = vi.fn();

      render(
        <ExplainTickerProvider>
          <a href="#test" onClick={parentLinkHandler} data-testid="parent-link">
            Link Title
            <ExplainTickerButton symbol="MSFT" />
          </a>
        </ExplainTickerProvider>
      );

      const infoBtn = screen.getByTestId("explain-ticker-btn-MSFT");
      await userEvent.click(infoBtn);

      expect(parentLinkHandler).not.toHaveBeenCalled();
    });

    it("prevents click from bubbling to parent table row <tr>", async () => {
      const rowClickHandler = vi.fn();

      render(
        <ExplainTickerProvider>
          <table>
            <tbody>
              <tr onClick={rowClickHandler} data-testid="parent-row">
                <td>
                  <TickerWithExplain symbol="GOOGL" />
                </td>
              </tr>
            </tbody>
          </table>
        </ExplainTickerProvider>
      );

      const infoBtn = screen.getByTestId("explain-ticker-btn-GOOGL");
      await userEvent.click(infoBtn);

      expect(rowClickHandler).not.toHaveBeenCalled();
    });

    it("renders nothing safely when symbol is empty string or falsy", () => {
      const { container } = render(
        <ExplainTickerProvider>
          <ExplainTickerButton symbol="" />
        </ExplainTickerProvider>
      );

      expect(container.querySelector("button")).toBeNull();
    });
  });

  // --------------------------------------------------------------------------
  // 3. Render Crash Safety with Null / Empty / Malformed Payloads
  // --------------------------------------------------------------------------
  describe("ExplainTickerDrawer Null Safety & Failure Modes", () => {
    it("renders safely when ALL optional fields in the payload are null", async () => {
      const allNullPayload: ExplainTickerResponse = {
        symbol: "NULLCO",
        company_profile: {
          available: true,
          company_name: null,
          description: null,
          sector: null,
          industry: null,
          exchange: null,
          website: null,
          ceo: null,
          market_cap: null,
          source: null,
          reason: null,
        },
        tracking: {
          tracked: true,
          held: false,
          quantity: null,
          avg_cost: null,
          market_value: null,
          watchlists: [],
          coverage_status: "unknown",
          rating_consecutive_bad_cycles: null,
          rating_excluded: false,
          reasons: [],
        },
        factor_breakdown: {
          available: true,
          as_of: null,
          multifactor: null,
          momentum: null,
          volatility_regime: null,
          tactical: null,
          sentiment: null,
          raw_factors: {},
          reason: null,
        },
        price_history_status: {
          available: true,
          bar_count: 0,
          earliest_date: null,
          latest_date: null,
          latest_close: null,
          status: "ok",
          reason: null,
        },
      };

      vi.spyOn(api, "getExplainTicker").mockResolvedValue(allNullPayload);

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="NULLCO" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      // Verify header renders with fallback
      expect(await screen.findByRole("heading", { name: "NULLCO" })).toBeInTheDocument();
      expect(screen.getByText("Explain This Ticker")).toBeInTheDocument();

      // Verify Section 1 renders description fallback
      expect(screen.getByText(/No detailed description provided by profile source/i)).toBeInTheDocument();

      // Verify Section 2 renders not-held state
      expect(screen.getByText("Not Held in Portfolio")).toBeInTheDocument();

      // Verify Section 4 renders no bars notice
      expect(screen.getByTestId("no-price-bars-notice")).toBeInTheDocument();
    });

    it("renders safely when factor breakdown has keys with null/undefined values", async () => {
      const nullFactorsPayload: ExplainTickerResponse = {
        symbol: "PARTIAL",
        company_profile: {
          available: true,
          company_name: "Partial Corp",
          description: "A partially loaded company.",
          sector: "Tech",
          industry: null,
          exchange: null,
          website: null,
          ceo: null,
          market_cap: null,
          source: "fmp",
          reason: null,
        },
        tracking: {
          tracked: true,
          held: true,
          quantity: 10,
          avg_cost: null,
          market_value: null,
          watchlists: ["alpha"],
          coverage_status: "full",
          rating_consecutive_bad_cycles: 2,
          rating_excluded: false,
          reasons: ["Held in portfolio"],
        },
        factor_breakdown: {
          available: true,
          as_of: new Date().toISOString(),
          multifactor: {
            value_z: null,
            quality_z: 1.25,
            low_vol_z: null,
            composite: null,
          },
          momentum: {
            rsi_14: null,
            macd_line: 0.45,
          },
          volatility_regime: {
            regime: null,
            hmm_risk_on_probability: null,
            garch_vol: null,
          },
          tactical: null,
          sentiment: {
            aggregate_score: null,
          },
          raw_factors: {},
          reason: null,
        },
        price_history_status: {
          available: false,
          bar_count: 0,
          earliest_date: null,
          latest_date: null,
          latest_close: null,
          status: "no_data",
          reason: "No bars stored",
        },
      };

      vi.spyOn(api, "getExplainTicker").mockResolvedValue(nullFactorsPayload);

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="PARTIAL" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      expect(await screen.findByRole("heading", { name: "PARTIAL" })).toBeInTheDocument();
      // Should show dash "—" for null factor values
      const dashes = screen.getAllByText("—");
      expect(dashes.length).toBeGreaterThan(0);
      // Quality score 1.25 should be displayed
      expect(screen.getByText("1.25")).toBeInTheDocument();
      // Consecutive bad cycles notice
      expect(screen.getByText(/Rating streak: 2 consecutive BAD cycle/i)).toBeInTheDocument();
    });

    it("displays error notice gracefully without React crash when API rejects", async () => {
      vi.spyOn(api, "getExplainTicker").mockRejectedValue(new Error("500 Internal Server Error: database is locked"));

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="ERR" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      // Verify the error notice is rendered without throwing
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("500 Internal Server Error: database is locked");
    });

    it("handles failed backfill gracefully with inline error message", async () => {
      vi.spyOn(api, "getExplainTicker").mockResolvedValue({
        symbol: "BACKFILL_FAIL",
        company_profile: { available: false, company_name: null, description: null, sector: null, industry: null, exchange: null, website: null, ceo: null, market_cap: null, source: null, reason: "Unavailable" },
        tracking: { tracked: false, held: false, quantity: null, avg_cost: null, market_value: null, watchlists: [], coverage_status: "uncovered", rating_consecutive_bad_cycles: null, rating_excluded: false, reasons: [] },
        factor_breakdown: { available: false, as_of: null, multifactor: null, momentum: null, volatility_regime: null, tactical: null, sentiment: null, raw_factors: {}, reason: "None" },
        price_history_status: { available: false, bar_count: 0, earliest_date: null, latest_date: null, latest_close: null, status: "no_data", reason: "None" },
      });
      vi.spyOn(api, "triggerSymbolBackfill").mockRejectedValue(new Error("Network connection lost"));

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="BACKFILL_FAIL" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      const backfillBtn = await screen.findByText("Add to Universe / Backfill");
      await userEvent.click(backfillBtn);

      // Should show the failure message inline
      expect(await screen.findByText(/Backfill failed: Network connection lost/i)).toBeInTheDocument();
    });

    it("closes when clicking the backdrop", async () => {
      const onClose = vi.fn();
      const { container } = render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="AAPL" isOpen={true} onClose={onClose} />
        </ExplainTickerProvider>
      );

      await screen.findByTestId("explain-ticker-drawer");
      const backdrop = container.querySelector(".sheet-backdrop");
      expect(backdrop).not.toBeNull();
      if (backdrop) {
        await userEvent.click(backdrop);
        expect(onClose).toHaveBeenCalledTimes(1);
      }
    });
  });

  // --------------------------------------------------------------------------
  // 4. Price chart honesty: bars gaps must never render as a fabricated
  //    interpolated line, and a "stale" price_history_status must be
  //    honestly surfaced rather than rendered indistinguishably from fresh
  //    data (CONSTRAINT #4; mirrors GexProfileView.tsx's chain_source
  //    honesty-banner precedent for degraded chart data).
  // --------------------------------------------------------------------------
  describe("Price chart bars-gap and staleness honesty", () => {
    const basePayload = (): ExplainTickerResponse => ({
      symbol: "GAPCO",
      company_profile: {
        available: true,
        company_name: "Gap Co",
        description: "A test company.",
        sector: "Technology",
        industry: "Software",
        exchange: "NASDAQ",
        website: null,
        ceo: null,
        market_cap: null,
        source: "fmp",
        reason: null,
      },
      tracking: {
        tracked: true,
        held: false,
        quantity: null,
        avg_cost: null,
        market_value: null,
        watchlists: [],
        coverage_status: "full",
        rating_consecutive_bad_cycles: null,
        rating_excluded: false,
        reasons: [],
      },
      factor_breakdown: {
        available: false,
        as_of: null,
        multifactor: null,
        momentum: null,
        volatility_regime: null,
        tactical: null,
        sentiment: null,
        raw_factors: {},
        reason: "No daily signals computed for this cycle",
      },
      price_history_status: {
        available: true,
        bar_count: 3,
        earliest_date: "2026-01-01",
        latest_date: "2026-02-01",
        latest_close: 150,
        status: "ok",
        reason: null,
      },
    });

    it("renders an honest gap notice and never a smooth line across a real multi-week bars gap", async () => {
      vi.spyOn(api, "getExplainTicker").mockResolvedValue(basePayload());
      vi.spyOn(api, "getDataBars").mockResolvedValue([
        { date: "2026-01-01", Open: 100, High: 101, Low: 99, Close: 100, Volume: 1000 },
        { date: "2026-01-02", Open: 101, High: 102, Low: 100, Close: 101, Volume: 1000 },
        // A genuine ~30-day gap in the underlying bars (e.g. an unbackfilled
        // stretch) -- the chart must break the line here, not connect it.
        { date: "2026-02-01", Open: 150, High: 151, Low: 149, Close: 150, Volume: 1000 },
      ]);

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="GAPCO" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
      const gapNotice = await screen.findByTestId("price-gap-notice");
      expect(gapNotice).toHaveTextContent(/Data gap detected/i);
      expect(gapNotice).toHaveTextContent(/1 period/i);
    });

    it("does not render a gap notice for a genuinely continuous bars series", async () => {
      vi.spyOn(api, "getExplainTicker").mockResolvedValue(basePayload());
      vi.spyOn(api, "getDataBars").mockResolvedValue([
        { date: "2026-01-01", Open: 100, High: 101, Low: 99, Close: 100, Volume: 1000 },
        { date: "2026-01-02", Open: 101, High: 102, Low: 100, Close: 101, Volume: 1000 },
        { date: "2026-01-03", Open: 102, High: 103, Low: 101, Close: 102, Volume: 1000 },
      ]);

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="GAPCO" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
      expect(screen.queryByTestId("price-gap-notice")).not.toBeInTheDocument();
    });

    it("honestly flags stale price history instead of rendering it indistinguishably from fresh data", async () => {
      const payload = basePayload();
      payload.price_history_status.status = "stale";
      payload.price_history_status.reason = "Last refreshed 3 days ago";
      vi.spyOn(api, "getExplainTicker").mockResolvedValue(payload);
      vi.spyOn(api, "getDataBars").mockResolvedValue([
        { date: "2026-01-01", Open: 100, High: 101, Low: 99, Close: 100, Volume: 1000 },
        { date: "2026-01-02", Open: 101, High: 102, Low: 100, Close: 101, Volume: 1000 },
      ]);

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="GAPCO" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
      const badge = await screen.findByTestId("price-stale-notice");
      expect(badge).toHaveTextContent(/stale/i);
      const banner = await screen.findByTestId("price-stale-banner");
      expect(banner).toHaveTextContent(/stale/i);
      expect(banner).toHaveTextContent(/Last refreshed 3 days ago/i);
    });

    it("does not show a stale notice for fresh (status: ok) price history", async () => {
      vi.spyOn(api, "getExplainTicker").mockResolvedValue(basePayload());
      vi.spyOn(api, "getDataBars").mockResolvedValue([
        { date: "2026-01-01", Open: 100, High: 101, Low: 99, Close: 100, Volume: 1000 },
      ]);

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="GAPCO" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
      expect(screen.queryByTestId("price-stale-notice")).not.toBeInTheDocument();
      expect(screen.queryByTestId("price-stale-banner")).not.toBeInTheDocument();
    });

    it("honestly falls back to the no-bars notice when status is 'no_data' even if available is (inconsistently) true", async () => {
      const payload = basePayload();
      payload.price_history_status.status = "no_data";
      payload.price_history_status.reason = "No historical bars stored";
      vi.spyOn(api, "getExplainTicker").mockResolvedValue(payload);
      vi.spyOn(api, "getDataBars").mockResolvedValue([
        { date: "2026-01-01", Open: 100, High: 101, Low: 99, Close: 100, Volume: 1000 },
      ]);

      render(
        <ExplainTickerProvider>
          <ExplainTickerDrawer symbol="GAPCO" isOpen={true} onClose={vi.fn()} />
        </ExplainTickerProvider>
      );

      const noBars = await screen.findByTestId("no-price-bars-notice");
      expect(noBars).toBeInTheDocument();
      expect(screen.queryByTestId("price-chart")).not.toBeInTheDocument();
    });
  });
});

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api } from "../api/client";
import type {
  ScreenerFilterOptions,
  ScreenerFilters,
  ScreenerResult,
  SymbolSearchResult,
} from "../api/types";
import { Button, EmptyState, Input, Loading, Select, Table } from "../components/ui";
import { TabGuide } from "../components/TabGuide";
import { fmtUsd, fmtNum } from "../format";
import { theme } from "../theme";

const ANY = "";

type Preset = {
  label: string;
  hint: string;
  filters: ScreenerFilters;
};

// Pure client-side pre-fills of the filter form -- no new backend logic, no
// fabricated "strategy fit" score. Just a taste of strategy-flavored
// discovery via the same real sector/beta/dividend/market-cap filters below.
const PRESETS: Preset[] = [
  {
    label: "Large Cap Tech",
    hint: "Technology, market cap > $100B",
    filters: { sector: "Technology", marketCapMoreThan: 100_000_000_000, isActivelyTrading: true, excludeFunds: true },
  },
  {
    label: "Dividend Income",
    hint: "Dividend yield-bearing, actively trading",
    filters: { dividendMoreThan: 2, isActivelyTrading: true, excludeFunds: true },
  },
  {
    label: "Low Vol",
    hint: "Beta < 0.8, actively trading",
    filters: { betaLowerThan: 0.8, isActivelyTrading: true, excludeFunds: true },
  },
];

export function SymbolScreener() {
  const navigate = useNavigate();

  // Free-text search
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SymbolSearchResult[]>([]);
  const [searchReason, setSearchReason] = useState<string | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  // Filter form
  const [sector, setSector] = useState(ANY);
  const [industry, setIndustry] = useState(ANY);
  const [marketCapMoreThan, setMarketCapMoreThan] = useState("");
  const [priceMoreThan, setPriceMoreThan] = useState("");
  const [priceLowerThan, setPriceLowerThan] = useState("");
  const [betaLowerThan, setBetaLowerThan] = useState("");
  const [dividendMoreThan, setDividendMoreThan] = useState("");
  const [activelyTradingOnly, setActivelyTradingOnly] = useState(true);
  const [excludeFunds, setExcludeFunds] = useState(true);

  const [filterOptions, setFilterOptions] = useState<ScreenerFilterOptions>({ sectors: [], industries: [] });
  const [screenerResults, setScreenerResults] = useState<ScreenerResult[]>([]);
  const [screenerReason, setScreenerReason] = useState<string | null>(null);
  const [screenerLoading, setScreenerLoading] = useState(false);
  const [screenerError, setScreenerError] = useState<string | null>(null);
  const [screenerRan, setScreenerRan] = useState(false);

  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    api
      .getScreenerFilterOptions()
      .then((opts) => {
        if (alive) setFilterOptions(opts);
      })
      .catch(() => {
        // Non-fatal: the sector/industry dropdowns just stay empty (free-form
        // filters like market cap/beta/dividend still work).
      });
    return () => {
      alive = false;
    };
  }, []);

  const runSearch = async () => {
    const q = searchQuery.trim();
    if (!q) return;
    setSearchLoading(true);
    setSearchError(null);
    try {
      const res = await api.getSymbolSearch(q, 20);
      setSearchResults(res.results);
      setSearchReason(res.reason);
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : "Symbol search failed.");
      setSearchResults([]);
    } finally {
      setSearchLoading(false);
    }
  };

  const buildFilters = (overrides?: ScreenerFilters): ScreenerFilters => ({
    sector: sector || undefined,
    industry: industry || undefined,
    marketCapMoreThan: marketCapMoreThan ? Number(marketCapMoreThan) : undefined,
    priceMoreThan: priceMoreThan ? Number(priceMoreThan) : undefined,
    priceLowerThan: priceLowerThan ? Number(priceLowerThan) : undefined,
    betaLowerThan: betaLowerThan ? Number(betaLowerThan) : undefined,
    dividendMoreThan: dividendMoreThan ? Number(dividendMoreThan) : undefined,
    isActivelyTrading: activelyTradingOnly || undefined,
    excludeFunds: excludeFunds || undefined,
    limit: 50,
    ...overrides,
  });

  const runScreener = async (overrides?: ScreenerFilters) => {
    setScreenerLoading(true);
    setScreenerError(null);
    setScreenerRan(true);
    try {
      const res = await api.getScreenerResults(buildFilters(overrides));
      setScreenerResults(res.results);
      setScreenerReason(res.reason);
    } catch (e) {
      setScreenerError(e instanceof Error ? e.message : "Screener query failed.");
      setScreenerResults([]);
    } finally {
      setScreenerLoading(false);
    }
  };

  const applyPreset = (preset: Preset) => {
    setSector(preset.filters.sector ?? ANY);
    setIndustry(preset.filters.industry ?? ANY);
    setMarketCapMoreThan(preset.filters.marketCapMoreThan != null ? String(preset.filters.marketCapMoreThan) : "");
    setPriceMoreThan("");
    setPriceLowerThan("");
    setBetaLowerThan(preset.filters.betaLowerThan != null ? String(preset.filters.betaLowerThan) : "");
    setDividendMoreThan(preset.filters.dividendMoreThan != null ? String(preset.filters.dividendMoreThan) : "");
    setActivelyTradingOnly(preset.filters.isActivelyTrading ?? true);
    setExcludeFunds(preset.filters.excludeFunds ?? true);
    void runScreener(preset.filters);
  };

  const toggleSelected = (symbol: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  };

  const quickTrade = (symbol: string) => {
    navigate(`/paper-broker?quickTradeSymbol=${encodeURIComponent(symbol)}`);
  };

  const sendToStrategyScan = () => {
    if (selected.size === 0) return;
    navigate(`/paper-broker?scanSymbols=${encodeURIComponent([...selected].join(","))}`);
  };

  return (
    <div className="screen">
      <h1 className="screen-title">Symbol Screener</h1>
      <p className="screen-sub">
        Search or filter FMP's full symbol universe by sector, industry, market cap, price, beta,
        or dividend yield — independent of your tracked watchlist. Send a discovered symbol
        straight to Paper Broker's Quick Trade, or a whole selection to its Strategy Scan.
      </p>

      <TabGuide tabKey="symbol-screener" />

      {/* Free-text search */}
      <section className="card card-pad" style={{ marginBottom: "var(--s-4)" }}>
        <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-3) 0" }}>Search by name or ticker</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void runSearch();
          }}
          style={{ display: "flex", gap: "var(--s-2)", alignItems: "flex-end", marginBottom: "var(--s-3)" }}
        >
          <div style={{ flex: 1 }}>
            <Input
              label="Company name or ticker"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="e.g. Apple, or AAPL"
            />
          </div>
          <Button type="submit" variant="primary" pending={searchLoading}>
            Search
          </Button>
        </form>

        {searchError && <EmptyState title="Search failed" hint={searchError} />}
        {!searchError && searchReason && searchResults.length === 0 && (
          <EmptyState title="No matches" hint={searchReason} />
        )}
        {searchResults.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <Table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Name</th>
                  <th>Exchange</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {searchResults.map((r) => (
                  <tr key={r.symbol}>
                    <td style={{ fontWeight: 600 }}>{r.symbol}</td>
                    <td>{r.name ?? "—"}</td>
                    <td>{r.exchange ?? "—"}</td>
                    <td>
                      <Button onClick={() => quickTrade(r.symbol)}>Quick Trade →</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </section>

      {/* Sector/industry/market-cap screener */}
      <section className="card card-pad">
        <h2 style={{ fontSize: "var(--t-subhead)", margin: "0 0 var(--s-3) 0" }}>Filter by sector, industry & more</h2>

        <div style={{ display: "flex", gap: "var(--s-2)", flexWrap: "wrap", marginBottom: "var(--s-3)" }}>
          {PRESETS.map((preset) => (
            <Button key={preset.label} onClick={() => applyPreset(preset)} title={preset.hint}>
              {preset.label}
            </Button>
          ))}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
            gap: "var(--s-3)",
            marginBottom: "var(--s-3)",
          }}
        >
          <Select
            label="Sector"
            value={sector}
            onChange={(e) => setSector(e.target.value)}
            options={[{ value: ANY, label: "Any sector" }, ...filterOptions.sectors.map((s) => ({ value: s, label: s }))]}
          />
          <Select
            label="Industry"
            value={industry}
            onChange={(e) => setIndustry(e.target.value)}
            options={[{ value: ANY, label: "Any industry" }, ...filterOptions.industries.map((s) => ({ value: s, label: s }))]}
          />
          <Input
            label="Market cap ≥ ($)"
            type="number"
            value={marketCapMoreThan}
            onChange={(e) => setMarketCapMoreThan(e.target.value)}
            placeholder="e.g. 10000000000"
          />
          <Input
            label="Price ≥ ($)"
            type="number"
            value={priceMoreThan}
            onChange={(e) => setPriceMoreThan(e.target.value)}
            placeholder="e.g. 10"
          />
          <Input
            label="Price ≤ ($)"
            type="number"
            value={priceLowerThan}
            onChange={(e) => setPriceLowerThan(e.target.value)}
            placeholder="e.g. 500"
          />
          <Input
            label="Beta ≤"
            type="number"
            value={betaLowerThan}
            onChange={(e) => setBetaLowerThan(e.target.value)}
            placeholder="e.g. 1.2"
          />
          <Input
            label="Dividend yield ≥ ($/yr)"
            type="number"
            value={dividendMoreThan}
            onChange={(e) => setDividendMoreThan(e.target.value)}
            placeholder="e.g. 2"
          />
        </div>

        <div style={{ display: "flex", gap: "var(--s-4)", alignItems: "center", marginBottom: "var(--s-3)" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)", fontSize: "var(--t-callout)" }}>
            <input
              type="checkbox"
              checked={activelyTradingOnly}
              onChange={(e) => setActivelyTradingOnly(e.target.checked)}
            />
            Actively trading only
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "var(--s-1-5)", fontSize: "var(--t-callout)" }}>
            <input type="checkbox" checked={excludeFunds} onChange={(e) => setExcludeFunds(e.target.checked)} />
            Exclude ETFs / funds
          </label>
          <Button variant="primary" onClick={() => runScreener()} pending={screenerLoading}>
            Apply filters
          </Button>
        </div>

        {screenerLoading && <Loading lines={3} />}
        {!screenerLoading && screenerError && <EmptyState title="Screener query failed" hint={screenerError} />}
        {!screenerLoading && !screenerError && screenerRan && screenerReason && screenerResults.length === 0 && (
          <EmptyState title="No symbols matched" hint={screenerReason} />
        )}
        {!screenerLoading && !screenerError && screenerResults.length > 0 && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "var(--s-2)" }}>
              <span style={{ fontSize: "var(--t-caption)", color: theme.textSecondary }}>
                {screenerResults.length} result{screenerResults.length === 1 ? "" : "s"}
              </span>
              <Button
                variant="primary"
                disabled={selected.size === 0}
                onClick={sendToStrategyScan}
                data-testid="send-to-strategy-scan"
              >
                Send {selected.size || ""} to Strategy Scan
              </Button>
            </div>
            <div style={{ overflowX: "auto" }}>
              <Table>
                <thead>
                  <tr>
                    <th></th>
                    <th>Symbol</th>
                    <th>Company</th>
                    <th>Sector</th>
                    <th>Industry</th>
                    <th className="num">Mkt Cap</th>
                    <th className="num">Price</th>
                    <th className="num">Beta</th>
                    <th className="num">Div/yr</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {screenerResults.map((r) => (
                    <tr key={r.symbol}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected.has(r.symbol)}
                          onChange={() => toggleSelected(r.symbol)}
                          aria-label={`Select ${r.symbol}`}
                        />
                      </td>
                      <td style={{ fontWeight: 600 }}>{r.symbol}</td>
                      <td>{r.company_name ?? "—"}</td>
                      <td>{r.sector ?? "—"}</td>
                      <td>{r.industry ?? "—"}</td>
                      <td className="num">{r.market_cap != null ? fmtUsd(r.market_cap, { compact: true }) : "—"}</td>
                      <td className="num">{r.price != null ? fmtUsd(r.price) : "—"}</td>
                      <td className="num">{r.beta != null ? fmtNum(r.beta, 2) : "—"}</td>
                      <td className="num">{r.last_annual_dividend != null ? fmtUsd(r.last_annual_dividend) : "—"}</td>
                      <td>
                        <Button onClick={() => quickTrade(r.symbol)}>Quick Trade →</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

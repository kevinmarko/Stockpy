import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { FixGatewayStatusRadar } from "./FixGatewayStatusRadar";
import { api } from "../../api/client";
import type {
  FixSessionStatusResponse,
  FixSessionControlResponse,
} from "../../api/types";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getFixSessionStatus: vi.fn(),
      sendFixTestRequest: vi.fn(),
      resetFixSequence: vi.fn(),
      reconnectFixSession: vi.fn(),
    },
  };
});

const mockFixStatusData: FixSessionStatusResponse = {
  session_id: "FIX.4.4:INVESTYO_PWA->FIX_GATEWAY",
  state: "ACTIVE",
  in_seq_num: 1048,
  out_seq_num: 1049,
  sender_comp_id: "INVESTYO_PWA",
  target_comp_id: "FIX_GATEWAY",
  gap_queue_depth: 0,
  last_heartbeat_at: "2026-08-17T21:45:00.000Z",
  venues_active: ["NYSE", "NASDAQ", "BATS", "IEX", "ARCA"],
  heartbeat_int: 30,
  session_uptime_sec: 14820,
  venue_stats: [
    {
      venue: "NYSE",
      market_center: "New York Stock Exchange",
      status: "ACTIVE",
      base_latency_ms: 1.1,
      current_latency_ms: 1.14,
      fill_rate_pct: 99.4,
      maker_fee: 0.0012,
      taker_fee: 0.003,
      maker_rebate: 0.002,
      liquidity_depth: 125000,
      share_of_flow_pct: 34.2,
    },
    {
      venue: "NASDAQ",
      market_center: "Nasdaq Stock Market",
      status: "ACTIVE",
      base_latency_ms: 0.9,
      current_latency_ms: 0.95,
      fill_rate_pct: 99.8,
      maker_fee: 0.0015,
      taker_fee: 0.003,
      maker_rebate: 0.0025,
      liquidity_depth: 140000,
      share_of_flow_pct: 38.5,
    },
    {
      venue: "BATS",
      market_center: "Cboe BZX Exchange",
      status: "ACTIVE",
      base_latency_ms: 0.7,
      current_latency_ms: 0.72,
      fill_rate_pct: 98.9,
      maker_fee: -0.002,
      taker_fee: 0.0025,
      maker_rebate: 0.002,
      liquidity_depth: 65000,
      share_of_flow_pct: 12.1,
    },
    {
      venue: "IEX",
      market_center: "Investors Exchange (D-Limit)",
      status: "ACTIVE",
      base_latency_ms: 1.8,
      current_latency_ms: 1.85,
      fill_rate_pct: 97.5,
      maker_fee: 0.0,
      taker_fee: 0.0009,
      maker_rebate: 0.0,
      liquidity_depth: 45000,
      share_of_flow_pct: 6.8,
    },
    {
      venue: "ARCA",
      market_center: "NYSE Arca Equities",
      status: "ACTIVE",
      base_latency_ms: 1.2,
      current_latency_ms: 1.23,
      fill_rate_pct: 99.1,
      maker_fee: -0.0022,
      taker_fee: 0.0028,
      maker_rebate: 0.0022,
      liquidity_depth: 85000,
      share_of_flow_pct: 8.4,
    },
  ],
  audit_log: [
    "8=FIX.4.4|9=112|35=0|49=FIX_GATEWAY|56=INVESTYO_PWA|34=1048|52=20260817-21:45:00.120|10=092|",
    "8=FIX.4.4|9=128|35=8|49=FIX_GATEWAY|56=INVESTYO_PWA|34=1047|52=20260817-21:44:58.330|37=ORD-99124|11=CL-3019|39=2|150=2|55=SPY|54=1|38=100|44=512.50|32=100|31=512.48|14=100|6=512.48|10=184|",
    "8=FIX.4.4|9=108|35=1|49=INVESTYO_PWA|56=FIX_GATEWAY|34=1048|52=20260817-21:44:30.010|112=TEST-9921|10=210|",
    "8=FIX.4.4|9=140|35=D|49=INVESTYO_PWA|56=FIX_GATEWAY|34=1047|52=20260817-21:44:00.000|11=CL-3019|55=SPY|54=1|38=100|40=2|44=512.50|59=0|10=156|",
    "8=FIX.4.4|9=100|35=4|49=INVESTYO_PWA|56=FIX_GATEWAY|34=1046|52=20260817-21:43:00.000|36=1047|123=Y|10=088|",
  ],
};

describe("FixGatewayStatusRadar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getFixSessionStatus as any).mockResolvedValue(mockFixStatusData);
    (api.sendFixTestRequest as any).mockResolvedValue({
      status: "ok",
      message: "FIX Test Request (35=1) verified. Heartbeat response received.",
      session_state: "ACTIVE",
      round_trip_ms: 1.18,
    } as FixSessionControlResponse);
    (api.resetFixSequence as any).mockResolvedValue({
      status: "ok",
      message: "Sequence reset (35=4) to seq #2000 successful.",
      session_state: "ACTIVE",
      new_seq_num: 2000,
    } as FixSessionControlResponse);
    (api.reconnectFixSession as any).mockResolvedValue({
      status: "ok",
      message: "FIX 4.4 Session re-established successfully.",
      session_state: "ACTIVE",
    } as FixSessionControlResponse);
  });

  it("renders FIX session status, sequence gauges, and session identifiers", async () => {
    render(<FixGatewayStatusRadar />);

    expect(screen.getByText(/FIX 4.4 Gateway & Routing Radar/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/ACTIVE \(SYNCHRONIZED\)/i)).toBeInTheDocument();
      expect(screen.getByText(/FIX\.4\.4:INVESTYO_PWA->FIX_GATEWAY/i)).toBeInTheDocument();
      expect(screen.getByText("#1,048")).toBeInTheDocument();
      expect(screen.getByText("#1,049")).toBeInTheDocument();
      expect(screen.getByText(/0 msgs \(Clean\)/i)).toBeInTheDocument();
    });
  });

  it("triggers Send Test Request (35=1) administrative action and displays latency confirmation", async () => {
    render(<FixGatewayStatusRadar />);

    await waitFor(() => {
      expect(screen.getByText(/Send Test Request \(35=1\)/i)).toBeInTheDocument();
    });

    const testReqButton = screen.getByRole("button", { name: /Send FIX Test Request 35=1/i });
    fireEvent.click(testReqButton);

    await waitFor(() => {
      expect(api.sendFixTestRequest).toHaveBeenCalledTimes(1);
      expect(
        screen.getByText(/Test Request \(35=1\) verified\. Heartbeat response received in 1\.18 ms\./i)
      ).toBeInTheDocument();
    });
  });

  it("opens sequence reset modal and triggers Reset Sequence (35=4) action", async () => {
    render(<FixGatewayStatusRadar />);

    await waitFor(() => {
      expect(screen.getByText(/Reset Sequence \(35=4\)/i)).toBeInTheDocument();
    });

    const openResetBtn = screen.getByRole("button", { name: /Open FIX Sequence Reset Dialog/i });
    fireEvent.click(openResetBtn);

    // Dialog should appear
    expect(screen.getByText(/Operator Sequence Reset \(MsgType 35=4\)/i)).toBeInTheDocument();

    const seqInput = screen.getByLabelText(/New Sequence Number/i);
    fireEvent.change(seqInput, { target: { value: "2000" } });

    const confirmBtn = screen.getByRole("button", { name: /Confirm Reset/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.resetFixSequence).toHaveBeenCalledWith({
        new_seq_num: 2000,
        gap_fill: false,
      });
      expect(screen.getByText(/Sequence reset \(35=4\) to #2000 applied\./i)).toBeInTheDocument();
    });
  });

  it("triggers Reconnect Session administrative action", async () => {
    render(<FixGatewayStatusRadar />);

    await waitFor(() => {
      expect(screen.getByText(/Reconnect Session/i)).toBeInTheDocument();
    });

    const reconnectBtn = screen.getByRole("button", { name: /Reconnect FIX Session/i });
    fireEvent.click(reconnectBtn);

    await waitFor(() => {
      expect(api.reconnectFixSession).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/FIX 4\.4 Session reconnected and synchronized\./i)).toBeInTheDocument();
    });
  });

  it("renders multi-venue routing radar market centers and fee economics", async () => {
    render(<FixGatewayStatusRadar />);

    await waitFor(() => {
      expect(screen.getByText(/Multi-Venue Execution Routing Radar/i)).toBeInTheDocument();
      expect(screen.getByText("NYSE")).toBeInTheDocument();
      expect(screen.getByText("NASDAQ")).toBeInTheDocument();
      expect(screen.getByText("BATS")).toBeInTheDocument();
      expect(screen.getByText("IEX")).toBeInTheDocument();
      expect(screen.getByText("ARCA")).toBeInTheDocument();
      expect(screen.getByText("99.8%")).toBeInTheDocument();
      expect(screen.getByText("99.4%")).toBeInTheDocument();
    });

    // Clicking a venue inspects it
    const nasdaqCard = screen.getByRole("button", { name: /Inspect Venue NASDAQ/i });
    fireEvent.click(nasdaqCard);
  });

  it("renders raw FIX 4.4 audit log with tag syntax highlighting and filters by message type", async () => {
    render(<FixGatewayStatusRadar />);

    await waitFor(() => {
      expect(screen.getByText(/Raw FIX 4\.4 Audit Log Viewer/i)).toBeInTheDocument();
      expect(screen.getByText("5 events")).toBeInTheDocument();
    });

    // Test filter chips
    const execReportChip = screen.getByRole("button", { name: "EXEC_REPORT" });
    fireEvent.click(execReportChip);

    // Search query filter
    const searchInput = screen.getByPlaceholderText(/Search tag/i);
    fireEvent.change(searchInput, { target: { value: "ORD-99124" } });

    await waitFor(() => {
      expect(screen.getByText("ORD-99124")).toBeInTheDocument();
    });
  });
});

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MultiBrokerGatewayView } from "./MultiBrokerGatewayView";
import { api } from "../../api/client";
import type { MultiBrokerStatusResponse } from "../../api/types";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMultiBrokerStatus: vi.fn(),
      triggerBrokerFailover: vi.fn(),
    },
  };
});

const mockGatewayData: MultiBrokerStatusResponse = {
  active_broker_id: "alpaca",
  manual_override_broker_id: null,
  priority_hierarchy: ["alpaca", "interactive_brokers", "tradier", "fmp_paper"],
  brokers: {
    alpaca: {
      broker_id: "alpaca",
      broker_type: "alpaca",
      connection_state: "connected",
      circuit_state: "closed",
      is_healthy: true,
      is_routable: true,
      latency_ms: 24.5,
      avg_latency_ms: 26.0,
      p95_latency_ms: 42.0,
      error_rate: 0.001,
      consecutive_failures: 0,
      status_message: "Alpaca REST/WS healthy, primary routing operational.",
    },
    interactive_brokers: {
      broker_id: "interactive_brokers",
      broker_type: "interactive_brokers",
      connection_state: "connected",
      circuit_state: "closed",
      is_healthy: true,
      is_routable: true,
      latency_ms: 38.0,
      avg_latency_ms: 40.0,
      p95_latency_ms: 68.0,
      error_rate: 0.004,
      consecutive_failures: 0,
      status_message: "TWS Gateway v10.19 connected.",
    },
    robinhood: {
      broker_id: "robinhood",
      broker_type: "robinhood",
      connection_state: "degraded",
      circuit_state: "half_open",
      is_healthy: false,
      is_routable: false,
      latency_ms: 182.0,
      avg_latency_ms: 165.0,
      p95_latency_ms: 340.0,
      error_rate: 0.12,
      consecutive_failures: 2,
      status_message: "Degraded latency probe. Canary half-open recovery active.",
    },
  },
  total_orders_routed: 14250,
  total_failovers: 2,
  last_failover_time: "2026-08-15T14:20:00Z",
  last_failover_reason: "High latency detected on primary adapter; automated failover to Alpaca.",
  recent_routing_audits: [
    {
      client_order_id: "ord_d89f2a01",
      symbol: "SPY",
      side: "BUY",
      qty: 100,
      primary_broker_id: "alpaca",
      executed_broker_id: "alpaca",
      was_failover: false,
      total_latency_ms: 23.4,
      final_status: "FILLED",
      timestamp: "2026-08-15T14:25:00Z",
    },
    {
      client_order_id: "ord_c44b9102",
      symbol: "QQQ",
      side: "SELL",
      qty: 50,
      primary_broker_id: "robinhood",
      executed_broker_id: "alpaca",
      was_failover: true,
      total_latency_ms: 88.2,
      final_status: "FILLED",
      failover_reason: "high_latency (>150ms)",
      timestamp: "2026-08-15T14:15:00Z",
    },
  ],
};

describe("MultiBrokerGatewayView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders gateway overview, brokers, and telemetry table", async () => {
    vi.mocked(api.getMultiBrokerStatus).mockResolvedValue(mockGatewayData);

    render(<MultiBrokerGatewayView />);

    await waitFor(() => {
      expect(screen.getByText("Unified Multi-Broker Execution Gateway")).toBeInTheDocument();
    });

    expect(screen.getAllByText("ALPACA").length).toBeGreaterThan(0);
    expect(screen.getByText("14,250")).toBeInTheDocument();
    expect(screen.getByText("INTERACTIVE_BROKERS")).toBeInTheDocument();
    expect(screen.getAllByText("ROBINHOOD").length).toBeGreaterThan(0);
    expect(screen.getByText("HALF-OPEN (CANARY PROBE)")).toBeInTheDocument();
    expect(screen.getByText("ord_d89f2a01")).toBeInTheDocument();
    expect(screen.getByText("ord_c44b9102")).toBeInTheDocument();
  });

  it("handles manual failover trigger successfully", async () => {
    const onFailoverMock = vi.fn();
    vi.mocked(api.getMultiBrokerStatus).mockResolvedValue(mockGatewayData);
    vi.mocked(api.triggerBrokerFailover).mockResolvedValueOnce({
      status: "ok",
      active_broker: "interactive_brokers",
      manual_override: "interactive_brokers",
      reason: "Manual switch",
      timestamp: "2026-08-15T14:30:00Z",
    });

    render(<MultiBrokerGatewayView onFailoverSuccess={onFailoverMock} />);

    await waitFor(() => {
      expect(screen.getByText("Unified Multi-Broker Execution Gateway")).toBeInTheDocument();
    });

    const routeBtns = screen.getAllByRole("button", { name: /Route Primary Traffic Here/i });
    fireEvent.click(routeBtns[0]);

    await waitFor(() => {
      expect(api.triggerBrokerFailover).toHaveBeenCalledWith({
        target_broker: "interactive_brokers",
        reason: "Manual override route request",
      });
      expect(onFailoverMock).toHaveBeenCalledWith("interactive_brokers");
    });
  });

  it("handles API error state", async () => {
    vi.mocked(api.getMultiBrokerStatus).mockRejectedValueOnce(new Error("Gateway unreachable"));

    render(<MultiBrokerGatewayView />);

    await waitFor(() => {
      expect(screen.getByText(/Gateway unreachable/i)).toBeInTheDocument();
    });
  });
});

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import Experiments from "./Experiments";
import { useApi } from "../hooks/useApi";
import { ExperimentsResponse } from "../api/types";

vi.mock("../hooks/useApi", () => ({
  useApi: vi.fn(),
}));

describe("Experiments Screen", () => {
  it("renders loading state", () => {
    vi.mocked(useApi).mockReturnValue({
      data: null,
      loading: true,
      error: null,
      status: null,
      reload: vi.fn(),
      stale: false,
      cachedAt: null,
    });
    render(<Experiments />);
    expect(document.querySelector(".skeleton")).toBeInTheDocument();
  });

  it("renders error state (non-404)", () => {
    vi.mocked(useApi).mockReturnValue({
      data: null,
      loading: false,
      error: "Failed to fetch",
      status: 500,
      reload: vi.fn(),
      stale: false,
      cachedAt: null,
    });
    render(<Experiments />);
    expect(screen.getByText(/Failed to fetch/)).toBeInTheDocument();
  });

  it("renders empty state on 404 (honest cold-start)", () => {
    vi.mocked(useApi).mockReturnValue({
      data: null,
      loading: false,
      error: "Not Found",
      status: 404,
      reload: vi.fn(),
      stale: false,
      cachedAt: null,
    });
    render(<Experiments />);
    expect(screen.getByText(/No experiments are currently configured/)).toBeInTheDocument();
  });

  it("renders empty state when data is empty", () => {
    vi.mocked(useApi).mockReturnValue({
      data: { experiments: [] },
      loading: false,
      error: null,
      status: 200,
      reload: vi.fn(),
      stale: false,
      cachedAt: null,
    });
    render(<Experiments />);
    expect(screen.getByText(/No experiments are currently configured/)).toBeInTheDocument();
  });

  it("renders insufficient_data state (honest branch)", () => {
    const mockData: ExperimentsResponse = {
      experiments: [
        {
          id: "exp-2",
          name: "Test Exp",
          description: "Desc",
          state: "insufficient_data",
          arms: [{ id: "arm-1", name: "Arm 1", weight: 100 }],
          comparisons: null,
          reason: "Not enough trades.",
          created_at: "2026-08-22T00:00:00Z",
          updated_at: null,
        }
      ]
    };
    vi.mocked(useApi).mockReturnValue({
      data: mockData,
      loading: false,
      error: null,
      status: 200,
      reload: vi.fn(),
      stale: false,
      cachedAt: null,
    });
    render(<Experiments />);
    expect(screen.getByText("Test Exp")).toBeInTheDocument();
    expect(screen.getByText("Not enough trades.")).toBeInTheDocument();
    expect(screen.queryByText("Results")).not.toBeInTheDocument();
  });

  it("renders happy path with comparisons and null significance", () => {
    const mockData: ExperimentsResponse = {
      experiments: [
        {
          id: "exp-1",
          name: "Happy Exp",
          description: "Desc",
          state: "running",
          arms: [{ id: "arm-1", name: "Arm 1", weight: 100 }],
          comparisons: [
            {
              metric_name: "Sharpe",
              control_value: 1.0,
              treatment_value: 1.2,
              relative_delta_pct: 20,
              p_value: null,
              significant: null,
            }
          ],
          reason: null,
          created_at: "2026-08-22T00:00:00Z",
          updated_at: null,
        }
      ]
    };
    vi.mocked(useApi).mockReturnValue({
      data: mockData,
      loading: false,
      error: null,
      status: 200,
      reload: vi.fn(),
      stale: false,
      cachedAt: null,
    });
    render(<Experiments />);
    expect(screen.getByText("Happy Exp")).toBeInTheDocument();
    expect(screen.getByText("Sharpe")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

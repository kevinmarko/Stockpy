import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { WeeklyDigestCard } from "./WeeklyDigestCard";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: {
    getWeeklyDigest: vi.fn(),
  },
}));

describe("WeeklyDigestCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading state initially", () => {
    vi.mocked(api.getWeeklyDigest).mockReturnValue(new Promise(() => {}));
    render(<WeeklyDigestCard />);
    expect(screen.getByText("This Week's Digest")).toBeInTheDocument();
  });

  it("renders entries when data loads", async () => {
    vi.mocked(api.getWeeklyDigest).mockResolvedValue([
      { symbol: "AAPL", reason: "Value buy", type: "Personalized" }
    ]);
    render(<WeeklyDigestCard />);
    
    await waitFor(() => {
      expect(screen.getByText("AAPL")).toBeInTheDocument();
    });
    expect(screen.getByText("Personalized")).toBeInTheDocument();
    expect(screen.getByText("Value buy")).toBeInTheDocument();
  });

  it("renders error state when fetch fails", async () => {
    vi.mocked(api.getWeeklyDigest).mockRejectedValue(new Error("Network error"));
    render(<WeeklyDigestCard />);
    
    await waitFor(() => {
      expect(screen.getByText(/Failed to load weekly digest/)).toBeInTheDocument();
    });
  });

  it("renders empty state when data is empty", async () => {
    vi.mocked(api.getWeeklyDigest).mockResolvedValue([]);
    render(<WeeklyDigestCard />);
    
    await waitFor(() => {
      expect(screen.getByText("No digest available.")).toBeInTheDocument();
    });
  });
});

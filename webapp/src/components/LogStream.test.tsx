/**
 * LogStream.test.tsx — the SSE log console used by Console.tsx. In the test
 * environment USE_MOCK is true (matching every other test's convention), so
 * this only exercises the mock-mode placeholder and static controls; the
 * real EventSource wiring is exercised manually against a live backend (see
 * this repo's manual verification steps for the Console screen).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LogStream } from "./LogStream";

describe("LogStream", () => {
  it("shows the mock-mode placeholder instead of attempting a live connection", () => {
    render(<LogStream jobId="job-1" isStreaming />);
    expect(
      screen.getByText("Log streaming is only available in live mode.")
    ).toBeInTheDocument();
  });

  it("renders the filter input, auto-scroll toggle, and clear button", () => {
    render(<LogStream jobId="job-1" isStreaming />);
    expect(screen.getByPlaceholderText("Filter logs...")).toBeInTheDocument();
    expect(screen.getByText("Auto-scroll")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("renders the idle state when no job is active", () => {
    render(<LogStream />);
    expect(
      screen.getByText("Log streaming is only available in live mode.")
    ).toBeInTheDocument();
  });
});

/**
 * ErrorBoundary.test.tsx — proves an uncaught render error is contained to
 * the fallback UI instead of unmounting the whole tree (the "goes black"
 * failure mode this component exists to bound -- see its own docstring and
 * PipelineDashboard.test.tsx's regression test for the bug that motivated
 * it).
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Bomb(): never {
  throw new Error("kaboom");
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    // React logs the caught error to console.error twice (its own internal
    // log plus this component's componentDidCatch) -- expected noise for
    // this test, not a real failure.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders children normally when nothing throws", () => {
    render(
      <ErrorBoundary>
        <div>fine</div>
      </ErrorBoundary>
    );
    expect(screen.getByText("fine")).toBeInTheDocument();
  });

  it("catches a render error and shows the fallback with the real error message", () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>
    );
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("kaboom")).toBeInTheDocument();
  });
});

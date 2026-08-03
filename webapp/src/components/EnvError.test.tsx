/**
 * EnvError.test.tsx
 *
 * Note what is deliberately NOT here: no `vi.mock("../api/client", ...)`.
 * EnvError has to be renderable when the app's configuration is broken, which
 * means it must not (even transitively) pull in api/client. Rendering it with
 * `fetch` stubbed to a throwing spy proves both that it needs no API layer and
 * that it performs no network work of its own.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { EnvError } from "./EnvError";
import type { EnvIssue } from "../config/env";

const ISSUES: EnvIssue[] = [
  {
    key: "VITE_USE_MOCK",
    severity: "error",
    message:
      'VITE_USE_MOCK must be one of: true, false, 1, 0, yes, no, on, off (case-insensitive). Got "maybe".',
  },
  {
    key: "VITE_API_BASE_URL",
    severity: "error",
    message: "VITE_API_BASE_URL must use the http: or https: protocol.",
  },
  {
    key: "VITE_API_TOKEN",
    severity: "warning",
    message: "VITE_API_TOKEN is empty while running live against a loopback host.",
  },
];

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("EnvError", () => {
  it("renders every error issue with its key and message", () => {
    render(<EnvError issues={ISSUES} />);

    expect(screen.getByText("VITE_USE_MOCK")).toBeInTheDocument();
    expect(screen.getByText(/must be one of: true, false/)).toBeInTheDocument();
    expect(screen.getByText("VITE_API_BASE_URL")).toBeInTheDocument();
    expect(
      screen.getByText(/must use the http: or https: protocol/)
    ).toBeInTheDocument();
  });

  it("renders warning issues too, under their own heading", () => {
    render(<EnvError issues={ISSUES} />);

    expect(screen.getByText("VITE_API_TOKEN")).toBeInTheDocument();
    expect(
      screen.getByText(/empty while running live against a loopback host/)
    ).toBeInTheDocument();
    expect(screen.getByText(/Also worth checking/i)).toBeInTheDocument();
  });

  it("tells the operator to edit .env.local and restart — and offers no retry button", () => {
    render(<EnvError issues={ISSUES} />);

    expect(screen.getByText(/\.env\.local/)).toBeInTheDocument();
    expect(screen.getByText(/npm run dev/)).toBeInTheDocument();
    // Reloading cannot pick up a .env change (Vite reads env files only at
    // startup), so a retry control would be dishonest.
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders with only warnings and no errors", () => {
    render(<EnvError issues={[ISSUES[2]]} />);
    expect(screen.getByText("VITE_API_TOKEN")).toBeInTheDocument();
    expect(screen.queryByText(/Also worth checking/i)).toBeInTheDocument();
  });

  it("renders an empty issue list without crashing", () => {
    render(<EnvError issues={[]} />);
    expect(screen.getByText(/Configuration error/i)).toBeInTheDocument();
    // The warnings section is omitted entirely when there are none.
    expect(screen.queryByText(/Also worth checking/i)).toBeNull();
  });

  it("performs no network work — fetch is never called", () => {
    const fetchSpy = vi.fn(() => {
      throw new Error("EnvError must not touch the network");
    });
    vi.stubGlobal("fetch", fetchSpy);

    render(<EnvError issues={ISSUES} />);

    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

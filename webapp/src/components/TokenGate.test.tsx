/**
 * TokenGate.test.tsx — the non-loopback token-entry prompt.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TokenGate } from "./TokenGate";
import { getStoredToken } from "../auth/apiToken";

describe("TokenGate", () => {
  let reloadSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    sessionStorage.clear();
    reloadSpy = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload: reloadSpy },
      writable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a token input and a disabled submit button until something is typed", () => {
    render(<TokenGate />);
    expect(screen.getByPlaceholderText("API token")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });

  it("storing a token and reloading on submit", () => {
    render(<TokenGate />);
    fireEvent.change(screen.getByPlaceholderText("API token"), {
      target: { value: "my-secret-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(getStoredToken()).toBe("my-secret-token");
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it("does not submit a blank/whitespace-only token", () => {
    render(<TokenGate />);
    fireEvent.change(screen.getByPlaceholderText("API token"), {
      target: { value: "   " },
    });
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    expect(getStoredToken()).toBe("");
    expect(reloadSpy).not.toHaveBeenCalled();
  });
});

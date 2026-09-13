/**
 * SearchDefaultToggle.test.tsx — the operator-facing control for
 * `SearchDefaultContext`'s `universeFirst` preference. Renders inside a real
 * `SearchDefaultProvider` (not a mock of `useSearchDefault`) so a click
 * exercises the actual context state transition, not just a spy call.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SearchDefaultToggle } from "./SearchDefaultToggle";
import { SearchDefaultProvider } from "../context/SearchDefaultContext";

function renderWith(initialUniverseFirst: boolean) {
  return render(
    <SearchDefaultProvider initialUniverseFirst={initialUniverseFirst}>
      <SearchDefaultToggle />
    </SearchDefaultProvider>
  );
}

describe("SearchDefaultToggle", () => {
  it("starting universeFirst=true: switch is on and labeled for the new default", () => {
    renderWith(true);
    const el = screen.getByRole("switch", { name: "Search any stock first (default)" });
    expect(el).toHaveAttribute("aria-checked", "true");
    expect(
      screen.getByText(/leads with results from the whole market/i)
    ).toBeInTheDocument();
  });

  it("starting universeFirst=false: switch is off and labeled for the legacy/rollback behavior", () => {
    renderWith(false);
    const el = screen.getByRole("switch", { name: "Search your saved list first (legacy)" });
    expect(el).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByText(/exactly like it did before September 2026/i)
    ).toBeInTheDocument();
  });

  it("clicking from universeFirst=true flips the real context state to false -- label, aria-checked, and description all update", async () => {
    const user = userEvent.setup();
    renderWith(true);

    await user.click(screen.getByTestId("search-default-toggle"));

    const el = screen.getByRole("switch", { name: "Search your saved list first (legacy)" });
    expect(el).toHaveAttribute("aria-checked", "false");
    expect(
      screen.getByText(/exactly like it did before September 2026/i)
    ).toBeInTheDocument();
  });

  it("clicking from universeFirst=false flips the real context state to true", async () => {
    const user = userEvent.setup();
    renderWith(false);

    await user.click(screen.getByTestId("search-default-toggle"));

    const el = screen.getByRole("switch", { name: "Search any stock first (default)" });
    expect(el).toHaveAttribute("aria-checked", "true");
  });

  it("persists the flipped preference via SearchDefaultContext's localStorage key", async () => {
    const user = userEvent.setup();
    renderWith(true);

    await user.click(screen.getByTestId("search-default-toggle"));

    expect(localStorage.getItem("stockpy_search_universe_first")).toBe("false");
  });
});

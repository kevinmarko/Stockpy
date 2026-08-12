/**
 * BottomNavigation.test.tsx
 *
 * Covers the one thing that makes Create Data App's saved views real
 * instead of decorative: a saved custom view actually shows up in both the
 * desktop Sidebar and the mobile "More" modal, live (no reload), and
 * disappears the same way on delete. useNavItems() (navigation.tsx) is the
 * hook under test here, exercised through the real Sidebar/BottomNav
 * components rather than in isolation, since the point is that the nav
 * surfaces themselves pick it up.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import { Sidebar, BottomNav } from "./BottomNavigation";
import { __resetCustomViewsForTests, addOrUpdateView, removeView, type CustomViewWidgets } from "../customViews";

function widgets(overrides: Partial<CustomViewWidgets> = {}): CustomViewWidgets {
  return {
    edgeByStrategy: false,
    symbolOverlay: false,
    aiChat: false,
    pilotsTable: false,
    sentimentMini: false,
    portfolioHeat: false,
    optionsDirective: false,
    signalBreakdown: false,
    macroRegime: false,
    ...overrides,
  };
}

function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar />
    </MemoryRouter>
  );
}

beforeEach(() => {
  __resetCustomViewsForTests();
});

describe("Sidebar / BottomNav dynamic nav items", () => {
  it("a saved custom view appears in the desktop Sidebar with no reload", () => {
    const { rerender } = renderSidebar();
    expect(screen.queryByText("My Custom App")).not.toBeInTheDocument();

    addOrUpdateView({
      name: "My Custom App",
      widgets: widgets({ edgeByStrategy: true }),
    });

    rerender(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    );
    expect(screen.getByText("My Custom App")).toBeInTheDocument();
  });

  it("deleting a saved view removes it from the Sidebar", () => {
    const { view } = addOrUpdateView({
      name: "Gone Soon",
      widgets: widgets({ edgeByStrategy: true }),
    });
    const { rerender } = renderSidebar();
    expect(screen.getByText("Gone Soon")).toBeInTheDocument();

    removeView(view.id);
    rerender(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    );
    expect(screen.queryByText("Gone Soon")).not.toBeInTheDocument();
  });

  it("a saved custom view is reachable on mobile via the 'More' modal", async () => {
    const user = userEvent.setup();
    addOrUpdateView({
      name: "Mobile Reachable",
      widgets: widgets({ edgeByStrategy: true }),
    });
    render(
      <MemoryRouter>
        <BottomNav />
      </MemoryRouter>
    );

    await user.click(screen.getByTestId("more-nav-button"));
    expect(await screen.findByText("Mobile Reachable")).toBeInTheDocument();
  });
});

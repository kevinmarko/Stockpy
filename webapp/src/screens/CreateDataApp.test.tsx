/**
 * CreateDataApp.test.tsx
 *
 * Covers real validation (empty name / zero widgets selected both block
 * creation), that creating a view actually navigates to its real /app/:slug
 * page (not a decorative success message), and full CRUD on the existing-
 * views list -- the surface PR #670's original stub never had.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";
import { CreateDataApp } from "./CreateDataApp";
import { __resetCustomViewsForTests, addOrUpdateView, type CustomViewWidgets } from "../customViews";

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

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location-probe">{loc.pathname}</div>;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/create-data-app"]}>
      <Routes>
        <Route
          path="/create-data-app"
          element={
            <>
              <CreateDataApp />
              <LocationProbe />
            </>
          }
        />
        <Route
          path="/app/:slug"
          element={<LocationProbe />}
        />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  __resetCustomViewsForTests();
});

describe("CreateDataApp screen", () => {
  it("create is disabled with an empty name", async () => {
    renderScreen();
    expect(screen.getByTestId("create-data-app-submit")).toBeDisabled();
  });

  it("create is disabled when no widgets are selected", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.type(screen.getByLabelText("Name"), "My View");
    // All three widgets default checked -- uncheck all of them.
    await user.click(screen.getByTestId("widget-toggle-edgeByStrategy"));
    await user.click(screen.getByTestId("widget-toggle-symbolOverlay"));
    await user.click(screen.getByTestId("widget-toggle-aiChat"));

    expect(screen.getByTestId("create-data-app-submit")).toBeDisabled();
    expect(screen.getByText("Pick at least one widget.")).toBeInTheDocument();
  });

  it("creating a view persists it and navigates to its real /app/:slug page", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.type(screen.getByLabelText("Name"), "Momentum Desk");
    await user.click(screen.getByTestId("create-data-app-submit"));

    expect(await screen.findByTestId("location-probe")).toHaveTextContent("/app/momentum-desk");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(1);
    expect(raw[0].name).toBe("Momentum Desk");
  });

  it("lists existing saved views with Open/Delete controls", async () => {
    const user = userEvent.setup();
    addOrUpdateView({
      name: "Existing View",
      widgets: widgets({ edgeByStrategy: true }),
    });
    renderScreen();

    const row = await screen.findByTestId("data-app-row-existing-view");
    expect(within(row).getByText("Existing View")).toBeInTheDocument();
    expect(within(row).getByText("Edge-by-strategy chart")).toBeInTheDocument();

    await user.click(screen.getByTestId("data-app-open-existing-view"));
    expect(await screen.findByTestId("location-probe")).toHaveTextContent("/app/existing-view");
  });

  it("deleting an existing view removes it from the list and from storage", async () => {
    const user = userEvent.setup();
    addOrUpdateView({ name: "Doomed View", widgets: widgets({ edgeByStrategy: true }) });
    renderScreen();

    expect(await screen.findByTestId("data-app-row-doomed-view")).toBeInTheDocument();
    await user.click(screen.getByTestId("data-app-delete-doomed-view"));

    expect(screen.queryByTestId("data-app-row-doomed-view")).not.toBeInTheDocument();
    expect(screen.getByText("No Data Apps yet")).toBeInTheDocument();
    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(0);
  });

  it("REGRESSION (review finding): unchecking a single widget checkbox saves exactly that partial widgets object, not a wrong key", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.type(screen.getByLabelText("Name"), "Partial Widgets");
    // Defaults are edgeByStrategy/symbolOverlay/aiChat ON, the rest OFF --
    // uncheck only aiChat and confirm exactly that one flips, through the
    // real UI checkbox (not a direct addOrUpdateView call), which is the
    // only thing that can catch a checkbox wired to the wrong widget key.
    await user.click(screen.getByTestId("widget-toggle-aiChat"));
    await user.click(screen.getByTestId("create-data-app-submit"));
    await screen.findByTestId("location-probe");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(1);
    expect(raw[0].widgets).toEqual(
      widgets({ edgeByStrategy: true, symbolOverlay: true, aiChat: false })
    );
  });

  it("saving again with the same name updates the existing view instead of duplicating", async () => {
    const user = userEvent.setup();
    const first = renderScreen();

    await user.type(screen.getByLabelText("Name"), "Repeat View");
    await user.click(screen.getByTestId("create-data-app-submit"));
    await screen.findByTestId("location-probe");
    first.unmount();

    renderScreen();
    await user.type(screen.getByLabelText("Name"), "Repeat View");
    await user.click(screen.getByTestId("create-data-app-submit"));
    await screen.findByTestId("location-probe");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(1);
  });
});

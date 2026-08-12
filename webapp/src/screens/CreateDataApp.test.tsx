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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CreateDataApp } from "./CreateDataApp";
import { __resetCustomViewsForTests, addOrUpdateView, type CustomViewWidgets } from "../customViews";

// Two tests below (a FileReader failure, and the stale-edit-refresh-on-import
// notice) need to inspect toast calls -- no `<Toaster />` is mounted in this
// tree (matching every other screen test in this suite), so the real
// react-hot-toast module pushes into an unrendered internal store with
// nothing to assert against. Mocked module-wide here the same way
// GenericSettingsEditor.test.tsx does for the same reason; every OTHER test
// in this file only cares that a save/delete/import succeeded (checked via
// localStorage / the rendered list), never toast copy, so this is safe to
// apply file-wide.
vi.mock("react-hot-toast", () => {
  const fn = vi.fn() as any;
  fn.success = vi.fn();
  fn.error = vi.fn();
  return { default: fn };
});
import toast from "react-hot-toast";

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
  vi.mocked(toast).mockClear();
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
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

  it("applying a template fills the form and produces the template's own widgetOrder on save", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.click(screen.getByTestId("use-template-risk-management-view"));
    expect(screen.getByLabelText("Name")).toHaveValue("Risk Management View");

    await user.click(screen.getByTestId("create-data-app-submit"));
    await screen.findByTestId("location-probe");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(1);
    expect(raw[0].widgetOrder).toEqual(["macroRegime", "portfolioHeat", "optionsDirective"]);
    expect(raw[0].widgets).toEqual(
      widgets({ macroRegime: true, portfolioHeat: true, optionsDirective: true })
    );
  });

  it("Edit loads an existing view into the form and Update saves in place instead of duplicating", async () => {
    const user = userEvent.setup();
    addOrUpdateView({ name: "Editable View", widgets: widgets({ edgeByStrategy: true }) });
    renderScreen();

    await user.click(await screen.findByTestId("data-app-edit-editable-view"));
    expect(screen.getByLabelText("Name")).toHaveValue("Editable View");
    expect(screen.getByTestId("create-data-app-submit")).toHaveTextContent("Update & save to sidebar");

    await user.click(screen.getByTestId("widget-toggle-macroRegime"));
    await user.click(screen.getByTestId("create-data-app-submit"));
    await screen.findByTestId("location-probe");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(1); // updated in place, not a second row
    expect(raw[0].widgets.edgeByStrategy).toBe(true);
    expect(raw[0].widgets.macroRegime).toBe(true);
  });

  it("REGRESSION: applying a template while mid-edit of an existing view creates a NEW view rather than silently overwriting the view being edited", async () => {
    const user = userEvent.setup();
    addOrUpdateView({ name: "My Original", widgets: widgets({ edgeByStrategy: true }) });
    renderScreen();

    await user.click(await screen.findByTestId("data-app-edit-my-original"));
    expect(screen.getByTestId("create-data-app-submit")).toHaveTextContent("Update & save to sidebar");

    await user.click(screen.getByTestId("use-template-sentiment-overview"));
    // Applying a template must discard the in-progress edit -- the submit
    // button reverts to "Create", not "Update".
    expect(screen.getByTestId("create-data-app-submit")).toHaveTextContent("Create & save to sidebar");

    await user.click(screen.getByTestId("create-data-app-submit"));
    await screen.findByTestId("location-probe");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(2);
    expect(raw.find((v: any) => v.name === "My Original")).toBeTruthy();
    expect(raw.find((v: any) => v.name === "Sentiment Overview")).toBeTruthy();
  });

  it("Duplicate creates a second, independent view with a '- Copy' suffix and its own id", async () => {
    const user = userEvent.setup();
    addOrUpdateView({ name: "Source View", widgets: widgets({ edgeByStrategy: true }) });
    renderScreen();

    await user.click(await screen.findByTestId("data-app-duplicate-source-view"));

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(2);
    const original = raw.find((v: any) => v.name === "Source View");
    const copy = raw.find((v: any) => v.name === "Source View - Copy");
    expect(copy).toBeTruthy();
    expect(copy.id).not.toBe(original.id);
    expect(copy.widgets).toEqual(original.widgets);
  });

  it("REGRESSION (review finding): clicking Duplicate a second time on the same source view produces a THIRD, independent view instead of overwriting the first copy", async () => {
    const user = userEvent.setup();
    addOrUpdateView({ name: "Source View", widgets: widgets({ edgeByStrategy: true }) });
    renderScreen();

    await user.click(await screen.findByTestId("data-app-duplicate-source-view"));
    await user.click(await screen.findByTestId("data-app-duplicate-source-view"));

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    // Source + first copy + second copy -- NOT two rows because the second
    // click's generated name collided with (and overwrote) the first.
    expect(raw).toHaveLength(3);
    const names = raw.map((v: any) => v.name).sort();
    expect(names).toEqual(["Source View", "Source View - Copy", "Source View - Copy 2"]);
    // All three are genuinely distinct rows, not the same row renamed twice.
    expect(new Set(raw.map((v: any) => v.id)).size).toBe(3);
  });

  it("the accessible Move up/down buttons reorder widgets, and the new order is what gets saved", async () => {
    const user = userEvent.setup();
    renderScreen();

    await user.type(screen.getByLabelText("Name"), "Reordered");
    // Defaults on: edgeByStrategy, symbolOverlay, aiChat -- in that order.
    expect(screen.getByTestId("widget-move-up-edgeByStrategy")).toBeDisabled();
    await user.click(screen.getByTestId("widget-move-down-edgeByStrategy"));

    await user.click(screen.getByTestId("create-data-app-submit"));
    await screen.findByTestId("location-probe");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw[0].widgetOrder).toEqual(["symbolOverlay", "edgeByStrategy", "aiChat"]);
  });

  it("Import JSON reads a dropped file and adds the view(s) it contains to the list", async () => {
    const user = userEvent.setup();
    renderScreen();

    const payload = JSON.stringify([
      {
        id: "foreign-id",
        name: "Imported View",
        slug: "imported-view",
        widgets: widgets({ macroRegime: true }),
        widgetOrder: ["macroRegime"],
      },
    ]);
    const file = new File([payload], "export.json", { type: "application/json" });
    await user.upload(screen.getByTestId("data-app-import-file-input"), file);

    expect(await screen.findByTestId("data-app-row-imported-view")).toBeInTheDocument();
    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw.some((v: any) => v.slug === "imported-view")).toBe(true);
    // Never trusts the foreign file's own id for a brand-new view.
    expect(raw.find((v: any) => v.slug === "imported-view").id).not.toBe("foreign-id");
  });

  it("REGRESSION (review finding): a FileReader failure while importing shows an error toast and resets the file input so the same file can be re-selected", async () => {
    const user = userEvent.setup();
    renderScreen();

    // Minimal FileReader test double that always fails asynchronously --
    // simulates a real read error (disk/permission issue), which
    // `handleImport` previously had no `onerror` handler for at all.
    const originalFileReader = window.FileReader;
    class FailingFileReader {
      onerror: ((this: FileReader, ev: any) => any) | null = null;
      onload: ((this: FileReader, ev: any) => any) | null = null;
      result: string | null = null;
      readAsText() {
        setTimeout(() => this.onerror?.call(this as any, new ProgressEvent("error")), 0);
      }
    }
    // @ts-expect-error -- deliberately swapping in a minimal test double for this one test
    window.FileReader = FailingFileReader;

    try {
      const input = screen.getByTestId("data-app-import-file-input") as HTMLInputElement;
      const file = new File(["irrelevant"], "export.json", { type: "application/json" });
      await user.upload(input, file);

      await vi.waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith("Failed to read the file. Please try again.");
      });
      // Reset so the operator can retry, including re-selecting the exact
      // same file (a browser won't fire `change` for the same file twice
      // unless the input's value was cleared first).
      expect(input.value).toBe("");
      // Nothing was imported.
      expect(localStorage.getItem("stockpy.custom-views:v1")).toBeNull();
    } finally {
      window.FileReader = originalFileReader;
    }
  });

  it("REGRESSION (review finding): importing an update to the view currently open for editing refreshes the in-form state instead of leaving it stale", async () => {
    const user = userEvent.setup();
    addOrUpdateView({ name: "Editable View", widgets: widgets({ edgeByStrategy: true }) });
    renderScreen();

    await user.click(await screen.findByTestId("data-app-edit-editable-view"));
    expect(screen.getByTestId("create-data-app-submit")).toHaveTextContent("Update & save to sidebar");

    // An import lands for the SAME view (same slug -> same id preserved by
    // importViews) while it's still open in the editor, enabling a widget
    // the in-form state doesn't know about.
    const payload = JSON.stringify([
      {
        name: "Editable View",
        widgets: widgets({ edgeByStrategy: true, macroRegime: true }),
        widgetOrder: ["edgeByStrategy", "macroRegime"],
      },
    ]);
    const file = new File([payload], "export.json", { type: "application/json" });
    await user.upload(screen.getByTestId("data-app-import-file-input"), file);

    // The editor picked up the import instead of staying stale.
    await vi.waitFor(() => {
      expect(screen.getByTestId("widget-toggle-macroRegime")).toBeChecked();
    });
    expect(toast).toHaveBeenCalledWith(
      expect.stringContaining("was just updated by this import"),
      expect.anything()
    );

    // Saving now does NOT clobber the import with the pre-import (stale)
    // widgets -- it saves the refreshed state.
    await user.click(screen.getByTestId("create-data-app-submit"));
    await screen.findByTestId("location-probe");

    const raw = JSON.parse(localStorage.getItem("stockpy.custom-views:v1") as string);
    expect(raw).toHaveLength(1);
    expect(raw[0].widgets.macroRegime).toBe(true);
  });

  describe("Export", () => {
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;

    afterEach(() => {
      URL.createObjectURL = originalCreateObjectURL;
      URL.revokeObjectURL = originalRevokeObjectURL;
    });

    it("builds and revokes a downloadable JSON blob for the given view", async () => {
      const user = userEvent.setup();
      addOrUpdateView({ name: "Exportable", widgets: widgets({ edgeByStrategy: true }) });
      renderScreen();

      const createObjectURL = vi.fn(() => "blob:mock-url");
      const revokeObjectURL = vi.fn();
      URL.createObjectURL = createObjectURL as any;
      URL.revokeObjectURL = revokeObjectURL as any;

      await user.click(await screen.findByTestId("data-app-export-exportable"));

      expect(createObjectURL).toHaveBeenCalledTimes(1);
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    });
  });
});

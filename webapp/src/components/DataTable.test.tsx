/**
 * DataTable.test.tsx — the reusable filterable/sortable/groupable table.
 * Was entirely unused anywhere in the app before this fix (dead code); now
 * wired into Console.tsx's job history. Covers filtering, sorting, grouping,
 * and the empty state, since nothing else in the app tests it yet.
 */
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DataTable, type Column } from "./DataTable";
import { DensityProvider } from "./DensityContext";

interface Row {
  id: string;
  name: string;
  group: string;
  value: number;
}

const ROWS: Row[] = [
  { id: "1", name: "Zebra", group: "A", value: 3 },
  { id: "2", name: "Apple", group: "B", value: 1 },
  { id: "3", name: "Mango", group: "A", value: 2 },
];

const COLUMNS: Column<Row>[] = [
  { key: "name", header: "Name" },
  { key: "group", header: "Group" },
  { key: "value", header: "Value" },
];

function renderTable(props: Partial<React.ComponentProps<typeof DataTable<Row>>> = {}) {
  return render(
    <DensityProvider>
      <DataTable data={ROWS} columns={COLUMNS} {...props} />
    </DensityProvider>
  );
}

describe("DataTable", () => {
  const originalClipboard = navigator.clipboard;

  afterEach(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      writable: true,
      configurable: true,
    });
    // vi.spyOn on an already-spied method (e.g. a second test's
    // `vi.spyOn(window, "alert")`) returns the SAME mock instance with its
    // prior calls still recorded, not a fresh one -- restore real
    // implementations between tests so each alert spy starts clean.
    vi.restoreAllMocks();
  });

  it("renders every row's cells", () => {
    renderTable();
    expect(screen.getByText("Zebra")).toBeInTheDocument();
    expect(screen.getByText("Apple")).toBeInTheDocument();
    expect(screen.getByText("Mango")).toBeInTheDocument();
    expect(screen.getByText("Showing 3 records")).toBeInTheDocument();
  });

  it("filters rows by the search input across all fields", () => {
    renderTable();
    fireEvent.change(screen.getByPlaceholderText("Filter data..."), { target: { value: "man" } });
    expect(screen.getByText("Mango")).toBeInTheDocument();
    expect(screen.queryByText("Zebra")).not.toBeInTheDocument();
    expect(screen.queryByText("Apple")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1 records")).toBeInTheDocument();
  });

  it("shows the empty state when the filter matches nothing", () => {
    renderTable();
    fireEvent.change(screen.getByPlaceholderText("Filter data..."), { target: { value: "nonexistent" } });
    expect(screen.getByText("No matching records found.")).toBeInTheDocument();
  });

  it("sorts by a clicked column header, toggling direction on repeat clicks", () => {
    renderTable();
    const nameHeader = screen.getByText(/^Name/);
    fireEvent.click(nameHeader);
    // Ascending by name: Apple, Mango, Zebra.
    let cells = screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
    expect(cells).toEqual(["Apple", "Mango", "Zebra"]);

    fireEvent.click(screen.getByText(/^Name/));
    cells = screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
    expect(cells).toEqual(["Zebra", "Mango", "Apple"]);
  });

  it("groups rows by the given key with a collapsible group header", () => {
    renderTable({ groupByKey: "group" });
    const groupARow = screen.getAllByRole("row").find((r) => r.textContent?.includes("A (2 events)"));
    expect(groupARow).toBeTruthy();
    expect(screen.getByText("Zebra")).toBeInTheDocument();

    fireEvent.click(groupARow!);
    expect(groupARow!.textContent).toContain("▶");
    expect(screen.queryByText("Zebra")).not.toBeInTheDocument();
    // Group B is untouched.
    expect(screen.getByText("Apple")).toBeInTheDocument();
  });

  it("copies a row as JSON to the clipboard and alerts on success", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    renderTable();

    fireEvent.click(screen.getAllByText("JSON")[0]);
    expect(writeText).toHaveBeenCalledWith(JSON.stringify(ROWS[0], null, 2));
    await waitFor(() => expect(alertSpy).toHaveBeenCalledWith("Copied event context as JSON to clipboard!"));
  });

  it("a rejected clipboard write does NOT alert success and leaves no unhandled rejection", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    renderTable();

    fireEvent.click(screen.getAllByText("JSON")[0]);
    expect(writeText).toHaveBeenCalledWith(JSON.stringify(ROWS[0], null, 2));
    // Flush the rejected microtask; the success alert must never fire.
    await Promise.resolve().then(() => Promise.resolve());
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it("uses a custom render function for a column when provided", () => {
    renderTable({
      columns: [
        { key: "name", header: "Name" },
        { key: "value", header: "Value", render: (row) => `#${row.value}` },
      ],
    });
    expect(screen.getByText("#3")).toBeInTheDocument();
  });

  it("shows the leaf-row count, not inflated by group-header rows, when grouped", () => {
    // 3 leaf rows across 2 groups (A x2, B x1). Before the fix this read
    // "Showing 5 records" -- the 3 leaf rows plus the 2 group-header rows
    // that TanStack's flattened, expanded row model also includes.
    renderTable({ groupByKey: "group" });
    expect(screen.getByText("Showing 3 records")).toBeInTheDocument();
  });

  describe("column pinning", () => {
    const PINNABLE_COLUMNS: Column<Row>[] = [
      { key: "name", header: "Name", pinnable: true },
      { key: "group", header: "Group" },
      { key: "value", header: "Value" },
    ];

    it("shows a pin control for a pinnable column and toggles pinned state on click", () => {
      renderTable({ columns: PINNABLE_COLUMNS });

      const pinButton = screen.getByRole("button", { name: "Pin column" });
      expect(pinButton).toBeInTheDocument();

      fireEvent.click(pinButton);
      expect(screen.getByRole("button", { name: "Unpin column" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Pin column" })).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "Unpin column" }));
      expect(screen.getByRole("button", { name: "Pin column" })).toBeInTheDocument();
    });

    it("shows no pin control for a column that isn't marked pinnable", () => {
      renderTable();
      expect(screen.queryByRole("button", { name: "Pin column" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Unpin column" })).not.toBeInTheDocument();
    });

    it("keeps sorting and filtering working correctly once a column is pinned", () => {
      renderTable({ columns: PINNABLE_COLUMNS });

      fireEvent.click(screen.getByRole("button", { name: "Pin column" }));

      // Sorting by the now-pinned Name column still works.
      fireEvent.click(screen.getByText(/^Name/));
      let cells = screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
      expect(cells).toEqual(["Apple", "Mango", "Zebra"]);

      fireEvent.click(screen.getByText(/^Name/));
      cells = screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);
      expect(cells).toEqual(["Zebra", "Mango", "Apple"]);

      // Filtering (on a different column's value) still works too.
      fireEvent.change(screen.getByPlaceholderText("Filter data..."), { target: { value: "man" } });
      expect(screen.getByText("Mango")).toBeInTheDocument();
      expect(screen.queryByText("Zebra")).not.toBeInTheDocument();
      expect(screen.queryByText("Apple")).not.toBeInTheDocument();
    });
  });
});

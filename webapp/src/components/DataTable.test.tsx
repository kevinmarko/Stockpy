/**
 * DataTable.test.tsx — the reusable filterable/sortable/groupable table.
 * Was entirely unused anywhere in the app before this fix (dead code); now
 * wired into Console.tsx's job history. Covers filtering, sorting, grouping,
 * and the empty state, since nothing else in the app tests it yet.
 */
import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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

  it("copies a row as JSON to the clipboard", () => {
    const writeText = vi.fn();
    Object.assign(navigator, { clipboard: { writeText } });
    vi.spyOn(window, "alert").mockImplementation(() => {});
    renderTable();

    fireEvent.click(screen.getAllByText("JSON")[0]);
    expect(writeText).toHaveBeenCalledWith(JSON.stringify(ROWS[0], null, 2));
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
});

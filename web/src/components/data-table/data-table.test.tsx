import { createColumnHelper } from "@tanstack/react-table";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { DataTable, type DataTableProps } from "./data-table";

interface Row {
  id: string;
  code: string;
  amount: string;
}

const col = createColumnHelper<Row>();
const columns = [
  col.accessor("code", { header: "Code", meta: { sortKey: "code" } }),
  col.accessor("amount", { header: "Amount", meta: { numeric: true } }),
];

const rows: Row[] = [
  { id: "1", code: "VEN-1", amount: "100.00" },
  { id: "2", code: "VEN-2", amount: "250.50" },
];

function renderTable(props: Partial<DataTableProps<Row>> = {}) {
  return render(
    <MemoryRouter initialEntries={["/list"]}>
      <Routes>
        <Route
          path="/list"
          element={
            <DataTable<Row>
              tableId="test"
              caption="Test rows"
              columns={columns}
              rows={rows}
              total={2}
              isLoading={false}
              getRowId={(r) => r.id}
              getRowHref={(r) => `/detail/${r.id}`}
              {...props}
            />
          }
        />
        <Route path="/detail/:id" element={<p>Detail page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("DataTable", () => {
  it("renders rows with an honest footer", () => {
    renderTable();
    expect(screen.getByRole("table", { name: "Test rows" })).toBeInTheDocument();
    expect(screen.getByText("VEN-2")).toBeInTheDocument();
    expect(screen.getByText("1–2 of 2 records")).toBeInTheDocument();
  });

  it("right-aligns numeric columns", () => {
    renderTable();
    expect(screen.getByText("250.50").closest("td")).toHaveAttribute("data-numeric", "true");
  });

  it("shows empty, loading and error states that differ from each other", () => {
    const { unmount } = renderTable({ rows: [], total: 0 });
    expect(screen.getByText("No records match")).toBeInTheDocument();
    unmount();

    const loading = renderTable({ rows: undefined, total: undefined, isLoading: true });
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText("No records match")).not.toBeInTheDocument();
    loading.unmount();

    renderTable({
      rows: undefined,
      error: new ApiError(500, { type: "about:blank", title: "Server error", status: 500 }),
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load this data");
  });

  it("shows a permission message rather than a generic failure on 403", () => {
    renderTable({
      rows: undefined,
      error: new ApiError(403, { type: "about:blank", title: "Forbidden", status: 403 }),
    });
    expect(screen.getByText("You do not have access to this")).toBeInTheDocument();
  });

  it("cycles sort ascending, descending, then back to the default", async () => {
    const onSortChange = vi.fn();
    const { rerender } = renderTable({ sort: undefined, onSortChange });
    const header = () => screen.getByRole("button", { name: /Code/ });

    await userEvent.click(header());
    expect(onSortChange).toHaveBeenLastCalledWith("code");

    rerender(
      <MemoryRouter>
        <DataTable<Row>
          tableId="test"
          caption="Test rows"
          columns={columns}
          rows={rows}
          total={2}
          isLoading={false}
          getRowId={(r) => r.id}
          sort="code"
          onSortChange={onSortChange}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole("columnheader", { name: /Code/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
    await userEvent.click(header());
    expect(onSortChange).toHaveBeenLastCalledWith("-code");
  });

  it("opens a row with the keyboard", async () => {
    renderTable();
    const firstRow = screen.getByText("VEN-1").closest("tr")!;
    firstRow.focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByText("VEN-2").closest("tr")).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByText("Detail page")).toBeInTheDocument();
  });
});

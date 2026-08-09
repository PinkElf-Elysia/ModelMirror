import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import XlsxDestinationChooser, { isXlsxFile } from "./XlsxDestinationChooser";

describe("XlsxDestinationChooser", () => {
  it("recognizes XLSX by extension without treating similar names as workbooks", () => {
    expect(isXlsxFile({ name: "预算.XLSX" })).toBe(true);
    expect(isXlsxFile({ name: "预算.xlsx.txt" })).toBe(false);
  });

  it("executes only the current module and exposes real routes for other uses", () => {
    const onUseCurrent = vi.fn();
    const onNavigate = vi.fn();
    render(
      <MemoryRouter>
        <XlsxDestinationChooser
          currentDestination="rag"
          fileName="预算.xlsx"
          onCancel={vi.fn()}
          onNavigate={onNavigate}
          onUseCurrent={onUseCurrent}
        />
      </MemoryRouter>,
    );

    const currentAction = screen.getByRole("button", { name: "加入资料库" });
    expect(currentAction).toHaveFocus();
    fireEvent.click(currentAction);
    expect(onUseCurrent).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "与模型讨论" })).toHaveAttribute(
      "href",
      "/models",
    );
    expect(screen.getByRole("link", { name: "用 Data X 分析" })).toHaveAttribute(
      "href",
      "/datax",
    );
    expect(screen.getByText(/目标页面重新选择/)).toBeVisible();
    fireEvent.click(screen.getByRole("link", { name: "与模型讨论" }));
    expect(onNavigate).toHaveBeenCalledWith("chat");
  });
});

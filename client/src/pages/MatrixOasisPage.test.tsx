import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MatrixOasisPage from "./MatrixOasisPage";
vi.mock("../hooks/useStudioPanelUrls", () => ({ useStudioPanelUrls: () => ({ matrixOasisConsoleUrl: "http://127.0.0.1:43110/" }) }));
const assign = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  const original = window;
  vi.stubGlobal("window", new Proxy(original, { get(target, key) {
    if (key === "location") return { assign };
    if (key === "matchMedia") return () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    return Reflect.get(target, key);
  } }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); assign.mockClear(); });
describe("Matrix Oasis original entrance", () => {
  it("preserves the entrance animation before opening Creator", () => {
    render(<MemoryRouter><MatrixOasisPage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "进入矩阵绿洲" }));
    expect(screen.getByRole("button", { name: "跳过" })).toBeVisible();
    expect(assign).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(3200));
    expect(assign).toHaveBeenCalledExactlyOnceWith("http://127.0.0.1:43110/");
  });
  it("opens the same Creator when skipping the animation", () => {
    render(<MemoryRouter><MatrixOasisPage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "进入矩阵绿洲" }));
    fireEvent.click(screen.getByRole("button", { name: "跳过" }));
    expect(assign).toHaveBeenCalledExactlyOnceWith("http://127.0.0.1:43110/");
  });
});

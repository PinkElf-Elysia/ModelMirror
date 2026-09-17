import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import StudioBetaPanels, { resolvePanelUrl } from "./StudioBetaPanels";
afterEach(() => vi.unstubAllGlobals());
describe("Studio Beta panel entrances", () => {
  it("uses safe runtime URLs and keeps the teaser separate from Creator", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ scienceConsoleUrl: "https://research.example/console/", matrixOasisConsoleUrl: "http://127.0.0.1:43116/" }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><StudioBetaPanels /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("link", { name: "打开 Science 控制面板（新标签页）" })).toHaveAttribute("href", "https://research.example/console/"));
    expect(screen.getByRole("link", { name: "打开 矩阵绿洲 控制面板（新标签页）" })).toHaveAttribute("href", "http://127.0.0.1:43116/");
    expect(screen.getByRole("link", { name: "查看预告" })).toHaveAttribute("href", "/matrix-oasis");
    for (const link of screen.getAllByRole("link", { name: /打开.*控制面板/ })) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/runtime-config.json");
  });
  it("retains documented local entrances when runtime config is unavailable", () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<MemoryRouter><StudioBetaPanels /></MemoryRouter>);
    expect(screen.getAllByText("Beta")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "打开 Science 控制面板（新标签页）" })).toHaveAttribute("href", "http://127.0.0.1:8900/");
    expect(screen.getByRole("link", { name: "打开 矩阵绿洲 控制面板（新标签页）" })).toHaveAttribute("href", "http://127.0.0.1:43110/");
  });
  it("rejects executable, credential-bearing and query-bearing URLs", () => {
    for (const value of ["javascript:alert(1)", "file:///tmp/a", "https://user:secret@example.test/", "https://example.test/?token=x", "https://example.test/#secret", {}, null]) expect(resolvePanelUrl(value, "fallback")).toBe("fallback");
  });
});

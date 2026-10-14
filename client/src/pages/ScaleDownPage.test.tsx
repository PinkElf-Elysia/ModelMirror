import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ScaleDownPage, { parseEntities } from "./ScaleDownPage";

vi.mock("../components/ResourceNav", () => ({ default: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function mount(id = "scaledown/summarize") {
  render(<MemoryRouter initialEntries={[`/scaledown/${encodeURIComponent(id)}`]}><Routes><Route path="/scaledown/:modelId" element={<ScaleDownPage />} /></Routes></MemoryRouter>);
}
describe("ScaleDown text workbench", () => {
  it("validates bounded field maps", () => {
    for (const source of ["[]", "null", "{}", '{"x": 2}', '{"x": ""}', '{"": "name"}', "bad"]) expect(parseEntities(source)).toBeNull();
    expect(parseEntities('{"person":"Person name"}')).toEqual({ person: "Person name" });
  });
  it("submits summary without entities and renders escaped result", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ content: "<script>not executable</script>" }) });
    vi.stubGlobal("fetch", fetchMock);
    mount();
    expect(screen.getByRole("button", { name: "生成摘要" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("原文"), { target: { value: "Public text" } });
    fireEvent.click(screen.getByRole("button", { name: "生成摘要" }));
    await screen.findByText("<script>not executable</script>");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ model: "scaledown/summarize", text: "Public text", instructions: "", max_tokens: 1000 });
    expect(document.querySelector("script")).toBeNull();
  });
  it("requires extraction fields and shows server failure without retry", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 504, json: async () => ({ error: "结果未知；不会自动重试。" }) });
    vi.stubGlobal("fetch", fetchMock);
    mount("scaledown/extract");
    fireEvent.change(screen.getByLabelText("原文"), { target: { value: "Public text" } });
    fireEvent.change(screen.getByLabelText("提取字段（JSON）"), { target: { value: "{}" } });
    expect(screen.getByRole("button", { name: "提取字段" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("提取字段（JSON）"), { target: { value: '{"person":"Person name"}' } });
    fireEvent.click(screen.getByRole("button", { name: "提取字段" }));
    await screen.findByRole("alert");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).entities).toEqual({ person: "Person name" });
  });
});

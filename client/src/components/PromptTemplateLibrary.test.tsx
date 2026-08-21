import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import PromptTemplateLibrary from "./PromptTemplateLibrary";

function LocationProbe() {
  const location = useLocation();
  return <output>{`${location.pathname}${location.search}`}</output>;
}

describe("PromptTemplateLibrary", () => {
  afterEach(() => vi.restoreAllMocks());

  it("creates a one-shot draft and navigates without sending", async () => {
    render(
      <MemoryRouter initialEntries={["/prompts"]}>
        <Routes>
          <Route element={<><PromptTemplateLibrary /><LocationProbe /></>} path="*" />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: /使用前选择/i })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /使用前选择/i }));
    fireEvent.click(screen.getAllByRole("option", { name: /GPT-5.6 Sol/i })[0]);
    fireEvent.click(screen.getAllByRole("button", { name: "用于对话" })[0]);
    expect(screen.getByText(/\/chat\/openai%2Fgpt-5.6-sol\?prompt_draft=/)).toBeInTheDocument();
  }, 15_000);

  it("keeps curated targets selectable without a runtime catalog request", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<MemoryRouter><PromptTemplateLibrary /></MemoryRouter>);
    const trigger = screen.getByRole("button", { name: /使用前选择/i });
    expect(trigger).toBeEnabled();
    fireEvent.click(trigger);
    expect(screen.getAllByRole("option", { name: /GPT-5.6 Sol/i })[0]).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("closes the model picker with Escape and restores trigger focus", async () => {
    render(<MemoryRouter><PromptTemplateLibrary /></MemoryRouter>);
    const trigger = screen.getByRole("button", { name: /使用前选择/i });
    fireEvent.click(trigger);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});

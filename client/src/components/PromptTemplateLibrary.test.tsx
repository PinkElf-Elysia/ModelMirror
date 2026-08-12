import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      models: [{ profile_id: "openai/gpt-5.6-sol", invocation_id: "openai/gpt-5.6-sol", invocable: true }],
      routes: [],
    }), { status: 200 }));
    render(
      <MemoryRouter initialEntries={["/prompts"]}>
        <Routes>
          <Route element={<><PromptTemplateLibrary /><LocationProbe /></>} path="*" />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("button", { name: /使用前选择/i })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: /使用前选择/i }));
    fireEvent.click(screen.getByRole("option", { name: /GPT-5.6 Sol/i }));
    fireEvent.click(screen.getAllByRole("button", { name: "用于对话" })[0]);
    expect(screen.getByText(/\/chat\/openai%2Fgpt-5.6-sol\?prompt_draft=/)).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  }, 15_000);

  it("fails closed when the live catalog is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 503 }));
    render(<MemoryRouter><PromptTemplateLibrary /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole("button", { name: /实时目录不可用/i })).toBeDisabled());
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("closes the model picker with Escape and restores trigger focus", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      models: [{ profile_id: "openai/gpt-5.6-sol", invocation_id: "openai/gpt-5.6-sol", invocable: true }],
      routes: [],
    }), { status: 200 }));
    render(<MemoryRouter><PromptTemplateLibrary /></MemoryRouter>);
    const trigger = await screen.findByRole("button", { name: /使用前选择/i });
    fireEvent.click(trigger);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});

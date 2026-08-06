import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentWorkbenchPage from "./AgentWorkbenchPage";


function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}


afterEach(() => {
  vi.unstubAllGlobals();
});


describe("AgentWorkbenchPage", () => {
  it("lists the idempotent General Agent when the feature is enabled", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/status")) {
          return jsonResponse({
            enabled: true,
            version: "agent-workspace-r1",
            runtime_enabled: false,
          });
        }
        if (url.endsWith("/agents")) {
          return jsonResponse({
            agents: [
              {
                agent_id: "default_agent",
                name: "General Agent",
                description: "General-purpose native Agent.",
                version: 1,
                builtin: true,
                skill_count: 16,
                revision: "a".repeat(64),
              },
            ],
          });
        }
        return jsonResponse({ detail: "not found" }, 404);
      }),
    );

    render(
      <MemoryRouter>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "General Agent" })).toBeVisible();
    expect(screen.getByText("16 个 Skill 快照")).toBeVisible();
    expect(screen.getByText(/任务执行将在下一轮接入/)).toBeVisible();
  });

  it("shows a truthful disabled state and does not request Agent data", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      expect(String(input)).toContain("/status");
      return jsonResponse({
        enabled: false,
        version: "agent-workspace-r1",
        runtime_enabled: false,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <AgentWorkbenchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Agent 工作区已关闭")).toBeVisible();
    expect(screen.getByRole("button", { name: "新建 Agent" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

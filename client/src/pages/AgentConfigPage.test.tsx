import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentPayload, AgentToolConfig } from "../types/agentWorkspace";
import AgentConfigPage from "./AgentConfigPage";


const toolNames: AgentToolConfig["name"][] = [
  "read_file",
  "edit_file",
  "write_file",
  "exec_command",
  "input_command",
  "run_subagent",
  "input_subagent",
  "read_image",
  "describe_image",
];

function fixtureAgent(): AgentPayload {
  const tools = toolNames.map((name) => ({
    name,
    description: `${name} description`,
    parameters: { type: "object" },
    permission: name.includes("read") || name === "describe_image" ? "r" as const : "rw" as const,
    timeoutMs: 30000,
    maxOutputLength: name === "read_file" ? 64000 : 16000,
    call_description: name.includes("command") || name.includes("subagent"),
  }));
  const skill = {
    skill_id: "software-engineering",
    name: "Software Engineering",
    description: "Complete software engineering tasks.",
    status: "ready" as const,
    reason: "Ready.",
    digest: "b".repeat(64),
    source_url: "https://github.com/Prism-Shadow/penguin-harness",
    source_path: "packages/skills/skills/software-engineering",
    source_license: "Apache-2.0" as const,
    adapted: true,
  };
  return {
    agent_id: "default_agent",
    builtin: true,
    revision: "a".repeat(64),
    state_path: "agents/default_agent/agent_state",
    agents_md: "",
    skills: [skill],
    config: {
      version: 1,
      name: "General Agent",
      description: "General-purpose Agent.",
      system_prompt: "# Role\n{{AGENTS_MD}}\n{{SKILL_METADATA}}",
      max_turns: 100,
      model: { max_tokens: 32000, thinking_level: "medium", timeoutMs: 120000 },
      compaction: {
        max_context_length: 128000,
        max_session_turns: -1,
        mode: "summarize",
        prompt: "Summarize the task transcript.",
      },
      tools: { builtin: tools },
      skillset_id: "general-agent-default",
    },
  };
}

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/agents/workbench/agents/default_agent"]}>
      <Routes>
        <Route
          element={<AgentConfigPage />}
          path="/agents/workbench/agents/:agentId"
        />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});


describe("AgentConfigPage", () => {
  it("edits and saves AGENTS.md while keeping all five configuration tabs", async () => {
    const original = fixtureAgent();
    let savedBody: Record<string, unknown> | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/skills/library") {
          return jsonResponse({
            total: 1,
            skills: [
              {
                ...original.skills[0],
                available: true,
                availability_reason: "Ready.",
                inject_runtime: true,
              },
            ],
          });
        }
        if (init?.method === "PUT") {
          savedBody = JSON.parse(String(init.body));
          return jsonResponse({
            ...original,
            agents_md: (savedBody as { agents_md: string }).agents_md,
            revision: "c".repeat(64),
          });
        }
        return jsonResponse(original);
      }),
    );

    renderPage();
    expect(await screen.findByRole("heading", { name: "General Agent" })).toBeVisible();
    for (const label of ["概览", "Prompt", "运行参数", "工具", "技能"]) {
      expect(screen.getByRole("tab", { name: label })).toBeVisible();
    }

    await userEvent.click(screen.getByRole("tab", { name: "Prompt" }));
    const agentsMd = screen.getByLabelText("AGENTS.md（用户行为层）");
    await userEvent.type(agentsMd, "# Review files carefully");
    expect(screen.getByText("有未保存修改")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(savedBody).toMatchObject({ agents_md: "# Review files carefully" }),
    );
    expect(await screen.findByText(/Agent State 已保存/)).toBeVisible();
  });

  it("renders the exact nine configured tool names and skill capability", async () => {
    const original = fixtureAgent();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        String(input) === "/api/skills/library"
          ? jsonResponse({
              total: 1,
              skills: [
                {
                  ...original.skills[0],
                  available: true,
                  availability_reason: "Ready.",
                  inject_runtime: true,
                },
              ],
            })
          : jsonResponse(original),
      ),
    );
    renderPage();

    await screen.findByRole("heading", { name: "General Agent" });
    fireEvent.click(screen.getByRole("tab", { name: "工具" }));
    for (const name of toolNames) {
      expect(screen.getByText(name)).toBeVisible();
    }

    fireEvent.click(screen.getByRole("tab", { name: "技能" }));
    expect(screen.getByText("software-engineering")).toBeVisible();
    expect(screen.getByText("可注入")).toBeVisible();
    expect(screen.getByText("1 / 16")).toBeVisible();
  });

  it("shows a recoverable loading error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse({ detail: "Agent State is invalid" }, 400)),
    );
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Agent State is invalid");
    expect(screen.getByRole("button", { name: "重试" })).toBeVisible();
  });
});

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelPreferenceProvider } from "../context/ModelPreferenceContext";
import AgentWorkbenchPage from "./AgentWorkbenchPage";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

const agent = {
  agent_id: "default_agent",
  name: "General Agent",
  description: "General-purpose native Agent.",
  version: 1,
  builtin: true,
  skill_count: 16,
  revision: "a".repeat(64),
};

const session = {
  session_id: "session-1",
  agent_id: "default_agent",
  workspace_id: "workspace-1",
  title: "General Agent 会话",
  model_id: "openai/gpt-5.6-sol",
  thinking_level: "medium",
  approval_mode: "always-ask",
  skillset_id: "general-agent-default",
  status: "idle",
  parent_session_id: null,
  depth: 0,
  created_at: 1,
  updated_at: 1,
};

const defaultSkillset = {
  skillset_id: "general-agent-default",
  name: "General Agent Default",
  description: "16 built-in Skills",
  builtin: true,
  members: Array.from({ length: 16 }, (_, index) => ({
    skill_id: index === 0 ? "agent-creation" : `skill-${index}`,
    digest: "b".repeat(64),
  })),
  revision: "c".repeat(64),
};

function detail(overrides: Record<string, unknown> = {}) {
  return {
    session,
    messages: [],
    tasks: [],
    approvals: [],
    last_event_sequence: 0,
    ...overrides,
  };
}

class EventSourceStub {
  static instances: EventSourceStub[] = [];
  onerror: ((event: Event) => void) | null = null;
  listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();
  addEventListener = vi.fn(
    (type: string, listener: (event: MessageEvent<string>) => void) => {
      const current = this.listeners.get(type) ?? [];
      current.push(listener);
      this.listeners.set(type, current);
    },
  );
  close = vi.fn();

  constructor() {
    EventSourceStub.instances.push(this);
  }

  emit(type: string, payload: unknown) {
    const event = { data: JSON.stringify(payload) } as MessageEvent<string>;
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ModelPreferenceProvider>
        <AgentWorkbenchPage />
      </ModelPreferenceProvider>
    </MemoryRouter>,
  );
}

function runtimeFetch(input: RequestInfo | URL, init?: RequestInit) {
  const url = String(input);
  if (url.endsWith("/status")) {
    return jsonResponse({
      enabled: true,
      version: "agent-workspace-r2",
      runtime_enabled: true,
    });
  }
  if (url.endsWith("/agents") && !init?.method) {
    return jsonResponse({ agents: [agent] });
  }
  if (url.endsWith("/api/skills/skillsets")) {
    return jsonResponse({ skillsets: [defaultSkillset] });
  }
  if (url.endsWith("/sessions") && !init?.method) {
    return jsonResponse({ sessions: [session] });
  }
  if (url.includes("/sessions/session-1/subagents")) {
    return jsonResponse({ subagents: [] });
  }
  if (url.includes("/sessions/session-1/workspace?")) {
    return jsonResponse({
      path: "",
      entries: [
        {
          name: "README.md",
          path: "README.md",
          kind: "file",
          size: 12,
          modified_at: 1,
        },
      ],
    });
  }
  if (url.endsWith("/sessions/session-1")) {
    return jsonResponse(detail());
  }
  if (url.includes("/sessions/session-1/tasks") && init?.method === "POST") {
    return jsonResponse(
      {
        task_id: "task-1",
        session_id: "session-1",
        kind: "chat",
        prompt: "检查项目",
        model_id: "openai/gpt-5.6-sol",
        thinking_level: "medium",
        approval_mode: "always-ask",
        status: "pending",
        output: "",
        error: "",
        created_at: 2,
        updated_at: 2,
        started_at: null,
        finished_at: null,
      },
      202,
    );
  }
  return jsonResponse({ detail: `not found: ${url}` }, 404);
}

beforeEach(() => {
  EventSourceStub.instances = [];
  vi.stubGlobal("EventSource", EventSourceStub);
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AgentWorkbenchPage", () => {
  it("renders the durable Session, conversation, Workspace, and sub-Agent panes", async () => {
    vi.stubGlobal("fetch", vi.fn(runtimeFetch));

    renderPage();

    expect(await screen.findByRole("heading", { name: "准备执行任务" })).toBeVisible();
    expect(screen.getByRole("button", { name: /General Agent 会话/ })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Workspace" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "子 Agent" })).toBeVisible();
    expect(screen.getByRole("button", { name: /README.md/ })).toBeVisible();
    expect(screen.getByText("Skillset · general-agent-default")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "新会话 Skillset" })).toHaveValue(
      "general-agent-default",
    );
  });

  it("uses the Agent-installed Skillset snapshot when the library digest advances", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/agents/default_agent") && !init?.method) {
        return jsonResponse({
          agent_id: "default_agent",
          config: { skillset_id: "general-agent-default" },
          skills: [
            {
              skill_id: "agent-creation",
              digest: "a".repeat(64),
            },
          ],
        });
      }
      if (url.endsWith("/sessions") && init?.method === "POST") {
        return jsonResponse(session, 201);
      }
      return runtimeFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const createButtons = await screen.findAllByRole("button", { name: "新建会话" });
    await userEvent.click(createButtons[0]);

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/sessions") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toMatchObject({
        agent_id: "default_agent",
        skillset_id: "general-agent-default",
      });
    });
  });

  it("sends a task with the selected runtime controls", async () => {
    const fetchMock = vi.fn(runtimeFetch);
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const prompt = await screen.findByLabelText("任务消息");
    await userEvent.type(prompt, "检查项目");
    await userEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/sessions/session-1/tasks") &&
          (init as RequestInit | undefined)?.method === "POST",
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toMatchObject({
        prompt: "检查项目",
        model_id: "openai/gpt-5.6-sol",
        thinking_level: "medium",
        approval_mode: "always-ask",
      });
    });
  });

  it("keeps pending approval actions visible and applies allow-all to the live Session", async () => {
    const waitingTask = {
      task_id: "task-waiting",
      session_id: "session-1",
      kind: "chat",
      prompt: "写文件",
      model_id: "openai/gpt-5.6-sol",
      thinking_level: "medium",
      approval_mode: "always-ask",
      status: "waiting_approval",
      output: "",
      error: "",
      created_at: 2,
      updated_at: 2,
      started_at: 2,
      finished_at: null,
    };
    const pendingApproval = {
      approval_id: "approval-1",
      session_id: "session-1",
      task_id: "task-waiting",
      tool_call_id: "call-1",
      tool_name: "write_file",
      arguments: { file_path: "result.txt", content: "saved" },
      status: "pending",
      decision_message: "",
      created_at: 2,
      decided_at: null,
    };
    let currentSession = session;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/sessions/session-1") && init?.method === "PATCH") {
        currentSession = { ...session, approval_mode: "allow-all", updated_at: 3 };
        return jsonResponse(currentSession);
      }
      if (url.endsWith("/sessions/session-1") && !init?.method) {
        return jsonResponse(
          detail({
            session: currentSession,
            tasks: [
              {
                ...waitingTask,
                approval_mode: currentSession.approval_mode,
              },
            ],
            approvals:
              currentSession.approval_mode === "always-ask"
                ? [pendingApproval]
                : [{ ...pendingApproval, status: "approved", decided_at: 3 }],
          }),
        );
      }
      if (url.endsWith("/sessions") && !init?.method) {
        return jsonResponse({ sessions: [currentSession] });
      }
      return runtimeFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    const dock = await screen.findByTestId("approval-dock");
    expect(within(dock).getByRole("button", { name: "批准" })).toBeVisible();
    expect(
      within(screen.getByTestId("conversation-scroll")).queryByRole("button", {
        name: "批准",
      }),
    ).not.toBeInTheDocument();

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "审批模式" }),
      "allow-all",
    );

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/sessions/session-1") &&
          (init as RequestInit | undefined)?.method === "PATCH",
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
        approval_mode: "allow-all",
      });
      expect(screen.getByRole("combobox", { name: "审批模式" })).toHaveValue(
        "allow-all",
      );
    });
    expect(screen.queryByTestId("approval-dock")).not.toBeInTheDocument();
  });

  it("starts one-sentence Agent generation as a controlled General Agent task", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/agents/generate") && init?.method === "POST") {
        return jsonResponse({
          session: { ...session, session_id: "generator-1", title: "生成 Agent" },
          task: { task_id: "generate-task-1", status: "pending" },
        }, 202);
      }
      if (url.endsWith("/sessions") && !init?.method && fetchMock.mock.calls.length > 6) {
        return jsonResponse({
          sessions: [{ ...session, session_id: "generator-1", title: "生成 Agent" }, session],
        });
      }
      return runtimeFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await screen.findByRole("heading", { name: "准备执行任务" });
    await userEvent.click(screen.getByRole("button", { name: "一句话创建 Agent" }));
    expect(screen.getByLabelText("Builder 模型")).toHaveValue(
      "deepseek/deepseek-v4-flash-0731",
    );
    const request = "创建一个负责审查 Python API 安全性的 Agent";
    await userEvent.type(screen.getByLabelText("Agent 需求"), request);
    await userEvent.click(screen.getByRole("button", { name: "开始生成" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input]) => String(input).endsWith("/agents/generate"),
      );
      expect(call).toBeTruthy();
      expect(JSON.parse(String((call?.[1] as RequestInit).body))).toMatchObject({
        prompt: request,
        model_id: "deepseek/deepseek-v4-flash-0731",
        approval_mode: "always-ask",
      });
    });
  });

  it("only reports creation after an agent_generated event", async () => {
    vi.stubGlobal("fetch", vi.fn(runtimeFetch));
    renderPage();

    await screen.findByRole("heading", { name: "准备执行任务" });
    const source = EventSourceStub.instances.at(-1);
    expect(source).toBeTruthy();
    source?.emit("session_created", {
      sequence: 1,
      session_id: "session-1",
      task_id: null,
      type: "session_created",
      payload: { agent_id: "default_agent" },
      created_at: 1,
    });
    expect(screen.queryByText(/已通过校验并创建/)).not.toBeInTheDocument();

    source?.emit("agent_generated", {
      sequence: 2,
      session_id: "session-1",
      task_id: "generate-task-1",
      type: "agent_generated",
      payload: { agent_id: "verified-agent" },
      created_at: 2,
    });
    expect(
      await screen.findByText("Agent “verified-agent” 已通过校验并创建。"),
    ).toBeVisible();
  });

  it("retries failed generation without allowing a plain chat fallback", async () => {
    const failedTask = {
      task_id: "generate-task-1",
      session_id: "session-1",
      kind: "generate_agent",
      prompt: "controlled generation prompt",
      model_id: "openai/gpt-5.6-sol",
      thinking_level: "medium",
      approval_mode: "always-ask",
      status: "failed",
      output: "",
      error: "Gateway unavailable",
      created_at: 2,
      updated_at: 3,
      started_at: 2,
      finished_at: 3,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/sessions/session-1") && !init?.method) {
        return jsonResponse(detail({ tasks: [failedTask] }));
      }
      if (
        url.endsWith("/tasks/generate-task-1/retry-generation") &&
        init?.method === "POST"
      ) {
        return jsonResponse({ ...failedTask, task_id: "generate-task-2", status: "pending" }, 202);
      }
      return runtimeFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const retry = await screen.findByRole("button", { name: "重新执行生成" });
    expect(screen.getByLabelText("任务消息")).toBeDisabled();
    await userEvent.click(retry);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/agent-workspace/tasks/generate-task-1/retry-generation",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).includes("/sessions/session-1/tasks") &&
          (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("shows a truthful disabled state and does not request Agent data", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      expect(String(input)).toContain("/status");
      return jsonResponse({
        enabled: false,
        version: "agent-workspace-r2",
        runtime_enabled: false,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByText("Agent 工作区已关闭")).toBeVisible();
    expect(screen.getByRole("button", { name: "一句话创建 Agent" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/coding-worker/v1", undefined);
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/agent-workspace/status", undefined);
  });
});

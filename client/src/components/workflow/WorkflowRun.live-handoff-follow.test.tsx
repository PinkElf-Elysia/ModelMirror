import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type WorkflowDefinition, type WorkflowRunEvent } from "../../types/workflow";
import WorkflowRun, {
  persistWorkflowRunRecovery,
  readWorkflowRunRecovery,
} from "./WorkflowRun";

vi.mock("../runtime/RuntimeApprovalPanel", () => ({
  default: ({ onResolved }: { onResolved: () => void }) => (
    <button onClick={() => onResolved()} type="button">
      模拟完成运行审批
    </button>
  ),
}));
vi.mock("../runtime/BrowserSessionPanel", () => ({ default: () => null }));
vi.mock("../runtime/ClientToolPanel", () => ({ default: () => null }));
vi.mock("../runtime/SandboxWorkspacePanel", () => ({ default: () => null }));
vi.mock("../../hooks/useSkillCreatorStatus", () => ({
  useSkillCreatorStatus: () => ({
    status: null,
    loading: false,
    error: "",
    reload: vi.fn(),
  }),
}));
vi.mock("../../data/fileOutputs", () => ({
  fetchFileOutputs: vi.fn(async () => []),
}));

const taskId = "a".repeat(32);
const runId = "123e4567-e89b-42d3-a456-426614174000";

const definition: WorkflowDefinition = {
  id: "handoff-follow-test",
  title: "durable handoff follow",
  updatedAt: "2026-08-25T00:00:00.000Z",
  nodes: [
    {
      id: "input-1",
      type: "workflowNode",
      position: { x: 0, y: 0 },
      data: {
        kind: "input",
        title: "输入",
        description: "输入",
        variableName: "user_input",
      },
    },
    {
      id: "handoff-1",
      type: "workflowNode",
      position: { x: 160, y: 0 },
      data: {
        kind: "agent_handoff",
        title: "等待协作复核",
        description: "等待协作复核",
        contractVersion: 2,
        taskVariable: "task_receipt",
        taskValueKind: "receipt",
        targetMode: "xpert",
        targetXpertId: "review-xpert",
        targetVersion: 3,
        waitForCompletion: true,
        outputVariable: "handoff_receipt",
        resultVariable: "review_result",
      },
    },
    {
      id: "output-1",
      type: "workflowNode",
      position: { x: 320, y: 0 },
      data: {
        kind: "output",
        title: "输出",
        description: "输出",
        outputVariable: "review_result",
      },
    },
  ],
  edges: [
    { id: "edge-1", source: "input-1", target: "handoff-1" },
    { id: "edge-2", source: "handoff-1", target: "output-1" },
  ],
};

const retryDefinition: WorkflowDefinition = {
  ...definition,
  id: "retry-follow-test",
  title: "durable retry follow",
  nodes: [
    definition.nodes[0],
    {
      id: "http-1",
      type: "workflowNode",
      position: { x: 160, y: 0 },
      data: {
        kind: "http_request",
        title: "安全 HTTP 请求",
        description: "安全 HTTP 请求",
        contractVersion: 2,
        method: "GET",
        bodyMode: "none",
        url: "https://example.test/status",
        outputVariable: "http_response",
        retryMode: "transient",
        maxAttempts: 2,
      },
    },
    definition.nodes[2],
  ],
  edges: [
    { id: "retry-edge-1", source: "input-1", target: "http-1" },
    { id: "retry-edge-2", source: "http-1", target: "output-1" },
  ],
};

function sseResponse(
  events: WorkflowRunEvent[],
  headers: Record<string, string> = {},
) {
  return new Response(
    events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
    {
      status: 200,
      headers: { "Content-Type": "text/event-stream", ...headers },
    },
  );
}

function waitingEvents(): WorkflowRunEvent[] {
  return [
    { event: "workflow_meta", sequence: 1, task_id: taskId, run_id: runId },
    {
      event: "node_start",
      sequence: 2,
      node_id: "handoff-1",
      node_title: "等待协作复核",
      node_type: "agent_handoff",
    },
    {
      event: "agent_handoff_waiting",
      sequence: 5,
      node_id: "handoff-1",
      node_title: "等待协作复核",
      node_type: "agent_handoff",
      wait_kind: "agent_handoff",
      wait_id: "handoff_safe",
      target_kind: "xpert",
      target_id: "review-xpert",
      target_version: 3,
      message: "任务已移交，正在等待完成。",
    },
  ];
}

function completedEvents(): WorkflowRunEvent[] {
  return [
    {
      event: "node_end",
      sequence: 6,
      node_id: "handoff-1",
      node_title: "等待协作复核",
      node_type: "agent_handoff",
      output: "协作任务已完成。",
    },
    {
      event: "node_start",
      sequence: 7,
      node_id: "output-1",
      node_title: "输出",
      node_type: "output",
    },
    {
      event: "node_end",
      sequence: 8,
      node_id: "output-1",
      node_title: "输出",
      node_type: "output",
      output: "已完成隔离的确定性复核。",
    },
    {
      event: "workflow_end",
      sequence: 9,
      task_id: taskId,
      run_id: runId,
      final_output: "已完成隔离的确定性复核。",
    },
  ];
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("WorkflowRun durable Handoff stream lifecycle", () => {
  it("follows a durable node retry and clears recovery only after completion", async () => {
    const statusChanges = vi.fn();
    const scheduledEvents: WorkflowRunEvent[] = [
      { event: "workflow_meta", sequence: 1, task_id: taskId, run_id: runId },
      {
        event: "node_start",
        sequence: 2,
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
      },
      {
        event: "node_retry_scheduled",
        sequence: 3,
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        attempt: 2,
        max_attempts: 2,
        resume_at: Date.now() / 1000 + 5,
        error_code: "HTTP_STATUS_503",
        classification: "transient",
      },
    ];
    const finishedEvents: WorkflowRunEvent[] = [
      {
        event: "node_retry_started",
        sequence: 4,
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        attempt: 2,
        max_attempts: 2,
      },
      {
        event: "node_end",
        sequence: 5,
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        output: "HTTP 200",
      },
      {
        event: "workflow_end",
        sequence: 6,
        task_id: taskId,
        run_id: runId,
        final_output: "HTTP 200",
      },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/workflow/run" && init?.method === "POST") {
        return sseResponse(scheduledEvents, {
          "X-ModelMirror-Runtime-Task-Id": taskId,
          "X-ModelMirror-Runtime-Run-Id": runId,
        });
      }
      if (url === `/api/workflow/run/${taskId}/stream?after_sequence=3`) {
        return sseResponse(finishedEvents);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WorkflowRun
        definition={retryDefinition}
        onNodeStatusChange={statusChanges}
      />,
    );

    fireEvent.change(screen.getByLabelText("user_input"), {
      target: { value: "读取合成的公开状态接口。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "运行工作流" }));

    expect(await screen.findByText("等待重试")).toBeInTheDocument();
    expect(screen.getByText(/第 2\/2 次尝试已排队/)).toBeInTheDocument();
    expect(statusChanges).toHaveBeenCalledWith("http-1", "retry_waiting");
    await waitFor(
      () => expect(screen.getAllByText("HTTP 200").length).toBeGreaterThan(0),
      { timeout: 2500 },
    );
    expect(statusChanges).toHaveBeenCalledWith("http-1", "running");
    expect(statusChanges).toHaveBeenCalledWith("http-1", "done");
    expect(readWorkflowRunRecovery(retryDefinition.id)).toBeNull();
  });

  it("automatically follows a live Handoff wait until completion", async () => {
    let followRequests = 0;
    const statusChanges = vi.fn();
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/workflow/run" && init?.method === "POST") {
          return sseResponse(waitingEvents(), {
            "X-ModelMirror-Runtime-Task-Id": taskId,
            "X-ModelMirror-Runtime-Run-Id": runId,
          });
        }
        if (
          url
          === `/api/workflow/run/${taskId}/stream?after_sequence=5`
        ) {
          followRequests += 1;
          return followRequests === 1
            ? sseResponse([])
            : sseResponse(completedEvents());
        }
        throw new Error(`Unexpected fetch: ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WorkflowRun
        definition={definition}
        onNodeStatusChange={statusChanges}
      />,
    );

    fireEvent.change(screen.getByLabelText("user_input"), {
      target: { value: "请让复核智能体确认这份合成数据。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "运行工作流" }));

    expect(await screen.findByText("任务已移交，正在等待完成。")).toBeInTheDocument();
    await waitFor(
      () => {
        expect(
          screen.getAllByText("已完成隔离的确定性复核。").length,
        ).toBeGreaterThan(0);
      },
      { timeout: 3500 },
    );
    expect(followRequests).toBe(2);
    expect(statusChanges).toHaveBeenCalledWith("handoff-1", "done");
  });

  it("keeps following after page refresh without a second manual action", async () => {
    persistWorkflowRunRecovery(definition.id, { taskId, runId });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (
        url
        === `/api/workflow/run/${taskId}/stream?after_sequence=0`
      ) {
        return sseResponse(waitingEvents());
      }
      if (
        url
        === `/api/workflow/run/${taskId}/stream?after_sequence=5`
      ) {
        return sseResponse(completedEvents());
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={definition} />);

    expect(await screen.findByText("任务已移交，正在等待完成。")).toBeInTheDocument();
    await waitFor(
      () => {
        expect(
          screen.getAllByText("已完成隔离的确定性复核。").length,
        ).toBeGreaterThan(0);
      },
      { timeout: 2500 },
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/workflow/run/${taskId}/stream?after_sequence=0`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/workflow/run/${taskId}/stream?after_sequence=5`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("treats a refreshed node retry as active and cancels the persisted task", async () => {
    persistWorkflowRunRecovery(retryDefinition.id, { taskId, runId });
    const scheduledEvents: WorkflowRunEvent[] = [
      { event: "workflow_meta", sequence: 1, task_id: taskId, run_id: runId },
      {
        event: "node_retry_scheduled",
        sequence: 2,
        node_id: "http-1",
        node_title: "安全 HTTP 请求",
        node_type: "http_request",
        attempt: 2,
        max_attempts: 2,
        resume_at: Date.now() / 1000 + 30,
        error_code: "HTTP_STATUS_NOT_SUCCESSFUL",
        classification: "transient",
      },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === `/api/workflow/run/${taskId}/stream?after_sequence=0`) {
        return sseResponse(scheduledEvents);
      }
      if (url === `/api/workflow/run/${taskId}/cancel` && init?.method === "POST") {
        return new Response(JSON.stringify({ status: "cancelled" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={retryDefinition} />);

    expect(await screen.findByText("等待重试")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "运行工作流" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消运行" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/workflow/run/${taskId}/cancel`,
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(
      await screen.findByRole("button", { name: "运行工作流" }),
    ).toBeInTheDocument();
    expect(readWorkflowRunRecovery(retryDefinition.id)).toBeNull();
  });

  it("clears persisted recovery after a node-scoped terminal retry error", async () => {
    persistWorkflowRunRecovery(retryDefinition.id, { taskId, runId });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `/api/workflow/run/${taskId}/stream?after_sequence=0`) {
        return sseResponse([
          { event: "workflow_meta", sequence: 1, task_id: taskId, run_id: runId },
          {
            event: "node_retry_started",
            sequence: 2,
            node_id: "http-1",
            node_title: "安全 HTTP 请求",
            node_type: "http_request",
            attempt: 2,
            max_attempts: 2,
            error_code: "HTTP_STATUS_NOT_SUCCESSFUL",
            classification: "transient",
          },
          {
            event: "error",
            sequence: 3,
            task_id: taskId,
            run_id: runId,
            node_id: "http-1",
            node_title: "安全 HTTP 请求",
            code: "HTTP_STATUS_NOT_SUCCESSFUL",
            message: "HTTP service returned an unsuccessful status.",
            terminal: true,
            attempt: 2,
            max_attempts: 2,
            classification: "transient",
            exhausted: true,
          },
        ]);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={retryDefinition} />);

    expect(
      await screen.findAllByText(/HTTP service returned an unsuccessful status\./),
    ).not.toHaveLength(0);
    expect(
      await screen.findByRole("button", { name: "运行工作流" }),
    ).toBeInTheDocument();
    expect(readWorkflowRunRecovery(retryDefinition.id)).toBeNull();
    expect(screen.getAllByText(/第 2\/2 次尝试/).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps a node-scoped compatibility error non-terminal until workflow_end", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) !== "/api/workflow/run" || init?.method !== "POST") {
        throw new Error(`Unexpected fetch: ${String(input)}`);
      }
      return sseResponse([
        { event: "workflow_meta", sequence: 1, task_id: taskId, run_id: runId },
        {
          event: "node_start",
          sequence: 2,
          node_id: "input-1",
          node_title: "兼容节点",
          node_type: "input",
        },
        {
          event: "error",
          sequence: 3,
          node_id: "input-1",
          node_title: "兼容节点",
          node_type: "input",
          message: "兼容节点返回了可继续的节点错误。",
        },
        {
          event: "node_start",
          sequence: 4,
          node_id: "output-1",
          node_title: "输出",
          node_type: "output",
        },
        {
          event: "node_end",
          sequence: 5,
          node_id: "output-1",
          node_title: "输出",
          node_type: "output",
          output: "下游仍然完成。",
        },
        {
          event: "workflow_end",
          sequence: 6,
          task_id: taskId,
          run_id: runId,
          final_output: "下游仍然完成。",
        },
      ], {
        "X-ModelMirror-Runtime-Task-Id": taskId,
        "X-ModelMirror-Runtime-Run-Id": runId,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={definition} />);
    fireEvent.click(screen.getByRole("button", { name: "运行工作流" }));

    expect((await screen.findAllByText("完成")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("下游仍然完成。").length).toBeGreaterThan(0);
    expect(readWorkflowRunRecovery(definition.id)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps recovery blocking a duplicate run after a temporary follow failure", async () => {
    persistWorkflowRunRecovery(retryDefinition.id, { taskId, runId });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === `/api/workflow/run/${taskId}/stream?after_sequence=0`) {
        return new Response("temporary failure", { status: 503 });
      }
      throw new Error(`Unexpected fetch: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={retryDefinition} />);

    expect(await screen.findByRole("button", { name: "继续跟踪" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消原运行" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "运行工作流" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/原运行可能仍在服务端继续/);
    expect(readWorkflowRunRecovery(retryDefinition.id)).not.toBeNull();
  });

  it("does not abort the live stream when cancellation reports authoritative completion", async () => {
    let followRequests = 0;
    let liveSignal: AbortSignal | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/workflow/run" && init?.method === "POST") {
        liveSignal = init.signal ?? undefined;
        return sseResponse([
          { event: "workflow_meta", sequence: 1, task_id: taskId, run_id: runId },
          {
            event: "node_retry_scheduled",
            sequence: 2,
            node_id: "http-1",
            node_title: "安全 HTTP 请求",
            node_type: "http_request",
            attempt: 2,
            max_attempts: 2,
            resume_at: Date.now() / 1000 + 5,
            error_code: "HTTP_STATUS_503",
            classification: "transient",
          },
        ], {
          "X-ModelMirror-Runtime-Task-Id": taskId,
          "X-ModelMirror-Runtime-Run-Id": runId,
        });
      }
      if (url === `/api/workflow/run/${taskId}/cancel`) {
        return new Response(JSON.stringify({ status: "completed" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `/api/workflow/run/${taskId}/stream?after_sequence=2`) {
        followRequests += 1;
        return sseResponse([
          {
            event: "workflow_end",
            sequence: 3,
            task_id: taskId,
            run_id: runId,
            final_output: "服务端先完成。",
          },
        ]);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={retryDefinition} />);
    fireEvent.click(screen.getByRole("button", { name: "运行工作流" }));
    expect(await screen.findByText("等待重试")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消运行" }));

    await waitFor(() => expect(followRequests).toBe(1), { timeout: 2500 });
    expect(liveSignal?.aborted).toBe(false);
    expect(screen.getAllByText("服务端先完成。").length).toBeGreaterThan(0);
  });

  it("clears the previous task identity before a new POST can be cancelled", async () => {
    let postCount = 0;
    let secondSignal: AbortSignal | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/workflow/run" && init?.method === "POST") {
        postCount += 1;
        if (postCount === 1) {
          return sseResponse([
            { event: "workflow_meta", sequence: 1, task_id: taskId, run_id: runId },
            {
              event: "workflow_end",
              sequence: 2,
              task_id: taskId,
              run_id: runId,
              final_output: "第一次完成。",
            },
          ], {
            "X-ModelMirror-Runtime-Task-Id": taskId,
            "X-ModelMirror-Runtime-Run-Id": runId,
          });
        }
        secondSignal = init.signal ?? undefined;
        return new Promise<Response>(() => undefined);
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={definition} />);
    fireEvent.click(screen.getByRole("button", { name: "运行工作流" }));
    expect((await screen.findAllByText("第一次完成。")).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "运行工作流" }));
    fireEvent.click(await screen.findByRole("button", { name: "取消运行" }));

    await waitFor(() => expect(secondSignal?.aborted).toBe(true));
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).endsWith(`/${taskId}/cancel`)),
    ).toBe(false);
  });

  it("continues following when an approval resume later reaches Handoff", async () => {
    persistWorkflowRunRecovery(definition.id, { taskId, runId });
    const replayUrl = `/api/workflow/run/${taskId}/stream?after_sequence=0`;
    const followUrl = `/api/workflow/run/${taskId}/stream?after_sequence=5`;
    let replayRequests = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === replayUrl) {
        replayRequests += 1;
        if (replayRequests === 1) {
          return sseResponse([
            {
              event: "workflow_meta",
              sequence: 1,
              task_id: taskId,
              run_id: runId,
            },
            {
              event: "runtime_approval_pending",
              sequence: 2,
              node_id: "approval-1",
              node_title: "运行审批",
              request_id: "approval-safe",
              request_status: "pending",
            },
          ]);
        }
        return sseResponse(waitingEvents());
      }
      if (url === followUrl) {
        return sseResponse(completedEvents());
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkflowRun definition={definition} />);

    await waitFor(() => expect(replayRequests).toBe(1));
    fireEvent.click(
      await screen.findByRole("button", { name: "模拟完成运行审批" }),
    );
    expect(
      await screen.findByText("任务已移交，正在等待完成。"),
    ).toBeInTheDocument();
    await waitFor(
      () => {
        expect(
          screen.getAllByText("已完成隔离的确定性复核。").length,
        ).toBeGreaterThan(0);
      },
      { timeout: 2500 },
    );
    expect(replayRequests).toBe(2);
    expect(fetchMock).toHaveBeenCalledWith(
      replayUrl,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      followUrl,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });
});

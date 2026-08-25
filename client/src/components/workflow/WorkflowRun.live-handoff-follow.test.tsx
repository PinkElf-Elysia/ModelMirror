import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type WorkflowDefinition, type WorkflowRunEvent } from "../../types/workflow";
import WorkflowRun, {
  persistWorkflowRunRecovery,
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

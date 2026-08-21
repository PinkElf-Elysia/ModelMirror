import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkflowRunEvent } from "../../types/workflow";
import type { XpertConversationMessage } from "../../types/xpert";
import SkillCreatorCaptureButton, {
  completedWorkflowCaptureSource,
  xpertMessageCaptureSource,
  type SkillCreatorCaptureSource,
} from "./SkillCreatorCaptureButton";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

function renderButton(source: SkillCreatorCaptureSource, enabled = true) {
  return render(
    <MemoryRouter initialEntries={["/origin"]}>
      <Routes>
        <Route
          element={<SkillCreatorCaptureButton enabled={enabled} source={source} />}
          path="/origin"
        />
        <Route element={<p>Creator destination</p>} path="/skills/create/:sessionId" />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("trusted Skill Creator capture sources", () => {
  it("accepts only persisted assistant messages with complete trusted linkage", () => {
    const linked: XpertConversationMessage = {
      message_id: "message_1",
      role: "assistant",
      content: "done",
      source_task_id: "task_1",
      source_run_id: "run_1",
    };

    expect(xpertMessageCaptureSource(linked, "xpert_1", "conversation_1")).toEqual({
      sourceKind: "xpert_chat",
      taskId: "task_1",
      runId: "run_1",
      xpertId: "xpert_1",
      conversationId: "conversation_1",
      messageId: "message_1",
    });
    expect(xpertMessageCaptureSource({ ...linked, role: "user" }, "xpert_1", "conversation_1")).toBeNull();
    expect(xpertMessageCaptureSource({ ...linked, source_run_id: null }, "xpert_1", "conversation_1")).toBeNull();
    expect(xpertMessageCaptureSource({ role: "assistant", content: "legacy" }, "xpert_1", "conversation_1")).toBeNull();
  });

  it("accepts only a fully completed classic workflow", () => {
    const complete = [{ event: "workflow_end", final_output: "done" }] as WorkflowRunEvent[];
    const waiting = [{ event: "runtime_approval_pending" }] as WorkflowRunEvent[];
    const failed = [{ event: "error", message: "failed" }] as WorkflowRunEvent[];

    expect(completedWorkflowCaptureSource(complete, "task_1", "run_1", false)).toEqual({
      sourceKind: "workflow_classic",
      taskId: "task_1",
      runId: "run_1",
    });
    expect(completedWorkflowCaptureSource(complete, "task_1", "run_1", true)).toBeNull();
    expect(completedWorkflowCaptureSource(waiting, "task_1", "run_1", false)).toBeNull();
    expect(completedWorkflowCaptureSource(failed, "task_1", "run_1", false)).toBeNull();
    expect(completedWorkflowCaptureSource(complete, "task_1", null, false)).toBeNull();
  });

  it("creates an Xpert source session and navigates to the studio", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: { session_id: "creator_xpert" } }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderButton({
      sourceKind: "xpert_chat",
      taskId: "task_1",
      runId: "run_1",
      xpertId: "xpert_1",
      conversationId: "conversation_1",
      messageId: "message_1",
    });

    await userEvent.click(screen.getByRole("button", { name: "沉淀为 Skill" }));

    expect(await screen.findByText("Creator destination")).toBeVisible();
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      mode: "run",
      source_kind: "xpert_chat",
      source_task_id: "task_1",
      source_run_id: "run_1",
      source_xpert_id: "xpert_1",
      source_conversation_id: "conversation_1",
      source_message_id: "message_1",
    });
  });

  it("creates a classic workflow session without Xpert-only identifiers", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: { session_id: "creator_workflow" } }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderButton({
      sourceKind: "workflow_classic",
      taskId: "task_2",
      runId: "run_2",
    });

    await userEvent.click(screen.getByRole("button", { name: "沉淀为 Skill" }));

    expect(await screen.findByText("Creator destination")).toBeVisible();
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      mode: "run",
      source_kind: "workflow_classic",
      source_task_id: "task_2",
      source_run_id: "run_2",
    });
  });

  it("stays hidden when Creator is disabled or the source is unsupported", () => {
    renderButton({
      sourceKind: "workflow_classic",
      taskId: "task_2",
      runId: "run_2",
    }, false);

    expect(screen.queryByRole("button", { name: "沉淀为 Skill" })).not.toBeInTheDocument();
  });

  it("supports an explicit retry label without changing the capture contract", () => {
    render(
      <MemoryRouter>
        <SkillCreatorCaptureButton
          busyLabel="正在重试..."
          enabled
          label="重试创建 Creator 会话"
          source={{
            sourceKind: "workflow_classic",
            taskId: "task-2",
            runId: "run-2",
          }}
        />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("button", { name: "重试创建 Creator 会话" }),
    ).toBeVisible();
  });
});

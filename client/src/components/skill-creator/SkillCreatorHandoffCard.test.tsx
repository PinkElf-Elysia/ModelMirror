import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkflowRunEvent } from "../../types/workflow";
import SkillCreatorHandoffCard, {
  latestSkillCreatorHandoff,
  skillCreatorHandoffFailureCopy,
  type SkillCreatorHandoffEvent,
} from "./SkillCreatorHandoffCard";

function renderCard(event: SkillCreatorHandoffEvent) {
  return render(
    <MemoryRouter initialEntries={["/workflow"]}>
      <Routes>
        <Route
          element={(
            <SkillCreatorHandoffCard
              captureEnabled
              captureSource={{
                sourceKind: "workflow_classic",
                taskId: "task-1",
                runId: "run-1",
              }}
              event={event}
            />
          )}
          path="/workflow"
        />
        <Route element={<p>Creator destination</p>} path="/skills/create/:sessionId" />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Skill Creator workflow handoff card", () => {
  it("recovers the latest persisted handoff event", () => {
    const events = [
      { event: "workflow_meta", task_id: "task-1" },
      {
        event: "skill_creator_handoff",
        status: "ready",
        session_id: "creator-old",
      },
      {
        event: "skill_creator_handoff",
        status: "ready",
        session_id: "creator-current",
      },
      { event: "workflow_end", run_id: "run-1" },
    ] as WorkflowRunEvent[];

    expect(latestSkillCreatorHandoff(events)?.session_id).toBe("creator-current");
  });

  it("links a ready handoff to the trusted Creator session", () => {
    renderCard({
      event: "skill_creator_handoff",
      status: "ready",
      session_id: "creator/session",
    });

    expect(screen.getByText("Creator 会话已准备好")).toBeVisible();
    expect(screen.getByText(/工作流分析会作为待确认素材显示/)).toBeVisible();
    expect(
      screen.getByRole("link", { name: "前往 Creator 检查分析" }),
    ).toHaveAttribute("href", "/skills/create/creator%2Fsession");
    expect(screen.queryByRole("button", { name: "沉淀为 Skill" })).not.toBeInTheDocument();
  });

  it("shows a stable reason and retries through trusted workflow capture", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(new Response(JSON.stringify({
        session: { session_id: "creator-retried" },
      }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      })),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderCard({
      event: "skill_creator_handoff",
      status: "failed",
      error_code: "skill_creator_handoff_unavailable",
    });

    expect(screen.getByText(
      skillCreatorHandoffFailureCopy("skill_creator_handoff_unavailable"),
    )).toBeVisible();
    await userEvent.click(
      screen.getByRole("button", { name: "重试创建 Creator 会话" }),
    );

    expect(await screen.findByText("Creator destination")).toBeVisible();
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      mode: "run",
      source_kind: "workflow_classic",
      source_task_id: "task-1",
      source_run_id: "run-1",
    });
  });

  it("fails closed when a ready event is missing its session id", () => {
    renderCard({
      event: "skill_creator_handoff",
      status: "ready",
    });

    expect(screen.getByText("Creator 交接未完成")).toBeVisible();
    expect(screen.queryByRole("link", { name: "前往 Creator 检查分析" })).not.toBeInTheDocument();
  });
});

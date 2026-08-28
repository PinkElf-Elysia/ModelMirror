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
import { clearSkillExperienceApiCache } from "../../utils/skillExperienceApi";

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
  clearSkillExperienceApiCache();
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
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const payload = url.endsWith("/status")
        ? { enabled: true, available: true, model_calls_enabled: false }
        : url.includes("candidates?")
          ? { candidates: [] }
          : {
              candidate: {
                candidate_id: "experience-retried",
                version: "skill-experience-candidate-v1",
                revision: 1,
                digest: "a".repeat(64),
                state: "captured",
                source_kind: "workflow_classic",
                source_task_id: "task-1",
                source_run_id: "run-1",
                selected_evidence: [],
                overlaps: [],
                updated_at: 1,
              },
              evidence_preview: {
                version: "creator-evidence-v1",
                source_kind: "workflow_classic",
                source_task_id: "task-1",
                source_run_id: "run-1",
                source_title: "重试运行",
                preview_fingerprint: "b".repeat(64),
                candidates: [{
                  candidate_id: "goal",
                  kind: "intent_summary",
                  title: "目标摘要",
                  summary: "恢复可信运行经验",
                  content_hash: "c".repeat(64),
                  default_selected: true,
                }],
              },
            };
      return Promise.resolve(new Response(JSON.stringify(payload), {
        status: init?.method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      }));
    });
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
      await screen.findByRole("button", { name: "重试创建 Creator 会话" }),
    );

    expect(await screen.findByText("确认可用于沉淀的素材")).toBeVisible();
    const createCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input).endsWith("/api/skills/experience/candidates")
      && (init as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
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

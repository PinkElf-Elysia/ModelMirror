import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelPreferenceProvider } from "../context/ModelPreferenceContext";
import { resourceNavItems } from "../theme/resources";
import AgentsPage, {
  featuredPlatformCapability,
  platformCapabilities,
  platformCapabilityPath,
} from "./AgentsPage";

vi.mock("../components/AgentCard", () => ({
  default: ({ agent }: { agent: { name: string } }) => (
    <div data-testid="agent-card">{agent.name}</div>
  ),
}));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderPage(workbenchEnabled = false) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ enabled: workbenchEnabled }), {
          headers: { "Content-Type": "application/json" },
          status: 200,
        }),
      ),
    ),
  );

  return render(
    <MemoryRouter>
      <ModelPreferenceProvider>
        <AgentsPage />
      </ModelPreferenceProvider>
    </MemoryRouter>,
  );
}

describe("AgentsPage platform capabilities", () => {
  it("uses the real Data X entry instead of the obsolete workflow showcase", () => {
    const dataX = platformCapabilities.find((item) => item.id === "datax");

    expect(dataX).toMatchObject({
      title: "Data X 数据分析",
      statusLabel: "服务状态：已开放",
    });
    expect(platformCapabilities.some((item) => item.id === "workflow-builder")).toBe(
      false,
    );
    expect(platformCapabilityPath("datax")).toBe("/datax");
    expect(platformCapabilities.map((item) => item.summary)).toEqual([
      "一句话生成可编辑工作流。",
      "定时运行已发布智能体。",
      "规划可暂停、可恢复的长期目标。",
      "导入数据，生成指标与分析。",
      "组合多个专家协同完成任务。",
    ]);
    expect(resourceNavItems.find((item) => item.key === "agents")).toMatchObject({
      shortTitle: "Agent",
      title: "Agent人才市场",
    });
  });

  it("promotes the publishing center and uses the approved Agent market hierarchy", () => {
    renderPage();

    expect(
      screen.getByRole("heading", { level: 1, name: "Agent人才市场" }),
    ).toBeVisible();
    expect(screen.getByText(/个部门 ·/)).toBeVisible();
    expect(screen.getByRole("searchbox", { name: "搜索专家" })).toBeVisible();
    expect(
      screen.getByRole("heading", { name: featuredPlatformCapability.title }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "管理智能体" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "部门招聘牌" })).toBeVisible();

    expect(screen.queryByText("资源分区")).not.toBeInTheDocument();
    expect(screen.queryByText("人才市场规模")).not.toBeInTheDocument();
    expect(screen.queryByText(/位 AI 专家现场递简历/)).not.toBeInTheDocument();
  });

  it("reuses the workbench sidebar and only reveals the development workshop when enabled", async () => {
    renderPage(true);

    const workflowLinks = screen.getAllByRole("link", {
      name: /自定义工作流/,
    });
    const ragLinks = screen.getAllByRole("link", { name: /RAG 知识库/ });
    expect(workflowLinks).toHaveLength(2);
    expect(ragLinks).toHaveLength(2);
    workflowLinks.forEach((link) =>
      expect(link).toHaveAttribute("href", "/workflow"),
    );
    ragLinks.forEach((link) => expect(link).toHaveAttribute("href", "/rag"));
    expect(await screen.findByText("AI 应用开发工坊")).toBeVisible();
  });
});

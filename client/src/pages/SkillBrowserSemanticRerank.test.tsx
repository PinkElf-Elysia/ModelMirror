import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { skillProjects } from "../data/skillProjects";
import type { SkillNeedProjectTarget } from "../data/skillNeedMatcher";
import SkillBrowserPage from "./SkillBrowserPage";

const mocks = vi.hoisted(() => ({
  findSkillsForNeed: vi.fn(),
  loadSkillNeedCandidates: vi.fn(),
  loadSkillTrustSummaryIndex: vi.fn(),
  saveSkillRerankFeedback: vi.fn(),
  searchSkills: vi.fn(),
  useSkillCreatorStatus: vi.fn(),
}));

vi.mock("../data/skillNeedCandidates", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/skillNeedCandidates")>()),
  loadSkillNeedCandidates: mocks.loadSkillNeedCandidates,
}));
vi.mock("../data/skillNeedMatcher", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/skillNeedMatcher")>()),
  findSkillsForNeed: mocks.findSkillsForNeed,
}));
vi.mock("../data/skillTrustIndex", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../data/skillTrustIndex")>()),
  loadSkillTrustSummaryIndex: mocks.loadSkillTrustSummaryIndex,
}));
vi.mock("../utils/skillRerankApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../utils/skillRerankApi")>()),
  saveSkillRerankFeedback: mocks.saveSkillRerankFeedback,
  searchSkills: mocks.searchSkills,
}));
vi.mock("../hooks/useSkillCreatorStatus", () => ({
  useSkillCreatorStatus: mocks.useSkillCreatorStatus,
}));

const project = skillProjects.find(
  (item) => item.installMode === "direct" && item.installStatus === "ready",
)!;
const target: SkillNeedProjectTarget = {
  targetType: "project",
  project,
  id: project.id,
  name: project.name,
  category: project.category,
  kind: project.kind,
  description: project.description,
  tags: project.tags,
  installStatus: project.installStatus,
};
const candidateId = `catalog:project:${project.id}`;
const receipt = {
  queryHash: "a".repeat(64),
  candidateSetFingerprint: "b".repeat(64),
  candidateFingerprints: [
    { candidateId, candidateFingerprint: "c".repeat(64) },
  ],
  lexicalRanks: [candidateId],
  semanticRanks: [candidateId],
  proposedRanks: [candidateId],
  finalRanks: [candidateId],
  rankChanges: [],
  provider: "api",
  model: "reranker",
  strategyVersion: "skill-semantic-rrf-v1",
  durationMs: 12,
  fallbackReason: null,
};

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("SkillBrowser semantic reranking", () => {
  it("uses the shared workbench sidebar and compact catalog header", async () => {
    mocks.useSkillCreatorStatus.mockReturnValue({ status: { enabled: false } });
    mocks.loadSkillTrustSummaryIndex.mockResolvedValue(null);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ skills: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(
      <MemoryRouter>
        <SkillBrowserPage />
      </MemoryRouter>,
    );

    const heading = screen.getByRole("heading", {
      level: 1,
      name: "Skill 技能货架",
    });
    const header = heading.closest("header");
    expect(header).not.toBeNull();
    const headerQueries = within(header as HTMLElement);
    const installableCount = skillProjects.filter(
      (item) => item.installStatus === "ready",
    ).length;
    const categoryCount = new Set(skillProjects.map((item) => item.category)).size;
    const skillsetCount = skillProjects.filter(
      (item) => item.kind === "skillset",
    ).length;

    expect(
      headerQueries.getByText("查找、安装并管理可复用的 AI 技能与 SkillSet。"),
    ).toBeVisible();
    expect(headerQueries.getByText(String(skillProjects.length))).toBeVisible();
    expect(headerQueries.getByText(String(installableCount))).toBeVisible();
    expect(headerQueries.getByText(String(categoryCount))).toBeVisible();
    expect(headerQueries.getByText(String(skillsetCount))).toBeVisible();
    expect(headerQueries.getByText("个 Skill")).toBeVisible();
    expect(headerQueries.getByText("可安装")).toBeVisible();
    expect(headerQueries.getByText("个分类")).toBeVisible();
    expect(headerQueries.getByText("个 SkillSet")).toBeVisible();

    expect(screen.getAllByText("工作台入口").length).toBeGreaterThan(0);
    ["自定义工作流", "RAG 知识库", "Coding", "系统设置"].forEach((entry) =>
      expect(screen.getAllByText(entry).length).toBeGreaterThan(0),
    );
    expect(screen.queryByText("技能培训服务台")).not.toBeInTheDocument();
    expect(screen.queryByText("技能培训教室开放报名")).not.toBeInTheDocument();
    expect(screen.queryByText("货架状态")).not.toBeInTheDocument();
    expect(screen.queryByText("可安装资源")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Agent 内置/ }),
    ).not.toBeInTheDocument();
    const builtinHeading = await screen.findByRole("heading", {
      name: "Agent 内置技能集",
    });
    const builtinCard = builtinHeading.closest("article");
    expect(builtinCard).not.toBeNull();
    expect(builtinCard?.previousElementSibling).not.toBeNull();
    const builtinCardQueries = within(builtinCard as HTMLElement);
    expect(builtinCardQueries.getByText("仅审计")).toBeVisible();
    expect(builtinCardQueries.getByText("可查看")).toBeVisible();
    expect(
      builtinCardQueries.getByText(/平台内置的通用技能集合/),
    ).toBeVisible();
    expect(builtinCardQueries.queryByText("不可安装")).not.toBeInTheDocument();
    expect(builtinCardQueries.queryByTestId("skill-card-source")).toBeNull();
    expect(
      builtinCardQueries.queryByRole("link", { name: "查看来源" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "按任务寻找 Skill" })).toBeVisible();
    expect(screen.queryByText("试试这些需求")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "浏览技能目录" })).not.toBeInTheDocument();
    expect(screen.queryByText(/当前显示 .* 项/)).not.toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "搜索技能" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "分类" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "安装状态" })).toBeVisible();
    expect(screen.getByRole("switch", { name: "语义重排" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("requires page-session disclosure and saves feedback only after a click", async () => {
    mocks.useSkillCreatorStatus.mockReturnValue({ status: { enabled: false } });
    mocks.loadSkillTrustSummaryIndex.mockResolvedValue(null);
    mocks.loadSkillNeedCandidates.mockResolvedValue([target]);
    mocks.searchSkills.mockResolvedValue({
      lexicalResults: [],
      finalResults: [
        {
          candidateId,
          candidateFingerprint: "c".repeat(64),
          name: project.name,
          summary: project.description,
          category: project.category,
          kind: project.kind,
          installStatus: "ready",
          score: 20,
          reasons: [
            { type: "name", label: "名称", origin: "direct", matchedTerms: ["pdf"] },
          ],
          lexicalRank: 3,
          semanticRank: 1,
          rankDelta: 2,
        },
      ],
      status: "semantic",
      warnings: [],
      receipt,
      governanceRevision: 7,
    });
    mocks.saveSkillRerankFeedback.mockResolvedValue({
      feedback: { feedbackId: "feedback_1" },
      governanceRevision: 8,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        return new Response(
          JSON.stringify(url.endsWith("/installed") ? { skills: [] } : { skills: [] }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );
    const user = userEvent.setup();
    const rendered = render(
      <MemoryRouter>
        <SkillBrowserPage />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("switch", { name: "语义重排" }));
    expect(screen.getByText(/最多 24 个公共目录候选/)).toBeInTheDocument();
    expect(mocks.searchSkills).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "确认启用语义重排" }),
    );

    const query = screen.getByRole("textbox", { name: /描述你要完成的事/ });
    await user.type(query, "分析 PDF");
    const searchButton = screen.getByRole("button", { name: "寻找合适的 Skill" });
    await user.click(searchButton);
    const resultsDialog = await screen.findByRole("dialog", {
      name: "Skill 推荐结果",
    });
    expect(await within(resultsDialog).findByText("语义第 1 名")).toBeVisible();
    expect(mocks.searchSkills).toHaveBeenCalledWith("分析 PDF", true);
    expect(mocks.saveSkillRerankFeedback).not.toHaveBeenCalled();

    await user.click(within(resultsDialog).getByRole("button", { name: "相关" }));
    await waitFor(() =>
      expect(mocks.saveSkillRerankFeedback).toHaveBeenCalledWith(
        expect.objectContaining({
          expectedRevision: 7,
          query: "分析 PDF",
          candidateId,
          judgment: "relevant",
        }),
      ),
    );
    expect(
      await within(resultsDialog).findByRole("button", { name: "已记为相关" }),
    ).toBeDisabled();

    await user.click(
      within(resultsDialog).getByRole("button", { name: "关闭推荐结果" }),
    );
    expect(
      screen.queryByRole("dialog", { name: "Skill 推荐结果" }),
    ).not.toBeInTheDocument();
    expect(searchButton).toHaveFocus();

    rendered.unmount();
    render(
      <MemoryRouter>
        <SkillBrowserPage />
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("switch", { name: "语义重排" }),
    ).toHaveAttribute("aria-checked", "false");
  }, 10_000);
});

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import ResourceNav from "../components/ResourceNav";
import {
  helpArticles,
  helpCenterCloseoutBaseline,
  helpModules,
  helpSections,
  remoteMcpReviewBaseline,
} from "../content/help-center";
import HelpArticlePage from "./HelpArticlePage";
import HelpCenterPage from "./HelpCenterPage";

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="current location">{`${location.pathname}${location.search}`}</output>;
}

function renderHelp(initialEntry = "/help") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route element={<><HelpCenterPage /><LocationProbe /></>} path="/help" />
        <Route element={<HelpArticlePage />} path="/help/sections/:sectionId" />
        <Route element={<HelpArticlePage />} path="/help/modules/:moduleId" />
        <Route element={<HelpArticlePage />} path="/help/modules/:moduleId/:topicId" />
        <Route element={<HelpArticlePage />} path="/help/:slug" />
      </Routes>
    </MemoryRouter>,
  );
}

describe("HelpCenterPage", () => {
  it("keeps the audited eight-module and 45-topic taxonomy", () => {
    const expectedTopicIds: Record<string, string[]> = {
      models: ["filter-and-compare", "smart-router", "text-and-files", "image-understanding", "image-generation", "video", "realtime-voice", "transcription", "speech-synthesis", "music-generation", "start-chatting"],
      agents: ["agent-market", "agent-studio", "workflow-generator", "automations", "goals", "evaluations", "evolution", "datax", "expert-team"],
      mcps: ["tool-shelf", "connected-registry", "mcp-hub", "toolsets"],
      skills: ["market", "installed", "creator", "local-import", "drafts", "proposals", "rerank"],
      prompts: ["templates", "prompt-command", "plugins"],
      runtime: ["run-records", "client-hosts", "runtime-resources"],
      workspace: ["workflow", "rag", "data-tables", "coding", "settings"],
      experimental: ["workflow-native", "science", "matrix-oasis"],
    };

    expect(helpModules.map((module) => module.id)).toEqual(Object.keys(expectedTopicIds));
    expect(helpModules.flatMap((module) => module.topics)).toHaveLength(45);
    helpModules.forEach((module) => {
      expect(module.topics.map((topic) => topic.id)).toEqual(expectedTopicIds[module.id]);
      expect(module.homeTopicIds).toHaveLength(2);
      expect(module.topics.some((topic) => topic.id === "overview")).toBe(false);
      module.homeTopicIds.forEach((topicId) => expect(expectedTopicIds[module.id]).toContain(topicId));
    });
  });

  it("renders five task-oriented areas and a compact two-link module index", () => {
    renderHelp();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    helpSections.forEach((section) => expect(screen.getByRole("heading", { name: section.title, level: 2 })).toBeInTheDocument());
    helpModules.forEach((module) => {
      expect(document.querySelector(`a[href="/help/modules/${module.id}"]`)).toBeInTheDocument();
      module.homeTopicIds.forEach((topicId) => expect(document.querySelector(`a[href="/help/modules/${module.id}/${topicId}"]`)).toBeInTheDocument());
    });
    expect(document.querySelector('a[href="/help/modules/expert-team"]')).not.toBeInTheDocument();
    expect(screen.queryByText("查看全部模块与功能")).not.toBeInTheDocument();
  });

  it("uses the goal area as a three-way decision aid", () => {
    renderHelp();
    ["只完成眼前这一次", "以后反复使用同一角色", "按固定顺序完成多步任务"].forEach((label) => expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: /让 AI 使用外部工具/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看全部目标" })).toHaveAttribute("href", "/help/sections/goals");
    expect(screen.getByRole("link", { name: /了解模镜的整体结构/ })).toHaveAttribute("href", "/help/modules-and-terms");
    expect(screen.getByRole("link", { name: /以后反复使用同一角色/ })).toHaveAttribute("href", "/help/create-repeatable-agent");
    expect(screen.getByRole("link", { name: /按固定顺序完成多步任务/ })).toHaveAttribute("href", "/help/build-first-workflow");
  });

  it("searches articles and second-level topics while writing q to the URL", async () => {
    const user = userEvent.setup();
    renderHelp();
    const search = screen.getByRole("searchbox", { name: "搜索帮助" });
    await user.type(search, "Qwen3.8 Max");
    expect(screen.getByRole("link", { name: /第一次使用：找到能看图片的模型/ })).toBeInTheDocument();
    expect(screen.getByLabelText("current location")).toHaveTextContent("/help?q=Qwen3.8+Max");
    await user.clear(search);
    await user.type(search, "RAG");
    expect(screen.getByRole("link", { name: /工作台与设置：RAG 知识库/ })).toBeInTheDocument();
  });

  it("offers recovery when search has no result", async () => {
    const user = userEvent.setup();
    renderHelp("/help?q=不存在的帮助词");
    expect(screen.getByText("没有找到相关帮助")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "清除搜索并浏览全部" }));
    expect(screen.getByRole("heading", { name: "第一次使用" })).toBeInTheDocument();
    expect(screen.getByLabelText("current location")).toHaveTextContent("/help");
  });
});

describe("unified help reading shell", () => {
  it.each(helpArticles.map((article) => [article.slug, article.title]))("renders article %s with one h1 and the full directory", (slug, title) => {
    renderHelp(`/help/${slug}`);
    expect(screen.getByRole("heading", { name: title, level: 1 })).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByRole("navigation", { name: "帮助目录" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "安全、费用与数据" }).length).toBeGreaterThan(0);
  });

  it("renders the evidence baseline owned by the current article", () => {
    renderHelp("/help/review-remote-mcp-auth");
    expect(screen.getByText(remoteMcpReviewBaseline.commit)).toBeInTheDocument();
  });

  it("renders a first-level index and module topic in the same shell", () => {
    const first = renderHelp("/help/sections/troubleshooting");
    expect(screen.getByRole("heading", { name: "解决问题", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "配置、权限与连接问题", level: 2 })).toBeInTheDocument();
    first.unmount();
    renderHelp("/help/modules/models/filter-and-compare");
    expect(screen.getByRole("heading", { name: "查找、筛选与比较", level: 1 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "图片理解" }).length).toBeGreaterThan(0);
  });

  it("uses each first-level module page as the complete second-level directory", () => {
    renderHelp("/help/modules/workspace");
    expect(screen.getByRole("heading", { name: "工作台与设置", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "二级功能", level: 2 })).toBeInTheDocument();
    ["经典工作流", "RAG 知识库", "本地数据表", "Coding", "系统设置"].forEach((label) => {
      expect(screen.getAllByRole("link", { name: new RegExp(label) }).length).toBeGreaterThan(0);
    });
    expect(screen.queryByRole("link", { name: /工作流节点/ })).not.toBeInTheDocument();
  });

  it("expands the full goal index beyond the three homepage choices", () => {
    renderHelp("/help/sections/goals");
    expect(screen.getByRole("heading", { name: "按目标找指南", level: 1 })).toBeInTheDocument();
    ["让 AI 使用外部工具", "根据自己的资料回答", "查找运行或连接问题"].forEach((label) => expect(screen.getByRole("link", { name: new RegExp(label) })).toBeInTheDocument());
  });

  it("keeps Expert Team as an Agent second-level index", () => {
    renderHelp("/help/modules/agents/expert-team");
    expect(screen.getByRole("heading", { name: "专家团", level: 1 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "专家团" }).length).toBeGreaterThan(0);
    expect(document.querySelector('a[href="/help/modules/agents/expert-team"]')).toBeInTheDocument();
    expect(document.querySelector('a[href="/help/modules/expert-team"]')).not.toBeInTheDocument();
  });

  it("uses the vacated first-level position for Runtime Ops", () => {
    renderHelp("/help/modules/runtime/run-records");
    expect(screen.getByRole("heading", { name: "运行记录", level: 1 })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "运维" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "打开当前产品入口" })).toHaveAttribute("href", "/runtime");
  });

  it("renders tutorial screenshots with descriptive alternative text", () => {
    renderHelp("/help/start-with-a-model");
    const article = document.querySelector("article.help-article")!;
    within(article as HTMLElement).getAllByRole("img").forEach((image) => expect(image).toHaveAccessibleName());
    expect(article.querySelectorAll("figure")).toHaveLength(2);
    expect(article.querySelectorAll("p figure")).toHaveLength(0);
    expect(article.querySelectorAll("figure figcaption")).toHaveLength(2);
  });

  it("keeps an unknown path inside the help center", () => {
    renderHelp("/help/not-a-real-article");
    expect(screen.getByRole("heading", { name: "这篇帮助不存在", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开帮助首页" })).toHaveAttribute("href", "/help");
  });

  it("shows a verified-date notice on published articles", () => {
    renderHelp("/help/start-with-a-model");
    expect(screen.getByText(`本文基于 ${helpCenterCloseoutBaseline.date} 的界面验证，产品更新后部分按钮名称、入口或价格可能变化。`)).toBeInTheDocument();
  });

  it("does not expose the removed article feedback control", () => {
    renderHelp("/help/start-with-a-model");
    expect(screen.queryByText("这篇对你有帮助吗？")).not.toBeInTheDocument();
  });

});

describe("ResourceNav help entry", () => {
  it("keeps six resource links in each nav and exposes help separately", () => {
    render(<MemoryRouter initialEntries={["/help"]}><ResourceNav /></MemoryRouter>);
    const resourceNavs = screen.getAllByRole("navigation", { name: "资源类型" });
    expect(resourceNavs).toHaveLength(2);
    resourceNavs.forEach((nav) => expect(within(nav).getAllByRole("link")).toHaveLength(6));
    const helpLinks = screen.getAllByRole("link", { name: "帮助" });
    expect(helpLinks).toHaveLength(2);
    helpLinks.forEach((link) => expect(link).toHaveAttribute("aria-current", "page"));
  });
});

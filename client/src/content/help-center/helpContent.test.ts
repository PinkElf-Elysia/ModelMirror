import { describe, expect, it } from "vitest";
import {
  getHelpSearchEntries,
  helpArticles,
  helpModules,
  helpSections,
  searchHelpContent,
  verifiedBaseline,
} from ".";

const requiredMetadata = ["slug", "title", "summary", "category", "contentType", "audience", "estimatedMinutes", "keywords", "relatedRoutes", "verifiedCommit", "verifiedDate", "content"] as const;

describe("help center content catalog", () => {
  it("has five complete first-level sections and eight module groups", () => {
    expect(helpSections.map((section) => section.title)).toEqual(["第一次使用", "按目标找指南", "按模块浏览", "解决问题", "安全、费用与数据"]);
    expect(helpSections.every((section) => section.items.length >= 3)).toBe(true);
    expect(helpSections.find((section) => section.id === "goals")?.items.map((item) => item.id)).toEqual(["one-time", "repeat-role", "repeat-process", "connect-tool", "use-own-docs", "check-runtime"]);
    expect(helpModules.map((module) => module.title)).toEqual(["模型", "Agent", "MCP", "Skill", "提示词", "运维", "工作台与设置", "实验功能"]);
    expect(helpModules.find((module) => module.id === "agents")?.topics.some((topic) => topic.id === "expert-team" && topic.title === "专家团")).toBe(true);
    expect(helpModules.find((module) => module.id === "agents")?.topics.find((topic) => topic.id === "agent-studio")?.productRoute).toBe("/agents/studio");
    expect(helpModules.some((module) => module.id === "expert-team")).toBe(false);
    helpModules.forEach((module) => {
      expect(module.homeTopicIds).toHaveLength(2);
      module.homeTopicIds.forEach((id) => expect(module.topics.some((topic) => topic.id === id)).toBe(true));
    });
  });

  it("keeps the five formal article slugs unique and metadata complete", () => {
    expect(helpArticles.map((article) => article.slug)).toEqual(["start-with-a-model", "choose-model-agent-workflow", "modules-and-terms", "recover-unavailable-feature", "check-availability-cost-data"]);
    expect(new Set(helpArticles.map((article) => article.slug)).size).toBe(helpArticles.length);
    helpArticles.forEach((article) => {
      requiredMetadata.forEach((field) => expect(article[field], `${article.slug}.${field}`).toBeTruthy());
      expect(article.verifiedCommit).toBe(verifiedBaseline.commit);
      expect(article.verifiedCommit).toMatch(/^[0-9a-f]{8}$/);
      expect(article.verifiedDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(article.content).not.toMatch(/内容稍后补充|coming soon/i);
    });
  });

  it("keeps operational articles within the required structure and step count", () => {
    helpArticles.filter((article) => ["tutorial", "how-to"].includes(article.contentType)).forEach((article) => {
      ["完成结果", "适用对象", "开始前", "真实范例", "操作步骤", "常见问题", "限制", "下一步"].forEach((heading) => expect(article.content, `${article.slug}: ${heading}`).toContain(`## ${heading}`));
      const steps = article.content.match(/^\d+\. /gm) ?? [];
      expect(steps.length).toBeGreaterThanOrEqual(5);
      expect(steps.length).toBeLessThanOrEqual(8);
    });
  });

  it("uses descriptive alt text and the latest screenshot baseline", () => {
    helpArticles.forEach((article) => {
      const images = [...article.content.matchAll(/!\[([^\]]*)\]\(([^)]+)\)/g)];
      images.forEach((image) => {
        expect(image[1].trim(), article.slug).not.toBe("");
        expect(image[2]).toMatch(/^\/help-center\/cc49136c\//);
      });
    });
    expect(helpArticles.find((article) => article.slug === "start-with-a-model")?.content).toContain("/help-center/cc49136c/kimi-k3-add-image-menu.png");
    expect(helpArticles.find((article) => article.slug === "start-with-a-model")?.content).not.toContain("kimi-k3-ready-to-send.png");
  });

  it("searches formal articles, indexes, modules, and second-level topics", () => {
    expect(searchHelpContent("Kimi").some((entry) => entry.id === "start-with-a-model")).toBe(true);
    expect(searchHelpContent("配置").some((entry) => entry.id === "troubleshooting")).toBe(true);
    expect(searchHelpContent("RAG").some((entry) => entry.id === "workspace/rag")).toBe(true);
    expect(searchHelpContent("Science").some((entry) => entry.id === "experimental/science")).toBe(true);
    expect(searchHelpContent("专家团").some((entry) => entry.id === "agents/expert-team")).toBe(true);
    expect(searchHelpContent("运行记录").some((entry) => entry.id === "runtime/run-records")).toBe(true);
    const ids = getHelpSearchEntries().map((entry) => `${entry.kind}:${entry.id}`);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

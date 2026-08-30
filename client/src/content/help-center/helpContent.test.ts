import { describe, expect, it } from "vitest";
import {
  getHelpSearchEntries,
  getHelpSearchSuggestions,
  helpArticles,
  helpModules,
  helpSections,
  emailReviewBaseline,
  ragDiversityBaseline,
  ragFormalIntegrityBaseline,
  remoteMcpReviewBaseline,
  rssReviewBaseline,
  searchHelpContent,
  skillExperienceBaseline,
  verifiedBaseline,
  workflowErrorRoutingBaseline,
} from ".";

const requiredMetadata = ["slug", "title", "summary", "category", "contentType", "audience", "estimatedMinutes", "keywords", "relatedRoutes", "verifiedCommit", "verifiedDate", "content"] as const;

describe("help center content catalog", () => {
  it("has five complete first-level sections and eight module groups", () => {
    expect(helpSections.map((section) => section.title)).toEqual(["第一次使用", "按目标找指南", "按模块浏览", "解决问题", "安全、费用与数据"]);
    expect(helpSections.every((section) => section.items.length >= 3)).toBe(true);
    expect(helpSections.find((section) => section.id === "goals")?.items.map((item) => item.id)).toEqual(["one-time", "repeat-role", "repeat-process", "connect-tool", "use-own-docs", "subscribe-feed", "subscribe-email", "propose-knowledge", "check-runtime"]);
    expect(helpModules.map((module) => module.title)).toEqual(["模型", "Agent", "MCP", "Skill", "提示词", "运维", "工作台与设置", "实验功能"]);
    expect(helpModules.find((module) => module.id === "agents")?.topics.some((topic) => topic.id === "expert-team" && topic.title === "专家团")).toBe(true);
    expect(helpModules.find((module) => module.id === "agents")?.topics.find((topic) => topic.id === "agent-studio")?.productRoute).toBe("/agents/studio");
    expect(helpModules.some((module) => module.id === "expert-team")).toBe(false);
    helpModules.forEach((module) => {
      expect(module.homeTopicIds).toHaveLength(2);
      module.homeTopicIds.forEach((id) => expect(module.topics.some((topic) => topic.id === id)).toBe(true));
    });
  });

  it("keeps the formal article slugs unique and metadata complete", () => {
    expect(helpArticles.map((article) => article.slug)).toEqual([
      "start-with-a-model",
      "choose-model-agent-workflow",
      "create-repeatable-agent",
      "build-first-workflow",
      "edit-and-recover-agent-workflow",
      "first-rag-knowledge-base",
      "file-boundaries-and-data-safety",
      "runtime-status-overview",
      "install-or-import-skill",
      "settings-and-provider-layers",
      "promote-run-to-skill",
      "submit-knowledge-proposal",
      "subscribe-rss-workflow",
      "subscribe-email-workflow",
      "handle-workflow-node-failure",
      "modules-and-terms",
      "recover-unavailable-feature",
      "review-remote-mcp-auth",
      "check-availability-cost-data",
    ]);
    expect(new Set(helpArticles.map((article) => article.slug)).size).toBe(helpArticles.length);
    helpArticles.forEach((article) => {
      requiredMetadata.forEach((field) => expect(article[field], `${article.slug}.${field}`).toBeTruthy());
      // 所有公开文章必须绑定真实验证基线，不允许 PENDING 占位进入公开链路。
      expect(article.verifiedCommit).toMatch(/^[0-9a-f]{8}$/);
      expect(article.verifiedDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(article.content).not.toMatch(/内容稍后补充|coming soon/i);
    });
    expect(helpArticles.filter((article) => !["recover-unavailable-feature", "review-remote-mcp-auth", "subscribe-rss-workflow", "subscribe-email-workflow", "promote-run-to-skill", "create-repeatable-agent", "build-first-workflow", "edit-and-recover-agent-workflow", "first-rag-knowledge-base", "file-boundaries-and-data-safety", "runtime-status-overview", "install-or-import-skill", "settings-and-provider-layers", "handle-workflow-node-failure"].includes(article.slug)).every((article) => article.verifiedCommit === verifiedBaseline.commit)).toBe(true);
    expect(helpArticles.find((article) => article.slug === "recover-unavailable-feature")?.verifiedCommit).toBe(ragFormalIntegrityBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "recover-unavailable-feature")?.verifiedDate).toBe(ragFormalIntegrityBaseline.date);
    expect(helpArticles.find((article) => article.slug === "review-remote-mcp-auth")?.verifiedCommit).toBe(remoteMcpReviewBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "subscribe-rss-workflow")?.verifiedCommit).toBe(rssReviewBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "subscribe-email-workflow")?.verifiedCommit).toBe(emailReviewBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "promote-run-to-skill")?.verifiedCommit).toBe(skillExperienceBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "handle-workflow-node-failure")?.verifiedCommit).toBe(workflowErrorRoutingBaseline.commit);
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
        expect(image[2]).toMatch(new RegExp(`^/help-center/${article.verifiedCommit}/`));
      });
    });
    expect(helpArticles.find((article) => article.slug === "start-with-a-model")?.content).toContain("/help-center/cc49136c/kimi-k3-add-image-menu.png");
    expect(helpArticles.find((article) => article.slug === "start-with-a-model")?.content).not.toContain("kimi-k3-ready-to-send.png");
  });

  it("searches formal articles, indexes, modules, and second-level topics", () => {
    expect(searchHelpContent("Kimi").some((entry) => entry.id === "start-with-a-model")).toBe(true);
    expect(searchHelpContent("配置").some((entry) => entry.id === "troubleshooting")).toBe(true);
    expect(searchHelpContent("RAG").some((entry) => entry.id === "workspace/rag")).toBe(true);
    const ragTopic = helpModules.find((module) => module.id === "workspace")?.topics.find((topic) => topic.id === "rag");
    expect(ragTopic?.verifiedCommit).toBe(ragDiversityBaseline.commit);
    expect(ragTopic?.verifiedDate).toBe(ragDiversityBaseline.date);
    expect(ragTopic?.points).toContain("正式评测只接受逐条审核、带 anchor 的锁定晋级集；候选选择集和阈值校准集不能替代最终验收");
    expect(ragTopic?.points).toContain("无答案样例必须同时核对近邻语料和完整语料复核回执");
    expect(searchHelpContent("Science").some((entry) => entry.id === "experimental/science")).toBe(true);
    expect(searchHelpContent("专家团").some((entry) => entry.id === "agents/expert-team")).toBe(true);
    expect(searchHelpContent("运行记录").some((entry) => entry.id === "runtime/run-records")).toBe(true);
    expect(searchHelpContent("Review Factory").some((entry) => entry.id === "review-remote-mcp-auth")).toBe(true);
    expect(searchHelpContent("RSS").some((entry) => entry.id === "subscribe-rss-workflow")).toBe(true);
    expect(searchHelpContent("IMAP").some((entry) => entry.id === "subscribe-email-workflow")).toBe(true);
    expect(searchHelpContent("已处理失败").some((entry) => entry.id === "handle-workflow-node-failure")).toBe(true);
    const ids = getHelpSearchEntries().map((entry) => `${entry.kind}:${entry.id}`);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("recalls content via Chinese synonyms and full-text body", () => {
    // 近义词：日常说法 → 帮助术语
    expect(searchHelpContent("看图").some((entry) => entry.id === "start-with-a-model")).toBe(true);
    expect(searchHelpContent("多步").some((entry) => entry.id === "workspace/workflow")).toBe(true);
    expect(searchHelpContent("收费").some((entry) => entry.id === "check-availability-cost-data")).toBe(true);
    expect(searchHelpContent("运维").some((entry) => entry.id === "runtime")).toBe(true);
    // 全文检索：只出现在正文、不在标题/摘要/关键词的词
    const bodyOnly = searchHelpContent("类型化转换节点").some((entry) => entry.id === "submit-knowledge-proposal");
    expect(bodyOnly).toBe(true);
    // 反向近义词：帮助术语 → 查询
    expect(searchHelpContent("Agent").some((entry) => entry.id === "agents/agent-studio")).toBe(true);
  });

  it("ranks title and article hits above module and body-only hits", () => {
    // "Kimi" 只在入门教程标题命中，应为第一名 article
    const titleHit = searchHelpContent("Kimi")[0];
    expect(titleHit?.kind).toBe("article");
    expect(titleHit?.id).toBe("start-with-a-model");
    // "Agent" 命中大量标题；title 命中的文章/主题应排在 module 之前
    const agentHits = searchHelpContent("Agent");
    expect(agentHits[0]?.kind).toBe("topic");
    expect(agentHits[0]?.id).toBe("agents/agent-studio");
    const moduleIdx = agentHits.findIndex((e) => e.kind === "module" && e.id === "agents");
    expect(moduleIdx).toBeGreaterThan(0);
  });

  it("suggests alternative task words when nothing matches", () => {
    const suggestions = getHelpSearchSuggestions("不存在的词");
    expect(suggestions.length).toBeGreaterThan(0);
    expect(suggestions.some((word) => word === "图片" || word === "费用")).toBe(true);
    const synonymSuggestions = getHelpSearchSuggestions("完全无关的看图");
    expect(synonymSuggestions.some((word) => word === "图片" || word === "图片识别")).toBe(true);
  });
});

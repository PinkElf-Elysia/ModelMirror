import { describe, expect, it } from "vitest";
import {
  agentWorkflowTutorialBaseline,
  getHelpSearchEntries,
  getHelpSearchSuggestions,
  helpCenterCloseoutBaseline,
  helpArticles,
  helpModules,
  helpSections,
  emailReviewBaseline,
  providerMultimodalR8cBaseline,
  ragDiversityBaseline,
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
    expect(helpSections.find((section) => section.id === "goals")?.items.map((item) => item.id)).toEqual(["one-time", "repeat-role", "repeat-process", "reuse-success", "connect-tool", "use-own-docs", "subscribe-feed", "subscribe-email", "propose-knowledge", "check-runtime"]);
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
    expect(helpArticles.filter((article) => !["start-with-a-model", "recover-unavailable-feature", "review-remote-mcp-auth", "subscribe-rss-workflow", "subscribe-email-workflow", "promote-run-to-skill", "choose-model-agent-workflow", "create-repeatable-agent", "build-first-workflow", "handle-workflow-node-failure", "modules-and-terms"].includes(article.slug)).every((article) => article.verifiedCommit === verifiedBaseline.commit)).toBe(true);
    expect(helpArticles.find((article) => article.slug === "start-with-a-model")?.verifiedCommit).toBe(helpCenterCloseoutBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "choose-model-agent-workflow")?.verifiedCommit).toBe(helpCenterCloseoutBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "create-repeatable-agent")?.verifiedCommit).toBe(agentWorkflowTutorialBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "create-repeatable-agent")?.verifiedDate).toBe(agentWorkflowTutorialBaseline.date);
    expect(helpArticles.find((article) => article.slug === "build-first-workflow")?.verifiedCommit).toBe(agentWorkflowTutorialBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "build-first-workflow")?.verifiedDate).toBe(agentWorkflowTutorialBaseline.date);
    expect(helpArticles.find((article) => article.slug === "modules-and-terms")?.verifiedCommit).toBe(helpCenterCloseoutBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "recover-unavailable-feature")?.verifiedCommit).toBe(providerMultimodalR8cBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "recover-unavailable-feature")?.verifiedDate).toBe(providerMultimodalR8cBaseline.date);
    expect(helpArticles.find((article) => article.slug === "recover-unavailable-feature")?.content).toContain("只读刷新模型证据");
    expect(helpArticles.find((article) => article.slug === "review-remote-mcp-auth")?.verifiedCommit).toBe(remoteMcpReviewBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "subscribe-rss-workflow")?.verifiedCommit).toBe(rssReviewBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "subscribe-email-workflow")?.verifiedCommit).toBe(emailReviewBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "promote-run-to-skill")?.verifiedCommit).toBe(skillExperienceBaseline.commit);
    expect(helpArticles.find((article) => article.slug === "handle-workflow-node-failure")?.verifiedCommit).toBe(workflowErrorRoutingBaseline.commit);
  });

  it("places every formal article in a first-level index", () => {
    const indexedPaths = new Set(
      helpSections.flatMap((section) => section.items.map((item) => item.to.split("#")[0])),
    );
    helpArticles.forEach((article) => {
      expect(indexedPaths, article.slug).toContain(`/help/${article.slug}`);
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
        expect(image[2]).toMatch(new RegExp(`^/help-center/${article.verifiedCommit}/`));
      });
    });
    expect(helpArticles.find((article) => article.slug === "start-with-a-model")?.content).toContain("/help-center/b5e0e85e/qwen38-add-image-menu.png");
    expect(helpArticles.find((article) => article.slug === "start-with-a-model")?.content).toContain("/help-center/b5e0e85e/model-market-qwen38-image-understanding.png");
    expect(helpArticles.find((article) => article.slug === "create-repeatable-agent")?.content).toContain("/help-center/b5e0e85e/agent-create-form.png");
    expect(helpArticles.find((article) => article.slug === "create-repeatable-agent")?.content).toContain("/help-center/b5e0e85e/agent-preflight-ready.png");
    expect(helpArticles.find((article) => article.slug === "build-first-workflow")?.content).toContain("/help-center/b5e0e85e/workflow-default-template.png");
    expect(helpArticles.find((article) => article.slug === "build-first-workflow")?.content).toContain("/help-center/b5e0e85e/workflow-draft-saved.png");
    expect(helpArticles.find((article) => article.slug === "recover-unavailable-feature")?.content).toContain("/help-center/ae284fbb/provider-audio-certification-evidence.png");
  });

  it("searches formal articles, indexes, modules, and second-level topics", () => {
    expect(searchHelpContent("Qwen3.8 Max").some((entry) => entry.id === "start-with-a-model")).toBe(true);
    expect(searchHelpContent("配置").some((entry) => entry.id === "troubleshooting")).toBe(true);
    expect(searchHelpContent("RAG").some((entry) => entry.id === "workspace/rag")).toBe(true);
    const ragTopic = helpModules.find((module) => module.id === "workspace")?.topics.find((topic) => topic.id === "rag");
    expect(ragTopic?.verifiedCommit).toBe(ragDiversityBaseline.commit);
    expect(ragTopic?.verifiedDate).toBe(ragDiversityBaseline.date);
    expect(ragTopic?.points).toContain("第一次进入时，先选择“新建知识库”并填写名称");
    expect(ragTopic?.points).toContain("文档上传后，等待页面显示处理完成，再把知识库用于回答");
    expect(ragTopic?.points.join(" ")).not.toMatch(/Formal|Gold|anchor|回执|阈值/);
    expect(searchHelpContent("Science").some((entry) => entry.id === "experimental/science")).toBe(true);
    expect(searchHelpContent("专家团").some((entry) => entry.id === "agents/expert-team")).toBe(true);
    expect(searchHelpContent("运行记录").some((entry) => entry.id === "runtime/run-records")).toBe(true);
    expect(searchHelpContent("Review Factory").some((entry) => entry.id === "review-remote-mcp-auth")).toBe(true);
    expect(searchHelpContent("RSS").some((entry) => entry.id === "subscribe-rss-workflow")).toBe(true);
    expect(searchHelpContent("IMAP").some((entry) => entry.id === "subscribe-email-workflow")).toBe(true);
    expect(searchHelpContent("已处理失败").some((entry) => entry.id === "handle-workflow-node-failure")).toBe(true);
    expect(searchHelpContent("发布预检").some((entry) => entry.id === "create-repeatable-agent")).toBe(true);
    expect(searchHelpContent("保存草稿").some((entry) => entry.id === "build-first-workflow")).toBe(true);
    expect(searchHelpContent("只读刷新模型证据").some((entry) => entry.id === "recover-unavailable-feature")).toBe(true);
    expect(searchHelpContent("受限重试").some((entry) => entry.id === "handle-workflow-node-failure")).toBe(true);
    expect(searchHelpContent("等待重试").some((entry) => entry.id === "handle-workflow-node-failure")).toBe(true);
    const ids = getHelpSearchEntries().map((entry) => `${entry.kind}:${entry.id}`);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("recalls content via Chinese synonyms and full-text body", () => {
    // 近义词：日常说法 → 帮助术语
    expect(searchHelpContent("看图").some((entry) => entry.id === "start-with-a-model")).toBe(true);
    expect(searchHelpContent("多步").some((entry) => entry.id === "workspace/workflow")).toBe(true);
    expect(searchHelpContent("收费").some((entry) => entry.id === "check-availability-cost-data")).toBe(true);
    const everydayCostHits = searchHelpContent("花钱");
    expect(everydayCostHits[0]?.id).toBe("check-availability-cost-data");
    expect(everydayCostHits.length).toBeLessThanOrEqual(8);
    expect(searchHelpContent("运维").some((entry) => entry.id === "runtime")).toBe(true);
    // 全文检索：只出现在正文、不在标题/摘要/关键词的词
    const bodyOnly = searchHelpContent("类型化转换节点").some((entry) => entry.id === "submit-knowledge-proposal");
    expect(bodyOnly).toBe(true);
    // 反向近义词：帮助术语 → 查询
    expect(searchHelpContent("Agent").some((entry) => entry.id === "agents/agent-studio")).toBe(true);
  });

  it("keeps task-oriented search queries precise", () => {
    const cases = [
      { query: "发布前检查", expected: "create-repeatable-agent", forbidden: ["build-first-workflow"] },
      { query: "上传文件会不会外发", expected: "check-availability-cost-data", forbidden: ["models/image-generation"] },
      { query: "工作流保存后怎么运行", expected: "build-first-workflow", forbidden: ["create-repeatable-agent"] },
      { query: "看图片", expected: "start-with-a-model", forbidden: ["models/image-generation"] },
      { query: "生成图片", expected: "models/image-generation", forbidden: ["start-with-a-model"] },
      { query: "外部工具怎么连接", expected: "mcps/connected-registry", forbidden: ["mcps/toolsets"] },
      { query: "工具集在哪里", expected: "mcps/toolsets", forbidden: ["mcps/tool-shelf"] },
      { query: "模型服务连接在哪里", expected: "workspace/settings", forbidden: ["troubleshooting"] },
      { query: "Agent 草稿怎么保存", expected: "create-repeatable-agent", forbidden: ["build-first-workflow"] },
      { query: "工作流节点失败怎么办", expected: "handle-workflow-node-failure", forbidden: ["start-with-a-model"] },
      { query: "功能不可用", expected: "recover-unavailable-feature", forbidden: ["troubleshooting"] },
    ];

    cases.forEach(({ expected, forbidden, query }) => {
      const hits = searchHelpContent(query);
      expect(hits[0]?.id, query).toBe(expected);
      forbidden.forEach((id) => expect(hits.slice(0, 3).map((entry) => entry.id), `${query}: ${id}`).not.toContain(id));
    });
  });

  it("keeps the two closeout tutorials free of unexplained implementation terms", () => {
    const agent = helpArticles.find((article) => article.slug === "create-repeatable-agent")!;
    const workflow = helpArticles.find((article) => article.slug === "build-first-workflow")!;

    expect(agent.content).not.toContain("revision");
    expect(workflow.content).toContain("任务输入框（页面标为 `user_input`）");
    expect(workflow.content).toContain("表示草稿已经保存");
  });

  it("keeps ordinary-user safety and recovery copy free of operator internals", () => {
    const safety = helpArticles.find((article) => article.slug === "check-availability-cost-data")!;
    const recovery = helpArticles.find((article) => article.slug === "recover-unavailable-feature")!;
    expect(`${safety.content}\n${recovery.content}`).not.toMatch(/mmbatch_|local_non_model_fallback|R8[A-F]|Promotion Gate|Formal|Gold|幂等键/);
  });

  it("ranks title and article hits above module and body-only hits", () => {
    // 精确的模型名应优先召回对应教程
    const titleHit = searchHelpContent("Qwen3.8 Max")[0];
    expect(titleHit?.kind).toBe("article");
    expect(titleHit?.id).toBe("start-with-a-model");
    // "Agent" 命中多类内容；任务指南在前，具体主题也应排在模块总页之前
    const agentHits = searchHelpContent("Agent");
    expect(agentHits[0]?.kind).toBe("article");
    expect(agentHits[0]?.id).toBe("choose-model-agent-workflow");
    const studioIdx = agentHits.findIndex((entry) => entry.kind === "topic" && entry.id === "agents/agent-studio");
    const moduleIdx = agentHits.findIndex((e) => e.kind === "module" && e.id === "agents");
    expect(studioIdx).toBeGreaterThan(0);
    expect(moduleIdx).toBeGreaterThan(studioIdx);
    expect(searchHelpContent("智能路由")[0]?.id).toBe("models/smart-router");
  });

  it("suggests alternative task words when nothing matches", () => {
    const suggestions = getHelpSearchSuggestions("不存在的词");
    expect(suggestions.length).toBeGreaterThan(0);
    expect(suggestions.some((word) => word === "图片" || word === "费用")).toBe(true);
    const synonymSuggestions = getHelpSearchSuggestions("完全无关的看图");
    expect(synonymSuggestions.some((word) => word === "图片" || word === "图片识别")).toBe(true);
  });
});

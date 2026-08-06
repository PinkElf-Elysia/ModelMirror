import { describe, expect, it } from "vitest";
import {
  findSkillsForNeed,
  type SkillNeedCandidate,
} from "./skillNeedMatcher";

const candidates: SkillNeedCandidate[] = [
  {
    id: "playwright-member",
    name: "playwright-browser-testing",
    category: "开发与测试",
    kind: "skill",
    description: "使用 Playwright 验证网页交互、错误状态和截图。",
    sourceDescription: "Test web applications with Playwright and browser screenshots.",
    tags: ["Playwright", "E2E", "browser"],
    pathTerms: ["skills/testing/playwright-browser-testing"],
    parentNames: ["Web quality skills"],
    installStatus: "ready",
  },
  {
    id: "generic-parent",
    name: "Web quality skills",
    category: "开发与测试",
    kind: "skillset",
    description: "网页开发与质量检查技能集合。",
    sourceDescription: "A collection of web development skills.",
    tags: ["web"],
    installStatus: "ready",
  },
  {
    id: "postgres-pending",
    name: "Postgres 权限审计",
    category: "安全与运维",
    kind: "skill",
    description: "检查 PostgreSQL 角色、权限和访问控制。",
    sourceDescription: "Audit PostgreSQL roles and access controls.",
    tags: ["Postgres", "security", "audit"],
    installStatus: "pending",
  },
  {
    id: "deprecated-exact",
    name: "Playwright testing legacy",
    category: "开发与测试",
    kind: "skill",
    description: "旧版网页测试流程。",
    sourceDescription: "Deprecated. Use playwright-browser-testing instead.",
    tags: ["Playwright"],
    installStatus: "ready",
    deprecated: true,
  },
  {
    id: "negative-clause",
    name: "SEO metadata",
    category: "营销与增长",
    kind: "skill",
    description: "生成网页搜索元数据。",
    sourceDescription: "Create SEO metadata. Not for database security audits.",
    searchDescription: "Create SEO metadata.",
    tags: ["SEO"],
    installStatus: "ready",
  },
];

describe("findSkillsForNeed", () => {
  it("让精确成员排在泛化父集合之前并解释命中来源", () => {
    const matches = findSkillsForNeed(
      "用 Playwright 验收网页交互并截图",
      candidates,
    );
    expect(matches[0].project.id).toBe("playwright-member");
    expect(matches.some((match) => match.project.id === "deprecated-exact")).toBe(
      false,
    );
    expect(matches[0].reasons.some((reason) => reason.origin === "direct")).toBe(
      true,
    );
    expect(matches[0].reasons.every((reason) => reason.matchedTerms.length > 0)).toBe(
      true,
    );
  });

  it("保留高度相关但待核验的结果", () => {
    const matches = findSkillsForNeed("audit postgres database permissions", candidates);
    expect(matches[0].project.id).toBe("postgres-pending");
    expect(matches[0].project.installStatus).toBe("pending");
    expect(matches.some((match) => match.project.id === "negative-clause")).toBe(
      false,
    );
  });

  it("结果稳定、限制为六项且无可靠结果时返回空数组", () => {
    const many = Array.from({ length: 10 }, (_, index) => ({
      ...candidates[0],
      id: `web-${index}`,
      name: `Playwright testing ${index}`,
    }));
    const first = findSkillsForNeed("Playwright testing", many);
    const second = findSkillsForNeed("Playwright testing", many);
    expect(first).toHaveLength(6);
    expect(first.map((match) => match.project.id)).toEqual(
      second.map((match) => match.project.id),
    );
    expect(findSkillsForNeed("量子引力弦理论实验", candidates)).toEqual([]);
  });
});

import { describe, expect, it } from "vitest";
import { loadSkillNeedCandidates } from "./skillNeedCandidates";
import { findSkillsForNeed } from "./skillNeedMatcher";

describe("完整 Skill Finder 候选", () => {
  it("懒加载全部顶层条目与已核验成员并保持来源唯一", async () => {
    const candidates = await loadSkillNeedCandidates();
    const members = candidates.filter((candidate) => candidate.targetType === "member");
    expect(candidates.length).toBeGreaterThan(4_500);
    expect(members.length).toBeGreaterThan(3_000);

    const sourceKeys = members.map(
      (candidate) =>
        `${candidate.installSource.repoUrl.toLowerCase()}#${candidate.installSource.subPath}#${candidate.installSource.verifiedCommit}`,
    );
    expect(new Set(sourceKeys).size).toBe(sourceKeys.length);
    expect(
      members.every((candidate) => /^[a-f0-9]{40}$/.test(candidate.installSource.verifiedCommit)),
    ).toBe(true);
  });

  it("直接召回成员、保留集合上下文并返回固定提交安装源", async () => {
    const candidates = await loadSkillNeedCandidates();
    const matches = findSkillsForNeed("track SERP rankings and SEO features", candidates);
    expect(matches.length).toBeGreaterThan(0);
    const memberMatch = matches.find((match) => match.project.targetType === "member");
    expect(memberMatch).toBeDefined();
    if (!memberMatch || memberMatch.project.targetType !== "member") return;
    expect(memberMatch.project.parentSkillSets.length).toBeGreaterThan(0);
    expect(memberMatch.project.sourceDescription).toBeTruthy();
    expect(memberMatch.project.installSource.verifiedCommit).toMatch(/^[a-f0-9]{40}$/);
  });

  it("覆盖中英文典型需求并为每项结果提供可追溯理由", async () => {
    const candidates = await loadSkillNeedCandidates();
    for (const query of [
      "用 Playwright 验收登录页并截图",
      "审计 Postgres 数据库权限",
      "分析 Excel 销售数据",
      "create a Figma design system",
    ]) {
      const matches = findSkillsForNeed(query, candidates);
      expect(matches.length).toBeGreaterThan(0);
      expect(matches.length).toBeLessThanOrEqual(6);
      expect(matches.every((match) => match.reasons.length > 0)).toBe(true);
    }
  });
});

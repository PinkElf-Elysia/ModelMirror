import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SkillCreatorDraft, SkillCreatorProposal } from "../../utils/skillCreatorApi";
import SkillProposalReview from "./SkillProposalReview";

function proposal(status: SkillCreatorProposal["status"]): SkillCreatorProposal {
  return {
    proposal_id: `proposal-${status}`,
    kind: "skill_update",
    title: "更新 PDF 审计 Skill",
    status,
    revision: 3,
    creator_session_id: "creator-1",
    apply_key: `apply-${status}`,
    payload_digest: "a".repeat(64),
    content_digest: "b".repeat(64),
    payload: {
      root_name: "pdf-audit",
      name: "pdf-audit",
      description: "审计 PDF 并保留页码。",
      skill_markdown: "---\nname: pdf-audit\ndescription: 审计 PDF 并保留页码。\n---\n\n# PDF 审计",
      files: {},
    },
    validation: {
      valid: true,
      validator_version: "skill-package-v2.1",
      issues: [],
    },
  };
}

describe("SkillProposalReview terminal states", () => {
  it("separates structural validity, draft completeness, and the future behavior evaluation", () => {
    render(
      <SkillProposalReview
        approving={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        proposal={proposal("pending")}
        rejecting={false}
      />,
    );

    expect(screen.getByRole("heading", { name: "结构与安全" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "初稿完整度" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "行为评测" })).toBeVisible();
    expect(screen.getByText("校验通过")).toBeVisible();
    expect(screen.getByText("后端未报告")).toBeVisible();
    expect(screen.getByText("PR3 尚未开放")).toBeVisible();
    expect(screen.getByText("兼容旧响应；结构通过不等于内容完整。批准仍由服务端规则决定。")).toBeVisible();
  });

  it("reads nested Creator quality and blocks approval when completeness fails", () => {
    const incomplete = proposal("pending");
    incomplete.validation = {
      ...incomplete.validation!,
      valid: false,
      creator_quality: {
        ready: false,
        version: "creator-quality-v1",
        score: 42,
        checks: [
          { code: "package_structure", label: "包结构与安全", passed: true },
          {
            code: "creator_failure_behavior_missing",
            label: "失败与降级处理",
            passed: false,
            message: "缺少失败处理。",
          },
          {
            code: "inputs_preconditions",
            label: "输入与前置条件",
            passed: false,
            message: "缺少输入要求。",
          },
        ],
        issues: [],
      },
    };

    render(
      <SkillProposalReview
        approving={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        proposal={incomplete}
        rejecting={false}
      />,
    );

    expect(screen.getByText("校验通过")).toBeVisible();
    expect(screen.getByText("未达到门槛")).toBeVisible();
    expect(screen.getByText("失败与降级处理")).toBeVisible();
    expect(screen.getByText("输入与前置条件")).toBeVisible();
    expect(screen.getByRole("button", { name: "批准并写入草稿" })).toBeDisabled();
  });

  it("accepts a top-level Creator quality report without confusing it with behavior evaluation", () => {
    const ready = proposal("pending");
    ready.creator_quality = {
      ready: true,
      version: "creator-quality-v1",
      score: 88,
      checks: [{ code: "package_structure", label: "包结构与安全", passed: true }],
      issues: [],
    };

    render(
      <SkillProposalReview
        approving={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        proposal={ready}
        rejecting={false}
      />,
    );

    expect(screen.getByText("门槛通过 · 88 分")).toBeVisible();
    expect(screen.getByText("PR3 尚未开放")).toBeVisible();
    expect(screen.getByRole("button", { name: "批准并写入草稿" })).toBeEnabled();
  });

  it.each([
    ["rejected", "提案已拒绝", "该提案没有写入草稿。你可以保留当前草稿并重新生成提案。"],
    ["cancelled", "提案已取消", "该提案没有写入草稿，可能已被新的生成请求替换。你可以重新生成提案。"],
    ["conflict", "提案与当前草稿冲突", "提案没有写入草稿。请重新加载当前版本，再生成更新提案。"],
  ] as const)("explains %s without claiming it was approved", (status, title, detail) => {
    render(
      <SkillProposalReview
        approving={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        proposal={proposal(status)}
        rejecting={false}
      />,
    );

    expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    expect(screen.getByText(detail)).toBeVisible();
    expect(screen.queryByText(/已批准并写入草稿/)).not.toBeInTheDocument();
  });

  it("allows an invalid no-change pending proposal to be discarded with a reason", async () => {
    const pending = proposal("pending");
    pending.validation = {
      valid: false,
      validator_version: "skill-package-v2.1",
      issues: [{
        code: "skill_package_invalid",
        message: "缺少必要步骤。",
        severity: "error",
        path: "SKILL.md",
      }],
    };
    const payload = pending.payload as Exclude<SkillCreatorProposal["payload"], { skill: unknown }>;
    const matchingDraft: SkillCreatorDraft = {
      ...payload,
      draft_id: "draft-1",
      slug: payload.root_name,
      status: "draft",
      revision: 1,
      content_revision: 1,
      content_digest: pending.content_digest,
    };
    const onReject = vi.fn(async () => undefined);

    render(
      <SkillProposalReview
        approving={false}
        baseDraft={matchingDraft}
        onApprove={vi.fn()}
        onReject={onReject}
        proposal={pending}
        rejecting={false}
      />,
    );

    expect(screen.getByRole("button", { name: "批准并写入草稿" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "丢弃提案" }));
    const confirmDiscard = screen.getByRole("button", { name: "确认丢弃提案" });
    expect(confirmDiscard).toBeDisabled();
    await userEvent.type(screen.getByLabelText("简短原因"), "内容不符合预期");
    await userEvent.click(confirmDiscard);

    expect(onReject).toHaveBeenCalledWith("内容不符合预期");
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  SkillRerankEvaluation,
  SkillRerankPolicyStatus,
} from "../utils/skillRerankApi";
import SkillRerankGovernancePage from "./SkillRerankGovernancePage";

const api = vi.hoisted(() => ({
  clearSkillRerankFeedback: vi.fn(),
  promoteSkillRerankPolicy: vi.fn(),
  readSkillRerankEvaluation: vi.fn(),
  readSkillRerankPolicy: vi.fn(),
  rollbackSkillRerankPolicy: vi.fn(),
  startSkillRerankEvaluation: vi.fn(),
}));

vi.mock("../utils/skillRerankApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../utils/skillRerankApi")>()),
  ...api,
}));

const evaluation: SkillRerankEvaluation = {
  evaluationId: "skill_rerank_eval_1",
  revision: 3,
  status: "completed",
  createdAt: 1,
  completedAt: 2,
  errorCode: null,
  provider: "api",
  model: "reranker-v1",
  baseline: {
    recallAt24: 1,
    mrrAt6: 0.4,
    nDCGAt6: 0.5,
    top1: 0.3,
    nearMissFalsePositiveRate: 0.1,
  },
  semantic: {
    recallAt24: 1,
    mrrAt6: 0.44,
    nDCGAt6: 0.54,
    top1: 0.4,
    nearMissFalsePositiveRate: 0.1,
    providerSuccessRate: 1,
    p95DurationMs: 220,
  },
  feedbackSummary: {
    sampleCount: 2,
    relevantCount: 1,
    notRelevantCount: 1,
    relevantNonWorseCount: 1,
    irrelevantNonWorseCount: 1,
  },
  caseReports: [
    {
      caseId: "router-near-miss-01",
      kind: "near_miss",
      scope: "router",
      status: "lexical_fallback",
      fallbackReason: "api_timeout",
      durationMs: 3000,
      rankChanges: 0,
    },
  ],
  gates: [{ code: "gold_cases_complete", passed: true, details: {} }],
  eligibleForPromotion: true,
};

function policy(overrides: Partial<SkillRerankPolicyStatus> = {}): SkillRerankPolicyStatus {
  return {
    provider: "api",
    providerAvailable: true,
    apiAvailable: true,
    llmAvailable: false,
    routerMode: "shadow",
    effectiveRouterMode: "shadow",
    searchIndexFingerprint: "a".repeat(64),
    governanceAvailable: true,
    governanceRevision: 4,
    feedbackCount: 2,
    evaluationCount: 1,
    evaluations: [evaluation],
    policyReasons: ["semantic_router_not_promoted"],
    warnings: [],
    policy: { revision: 1, mode: "shadow", promotion: null, updatedAt: 1 },
    shadow: {
      sampleCount: 10,
      changedCount: 4,
      fallbackCount: 1,
      fallbackRate: 0.1,
      p95DurationMs: 200,
      fallbackReasons: { api_timeout: 1 },
    },
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("SkillRerankGovernancePage", () => {
  it("restores the latest evaluation and exposes all promotion evidence", async () => {
    api.readSkillRerankPolicy.mockResolvedValue(policy());
    render(
      <MemoryRouter>
        <SkillRerankGovernancePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("评测已完成")).toBeInTheDocument();
    expect(screen.getByText("达到晋级门槛")).toBeInTheDocument();
    expect(screen.getByText("固定金标全部完成")).toBeInTheDocument();
    expect(screen.getByText("100.0%")).toBeInTheDocument();
    expect(screen.getByText("失败或降级用例")).toBeInTheDocument();
    expect(screen.getByText(/router-near-miss-01 · api_timeout/)).toBeInTheDocument();
    expect(screen.getByText(/相关 1 条；不相关 1 条/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认晋级 Router" })).toBeEnabled();
  });

  it("starts an evaluation with the current governance revision", async () => {
    const queued = { ...evaluation, status: "queued" as const, eligibleForPromotion: false };
    api.readSkillRerankPolicy.mockResolvedValue(policy({ evaluations: [] }));
    api.startSkillRerankEvaluation.mockResolvedValue(queued);
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SkillRerankGovernancePage />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: "运行固定评测" }));
    expect(api.startSkillRerankEvaluation).toHaveBeenCalledWith(4);
    expect(await screen.findByText("评测运行中")).toBeInTheDocument();
  });

  it("requires confirmation before immediate rollback", async () => {
    api.readSkillRerankPolicy.mockResolvedValue(
      policy({
        routerMode: "on",
        effectiveRouterMode: "on",
        policy: { revision: 2, mode: "on", promotion: {}, updatedAt: 2 },
      }),
    );
    api.rollbackSkillRerankPolicy.mockResolvedValue({
      status: policy({ effectiveRouterMode: "shadow" }),
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <SkillRerankGovernancePage />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole("button", { name: /立即回退词典排序/ }));
    await waitFor(() => expect(api.rollbackSkillRerankPolicy).toHaveBeenCalledWith(4));
    expect(await screen.findByText(/Router 已恢复影子模式/)).toBeInTheDocument();
  });

  it("fails closed when governance storage is unavailable", async () => {
    api.readSkillRerankPolicy.mockResolvedValue(
      policy({ governanceAvailable: false, providerAvailable: false, evaluations: [] }),
    );
    render(
      <MemoryRouter>
        <SkillRerankGovernancePage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("治理 Store 不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "运行固定评测" })).toBeDisabled();
  });
});

import { describe, expect, it } from "vitest";
import {
  canApproveGoldReview,
  summarizeFormalReadiness,
  summarizeGoldReview,
} from "./KnowledgeEvaluationPage";

describe("summarizeGoldReview", () => {
  it("requires review for every Gold v2 positive and hard negative", () => {
    const cases = [
      ...Array.from({ length: 30 }, (_, index) => ({
        case_id: `positive-${index}`,
        query: `positive ${index}`,
        expected_refs: [{ document_id: "doc", relevance: 3 }],
        expected_no_result: false,
        review_status: index < 29 ? "approved" as const : "pending" as const,
        review_evidence: index < 29 ? { reason: "checked" } : {},
        targeting: { query_type: "factual_lookup", locale: index < 15 ? "zh-CN" : "en-US" },
        tags: [],
        notes: "",
      })),
      ...Array.from({ length: 12 }, (_, index) => ({
        case_id: `negative-${index}`,
        query: `negative ${index}`,
        expected_refs: [],
        expected_no_result: true,
        review_status: index === 11 ? "rejected" as const : "approved" as const,
        review_evidence: {},
        targeting: { query_type: "no_result", locale: index < 6 ? "zh-CN" : "en-US" },
        tags: ["corpus_near", "hard_negative"],
        notes: "",
      })),
    ];

    const summary = summarizeGoldReview({
      cases,
      provenance: { benchmark_contract_version: "rag-gold-v2" },
      qualification_manifest: {
        qualified: false,
        checks: [
          { id: "manual_reviews", passed: false },
          { id: "source_block_reuse", passed: false },
        ],
      },
    });

    expect(summary.required).toBe(42);
    expect(summary.approved).toBe(40);
    expect(summary.pending).toBe(1);
    expect(summary.rejected).toBe(1);
    expect(summary.reviewAllowed).toBe(false);
    expect(summary.reviewBlockers).toContain("source_block_reuse");
    expect(summary.readyForCalibration).toBe(false);
    expect(summary.blockers).toContain("仍有 1 条待审核");
    expect(summary.blockers).toContain("有 1 条已拒绝，需替换或修改后重新审核");
    expect(summary.blockers).toContain("质量检查未通过：source_block_reuse");
  });

  it("keeps review-stage leakage decisions available after structural checks pass", () => {
    const cases = [
      ...Array.from({ length: 30 }, (_, index) => ({
        case_id: `positive-${index}`,
        query: `positive ${index}`,
        expected_refs: [{ document_id: "doc", relevance: 3 }],
        expected_no_result: false,
        review_status: "pending" as const,
        review_evidence: {},
        targeting: { query_type: "factual_lookup", locale: index < 15 ? "zh-CN" : "en-US" },
        tags: [],
        notes: "",
      })),
      ...Array.from({ length: 12 }, (_, index) => ({
        case_id: `negative-${index}`,
        query: `negative ${index}`,
        expected_refs: [],
        expected_no_result: true,
        review_status: "pending" as const,
        review_evidence: {},
        targeting: { query_type: "no_result", locale: index < 6 ? "zh-CN" : "en-US" },
        tags: ["corpus_near", "hard_negative"],
        notes: "",
      })),
    ];

    const summary = summarizeGoldReview({
      cases,
      provenance: { benchmark_contract_version: "rag-gold-v2" },
      qualification_manifest: {
        qualified: false,
        checks: [
          { id: "manual_reviews", passed: false },
          { id: "leakage_warning_reasons", passed: false },
          { id: "no_blocking_leakage", passed: false },
        ],
      },
    });

    expect(summary.reviewAllowed).toBe(true);
    expect(summary.reviewBlockers).toEqual([]);
    expect(summary.readyForCalibration).toBe(false);
  });
});

describe("summarizeFormalReadiness", () => {
  it("requires qualified Gold v2 and exactly one baseline plus candidate", () => {
    const version = {
      benchmark_contract_version: "rag-gold-v2",
      qualification_manifest: { qualified: true },
    };

    expect(summarizeFormalReadiness(version, ["base", "candidate"], "base")).toEqual({
      ready: true,
      blockers: [],
    });
    expect(summarizeFormalReadiness(version, ["base"], "base").ready).toBe(false);
    expect(
      summarizeFormalReadiness(
        { benchmark_contract_version: "rag-gold-v1", qualification_manifest: { qualified: true } },
        ["base", "candidate"],
        "base",
      ).blockers,
    ).toContain("请选择已发布且合格的 rag-gold-v2");
  });
});

describe("canApproveGoldReview", () => {
  it("never enables approval for source leakage at the blocking threshold", () => {
    expect(canApproveGoldReview({
      reviewAllowed: true,
      evidenceLoaded: true,
      reviewBusy: false,
      leakageWarning: true,
      leakageBlocked: true,
      reason: "The reviewer entered a reason.",
    })).toBe(false);
  });
});

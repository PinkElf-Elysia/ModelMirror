import { describe, expect, it } from "vitest";
import { isCaseReviewEvidenceReady, isEvaluationPromotionReady, isFormalEvaluationVersionEligible } from "./KnowledgeEvaluationPage";

const passedTarget = {
  promotion_gate: { passed: true },
  execution_integrity: { qualified: true },
};

describe("isEvaluationPromotionReady", () => {
  it("allows a succeeded, current Formal run with qualified execution evidence", () => {
    expect(isEvaluationPromotionReady({
      status: "succeeded",
      run_mode: "formal",
      reproducibility_status: "current",
    }, passedTarget)).toBe(true);
  });

  it("rejects a historical passed gate when its references are no longer reproducible", () => {
    expect(isEvaluationPromotionReady({
      status: "succeeded",
      run_mode: "formal",
      reproducibility_status: "orphaned",
    }, passedTarget)).toBe(false);
  });

  it("rejects a current run with incomplete execution evidence", () => {
    expect(isEvaluationPromotionReady({
      status: "succeeded",
      run_mode: "formal",
      reproducibility_status: "current",
    }, {
      promotion_gate: { passed: true },
      execution_integrity: { qualified: false },
    })).toBe(false);
  });
});

describe("isFormalEvaluationVersionEligible", () => {
  const qualifiedHeldOut = {
    benchmark_contract_version: "rag-gold-v3" as const,
    benchmark_role: "held_out_qualification" as const,
    qualification_manifest: {
      status: "qualified",
      dataset_role: "held_out_qualification",
      tuner_usage_lineage: [],
    },
  };

  it("accepts only an unused qualified held-out rag-gold-v3 version", () => {
    expect(isFormalEvaluationVersionEligible(qualifiedHeldOut)).toBe(true);
  });

  it("rejects tuning data and held-out data exposed to the tuner", () => {
    expect(isFormalEvaluationVersionEligible({
      ...qualifiedHeldOut,
      benchmark_role: "strategy_tuning",
    })).toBe(false);
    expect(isFormalEvaluationVersionEligible({
      ...qualifiedHeldOut,
      qualification_manifest: {
        ...qualifiedHeldOut.qualification_manifest,
        tuner_usage_lineage: [{ run_id: "tuner_run" }],
      },
    })).toBe(false);
  });

  it("rejects legacy qualification evidence", () => {
    expect(isFormalEvaluationVersionEligible({
      ...qualifiedHeldOut,
      benchmark_contract_version: "rag-gold-v2",
    })).toBe(false);
  });
});

describe("isCaseReviewEvidenceReady", () => {
  it("requires the completed full-corpus receipt for a no-result review", () => {
    const evidence = [{ document_id: "doc-a" }];
    expect(isCaseReviewEvidenceReady(
      { expected_no_result: true },
      { evidence, full_corpus_verification: { completed: true } },
    )).toBe(true);
    expect(isCaseReviewEvidenceReady(
      { expected_no_result: true },
      { evidence, full_corpus_verification: { completed: false } },
    )).toBe(false);
  });

  it("allows positive review after canonical evidence is loaded", () => {
    expect(isCaseReviewEvidenceReady(
      { expected_no_result: false },
      { evidence: [{ anchor_hash: "a".repeat(64) }] },
    )).toBe(true);
  });
});

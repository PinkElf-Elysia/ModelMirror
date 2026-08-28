import { describe, expect, it } from "vitest";
import { isEvaluationPromotionReady } from "./KnowledgeEvaluationPage";

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

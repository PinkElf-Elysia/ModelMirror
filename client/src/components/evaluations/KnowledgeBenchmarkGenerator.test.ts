import { describe, expect, it } from "vitest";
import { isFormalGoldGenerationConfigurationValid } from "./KnowledgeBenchmarkGenerator";

const qualified = {
  caseCount: 42,
  noResultCount: 12,
  locales: ["zh-CN", "en-US"],
  coverage: [
    "factual_lookup",
    "paraphrase",
    "section_context",
    "cross_language",
    "multi_evidence",
    "confusable_content",
  ],
};

describe("rag-gold-v2 generation configuration", () => {
  it("accepts only the fixed 42-case bilingual six-type matrix", () => {
    expect(isFormalGoldGenerationConfigurationValid(qualified)).toBe(true);
    expect(isFormalGoldGenerationConfigurationValid({ ...qualified, caseCount: 43 })).toBe(false);
    expect(isFormalGoldGenerationConfigurationValid({ ...qualified, noResultCount: 0 })).toBe(false);
    expect(isFormalGoldGenerationConfigurationValid({ ...qualified, locales: ["en-US"] })).toBe(false);
    expect(isFormalGoldGenerationConfigurationValid({ ...qualified, coverage: ["factual_lookup"] })).toBe(false);
  });
});

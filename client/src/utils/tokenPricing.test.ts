import { describe, expect, it } from "vitest";
import { models } from "../data/models";
import {
  formatPricingOverridesCny,
  tokenPricingForPrompt,
} from "./tokenPricing";

describe("tokenPricingForPrompt", () => {
  it("selects Seed 2.0 Code long-context pricing at 128K", () => {
    const model = models.find(
      (candidate) => candidate.id === "bytedance-seed/seed-2.0-code",
    );
    expect(model).toBeDefined();
    expect(tokenPricingForPrompt(model!, 127_999)).toEqual({
      input: 0.5,
      output: 3,
    });
    expect(tokenPricingForPrompt(model!, 128_000)).toEqual({
      input: 1,
      output: 6,
    });
  });

  it("formats Grok 4.6 tiered pricing for the catalog UI", () => {
    const model = models.find(
      (candidate) => candidate.id === "x-ai/grok-4.6",
    );
    expect(model).toBeDefined();
    expect(tokenPricingForPrompt(model!, 200_000)).toEqual({
      input: 4,
      output: 12,
    });
    expect(formatPricingOverridesCny(model!)).toBe(
      "≥200K：输入 ¥27.08 / 输出 ¥81.24 / M",
    );
  });
});

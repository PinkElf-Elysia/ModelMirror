import { describe, expect, it } from "vitest";
import { models } from "../data/models";
import {
  formatPricingOverridesCny,
  formatUtcClock,
  formatUtcPricingWindow,
  priceCnyForUtcTime,
  pricingWindowForUtcTime,
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

  it("formats UTC HHMM windows and marks overnight ranges", () => {
    expect(formatUtcClock(100)).toBe("01:00");
    expect(
      formatUtcPricingWindow({ utc_start: 1000, utc_end: 100 }),
    ).toBe("10:00–次日 01:00");
    expect(
      formatUtcPricingWindow({ utc_start: 400, utc_end: 600 }),
    ).toBe("04:00–06:00");
  });

  it("selects the live UTC price with inclusive start and exclusive end", () => {
    const model = models.find(
      (candidate) => candidate.id === "deepseek/deepseek-v4-pro-0813",
    );
    expect(model).toBeDefined();

    const highPriceAtStart = new Date("2026-08-17T01:00:00Z");
    const lowPriceAtEnd = new Date("2026-08-17T04:00:00Z");
    const overnightPrice = new Date("2026-08-17T23:30:00Z");

    expect(pricingWindowForUtcTime(model!, highPriceAtStart)).toMatchObject({
      utc_start: 100,
      utc_end: 400,
    });
    expect(priceCnyForUtcTime(model!, highPriceAtStart)).toEqual({
      input: 8.94,
      output: 26.81,
    });
    expect(priceCnyForUtcTime(model!, lowPriceAtEnd)).toEqual({
      input: 4.47,
      output: 13.4,
    });
    expect(pricingWindowForUtcTime(model!, overnightPrice)).toMatchObject({
      utc_start: 1000,
      utc_end: 100,
    });
  });
});

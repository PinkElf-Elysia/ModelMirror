import { describe, expect, it } from "vitest";
import { models } from "./models";
import { chatModelOptions } from "./modelOptions";

describe("October 13 contracts and placement", () => {
  it("reserves the requested flagship and front-six-row models without duplicates", () => {
    expect(models[0].id).toBe("anthropic/claude-opus-5.5");
    for (const id of ["google/gemini-4-argon", "openai/gpt-6.1-sol", "anthropic/claude-sonnet-5.5", "openai/gpt-6-luna", "anthropic/claude-haiku-5.5", "anthropic/claude-fable-5.1"]) {
      expect(models.filter((model) => model.id === id)).toHaveLength(1);
      const index = models.findIndex((model) => model.id === id);
      expect(index).toBeGreaterThan(1);
      expect(index).toBeLessThan(20);
    }
    expect(new Set(models.map((model) => model.id)).size).toBe(models.length);
  });
  it("keeps ScaleDown specialized and behind six rows", () => {
    for (const id of ["scaledown/summarize", "scaledown/extract", "scaledown/classify-1"]) {
      const model = models.find((entry) => entry.id === id);
      expect(model).toMatchObject({ catalog_status: "live", context_length: 1000000, pricing: { input: 0.04, output: 0 }, ui_entrypoint: id.endsWith("classify-1") ? "decisions" : "scaledown" });
      expect(model?.note).toContain("尚未适配");
      expect(chatModelOptions.some((entry) => entry.id === id)).toBe(false);
      expect(models.findIndex((entry) => entry.id === id)).toBeGreaterThanOrEqual(20);
    }
  });
  it("preserves Argon's reasoning and attaches only a text-only Batch serving tier", () => {
    const model = models.find((entry) => entry.id === "google/gemini-4-argon");
    expect(model).toMatchObject({ context_length: 1048576, pricing: { input: 2, output: 10 }, input_modalities: ["audio", "file", "image", "text", "video"], output_modalities: ["text"], ui_entrypoint: "chat", reasoning_declared: true });
    expect(model?.serving_variants.find((variant) => variant.type === "batch")).toMatchObject({
      catalog_id: "google/gemini-4-argon:batch", request_model_id: "google/gemini-4-argon",
      pricing: { input: 1, output: 5 }, input_modalities: ["text"], output_modalities: ["text"], completion_window: "24h", data_retention_days: 30,
    });
    expect(models.some((entry) => entry.id.endsWith("argon:batch"))).toBe(false);
  });
  it("adapts the second-window entries below six rows with explicit speech units", () => {
    for (const id of ["~unbiased/pareto-latest", "unbiased/pareto-26.9", "heygen/heygen-voice-1", "heygen/heygen-voice-1-turbo", "liquid/lfm-2.5-24b-a2b", "fish-audio/drama-3"]) {
      expect(models.filter((model) => model.id === id)).toHaveLength(1);
      expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    }
    for (const [id, unit, usd] of [["heygen/heygen-voice-1", "character_million", 15], ["heygen/heygen-voice-1-turbo", "character_million", 4], ["fish-audio/drama-3", "utf8_byte_million", 2]]) {
      expect(models.find((model) => model.id === id)).toMatchObject({ pricing_basis: "media", media_pricing: { unit, usd }, primary_operation: "synthesize_speech" });
    }
    expect(models.find((model) => model.id === "~unbiased/pareto-latest")?.note).toContain("unbiased/pareto-26.9");
    expect(models.find((model) => model.id === "liquid/lfm-2.5-24b-a2b")).toMatchObject({ context_length: 128000, reasoning_declared: true });
  });
});

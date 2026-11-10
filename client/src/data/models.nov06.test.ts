import { describe, expect, it } from "vitest";
import { models } from "./models";

describe("November 6 refresh", () => {
  it("keeps Parallel on request-priced Decisions, not free chat", () => {
    const id = "parallel/decisions-fast";
    expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    expect(models.find((model) => model.id === id)).toMatchObject({primary_operation:"decide", ui_entrypoint:"decisions", pricing_basis:"request", pricing_status:"dynamic", catalog_status:"uncertain"});
  });
  it("places Claude Fable 5.2 in the first-row flagship slot and moves Opus 5.5 to row 2", () => {
    expect(models[0]?.id).toBe("anthropic/claude-fable-5.2");
    expect(models[0]?.serving_variants.find((v) => v.type === "batch")).toMatchObject({
      catalog_id: "anthropic/claude-fable-5.2:batch",
      request_model_id: "anthropic/claude-fable-5.2",
      pricing: { input: 4, output: 20 },
    });
    expect(models[4]?.id).toBe("anthropic/claude-opus-5.5");
  });
  it("adapts November 10 Decisions, Reranker, and post-six-row chat entries", () => {
    for (const id of [
      "nex-agi/nex-n2.6",
      "nace-ai/drex-max-finance",
      "thinkingmachines/inkling-2-small:free",
      "meta/muse-ember",
      "liquid/lfm-3-1.7b",
      "liquid/lfm-3-1.7b:free",
      "parallel/decisions-fast:free",
      "qwen/qwen3-reranker-0.6b",
    ]) {
      expect(models.filter((model) => model.id === id)).toHaveLength(1);
      expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    }
    expect(models.find((model) => model.id === "nace-ai/drex-max-finance")).toMatchObject({
      primary_operation: "decide",
      ui_entrypoint: "decisions",
      pricing_basis: "token",
      pricing: { input: 0.06, output: 0 },
    });
    expect(models.find((model) => model.id === "parallel/decisions-fast:free")).toMatchObject({
      primary_operation: "decide",
      ui_entrypoint: "decisions",
      pricing_basis: "free",
      pricing_status: "free",
    });
    expect(models.find((model) => model.id === "qwen/qwen3-reranker-0.6b")).toMatchObject({
      primary_operation: "rerank",
      ui_entrypoint: "rag",
      pricing_basis: "request",
      pricing_status: "dynamic",
    });
  });
  it.each(["google/gemini-omni-1.1-flash", "google/gemini-omni-1.2-flash"])("routes %s to dedicated video", (id) => {
    expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    expect(models.find((model) => model.id === id)).toMatchObject({primary_operation:"generate_video", ui_entrypoint:"multimodal", pricing_basis:"media"});
  });
  it("keeps Auto Code on dynamic chat rather than dedicated image generation", () => {
    const model = models.find((item) => item.id === "openrouter/auto-code");
    expect(model).toMatchObject({ui_entrypoint:"chat", pricing_basis:"dynamic"});
    expect(model?.operations).not.toContain("generate_image");
  });
  it("retains explicit per-hour Nemotron transcription pricing", () => {
    expect(models.find((model) => model.id === "nvidia/nemotron-3.5-transcribe")?.media_pricing).toEqual({unit:"audio_hour", usd:0.06000012});
  });
});

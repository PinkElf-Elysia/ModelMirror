import { describe, expect, it } from "vitest";
import { models } from "./models";
import {
  estimateImageCost,
  imageTokenPricingSummary,
  MINIMAX_H3_IMAGE_TOKEN_PRICING_BY_MODEL_ID,
  SEEDREAM_5_5_PRICING,
} from "../utils/imageCostEstimate";

describe("November 6 refresh", () => {
  it("keeps Parallel on request-priced Decisions, not free chat", () => {
    const id = "parallel/decisions-fast";
    expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    expect(models.find((model) => model.id === id)).toMatchObject({primary_operation:"decide", ui_entrypoint:"decisions", pricing_basis:"request", pricing_status:"dynamic", catalog_status:"live"});
  });
  it("places Claude Fable 5.2 in the first-row flagship slot, moves Opus 5.5 to row 2, and places Gemini 4 Carbon in the front six rows", () => {
    expect(models[0]?.id).toBe("anthropic/claude-fable-5.2");
    expect(models[0]?.serving_variants.find((v) => v.type === "batch")).toMatchObject({
      catalog_id: "anthropic/claude-fable-5.2:batch",
      request_model_id: "anthropic/claude-fable-5.2",
      pricing: { input: 4, output: 20 },
    });
    expect(models[4]?.id).toBe("anthropic/claude-opus-5.5");
    const carbonIndex = models.findIndex((model) => model.id === "google/gemini-4-carbon");
    expect(carbonIndex).toBeGreaterThan(1);
    expect(carbonIndex).toBeLessThan(20);
    expect(models[carbonIndex]).toMatchObject({
      context_length: 1_048_576,
      pricing: { input: 2, output: 10 },
      input_modalities: ["audio", "file", "image", "text", "video"],
      output_modalities: ["text"],
      ui_entrypoint: "chat",
      reasoning_declared: true,
    });
    expect(models[carbonIndex]?.serving_variants.find((v) => v.type === "batch")).toMatchObject({
      catalog_id: "google/gemini-4-carbon:batch",
      request_model_id: "google/gemini-4-carbon",
      pricing: { input: 1, output: 5 },
      input_modalities: ["text"],
      output_modalities: ["text"],
    });
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
  it("adapts November 11 MiniMax H3 Image, Grok STT 2.0, Baidu ERNIE, KT Mi:dm, and Motif 3 below the first six rows", () => {
    for (const id of [
      "minimax/minimax-h3-image",
      "minimax/minimax-h3-image-highspeed",
      "x-ai/grok-stt-2.0",
      "baidu/ernie-5.5-flash-preview:free",
      "baidu/ernie-light-dt-1.0-intl",
      "kt/midm-k-3.0-pro",
      "motif-technologies/motif-3",
    ]) {
      expect(models.filter((model) => model.id === id)).toHaveLength(1);
      expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    }

    for (const [id, summary] of [
      [
        "minimax/minimax-h3-image",
        "文本输入 $2.30/M Token · 图片输入 $1.50/M Token · 图片输出 $3.80/M Token",
      ],
      [
        "minimax/minimax-h3-image-highspeed",
        "文本输入 $2.30/M Token · 图片输入 $3/M Token · 图片输出 $7.60/M Token",
      ],
    ] as const) {
      const model = models.find((entry) => entry.id === id);
      expect(model).toMatchObject({
        primary_operation: "generate_image",
        ui_entrypoint: "multimodal",
        interaction_status: "ready",
        pricing_basis: "media",
        supported_parameters: [
          "resolution",
          "aspect_ratio",
          "output_format",
          "n",
          "input_references",
          "size",
        ],
      });
      const pricing = MINIMAX_H3_IMAGE_TOKEN_PRICING_BY_MODEL_ID[id];
      expect(estimateImageCost(pricing, { outputCount: 4, referenceCount: 2, resolution: "2K" })).toBeNull();
      expect(imageTokenPricingSummary(pricing)).toBe(summary);
    }

    expect(models.find((entry) => entry.id === "x-ai/grok-stt-2.0")).toMatchObject({
      primary_operation: "transcribe",
      ui_entrypoint: "chat",
      interaction_status: "ready",
      pricing_basis: "media",
      media_pricing: { unit: "audio_hour", usd: 0.10000000000008 },
    });
    expect(models.find((entry) => entry.id === "baidu/ernie-5.5-flash-preview:free")).toMatchObject({
      primary_operation: "chat",
      ui_entrypoint: "chat",
      pricing_basis: "free",
      pricing_status: "free",
      reasoning_declared: true,
    });
    expect(models.find((entry) => entry.id === "baidu/ernie-light-dt-1.0-intl")).toMatchObject({
      primary_operation: "chat",
      ui_entrypoint: "chat",
      pricing_basis: "token",
      reasoning_declared: true,
    });
  });

  it("places Gemini 4 Hydrogen in the sixth row and adapts November 12/13 Seedream 5.5, Simba 3.5, Decisions, and chat entries", () => {
    const hydrogenIndex = models.findIndex((model) => model.id === "google/gemini-4-hydrogen");
    expect(hydrogenIndex).toBe(19);
    expect(hydrogenIndex).toBeGreaterThanOrEqual(17);
    expect(hydrogenIndex).toBeLessThan(20);
    expect(models[hydrogenIndex]).toMatchObject({
      context_length: 1_048_576,
      pricing: { input: 1.5, output: 7.5 },
      input_modalities: ["audio", "file", "image", "text", "video"],
      output_modalities: ["text"],
      ui_entrypoint: "chat",
      reasoning_declared: true,
    });
    expect(models[hydrogenIndex]?.serving_variants.find((v) => v.type === "batch")).toMatchObject({
      catalog_id: "google/gemini-4-hydrogen:batch",
      request_model_id: "google/gemini-4-hydrogen",
      pricing: { input: 0.75, output: 3.75 },
      input_modalities: ["text"],
      output_modalities: ["text"],
    });
    expect(models.find((m) => m.id === "moonshotai/kimi-k3.1-pro")?.serving_variants.find((v) => v.type === "batch")).toMatchObject({
      catalog_id: "moonshotai/kimi-k3.1-pro:batch",
      request_model_id: "moonshotai/kimi-k3.1-pro",
      pricing: { input: 0.7, output: 6 },
      input_modalities: ["text"],
      output_modalities: ["text"],
    });

    for (const id of [
      "bytedance-seed/seedream-5-5",
      "perplexity/pplx-decider-flash-v1.3:free",
      "perplexity/pplx-decider-v1.3:free",
      "speechify/simba-3.5",
      "google/gemini-decision-1",
      "thinkingmachines/inkling-2-small",
      "particle-ai/particle-1:free",
    ]) {
      expect(models.filter((model) => model.id === id)).toHaveLength(1);
      expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    }

    expect(models.find((m) => m.id === "bytedance-seed/seedream-5-5")).toMatchObject({
      primary_operation: "generate_image",
      ui_entrypoint: "multimodal",
      interaction_status: "ready",
      pricing_basis: "media",
      supported_parameters: ["resolution", "aspect_ratio", "n", "input_references", "seed"],
    });
    const standardEstimate = estimateImageCost(SEEDREAM_5_5_PRICING, { outputCount: 1, referenceCount: 2, resolution: "1K" });
    expect(standardEstimate?.minUsd).toBeCloseTo(0.036);
    expect(standardEstimate?.maxUsd).toBeCloseTo(0.036);
    expect(standardEstimate?.inputUsd).toBeCloseTo(0.016);
    expect(standardEstimate?.exact).toBe(true);
    const highResEstimate = estimateImageCost(SEEDREAM_5_5_PRICING, { outputCount: 1, referenceCount: 2, resolution: "2K" });
    expect(highResEstimate?.minUsd).toBeCloseTo(0.051);
    expect(highResEstimate?.maxUsd).toBeCloseTo(0.051);
    expect(highResEstimate?.inputUsd).toBeCloseTo(0.016);
    expect(highResEstimate?.exact).toBe(true);

    expect(models.find((m) => m.id === "speechify/simba-3.5")).toMatchObject({
      primary_operation: "synthesize_speech",
      pricing_basis: "media",
      media_pricing: { unit: "character_million", usd: 15 },
    });

    for (const id of ["google/gemini-decision-1", "perplexity/pplx-decider-v1.3:free", "perplexity/pplx-decider-flash-v1.3:free"]) {
      expect(models.find((m) => m.id === id)).toMatchObject({
        primary_operation: "decide",
        ui_entrypoint: "decisions",
        interaction_status: "ready",
      });
    }
  });
});

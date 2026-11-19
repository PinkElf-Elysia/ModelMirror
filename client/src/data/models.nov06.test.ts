import { describe, expect, it } from "vitest";
import { models } from "./models";
import {
  estimateImageCost,
  imageTokenPricingSummary,
  MAI_IMAGE_TOKEN_PRICING_BY_MODEL_ID,
  MINIMAX_H3_IMAGE_TOKEN_PRICING_BY_MODEL_ID,
  SEEDREAM_5_5_PRICING,
} from "../utils/imageCostEstimate";
import { estimateVideoCost } from "../utils/videoCostEstimate";

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
      pricing: { input: 0.372, output: 5.582999999999999 },
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

  it("adds November 16 Qwen4 Max below the first six rows and reconciles Batch drift", () => {
    const qwen4Index = models.findIndex((model) => model.id === "qwen/qwen4-max");
    expect(models.filter((model) => model.id === "qwen/qwen4-max")).toHaveLength(1);
    expect(qwen4Index).toBeGreaterThanOrEqual(20);
    expect(models[qwen4Index]).toMatchObject({
      context_length: 1_000_000,
      input_modalities: ["text", "image", "video"],
      output_modalities: ["text"],
      pricing: { input: 1.4249999999999998, output: 4.2749999999999995 },
      pricing_basis: "token",
      reasoning_declared: true,
      primary_operation: "chat",
      ui_entrypoint: "chat",
      interaction_status: "ready",
      catalog_status: "live",
    });

    expect(
      models.find((m) => m.id === "z-ai/glm-5.2")?.serving_variants.filter((v) => v.type === "batch"),
    ).toHaveLength(1);
    expect(
      models.find((m) => m.id === "deepseek/deepseek-v4.1-flash-1017")?.serving_variants.find((v) => v.type === "batch"),
    ).toMatchObject({
      catalog_id: "deepseek/deepseek-v4.1-flash-1017:batch",
      request_model_id: "deepseek/deepseek-v4.1-flash-1017",
      pricing: { input: 0.036, output: 0.15 },
    });
  });

  it("adapts November 16/17 Hy4 flash, Liquid d1.2 decisions, and Seedance 2.5 Flash below the first six rows", () => {
    for (const id of [
      "tencent/hy4-flash",
      "liquid/d1.2",
      "liquid/d1.2-ultra",
      "bytedance/seedance-2.5-flash",
    ]) {
      expect(models.filter((model) => model.id === id)).toHaveLength(1);
      expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    }

    expect(models.find((m) => m.id === "tencent/hy4-flash")).toMatchObject({
      context_length: 1_048_576,
      input_modalities: ["text", "image"],
      output_modalities: ["text"],
      pricing: { input: 0.035, output: 0.14049999999999999 },
      pricing_basis: "token",
      reasoning_declared: true,
      primary_operation: "chat",
      ui_entrypoint: "chat",
      interaction_status: "ready",
      catalog_status: "live",
    });

    expect(models.find((m) => m.id === "liquid/d1.2")).toMatchObject({
      context_length: 65_536,
      input_modalities: ["text", "image"],
      output_modalities: ["decisions"],
      pricing: { input: 0.02, output: 0 },
      primary_operation: "decide",
      ui_entrypoint: "decisions",
      interaction_status: "ready",
      catalog_status: "live",
    });
    expect(models.find((m) => m.id === "liquid/d1.2-ultra")).toMatchObject({
      context_length: 65_536,
      input_modalities: ["text", "image"],
      output_modalities: ["decisions"],
      pricing: { input: 0.041999999999999996, output: 0 },
      primary_operation: "decide",
      ui_entrypoint: "decisions",
      interaction_status: "ready",
      catalog_status: "live",
    });

    expect(models.find((m) => m.id === "bytedance/seedance-2.5-flash")).toMatchObject({
      input_modalities: ["text", "image", "video", "audio"],
      output_modalities: ["video"],
      pricing_basis: "media",
      primary_operation: "generate_video",
      ui_entrypoint: "multimodal",
      interaction_status: "ready",
      catalog_status: "live",
    });

    const flashCost = estimateVideoCost(
      {
        supported_resolutions: ["480p", "720p"],
        supported_aspect_ratios: ["16:9"],
        supported_sizes: ["1280x720"],
        pricing_skus: {
          video_tokens: "0.000007",
          video_tokens_without_audio: "0.000007",
        },
      },
      {
        duration: 5,
        resolution: "720p",
        aspectRatio: "16:9",
        generateAudio: true,
        imageInputCount: 0,
      },
    );
    expect(flashCost).toBeCloseTo(((1280 * 720 * 5 * 24) / 1024) * 0.000007);
  });

  it("places GPT-6.2 Astra and Sol in flagship slots and adapts November 17 GPT-6.2 Pro, MAI-Image 2.7, and Decision 1.1 contracts", () => {
    expect(models[0]?.id).toBe("anthropic/claude-fable-5.2");
    expect(models[1]?.id).toBe("openai/gpt-6.2-astra");
    expect(models[2]?.id).toBe("openai/gpt-6.2-sol");
    expect(models.findIndex((m) => m.id === "openai/gpt-6-astra")).toBeGreaterThanOrEqual(20);
    expect(models.findIndex((m) => m.id === "openai/gpt-6.1-sol")).toBeGreaterThanOrEqual(20);

    for (const [id, input, output, highInput, highOutput] of [
      ["openai/gpt-6.2-astra", 8, 40, 16, 60],
      ["openai/gpt-6.2-astra-pro", 8, 40, 16, 60],
      ["openai/gpt-6.2-sol", 2, 10, 4, 15],
      ["openai/gpt-6.2-sol-pro", 2, 10, 4, 15],
    ] as const) {
      expect(models.filter((m) => m.id === id)).toHaveLength(1);
      expect(models.find((m) => m.id === id)).toMatchObject({
        context_length: 1_050_000,
        input_modalities: ["file", "image", "text"],
        output_modalities: ["text"],
        pricing: { input, output },
        pricing_overrides: [
          expect.objectContaining({
            min_prompt_tokens: 272_000,
            pricing: { input: highInput, output: highOutput },
          }),
        ],
        reasoning_declared: true,
        primary_operation: "chat",
        ui_entrypoint: "chat",
        interaction_status: "ready",
        catalog_status: "live",
      });
    }

    for (const id of [
      "openai/gpt-6.2-astra-pro",
      "openai/gpt-6.2-sol-pro",
      "microsoft/mai-image-2.7",
      "microsoft/mai-image-2.7-flash",
      "microsoft/microsoft-decision-1.1",
    ]) {
      expect(models.filter((model) => model.id === id)).toHaveLength(1);
      expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
    }

    for (const [id, summary] of [
      [
        "microsoft/mai-image-2.7",
        "文本输入 $5/M Token · 图片输入 $8/M Token · 图片输出 $34/M Token",
      ],
      [
        "microsoft/mai-image-2.7-flash",
        "文本输入 $1.75/M Token · 图片输入 $2.50/M Token · 图片输出 $10/M Token",
      ],
    ] as const) {
      expect(models.find((m) => m.id === id)).toMatchObject({
        primary_operation: "generate_image",
        ui_entrypoint: "multimodal",
        interaction_status: "ready",
        pricing_basis: "media",
        supported_parameters: ["aspect_ratio", "background", "n", "input_references"],
      });
      const pricing = MAI_IMAGE_TOKEN_PRICING_BY_MODEL_ID[id];
      expect(estimateImageCost(pricing, { outputCount: 1, referenceCount: 2, resolution: "2K" })).toBeNull();
      expect(imageTokenPricingSummary(pricing)).toBe(summary);
    }

    expect(models.find((m) => m.id === "microsoft/microsoft-decision-1.1")).toMatchObject({
      context_length: 32_768,
      input_modalities: ["text", "image"],
      output_modalities: ["decisions"],
      pricing: { input: 0.041999999999999996, output: 0 },
      primary_operation: "decide",
      ui_entrypoint: "decisions",
      interaction_status: "ready",
      catalog_status: "live",
    });

    for (const id of ["heygen/heygen-avatar-6", "heygen/heygen-avatar-6-turbo"]) {
      expect(models.filter((model) => model.id === id)).toHaveLength(1);
      expect(models.findIndex((model) => model.id === id)).toBeGreaterThanOrEqual(20);
      expect(models.find((model) => model.id === id)).toMatchObject({
        primary_operation: "generate_video",
        interaction_status: "ready",
        ui_entrypoint: "multimodal",
        pricing_basis: "media",
        pricing_status: "dynamic",
        catalog_status: "live",
      });
    }
  });

  it("places Gemini 4 Neon in the front six rows, demotes Fable 5.1, and attaches November 18 Batch variants", () => {
    const neonIndex = models.findIndex((m) => m.id === "google/gemini-4-neon");
    expect(neonIndex).toBe(17);
    expect(neonIndex).toBeLessThan(20);
    expect(models.findIndex((m) => m.id === "anthropic/claude-fable-5.1")).toBeGreaterThanOrEqual(20);
    expect(models[neonIndex]).toMatchObject({
      context_length: 1_048_576,
      pricing: { input: 3, output: 15 },
      input_modalities: ["audio", "file", "image", "text", "video"],
      output_modalities: ["text"],
      primary_operation: "chat",
      ui_entrypoint: "chat",
      interaction_status: "ready",
      reasoning_declared: true,
      catalog_status: "live",
    });
    expect(models[neonIndex]?.serving_variants.find((v) => v.type === "batch")).toMatchObject({
      catalog_id: "google/gemini-4-neon:batch",
      request_model_id: "google/gemini-4-neon",
      pricing: { input: 1.5, output: 7.5 },
    });
    for (const [id, batchIn, batchOut] of [
      ["openai/gpt-6.2-astra", 4, 20],
      ["openai/gpt-6.2-astra-pro", 4, 20],
      ["openai/gpt-6.2-sol", 1, 5],
      ["openai/gpt-6.2-sol-pro", 1, 5],
    ] as const) {
      expect(
        models.find((m) => m.id === id)?.serving_variants.find((v) => v.type === "batch"),
      ).toMatchObject({
        catalog_id: `${id}:batch`,
        request_model_id: id,
        pricing: { input: batchIn, output: batchOut },
      });
    }
  });
});

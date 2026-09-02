import { describe, expect, it } from "vitest";
import {
  DEFAULT_AGENT_BUILDER_MODEL_ID,
  DEFAULT_WORKFLOW_AGENT_MODEL_ID,
} from "./modelOptions";
import { models } from "./models";

const middleModelIds = [
  "sakana/sakana-namazu",
  "upstage/solar-pro4",
  "meta/muse-glimmer-30b",
];

const august13ModelIds = [
  "qwen/qwen3-reranker-8b",
  "qwen/qwen3-asr-1.7b",
  "qwen/qwen3-asr-0.6b",
  "x-ai/grok-4.6",
  "bytedance-seed/seedream-5-0-pro",
  "deepgram/flux-tts:free",
  "qwen/qwen3.8-2.4t-a95b",
  "bytedance-seed/seed-2-1-turbo",
  "bytedance-seed/seed-2.0-code",
  "deepseek/deepseek-v4-pro-0813",
  "bytedance/seedance-2.0-mini",
];

const august14ModelIds = [
  "qwen/qwen3-reranker-8b",
  "voyageai/voyage-code-4",
  "google/gemini-3.7-flash",
  "bytedance-seed/seedream-5-0-lite",
  "mistralai/voxtral-mini-3b-2507",
  "mistralai/voxtral-small-24b-2507-stt",
  "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
];

const august16ModelIds = [
  "dots-studio/dots-3-note-preview:free",
  "qwen/qwen3.8-27b",
];

const august20ModelIds = [
  "liquid/lfm-2.5-embedding-350m:free",
  "z-ai/glm-5.3",
  "~z-ai/glm-latest",
];

const august20SecondRefreshModelIds = [
  "stealth/ox-alpha",
  "tencent/hy-mt2-1.8b",
  "tencent/hy-mt2-30b-a3b",
  "black-forest-labs/flux-video-upscale",
];

const august21ModelIds = [
  "meta/muse-spark-1.2-contributor",
  "deepseek/deepseek-v4-flash-vision-exp",
];

const august24VideoModelIds = [
  "alibaba/wan-3.0",
  "heygen/avatar-iv",
];

const august26RecraftStyleModelIds = [
  "recraft/recraft-v4-styles",
  "recraft/recraft-v4-styles-pro-vector",
  "recraft/recraft-v4-styles-vector",
  "recraft/recraft-v4-styles-pro",
];

const august26CatalogDriftModelIds = [
  "z-ai/glm-5.3-flash",
  "tencent/hy-mt2-7b",
  "thinkingmachines/inkling-small:free",
  "thinkingmachines/inkling:free",
  "z-ai/glm-5.2:free",
  "minimax/minimax-m3:free",
  "minimax/minimax-m2.7:free",
];

const august26LatestModelIds = [
  "qwen/qwen3.8-flash",
  "meta/muse-image",
];

const august27LatestModelIds = [
  "alibaba/wan-3.0-prime",
  "inclusionai/ling-3.0-flash-fin:free",
];

const august28LatestModelIds = ["tencent/hy4-preview"];
const august31LatestModelIds = ["ibm-granite/granite-4.2-8b"];
const september1LatestModelIds = [
  "inception/mercury-2.5-preview",
  "anthropic/claude-fable-5.1",
  "minimax/hailuo-3-max",
];

const august28BatchCatalogIds = [
  "qwen/qwen3.8-2.4t-a95b:batch",
  "mistralai/mistral-medium-3-5:batch",
];

describe("OpenRouter model refresh", () => {
  it("reconciles the refreshed counted catalog totals", () => {
    const counted = models.filter((model) => model.catalog_counted);
    expect(counted).toHaveLength(578);
    expect(counted.filter((model) => model.catalog_status === "live")).toHaveLength(499);
    expect(counted.filter((model) => model.catalog_status === "uncertain")).toHaveLength(73);
    expect(counted.filter((model) => model.catalog_status === "expired")).toHaveLength(6);
    expect(counted.filter((model) => model.catalog_status !== "expired")).toHaveLength(572);
  });

  it("places Fable 5.1 in the flagship slot and moves Opus 5 to row two", () => {
    expect(models[0]?.id).toBe("anthropic/claude-fable-5.1");
    expect(models[2]?.id).toBe("deepseek/deepseek-v4-flash-0731");
    expect(models[3]?.id).toBe("anthropic/claude-opus-5");
    expect(models[5]?.id).toBe("deepseek/deepseek-v4-pro-0813");
    expect(models[8]?.id).toBe("bytedance-seed/seedream-5-0-pro");
  });

  it("places Seedance 2.5 at the former GPT-4o Mini TTS slot", () => {
    expect(models[11]?.id).toBe("bytedance/seedance-2.5");
    expect(models[12]?.id).toBe("gpt-4o-mini-tts");
  });

  it("keeps the four general models in the catalog middle", () => {
    const countedModels = models.filter((model) => model.catalog_counted);
    const lowerBound = Math.floor(countedModels.length * 0.25);
    const upperBound = Math.ceil(countedModels.length * 0.75);

    for (const modelId of middleModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      const index = countedModels.findIndex((model) => model.id === modelId);
      expect(index).toBeGreaterThan(lowerBound);
      expect(index).toBeLessThan(upperBound);
    }
  });

  it("adds all ten live counted snapshots exactly once", () => {
    for (const modelId of august13ModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      expect(matches[0]).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
      });
    }
  });

  it("adds or refreshes all seven August 14 models exactly once", () => {
    for (const modelId of august14ModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      expect(matches[0]).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 5 * 3,
      );
    }
  });

  it("adds the two requested August 16 snapshots below the first six rows", () => {
    for (const modelId of august16ModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      expect(matches[0]).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
        primary_operation: "chat",
        interaction_status: "ready",
        ui_entrypoint: "chat",
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves the August 16 multimodal chat contracts", () => {
    expect(
      models.find(
        (model) => model.id === "dots-studio/dots-3-note-preview:free",
      ),
    ).toMatchObject({
      input_modalities: ["text", "image"],
      output_modalities: ["text"],
      operations: expect.arrayContaining(["analyze_image", "chat"]),
      context_length: 512_000,
      pricing_status: "free",
      pricing_basis: "free",
      openrouter_market: {
        series: "Other",
        author: "dots-studio",
        providers: ["AtlasCloud"],
      },
    });

    expect(models.find((model) => model.id === "qwen/qwen3.8-27b")).toMatchObject({
      input_modalities: ["text", "image", "video"],
      output_modalities: ["text"],
      operations: expect.arrayContaining([
        "analyze_image",
        "analyze_video",
        "chat",
      ]),
      context_length: 1_000_000,
      pricing_status: "fixed",
      pricing_basis: "token",
      openrouter_market: {
        series: "Qwen",
        author: "qwen",
        providers: ["Chutes"],
      },
    });
    expect(
      models.find((model) => model.id === "qwen/qwen3.8-27b")?.pricing.input,
    ).toBeCloseTo(0.425);
    expect(
      models.find((model) => model.id === "qwen/qwen3.8-27b")?.pricing.output,
    ).toBeCloseTo(2.55);
  });

  it("adds the three August 20 snapshots below the first six rows", () => {
    for (const modelId of august20ModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      expect(matches[0]).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("keeps the August 20 endpoint contracts and current prices", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    expect(byId.get("liquid/lfm-2.5-embedding-350m:free")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["embeddings"],
      primary_operation: "embed",
      interaction_status: "ready",
      ui_entrypoint: "rag",
      context_length: 512,
      pricing: { input: 0, output: 0 },
      pricing_status: "free",
      pricing_basis: "free",
    });
    expect(byId.get("z-ai/glm-5.3")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["text"],
      primary_operation: "chat",
      interaction_status: "ready",
      ui_entrypoint: "chat",
      context_length: 1_310_720,
      pricing: { input: 1.4, output: 4.4 },
      reasoning_declared: true,
      openrouter_market: { author: "z-ai" },
    });
    expect(byId.get("~z-ai/glm-latest")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["text"],
      primary_operation: "chat",
      interaction_status: "ready",
      ui_entrypoint: "chat",
      context_length: 1_310_720,
      pricing: { input: 1.17, output: 3.9600000000000004 },
      reasoning_declared: true,
      openrouter_market: { author: "z-ai" },
    });
  });

  it("adds the second August 20 refresh below the first six rows", () => {
    for (const modelId of august20SecondRefreshModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      expect(matches[0]).toMatchObject({
        catalog_status: modelId === "stealth/ox-alpha" ? "expired" : "live",
        catalog_counted: true,
        active: modelId !== "stealth/ox-alpha",
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves the second August 20 API contracts and market metadata", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    expect(byId.get("stealth/ox-alpha")).toMatchObject({
      input_modalities: ["text", "image", "video"],
      output_modalities: ["text"],
      operations: expect.arrayContaining(["analyze_image", "analyze_video", "chat"]),
      context_length: 1_048_576,
      pricing_status: "dynamic",
      reasoning_declared: true,
      openrouter_market: {
        author: "stealth",
        providers: [],
        zero_data_retention: false,
      },
    });
    expect(byId.get("tencent/hy-mt2-1.8b")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["text"],
      primary_operation: "chat",
      context_length: 8_192,
      openrouter_market: {
        author: "tencent",
        providers: ["Tencent"],
        zero_data_retention: true,
      },
    });
    expect(byId.get("tencent/hy-mt2-1.8b")?.pricing.input).toBeCloseTo(0.044);
    expect(byId.get("tencent/hy-mt2-1.8b")?.pricing.output).toBeCloseTo(0.177);
    expect(byId.get("tencent/hy-mt2-30b-a3b")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["text"],
      primary_operation: "chat",
      context_length: 8_192,
      pricing: { input: 0.074, output: 0.295 },
      supported_parameters: expect.arrayContaining([
        "response_format",
        "structured_outputs",
      ]),
    });
    expect(byId.get("black-forest-labs/flux-video-upscale")).toMatchObject({
      input_modalities: ["video", "text"],
      output_modalities: ["video"],
      primary_operation: "generate_video",
      interaction_status: "ready",
      ui_entrypoint: "multimodal",
      pricing_status: "dynamic",
      pricing_basis: "media",
      supported_parameters: [
        "input_references",
        "upscale_factor",
        "creativity",
        "safety_tolerance",
      ],
    });
  });

  it("adds the two August 21 snapshots below the first six rows", () => {
    for (const modelId of august21ModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      expect(matches[0]).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
        primary_operation: "chat",
        interaction_status: "ready",
        ui_entrypoint: "chat",
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves the August 21 multimodal, retention, and segmented pricing contracts", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    expect(byId.get("meta/muse-spark-1.2-contributor")).toMatchObject({
      canonical_slug: "meta/muse-spark-1.2-contributor-20260805",
      input_modalities: ["text", "image", "video", "file", "audio"],
      output_modalities: ["text"],
      operations: expect.arrayContaining([
        "analyze_document",
        "analyze_image",
        "analyze_audio",
        "analyze_video",
        "chat",
      ]),
      context_length: 1_048_576,
      pricing_status: "fixed",
      pricing_basis: "token",
      reasoning_declared: true,
      openrouter_market: {
        series: "Other",
        author: "meta",
        providers: ["Meta"],
        distillable: false,
        zero_data_retention: false,
      },
    });
    expect(
      byId.get("meta/muse-spark-1.2-contributor")?.pricing.input,
    ).toBeCloseTo(0.1);
    expect(
      byId.get("meta/muse-spark-1.2-contributor")?.pricing.output,
    ).toBeCloseTo(0.2);
    expect(byId.get("meta/muse-spark-1.2-contributor")?.note).toContain(
      "保留提示词与输出 30 天",
    );

    expect(byId.get("deepseek/deepseek-v4-flash-vision-exp")).toMatchObject({
      canonical_slug: "deepseek/deepseek-v4-flash-vision-exp-20260821",
      input_modalities: ["text", "image"],
      output_modalities: ["text"],
      operations: expect.arrayContaining(["analyze_image", "chat"]),
      context_length: 1_048_576,
      pricing: { input: 0.44, output: 1.32 },
      pricing_status: "fixed",
      pricing_basis: "token",
      reasoning_declared: true,
      openrouter_market: {
        series: "DeepSeek",
        author: "deepseek",
        providers: ["Fireworks"],
        distillable: true,
        zero_data_retention: true,
      },
    });
    expect(
      byId.get("deepseek/deepseek-v4-flash-vision-exp")?.pricing_time_windows,
    ).toEqual([
      {
        utc_start: 0,
        utc_end: 100,
        pricing: { input: 0.22, output: 0.66 },
        price_cny: { input: 1.49, output: 4.47 },
      },
      {
        utc_start: 100,
        utc_end: 400,
        pricing: { input: 0.44, output: 1.32 },
        price_cny: { input: 2.98, output: 8.94 },
      },
      {
        utc_start: 400,
        utc_end: 600,
        pricing: { input: 0.22, output: 0.66 },
        price_cny: { input: 1.49, output: 4.47 },
      },
      {
        utc_start: 600,
        utc_end: 1000,
        pricing: { input: 0.44, output: 1.32 },
        price_cny: { input: 2.98, output: 8.94 },
      },
      {
        utc_start: 1000,
        utc_end: 0,
        pricing: { input: 0.22, output: 0.66 },
        price_cny: { input: 1.49, output: 4.47 },
      },
    ]);
  });

  it("adds both August 24 video snapshots below the first six rows", () => {
    for (const modelId of august24VideoModelIds) {
      const matches = models.filter((model) => model.id === modelId);
      expect(matches).toHaveLength(1);
      expect(matches[0]).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
        primary_operation: "generate_video",
        interaction_status: "ready",
        ui_entrypoint: "multimodal",
        pricing_status: "dynamic",
        pricing_basis: "media",
        openrouter_market: { series: "Other" },
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves the August 24 model-specific video contracts", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    expect(byId.get("alibaba/wan-3.0")).toMatchObject({
      canonical_slug: "alibaba/wan-3.0-20260824",
      input_modalities: ["text", "image"],
      output_modalities: ["video"],
      supported_parameters: [
        "resolution",
        "aspect_ratio",
        "duration",
        "frame_images",
        "input_references",
        "generate_audio",
        "seed",
      ],
      openrouter_market: {
        author: "alibaba",
        series: "Other",
        providers: ["Alibaba"],
        discounted: true,
        zero_data_retention: false,
      },
    });
    expect(byId.get("alibaba/wan-3.0")?.note).toContain("$0.20/视频秒");

    expect(byId.get("heygen/avatar-iv")).toMatchObject({
      canonical_slug: "heygen/avatar-iv-20260625",
      input_modalities: ["text", "image", "audio"],
      output_modalities: ["video"],
      supported_parameters: [
        "resolution",
        "aspect_ratio",
        "input_references",
      ],
      openrouter_market: {
        author: "heygen",
        series: "Other",
        providers: ["HeyGen"],
        discounted: false,
        zero_data_retention: false,
      },
    });
    expect(byId.get("heygen/avatar-iv")?.note).toContain("本地暂不开放");
  });

  it("adds and routes all four August 26 Recraft style models", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    for (const modelId of august26RecraftStyleModelIds) {
      expect(byId.get(modelId)).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
        input_modalities: ["text", "image"],
        output_modalities: ["image"],
        primary_operation: "generate_image",
        pricing_status: "dynamic",
        pricing_basis: "media",
        openrouter_market: {
          series: "Other",
          author: "recraft",
          providers: ["Recraft"],
          zero_data_retention: false,
        },
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }

    expect(byId.get("recraft/recraft-v4-styles")?.supported_parameters).toEqual([
      "aspect_ratio",
      "n",
      "input_references",
    ]);
    expect(
      byId.get("recraft/recraft-v4-styles-pro-vector")?.supported_parameters,
    ).toEqual(["aspect_ratio", "output_format", "n", "input_references"]);
  });

  it("adds all seven August 26 catalog drift snapshots below the first six rows", () => {
    for (const modelId of august26CatalogDriftModelIds) {
      expect(models.find((model) => model.id === modelId)).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
        primary_operation: "chat",
        interaction_status: "ready",
        ui_entrypoint: "chat",
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves the August 26 catalog drift model contracts", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    expect(byId.get("z-ai/glm-5.3-flash")).toMatchObject({
      input_modalities: ["text", "image", "video"],
      output_modalities: ["text"],
      context_length: 1_310_720,
      reasoning_declared: true,
      operations: expect.arrayContaining([
        "analyze_image",
        "analyze_video",
        "chat",
      ]),
      supported_parameters: expect.arrayContaining([
        "reasoning_effort",
        "tool_choice",
        "tools",
      ]),
    });
    expect(byId.get("z-ai/glm-5.3-flash")?.pricing.input).toBeCloseTo(0.075);
    expect(byId.get("z-ai/glm-5.3-flash")?.pricing.output).toBeCloseTo(0.25);

    expect(byId.get("tencent/hy-mt2-7b")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["text"],
      context_length: 8_192,
      pricing: { input: 0.074, output: 0.295 },
      supported_parameters: expect.arrayContaining([
        "response_format",
        "structured_outputs",
      ]),
    });
    for (const modelId of [
      "thinkingmachines/inkling-small:free",
      "thinkingmachines/inkling:free",
    ]) {
      expect(byId.get(modelId)).toMatchObject({
        input_modalities: ["text", "image", "audio"],
        output_modalities: ["text"],
        context_length: 1_048_576,
        pricing_status: "free",
        pricing_basis: "free",
        reasoning_declared: true,
        operations: expect.arrayContaining([
          "analyze_audio",
          "analyze_image",
          "chat",
        ]),
      });
    }
    expect(byId.get("z-ai/glm-5.2:free")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["text"],
      context_length: 256_000,
      pricing_status: "free",
      pricing_basis: "free",
      reasoning_declared: true,
    });
    expect(byId.get("minimax/minimax-m3:free")).toMatchObject({
      input_modalities: ["text", "image", "video"],
      output_modalities: ["text"],
      context_length: 1_048_576,
      pricing_status: "free",
      pricing_basis: "free",
      operations: expect.arrayContaining([
        "analyze_image",
        "analyze_video",
        "chat",
      ]),
    });
    expect(byId.get("minimax/minimax-m2.7:free")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["text"],
      context_length: 196_608,
      pricing_status: "free",
      pricing_basis: "free",
      reasoning_declared: true,
    });
    expect(byId.get("mistralai/ministral-8b")).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["text"],
      context_length: 128_000,
      pricing: { input: 0.11, output: 0.11 },
    });
  });

  it("adds the latest August 26 models below the first six rows", () => {
    for (const modelId of august26LatestModelIds) {
      expect(models.find((model) => model.id === modelId)).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves Qwen3.8 Flash and Muse Image contracts", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    expect(byId.get("qwen/qwen3.8-flash")).toMatchObject({
      canonical_slug: "qwen/qwen3.8-flash-20260826",
      input_modalities: ["text", "image", "video"],
      output_modalities: ["text"],
      operations: expect.arrayContaining([
        "analyze_image",
        "analyze_video",
        "chat",
      ]),
      primary_operation: "chat",
      interaction_status: "ready",
      ui_entrypoint: "chat",
      context_length: 1_000_000,
      pricing: { input: 0.15, output: 0.47 },
      reasoning_declared: true,
      openrouter_market: {
        series: "Qwen",
        author: "qwen",
        providers: ["Alibaba"],
        distillable: true,
        zero_data_retention: false,
      },
    });

    expect(byId.get("meta/muse-image")).toMatchObject({
      canonical_slug: "meta/muse-image-1.0-eval-20260824",
      input_modalities: ["text", "image"],
      output_modalities: ["image"],
      operations: ["generate_image"],
      primary_operation: "generate_image",
      pricing_status: "dynamic",
      pricing_basis: "media",
      supported_parameters: [],
      openrouter_market: {
        series: "Other",
        author: "meta",
        providers: ["Meta"],
        zero_data_retention: false,
      },
    });
    expect(byId.get("meta/muse-image")?.note).toContain("$0.01/张");
    expect(byId.get("meta/muse-image")?.note).toContain("暂不开放这些控件");
  });

  it("adds the August 27 models below the first six rows", () => {
    for (const modelId of august27LatestModelIds) {
      expect(models.find((model) => model.id === modelId)).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves Wan 3.0 Prime and Ling 3.0 Flash Fin contracts", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    expect(byId.get("alibaba/wan-3.0-prime")).toMatchObject({
      canonical_slug: "alibaba/wan-3.0-prime-20260827",
      input_modalities: ["text", "image"],
      output_modalities: ["video"],
      operations: ["generate_video"],
      primary_operation: "generate_video",
      interaction_status: "ready",
      ui_entrypoint: "multimodal",
      pricing_status: "dynamic",
      pricing_basis: "media",
      supported_parameters: [
        "resolution",
        "aspect_ratio",
        "duration",
        "frame_images",
        "generate_audio",
        "seed",
      ],
      openrouter_market: {
        series: "Other",
        author: "alibaba",
        providers: ["Alibaba"],
        discounted: false,
        zero_data_retention: false,
      },
    });
    expect(byId.get("alibaba/wan-3.0-prime")?.note).toContain(
      "$0.068、$0.14、$0.28/视频秒",
    );
    expect(byId.get("alibaba/wan-3.0-prime")?.note).toContain(
      "不开放参考图控件",
    );

    expect(byId.get("inclusionai/ling-3.0-flash-fin:free")).toMatchObject({
      canonical_slug: "inclusionai/ling-3.0-flash-fin-20260827",
      input_modalities: ["text"],
      output_modalities: ["text"],
      context_length: 262_144,
      pricing: { input: 0, output: 0 },
      pricing_status: "free",
      pricing_basis: "free",
      reasoning_declared: true,
      operations: ["chat"],
      supported_parameters: expect.arrayContaining([
        "reasoning",
        "include_reasoning",
        "tools",
        "tool_choice",
      ]),
      openrouter_market: {
        series: "Other",
        author: "inclusionai",
        providers: ["Novita"],
        discounted: false,
        zero_data_retention: true,
        tool_call_success_rate: 97.85,
      },
    });
  });

  it("adds the August 28 Hy4 snapshot below the first six rows", () => {
    for (const modelId of august28LatestModelIds) {
      expect(models.find((model) => model.id === modelId)).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
        primary_operation: "chat",
        interaction_status: "ready",
        ui_entrypoint: "chat",
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves the Hy4 reasoning, tools, pricing, and market contract", () => {
    expect(models.find((model) => model.id === "tencent/hy4-preview")).toMatchObject({
      canonical_slug: "tencent/hy4-preview-20260827",
      input_modalities: ["text"],
      output_modalities: ["text"],
      operations: ["chat"],
      job_capabilities: expect.arrayContaining([
        "text_chat",
        "coding",
        "reasoning",
        "tool_use",
      ]),
      context_length: 1_048_576,
      pricing: { input: 0.834, output: 2.501 },
      pricing_status: "fixed",
      pricing_basis: "token",
      reasoning_declared: true,
      supported_parameters: expect.arrayContaining([
        "reasoning",
        "reasoning_effort",
        "include_reasoning",
        "response_format",
        "structured_outputs",
        "tools",
        "tool_choice",
        "max_completion_tokens",
      ]),
      openrouter_market: {
        series: "Other",
        author: "tencent",
        providers: ["Tencent"],
        discounted: false,
        zero_data_retention: true,
        tool_call_success_rate: expect.any(Number),
      },
    });
  });

  it("adds the August 31 Granite snapshot below the first six rows", () => {
    for (const modelId of august31LatestModelIds) {
      expect(models.find((model) => model.id === modelId)).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
        primary_operation: "chat",
        interaction_status: "ready",
        ui_entrypoint: "chat",
      });
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("preserves the Granite text, reasoning, tools, pricing, and market contract", () => {
    const granite = models.find(
      (model) => model.id === "ibm-granite/granite-4.2-8b",
    );

    expect(granite).toMatchObject({
      canonical_slug: "ibm-granite/granite-4.2-8b-20260831",
      input_modalities: ["text"],
      output_modalities: ["text"],
      operations: ["chat"],
      job_capabilities: expect.arrayContaining([
        "text_chat",
        "coding",
        "reasoning",
        "tool_use",
      ]),
      context_length: 131_072,
      pricing: { output: 0.15 },
      pricing_status: "fixed",
      pricing_basis: "token",
      reasoning_declared: true,
      supported_parameters: expect.arrayContaining([
        "reasoning",
        "reasoning_effort",
        "include_reasoning",
        "response_format",
        "structured_outputs",
        "tools",
        "tool_choice",
      ]),
      openrouter_market: {
        series: "Other",
        author: "ibm-granite",
        providers: ["CoreWeave"],
        discounted: false,
        zero_data_retention: true,
        tool_call_success_rate: 95.55,
      },
    });
    expect(granite?.pricing.input).toBeCloseTo(0.1);
  });

  it("adds the September 1 catalog snapshots exactly once", () => {
    for (const modelId of september1LatestModelIds) {
      expect(models.filter((model) => model.id === modelId)).toHaveLength(1);
      expect(models.find((model) => model.id === modelId)).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        active: true,
      });
    }
    for (const modelId of [
      "inception/mercury-2.5-preview",
      "minimax/hailuo-3-max",
    ]) {
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("adds the GLM Flash latest alias as a callable router entry", () => {
    const alias = models.find(
      (model) => model.id === "~z-ai/glm-flash-latest",
    );

    expect(alias).toMatchObject({
      canonical_slug: "~z-ai/glm-flash-latest",
      input_modalities: ["text", "image", "video"],
      output_modalities: ["text"],
      catalog_status: "live",
      catalog_counted: true,
      active: true,
      primary_operation: "chat",
      interaction_status: "ready",
      pricing: { input: 0.075, output: 0.25 },
      openrouter_market: {
        series: "Router",
        author: "z-ai",
        providers: ["Z.AI"],
        discounted: true,
        zero_data_retention: true,
      },
    });
    expect(models.findIndex((model) => model.id === alias?.id)).toBeGreaterThanOrEqual(
      2 + 6 * 3,
    );
  });

  it("preserves Mercury 2.5 and Claude Fable 5.1 contracts", () => {
    const byId = new Map(models.map((model) => [model.id, model]));
    const mercury = byId.get("inception/mercury-2.5-preview");
    const fable = byId.get("anthropic/claude-fable-5.1");

    expect(mercury).toMatchObject({
      canonical_slug: "inception/mercury-2.5-preview-20260831",
      input_modalities: ["text"],
      output_modalities: ["text"],
      primary_operation: "chat",
      interaction_status: "ready",
      context_length: 260_000,
      pricing: { input: 0.04, output: 0.15 },
      reasoning_declared: true,
      openrouter_market: {
        series: "Other",
        author: "inception",
        providers: ["Inception"],
        discounted: true,
        zero_data_retention: true,
        tool_call_success_rate: 97.12,
      },
    });
    expect(fable).toMatchObject({
      canonical_slug: "anthropic/claude-fable-5.1-20260831",
      input_modalities: ["text", "image", "file"],
      output_modalities: ["text"],
      primary_operation: "chat",
      interaction_status: "ready",
      context_length: 1_000_000,
      pricing: { input: 10, output: 50 },
      reasoning_declared: true,
      supported_parameters: expect.arrayContaining([
        "reasoning",
        "reasoning_effort",
        "include_reasoning",
        "response_format",
        "structured_outputs",
        "tools",
        "verbosity",
      ]),
      openrouter_market: {
        series: "Claude",
        author: "anthropic",
        providers: ["Anthropic", "Google"],
        discounted: false,
        zero_data_retention: false,
        tool_call_success_rate: 99.42,
        artificial_analysis: {
          intelligence_index: 65.7,
          coding_index: 81.6,
          agentic_index: 61.3,
        },
      },
    });
  });

  it("routes Hailuo 3 Max to the dedicated video workspace", () => {
    const hailuo = models.find(
      (model) => model.id === "minimax/hailuo-3-max",
    );

    expect(hailuo).toMatchObject({
      canonical_slug: "minimax/hailuo-3-max-20260901",
      input_modalities: ["text", "image"],
      output_modalities: ["video"],
      operations: ["generate_video"],
      primary_operation: "generate_video",
      interaction_status: "ready",
      ui_entrypoint: "multimodal",
      pricing_status: "dynamic",
      pricing_basis: "media",
      supported_parameters: [
        "resolution",
        "aspect_ratio",
        "duration",
        "frame_images",
      ],
      openrouter_market: {
        series: "Other",
        author: "minimax",
        providers: ["Minimax"],
        discounted: false,
        zero_data_retention: false,
      },
    });
    expect(hailuo?.note).toContain("480p $0.05/视频秒");
    expect(hailuo?.note).toContain("768p $0.08/视频秒");
  });

  it("routes the August 14 specialized models by their dedicated contracts", () => {
    for (const modelId of [
      "mistralai/voxtral-mini-3b-2507",
      "mistralai/voxtral-small-24b-2507-stt",
      "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
    ]) {
      expect(models.find((model) => model.id === modelId)).toMatchObject({
        input_modalities: ["audio"],
        output_modalities: ["transcription"],
        primary_operation: "transcribe",
        ui_entrypoint: "chat",
      });
    }

    expect(
      models.find((model) => model.id === "bytedance-seed/seedream-5-0-lite"),
    ).toMatchObject({
      input_modalities: ["text", "image"],
      output_modalities: ["image"],
      primary_operation: "generate_image",
      pricing_basis: "media",
      supported_parameters: [
        "resolution",
        "aspect_ratio",
        "n",
        "input_references",
        "seed",
      ],
    });
    expect(
      models.find((model) => model.id === "voyageai/voyage-code-4"),
    ).toMatchObject({
      input_modalities: ["text"],
      output_modalities: ["embeddings"],
      primary_operation: "embed",
      ui_entrypoint: "rag",
      context_length: 32_000,
      pricing: { input: 0.12, output: 0 },
    });
  });

  it("scatters non-positioned refresh cards below the sixth row", () => {
    for (const modelId of [
      "qwen/qwen3-reranker-8b",
      "qwen/qwen3-asr-1.7b",
      "qwen/qwen3-asr-0.6b",
      "x-ai/grok-4.6",
      "deepgram/flux-tts:free",
      "qwen/qwen3.8-2.4t-a95b",
      "bytedance-seed/seed-2-1-turbo",
      "bytedance-seed/seed-2.0-code",
      "bytedance/seedance-2.0-mini",
    ]) {
      expect(models.findIndex((model) => model.id === modelId)).toBeGreaterThanOrEqual(
        2 + 6 * 3,
      );
    }
  });

  it("adds both Qwen3 ASR snapshots as transcription entries", () => {
    for (const modelId of [
      "qwen/qwen3-asr-1.7b",
      "qwen/qwen3-asr-0.6b",
    ]) {
      expect(models.find((model) => model.id === modelId)).toMatchObject({
        catalog_status: "live",
        catalog_counted: true,
        input_modalities: ["audio"],
        output_modalities: ["transcription"],
        primary_operation: "transcribe",
        ui_entrypoint: "chat",
      });
      expect(
        models.find((model) => model.id === modelId)?.job_capabilities,
      ).not.toEqual(expect.arrayContaining(["coding", "translation"]));
    }
  });

  it("adds the newly discovered Qwen3 Reranker as a request-priced RAG entry", () => {
    expect(
      models.find((model) => model.id === "qwen/qwen3-reranker-8b"),
    ).toMatchObject({
      catalog_status: "live",
      catalog_counted: true,
      input_modalities: ["text"],
      output_modalities: ["rerank"],
      primary_operation: "rerank",
      ui_entrypoint: "rag",
      pricing_status: "dynamic",
      pricing_basis: "request",
      openrouter_market: {
        series: "Qwen3",
        author: "qwen",
      },
    });
  });

  it("keeps OpenRouter market facets separate from capability claims", () => {
    expect(
      models.find((model) => model.id === "xiaomi/mimo-v2.5")
        ?.openrouter_market,
    ).toMatchObject({
      series: "Other",
      author: "xiaomi",
      providers: ["GMICloud"],
      categories: expect.arrayContaining(["programming", "translation"]),
    });
    expect(
      models.find((model) => model.id === "deepseek/deepseek-v4-flash")
        ?.openrouter_market,
    ).toMatchObject({
      series: "DeepSeek",
      author: "deepseek",
      providers: ["DigitalOcean"],
      categories: expect.arrayContaining(["translation"]),
    });
    expect(
      models.some(
        (model) =>
          model.categories.includes("math") ||
          model.categories.includes("analysis"),
      ),
    ).toBe(false);
  });

  it("requires structured signals for coding and reasoning classifications", () => {
    expect(
      models.find((model) => model.id === "nvidia/nemotron-3-embed-1b:free")
        ?.job_capabilities,
    ).not.toContain("coding");

    for (const model of models.filter((candidate) =>
      candidate.job_capabilities.includes("reasoning"),
    )) {
      expect(
        model.supported_parameters.some((parameter) =>
          ["reasoning", "include_reasoning", "reasoning_effort"].includes(
            parameter,
          ),
        ) || model.reasoning_declared,
      ).toBe(true);
    }
  });

  it("separates explicit free tiers from non-token billing", () => {
    expect(
      models.find((model) => model.id === "bytedance-seed/seedream-5-0-pro"),
    ).toMatchObject({ pricing_status: "dynamic", pricing_basis: "media" });
    expect(
      models.find((model) => model.id === "bytedance/seedance-2.0-mini"),
    ).toMatchObject({ pricing_status: "dynamic", pricing_basis: "media" });
    expect(
      models.find((model) => model.id === "voyageai/rerank-2.5"),
    ).toMatchObject({ pricing_status: "dynamic", pricing_basis: "request" });
    expect(
      models.find((model) => model.id === "deepgram/flux-tts:free"),
    ).toMatchObject({ pricing_status: "free", pricing_basis: "free" });
  });

  it("preserves normalized provider identities instead of collapsing to other", () => {
    expect(
      models.filter(
        (model) => model.catalog_counted && model.provider === "其他",
      ),
    ).toHaveLength(0);
  });

  it("keeps audited structured metadata aligned with OpenRouter", () => {
    const byId = new Map(models.map((model) => [model.id, model]));

    expect(byId.get("qwen/qwen3.8-2.4t-a95b")?.context_length).toBe(
      1_048_576,
    );
    expect(byId.get("nvidia/nemotron-3.5-lightning")).toMatchObject({
      context_length: 262_144,
      supported_parameters: expect.arrayContaining(["tool_choice", "tools"]),
    });
    expect(
      byId.get("bytedance-seed/seed-2-1-turbo")?.supported_parameters,
    ).not.toEqual(
      expect.arrayContaining(["presence_penalty", "reasoning_effort", "seed"]),
    );
    expect(
      byId.get("deepseek/deepseek-v4-pro-0813")?.supported_parameters,
    ).toEqual(
      expect.arrayContaining([
        "logit_bias",
        "min_p",
        "repetition_penalty",
        "seed",
        "structured_outputs",
        "top_k",
      ]),
    );

    expect(byId.get("~deepseek/deepseek-v4-flash-latest")?.pricing).toEqual({
      input: 0.049999999999999996,
      output: 0.16,
    });
    expect(byId.get("z-ai/glm-5.2")?.pricing).toEqual({
      input: 0.966,
      output: 3.036,
    });
    expect(byId.get("moonshotai/kimi-k2.7-code")?.pricing).toEqual({
      input: 0.66,
      output: 3.4,
    });
    expect(byId.get("deepseek/deepseek-v4-pro-0813")?.pricing).toEqual({
      input: 1.1154,
      output: 3.3461999999999996,
    });
    expect(byId.get("deepseek/deepseek-v4-pro")?.pricing).toEqual({
      input: 1.04226,
      output: 2.08452,
    });
    expect(byId.get("tencent/hy3")?.pricing).toEqual({
      input: 0.13199999999999998,
      output: 0.5279999999999999,
    });
    expect(byId.get("qwen/qwen3.6-27b")?.pricing).toEqual({
      input: 0.6,
      output: 3.5999999999999996,
    });
    expect(byId.get("deepseek/deepseek-v4-flash")?.pricing).toEqual({
      input: 0.088606,
      output: 0.177212,
    });
    expect(byId.get("qwen/qwen3.5-122b-a10b")?.pricing).toEqual({
      input: 0.29,
      output: 2.4,
    });
    expect(byId.get("deepseek/deepseek-v3.2")?.pricing).toEqual({
      input: 0.26899999999999996,
      output: 0.39999999999999997,
    });
    expect(byId.get("mistralai/voxtral-small-24b-2507")?.context_length).toBe(
      32_768,
    );
    expect(byId.get("google/gemma-3-27b-it")?.context_length).toBe(131_072);
    for (const modelId of ["z-ai/glm-5.3", "~z-ai/glm-latest"]) {
      expect(byId.get(modelId)?.supported_parameters).toEqual(
        expect.arrayContaining([
          "frequency_penalty",
          "logit_bias",
          "logprobs",
          "min_p",
          "presence_penalty",
          "repetition_penalty",
          "seed",
          "stop",
          "structured_outputs",
          "top_logprobs",
        ]),
      );
    }
    expect(
      byId.get("deepseek/deepseek-v4-pro-0813")?.pricing_time_windows,
    ).toEqual([]);
    expect(
      byId.get("deepseek/deepseek-v4-pro")?.pricing_time_windows,
    ).toEqual([]);
    expect(byId.get("qwen/qwen3.5-35b-a3b")?.pricing).toEqual({
      input: 0.25,
      output: 1.25,
    });
    expect(byId.get("qwen/qwen3.5-397b-a17b")?.pricing).toEqual({
      input: 0.39,
      output: 2.34,
    });
    expect(byId.get("z-ai/glm-4.6")?.pricing).toEqual({
      input: 0.43,
      output: 1.75,
    });
    expect(byId.get("inclusionai/ling-3.0-tiny:free")?.catalog_status).toBe(
      "expired",
    );

    expect(byId.get("bytedance-seed/seed-2.0-code")?.pricing_overrides).toEqual([
      {
        min_prompt_tokens: 128_000,
        pricing: { input: 1, output: 6 },
        price_cny: { input: 6.77, output: 40.62 },
      },
    ]);
    expect(byId.get("x-ai/grok-4.6")?.pricing_overrides).toEqual([
      {
        min_prompt_tokens: 200_000,
        pricing: { input: 4, output: 12 },
        price_cny: { input: 27.08, output: 81.24 },
      },
    ]);
  });

  it("routes the three media snapshots to their dedicated workspaces", () => {
    expect(
      models.find((model) => model.id === "bytedance-seed/seedream-5-0-pro"),
    ).toMatchObject({
      primary_operation: "generate_image",
      output_modalities: ["image"],
    });
    expect(
      models.find((model) => model.id === "deepgram/flux-tts:free"),
    ).toMatchObject({
      primary_operation: "synthesize_speech",
      interaction_status: "ready",
      output_modalities: ["speech"],
    });
    expect(
      models.find((model) => model.id === "bytedance/seedance-2.0-mini"),
    ).toMatchObject({
      primary_operation: "generate_video",
      interaction_status: "ready",
      output_modalities: ["video"],
    });
  });

  it("uses V4 Flash for agent builder and workflow-agent defaults", () => {
    expect(DEFAULT_AGENT_BUILDER_MODEL_ID).toBe("deepseek/deepseek-v4-flash-0731");
    expect(DEFAULT_WORKFLOW_AGENT_MODEL_ID).toBe("deepseek/deepseek-v4-flash-0731");
  });

  it("restores GPT-5.2 Chat as live", () => {
    const model = models.find(
      (candidate) => candidate.id === "openai/gpt-5.2-chat",
    );

    expect(model?.catalog_status).toBe("live");
    expect(model?.active).toBe(true);
  });

  it("keeps uncertain entries callable below clearly available models", () => {
    const uncertain = models.filter(
      (model) => model.catalog_status === "uncertain",
    );
    const ling = uncertain.find(
      (model) => model.id === "inclusionai/ling-3.0-flash:free",
    );
    const lastClearIndex = models.reduce(
      (lastIndex, model, index) =>
        model.catalog_status === "live" ||
        model.catalog_status === "curated"
          ? index
          : lastIndex,
      -1,
    );
    const firstUncertainIndex = models.findIndex(
      (model) => model.catalog_status === "uncertain",
    );
    const firstExpiredIndex = models.findIndex(
      (model) => model.catalog_status === "expired",
    );

    expect(uncertain).toHaveLength(73);
    expect(ling?.active).toBe(true);
    expect(
      uncertain.find((model) => model.id === "mistralai/ministral-8b")
        ?.active,
    ).toBe(true);
    expect(
      uncertain.find((model) => model.id === "thedrummer/rocinante-12b")
        ?.active,
    ).toBe(true);
    expect(
      uncertain
        .filter((model) => model.id.startsWith("zyphra/zonos-v0.1-"))
        .map((model) => model.id)
        .sort(),
    ).toEqual([
      "zyphra/zonos-v0.1-hybrid",
      "zyphra/zonos-v0.1-transformer",
    ]);
    for (const modelId of [
      "allenai/olmo-3-32b-think",
      "arcee-ai/virtuoso-large",
      "kwaipilot/kat-coder-air-v2.5",
      "anthropic/claude-opus-4.7-fast",
      "anthropic/claude-opus-4.8-fast",
      "anthropic/claude-opus-5-fast",
    ]) {
      expect(uncertain.find((model) => model.id === modelId)).toMatchObject({
        active: true,
        catalog_status: "uncertain",
      });
    }
    expect(firstUncertainIndex).toBeGreaterThan(lastClearIndex);
    expect(firstExpiredIndex).toBeGreaterThan(firstUncertainIndex);
  });

  it("attaches all batch entries as uncounted serving variants", () => {
    const batchVariants = models.flatMap((model) =>
      model.serving_variants.filter((variant) => variant.type === "batch"),
    );
    const gemini = models.find(
      (model) => model.id === "google/gemini-2.5-flash",
    );
    const geminiBatch = gemini?.serving_variants.find(
      (variant) => variant.type === "batch",
    );

    expect(models.some((model) => model.id.endsWith(":batch"))).toBe(false);
    const gemini37 = models.find(
      (model) => model.id === "google/gemini-3.7-flash",
    );
    const gemini37Batch = gemini37?.serving_variants.find(
      (variant) => variant.type === "batch",
    );

    const geminiEmbedding = models.find(
      (model) => model.id === "google/gemini-embedding-2",
    );
    const geminiEmbeddingBatch = geminiEmbedding?.serving_variants.find(
      (variant) => variant.type === "batch",
    );
    const batchCatalogIds = batchVariants.map((variant) => variant.catalog_id);

    expect(batchVariants).toHaveLength(70);
    expect(batchCatalogIds).toContain("openai/gpt-4o:batch");
    expect(batchCatalogIds).toContain("openai/gpt-5.6-luna:batch");
    expect(batchCatalogIds).not.toContain(
      "moonshotai/kimi-k2.7-code:batch",
    );
    expect(
      batchVariants.filter((variant) => variant.endpoint === "/v1/embeddings"),
    ).toHaveLength(4);
    for (const variant of batchVariants) {
      const isEmbedding = variant.endpoint === "/v1/embeddings";
      expect(variant.catalog_id).toBe(`${variant.request_model_id}:batch`);
      expect(variant.request_model_id).not.toContain(":batch");
      expect(variant.input_modalities).toEqual(["text"]);
      expect(variant.output_modalities).toEqual(
        isEmbedding ? ["embeddings"] : ["text"],
      );
      expect(variant.completion_window).toBe("24h");
      expect(variant.data_retention_days).toBe(30);
      expect(variant.endpoint).toBe(
        isEmbedding ? "/v1/embeddings" : "/v1/chat/completions",
      );
    }
    for (const catalogId of august28BatchCatalogIds) {
      const requestModelId = catalogId.replace(/:batch$/, "");
      const variant = models
        .find((model) => model.id === requestModelId)
        ?.serving_variants.find((candidate) => candidate.catalog_id === catalogId);
      expect(variant).toMatchObject({
        type: "batch",
        catalog_id: catalogId,
        request_model_id: requestModelId,
        endpoint: "/v1/chat/completions",
        input_modalities: ["text"],
        output_modalities: ["text"],
        completion_window: "24h",
        data_retention_days: 30,
      });
    }
    expect(geminiBatch).toMatchObject({
      catalog_id: "google/gemini-2.5-flash:batch",
      request_model_id: "google/gemini-2.5-flash",
      endpoint: "/v1/chat/completions",
      input_modalities: ["text"],
      output_modalities: ["text"],
      completion_window: "24h",
      data_retention_days: 30,
    });
    expect(geminiBatch?.pricing.input).toBeCloseTo(
      (gemini?.pricing.input ?? 0) / 2,
    );
    expect(geminiBatch?.pricing.output).toBeCloseTo(
      (gemini?.pricing.output ?? 0) / 2,
    );
    expect(gemini37Batch).toMatchObject({
      catalog_id: "google/gemini-3.7-flash:batch",
      request_model_id: "google/gemini-3.7-flash",
      endpoint: "/v1/chat/completions",
      input_modalities: ["text"],
      output_modalities: ["text"],
      pricing: { input: 0.1875, output: 0.9375 },
      completion_window: "24h",
      data_retention_days: 30,
    });
    expect(geminiEmbeddingBatch).toMatchObject({
      catalog_id: "google/gemini-embedding-2:batch",
      request_model_id: "google/gemini-embedding-2",
      endpoint: "/v1/embeddings",
      input_modalities: ["text"],
      output_modalities: ["embeddings"],
      completion_window: "24h",
      data_retention_days: 30,
    });
    expect(
      models
        .find((model) => model.id === "anthropic/claude-fable-5.1")
        ?.serving_variants.find(
          (variant) =>
            variant.catalog_id === "anthropic/claude-fable-5.1:batch",
        ),
    ).toMatchObject({
      type: "batch",
      request_model_id: "anthropic/claude-fable-5.1",
      endpoint: "/v1/chat/completions",
      input_modalities: ["text"],
      output_modalities: ["text"],
      pricing: { input: 5, output: 25 },
      completion_window: "24h",
      data_retention_days: 30,
    });
    expect(
      models
        .find((model) => model.id === "qwen/qwen3.8-2.4t-a95b")
        ?.serving_variants.find(
          (variant) =>
            variant.catalog_id === "qwen/qwen3.8-2.4t-a95b:batch",
        )?.pricing,
    ).toEqual({ input: 2, output: 6 });
  });

  it("keeps only explicitly expired models inactive", () => {
    expect(
      models
        .filter((model) => model.catalog_status === "expired")
        .map((model) => model.id)
        .sort(),
    ).toEqual([
      "inclusionai/ling-3.0-tiny:free",
      "openai/gpt-5.3-chat",
      "poolside/laguna-m.1",
      "poolside/laguna-m.1:free",
      "stealth/ox-alpha",
      "tencent/hy3:free",
    ]);
  });
});

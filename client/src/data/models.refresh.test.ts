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

describe("OpenRouter 2026-08-13 model refresh", () => {
  it("restores V4 Flash ahead of V4 Pro and keeps Seedream in row four", () => {
    expect(models[2]?.id).toBe("deepseek/deepseek-v4-flash-0731");
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
      providers: ["Xiaomi"],
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
      1_000_000,
    );
    expect(byId.get("nvidia/nemotron-3.5-lightning")).toMatchObject({
      context_length: 1_048_576,
      supported_parameters: expect.arrayContaining(["tool_choice", "tools"]),
    });
    expect(
      byId.get("bytedance-seed/seed-2-1-turbo")?.supported_parameters,
    ).not.toEqual(
      expect.arrayContaining(["presence_penalty", "reasoning_effort", "seed"]),
    );
    expect(
      byId.get("deepseek/deepseek-v4-pro-0813")?.supported_parameters,
    ).not.toEqual(
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
      input: 0.079996,
      output: 0.252,
    });
    expect(byId.get("z-ai/glm-5.2")?.pricing).toEqual({
      input: 0.5,
      output: 3.15,
    });
    expect(byId.get("moonshotai/kimi-k2.7-code")?.pricing).toEqual({
      input: 0.67,
      output: 3.4,
    });
    expect(byId.get("deepseek/deepseek-v4-pro")?.pricing).toEqual({
      input: 1.1680000000000001,
      output: 2.3360000000000003,
    });
    expect(byId.get("qwen/qwen3.5-35b-a3b")?.pricing).toEqual({
      input: 0.25,
      output: 1.25,
    });
    expect(byId.get("qwen/qwen3.5-397b-a17b")?.pricing).toEqual({
      input: 0.5,
      output: 3.5999999999999996,
    });
    expect(byId.get("z-ai/glm-4.6")?.pricing).toEqual({
      input: 0.5,
      output: 2,
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

    expect(uncertain).toHaveLength(55);
    expect(ling?.active).toBe(true);
    expect(
      uncertain
        .filter((model) => model.id.startsWith("zyphra/zonos-v0.1-"))
        .map((model) => model.id)
        .sort(),
    ).toEqual([
      "zyphra/zonos-v0.1-hybrid",
      "zyphra/zonos-v0.1-transformer",
    ]);
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
    expect(batchVariants).toHaveLength(62);
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
      "tencent/hy3:free",
    ]);
  });
});

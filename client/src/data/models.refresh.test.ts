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
  "inclusionai/ling-3.0-tiny:free",
];

const august13ModelIds = [
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
  it("places V4 Pro, the former Flash default, and Seedream in their requested rows", () => {
    expect(models[2]?.id).toBe("deepseek/deepseek-v4-pro-0813");
    expect(models[5]?.id).toBe("deepseek/deepseek-v4-flash-0731");
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

  it("adds all eight live counted snapshots exactly once", () => {
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

  it("uses V4 Pro for agent builder and workflow-agent defaults", () => {
    expect(DEFAULT_AGENT_BUILDER_MODEL_ID).toBe("deepseek/deepseek-v4-pro-0813");
    expect(DEFAULT_WORKFLOW_AGENT_MODEL_ID).toBe("deepseek/deepseek-v4-pro-0813");
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

    expect(uncertain).toHaveLength(53);
    expect(ling?.active).toBe(true);
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
      "openai/gpt-5.3-chat",
      "poolside/laguna-m.1",
      "poolside/laguna-m.1:free",
      "tencent/hy3:free",
    ]);
  });
});

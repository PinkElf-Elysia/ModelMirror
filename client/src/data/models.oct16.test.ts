import { describe, expect, it } from "vitest";
import { models } from "./models";
import { estimateImageCost } from "../utils/imageCostEstimate";

const ids = ["elorian/glimpse", "qwen/qwen-image-2.1-turbo", "qwen/qwen-image-2.1-pro", "perplexity/pplx-decider-flash-v1.2", "perplexity/pplx-decider-v1.2", "xingchen-agi/xing4.0-29b-a4b:free", "~perplexity/pplx-decider-flash-latest", "reflection/beam-1.0-501b-a23b"];
describe("October 16 expanded contracts", () => {
  it("promotes Flash 1017 without changing the previous model identity", () => {
    const model = models.find(m => m.id === "deepseek/deepseek-v4.1-flash-1017")!;
    expect(models.indexOf(model)).toBe(3);
    expect(model).toMatchObject({ context_length: 1048576, input_modalities: ["text", "image"], output_modalities: ["text"], ui_entrypoint: "chat", reasoning_declared: true });
    expect(model.pricing_time_windows).toHaveLength(6);
    expect(models.findIndex(m => m.id === "deepseek/deepseek-v4.1-flash")).toBeGreaterThanOrEqual(20);
  });
  it.each(ids)("keeps %s unique and after six rows", id => {
    expect(models.filter(m => m.id === id)).toHaveLength(1);
    expect(models.findIndex(m => m.id === id)).toBeGreaterThanOrEqual(20);
  });
  it.each(ids.filter(id => id.includes("pplx-decider")))("routes %s to text decisions", id => {
    expect(models.find(m => m.id === id)).toMatchObject({ operations: ["decide"], ui_entrypoint: "decisions" });
    expect(models.find(m => m.id === id)?.note).toContain("文字");
  });
  it.each([["qwen/qwen-image-2.1-turbo", 0.0184], ["qwen/qwen-image-2.1-pro", 0.046]] as const)("keeps %s paid and estimates six outputs", (id, price) => {
    const model = models.find(m => m.id === id)!;
    expect(model.pricing_status).not.toBe("free");
    expect(model.media_pricing).toEqual({ unit: "image", usd: price });
    expect(model.supported_parameters).toEqual(["resolution", "aspect_ratio", "n", "input_references", "seed"]);
    const estimate = estimateImageCost([{ billable: "input_image", unit: "image", cost_usd: 0 }, { billable: "output_image", unit: "image", cost_usd: price, variant: "2k" }], { outputCount: 6, referenceCount: 4, resolution: "2K" });
    expect(estimate?.minUsd).toBeCloseTo(price * 6);
    expect(estimate?.exact).toBe(true);
  });
  it("keeps Qwen Batch uncounted and text-only", () => {
    expect(models.some(m => m.id === "qwen/qwen3.7-plus:batch")).toBe(false);
    expect(models.find(m => m.id === "qwen/qwen3.7-plus")?.serving_variants).toEqual(expect.arrayContaining([expect.objectContaining({ catalog_id: "qwen/qwen3.7-plus:batch", request_model_id: "qwen/qwen3.7-plus", input_modalities: ["text"] })]));
  });
  it("preserves speech character billing after the price change", () => {
    expect(models.find(m => m.id === "heygen/heygen-voice-1-turbo")?.media_pricing).toEqual({ unit: "character_million", usd: 4 });
  });
});

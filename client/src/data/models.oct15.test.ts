import { describe, expect, it } from "vitest";
import { models } from "./models";
import { estimateImageCost } from "../utils/imageCostEstimate";

const ids = [
  "meta/muse-spark-open", "black-forest-labs/flux-3-image-klein",
  "nvidia/nemotron-3.5-super-vl", "nvidia/nemotron-3.5-super-vl:free",
  "respan/span-02", "liquid/d1-ultra", "krea/k3-edit",
  "moondream/moondream3-preview", "moondream/moondream3.1-9b-a2b",
];

describe("October 15 model contracts", () => {
  it.each(ids)("keeps %s unique and below six rows", id => {
    expect(models.filter(m => m.id === id)).toHaveLength(1);
    expect(models.findIndex(m => m.id === id)).toBeGreaterThanOrEqual(20);
  });
  it.each(["respan/span-02", "liquid/d1-ultra"])("routes %s to decisions only", id => {
    expect(models.find(m => m.id === id)).toMatchObject({
      operations: ["decide"], ui_entrypoint: "decisions",
    });
    expect(models.find(m => m.id === id)?.note).toContain("仅开放文字状态");
  });
  it.each(["black-forest-labs/flux-3-image-klein", "krea/k3-edit"])("does not label paid image model %s free", id => {
    const model = models.find(m => m.id === id)!;
    expect(model.operations).toContain("generate_image");
    expect(model.pricing_basis).toBe("media");
    expect(model.pricing_status).not.toBe("free");
    expect(model.supported_parameters).toContain("input_references");
  });
  it.each(["meta/muse-spark-open", "nvidia/nemotron-3.5-super-vl", "nvidia/nemotron-3.5-super-vl:free", "moondream/moondream3-preview", "moondream/moondream3.1-9b-a2b"])("retains vision chat for %s", id => {
    const model = models.find(m => m.id === id)!;
    expect(model.operations).toContain("chat");
    expect(model.operations).toContain("analyze_image");
    expect(model.operations).not.toContain("generate_image");
  });
  it("estimates Klein by the live endpoint resolution and leaves missing Krea prices unknown", () => {
    const pricing = [0.008, 0.009, 0.011, 0.014, 0.036].map((cost_usd, i) => ({
      billable: "output_image" as const, unit: "image" as const, cost_usd,
      variant: ["768", "1k", "1.5k", "2k", "4k"][i],
    }));
    expect(estimateImageCost(pricing, { outputCount: 1, referenceCount: 0, resolution: "4K" })).toMatchObject({ minUsd: 0.036, maxUsd: 0.036, exact: true });
    expect(estimateImageCost([], { outputCount: 1, referenceCount: 1, resolution: "1K" })).toBeNull();
  });
});
